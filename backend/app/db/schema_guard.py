"""Validación de que la base de datos está alineada con Alembic."""
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine


class SchemaDriftError(RuntimeError):
    """La base de datos no está en uno de los heads de Alembic."""


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    return Config(str(backend_root / "alembic.ini"))


def check_database_schema(database_url: str) -> None:
    """Falla con un mensaje accionable cuando el esquema quedó atrás."""
    config = _alembic_config()
    config.set_main_option("sqlalchemy.url", database_url)
    script = ScriptDirectory.from_config(config)
    expected_heads = set(script.get_heads())

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current_heads = set(context.get_current_heads())
    finally:
        engine.dispose()

    if current_heads != expected_heads:
        current = ", ".join(sorted(current_heads)) or "none"
        expected = ", ".join(sorted(expected_heads)) or "none"
        raise SchemaDriftError(
            "Database schema is outdated or uninitialized. "
            f"Current: {current}; expected: {expected}. "
            "Run: python -m alembic upgrade head"
        )


def check_database_schema_if_present(database_url: str) -> None:
    """Valida SQLite existente; una DB inexistente se crea mediante Alembic."""
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        if not db_path.exists():
            return
    check_database_schema(database_url)
