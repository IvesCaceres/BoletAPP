from datetime import date
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from playwright.sync_api import sync_playwright

from app.services.sii_browser_service import (
    BHEAutomationData,
    RetentionMode,
    SIIBHEAutomationService,
    SiiBrowserService,
    SiiNavigationError,
    SiiNavigationState,
    SiiPocSettings,
    SiiValidationError,
    split_chilean_rut,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_authenticated_bhe_menu_state_from_fixture():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_authenticated.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()

        assert service.is_authenticated(page) is True
        assert service.detect_state(page) == SiiNavigationState.BHE_MENU
        browser.close()


def test_emitter_menu_beats_generic_login_markers():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_emitter_menu.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()

        assert service.detect_state(page) == SiiNavigationState.BHE_EMITTER_MENU
        assert service.detect_state(page) != SiiNavigationState.LOGIN
        browser.close()


def test_retention_fixture_detects_both_semantic_modes():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_retention.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()

        assert service.detect_state(page) == SiiNavigationState.RETENTION_SELECTION
        receiver = service._find_retention_option(page, RetentionMode.RECEIVER_RETAINS_PPM)
        issuer = service._find_retention_option(page, RetentionMode.ISSUER_HANDLES_PPM)
        assert receiver is not None
        assert issuer is not None
        browser.close()


def test_bhe_form_fixture_is_not_retention_selection(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_bhe_form.html").read_text(encoding="utf-8"))
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))

        assert service.detect_state(page) == SiiNavigationState.BHE_FORM
        mapping = service._map_bhe_form_fields(page)
        assert mapping.required_ready is True
        assert mapping.region.get_attribute("name") == "cod_region"
        assert mapping.comuna.get_attribute("name") == "cbo_comuna"
        service._select_region_and_comuna(page, mapping, "10", "PUERTO VARAS")
        assert mapping.region.input_value() == "10"
        assert mapping.comuna.input_value() == "10101"
        browser.close()


def test_region_selection_waits_for_semantic_comuna_option(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select id='cod_region' name='cod_region'>
              <option value='10'>10 - Los Lagos</option>
            </select>
            <select id='cbo_comuna' name='cbo_comuna'>
              <option value=''>Seleccione</option>
            </select>
            <script>
              document.querySelector('#cod_region').addEventListener('change', () => {
                setTimeout(() => {
                  document.querySelector('#cbo_comuna').innerHTML =
                    '<option value="10101">Puerto Varas</option>';
                }, 20);
              });
            </script>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        mapping = service._map_bhe_form_fields(page)
        service._select_region_and_comuna(page, mapping, "Los Lagos", "puerto varas")

        assert mapping.region.input_value() == "10"
        assert mapping.comuna.input_value() == "10101"
        browser.close()


def test_region_normalization_matches_sii_prefix_without_partial_matching():
    service = SiiBrowserService()

    assert service.normalize_region("LOS LAGOS") == service.normalize_region("REGION DE LOS LAGOS")
    assert service.normalize_region("REGIÓN DE LOS LAGOS") == service.normalize_region("region de los lagos")
    assert service.normalize_region("LOS LAGOS") != service.normalize_region("LOS RIOS")


def test_real_date_selects_use_names_and_real_option_values(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select name='cbo_dia_boleta'><option value='08'>08</option></select>
            <select name='cbo_mes_boleta'><option value='sii-month-08'>Agosto</option></select>
            <select name='cbo_anio_boleta'><option value='2026'>2026</option></select>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        mapping = service._map_bhe_form_fields(page)
        service._select_date(mapping, date(2026, 8, 8))

        assert mapping.day.input_value() == "08"
        assert mapping.month.input_value() == "sii-month-08"
        assert mapping.year.input_value() == "2026"
        browser.close()


def test_rut_split_normalizes_formats_and_uppercases_dv():
    assert split_chilean_rut("99.990.006-0") is None  # mathematically invalid RUT
    assert split_chilean_rut("99990004-6") == ("99990004", "6")
    assert split_chilean_rut("99990018-6") == ("99990018", "6")
    assert split_chilean_rut("99990003-0") is None


def test_bhe_fill_requires_and_fills_rut_body_and_dv():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_bhe_form.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()
        mapping = service._map_bhe_form_fields(page)
        data = BHEAutomationData(
            fecha_emision=date(2026, 8, 1),
            rut_receptor="99990018-6",
            razon_social="POC",
            direccion="DIRECCION PRUEBA 024",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio POC",
            monto=1000,
        )

        service._fill_bhe_form(page, mapping, data)

        assert mapping.rut_body.input_value() == "99990018"
        assert mapping.rut_dv.input_value() == "6"
        assert mapping.recipient_name.input_value() == ""
        service._validate_bhe_form_fill(page, mapping, data)
        browser.close()


def test_bhe_fill_does_not_require_optional_recipient_name():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_bhe_form.html").read_text(encoding="utf-8"))
        page.locator("#recipient_name").evaluate("node => node.remove()")
        service = SiiBrowserService()
        mapping = service._map_bhe_form_fields(page)
        data = BHEAutomationData(
            fecha_emision=date(2026, 8, 1),
            rut_receptor="99990018-6",
            razon_social="",
            direccion="DIRECCION PRUEBA 024",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio POC",
            monto=1000,
        )

        assert mapping.recipient_name is None
        assert mapping.required_ready is True
        service._fill_bhe_form(page, mapping, data)
        service._validate_bhe_form_fill(page, mapping, data)
        browser.close()


def test_select_option_resolution_fails_on_zero_or_multiple_matches():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select id='empty'><option value='1'>Santiago</option></select>
            <select id='ambiguous'><option value='1'>Los Lagos</option><option value='2'>Los Lagos</option></select>
            """
        )
        service = SiiBrowserService()

        with pytest.raises(SiiNavigationError, match="0 matches"):
            service._select_unique_option(page.locator("#empty"), ("Puerto Varas",), "Comuna")
        with pytest.raises(SiiNavigationError, match="2 matches"):
            service._select_unique_option(page.locator("#ambiguous"), ("Los Lagos",), "Región")
        browser.close()


def test_issuer_retention_selection_overrides_default_and_verifies_checked():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.route("https://sii.invalid/**", lambda route: route.fulfill(status=200, body="synthetic"))
        page = context.new_page()
        page.set_content((FIXTURES / "sii_retention.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()
        service._context = context

        active_page = service._select_retention_and_continue(
            page, RetentionMode.ISSUER_HANDLES_PPM
        )

        assert active_page is page
        assert service.last_retention_diagnostics["requested_retention_mode"] == "issuer_handles_ppm"
        assert service.last_retention_diagnostics["selected_retention_mode"] == "issuer_handles_ppm"
        assert service.last_retention_diagnostics["selection_verified"] is True
        assert service.last_retention_diagnostics["selection_label_mode"] == "issuer_handles_ppm"
        selection = service.last_retention_diagnostics["selection_diagnostics"]
        assert selection["diagnostic_version"] == "retention-selection-v1"
        assert [radio["semantic_mode"] for radio in selection["options_after_selection"]] == [
            "receiver_retains_ppm", "issuer_handles_ppm",
        ]
        assert [radio["radio"]["checked"] for radio in selection["options_after_selection"]] == [False, True]
        assert selection["selected_radio_name"] == "OptTipoRetencion"
        assert selection["selected_radio_id"] == "retain-b"
        assert selection["selected_radio_value"] == "issuerChoice"
        assert all("query" not in radio["form"]["action_path"] for radio in selection["options_after_selection"])
        assert selection["form"]["action_path"] == "/cgi/retention.cgi"
        assert selection["form"]["method"] == "POST"
        field_names = {field["name"] for field in selection["form"]["fields"]}
        assert {"OptTipoRetencion", "retention_source"} <= field_names
        assert "unrelated_tax_field" not in field_names
        assert selection["js"]["retention_transform_present"] is True, selection["js"]
        assert "applyRetention" in selection["js"]["function_names"]
        assert selection["js"]["affected_retention_field_names"] == ["OptTipoRetencion"]
        assert selection["submission"]["observed"] is True
        assert selection["submission"]["requests"][0]["retention_fields"] == [
            {"name": "OptTipoRetencion", "value": "receiverChoice"},
            {"name": "retention_source", "value": "fixture"},
        ]

        selection_snapshot = selection
        page.set_content(
            "<p>Fecha 12 Septiembre 2026</p><p>RUT Destinatario: 99.990.018-6</p>"
            "<p>Domicilio: DIRECCION PRUEBA 008</p><p>Prestacion: Servicio</p><p>Valor total: $1.000</p>"
            "<p>El contribuyente para el cual está destinada esta boleta, es el encargado de retener el porcentaje definido.</p>"
            "<h2>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</h2>"
            "<button>Emitir Boleta de Honorarios Electrónica</button>"
        )
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12), rut_receptor="99990018-6", razon_social="Cliente",
            direccion="DIRECCION PRUEBA 008", region="LOS LAGOS", comuna="PUERTO VARAS",
            detalle="Servicio", monto=1000, retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
        )
        checks = service._validate_bhe_preview(page, data)
        assert checks["preview_retention_mode_ok"] is False
        assert service.last_retention_diagnostics["selection"] == selection_snapshot
        assert service.last_retention_diagnostics["selection_diagnostics"] == selection_snapshot
        assert service.last_retention_diagnostics["preview"]["detected_retention_mode"] == "receiver_retains_ppm"
        context.close()
        browser.close()


@pytest.mark.parametrize("fixture_name", [
    "sii_retention_legacy_table.html", "sii_retention_legacy_sibling.html",
])
def test_real_named_retention_radios_are_captured_without_label_for(fixture_name):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        service = SiiBrowserService()
        snapshot = service._retention_selection_inventory(page)

        assert len(snapshot["options"]) == 2
        assert all(option["radio"]["name"] == "OptTipoRetencion" for option in snapshot["options"])
        assert [option["semantic_mode"] for option in snapshot["options"]] == [
            "receiver_retains_ppm", "issuer_handles_ppm",
        ]
        assert all(option["label_source"] in {"parent", "row", "sibling", "unknown"}
                   for option in snapshot["options"])
        browser.close()


def test_unknown_named_retention_radio_is_kept_in_snapshot():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<input type="radio" name="OptTipoRetencion" value="opaque">'
            '<span>Modalidad no reconocida</span>'
        )
        snapshot = SiiBrowserService()._retention_selection_inventory(page)
        assert len(snapshot["options"]) == 1
        assert snapshot["options"][0]["semantic_mode"] is None
        browser.close()


def test_retention_selection_mismatch_fails_before_continuing(monkeypatch):
    class Option:
        def __init__(self, checked):
            self.checked = checked

        def check(self):
            pass

        def is_checked(self):
            return self.checked

    service = SiiBrowserService()
    service._reset_retention_diagnostics(RetentionMode.ISSUER_HANDLES_PPM)
    options = {
        RetentionMode.ISSUER_HANDLES_PPM: Option(False),
        RetentionMode.RECEIVER_RETAINS_PPM: Option(True),
    }
    monkeypatch.setattr(service, "_find_retention_option", lambda _page, mode: options[mode])
    monkeypatch.setattr(service, "_radio_context_text", lambda _page, _radio: "El receptor retiene PPM")
    monkeypatch.setattr(service, "_retention_selection_inventory", lambda _page: {
        "options": [], "js": {"retention_transform_present": False},
    })

    with pytest.raises(SiiValidationError) as caught:
        service._select_retention_and_continue(object(), RetentionMode.ISSUER_HANDLES_PPM)

    assert caught.value.failed_checks == ["retention_selection_verified"]
    assert caught.value.preview_diagnostics["selected_retention_mode"] == "receiver_retains_ppm"
    assert caught.value.preview_diagnostics["selection_verified"] is False


def test_before_snapshot_survives_selection_failure(monkeypatch):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_retention_legacy_table.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()
        monkeypatch.setattr(service, "_find_retention_option", lambda _page, _mode: None)
        with pytest.raises(SiiValidationError):
            service._select_retention_and_continue(page, RetentionMode.ISSUER_HANDLES_PPM)
        diagnostics = service.last_retention_diagnostics
        assert len(diagnostics["selection"]["options_before_selection"]) == 2
        assert diagnostics["selection_action_succeeded"] is False
        assert diagnostics["semantic_selection_verified"] is False
        browser.close()


def test_checked_radio_with_wrong_semantic_label_fails_selection(monkeypatch):
    class Option:
        def __init__(self, checked, label):
            self.checked = checked
            self.label = label

        def check(self):
            pass

        def is_checked(self):
            return self.checked

    service = SiiBrowserService()
    service._reset_retention_diagnostics(RetentionMode.ISSUER_HANDLES_PPM)
    requested = Option(True, "El receptor de la boleta actuará reteniendo el porcentaje vigente de Pago Provisional Mensual")
    other = Option(False, "El contribuyente emisor será quien se encargue de la retención del porcentaje vigente de Pago Provisional Mensual")
    options = {
        RetentionMode.ISSUER_HANDLES_PPM: requested,
        RetentionMode.RECEIVER_RETAINS_PPM: other,
    }
    monkeypatch.setattr(service, "_find_retention_option", lambda _page, mode: options[mode])
    monkeypatch.setattr(service, "_radio_context_text", lambda _page, radio: radio.label)
    monkeypatch.setattr(service, "_retention_selection_inventory", lambda _page: {
        "options": [], "js": {"retention_transform_present": False},
    })

    with pytest.raises(SiiValidationError):
        service._select_retention_and_continue(object(), RetentionMode.ISSUER_HANDLES_PPM)

    assert service.last_retention_diagnostics["selection_verified"] is False
    assert service.last_retention_diagnostics["selection_label_mode"] == "receiver_retains_ppm"


@pytest.mark.parametrize(
    ("requested", "detected", "expected"),
    [
        ("issuer_handles_ppm", "issuer_handles_ppm", True),
        ("issuer_handles_ppm", "receiver_retains_ppm", False),
        ("issuer_handles_ppm", None, False),
        ("issuer_handles_ppm", "ambiguous", False),
        ("receiver_retains_ppm", "receiver_retains_ppm", True),
        ("receiver_retains_ppm", "issuer_handles_ppm", False),
        ("receiver_retains_ppm", None, False),
        ("receiver_retains_ppm", "ambiguous", False),
    ],
)
def test_requested_retention_must_match_detected_retention(requested, detected, expected):
    assert SiiBrowserService._retention_mode_matches(requested, detected) is expected


def test_preview_fixture_is_classified_and_final_button_is_only_detected():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((FIXTURES / "sii_bhe_preview.html").read_text(encoding="utf-8"))
        service = SiiBrowserService()

        assert service.detect_state(page) == SiiNavigationState.BHE_PREVIEW
        assert service._final_emit_button(page) is not None
        service._wait_for_preview_stable(page)
        checks = service._validate_bhe_preview(page)
        assert all(checks.values())
        assert service.last_retention_diagnostics["detected_retention_mode"] == "issuer_handles_ppm"
        assert service.last_retention_diagnostics["retention_evidence_kind"] == "issuer_text"
        assert service.last_retention_diagnostics["preview_retention_mode_ok"] is True
        browser.close()


def test_preview_retention_only_reads_semantically_identified_frames(monkeypatch):
    class Body:
        def __init__(self, text, blocks):
            self.text = text
            self.blocks = blocks

        def inner_text(self, **_kwargs):
            return self.text

        def all_inner_texts(self):
            return self.blocks

    class Frame:
        def __init__(self, url, text, blocks):
            self.url = url
            self.body = Body(text, blocks)

        def locator(self, _selector):
            return self.body

    class Page:
        def __init__(self, frames):
            self.frames = frames

    draft = "Este es un borrador, no tiene ninguna validez."
    issuer_text = "El contribuyente emisor declara y entera el PPM."
    main = Frame("https://loa.sii.cl/preview", draft + " " + issuer_text, [draft, issuer_text])
    unrelated = Frame("https://example.test/other", "", ["El receptor retiene el PPM."])
    service = SiiBrowserService()
    monkeypatch.setattr(service, "_final_emit_button", lambda _page: object())

    diagnostic = service._preview_retention_diagnostic_for_page(Page([main, unrelated]))

    assert diagnostic["detected_retention_mode"] == "issuer_handles_ppm"
    assert diagnostic["retention_evidence_kind"] == "issuer_text"
    assert diagnostic["preview_frame_index"] == 0


def test_valid_bhe_child_frame_with_conflicting_retention_is_ambiguous(monkeypatch):
    class Body:
        def __init__(self, text, blocks):
            self.text = text
            self.blocks = blocks

        def inner_text(self, **_kwargs):
            return self.text

        def all_inner_texts(self):
            return self.blocks

    class Frame:
        def __init__(self, url, text, blocks):
            self.url = url
            self.body = Body(text, blocks)

        def locator(self, _selector):
            return self.body

    class Page:
        def __init__(self, frames):
            self.frames = frames

    draft = "Este es un borrador, no tiene ninguna validez."
    issuer_text = "El contribuyente emisor declara y entera el PPM."
    main = Frame("https://loa.sii.cl/preview", draft + " " + issuer_text, [draft, issuer_text])
    receiver_text = "El receptor retiene el PPM."
    child = Frame(
        "https://loa.sii.cl/cgi_IMT/TMBECN_preview.cgi",
        draft + " " + receiver_text,
        [draft, receiver_text],
    )
    service = SiiBrowserService()
    monkeypatch.setattr(service, "_final_emit_button", lambda _page: object())

    diagnostic = service._preview_retention_diagnostic_for_page(Page([main, child]))

    assert diagnostic["detected_retention_mode"] is None
    assert diagnostic["retention_evidence_kind"] == "ambiguous_text"
    assert diagnostic["diagnostic_reason"] == "both_retention_modes_found"


def _diagnostic_preview_html(retention_markup: str) -> str:
    return f"""
      <h2>Este es un borrador, no tiene ninguna validez</h2>
      <p>RUT destinatario: 99.990.018-6</p>
      <p>Nombre: EMPRESA DEMO 010 SPA</p>
      <p>Domicilio: DIRECCION PRUEBA 004</p>
      <p>Fecha: 12/09/2026</p><p>Detalle: Servicio</p><p>Monto: $12.345</p>
      {retention_markup}
      <form><input type="hidden" name="OptTipoRetencion" value="RETRECEPTOR"><button name="cmdconfirmar">Confirmar</button></form>
      <button>Emitir Boleta de Honorarios Electrónica</button>
    """


def test_unknown_preview_persists_retention_fragments_and_frame_inventory(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(_diagnostic_preview_html(
            "<section id='tax-option'><span>Pago Provisional Mensual</span></section>"
        ))
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._reset_retention_diagnostics(RetentionMode.ISSUER_HANDLES_PPM)
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12), rut_receptor="99990018-6",
            razon_social="EMPRESA DEMO 010 SPA", direccion="DIRECCION PRUEBA 004",
            region="LOS LAGOS", comuna="PUERTO VARAS", detalle="Servicio", monto=12345,
        )

        checks = service._validate_bhe_preview(page, data)
        diag = service.last_retention_diagnostics

        assert checks["preview_retention_mode_ok"] is False
        assert diag["detected_retention_mode"] is None
        assert diag["retention_evidence_kind"] == "none"
        assert diag["diagnostic_version"] == "retention-preview-v1"
        assert diag["decision_surface"][0]["index"] == 0
        assert diag["preview_frame_inventory"][0]["structural_bhe_preview"] is True
        assert any("Pago Provisional Mensual" in c["local_text"] for c in diag["retention_candidates"])
        assert all(len(c["local_text"]) <= 300 for c in diag["retention_candidates"])
        browser.close()


def test_unknown_preview_scans_non_decision_child_frames_for_diagnostics(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(_diagnostic_preview_html("<p>Resumen tributario</p>"))
        page.evaluate("""() => {
          const f = document.createElement('iframe');
          f.srcdoc = '<div><span>Retención PPM informada en panel secundario</span></div>';
          document.body.appendChild(f);
        }""")
        page.wait_for_timeout(50)
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        diagnostic = service._preview_retention_diagnostic_for_page(page)

        assert diagnostic["detected_retention_mode"] is None
        assert diagnostic["retention_evidence_kind"] == "none"
        assert diagnostic["preview_frame_inventory"][0]["structural_bhe_preview"] is True
        child = next(item for item in diagnostic["preview_frame_inventory"] if item["index"] == 1)
        assert child["structural_bhe_preview"] is False
        assert any(c["frame_index"] == 1 for c in diagnostic["retention_candidates"])
        browser.close()


def test_retention_diagnostic_sanitizes_sensitive_values_and_caps_candidates(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        sensitive_parent = (
            "RUT: 99.990.018-6 Nombre: EMPRESA DEMO 010 SPA Dirección: DIRECCION PRUEBA 004 "
            "Email: fixture_064dad72a8@example.invalid Teléfono: +56 9 0000 0000 Monto: $12.345 "
            "Fecha: 12/09/2026 Folio: 9876"
        )
        candidates = "".join(
            f"<div>{sensitive_parent}<span>Retención opción técnica {index} {'x' * 350}</span></div>"
            for index in range(25)
        )
        page.set_content(_diagnostic_preview_html(candidates))
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        diagnostic = service._preview_retention_diagnostic_for_page(
            page,
            ("99.990.018-6", "EMPRESA DEMO 010 SPA", "DIRECCION PRUEBA 004"),
        )
        serialized = str(diagnostic["retention_candidates"])

        for secret in (
            "99.990.018-6", "EMPRESA DEMO 010 SPA", "DIRECCION PRUEBA 004",
            "fixture_064dad72a8@example.invalid", "+56 9 0000 0000", "$12.345", "12/09/2026", "9876",
        ):
            assert secret not in serialized
        assert len(diagnostic["retention_candidates"]) <= 20
        assert all(len(c["local_text"]) <= 300 for c in diagnostic["retention_candidates"])
        browser.close()


@pytest.mark.parametrize(
    "retention_markup",
    [
        "<section><span>Pago Provisional Mensual</span></section>",
        "<section>El emisor declara el PPM. El receptor retiene el PPM.</section>",
        "<section>El contribuyente para el cual está destinada esta boleta, es el encargado de retener el porcentaje definido.</section>",
    ],
    ids=["unknown", "ambiguous", "requested-issuer-detected-receiver"],
)
def test_unknown_or_ambiguous_retention_never_reaches_final_click(
    retention_markup, monkeypatch, tmp_path
):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(_diagnostic_preview_html(retention_markup))
        page.locator("button[name='cmdconfirmar']").evaluate(
            "button => button.addEventListener('click', () => window.__finalClicks = (window.__finalClicks || 0) + 1)"
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._context = SimpleNamespace(pages=[page], new_page=lambda: page)
        service._batch_session_owner = threading.get_ident()
        service._batch_start_prepared = True
        service._active_page = page
        service._last_retention_diagnostics = {
            "requested_retention_mode": "issuer_handles_ppm",
            "selected_retention_mode": "issuer_handles_ppm",
            "selection_verified": True,
        }
        monkeypatch.setattr(service, "prepare_clean_retention_validation", lambda: page)
        states = iter([
            SiiNavigationState.RETENTION_SELECTION,
            SiiNavigationState.BHE_FORM,
            SiiNavigationState.BHE_PREVIEW,
        ])
        monkeypatch.setattr(service, "detect_state", lambda _page: next(states))
        monkeypatch.setattr(service, "_wait_for_preview_stable", lambda _page: None)
        def select_retention(current_page, _mode):
            service._last_retention_diagnostics.update({
                "requested_retention_mode": "issuer_handles_ppm",
                "selected_retention_mode": "issuer_handles_ppm",
                "selection_verified": True,
            })
            return current_page
        monkeypatch.setattr(service, "_select_retention_and_continue", select_retention)

        class Automation:
            def __init__(self, _browser_service):
                pass

            def map_form(self, _page):
                return SimpleNamespace(required_ready=True)

            def fill_bhe_form(self, *_args):
                pass

            def validate_bhe_form(self, *_args):
                pass

            def confirm_to_preview(self, current_page, _mapping):
                return current_page, []

            def validate_preview(self, current_page, data):
                return service._validate_bhe_preview(current_page, data)

        monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService", Automation)
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12), rut_receptor="99990018-6",
            razon_social="EMPRESA DEMO 010 SPA", direccion="DIRECCION PRUEBA 004",
            region="LOS LAGOS", comuna="PUERTO VARAS", detalle="Servicio", monto=12345,
        )

        with pytest.raises(SiiValidationError) as caught:
            service._run_bhe_final_emission_in_context(data)

        assert caught.value.failed_checks == ["preview_retention_mode_ok"]
        assert service.last_retention_diagnostics["preview_retention_mode_ok"] is False
        assert page.evaluate("window.__finalClicks || 0") == 0
        browser.close()


def test_preview_recipient_identity_uses_rut_and_captures_sii_name(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <p>Fecha 12 Septiembre 2026</p>
            <p>RUT Destinatario: 99.990.018-6</p>
            <p>Nombre: EMPRESA DEMO 027 SPA</p>
            <p>Domicilio: DIRECCION PRUEBA 022</p>
            <p>Prestación: Servicio POC</p>
            <p>Valor total: $1.000</p>
            <p>El emisor declara y entera el PPM.</p>
            <button>Emitir Boleta de Honorarios Electrónica</button>
            <h2>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</h2>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12),
            rut_receptor="99990018-6",
            razon_social="EMPRESA DEMO 028 SPA",
            direccion="DIRECCION PRUEBA 022",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio POC",
            monto=1000,
        )

        checks = service._validate_bhe_preview(page, data)
        assert checks["preview_recipient_ok"] is True
        assert all(checks.values())
        assert service.sii_resolved_recipient_name == "empresa demo 027 spa"
        assert service.last_preview_result["recipient_rut_ok"] is True
        assert service.last_retention_diagnostics["preview_retention_mode_ok"] is True

        mismatch = BHEAutomationData(**{**data.__dict__, "razon_social": "EMPRESA DEMO 026 SPA"})
        assert service._validate_bhe_preview(page, mismatch)["preview_recipient_ok"] is True
        browser.close()


def test_receiver_requested_mode_accepts_matching_receiver_preview(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            "<p>Fecha 12 Septiembre 2026</p><p>RUT Destinatario: 99.990.018-6</p>"
            "<p>Domicilio: DIRECCION PRUEBA 008</p><p>Prestacion: Servicio</p><p>Valor total: $1.000</p>"
            "<p>El contribuyente para el cual está destinada esta boleta, es el encargado de retener el porcentaje definido.</p>"
            "<h2>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</h2>"
            "<button>Emitir Boleta de Honorarios Electrónica</button>"
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12), rut_receptor="99990018-6", razon_social="Cliente",
            direccion="DIRECCION PRUEBA 008", region="LOS LAGOS", comuna="PUERTO VARAS",
            detalle="Servicio", monto=1000, retention_mode=RetentionMode.RECEIVER_RETAINS_PPM,
        )

        checks = service._validate_bhe_preview(page, data)
        assert checks["preview_retention_mode_ok"] is True
        assert service.last_retention_diagnostics["preview"]["detected_retention_mode"] == "receiver_retains_ppm"
        browser.close()


def test_preview_recipient_wrong_rut_fails_even_when_name_matches(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            "<p>RUT Destinatario: 99.990.018-6</p>"
            "<p>Nombre: RECEPTOR TEST 023</p>"
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12),
            rut_receptor="99990021-0",
            razon_social="RECEPTOR TEST 023",
            direccion="Direccion",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio",
            monto=1000,
        )

        checks = service._validate_bhe_preview(page, data)

        assert checks["preview_recipient_ok"] is False
        assert service.sii_resolved_recipient_name == "receptor test 023"
        browser.close()


def test_preview_recipient_name_absent_does_not_fail_rut_identity(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<p>Rut: 99.990.018-6</p>")
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12),
            rut_receptor="99990018-6",
            razon_social="",
            direccion="Direccion",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio",
            monto=1000,
        )

        checks = service._validate_bhe_preview(page, data)

        assert checks["preview_recipient_ok"] is True
        assert service.last_preview_result["sii_resolved_recipient_name_present"] is False
        browser.close()


def test_preview_recipient_extracts_real_sii_labeled_text_lines(tmp_path, capsys):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <h2>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</h2>
            <div class="recipient">
              <div>Señor(es): EMPRESA DEMO 028 SPA</div>
              <div>Domicilio: DIRECCION PRUEBA 022</div>
              <div>Rut: 99.990.018-6</div>
            </div>
            <p>Fecha de emisión</p>
            <p>Prestación y valor total</p>
            <p>El contribuyente emisor declara y entera el PPM.</p>
            <button>Emitir Boleta de Honorarios Electrónica</button>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12),
            rut_receptor="99990018-6",
            razon_social="EMPRESA DEMO 028 SPA",
            direccion="DIRECCION PRUEBA 022",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio POC",
            monto=1000,
        )

        checks = service._validate_bhe_preview(page, data)

        assert checks["preview_recipient_ok"] is True
        diagnostic = (tmp_path / "bhe-preview-recipient-structure.txt").read_text()
        assert "EXPECTED_RUT_FOUND_ANYWHERE=YES" in diagnostic
        assert "EXPECTED_NAME_FOUND_ANYWHERE" not in diagnostic
        assert "NAME_CANDIDATE" not in diagnostic
        assert "value=" not in diagnostic
        captured = capsys.readouterr().out
        assert "rut_label_found=YES" in captured
        assert "rut_value_node_found=YES" in captured
        assert "name_label_found=YES" in captured
        assert "name_value_node_found=YES" in captured
        assert "EXPECTED_RUT_FOUND_ANYWHERE=YES" in captured
        assert "EXPECTED_NAME_FOUND_ANYWHERE" not in captured
        assert "NAME_CANDIDATE" not in captured
        assert "EMPRESA DEMO 028 SPA" not in captured
        browser.close()


def test_preview_recipient_extracts_combined_legacy_sii_element(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <h2>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</h2>
            <div>Señor(es): EMPRESA DEMO 028 SPA Domicilio: DIRECCION PRUEBA 022 Rut: 99.990.018-6</div>
            <button>Emitir Boleta de Honorarios Electrónica</button>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12),
            rut_receptor="99.990.018-6",
            razon_social="EMPRESA DEMO 028 SPA",
            direccion="DIRECCION PRUEBA 022",
            region="LOS LAGOS",
            comuna="PUERTO VARAS",
            detalle="Servicio POC",
            monto=1000,
        )

        checks = service._validate_bhe_preview(page, data)
        diagnostic = (tmp_path / "bhe-preview-recipient-structure.txt").read_text()

        assert checks["preview_recipient_ok"] is True
        assert "rut_label_found=YES" in diagnostic
        assert "rut_value_node_found=YES" in diagnostic
        assert "name_label_found=YES" in diagnostic
        assert "name_value_node_found=YES" in diagnostic
        browser.close()


def test_poc_uses_reusable_sii_automation_service():
    browser_service = SiiBrowserService()
    automation = SIIBHEAutomationService(browser_service)

    assert automation.browser is browser_service
    assert hasattr(automation, "confirm_to_preview")
    assert hasattr(automation, "emit_final")


def test_confirm_emission_navigation_reaches_preview_without_clicking_final_button(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(
            """
            <button type='button' id='confirm'>Confirmar Emisión</button>
            <script>
              document.querySelector('#confirm').addEventListener('click', () => {
                document.body.innerHTML = `<h1>Este es un borrador, no tiene ninguna validez</h1>
                  <p>Fecha de emisión Destinatario Domicilio Prestación Valor total
                  El emisor declara y entera el PPM.</p>
                  <button type='button'>Emitir Boleta de Honorarios Electrónica</button>`;
              });
            </script>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._context = context

        preview_page = service._click_navigation(
            page, page.get_by_role("button", name="Confirmar Emisión"), "confirm-emission"
        )

        assert service.detect_state(preview_page) == SiiNavigationState.BHE_PREVIEW
        assert service._final_emit_button(preview_page) is not None
        context.close()
        browser.close()


def test_final_emit_control_is_blocked_by_navigation_guard():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(
            "<p>ESTE ES UN BORRADOR, NO TIENE NINGUNA VALIDEZ</p>"
            "<button type='button'>Emitir Boleta de Honorarios Electrónica</button>"
        )
        service = SiiBrowserService()
        service._context = context

        with pytest.raises(RuntimeError, match="Final SII emission control is blocked"):
            service._click_navigation(
                page,
                service._final_emit_button(page),
                "final-emission-must-never-run",
            )
        context.close()
        browser.close()


def test_bhe_menu_emit_link_is_allowed_by_contextual_guard(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(
            """
            <a href='#' id='bhe'>Emitir boleta de honorarios electrónica</a>
            <script>
              document.querySelector('#bhe').addEventListener('click', (event) => {
                event.preventDefault();
                document.body.innerHTML = `<h1>Emisor de boleta de honorarios</h1>
                  <a>Por contribuyente</a>`;
              });
            </script>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._context = context

        next_page = service._click_navigation(
            page,
            page.get_by_role("link", name="Emitir boleta de honorarios electrónica", exact=True),
            "bhe-menu",
        )

        assert "Emisor de boleta de honorarios" in next_page.locator("body").inner_text()
        context.close()
        browser.close()


def test_navigation_adopts_delayed_bhe_popup_by_semantic_evidence(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(
            """
            <button id='open'>Abrir BHE</button>
            <script>
              document.querySelector('#open').addEventListener('click', () => {
                setTimeout(() => {
                  const popup = window.open('', '_blank');
                  popup.document.write('<p>El receptor de la boleta retiene el PPM</p><input type="radio">');
                }, 1200);
              });
            </script>
            """
        )
        service = SiiBrowserService(SiiPocSettings(debug_dir=tmp_path))
        service._context = context

        next_page = service._click_navigation(
            page, page.get_by_role("button", name="Abrir BHE"), "bhe-menu"
        )

        assert next_page is not page
        assert "retiene" in next_page.locator("body").inner_text()
        inventory = (tmp_path / "navigation-bhe-menu.txt").read_text(encoding="utf-8")
        assert "page_count=2" in inventory
        assert "active_page_reason=known_retention_path" not in inventory
        context.close()
        browser.close()


def test_navigation_page_without_bhe_evidence_is_not_selected():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<p>Contenido no relacionado</p>")
        service = SiiBrowserService()

        score, reason = service._navigation_page_score(page)

        assert score == 0
        assert reason == "no_bhe_evidence"
        browser.close()
