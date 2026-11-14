import json
import threading
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from app.services.sii_browser_service import (
    BHEAutomationData, RetentionMode, SIIBHEAutomationService,
    SiiBrowserService, SiiNavigationState, SiiPocSettings, SiiValidationError,
)


@pytest.mark.parametrize("expected,opposite", [
    ("RETCONTRIBUYENTE", "RETRECEPTOR"),
    ("RETRECEPTOR", "RETCONTRIBUYENTE"),
])
def test_native_formdata_listener_changes_actual_request(expected, opposite):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        observed = []
        page.route("**/*", lambda route: (
            observed.append(route.request.post_data) if route.request.method == "POST" else None,
            route.fulfill(content_type="text/html", body="<p>Fixture</p>"),
        ))
        page.goto("http://127.0.0.1/fixture")
        page.set_content(f'''<form method="post" action="/preview">
            <input type="hidden" name="OptTipoRetencion" value="{expected}">
            <button type="button" onclick="this.form.elements.OptTipoRetencion.value='{opposite}'; this.form.submit()">Confirmar</button>
        </form>''')
        page.locator("form").evaluate(
            "(form, expected) => form.addEventListener('formdata', event => event.formData.set('OptTipoRetencion', expected))",
            expected,
        )
        with page.expect_navigation():
            page.locator("button").click()
        assert observed == [f"OptTipoRetencion={expected}"]
        browser.close()


def test_hidden_property_assignment_also_changes_attribute_and_reset_value():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<form><input name="OptTipoRetencion" type="hidden" value="RETCONTRIBUYENTE"></form>')
        result = page.locator("input").evaluate('''element => {
            element.value = 'RETRECEPTOR';
            const assigned = {property: element.value, attribute: element.getAttribute('value'), defaultValue: element.defaultValue};
            element.form.reset();
            return {assigned, afterReset: element.value};
        }''')
        assert result == {
            "assigned": {"property": "RETRECEPTOR", "attribute": "RETRECEPTOR", "defaultValue": "RETRECEPTOR"},
            "afterReset": "RETRECEPTOR",
        }
        page.locator("input").evaluate("element => { element.value = 'RETCONTRIBUYENTE'; element.form.reset(); }")
        assert page.locator("input").input_value() == "RETCONTRIBUYENTE"
        browser.close()


def _service(tmp_path, requested):
    service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=True))
    service._diagnostic_artifact_dir = tmp_path
    service._reset_retention_diagnostics(requested)
    return service


def _form_page(browser, expected, onclick):
    page = browser.new_page()
    post_bodies = []
    def fulfill(route):
        if route.request.method == "POST":
            post_bodies.append(route.request.post_data)
        route.fulfill(content_type="text/html", body="<p>Fixture preview</p>")
    page.route("**/*", fulfill)
    page.goto("http://127.0.0.1/fixture")
    page.set_content(f'''<form method="post" action="/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib.cgi">
        <input type="hidden" name="OptTipoRetencion" value="{expected}">
        <input name="other_field" value="FICTIONAL_DATA_UNCHANGED">
        <button type="button" onclick="{onclick}">Confirmar Emisión</button></form>''')
    return page, post_bodies


@pytest.mark.parametrize("requested,expected,opposite,mutate", [
    (RetentionMode.ISSUER_HANDLES_PPM, "RETCONTRIBUYENTE", "RETRECEPTOR", True),
    (RetentionMode.RECEIVER_RETAINS_PPM, "RETRECEPTOR", "RETCONTRIBUYENTE", True),
    (RetentionMode.ISSUER_HANDLES_PPM, "RETCONTRIBUYENTE", "RETRECEPTOR", False),
])
def test_production_enforcement_corrects_native_request_and_preserves_other_fields(
    tmp_path, requested, expected, opposite, mutate
):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        mutation = f"this.form.elements.OptTipoRetencion.value='{opposite}';" if mutate else ""
        page, bodies = _form_page(browser, expected, mutation + "this.form.submit()")
        service = _service(tmp_path, requested)
        control = page.locator("button")
        service._ensure_preview_retention_submission(page, requested, control)
        def click():
            with page.expect_navigation():
                control.click()
            return page
        service._capture_bhe_confirm_submission(page, click)
        assert bodies == [f"OptTipoRetencion={expected}&other_field=FICTIONAL_DATA_UNCHANGED"]
        diagnostic = json.loads((tmp_path / "post2_request_diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["post2_network_guard_verified"] is True
        assert diagnostic["network_value_after_enforcement"] == expected
        assert service.last_retention_diagnostics["formdata_value_after_enforcement"] == [expected]
        assert service.last_retention_diagnostics["formdata_value_before_enforcement"] == [opposite if mutate else expected]
        trace = (tmp_path / "retention_mutation_trace.json").read_text(encoding="utf-8")
        assert '"formdata_enforcement"' in trace
        assert "FICTIONAL_DATA_UNCHANGED" not in trace
        browser.close()


@pytest.mark.parametrize("field_html", [
    '<input type="hidden" name="OptTipoRetencion" value="RETCONTRIBUYENTE"><input type="hidden" name="OptTipoRetencion" value="RETCONTRIBUYENTE">',
    '',
    '<input disabled type="hidden" name="OptTipoRetencion" value="RETCONTRIBUYENTE">',
])
def test_invalid_confirmation_fields_block_before_click(tmp_path, field_html):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(f'<form>{field_html}<button type="button">Confirmar Emisión</button></form>')
        service = _service(tmp_path, RetentionMode.ISSUER_HANDLES_PPM)
        with pytest.raises(SiiValidationError):
            service._ensure_preview_retention_submission(page, RetentionMode.ISSUER_HANDLES_PPM, page.locator("button"))
        assert service._retention_submission is None
        browser.close()


def test_later_formdata_handler_mismatch_aborts_without_forwarding(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page, bodies = _form_page(browser, "RETCONTRIBUYENTE", "this.form.submit()")
        service = _service(tmp_path, RetentionMode.ISSUER_HANDLES_PPM)
        control = page.locator("button")
        service._ensure_preview_retention_submission(page, RetentionMode.ISSUER_HANDLES_PPM, control)
        page.locator("form").evaluate("form => form.addEventListener('formdata', event => event.formData.set('OptTipoRetencion', 'RETRECEPTOR'))")
        def click():
            with page.expect_navigation():
                control.click()
            return page
        with pytest.raises(SiiValidationError, match="request retention"):
            service._capture_bhe_confirm_submission(page, click)
        assert bodies == []  # Guard never forwards to the downstream local response route.
        network = service.last_retention_diagnostics["post2_network_capture"]
        assert network["post2_request_aborted"] is True
        assert network["post2_network_guard_verified"] is False
        assert service._retention_submission is None
        browser.close()


def test_normal_emission_cannot_reach_final_click_on_network_mismatch(tmp_path, monkeypatch):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page, bodies = _form_page(browser, "RETCONTRIBUYENTE", """
            this.form.addEventListener('formdata', event => event.formData.set('OptTipoRetencion', 'RETRECEPTOR'));
            this.form.submit();
        """)
        service = _service(tmp_path, RetentionMode.ISSUER_HANDLES_PPM)
        service._context = page.context
        service._batch_session_owner = threading.get_ident()
        def prepare():
            service._last_retention_diagnostics["selection_verified"] = True
            return page
        monkeypatch.setattr(service, "prepare_clean_retention_validation", prepare)
        monkeypatch.setattr(service, "detect_state", lambda _page: SiiNavigationState.BHE_FORM)
        monkeypatch.setattr(SIIBHEAutomationService, "map_form", lambda *_: SimpleNamespace(required_ready=True, confirm_emission=page.locator("button")))
        monkeypatch.setattr(SIIBHEAutomationService, "fill_bhe_form", lambda *_: None)
        monkeypatch.setattr(SIIBHEAutomationService, "validate_bhe_form", lambda *_: None)
        validate_preview = Mock(return_value={"all_checks": True})
        monkeypatch.setattr(SIIBHEAutomationService, "validate_preview", validate_preview)
        final_click = Mock()
        monkeypatch.setattr(service, "_final_emit_button", lambda _: SimpleNamespace(click=final_click))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 15), rut_receptor="99990008-9", razon_social="RECEPTOR PRUEBA 04",
            direccion="DIRECCION PRUEBA 009", region="METROPOLITANA", comuna="Santiago",
            detalle="Servicio ficticio", monto=1000, retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
        )
        with pytest.raises(SiiValidationError, match="request retention"):
            service._run_bhe_final_emission_in_context(data)
        assert final_click.call_count == 0
        assert validate_preview.call_count == 0
        assert bodies == []
        browser.close()
