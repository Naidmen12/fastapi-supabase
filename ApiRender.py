# RenderApi.py
import os
import io
import uuid
import tempfile
import logging
import re
from typing import List, Optional, Iterator
from urllib.parse import urlparse, unquote

import requests
from fastapi import FastAPI, Depends, HTTPException, Body, Path, File, UploadFile, Form, Request, Query, Response
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy import text

# importar la dependencia de BD y helper init_db
from db import obtener_bd, init_db
from models import Usuario
from schemas import (
    PeticionInicio,
    RespuestaUsuario,
    UsuarioCreate,
    UsuarioUpdate,
    RecursoCreate,
    RecursoUpdate,
    RecursoOut,
    PestanaCreate,
    PestanaUpdate,
    PestanaOut,
)

# Intento importar cliente Supabase
try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

logger = logging.getLogger("uvicorn.error")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
BUCKET_NAME = os.environ.get("SUPABASE_BUCKET", "pdf")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    logger.warning("AVISO: SUPABASE_URL o SUPABASE_SERVICE_KEY no estan definidas. Define las variables de entorno.")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY and create_client is not None:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        logger.info("Supabase client inicializado")
    except Exception:
        supabase = None
        logger.exception("No se pudo inicializar Supabase client")
else:
    if create_client is None:
        logger.warning("SDK de supabase no disponible: 'supabase' package no importado")

app = FastAPI(title="FastAPI - Identificacion (Render)")

# ------------------------------------------------------------------
# Startup: intentar warmup DB
# ------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    try:
        # intenta verificar la BD en startup para reducir errores iniciales
        init_db(startup_retries=3, startup_delay=1.0)
    except Exception:
        logger.exception("Error en init_db durante startup")

# ------------------------------------------------------------------
# Handler para HTTPException: loggea y pasa headers como Retry-After
# ------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # Loguear stack trace para 503 y errores criticos
    if exc.status_code == 503:
        logger.exception("HTTPException 503: %s", exc.detail)
    # Asegurarse de propagar headers (ej: Retry-After)
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=headers)

# Handler global para debug (mantener pero mas limpio)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    if os.environ.get("DEBUG_SHOW_ERROR", "false").lower() in ("1", "true", "yes"):
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

# ---------- helpers ----------
def extract_path_from_supabase_public_url(url: str) -> Optional[str]:
    try:
        p = urlparse(url)
        path = unquote(p.path or "")
        marker = "/storage/v1/object/public/"
        idx = path.find(marker)
        if idx == -1:
            return None
        after = path[idx + len(marker):]
        if after.startswith(BUCKET_NAME + "/"):
            return after[len(BUCKET_NAME) + 1:]
        return after
    except Exception:
        return None


def delete_file_from_supabase(file_path_in_bucket: str) -> dict:
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    try:
        res = supabase.storage.from_(BUCKET_NAME).remove([file_path_in_bucket])
        # algunos SDKs devuelven (data, error) o dict
        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=500, detail=f"Error al eliminar archivo en Supabase: {res['error']}")
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error eliminando archivo en Supabase: %s", e)
        raise HTTPException(status_code=500, detail=f"Error interno al eliminar archivo en Supabase: {str(e)}")


def upload_bytes_to_supabase(file_bytes: bytes, dest_path_in_bucket: str) -> str:
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    try:
        try:
            res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, file_bytes)
        except TypeError:
            # algunos SDKs esperan file-like objects
            try:
                file_obj = io.BytesIO(file_bytes)
                res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, file_obj)
            except Exception:
                # fallback a archivo temporal
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, tmp_path)
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=500, detail=f"Error al subir a Supabase: {res['error']}")

        public = supabase.storage.from_(BUCKET_NAME).get_public_url(dest_path_in_bucket)
        # normalizar distintos retornos
        if isinstance(public, dict):
            for k in ("publicUrl", "publicURL", "public_url", "url"):
                if k in public:
                    return public[k]
            return str(public)
        else:
            return str(public)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error subiendo archivo a Supabase: %s", e)
        raise HTTPException(status_code=500, detail=f"Error interno al subir archivo: {str(e)}")

# -----------------------
# RUTAS RAIZ y HEALTH
# -----------------------
@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def raiz_get():
    return {"mensaje": "API funcionando correctamente"}

# Health simple (sin DB) - aceptar HEAD para Render
@app.get("/health", response_class=PlainTextResponse)
@app.head("/health", include_in_schema=False)
async def health():
    return PlainTextResponse("OK", status_code=200)

# Health que comprueba la BD (dependencia puede lanzar HTTPException 503)
@app.get("/health/db", response_class=PlainTextResponse)
def health_db(db = Depends(obtener_bd)):
    return PlainTextResponse("OK", status_code=200)

# ---------- test db ----------
@app.get("/test-db")
def test_db(db = Depends(obtener_bd)):
    try:
        row = db.execute(text("SELECT 1")).fetchone()
        return {"ok": True, "result": row[0] if row else None}
    except Exception as e:
        logger.exception("test-db error: %s", e)
        return {"ok": False, "error": str(e)}

# -----------------------
# RUTAS USUARIOS CRUD
# -----------------------
@app.get("/usuarios", response_model=List[RespuestaUsuario])
def listar_usuarios(db = Depends(obtener_bd)):
    try:
        q = text("SELECT id, rol, codigo, clave, creado_en FROM usuarios ORDER BY id")
        rows = db.execute(q).mappings().all()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "rol": r["rol"],
                "codigo": r["codigo"],
                "clave": r["clave"],
            })
        return result
    except OperationalError:
        logger.exception("listar_usuarios: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        logger.exception("listar_usuarios: unexpected")
        raise HTTPException(status_code=500, detail="Error interno al listar usuarios")

@app.post("/usuarios", response_model=RespuestaUsuario, status_code=201)
def crear_usuario(payload: UsuarioCreate = Body(...), db = Depends(obtener_bd)):
    try:
        rol_val = payload.rol.value if hasattr(payload.rol, "value") else payload.rol
        insert_sql = text("""
            INSERT INTO usuarios (rol, codigo, clave)
            VALUES (:rol, :codigo, :clave)
            RETURNING id, rol, codigo, clave, creado_en
        """)
        params = {"rol": rol_val, "codigo": payload.codigo, "clave": payload.clave}
        row = db.execute(insert_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo crear el usuario")
        return {"id": row["id"], "rol": row["rol"], "codigo": row["codigo"], "clave": row["clave"]}
    except IntegrityError as e:
        db.rollback()
        logger.exception("crear_usuario: IntegrityError")
        raise HTTPException(status_code=400, detail="Usuario ya existe o dato invalido")
    except OperationalError:
        db.rollback()
        logger.exception("crear_usuario: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("crear_usuario: unexpected: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al crear usuario")

@app.put("/usuarios/{usuario_id}", response_model=RespuestaUsuario)
def actualizar_usuario(usuario_id: int = Path(...), payload: UsuarioUpdate = Body(...), db = Depends(obtener_bd)):
    try:
        # obtener datos enviados (compatible pydantic v1 y v2)
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        if not data:
            raise HTTPException(status_code=400, detail="No hay campos para actualizar")

        set_fragments = []
        params = {"id": usuario_id}
        idx = 0
        for k, v in data.items():
            idx += 1
            key = f"v{idx}"
            set_fragments.append(f"{k} = :{key}")
            params[key] = v

        set_sql = ", ".join(set_fragments)
        update_sql = text(f"UPDATE usuarios SET {set_sql} WHERE id = :id RETURNING id, rol, codigo, clave, creado_en")
        row = db.execute(update_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        return {"id": row["id"], "rol": row["rol"], "codigo": row["codigo"], "clave": row["clave"]}
    except OperationalError:
        db.rollback()
        logger.exception("actualizar_usuario: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("actualizar_usuario: unexpected: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al actualizar usuario")

@app.delete("/usuarios/{usuario_id}", response_model=dict)
def eliminar_usuario(usuario_id: int = Path(...), db = Depends(obtener_bd)):
    try:
        delete_sql = text("DELETE FROM usuarios WHERE id = :id")
        db.execute(delete_sql, {"id": usuario_id})
        db.commit()
        return {"ok": True}
    except OperationalError:
        db.rollback()
        logger.exception("eliminar_usuario: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception as e:
        db.rollback()
        logger.exception("eliminar_usuario: unexpected: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al eliminar usuario")

# -------------------------------------------------
# INICIO: FUNCIONES Y ENDPOINTS PDF -> IMAGEN / DOWNLOAD
# -------------------------------------------------

# util: sanitizar nombre (para Content-Disposition)
def sanitizar_nombre(nombre: Optional[str]) -> str:
    if not nombre:
        nombre = "documento"
    nombre_seguro = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)
    return nombre_seguro[:120]

# normalizar distintos retornos del SDK de supabase
def _normalizar_respuesta_supabase(res) -> Optional[bytes]:
    if res is None:
        return None
    if isinstance(res, dict):
        if "data" in res and isinstance(res["data"], (bytes, bytearray)):
            return bytes(res["data"])
        return None
    if hasattr(res, "read"):
        try:
            return res.read()
        except Exception:
            return None
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    # caso tupla (data, error)
    if isinstance(res, (list, tuple)) and len(res) >= 1 and isinstance(res[0], (bytes, bytearray)):
        return bytes(res[0])
    return None

# intento de obtener bytes del PDF: storage primero, luego ruta publica HTTP
def obtener_bytes_pdf_desde_recurso(ruta_publica: Optional[str], path_archivo: Optional[str]) -> bytes:
    # 1) intentar desde supabase storage si existe path_archivo
    if path_archivo and supabase:
        try:
            try:
                res = supabase.storage.from_(BUCKET_NAME).download(path_archivo)
            except TypeError:
                res = supabase.storage.from_(BUCKET_NAME).download(path_archivo)
            datos = _normalizar_respuesta_supabase(res)
            if datos:
                return datos
            # si res es (data, error)
            if isinstance(res, (list, tuple)) and len(res) >= 1 and isinstance(res[0], (bytes, bytearray)):
                return bytes(res[0])
        except Exception as e:
            logger.exception("Error descargando desde Supabase storage: %s", e)
            # continuar a intentar ruta publica

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

# genera destino para preview dentro del bucket (sin '/')
def generar_destino_preview(path_archivo: str, pagina: int) -> str:
    seguro = path_archivo.replace("/", "_")
    return f"previews/{seguro}_pagina_{pagina}.png"

# iterador para streaming desde requests
def _iter_requests_content(resp: requests.Response, chunk_size: int = 8192) -> Iterator[bytes]:
    for chunk in resp.iter_content(chunk_size=chunk_size):
        if chunk:
            yield chunk

# Endpoint: preview -> convierte una pagina a PNG
@app.get("/recursos/{id_recurso}/preview", responses={200: {"content": {"image/png": {}}}})
def recurso_preview(
    id_recurso: int = Path(..., description="ID del recurso"),
    pagina: int = Query(0, ge=0, description="Pagina del PDF (0-index)"),
    subir_cache: bool = Query(False, description="Si true sube preview a Supabase y devuelve X-Preview-Url"),
    db = Depends(obtener_bd),
):
    try:
        consulta = text("SELECT ruta, file_path FROM recursos WHERE id = :id")
        fila = db.execute(consulta, {"id": id_recurso}).mappings().fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = fila.get("ruta")
        path_archivo = fila.get("file_path")

        if not path_archivo and ruta:
            path_archivo = extract_path_from_supabase_public_url(ruta)

        # obtener bytes del PDF (puede lanzar 404)
        bytes_pdf = obtener_bytes_pdf_desde_recurso(ruta, path_archivo)

        # convertir con PyMuPDF
        try:
            import fitz  # pymupdf
        except Exception:
            logger.exception("PyMuPDF no disponible")
            raise HTTPException(status_code=500, detail="PyMuPDF no esta instalado en el servidor")

        doc = fitz.open(stream=bytes_pdf, filetype="pdf")

        if pagina < 0 or pagina >= doc.page_count:
            raise HTTPException(status_code=400, detail="Pagina fuera de rango")

        pag = doc.load_page(pagina)
        pix = pag.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        headers = {"Cache-Control": "public, max-age=300"}

        if subir_cache and path_archivo and supabase:
            try:
                destino = generar_destino_preview(path_archivo, pagina)
                # intentar usar tu helper upload_bytes_to_supabase
                try:
                    url_publica = upload_bytes_to_supabase(img_bytes, destino)
                except Exception:
                    # fallback directo al SDK
                    try:
                        supabase.storage.from_(BUCKET_NAME).upload(destino, io.BytesIO(img_bytes))
                        pu = supabase.storage.from_(BUCKET_NAME).get_public_url(destino)
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

# Endpoint: download -> devuelve PDF original (streaming)
@app.get("/recursos/{id_recurso}/download", responses={200: {"content": {"application/pdf": {}}}})
def recurso_download(
    id_recurso: int = Path(..., description="ID del recurso"),
    db = Depends(obtener_bd),
):
    try:
        consulta = text("SELECT ruta, file_path, titulo FROM recursos WHERE id = :id")
        fila = db.execute(consulta, {"id": id_recurso}).mappings().fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = fila.get("ruta")
        path_archivo = fila.get("file_path")
        titulo = fila.get("titulo") or "documento"
        nombre_seguro = sanitizar_nombre(titulo) + ".pdf"

        # intentar descargar desde supabase storage si path_archivo
        if path_archivo and supabase:
            try:
                res = supabase.storage.from_(BUCKET_NAME).download(path_archivo)
                datos = _normalizar_respuesta_supabase(res)
                if datos is not None:
                    fileobj = io.BytesIO(datos)
                    headers = {"Content-Disposition": f'attachment; filename="{nombre_seguro}"'}
                    return StreamingResponse(fileobj, media_type="application/pdf", headers=headers)
                # si res es file-like y no lo leimos antes, intentar usarlo directo
                if hasattr(res, "read") and not isinstance(res, (bytes, bytearray)):
                    headers = {"Content-Disposition": f'attachment; filename="{nombre_seguro}"'}
                    try:
                        return StreamingResponse(res, media_type="application/pdf", headers=headers)
                    except Exception:
                        pass
            except Exception as e:
                logger.exception("Error descargando desde storage para download: %s", e)
                # fallback a ruta publica

        # fallback: intentar ruta publica por HTTP (si existe)
        if ruta:
            try:
                r = requests.get(ruta, timeout=15, stream=True)
                r.raise_for_status()
                headers = {"Content-Disposition": f'attachment; filename="{nombre_seguro}"'}
                return StreamingResponse(_iter_requests_content(r), media_type="application/pdf", headers=headers)
            except Exception as e:
                logger.exception("Error descargando ruta publica para download: %s", e)

        raise HTTPException(status_code=404, detail="PDF no disponible para descarga")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en recurso_download: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al descargar PDF")

# -------------------------------------------------
# FIN: FUNCIONES Y ENDPOINTS PDF
# -------------------------------------------------

# ---------- recursos CRUD ----------
@app.get("/recursos", response_model=List[RecursoOut])
def listar_recursos(db=Depends(obtener_bd)):
    try:
        q = text("SELECT id, titulo, tipo, ruta, file_path, url_youtube, publico, subido_por, creado_en FROM recursos ORDER BY id")
        rows = db.execute(q).mappings().all()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "titulo": r["titulo"],
                "tipo": r["tipo"],
                "ruta": r["ruta"],
                "file_path": r["file_path"],
                "url_youtube": r["url_youtube"],
                "publico": bool(r["publico"]) if r["publico"] is not None else False,
                "subido_por": r["subido_por"],
                "creado_en": str(r["creado_en"]) if r["creado_en"] is not None else None
            })
        return result
    except OperationalError:
        logger.exception("listar_recursos: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        logger.exception("listar_recursos: unexpected")
        raise HTTPException(status_code=500, detail="Error interno al listar recursos")

@app.post("/recursos", response_model=RecursoOut)
def crear_recurso(payload: RecursoCreate = Body(...), db=Depends(obtener_bd)):
    try:
        if not payload.ruta and not payload.url_youtube:
            raise HTTPException(status_code=400, detail="Debe proporcionar 'ruta' (archivo) o 'url_youtube'")
        if payload.url_youtube:
            if "youtube.com" not in payload.url_youtube and "youtu.be" not in payload.url_youtube:
                raise HTTPException(status_code=400, detail="url_youtube no parece una URL de YouTube valida")

        file_path_val = None
        try:
            if hasattr(payload, "file_path") and payload.file_path:
                file_path_val = payload.file_path
        except Exception:
            pass

        if not file_path_val and payload.ruta:
            file_path_val = extract_path_from_supabase_public_url(payload.ruta)

        insert_sql = text("""
            INSERT INTO recursos (titulo, tipo, ruta, file_path, url_youtube, publico, subido_por)
            VALUES (:titulo, :tipo, :ruta, :file_path, :url_youtube, :publico, :subido_por)
            RETURNING id, titulo, tipo, ruta, file_path, url_youtube, publico, subido_por, creado_en
        """)
        params = {
            "titulo": payload.titulo,
            "tipo": payload.tipo,
            "ruta": payload.ruta,
            "file_path": file_path_val,
            "url_youtube": payload.url_youtube,
            "publico": payload.publico,
            "subido_por": payload.subido_por
        }
        row = db.execute(insert_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo crear el recurso")
        return {
            "id": row["id"],
            "titulo": row["titulo"],
            "tipo": row["tipo"],
            "ruta": row["ruta"],
            "file_path": row.get("file_path"),
            "url_youtube": row.get("url_youtube"),
            "publico": bool(row["publico"]) if row["publico"] is not None else False,
            "subido_por": row.get("subido_por"),
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        logger.exception("crear_recurso: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("crear_recurso: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al crear recurso")

@app.put("/recursos/{recurso_id}", response_model=RecursoOut)
def actualizar_recurso(recurso_id: int = Path(...), payload: RecursoUpdate = Body(...), db=Depends(obtener_bd)):
    try:
        select_sql = text("SELECT id, titulo, tipo, ruta, file_path, url_youtube, publico, subido_por, creado_en FROM recursos WHERE id = :id")
        existing = db.execute(select_sql, {"id": recurso_id}).mappings().fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        allowed = {"titulo", "tipo", "ruta", "file_path", "url_youtube", "publico", "subido_por"}
        updates = {k: v for k, v in data.items() if k in allowed}

        if not updates:
            return {
                "id": existing["id"],
                "titulo": existing["titulo"],
                "tipo": existing["tipo"],
                "ruta": existing["ruta"],
                "file_path": existing["file_path"],
                "url_youtube": existing["url_youtube"],
                "publico": bool(existing["publico"]) if existing["publico"] is not None else False,
                "subido_por": existing["subido_por"],
                "creado_en": str(existing["creado_en"]) if existing["creado_en"] is not None else None
            }

        set_fragments = []
        params = {"id": recurso_id}
        idx = 0
        for k, v in updates.items():
            idx += 1
            key = f"v{idx}"
            set_fragments.append(f"{k} = :{key}")
            params[key] = v
        set_sql = ", ".join(set_fragments)
        update_sql = text(f"UPDATE recursos SET {set_sql} WHERE id = :id RETURNING id, titulo, tipo, ruta, file_path, url_youtube, publico, subido_por, creado_en")
        row = db.execute(update_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo actualizar el recurso")
        return {
            "id": row["id"],
            "titulo": row["titulo"],
            "tipo": row["tipo"],
            "ruta": row["ruta"],
            "file_path": row["file_path"],
            "url_youtube": row["url_youtube"],
            "publico": bool(row["publico"]) if row["publico"] is not None else False,
            "subido_por": row["subido_por"],
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        logger.exception("actualizar_recurso: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("actualizar_recurso: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al actualizar recurso")

@app.delete("/recursos/{recurso_id}", response_model=dict)
def eliminar_recurso(recurso_id: int = Path(...), db=Depends(obtener_bd)):
    try:
        select_sql = text("SELECT ruta, file_path FROM recursos WHERE id = :id")
        existing = db.execute(select_sql, {"id": recurso_id}).mappings().fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = existing["ruta"]
        file_path = existing["file_path"]
        if file_path:
            try:
                delete_file_from_supabase(file_path)
            except HTTPException:
                logger.exception("eliminar_recurso: fallo al eliminar archivo en supabase")

        delete_sql = text("DELETE FROM recursos WHERE id = :id")
        db.execute(delete_sql, {"id": recurso_id})
        db.commit()
        return {"ok": True}
    except OperationalError:
        logger.exception("eliminar_recurso: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("eliminar_recurso: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al eliminar recurso")

# ---------- storage endpoints ----------
@app.post("/recursos/upload", response_model=dict)
async def upload_recurso_file(file: UploadFile = File(...)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    try:
        raw = await file.read()
        filename = f"{uuid.uuid4().hex}_{file.filename}"
        dest_path = filename

        public_url = upload_bytes_to_supabase(raw, dest_path)
        if not public_url or not isinstance(public_url, str):
            raise HTTPException(status_code=500, detail="No se obtuvo URL publica despues de subir el archivo.")
        return {"ruta": public_url, "file_path": dest_path}
    except HTTPException:
        raise
    except Exception:
        logger.exception("upload_recurso_file: unexpected")
        raise HTTPException(status_code=500, detail="Error interno al subir archivo")

@app.delete("/recursos/delete_file/{file_path:path}", response_model=dict)
def delete_file_endpoint(file_path: str = Path(..., description="Ruta relativa dentro del bucket (puede contener /)")):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    try:
        delete_file_from_supabase(file_path)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("delete_file_endpoint: unexpected")
        raise HTTPException(status_code=500, detail="Error interno al eliminar archivo")

@app.post("/recursos/upload_and_create", response_model=dict)
async def upload_and_create_recurso(
    titulo: str = Form(...),
    publico: Optional[bool] = Form(False),
    subido_por: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db = Depends(obtener_bd),
):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    if not titulo or titulo.strip() == "":
        raise HTTPException(status_code=400, detail="El campo 'titulo' es requerido.")

    nombre = None
    try:
        try:
            contenido = await file.read()
        except Exception as e:
            logger.exception("upload_and_create_recurso: no se pudo leer archivo: %s", e)
            raise HTTPException(status_code=500, detail=f"No se pudo leer el archivo: {str(e)}")

        nombre = f"{uuid.uuid4().hex}_{file.filename}"

        try:
            public = upload_bytes_to_supabase(contenido, nombre)
            if not public or not isinstance(public, str):
                raise HTTPException(status_code=500, detail="No se obtuvo URL publica despues de subir el archivo.")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("upload_and_create_recurso: error subiendo archivo: %s", e)
            raise HTTPException(status_code=500, detail=f"Error al subir archivo: {str(e)}")

        try:
            insert_sql = text("""
                INSERT INTO recursos (titulo, tipo, ruta, file_path, publico, subido_por)
                VALUES (:titulo, 'pdf', :ruta, :file_path, :publico, :subido_por)
                RETURNING id, titulo, tipo, ruta, file_path, publico, subido_por, creado_en
            """)
            params = {
                "titulo": titulo,
                "ruta": public,
                "file_path": nombre,
                "publico": publico,
                "subido_por": subido_por
            }
            row = db.execute(insert_sql, params).mappings().fetchone()
            db.commit()
            if not row:
                try:
                    delete_file_from_supabase(nombre)
                except Exception:
                    logger.exception("upload_and_create_recurso: fallo cleanup archivo")
                raise HTTPException(status_code=500, detail="No se pudo crear el registro en la base de datos.")

            respuesta = {
                "id": row["id"],
                "titulo": row["titulo"],
                "tipo": row["tipo"],
                "ruta": row["ruta"],
                "file_path": row["file_path"],
                "publico": bool(row["publico"]) if row["publico"] is not None else False,
                "subido_por": row["subido_por"],
                "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
            }
            return respuesta

        except OperationalError:
            logger.exception("upload_and_create_recurso: OperationalError")
            try:
                if nombre:
                    delete_file_from_supabase(nombre)
            except Exception:
                logger.exception("upload_and_create_recurso: fallo cleanup OperationalError")
            db.rollback()
            raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("upload_and_create_recurso: unexpected: %s", e)
            try:
                if nombre:
                    delete_file_from_supabase(nombre)
            except Exception:
                logger.exception("upload_and_create_recurso: fallo cleanup unexpected")
            try:
                db.rollback()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Error interno al crear el recurso: {str(e)}")
    finally:
        try:
            contenido = None
        except Exception:
            pass

# ---------- pestanas CRUD ----------
@app.get("/pestanas", response_model=List[PestanaOut])
def listar_pestanas(db = Depends(obtener_bd)):
    try:
        q = text("SELECT id, nombre, orden, creado_en FROM pestanas ORDER BY id")
        rows = db.execute(q).mappings().all()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "nombre": r["nombre"],
                "orden": list(r["orden"]) if r["orden"] is not None else [],
                "creado_en": str(r["creado_en"]) if r["creado_en"] is not None else None
            })
        return result
    except OperationalError:
        logger.exception("listar_pestanas: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        logger.exception("listar_pestanas: unexpected")
        raise HTTPException(status_code=500, detail="Error interno al listar pestanas")

@app.post("/pestanas", response_model=PestanaOut, status_code=201)
def crear_pestana(payload: PestanaCreate = Body(...), db = Depends(obtener_bd)):
    try:
        insert_sql = text("""
            INSERT INTO pestanas (nombre, orden)
            VALUES (:nombre, :orden)
            RETURNING id, nombre, orden, creado_en
        """)
        params = {
            "nombre": payload.nombre,
            "orden": payload.orden or []
        }
        row = db.execute(insert_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo crear la pestana")
        return {
            "id": row["id"],
            "nombre": row["nombre"],
            "orden": list(row["orden"]) if row["orden"] is not None else [],
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        logger.exception("crear_pestana: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("crear_pestana: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al crear pestana")

@app.put("/pestanas/{pestana_id}", response_model=PestanaOut)
def actualizar_pestana(pestana_id: int = Path(...), payload: PestanaUpdate = Body(...), db = Depends(obtener_bd)):
    try:
        select_sql = text("SELECT id, nombre, orden, creado_en FROM pestanas WHERE id = :id")
        existing = db.execute(select_sql, {"id": pestana_id}).mappings().fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Pestana no encontrada")

        # obtener datos enviados (compatible pydantic v1 y v2)
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        data.pop("id", None)
        data.pop("creado_en", None)

        allowed = {"nombre", "orden"}
        updates = {k: v for k, v in data.items() if k in allowed}

        if not updates:
            # nada que actualizar; devolver la fila existente
            return {
                "id": existing["id"],
                "nombre": existing["nombre"],
                "orden": list(existing["orden"]) if existing["orden"] is not None else [],
                "creado_en": str(existing["creado_en"]) if existing["creado_en"] is not None else None
            }

        set_fragments = []
        params = {"id": pestana_id}
        idx = 0
        for k, v in updates.items():
            idx += 1
            key = f"v{idx}"
            set_fragments.append(f"{k} = :{key}")
            params[key] = v

        set_sql = ", ".join(set_fragments)
        update_sql = text(f"UPDATE pestanas SET {set_sql} WHERE id = :id RETURNING id, nombre, orden, creado_en")
        row = db.execute(update_sql, params).mappings().fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo actualizar la pestana")
        return {
            "id": row["id"],
            "nombre": row["nombre"],
            "orden": list(row["orden"]) if row["orden"] is not None else [],
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        logger.exception("actualizar_pestana: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("actualizar_pestana: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al actualizar pestana")

@app.delete("/pestanas/{pestana_id}", response_model=dict)
def eliminar_pestana(pestana_id: int = Path(...), db = Depends(obtener_bd)):
    try:
        select_sql = text("SELECT id FROM pestanas WHERE id = :id")
        existing = db.execute(select_sql, {"id": pestana_id}).mappings().fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Pestana no encontrada")

        delete_sql = text("DELETE FROM pestanas WHERE id = :id")
        db.execute(delete_sql, {"id": pestana_id})
        db.commit()
        return {"ok": True}
    except OperationalError:
        logger.exception("eliminar_pestana: OperationalError")
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        logger.exception("eliminar_pestana: unexpected")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al eliminar pestana")

# ---------- login ----------
@app.post("/login", response_model=RespuestaUsuario)
def login(datos: PeticionInicio, db=Depends(obtener_bd)):
    try:
        rol_value = datos.rol.value if hasattr(datos.rol, "value") else datos.rol

        usuario = (
            db.query(Usuario)
            .filter(Usuario.codigo == datos.codigo, Usuario.rol == rol_value)
            .first()
        )
    except OperationalError as e:
        logger.exception("login: OperationalError")
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        # si obtener_bd ya lanzo HTTPException (ej: 503) dejamos pasar
        raise
    except Exception:
        logger.exception("login: unexpected")
        raise HTTPException(status_code=500, detail="Error interno")

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if (rol_value or "").lower() == "profesor":
        if not datos.clave:
            raise HTTPException(status_code=401, detail="Clave requerida")

        stored = usuario.clave or ""
        verified = (datos.clave == stored)

        if not verified:
            raise HTTPException(status_code=401, detail="Clave incorrecta")

    return usuario

# -------------------------------------------------
# Incluir pdf_control router (import despues de inicializar supabase y helpers)
# -------------------------------------------------
try:
    from pdf_control import router as pdf_router  # type: ignore
    app.include_router(pdf_router)
    logger.info("pdf_control router incluido correctamente")
except Exception as e:
    logger.exception("No se pudo incluir pdf_control router: %s", e)

# run local (solo para debug)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("RenderApi:app", host="0.0.0.0", port=port)
