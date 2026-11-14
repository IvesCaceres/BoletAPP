from app import local


def test_local_uses_configured_port_without_reload(monkeypatch):
    captured = {}

    monkeypatch.setenv("APP_PORT", "8123")
    monkeypatch.setattr(local.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        local.uvicorn,
        "run",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    local.main([])

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["reload"] is False


def test_local_reload_is_opt_in(monkeypatch):
    captured = {}

    monkeypatch.delenv("APP_PORT", raising=False)
    monkeypatch.setattr(local.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        local.uvicorn,
        "run",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    local.main(["--reload"])

    assert captured["port"] == 8001
    assert captured["reload"] is True
