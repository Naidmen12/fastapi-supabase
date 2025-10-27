import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")  # La defines en Render

motor = create_engine(DATABASE_URL, pool_pre_ping=True)
SesionLocal = sessionmaker(autocommit=False, autoflush=False, bind=motor)
Base = declarative_base()

def obtener_bd():
    db = SesionLocal()
    try:
        yield db
    finally:
        db.close()