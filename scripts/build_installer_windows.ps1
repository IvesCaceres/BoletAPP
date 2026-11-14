param(
    [string]$Python = "",
    [string]$Compiler = "",
    [switch]$ReusePortable,
    [switch]$SkipBrowserInstall,
    [switch]$Candidate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root 'backend\venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Build Python is missing; no dependencies will be installed automatically.' }
if (-not $Compiler) {
    $CompilerCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($CompilerCommand) { $Compiler = $CompilerCommand.Source }
    foreach ($CompilerCandidate in @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
    )) {
        if (-not $Compiler -and (Test-Path -LiteralPath $CompilerCandidate -PathType Leaf)) { $Compiler = $CompilerCandidate }
    }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw 'Inno Setup is missing. Obtain authorization and install from https://jrsoftware.org/isdl.php. This script never installs tools.'
}
$CompilerInfo = (Get-Item -LiteralPath $Compiler).VersionInfo
$CompilerVersion = [version]::new($CompilerInfo.FileMajorPart, $CompilerInfo.FileMinorPart, $CompilerInfo.FileBuildPart, $CompilerInfo.FilePrivatePart)
# Inno 7's ISCC is a generic loader (file version 0); query its signed compiler engine.
if ($CompilerVersion.Major -eq 0) {
    $EngineVersion = (& $Compiler --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $EngineVersion -notmatch '(?m)^(\d+\.\d+\.\d+(?:\.\d+)?)$') {
        throw 'Could not verify the Inno compiler engine version.'
    }
    $CompilerVersion = [version]$Matches[1]
}
if ($CompilerInfo.ProductName -notlike '*Inno Setup*' -or
    $CompilerVersion -lt [version]'6.3.0') { throw 'A trusted Inno Setup compiler >= 6.3 is required.' }
$AppVersion = (Get-Content -LiteralPath (Join-Path $Root 'packaging\VERSION') -Raw).Trim()
if ($AppVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid AppBoletas release version.' }
$ReleaseDir = Join-Path $Root 'dist\release'
$PortableOutput = Join-Path $ReleaseDir "portable-$AppVersion"
$PayloadDir = Join-Path $PortableOutput 'AppBoletas'
if ($Candidate) { $ReleaseDir = Join-Path $ReleaseDir 'candidate' }

Push-Location $Root
try {
    if ((git status --porcelain) -and -not $Candidate) {
        throw 'Source tree must be committed and clean for a final release. Validate a candidate first; do not claim final provenance for dirty code.'
    }
    & $Python -c "import PyInstaller, playwright, fastapi; print('Build dependencies available')"
    if ($LASTEXITCODE -ne 0) { throw 'Missing build dependencies; no installation was attempted.' }
    if (-not $ReusePortable) {
        & (Join-Path $Root 'scripts\build_windows.ps1') -Python $Python -PortableOutput $PortableOutput -SkipBrowserInstall:$SkipBrowserInstall
    }
    $CandidateArgs = @()
    if ($Candidate) { $CandidateArgs = @('--candidate') }
    & $Python scripts/windows_release_metadata.py --validate-installer-payload $PayloadDir @CandidateArgs
    if ($LASTEXITCODE -ne 0) { throw 'Portable validation failed; Setup was not compiled.' }
    & $Compiler "/DAppVersion=$AppVersion" "/DPayloadDir=$PayloadDir" "/DReleaseDir=$ReleaseDir" packaging/AppBoletas.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
    $Installer = Join-Path $ReleaseDir "AppBoletas-Setup-$AppVersion-windows-x64.exe"
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) { throw 'Setup output is missing.' }
    & $Python scripts/windows_release_metadata.py --installer $Installer --payload-dir $PayloadDir --compiler-version $CompilerVersion.ToString() @CandidateArgs
    if ($LASTEXITCODE -ne 0) { throw 'Installer manifest validation failed.' }
    Write-Host "Installer: $Installer"
    Get-FileHash -LiteralPath $Installer -Algorithm SHA256
} finally {
    Pop-Location
}
