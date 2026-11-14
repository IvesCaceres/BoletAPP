from datetime import date
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BHEBatch, BHEBatchItem, BHEEmission
from app.services.bhe_batch_service import validate_preview_only_item
from app.services.sii_browser_service import (
    BHEAutomationData,
    RetentionMode,
    SiiBrowserService,
    SiiValidationError,
    SiiNavigationState,
    SiiPocSettings,
)
from app.main import create_app


def _failed_item_db(tmp_path, *, item_status="failed", final_click=False, folio=None, provider="sii"):
    engine = create_engine(f"sqlite:///{tmp_path / 'preview-only.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    batch = BHEBatch(
        period="2026-09", status="completed_with_errors", fecha_emision=date(2026, 9, 15),
        retention_mode=RetentionMode.ISSUER_HANDLES_PPM.value, provider=provider,
    )
    db.add(batch)
    db.flush()
    emission = BHEEmission(
        batch_item_id=None, rut_receptor="99990008-9", razon_social="Cliente",
        direccion="Direccion", region=13, comuna="Santiago", detalle=[{"descripcion": "Servicio"}],
        monto_total=1000, fecha_emision_solicitada=date(2026, 9, 15), status="failed",
        provider="sii", folio=folio,
    )
    db.add(emission)
    db.flush()
    item = BHEBatchItem(
        batch_id=batch.id, client_id=1, rut_receptor="99990008-9", razon_social="Cliente",
        direccion="Direccion", region=13, comuna="Santiago", detalle=[{"descripcion": "Servicio"}],
        monto=1000, status=item_status, bhe_emission_id=emission.id,
        final_click_occurred=final_click,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return db, batch, item


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"item_status": "success"}, "fallido"),
        ({"final_click": True}, "final_click"),
        ({"item_status": "emission_status_unknown"}, "desconocido"),
        ({"provider": "simplefactura"}, "proveedor"),
    ],
)
def test_preview_only_guards_reject_unsafe_items(tmp_path, changes, message):
    db, batch, item = _failed_item_db(tmp_path, **changes)
    with pytest.raises(Exception, match=message):
        validate_preview_only_item(db, batch.id, item.id)
    db.close()


def test_preview_only_rejects_existing_folio(tmp_path):
    db, batch, item = _failed_item_db(tmp_path, folio=1001)
    with pytest.raises(Exception, match="folio"):
        validate_preview_only_item(db, batch.id, item.id)
    db.close()


def test_diagnostic_endpoint_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SII_DIAGNOSTIC_PREVIEW_ENABLED", raising=False)
    with TestClient(create_app()) as client:
        response = client.post(
            "/bhe/batches/1/items/1/diagnostic-preview",
            json={"confirm": True},
        )
    assert response.status_code == 403


def test_preview_only_runs_have_unique_manifest_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("SII_DIAGNOSTIC_ARTIFACTS", "1")
    settings = SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=True)
    first = SiiBrowserService(settings)
    second = SiiBrowserService(settings)

    run_id_1, dir_1 = first.begin_preview_only_run(batch_id=1, item_id=1)
    run_id_2, dir_2 = second.begin_preview_only_run(batch_id=1, item_id=1)

    assert run_id_1 != run_id_2
    assert dir_1 != dir_2
    assert (dir_1 / "run_manifest.json").is_file()
    assert (dir_2 / "run_manifest.json").is_file()
    assert json.loads((dir_1 / "run_manifest.json").read_text(encoding="utf-8"))["preview_only"] is True
    assert json.loads((dir_2 / "run_manifest.json").read_text(encoding="utf-8"))["diagnostic_run_id"] == run_id_2


def test_preview_only_manifest_records_artifacts_flag_state(tmp_path, monkeypatch):
    monkeypatch.delenv("SII_DIAGNOSTIC_ARTIFACTS", raising=False)
    settings = SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=False)
    service = SiiBrowserService(settings)
    _run_id, artifact_dir = service.begin_preview_only_run(batch_id=1, item_id=1)
    manifest = json.loads((artifact_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["diagnostic_artifacts_enabled"] is False
    assert (artifact_dir / "run_manifest.json").is_file()


def test_preview_only_valid_preview_has_no_final_click(monkeypatch, tmp_path):
    class Page:
        def set_default_timeout(self, _timeout):
            pass

        def goto(self, *_args, **_kwargs):
            pass

    class Context:
        pages = [Page()]

        def close(self):
            pass

    class Runtime:
        chromium = SimpleNamespace(launch_persistent_context=lambda **_kwargs: Context())

    class Manager:
        def __enter__(self):
            return Runtime()

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("app.services.sii_browser_service.sync_playwright", lambda: Manager())
    service = SiiBrowserService(SiiPocSettings(profile_dir=tmp_path / "profile"))
    monkeypatch.setattr(service, "_ensure_preview_retention_submission", lambda *_args: ["RETCONTRIBUYENTE"])
    page = Context.pages[0]
    monkeypatch.setattr(service, "ensure_authenticated", lambda *_args, **_kwargs: None)
    states = iter([SiiNavigationState.BHE_FORM, SiiNavigationState.BHE_PREVIEW])
    monkeypatch.setattr(service, "navigate_to_bhe", lambda _page: SiiNavigationState.RETENTION_SELECTION)
    monkeypatch.setattr(service, "detect_state", lambda _page: next(states))
    def select_retention(_page, mode):
        service._last_retention_diagnostics.update({
            "selection_verified": True,
            "requested_retention_mode": mode.value,
            "selected_retention_mode": mode.value,
        })
        return page
    monkeypatch.setattr(service, "_select_retention_and_continue", select_retention)
    monkeypatch.setattr(service, "_wait_for_preview_stable", lambda _page: None)
    monkeypatch.setattr(service, "_final_emit_button", lambda _page: SimpleNamespace(click=lambda: pytest.fail("final click")))
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.map_form", lambda *_args: SimpleNamespace(required_ready=True))
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.fill_bhe_form", lambda *_args: None)
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.validate_bhe_form", lambda *_args: None)
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.confirm_to_preview", lambda *_args: (page, []))
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.validate_preview", lambda *_args: {"preview_retention_mode_ok": True})
    monkeypatch.setattr(service, "run_bhe_final_emission", lambda *_args: pytest.fail("final emission path called"))

    result = service.run_preview_only(BHEAutomationData(
        fecha_emision=date(2026, 9, 15), rut_receptor="99990008-9", razon_social="Cliente",
        direccion="Direccion", region="METROPOLITANA", comuna="Santiago", detalle="Servicio",
        monto=1000, retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
    ))

    assert result.preview_reached is True
    assert result.structural_preview_valid is True
    assert result.blocked_reason is None


def test_preview_submit_revalidates_and_repairs_hidden_issuer_value():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<form><input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">'
            '<button name="cmdconfirmar" type="button">Confirmar</button></form>'
        )
        service = SiiBrowserService(SiiPocSettings())
        actual = service._ensure_preview_retention_submission(
            page, RetentionMode.ISSUER_HANDLES_PPM
        )
        assert actual == ["RETCONTRIBUYENTE"]
        assert page.locator("input[name='OptTipoRetencion']").input_value() == "RETCONTRIBUYENTE"
        browser.close()


def test_preview_submit_preserves_receiver_value():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<form><input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">'
            '<button name="cmdconfirmar" type="button">Confirmar</button></form>'
        )
        service = SiiBrowserService(SiiPocSettings())
        actual = service._ensure_preview_retention_submission(
            page, RetentionMode.RECEIVER_RETAINS_PPM
        )
        assert actual == ["RETRECEPTOR"]
        browser.close()


def test_preview_submit_targets_confirmation_form_not_another_retention_form():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <form id="selection-form">
              <input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">
            </form>
            <form id="form-b" action="/confirmar" method="post">
              <input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">
              <button name="confirm-emission" type="button">Confirmar Emisión</button>
            </form>
            """
        )
        service = SiiBrowserService(SiiPocSettings())
        confirm_control = page.locator("#form-b [name='confirm-emission']")
        actual = service._ensure_preview_retention_submission(
            page, RetentionMode.ISSUER_HANDLES_PPM, confirm_control
        )
        assert actual == ["RETCONTRIBUYENTE"]
        assert page.locator("#selection-form [name='OptTipoRetencion']").input_value() == "RETRECEPTOR"
        assert page.locator("#form-b [name='OptTipoRetencion']").input_value() == "RETCONTRIBUYENTE"
        assert service.last_retention_diagnostics["target_form_action"] == "/confirmar"
        assert service.last_retention_diagnostics["effective_formdata_retention"] == ["RETCONTRIBUYENTE"]
        assert service.last_retention_diagnostics["pre_submit_retention_verified"] is True
        browser.close()


def test_preview_submit_real_form_preserves_receiver_value():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <form id="form-b" action="/confirmar" method="post">
              <input type="hidden" name="OptTipoRetencion" value="RETCONTRIBUYENTE">
              <button name="confirm-emission" type="button">Confirmar Emisión</button>
            </form>
            """
        )
        service = SiiBrowserService(SiiPocSettings())
        actual = service._ensure_preview_retention_submission(
            page,
            RetentionMode.RECEIVER_RETAINS_PPM,
            page.locator("#form-b [name='confirm-emission']"),
        )
        assert actual == ["RETRECEPTOR"]
        assert service.last_retention_diagnostics["preview_submit_retention_input_before"] == "RETCONTRIBUYENTE"
        assert service.last_retention_diagnostics["preview_submit_retention_input_after"] == "RETRECEPTOR"
        browser.close()


def test_preview_submit_blocks_when_effective_value_cannot_be_determined():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<form><button name="cmdconfirmar" type="button">Confirmar</button></form>')
        service = SiiBrowserService(SiiPocSettings())
        with pytest.raises(SiiValidationError) as error:
            service._ensure_preview_retention_submission(page, RetentionMode.ISSUER_HANDLES_PPM)
        assert error.value.failed_checks == ["preview_retention_submit_value"]
        browser.close()


@pytest.mark.parametrize(
    ("requested_value", "network_value"),
    [
        ("RETCONTRIBUYENTE", "RETCONTRIBUYENTE"),
        ("RETCONTRIBUYENTE", "RETRECEPTOR"),
        ("RETRECEPTOR", "RETRECEPTOR"),
    ],
)
def test_post2_network_capture_records_only_retention_value(tmp_path, requested_value, network_value):
    class Request:
        method = "POST"
        url = "https://loa.sii.cl/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib.cgi"
        post_data = f"OptTipoRetencion={network_value}&rut_receptor=private"

    class Owner:
        main_frame = object()

        def on(self, _event, callback):
            self.callback = callback

        def remove_listener(self, _event, _callback):
            pass

        def route(self, _pattern, callback):
            self.guard = callback

        def unroute(self, _pattern, _callback):
            pass

    service = SiiBrowserService(
        SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=True)
    )
    service._context = Owner()
    service._diagnostic_artifact_dir = tmp_path
    service._last_retention_diagnostics["requested_retention_mode"] = (
        "issuer_handles_ppm" if requested_value == "RETCONTRIBUYENTE" else "receiver_retains_ppm"
    )
    request = Request()
    request.frame = service._context.main_frame
    request.is_navigation_request = lambda: True

    def submit():
        service._context.callback(request)
        service._context.guard(SimpleNamespace(
            request=request, fallback=lambda: None, abort=lambda _reason: None,
        ))

    if network_value != requested_value:
        with pytest.raises(SiiValidationError, match="request retention"):
            service._capture_bhe_confirm_submission(service._context, submit)
    else:
        service._capture_bhe_confirm_submission(service._context, submit)

    summary = service.last_retention_diagnostics["post2_network_capture"]
    assert summary["post2_request_observed"] is True
    assert summary["post2_method"] == "POST"
    assert summary["post2_retention_value"] == network_value
    assert summary["post2_retention_occurrences"] == 1
    assert summary["post2_retention_matches_requested"] is (
        network_value == requested_value
    )
    assert "rut_receptor" not in (tmp_path / "post2_request_diagnostic.json").read_text(encoding="utf-8")
