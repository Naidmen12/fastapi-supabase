# schemas.py
from pydantic import BaseModel
from typing import Optional

class PeticionInicio(BaseModel):
    rol: str
    codigo: str                   # string para conservar ceros a la izquierda
    clave: Optional[str] = None

class RespuestaUsuario(BaseModel):
    id: int
    rol: str
    codigo: str

    class Config:
        orm_mode = True
