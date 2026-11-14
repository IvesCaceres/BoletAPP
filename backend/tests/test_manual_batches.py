# Synthetic test-only RUTs and folios; not sourced from user or production data.
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import BHEBatch, BHEBatchItem, BHEEmission, Client
from app.db.session import get_db
from app.main import app
from app.schemas.bhe_emission import EmissionStatus
from app.schemas.bhe import BHERequest
from app.services.bhe_batch_service import _process_sii_items_in_session, _retryable_failed_items
from app.services.bhe_emission_history_service import create_pending_emission, mark_emission_failed, mark_emission_success


def setup_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-batches.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    app.dependency_overrides[get_db] = lambda: db
    return engine, db, TestClient(app)


def payload(rut: str, retention: str, fecha: str):
    return {
        "rut_receptor": rut,
        "razon_social": "RECEPTOR TEST 034",
        "giro": "Servicios",
        "direccion": "DIRECCION PRUEBA 006",
        "region": 13,
        "comuna": "Santiago",
        "email": "temporal@example.com",
        "detalle": [{"descripcion": "Servicio puntual", "cantidad": 1, "precio_unitario": 45000}],
        "fecha_emision": fecha,
        "retention_mode": retention,
    }


def close_db(engine, db):
    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_manual_batch_keeps_snapshots_without_creating_clients(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        response = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", "2026-09-10"),
            payload("99990011-9", "receiver_retains_ppm", "2026-09-11"),
        ]})
        assert response.status_code == 201, response.text
        batch = response.json()
        assert batch["source"] == "manual"
        assert [item["client_id"] for item in batch["items"]] == [None, None]
        assert [item["retention_mode_override"] for item in batch["items"]] == ["issuer_handles_ppm", "receiver_retains_ppm"]
        assert [item["fecha_emision"] for item in batch["items"]] == ["2026-09-10", "2026-09-11"]
        assert batch["items"][0]["giro"] == "Servicios"
        assert batch["items"][0]["email"] == "temporal@example.com"
        assert db.scalar(select(func.count()).select_from(BHEBatchItem).where(BHEBatchItem.batch_id == batch["id"])) == 2
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_manual_item_accepts_missing_optional_recipient_fields(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        item = payload("99990001-1", "issuer_handles_ppm", date.today().isoformat())
        item.pop("razon_social")
        item.pop("giro")
        item.pop("email")
        response = client.post("/bhe/batches/manual", json={"items": [item]})
        assert response.status_code == 201, response.text
        saved = response.json()["items"][0]
        assert saved["client_id"] is None
        assert saved["razon_social"] == ""
        assert saved["giro"] is None
        assert saved["email"] is None
        assert saved["direccion"] == item["direccion"]
        assert saved["comuna"] == item["comuna"]
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_manual_queue_can_append_edit_and_delete_snapshots_without_clients(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        batch = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", "2026-09-10"),
        ]}).json()
        appended = client.post(f"/bhe/batches/{batch['id']}/manual-items", json=payload(
            "99990011-9", "receiver_retains_ppm", "2026-09-11"
        ))
        assert appended.status_code == 200, appended.text
        assert len(appended.json()["items"]) == 2
        assert all(item["client_id"] is None for item in appended.json()["items"])

        item_id = appended.json()["items"][0]["id"]
        edited = client.patch(f"/bhe/batches/{batch['id']}/manual-items/{item_id}", json=payload(
            "99990001-1", "receiver_retains_ppm", "2026-09-12"
        ))
        assert edited.status_code == 200, edited.text
        assert edited.json()["items"][0]["fecha_emision"] == "2026-09-12"
        assert edited.json()["items"][0]["retention_mode_override"] == "receiver_retains_ppm"
        assert db.scalar(select(func.count()).select_from(Client)) == 0

        assert client.delete(f"/bhe/batches/{batch['id']}/items/{item_id}").status_code == 204
        remaining = client.get(f"/bhe/batches/{batch['id']}").json()
        assert len(remaining["items"]) == 1
        last_id = remaining["items"][0]["id"]
        assert client.delete(f"/bhe/batches/{batch['id']}/items/{last_id}").status_code == 204
        assert db.get(BHEBatch, batch["id"]) is None
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_manual_queue_rejects_mutation_after_processing_evidence(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        batch = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", "2026-09-10"),
        ]}).json()
        row = db.get(BHEBatch, batch["id"])
        row.items[0].status = "emission_status_unknown"
        db.commit()
        response = client.post(f"/bhe/batches/{batch['id']}/manual-items", json=payload(
            "99990011-9", "issuer_handles_ppm", "2026-09-11"
        ))
        assert response.status_code == 409
        assert db.scalar(select(func.count()).select_from(BHEBatchItem)) == 1
    finally:
        close_db(engine, db)


def test_manual_batch_worker_reuses_one_session_and_links_history_without_client(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        created = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", "2026-09-10"),
            payload("99990011-9", "receiver_retains_ppm", "2026-09-11"),
        ]}).json()
        batch = db.get(BHEBatch, created["id"])
        seen = []

        class Browser:
            @contextmanager
            def batch_session(self):
                seen.append("session_open")
                try: yield self
                finally: seen.append("session_close")

        class Provider:
            def execute_in_batch_session(self, session, request, browser, client_id=None, batch_item_id=None):
                seen.append((client_id, request.retention_mode.value, request.fecha_emision.isoformat()))
                emission = create_pending_emission(session, request, client_id=client_id, provider="sii", batch_item_id=batch_item_id)
                mark_emission_success(session, emission, {"folio": 1000 + len(seen), "status": 200, "fecha_emision": request.fecha_emision.isoformat(), "sii_resolved_recipient_name": f"Nombre SII {len(seen)}"})
                return SimpleNamespace(status="success", emission=emission, message="ok")

        results = _process_sii_items_in_session(db, batch, list(batch.items), Provider(), Browser(), 0)
        db.expire_all()
        refreshed = db.get(BHEBatch, batch.id)
        assert seen[0] == "session_open" and seen[-1] == "session_close"
        assert seen.count("session_open") == 1
        assert [row[0] for row in seen if isinstance(row, tuple)] == [None, None]
        assert [row[1] for row in seen if isinstance(row, tuple)] == ["issuer_handles_ppm", "receiver_retains_ppm"]
        assert refreshed.status == "completed"
        assert [item.folio for item in refreshed.items] == [1002, 1003]
        assert [result.folio for result in results] == [1002, 1003]
        emissions = db.scalars(select(BHEEmission).order_by(BHEEmission.id)).all()
        assert len(emissions) == 2
        assert all(emission.client_id is None and emission.status == EmissionStatus.SUCCESS.value for emission in emissions)
        batch_api = client.get(f"/bhe/batches/{batch.id}").json()
        assert [item["sii_resolved_recipient_name"] for item in batch_api["items"]] == ["Nombre SII 2", "Nombre SII 3"]
        history = client.get("/bhe/history").json()
        assert len(history) == 2
        assert {record["folio"] for record in history} == {1002, 1003}
        assert all(record["provider"] == "sii" for record in history)
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_manual_unknown_result_cannot_be_retried_through_batch_api(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        response = client.post("/bhe/batches/manual", json={"items": [payload("99990001-1", "issuer_handles_ppm", date.today().isoformat())]})
        batch = db.get(BHEBatch, response.json()["id"])
        item = batch.items[0]
        item.status = "emission_status_unknown"
        item.final_click_occurred = True
        batch.status = "completed_with_errors"
        db.commit()
        assert client.post(f"/bhe/batches/{batch.id}/retry-failed", json={"confirm": True}).status_code == 409
        assert client.post(f"/bhe/batches/{batch.id}/items/{item.id}/retry", json={"confirm": True}).status_code == 409
    finally:
        close_db(engine, db)


def test_retryability_requires_persisted_pre_final_failure_evidence(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        created = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", date.today().isoformat()),
            payload("99990011-9", "issuer_handles_ppm", date.today().isoformat()),
            payload("99990013-5", "issuer_handles_ppm", date.today().isoformat()),
            payload("99990015-1", "issuer_handles_ppm", date.today().isoformat()),
            payload("99990016-K", "issuer_handles_ppm", date.today().isoformat()),
        ]}).json()
        batch = db.get(BHEBatch, created["id"])
        safe, clicked, unknown, folio, no_diagnostics = batch.items

        def failed_emission(item, stage=None):
            request = BHERequest.model_validate(payload(item.rut_receptor, "issuer_handles_ppm", date.today().isoformat()))
            emission = create_pending_emission(db, request, client_id=None, provider="sii", batch_item_id=item.id)
            mark_emission_failed(db, emission, "Synthetic failure")
            if stage is not None:
                emission.sii_retention_diagnostics = {"failure_stage": stage}
            item.bhe_emission_id = emission.id
            item.status = "failed"
            return emission

        safe_emission = failed_emission(safe, "preview_reached")
        failed_emission(clicked, "final_button_click")
        clicked.final_click_occurred = True
        unknown_emission = failed_emission(unknown, "final_button_click")
        unknown.status = "emission_status_unknown"
        unknown.final_click_occurred = True
        unknown_emission.status = "emission_status_unknown"
        folio_emission = failed_emission(folio, "preview_reached")
        folio_emission.status = "success"
        folio_emission.folio = 2001
        folio_emission.updated_at = folio_emission.updated_at
        folio.status = "failed"
        failed_emission(no_diagnostics)
        batch.status = "completed_with_errors"
        db.commit()

        response = client.get(f"/bhe/batches/{batch.id}")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert rows[0]["retryable"] is True
        assert rows[0]["retry_block_reason"] is None
        assert rows[1]["retryable"] is False
        assert rows[1]["retry_block_reason"] == "final_click_occurred"
        assert rows[2]["retryable"] is False
        assert rows[2]["retry_block_reason"] == "emission_status_unknown"
        assert rows[3]["retryable"] is False
        assert rows[3]["retry_block_reason"] == "folio_present"
        assert rows[4]["retryable"] is False
        assert rows[4]["retry_block_reason"] == "pre_final_failure_not_proven"
        cloned_response = client.post(f"/bhe/batches/{batch.id}/clone-editable")
        assert cloned_response.status_code == 201, cloned_response.text
        cloned = cloned_response.json()
        assert cloned["source_batch_id"] == batch.id
        assert [item["rut_receptor"] for item in cloned["items"]] == [safe.rut_receptor]
        assert cloned["items"][0]["id"] != safe.id
        assert cloned["items"][0]["status"] == "pending"
        assert db.get(BHEBatch, batch.id).status == "completed_with_errors"
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_clone_fourteen_safe_failures_and_atomically_apply_common_data(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        original_date = (date.today() - timedelta(days=1)).isoformat()
        created = client.post("/bhe/batches/manual", json={"items": [
            {**payload("99990001-1", "issuer_handles_ppm", original_date),
             "detalle": [{"descripcion": "Old detail", "cantidad": 1, "precio_unitario": 100 + index}]}
            for index in range(14)
        ]}).json()
        batch = db.get(BHEBatch, created["id"])
        original_ids = [item.id for item in batch.items]
        original_amounts = [item.monto for item in batch.items]
        for item in batch.items:
            request = BHERequest.model_validate(payload(item.rut_receptor, "issuer_handles_ppm", original_date))
            emission = create_pending_emission(db, request, batch_item_id=item.id)
            mark_emission_failed(db, emission, "Original failure")
            emission.sii_retention_diagnostics = {"failure_stage": "form_filled", "post2_network_capture": {"post2_request_observed": False}}
            item.bhe_emission_id = emission.id
            item.status = "failed"
            item.error_message = "Original failure"
        batch.status = "completed_with_errors"
        db.commit()
        clone_response = client.post(f"/bhe/batches/{batch.id}/clone-editable")
        assert clone_response.status_code == 201, clone_response.text
        cloned = clone_response.json()
        assert cloned["source_batch_id"] == batch.id and cloned["status"] == "ready"
        assert len(cloned["items"]) == 14
        assert not set(original_ids) & {item["id"] for item in cloned["items"]}
        assert [item["monto"] for item in cloned["items"]] == original_amounts
        assert all(item["client_id"] is None and item["status"] == "pending" and item["bhe_emission_id"] is None for item in cloned["items"])

        update = {"retention_mode": "issuer_handles_ppm", "direccion": "DIRECCION PRUEBA 025", "region": 10,
                  "comuna": "Puerto Montt", "detalle": "New detail", "fecha_emision": date.today().isoformat()}
        applied = client.post(f"/bhe/batches/{cloned['id']}/apply-manual-common", json=update)
        assert applied.status_code == 200, applied.text
        updated = applied.json()["items"]
        assert all(item["fecha_emision"] == update["fecha_emision"] and item["direccion"] == "DIRECCION PRUEBA 025" and item["detalle"][0]["descripcion"] == "New detail" for item in updated)
        assert [item["monto"] for item in updated] == original_amounts
        assert all(item["rut_receptor"] == "999900011" for item in updated)
        assert client.post(f"/bhe/batches/{batch.id}/apply-manual-common", json=update).status_code == 409
        independent = payload("99990001-1", "receiver_retains_ppm", original_date)
        independent["direccion"] = "Only this item"
        edited = client.patch(f"/bhe/batches/{cloned['id']}/manual-items/{updated[0]['id']}", json=independent)
        assert edited.status_code == 200
        assert edited.json()["items"][0]["direccion"] == "Only this item"
        assert all(item["direccion"] == "DIRECCION PRUEBA 025" for item in edited.json()["items"][1:])
        original = client.get(f"/bhe/batches/{batch.id}").json()
        assert all(item["status"] == "failed" and item["fecha_emision"] == original_date and item["error_message"] == "Original failure" for item in original["items"])
        assert all((db.get(BHEEmission, item["bhe_emission_id"]).sii_retention_diagnostics or {}).get("failure_stage") == "form_filled" for item in original["items"])
        assert db.scalar(select(func.count()).select_from(Client)) == 0

        processed_ids = []

        class Browser:
            @contextmanager
            def batch_session(self):
                yield self

        class Provider:
            def execute_in_batch_session(self, session, request, browser, client_id=None, batch_item_id=None):
                assert client_id is None
                processed_ids.append(batch_item_id)
                emission = create_pending_emission(session, request, client_id=None, provider="sii", batch_item_id=batch_item_id)
                mark_emission_success(session, emission, {
                    "folio": 2002 + len(processed_ids), "status": 200,
                    "fecha_emision": request.fecha_emision.isoformat(),
                })
                return SimpleNamespace(status="success", emission=emission, message="ok")

        cloned_batch = db.get(BHEBatch, cloned["id"])
        _process_sii_items_in_session(db, cloned_batch, list(cloned_batch.items), Provider(), Browser(), 0)
        db.expire_all()
        assert set(processed_ids) == {item["id"] for item in cloned["items"]}
        assert db.get(BHEBatch, cloned["id"]).status == "completed"
        assert db.get(BHEBatch, batch.id).status == "completed_with_errors"
        assert all(item.status == "failed" for item in db.get(BHEBatch, batch.id).items)
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)


def test_batch_retry_runner_receives_only_safe_failed_items(tmp_path):
    engine, db, client = setup_db(tmp_path)
    try:
        created = client.post("/bhe/batches/manual", json={"items": [
            payload("99990001-1", "issuer_handles_ppm", date.today().isoformat()),
            payload("99990011-9", "receiver_retains_ppm", date.today().isoformat()),
            payload("99990013-5", "issuer_handles_ppm", date.today().isoformat()),
        ]}).json()
        batch = db.get(BHEBatch, created["id"])
        first, retry, last = batch.items

        def request_for(item):
            return BHERequest.model_validate(payload(item.rut_receptor, item.retention_mode_override, date.today().isoformat()))

        for item, folio in ((first, 1002), (last, 1003)):
            emission = create_pending_emission(db, request_for(item), client_id=None, provider="sii", batch_item_id=item.id)
            mark_emission_success(db, emission, {"folio": folio, "status": 200, "fecha_emision": date.today().isoformat()})
            item.bhe_emission_id = emission.id
            item.status = "success"
            item.final_click_occurred = True
        failed = create_pending_emission(db, request_for(retry), client_id=None, provider="sii", batch_item_id=retry.id)
        mark_emission_failed(db, failed, "Synthetic pre-final failure")
        failed.sii_retention_diagnostics = {"failure_stage": "preview_reached"}
        retry.bhe_emission_id = failed.id
        retry.status = "failed"
        retry.final_click_occurred = False
        batch.status = "completed_with_errors"
        db.commit()
        db.expire_all()
        batch = db.get(BHEBatch, batch.id)
        selected = _retryable_failed_items(db, batch)
        assert [item.id for item in selected] == [retry.id]
        touched = []

        class Browser:
            @contextmanager
            def batch_session(self):
                yield self

        class Provider:
            def execute_in_batch_session(self, session, request, browser, client_id=None, batch_item_id=None):
                touched.append(batch_item_id)
                emission = create_pending_emission(session, request, client_id=client_id, provider="sii", batch_item_id=batch_item_id)
                mark_emission_success(session, emission, {"folio": 1004, "status": 200, "fecha_emision": date.today().isoformat()})
                return SimpleNamespace(status="success", emission=emission, message="ok")

        results = _process_sii_items_in_session(db, batch, selected, Provider(), Browser(), 0)
        db.expire_all()
        refreshed = db.get(BHEBatch, batch.id)
        assert touched == [retry.id]
        assert [item.folio for item in (refreshed.items[0], refreshed.items[2])] == [1002, 1003]
        assert refreshed.items[1].status == "success"
        assert refreshed.items[1].folio == 1004
        assert len(results) == 1 and results[0].item_id == retry.id
        assert db.scalar(select(func.count()).select_from(BHEEmission)) == 4
        assert db.scalar(select(func.count()).select_from(Client)) == 0
    finally:
        close_db(engine, db)
