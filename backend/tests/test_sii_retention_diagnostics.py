from datetime import date

from playwright.sync_api import sync_playwright

from app.services.sii_browser_service import (
    BHEAutomationData,
    RetentionMode,
    SiiBrowserService,
    SiiPocSettings,
)


def test_classifier_records_exact_issuer_and_receiver_matches():
    issuer = SiiBrowserService._preview_retention_diagnostic(
        "El contribuyente emisor declara y entera el PPM.", include_matches=True
    )
    receiver = SiiBrowserService._preview_retention_diagnostic(
        "El receptor retiene el PPM.", include_matches=True
    )
    both = SiiBrowserService._preview_retention_diagnostic(
        "El emisor declara el PPM. El receptor retiene el PPM.", include_matches=True
    )
    none = SiiBrowserService._preview_retention_diagnostic("Resumen de la boleta.", include_matches=True)

    assert issuer["detected_retention_mode"] == RetentionMode.ISSUER_HANDLES_PPM.value
    assert issuer["retention_evidence_kind"] == "issuer_text"
    assert issuer["issuer_matches"] and issuer["receiver_matches"] == []
    assert "emisor declara" in issuer["issuer_matches"][0]["matched_text"].lower()

    assert receiver["detected_retention_mode"] == RetentionMode.RECEIVER_RETAINS_PPM.value
    assert receiver["retention_evidence_kind"] == "receiver_text"
    assert receiver["receiver_matches"] and receiver["issuer_matches"] == []
    assert "receptor retiene" in receiver["receiver_matches"][0]["matched_text"].lower()

    assert both["detected_retention_mode"] is None
    assert both["retention_evidence_kind"] == "ambiguous_text"
    assert both["issuer_matches"] and both["receiver_matches"]

    assert none["detected_retention_mode"] is None
    assert none["retention_evidence_kind"] == "none"
    assert none["issuer_matches"] == []
    assert none["receiver_matches"] == []


def test_classifier_records_visible_element_context_and_failure_artifacts(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <h2>Este es un borrador, no tiene ninguna validez</h2>
            <p>Fecha: 12/09/2026</p>
            <p>RUT destinatario: 99.990.018-6</p>
            <p>Domicilio: DIRECCION PRUEBA 004</p>
            <p>Detalle: Servicio</p>
            <p>Monto: $1.000</p>
            <span id="retention-copy" class="retention-copy">El receptor retiene el PPM.</span>
            <button>Emitir Boleta de Honorarios Electrónica</button>
            """
        )
        settings = SiiPocSettings(debug_dir=tmp_path, diagnostics_enabled=True)
        service = SiiBrowserService(settings)
        service.set_diagnostic_context(batch_item_id=12, emission_id=34)
        data = BHEAutomationData(
            fecha_emision=date(2026, 9, 12), rut_receptor="99990018-6",
            razon_social="Cliente de prueba", direccion="DIRECCION PRUEBA 004",
            region="LOS LAGOS", comuna="PUERTO VARAS", detalle="Servicio", monto=1000,
            retention_mode=RetentionMode.ISSUER_HANDLES_PPM,
        )

        checks = service._validate_bhe_preview(page, data)
        diagnostic = service.last_retention_diagnostics
        match = diagnostic["receiver_matches"][0]

        assert checks["preview_retention_mode_ok"] is False
        assert diagnostic["preview_retention_detected_mode"] == "receiver_retains_ppm"
        assert diagnostic["preview_retention_evidence_type"] == "receiver_text"
        assert match["frame_index"] == 0
        assert match["element_tag"] == "span"
        assert match["element_id"] == "retention-copy"
        assert match["element_class"] == "retention-copy"
        assert match["visible"] is True
        assert "receptor retiene" in match["matched_text"].lower()

        artifact_dir = tmp_path / "item_12_emission_34"
        assert (artifact_dir / "diagnostic.json").is_file()
        assert (artifact_dir / "preview.png").is_file()
        assert (artifact_dir / "preview.html").is_file()
        assert "sensitive_local_debug" in (artifact_dir / "diagnostic.json").read_text(encoding="utf-8")
        browser.close()
