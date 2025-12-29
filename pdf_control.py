# pdf_control.py
import io
import logging
import re
from typing import Optional, Iterator

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import text

# dependencias del proyecto
from db import obtener_bd
from RenderApi import supabase, BUCKET_NAME, extract_path_from_supabase_public_url

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/recursos", tags=["pdf"])

# --------------------------------------------------
# helpers
# --------------------------------------------------

def sanitizar_nombre(nombre: Optional[str]) -> str:
    if not nombre:
        nombre = "documento"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)[:120]


def normalizar_respuesta_supabase(respuesta) -> Optional[bytes]:
    if respuesta is None:
        return None
    if isinstance(respuesta, dict) and "data" in respuesta:
        return respuesta["data"]
    if isinstance(respuesta, (bytes, bytearray)):
        return bytes(respuesta)
    if hasattr(respuesta, "read"):
        return respuesta.read()
    return None


def obtener_bytes_pdf(ruta_publica: Optional[str], file_path: Optional[str]) -> bytes:
    # 1. intentar desde supabase storage
    if file_path and supabase:
        try:
            res = supabase.storage.from_(BUCKET_NAME).download(file_path)
            datos = normalizar_respuesta_supabase(res)
            if datos:
                return datos
        except Exception as e:
            logger.exception("Error descargando pdf desde supabase: %s", e)

    # 2. intentar desde url publica
    if ruta_publica:
        try:
            r = requests.get(ruta_publica, timeout=15)
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.exception("Error descargando pdf desde ruta publica: %s", e)

    raise HTTPException(status_code=404, detail="PDF no encontrado")


def generar_destino_preview(file_path: str, pagina: int) -> str:
    seguro = file_path.replace("/", "_")
    return f"previews/{seguro}_pagina_{pagina}.png"


def iterar_stream_requests(respuesta: requests.Response, tamano: int = 8192) -> Iterator[bytes]:
    for chunk in respuesta.iter_content(chunk_size=tamano):
        if chunk:
            yield chunk

# --------------------------------------------------
# endpoint preview (pdf -> imagen)
# --------------------------------------------------

@router.get("/{recurso_id}/preview", responses={200: {"content": {"image/png": {}}}})
def recurso_preview(
    recurso_id: int = Path(...),
    pagina: int = Query(0, ge=0),
    subir_cache: bool = Query(False),
    db = Depends(obtener_bd),
):
    try:
        consulta = text("SELECT ruta, file_path FROM recursos WHERE id = :id")
        fila = db.execute(consulta, {"id": recurso_id}).mappings().fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = fila.get("ruta")
        file_path = fila.get("file_path")

        if not file_path and ruta:
            file_path = extract_path_from_supabase_public_url(ruta)

        pdf_bytes = obtener_bytes_pdf(ruta, file_path)

        try:
            import fitz  # pymupdf
        except Exception:
            raise HTTPException(status_code=500, detail="PyMuPDF no instalado")

        documento = fitz.open(stream=pdf_bytes, filetype="pdf")

        if pagina < 0 or pagina >= documento.page_count:
            raise HTTPException(status_code=400, detail="Pagina fuera de rango")

        pagina_pdf = documento.load_page(pagina)
        pix = pagina_pdf.get_pixmap(dpi=150)
        imagen_bytes = pix.tobytes("png")

        headers = {"Cache-Control": "public, max-age=300"}

        # opcional: subir preview a supabase
        if subir_cache and supabase and file_path:
            try:
                destino = generar_destino_preview(file_path, pagina)
                supabase.storage.from_(BUCKET_NAME).upload(destino, imagen_bytes)
                public = supabase.storage.from_(BUCKET_NAME).get_public_url(destino)
                if isinstance(public, dict):
                    headers["X-Preview-Url"] = public.get("publicUrl")
                else:
                    headers["X-Preview-Url"] = str(public)
            except Exception:
                logger.exception("No se pudo subir preview a supabase")

        return Response(content=imagen_bytes, media_type="image/png", headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en preview: %s", e)
        raise HTTPException(status_code=500, detail="Error interno generando preview")

# --------------------------------------------------
# endpoint download (pdf original)
# --------------------------------------------------

@router.get("/{recurso_id}/download", responses={200: {"content": {"application/pdf": {}}}})
def recurso_download(
    recurso_id: int = Path(...),
    db = Depends(obtener_bd),
):
    try:
        consulta = text("SELECT ruta, file_path, titulo FROM recursos WHERE id = :id")
        fila = db.execute(consulta, {"id": recurso_id}).mappings().fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = fila.get("ruta")
        file_path = fila.get("file_path")
        titulo = sanitizar_nombre(fila.get("titulo")) + ".pdf"

        # 1. desde supabase
        if file_path and supabase:
            try:
                res = supabase.storage.from_(BUCKET_NAME).download(file_path)
                datos = normalizar_respuesta_supabase(res)
                if datos:
                    headers = {
                        "Content-Disposition": f'attachment; filename="{titulo}"'
                    }
                    return StreamingResponse(
                        io.BytesIO(datos),
                        media_type="application/pdf",
                        headers=headers,
                    )
            except Exception as e:
                logger.exception("Error download supabase: %s", e)

        # 2. desde ruta publica
        if ruta:
            try:
                r = requests.get(ruta, stream=True, timeout=15)
                r.raise_for_status()
                headers = {
                    "Content-Disposition": f'attachment; filename="{titulo}"'
                }
                return StreamingResponse(
                    iterar_stream_requests(r),
                    media_type="application/pdf",
                    headers=headers,
                )
            except Exception as e:
                logger.exception("Error download ruta publica: %s", e)

        raise HTTPException(status_code=404, detail="PDF no disponible")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en download: %s", e)
        raise HTTPException(status_code=500, detail="Error interno descargando pdf")
