"""Consultas SQLAlchemy para el historial de emisiones BHE."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BHEEmission


def create_pending_emission(db: Session, values: dict) -> BHEEmission:
    emission = BHEEmission(**values)
    db.add(emission)
    db.flush()
    db.refresh(emission)
    return emission


def get_emission(db: Session, emission_id: int) -> BHEEmission | None:
    return db.get(BHEEmission, emission_id)


def list_emissions(
    db: Session,
    *,
    status: str | None = None,
    rut_receptor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[BHEEmission]:
    statement = select(BHEEmission)
    if status:
        statement = statement.where(BHEEmission.status == status)
    if rut_receptor:
        statement = statement.where(BHEEmission.rut_receptor == rut_receptor)
    if date_from:
        statement = statement.where(BHEEmission.fecha_emision_solicitada >= date_from)
    if date_to:
        statement = statement.where(BHEEmission.fecha_emision_solicitada <= date_to)
    statement = statement.order_by(BHEEmission.created_at.desc(), BHEEmission.id.desc())
    return list(db.scalars(statement).all())
