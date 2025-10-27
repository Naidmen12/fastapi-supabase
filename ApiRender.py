from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from db import obtener_bd, Base, motor
from models import Usuario
from schemas import PeticionInicio, RespuestaUsuario

app = FastAPI()

# Crear tablas al iniciar el servidor (manejo de errores si DB no disponible)
@app.on_event("startup")
def on_startup():
    try:
        Base.metadata.create_all(bind=motor)
        print("Tablas creadas / DB OK")
    except Exception as e:
        print("No se pudo conectar a la DB en startup:", e)

@app.get("/")
def raiz():
    return {"mensaje": "API funcionando correctamente"}

@app.post("/login", response_model=RespuestaUsuario)
def login(datos: PeticionInicio, db: Session = Depends(obtener_bd)):
    usuario = db.query(Usuario).filter(
        Usuario.codigo == datos.codigo,
        Usuario.rol == datos.rol
    ).first()

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Validación para profesores
    if usuario.rol == "profesor":
        if not datos.clave or datos.clave != usuario.clave_hash:
            raise HTTPException(status_code=401, detail="Clave incorrecta")

    return usuario