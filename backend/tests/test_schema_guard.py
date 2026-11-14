from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.db.schema_guard import SchemaDriftError, check_database_schema


def test_schema_guard_accepts_database_at_head(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'head.db'}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    check_database_schema(database_url)


def test_schema_guard_rejects_database_behind_head(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'outdated.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0004_add_batch_global_detail')"))
    engine.dispose()

    with pytest.raises(SchemaDriftError, match="python -m alembic upgrade head"):
        check_database_schema(database_url)
