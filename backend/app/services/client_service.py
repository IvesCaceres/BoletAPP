"""Reglas de negocio para clientes frecuentes."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Client
from app.repositories import client_repository
from app.schemas.client import ClientCreate, ClientUpdate


class ClientNotFoundError(Exception):
    pass


class DuplicateClientError(Exception):
    pass


def create_client(db: Session, payload: ClientCreate) -> Client:
    if client_repository.get_client_by_rut(db, payload.rut):
        raise DuplicateClientError("Ya existe un cliente con ese RUT")
    try:
        client = client_repository.create_client(db, payload.model_dump())
        db.commit()
        return client
    except IntegrityError as error:
        db.rollback()
        raise DuplicateClientError("Ya existe un cliente con ese RUT") from error


def get_client(db: Session, client_id: int) -> Client:
    client = client_repository.get_client(db, client_id)
    if client is None:
        raise ClientNotFoundError("Cliente no encontrado")
    return client


def list_clients(db: Session, include_inactive: bool = False) -> list[Client]:
    return client_repository.list_clients(db, include_inactive=include_inactive)


def update_client(db: Session, client_id: int, payload: ClientUpdate) -> Client:
    client = get_client(db, client_id)
    values = payload.model_dump(exclude_unset=True)
    if "rut" in values:
        existing = client_repository.get_client_by_rut(db, values["rut"])
        if existing is not None and existing.id != client_id:
            raise DuplicateClientError("Ya existe un cliente con ese RUT")
    try:
        client = client_repository.update_client(db, client, values)
        db.commit()
        return client
    except IntegrityError as error:
        db.rollback()
        raise DuplicateClientError("Ya existe un cliente con ese RUT") from error


def deactivate_client(db: Session, client_id: int) -> Client:
    client = get_client(db, client_id)
    client.activo = False
    db.commit()
    db.refresh(client)
    return client


def client_to_bhe_receptor(client: Client) -> dict:
    """Convierte un cliente persistido en datos de receptor para una BHE futura."""
    return {
        "rut_receptor": client.rut,
        "razon_social": client.razon_social,
        "giro": client.giro,
        "direccion": client.direccion,
        "region": client.region,
        "comuna": client.comuna,
        "email": client.email,
    }
