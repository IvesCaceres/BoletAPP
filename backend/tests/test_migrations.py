from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migration_upgrade_downgrade_upgrade(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'migration-test.db'}"
    alembic_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    assert {"clients", "bhe_emissions"}.issubset(inspect(engine).get_table_names())

    command.downgrade(alembic_config, "-1")
    assert {"clients", "bhe_emissions"}.issubset(inspect(engine).get_table_names())
    assert "bhe_batches" in inspect(engine).get_table_names()
    assert "detalle_global" in {
        column["name"] for column in inspect(engine).get_columns("bhe_batches")
    }
    # `downgrade -1` reverts only the newest migration (0006), so the
    # client fantasy-name column from 0005 remains present.
    assert "nombre_fantasia" in {
        column["name"] for column in inspect(engine).get_columns("clients")
    }

    command.upgrade(alembic_config, "head")
    assert {"clients", "bhe_emissions", "bhe_batches", "bhe_batch_items"}.issubset(
        inspect(engine).get_table_names()
    )
    assert "nombre_fantasia" in {
        column["name"] for column in inspect(engine).get_columns("clients")
    }
    engine.dispose()
