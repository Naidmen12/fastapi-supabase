# schemas.py
from pydantic.v1 import BaseModel  # usando compat v1 para orm_mode
from typing import Optional

class PeticionInicio(BaseModel):
    rol: str
    codigo: int
    clave: Optional[str] = None

class RespuestaUsuario(BaseModel):
    id: int
    rol: str
    codigo: int

    class Config:
        orm_mode = True
