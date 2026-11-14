"""Endpoints para clientes frecuentes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.client import ClientCreate, ClientResponse, ClientUpdate
from app.services.client_service import (
    ClientNotFoundError,
    DuplicateClientError,
    create_client,
    deactivate_client,
    get_client,
    list_clients,
    update_client,
)


router = APIRouter(prefix="/clients", tags=["clients"])


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
def create_client_endpoint(payload: ClientCreate, db: Session = Depends(get_db)):
    try:
        return create_client(db, payload)
    except DuplicateClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[ClientResponse])
def list_clients_endpoint(
    include_inactive: bool = Query(False), db: Session = Depends(get_db)
):
    return list_clients(db, include_inactive=include_inactive)


@router.get("/{client_id}", response_model=ClientResponse)
def get_client_endpoint(client_id: int, db: Session = Depends(get_db)):
    try:
        return get_client(db, client_id)
    except ClientNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/{client_id}", response_model=ClientResponse)
def update_client_endpoint(
    client_id: int, payload: ClientUpdate, db: Session = Depends(get_db)
):
    try:
        return update_client(db, client_id, payload)
    except ClientNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{client_id}", response_model=ClientResponse)
def deactivate_client_endpoint(client_id: int, db: Session = Depends(get_db)):
    try:
        return deactivate_client(db, client_id)
    except ClientNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
