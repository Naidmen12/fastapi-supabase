# models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, TIMESTAMP
from sqlalchemy.sql import func
from db import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)            # 1,2,3...
    rol = Column(String(32), nullable=False)                      # 'Estudiante' / 'Profesor'
    codigo = Column(String(64), unique=True, nullable=False, index=True)  # TEXT para mantener ceros
    clave = Column(Text, nullable=True)                           # contraseña en texto plano
    creado_en = Column(TIMESTAMP(timezone=True), server_default=func.now())

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
