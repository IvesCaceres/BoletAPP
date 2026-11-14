"""Generate non-sensitive provenance and inspect the explicit release inputs."""
import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import shutil
import zipfile

import playwright

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "packaging/VERSION").read_text(encoding="utf-8").strip()


def browser_requirements():
    vendor = Path(playwright.__file__).parent / "driver/package/browsers.json"
    data = json.loads(vendor.read_text(encoding="utf-8"))
    return {entry["name"].replace("-", "_"): entry["revision"] for entry in data["browsers"]
            if entry["name"] in ("chromium", "chromium-headless-shell", "ffmpeg", "winldd")}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def inspect_release(directory):
    forbidden = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        relative = path.relative_to(directory).as_posix().lower()
        if (name.startswith(".env") or name.endswith((".db", ".sqlite", ".sqlite3", ".csv", ".log"))
                or any(suffix in name for suffix in (".db-", ".sqlite-", ".sqlite3-"))
                or any(part in relative.split("/") for part in ("sii-browser-profile", "debug", "logs", ".git", ".links", "storage_state", "screenshots", "backups"))
                or name in ("cookies", "login data", "history", "diagnostic.json", "preview.html", "preview.png",
                            "pre_submit.html", "pre_submit.png", "run_manifest.json", "retention_mutation_trace.json")):
            forbidden.append(relative)
    if forbidden:
        raise RuntimeError("Forbidden distribution files: " + ", ".join(forbidden))
    if not (directory / "AppBoletas.exe").is_file():
        raise RuntimeError("Missing executable")
    if not (directory / "_internal" / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("Missing compiled frontend")


def validate_installer_payload(directory, *, candidate=False):
    directory = Path(directory).resolve()
    inspect_release(directory)
    info = json.loads((directory / "_internal/build_info.json").read_text(encoding="utf-8"))
    if info["version"] != VERSION or info["code_commit"] != git("rev-parse", "HEAD"):
        raise RuntimeError("Portable version/source commit differs; rebuild before compiling Setup")
    if not candidate and (info.get("source_dirty") or git("status", "--porcelain")):
        raise RuntimeError("Release requires a clean source tree; do not label uncommitted code as a final release")
    for name, revision in info["browser_revisions"].items():
        if not (directory / "_internal/playwright-browsers" / f"{name}-{revision}").is_dir():
            raise RuntimeError("Missing packaged browser revision")
    if info["browser_revisions"] != browser_requirements() or info["playwright_version"] != version("playwright"):
        raise RuntimeError("Portable Playwright provenance differs from the build environment")
    inventory = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Reparse points are not allowed in release inputs")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative not in ("AppBoletas.exe", "LEEME.md") and not relative.startswith("_internal/"):
                raise RuntimeError("Unexpected installer input: " + relative)
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            inventory.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest})
    return info, inventory


def installer_manifest(directory, installer, compiler_version, *, candidate=False):
    info, inventory = validate_installer_payload(directory, candidate=candidate)
    installer = Path(installer)
    expected = f"AppBoletas-Setup-{VERSION}-windows-x64.exe"
    if installer.name != expected or not installer.is_file():
        raise RuntimeError("Missing or unexpected Setup artifact")
    with installer.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = {**info, "artifact": installer.name, "bytes": installer.stat().st_size, "sha256": digest,
                "inno_setup_version": compiler_version, "install_mode": "PER_USER", "uninstaller": True,
                "digitally_signed": False, "sensitive_file_scan": "PASS", "clean_windows_test": "PENDING",
                "installer_tests": "NOT_RUN", "candidate": candidate, "payload_files": inventory}
    target = installer.parent / "installer_manifest.json"
    target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "payload_files"}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--validate-installer-payload", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--compiler-version")
    parser.add_argument("--payload-dir", type=Path, default=ROOT / "dist/release/portable/AppBoletas")
    parser.add_argument("--candidate", action="store_true", help="Allow uncommitted validation candidate, never a final release")
    args = parser.parse_args()
    if args.validate_installer_payload:
        validate_installer_payload(args.validate_installer_payload, candidate=args.candidate)
        print("Installer payload validation: PASS")
        return
    if args.installer:
        if not args.compiler_version:
            parser.error("--compiler-version is required with --installer")
        installer_manifest(args.payload_dir, args.installer, args.compiler_version, candidate=args.candidate)
        return
    requirements = browser_requirements()
    info = {"version": VERSION, "code_commit": git("rev-parse", "HEAD"), "source_dirty": bool(git("status", "--porcelain")),
            "platform": "windows-x64", "playwright_version": version("playwright"), "browser_revisions": requirements}
    build = ROOT / "build" / "windows"
    build.mkdir(parents=True, exist_ok=True)
    if not args.archive:
        for name, revision in requirements.items():
            if not (build / "playwright-browsers" / f"{name}-{revision}").is_dir():
                raise RuntimeError(f"Missing matching Playwright browser: {name}-{revision}. Build without -SkipBrowserInstall.")
        (build / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        return
    release = ROOT / "dist" / "release"
    portable = args.payload_dir.resolve()
    shutil.copyfile(ROOT / "docs/windows-release.md", portable / "LEEME.md")
    inspect_release(portable)
    archive = release / f"AppBoletas-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in portable.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(portable.parent))
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = {**info, "artifact": archive.name, "bytes": archive.stat().st_size, "sha256": digest, "sensitive_file_scan": "PASS"}
    (release / f"release_manifest-{VERSION}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
