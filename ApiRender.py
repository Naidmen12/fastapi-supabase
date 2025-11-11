# ApiRender.py (modificado)
import os
import io
import uuid
import traceback
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Body, Path, File, UploadFile, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy import text

from db import obtener_bd
from models import Usuario  # mantenemos Usuario si lo usas
# NOTA: No importamos Recurso para evitar dependencias rígidas en el ORM
from schemas import (
    PeticionInicio,
    RespuestaUsuario,
    UsuarioCreate,
    UsuarioUpdate,
    RecursoCreate,
    RecursoUpdate,
    RecursoOut,
)

# Supabase
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    # No crash inmediato; pero los endpoints de storage lanzarán error si no están definidos.
    print("AVISO: SUPABASE_URL o SUPABASE_SERVICE_KEY no estan definidas. Define las variables de entorno.")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

BUCKET_NAME = os.environ.get("SUPABASE_BUCKET", "pdf")  # default 'pdf'

app = FastAPI(title="FastAPI - Identificacion (Render)")

# ---------- helpers ----------
def extract_path_from_supabase_public_url(url: str) -> Optional[str]:
    """
    Dada una URL pública de Supabase como:
    https://<project>.supabase.co/storage/v1/object/public/<bucket>/<path/to/file.pdf>
    devuelve '<path/to/file.pdf>' (ruta relativa dentro del bucket).
    """
    try:
        p = urlparse(url)
        path = p.path  # ej: /storage/v1/object/public/pdf/folder/file.pdf
        marker = "/storage/v1/object/public/"
        idx = path.find(marker)
        if idx == -1:
            return None
        after = path[idx + len(marker):]  # -> 'pdf/folder/file.pdf'
        # remover el prefijo del bucket si está (bucket + '/'), devolvemos la parte relativa al bucket
        if after.startswith(BUCKET_NAME + "/"):
            return after[len(BUCKET_NAME) + 1:]  # 'folder/file.pdf' o 'file.pdf'
        # si after startswith diferente bucket, devolver after (incluye bucket) -> el caller debe manejar
        return after
    except Exception:
        return None

def delete_file_from_supabase(file_path_in_bucket: str) -> dict:
    """
    file_path_in_bucket: ruta relativa dentro del bucket (ej: 'folder/file.pdf' o 'file.pdf')
    Retorna el response del SDK o lanza HTTPException en error.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        res = supabase.storage.from_(BUCKET_NAME).remove([file_path_in_bucket])
        # la librería a veces devuelve {'error': ...} o {'data': None, 'error': None}
        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=500, detail=f"Error al eliminar archivo en Supabase: {res['error']}")
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error interno al eliminar archivo en Supabase: {str(e)}")

def upload_bytes_to_supabase(file_bytes: bytes, dest_path_in_bucket: str) -> str:
    """
    Subir bytes a Supabase en dest_path_in_bucket (ej: 'folder/uuid_name.pdf').
    Retorna la URL pública generada.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        # la librería acepta file-like, así que usamos BytesIO
        file_obj = io.BytesIO(file_bytes)
        # upload: ruta relativa dentro del bucket
        res = supabase.storage.from_(BUCKET_NAME).upload(dest_path_in_bucket, file_obj)
        # comprobar errores
        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=500, detail=f"Error al subir a Supabase: {res['error']}")
        # obtener public url
        public = supabase.storage.from_(BUCKET_NAME).get_public_url(dest_path_in_bucket)
        # get_public_url puede devolver string o dict; manejamos ambos casos:
        if isinstance(public, dict):
            # buscar la clave que contenga 'public' o 'publicUrl'
            for k in ("publicUrl", "publicURL", "public_url", "url"):
                if k in public:
                    return public[k]
            # si no, intentar serializar
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

# ---------- usuarios (sin cambios) ----------
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

# ---------- recursos CRUD (modificados para usar columnas: titulo, tipo, ruta, url_youtube) ----------
@app.get("/recursos", response_model=List[RecursoOut])
def listar_recursos(db=Depends(obtener_bd)):
    try:
        # Usamos SQL directo para evitar problemas si el modelo ORM difiere de la tabla real
        q = text("SELECT id, titulo, tipo, ruta, url_youtube, creado_en FROM recursos ORDER BY id")
        rows = db.execute(q).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "titulo": r["titulo"],
                "tipo": r["tipo"],
                "ruta": r["ruta"],
                "url_youtube": r["url_youtube"],
                "creado_en": str(r["creado_en"]) if r["creado_en"] is not None else None
            })
        return result
    except OperationalError:
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al listar recursos")

@app.post("/recursos", response_model=RecursoOut)
def crear_recurso(payload: RecursoCreate = Body(...), db=Depends(obtener_bd)):
    try:
        if not payload.ruta and not payload.url_youtube:
            raise HTTPException(status_code=400, detail="Debe proporcionar 'ruta' (archivo) o 'url_youtube'")
        if payload.url_youtube:
            if "youtube.com" not in payload.url_youtube and "youtu.be" not in payload.url_youtube:
                raise HTTPException(status_code=400, detail="url_youtube no parece una URL de YouTube valida")

        # Insertamos solo las columnas que definiste en la tabla: titulo, tipo, ruta, url_youtube
        insert_sql = text("""
            INSERT INTO recursos (titulo, tipo, ruta, url_youtube)
            VALUES (:titulo, :tipo, :ruta, :url_youtube)
            RETURNING id, titulo, tipo, ruta, url_youtube, creado_en
        """)
        params = {
            "titulo": payload.titulo,
            "tipo": payload.tipo,
            "ruta": payload.ruta,
            "url_youtube": payload.url_youtube
        }
        row = db.execute(insert_sql, params).fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo crear el recurso")
        return {
            "id": row["id"],
            "titulo": row["titulo"],
            "tipo": row["tipo"],
            "ruta": row["ruta"],
            "url_youtube": row["url_youtube"],
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al crear recurso")

@app.put("/recursos/{recurso_id}", response_model=RecursoOut)
def actualizar_recurso(
    recurso_id: int = Path(...), payload: RecursoUpdate = Body(...), db=Depends(obtener_bd)
):
    try:
        # Verificar existencia
        select_sql = text("SELECT id, titulo, tipo, ruta, url_youtube, creado_en FROM recursos WHERE id = :id")
        existing = db.execute(select_sql, {"id": recurso_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        # Obtener campos enviados
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        # Solo permitimos actualizar las columnas: titulo, tipo, ruta, url_youtube
        allowed = {"titulo", "tipo", "ruta", "url_youtube"}
        updates = {k: v for k, v in data.items() if k in allowed}

        if not updates:
            # nada que actualizar; devolvemos el recurso actual
            return {
                "id": existing["id"],
                "titulo": existing["titulo"],
                "tipo": existing["tipo"],
                "ruta": existing["ruta"],
                "url_youtube": existing["url_youtube"],
                "creado_en": str(existing["creado_en"]) if existing["creado_en"] is not None else None
            }

        # Construir UPDATE dinámico
        set_fragments = []
        params = {"id": recurso_id}
        idx = 0
        for k, v in updates.items():
            idx += 1
            key = f"v{idx}"
            set_fragments.append(f"{k} = :{key}")
            params[key] = v
        set_sql = ", ".join(set_fragments)
        update_sql = text(f"UPDATE recursos SET {set_sql} WHERE id = :id RETURNING id, titulo, tipo, ruta, url_youtube, creado_en")
        row = db.execute(update_sql, params).fetchone()
        db.commit()
        if not row:
            raise HTTPException(status_code=500, detail="No se pudo actualizar el recurso")
        return {
            "id": row["id"],
            "titulo": row["titulo"],
            "tipo": row["tipo"],
            "ruta": row["ruta"],
            "url_youtube": row["url_youtube"],
            "creado_en": str(row["creado_en"]) if row["creado_en"] is not None else None
        }
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al actualizar recurso")

@app.delete("/recursos/{recurso_id}", response_model=dict)
def eliminar_recurso(recurso_id: int = Path(...), db=Depends(obtener_bd)):
    try:
        # obtener ruta si existe
        select_sql = text("SELECT ruta FROM recursos WHERE id = :id")
        existing = db.execute(select_sql, {"id": recurso_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")

        ruta = existing["ruta"]
        # intentar eliminar archivo en Supabase si la ruta apunta a storage
        if ruta:
            file_path = extract_path_from_supabase_public_url(ruta)
            if file_path:
                # file_path es la ruta relativa dentro del bucket: 'folder/file.pdf' o 'file.pdf'
                try:
                    delete_file_from_supabase(file_path)
                except HTTPException:
                    # no abortamos la operación de DB si no se pudo borrar el archivo; lo registramos
                    traceback.print_exc()

        delete_sql = text("DELETE FROM recursos WHERE id = :id")
        db.execute(delete_sql, {"id": recurso_id})
        db.commit()
        return {"ok": True}
    except OperationalError:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible")
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno al eliminar recurso")

# ---------- endpoints para manejo directo de archivos (Supabase Storage) ----------
@app.post("/recursos/upload", response_model=dict)
async def upload_recurso_file(file: UploadFile = File(...)):
    """
    Recibe un multipart file, lo sube al bucket de Supabase y devuelve {'ruta': '<public_url>'}.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    try:
        raw = await file.read()
        # crear nombre único (puedes añadir subfolders si quieres)
        filename = f"{uuid.uuid4()}_{file.filename}"
        dest_path = filename  # dentro del bucket root; si quieres carpeta: 'uploads/' + filename

        public_url = upload_bytes_to_supabase(raw, dest_path)
        return {"ruta": public_url, "file_path": dest_path}
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno al subir archivo")

@app.delete("/recursos/delete_file/{file_path}", response_model=dict)
def delete_file_endpoint(file_path: str = Path(...)):
    """
    Elimina un archivo dentro del bucket. file_path es la ruta relativa dentro del bucket
    (por ejemplo 'folder/archivo.pdf' o 'archivo.pdf').
    """
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
