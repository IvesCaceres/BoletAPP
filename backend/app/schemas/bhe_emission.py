"""Schemas para historial de emisiones BHE."""
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class EmissionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    AWAITING_FINAL_CONFIRMATION = "awaiting_final_confirmation"
    FINALIZING = "finalizing"
    EMISSION_STATUS_UNKNOWN = "emission_status_unknown"


class BHEEmissionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int | None
    nombre_fantasia: str | None = None
    rut_receptor: str
    razon_social: str
    monto_total: int
    direccion: str
    region: int
    comuna: str
    detalle: list[dict[str, Any]]
    fecha_emision_solicitada: date
    status: EmissionStatus
    provider: str
    provider_status: int | None
    folio: int | None
    codigo_barras: str | None
    fecha_emision_provider: str | None
    error_message: str | None
    provider_message: str | None
    sii_resolved_recipient_name: str | None
    retention_mode: str | None
    created_at: datetime
    updated_at: datetime


class BHEEmissionDetail(BHEEmissionSummary):
    pdf_base64: str | None
