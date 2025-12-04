#schemas.py
from pydantic import BaseModel
from typing import Optional, List

from enum import Enum as PyEnum

class RoleEnum(str, PyEnum):
    Estudiante = "Estudiante"
    Profesor = "Profesor"

class PeticionInicio(BaseModel):
    rol: RoleEnum
    codigo: str
    clave: Optional[str] = None

class RespuestaUsuario(BaseModel):
    id: int
    rol: str
    codigo: str
    clave: Optional[str] = None
    model_config = {"from_attributes": True}

class UsuarioCreate(BaseModel):
    rol: RoleEnum
    codigo: str
    clave: Optional[str] = None

class UsuarioUpdate(BaseModel):
    rol: Optional[RoleEnum] = None
    codigo: Optional[str] = None
    clave: Optional[str] = None

# Recurso
class RecursoBase(BaseModel):
    titulo: str
    tipo: str
    ruta: Optional[str] = None
    file_path: Optional[str] = None
    url_youtube: Optional[str] = None
    youtube_id: Optional[str] = None
    subido_por: Optional[int] = None
    publico: Optional[bool] = False

class RecursoCreate(RecursoBase):
    pass

class RecursoUpdate(BaseModel):
    titulo: Optional[str] = None
    tipo: Optional[str] = None
    ruta: Optional[str] = None
    file_path: Optional[str] = None
    url_youtube: Optional[str] = None
    youtube_id: Optional[str] = None
    subido_por: Optional[int] = None
    publico: Optional[bool] = None

class RecursoOut(RecursoBase):
    id: int
    creado_en: Optional[str] = None
    model_config = {"from_attributes": True}

# ------------------------------------------------
# Pestanas (nueva entidad para ordenar recursos)
# ------------------------------------------------
class PestanaBase(BaseModel):
    nombre: str
    orden: Optional[List[int]] = []

class PestanaCreate(PestanaBase):
    pass

class PestanaUpdate(BaseModel):
    nombre: Optional[str] = None
    orden: Optional[List[int]] = None

class PestanaOut(PestanaBase):
    id: int
    creado_en: Optional[str] = None
    model_config = {"from_attributes": True}
