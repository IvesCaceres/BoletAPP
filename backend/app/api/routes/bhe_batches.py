"""API para preparar y procesar batches mensuales de BHE."""
import asyncio
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BHEEmission
from app.db.session import get_db
from app.schemas.bhe_batch import (
    BatchConfirm,
    BatchCreate,
    BatchEmissionResponse,
    BatchItemCreate,
    BatchItemResponse,
    BatchItemUpdate,
    BatchPreview,
    BatchResponse,
    BatchUpdate,
    DiagnosticPreviewResponse,
    ManualBatchCreate,
    ManualBatchCommonUpdate,
)
from app.schemas.bhe import BHERequest
from app.services.bhe_batch_service import (
    BatchConflictError,
    BatchItemNotFoundError,
    BatchNotFoundError,
    BatchValidationError,
    claim_ready_batch,
    run_sii_batch_worker,
    add_item,
    apply_global_detail,
    create_batch,
    delete_item,
    get_batch,
    list_batches,
    prepare_batch,
    retry_sii_batch_worker,
    retry_failed_item,
    update_item,
    update_batch,
    run_preview_only_item,
    validate_preview_only_item,
    create_manual_batch,
    clone_editable_manual_batch,
    apply_manual_common_to_pending,
    append_manual_item,
    replace_manual_item,
    item_retryability,
)
from app.services.sii_browser_service import RETENTION_DIAGNOSTIC_PREVIEW_ENV, SiiPocSettings
_sii_batch_tasks: set[asyncio.Task] = set()


router = APIRouter(prefix="/bhe/batches", tags=["BHE batches"])


@router.post("/manual", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_manual_batch_endpoint(payload: ManualBatchCreate, db: Session = Depends(get_db)):
    try:
        batch = create_manual_batch(db, payload)
        return _batch_response(batch, db)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/manual-items", response_model=BatchResponse)
def append_manual_item_endpoint(batch_id: int, payload: BHERequest, db: Session = Depends(get_db)):
    try:
        return _batch_response(append_manual_item(db, batch_id, payload), db)
    except Exception as error:
        _raise_service_error(error)


@router.patch("/{batch_id}/manual-items/{item_id}", response_model=BatchResponse)
def replace_manual_item_endpoint(batch_id: int, item_id: int, payload: BHERequest, db: Session = Depends(get_db)):
    try:
        return _batch_response(replace_manual_item(db, batch_id, item_id, payload), db)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/clone-editable", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def clone_editable_manual_batch_endpoint(batch_id: int, db: Session = Depends(get_db)):
    try:
        return _batch_response(clone_editable_manual_batch(db, batch_id), db)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/apply-manual-common", response_model=BatchResponse)
def apply_manual_common_endpoint(batch_id: int, payload: ManualBatchCommonUpdate, db: Session = Depends(get_db)):
    try:
        return _batch_response(apply_manual_common_to_pending(db, batch_id, payload), db)
    except Exception as error:
        _raise_service_error(error)


def _emission_response(batch, results):
    return BatchEmissionResponse(
        batch_id=batch.id,
        status=batch.status,
        provider=batch.provider,
        total=len(batch.items),
        success=sum(item.status == "success" for item in batch.items),
        failed=sum(item.status == "failed" for item in batch.items),
        unknown=sum(item.status == "emission_status_unknown" for item in batch.items),
        pending=sum(item.status == "pending" for item in batch.items),
        processing=sum(item.status == "processing" for item in batch.items),
        results=results,
    )


def _batch_response(batch, db: Session):
    response = BatchResponse.model_validate(batch)
    emission_ids = [item.bhe_emission_id for item in batch.items if item.bhe_emission_id is not None]
    resolved_names = {}
    if emission_ids:
        resolved_names = dict(db.execute(
            select(BHEEmission.id, BHEEmission.sii_resolved_recipient_name)
            .where(BHEEmission.id.in_(emission_ids))
        ).all())
    for item_response, item in zip(response.items, batch.items):
        item_response.retryable, item_response.retry_block_reason = item_retryability(db, item)
        item_response.sii_resolved_recipient_name = resolved_names.get(item.bhe_emission_id)
    return response


def _raise_service_error(error: Exception) -> None:
    if isinstance(error, (BatchNotFoundError, BatchItemNotFoundError)):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, BatchConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, BatchValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_batch_endpoint(payload: BatchCreate, db: Session = Depends(get_db)):
    try:
        return create_batch(db, payload)
    except Exception as error:
        _raise_service_error(error)


@router.get("", response_model=list[BatchResponse])
def list_batches_endpoint(db: Session = Depends(get_db)):
    return [_batch_response(batch, db) for batch in list_batches(db)]


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch_endpoint(batch_id: int, db: Session = Depends(get_db)):
    try:
        return _batch_response(get_batch(db, batch_id), db)
    except Exception as error:
        _raise_service_error(error)


@router.patch("/{batch_id}", response_model=BatchResponse)
def update_batch_endpoint(
    batch_id: int, payload: BatchUpdate, db: Session = Depends(get_db)
):
    try:
        return update_batch(db, batch_id, payload)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/apply-detail", response_model=BatchResponse)
def apply_global_detail_endpoint(batch_id: int, db: Session = Depends(get_db)):
    try:
        return apply_global_detail(db, batch_id)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/items", response_model=BatchItemResponse, status_code=201)
def add_item_endpoint(
    batch_id: int, payload: BatchItemCreate, db: Session = Depends(get_db)
):
    try:
        return add_item(db, batch_id, payload)
    except Exception as error:
        _raise_service_error(error)


@router.patch("/{batch_id}/items/{item_id}", response_model=BatchItemResponse)
def update_item_endpoint(
    batch_id: int,
    item_id: int,
    payload: BatchItemUpdate,
    db: Session = Depends(get_db),
):
    try:
        return update_item(db, batch_id, item_id, payload)
    except Exception as error:
        _raise_service_error(error)


@router.delete("/{batch_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_endpoint(batch_id: int, item_id: int, db: Session = Depends(get_db)):
    try:
        delete_item(db, batch_id, item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/prepare", response_model=BatchPreview)
def prepare_batch_endpoint(batch_id: int, db: Session = Depends(get_db)):
    try:
        return prepare_batch(db, batch_id)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/emit", response_model=BatchEmissionResponse)
async def emit_batch_endpoint(
    batch_id: int,
    confirmation: BatchConfirm,
    db: Session = Depends(get_db),
):
    if confirmation.confirm is not True:
        raise HTTPException(status_code=422, detail="confirm debe ser true")
    try:
        batch = get_batch(db, batch_id)
        if batch.provider != "sii":
            raise BatchConflictError("Los batches históricos SimpleFactura no pueden emitir nuevas BHE")
        claimed = claim_ready_batch(db, batch_id)

        async def run_sii_batch() -> None:
            await asyncio.to_thread(run_sii_batch_worker, batch_id)

        task = asyncio.create_task(run_sii_batch())
        _sii_batch_tasks.add(task)
        task.add_done_callback(_sii_batch_tasks.discard)
        return _emission_response(claimed, [])
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/retry-failed", response_model=BatchEmissionResponse)
async def retry_failed_endpoint(
    batch_id: int,
    confirmation: BatchConfirm,
    db: Session = Depends(get_db),
):
    if confirmation.confirm is not True:
        raise HTTPException(status_code=422, detail="confirm debe ser true")
    try:
        current = get_batch(db, batch_id)
        if current.provider != "sii":
            raise BatchConflictError("Los batches históricos SimpleFactura no pueden reintentarse")
        batch, results = await asyncio.to_thread(retry_sii_batch_worker, batch_id)
        db.expire_all()
        return _emission_response(batch, results)
    except Exception as error:
        _raise_service_error(error)


@router.post("/{batch_id}/items/{item_id}/retry", response_model=BatchEmissionResponse)
async def retry_failed_item_endpoint(
    batch_id: int,
    item_id: int,
    confirmation: BatchConfirm,
    db: Session = Depends(get_db),
):
    if confirmation.confirm is not True:
        raise HTTPException(status_code=422, detail="confirm debe ser true")
    try:
        batch = get_batch(db, batch_id)
        if batch.provider != "sii":
            raise BatchConflictError("Los items históricos SimpleFactura no pueden reintentarse")
        batch, results = await retry_failed_item(db, batch_id, item_id)
        return _emission_response(batch, results)
    except Exception as error:
        _raise_service_error(error)


@router.post(
    "/{batch_id}/items/{item_id}/diagnostic-preview",
    response_model=DiagnosticPreviewResponse,
)
async def diagnostic_preview_endpoint(
    batch_id: int,
    item_id: int,
    confirmation: BatchConfirm,
    db: Session = Depends(get_db),
):
    """Run one explicitly enabled, non-emitting SII preview diagnostic."""
    if not SiiPocSettings.from_environment().diagnostic_preview_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Diagnóstico preview-only deshabilitado ({RETENTION_DIAGNOSTIC_PREVIEW_ENV})",
        )
    if confirmation.confirm is not True:
        raise HTTPException(status_code=422, detail="confirm debe ser true")
    try:
        batch, item = validate_preview_only_item(db, batch_id, item_id)
        result = await asyncio.to_thread(run_preview_only_item, batch, item)
        payload = asdict(result)
        payload["artifact_dir"] = payload.pop("diagnostic_artifact_dir", None)
        return DiagnosticPreviewResponse(batch_id=batch_id, item_id=item_id, **payload)
    except Exception as error:
        _raise_service_error(error)
