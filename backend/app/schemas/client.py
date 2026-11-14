"""Schemas HTTP para clientes frecuentes."""
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.rut import clean_rut, validate_rut


EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rut: str = Field(..., min_length=1, max_length=20)
    razon_social: str = Field(..., min_length=1, max_length=100)
    nombre_fantasia: str | None = Field(None, max_length=100)
    giro: str | None = Field(None, max_length=80)
    direccion: str = Field(..., min_length=1, max_length=80)
    region: int = Field(..., ge=1, le=16)
    comuna: str = Field(..., min_length=1, max_length=80)
    email: str | None = Field(None, max_length=254)
    detalle_default: list[dict[str, Any]] | None = None

    @field_validator("rut")
    @classmethod
    def validate_and_normalize_rut(cls, value: str) -> str:
        if not validate_rut(value):
            raise ValueError(f"RUT inválido: {value}")
        return clean_rut(value)

    @field_validator("razon_social", "nombre_fantasia", "direccion", "comuna", "giro")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("El texto no puede estar vacío")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not EMAIL_PATTERN.match(cleaned):
            raise ValueError(f"Email inválido: {value}")
        return cleaned


class ClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rut: str | None = Field(None, min_length=1, max_length=20)
    razon_social: str | None = Field(None, min_length=1, max_length=100)
    nombre_fantasia: str | None = Field(None, max_length=100)
    giro: str | None = Field(None, max_length=80)
    direccion: str | None = Field(None, min_length=1, max_length=80)
    region: int | None = Field(None, ge=1, le=16)
    comuna: str | None = Field(None, min_length=1, max_length=80)
    email: str | None = Field(None, max_length=254)
    detalle_default: list[dict[str, Any]] | None = None
    activo: bool | None = None

    @field_validator("rut")
    @classmethod
    def validate_and_normalize_rut(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not validate_rut(value):
            raise ValueError(f"RUT inválido: {value}")
        return clean_rut(value)

    @field_validator("razon_social", "nombre_fantasia", "direccion", "comuna", "giro")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("El texto no puede estar vacío")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not EMAIL_PATTERN.match(cleaned):
            raise ValueError(f"Email inválido: {value}")
        return cleaned


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    razon_social: str
    nombre_fantasia: str | None
    giro: str | None
    direccion: str
    region: int
    comuna: str
    email: str | None
    detalle_default: list[dict[str, Any]] | None
    activo: bool
    created_at: datetime
    updated_at: datetime
