"""Arranque de desarrollo local: migra y luego inicia Uvicorn."""
import subprocess
import sys
import argparse
import os
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Arranque local estable de AppBoletas")
    parser.add_argument("--reload", action="store_true", help="habilita autoreload solo para desarrollo")
    args = parser.parse_args(argv)
    port = int(os.getenv("APP_PORT", "8001"))
    backend_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_root,
        check=True,
    )
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=args.reload)


if __name__ == "__main__":
    main()
