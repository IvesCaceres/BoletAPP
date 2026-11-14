"""Owned loopback server and desktop controller; no development server required."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from fastapi import Request

from app.runtime import (
    bundled_playwright_dir, database_path, debug_dir, ensure_user_directories,
    frontend_dist_dir, log_dir, resource_dir, sii_profile_dir, user_data_dir,
)

LOGGER = logging.getLogger("app.launcher")


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_info() -> dict:
    path = resource_dir() / "build_info.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"version": "development", "code_commit": "unknown"}


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(handler, "appboletas_launcher", False) for handler in root.handlers):
        handler = RotatingFileHandler(log_dir() / "appboletas.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.appboletas_launcher = True
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)


class InstanceLock:
    """OS lock, released on exit/crash; acquired before migrations/profile use."""

    def __enter__(self):
        import msvcrt
        self.handle = (user_data_dir() / "instance.lock").open("a+b")
        if self.handle.seek(0, 2) == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("AppBoletas ya esta abierta. Utilice la ventana existente.") from exc
        return self

    def __exit__(self, *args):
        import msvcrt
        self.handle.seek(0)
        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


def reserve_loopback_socket(preferred: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", preferred))
    except OSError:
        if preferred == 0:
            sock.close()
            raise
        sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


def _prepare_runtime() -> None:
    # Frozen distribution never inherits a developer DB/profile/.env by accident.
    os.environ["APPBOLETAS_RELEASE"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path()}"
    os.environ["SII_BROWSER_PROFILE_DIR"] = str(sii_profile_dir())
    os.environ["SII_DEBUG_DIR"] = str(debug_dir())
    browser_dir = bundled_playwright_dir()
    if not (frontend_dist_dir() / "index.html").is_file():
        raise RuntimeError("Faltan archivos de interfaz. Extraiga la carpeta completa de AppBoletas.")
    if not browser_dir.is_dir():
        raise RuntimeError("Falta el navegador integrado. Extraiga la carpeta completa de AppBoletas.")
    revisions = build_info().get("browser_revisions", {})
    for name, revision in revisions.items():
        if not (browser_dir / f"{name}-{revision}").is_dir():
            raise RuntimeError("El navegador integrado no corresponde a esta version de AppBoletas.")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    from app.core.config import get_settings
    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config
    config = Config(str(resource_dir() / "alembic.ini"))
    config.set_main_option("script_location", str(resource_dir() / "alembic"))
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("%", "%%"))
    command.upgrade(config, "head")
    configure_logging()  # Alembic may have reconfigured root logging.


class LocalServer:
    def __init__(self, headless: bool = False):
        self.instance_id = secrets.token_hex(16)
        self.control_token = secrets.token_hex(32) if headless else None
        self.headless = headless
        self.url = None
        self.server = None
        self.error = None
        self.done = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, name="AppBoletas-backend", daemon=False)

    def state(self, status: str) -> None:
        data = {**build_info(), "instance_id": self.instance_id, "pid": os.getpid(), "status": status, "url": self.url}
        if self.control_token:
            data["test_control_token"] = self.control_token
        write_json(user_data_dir() / "instance.json", data)

    def _run(self) -> None:
        sock = None
        try:
            self.state("starting")
            LOGGER.info("Starting owned backend instance=%s", self.instance_id)
            _prepare_runtime()
            if self.stop_requested.is_set():
                return
            import uvicorn
            from app.main import create_app
            app = create_app()
            app.state.launcher_identity = {**build_info(), "instance_id": self.instance_id}
            if self.headless:
                from fastapi import HTTPException

                @app.post("/__local_test_shutdown", include_in_schema=False)
                async def test_shutdown(request: Request):
                    if not secrets.compare_digest(request.headers.get("X-AppBoletas-Control", ""), self.control_token):
                        raise HTTPException(403)
                    self.stop()
                    return {"stopping": True}

            sock = reserve_loopback_socket(int(os.getenv("APPBOLETAS_PORT", "8001")))
            self.url = f"http://127.0.0.1:{sock.getsockname()[1]}/"
            self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_config=None, access_log=False, loop="asyncio"))
            self.state("listening")
            if not self.stop_requested.is_set():
                self.server.run(sockets=[sock])
        except (Exception, SystemExit) as exc:
            self.error = exc
            LOGGER.exception("Owned backend startup/runtime failed")
            self.state("failed")
        finally:
            if sock:
                sock.close()
            if not self.error:
                self.state("stopped")
            self.done.set()

    def ready(self) -> bool:
        if not self.url or not self.server or not self.server.started or self.done.is_set():
            return False
        try:
            # No system HTTP proxy should handle the application's loopback health check.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.url + "health", timeout=0.3) as response:
                data = json.load(response)
            return data.get("success") is True and data.get("instance_id") == self.instance_id
        except (OSError, ValueError):
            return False

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error or self.done.is_set():
                raise RuntimeError("No se pudo iniciar el servidor local. Revise los diagnosticos.") from self.error
            if self.ready():
                self.state("ready")
                return
            self.done.wait(0.1)
        self.stop()
        self.error = TimeoutError("El servidor local no estuvo listo dentro del tiempo de inicio.")
        self.state("failed")
        raise self.error

    def stop(self) -> None:
        self.stop_requested.set()
        if self.server:
            self.server.should_exit = True


def show_controller(runtime: LocalServer, timeout: float) -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title("AppBoletas")
    root.geometry("460x190")
    root.resizable(False, False)
    status = tk.StringVar(value="Iniciando servidor local...")
    tk.Label(root, text="AppBoletas", font=("Segoe UI", 18)).pack(pady=12)
    tk.Label(root, textvariable=status, wraplength=430).pack(pady=8)
    button = tk.Button(root, text="Abrir interfaz", state="disabled", command=lambda: webbrowser.open(runtime.url))
    button.pack()
    tk.Label(root, text="Mantenga esta ventana abierta. Cerrarla detiene el servidor.", font=("Segoe UI", 9)).pack(pady=10)
    deadline = time.monotonic() + timeout
    closing = False

    def close():
        nonlocal closing
        from app.services.sii_browser_service import SII_BROWSER_EXECUTION_LOCK
        if SII_BROWSER_EXECUTION_LOCK.locked():
            messagebox.showwarning("AppBoletas", "Hay una operacion SII activa. Espere a que termine antes de cerrar.")
            return
        closing = True
        status.set("Cerrando servidor local...")
        button.config(state="disabled")
        runtime.stop()
        root.after(100, poll_close)

    def poll_close():
        if runtime.done.is_set():
            root.destroy()
        else:
            root.after(100, poll_close)

    def poll_start():
        if closing:
            return
        if runtime.error or runtime.done.is_set() or time.monotonic() >= deadline:
            LOGGER.error("Startup did not become ready")
            if runtime.error is None:
                runtime.error = RuntimeError("El servidor local no alcanzo el estado listo.")
                runtime.state("failed")
            messagebox.showerror("AppBoletas", "No se pudo iniciar el servidor local.\nDiagnostico: " + str(log_dir() / "appboletas.log"))
            runtime.stop()
            root.after(100, poll_close)
        elif runtime.ready():
            runtime.state("ready")
            status.set("Servidor local listo.")
            button.config(state="normal")
            if os.getenv("APPBOLETAS_NO_AUTO_OPEN") != "1":
                webbrowser.open(runtime.url)
        else:
            root.after(100, poll_start)

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll_start)
    root.mainloop()


def browser_self_test() -> None:
    """Offline packaged-browser check, explicitly never contacts SII."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_playwright_dir())
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content("<title>AppBoletas offline browser check</title>")
            assert page.title() == "AppBoletas offline browser check"
            write_json(log_dir() / "chromium-self-test.json", {"success": True, "offline": True, "browser_version": browser.version})
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless-launcher", action="store_true", help="Offline packaging test controller; no browser UI")
    parser.add_argument("--self-test-browser", action="store_true", help="Offline bundled Chromium verification")
    args = parser.parse_args()
    os.environ["APPBOLETAS_RELEASE"] = "1"
    ensure_user_directories()
    if sys.stdout is None:
        sys.stdout = open(log_dir() / "runtime-output.log", "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    configure_logging()
    runtime = None
    try:
        with InstanceLock():
            if args.self_test_browser:
                browser_self_test()
                return 0
            runtime = LocalServer(args.headless_launcher)
            runtime.thread.start()
            try:
                timeout = float(os.getenv("APPBOLETAS_STARTUP_TIMEOUT", "45"))
                if args.headless_launcher:
                    runtime.wait_ready(timeout)
                    runtime.done.wait()
                else:
                    show_controller(runtime, timeout)
                return 1 if runtime.error else 0
            finally:
                runtime.stop()
                runtime.thread.join()  # Do not release instance lock before backend closes.
    except Exception as exc:
        LOGGER.exception("Launcher failed")
        if not args.headless_launcher and not args.self_test_browser:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc) + "\nDiagnosticos: " + str(log_dir()), "AppBoletas", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
