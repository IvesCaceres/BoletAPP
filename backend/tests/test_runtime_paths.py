from pathlib import Path
from app import runtime


def test_release_paths_use_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("APPBOLETAS_RELEASE", "1")
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path / "AppBoletas"))
    assert runtime.user_data_dir() == (tmp_path / "AppBoletas").resolve()
    assert runtime.database_path() == (tmp_path / "AppBoletas" / "data" / "appboletas.db").resolve()
    assert runtime.sii_profile_dir() == (tmp_path / "AppBoletas" / "sii-browser-profile").resolve()


def test_development_database_path_stays_compatible(monkeypatch):
    monkeypatch.delenv("APPBOLETAS_RELEASE", raising=False)
    monkeypatch.delenv("APPBOLETAS_USER_DATA_DIR", raising=False)
    assert runtime.database_path().name == "appboletas.db"
    assert runtime.database_path().parent == Path(__file__).resolve().parents[2]


def test_user_directories_are_created(monkeypatch, tmp_path):
    root = tmp_path / "AppBoletas"
    monkeypatch.setenv("APPBOLETAS_RELEASE", "1")
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(root))
    runtime.ensure_user_directories()
    assert (root / "data").is_dir()
    assert (root / "sii-browser-profile").is_dir()
    assert (root / "logs").is_dir()
