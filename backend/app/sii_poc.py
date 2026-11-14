"""Entry point del POC SII: ``python -m app.sii_poc``."""
import inspect
import os

from app.services.sii_browser_service import (
    SiiBrowserService,
    SiiNavigationError,
    SII_BROWSER_SERVICE_VERSION,
    SiiPocSettings,
    load_sii_poc_env,
)


def main() -> int:
    try:
        print(f"SII_BROWSER_SERVICE_FILE={inspect.getfile(SiiBrowserService)}")
        print(f"SII_BROWSER_SERVICE_VERSION={SII_BROWSER_SERVICE_VERSION}")
        env_loaded = load_sii_poc_env()
        if env_loaded:
            print("Loaded local SII POC configuration from backend/.env.sii-poc")
        settings = SiiPocSettings.from_environment()
        print(
            "SII_POC_PAUSE_BEFORE_CONFIRM_RAW_PRESENT="
            f"{'YES' if 'SII_POC_PAUSE_BEFORE_CONFIRM' in os.environ else 'NO'}"
        )
        print(
            "SII_POC_PAUSE_BEFORE_CONFIRM_ENABLED="
            f"{'YES' if settings.pause_before_confirm else 'NO'}"
        )
        reached = SiiBrowserService(settings).run()
    except SiiNavigationError as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # pragma: no cover - protección del CLI interactivo
        print(f"SII POC failed: {exc}")
        return 1
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
