# schemas.py (añadir estas clases)
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

# --- Usuario create/update ---
class UsuarioCreate(BaseModel):
    rol: str
    codigo: str
    clave: Optional[str] = None

class UsuarioUpdate(BaseModel):
    rol: Optional[str] = None
    codigo: Optional[str] = None
    clave: Optional[str] = None

# --- Recurso (ya lo tenías) ---
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
