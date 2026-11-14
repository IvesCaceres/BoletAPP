import asyncio
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fastapi.testclient import TestClient

from app.core.config import BHEProviderName
from app.main import app
from app.services.bhe_provider import (
    SIIBHEProvider,
    _run_sii_preview_with_windows_subprocess_support,
    get_bhe_provider,
)
from app.services.sii_browser_service import SIIBHEAutomationService, normalize_sii_region
from app.schemas.bhe import BHERequest
from app.schemas.retention import RetentionMode
from app.db.base import Base
from app.db.models import BHEEmission
from app.services.sii_browser_service import SiiBrowserService, SiiPocSettings


def test_only_sii_provider_is_available():
    provider = get_bhe_provider()
    assert isinstance(provider, SIIBHEProvider)
    assert provider.name == BHEProviderName.SII.value == "sii"


def test_numeric_region_is_mapped_to_sii_semantic_region():
    assert normalize_sii_region(10) == "LOS LAGOS"
    assert normalize_sii_region("LOS LAGOS") == "LOS LAGOS"


def test_sii_playwright_worker_restores_event_loop_policy(monkeypatch):
    class FakeAutomation:
        def run_preview(self, data, *, interactive_console):
            assert interactive_console is False
            return {"preview_validated": True}

    previous_policy = asyncio.get_event_loop_policy()
    monkeypatch.setattr(asyncio, "WindowsProactorEventLoopPolicy", lambda: previous_policy)
    result = _run_sii_preview_with_windows_subprocess_support(FakeAutomation(), object())
    assert result["preview_validated"] is True
    assert asyncio.get_event_loop_policy() is previous_policy


def test_new_emission_request_does_not_accept_provider_selector():
    client = TestClient(app)
    response = client.post(
        "/bhe/emit",
        json={
            "provider": "simplefactura",
            "rut_receptor": "99990008-9",
            "direccion": "DIRECCION PRUEBA 007",
            "region": 10,
            "comuna": "Puerto Varas",
            "detalle": [{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 1000}],
            "fecha_emision": date.today().isoformat(),
        },
    )
    assert response.status_code == 422


def test_sii_provider_keeps_final_emission_automation(monkeypatch):
    assert hasattr(SIIBHEAutomationService, "emit_final")


def test_individual_provider_uses_one_batch_session_and_one_page(monkeypatch, tmp_path):
    class FakeBrowser:
        def __init__(self, _settings):
            self.settings = SimpleNamespace(
                profile_dir=tmp_path / "profile",
                debug_dir=tmp_path / "debug",
            )
            self.current_stage = "initialized"
            self.last_retention_diagnostics = {}
            self.session_enters = 0
            self.session_exits = 0
            self.page_count = 0

        @property
        def session_active(self):
            return self.session_enters > self.session_exits

        def set_diagnostic_context(self, **_kwargs):
            pass

        def _reset_retention_diagnostics(self, _mode):
            self.last_retention_diagnostics = {}

        def _set_stage(self, stage):
            self.current_stage = stage

        @contextmanager
        def batch_session(self):
            self.session_enters += 1
            self.page_count = 1
            try:
                yield self
            finally:
                self.session_exits += 1

    emission = SimpleNamespace(id=1, sii_retention_diagnostics=None)
    result = SimpleNamespace(emission=emission, status="success")
    browser_holder = {}

    def make_browser(settings):
        browser_holder["browser"] = FakeBrowser(settings)
        return browser_holder["browser"]

    monkeypatch.setattr("app.services.bhe_provider.SiiBrowserService", make_browser)
    monkeypatch.setattr("app.services.bhe_provider.create_pending_emission", lambda *args, **kwargs: emission)
    monkeypatch.setattr(
        SIIBHEProvider,
        "_execute_in_session",
        lambda self, *args, **kwargs: result,
    )
    db = SimpleNamespace(commit=lambda: None)
    request = BHERequest(
        rut_receptor="99990008-9", direccion="Direccion", region=13,
        comuna="Santiago", fecha_emision=date(2026, 9, 21),
        retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
        detalle=[{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 1000}],
    )

    response = asyncio.run(SIIBHEProvider().execute(db, request))

    browser = browser_holder["browser"]
    assert response is result
    assert browser.session_enters == browser.session_exits == 1
    assert browser.page_count == 1


def test_individual_browser_start_failure_is_sanitized_and_has_no_retry(monkeypatch, tmp_path):
    class FakeBrowser:
        def __init__(self, _settings):
            self.settings = SimpleNamespace(
                profile_dir=tmp_path / "profile",
                debug_dir=tmp_path / "debug",
            )
            self.current_stage = "initialized"
            self.last_retention_diagnostics = {}
            self.last_preview_result = {}

        @property
        def session_active(self):
            return True

        def set_diagnostic_context(self, **_kwargs):
            pass

        def _reset_retention_diagnostics(self, _mode):
            self.last_retention_diagnostics = {}

        def _set_stage(self, stage):
            self.current_stage = stage

        @contextmanager
        def batch_session(self):
            error = RuntimeError("TargetClosedError: local profile details")
            setattr(error, "browser_session_active", True)
            raise error
            yield self

    emission = SimpleNamespace(id=2, sii_retention_diagnostics=None, status="pending", folio=None)
    failures = []
    monkeypatch.setattr("app.services.bhe_provider.SiiBrowserService", FakeBrowser)
    monkeypatch.setattr("app.services.bhe_provider.create_pending_emission", lambda *args, **kwargs: emission)
    monkeypatch.setattr("app.services.bhe_provider.mark_emission_failed", lambda _db, item, message: failures.append((item, message)))
    db = SimpleNamespace(commit=lambda: None)
    request = BHERequest(
        rut_receptor="99990008-9", direccion="Direccion", region=13,
        comuna="Santiago", fecha_emision=date(2026, 9, 21),
        retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
        detalle=[{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 1000}],
    )

    response = asyncio.run(SIIBHEProvider().execute(db, request))

    assert response.status == "failed"
    assert response.emission.folio is None
    assert len(failures) == 1
    assert response.emission.sii_retention_diagnostics["failure_stage"] == "browser_starting"
    assert response.emission.sii_retention_diagnostics["failure_exception_type"] == "RuntimeError"
    assert response.emission.sii_retention_diagnostics["browser_session_active"] is True
    assert response.emission.sii_retention_diagnostics["failure_message"] == "Browser, context or page closed."
    assert "local profile details" not in response.emission.sii_retention_diagnostics["failure_message"]


@pytest.mark.parametrize("outcome", ["success", "pre_click_failure", "unknown"])
def test_individual_real_session_preserves_result_and_closes_on_failure(tmp_path, monkeypatch, outcome):
    context = SimpleNamespace(pages=[SimpleNamespace(set_default_timeout=lambda _: None)], close=Mock())
    runtime = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=Mock(return_value=context)), stop=Mock())
    manager = SimpleNamespace(start=Mock(return_value=runtime))
    monkeypatch.setattr("app.services.sii_browser_service.sync_playwright", lambda: manager)
    browser = SiiBrowserService(SiiPocSettings(profile_dir=tmp_path / "profile"))
    monkeypatch.setattr("app.services.bhe_provider.SiiBrowserService", lambda _: browser)
    final = Mock()

    def emit(_self, _data):
        final()
        if outcome == "success":
            return {"folio": 999, "provider_message": "Synthetic success"}
        error = RuntimeError("Synthetic failure")
        error.click_performed = outcome == "unknown"
        raise error

    monkeypatch.setattr(SIIBHEAutomationService, "emit_final", emit)
    # Even if teardown also fails, it must not change success/unknown.
    runtime.stop.side_effect = RuntimeError("Synthetic runtime close failure")
    engine = create_engine(f"sqlite:///{tmp_path / 'individual.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    request = BHERequest(rut_receptor="99990004-6", direccion="Ficticia", region=13, comuna="Santiago",
                         detalle=[{"descripcion": "Prueba", "cantidad": 1, "precio_unitario": 1000}])
    with Session(engine) as db:
        result = asyncio.run(SIIBHEProvider().execute(db, request))
        assert result.status == {"success": "success", "pre_click_failure": "failed", "unknown": "emission_status_unknown"}[outcome]
        assert db.query(BHEEmission).count() == 1
        assert result.emission.folio == (999 if outcome == "success" else None)
    assert final.call_count == runtime.chromium.launch_persistent_context.call_count == 1
    assert context.close.call_count == runtime.stop.call_count == 1
    assert browser._context is None
    engine.dispose()
