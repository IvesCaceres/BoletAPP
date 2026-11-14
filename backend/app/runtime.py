"""Runtime paths used by development and the Windows portable build."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_release_runtime() -> bool:
    return bool(getattr(sys, "frozen", False)) or os.getenv("APPBOLETAS_RELEASE") == "1"


def user_data_dir() -> Path:
    override = os.getenv("APPBOLETAS_USER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_release_runtime() and os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AppBoletas"
    return Path(__file__).resolve().parents[1] / ".local"


def database_path() -> Path:
    if is_release_runtime():
        return user_data_dir() / "data" / "appboletas.db"
    return Path(__file__).resolve().parents[2] / "appboletas.db"


def sii_profile_dir() -> Path:
    return user_data_dir() / "sii-browser-profile"


def debug_dir() -> Path:
    return user_data_dir() / "debug"


def log_dir() -> Path:
    return user_data_dir() / "logs"


def ensure_user_directories() -> None:
    for path in (user_data_dir(), database_path().parent, sii_profile_dir(), debug_dir(), log_dir()):
        path.mkdir(parents=True, exist_ok=True)


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def frontend_dist_dir() -> Path:
    override = os.getenv("APPBOLETAS_FRONTEND_DIST")
    return Path(override).expanduser().resolve() if override else resource_dir() / "frontend" / "dist"


def bundled_playwright_dir() -> Path:
    return resource_dir() / "playwright-browsers"
