from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import BHEBatch
from app.db.session import get_db
from app.main import app
from app.schemas.client import ClientCreate
from app.services.client_service import create_client


def make_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batches-test.db'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def make_client(db: Session, rut: str = "99990008-9", detail: bool = True):
    return create_client(
        db,
        ClientCreate(
            rut=rut,
            razon_social=f"Cliente {rut}",
            direccion="DIRECCION PRUEBA 011",
            region=13,
            comuna="Santiago",
            detalle_default=([{"descripcion": "Servicio default", "cantidad": 1, "precio_unitario": 1}] if detail else None),
        ),
    )


def client_app(tmp_path):
    engine, db = make_db(tmp_path)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return engine, db, TestClient(app)


def close_db(engine, db):
    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_batch(client, **payload):
    data = {"period": "2026-09", "fecha_emision": date.today().isoformat()}
    data.update(payload)
    return client.post("/bhe/batches", json=data)


def test_new_batch_is_always_sii_and_provider_input_is_rejected(tmp_path):
    engine, db, client = client_app(tmp_path)
    try:
        created = create_batch(client)
        assert created.status_code == 201
        assert created.json()["provider"] == "sii"
        rejected = create_batch(client, provider="simplefactura")
        assert rejected.status_code == 422
    finally:
        close_db(engine, db)


def test_batch_retention_default_and_receiver_override(tmp_path):
    engine, db, client = client_app(tmp_path)
    try:
        created = create_batch(client)
        assert created.json()["retention_mode"] == "issuer_handles_ppm"
        changed = client.patch(
            f"/bhe/batches/{created.json()['id']}",
            json={"retention_mode": "receiver_retains_ppm"},
        )
        assert changed.status_code == 200
        assert changed.json()["retention_mode"] == "receiver_retains_ppm"
    finally:
        close_db(engine, db)


def test_batch_items_keep_snapshot_and_retention_override(tmp_path):
    engine, db, client = client_app(tmp_path)
    try:
        source = make_client(db)
        batch = create_batch(client).json()
        item = client.post(
            f"/bhe/batches/{batch['id']}/items",
            json={"client_id": source.id, "monto": 350000, "detalle": "Detalle", "retention_mode_override": "receiver_retains_ppm"},
        )
        assert item.status_code == 201
        assert item.json()["rut_receptor"] == "999900089"
        assert item.json()["retention_mode_override"] == "receiver_retains_ppm"
        assert client.patch(
            f"/bhe/batches/{batch['id']}/items/{item.json()['id']}",
            json={"retention_mode_override": None},
        ).json()["retention_mode_override"] is None
    finally:
        close_db(engine, db)


def test_prepare_requires_items_and_returns_preview(tmp_path):
    engine, db, client = client_app(tmp_path)
    try:
        batch = create_batch(client).json()
        assert client.post(f"/bhe/batches/{batch['id']}/prepare").status_code == 422
        source = make_client(db)
        client.post(f"/bhe/batches/{batch['id']}/items", json={"client_id": source.id, "monto": 350000})
        preview = client.post(f"/bhe/batches/{batch['id']}/prepare")
        assert preview.status_code == 200
        assert preview.json()["total"] == 350000
        assert client.get(f"/bhe/batches/{batch['id']}").json()["status"] == "ready"
    finally:
        close_db(engine, db)


def test_historical_simplefactura_batch_is_readable_but_cannot_emit(tmp_path):
    engine, db, client = client_app(tmp_path)
    try:
        historical = BHEBatch(period="2025-01", fecha_emision=date(2025, 1, 15), provider="simplefactura", status="ready")
        db.add(historical)
        db.commit()
        response = client.post(f"/bhe/batches/{historical.id}/emit", json={"confirm": True})
        assert response.status_code == 409
        assert client.get(f"/bhe/batches/{historical.id}").json()["provider"] == "simplefactura"
    finally:
        close_db(engine, db)
