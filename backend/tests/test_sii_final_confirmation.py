from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import BHEEmission
from app.db.session import get_db
from app.main import app
from app.schemas.bhe import BHERequest
from app.schemas.retention import RetentionMode
from app.services.sii_browser_service import (
    SiiBrowserService,
    SiiFinalResultSurface,
    parse_sii_final_result,
    parse_sii_final_result_surfaces,
)
from app.services.bhe_provider import SIIBHEProvider
from pydantic import ValidationError
from playwright.sync_api import sync_playwright


@pytest.fixture
def final_api_client(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'final.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, db
    app.dependency_overrides.clear()
    db.close(); Base.metadata.drop_all(engine); engine.dispose()


def test_final_result_requires_confirmation_and_folio():
    result = parse_sii_final_result("Boleta emitida correctamente. Folio: 12345")
    assert result.validated is True
    assert result.folio == 12345


def test_ambiguous_final_result_is_not_success():
    result = parse_sii_final_result("El trámite continúa. Folio: 12345")
    assert result.validated is False


def test_ambiguous_result_is_distinct_from_success():
    assert parse_sii_final_result("Resultado pendiente").validated is False


def test_folio_diagnostics_count_labels_without_accepting_numbers():
    assert SiiBrowserService._count_contextual_folio_candidates("Folio: 12345") == 0
    assert SiiBrowserService._count_contextual_folio_candidates(
        "BOLETA DE HONORARIOS ELECTRONICA N ° 12345 Res. Ex. N° 83"
    ) == 1
    assert parse_sii_final_result("Folio: 12345").validated is False


def test_final_result_parser_finds_success_and_folio_in_child_frame():
    result = parse_sii_final_result_surfaces([
        SiiFinalResultSurface(0, "https://loa.sii.cl/cgi_IMT/TMBECN_BoletaHonorariosElectronica.cgi", ""),
        SiiFinalResultSurface(
            1,
            "https://loa.sii.cl/IMT/pdf/bhe_synthetic.html",
            "BOLETA DE HONORARIOS ELECTRONICA N ° 12345 Fecha / Hora Emisión: 01/09/2026 10:30",
        ),
    ])
    assert result.validated is True
    assert result.folio == 12345
    assert result.source_frame_index == 1


@pytest.mark.skip(reason="legacy non-structural fixture; real-like coverage is below")
def test_final_result_parser_combines_evidence_across_frames():
    result = parse_sii_final_result_surfaces([
        SiiFinalResultSurface(0, "https://loa.sii.cl/result.cgi", "Emisión realizada correctamente"),
        SiiFinalResultSurface(1, "https://loa.sii.cl/detail.cgi", "N° Boleta: 98765"),
    ])
    assert result.validated is True
    assert result.folio == 98765
    assert result.source_frame_index == 1


def test_final_result_url_alone_does_not_imply_success():
    result = parse_sii_final_result_surfaces([
        SiiFinalResultSurface(
            1,
            "https://loa.sii.cl/cgi_IMT/TMBECN_BoletaHonorariosElectronica.cgi",
            "BOLETA DE HONORARIOS ELECTRONICA",
        ),
    ])
    assert result.validated is False


def test_print_datetime_is_not_used_as_emission_datetime():
    result = parse_sii_final_result_surfaces([
        SiiFinalResultSurface(0, "https://loa.sii.cl/cgi_IMT/TMBECN_BoletaHonorariosElectronica.cgi", ""),
        SiiFinalResultSurface(
            1,
            "https://loa.sii.cl/IMT/pdf/bhe_synthetic.html",
            "BOLETA DE HONORARIOS ELECTRONICA N° 12345 Fecha / Hora Impresión: 01/09/2026 10:30",
        ),
    ])
    assert result.validated is False
    assert result.fecha_emision is None


def test_empty_or_inaccessible_frame_surfaces_fail_safe():
    assert parse_sii_final_result_surfaces([]).validated is False
    assert parse_sii_final_result_surfaces([
        SiiFinalResultSurface(1, "", "Contenido sin evidencia de emisión"),
    ]).validated is False


def test_result_control_is_located_in_child_frame_without_clicking_it():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            "<iframe srcdoc=\"<button>Ver PDF</button>\"></iframe>"
        )
        page.wait_for_timeout(20)
        control = SiiBrowserService._find_result_control(page, r"pdf")
        assert control == (1, "button", "button")
        browser.close()


def test_real_like_frame_fixture_is_parsed_without_clicking_controls():
    fixture = Path(__file__).parent / "fixtures" / "sii_final_result_frames.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(fixture.read_text(encoding="utf-8"))
        service = SiiBrowserService()
        collected = service.collect_final_result_surfaces(page)
        surfaces = [
            SiiFinalResultSurface(
                surface.frame_index,
                "https://loa.sii.cl/cgi_IMT/TMBECN_BoletaHonorariosElectronica.cgi"
                if surface.frame_index == 0
                else "https://loa.sii.cl/IMT/pdf/bhe_synthetic.html",
                surface.text,
            )
            for surface in collected
        ]
        result = parse_sii_final_result_surfaces(surfaces)
        assert result.validated is True
        assert result.folio == 54321
        assert result.fecha_emision == "2026-09-01 10:30:00"
        assert result.source_frame_index == 1
        assert SiiBrowserService._extract_final_barcode(page) == "SYNTHETIC-BARCODE"
        assert SiiBrowserService._has_post_emission_controls(page) is True
        assert SiiBrowserService._find_result_control(page, r"pdf") == (1, "a", "javascript")
        browser.close()


def test_safe_url_drops_query_parameters():
    assert SiiBrowserService._safe_url("https://loa.sii.cl/result.cgi?token=secret") == "https://loa.sii.cl/result.cgi"


def test_retention_mode_is_semantic_and_defaults_to_issuer():
    base = dict(rut_receptor="99990008-9", direccion="DIRECCION PRUEBA 002", region=10, comuna="Puerto Varas",
                detalle=[{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 1000}])
    assert BHERequest(**base).retention_mode is RetentionMode.ISSUER_HANDLES_PPM
    assert BHERequest(**base, retention_mode="receiver_retains_ppm").retention_mode is RetentionMode.RECEIVER_RETAINS_PPM


def test_invalid_retention_mode_is_rejected():
    with pytest.raises(ValidationError):
        BHERequest(rut_receptor="99990008-9", direccion="DIRECCION PRUEBA 002", region=10,
                   comuna="Puerto Varas", detalle=[{"descripcion": "Servicio", "precio_unitario": 1000}],
                   retention_mode="1")


def test_sii_provider_preserves_selected_semantic_retention_mode():
    request = BHERequest(rut_receptor="99990008-9", direccion="DIRECCION PRUEBA 002", region=10,
                         comuna="Puerto Varas", detalle=[{"descripcion": "Servicio", "precio_unitario": 1000}],
                         retention_mode="receiver_retains_ppm")
    assert SIIBHEProvider._automation_data(request).retention_mode.value == "receiver_retains_ppm"


def test_final_confirmation_requires_true(final_api_client):
    client, db = final_api_client
    response = client.post("/bhe/emissions/1/confirm-final", json={"confirm": False})
    assert response.status_code == 422


def test_final_confirmation_only_accepts_sii_and_awaiting(final_api_client):
    client, db = final_api_client
    emission = BHEEmission(
        rut_receptor="999900089", razon_social="", direccion="DIRECCION PRUEBA 002", region=10,
        comuna="Puerto Varas", detalle=[{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 1000}],
        monto_total=1000, fecha_emision_solicitada=date.today(), status="success", provider="sii",
    )
    db.add(emission); db.commit(); db.refresh(emission)
    response = client.post(f"/bhe/emissions/{emission.id}/confirm-final", json={"confirm": True})
    assert response.status_code == 409
