"""Consultas SQLAlchemy para clientes."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Client


def create_client(db: Session, values: dict) -> Client:
    client = Client(**values)
    db.add(client)
    db.flush()
    db.refresh(client)
    return client


def get_client(db: Session, client_id: int) -> Client | None:
    return db.get(Client, client_id)


def get_client_by_rut(db: Session, rut: str) -> Client | None:
    return db.scalar(select(Client).where(Client.rut == rut))


def list_clients(db: Session, include_inactive: bool = False) -> list[Client]:
    statement = select(Client)
    if not include_inactive:
        statement = statement.where(Client.activo.is_(True))
    statement = statement.order_by(Client.razon_social.asc(), Client.id.asc())
    return list(db.scalars(statement).all())


def update_client(db: Session, client: Client, values: dict) -> Client:
    for field, value in values.items():
        setattr(client, field, value)
    db.flush()
    db.refresh(client)
    return client
