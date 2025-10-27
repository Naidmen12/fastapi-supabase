import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está definida. Defínela en las Environment Variables (Render).")

# Forzar sslmode=require si no viene en la URL (útil para conexiones remotas)
if "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL + "&sslmode=require"
    else:
        DATABASE_URL = DATABASE_URL + "?sslmode=require"

# Crear motor con pool_pre_ping para recuperar conexiones muertas
motor = create_engine(DATABASE_URL, pool_pre_ping=True)
SesionLocal = sessionmaker(autocommit=False, autoflush=False, bind=motor)
Base = declarative_base()

def obtener_bd():
    db = SesionLocal()
    try:
        yield db
    finally:
        db.close()
