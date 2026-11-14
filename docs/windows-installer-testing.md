# Validación aislada del instalador

No ejecutar instalación/desinstalación de prueba en el usuario del desarrollador.
No apuntar nunca las pruebas a la DB del repositorio ni al directorio real de
datos del usuario. No cerrar el backend de desarrollo.

Preparar una VM Windows x64 desechable o Windows Sandbox **ya habilitado**, con
red deshabilitada. Habilitar Sandbox o instalar un hipervisor requiere autorización;
los scripts de AppBoletas no lo hacen. Copiar sólo el Setup candidato y los scripts
de prueba. No mapear perfiles, DB, backups ni el directorio personal del host.

En la VM crear `C:\AppBoletasInstallerTestEnvironment.marker`, sólo después de
comprobar que es desechable. La automatización se niega a continuar sin el marcador
y `-ConfirmDisposableVM`, o si ya existe una instalación/directorio de datos.

```powershell
.\test_windows_installer.ps1 `
  -Installer .\AppBoletas-Setup-0.4.0-windows-x64.exe `
  -ConfirmDisposableVM
```

El script no instala Python/Node/Playwright, no abre SII y sólo usa datos ficticios.
Comprueba instalación HKCU, acceso Inicio/escritorio, backend propio, SQLite,
segunda instancia, bloqueo con app abierta, actualización in-place con el mismo
Setup, desinstalación, conservación de datos y reinstalación. Esa actualización
prueba el mecanismo de reemplazo y AppId, **no** una migración desde otra versión.

Pruebas manuales adicionales obligatorias antes de declarar el instalador final:

| Prueba | Evidencia necesaria |
| --- | --- |
| Usuario Windows estándar sin herramientas de desarrollo | Instalación sin UAC y arranque correcto |
| Primera instalación interactiva | Español/inglés, escritorio opcional, versión/editor, Abrir AppBoletas |
| App abierta al instalar y desinstalar | Mensaje de cierre; nunca taskkill ni eliminación parcial |
| Actualización desde otro release con AppId estable | DB/historial/clientes/perfil preservados |
| Portable 0.2.0 abierto usando el mismo directorio de datos | Setup bloqueado por instance.lock |
| Historial no vacío ficticio | Folios/estados/listado iguales antes y después |
| Frontend y Chromium | Usar smoke test del EXE y revisar interfaz sin emitir |
| Desinstalar desde Aplicaciones instaladas | Ejecutables/accesos/registro quitados, datos intactos |
| Reinstalación | Datos originales disponibles |
| SmartScreen sin firma | Advertencia documentada; no prometer ausencia de aviso |

El reporte automático nunca sustituye estas comprobaciones ni una prueba en un
Windows limpio. Si falta VM/compiler/intervención, marcar PENDING/NOT_TESTED.
