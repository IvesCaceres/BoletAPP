"""Esquemas Pydantic para BHE emitidas mediante SII."""

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.retention import RetentionMode
from app.utils.rut import clean_rut, validate_rut


class BHEDetalle(BaseModel):
    descripcion: str = Field(..., min_length=1, max_length=100)
    cantidad: float = Field(default=1, gt=0)
    precio_unitario: float = Field(..., gt=0)

    @property
    def subtotal(self) -> Decimal:
        return (Decimal(str(self.cantidad)) * Decimal(str(self.precio_unitario))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def get_valor_int(self) -> int:
        return int(self.subtotal.quantize(Decimal("1")))


class BHERequest(BaseModel):
    """Datos de una nueva BHE; el provider activo es siempre SII."""

    model_config = ConfigDict(extra="forbid")

    retention_mode: RetentionMode = RetentionMode.ISSUER_HANDLES_PPM
    rut_receptor: str
    razon_social: str = Field("", max_length=100)
    giro: Optional[str] = Field(None, max_length=80)
    direccion: str = Field(..., min_length=1, max_length=80)
    region: Optional[int] = Field(None, ge=1, le=16)
    comuna: str = Field(..., min_length=1, max_length=80)
    email: Optional[str] = None
    detalle: list[BHEDetalle] = Field(..., min_length=1)
    fecha_emision: Optional[date] = None

    @field_validator("rut_receptor")
    @classmethod
    def validate_rut_receptor(cls, value: str) -> str:
        if not validate_rut(value):
            raise ValueError(f"RUT invalido: {value}")
        return clean_rut(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", value):
            raise ValueError(f"Email invalido: {value}")
        return value.lower().strip()

    @field_validator("razon_social")
    @classmethod
    def clean_optional_name(cls, value: str) -> str:
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value.strip())

    @field_validator("direccion", "comuna")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("String no puede estar vacio")
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)

    @model_validator(mode="after")
    def validate_fecha_no_futura(self) -> "BHERequest":
        if self.fecha_emision and self.fecha_emision > date.today():
            raise ValueError("fecha_emision no puede ser una fecha futura")
        return self

    @property
    def total(self) -> Decimal:
        return sum(item.subtotal for item in self.detalle)


class BHEResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: Optional[int | str] = None
    message: Optional[str] = None
    errors: Optional[object] = None
    folio: Optional[int] = None
    codigo_barras: Optional[str] = None
    fecha_emision: Optional[str] = None
    pdf_base64: Optional[str] = None
    provider: Optional[str] = None
    emission_id: Optional[int] = None
    preview_validated: bool = False
    recipient_rut_ok: Optional[bool] = None
    sii_resolved_recipient_name_present: Optional[bool] = None
    sii_resolved_recipient_name: Optional[str] = None


class FinalBHEConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool


class BHETestPayloadResponse(BaseModel):
    """Compatibility schema for the offline payload contract."""

    input_data: BHERequest
    final_payload: dict
    api_endpoint: str
