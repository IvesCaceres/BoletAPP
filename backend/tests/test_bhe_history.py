from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Client
from app.db.session import get_db
from app.main import app
from app.schemas.bhe import BHERequest
from app.services.bhe_emission_history_service import (
    create_pending_emission,
    mark_emission_failed,
    mark_emission_success,
)


def make_request() -> BHERequest:
    return BHERequest(
        rut_receptor="99990008-9",
        razon_social="RECEPTOR TEST 032",
        direccion="DIRECCION PRUEBA 010",
        region=13,
        comuna="Santiago",
        detalle=[{"descripcion": "Servicio A", "cantidad": 2, "precio_unitario": 100}],
    )


def make_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'emissions-test.db'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_create_pending_defaults_new_emissions_to_sii(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        emission = create_pending_emission(db, make_request())
        assert emission.provider == "sii"
        assert emission.monto_total == 200
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_historical_simplefactura_provider_remains_readable(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        emission = create_pending_emission(db, make_request(), provider="simplefactura")
        mark_emission_success(db, emission, {"status": 200, "folio": 600, "codigo_barras": "BARCODE-600"})

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client:
            summary = client.get("/bhe/history").json()[0]
            detail = client.get(f"/bhe/history/{emission.id}").json()
        assert summary["provider"] == "simplefactura"
        assert detail["provider"] == "simplefactura"
        assert detail["folio"] == 600
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_history_summary_hides_pdf_and_detail_includes_it(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        emission = create_pending_emission(db, make_request())
        mark_emission_success(db, emission, {"status": 200, "folio": 601, "pdf_base64": "PDF-SYNTHETIC"})

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client:
            summary = client.get("/bhe/history").json()[0]
            detail = client.get(f"/bhe/history/{emission.id}").json()
        assert "pdf_base64" not in summary
        assert detail["pdf_base64"] == "PDF-SYNTHETIC"
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_failed_message_sanitizes_sensitive_values(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        emission = create_pending_emission(db, make_request())
        mark_emission_failed(db, emission, "accessToken=JWT-SYNTHETIC Authorization: Bearer SECRET", provider_status=502)
        assert emission.status == "failed"
        assert "SECRET" not in emission.error_message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_history_exposes_current_fantasy_name_without_changing_fiscal_snapshot(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        client = Client(
            rut="99990020-8",
            razon_social="RECEPTOR TEST 030",
            nombre_fantasia="RECEPTOR TEST 024 actual",
            direccion="DIRECCION PRUEBA 015",
            region=13,
            comuna="Santiago",
        )
        db.add(client)
        db.commit()
        emission = create_pending_emission(
            db,
            make_request(),
            client_id=client.id,
        )
        emission.razon_social = "RECEPTOR TEST 029"
        emission.direccion = "DIRECCION PRUEBA 018"
        emission.comuna = "Comuna del snapshot"
        mark_emission_success(db, emission, {"status": 200, "folio": 602})

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            summary = test_client.get("/bhe/history").json()[0]
            detail = test_client.get(f"/bhe/history/{emission.id}").json()

        assert summary["nombre_fantasia"] == "RECEPTOR TEST 024 actual"
        assert summary["razon_social"] == "RECEPTOR TEST 029"
        assert summary["direccion"] == "DIRECCION PRUEBA 018"
        assert detail["nombre_fantasia"] == "RECEPTOR TEST 024 actual"
        assert detail["folio"] == 602
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_history_keeps_fiscal_identity_when_client_is_missing_or_has_no_fantasy_name(tmp_path):
    engine, db = make_db(tmp_path)
    try:
        client = Client(
            rut="99990020-8",
            razon_social="Cliente sin receptor test 024",
            nombre_fantasia=None,
            direccion="Dirección",
            region=13,
            comuna="Santiago",
        )
        db.add(client)
        db.commit()
        emission = create_pending_emission(db, make_request(), client_id=client.id)
        mark_emission_success(db, emission, {"status": 200, "folio": 603})
        emission.client_id = None
        db.commit()

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            summary = test_client.get("/bhe/history").json()[0]

        assert summary["nombre_fantasia"] is None
        assert summary["razon_social"] == "RECEPTOR TEST 032"
        assert summary["folio"] == 603
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
