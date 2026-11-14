"""Installer policy and Windows handle tests; never run Setup on the host."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

import pytest

from app import launcher
from test_release_resources import metadata

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "packaging/AppBoletas.iss").read_text(encoding="utf-8")


def test_stable_id_per_user_and_non_elevated():
    assert "AppId={{9EB849EA-4560-4554-8688-C4FE108FD3C9}" in SCRIPT
    assert "PrivilegesRequired=lowest" in SCRIPT
    assert "DefaultDirName={localappdata}\\Programs\\AppBoletas" in SCRIPT
    assert "UsePreviousAppDir=yes" in SCRIPT
    assert "{userprograms}\\AppBoletas\\AppBoletas" in SCRIPT
    assert "{userdesktop}\\AppBoletas" in SCRIPT
    assert "Flags: unchecked" in SCRIPT


def test_uninstall_never_deletes_user_data_or_force_closes():
    assert "[UninstallDelete]" not in SCRIPT
    assert "[InstallDelete]" not in SCRIPT
    assert "CloseApplications=no" in SCRIPT
    assert "RestartApplications=no" in SCRIPT
    assert "function InitializeUninstall: Boolean;\nbegin\n  Result := AcquireMaintenanceLock;" in SCRIPT
    assert "function InitializeSetup: Boolean;\nbegin\n  Result := AcquireMaintenanceLock;" in SCRIPT
    assert "MaintenanceLock := TFileStream.Create(LockPath, $0012)" in SCRIPT
    assert "BeforeInstall: ReleaseMaintenanceLock" in SCRIPT
    file_section = SCRIPT.split("[Files]")[1].split("[Icons]")[0]
    assert "{#PayloadDir}\\_internal\\*" in file_section
    assert "localappdata" not in file_section.lower()


def test_version_single_source():
    assert metadata.VERSION == (ROOT / "packaging/VERSION").read_text().strip() == "0.4.0"
    build = (ROOT / "scripts/build_installer_windows.ps1").read_text(encoding="utf-8")
    assert "packaging\\VERSION" in build
    assert "/DAppVersion=$AppVersion" in build
    assert "winget install" not in build
    assert "foreach ($Candidate " not in build
    assert '"portable-$AppVersion"' in build
    assert '-PortableOutput $PortableOutput' in build
    assert '--payload-dir $PayloadDir' in build
    assert '& $Compiler --version' in build


@pytest.mark.parametrize("private", ["db.db-wal", "db.sqlite-shm", "preview.html", "pre_submit.png", "diagnostic.json", "run_manifest.json", "backups/private.txt"])
def test_private_artifacts_rejected(tmp_path, private):
    path = tmp_path / private
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fictitious", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Forbidden"):
        metadata.inspect_release(tmp_path)


def fake_payload(tmp_path, monkeypatch):
    (tmp_path / "AppBoletas.exe").write_bytes(b"fictitious executable for policy test")
    internal = tmp_path / "_internal"
    frontend = internal / "frontend/dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("fake frontend")
    (internal / "playwright-browsers/chromium-000").mkdir(parents=True)
    info = {"version": "0.4.0", "code_commit": "fake-commit", "source_dirty": False,
            "browser_revisions": {"chromium": "000"}, "playwright_version": "fake"}
    (internal / "build_info.json").write_text(json.dumps(info))
    monkeypatch.setattr(metadata, "git", lambda *args: "fake-commit" if args[0] == "rev-parse" else "")
    monkeypatch.setattr(metadata, "browser_requirements", lambda: {"chromium": "000"})
    monkeypatch.setattr(metadata, "version", lambda name: "fake")
    return info


def test_payload_provenance_and_inventory(tmp_path, monkeypatch):
    fake_payload(tmp_path, monkeypatch)
    info, inventory = metadata.validate_installer_payload(tmp_path)
    assert info["version"] == "0.4.0"
    assert all(len(record["sha256"]) == 64 for record in inventory)


def test_final_payload_rejects_dirty_sources(tmp_path, monkeypatch):
    fake_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(metadata, "git", lambda *args: "fake-commit" if args[0] == "rev-parse" else " M fake.py")
    with pytest.raises(RuntimeError, match="clean source tree"):
        metadata.validate_installer_payload(tmp_path)
    metadata.validate_installer_payload(tmp_path, candidate=True)


def test_payload_rejects_old_portable_version(tmp_path, monkeypatch):
    info = fake_payload(tmp_path, monkeypatch)
    info["version"] = "0.2.0"
    (tmp_path / "_internal/build_info.json").write_text(json.dumps(info))
    with pytest.raises(RuntimeError, match="version/source"):
        metadata.validate_installer_payload(tmp_path)


def exclusive_handle(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    return kernel, handle


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_maintenance_excludes_legacy_02_instance_and_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setenv("APPBOLETAS_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "preserve.db").write_bytes(b"fictitious database sentinel")
    invalid = ctypes.c_void_p(-1).value
    with launcher.InstanceLock():
        _, handle = exclusive_handle(tmp_path / "instance.lock")
        assert handle == invalid
    kernel, handle = exclusive_handle(tmp_path / "instance.lock")
    assert handle != invalid
    try:
        with pytest.raises(OSError):
            with launcher.InstanceLock():
                pass
    finally:
        kernel.CloseHandle(handle)
    with launcher.InstanceLock():
        pass
    assert (tmp_path / "preserve.db").read_bytes() == b"fictitious database sentinel"
