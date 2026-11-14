from pathlib import Path
import json
import sys

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPEC).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend" / "dist"
BROWSER = ROOT / "build" / "windows" / "playwright-browsers"
sys.path.insert(0, str(BACKEND))

datas = [
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "alembic.ini"), "."),
    (str(FRONTEND), "frontend/dist"),
    (str(ROOT / "build" / "windows" / "build_info.json"), "."),
    (str(BACKEND / "scripts" / "import_clients_csv.py"), "tools"),
]
if not BROWSER.is_dir():
    raise RuntimeError("Bundled Chromium is required; run scripts/build_windows.ps1")
# Only vendor browser distributions, not Playwright's .links installation metadata.
revisions = json.loads((ROOT / "build/windows/build_info.json").read_text(encoding="utf-8"))["browser_revisions"]
for browser_name, revision in revisions.items():
    browser = BROWSER / f"{browser_name}-{revision}"
    if not browser.is_dir():
        raise RuntimeError(f"Missing matching browser: {browser.name}")
    datas.append((str(browser), f"playwright-browsers/{browser.name}"))

hiddenimports = collect_submodules("app") + [
    "alembic",
    "sqlalchemy.dialects.sqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(BACKEND / "app" / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AppBoletas", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AppBoletas")
