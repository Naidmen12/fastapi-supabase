# ApiRender.py
import os
import io
import uuid
import traceback
import tempfile
from typing import List, Optional
from urllib.parse import urlparse, unquote

from fastapi import FastAPI, Depends, HTTPException, Body, Path, File, UploadFile, Form
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy import text

from db import obtener_bd
from models import Usuario
from schemas import (
    PeticionInicio,
    RespuestaUsuario,
    UsuarioCreate,
    UsuarioUpdate,
    RecursoCreate,
    RecursoUpdate,
    RecursoOut,
)

# Intentamos importar el cliente de Supabase
try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("AVISO: SUPABASE_URL o SUPABASE_SERVICE_KEY no estan definidas. Define las variables de entorno.")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY and create_client is not None:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception:
        supabase = None
        traceback.print_exc()

BUCKET_NAME = os.environ.get("SUPABASE_BUCKET", "pdf")  # default 'pdf'

app = FastAPI(title="FastAPI - Identificacion (Render)")

# ---- Handler global para debug (opcional) ----
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    traceback.print_exc()
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
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        res = supabase.storage.from_(BUCKET_NAME).remove([file_path_in_bucket])
        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=500, detail=f"Error al eliminar archivo en Supabase: {res['error']}")
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error interno al eliminar archivo en Supabase: {str(e)}")


def upload_bytes_to_supabase(file_bytes: bytes, dest_path_in_bucket: str) -> str:
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        try:
            res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, file_bytes)
        except TypeError:
            try:
                file_obj = io.BytesIO(file_bytes)
                res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, file_obj)
            except Exception:
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
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error interno al subir archivo: {str(e)}")


# ---------- rutas base / health ----------
@app.get("/", include_in_schema=False)
def raiz_get():
    return {"mensaje": "API funcionando correctamente"}


@app.head("/", include_in_schema=False)
def raiz_head():
    return PlainTextResponse(status_code=200)


@app.get("/health", response_class=PlainTextResponse)
async def health():
    return PlainTextResponse("OK", status_code=200)


@app.head("/health")
async def health_head():
    return PlainTextResponse(status_code=200)


# ---------- test db ----------
@app.get("/test-db")
def test_db():
    db = None
    try:
        db = next(obtener_bd())
        row = db.execute(text("SELECT 1")).fetchone()
        return {"ok": True, "result": row[0] if row else None}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


# ---------- usuarios (sin cambios, siguen usando Depends) ----------
@app.get("/usuarios", response_model=List[RespuestaUsuario])
def listar_usuarios(db=Depends(obtener_bd)):
    try:
        usuarios = db.query(Usuario).order_by(Usuario.id).all()
        return usuarios
    except OperationalError:
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al listar usuarios")


@app.post("/usuarios", response_model=RespuestaUsuario, status_code=201)
def crear_usuario(payload: UsuarioCreate = Body(...), db=Depends(obtener_bd)):
    try:
        existing = db.query(Usuario).filter(Usuario.codigo == payload.codigo).first()
        if existing:
            raise HTTPException(status_code=400, detail="Codigo ya registrado")

        clave_to_store = None
        if payload.clave is not None:
            clave_to_store = payload.clave if isinstance(payload.clave, str) else str(payload.clave)

        rol_value = payload.rol.value if hasattr(payload.rol, "value") else payload.rol

        nuevo = Usuario(
            rol=rol_value,
            codigo=payload.codigo,
            clave=clave_to_store,
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        return nuevo
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except IntegrityError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=400, detail="Codigo duplicado")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al crear usuario")


@app.put("/usuarios/{usuario_id}", response_model=RespuestaUsuario)
def actualizar_usuario(
    usuario_id: int = Path(...),
    payload: UsuarioUpdate = Body(...),
    db=Depends(obtener_bd),
):
    try:
        item = db.query(Usuario).filter(Usuario.id == usuario_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        data.pop("id", None)
        data.pop("creado_en", None)

        if "clave" in data and data["clave"] is not None:
            data["clave"] = data["clave"] if isinstance(data["clave"], str) else str(data["clave"])

        if "codigo" in data and data["codigo"] != item.codigo:
            collision = db.query(Usuario).filter(Usuario.codigo == data["codigo"]).first()
            if collision:
                raise HTTPException(status_code=400, detail="Codigo ya en uso por otro usuario")

        if "rol" in data and data["rol"] is not None:
            data["rol"] = data["rol"].value if hasattr(data["rol"], "value") else data["rol"]

        for k, v in data.items():
            if hasattr(item, k):
                setattr(item, k, v)

        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al actualizar usuario")


@app.delete("/usuarios/{usuario_id}", response_model=dict)
def eliminar_usuario(usuario_id: int = Path(...), db=Depends(obtener_bd)):
    try:
        item = db.query(Usuario).filter(Usuario.id == usuario_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        db.delete(item)
        db.commit()
        return {"ok": True}
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al eliminar usuario")


# ---------- recursos CRUD ----------
@app.get("/recursos", response_model=List[RecursoOut])
def listar_recursos():
    db = None
    try:
        db = next(obtener_bd())
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
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al listar recursos")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


@app.post("/recursos", response_model=RecursoOut)
def crear_recurso(payload: RecursoCreate = Body(...)):
    db = None
    try:
        db = next(obtener_bd())
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
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al crear recurso")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


@app.put("/recursos/{recurso_id}", response_model=RecursoOut)
def actualizar_recurso(recurso_id: int = Path(...), payload: RecursoUpdate = Body(...)):
    db = None
    try:
        db = next(obtener_bd())
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
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al actualizar recurso")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


@app.delete("/recursos/{recurso_id}", response_model=dict)
def eliminar_recurso(recurso_id: int = Path(...)):
    db = None
    try:
        db = next(obtener_bd())
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
                traceback.print_exc()

        delete_sql = text("DELETE FROM recursos WHERE id = :id")
        db.execute(delete_sql, {"id": recurso_id})
        db.commit()
        return {"ok": True}
    except OperationalError:
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        if db:
            db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al eliminar recurso")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


# ---------- endpoints para manejo directo de archivos (Supabase Storage) ----------
@app.post("/recursos/upload", response_model=dict)
async def upload_recurso_file(file: UploadFile = File(...)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
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
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al subir archivo")


# endpoint que acepta file_path con slashes
@app.delete("/recursos/delete_file/{file_path:path}", response_model=dict)
def delete_file_endpoint(file_path: str = Path(..., description="Ruta relativa dentro del bucket (puede contener /)")):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        delete_file_from_supabase(file_path)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al eliminar archivo")


# ---------- upload + create (opcional, util para cliente) ----------
@app.post("/recursos/upload_and_create", response_model=dict)
async def upload_and_create_recurso(
    titulo: str = Form(...),
    publico: Optional[bool] = Form(False),
    subido_por: Optional[int] = Form(None),
    file: UploadFile = File(...),
):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no esta configurado en el servidor.")
    if not titulo or titulo.strip() == "":
        raise HTTPException(status_code=400, detail="El campo 'titulo' es requerido.")

    db = None
    nombre = None
    try:
        # leer bytes
        try:
            contenido = await file.read()
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"No se pudo leer el archivo: {str(e)}")

        # generar nombre en bucket
        nombre = f"{uuid.uuid4().hex}_{file.filename}"

        # subir al storage
        try:
            public = upload_bytes_to_supabase(contenido, nombre)
            if not public or not isinstance(public, str):
                raise HTTPException(status_code=500, detail="No se obtuvo URL publica despues de subir el archivo.")
        except HTTPException:
            raise
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error al subir archivo: {str(e)}")

        # insertar en DB
        try:
            db = next(obtener_bd())
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
                    traceback.print_exc()
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

        except HTTPException:
            raise
        except Exception as e:
            traceback.print_exc()
            try:
                if nombre:
                    delete_file_from_supabase(nombre)
            except Exception:
                traceback.print_exc()
            try:
                if db:
                    db.rollback()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Error interno al crear el recurso: {str(e)}")
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass
        try:
            contenido = None
        except Exception:
            pass


# ---------- login (sin cambios) ----------
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
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        traceback.print_exc()
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


# run local (solo para debug)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("ApiRender:app", host="0.0.0.0", port=port)
