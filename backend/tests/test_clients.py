from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.client_service import client_to_bhe_receptor, create_client
from app.schemas.client import ClientCreate


VALID_RUT = "99990008-9"


@pytest.fixture
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'clients-test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def api_client(db_session: Session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def client_payload(**overrides):
    payload = {
        "rut": VALID_RUT,
        "razon_social": "Receptor Test 008",
        "giro": "Servicios profesionales",
        "direccion": "DIRECCION PRUEBA 003",
        "region": 13,
        "comuna": "Santiago",
        "email": "cliente@example.com",
        "detalle_default": [{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 100}],
    }
    payload.update(overrides)
    return payload


def test_create_client_valid_and_normalizes_rut(api_client):
    response = api_client.post("/clients", json=client_payload(rut="99.990.008-9"))

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["rut"] == "999900089"
    assert body["activo"] is True


def test_create_asevet_client_payload(api_client):
    response = api_client.post(
        "/clients",
        json={
            "rut": "99990022-4",
            "razon_social": "EMPRESA DEMO 001 SPA",
            "giro": None,
            "direccion": "DIRECCION PRUEBA 001",
            "region": 10,
            "comuna": "PUERTO VARAS",
            "email": None,
            "detalle_default": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["rut"] == "999900224"
    assert response.json()["region"] == 10


def test_client_without_legal_name_is_validation_error_not_server_error(api_client):
    payload = client_payload(razon_social="")
    response = api_client.post("/clients", json=payload)

    assert response.status_code == 422


def test_client_fantasy_name_is_optional_and_persisted(api_client):
    without_name = api_client.post("/clients", json=client_payload())
    with_name = api_client.post(
        "/clients",
        json=client_payload(rut="99990020-8", nombre_fantasia="Empresa Demo 015 Spa"),
    )

    assert without_name.status_code == 201
    assert without_name.json()["nombre_fantasia"] is None
    assert with_name.status_code == 201
    assert with_name.json()["nombre_fantasia"] == "Empresa Demo 015 Spa"
    assert api_client.get("/clients").json()[1]["nombre_fantasia"] == "Empresa Demo 015 Spa"


def test_client_fantasy_name_can_be_updated(api_client):
    created = api_client.post("/clients", json=client_payload()).json()
    response = api_client.patch(
        f"/clients/{created['id']}",
        json={"nombre_fantasia": "EMPRESA DEMO 014 SPA"},
    )

    assert response.status_code == 200
    assert response.json()["nombre_fantasia"] == "EMPRESA DEMO 014 SPA"


def test_rejects_invalid_rut(api_client):
    response = api_client.post("/clients", json=client_payload(rut="99.990.005-0"))

    assert response.status_code == 422


def test_rejects_duplicate_rut(api_client):
    api_client.post("/clients", json=client_payload())
    response = api_client.post("/clients", json=client_payload(rut="99.990.008-9"))

    assert response.status_code == 409


def test_list_get_and_update_client(api_client):
    created = api_client.post("/clients", json=client_payload()).json()
    client_id = created["id"]

    listed = api_client.get("/clients")
    fetched = api_client.get(f"/clients/{client_id}")
    updated = api_client.patch(
        f"/clients/{client_id}", json={"razon_social": "Receptor Test 003"}
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert fetched.status_code == 200
    assert fetched.json()["id"] == client_id
    assert updated.status_code == 200
    assert updated.json()["razon_social"] == "Receptor Test 003"


def test_soft_delete_and_include_inactive(api_client):
    created = api_client.post("/clients", json=client_payload()).json()
    client_id = created["id"]

    deleted = api_client.delete(f"/clients/{client_id}")
    active_only = api_client.get("/clients")
    all_clients = api_client.get("/clients?include_inactive=true")

    assert deleted.status_code == 200
    assert deleted.json()["activo"] is False
    assert active_only.json() == []
    assert len(all_clients.json()) == 1
    assert all_clients.json()[0]["activo"] is False


def test_client_endpoints_return_not_found(api_client):
    assert api_client.get("/clients/999").status_code == 404
    assert api_client.patch("/clients/999", json={"activo": True}).status_code == 404
    assert api_client.delete("/clients/999").status_code == 404


def test_client_rejects_unknown_fields(api_client):
    response = api_client.post("/clients", json=client_payload(monto=100))

    assert response.status_code == 422


def test_client_to_bhe_receptor(db_session: Session):
    client = create_client(
        db_session,
        ClientCreate(**client_payload(nombre_fantasia="RECEPTOR TEST 024")),
    )

    receptor = client_to_bhe_receptor(client)

    assert receptor == {
        "rut_receptor": "999900089",
        "razon_social": "Receptor Test 008",
        "giro": "Servicios profesionales",
        "direccion": "DIRECCION PRUEBA 003",
        "region": 13,
        "comuna": "Santiago",
        "email": "cliente@example.com",
    }
