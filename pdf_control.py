# pdf_control.py
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Path
from sqlalchemy import text

# dependencias y helpers del proyecto
from db import obtener_bd
from RenderApi import supabase, BUCKET_NAME, extract_path_from_supabase_public_url

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

# -------------------------------------------------
# Funcion auxiliar para obtener los bytes del PDF
# -------------------------------------------------
def obtener_bytes_pdf_desde_recurso(
    ruta_publica: Optional[str],
    path_archivo: Optional[str]
) -> bytes:

    # 1) intentar descargar desde Supabase Storage
    if path_archivo and supabase:
        try:
            try:
                respuesta = supabase.storage.from_(BUCKET_NAME).download(path_archivo)
            except TypeError:
                respuesta = supabase.storage.from_(BUCKET_NAME).download(path_archivo)

            if isinstance(respuesta, dict) and respuesta.get("data"):
                return respuesta["data"]

            if hasattr(respuesta, "read"):
                return respuesta.read()

            if isinstance(respuesta, (bytes, bytearray)):
                return bytes(respuesta)

        except Exception as error:
            logger.exception(
                "Error descargando PDF desde Supabase storage: %s",
                error
            )

    # 2) intentar descargar desde ruta publica
    if ruta_publica:
        try:
            import requests
            respuesta = requests.get(ruta_publica, timeout=10)
            respuesta.raise_for_status()
            return respuesta.content
        except Exception as error:
            logger.exception(
                "Error descargando PDF desde ruta publica: %s",
                error
            )

    raise HTTPException(
        status_code=404,
        detail="No se pudo obtener el PDF"
    )

# -------------------------------------------------
# Funcion para generar el path de la imagen preview
# -------------------------------------------------
def generar_destino_preview(path_archivo: str, pagina: int) -> str:
    path_seguro = path_archivo.replace("/", "_")
    return f"previews/{path_seguro}_pagina_{pagina}.png"

# -------------------------------------------------
# Endpoint PREVIEW - convierte PDF a imagen PNG
# -------------------------------------------------
@router.get(
    "/recursos/{id_recurso}/preview",
    responses={200: {"content": {"image/png": {}}}}
)
def recurso_preview(
    id_recurso: int = Path(..., description="ID del recurso"),
    pagina: int = Query(0, ge=0, description="Pagina del PDF"),
    subir_cache: bool = Query(
        False,
        description="Subir preview a Supabase y devolver URL"
    ),
    bd = Depends(obtener_bd),
):
    try:
        consulta = text(
            "SELECT ruta, file_path FROM recursos WHERE id = :id"
        )
        fila = bd.execute(
            consulta,
            {"id": id_recurso}
        ).mappings().fetchone()

        if not fila:
            raise HTTPException(
                status_code=404,
                detail="Recurso no encontrado"
            )

        ruta = fila.get("ruta")
        path_archivo = fila.get("file_path")

        if not path_archivo and ruta:
            path_archivo = extract_path_from_supabase_public_url(ruta)

        # obtener bytes del PDF
        bytes_pdf = obtener_bytes_pdf_desde_recurso(
            ruta,
            path_archivo
        )

        # convertir PDF a imagen usando PyMuPDF
        try:
            import fitz
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="PyMuPDF no esta instalado"
            )

        documento = fitz.open(
            stream=bytes_pdf,
            filetype="pdf"
        )

        if pagina >= documento.page_count:
            raise HTTPException(
                status_code=400,
                detail="Pagina fuera de rango"
            )

        pagina_pdf = documento.load_page(pagina)
        imagen = pagina_pdf.get_pixmap(dpi=180)
        bytes_imagen = imagen.tobytes("png")

        headers = {}

        # subir preview a Supabase si se solicita
        if subir_cache and supabase and path_archivo:
            try:
                destino = generar_destino_preview(
                    path_archivo,
                    pagina
                )

                url_publica = None

                try:
                    from RenderApi import upload_bytes_to_supabase
                    url_publica = upload_bytes_to_supabase(
                        bytes_imagen,
                        destino
                    )
                except Exception:
                    supabase.storage.from_(BUCKET_NAME).upload(
                        destino,
                        io.BytesIO(bytes_imagen)
                    )
                    respuesta_url = supabase.storage.from_(
                        BUCKET_NAME
                    ).get_public_url(destino)

                    if isinstance(respuesta_url, dict):
                        url_publica = respuesta_url.get("publicUrl")
                    else:
                        url_publica = str(respuesta_url)

                headers["X_Preview_Url"] = str(url_publica)

            except Exception:
                logger.exception(
                    "No se pudo subir la imagen preview"
                )

        return Response(
            content=bytes_imagen,
            media_type="image/png",
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as error:
        logger.exception(
            "Error inesperado en recurso_preview: %s",
            error
        )
        raise HTTPException(
            status_code=500,
            detail="Error interno al generar preview"
        )

# -------------------------------------------------
# Endpoint DOWNLOAD - devuelve el PDF original
# -------------------------------------------------
@router.get(
    "/recursos/{id_recurso}/download",
    responses={200: {"content": {"application/pdf": {}}}}
)
def recurso_download(
    id_recurso: int = Path(..., description="ID del recurso"),
    bd = Depends(obtener_bd),
):
    try:
        consulta = text(
            "SELECT ruta, file_path, titulo FROM recursos WHERE id = :id"
        )
        fila = bd.execute(
            consulta,
            {"id": id_recurso}
        ).mappings().fetchone()

        if not fila:
            raise HTTPException(
                status_code=404,
                detail="Recurso no encontrado"
            )

        ruta = fila.get("ruta")
        path_archivo = fila.get("file_path")
        titulo = fila.get("titulo") or "documento"

        bytes_pdf = obtener_bytes_pdf_desde_recurso(
            ruta,
            path_archivo
        )

        headers = {
            "Content-Disposition": f'attachment; filename="{titulo}.pdf"'
        }

        return Response(
            content=bytes_pdf,
            media_type="application/pdf",
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as error:
        logger.exception(
            "Error inesperado en recurso_download: %s",
            error
        )
        raise HTTPException(
            status_code=500,
            detail="Error interno al descargar PDF"
        )
