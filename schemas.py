# schemas.py
from pydantic import BaseModel
from typing import Optional
from enum import Enum as PyEnum

class RoleEnum(str, PyEnum):
    Estudiante = "Estudiante"
    Profesor = "Profesor"

class PeticionInicio(BaseModel):
    rol: RoleEnum
    codigo: str
    clave: Optional[str] = None

# Respuesta de usuario incluyendo 'clave'
class RespuestaUsuario(BaseModel):
    id: int
    rol: str
    codigo: str
    clave: Optional[str] = None  # agregado para que la API devuelva la clave

    model_config = {"from_attributes": True}

# Usuario create/update
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
