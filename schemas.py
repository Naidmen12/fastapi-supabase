# schemas.py
from pydantic import BaseModel
from typing import Optional

class PeticionInicio(BaseModel):
    rol: str
    codigo: str
    clave: Optional[str] = None

class RespuestaUsuario(BaseModel):
    id: int
    rol: str
    codigo: str

    model_config = {"from_attributes": True}

# schemas para Recurso
class RecursoBase(BaseModel):
    titulo: str
    tipo: str
    ruta: Optional[str] = None
    url_youtube: Optional[str] = None
    subido_por: Optional[int] = None
    publico: Optional[bool] = False

class RecursoCreate(RecursoBase):
    pass

class RecursoUpdate(BaseModel):
    titulo: Optional[str] = None
    tipo: Optional[str] = None
    ruta: Optional[str] = None
    url_youtube: Optional[str] = None
    subido_por: Optional[int] = None
    publico: Optional[bool] = None

class RecursoOut(RecursoBase):
    id: int
    creado_en: Optional[str] = None

    model_config = {"from_attributes": True}
