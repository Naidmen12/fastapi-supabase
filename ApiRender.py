# ApiRender.py
import os
import traceback
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.exc import OperationalError
from db import obtener_bd, engine, SessionLocal
from models import Usuario
from schemas import PeticionInicio, RespuestaUsuario

app = FastAPI(title="FastAPI - Identificación (Railway)")

# Endpoint raíz
@app.get("/")
def raiz():
    return {"mensaje": "API funcionando correctamente"}

# Endpoint de test de la DB (temporal): verifica la conectividad desde Railway
@app.get("/test-db")
def test_db():
    db = None
    try:
        db = next(obtener_bd())
        # consulta ligera para comprobar la conexión
        row = db.execute("SELECT 1").fetchone()
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

# Endpoint login
@app.post("/login", response_model=RespuestaUsuario)
def login(datos: PeticionInicio, db = Depends(obtener_bd)):
    try:
        usuario = db.query(Usuario).filter(
            Usuario.codigo == datos.codigo,
            Usuario.rol == datos.rol
        ).first()
    except OperationalError as e:
        # error de conexión con la BD (timed out, network unreachable, etc.)
        print("OperationalError al consultar la BD:", e)
        traceback.print_exc()
        raise HTTPException(
            status_code=503,
            detail="Servicio de base de datos no disponible. Intente más tarde."
        )
    except Exception as e:
        print("Error al consultar la BD:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error interno")

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if usuario.rol == "profesor":
        if not datos.clave or datos.clave != usuario.clave_hash:
            raise HTTPException(status_code=401, detail="Clave incorrecta")

    return usuario

# Run local (solo para debug)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("ApiRender:app", host="0.0.0.0", port=port)
