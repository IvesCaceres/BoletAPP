"""Inspect distribution resources without touching local application data."""
import importlib.util
from pathlib import Path

import pytest

from app import launcher

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/windows_release_metadata.py"
SPEC = importlib.util.spec_from_file_location("windows_release_metadata_test", MODULE_PATH)
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


@pytest.mark.parametrize("filename", ["client.db", ".env", "Libro1.csv", "personal.log", ".links/install"])
def test_distribution_inspection_rejects_sensitive_resources(tmp_path, filename):
    private = tmp_path / filename
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("fictitious test file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Forbidden distribution files"):
        metadata.inspect_release(tmp_path)


def test_prepare_runtime_rejects_browser_revision_mismatch(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("fake UI", encoding="utf-8")
    browser = tmp_path / "browsers"
    browser.mkdir()
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(launcher, "frontend_dist_dir", lambda: frontend)
    monkeypatch.setattr(launcher, "bundled_playwright_dir", lambda: browser)
    monkeypatch.setattr(launcher, "build_info", lambda: {"browser_revisions": {"chromium": "000"}})
    for name in ("APPBOLETAS_RELEASE", "DATABASE_URL", "SII_BROWSER_PROFILE_DIR", "SII_DEBUG_DIR"):
        monkeypatch.setenv(name, "test-previous-value")
    with pytest.raises(RuntimeError, match="no corresponde"):
        launcher._prepare_runtime()
