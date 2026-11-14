"""Exercise the COMPILED EXE offline, against disposable fictitious user data only."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def http(url, method="GET", body=None, headers=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        payload = response.read()
        return json.loads(payload) if "application/json" in response.headers.get("Content-Type", "") else payload


def wait_instance(process, user_dir, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"EXE exited before ready, code={process.returncode}; logs={user_dir / 'logs'}")
        try:
            data = json.loads((user_dir / "instance.json").read_text())
            if data["pid"] == process.pid and data["status"] == "ready":
                health = http(data["url"] + "health")
                assert health["instance_id"] == data["instance_id"]
                return data
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.1)
    raise AssertionError("Compiled backend never ready")


def stop(process, state):
    http(state["url"] + "__local_test_shutdown", "POST", {}, {"X-AppBoletas-Control": state["test_control_token"]})
    assert process.wait(timeout=30) == 0
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", int(state["url"].split(":")[2].strip("/")))) != 0


def close_controller(process):
    """Close only the test EXE's Tk window; never touch development processes."""
    user32 = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value == "AppBoletas":
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    assert found, "Compiled desktop controller missing"
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW(found[0], 0x0010, 0, 0)
    assert process.wait(timeout=30) == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/release/portable/AppBoletas/AppBoletas.exe")
    parser.add_argument("--report", type=Path, default=ROOT / "dist/release/smoke-test-report.json")
    args = parser.parse_args()
    exe = args.exe.resolve()
    work = Path(tempfile.mkdtemp(prefix="appboletas-release-test-"))
    user_dir = work / "fictitious-user"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("APPBOLETAS_", "SII_", "PLAYWRIGHT_", "PYTHON")) and key != "DATABASE_URL"}
    env.update(APPBOLETAS_USER_DATA_DIR=str(user_dir), APPBOLETAS_NO_AUTO_OPEN="1",
               PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    checks = {}
    owned = []

    def launch(extra=(), overrides=None):
        process = subprocess.Popen([str(exe), *extra], env={**env, **(overrides or {})}, cwd=work,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        owned.append(process)
        return process

    try:
        # Force conflict with an independent service; never stop the real dev server.
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            preferred = occupied.getsockname()[1]
            env["APPBOLETAS_PORT"] = str(preferred)
            process = launch(("--headless-launcher",))
            state = wait_instance(process, user_dir)
            assert int(state["url"].split(":")[2].strip("/")) != preferred
            checks["occupied_port_fallback"] = "PASS"
            checks["compiled_health_identity"] = "PASS"
            checks["no_python_node_in_path"] = "PASS"
            assert b"/assets/" in http(state["url"])
            second = launch(("--headless-launcher",))
            assert second.wait(timeout=30) != 0
            assert http(state["url"] + "health")["instance_id"] == state["instance_id"]
            checks["second_instance_rejected"] = "PASS"
            client = http(state["url"] + "clients", "POST", {
                "rut": "99990004-6", "razon_social": "EMPRESA FICTICIA DE PRUEBAS",
                "nombre_fantasia": "RECEPTOR TEST 007", "direccion": "DIRECCION PRUEBA 005",
                "region": 13, "comuna": "Santiago", "detalle_default": [{"descripcion": "Servicio ficticio", "monto": 1000}],
            })
            assert client["id"] > 0
            stop(process, state)

        # Historical fixture in the disposable database, NOT an emission or SII call.
        with sqlite3.connect(user_dir / "data/appboletas.db") as db:
            db.execute("""INSERT INTO bhe_emissions
                (client_id,rut_receptor,razon_social,direccion,region,comuna,detalle,monto_total,
                 fecha_emision_solicitada,status,provider,folio,retention_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                client["id"], "99990004-6", "EMPRESA FICTICIA DE PRUEBAS", "DIRECCION PRUEBA 005", 13,
                "Santiago", '[{"descripcion":"Fixture historico ficticio","monto":1000}]', 1000,
                "2026-01-01", "success", "sii", 999999, "issuer_handles_ppm"))
        process = launch(("--headless-launcher",))
        state = wait_instance(process, user_dir)
        assert len(http(state["url"] + "clients")) == 1
        assert len(http(state["url"] + "bhe/history")) == 1
        checks["client_and_history_persistence"] = "PASS"

        from playwright.sync_api import sync_playwright
        browser_root = exe.parent / "_internal/playwright-browsers"
        chromium = next(browser_root.glob("chromium-*/chrome-win*/chrome.exe"))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(chromium), headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def offline_only(route):
                    if not route.request.url.startswith(state["url"]):
                        route.abort()
                    elif (route.request.method == "POST" and "/bhe/" in route.request.url
                          and route.request.url.rstrip("/") != state["url"].rstrip("/") + "/bhe/batches"):
                        route.abort()  # No accidental emit/retry/diagnostic in packaging tests.
                    else:
                        route.continue_()

                page.route("**/*", offline_only)
                page.goto(state["url"], wait_until="networkidle")
                page.get_by_role("heading", name="Emisión mensual", exact=True).wait_for()
                checks["monthly_view"] = "PASS"
                page.get_by_role("button", name="Crear batch draft").click()
                page.locator(".available-client[role='button']").first.click()
                monthly_add = page.get_by_role("button", name="Agregar 1 clientes al lote")
                monthly_add.wait_for()
                assert monthly_add.evaluate("el => getComputedStyle(el).marginTop") == "16px"
                checks["monthly_add_button_spacing"] = "PASS"
                page.get_by_role("button", name="Clientes", exact=True).click()
                page.get_by_role("heading", name="Clientes frecuentes").wait_for()
                page.get_by_text("RECEPTOR TEST 007", exact=True).first.wait_for()
                checks["packaged_frontend_clients"] = "PASS"
                page.get_by_role("button", name="Nuevo cliente").click()
                client_actions = page.get_by_role("button", name="Guardar cliente").locator("..")
                assert client_actions.evaluate("el => getComputedStyle(el).marginTop") == "16px"
                checks["client_form_button_spacing"] = "PASS"
                page.get_by_role("button", name="Cancelar", exact=True).click()
                page.get_by_role("button", name="Historial", exact=True).click()
                page.get_by_role("heading", name="Historial BHE").wait_for()
                page.get_by_text("RECEPTOR TEST 007", exact=True).first.wait_for()
                page.get_by_text("999999", exact=False).first.wait_for()
                checks["packaged_history_fantasy_and_folio"] = "PASS"
                page.get_by_role("button", name="Nueva boleta", exact=True).click()
                page.get_by_role("heading", name="Nueva boleta", exact=False).wait_for()
                checks["individual_view_without_emitting"] = "PASS"
                manual_add = page.get_by_role("button", name="+ Agregar boleta")
                assert manual_add.locator("..").evaluate("el => getComputedStyle(el).marginTop") == "16px"
                checks["manual_add_button_spacing"] = "PASS"
                assert not errors, errors
                assert "No fue posible conectar con el servidor" not in page.locator("body").inner_text()
                checks["frontend_no_connection_errors"] = "PASS"
            finally:
                browser.close()
        stop(process, state)

        # Production desktop controller + actual WM_CLOSE graceful shutdown.
        process = launch()
        state = wait_instance(process, user_dir)
        close_controller(process)
        checks["compiled_desktop_start_and_close"] = "PASS"

        failure_dir = work / "failed-user"
        failed = launch(("--headless-launcher",), {"APPBOLETAS_USER_DATA_DIR": str(failure_dir),
                                                  "APPBOLETAS_FRONTEND_DIST": str(work / "missing-frontend")})
        assert failed.wait(timeout=30) != 0
        assert json.loads((failure_dir / "instance.json").read_text())["status"] == "failed"
        assert (failure_dir / "logs/appboletas.log").is_file()
        checks["controlled_startup_failure_logged"] = "PASS"
        chromium_test = launch(("--self-test-browser",))
        assert chromium_test.wait(timeout=60) == 0
        assert json.loads((user_dir / "logs/chromium-self-test.json").read_text())["offline"] is True
        checks["compiled_bundled_chromium_offline"] = "PASS"
    finally:
        for process in owned:
            if process.poll() is None:
                # Only subprocesses created by THIS test, never a development backend.
                process.terminate()
                process.wait(timeout=10)
        report = {"exe": str(exe), "isolated_test_dir": str(work), "checks": checks,
                  "sii_navigation": False, "real_db_modified": False,
                  "clean_windows_machine": "PENDING"}
        output = args.report.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
