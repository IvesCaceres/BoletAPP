# Windows portable POC

Esta POC demuestra una distribución local de AppBoletas como carpeta portable.
Todavía no crea un instalador Windows.

## Construcción

La construcción se realiza en Windows desde la raíz del repositorio:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

El script compila el frontend, prepara el navegador administrado por
Playwright y genera una distribución PyInstaller `onedir` en:

```text
dist\windows\AppBoletas\
```

Para ejecutar una construcción usando un navegador ya preparado localmente,
se puede usar `-SkipBrowserInstall`.

## Ejecución

Ejecutar `dist\windows\AppBoletas\AppBoletas.exe`. La POC inicia FastAPI en
`127.0.0.1` usando un puerto local disponible y abre la interfaz compilada en
el navegador predeterminado. El usuario final no necesita Python, Node, npm ni
uvicorn instalados.

El ejecutable usa una carpeta local del usuario:

```text
%LOCALAPPDATA%\AppBoletas\
  data\appboletas.db
  sii-browser-profile\
  logs\
  debug\
```

La base de datos y el perfil del navegador no se almacenan dentro de la
carpeta instalada ni se incluyen en el bundle. Las migraciones se aplican
automáticamente al iniciar una instalación limpia. El navegador Chromium de
Playwright se incluye en la distribución generada; no se requiere ejecutar
`playwright install` manualmente.

## Alcance

Esta POC no inicia sesión en SII ni emite BHE. La validación de emisión real
queda para una fase posterior. El launcher enlaza únicamente a localhost y
no requiere un archivo `.env` para el arranque normal.

La distribución actual es una carpeta portable para validar la viabilidad del
runtime. El instalador, firma y actualización automática se abordarán después.
