"""Offline lifecycle checks. No real DB, browser profile or SII execution."""
import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import launcher


def test_occupied_port_is_not_reused():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        reserved = launcher.reserve_loopback_socket(occupied.getsockname()[1])
        try:
            assert reserved.getsockname()[0] == "127.0.0.1"
            assert reserved.getsockname()[1] != occupied.getsockname()[1]
        finally:
            reserved.close()


def test_ready_rejects_another_backend(monkeypatch):
    runtime = launcher.LocalServer()
    runtime.url = "http://127.0.0.1:12345/"
    runtime.server = SimpleNamespace(started=True)
    opener = Mock()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock()
    response.read.return_value = json.dumps({"success": True, "instance_id": "another-instance"}).encode()
    opener.open.return_value = response
    monkeypatch.setattr(launcher.urllib.request, "build_opener", Mock(return_value=opener))
    assert not runtime.ready()
    response.read.return_value = json.dumps({"success": True, "instance_id": runtime.instance_id}).encode()
    assert runtime.ready()


@pytest.mark.parametrize("error", [RuntimeError("missing bundled files"), SystemExit(3)])
def test_startup_failure_is_logged_and_never_opens_browser(tmp_path, monkeypatch, error):
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "_prepare_runtime", Mock(side_effect=error))
    browser = Mock()
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    runtime = launcher.LocalServer(True)
    runtime.thread.start()
    runtime.thread.join(5)
    assert runtime.done.is_set()
    assert runtime.error is not None
    assert json.loads((tmp_path / "instance.json").read_text())["status"] == "failed"
    with pytest.raises(RuntimeError):
        runtime.wait_ready(1)
    browser.assert_not_called()


def test_instance_lock_blocks_second_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path))
    with launcher.InstanceLock():
        with pytest.raises(RuntimeError, match="ya esta abierta"):
            with launcher.InstanceLock():
                pass
    with launcher.InstanceLock():
        pass


def test_startup_timeout_requests_owned_server_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path))
    runtime = launcher.LocalServer()
    with pytest.raises(TimeoutError):
        runtime.wait_ready(0.01)
    assert runtime.stop_requested.is_set()
    assert json.loads((tmp_path / "instance.json").read_text())["status"] == "failed"


def test_release_settings_do_not_read_cwd_env(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///should-not-be-used.db", encoding="utf-8")
    monkeypatch.setenv("APPBOLETAS_RELEASE", "1")
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path / "user"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        assert "should-not-be-used" not in get_settings().database_url
        assert str(tmp_path / "user" / "data" / "appboletas.db") in get_settings().database_url
    finally:
        get_settings.cache_clear()
