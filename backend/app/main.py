"""
Punto de entrada principal de la aplicación FastAPI.
Configura el app, eventos de startup y routing.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import health, bhe, bhe_history, bhe_batches, clients
from app.core.config import get_settings
from app.db.schema_guard import check_database_schema_if_present
from app.db.session import get_session_factory
from app.services.bhe_batch_service import reconcile_interrupted_batches
from app.runtime import frontend_dist_dir

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Evento de lifecycle: startup y shutdown."""
    settings = get_settings()
    check_database_schema_if_present(settings.database_url)
    recovery_db = get_session_factory()()
    try:
        reconcile_interrupted_batches(recovery_db)
    finally:
        recovery_db.close()
    logger.info("=" * 50)
    logger.info("Iniciando AppBoletas API")
    logger.info("Provider activo para nuevas BHE: SII")
    logger.info("=" * 50)

    yield

    logger.info("Deteniendo AppBoletas API")


def create_app() -> FastAPI:
    """Factory para crear la aplicación FastAPI."""
    app = FastAPI(
        title="AppBoletas API",
        description="API para emisión de Boletas de Honorarios Electrónicas",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # Registrar routers
    app.include_router(health.router)
    app.include_router(bhe.router)
    app.include_router(bhe_history.router)
    app.include_router(bhe_batches.router)
    app.include_router(clients.router)

    frontend_dist = frontend_dist_dir()
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", tags=["root"])
    async def root():
        """Endpoint raíz de la API o entrada de la interfaz compilada."""
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {
            "name": "AppBoletas API",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
            "bhe_emit": "/bhe/emit",
            "bhe_test_payload": "/bhe/test-payload",
        }

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_fallback(path: str):
        if path.startswith(("api/", "bhe/", "clients", "health")):
            return {"detail": "Not found"}
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {"detail": "Not found"}

    return app


app = create_app()
