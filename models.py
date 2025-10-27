from sqlalchemy import Column, Integer, String, Boolean, Text, BigInteger, ForeignKey, TIMESTAMP
from sqlalchemy.sql import func
from db import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    rol = Column(String(16), nullable=False)
    codigo = Column(BigInteger, unique=True, nullable=False)
    clave_hash = Column(Text, nullable=True)
    creado_en = Column(TIMESTAMP(timezone=True), server_default=func.now())

class Recurso(Base):
    __tablename__ = "recursos"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(Text, nullable=False)
    tipo = Column(String(16), nullable=False)
    ruta = Column(Text, nullable=True)
    url_youtube = Column(Text, nullable=True)
    subido_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    publico = Column(Boolean, default=False)
    creado_en = Column(TIMESTAMP(timezone=True), server_default=func.now())