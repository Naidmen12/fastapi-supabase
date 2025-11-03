# models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, TIMESTAMP
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from db import Base

# Definimos el ENUM de SQLAlchemy indicando create_type=False
# porque el tipo role_type ya existe en la base de datos (Postgres enum).
role_enum = PG_ENUM('Estudiante', 'Profesor', name='role_type', create_type=False)

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    rol = Column(role_enum, nullable=False)  # usamos el ENUM mapeado a Postgres
    codigo = Column(String(64), unique=True, nullable=False, index=True)
    clave = Column(Text, nullable=True)
    creado_en = Column(TIMESTAMP(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Usuario id={self.id} codigo={self.codigo} rol={self.rol}>"

class Recurso(Base):
    __tablename__ = "recursos"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(Text, nullable=False)
    tipo = Column(String(16), nullable=False)
    ruta = Column(Text, nullable=True)
    url_youtube = Column(Text, nullable=True)
    subido_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    publico = Column(Boolean, default=False)
    creado_en = Column(TIMESTAMP(timezone=True), server_default=func.now())
