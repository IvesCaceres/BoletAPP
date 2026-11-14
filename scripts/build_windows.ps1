param(
    [string]$Python = "",
    [string]$PortableOutput = "",
    [switch]$SkipBrowserInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"
$BuildRoot = Join-Path $Root "build\windows"
$BrowserDir = Join-Path $BuildRoot "playwright-browsers"
$Output = if ($PortableOutput) { [IO.Path]::GetFullPath($PortableOutput) } else { Join-Path $Root "dist\release\portable" }
$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $Root 'dist\release'))
if (-not $Output.StartsWith($ReleaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Portable output must be a dedicated directory under dist/release.'
}
$PayloadDir = Join-Path $Output 'AppBoletas'
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("AppBoletas-build-" + [guid]::NewGuid().ToString("N"))
if (-not $Python) { $Python = Join-Path $Root "backend\venv\Scripts\python.exe" }

Push-Location $Root
$PreviousPythonPath = $env:PYTHONPATH
$PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    & npm --prefix $Frontend run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
    if (-not $SkipBrowserInstall) {
        New-Item -ItemType Directory -Force -Path $BrowserDir | Out-Null
        $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserDir
        & $Python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Chromium installation failed" }
    }
    & $Python scripts\windows_release_metadata.py
    if ($LASTEXITCODE -ne 0) { throw "Release metadata failed" }
    # Fresh work outside OneDrive avoids locked/read-only compiler caches from older builds.
    & $Python -m PyInstaller packaging\AppBoletas.spec --noconfirm --distpath $Output --workpath $WorkDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    & $Python scripts\windows_release_metadata.py --archive --payload-dir $PayloadDir
    if ($LASTEXITCODE -ne 0) { throw "Release inspection/archive failed" }
    Write-Host "Portable artifact: $Output\AppBoletas"
} finally {
    if ($null -eq $PreviousPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $PreviousPythonPath }
    if ($null -eq $PreviousBrowserPath) { Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue } else { $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowserPath }
    Pop-Location
}
