from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BHEBatch, BHEEmission, Client
from app.db.session import get_db
from app.main import app
from app.services import manual_date_policy
from app.services import bhe_batch_service
from app.services.bhe_batch_service import BatchValidationError, claim_ready_batch


TODAY = date(2026, 11, 6)
MINIMUM = date(2026, 8, 8)
TOO_OLD = date(2026, 8, 7)


def payload(day: date, amount: int = 100) -> dict:
    return {
        "rut_receptor": "99990001-1", "direccion": "Puerto Montt",
        "region": 10, "comuna": "Puerto Montt",
        "detalle": [{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": amount}],
        "fecha_emision": day.isoformat(), "retention_mode": "issuer_handles_ppm",
    }


def test_local_date_bounds_are_inclusive_and_reject_day_91():
    assert manual_date_policy.allowed_issue_dates(TODAY) == (MINIMUM, TODAY)
    for allowed in (TODAY, date(2026, 8, 9), MINIMUM):
        assert manual_date_policy.issue_date_error(allowed, TODAY) is None
    assert "90 días" in manual_date_policy.issue_date_error(TOO_OLD, TODAY)
    assert "futura" in manual_date_policy.issue_date_error(date(2026, 11, 7), TODAY)


def test_manual_create_edit_and_common_apply_reject_old_date_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_date_policy, "local_today", lambda: TODAY)
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-date.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        assert client.post("/bhe/batches/manual", json={"items": [payload(TOO_OLD)]}).status_code == 422
        created = client.post("/bhe/batches/manual", json={"items": [payload(MINIMUM)]})
        assert created.status_code == 201, created.text
        batch = created.json()
        assert client.post(f"/bhe/batches/{batch['id']}/manual-items", json=payload(TOO_OLD)).status_code == 422
        item_id = batch["items"][0]["id"]
        assert client.patch(f"/bhe/batches/{batch['id']}/manual-items/{item_id}", json=payload(TOO_OLD)).status_code == 422
        common = {"retention_mode": "issuer_handles_ppm", "direccion": "DIRECCION PRUEBA 026", "region": 10,
                  "comuna": "Puerto Montt", "detalle": "Otro servicio", "fecha_emision": TOO_OLD.isoformat()}
        assert client.post(f"/bhe/batches/{batch['id']}/apply-manual-common", json=common).status_code == 422
        unchanged = client.get(f"/bhe/batches/{batch['id']}").json()
        assert len(unchanged["items"]) == 1
        assert unchanged["items"][0]["fecha_emision"] == MINIMUM.isoformat()
        assert unchanged["items"][0]["direccion"] == "Puerto Montt"
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_one_invalid_of_fourteen_blocks_claim_before_any_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_date_policy, "local_today", lambda: TODAY)
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-claim.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        created = client.post("/bhe/batches/manual", json={"items": [payload(TODAY, 100 + i) for i in range(14)]})
        assert created.status_code == 201, created.text
        batch_id = created.json()["id"]
        batch = db.get(BHEBatch, batch_id)
        batch.items[-1].fecha_emision = TOO_OLD  # Simulates a historical or externally edited item.
        db.commit()
        rejected = client.post(f"/bhe/batches/{batch_id}/emit", json={"confirm": True})
        assert rejected.status_code == 422, rejected.text
        assert "1 con más de 90 días" in rejected.json()["detail"]
        db.refresh(batch)
        assert batch.status == "ready" and batch.confirmed_at is None
        assert db.scalar(select(func.count()).select_from(BHEEmission)) == 0
        assert all(item.status == "pending" for item in batch.items)

        batch.items[-1].fecha_emision = MINIMUM
        db.commit()
        claimed = claim_ready_batch(db, batch_id)
        assert claimed.status == "processing"
        assert db.scalar(select(func.count()).select_from(BHEEmission)) == 0
        assert len(claimed.items) == 14
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_old_failed_batch_can_be_cloned_but_cannot_emit_until_corrected(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_date_policy, "local_today", lambda: TODAY)
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-clone-date.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        created = client.post("/bhe/batches/manual", json={"items": [payload(MINIMUM)]})
        source = db.get(BHEBatch, created.json()["id"])
        source.status = "completed_with_errors"
        source.items[0].status = "failed"
        source.items[0].fecha_emision = TOO_OLD
        source.items[0].error_message = "Historical pre-final failure"
        db.commit()
        cloned = client.post(f"/bhe/batches/{source.id}/clone-editable")
        assert cloned.status_code == 201, cloned.text
        copy = cloned.json()
        assert copy["source_batch_id"] == source.id
        assert copy["items"][0]["fecha_emision"] == TOO_OLD.isoformat()
        assert client.post(f"/bhe/batches/{copy['id']}/emit", json={"confirm": True}).status_code == 422
        common = {"retention_mode": "issuer_handles_ppm", "direccion": "Puerto Montt", "region": 10,
                  "comuna": "Puerto Montt", "detalle": "Servicio", "fecha_emision": MINIMUM.isoformat()}
        applied = client.post(f"/bhe/batches/{copy['id']}/apply-manual-common", json=common)
        assert applied.status_code == 200, applied.text
        assert applied.json()["items"][0]["fecha_emision"] == MINIMUM.isoformat()
        assert db.get(BHEBatch, source.id).items[0].fecha_emision == TOO_OLD
        assert db.get(BHEBatch, source.id).items[0].error_message == "Historical pre-final failure"
        assert db.scalar(select(func.count()).select_from(Client)) == 0

        db.rollback()
        monkeypatch.setattr(bhe_batch_service, "get_session_factory", lambda: sessionmaker(bind=engine, autoflush=False))
        monkeypatch.setattr(bhe_batch_service, "get_bhe_provider", lambda: pytest.fail("SII provider was opened"))
        with pytest.raises(BatchValidationError, match="90 días"):
            bhe_batch_service.retry_sii_batch_worker(source.id)
        db.refresh(source)
        assert source.status == "completed_with_errors"
        assert db.scalar(select(func.count()).select_from(BHEEmission)) == 0
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
