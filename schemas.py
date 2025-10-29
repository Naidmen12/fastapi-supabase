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

    # En Pydantic v2: use model_config para 'from_attributes' (equivalente a orm_mode=True)
    model_config = {"from_attributes": True}
