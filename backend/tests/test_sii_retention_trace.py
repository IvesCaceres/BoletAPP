import json

import pytest
from playwright.sync_api import sync_playwright

from app.services.sii_browser_service import RetentionMode, SiiBrowserService, SiiPocSettings, SiiValidationError
from app.services.sii_retention_trace import SiiRetentionTrace


@pytest.mark.parametrize(
    ("requested", "expected", "mutate", "native_submit"),
    [
        (RetentionMode.ISSUER_HANDLES_PPM, "RETCONTRIBUYENTE", False, False),
        (RetentionMode.ISSUER_HANDLES_PPM, "RETCONTRIBUYENTE", True, False),
        (RetentionMode.RECEIVER_RETAINS_PPM, "RETRECEPTOR", False, False),
        (RetentionMode.ISSUER_HANDLES_PPM, "RETCONTRIBUYENTE", True, True),
    ],
)
def test_trace_survives_navigation_and_observes_click_mutation(
    tmp_path, requested, expected, mutate, native_submit
):
    mutation = "this.form.elements.OptTipoRetencion.value = 'RETRECEPTOR';" if mutate else ""
    submit = "this.form.submit()" if native_submit else "this.form.requestSubmit()"
    html = f"""
    <form id="wrong-form"><input name="OptTipoRetencion" type="hidden" value="RETRECEPTOR"></form>
    <form id="form-b" method="POST" action="/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib.cgi">
      <input name="OptTipoRetencion" type="hidden" value="RETRECEPTOR">
      <input name="recipient" value="PRIVATE_FAKE_RECIPIENT">
      <button type="button" onclick="{mutation}{submit}">Confirmar Emisión</button>
    </form>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # Every request is fulfilled locally; no test can contact SII.
        page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html",
            body=html if route.request.method == "GET" else "<p>Fixture preview</p>",
        ))
        page.goto("http://127.0.0.1/fixture")
        service = SiiBrowserService(SiiPocSettings(diagnostics_enabled=True, debug_dir=tmp_path))
        service._diagnostic_artifact_dir = tmp_path
        service._reset_retention_diagnostics(requested)
        control = page.locator("#form-b button")
        service._ensure_preview_retention_submission(page, requested, control)

        def click():
            service._snapshot_retention_trace(page, "T2_before_click")
            with page.expect_navigation():
                control.click()
            return page

        service._capture_bhe_confirm_submission(page, click)
        trace = json.loads((tmp_path / "retention_mutation_trace.json").read_text(encoding="utf-8"))
        snapshots = trace["snapshots"]
        stages = [snapshot["stage"] for snapshot in snapshots]
        assert stages.index("T0_form_located") < stages.index("T1_after_correction")
        assert stages.index("T1_after_correction") < stages.index("T2_before_click")
        assert stages.index("T2_before_click") < stages.index("T3_click_capture")
        assert stages.index("T3_click_capture") < stages.index("T5_network_request")
        before = next(snapshot for snapshot in snapshots if snapshot["stage"] == "T1_after_correction")
        assert before["element_count"] == 2
        assert before["elements"][0]["belongs_to_target"] is False
        assert before["elements"][0]["value"] == "RETRECEPTOR"
        assert before["elements"][1]["belongs_to_target"] is True
        assert before["elements"][1]["value"] == expected
        assert before["serialized_retention_values"] == [expected]
        if native_submit:
            assert "T4_submit_capture" not in stages  # native submit bypasses submit listeners
        else:
            assert stages.index("T3_click_capture") < stages.index("T4_submit_capture") < stages.index("T5_network_request")
            submit_snapshot = next(snapshot for snapshot in snapshots if snapshot["stage"] == "T4_submit_capture")
            assert submit_snapshot["elements"][1]["value"] == ("RETRECEPTOR" if mutate else expected)
        network = service.last_retention_diagnostics["post2_network_capture"]
        assert network["post2_retention_value"] == expected
        assert network["post2_retention_matches_requested"] is True
        assert network["post2_network_guard_verified"] is True
        assert "PRIVATE_FAKE_RECIPIENT" not in json.dumps(trace)
        browser.close()


def test_trace_redacts_unknown_values_and_does_not_record_extra_fields(tmp_path):
    trace = SiiRetentionTrace(tmp_path, "RETCONTRIBUYENTE")
    trace.record("T0_form_located", {
        "element_count": 1,
        "recipient": "PRIVATE_FAKE_RECIPIENT",
        "elements": [{"value": "PRIVATE_FAKE_RECIPIENT", "attribute_value": "PRIVATE_FAKE_RECIPIENT"}],
        "serialized_retention_values": ["PRIVATE_FAKE_RECIPIENT"],
    })
    content = (tmp_path / "retention_mutation_trace.json").read_text(encoding="utf-8")
    assert "PRIVATE_FAKE_RECIPIENT" not in content
    assert '"value": null' in content


def test_network_capture_ignores_unrelated_post(tmp_path):
    class Owner:
        def on(self, _event, callback):
            self.callback = callback

        def remove_listener(self, _event, _callback):
            pass

        def route(self, _pattern, _callback):
            pass

        def unroute(self, _pattern, _callback):
            pass

    owner = Owner()
    service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=True))
    service._context = owner
    service._diagnostic_artifact_dir = tmp_path
    service._reset_retention_diagnostics(RetentionMode.ISSUER_HANDLES_PPM)
    request = type("Request", (), {
        "method": "POST", "url": "http://127.0.0.1/unrelated",
        "post_data": "OptTipoRetencion=RETRECEPTOR",
    })()
    with pytest.raises(SiiValidationError, match="request retention"):
        service._capture_bhe_confirm_submission(owner, lambda: owner.callback(request))
    assert service.last_retention_diagnostics["post2_network_capture"]["post2_request_observed"] is False


def test_trace_reports_reconstructed_form_instead_of_original_target(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(body="<p>Fixture preview</p>"))
        page.goto("http://127.0.0.1/fixture")
        page.set_content('''
            <form id="form-b" method="post" action="/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib.cgi">
              <input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">
              <button type="button">Confirmar Emisión</button>
            </form>
            <script>
              document.querySelector('button').onclick = function () {
                const original = this.form;
                const replacement = original.cloneNode(true);
                replacement.elements.OptTipoRetencion.value = 'RETRECEPTOR';
                original.replaceWith(replacement);
                replacement.requestSubmit();
              };
            </script>
        ''')
        service = SiiBrowserService(SiiPocSettings(diagnostics_enabled=True, debug_dir=tmp_path))
        service._diagnostic_artifact_dir = tmp_path
        service._reset_retention_diagnostics(RetentionMode.ISSUER_HANDLES_PPM)
        control = page.locator("#form-b button")
        service._ensure_preview_retention_submission(page, RetentionMode.ISSUER_HANDLES_PPM, control)

        def click():
            service._snapshot_retention_trace(page, "T2_before_click")
            with page.expect_navigation():
                control.click()
            return page

        with pytest.raises(SiiValidationError, match="request retention"):
            service._capture_bhe_confirm_submission(page, click)
        snapshots = json.loads((tmp_path / "retention_mutation_trace.json").read_text(encoding="utf-8"))["snapshots"]
        submit = next(snapshot for snapshot in snapshots if snapshot["stage"] == "T4_submit_capture")
        assert submit["target_connected"] is False
        assert submit["elements"][0]["belongs_to_target"] is False
        assert submit["elements"][0]["value"] == "RETRECEPTOR"
        assert service.last_retention_diagnostics["post2_network_capture"]["post2_retention_matches_requested"] is False
        browser.close()


def test_trace_disabled_by_default_even_with_an_artifact_directory(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<form><input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR">
            <button type="button">Confirmar Emisión</button></form>''')
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._diagnostic_artifact_dir = tmp_path
        service._ensure_preview_retention_submission(page, RetentionMode.ISSUER_HANDLES_PPM, page.locator("button"))
        assert service._retention_trace is None
        assert not (tmp_path / "retention_mutation_trace.json").exists()
        browser.close()
