"""Endpoints de historial de emisiones BHE."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.bhe_emission import BHEEmissionDetail, BHEEmissionSummary, EmissionStatus
from app.services.bhe_emission_history_service import (
    BHEEmissionNotFoundError,
    get_emission,
    list_emissions,
)


router = APIRouter(prefix="/bhe", tags=["BHE history"])


@router.get("/history", response_model=list[BHEEmissionSummary])
def list_bhe_history(
    status_filter: EmissionStatus | None = Query(None, alias="status"),
    rut_receptor: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    return list_emissions(
        db,
        status=status_filter.value if status_filter else None,
        rut_receptor=rut_receptor,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/history/{emission_id}", response_model=BHEEmissionDetail)
def get_bhe_history(emission_id: int, db: Session = Depends(get_db)):
    try:
        return get_emission(db, emission_id)
    except BHEEmissionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
