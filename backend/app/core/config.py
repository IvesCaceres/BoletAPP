"""Configuración local de AppBoletas y del flujo SII."""

from functools import lru_cache
from enum import Enum
from pydantic import Field
from pydantic_settings import BaseSettings
from app.runtime import database_path, is_release_runtime


class ConfigurationError(Exception):
    """Error de configuración de la aplicación."""


class BHEProviderName(str, Enum):
    """Nombre del único provider activo para nuevas emisiones."""

    SII = "sii"


class Settings(BaseSettings):
    """Configuración que permanece necesaria para ejecución local SII."""

    bhe_batch_delay_seconds: float = Field(default=1.0, ge=0)
    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{database_path()}"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Singleton de configuración cacheada."""
    return Settings(_env_file=None) if is_release_runtime() else Settings()
