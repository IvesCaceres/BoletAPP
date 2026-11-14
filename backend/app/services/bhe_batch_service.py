"""Reglas de negocio para batches mensuales BHE."""
import asyncio
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.db.models import BHEBatch, BHEBatchItem, BHEEmission, Client
from app.repositories import bhe_batch_repository, client_repository
from app.schemas.bhe import BHERequest
from app.schemas.bhe_batch import (
    BatchCreate,
    BatchEmissionResult,
    BatchItemCreate,
    BatchItemStatus,
    BatchItemUpdate,
    BatchUpdate,
    BatchPreview,
    BatchPreviewItem,
    BatchStatus,
    ManualBatchCreate,
    ManualBatchCommonUpdate,
)
from app.schemas.bhe_emission import EmissionStatus
from app.schemas.retention import RetentionMode
from app.services.bhe_emission_history_service import mark_emission_failed, sanitize_error_message
from app.services.bhe_provider import SIIBHEProvider, get_bhe_provider
from app.services.manual_date_policy import allowed_issue_dates, issue_date_error
from app.services.sii_browser_service import SiiBrowserService, SiiPocSettings
from app.utils.rut import validate_rut


class BatchNotFoundError(Exception):
    pass


class BatchItemNotFoundError(Exception):
    pass


class BatchConflictError(Exception):
    pass


class BatchValidationError(Exception):
    pass


_SII_BATCH_WORKER_LOCK = threading.Lock()
_BATCH_IN_FLIGHT_STATUSES = frozenset({
    BatchItemStatus.PENDING.value, "processing",
})
_BATCH_TERMINAL_STATUSES = frozenset({
    BatchItemStatus.SUCCESS.value, BatchItemStatus.FAILED.value,
    BatchItemStatus.EMISSION_STATUS_UNKNOWN.value,
})
_SAFE_PRE_FINAL_FAILURE_STAGES = frozenset({
    "initialized", "browser_starting", "browser_started", "clean_retention_start",
    "session_check", "navigating_bhe", "retention_selected", "form_fill_started",
    "form_filled", "preview_reached",
})


def create_batch(db: Session, payload: BatchCreate) -> BHEBatch:
    batch = bhe_batch_repository.create_batch(
        db,
        {
            "period": payload.period,
            "status": BatchStatus.DRAFT.value,
            "fecha_emision": payload.fecha_emision,
            "detalle_global": payload.detalle_global,
            "retention_mode": payload.retention_mode.value,
            "provider": "sii",
        },
    )
    db.commit()
    db.refresh(batch)
    return batch


def create_manual_batch(db: Session, payload: ManualBatchCreate) -> BHEBatch:
    """Persist a confirmed one-off queue as detached item snapshots."""
    for request in payload.items:
        _validate_manual_request(request)
    dates = [entry.fecha_emision for entry in payload.items]
    batch = bhe_batch_repository.create_batch(db, {
        "period": dates[0].strftime("%Y-%m"),
        "status": BatchStatus.READY.value,
        "fecha_emision": dates[0],
        "detalle_global": None,
        "retention_mode": None,
        "provider": "sii",
        "source": "manual",
    })
    for request in payload.items:
        _add_manual_snapshot(db, batch.id, request)
    db.commit()
    return get_batch(db, batch.id)


def clone_editable_manual_batch(db: Session, batch_id: int) -> BHEBatch:
    """Copy only backend-approved failures; retain the source and its evidence."""
    source = get_batch(db, batch_id)
    if source.source != "manual" or source.provider != "sii" or source.status != BatchStatus.COMPLETED_WITH_ERRORS.value:
        raise BatchConflictError("El lote manual no esta terminado con errores")
    eligible = [item for item in source.items if item_retryability(db, item)[0]]
    if not eligible:
        raise BatchConflictError("El lote no tiene boletas seguras para copiar")
    try:
        first_date = eligible[0].fecha_emision or source.fecha_emision
        new_batch = bhe_batch_repository.create_batch(db, {
            "period": first_date.strftime("%Y-%m"), "status": BatchStatus.READY.value,
            "fecha_emision": first_date, "detalle_global": None,
            "retention_mode": None, "provider": "sii", "source": "manual",
            "source_batch_id": source.id,
        })
        for item in eligible:
            db.add(BHEBatchItem(
                batch_id=new_batch.id, client_id=None, rut_receptor=item.rut_receptor,
                razon_social=item.razon_social, giro=item.giro, email=item.email,
                direccion=item.direccion, region=item.region, comuna=item.comuna,
                fecha_emision=item.fecha_emision, detalle=deepcopy(item.detalle),
                monto=item.monto, retention_mode_override=item.retention_mode_override,
                status=BatchItemStatus.PENDING.value,
            ))
        db.commit()
        return get_batch(db, new_batch.id)
    except Exception:
        db.rollback()
        raise


def apply_manual_common_to_pending(db: Session, batch_id: int, payload: ManualBatchCommonUpdate) -> BHEBatch:
    """Atomically update only editable pending snapshots in a manual batch."""
    error = issue_date_error(payload.fecha_emision)
    if error:
        raise BatchValidationError(error)
    try:
        claimed = db.execute(update(BHEBatch).where(
            BHEBatch.id == batch_id, BHEBatch.source == "manual",
            BHEBatch.status == BatchStatus.READY.value,
        ).values(updated_at=datetime.now(timezone.utc)))
        if claimed.rowcount != 1:
            raise BatchConflictError("El lote manual ya no esta disponible para editar")
        db.expire_all()
        batch = get_batch(db, batch_id)
        if not batch.items or any(
            item.status != BatchItemStatus.PENDING.value or item.bhe_emission_id is not None
            or item.final_click_occurred or item.folio is not None for item in batch.items
        ):
            raise BatchConflictError("El lote contiene items que no son pendientes editables")
        for item in batch.items:
            item.retention_mode_override = payload.retention_mode.value
            item.direccion = payload.direccion
            item.region = payload.region
            item.comuna = payload.comuna
            item.detalle = [{"descripcion": payload.detalle, "cantidad": 1, "precio_unitario": item.monto}]
            item.fecha_emision = payload.fecha_emision
        batch.fecha_emision = payload.fecha_emision
        batch.period = payload.fecha_emision.strftime("%Y-%m")
        db.commit()
        return get_batch(db, batch_id)
    except Exception:
        db.rollback()
        raise


def _add_manual_snapshot(db: Session, batch_id: int, request: BHERequest) -> BHEBatchItem:
    detail = request.detalle[0]
    item = BHEBatchItem(
        batch_id=batch_id,
        client_id=None,
        rut_receptor=request.rut_receptor,
        razon_social=request.razon_social,
        direccion=request.direccion,
        region=request.region or 13,
        comuna=request.comuna,
        giro=request.giro,
        email=request.email,
        fecha_emision=request.fecha_emision,
        detalle=[detail.model_dump()],
        retention_mode_override=request.retention_mode.value,
        monto=detail.get_valor_int(),
        status=BatchItemStatus.PENDING.value,
    )
    db.add(item)
    return item


def append_manual_item(db: Session, batch_id: int, request: BHERequest) -> BHEBatch:
    batch = get_batch(db, batch_id)
    _require_manual_queue_editable(db, batch)
    _validate_manual_request(request)
    _add_manual_snapshot(db, batch_id, request)
    db.commit()
    return get_batch(db, batch_id)


def replace_manual_item(db: Session, batch_id: int, item_id: int, request: BHERequest) -> BHEBatch:
    batch, item = _get_item(db, batch_id, item_id)
    _require_manual_queue_editable(db, batch)
    if item.status != BatchItemStatus.PENDING.value or item.bhe_emission_id or item.final_click_occurred:
        raise BatchConflictError("El item manual ya no se puede editar")
    _validate_manual_request(request)
    detail = request.detalle[0]
    item.client_id = None
    item.rut_receptor = request.rut_receptor
    item.razon_social = request.razon_social
    item.direccion = request.direccion
    item.region = request.region or 13
    item.comuna = request.comuna
    item.giro = request.giro
    item.email = request.email
    item.fecha_emision = request.fecha_emision
    item.detalle = [detail.model_dump()]
    item.retention_mode_override = request.retention_mode.value
    item.monto = detail.get_valor_int()
    db.commit()
    return get_batch(db, batch_id)


def _require_manual_queue_editable(db: Session, batch: BHEBatch) -> None:
    if batch.source != "manual" or batch.status != BatchStatus.READY.value:
        raise BatchConflictError("El lote manual no permite cambios en su estado actual")
    if any(item.status != BatchItemStatus.PENDING.value or item.bhe_emission_id or item.final_click_occurred for item in batch.items):
        raise BatchConflictError("El lote manual ya contiene evidencia de procesamiento")
    claim = update(BHEBatch).where(
        BHEBatch.id == batch.id,
        BHEBatch.source == "manual",
        BHEBatch.status == BatchStatus.READY.value,
    ).values(updated_at=datetime.now(timezone.utc))
    result = db.execute(claim)
    if result.rowcount != 1:
        db.rollback()
        raise BatchConflictError("El lote manual comenzó a procesarse y ya no admite cambios")


def _validate_manual_request(request: BHERequest) -> None:
    try:
        ManualBatchCreate(items=[request])
    except Exception as error:
        raise BatchValidationError(str(error)) from error
    error = issue_date_error(request.fecha_emision)
    if error:
        raise BatchValidationError(error)


def get_batch(db: Session, batch_id: int) -> BHEBatch:
    batch = bhe_batch_repository.get_batch(db, batch_id)
    if batch is None:
        raise BatchNotFoundError("Batch no encontrado")
    return batch


def list_batches(db: Session) -> list[BHEBatch]:
    return bhe_batch_repository.list_batches(db)


def update_batch(db: Session, batch_id: int, payload: BatchUpdate) -> BHEBatch:
    batch = get_batch(db, batch_id)
    _require_draft(batch)
    values = payload.model_dump(exclude_unset=True)
    if "detalle_global" in values:
        batch.detalle_global = values["detalle_global"]
    if "fecha_emision" in values:
        batch.fecha_emision = values["fecha_emision"]
    if "retention_mode" in values:
        batch.retention_mode = values["retention_mode"].value if values["retention_mode"] else None
    db.commit()
    return get_batch(db, batch_id)


def _require_draft(batch: BHEBatch) -> None:
    if batch.status != BatchStatus.DRAFT.value:
        raise BatchConflictError("El batch no permite modificaciones en su estado actual")


def _get_item(db: Session, batch_id: int, item_id: int) -> tuple[BHEBatch, BHEBatchItem]:
    batch = get_batch(db, batch_id)
    item = bhe_batch_repository.get_item(db, item_id)
    if item is None or item.batch_id != batch_id:
        raise BatchItemNotFoundError("Item de batch no encontrado")
    return batch, item


def add_item(db: Session, batch_id: int, payload: BatchItemCreate) -> BHEBatchItem:
    batch = get_batch(db, batch_id)
    _require_draft(batch)
    client = client_repository.get_client(db, payload.client_id)
    if client is None:
        raise BatchItemNotFoundError("Cliente no encontrado")
    if not client.activo:
        raise BatchValidationError("El cliente está inactivo")
    if bhe_batch_repository.get_item_by_client(db, batch_id, payload.client_id):
        raise BatchConflictError("El cliente ya existe en este batch")

    detalle = (
        payload.detalle
        if payload.detalle is not None
        else batch.detalle_global or client.detalle_default
    )
    if not detalle:
        raise BatchValidationError("El cliente no tiene detalle_default y no se proporcionó detalle")

    item = bhe_batch_repository.create_item(
        db,
        {
            "batch_id": batch_id,
            "client_id": client.id,
            "rut_receptor": client.rut,
            "razon_social": client.razon_social,
            "direccion": client.direccion,
            "region": client.region,
            "comuna": client.comuna,
            "detalle": deepcopy(detalle),
            "monto": payload.monto,
            "status": BatchItemStatus.DRAFT.value,
            "retention_mode_override": (
                payload.retention_mode_override.value
                if payload.retention_mode_override else None
            ),
        },
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise BatchConflictError("El cliente ya existe en este batch") from error
    db.refresh(item)
    return item


def update_item(db: Session, batch_id: int, item_id: int, payload: BatchItemUpdate) -> BHEBatchItem:
    batch, item = _get_item(db, batch_id, item_id)
    _require_draft(batch)
    values = payload.model_dump(exclude_unset=True)
    if "monto" in values:
        item.monto = values["monto"]
    if "detalle" in values:
        if not values["detalle"]:
            raise BatchValidationError("detalle no puede estar vacío")
        item.detalle = deepcopy(values["detalle"])
    if "retention_mode_override" in values:
        item.retention_mode_override = (
            values["retention_mode_override"].value
            if values["retention_mode_override"] else None
        )
    db.commit()
    db.refresh(item)
    return item


def apply_global_detail(db: Session, batch_id: int) -> BHEBatch:
    batch = get_batch(db, batch_id)
    _require_draft(batch)
    if not batch.detalle_global:
        raise BatchValidationError("El batch no tiene detalle_global")
    for item in bhe_batch_repository.list_draft_items(db, batch_id):
        item.detalle = batch.detalle_global
    db.commit()
    return get_batch(db, batch_id)


def delete_item(db: Session, batch_id: int, item_id: int) -> None:
    batch, item = _get_item(db, batch_id, item_id)
    if batch.source == "manual":
        _require_manual_queue_editable(db, batch)
    else:
        _require_draft(batch)
    db.delete(item)
    if batch.source == "manual" and len(batch.items) == 1:
        db.delete(batch)
    db.commit()


def _preview(batch: BHEBatch) -> BatchPreview:
    items = [
        BatchPreviewItem(
            item_id=item.id,
            client_id=item.client_id,
            rut_receptor=item.rut_receptor,
            razon_social=item.razon_social,
            detalle=item.detalle,
            monto=item.monto,
            retention_mode=effective_retention_mode(batch, item),
        )
        for item in batch.items
    ]
    return BatchPreview(
        batch_id=batch.id,
        period=batch.period,
        fecha_emision=batch.fecha_emision,
        detalle_global=batch.detalle_global,
        retention_mode=RetentionMode(batch.retention_mode) if batch.retention_mode else None,
        cantidad=len(items),
        total=sum(item.monto for item in items),
        items=items,
    )


def prepare_batch(db: Session, batch_id: int) -> BatchPreview:
    batch = get_batch(db, batch_id)
    _require_draft(batch)
    if batch.fecha_emision > date.today():
        raise BatchValidationError("fecha_emision no puede ser futura")
    if not batch.items:
        raise BatchValidationError("El batch debe tener al menos un item")
    seen_clients: set[int] = set()
    for item in batch.items:
        if item.client_id in seen_clients:
            raise BatchValidationError("Existen clientes duplicados en el batch")
        seen_clients.add(item.client_id)
        if item.status != BatchItemStatus.DRAFT.value:
            raise BatchValidationError("Todos los items deben estar en estado draft")
        if not validate_rut(item.rut_receptor):
            raise BatchValidationError(f"RUT inválido en item {item.id}")
        if not item.detalle:
            raise BatchValidationError(f"Falta detalle en item {item.id}")
        if item.monto <= 0:
            raise BatchValidationError(f"Monto inválido en item {item.id}")
    batch.status = BatchStatus.READY.value
    db.commit()
    db.refresh(batch)
    return _preview(batch)


def effective_retention_mode(
    batch: BHEBatch, item: BHEBatchItem
) -> RetentionMode | None:
    """Resolve item override without copying the batch value into each item."""
    value = item.retention_mode_override or batch.retention_mode
    return RetentionMode(value) if value else None


def _terminal_batch_status(batch: BHEBatch) -> str | None:
    statuses = [item.status for item in batch.items]
    if not statuses or any(status in _BATCH_IN_FLIGHT_STATUSES for status in statuses):
        return None
    if any(status not in _BATCH_TERMINAL_STATUSES for status in statuses):
        return None
    if all(status == BatchItemStatus.SUCCESS.value for status in statuses):
        return BatchStatus.COMPLETED.value
    # Failed and unknown items both need a terminal parent while preserving
    # their distinct item-level semantics for retry/manual review.
    return BatchStatus.COMPLETED_WITH_ERRORS.value


def reconcile_batch_status(
    db: Session,
    batch_id: int,
    *,
    worker_lock_owned: bool = False,
    stamp_completed_at: bool = False,
) -> bool:
    """Derive a processing batch's parent status only when no worker can advance it."""
    acquired_lock = False
    if worker_lock_owned:
        if not _SII_BATCH_WORKER_LOCK.locked():
            return False
    else:
        acquired_lock = _SII_BATCH_WORKER_LOCK.acquire(blocking=False)
        if not acquired_lock:
            return False
    try:
        batch = db.get(BHEBatch, batch_id)
        if batch is None or batch.status != BatchStatus.PROCESSING.value:
            return False
        terminal_status = _terminal_batch_status(batch)
        if terminal_status is None:
            return False
        batch.status = terminal_status
        if stamp_completed_at:
            batch.completed_at = datetime.now(timezone.utc)
        db.commit()
        return True
    finally:
        if acquired_lock:
            _SII_BATCH_WORKER_LOCK.release()


def reconcile_interrupted_batches(
    db: Session,
    batch_id: int | None = None,
    *,
    worker_lock_owned: bool = False,
) -> int:
    """Recover abandoned batches; active SII workers are protected by the worker lock."""
    acquired_lock = False
    if worker_lock_owned:
        if not _SII_BATCH_WORKER_LOCK.locked():
            return 0
    else:
        acquired_lock = _SII_BATCH_WORKER_LOCK.acquire(blocking=False)
        if not acquired_lock:
            return 0
    recovered_items = 0
    changed = False
    try:
        query = db.query(BHEBatch).filter(BHEBatch.status == BatchStatus.PROCESSING.value)
        if batch_id is not None:
            query = query.filter(BHEBatch.id == batch_id)
        for batch in query.all():
            recovered_in_batch = 0
            for item in batch.items:
                if item.status in _BATCH_IN_FLIGHT_STATUSES:
                    # After an interrupted worker, these items may have crossed
                    # the provider's final-action boundary; quarantine them.
                    item.status = BatchItemStatus.EMISSION_STATUS_UNKNOWN.value
                    item.final_click_occurred = True
                    item.error_message = "Procesamiento interrumpido; verificar en SII antes de reintentar."
                    recovered_items += 1
                    recovered_in_batch += 1
                    changed = True
            terminal_status = _terminal_batch_status(batch)
            if terminal_status is not None and batch.status != terminal_status:
                batch.status = terminal_status
                if recovered_in_batch:
                    batch.completed_at = datetime.now(timezone.utc)
                changed = True
        if changed:
            db.commit()
        return recovered_items
    finally:
        if acquired_lock:
            _SII_BATCH_WORKER_LOCK.release()


def _set_processing(db: Session, batch: BHEBatch) -> None:
    batch.status = BatchStatus.PROCESSING.value
    batch.confirmed_at = datetime.now(timezone.utc)
    db.commit()


def _has_nonretryable_emission_evidence(db: Session, item: BHEBatchItem) -> bool:
    """Return true when persisted data indicates an emission may have occurred."""
    db.refresh(item)
    if (item.status in {BatchItemStatus.SUCCESS.value, BatchItemStatus.EMISSION_STATUS_UNKNOWN.value}
            or item.folio is not None or item.final_click_occurred):
        return True
    conditions = [BHEEmission.batch_item_id == item.id]
    if item.bhe_emission_id is not None:
        conditions.append(BHEEmission.id == item.bhe_emission_id)
    emissions = db.scalars(select(BHEEmission).where(or_(*conditions))).all()
    for emission in emissions:
        if emission.status != EmissionStatus.FAILED.value or emission.folio is not None:
            return True
        diagnostic = emission.sii_retention_diagnostics or {}
        if any(diagnostic.get(flag) is True for flag in (
            "final_click_executed", "final_post_sent", "possible_emission",
        )):
            return True
        if diagnostic.get("failure_stage") not in _SAFE_PRE_FINAL_FAILURE_STAGES:
            return True
    return False


def _retryable_failed_items(db: Session, batch: BHEBatch) -> list[BHEBatchItem]:
    """Select only failures proven safe to retry; never infer from error text."""
    return [item for item in batch.items if is_retryable_failed_item(db, item)]


def is_retryable_failed_item(db: Session, item: BHEBatchItem) -> bool:
    return item_retryability(db, item)[0]


def item_retryability(db: Session, item: BHEBatchItem) -> tuple[bool, str | None]:
    """Require affirmative persisted evidence that no final emission could occur."""
    db.refresh(item)
    if item.status == BatchItemStatus.EMISSION_STATUS_UNKNOWN.value:
        return False, "emission_status_unknown"
    if item.status != BatchItemStatus.FAILED.value:
        return False, "item_not_failed"
    if item.folio is not None:
        return False, "folio_present"
    if item.final_click_occurred:
        return False, "final_click_occurred"
    conditions = [BHEEmission.batch_item_id == item.id]
    if item.bhe_emission_id is not None:
        conditions.append(BHEEmission.id == item.bhe_emission_id)
    emissions = db.scalars(select(BHEEmission).where(or_(*conditions))).all()
    if item.bhe_emission_id is not None and not any(row.id == item.bhe_emission_id for row in emissions):
        return False, "linked_emission_missing"
    if not emissions:
        # The provider commits an emission row before any browser/SII operation.
        return True, None
    for emission in emissions:
        db.refresh(emission)
        if emission.folio is not None:
            return False, "folio_present"
        if emission.status != EmissionStatus.FAILED.value:
            return False, "emission_not_confirmed_failed"
        diagnostic = emission.sii_retention_diagnostics or {}
        if any(diagnostic.get(flag) is True for flag in (
            "final_click_executed", "final_post_sent", "possible_emission",
        )):
            return False, "final_emission_evidence"
        if diagnostic.get("failure_stage") not in _SAFE_PRE_FINAL_FAILURE_STAGES:
            return False, "pre_final_failure_not_proven"
    return True, None


def _validate_manual_processing_dates(batch: BHEBatch, items: list[BHEBatchItem]) -> None:
    """Reject the whole manual run before opening a browser or changing status."""
    if batch.source != "manual":
        return
    minimum, maximum = allowed_issue_dates()
    too_old = sum((item.fecha_emision or batch.fecha_emision) < minimum for item in items)
    future = sum((item.fecha_emision or batch.fecha_emision) > maximum for item in items)
    if too_old or future:
        problems = []
        if too_old:
            problems.append(f"{too_old} con más de 90 días de antigüedad")
        if future:
            problems.append(f"{future} con fecha futura")
        raise BatchValidationError(
            "No se inició la emisión: " + ", ".join(problems)
            + f". Fechas permitidas: {minimum.isoformat()} a {maximum.isoformat()}."
        )


def claim_ready_batch(db: Session, batch_id: int) -> BHEBatch:
    """Atomically claim a ready batch before any provider work starts."""
    claimed = db.execute(
        update(BHEBatch)
        .where(BHEBatch.id == batch_id, BHEBatch.status == BatchStatus.READY.value)
        .values(status=BatchStatus.PROCESSING.value, confirmed_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BatchConflictError("El batch ya está siendo procesado o no está listo")
    try:
        db.expire_all()
        batch = get_batch(db, batch_id)
        _validate_manual_processing_dates(batch, list(batch.items))
    except Exception:
        db.rollback()
        raise
    db.commit()
    return get_batch(db, batch_id)


async def _process_items(
    db: Session,
    batch: BHEBatch,
    items: list[BHEBatchItem],
    service: object | None = None,
    delay_seconds: float = 0,
) -> list[BatchEmissionResult]:
    _set_processing(db, batch)
    results: list[BatchEmissionResult] = []
    for index, item in enumerate(items):
        if _has_nonretryable_emission_evidence(db, item):
            results.append(
                BatchEmissionResult(
                    item_id=item.id,
                    status=BatchItemStatus.SUCCESS,
                    bhe_emission_id=item.bhe_emission_id,
                    folio=item.folio,
                )
            )
            continue
        item.status = BatchItemStatus.PENDING.value
        item.error_message = None
        db.commit()
        try:
            request = BHERequest(
                rut_receptor=item.rut_receptor,
                razon_social=item.razon_social,
                direccion=item.direccion,
                region=item.region,
                comuna=item.comuna,
                fecha_emision=item.fecha_emision or batch.fecha_emision,
                retention_mode=effective_retention_mode(batch, item) or RetentionMode.ISSUER_HANDLES_PPM,
                detalle=[
                    {
                        "descripcion": (
                            item.detalle
                            if isinstance(item.detalle, str)
                            else item.detalle[0].get("descripcion", "Servicio")
                        ),
                        "cantidad": 1,
                        "precio_unitario": item.monto,
                    }
                ],
            )
            provider = get_bhe_provider()
            if batch.provider == "sii":
                execution = await provider.execute(db, request, client_id=item.client_id, batch_item_id=item.id)
                item.bhe_emission_id = execution.emission.id
                if execution.status == "awaiting_final_confirmation":
                    # Historical compatibility only: new SII executions finalize in execute().
                    item.final_click_occurred = False
                    try:
                        final_result = await provider.confirm_final(db, execution.emission)
                        item.final_click_occurred = True
                        execution.emission.folio = final_result.get("folio")
                    except Exception as error:
                        item.final_click_occurred = bool(getattr(error, "click_performed", False))
                        if item.final_click_occurred:
                            item.status = BatchItemStatus.EMISSION_STATUS_UNKNOWN.value
                            item.error_message = sanitize_error_message(error)
                            results.append(BatchEmissionResult(item_id=item.id, status=BatchItemStatus.EMISSION_STATUS_UNKNOWN, bhe_emission_id=item.bhe_emission_id, error_message=item.error_message))
                            db.commit()
                            continue
                        mark_emission_failed(db, execution.emission, error)
                        execution.error = error
                    else:
                        execution.error = None
                elif execution.status == "success":
                    item.final_click_occurred = True
                elif execution.status == "emission_status_unknown":
                    item.final_click_occurred = True
                    item.status = BatchItemStatus.EMISSION_STATUS_UNKNOWN.value
                    item.error_message = sanitize_error_message(execution.message)
                    results.append(BatchEmissionResult(
                        item_id=item.id,
                        status=BatchItemStatus.EMISSION_STATUS_UNKNOWN,
                        bhe_emission_id=item.bhe_emission_id,
                        error_message=item.error_message,
                    ))
                    db.commit()
                    continue
                else:
                    execution.error = execution.error or RuntimeError(execution.message)
            else:
                raise BatchConflictError("Los batches históricos SimpleFactura no pueden emitir nuevas BHE")
            if execution.error is None:
                item.status = BatchItemStatus.SUCCESS.value
                item.error_message = None
                results.append(
                    BatchEmissionResult(
                        item_id=item.id,
                        status=BatchItemStatus.SUCCESS,
                        bhe_emission_id=item.bhe_emission_id,
                        folio=execution.emission.folio,
                    )
                )
            else:
                item.status = BatchItemStatus.FAILED.value
                item.error_message = execution.emission.error_message
                results.append(
                    BatchEmissionResult(
                        item_id=item.id,
                        status=BatchItemStatus.FAILED,
                        bhe_emission_id=item.bhe_emission_id,
                        error_message=item.error_message,
                    )
                )
        except Exception as error:
            item.status = BatchItemStatus.FAILED.value
            item.error_message = sanitize_error_message(error)
            results.append(
                BatchEmissionResult(
                    item_id=item.id,
                    status=BatchItemStatus.FAILED,
                    bhe_emission_id=item.bhe_emission_id,
                    error_message=item.error_message,
                )
            )
        db.commit()
        if index < len(items) - 1 and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    reconcile_batch_status(db, batch.id, stamp_completed_at=True)
    return results


def _request_for_batch_item(batch: BHEBatch, item: BHEBatchItem) -> BHERequest:
    return BHERequest(
        rut_receptor=item.rut_receptor,
        razon_social=item.razon_social,
        direccion=item.direccion,
        region=item.region,
        comuna=item.comuna,
        fecha_emision=item.fecha_emision or batch.fecha_emision,
        retention_mode=effective_retention_mode(batch, item) or RetentionMode.ISSUER_HANDLES_PPM,
        detalle=[{
            "descripcion": (
                item.detalle if isinstance(item.detalle, str)
                else item.detalle[0].get("descripcion", "Servicio")
            ),
            "cantidad": 1,
            "precio_unitario": item.monto,
        }],
    )


def validate_preview_only_item(db: Session, batch_id: int, item_id: int) -> tuple[BHEBatch, BHEBatchItem]:
    """Validate the non-mutating, single-item diagnostic preconditions."""
    batch, item = _get_item(db, batch_id, item_id)
    if batch.provider != "sii":
        raise BatchConflictError("El proveedor del batch no es compatible con preview-only")
    if batch.status != BatchStatus.COMPLETED_WITH_ERRORS.value:
        raise BatchConflictError("El batch no está en estado de diagnóstico permitido")
    if item.status == BatchItemStatus.EMISSION_STATUS_UNKNOWN.value:
        raise BatchConflictError("El estado de emisión del item es desconocido")
    if item.status != BatchItemStatus.FAILED.value:
        raise BatchConflictError("Sólo un item fallido puede ejecutar preview-only")
    if item.final_click_occurred:
        raise BatchConflictError("El item ya registra final_click=true")
    if item.folio is not None:
        raise BatchConflictError("El item ya tiene folio y no puede ejecutar preview-only")
    if item.bhe_emission_id is not None:
        emission = db.get(BHEEmission, item.bhe_emission_id)
        if emission is not None and emission.status != EmissionStatus.FAILED.value:
            raise BatchConflictError("La emisión asociada no está confirmada como fallida")
    return batch, item


def run_preview_only_item(batch: BHEBatch, item: BHEBatchItem):
    """Execute the explicitly diagnostic browser path without touching the DB."""
    settings = SiiPocSettings.from_environment()
    browser = SiiBrowserService(settings)
    browser.begin_preview_only_run(batch_id=batch.id, item_id=item.id)
    request = _request_for_batch_item(batch, item)
    provider = SIIBHEProvider()
    return browser.run_preview_only(provider._automation_data(request))


def _process_sii_items_in_session(
    db: Session,
    batch: BHEBatch,
    items: list[BHEBatchItem],
    provider: SIIBHEProvider,
    browser: SiiBrowserService,
    delay_seconds: float,
    *,
    worker_lock_owned: bool = False,
) -> list[BatchEmissionResult]:
    try:
        return _process_sii_items_in_session_body(
            db, batch, items, provider, browser, delay_seconds
        )
    finally:
        reconcile_batch_status(
            db, batch.id, worker_lock_owned=worker_lock_owned,
            stamp_completed_at=True,
        )


def _process_sii_items_in_session_body(
    db: Session,
    batch: BHEBatch,
    items: list[BHEBatchItem],
    provider: SIIBHEProvider,
    browser: SiiBrowserService,
    delay_seconds: float,
) -> list[BatchEmissionResult]:
    """Process items sequentially while one thread owns the whole Playwright session."""
    _set_processing(db, batch)
    results: list[BatchEmissionResult] = []
    with browser.batch_session() as session:
        for index, item in enumerate(items):
            if _has_nonretryable_emission_evidence(db, item):
                results.append(BatchEmissionResult(
                    item_id=item.id, status=BatchItemStatus.SUCCESS,
                    bhe_emission_id=item.bhe_emission_id, folio=item.folio,
                ))
                continue
            item.status = BatchItemStatus.PENDING.value
            item.error_message = None
            db.commit()
            try:
                execution = provider.execute_in_batch_session(
                    db, _request_for_batch_item(batch, item), session,
                    client_id=item.client_id, batch_item_id=item.id,
                )
                item.bhe_emission_id = execution.emission.id
                if execution.status == "success":
                    item.final_click_occurred = True
                    item.status = BatchItemStatus.SUCCESS.value
                    item.error_message = None
                    results.append(BatchEmissionResult(
                        item_id=item.id, status=BatchItemStatus.SUCCESS,
                        bhe_emission_id=item.bhe_emission_id, folio=execution.emission.folio,
                    ))
                elif execution.status == "emission_status_unknown":
                    item.final_click_occurred = True
                    item.status = BatchItemStatus.EMISSION_STATUS_UNKNOWN.value
                    item.error_message = sanitize_error_message(execution.message)
                    results.append(BatchEmissionResult(
                        item_id=item.id, status=BatchItemStatus.EMISSION_STATUS_UNKNOWN,
                        bhe_emission_id=item.bhe_emission_id, error_message=item.error_message,
                    ))
                else:
                    item.final_click_occurred = False
                    item.status = BatchItemStatus.FAILED.value
                    item.error_message = execution.emission.error_message or sanitize_error_message(execution.message)
                    results.append(BatchEmissionResult(
                        item_id=item.id, status=BatchItemStatus.FAILED,
                        bhe_emission_id=item.bhe_emission_id, error_message=item.error_message,
                    ))
            except Exception as error:
                item.status = BatchItemStatus.FAILED.value
                item.error_message = sanitize_error_message(error)
                results.append(BatchEmissionResult(
                    item_id=item.id, status=BatchItemStatus.FAILED,
                    bhe_emission_id=item.bhe_emission_id, error_message=item.error_message,
                ))
            db.commit()

            # The next provider call performs its own clean page start; do
            # not recover or reuse the previous item's page here.
            if index < len(items) - 1 and False:
                if not recovered:
                    for remaining in items[index + 1:]:
                        remaining.status = BatchItemStatus.FAILED.value
                        remaining.final_click_occurred = False
                        remaining.error_message = "Batch detenido: no se pudo recuperar una sesión SII segura."
                    db.commit()
                    break
            if index < len(items) - 1 and delay_seconds > 0:
                time.sleep(delay_seconds)

    return results


def run_sii_batch_worker(batch_id: int) -> None:
    """Own DB session and Playwright lifecycle in the same worker thread."""
    with _SII_BATCH_WORKER_LOCK:
        _run_sii_batch_worker_locked(batch_id)


def _run_sii_batch_worker_locked(batch_id: int) -> None:
    db = get_session_factory()()
    try:
        batch = get_batch(db, batch_id)
        if batch.provider != "sii" or batch.status != BatchStatus.PROCESSING.value:
            raise BatchConflictError("El worker requiere un batch SII reclamado en processing")
        provider = get_bhe_provider()
        if not isinstance(provider, SIIBHEProvider):
            raise BatchConflictError("No se pudo inicializar el provider SII")
        browser = SiiBrowserService(SiiPocSettings.from_environment())
        _process_sii_items_in_session(
            db, batch, list(batch.items), provider, browser,
            get_settings().bhe_batch_delay_seconds, worker_lock_owned=True,
        )
    except Exception:
        db.rollback()
        reconcile_interrupted_batches(db, batch_id, worker_lock_owned=True)
    finally:
        reconcile_batch_status(
            db, batch_id, worker_lock_owned=True, stamp_completed_at=True
        )
        db.close()


def retry_sii_batch_worker(batch_id: int) -> tuple[BHEBatch, list[BatchEmissionResult]]:
    """Claim and retry safe SII failures in one worker-owned browser lifecycle."""
    claim_db = get_session_factory()()
    try:
        claimed = claim_db.execute(
            update(BHEBatch)
            .where(
                BHEBatch.id == batch_id,
                BHEBatch.status == BatchStatus.COMPLETED_WITH_ERRORS.value,
                BHEBatch.provider == "sii",
            )
            .values(status=BatchStatus.PROCESSING.value, confirmed_at=datetime.now(timezone.utc))
        )
        if claimed.rowcount != 1:
            claim_db.rollback()
            raise BatchConflictError("El batch ya está siendo procesado o no es retryable")
        try:
            claim_db.expire_all()
            batch = get_batch(claim_db, batch_id)
            _validate_manual_processing_dates(batch, _retryable_failed_items(claim_db, batch))
        except Exception:
            claim_db.rollback()
            raise
        claim_db.commit()
    finally:
        claim_db.close()
    with _SII_BATCH_WORKER_LOCK:
        return _retry_sii_batch_worker_locked(batch_id)


def _retry_sii_batch_worker_locked(batch_id: int) -> tuple[BHEBatch, list[BatchEmissionResult]]:
    db = get_session_factory()()
    claimed_this_worker = True
    try:
        batch = get_batch(db, batch_id)
        if batch.status != BatchStatus.PROCESSING.value or batch.provider != "sii":
            raise BatchConflictError("El batch SII no fue reclamado para retry")
        batch = get_batch(db, batch_id)
        failed_items = _retryable_failed_items(db, batch)
        if not failed_items:
            batch.status = BatchStatus.COMPLETED_WITH_ERRORS.value
            db.commit()
            raise BatchConflictError("El batch no tiene items fallidos para reintentar")
        provider = get_bhe_provider()
        if not isinstance(provider, SIIBHEProvider):
            raise BatchConflictError("No se pudo inicializar el provider SII")
        browser = SiiBrowserService(SiiPocSettings.from_environment())
        results = _process_sii_items_in_session(
            db, batch, failed_items, provider, browser,
            get_settings().bhe_batch_delay_seconds, worker_lock_owned=True,
        )
        return get_batch(db, batch_id), results
    except Exception:
        db.rollback()
        if claimed_this_worker:
            reconcile_interrupted_batches(db, batch_id, worker_lock_owned=True)
        raise
    finally:
        if claimed_this_worker:
            reconcile_batch_status(
                db, batch_id, worker_lock_owned=True, stamp_completed_at=True
            )
        db.close()


async def emit_batch(
    db: Session,
    batch_id: int,
    service: object | None = None,
    *,
    delay_seconds: float | None = None,
) -> tuple[BHEBatch, list[BatchEmissionResult]]:
    batch = get_batch(db, batch_id)
    if batch.status != BatchStatus.READY.value:
        raise BatchConflictError("El batch debe estar en estado ready")
    batch = claim_ready_batch(db, batch_id)
    if delay_seconds is None:
        delay_seconds = get_settings().bhe_batch_delay_seconds
    results = await _process_items(db, batch, list(batch.items), service, delay_seconds)
    return get_batch(db, batch_id), results


async def retry_failed_batch(
    db: Session,
    batch_id: int,
    service: object | None = None,
    *,
    delay_seconds: float | None = None,
) -> tuple[BHEBatch, list[BatchEmissionResult]]:
    batch = get_batch(db, batch_id)
    if batch.status != BatchStatus.COMPLETED_WITH_ERRORS.value:
        raise BatchConflictError("Sólo se pueden reintentar batches con errores")
    claimed = db.execute(
        update(BHEBatch)
        .where(
            BHEBatch.id == batch_id,
            BHEBatch.status == BatchStatus.COMPLETED_WITH_ERRORS.value,
        )
        .values(status=BatchStatus.PROCESSING.value, confirmed_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BatchConflictError("El batch ya estÃ¡ siendo procesado")
    try:
        db.expire_all()
        batch = get_batch(db, batch_id)
        _validate_manual_processing_dates(batch, _retryable_failed_items(db, batch))
    except Exception:
        db.rollback()
        raise
    db.commit()
    batch = get_batch(db, batch_id)
    failed_items = _retryable_failed_items(db, batch)
    if not failed_items:
        batch.status = BatchStatus.COMPLETED_WITH_ERRORS.value
        db.commit()
        raise BatchConflictError("El batch no tiene items fallidos para reintentar")
    if delay_seconds is None:
        delay_seconds = get_settings().bhe_batch_delay_seconds
    results = await _process_items(db, batch, failed_items, service, delay_seconds)
    return get_batch(db, batch_id), results


async def retry_failed_item(
    db: Session,
    batch_id: int,
    item_id: int,
    service: object | None = None,
    *,
    delay_seconds: float | None = None,
) -> tuple[BHEBatch, list[BatchEmissionResult]]:
    """Retry exactly one failure after an atomic batch claim."""
    batch, item = _get_item(db, batch_id, item_id)
    if batch.status != BatchStatus.COMPLETED_WITH_ERRORS.value:
        raise BatchConflictError("Sólo se pueden reintentar batches con errores")
    if batch.provider != "sii":
        raise BatchConflictError("El proveedor del batch no es válido")

    claimed = db.execute(
        update(BHEBatch)
        .where(
            BHEBatch.id == batch_id,
            BHEBatch.status == BatchStatus.COMPLETED_WITH_ERRORS.value,
        )
        .values(status=BatchStatus.PROCESSING.value, confirmed_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BatchConflictError("El batch ya está siendo procesado")
    try:
        db.expire_all()
        batch, item = _get_item(db, batch_id, item_id)
        if is_retryable_failed_item(db, item):
            _validate_manual_processing_dates(batch, [item])
    except Exception:
        db.rollback()
        raise
    db.commit()

    batch, item = _get_item(db, batch_id, item_id)
    if not any(candidate.id == item.id for candidate in _retryable_failed_items(db, batch)):
        batch.status = BatchStatus.COMPLETED_WITH_ERRORS.value
        db.commit()
        raise BatchConflictError("El item no es seguro para reintentar")

    if delay_seconds is None:
        delay_seconds = get_settings().bhe_batch_delay_seconds
    results = await _process_items(db, batch, [item], service, delay_seconds)
    return get_batch(db, batch_id), results
