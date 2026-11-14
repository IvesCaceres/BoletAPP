param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [switch]$ConfirmDisposableVM
)
$ErrorActionPreference = 'Stop'
# Destructive operations are permitted ONLY inside a disposable VM.
$Machine = Get-CimInstance Win32_ComputerSystem
if (-not $ConfirmDisposableVM -or
    -not (Test-Path -LiteralPath 'C:\AppBoletasInstallerTestEnvironment.marker') -or
    $Machine.Model -notmatch 'Virtual Machine|VirtualBox|VMware|KVM|QEMU') {
    throw 'REFUSED: use a disposable Windows VM with marker and -ConfirmDisposableVM. Never run on the developer host.'
}
$Installer = (Resolve-Path -LiteralPath $Installer).Path
if ([IO.Path]::GetFileName($Installer) -ne 'AppBoletas-Setup-0.4.0-windows-x64.exe') { throw 'Unexpected installer.' }
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\AppBoletas'
$DataDir = Join-Path $env:LOCALAPPDATA 'AppBoletas'
$Registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{9EB849EA-4560-4554-8688-C4FE108FD3C9}_is1'
if ((Test-Path -LiteralPath $InstallDir) -or (Test-Path -LiteralPath $DataDir) -or (Test-Path -LiteralPath $Registry)) {
    throw 'REFUSED: the test account already contains AppBoletas files or data. Use a fresh disposable VM.'
}
$MenuShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'AppBoletas\AppBoletas.lnk'
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'AppBoletas.lnk'
if ((Test-Path -LiteralPath $MenuShortcut) -or (Test-Path -LiteralPath $DesktopShortcut)) { throw 'Existing shortcuts; use a fresh VM.' }
$ReportDir = Join-Path $env:TEMP ('AppBoletas-installer-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ReportDir | Out-Null
$Checks = [ordered]@{}
$PreviousEnv = @{}
foreach ($Name in @('APPBOLETAS_USER_DATA_DIR','APPBOLETAS_NO_AUTO_OPEN','APPBOLETAS_PORT','APPBOLETAS_FRONTEND_DIST','DATABASE_URL')) {
    $PreviousEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}
$env:APPBOLETAS_USER_DATA_DIR = $DataDir
$env:APPBOLETAS_NO_AUTO_OPEN = '1'
$env:APPBOLETAS_PORT = '0'
Remove-Item Env:APPBOLETAS_FRONTEND_DIST,Env:DATABASE_URL -ErrorAction SilentlyContinue

Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class AppBoletasInstallerTestWindow {
  public delegate bool Visitor(IntPtr hwnd, IntPtr data);
  [DllImport("user32.dll")] static extern bool EnumWindows(Visitor visit, IntPtr data);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
  public static void Close(int ownedPid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((hwnd, data) => {
      uint pid; GetWindowThreadProcessId(hwnd, out pid);
      var title = new StringBuilder(256); GetWindowText(hwnd, title, 256);
      if (pid == ownedPid && title.ToString() == "AppBoletas") found = hwnd;
      return true;
    }, IntPtr.Zero);
    if (found == IntPtr.Zero) throw new Exception("Owned controller not found");
    PostMessage(found, 0x10, IntPtr.Zero, IntPtr.Zero);
  }
}
'@

function Assert-Check([bool]$Condition, [string]$Name) {
    if (-not $Condition) { throw "FAIL: $Name" }
    $Checks[$Name] = 'PASS'
}

function Run-Setup {
    $Run = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS=desktopicon' -PassThru -Wait -WindowStyle Hidden
    return $Run.ExitCode
}

function Read-Ready {
    $Deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $Deadline) {
        try {
            $State = Get-Content -LiteralPath (Join-Path $DataDir 'instance.json') -Raw | ConvertFrom-Json
            if ($State.status -eq 'ready') {
                $Owned = Get-Process -Id $State.pid -ErrorAction Stop
                if ($Owned.Path -ne (Join-Path $InstallDir 'AppBoletas.exe')) { throw 'Unknown process; refusing to control it.' }
                $Health = Invoke-RestMethod -Uri ($State.url + 'health') -TimeoutSec 3
                if ($Health.instance_id -eq $State.instance_id) { return $State }
            }
        } catch { }
        Start-Sleep -Milliseconds 100
    }
    throw 'Installed executable did not become ready.'
}

function Close-Owned($State) {
    $Owned = Get-Process -Id $State.pid -ErrorAction Stop
    if ($Owned.Path -ne (Join-Path $InstallDir 'AppBoletas.exe')) { throw 'Refused to close an unknown process.' }
    [AppBoletasInstallerTestWindow]::Close($Owned.Id)
    if (-not $Owned.WaitForExit(30000)) { throw 'Owned application did not close; no forced termination.' }
}

function Start-Shortcut([string]$Path) {
    Start-Process -FilePath $Path -WindowStyle Hidden
    return Read-Ready
}

function Check-Client($State) {
    $Clients = Invoke-RestMethod -Uri ($State.url + 'clients')
    Assert-Check ($Clients.Count -eq 1 -and $Clients[0].nombre_fantasia -eq 'RECEPTOR TEST 006') 'client_persisted'
    $History = Invoke-RestMethod -Uri ($State.url + 'bhe/history')
    Assert-Check ($History.Count -eq 0) 'history_endpoint_empty_fixture'
}

try {
    Assert-Check ((Run-Setup) -eq 0) 'per_user_install'
    Assert-Check ((Test-Path -LiteralPath $Registry)) 'installed_app_registered_hkcu'
    $Entry = Get-ItemProperty -LiteralPath $Registry
    Assert-Check ($Entry.DisplayVersion -eq '0.4.0' -and $Entry.Publisher -eq 'AppBoletas') 'version_publisher'
    Assert-Check ((Test-Path -LiteralPath $MenuShortcut) -and (Test-Path -LiteralPath $DesktopShortcut)) 'shortcuts_created'
    $Shell = New-Object -ComObject WScript.Shell
    Assert-Check ($Shell.CreateShortcut($MenuShortcut).TargetPath -eq (Join-Path $InstallDir 'AppBoletas.exe')) 'shortcut_target'
    $State = Start-Shortcut $MenuShortcut
    Assert-Check ($State.version -eq '0.4.0') 'start_menu_backend_health'
    $Payload = @{rut='99990004-6'; razon_social='EMPRESA DEMO 017 SPA'; nombre_fantasia='RECEPTOR TEST 006'; direccion='DIRECCION PRUEBA 005'; region=13; comuna='Santiago'} | ConvertTo-Json
    Invoke-RestMethod -Uri ($State.url + 'clients') -Method Post -ContentType 'application/json' -Body $Payload | Out-Null
    Assert-Check ((Test-Path -LiteralPath (Join-Path $DataDir 'data\appboletas.db'))) 'persistent_sqlite_created'
    $Second = Start-Process -FilePath (Join-Path $InstallDir 'AppBoletas.exe') -ArgumentList '--headless-launcher' -PassThru -Wait -WindowStyle Hidden
    Assert-Check ($Second.ExitCode -ne 0) 'second_instance_blocked'
    Assert-Check ((Run-Setup) -ne 0) 'setup_with_app_open_blocked'
    $Uninstaller = Join-Path $InstallDir 'unins000.exe'
    $BlockedUninstall = Start-Process -FilePath $Uninstaller -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru -WindowStyle Hidden
    Assert-Check ($BlockedUninstall.ExitCode -ne 0) 'uninstall_with_app_open_blocked'
    Check-Client $State
    Close-Owned $State
    $ProfileMarker = Join-Path $DataDir 'sii-browser-profile\installer-test-marker.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ProfileMarker) | Out-Null
    'FICTITIOUS PROFILE; NO COOKIES' | Set-Content -LiteralPath $ProfileMarker
    $Database = Join-Path $DataDir 'data\appboletas.db'
    $DbHash = (Get-FileHash -LiteralPath $Database).Hash
    $ProfileHash = (Get-FileHash -LiteralPath $ProfileMarker).Hash
    Assert-Check ((Run-Setup) -eq 0) 'in_place_reinstall_update_mechanism'
    Assert-Check ((Get-FileHash -LiteralPath $Database).Hash -eq $DbHash) 'update_preserves_sqlite_bytes'
    Assert-Check ((Get-FileHash -LiteralPath $ProfileMarker).Hash -eq $ProfileHash) 'update_preserves_profile'
    $State = Start-Shortcut $DesktopShortcut
    Check-Client $State
    Close-Owned $State
    $BrowserCheck = Start-Process -FilePath (Join-Path $InstallDir 'AppBoletas.exe') -ArgumentList '--self-test-browser' -Wait -PassThru -WindowStyle Hidden
    Assert-Check ($BrowserCheck.ExitCode -eq 0) 'bundled_chromium_offline'
    $DbHash = (Get-FileHash -LiteralPath $Database).Hash
    $Removed = Start-Process -FilePath $Uninstaller -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru -WindowStyle Hidden
    Assert-Check ($Removed.ExitCode -eq 0) 'uninstall'
    Assert-Check (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'AppBoletas.exe'))) 'executables_removed'
    Assert-Check (-not (Test-Path -LiteralPath (Join-Path $InstallDir '_internal'))) 'dependencies_removed'
    Assert-Check (-not (Test-Path -LiteralPath $Registry)) 'installed_app_entry_removed'
    Assert-Check (-not (Test-Path -LiteralPath $MenuShortcut) -and -not (Test-Path -LiteralPath $DesktopShortcut)) 'shortcuts_removed'
    Assert-Check ((Get-FileHash -LiteralPath $Database).Hash -eq $DbHash) 'uninstall_preserves_sqlite_bytes'
    Assert-Check ((Get-FileHash -LiteralPath $ProfileMarker).Hash -eq $ProfileHash) 'uninstall_preserves_profile'
    Assert-Check ((Run-Setup) -eq 0) 'reinstall'
    $State = Start-Shortcut $MenuShortcut
    Check-Client $State
    Close-Owned $State
} finally {
    foreach ($Name in $PreviousEnv.Keys) { [Environment]::SetEnvironmentVariable($Name, $PreviousEnv[$Name], 'Process') }
    @{checks=$Checks; clean_windows_test='PENDING'; cross_version_update='NOT_TESTED'; nonempty_history='NOT_TESTED'; sii_navigation=$false; real_db_modified=$false} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReportDir 'installer-test-report.json')
    Write-Host "Isolated test report: $ReportDir"
}
