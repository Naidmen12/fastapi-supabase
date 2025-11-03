# ApiRender.py
import os
import traceback
from typing import List
from fastapi import FastAPI, Depends, HTTPException, Body, Path
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy import text
from db import obtener_bd
from models import Usuario, Recurso
from schemas import (
    PeticionInicio,
    RespuestaUsuario,
    UsuarioCreate,
    UsuarioUpdate,
    RecursoCreate,
    RecursoUpdate,
    RecursoOut,
)
from passlib.context import CryptContext

app = FastAPI(title="FastAPI - Identificacion (Render)")

# password context: usa bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# si quieres probar sin hashear pon SKIP_HASH=1 en las env de Render (solo para debug)
SKIP_HASH = os.environ.get("SKIP_HASH", "0") == "1"

# helper: bcrypt limita a 72 bytes -> truncar consistentemente antes de hashear / verificar
def normalize_password_for_bcrypt(raw: str) -> str:
    if raw is None:
        return raw
    if not isinstance(raw, str):
        raw = str(raw)
    b = raw.encode("utf-8", errors="ignore")
    if len(b) <= 72:
        return raw
    b2 = b[:72]
    truncated = b2.decode("utf-8", errors="ignore")
    return truncated

def looks_like_bcrypt_hash(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    return s.startswith("$2a$") or s.startswith("$2b$") or s.startswith("$2y$")

# raiz y health
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

# test DB
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

# usuarios
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
        if payload.clave:
            if SKIP_HASH:
                clave_to_store = payload.clave if isinstance(payload.clave, str) else str(payload.clave)
            else:
                try:
                    safe = normalize_password_for_bcrypt(payload.clave)
                    clave_to_store = pwd_context.hash(safe)
                except Exception as e:
                    traceback.print_exc()
                    raise HTTPException(status_code=500, detail=f"Error al procesar clave: {str(e)}")

        # payload.rol es un Enum (RoleEnum). Convertimos a string si tiene .value
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

        # compatibilidad pydantic v1/v2
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)

        # no permitir campos protegidos
        data.pop("id", None)
        data.pop("creado_en", None)

        # procesar clave si viene
        if "clave" in data and data["clave"] is not None:
            if SKIP_HASH:
                data["clave"] = data["clave"] if isinstance(data["clave"], str) else str(data["clave"])
            else:
                try:
                    safe = normalize_password_for_bcrypt(data["clave"])
                    data["clave"] = pwd_context.hash(safe)
                except Exception as e:
                    traceback.print_exc()
                    raise HTTPException(status_code=500, detail=f"Error al procesar clave: {str(e)}")

        # si cambian codigo, verificar colision
        if "codigo" in data and data["codigo"] != item.codigo:
            collision = db.query(Usuario).filter(Usuario.codigo == data["codigo"]).first()
            if collision:
                raise HTTPException(status_code=400, detail="Codigo ya en uso por otro usuario")

        # si rol viene como Enum, convertir a string
        if "rol" in data and data["rol"] is not None:
            data["rol"] = data["rol"].value if hasattr(data["rol"], "value") else data["rol"]

        # aplicar cambios
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

# recursos
@app.get("/recursos", response_model=List[RecursoOut])
def listar_recursos(db=Depends(obtener_bd)):
    try:
        rows = db.query(Recurso).order_by(Recurso.id).all()
        return rows
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

        nuevo = Recurso(
            titulo=payload.titulo,
            tipo=payload.tipo,
            ruta=payload.ruta,
            url_youtube=payload.url_youtube,
            subido_por=payload.subido_por,
            publico=payload.publico,
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        return nuevo
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
        item = db.query(Recurso).filter(Recurso.id == recurso_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_unset=True)
        else:
            data = payload.dict(exclude_unset=True)
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
        raise HTTPException(status_code=500, detail="Error interno al actualizar recurso")

@app.delete("/recursos/{recurso_id}", response_model=dict)
def eliminar_recurso(recurso_id: int = Path(...), db=Depends(obtener_bd)):
    try:
        item = db.query(Recurso).filter(Recurso.id == recurso_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")
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
        raise HTTPException(status_code=500, detail="Error interno al eliminar recurso")

# login
@app.post("/login", response_model=RespuestaUsuario)
def login(datos: PeticionInicio, db=Depends(obtener_bd)):
    try:
        # datos.rol puede ser un Enum; obtener el valor string si existe .value
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

    # solo los Profesores requieren clave
    if (rol_value or "").lower() == "profesor":
        if not datos.clave:
            raise HTTPException(status_code=401, detail="Clave requerida")

        stored = usuario.clave or ""
        verified = False

        if stored:
            # si el valor almacenado parece ser un hash bcrypt, verificar con pwd_context
            if looks_like_bcrypt_hash(stored):
                try:
                    candidate = normalize_password_for_bcrypt(datos.clave)
                    verified = pwd_context.verify(candidate, stored)
                except Exception:
                    # fallback a comparacion directa si algo falla
                    verified = (datos.clave == stored)
            else:
                # almacenado en texto plano, comparar directo
                verified = (datos.clave == stored)
        else:
            verified = False

        if not verified:
            raise HTTPException(status_code=401, detail="Clave incorrecta")

    return usuario

# run local (solo para debug)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("ApiRender:app", host="0.0.0.0", port=port)
