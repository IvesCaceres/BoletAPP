"""Schemas para batches mensuales de BHE SII."""

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.schemas.bhe import BHERequest
from app.schemas.retention import RetentionMode
from app.services.manual_date_policy import issue_date_error


class BatchStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"


class BatchItemStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    EMISSION_STATUS_UNKNOWN = "emission_status_unknown"


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    fecha_emision: date
    detalle_global: str | None = Field(None, max_length=200)
    retention_mode: RetentionMode = RetentionMode.ISSUER_HANDLES_PPM

    @field_validator("detalle_global")
    @classmethod
    def validate_global_detail(cls, value):
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("detalle_global no puede estar vacio")
        return cleaned


class ManualBatchCreate(BaseModel):
    """One-off BHE batch; items carry fiscal snapshots, never client rows."""
    model_config = ConfigDict(extra="forbid")
    items: list["BHERequest"] = Field(..., min_length=1)

    @field_validator("items")
    @classmethod
    def validate_single_detail(cls, items):
        if any(len(item.detalle) != 1 for item in items):
            raise ValueError("Cada boleta debe tener exactamente un detalle")
        if any(item.fecha_emision is None for item in items):
            raise ValueError("Cada boleta requiere fecha de emisión")
        if any(item.detalle[0].get_valor_int() != int(item.detalle[0].precio_unitario) for item in items):
            raise ValueError("El monto debe ser un entero en CLP")
        return items


class ManualBatchCommonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_mode: RetentionMode
    direccion: str = Field(..., min_length=1, max_length=80)
    region: int = Field(..., ge=1, le=16)
    comuna: str = Field(..., min_length=1, max_length=80)
    detalle: str = Field(..., min_length=1, max_length=100)
    fecha_emision: date

    @field_validator("direccion", "comuna", "detalle")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El campo no puede estar vacio")
        return value

    @field_validator("fecha_emision")
    @classmethod
    def validate_manual_date_range(cls, value: date) -> date:
        error = issue_date_error(value)
        if error:
            raise ValueError(error)
        return value


class BatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha_emision: date | None = None
    detalle_global: str | None = Field(None, max_length=200)
    retention_mode: RetentionMode | None = None

    @field_validator("detalle_global")
    @classmethod
    def validate_global_detail(cls, value):
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("detalle_global no puede estar vacio")
        return cleaned


class BatchItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: int = Field(..., gt=0)
    monto: StrictInt = Field(..., gt=0)
    detalle: list[dict[str, Any]] | str | None = None
    retention_mode_override: RetentionMode | None = None

    @field_validator("detalle")
    @classmethod
    def validate_detail(cls, value):
        if value is not None and ((isinstance(value, str) and not value.strip()) or len(value) == 0):
            raise ValueError("detalle no puede estar vacio")
        return value.strip() if isinstance(value, str) else value


class BatchItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monto: StrictInt | None = Field(None, gt=0)
    detalle: list[dict[str, Any]] | str | None = None
    retention_mode_override: RetentionMode | None = None

    @field_validator("detalle")
    @classmethod
    def validate_detail(cls, value):
        if value is not None and ((isinstance(value, str) and not value.strip()) or len(value) == 0):
            raise ValueError("detalle no puede estar vacio")
        return value.strip() if isinstance(value, str) else value


class BatchConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool


class BatchItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    client_id: int | None
    rut_receptor: str
    razon_social: str
    sii_resolved_recipient_name: str | None = None
    direccion: str
    region: int
    comuna: str
    giro: str | None = None
    email: str | None = None
    fecha_emision: date | None = None
    detalle: list[dict[str, Any]] | str
    retention_mode_override: RetentionMode | None
    monto: int
    status: BatchItemStatus
    bhe_emission_id: int | None
    folio: int | None = None
    error_message: str | None
    final_click_occurred: bool
    retryable: bool = False
    retry_block_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class BatchPreviewItem(BaseModel):
    item_id: int
    client_id: int
    rut_receptor: str
    razon_social: str
    detalle: list[dict[str, Any]] | str
    monto: int
    retention_mode: RetentionMode | None = None


class BatchPreview(BaseModel):
    batch_id: int
    period: str
    fecha_emision: date
    detalle_global: str | None
    retention_mode: RetentionMode | None
    cantidad: int
    total: int
    items: list[BatchPreviewItem]


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period: str
    status: BatchStatus
    fecha_emision: date
    detalle_global: str | None
    retention_mode: RetentionMode | None
    provider: str
    source: str = "monthly"
    source_batch_id: int | None = None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None
    items: list[BatchItemResponse] = Field(default_factory=list)


class BatchEmissionResult(BaseModel):
    item_id: int
    status: BatchItemStatus
    bhe_emission_id: int | None
    folio: int | None = None
    error_message: str | None = None


class BatchEmissionResponse(BaseModel):
    batch_id: int
    status: BatchStatus
    provider: str
    total: int
    success: int
    failed: int
    unknown: int
    pending: int
    processing: int
    results: list[BatchEmissionResult]


class DiagnosticPreviewResponse(BaseModel):
    batch_id: int
    item_id: int
    diagnostic_run_id: str | None = None
    artifact_dir: str | None = None
    preview_reached: bool
    structural_preview_valid: bool
    requested_retention: str | None = None
    selected_retention: str | None = None
    selected_radio_value: str | None = None
    selection_verified: bool | None = None
    post_retention_value: str | None = None
    detected_retention: str | None = None
    evidence_type: str | None = None
    issuer_matches: list[dict[str, Any]] = Field(default_factory=list)
    receiver_matches: list[dict[str, Any]] = Field(default_factory=list)
    diagnostic_artifact_dir: str | None = None
    blocked_reason: str | None = None
