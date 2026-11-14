from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BHEBatch, BHEBatchItem, BHEEmission
from app.schemas.bhe import BHERequest
from app.services.bhe_batch_service import (
    _SII_BATCH_WORKER_LOCK,
    _process_sii_items_in_session,
    _terminal_batch_status,
    item_retryability,
    reconcile_batch_status,
    reconcile_interrupted_batches,
)
from app.services.bhe_emission_history_service import create_pending_emission, mark_emission_failed


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch-reconciliation.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def add_batch(db, item_statuses, *, batch_status="processing"):
    batch = BHEBatch(
        period="2026-11", status=batch_status, fecha_emision=date(2026, 11, 5),
        provider="sii", source="manual",
    )
    db.add(batch)
    db.flush()
    items = []
    for item_status in item_statuses:
        item = BHEBatchItem(
            batch_id=batch.id, client_id=None, rut_receptor="999900011",
            razon_social="RECEPTOR PRUEBA 06", direccion="DIRECCION PRUEBA 002", region=13,
            comuna="Santiago", fecha_emision=date(2026, 11, 5),
            detalle=[{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 45000}],
            retention_mode_override="issuer_handles_ppm", monto=45000,
            status=item_status, final_click_occurred=False,
        )
        db.add(item)
        items.append(item)
    db.commit()
    db.refresh(batch)
    return batch, items


def add_safe_failed_emission(db, item):
    request = BHERequest.model_validate({
        "rut_receptor": item.rut_receptor,
        "razon_social": item.razon_social,
        "direccion": item.direccion,
        "region": item.region,
        "comuna": item.comuna,
        "fecha_emision": item.fecha_emision.isoformat(),
        "retention_mode": item.retention_mode_override,
        "detalle": [{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": item.monto}],
    })
    emission = create_pending_emission(
        db, request, client_id=None, provider="sii", batch_item_id=item.id
    )
    mark_emission_failed(db, emission, "Synthetic preview failure")
    emission.sii_retention_diagnostics = {"failure_stage": "preview_reached"}
    item.bhe_emission_id = emission.id
    item.status = "failed"
    item.error_message = "Exception at stage preview_reached"
    db.commit()
    db.refresh(item)
    db.refresh(emission)
    return emission


def test_stale_processing_batch_with_terminal_safe_failure_is_reconciled(db):
    batch, (item,) = add_batch(db, ["failed"])
    emission = add_safe_failed_emission(db, item)
    original_error = item.error_message
    original_diagnostics = dict(emission.sii_retention_diagnostics)
    assert item_retryability(db, item) == (True, None)

    assert reconcile_batch_status(db, batch.id) is True
    db.expire_all()
    batch = db.get(BHEBatch, batch.id)
    item = db.get(BHEBatchItem, item.id)
    emission = db.get(BHEEmission, emission.id)
    assert batch.status == "completed_with_errors"
    assert item.status == "failed"
    assert item.error_message == original_error
    assert item.final_click_occurred is False
    assert item_retryability(db, item) == (True, None)
    assert item.folio is None
    assert emission.sii_retention_diagnostics == original_diagnostics


def test_processing_and_pending_items_are_not_reconciled(db):
    pending_batch, _ = add_batch(db, ["pending"])
    assert reconcile_batch_status(db, pending_batch.id) is False
    assert db.get(BHEBatch, pending_batch.id).status == "processing"
    # Current schema does not permit a persisted item-level "processing" state;
    # keep the guard for legacy/future rows as well.
    assert _terminal_batch_status(SimpleNamespace(items=[SimpleNamespace(status="processing")])) is None


def test_reconciliation_refuses_to_run_while_worker_lock_is_held(db):
    batch, _ = add_batch(db, ["failed"])
    assert _SII_BATCH_WORKER_LOCK.acquire(blocking=False)
    try:
        assert reconcile_batch_status(db, batch.id) is False
        assert db.get(BHEBatch, batch.id).status == "processing"
    finally:
        _SII_BATCH_WORKER_LOCK.release()


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["success"], "completed"),
        (["success", "failed"], "completed_with_errors"),
        (["failed"], "completed_with_errors"),
        (["emission_status_unknown"], "completed_with_errors"),
    ],
)
def test_reconciliation_derives_parent_without_rewriting_item_semantics(db, statuses, expected):
    batch, items = add_batch(db, statuses)
    before = [(item.status, item.final_click_occurred, item.error_message) for item in items]
    assert reconcile_batch_status(db, batch.id) is True
    db.expire_all()
    assert db.get(BHEBatch, batch.id).status == expected
    assert [(item.status, item.final_click_occurred, item.error_message) for item in items] == before


def test_interrupted_recovery_commits_parent_even_when_no_items_need_quarantine(db):
    batch, (item,) = add_batch(db, ["failed"])
    add_safe_failed_emission(db, item)
    assert reconcile_interrupted_batches(db, batch.id) == 0
    db.expire_all()
    assert db.get(BHEBatch, batch.id).status == "completed_with_errors"
    assert db.get(BHEBatchItem, item.id).status == "failed"


def test_browser_context_exit_exception_still_finalizes_terminal_item(db):
    batch, (item,) = add_batch(db, ["pending"], batch_status="ready")

    class Browser:
        @contextmanager
        def batch_session(self):
            yield self
            raise RuntimeError("synthetic context teardown error")

    class Provider:
        def execute_in_batch_session(self, session, request, browser, client_id=None, batch_item_id=None):
            emission = create_pending_emission(
                session, request, client_id=None, provider="sii", batch_item_id=batch_item_id
            )
            mark_emission_failed(session, emission, "Synthetic preview failure")
            emission.sii_retention_diagnostics = {"failure_stage": "preview_reached"}
            session.commit()
            return SimpleNamespace(status="failed", emission=emission, message="failed")

    with pytest.raises(RuntimeError, match="context teardown"):
        _process_sii_items_in_session(db, batch, [item], Provider(), Browser(), 0)
    db.expire_all()
    assert db.get(BHEBatch, batch.id).status == "completed_with_errors"
    assert db.get(BHEBatchItem, item.id).status == "failed"
    assert item_retryability(db, db.get(BHEBatchItem, item.id)) == (True, None)
