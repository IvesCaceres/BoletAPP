from pathlib import Path
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.sii_browser_service import (
    BHEFormData,
    BHEAutomationData,
    RetentionMode,
    SiiFinalEmissionResult,
    SiiFinalResultSurface,
    SiiBrowserService,
    SiiPocSettings,
    SiiNavigationState,
    SII_NEW_BHE_BUTTON_SELECTOR,
    SII_NEW_BHE_FAST_PATH_ENABLED,
    load_sii_poc_env,
    parse_env_bool,
)


def test_sii_poc_uses_dedicated_ignored_paths(tmp_path: Path):
    settings = SiiPocSettings(profile_dir=tmp_path / "profile", debug_dir=tmp_path / "debug")

    assert settings.profile_dir != Path.home() / ".config" / "google-chrome"
    assert settings.profile_dir.name == "profile"
    assert settings.debug_dir.name == "debug"


def test_sii_poc_defaults_to_visible_manual_browser():
    settings = SiiPocSettings()

    assert settings.headless is False
    assert settings.pause_before_confirm is False
    assert settings.home_url.startswith("https://")


def test_pause_before_confirm_defaults_to_false(monkeypatch):
    monkeypatch.delenv("SII_POC_PAUSE_BEFORE_CONFIRM", raising=False)

    assert SiiPocSettings.from_environment().pause_before_confirm is False


def test_pause_boolean_parser_accepts_true_variants():
    for value in ("true", "TRUE", "1", "yes", "YES", "on", "ON"):
        assert parse_env_bool(value) is True


def test_pause_boolean_parser_accepts_false_variants():
    for value in ("false", "FALSE", "0", "no", "NO", "off", "OFF", ""):
        assert parse_env_bool(value) is False


def test_pause_before_confirm_false_does_not_pause(monkeypatch, capsys):
    service = SiiBrowserService(SiiPocSettings(pause_before_confirm=False))

    def fail_if_called(_prompt):
        raise AssertionError("pause must not be requested")

    monkeypatch.setattr("builtins.input", fail_if_called)
    service._pause_before_confirm()

    assert capsys.readouterr().out == ""


def test_batch_session_creates_and_closes_one_context_on_owner_thread(monkeypatch, tmp_path):
    class Page:
        def set_default_timeout(self, _timeout):
            pass

    class Context:
        def __init__(self):
            self.pages = [Page()]
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class Runtime:
        def __init__(self, context):
            self.context = context
            self.chromium = self
            self.launch_calls = 0

        def launch_persistent_context(self, **_kwargs):
            self.launch_calls += 1
            return self.context

    class Manager:
        def __init__(self, runtime):
            self.runtime = runtime
            self.stop_calls = 0

        def start(self):
            return self.runtime

        def stop(self):
            self.stop_calls += 1

    context = Context()
    runtime = Runtime(context)
    manager = Manager(runtime)
    monkeypatch.setattr("app.services.sii_browser_service.sync_playwright", lambda: manager)
    service = SiiBrowserService(SiiPocSettings(profile_dir=tmp_path / "profile"))

    with service.batch_session() as same_service:
        assert same_service is service
        assert service._batch_session_owner == threading.get_ident()
        assert service._context is context
        assert runtime.launch_calls == 1
        assert context.close_calls == 0

    assert context.close_calls == 1
    assert manager.stop_calls == 1
    assert service._context is None


def test_batch_session_rejects_use_from_another_thread(monkeypatch, tmp_path):
    class Context:
        pages = []

        def new_page(self):
            return type("Page", (), {"set_default_timeout": lambda self, _timeout: None})()

        def close(self):
            pass

    context = Context()
    runtime = type("Runtime", (), {
        "chromium": type("Chromium", (), {
            "launch_persistent_context": lambda self, **_kwargs: context
        })()
    })()
    manager = type("Manager", (), {
        "start": lambda self: runtime,
        "stop": lambda self: None,
    })()
    monkeypatch.setattr("app.services.sii_browser_service.sync_playwright", lambda: manager)
    service = SiiBrowserService(SiiPocSettings(profile_dir=tmp_path / "profile"))
    errors = []

    with service.batch_session():
        thread = threading.Thread(target=lambda: _capture_thread_error(service, errors))
        thread.start()
        thread.join()

    assert len(errors) == 1
    assert "outside their owning worker thread" in str(errors[0])


def test_unknown_new_bhe_destination_keeps_fast_path_disabled(monkeypatch):
    service = SiiBrowserService(SiiPocSettings())
    calls = []
    monkeypatch.setattr(service, "recover_to_known_bhe_start", lambda: calls.append(True) or True)

    assert SII_NEW_BHE_BUTTON_SELECTOR == "input[name='cmdemitir1']"
    assert SII_NEW_BHE_FAST_PATH_ENABLED is False
    assert service.prepare_next_bhe() is True
    assert calls == [True]


def test_individual_final_flow_owns_and_closes_one_context(monkeypatch, tmp_path):
    class Page:
        def __init__(self):
            self.closed = False
            self.click_waits = 0

        def set_default_timeout(self, _timeout):
            pass

        def goto(self, *_args, **_kwargs):
            pass

        def is_closed(self):
            return self.closed

        def wait_for_load_state(self, *_args, **_kwargs):
            self.click_waits += 1

        def wait_for_function(self, *_args, **_kwargs):
            pass

        def locator(self, *_args, **_kwargs):
            return SimpleNamespace(
                wait_for=lambda **_kw: None,
            )

    class Context:
        def __init__(self, page):
            self.pages = [page]
            self.close_calls = 0

        def new_page(self):
            return self.pages[0]

        def close(self):
            self.close_calls += 1

    class Runtime:
        def __init__(self, context):
            self.chromium = self
            self.context = context
            self.launch_calls = 0

        def launch_persistent_context(self, **_kwargs):
            self.launch_calls += 1
            return self.context

    class Manager:
        def __init__(self, runtime):
            self.runtime = runtime
            self.enter_calls = 0
            self.exit_calls = 0

        def __enter__(self):
            self.enter_calls += 1
            return self.runtime

        def __exit__(self, *_args):
            self.exit_calls += 1

    page = Page()
    context = Context(page)
    runtime = Runtime(context)
    manager = Manager(runtime)
    monkeypatch.setattr("app.services.sii_browser_service.sync_playwright", lambda: manager)
    service = SiiBrowserService(SiiPocSettings(profile_dir=tmp_path / "profile"))
    monkeypatch.setattr(service, "_ensure_preview_retention_submission", lambda *_args: ["issuer_handles_ppm"])
    monkeypatch.setattr(service, "ensure_authenticated", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "navigate_to_bhe", lambda _page: setattr(service, "_active_page", page) or SiiNavigationState.RETENTION_SELECTION)
    states = iter([
        SiiNavigationState.RETENTION_SELECTION,
        SiiNavigationState.BHE_FORM,
        SiiNavigationState.BHE_PREVIEW,
    ])
    monkeypatch.setattr(service, "detect_state", lambda _page: next(states))
    def select_retention(_page, mode):
        service._last_retention_diagnostics.update({
            "requested_retention_mode": mode.value,
            "selected_retention_mode": mode.value,
            "selection_verified": True,
        })
        return page
    monkeypatch.setattr(service, "_select_retention_and_continue", select_retention)
    button_clicks = []
    monkeypatch.setattr(service, "_final_emit_button", lambda _page: SimpleNamespace(click=lambda: button_clicks.append(True)))
    monkeypatch.setattr(service, "_capture_post_emission_debug", lambda _page: None)
    monkeypatch.setattr(service, "collect_final_result_surfaces", lambda _page: [SiiFinalResultSurface(0, "https://loa.sii.cl/result", "synthetic")])
    monkeypatch.setattr(service, "_extract_final_barcode", lambda _page: None)
    monkeypatch.setattr(service, "_has_post_emission_controls", lambda _page: False)
    monkeypatch.setattr(service, "_count_contextual_folio_candidates", lambda _text: 0)
    monkeypatch.setattr(service, "_has_result_control", lambda *_args: False)
    monkeypatch.setattr(service, "_find_result_control", lambda *_args: None)
    monkeypatch.setattr(service, "_match_final_retention_mode", lambda *_args: True)
    monkeypatch.setattr(
        "app.services.sii_browser_service.parse_sii_final_result_surfaces",
        lambda _surfaces: SiiFinalEmissionResult(
            validated=True, folio=321, fecha_emision="2026-09-21 10:30:00"
        ),
    )
    monkeypatch.setattr(
        "app.services.sii_browser_service.SIIBHEAutomationService.map_form",
        lambda *_args: SimpleNamespace(required_ready=True, confirm_emission=object()),
    )
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.fill_bhe_form", lambda *_args: None)
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.validate_bhe_form", lambda *_args: None)
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.confirm_to_preview", lambda _self, _page, _mapping: (page, []))
    monkeypatch.setattr("app.services.sii_browser_service.SIIBHEAutomationService.validate_preview", lambda *_args: {"preview_amount_ok": True})

    result = service.run_bhe_final_emission(BHEAutomationData(
        fecha_emision=date(2026, 9, 21), rut_receptor="99990008-9", razon_social="",
        direccion="Direccion", region="METROPOLITANA", comuna="Santiago",
        detalle="Servicio", monto=1000, retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
    ))

    assert result["folio"] == 321
    assert button_clicks == [True]
    assert manager.enter_calls == manager.exit_calls == 1
    assert runtime.launch_calls == context.close_calls == 1


def _capture_thread_error(service, errors):
    try:
        service._assert_batch_session_owner()
    except Exception as error:
        errors.append(error)


def test_pause_before_confirm_true_pauses(monkeypatch, capsys):
    monkeypatch.setenv("SII_POC_PAUSE_BEFORE_CONFIRM", "true")
    service = SiiBrowserService(SiiPocSettings.from_environment())
    assert service.settings.pause_before_confirm is True
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")

    service._pause_before_confirm()

    assert prompts == [
        "Revisa visualmente el formulario SII. Presiona Enter para continuar al borrador..."
    ]
    assert capsys.readouterr().out == "PAUSED_BEFORE_CONFIRM=YES\n"


def test_confirm_flow_pauses_before_callback_when_enabled(monkeypatch, capsys):
    service = SiiBrowserService(SiiPocSettings(pause_before_confirm=True))
    events = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: events.append("input") or "",
    )

    result = service._confirm_to_preview_after_optional_pause(
        lambda: events.append("confirm") or "preview"
    )

    assert result == "preview"
    assert events == ["input", "confirm"]
    assert "PAUSE_BEFORE_CONFIRM_ENABLED=YES" in capsys.readouterr().out


def test_confirm_flow_does_not_pause_when_disabled(monkeypatch, capsys):
    service = SiiBrowserService(SiiPocSettings(pause_before_confirm=False))
    events = []

    def fail_if_called(_prompt):
        raise AssertionError("pause must not be requested")

    monkeypatch.setattr("builtins.input", fail_if_called)
    service._confirm_to_preview_after_optional_pause(
        lambda: events.append("confirm") or "preview"
    )

    assert events == ["confirm"]
    assert "PAUSE_BEFORE_CONFIRM_ENABLED=NO" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("preview_text", "detected", "evidence"),
    [
        (
            "El contribuyente emisor declara y entera el pago provisional mensual (PPM).",
            "issuer_handles_ppm",
            "issuer_text",
        ),
        (
            "El   receptor   retiene el pago provisional mensual (PPM).",
            "receiver_retains_ppm",
            "receiver_text",
        ),
        (
            "El contribuyente para el cual está destinada esta boleta, es el encargado de retener el porcentaje definido.",
            "receiver_retains_ppm",
            "receiver_text",
        ),
        ("La vista previa no contiene información de retención.", None, "none"),
        (
            "El emisor declara y entera el PPM. El receptor retiene el PPM.",
            None,
            "ambiguous_text",
        ),
    ],
)
def test_preview_retention_diagnostic_is_semantic_and_conservative(
    preview_text, detected, evidence
):
    diagnostic = SiiBrowserService._preview_retention_diagnostic(preview_text)

    assert diagnostic["detected_retention_mode"] == detected
    assert diagnostic["retention_evidence_kind"] == evidence


@pytest.mark.parametrize("phrase", [
    "El contribuyente para el cual está destinada esta boleta, es el encargado de retener el porcentaje definido.",
    " EL   CONTRIBUYENTE PARA EL CUAL ESTA   DESTINADA ESTA BOLETA ES EL ENCARGADO DE RETENER EL PORCENTAJE DEFINIDO ",
])
def test_real_sii_recipient_wording_is_strictly_receiver_retention(phrase):
    diagnostic = SiiBrowserService._preview_retention_diagnostic(phrase)
    assert diagnostic == {
        "detected_retention_mode": "receiver_retains_ppm",
        "retention_evidence_kind": "receiver_text",
    }


@pytest.mark.parametrize("phrase", [
    "El contribuyente será informado.",
    "Se debe retener el porcentaje definido.",
    "El contribuyente declara el porcentaje.",
])
def test_real_sii_recipient_wording_does_not_match_without_all_semantic_clauses(phrase):
    diagnostic = SiiBrowserService._preview_retention_diagnostic(phrase)
    assert diagnostic["detected_retention_mode"] is None
    assert diagnostic["retention_evidence_kind"] == "none"


def test_public_login_page_is_not_mistaken_for_authenticated_session():
    service = SiiBrowserService()

    assert service._AUTHENTICATED_MARKERS.search("Mi Sii Servicios online") is None
    assert service._LOGIN_MARKERS.search("Identificación de Contribuyentes Clave Tributaria")

def test_sii_poc_env_file_loads_values_without_overriding_process_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.sii-poc"
    env_file.write_text(
        "SII_POC_RUT_RECEPTOR=99990001-1\n"
        "SII_POC_NOMBRE_RECEPTOR=POC Demo\n"
        "SII_POC_MONTO=1000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SII_POC_NOMBRE_RECEPTOR", "Proceso")

    assert load_sii_poc_env(env_file) is True
    assert __import__("os").environ["SII_POC_RUT_RECEPTOR"] == "99990001-1"
    assert __import__("os").environ["SII_POC_NOMBRE_RECEPTOR"] == "Proceso"
    assert __import__("os").environ["SII_POC_MONTO"] == "1000"


def test_missing_poc_fields_are_reported_without_partial_form_data(monkeypatch):
    for name in BHEFormData.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    assert BHEFormData.from_environment() is None
    assert BHEFormData.missing_environment_fields() == list(BHEFormData.ENV_NAMES)


def test_poc_date_accepts_day_month_year_format(monkeypatch):
    values = {
        "SII_POC_RUT_RECEPTOR": "99990001-1",
        "SII_POC_NOMBRE_RECEPTOR": "POC Demo",
        "SII_POC_DOMICILIO_RECEPTOR": "DIRECCION PRUEBA 024",
        "SII_POC_REGION": "LOS LAGOS",
        "SII_POC_COMUNA": "PUERTO VARAS",
        "SII_POC_DETALLE": "Servicio POC",
        "SII_POC_MONTO": "1000",
        "SII_POC_FECHA": "12-09-2026",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    data = BHEFormData.from_environment()

    assert data is not None
    assert data.fecha_emision.isoformat() == "2026-09-12"


def test_poc_name_is_optional(monkeypatch):
    values = {
        "SII_POC_RUT_RECEPTOR": "99990001-1",
        "SII_POC_DOMICILIO_RECEPTOR": "DIRECCION PRUEBA 024",
        "SII_POC_REGION": "LOS LAGOS",
        "SII_POC_COMUNA": "PUERTO VARAS",
        "SII_POC_DETALLE": "Servicio POC",
        "SII_POC_MONTO": "1000",
        "SII_POC_FECHA": "2026-09-12",
    }
    monkeypatch.delenv("SII_POC_NOMBRE_RECEPTOR", raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    data = BHEFormData.from_environment()

    assert data is not None
    assert data.nombre_receptor == ""
    assert "SII_POC_NOMBRE_RECEPTOR" not in BHEFormData.ENV_NAMES
