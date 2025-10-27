# db.py
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

# Conect timeout rápido para fallos inmediatos desde Render si la DB no responde
# pool_pre_ping para sacar conexiones muertas del pool
# ajusta pool_size / max_overflow según necesidad
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},  # fallo rápido (segundos)
    pool_size=10,
    max_overflow=20,
    # echo=True,  # opcional para debug
)

SesionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def obtener_bd():
    db = SesionLocal()
    try:
        yield db
    finally:
        db.close()
