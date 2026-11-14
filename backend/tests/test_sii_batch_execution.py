import asyncio
from contextlib import contextmanager
from datetime import date
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BHEEmission
from app.schemas.bhe import BHERequest
from app.schemas.bhe_batch import BatchCreate, BatchItemCreate
from app.schemas.client import ClientCreate
from app.schemas.retention import RetentionMode
from app.services.bhe_batch_service import (
    _process_items,
    _process_sii_items_in_session,
    run_sii_batch_worker,
    add_item,
    create_batch,
    effective_retention_mode,
    get_batch,
    prepare_batch,
)
from app.services.bhe_emission_history_service import (
    create_pending_emission,
    mark_emission_failed,
    mark_emission_status_unknown,
    mark_emission_success,
)
from app.services.client_service import create_client


class FakeSIIProvider:
    name = "sii"

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.requests = []

    async def execute(self, db, request: BHERequest, client_id=None, batch_item_id=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.requests.append(request)
        await asyncio.sleep(0)
        emission = create_pending_emission(
            db, request, client_id=client_id, provider="sii", batch_item_id=batch_item_id
        )
        result = {
            "folio": 900 + emission.id,
            "fecha_emision": "2026-09-15 10:00:00",
            "codigo_barras": f"BAR-{emission.id}",
            "provider_message": "BHE emitida y resultado SII validado.",
        }
        mark_emission_success(db, emission, result)
        self.active -= 1
        return SimpleNamespace(
            emission=emission,
            status="success",
            message="success",
            error=None,
        )

    async def confirm_final(self, db, emission):
        raise AssertionError("One-pass SII batch must not reconstruct confirm-final")

    def execute_in_batch_session(self, db, request, browser, client_id=None, batch_item_id=None):
        self.requests.append(request)
        self.sessions.append(browser)
        browser.prepare_clean_retention_validation()
        emission = create_pending_emission(
            db, request, client_id=client_id, provider="sii", batch_item_id=batch_item_id
        )
        outcome = self.outcomes.pop(0) if getattr(self, "outcomes", None) else "success"
        if outcome == "success":
            mark_emission_success(db, emission, {
                "folio": 900 + emission.id,
                "fecha_emision": "2026-09-15 10:00:00",
                "provider_message": "synthetic success",
            })
        elif outcome == "failed":
            mark_emission_failed(db, emission, "synthetic pre-click failure")
        else:
            mark_emission_status_unknown(db, emission, "synthetic post-click ambiguity")
        return SimpleNamespace(
            emission=emission,
            status="emission_status_unknown" if outcome == "unknown" else outcome,
            message=outcome,
            error=None if outcome == "success" else RuntimeError(outcome),
        )


class FakeBatchBrowserSession:
    def __init__(self, recovery=(True, True)):
        self.context_created = 0
        self.context_closed = 0
        self.recovery = list(recovery)
        self.recovery_calls = 0
        self.restart_calls = 0
        self.clean_start_calls = 0

    def prepare_clean_retention_validation(self):
        self.clean_start_calls += 1
        return object()

    @contextmanager
    def batch_session(self):
        self.context_created += 1
        try:
            yield self
        finally:
            self.context_closed += 1

    def recover_to_known_bhe_start(self):
        self.recovery_calls += 1
        return self.recovery.pop(0) if self.recovery else True

    def prepare_next_bhe(self):
        return self.recover_to_known_bhe_start()

    def restart_batch_context(self):
        self.restart_calls += 1
        self.context_closed += 1
        self.context_created += 1
        return True


def _make_three_item_sii_batch(db):
    clients = []
    for rut, index in (("99990001-1", 1), ("99990011-9", 2), ("99990019-4", 3)):
        clients.append(create_client(db, ClientCreate(
            rut=rut, razon_social=f"Cliente {index}", direccion=f"Direccion {index}",
            region=13, comuna="Santiago", detalle_default=[{"descripcion": "Servicio"}],
        )))
    batch = create_batch(db, BatchCreate(
        period="2026-09", fecha_emision=date(2026, 9, 15),
        retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
    ))
    items = [add_item(db, batch.id, BatchItemCreate(client_id=client.id, monto=1000)) for client in clients]
    prepare_batch(db, batch.id)
    return get_batch(db, batch.id), items


def test_three_item_batch_uses_one_session_and_context(execution_db):
    batch, items = _make_three_item_sii_batch(execution_db)
    browser = FakeBatchBrowserSession()
    provider = FakeSIIProvider()
    provider.sessions = []

    results = _process_sii_items_in_session(
        execution_db, batch, items, provider, browser, 0
    )

    assert [result.status.value for result in results] == ["success"] * 3
    assert browser.context_created == 1
    assert browser.context_closed == 1
    assert len(provider.sessions) == 3
    assert all(session is browser for session in provider.sessions)
    assert len({id(session) for session in provider.sessions}) == 1
    assert [item.status for item in items] == ["success"] * 3
    assert batch.status == "completed"


def test_pre_click_failure_starts_next_item_clean_in_same_context(execution_db):
    batch, items = _make_three_item_sii_batch(execution_db)
    browser = FakeBatchBrowserSession(recovery=(True,))
    provider = FakeSIIProvider()
    provider.sessions = []
    provider.outcomes = ["failed", "success", "success"]

    _process_sii_items_in_session(execution_db, batch, items, provider, browser, 0)

    assert [item.status for item in items] == ["failed", "success", "success"]
    assert browser.clean_start_calls == 3
    assert browser.recovery_calls == 0
    assert browser.restart_calls == 0
    assert browser.context_created == browser.context_closed == 1
    assert len(provider.sessions) == 3


def test_pre_click_failure_does_not_restart_context_before_next_item(execution_db):
    batch, items = _make_three_item_sii_batch(execution_db)
    browser = FakeBatchBrowserSession(recovery=(False,))
    provider = FakeSIIProvider()
    provider.sessions = []
    provider.outcomes = ["failed", "success", "success"]

    _process_sii_items_in_session(execution_db, batch, items, provider, browser, 0)

    assert [item.status for item in items] == ["failed", "success", "success"]
    assert browser.clean_start_calls == 3
    assert browser.recovery_calls == 0
    assert browser.restart_calls == 0
    assert browser.context_created == browser.context_closed == 1


def test_unknown_is_not_reprocessed_and_next_item_runs_clean(execution_db):
    batch, items = _make_three_item_sii_batch(execution_db)
    browser = FakeBatchBrowserSession(recovery=(True, True))
    provider = FakeSIIProvider()
    provider.sessions = []
    provider.outcomes = ["unknown", "success", "success"]

    results = _process_sii_items_in_session(
        execution_db, batch, items, provider, browser, 0
    )

    assert [result.status.value for result in results] == [
        "emission_status_unknown", "success", "success"
    ]
    assert [item.status for item in items] == [
        "emission_status_unknown", "success", "success"
    ]
    assert len(provider.requests) == 3
    assert browser.clean_start_calls == 3
    assert items[0].final_click_occurred is True
    assert items[0].bhe_emission_id is not None


@pytest.mark.asyncio
async def test_batch_worker_creates_db_and_playwright_owner_in_one_worker_thread(
    execution_db, monkeypatch
):
    batch = create_batch(execution_db, BatchCreate(
        period="2026-09", fecha_emision=date(2026, 9, 15),
        retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
    ))
    batch.status = "processing"
    execution_db.commit()
    factory = sessionmaker(bind=execution_db.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr("app.services.bhe_batch_service.get_session_factory", lambda: factory)
    created = {}

    def make_browser(_settings):
        created["owner_thread"] = threading.get_ident()
        return FakeBatchBrowserSession()

    monkeypatch.setattr("app.services.bhe_batch_service.SiiBrowserService", make_browser)

    def observe_worker(db, batch, items, provider, browser, delay, **kwargs):
        created["process_thread"] = threading.get_ident()
        created["db_thread"] = threading.get_ident()
        created["browser"] = browser
        assert provider.name == "sii"
        assert delay >= 0

    monkeypatch.setattr("app.services.bhe_batch_service._process_sii_items_in_session", observe_worker)
    caller_thread = threading.get_ident()

    await asyncio.to_thread(run_sii_batch_worker, batch.id)

    assert created["owner_thread"] == created["process_thread"] == created["db_thread"]
    assert created["owner_thread"] != caller_thread


@pytest.fixture
def execution_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sii-batch.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_sii_batch_uses_snapshots_retention_and_strict_sequence(execution_db, monkeypatch):
    first = create_client(execution_db, ClientCreate(
        rut="99990001-1", razon_social="Receptor Test 002", direccion="DIRECCION PRUEBA 014",
        region=10, comuna="Puerto Varas", detalle_default=[{"descripcion": "Servicio A"}],
    ))
    second = create_client(execution_db, ClientCreate(
        rut="99990011-9", razon_social="Receptor Test 004", direccion="DIRECCION PRUEBA 016",
        region=13, comuna="Santiago", detalle_default=[{"descripcion": "Servicio B"}],
    ))
    batch = create_batch(execution_db, BatchCreate(
        period="2026-09", fecha_emision=date(2026, 9, 15),
        detalle_global="Detalle snapshot", retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
    ))
    item_a = add_item(execution_db, batch.id, BatchItemCreate(client_id=first.id, monto=1000))
    item_b = add_item(execution_db, batch.id, BatchItemCreate(
        client_id=second.id, monto=2000, retention_mode_override=RetentionMode.RECEIVER_RETAINS_PPM,
    ))
    prepare_batch(execution_db, batch.id)
    first.direccion = "DIRECCION PRUEBA 020"
    execution_db.commit()

    fake = FakeSIIProvider()
    monkeypatch.setattr("app.services.bhe_batch_service.get_bhe_provider", lambda: fake)
    loaded = get_batch(execution_db, batch.id)
    results = await _process_items(execution_db, loaded, list(loaded.items), None, 0)

    assert [result.status.value for result in results] == ["success", "success"]
    assert fake.max_active == 1
    assert [request.direccion for request in fake.requests] == ["DIRECCION PRUEBA 014", "DIRECCION PRUEBA 016"]
    assert [request.fecha_emision for request in fake.requests] == [date(2026, 9, 15)] * 2
    assert [request.retention_mode.value for request in fake.requests] == [
        "issuer_handles_ppm", "receiver_retains_ppm"
    ]
    assert all(item.bhe_emission_id for item in loaded.items)
    assert [item.batch_item_id for item in execution_db.query(BHEEmission).order_by(BHEEmission.id)] == [item_a.id, item_b.id]
    emissions = execution_db.query(BHEEmission).order_by(BHEEmission.id).all()
    assert [(emission.status, emission.folio, emission.codigo_barras) for emission in emissions] == [
        ("success", 901, "BAR-1"), ("success", 902, "BAR-2")
    ]
