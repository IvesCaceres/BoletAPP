"""Endpoints de salud locales de AppBoletas."""

from fastapi import APIRouter, Request


router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(request: Request):
    return {"success": True, "provider": "sii", **getattr(request.app.state, "launcher_identity", {})}
