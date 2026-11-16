# Backend de BoletAPP

El backend local está implementado con FastAPI y SQLite. Sirve la API de BoletAPP, ejecuta las validaciones de negocio y coordina los flujos de emisión y lotes.

Para la descripción del producto, arquitectura, versión de escritorio y pruebas del release, consulta el [README principal](../README.md).

## Desarrollo local

Desde esta carpeta, con el entorno virtual del proyecto activado:

```powershell
pip install -r requirements.txt
$env:DATABASE_URL = "sqlite:///./appboletas-dev.db"
python -m app.local
```

`app.local` aplica las migraciones al iniciar y ejecuta el backend local en `127.0.0.1:8001`. En desarrollo, ejecuta Vite desde `frontend` en otra terminal. El ejemplo selecciona una base desechable dentro de `backend`; no lo sustituyas por una URL de datos reales para hacer pruebas.

## Pruebas

```powershell
pytest
```

Las pruebas del backend usan bases temporales y no deben contactar al SII.
