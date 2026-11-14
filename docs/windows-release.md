# AppBoletas 0.4.0 para Windows x64

## Estado y distribución

La versión funcional anterior es AppBoletas 0.2.0 portable. La infraestructura de
0.4.0 añade un instalador Inno Setup sobre el mismo PyInstaller **onedir**, sin
cambios al motor tributario. Un script preparado no demuestra que el instalador
se haya compilado o probado: consultar `installer_manifest.json` y el reporte de
pruebas de cada entrega. No distribuir un candidato como release validado.

El archivo previsto es `AppBoletas-Setup-0.4.0-windows-x64.exe`. El editor mostrado
es AppBoletas (nombre del proyecto), no una afirmación de firma digital.

## Instalación y primer inicio

Ejecutar el Setup por doble clic como usuario normal. Instala en
`%LOCALAPPDATA%\Programs\AppBoletas`, registra la aplicación en Aplicaciones
instaladas y crea el acceso del menú Inicio. El escritorio es opcional.
No requiere instalar Python, Node, FastAPI ni Playwright; Chromium está incluido.

No ejecutar Setup como administrador ni como otro usuario: la instalación y los
datos pertenecen al usuario que lo ejecuta. La aplicación sólo escucha en
`127.0.0.1`; verifica su propio backend antes de abrir el navegador y cambia de
puerto si 8001 está ocupado. La interfaz utiliza el mismo origen.

Mantener abierta la ventana pequeña de AppBoletas. Cerrarla detiene el servidor
cuando no hay una operación SII activa; cerrar sólo la pestaña no lo detiene.
La segunda instancia está bloqueada antes de acceder a SQLite o al perfil.

## Datos persistentes: no están dentro de la instalación

| Contenido | Ubicación |
| --- | --- |
| SQLite: clientes e historial | `%LOCALAPPDATA%\AppBoletas\data\appboletas.db` |
| Perfil y sesiones SII | `%LOCALAPPDATA%\AppBoletas\sii-browser-profile\` |
| Logs | `%LOCALAPPDATA%\AppBoletas\logs\` |
| Diagnósticos sensibles locales | `%LOCALAPPDATA%\AppBoletas\debug\` |
| Respaldos manuales | Ubicación privada elegida por el usuario, fuera de la instalación |

El instalador nunca copia datos de desarrollo ni `%LOCALAPPDATA%\AppBoletas` al
paquete. Tampoco incluye CSV, credenciales, cookies, `.env` o capturas tributarias.
Si se usa el override avanzado `APPBOLETAS_USER_DATA_DIR`, debe ser absoluto;
Setup y launcher deben recibir el mismo entorno. No cambiarlo al actualizar.

## Actualización manual

1. Hacer un respaldo consistente de los datos (sección siguiente).
2. Cerrar AppBoletas desde su ventana; esperar que termine el proceso.
3. Descargar el nuevo Setup y contrastar su SHA-256 con el manifiesto.
4. Ejecutarlo con el **mismo usuario**. El AppId no debe cambiar entre releases.
5. Abrir y comprobar clientes/historial antes de cualquier emisión.

Setup reutiliza la instalación anterior. El directorio de datos y el perfil son
los mismos de 0.2.0; no crea una segunda base al instalar. No modifica ajustes
personales ni ejecuta migraciones: las migraciones existentes del launcher sólo
corren al iniciar la aplicación. Esta versión incluye las migraciones 0013 y 0014
para lotes manuales; hacer respaldo antes de abrir una instalación actualizada.
Un downgrade después de futuras migraciones requiere revisión específica.

Setup mantiene un acceso exclusivo a `instance.lock` durante el reemplazo para
evitar que la app arranque entre la comprobación y la copia. Si está abierta o
no se puede verificar el bloqueo, se niega a instalar/desinstalar y pide cerrarla.
No mata procesos, no cierra SII a la fuerza y no reinicia automáticamente.
No hay actualizaciones automáticas.

## Desinstalación y reinstalación

Usar Configuración de Windows → Aplicaciones instaladas → AppBoletas → Desinstalar.
Cerrar AppBoletas primero. El desinstalador elimina sólo archivos registrados de
la aplicación, accesos directos y entrada de instalación.

**Desinstalar NO elimina datos tributarios personales.** SQLite, clientes,
historial, perfil SII, cookies y respaldos permanecen en el directorio de datos.
No existe opción automática de borrado de datos personales en esta versión.
Reinstalar con el mismo usuario y directorio de datos recupera la misma información.

## Respaldo consistente y restauración

No hay nueva interfaz de respaldo en esta entrega. La auditoría del código actual
no encontró un mecanismo general integrado de backup SQLite; el importador CSV
usa transacciones, pero no debe tratarse como una herramienta de respaldo. Los
respaldos locales anteriores no se incluyen en el instalador.

Respaldo manual sin instalar herramientas:

1. Cerrar AppBoletas y cualquier otra herramienta que abra esa base.
2. Verificar que el proceso AppBoletas.exe terminó; no copiar durante una emisión.
3. Copiar **todo el directorio de datos** a una carpeta privada fechada. Mantener
   juntos `appboletas.db` y cualquier `-wal`/`-shm` presente; no omitir el WAL.
   Copiar también el perfil si se quiere conservar la sesión local.
4. Mantener el origen cerrado durante toda la copia. Guardar el respaldo cifrado
   o en un medio privado: contiene información tributaria y posibles cookies.

Para respaldo con la aplicación activa, usar una herramienta confiable que invoque
la API de respaldo de SQLite (Online Backup API) o `VACUUM INTO`, no el explorador.
Una copia simple de un SQLite activo no garantiza consistencia.

Restaurar: cerrar todas las instancias, conservar primero una copia del estado
actual y reponer la carpeta respaldada completa, con DB/WAL/SHM del **mismo**
respaldo. No mezclar archivos de momentos diferentes. Abrir la misma versión o
una compatible, comprobar clientes/historial y conservar el respaldo. No asumir
que restaurar archivos permite repetir una BHE ya emitida en SII.

## Problemas y advertencias

Consultar `%LOCALAPPDATA%\AppBoletas\logs\appboletas.log` ante un fallo de inicio.
No publicar DB, perfiles, HTML, capturas ni logs sin revisar su contenido sensible.
Si Setup pide cerrar la aplicación, cerrar su ventana de control, no sólo la pestaña.
Si persiste, revisar procesos y permisos; no borrar instance.lock ni forzar un cierre
durante una emisión. Los ejecutables deben conservar la carpeta `_internal` completa.

La entrega puede estar **sin firma digital**. Windows/SmartScreen puede advertirlo;
no se promete reputación ni ausencia de alertas. Obtener el archivo desde el canal
oficial del proyecto y verificar SHA-256. No desactivar Defender ni añadir exclusiones.

## Compilación reproducible

Inno Setup >= 6.3 debe obtenerse del fabricante: https://jrsoftware.org/isdl.php.
El sitio documenta WinGet y la verificación de su firma. Instalarlo requiere
autorización; los scripts de AppBoletas nunca instalan herramientas ni cambian PATH.
Revisar los términos del fabricante para uso comercial.

```powershell
# Candidato antes de commit: no presentarlo como entrega final.
.\scripts\build_installer_windows.ps1 -Candidate -SkipBrowserInstall

# Release desde un árbol limpio, después de validar y commitear.
.\scripts\build_installer_windows.ps1 -SkipBrowserInstall

# Sólo reutilizar un portable de versión/commit/Playwright coincidentes.
.\scripts\build_installer_windows.ps1 -ReusePortable
```

La versión tiene una sola fuente: `packaging/VERSION`. El build verifica inputs,
Chromium/Playwright, archivos sensibles y procedencia; el manifiesto guarda versión,
commit, tamaño, SHA-256 del Setup e inventario SHA-256 del payload. Un candidato
sucio está marcado explícitamente y sale a `dist/release/candidate/`.
No subir ejecutables, datos ni manifests personales a Git.

Pruebas aisladas: [windows-installer-testing.md](windows-installer-testing.md).
Prueba del EXE: `backend/venv/Scripts/python.exe scripts/test_windows_release.py`.
Windows limpio e instalación/desinstalación deben validarse por separado; el éxito
de PyInstaller o ISCC por sí solo no acredita estas pruebas.

El build del instalador usa `dist/release/portable-0.4.0/`, separado del portable
0.2.0 original. Mientras el árbol tenga cambios sin commit, utilizar `-Candidate`;
el Setup quedará en `dist/release/candidate/`, marcado como candidato no publicado.
