# pdf_control.py
import io
import importlib
import logging
import re
from typing import Optional, Iterator, Tuple

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from db import obtener_bd

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/recursos", tags=["pdf"])

# ---------------------------
# Helpers para cargar RenderApi dinamicamente
# ---------------------------
def _obtener_renderapi() -> Tuple[Optional[object], Optional[str], Optional[callable], Optional[callable]]:
    """
    Intenta importar RenderApi y devolver:
      (supabase_client, BUCKET_NAME, extract_path_from_supabase_public_url, upload_bytes_to_supabase)
    Si no esta disponible devuelve (None, None, None, None)
    """
    try:
        mod = importlib.import_module("RenderApi")
        supabase = getattr(mod, "supabase", None)
        bucket = getattr(mod, "BUCKET_NAME", None)
        extraer = getattr(mod, "extract_path_from_supabase_public_url", None)
        subir_helper = getattr(mod, "upload_bytes_to_supabase", None)
        return supabase, bucket, extraer, subir_helper
    except Exception as e:
        logger.debug("No se pudo importar RenderApi dinamicamente: %s", e)
        return None, None, None, None

# ---------------------------
# Util: sanitizar filename
# ---------------------------
def sanitizar_nombre(nombre: Optional[str]) -> str:
    if not nombre:
        nombre = "documento"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)[:120]

# ---------------------------
# Normalizar distintos retornos del SDK Supabase
# ---------------------------
def _normalizar_respuesta_supabase(res) -> Optional[bytes]:
    if res is None:
        return None
    # caso dict con data
    if isinstance(res, dict):
        if "data" in res and isinstance(res["data"], (bytes, bytearray)):
            return bytes(res["data"])
        # algunos SDKs regresan {'error': ...} u otros campos
        return None
    # file-like object
    if hasattr(res, "read"):
        try:
            return res.read()
        except Exception:
            return None
    # bytes directo
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    # tupla (data, error)
    if isinstance(res, (list, tuple)) and len(res) >= 1 and isinstance(res[0], (bytes, bytearray)):
        return bytes(res[0])
    return None

# ---------------------------
# Obtener bytes del PDF: storage primero, luego ruta publica (HTTP)
# ---------------------------
def obtener_bytes_pdf(ruta_publica: Optional[str], file_path: Optional[str]) -> bytes:
    supabase, bucket, _, _ = _obtener_renderapi()

    # 1) intentar desde Supabase Storage si hay path
    if file_path and supabase:
        try:
            # la firma puede variar entre versiones
            try:
                res = supabase.storage.from_(bucket).download(file_path)
            except TypeError:
                res = supabase.storage.from_(bucket).download(file_path)
            datos = _normalizar_respuesta_supabase(res)
            if datos:
                return datos
            # si res es (data, error)
            if isinstance(res, (list, tuple)) and len(res) >= 1 and isinstance(res[0], (bytes, bytearray)):
                return bytes(res[0])
        except Exception as e:
            logger.exception("Error descargando desde Supabase storage (intentando fallback a ruta publica): %s", e)

    # 2) intentar descargar por HTTP desde ruta_publica
    if ruta_publica:
        try:
            r = requests.get(ruta_publica, timeout=15)
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.exception("Error descargando desde ruta publica: %s", e)

    # no se pudo obtener
    raise HTTPException(status_code=404, detail="PDF no encontrado en storage ni en ruta publica")

# ---------------------------
# Generador de destino de preview en bucket
# ---------------------------
def generar_destino_preview(path_archivo: str, pagina: int) -> str:
    seguro = path_archivo.replace("/", "_")
    return f"previews/{seguro}_pagina_{pagina}.png"

# ---------------------------
# Iterador para streaming desde requests
# ---------------------------
def _iter_requests_content(resp: requests.Response, chunk_size: int = 8192) -> Iterator[bytes]:
    for chunk in resp.iter_content(chunk_size=chunk_size):
        if chunk:
            yield chunk

# ---------------------------
# ENDPOINT: preview -> convierte 1 pagina a PNG
# ---------------------------
@router.get("/{recurso_id}/preview", responses={200: {"content": {"image/png": {}}}})
def recurso_preview(
    recurso_id: int = Path(..., description="ID del recurso"),
    pagina: int = Query(0, ge=0, description="Pagina (0-index)"),
    subir_cache: bool = Query(False, description="Si true sube preview a storage y devuelve X-Preview-Url"),
    db = Depends(obtener_bd),
):
    try:
        # obtener fila del recurso
        consulta = text("SELECT ruta, file_path FROM recursos WHERE id = :id")
        fila = db.execute(consulta, {"id": recurso_id}).mappings().fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = fila.get("ruta")
        file_path = fila.get("file_path")

        # si no hay file_path, intentar extraer de URL publica si el helper existe
        if not file_path and ruta:
            _, _, extraer, _ = _obtener_renderapi()
            if extraer:
                try:
                    file_path = extraer(ruta)
                except Exception:
                    logger.debug("extract_path_from_supabase_public_url fallo")

        # obtener bytes del PDF (puede lanzar 404)
        pdf_bytes = obtener_bytes_pdf(ruta, file_path)

        # convertir con pymupdf
        try:
            import fitz  # pymupdf
        except Exception:
            logger.exception("PyMuPDF no disponible")
            raise HTTPException(status_code=500, detail="PyMuPDF no esta instalado en el servidor")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if pagina < 0 or pagina >= doc.page_count:
            raise HTTPException(status_code=400, detail="Pagina fuera de rango")

        pag = doc.load_page(pagina)
        pix = pag.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        headers = {"Cache-Control": "public, max-age=300"}

        # si se pide subir cache, intentar subir (si helper o sdk esta disponible)
        if subir_cache and file_path:
            supabase, bucket, _, subir_helper = _obtener_renderapi()
            if subir_helper:
                try:
                    destino = generar_destino_preview(file_path, pagina)
                    try:
                        url_publica = subir_helper(img_bytes, destino)
                    except Exception:
                        # fallback a SDK directo
                        try:
                            supabase.storage.from_(bucket).upload(destino, io.BytesIO(img_bytes))
                            pu = supabase.storage.from_(bucket).get_public_url(destino)
                            if isinstance(pu, dict):
                                url_publica = pu.get("publicUrl") or pu.get("publicURL") or pu.get("public_url") or str(pu)
                            else:
                                url_publica = str(pu)
                        except Exception as e:
                            logger.exception("Error subiendo preview via SDK: %s", e)
                            url_publica = None
                    if url_publica:
                        headers["X-Preview-Url"] = str(url_publica)
                except Exception:
                    logger.exception("Fallo no critico al subir preview")

        return Response(content=img_bytes, media_type="image/png", headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en recurso_preview: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al generar preview")

# ---------------------------
# ENDPOINT: download -> devuelve PDF original (streaming)
# ---------------------------
@router.get("/{recurso_id}/download", responses={200: {"content": {"application/pdf": {}}}})
def recurso_download(
    recurso_id: int = Path(..., description="ID del recurso"),
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

        # intentar desde supabase storage si hay path
        supabase, bucket, _, _ = _obtener_renderapi()
        if file_path and supabase:
            try:
                res = supabase.storage.from_(bucket).download(file_path)
                datos = _normalizar_respuesta_supabase(res)
                if datos is not None:
                    headers = {"Content-Disposition": f'attachment; filename="{titulo}"'}
                    return StreamingResponse(io.BytesIO(datos), media_type="application/pdf", headers=headers)
                # si res es file-like (stream)
                if hasattr(res, "read") and not isinstance(res, (bytes, bytearray)):
                    headers = {"Content-Disposition": f'attachment; filename="{titulo}"'}
                    try:
                        return StreamingResponse(res, media_type="application/pdf", headers=headers)
                    except Exception:
                        pass
            except Exception as e:
                logger.exception("Error descargando desde storage para download: %s", e)

        # fallback: ruta publica via HTTP
        if ruta:
            try:
                r = requests.get(ruta, timeout=15, stream=True)
                r.raise_for_status()
                headers = {"Content-Disposition": f'attachment; filename="{titulo}"'}
                return StreamingResponse(_iter_requests_content(r), media_type="application/pdf", headers=headers)
            except Exception as e:
                logger.exception("Error descargando ruta publica para download: %s", e)

        raise HTTPException(status_code=404, detail="PDF no disponible para descarga")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en recurso_download: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al descargar PDF")
