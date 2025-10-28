# db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Railway (o cualquier PaaS) suele proporcionar DATABASE_URL en las env vars
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está definida. Configure la variable de entorno en Railway.")

# Forzar sslmode=require si no viene en la URL (útil para conexiones remotas)
if "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

# Crear motor con pool_pre_ping y timeout de conexión (falla rápido si no conecta)
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5}
)

# Session factory y Base para modelos
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia para FastAPI
def obtener_bd():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
