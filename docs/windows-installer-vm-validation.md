# AppBoletas 0.4.0 — Validación del instalador en Windows aislado

Fecha de auditoría: 2026-10-13, America/Santiago.

**Resultado: BLOQUEADO / VALIDACIÓN INCOMPLETA. El release no está listo.**

## Continuación autorizada: instalación de Oracle VirtualBox

Actualización: 2026-10-13, 19:40 America/Santiago. El usuario autorizó instalar
VirtualBox y sus controladores oficiales con UAC, sin reiniciar ni modificar
protecciones, y continuar solo si existen recursos suficientes para la VM.

### Procedencia, firma e integridad — PASS

- La [página oficial de descargas de Oracle](https://www.oracle.com/virtualization/technologies/vm/downloads/virtualbox-downloads.html)
  identifica **VirtualBox 7.2.20** y enlaza el instalador Windows del dominio oficial.
- Descarga realizada exclusivamente desde
  `https://download.virtualbox.org/virtualbox/7.2.20/VirtualBox-7.2.20-175154-Win.exe`.
- Checksum oficial obtenido desde
  `https://download.virtualbox.org/virtualbox/7.2.20/SHA256SUMS`.
- Archivo local, ignorado por Git:
  `build/tools/virtualbox-7.2.20/VirtualBox-7.2.20-175154-Win.exe`.
- Tamaño: **999900090 bytes**.
- SHA-256 calculado y comparado con Oracle: **PASS**,
  `a81777d2b36380ce042a29e9c554cf032eb46a793f62e3cc82e7411e535c2c26`.
- Authenticode: **Valid**, mensaje «Firma comprobada».
- Firmante: **Oracle America, Inc.**, Redwood City, California, US.
- Thumbprint del certificado: `2B1BD188256EB612578949A943DE0DDC52D1DF79`.
- ProductVersion del archivo: `7.2.20.175154`.
- Visual C++ x64 instalado: `v14.51.36247.00`; no se instaló otro runtime.

### Estado de instalación — BLOCKED_UAC_CANCELLED

Se solicitó la extracción oficial del paquete ya verificado mediante
`-extract -silent -path`, a un subdirectorio dedicado de `build/tools/virtualbox-7.2.20`.
Windows abrió una solicitud UAC que requería confirmación manual del usuario en el
escritorio seguro. Se observó `consent.exe` PID 19152. No se automatizó su aceptación.
La solicitud terminó y `Start-Process` devolvió **«El usuario ha cancelado la operación»**.
Esto demuestra que la elevación no se completó; no permite atribuir la cancelación
a una decisión deliberada del usuario frente a expiración u otra condición del UAC.
No existía `C:\Program Files\Oracle\VirtualBox\VBoxManage.exe` ni había MSI extraído
disponible. **VirtualBox no quedó instalado y la extracción no se ejecutó.**

Se informó al usuario que solo debe aceptar si el editor mostrado es
Oracle America, Inc. No se volvió a solicitar UAC automáticamente tras la
cancelación. La operación solicitada era solo extracción: no instala AppBoletas
ni inicia una VM. No se invocó `msiexec` para instalar VirtualBox.

Después de completar la extracción se deberá comprobar el MSI oficial y su firma
antes de instalar. Plan de instalación mínima, aún no ejecutado:

- `ADDLOCAL=VBoxApplication`: núcleo y controladores mínimos oficiales.
- Sin VBoxUSB, VBoxPython, VBoxNetworkFlt ni VBoxNetworkAdp: no necesitamos USB,
  integración Python, bridge ni host-only para pruebas offline o NAT básico.
- Suprimir reinicio mediante `/norestart` y `REBOOT=ReallySuppress`; si el resultado
  indica reinicio necesario, detenerse y solicitar autorización, no reiniciar.
- No iniciar VirtualBox automáticamente ni instalar Extension Pack.
- Verificar posteriormente `VBoxManage --version`, servicio/controlador y
  enumeración del host sin crear/iniciar un invitado bajo falta de recursos.

Opciones documentadas por
[Oracle: instalación Windows y ADDLOCAL](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html).
No se aplicaron cambios de seguridad, activación de características ni cambios BIOS.

### Recursos actuales y decisión de VM — BLOCK

| Medición | Resultado |
| --- | --- |
| RAM libre al comenzar esta continuación | 1229140 KiB, aproximadamente 1,17 GiB |
| RAM libre a las 19:40:48 | 2090428 KiB, aproximadamente 1,99 GiB |
| Disco C: libre a las 19:40:48 | 57056411648 bytes, aproximadamente 53,14 GiB |
| CPU física | 8 núcleos / 12 lógicos, sin cambios |
| Configuración autorizada del invitado | 2 vCPU, hasta 4 GiB RAM, disco dinámico |
| RAM asignada / VM creada / VM iniciada | Ninguna / NO / NO |

Una VM Windows 11 con 4 GiB no cabe en la memoria libre medida dejando margen al
host. No reducir su memoria a un valor insuficiente para fingir una validación
representativa. Se requiere liberar RAM de aplicaciones prescindibles, sin detener
PID 19708 ni cerrar procesos ajenos automáticamente. Como margen operativo inicial
propuesto, volver a comprobar al menos 6 GiB libres antes de asignar 4 GiB al invitado;
este margen es una decisión conservadora de prueba, no un requisito oficial de Oracle.

El disco dinámico no reserva toda su capacidad al crearlo: además de escoger una
capacidad suficiente para Windows, habrá que verificar espacio real para ISO,
instalación, ambos paquetes AppBoletas, evidencias y eventual snapshot antes de
crear la VM. No se descargó ISO, no se creó disco ni se registró ninguna VM.

### Resultado de esta continuación

| Comprobación | Estado |
| --- | --- |
| Fuente oficial y hash de VirtualBox | PASS |
| Firma Oracle del instalador | PASS |
| Runtime Visual C++ ya existente | PASS |
| Extracción / aprobación UAC | SKIP — elevación cancelada; extracción no iniciada |
| Instalación VirtualBox / VBoxManage funcional | SKIP — pendiente UAC e instalación |
| Creación de invitado | SKIP — RAM insuficiente |
| Instalación / actualización / bloqueo / desinstalación AppBoletas | SKIP — sin invitado |
| Conservación y recuperación de datos ficticios | SKIP — no ejecutadas |

Backend PID **19708** permanece activo con la misma hora de inicio. No se detuvo
ni reinició. Sin instalación de AppBoletas en el host, acceso a DB real, emisiones,
retries, cambios funcionales, commits, push ni publicación.

**Acciones pendientes:** retomar cuando el usuario esté disponible para aceptar
el UAC de Oracle, completar la extracción e instalación verificadas sin reinicio,
y liberar memoria antes de preparar el invitado. Las pruebas críticas y el Windows
limpio continúan pendientes;
**RELEASE_READY=NO**.

---

## Auditoría inicial anterior a la autorización de VirtualBox

No se creó ni inició un invitado Windows. No se ejecutó el instalador ni el
desinstalador de AppBoletas en el computador principal. Todos los escenarios
críticos de instalación, actualización y conservación de datos siguen pendientes.

## Identificación del candidato

- Repositorio: `%USERPROFILE%\Developer\AppBoletas`.
- Rama: `feature/windows-packaging-poc`.
- HEAD: `9ca053964f3c80bae6a558448f5a238eeac22c6c`.
- Instalador: `dist/release/candidate/AppBoletas-Setup-0.4.0-windows-x64.exe`.
- Tamaño comprobado: **999900140 bytes**.
- SHA-256 calculado físicamente en esta auditoría:
  `ac19b0ff3cd01cfd650d949019d0e2b1a16b5b489030575cef0ab4a4bea93dc5`.
- Comparación con el SHA-256 autorizado: **PASS**.
- `installer_manifest.json`: versión 0.4.0, candidato, `source_dirty=true`,
  compilador Inno Setup 7.1.0, instalación por usuario, sin firma digital.
- El manifiesto registra `sensitive_file_scan=PASS`; ese resultado corresponde a
  la preparación previa del candidato, no a una nueva extracción del instalador.
- El worktree ya tenía modificaciones y archivos nuevos de packaging. Se
  conservaron. No se creó commit ni se hizo push.

## Detección del computador principal

| Dato | Evidencia local |
| --- | --- |
| Edición | Microsoft Windows 11 Home Single Language; `EditionID=CoreSingleLanguage` |
| Versión | 25H2; 10.0.26200; build 99.990.0120 |
| Arquitectura | x64 |
| CPU | Intel Core i5-12450H; 8 núcleos, 12 procesadores lógicos |
| RAM física | Aproximadamente 16 GB; 16831897600 bytes reportados por CIM |
| RAM libre en la medición | 2401044 KiB, aproximadamente 2,29 GiB |
| Disco C: libre en la medición | 57824190464 bytes, aproximadamente 53,85 GiB |
| Hipervisor detectado | `Win32_ComputerSystem.HypervisorPresent=true` |
| Capacidades de CPU por CIM | `VirtualizationFirmwareEnabled=false`, SLAT y VMMonitorModeExtensions también false |
| Windows Sandbox | Ejecutable, comando y paquete Appx no encontrados |
| Hyper-V administrable | Sin `vmconnect.exe`, comando `Get-VM`, módulo Hyper-V ni servicio `vmms` detectados |
| Servicio de cómputo | `vmcompute` existe, detenido, inicio manual |
| VirtualBox | Sin comando `VBoxManage.exe`, ejecutable en la ruta habitual ni registro de instalación detectados |
| VMware | Sin comando `vmrun.exe`, ejecutable en rutas habituales ni registro de instalación detectados |
| QEMU | Comando `qemu-system-x86_64.exe` no encontrado |

Las búsquedas de software incluyeron PATH, ubicaciones habituales y entradas de
desinstalación HKLM/HKCU. No constituyen un inventario recursivo de todos los
archivos del equipo ni descartan un ejecutable portable en una ubicación desconocida.

**Virtualización:** hay un hipervisor activo, pero no se pudo demostrar que exista
una plataforma disponible para crear este invitado. Las propiedades false del
procesador no bastan para concluir que la virtualización esté deshabilitada en
BIOS cuando hay un hipervisor presente. No se modificó BIOS/UEFI ni se solicitó
reiniciar el equipo.

Las consultas de solo lectura `Get-WindowsOptionalFeature -Online` para Sandbox,
Hyper-V, VirtualMachinePlatform e HypervisorPlatform requirieron elevación. No se
elevó el proceso, no se activaron características y sus estados exactos quedan
sin verificar por esa vía.

## Alternativa y bloqueo

No hay un entorno Windows aislado utilizable detectado bajo la autorización
actual. Windows Sandbox no está soportado en Windows Home y el rol Hyper-V
tampoco puede instalarse en esa edición:

- [Windows Sandbox: ediciones admitidas](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/).
- [Hyper-V: requisitos y limitación de Windows Home](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v).

No se intentaron soluciones no oficiales para habilitarlos en Home.

La alternativa propuesta para este host es **Oracle VirtualBox desde su fuente
oficial**, previa autorización específica para su instalación con elevación y
controladores. No está instalado y no se descargó ni ejecutó su instalador.
Debe verificarse su firma digital y la compatibilidad con el hipervisor activo;
si requiere desactivar protecciones, cambiar características del sistema o
reiniciar, detenerse y solicitar autorización adicional, no hacerlo automáticamente.

Documentación oficial:
[Instalación de VirtualBox](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html).

Una VM Windows ya preparada en otro equipo compatible también permite evitar
cambios del sistema principal. No se borraron ni modificaron VMs preexistentes.

### Invitado, recursos e imagen

- Tipo de virtualización utilizado: **NINGUNO**.
- Versión de Windows invitado: **NO CREADO / NO VERIFICADO**.
- Recursos asignados: **NINGUNO**.
- Propuesta inicial, no aplicada: Windows 11 x64, 2–4 vCPU, 4–6 GiB de RAM,
  disco virtual dinámico de al menos 64 GiB, con TPM/UEFI según requisitos del
  Windows elegido. Verificar espacio real disponible y dejar margen para el host.
- Con solo 2,29 GiB de RAM libre no conviene iniciar esa VM ahora. El usuario
  deberá liberar RAM cerrando aplicaciones prescindibles; no cerrar el backend
  ni procesos ajenos automáticamente.
- Procedencia de imagen: **NO DESCARGADA**. Usar únicamente una ISO oficial de
  Microsoft y verificar su hash publicado para la versión descargada. La
  evaluación puede requerir registro y aceptación de licencia por el usuario:
  [Windows 11 Enterprise Evaluation](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise).
- No utilizar Wine, Linux, WSL ni contenedores como sustitutos del invitado Windows.

## Resultados de esta ejecución

`SKIP` significa no ejecutado por falta de entorno, **no aprobado**.

| Escenario | Estado | Evidencia / pendiente |
| --- | --- | --- |
| Integridad del instalador candidato | PASS | Hash y tamaño físicos coinciden |
| Detección de entorno y recursos | PASS | Consultas CIM, rutas, comandos, servicios y registro |
| Sintaxis del script de pruebas existente | PASS | Parser PowerShell: 0 errores; no se ejecutó el script |
| 1. Instalación limpia por usuario | SKIP | No existe VM disponible |
| 2. Accesos directos Inicio/escritorio | SKIP | Requiere instalación aislada |
| 3. Apertura y cierre | SKIP | Requiere EXE instalado dentro del invitado |
| 4. Frontend, backend y Chromium básico | SKIP | No se ejecutó el EXE del candidato en invitado |
| 5. Instalación de 0.2.0 | SKIP | Disponible como portable; no se encontró Setup 0.2.0 |
| 6. Registros ficticios en 0.2.0 | SKIP | Requiere cuenta Windows de prueba aislada |
| 7. Transición 0.2.0 → 0.4.0 | SKIP | Probar migración desde portable; no afirmar actualización entre dos instaladores |
| 8. Conservación de datos/configuración | SKIP | Sin base ficticia ni comparación antes/después |
| 9. Actualización con aplicación abierta bloqueada | SKIP | Sin proceso de prueba en invitado |
| 10. Desinstalación | SKIP | Prohibida en el host; no hay invitado |
| 11. Datos conservados tras desinstalar | SKIP | Comparar hashes y registros de fixture en VM |
| 12. Reinstalación 0.4.0 | SKIP | Requiere desinstalación previa aislada |
| 13. Recuperación de registros | SKIP | Verificar mismos identificadores, folios/estados ficticios y configuración |

No hay evidencia nueva de conservación de datos. La configuración de Inno evita
`UninstallDelete` y conserva el directorio externo del usuario por diseño, pero
**esto no sustituye una prueba ejecutada de actualización/desinstalación**.

## Cobertura y límites de la automatización existente

Se leyó `scripts/test_windows_installer.ps1`, sin ejecutarlo. Rechaza el host si
no coincide el modelo de VM, falta `C:\AppBoletasInstallerTestEnvironment.marker`
o no se pasa `-ConfirmDisposableVM`. También rechaza cuentas con una instalación,
datos o accesos directos existentes.

El script cubre instalación 0.4.0, Inicio/escritorio, salud de backend propio,
cliente ficticio, segunda instancia, bloqueo de instalación/desinstalación con
app abierta, reinstalación in-place **de la misma versión**, hash de SQLite y
marcador de perfil ficticio, Chromium offline, desinstalación y recuperación del
cliente tras reinstalar. Su historial fixture está vacío.

No demuestra transición desde 0.2.0 ni conservación de historial no vacío.
No debe ejecutarse después de preparar datos 0.2.0 en la misma cuenta porque su
guard de cuenta limpia lo rechazaría. Usar dos snapshots/cuentas de prueba:

1. Cuenta/snapshot A completamente limpio para el script existente.
2. Cuenta/snapshot B independiente para la transición desde el portable 0.2.0,
   historial ficticio y configuración, con una automatización específica que
   todavía debe prepararse y validarse una vez disponible la VM.

El portable original `dist/release/portable/AppBoletas` se inspeccionó solo como
archivos; no se abrió ni modificó. El manifiesto original indica versión 0.2.0.
No se generó retroactivamente un instalador 0.2.0.

## Pasos mínimos para desbloquear y continuar

1. Autorizar específicamente la instalación oficial de VirtualBox con elevación
   y sus controladores, o proporcionar una VM Windows desechable ya disponible
   en otro equipo. No activar Hyper-V/Sandbox no soportados en Home.
2. Liberar memoria y comprobar espacio suficiente sin interrumpir el backend.
3. Obtener ISO oficial de Microsoft, verificar hash/licencia, crear un invitado
   nuevo y un usuario estándar de prueba. No acceder a cuentas tributarias.
4. Tras preparar Windows, tomar snapshot limpio y desconectar la red virtual
   antes de probar AppBoletas; no cambiar el firewall del host.
5. Transferir únicamente el candidato verificado, scripts inspeccionados y copia
   del portable 0.2.0. No compartir repositorio completo, perfil personal,
   `%LOCALAPPDATA%\AppBoletas`, bases reales, CSV, cookies ni logs del desarrollador.
6. Si se usan carpetas compartidas, dedicar una entrada sin datos reales de solo
   lectura y una salida vacía exclusiva para evidencia ficticia. Guardar informes
   fuera de la VM antes de revertir/cerrar el entorno efímero.
7. Dentro del invitado limpio y comprobado como desechable, crear el marcador y
   ejecutar el script existente. **Estos comandos no se ejecutaron aquí y no son
   instrucciones para el host principal:**

   ```powershell
   New-Item -ItemType File -Path C:\AppBoletasInstallerTestEnvironment.marker
   .\test_windows_installer.ps1 `
     -Installer .\AppBoletas-Setup-0.4.0-windows-x64.exe `
     -ConfirmDisposableVM
   ```

8. En el snapshot/cuenta B, ejecutar la copia del portable 0.2.0 contra el
   directorio persistente del invitado, crear fixtures de clientes e historial
   por mecanismos locales sin proveedor SII, y cerrarlo correctamente. Guardar
   hashes con SQLite cerrado, registros esperados y marcadores de configuración.
9. Probar primero el bloqueo del Setup 0.4.0 con el portable de esa cuenta abierto;
   después cerrarlo y probar instalación, recuperación, desinstalación y
   reinstalación. No convertir ese escenario en una supuesta actualización de un
   Setup 0.2.0 inexistente.
10. Exportar reportes, hashes y logs de prueba ficticios a una carpeta dedicada
    ignorada por Git. Actualizar este documento con evidencia de cada PASS/FAIL.

## Salvaguardas y recomendación

- Backend PID **19708** confirmado activo: `C:\Python\python.exe`, escucha en
  `127.0.0.1:8001`. No se detuvo, reinició ni se le enviaron solicitudes de prueba.
- No se abrió ni modificó la base de datos real ni el perfil SII personal.
- No hubo navegación SII, emisión, retry, instalación/desinstalación de AppBoletas
  en el host, activación de características, reinicio, descarga de imágenes o
  instalación de hipervisor.
- No se modificó antivirus, firewall, BIOS/UEFI ni configuración de seguridad.
- Código funcional `backend/app`, `frontend/src` y `backend/alembic` sin diferencias
  frente a HEAD en esta auditoría. El único archivo agregado por esta tarea es
  este informe; los cambios de packaging anteriores permanecen intactos.
- Sin commit, push ni publicación de artefactos.

**CLEAN_WINDOWS_TEST=PENDING**

**INSTALLER_CRITICAL_TESTS=SKIP**

**UPDATE_PRESERVES_DATA=NOT_TESTED**

**RELEASE_READY=NO**

Mantener el instalador como candidato privado. No declarar completa la validación
hasta aprobar instalación, transición desde 0.2.0, bloqueos con app abierta,
desinstalación y conservación/recuperación de datos en Windows aislado.
