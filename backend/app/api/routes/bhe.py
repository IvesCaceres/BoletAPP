"""Rutas para emisión de BHE mediante el flujo local SII."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.bhe import BHERequest, BHEResponse, FinalBHEConfirmation
from app.schemas.responses import APIErrorResponse
from app.services.bhe_emission_history_service import BHEEmissionNotFoundError, get_emission
from app.services.bhe_provider import SIIBHEProvider
from app.services.bhe_provider import SIIProviderResult
from app.utils.rut import clean_rut, format_rut, split_rut, validate_rut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bhe", tags=["BHE"])


def _sii_response(result: SIIProviderResult) -> BHEResponse:
    return BHEResponse(
        success=result.status == "success",
        status=result.status,
        message=result.message,
        provider="sii",
        emission_id=result.emission.id,
        folio=result.emission.folio,
        fecha_emision=(
            result.emission.fecha_emision_provider.isoformat()
            if hasattr(result.emission.fecha_emision_provider, "isoformat")
            else result.emission.fecha_emision_provider
        ),
        codigo_barras=result.emission.codigo_barras,
        preview_validated=result.preview_validated,
        recipient_rut_ok=result.recipient_rut_ok,
        sii_resolved_recipient_name_present=result.sii_resolved_recipient_name_present,
        sii_resolved_recipient_name=result.sii_resolved_recipient_name,
    )


@router.post(
    "/emit",
    response_model=BHEResponse,
    responses={422: {"model": APIErrorResponse, "description": "Error de validación"}},
    summary="Emitir BHE en SII",
)
async def emit_bhe(request_data: BHERequest, db: Session = Depends(get_db)) -> BHEResponse:
    logger.info("Recibido request de BHE SII - receptor omitido por privacidad")
    try:
        return _sii_response(await SIIBHEProvider().execute(db, request_data))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except Exception as error:
        logger.exception("Error en emisión SII")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno en el flujo SII") from error


@router.post("/emissions/{emission_id}/confirm-final", response_model=BHEResponse)
async def confirm_final_bhe(
    emission_id: int,
    confirmation: FinalBHEConfirmation,
    db: Session = Depends(get_db),
) -> BHEResponse:
    if confirmation.confirm is not True:
        raise HTTPException(status_code=422, detail="confirm debe ser true")
    try:
        emission = get_emission(db, emission_id)
    except BHEEmissionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Emisión no encontrada") from error
    if emission.provider != "sii":
        raise HTTPException(status_code=409, detail="La confirmación final solo aplica a emisiones SII")
    if emission.status != "awaiting_final_confirmation":
        raise HTTPException(status_code=409, detail="La emisión no está pendiente de confirmación final")
    try:
        result = await SIIBHEProvider().confirm_final(db, emission)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError:
        return BHEResponse(
            success=False,
            status="emission_status_unknown",
            message="No fue posible confirmar automáticamente si la boleta fue emitida. No vuelvas a emitirla hasta verificarla en el SII.",
            provider="sii",
            emission_id=emission_id,
        )
    return BHEResponse(
        success=True,
        status="success",
        message=str(result.get("provider_message") or "Boleta emitida correctamente."),
        folio=result.get("folio"),
        fecha_emision=result.get("fecha_emision"),
        pdf_base64=result.get("pdf_base64"),
        codigo_barras=result.get("codigo_barras"),
        provider="sii",
        emission_id=emission_id,
    )


@router.get("/validate-rut/{rut}")
async def validate_rut_endpoint(rut: str):
    valid = validate_rut(rut)
    parts = split_rut(rut) if valid else None
    return {
        "input": rut,
        "valid": valid,
        "formatted": format_rut(rut) if valid else None,
        "number": parts[0] if parts else None,
        "dv": parts[1] if parts else None,
    }
