from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Client
import scripts.import_clients_csv as importer


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text(
        "Nombre de fantasía (opcional);Razón social / Nombre;Rut;Direccion;comuna;region\n"
        + "\n".join(";".join(row) for row in rows),
        encoding="utf-8",
    )


def row(rut: str, legal: str = "Legal", fantasy: str = "Visual") -> list[str]:
    return [fantasy, legal, rut, "Calle Ñandú 1", "Santiago", "Metropolitana de Santiago"]


def test_dry_run_does_not_modify_db(tmp_path: Path):
    db = tmp_path / "dry.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    csv_path = tmp_path / "clients.csv"
    write_csv(csv_path, [row("99990008-9")])

    assert importer.main([str(csv_path), "--database-url", f"sqlite:///{db}", "--dry-run"]) == 0
    with Session(engine) as session:
        assert session.query(Client).count() == 0


def test_apply_inserts_new_and_skips_existing(tmp_path: Path):
    db = tmp_path / "apply.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Client(rut="999900089", razon_social="RECEPTOR PRUEBA 02", direccion="Old", region=13, comuna="Santiago"))
        session.commit()
    csv_path = tmp_path / "clients.csv"
    write_csv(csv_path, [row("99990008-9"), row("99990020-8", legal="New Legal")])

    assert importer.main([str(csv_path), "--database-url", f"sqlite:///{db}", "--apply"]) == 0
    with Session(engine) as session:
        clients = session.query(Client).order_by(Client.rut).all()
        assert len(clients) == 2
        assert clients[0].razon_social == "RECEPTOR PRUEBA 02"
        assert clients[1].razon_social == "New Legal"


def test_invalid_duplicate_conflict_and_rollback(tmp_path: Path):
    db = tmp_path / "conflict.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    csv_path = tmp_path / "clients.csv"
    write_csv(csv_path, [row("99990020-8", legal="A"), row("99990020-8", legal="B"), row("99.990.005-0", legal="Bad")])

    assert importer.main([str(csv_path), "--database-url", f"sqlite:///{db}", "--dry-run"]) == 0
    with Session(engine) as session:
        assert session.query(Client).count() == 0


def test_apply_rolls_back_when_commit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "rollback.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    csv_path = tmp_path / "clients.csv"
    write_csv(csv_path, [row("99990020-8")])

    def fail_commit(self):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(importer.Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        importer.main([str(csv_path), "--database-url", f"sqlite:///{db}", "--apply"])
    with Session(engine) as session:
        assert session.query(Client).count() == 0
