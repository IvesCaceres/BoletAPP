"""Reglas de negocio para persistir el historial de emisiones BHE."""
import re
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import update

from app.db.models import BHEEmission
from app.repositories import bhe_emission_repository
from app.schemas.bhe import BHERequest
from app.schemas.bhe_emission import EmissionStatus


class BHEEmissionNotFoundError(Exception):
    pass


def sanitize_error_message(value: object) -> str:
    message = str(value)
    message = re.sub(
        r"(?i)(accessToken|authorization|password|pdfBase64|token|jwt)\s*[:=]\s*(?:Bearer\s+)?[^,;\s]+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:1000]


def _total_as_clp(request: BHERequest) -> int:
    return int(request.total.quantize(Decimal("1")))


def create_pending_emission(
    db: Session, request: BHERequest, client_id: int | None = None,
    provider: str = "sii", batch_item_id: int | None = None,
) -> BHEEmission:
    values = {
        "client_id": client_id,
        "batch_item_id": batch_item_id,
        "rut_receptor": request.rut_receptor,
        "razon_social": request.razon_social,
        "direccion": request.direccion,
        "region": request.region or 13,
        "comuna": request.comuna,
        "detalle": [item.model_dump() for item in request.detalle],
        "monto_total": _total_as_clp(request),
        "fecha_emision_solicitada": request.fecha_emision or date.today(),
        "status": EmissionStatus.PENDING.value,
        "provider": provider,
        "retention_mode": request.retention_mode.value,
    }
    emission = bhe_emission_repository.create_pending_emission(db, values)
    db.commit()
    db.refresh(emission)
    return emission


def mark_emission_awaiting_final_confirmation(
    db: Session,
    emission: BHEEmission,
    *,
    provider_message: str | None = None,
    sii_resolved_recipient_name: str | None = None,
) -> BHEEmission:
    emission.status = EmissionStatus.AWAITING_FINAL_CONFIRMATION.value
    emission.provider_status = None
    emission.folio = None
    emission.codigo_barras = None
    emission.fecha_emision_provider = None
    emission.pdf_base64 = None
    emission.error_message = None
    emission.provider_message = sanitize_error_message(provider_message) if provider_message else None
    emission.sii_resolved_recipient_name = sii_resolved_recipient_name
    db.commit()
    db.refresh(emission)
    return emission


def mark_emission_success(
    db: Session, emission: BHEEmission, parsed_response: dict
) -> BHEEmission:
    emission.status = EmissionStatus.SUCCESS.value
    emission.provider_status = parsed_response.get("status")
    emission.folio = parsed_response.get("folio")
    emission.codigo_barras = parsed_response.get("codigo_barras")
    emission.fecha_emision_provider = parsed_response.get("fecha_emision")
    emission.pdf_base64 = parsed_response.get("pdf_base64")
    emission.provider_message = sanitize_error_message(parsed_response.get("provider_message", "")) or None
    if parsed_response.get("sii_resolved_recipient_name"):
        emission.sii_resolved_recipient_name = parsed_response["sii_resolved_recipient_name"]
    emission.error_message = None
    db.commit()
    db.refresh(emission)
    return emission


def begin_sii_finalization(db: Session, emission_id: int) -> BHEEmission | None:
    """Atomically claim an awaiting SII emission for finalization."""
    result = db.execute(
        update(BHEEmission)
        .where(
            BHEEmission.id == emission_id,
            BHEEmission.provider == "sii",
            BHEEmission.status == EmissionStatus.AWAITING_FINAL_CONFIRMATION.value,
        )
        .values(status=EmissionStatus.FINALIZING.value)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return get_emission(db, emission_id)


def begin_sii_emission(db: Session, emission_id: int) -> BHEEmission | None:
    """Claim a newly requested SII emission before opening its single-pass flow."""
    result = db.execute(
        update(BHEEmission)
        .where(
            BHEEmission.id == emission_id,
            BHEEmission.provider == "sii",
            BHEEmission.status == EmissionStatus.PENDING.value,
        )
        .values(status=EmissionStatus.FINALIZING.value)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return get_emission(db, emission_id)


def mark_emission_status_unknown(db: Session, emission: BHEEmission, message: object) -> BHEEmission:
    emission.status = EmissionStatus.EMISSION_STATUS_UNKNOWN.value
    emission.provider_message = sanitize_error_message(message)
    emission.error_message = None
    db.commit()
    db.refresh(emission)
    return emission


def revert_sii_finalization(db: Session, emission: BHEEmission, message: object) -> BHEEmission:
    """Reopen only failures known to happen before the irreversible click."""
    emission.status = EmissionStatus.AWAITING_FINAL_CONFIRMATION.value
    emission.provider_message = sanitize_error_message(message)
    db.commit()
    db.refresh(emission)
    return emission


def mark_emission_failed(
    db: Session,
    emission: BHEEmission,
    error: object,
    provider_status: int | None = None,
) -> BHEEmission:
    emission.status = EmissionStatus.FAILED.value
    emission.provider_status = provider_status
    emission.error_message = sanitize_error_message(error)
    emission.pdf_base64 = None
    db.commit()
    db.refresh(emission)
    return emission


def get_emission(db: Session, emission_id: int) -> BHEEmission:
    emission = bhe_emission_repository.get_emission(db, emission_id)
    if emission is None:
        raise BHEEmissionNotFoundError("Emisión BHE no encontrada")
    return emission


def list_emissions(
    db: Session,
    *,
    status: str | None = None,
    rut_receptor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[BHEEmission]:
    return bhe_emission_repository.list_emissions(
        db,
        status=status,
        rut_receptor=rut_receptor,
        date_from=date_from,
        date_to=date_to,
    )
