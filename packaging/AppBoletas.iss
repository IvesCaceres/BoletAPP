; Stable identity: never change AppId for future releases.
#ifndef AppVersion
  #error AppVersion must be provided by build_installer_windows.ps1
#endif
#ifndef PayloadDir
  #error PayloadDir must identify the inspected PyInstaller onedir payload
#endif
#ifndef ReleaseDir
  #error ReleaseDir must be provided
#endif

[Setup]
AppId={{9EB849EA-4560-4554-8688-C4FE108FD3C9}
AppName=AppBoletas
AppVersion={#AppVersion}
AppPublisher=AppBoletas
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=AppBoletas Windows Setup
DefaultDirName={localappdata}\Programs\AppBoletas
DefaultGroupName=AppBoletas
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename=AppBoletas-Setup-{#AppVersion}-windows-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayIcon={app}\AppBoletas.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\AppBoletas.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\LEEME.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\AppBoletas\AppBoletas"; Filename: "{app}\AppBoletas.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\AppBoletas"; Filename: "{app}\AppBoletas.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AppBoletas.exe"; Description: "Abrir AppBoletas"; Flags: nowait postinstall skipifsilent; BeforeInstall: ReleaseMaintenanceLock

[Code]
var
  MaintenanceLock: TFileStream;

procedure ReleaseMaintenanceLock;
begin
  if MaintenanceLock <> nil then begin
    MaintenanceLock.Free;
    MaintenanceLock := nil;
  end;
end;

function AcquireMaintenanceLock: Boolean;
var
  DataRoot, LockPath: String;
begin
  Result := False;
  DataRoot := GetEnv('APPBOLETAS_USER_DATA_DIR');
  if DataRoot = '' then
    DataRoot := ExpandConstant('{localappdata}\AppBoletas');
  { Custom runtime overrides must be absolute, so shortcuts and Setup agree. }
  if (Length(DataRoot) < 3) or
     ((Copy(DataRoot, 2, 2) <> ':\') and (Copy(DataRoot, 1, 2) <> '\\')) then begin
    SuppressibleMsgBox('El directorio de datos debe ser una ruta absoluta.', mbError, MB_OK, IDOK);
    Exit;
  end;
  DataRoot := ExpandFileName(DataRoot);
  LockPath := AddBackslash(DataRoot) + 'instance.lock';
  try
    if not DirExists(DataRoot) then
      if not ForceDirectories(DataRoot) then
        RaiseException('No se pudo acceder al directorio de datos.');
    { fmOpenReadWrite=$0002; fmShareExclusive=$0010; fmCreate=$FFFF.
      Held throughout maintenance: also prevents a new launcher racing Setup.
      Compatible with the OS handle already held by portable 0.2.0.
      Never opens, rewrites or deletes SQLite, profile or personal settings. }
    if FileExists(LockPath) then
      MaintenanceLock := TFileStream.Create(LockPath, $0012)
    else
      MaintenanceLock := TFileStream.Create(LockPath, $FFFF);
    Result := True;
  except
    Log('Maintenance blocked: application active or data directory inaccessible.');
    SuppressibleMsgBox(
      'Cierre AppBoletas desde su ventana de control y vuelva a ejecutar este instalador o desinstalador. ' +
      'No se cerrara la aplicacion por la fuerza. Si ya esta cerrada, revise los permisos del directorio de datos.',
      mbError, MB_OK, IDOK);
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := AcquireMaintenanceLock;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if MaintenanceLock = nil then
    Result := 'No se obtuvo el bloqueo de mantenimiento. No se reemplazaran archivos.';
end;

procedure DeinitializeSetup;
begin
  ReleaseMaintenanceLock;
end;

function InitializeUninstall: Boolean;
begin
  Result := AcquireMaintenanceLock;
end;

procedure DeinitializeUninstall;
begin
  ReleaseMaintenanceLock;
end;

{ Intentionally NO UninstallDelete/InstallDelete, data removal, taskkill,
  service, PATH, firewall, elevation, credentials or automatic updates. }
