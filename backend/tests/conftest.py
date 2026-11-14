import os
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Configuracion local de pruebas; no contiene credenciales ni endpoints externos.
TEST_DATABASE = Path(tempfile.gettempdir()) / "appboletas-pytest.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE}"
alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
alembic_config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
command.upgrade(alembic_config, "head")
