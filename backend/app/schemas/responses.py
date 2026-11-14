"""
Esquemas de respuestas estándar de la API.
"""
from typing import Optional, Any
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Detalle de un error."""

    error: str = Field(..., description="Tipo de error")
    detail: str = Field(..., description="Descripción del error")
    field: Optional[str] = Field(None, description="Campo que originó el error")


class APIErrorResponse(BaseModel):
    """Respuesta estándar para errores de la API."""

    success: bool = Field(False)
    error: str = Field(..., description="Tipo de error")
    detail: str = Field(..., description="Mensaje detallado")
    status_code: Optional[int] = Field(None, description="Código HTTP")
    errors: Optional[list[ErrorDetail]] = Field(None, description="Lista de errores específicos")


class APISuccessResponse(BaseModel):
    """Respuesta estándar para éxito de la API."""

    success: bool = Field(True)
    message: str = Field(..., description="Mensaje de éxito")
    data: Optional[Any] = Field(None, description="Datos de respuesta")