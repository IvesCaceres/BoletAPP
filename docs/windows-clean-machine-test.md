# Prueba en una máquina Windows limpia

Este archivo describe una prueba de la POC portable. No es una release
estable ni un instalador.

## Preparación

1. Copia `AppBoletas-Windows-POC.zip` a un PC Windows de prueba.
2. Descomprímelo en una carpeta normal, por ejemplo el Escritorio.
3. Idealmente verifica que el PC no tenga Python ni Node instalados. No son
   requisitos de ejecución.
4. Si Windows SmartScreen muestra una advertencia, regístrala como resultado
   de la prueba; el ejecutable todavía no está firmado digitalmente.

## Primera ejecución

1. Abre la carpeta `AppBoletas`.
2. Ejecuta `AppBoletas.exe` con doble clic.
3. Comprueba que se abre el navegador con la interfaz de AppBoletas.
4. Comprueba la navegación básica entre Nueva boleta, Emisión mensual,
   Clientes e Historial.
5. Crea un cliente ficticio usando datos de prueba, nunca datos reales.
6. Cierra la ventana/proceso de AppBoletas según el mecanismo disponible.

La aplicación guarda sus datos en `%LOCALAPPDATA%\AppBoletas`, fuera de la
carpeta descomprimida. La primera ejecución debe crear allí la base SQLite y
aplicar sus migraciones automáticamente.

## Persistencia

1. Ejecuta nuevamente `AppBoletas.exe`.
2. Comprueba que el cliente ficticio continúa presente.
3. Comprueba que la aplicación no requiere terminal, Python, Node, npm,
   uvicorn, Git ni un archivo `.env`.

## Playwright sin SII

La POC incluye el navegador administrado por Playwright. Si existe una
herramienta de diagnóstico local, prueba únicamente abrir una página local o
`about:blank` y cerrarla. No navegues a `sii.cl`, no inicies sesión y no
emitas BHE durante esta prueba.

## Resultado a registrar

- Inicio con doble clic.
- Advertencia de SmartScreen.
- Carga de la interfaz.
- Creación automática de DB y migraciones.
- Persistencia del cliente ficticio.
- Ausencia de requisitos Python/Node/.env.
- Apertura local de Chromium mediante Playwright.
- Comportamiento al cerrar la interfaz y al iniciar una segunda instancia.
