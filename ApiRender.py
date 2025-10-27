# ApiRender.py
import os
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.exc import OperationalError
from db import obtener_bd, Base, motor
from models import Usuario
from schemas import PeticionInicio, RespuestaUsuario

app = FastAPI()

@app.on_event("startup")
def on_startup():
    try:
        Base.metadata.create_all(bind=motor)
        print("Tablas creadas / DB OK")
    except Exception as e:
        # Registra y continúa; la app responderá pero endpoints que usen DB devolverán 503.
        print("No se pudo conectar a la DB en startup:", e)

@app.get("/")
def raiz():
    return {"mensaje": "API funcionando correctamente"}

@app.post("/login", response_model=RespuestaUsuario)
def login(datos: PeticionInicio, db = Depends(obtener_bd)):
    try:
        usuario = db.query(Usuario).filter(
            Usuario.codigo == datos.codigo,
            Usuario.rol == datos.rol
        ).first()
    except OperationalError as e:
        # DB inaccesible: devolver 503 para que el cliente reintente
        print("OperationalError al consultar la BD:", e)
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible. Intente más tarde.")

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Para producción: usar verify de passlib contra el hash (aquí asumo texto para pruebas)
    if usuario.rol == "profesor":
        if not datos.clave or datos.clave != usuario.clave_hash:
            raise HTTPException(status_code=401, detail="Clave incorrecta")

    return usuario

# Si ejecutas uvicorn directamente desde aquí, respeta $PORT
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("ApiRender:app", host="0.0.0.0", port=port)
