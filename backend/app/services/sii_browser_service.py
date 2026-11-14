"""SII Playwright navigation, validation, and BHE emission automation."""
from __future__ import annotations

import os
import json
import re
import subprocess
import threading
import time
import unicodedata
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import uuid4
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.utils.rut import split_rut
from app.runtime import debug_dir, sii_profile_dir
from app.services.sii_retention_trace import SiiRetentionTrace
from app.services.sii_retention_submission import SiiRetentionSubmission


DEFAULT_SII_HOME_URL = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
DEFAULT_PROFILE_DIR = sii_profile_dir()
DEFAULT_DEBUG_DIR = debug_dir()
DEFAULT_POC_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.sii-poc"
SII_BROWSER_SERVICE_VERSION = "recipient-validation-v2"
SII_NEW_BHE_BUTTON_SELECTOR = "input[name='cmdemitir1']"
SII_NEW_BHE_FAST_PATH_ENABLED = False
SII_BROWSER_EXECUTION_LOCK = threading.Lock()
RETENTION_DIAGNOSTIC_VERSION = "retention-preview-v1"
MAX_RETENTION_CANDIDATES = 20
MAX_RETENTION_TEXT_LENGTH = 300
RETENTION_DIAGNOSTIC_ARTIFACTS_ENV = "SII_DIAGNOSTIC_ARTIFACTS"
RETENTION_DIAGNOSTIC_PREVIEW_ENV = "SII_DIAGNOSTIC_PREVIEW_ENABLED"

ISSUER_RETENTION_PATTERNS = (
    r"(?:contribuyente\s+)?emisor.{0,140}(?:declara|entera|retien|retencion|"
    r"se\s+hace\s+cargo|pago\s+provisional\s+mensual|ppm)",
    r"(?:declara|entera|se\s+hace\s+cargo).{0,100}(?:ppm|pago\s+provisional\s+mensual)",
)
RECEIVER_RETENTION_PATTERNS = (
    r"receptor.{0,140}(?:retien|retencion|pago\s+provisional\s+mensual|ppm)",
    r"contribuyente\s+para\s+el\s+cual\s+(?:esta\s+)?destinada\s+"
    r"esta\s+boleta.{0,100}encargado\s+de\s+retener.{0,80}porcentaje",
)

_SENSITIVE_DIAGNOSTIC_PATTERNS = (
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)(?<!\w)(?:\+?56\s*)?(?:9\s*)?\d{4}[\s.-]?\d{4}(?!\w)"),
    re.compile(r"(?i)(?<!\d)\d{1,2}(?:\.?\d{3}){2}-?[\dkK](?!\w)"),
    re.compile(r"(?<!\w)(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\w)"),
    re.compile(r"(?i)(?<!\w)\d{1,2}\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+\d{4}(?!\w)"),
    re.compile(r"(?<!\d)\d{4,}(?!\d)"),
    re.compile(r"(?i)(?:CLP|UF)?\s*\$\s*[\d.,]+|(?<!\w)\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?(?!\w)"),
    re.compile(
        r"(?i)\b(?:rut|nombre(?:\s+de\s+fantas[ií]a)?|raz[oó]n\s+social|"
        r"domicilio|direcci[oó]n|correo|email|tel[eé]fono|m[oó]vil|emisor|receptor|contribuyente|"
        r"monto|valor|folio|fecha)\b\s*[:=\-]\s*[^|;\n]{1,180}"
    ),
)
_NON_TECHNICAL_ATTR = re.compile(
    r"(?i)(?:rut|nombre|name|razon|direc|address|email|correo|telefono|phone|"
    r"monto|valor|folio|fecha|client|contribuyente|receptor|emisor)"
)


def split_chilean_rut(rut: str) -> tuple[str, str] | None:
    """Normalize and validate a Chilean RUT before touching the SII form."""
    return split_rut(rut)


def parse_env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False
    return default


class SiiNavigationError(RuntimeError):
    """The POC could not reach the expected SII state."""


class SiiValidationError(SiiNavigationError):
    """A safe form/preview validation stopped the SII flow."""

    def __init__(
        self,
        message: str,
        failed_checks: list[str] | None = None,
        preview_diagnostics: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.failed_checks = failed_checks or []
        self.preview_diagnostics = preview_diagnostics or {}


class SiiFinalEmissionUncertainError(SiiNavigationError):
    """The irreversible click happened, but SII result evidence was unclear."""

    click_performed = True


class SiiNavigationState(str, Enum):
    LOGIN = "STATE_LOGIN"
    AUTHENTICATED = "STATE_AUTHENTICATED"
    BHE_MENU = "STATE_BHE_MENU"
    BHE_EMITTER_MENU = "STATE_BHE_EMITTER_MENU"
    RETENTION_SELECTION = "STATE_RETENTION_SELECTION"
    BHE_FORM = "STATE_BHE_FORM"
    BHE_PREVIEW = "STATE_BHE_PREVIEW"
    UNKNOWN = "STATE_UNKNOWN"


class RetentionMode(str, Enum):
    RECEIVER_RETAINS_PPM = "receiver_retains_ppm"
    ISSUER_HANDLES_PPM = "issuer_handles_ppm"


SII_RETENTION_POST_VALUES = {
    RetentionMode.ISSUER_HANDLES_PPM: "RETCONTRIBUYENTE",
    RetentionMode.RECEIVER_RETAINS_PPM: "RETRECEPTOR",
}


@dataclass(frozen=True)
class SiiFinalEmissionResult:
    validated: bool
    folio: int | None = None
    fecha_emision: str | None = None
    pdf_base64: str | None = None
    codigo_barras: str | None = None
    message: str = ""
    source_frame_index: int | None = None
    pdf_frame_index: int | None = None
    retention_mode_match: bool | None = None


@dataclass(frozen=True)
class SiiFinalResultSurface:
    frame_index: int
    url: str
    text: str


@dataclass(frozen=True)
class PreviewOnlyResult:
    """Non-emission result returned by the explicitly diagnostic flow."""

    preview_reached: bool
    structural_preview_valid: bool
    requested_retention: str | None = None
    selected_retention: str | None = None
    selected_radio_value: str | None = None
    selection_verified: bool | None = None
    post_retention_value: str | None = None
    detected_retention: str | None = None
    evidence_type: str | None = None
    issuer_matches: list[dict[str, object]] | None = None
    receiver_matches: list[dict[str, object]] | None = None
    diagnostic_run_id: str | None = None
    diagnostic_artifact_dir: str | None = None
    blocked_reason: str | None = None


def parse_sii_final_result(text: str) -> SiiFinalEmissionResult:
    """Conservative parser: success requires confirmation text and a folio."""
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    success = bool(re.search(r"boleta.*(?:emitida|generada)|emisi[oó]n.*(?:exitosa|realizada)", normalized, re.I))
    folio_match = re.search(r"(?:folio|n[uú]mero\s+de\s+boleta)\s*[:#-]?\s*(\d{1,12})", normalized, re.I)
    folio = int(folio_match.group(1)) if folio_match else None
    return SiiFinalEmissionResult(
        validated=bool(success and folio is not None),
        folio=folio,
        message="Resultado SII validado." if success and folio is not None else "Resultado SII insuficiente para confirmar emisión.",
    )


def parse_sii_final_result_surfaces(surfaces: list[SiiFinalResultSurface]) -> SiiFinalEmissionResult:
    """Parse the real post-emission main document plus its BHE frame."""
    main = next((surface for surface in surfaces if surface.frame_index == 0), None)
    bhe_frame = next(
        (
            surface for surface in surfaces
            if surface.frame_index != 0
            and re.search(r"/IMT/pdf/bhe_[^/]+\.html$", urlsplit(surface.url).path, re.IGNORECASE)
        ),
        None,
    )
    if main is None or bhe_frame is None:
        return SiiFinalEmissionResult(validated=False, message="Resultado SII sin documento principal y frame BHE.")

    main_path_ok = urlsplit(main.url).path == "/cgi_IMT/TMBECN_BoletaHonorariosElectronica.cgi"
    frame_text = _normalize_final_result_text(bhe_frame.text)
    header_match = re.search(r"boleta\s+de\s+honorarios\s+electronica", frame_text, re.IGNORECASE)
    if not main_path_ok or header_match is None:
        return SiiFinalEmissionResult(validated=False, message="Resultado SII sin estructura BHE emitida.")

    header_tail = frame_text[header_match.end():header_match.end() + 160].casefold().split(" res. ex.", 1)[0]
    folio_match = re.search(r"\bn\s*(?:[º°o]\s*)?(\d{1,12})\b", header_tail, re.IGNORECASE)
    emission_match = re.search(
        r"fecha\s*/\s*hora\s+emision\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})",
        frame_text,
        re.IGNORECASE,
    )
    if not folio_match or not emission_match:
        return SiiFinalEmissionResult(validated=False, message="Resultado SII sin folio o fecha de emisión verificable.")

    emission_datetime = datetime.strptime(emission_match.group(1), "%d/%m/%Y %H:%M").isoformat(sep=" ")
    return SiiFinalEmissionResult(
        validated=True,
        folio=int(folio_match.group(1)),
        fecha_emision=emission_datetime,
        source_frame_index=bhe_frame.frame_index,
        message="BHE emitida y resultado SII validado.",
    )


def _normalize_final_result_text(value: str) -> str:
    """Normalize accents and whitespace for conservative result matching."""
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents).strip()


SII_REGION_NAMES = {
    1: "TARAPACA",
    2: "ANTOFAGASTA",
    3: "ATACAMA",
    4: "COQUIMBO",
    5: "VALPARAISO",
    6: "OHIGGINS",
    7: "MAULE",
    8: "BIOBIO",
    9: "ARAUCANIA",
    10: "LOS LAGOS",
    11: "AYSEN",
    12: "MAGALLANES",
    13: "METROPOLITANA",
    14: "LOS RIOS",
    15: "ARICA Y PARINACOTA",
    16: "ÑUBLE",
}


def normalize_sii_region(value: str | int) -> str:
    """Convert AppBoletas' numeric domain region to SII's semantic label."""
    raw = str(value).strip()
    if raw.isdigit() and int(raw) in SII_REGION_NAMES:
        return SII_REGION_NAMES[int(raw)]
    return raw


@dataclass(frozen=True)
class BHEFormData:
    ENV_NAMES = (
        "SII_POC_RUT_RECEPTOR",
        "SII_POC_DOMICILIO_RECEPTOR",
        "SII_POC_REGION",
        "SII_POC_COMUNA",
        "SII_POC_DETALLE",
        "SII_POC_MONTO",
        "SII_POC_FECHA",
    )

    fecha_emision: date
    rut_receptor: str
    domicilio_receptor: str
    region: str
    comuna: str
    detalle: str
    monto: int
    nombre_receptor: str = ""

    @classmethod
    def from_environment(cls) -> "BHEFormData | None":
        values = {name: os.getenv(name, "").strip() for name in cls.ENV_NAMES}
        if not all(values.values()):
            return None
        try:
            emission_date = datetime.strptime(values["SII_POC_FECHA"], "%Y-%m-%d").date()
        except ValueError:
            try:
                emission_date = datetime.strptime(values["SII_POC_FECHA"], "%d-%m-%Y").date()
            except ValueError as exc:
                raise SiiNavigationError(
                    "Invalid POC data. SII_POC_FECHA must be YYYY-MM-DD or DD-MM-YYYY and "
                    "SII_POC_MONTO must be an integer."
                ) from exc
        try:
            amount = int(values["SII_POC_MONTO"])
        except ValueError as exc:
            raise SiiNavigationError(
                "Invalid POC data. SII_POC_MONTO must be an integer."
            ) from exc
        if amount <= 0:
            raise SiiNavigationError("SII_POC_MONTO must be greater than zero.")
        return cls(
            fecha_emision=emission_date,
            rut_receptor=values["SII_POC_RUT_RECEPTOR"],
            nombre_receptor=os.getenv("SII_POC_NOMBRE_RECEPTOR", "").strip(),
            domicilio_receptor=values["SII_POC_DOMICILIO_RECEPTOR"],
            region=values["SII_POC_REGION"],
            comuna=values["SII_POC_COMUNA"],
            detalle=values["SII_POC_DETALLE"],
            monto=amount,
        )

    @classmethod
    def missing_environment_fields(cls) -> list[str]:
        return [name for name in cls.ENV_NAMES if not os.getenv(name, "").strip()]

    def to_automation_data(
        self, retention_mode: RetentionMode = RetentionMode.ISSUER_HANDLES_PPM
    ) -> "BHEAutomationData":
        return BHEAutomationData(
            fecha_emision=self.fecha_emision,
            rut_receptor=self.rut_receptor,
            razon_social=self.nombre_receptor,
            direccion=self.domicilio_receptor,
            region=self.region,
            comuna=self.comuna,
            detalle=self.detalle,
            monto=self.monto,
            retention_mode=retention_mode,
        )


@dataclass(frozen=True)
class BHEAutomationData:
    fecha_emision: date
    rut_receptor: str
    razon_social: str
    direccion: str
    region: str
    comuna: str
    detalle: str
    monto: int
    retention_mode: RetentionMode = RetentionMode.ISSUER_HANDLES_PPM


def load_sii_poc_env(path: Path = DEFAULT_POC_ENV_FILE) -> bool:
    """Load the ignored local POC file without adding a dotenv dependency.

    Existing process environment variables win over values from the file.
    Only simple KEY=VALUE lines are accepted; secrets are never printed.
    """
    if not path.is_file():
        return False
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name.startswith("SII_POC_"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)
    return True


@dataclass(frozen=True)
class BHEFormFieldMap:
    issuer_address: Locator | None
    issuer_comuna: Locator | None
    day: Locator | None
    month: Locator | None
    year: Locator | None
    rut_controls: tuple[Locator, ...]
    rut_body: Locator | None
    rut_dv: Locator | None
    recipient_name: Locator | None
    recipient_address: Locator | None
    region: Locator | None
    comuna: Locator | None
    detail: Locator | None
    amount: Locator | None
    confirm_emission: Locator | None

    @property
    def required_ready(self) -> bool:
        return all(
            (
                self.day,
                self.month,
                self.year,
                self.rut_controls,
                self.rut_body,
                self.rut_dv,
                self.recipient_address,
                self.region,
                self.comuna,
                self.detail,
                self.amount,
            )
        )


@dataclass(frozen=True)
class SiiPocSettings:
    home_url: str = DEFAULT_SII_HOME_URL
    profile_dir: Path = DEFAULT_PROFILE_DIR
    debug_dir: Path = DEFAULT_DEBUG_DIR
    headless: bool = False
    pause_before_confirm: bool = False
    timeout_ms: int = 15_000
    login_wait_timeout_ms: int = 120_000
    diagnostics_enabled: bool = False
    diagnostic_preview_enabled: bool = False

    @classmethod
    def from_environment(cls) -> "SiiPocSettings":
        return cls(
            home_url=os.getenv("SII_HOME_URL", DEFAULT_SII_HOME_URL),
            profile_dir=Path(os.getenv("SII_BROWSER_PROFILE_DIR", str(DEFAULT_PROFILE_DIR))),
            debug_dir=Path(os.getenv("SII_DEBUG_DIR", str(DEFAULT_DEBUG_DIR))),
            headless=parse_env_bool(os.getenv("SII_POC_HEADLESS")),
            pause_before_confirm=parse_env_bool(os.getenv("SII_POC_PAUSE_BEFORE_CONFIRM")),
            timeout_ms=int(os.getenv("SII_POC_TIMEOUT_MS", "15000")),
            login_wait_timeout_ms=int(os.getenv("SII_LOGIN_WAIT_TIMEOUT_MS", "120000")),
            diagnostics_enabled=parse_env_bool(os.getenv(RETENTION_DIAGNOSTIC_ARTIFACTS_ENV)),
            diagnostic_preview_enabled=parse_env_bool(os.getenv(RETENTION_DIAGNOSTIC_PREVIEW_ENV)),
        )


class SiiBrowserService:
    """SII browser lifecycle and automation, including owned batch sessions."""

    _AUTHENTICATED_MARKERS = re.compile(
        r"cerrar\s+sesi[oó]n|desconectar|salir\s+de\s+mi\s+sii", re.IGNORECASE
    )
    _LOGIN_MARKERS = re.compile(
        r"clave\s+tributaria|iniciar\s+sesi[oó]n|identificaci[oó]n\s+de\s+contribuyentes|"
        r"ingresar\s+clave\s+tributaria|autenticaci[oó]n",
        re.IGNORECASE,
    )
    _ONLINE_SERVICES_MARKER = re.compile(r"servicios\s+online", re.IGNORECASE)

    def __init__(self, settings: SiiPocSettings | None = None) -> None:
        self.settings = settings or SiiPocSettings.from_environment()
        self._context: BrowserContext | None = None
        self._playwright = None
        self._playwright_manager = None
        self._batch_session_owner: int | None = None
        self._batch_start_prepared = False
        self._active_page: Page | None = None
        self._last_sii_resolved_recipient_name: str | None = None
        self._last_preview_result: dict[str, object] = {}
        self._last_retention_diagnostics: dict[str, object] = {}
        self._last_preview_recipient_diagnostics = {
            "rut_label_found": False,
            "rut_value_node_found": False,
            "name_label_found": False,
            "name_value_node_found": False,
            "expected_rut_found_anywhere": False,
            "recipient_structure_candidates": 0,
        }
        self._diagnostic_context: dict[str, int] = {}
        self._diagnostic_run_id: str | None = None
        self._diagnostic_artifact_dir: Path | None = None
        self._capture_all_preview_artifacts = False
        self._retention_trace: SiiRetentionTrace | None = None
        self._retention_submission: SiiRetentionSubmission | None = None

    def set_diagnostic_context(self, *, batch_item_id: int | None, emission_id: int | None) -> None:
        """Set non-PII identifiers used only to isolate local debug artifacts."""
        self._diagnostic_context = {
            key: value for key, value in {
                "batch_item_id": batch_item_id,
                "emission_id": emission_id,
            }.items() if isinstance(value, int) and value > 0
        }
        self._current_stage = "initialized"

    def begin_preview_only_run(self, *, batch_id: int, item_id: int) -> tuple[str, Path]:
        """Create an immutable run identity and manifest before browser work."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"preview_{timestamp}_{uuid4().hex[:8]}"
        artifact_dir = Path(self.settings.debug_dir).expanduser().resolve() / f"item_{item_id}" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        self._diagnostic_run_id = run_id
        self._diagnostic_artifact_dir = artifact_dir
        self._diagnostic_context = {"batch_item_id": batch_id, "item_id": item_id}
        self._current_stage = "initialized"
        self._write_preview_manifest(batch_id=batch_id, item_id=item_id, status="created")
        print(
            "SII preview diagnostic started "
            f"run_id={run_id} batch_id={batch_id} item_id={item_id} "
            f"artifact_dir={artifact_dir}"
        )
        return run_id, artifact_dir

    @staticmethod
    def _runtime_code_commit() -> str:
        repo_root = Path(__file__).resolve().parents[3]
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        value = completed.stdout.strip()
        return value or "unavailable"

    def _write_preview_manifest(self, *, batch_id: int, item_id: int, status: str) -> None:
        if self._diagnostic_artifact_dir is None or self._diagnostic_run_id is None:
            return
        manifest = {
            "diagnostic_run_id": self._diagnostic_run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "batch_id": batch_id,
            "item_id": item_id,
            "code_commit": self._runtime_code_commit(),
            "preview_only": True,
            "diagnostic_artifacts_enabled": self.settings.diagnostics_enabled,
            "debug_dir": str(Path(self.settings.debug_dir).expanduser().resolve()),
            "status": status,
        }
        (self._diagnostic_artifact_dir / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _update_preview_manifest(self, status: str) -> None:
        if self._diagnostic_artifact_dir is None:
            return
        manifest_path = self._diagnostic_artifact_dir / "run_manifest.json"
        if not manifest_path.is_file():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = status
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def current_stage(self) -> str:
        return self._current_stage

    @property
    def session_active(self) -> bool:
        """Whether Playwright runtime/context ownership is currently active."""
        return self._context is not None or self._playwright_manager is not None

    def _set_stage(self, stage: str) -> None:
        self._current_stage = stage
        print(f"SII_APP_STAGE={stage}")

    @contextmanager
    def batch_session(self):
        """Own one persistent Playwright lifecycle for a sequential SII batch worker."""
        if self._context is not None or self._playwright_manager is not None:
            raise RuntimeError("SII browser session is already open")
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        SII_BROWSER_EXECUTION_LOCK.acquire()
        self._batch_session_owner = threading.get_ident()
        operation = "launch"
        primary_error = None
        try:
            self._playwright_manager = sync_playwright()
            self._playwright = self._playwright_manager.start()
            self._launch_batch_context()
            operation = "execution"
            yield self
        except Exception as error:
            primary_error = error
            setattr(error, "browser_lifecycle_operation", operation)
            setattr(error, "browser_session_active", self.session_active)
            raise
        finally:
            try:
                try:
                    self._close_batch_context()
                except Exception as error:
                    setattr(error, "browser_lifecycle_operation", "context_close")
                    if primary_error is None:
                        raise
            finally:
                try:
                    runtime_stop = getattr(self._playwright, "stop", None)
                    if runtime_stop is None and self._playwright_manager is not None:
                        runtime_stop = getattr(self._playwright_manager, "stop", None)
                    if runtime_stop is not None:
                        try:
                            runtime_stop()
                        except Exception as error:
                            setattr(error, "browser_lifecycle_operation", "runtime_stop")
                            if primary_error is None:
                                raise
                finally:
                    self._playwright_manager = None
                    self._playwright = None
                    self._batch_session_owner = None
                    SII_BROWSER_EXECUTION_LOCK.release()

    def _assert_batch_session_owner(self) -> None:
        if self._batch_session_owner is not None and threading.get_ident() != self._batch_session_owner:
            raise RuntimeError("SII batch Playwright objects used outside their owning worker thread")

    def _launch_batch_context(self) -> None:
        self._assert_batch_session_owner()
        if self._playwright is None:
            raise RuntimeError("SII Playwright runtime is not initialized")
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.profile_dir),
            headless=self.settings.headless,
            viewport={"width": 1440, "height": 900},
        )
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.set_default_timeout(self.settings.timeout_ms)
        self._active_page = page

    def _close_batch_context(self) -> None:
        self._assert_batch_session_owner()
        context, self._context = self._context, None
        self._active_page = None
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                # The browser may already have closed the context after a
                # navigation/target failure; teardown must not mask it.
                pass

    def recover_to_known_bhe_start(self) -> bool:
        """Reset to the known SII navigation path in the current batch context."""
        self._assert_batch_session_owner()
        if self._context is None:
            return False
        page = self._active_page
        if page is None or page.is_closed():
            page = next((candidate for candidate in self._context.pages if not candidate.is_closed()), None)
        if page is None:
            return False
        page.set_default_timeout(self.settings.timeout_ms)
        page.goto(self.settings.home_url, wait_until="domcontentloaded")
        self._active_page = page
        self.ensure_authenticated(page, interactive_console=False)
        state = self.navigate_to_bhe(page)
        self._active_page = self._active_page or page
        self._batch_start_prepared = state in {
            SiiNavigationState.RETENTION_SELECTION, SiiNavigationState.BHE_FORM
        }
        return self._batch_start_prepared

    def prepare_next_bhe(self) -> bool:
        """Use known menu navigation; the observed new-BHE button stays disabled."""
        if SII_NEW_BHE_FAST_PATH_ENABLED:
            raise RuntimeError("SII new-BHE fast path has not been validated")
        return self.recover_to_known_bhe_start()

    def prepare_clean_retention_validation(self) -> Page:
        """Start a new BHE at a fresh retention-selection page.

        Authentication remains in the persistent profile, but no previous
        Page, DOM, preview, back/forward state, or BHE form is reused. This
        is used by both controlled validations and normal SII emissions.
        """
        self._assert_batch_session_owner()
        if self._context is None:
            raise RuntimeError("A batch browser context is required for clean validation")
        previous_pages = list(self._context.pages)
        page = self._context.new_page()
        page.set_default_timeout(self.settings.timeout_ms)
        for current in previous_pages:
            if current is not page and not current.is_closed():
                try:
                    current.close()
                except PlaywrightError:
                    # Closing an old tab is cleanup; the new page is the
                    # authoritative page for this independent validation.
                    pass
        page.goto(self.settings.home_url, wait_until="domcontentloaded")
        self.ensure_authenticated(page, interactive_console=False)
        state = self.navigate_to_bhe(page)
        page = self._active_page or page
        if state is not SiiNavigationState.RETENTION_SELECTION:
            raise SiiNavigationError(
                "Clean retention validation did not reach a new retention selection page: "
                f"{state.value}"
            )
        self._batch_start_prepared = False
        self._active_page = page
        return page

    def restart_batch_context(self) -> bool:
        """Recreate a broken context while retaining this worker's Playwright runtime."""
        self._assert_batch_session_owner()
        try:
            try:
                self._close_batch_context()
            except Exception:
                self._context = None
                self._active_page = None
            self._launch_batch_context()
            self._batch_start_prepared = False
            return True
        except Exception:
            return False

    def run(self, input_fn: Callable[[str], str] = input) -> bool:
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            self._context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.profile_dir),
                headless=self.settings.headless,
                viewport={"width": 1440, "height": 900},
            )
            try:
                page = self._context.pages[0] if self._context.pages else self._context.new_page()
                page.set_default_timeout(self.settings.timeout_ms)
                page.goto(self.settings.home_url, wait_until="domcontentloaded")
                self.ensure_authenticated(page, input_fn)
                self._diagnose_context("after-login")
                state = self.navigate_to_bhe(page)
                page = self._active_page or page
                if state == SiiNavigationState.RETENTION_SELECTION:
                    page = self._select_retention_and_continue(
                        page, RetentionMode.ISSUER_HANDLES_PPM
                    )
                    self._active_page = page
                    state = self.detect_state(page)
                    self._diagnose_context("after-retention")
                    self._save_debug_screenshot(page, "after-retention")
                elif state == SiiNavigationState.BHE_FORM:
                    state = self.reach_bhe_emission_form(page)
                self._diagnose_context("after-step-1")
                if state == SiiNavigationState.BHE_FORM:
                    state = self._handle_bhe_form(page)
                    if state == SiiNavigationState.BHE_FORM:
                        print("SII BHE form reached successfully.")
                        print("STOP_BEFORE_CONFIRMATION")
                    elif state == SiiNavigationState.BHE_PREVIEW:
                        print("SII BHE preview reached successfully.")
                        print("STOP_BEFORE_REAL_EMISSION")
                elif state == SiiNavigationState.RETENTION_SELECTION:
                    self._save_debug_screenshot(page, "retention-selection")
                    print("SII retention selection reached successfully.")
                    print("POC stopped before filling or emission.")
                else:
                    self._save_debug_screenshot(page, "navigation-state")
                    print(f"SII navigation stopped at {state.value}.")
                    print("Review backend/.local/sii-debug/ for sanitized diagnostics.")
                input_fn("Review the screen and press Enter to close... ")
                return state in {
                    SiiNavigationState.BHE_FORM,
                    SiiNavigationState.BHE_PREVIEW,
                    SiiNavigationState.RETENTION_SELECTION,
                }
            finally:
                self._context.close()
                self._context = None
                self._active_page = None

    def run_bhe_preview(
        self,
        data: BHEAutomationData,
        input_fn: Callable[[str], str] = input,
        *,
        interactive_console: bool = True,
        capture_artifacts: bool = False,
    ) -> dict[str, object]:
        """Run one request-backed SII flow and stop before irreversible emission."""
        self._reset_retention_diagnostics(data.retention_mode)
        self._capture_all_preview_artifacts = capture_artifacts
        print("SII_APP_FLOW_STARTED=YES")
        self._set_stage("browser_starting")
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            try:
                self._context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.settings.profile_dir),
                    headless=self.settings.headless,
                    viewport={"width": 1440, "height": 900},
                )
                self._set_stage("browser_started")
                self._update_preview_manifest("browser_started")
                page = self._context.pages[0] if self._context.pages else self._context.new_page()
                page.set_default_timeout(self.settings.timeout_ms)
                page.goto(self.settings.home_url, wait_until="domcontentloaded")
                self._set_stage("session_check")
                self.ensure_authenticated(
                    page, input_fn, interactive_console=interactive_console
                )
                self._set_stage("navigating_bhe")
                state = self.navigate_to_bhe(page)
                page = self._active_page or page
                if state == SiiNavigationState.RETENTION_SELECTION:
                    page = self._select_retention_and_continue(
                        page, data.retention_mode
                    )
                    self._active_page = page
                    state = self.detect_state(page)
                    self._set_stage("retention_selected")
                if self.last_retention_diagnostics.get("selection_verified") is not True:
                    self._fail_retention_selection("retention_selection_state_not_seen")
                if state != SiiNavigationState.BHE_FORM:
                    raise SiiNavigationError(
                        f"SII request flow did not reach the BHE form: {state.value}"
                    )
                automation = SIIBHEAutomationService(self)
                self._set_stage("form_fill_started")
                mapping = automation.map_form(page)
                if not mapping.required_ready:
                    raise SiiNavigationError("SII BHE form controls could not be mapped safely.")
                automation.fill_bhe_form(page, mapping, data)
                automation.validate_bhe_form(page, mapping, data)
                self._set_stage("form_filled")
                print("BHE_FORM_FILLED=YES")
                try:
                    self._ensure_preview_retention_submission(
                        page, data.retention_mode, getattr(mapping, "confirm_emission", None)
                    )
                except SiiValidationError:
                    self._capture_pre_submit_debug(page, self.last_retention_diagnostics)
                    raise
                next_page, _ = automation.confirm_to_preview(page, mapping)
                self._active_page = next_page
                self._wait_for_preview_stable(next_page)
                if self.detect_state(next_page) != SiiNavigationState.BHE_PREVIEW:
                    raise SiiNavigationError(
                        "SII confirmation did not reach the preview; final emission remains blocked."
                    )
                self._set_stage("preview_reached")
                self._update_preview_manifest("preview_reached")
                preview_checks = automation.validate_preview(next_page, data)
                failed_checks = [name for name, passed in preview_checks.items() if not passed]
                if failed_checks:
                    print("BHE_PREVIEW_VALIDATED=NO")
                    print(f"PREVIEW_VALIDATION_FAILURES={','.join(failed_checks)}")
                    raise SiiValidationError(
                        "SII BHE preview validation failed; final emission remains blocked.",
                        failed_checks,
                        dict(self.last_preview_result),
                    )
                final_button = self._final_emit_button(next_page)
                if final_button is None:
                    raise SiiNavigationError("SII final emission control was not detected safely.")
                print("BHE_PREVIEW_VALIDATED=YES")
                print("FINAL_EMIT_BUTTON_DETECTED=YES")
                print("FINAL_EMIT_BUTTON_BLOCKED=YES")
                return {
                    "preview_validated": True,
                    "recipient_rut_ok": bool(self.last_preview_result.get("recipient_rut_ok")),
                    "sii_resolved_recipient_name_present": bool(
                        self.last_preview_result.get("sii_resolved_recipient_name_present")
                    ),
                    "sii_resolved_recipient_name": self.sii_resolved_recipient_name,
                    "message": "Borrador validado en SII. La boleta aún no ha sido emitida.",
                }
            except Exception as error:
                print(f"SII_APP_FLOW_EXCEPTION_STAGE={self.current_stage}")
                print(f"SII_APP_FLOW_EXCEPTION_TYPE={type(error).__name__}")
                print("SII_APP_FLOW_FAILED=YES")
                raise
            finally:
                if self._context is not None:
                    self._context.close()
                self._context = None
                self._active_page = None
                self._capture_all_preview_artifacts = False

    def run_preview_only(self, data: BHEAutomationData) -> PreviewOnlyResult:
        """Run to preview and return diagnostics without any emission operation.

        The delegated flow has no final-click call site. Its final-button
        lookup is read-only and exists only to prove the preview is complete.
        """
        try:
            result = self.run_bhe_preview(
                data,
                interactive_console=False,
                capture_artifacts=True,
            )
            diagnostics = self.last_retention_diagnostics
            self._update_preview_manifest("failed")
            return self._preview_only_result_from_diagnostics(
                diagnostics,
                preview_reached=True,
                structural_preview_valid=bool(result.get("preview_validated", False)),
            )
        except SiiValidationError as error:
            diagnostics = dict(self.last_retention_diagnostics)
            diagnostics.update(error.preview_diagnostics or {})
            self._update_preview_manifest("failed")
            return self._preview_only_result_from_diagnostics(
                diagnostics,
                preview_reached=self.current_stage == "preview_reached",
                structural_preview_valid=self.current_stage == "preview_reached",
                blocked_reason=str(error),
            )
        except SiiNavigationError as error:
            self._update_preview_manifest("failed")
            return self._preview_only_result_from_diagnostics(
                self.last_retention_diagnostics,
                preview_reached=self.current_stage == "preview_reached",
                structural_preview_valid=False,
                blocked_reason=str(error),
            )

    def _preview_only_result_from_diagnostics(
        self,
        diagnostics: dict[str, object],
        *,
        preview_reached: bool,
        structural_preview_valid: bool,
        blocked_reason: str | None = None,
    ) -> PreviewOnlyResult:
        artifact_dir = None
        if self._diagnostic_artifact_dir is not None:
            artifact_dir = str(self._diagnostic_artifact_dir)
        elif self._diagnostic_context:
            item_id = self._diagnostic_context.get("batch_item_id", 0)
            emission_id = self._diagnostic_context.get("emission_id", 0)
            artifact_dir = str(self.settings.debug_dir / f"item_{item_id}_emission_{emission_id}")
        selection = diagnostics.get("selection") or {}
        submission = diagnostics.get("bhe_confirm_submission") or {}
        post_retention_value = diagnostics.get("post_retention_value")
        if post_retention_value is None:
            requests = submission.get("requests", []) if isinstance(submission, dict) else []
            fields = requests[0].get("retention_fields", []) if requests else []
            post_retention_value = fields[0].get("value") if fields else None
        return PreviewOnlyResult(
            preview_reached=preview_reached,
            structural_preview_valid=structural_preview_valid,
            requested_retention=diagnostics.get("requested_retention_mode"),
            selected_retention=diagnostics.get("selected_retention_mode"),
            selected_radio_value=diagnostics.get("selected_radio_value") or (
                selection.get("selected_radio_value") if isinstance(selection, dict) else None
            ),
            selection_verified=diagnostics.get("selection_verified"),
            post_retention_value=post_retention_value,
            detected_retention=diagnostics.get("detected_retention_mode"),
            evidence_type=diagnostics.get("retention_evidence_kind"),
            issuer_matches=diagnostics.get("issuer_matches", []),
            receiver_matches=diagnostics.get("receiver_matches", []),
            diagnostic_run_id=self._diagnostic_run_id,
            diagnostic_artifact_dir=artifact_dir,
            blocked_reason=blocked_reason,
        )

    def run_bhe_final_emission(self, data: BHEAutomationData) -> dict[str, object]:
        """Serialize independent SII runs; batch-owned sessions already hold the lock."""
        if self._context is not None:
            self._assert_batch_session_owner()
            return self._run_bhe_final_emission_in_context(data)
        with SII_BROWSER_EXECUTION_LOCK:
            return self._run_bhe_final_emission_in_context(data)

    def _run_bhe_final_emission_in_context(self, data: BHEAutomationData) -> dict[str, object]:
        """Rebuild and validate the preview, then perform the one irreversible click."""
        self._reset_retention_diagnostics(data.retention_mode)
        print("SII_FINAL_EMISSION_STARTED=YES")
        self._assert_batch_session_owner()
        self._set_stage("browser_starting")
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        clicked = False
        owns_context = self._context is None
        runtime = sync_playwright() if owns_context else nullcontext(self._playwright)
        with runtime as playwright:
            try:
                if owns_context:
                    self._playwright = playwright
                    self._context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.settings.profile_dir),
                        headless=self.settings.headless,
                        viewport={"width": 1440, "height": 900},
                    )
                self._set_stage("browser_started")
                # Reuse the authenticated context, never the working page or
                # its form/preview state, for another BHE.
                page = self.prepare_clean_retention_validation()
                self._set_stage("clean_retention_start")
                state = self.detect_state(page)
                if state == SiiNavigationState.RETENTION_SELECTION:
                    page = self._select_retention_and_continue(page, data.retention_mode)
                    state = self.detect_state(page)
                    self._set_stage("retention_selected")
                if self.last_retention_diagnostics.get("selection_verified") is not True:
                    self._fail_retention_selection("retention_selection_state_not_seen")
                if state != SiiNavigationState.BHE_FORM:
                    raise SiiNavigationError(f"SII final flow did not reach form: {state.value}")
                automation = SIIBHEAutomationService(self)
                self._set_stage("form_fill_started")
                mapping = automation.map_form(page)
                if not mapping.required_ready:
                    raise SiiNavigationError("SII BHE form controls could not be mapped safely.")
                automation.fill_bhe_form(page, mapping, data)
                automation.validate_bhe_form(page, mapping, data)
                self._set_stage("form_filled")
                print("BHE_FORM_FILLED=YES")
                self._ensure_preview_retention_submission(
                    page, data.retention_mode, getattr(mapping, "confirm_emission", None)
                )
                next_page, _ = automation.confirm_to_preview(page, mapping)
                self._active_page = next_page
                self._wait_for_preview_stable(next_page)
                if self.detect_state(next_page) != SiiNavigationState.BHE_PREVIEW:
                    raise SiiNavigationError("SII confirmation did not reach preview.")
                self._set_stage("preview_reached")
                checks = automation.validate_preview(next_page, data)
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise SiiValidationError(
                        "SII preview validation failed before final click.",
                        failed,
                        dict(self.last_preview_result),
                    )
                final_button = self._final_emit_button(next_page)
                if final_button is None:
                    raise SiiNavigationError("SII final emission control was not detected safely.")
                print("SII_FINAL_BUTTON_CLICKED=YES")
                self._set_stage("final_button_click")
                # This is the only call in the codebase allowed to click this control.
                clicked = True
                pages_before = set(self._context.pages)
                final_button.click()
                try:
                    next_page.wait_for_load_state("domcontentloaded", timeout=self.settings.timeout_ms)
                except PlaywrightTimeoutError:
                    pass
                pages_after = set(self._context.pages)
                result_page = next(iter(pages_after - pages_before), next_page)
                self._active_page = result_page
                result_page.locator("body").wait_for(state="visible", timeout=self.settings.timeout_ms)
                result_page.wait_for_function("document.readyState === 'complete'", timeout=self.settings.timeout_ms)
                print("SII_FINAL_RESULT_PAGE_REACHED=YES")
                self._capture_post_emission_debug(result_page)
                surfaces = self.collect_final_result_surfaces(result_page)
                result_text = "\n".join(surface.text for surface in surfaces)
                barcode = self._extract_final_barcode(result_page)
                post_controls_found = self._has_post_emission_controls(result_page)
                retention_match = self._match_final_retention_mode(surfaces, data.retention_mode)
                final_retention = self._preview_retention_diagnostic(result_text)
                self._last_retention_diagnostics.update({
                    "final_document_detected_mode": final_retention["detected_retention_mode"],
                    "final_document_retention_evidence_kind": final_retention["retention_evidence_kind"],
                    "final_document_retention_match": retention_match,
                })
                result = parse_sii_final_result_surfaces(surfaces)
                result = replace(result, codigo_barras=barcode, retention_mode_match=retention_match)
                bhe_surface = next(
                    (surface for surface in surfaces if surface.frame_index != 0 and re.search(
                        r"/IMT/pdf/bhe_[^/]+\.html$", urlsplit(surface.url).path, re.IGNORECASE
                    )),
                    None,
                )
                bhe_header_found = bool(
                    bhe_surface and re.search(
                        r"boleta\s+de\s+honorarios\s+electronica",
                        _normalize_final_result_text(bhe_surface.text),
                        re.IGNORECASE,
                    )
                )
                print(f"SII_FINAL_FOLIO_CANDIDATE_COUNT={self._count_contextual_folio_candidates(result_text)}")
                print(f"SII_FINAL_BHE_FRAME_FOUND={'YES' if bhe_surface else 'NO'}")
                print(f"SII_FINAL_BHE_HEADER_FOUND={'YES' if bhe_header_found else 'NO'}")
                print(f"SII_FINAL_FOLIO_FOUND_IN_FRAME={'YES' if result.source_frame_index is not None else 'NO'}")
                if result.source_frame_index is not None:
                    print(f"SII_FINAL_FOLIO_SOURCE_FRAME_INDEX={result.source_frame_index}")
                print(f"SII_FINAL_EMISSION_DATETIME_FOUND={'YES' if result.fecha_emision else 'NO'}")
                print(f"SII_FINAL_BARCODE_FOUND={'YES' if barcode else 'NO'}")
                print(f"SII_FINAL_POST_EMISSION_CONTROLS_FOUND={'YES' if post_controls_found else 'NO'}")
                retention_label = "UNKNOWN" if retention_match is None else ("YES" if retention_match else "NO")
                print(f"SII_FINAL_RETENTION_MODE_MATCH={retention_label}")
                print(f"SII_FINAL_PRINT_CONTROL_FOUND={'YES' if self._has_result_control(result_page, r'print|imprimir') else 'NO'}")
                pdf_control = self._find_result_control(result_page, r'pdf|descargar|ver\s+boleta')
                print(f"SII_FINAL_PDF_CONTROL_FOUND={'YES' if pdf_control else 'NO'}")
                if pdf_control:
                    frame_index, tag, action_kind = pdf_control
                    print(f"SII_FINAL_PDF_CONTROL_FRAME_INDEX={frame_index}")
                    print(f"SII_FINAL_PDF_CONTROL_TYPE={tag}")
                    print(f"SII_FINAL_PDF_CONTROL_ACTION_KIND={action_kind}")
                print(f"SII_FINAL_FOLIO_FOUND={'YES' if result.folio is not None else 'NO'}")
                print(f"SII_FINAL_PDF_AVAILABLE={'YES' if result.pdf_base64 else 'NO'}")
                print(f"SII_FINAL_RESULT_VALIDATED={'YES' if result.validated else 'NO'}")
                if not result.validated:
                    raise SiiFinalEmissionUncertainError(result.message)
                print("SII_FINAL_EMISSION_COMPLETED=YES")
                return {
                    "validated": True,
                    "folio": result.folio,
                    "fecha_emision": result.fecha_emision,
                    "pdf_base64": result.pdf_base64,
                    "codigo_barras": result.codigo_barras,
                    "sii_resolved_recipient_name": self._last_sii_resolved_recipient_name,
                    "provider_message": result.message,
                }
            except SiiFinalEmissionUncertainError:
                print("SII_FINAL_EMISSION_COMPLETED=NO")
                raise
            except Exception as error:
                print(f"SII_FINAL_FAILURE_STAGE={self.current_stage}")
                print(f"SII_FINAL_EXCEPTION_TYPE={type(error).__name__}")
                print("SII_FINAL_EMISSION_COMPLETED=NO")
                setattr(error, "click_performed", clicked)
                raise
            finally:
                if owns_context:
                    if self._context is not None:
                        self._context.close()
                    self._context = None
                    self._active_page = None
                    self._playwright = None

    def is_authenticated(self, page: Page) -> bool:
        """Use page evidence only; never read credentials, cookies, or tokens."""
        body_text = page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        if self._has_bhe_evidence(body_text):
            return True
        if self._AUTHENTICATED_MARKERS.search(body_text):
            return True
        if self._LOGIN_MARKERS.search(body_text):
            return False
        return "login" not in page.url.lower() and "autentic" not in page.url.lower()

    def ensure_authenticated(
        self,
        page: Page,
        input_fn: Callable[[str], str] = input,
        *,
        interactive_console: bool = True,
    ) -> None:
        if self.is_authenticated(page):
            print("SII authenticated session detected in the persistent profile.")
            self._save_debug_screenshot(page, "after-login")
            return
        print("Login required.")
        print("Log in manually in the visible SII browser.")
        if interactive_console:
            input_fn("After login is complete, press Enter here to continue... ")
        else:
            deadline = time.monotonic() + self.settings.login_wait_timeout_ms / 1000
            while time.monotonic() < deadline:
                page.wait_for_timeout(500)
                if self.is_authenticated(page):
                    self._save_debug_screenshot(page, "after-login")
                    return
            raise SiiNavigationError(
                "SII login was not detected before the application timeout."
            )
        self._diagnose_context("after-login")
        if not self.is_authenticated(page):
            raise SiiNavigationError(
                "SII session could not be confirmed after manual login. "
                "Review the open page and run the POC again."
            )
        self._save_debug_screenshot(page, "after-login")

    def navigate_to_bhe(self, page: Page) -> SiiNavigationState:
        """Click only the observed BHE navigation entry and classify the result."""
        try:
            link = self._bhe_link(page)
            if link.count() == 0:
                online = page.get_by_text(self._ONLINE_SERVICES_MARKER).first
                if online.count() > 0:
                    page = self._click_navigation(page, online, "online-services")
                link = self._bhe_link(page)
            if link.count() == 0:
                raise SiiNavigationError(
                    "SII navigation failed: Expected BHE menu was not found. "
                    "The SII interface may have changed."
                )
            page = self._click_navigation(page, link, "bhe-menu")
            self._active_page = page
            state = self.detect_state(page)
            if state == SiiNavigationState.BHE_EMITTER_MENU:
                page = self._click_contributor(page)
                self._active_page = page
                self._diagnose_context("after-contributor")
                self._save_debug_screenshot(page, "after-contributor")
                return self.detect_state(page)
            return state
        except PlaywrightTimeoutError as exc:
            raise SiiNavigationError(
                "SII navigation failed: timeout while opening the BHE menu."
            ) from exc

    def _click_contributor(self, page: Page) -> Page:
        contributor = page.get_by_role("link", name="Por contribuyente", exact=True).first
        if contributor.count() == 0:
            contributor = page.get_by_text("Por contribuyente", exact=True).first
        if contributor.count() == 0:
            raise SiiNavigationError(
                "SII navigation failed: 'Por contribuyente' was not found in the emitter menu."
            )
        return self._click_navigation(page, contributor, "por-contribuyente")

    def reach_bhe_emission_form(self, page: Page) -> SiiNavigationState:
        """Validate the safe stopping point without filling or submitting fields."""
        state = self.detect_state(page)
        if state != SiiNavigationState.BHE_FORM:
            raise SiiNavigationError(
                "SII BHE emission form was not reached; "
                f"current state is {state.value}."
            )
        return state

    def _select_retention_and_continue(self, page: Page, mode: RetentionMode) -> Page:
        other_mode = (
            RetentionMode.RECEIVER_RETAINS_PPM
            if mode is RetentionMode.ISSUER_HANDLES_PPM
            else RetentionMode.ISSUER_HANDLES_PPM
        )
        self._last_retention_diagnostics.update({
            "requested_retention_mode": mode.value,
            "selected_retention_mode": None,
            "selection_verified": False,
            "detected_retention_mode": None,
            "retention_evidence_kind": "not_evaluated",
            "preview_retention_mode_ok": None,
            "failed_checks": [],
            "diagnostic_reason": "retention_selection_in_progress",
            "preview_frame_index": None,
            "preview_stabilized": None,
            "selection_action_succeeded": False,
            "semantic_selection_verified": False,
        })
        # Discover and retain the raw SII controls before any semantic lookup
        # or guard can fail. This is intentionally independent of labels.
        selection_before = self._retention_selection_inventory(page)
        self._last_retention_diagnostics.update({
            "selection_diagnostics": {
                "diagnostic_version": "retention-selection-v1",
                "requested": mode.value,
                "options_before_selection": selection_before["options"],
                "options_after_selection": [],
            },
            "selection": {
                "diagnostic_version": "retention-selection-v1",
                "requested": mode.value,
                "options_before_selection": selection_before["options"],
                "options_after_selection": [],
            },
        })
        option = self._find_retention_option(page, mode)
        if option is None:
            self._fail_retention_selection("retention_option_not_found")
        other_option = self._find_retention_option(page, other_mode)
        try:
            option.check()
            self._last_retention_diagnostics["selection_action_succeeded"] = True
            requested_checked = bool(option.is_checked())
            other_checked = bool(other_option and other_option.is_checked())
            selected_label = self._radio_context_text(page, option)
            options_after = self._retention_selection_inventory(page)
        except Exception:
            self._fail_retention_selection("retention_selection_control_error")
        selected_label_mode = self._classify_retention_selection_label(selected_label)
        verified = (
            requested_checked and other_option is not None and not other_checked
            and selected_label_mode == mode.value
        )
        selected = (
            mode.value if requested_checked and not other_checked
            else other_mode.value if other_checked and not requested_checked
            else "ambiguous" if requested_checked and other_checked
            else None
        )
        selection_snapshot_base = {
            "diagnostic_version": "retention-selection-v1",
            "requested": mode.value,
            "options_before_selection": selection_before["options"],
            "options_after_selection": options_after["options"],
        }
        self._last_retention_diagnostics.update({
            "selected_retention_mode": selected,
            "selection_verified": verified,
            "selection_label_mode": selected_label_mode,
            "selection_diagnostics": {
                **selection_snapshot_base,
                "options_before_selection": selection_before["options"],
                "options_after_selection": options_after["options"],
            },
            "selection": {
                **selection_snapshot_base,
                "options_before_selection": selection_before["options"],
                "options_after_selection": options_after["options"],
            },
            "diagnostic_reason": "selection_verified" if verified else "retention_selection_mismatch",
            "semantic_selection_verified": verified,
        })
        if not verified:
            self._fail_retention_selection("retention_selection_mismatch")

        continue_button = page.get_by_role("button", name="Continuar", exact=True).first
        if continue_button.count() == 0:
            continue_button = page.get_by_text("Continuar", exact=True).first
        if continue_button.count() == 0:
            self._fail_retention_selection("retention_continue_control_not_found")
        # Take the persisted snapshot immediately before submitting/navigating.
        options_before_continue = self._retention_selection_inventory(page)
        selected_record = next(
            (item for item in options_before_continue["options"]
             if item.get("selected_radio") is True and item.get("semantic_mode") == mode.value),
            None,
        )
        opposite_record = next(
            (item for item in options_before_continue["options"]
             if item.get("semantic_mode") == other_mode.value),
            None,
        )
        selected_radio = selected_record.get("radio", {}) if selected_record else {}
        selection_snapshot = {
            "diagnostic_version": "retention-selection-v1",
            "requested": mode.value,
            "options_before_selection": selection_before["options"],
            "options_after_selection": options_after["options"],
            "options_before_continue": options_before_continue["options"],
            "selected": selected_record,
            "selected_radio_index": selected_radio.get("index"),
            "selected_radio_name": selected_radio.get("name"),
            "selected_radio_id": selected_radio.get("id"),
            "selected_radio_value": selected_radio.get("value"),
            "selected_radio_checked": selected_radio.get("checked"),
            "selected_radio_label": selected_record.get("label") if selected_record else None,
            "selected_radio_semantic_mode": selected_record.get("semantic_mode") if selected_record else None,
            "opposite_radio_checked": bool(
                opposite_record and opposite_record.get("radio", {}).get("checked")
            ),
            "form": selected_record.get("form") if selected_record else None,
            "js": options_before_continue["js"],
        }
        self._last_retention_diagnostics.update({
            "selection_diagnostics": selection_snapshot,
            "selection": selection_snapshot,
        })
        if not selected_record or not selected_radio.get("checked") or selection_snapshot["opposite_radio_checked"]:
            self._fail_retention_selection("retention_selection_mismatch_before_continue")
        return self._click_retention_continue(
            page, continue_button, selection_snapshot, selected_record
        )

    def _click_retention_continue(self, page, button, snapshot, selected_record):
        form = selected_record.get("form") or {}
        form_fields = form.get("fields") or []
        allowed_names = {
            field.get("name") for field in form_fields
            if isinstance(field, dict) and field.get("name")
        }
        selected_name = (selected_record.get("radio") or {}).get("name")
        if selected_name:
            allowed_names.add(selected_name)
        action_path = str(form.get("action_path") or "")
        observed: list[dict[str, object]] = []

        def capture_request(request):
            try:
                parts = urlsplit(request.url)
                if request.method.upper() != str(form.get("method") or "GET").upper():
                    return
                if action_path and parts.path != action_path:
                    return
                pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=500)
                body = request.post_data
                body_encoding = "none"
                body_parsed = True
                if body:
                    content_type = str(request.headers.get("content-type", "")).casefold()
                    if "json" in content_type:
                        body_encoding = "json"
                        payload = json.loads(body)
                        if isinstance(payload, dict):
                            pairs.extend((str(key), str(value)) for key, value in payload.items()
                                         if isinstance(value, (str, int, float, bool)))
                    elif "multipart/form-data" in content_type:
                        body_encoding = "multipart"
                        body_parsed = False
                    else:
                        body_encoding = "urlencoded"
                        pairs.extend(parse_qsl(body, keep_blank_values=True, max_num_fields=500))
                selected_pairs = [
                    {"name": key, "value": self._safe_selection_value(value)}
                    for key, value in pairs if key in allowed_names
                ][:20]
                observed.append({
                    "method": request.method.upper(),
                    "path": self._sanitize_retention_fragment(parts.path)[:240],
                    "retention_fields": selected_pairs,
                    "body_encoding": body_encoding,
                    "encoding_parsed": body_parsed,
                })
            except Exception:
                observed.append({"capture_error": "request_metadata_unavailable"})

        owner = self._context or page
        owner.on("request", capture_request)
        try:
            return self._click_navigation(page, button, "retention-continue")
        finally:
            try:
                owner.remove_listener("request", capture_request)
            except Exception:
                pass
            snapshot["submission"] = {
                "observed": bool(observed),
                "requests": observed[:3],
                "expected_method": str(form.get("method") or "GET").upper(),
                "expected_path": action_path,
                "capture_scope": "matching_form_action_and_retention_field_names_only",
            }
            self._last_retention_diagnostics["selection"] = snapshot
            self._last_retention_diagnostics["selection_diagnostics"] = snapshot

    def _fail_retention_selection(self, reason: str) -> None:
        diagnostics = self.last_retention_diagnostics
        diagnostics.update({
            "failed_checks": ["retention_selection_verified"],
            "diagnostic_reason": reason,
            "selection_verified": False,
            "semantic_selection_verified": False,
        })
        self._last_retention_diagnostics = diagnostics
        raise SiiValidationError(
            "SII retention selection could not be verified; stopping before form submission.",
            ["retention_selection_verified"],
            diagnostics,
        )

    @property
    def last_retention_diagnostics(self) -> dict[str, object]:
        return dict(self._last_retention_diagnostics)

    def _reset_retention_diagnostics(self, requested: RetentionMode) -> None:
        self._last_preview_result = {}
        self._last_retention_diagnostics = {
            "requested_retention_mode": requested.value,
            "selected_retention_mode": None,
            "selection_verified": None,
            "detected_retention_mode": None,
            "retention_evidence_kind": "not_evaluated",
            "preview_retention_mode_ok": None,
            "failed_checks": [],
            "diagnostic_reason": "retention_selection_not_reached",
            "preview_frame_index": None,
            "preview_stabilized": None,
            "diagnostic_version": RETENTION_DIAGNOSTIC_VERSION,
            "selection_label_mode": None,
            "selection_diagnostics": None,
            "selection": None,
            "preview": None,
            "selection_action_succeeded": False,
            "semantic_selection_verified": None,
            "decision_surface": [],
            "preview_frame_inventory": [],
            "retention_candidates": [],
        }

    def _wait_for_preview_stable(self, page: Page) -> None:
        try:
            page.wait_for_function(
                """() => {
                    const body = document.body?.innerText || '';
                    const draft = /borrador[\\s\\S]*no\\s+tiene[\\s\\S]*ninguna\\s+validez/i.test(body);
                    const finalControl = [...document.querySelectorAll('button,input[type=button],input[type=submit]')]
                      .some(node => /emitir\\s+boleta\\s+de\\s+honorarios\\s+electr[oó]nica/i.test(node.innerText || node.value || ''));
                    return document.readyState === 'complete' && draft && finalControl;
                }""",
                timeout=self.settings.timeout_ms,
            )
        except PlaywrightTimeoutError as error:
            self._last_retention_diagnostics.update({
                "preview_stabilized": False,
                "retention_evidence_kind": "preview_not_stabilized",
                "diagnostic_reason": "preview_structure_not_stable_before_timeout",
                "failed_checks": ["preview_structure_stable"],
            })
            raise SiiValidationError(
                "SII preview did not reach its expected stable structure.",
                ["preview_structure_stable"],
                self.last_retention_diagnostics,
            ) from error
        self._last_retention_diagnostics["preview_stabilized"] = True

    def _capture_bhe_confirm_submission(self, page: Page, click) -> Page:
        """Observe POST #2 and abort incorrect retention without rewriting its body."""
        observed: list[dict[str, object]] = []
        requested_mode = self._last_retention_diagnostics.get("requested_retention_mode")
        if requested_mode not in {mode.value for mode in RetentionMode}:
            raise SiiValidationError(
                "SII preview request has no verified requested retention.",
                ["preview_retention_network_value"], self.last_retention_diagnostics,
            )
        expected_value = SII_RETENTION_POST_VALUES[RetentionMode(requested_mode)]
        route_pattern = re.compile(
            r"^https?://[^/]+/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib\.cgi(?:\?.*)?$"
        )
        approved_requests = 0
        request_aborted = False

        def guard_request(route):
            nonlocal approved_requests, request_aborted
            request = route.request
            if request.method.upper() != "POST":
                route.fallback()
                return
            try:
                pairs = parse_qsl(request.post_data or "", keep_blank_values=True, max_num_fields=500)
                values = [value for name, value in pairs if name == "OptTipoRetencion"]
                valid = (
                    request.is_navigation_request() and request.frame == page.main_frame
                    and values == [expected_value] and approved_requests == 0
                )
            except Exception:
                valid = False
            if not valid:
                request_aborted = True
                route.abort("blockedbyclient")
                return
            approved_requests += 1
            route.fallback()

        def capture_request(request):
            if request.method.upper() != "POST":
                return
            if urlsplit(request.url).path != "/cgi_IMT/TMBECN_ConfirmaTimbrajeContrib.cgi":
                return
            try:
                pairs = parse_qsl(request.post_data or "", keep_blank_values=True, max_num_fields=500)
                fields = [
                    {"name": "OptTipoRetencion", "value": value if value in SII_RETENTION_POST_VALUES.values() else None}
                    for name, value in pairs
                    if str(name).casefold() == "opttiporetencion"
                ]
                observed.append({
                    "method": "POST",
                    "path": urlsplit(request.url).path,
                    "retention_fields": fields[:20],
                    "body_observed": bool(request.post_data),
                })
                if self._retention_trace is not None:
                    self._retention_trace.record("T5_network_request", {
                        "serialized_retention_values": [field["value"] for field in fields],
                    })
            except Exception:
                observed.append({"capture_error": "request_metadata_unavailable"})

        owner = self._context or page
        page.route(route_pattern, guard_request)
        owner.on("request", capture_request)
        try:
            return click()
        finally:
            try:
                owner.remove_listener("request", capture_request)
            except Exception:
                pass
            page.unroute(route_pattern, guard_request)
            if self._retention_submission is not None:
                try:
                    self._retention_submission.dispose(page)
                except PlaywrightError:
                    pass  # Navigation/closure already destroyed the form and its listener.
                self._retention_submission = None
            self._last_retention_diagnostics["bhe_confirm_submission"] = {
                "requests": observed[:5],
                "capture_scope": "POST path and retention-related fields only",
            }
            retention_values = [
                field["value"]
                for request in observed
                for field in request.get("retention_fields", [])
                if isinstance(field, dict) and field.get("name") == "OptTipoRetencion"
            ]
            first_request = next(
                (request for request in observed if request.get("method") == "POST"),
                {},
            )
            network_summary = {
                "post2_request_observed": bool(first_request),
                "post2_method": first_request.get("method"),
                "post2_url_path": first_request.get("path"),
                "post2_retention_value": retention_values[0] if retention_values else "missing",
                "post2_retention_occurrences": len(retention_values),
            }
            network_summary["post2_expected_retention"] = expected_value
            network_summary["post2_retention_matches_requested"] = (
                expected_value is not None
                and network_summary["post2_retention_value"] == expected_value
                and network_summary["post2_retention_occurrences"] == 1
            )
            network_summary["post2_request_aborted"] = request_aborted
            network_summary["post2_network_guard_verified"] = (
                not request_aborted and approved_requests == 1
                and network_summary["post2_retention_matches_requested"]
            )
            network_summary["network_value_after_enforcement"] = network_summary["post2_retention_value"]
            self._last_retention_diagnostics["post2_network_capture"] = network_summary
            if self.settings.diagnostics_enabled and self._diagnostic_artifact_dir is not None:
                (self._diagnostic_artifact_dir / "post2_request_diagnostic.json").write_text(
                    json.dumps(
                        {"sensitive_local_debug": True, **network_summary},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            if not network_summary["post2_network_guard_verified"]:
                raise SiiValidationError(
                    "SII preview request retention was not verified; final emission remains blocked.",
                    ["preview_retention_network_value"], self.last_retention_diagnostics,
                )

    def _ensure_preview_retention_submission(
        self, page: Page, requested: RetentionMode, confirm_control: Locator | None = None
    ) -> list[str]:
        """Ensure the actual POST-2 FormData retention value matches the request.

        The confirmation form is the FORM B that generates POST #2 and may contain a hidden
        ``OptTipoRetencion`` independent of the radio selected on POST #1.
        Validate the serialized form state, not merely the earlier radio.
        """
        expected = SII_RETENTION_POST_VALUES[requested]
        target = None
        if confirm_control is not None:
            candidate = confirm_control.locator("xpath=ancestor::form[1]")
            if candidate.count() > 0:
                target = candidate
        else:
            forms = page.locator("form")
            for index in range(forms.count()):
                candidate = forms.nth(index)
                if candidate.locator("[name='cmdconfirmar']").count() > 0:
                    target = candidate
                    break
        if target is None:
            self._last_retention_diagnostics.update({
                "preview_submit_expected_retention": expected,
                "preview_submit_actual_retention_values": [],
                "preview_submit_retention_verified": False,
                "preview_submit_retention_reason": "preview_form_not_found",
                "preview_submit_target_form_found": False,
                "target_form_found": False,
                "confirm_control_found": False,
                "retention_input_found": False,
                "pre_submit_retention_verified": False,
            })
            raise SiiValidationError(
                "SII preview form could not be located for retention verification.",
                ["preview_retention_submit_value"],
                self.last_retention_diagnostics,
            )

        hidden = target.locator("input[type='hidden'][name='OptTipoRetencion']")
        if self.settings.diagnostics_enabled and self._diagnostic_artifact_dir is not None:
            self._retention_trace = SiiRetentionTrace(self._diagnostic_artifact_dir, expected)
            try:
                self._retention_trace.install(page, target)
                self._last_retention_diagnostics["retention_trace_installed"] = True
            except (PlaywrightError, OSError):
                self._last_retention_diagnostics["retention_trace_installed"] = False
                self._retention_trace = None
        self._last_retention_diagnostics["preview_submit_target_form_found"] = True
        self._last_retention_diagnostics["preview_submit_target_form_action"] = target.get_attribute("action") or ""
        self._last_retention_diagnostics["preview_submit_target_form_method"] = (target.get_attribute("method") or "GET").upper()
        self._last_retention_diagnostics["preview_submit_confirm_control_found"] = confirm_control is not None
        self._last_retention_diagnostics["target_form_found"] = True
        self._last_retention_diagnostics["target_form_action"] = target.get_attribute("action") or ""
        self._last_retention_diagnostics["target_form_method"] = (target.get_attribute("method") or "GET").upper()
        self._last_retention_diagnostics["confirm_control_found"] = confirm_control is not None
        self._last_retention_diagnostics["retention_input_found"] = hidden.count() > 0
        if hidden.count() == 0:
            self._last_retention_diagnostics.update({
                "preview_submit_expected_retention": expected,
                "preview_submit_actual_retention_values": [],
                "preview_submit_retention_verified": False,
                "preview_submit_retention_reason": "preview_retention_hidden_input_not_found",
                "pre_submit_retention_verified": False,
            })
            raise SiiValidationError(
                "SII preview retention value could not be identified safely.",
                ["preview_retention_submit_value"],
                self.last_retention_diagnostics,
            )

        control_inventory = target.evaluate("""form => {
            const controls = Array.from(form.elements).filter(element => element.name === 'OptTipoRetencion');
            return {count: controls.length, enabled: controls.length === 1 && !controls[0].matches(':disabled')};
        }""")
        if hidden.count() != 1 or control_inventory.get("count") != 1 or not control_inventory.get("enabled"):
            self._last_retention_diagnostics.update({
                "pre_submit_retention_verified": False,
                "preview_submit_retention_reason": "retention_input_duplicate_or_disabled",
            })
            raise SiiValidationError(
                "SII confirmation form must have exactly one enabled retention input.",
                ["preview_retention_submit_value"], self.last_retention_diagnostics,
            )

        for index in range(hidden.count()):
            if index == 0:
                self._last_retention_diagnostics["preview_submit_retention_input_before"] = hidden.nth(index).input_value()
            hidden.nth(index).evaluate(
                "(element, value) => { element.value = value; }", expected
            )
        self._retention_submission = SiiRetentionSubmission(expected)
        diagnostic_binding = None
        if self.settings.diagnostics_enabled and self._diagnostic_artifact_dir is not None:
            diagnostic_binding = f"__appboletasFormdataEnforcement_{uuid4().hex}"
            page.expose_binding(
                diagnostic_binding,
                lambda _source, snapshot: self._record_formdata_enforcement(snapshot),
            )
        self._retention_submission.install(target, diagnostic_binding)
        actual_values = target.evaluate(
            """
            form => Array.from(new FormData(form).getAll('OptTipoRetencion'))
                .map(value => String(value))
            """
        )
        actual_values = [str(value) for value in (actual_values or [])]
        self._snapshot_retention_trace(page, "T1_after_correction", actual_values)
        verified = actual_values == [expected]
        self._last_retention_diagnostics.update({
            "preview_submit_expected_retention": expected,
            "preview_submit_actual_retention_values": actual_values,
            "preview_submit_retention_verified": verified,
            "preview_submit_retention_reason": "verified" if verified else "serialized_value_mismatch",
            "preview_submit_retention_input_after": hidden.nth(0).input_value(),
            "preview_submit_effective_formdata_retention": actual_values,
            "target_form_found": True,
            "confirm_control_found": confirm_control is not None,
            "retention_input_found": True,
            "retention_input_before": self._last_retention_diagnostics.get("preview_submit_retention_input_before"),
            "retention_input_after": self._last_retention_diagnostics.get("preview_submit_retention_input_after"),
            "effective_formdata_retention": actual_values,
            "pre_submit_retention_verified": verified,
        })
        if not verified:
            raise SiiValidationError(
                "SII preview retention submission did not match the requested mode.",
                ["preview_retention_submit_value"],
                self.last_retention_diagnostics,
            )
        return actual_values

    def _record_formdata_enforcement(self, snapshot: dict[str, object]) -> None:
        safe_snapshot = {
            key: [value if value in SII_RETENTION_POST_VALUES.values() else None for value in snapshot.get(key, [])[:40]]
            for key in ("formdata_value_before_enforcement", "formdata_value_after_enforcement")
        }
        safe_snapshot["formdata_enforcement_applied"] = snapshot.get("formdata_enforcement_applied") is True
        self._last_retention_diagnostics.update(safe_snapshot)
        if self._retention_trace is not None:
            self._retention_trace.record("formdata_enforcement", safe_snapshot)

    def _snapshot_retention_trace(
        self, page: Page, stage: str, serialized_values: list[str] | None = None
    ) -> None:
        if self._retention_trace is None:
            return
        try:
            self._retention_trace.snapshot(page, stage, serialized_values)
        except (PlaywrightError, OSError):
            self._last_retention_diagnostics["retention_trace_snapshot_failed"] = True

    def _capture_pre_submit_debug(self, page: Page, diagnostic: dict[str, object]) -> None:
        """Capture local evidence when preview-only stops before POST #2."""
        if not self.settings.diagnostics_enabled or self._diagnostic_artifact_dir is None:
            return
        artifact_dir = self._diagnostic_artifact_dir
        (artifact_dir / "pre_submit_diagnostic.json").write_text(
            json.dumps({
                "sensitive_local_debug": True,
                "capture_reason": "pre_submit_retention_validation_failed",
                "diagnostic": diagnostic,
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        page.screenshot(path=str(artifact_dir / "pre_submit.png"), full_page=True)
        try:
            (artifact_dir / "pre_submit.html").write_text(page.main_frame.content(), encoding="utf-8")
        except Exception:
            (artifact_dir / "pre_submit.html").write_text(
                "<!-- SENSITIVE_LOCAL_DEBUG: surface HTML unavailable -->\n", encoding="utf-8"
            )
        self._update_preview_manifest("artifacts_written")

    @staticmethod
    def _normalize_preview_text(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value)
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return re.sub(r"\s+", " ", normalized).strip().casefold()

    @classmethod
    def _classify_retention_selection_label(cls, label: str) -> str | None:
        return cls._preview_retention_diagnostic(label)["detected_retention_mode"]

    @staticmethod
    def _safe_selection_token(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        token = value.strip()
        if not token or len(token) > 50 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", token):
            return None
        return token

    def _retention_selection_inventory(self, page: Page) -> dict[str, object]:
        """Capture retention controls and semantic labels, excluding unrelated form data."""
        radios = page.locator('input[type="radio"][name="OptTipoRetencion"]')
        try:
            raw = radios.evaluate_all("""nodes => nodes.slice(0, 20).map((el, index) => {
              const form = el.form;
              const explicit = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
              const enclosing = el.closest('label');
              const parent = el.parentElement;
              const row = el.closest('tr');
              const sibling = el.nextElementSibling || el.previousElementSibling;
              const pick = explicit ? ['explicit_for', explicit.innerText || explicit.textContent || '']
                : enclosing ? ['enclosing_label', enclosing.innerText || enclosing.textContent || '']
                : sibling && (sibling.innerText || sibling.textContent || '').trim() ? ['sibling', sibling.innerText || sibling.textContent]
                : parent && (parent.innerText || '').trim() ? ['parent', parent.innerText]
                : row && (row.innerText || '').trim() ? ['row', row.innerText]
                : ['unknown', ''];
              const labelText = [pick[1], el.closest('td,th')?.innerText || '', row?.innerText || '']
                .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
              return {
                index, type: el.type || '', id: el.id || '', name: el.name || '', value: el.value || '',
                checked: !!el.checked, disabled: !!el.disabled, labelText, labelSource: pick[0],
                form: form ? {action: form.action || '', method: form.method || '',
                  fields: [...form.elements].filter(x => /retenc|retention|ppm/i.test((x.name || '') + ' ' + (x.id || ''))
                    || (x.type === 'radio' && x.name && x.name === el.name))
                    .slice(0, 20).map(x => ({type: x.type || x.tagName.toLowerCase(), name: x.name || '', id: x.id || '',
                      value: x.value || '', checked: typeof x.checked === 'boolean' ? !!x.checked : null,
                      disabled: !!x.disabled, label: x.labels ? [...x.labels].map(l => l.innerText || l.textContent || '').join(' ') : ''}))} : null,
                formHandler: form?.getAttribute('onsubmit') || '',
                continueHandler: [...(form?.querySelectorAll('button,input[type=submit],input[type=button]') || [])]
                  .concat([...document.querySelectorAll('button,input[type=submit],input[type=button]')]
                    .filter(x => /continuar/i.test(x.innerText || x.value || '')))
                  .map(x => x.getAttribute('onclick') || '').filter(Boolean).join(' ')
              };
            })""")
        except Exception:
            raw = []
            for index in range(min(radios.count(), 20)):
                radio = radios.nth(index)
                try:
                    raw.append({
                        "index": index, "type": radio.get_attribute("type") or "radio",
                        "id": radio.get_attribute("id") or "", "name": radio.get_attribute("name") or "",
                        "value": radio.get_attribute("value") or "", "checked": radio.is_checked(),
                        "disabled": radio.is_disabled(), "labelText": self._radio_context_text(page, radio),
                        "labelSource": "unknown", "form": None,
                    })
                except Exception:
                    continue

        options: list[dict[str, object]] = []
        all_handlers: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("labelText") or "")
            semantic = self._classify_retention_selection_label(label)
            if False and semantic is None and not re.search(
                r"retenci[oó]n|retencion|ppm|pago\s+provisional|emisor|receptor|contribuyente|retener",
                label, re.IGNORECASE,
            ):
                continue
            radio = {
                "index": int(item.get("index", 0)), "type": "radio",
                "name": self._safe_selection_token(item.get("name")),
                "id": self._safe_technical_attribute(item.get("id")),
                "value": self._safe_selection_value(item.get("value")),
                "checked": bool(item.get("checked")), "disabled": bool(item.get("disabled")),
            }
            fields = []
            form = item.get("form") if isinstance(item.get("form"), dict) else {}
            for field in form.get("fields", [])[:20]:
                if not isinstance(field, dict):
                    continue
                context = self._sanitize_retention_fragment(field.get("label"))
                fields.append({
                    "type": str(field.get("type") or "")[:20],
                    "name": self._safe_selection_token(field.get("name")),
                    "id": self._safe_technical_attribute(field.get("id")),
                    "value": self._safe_selection_value(field.get("value")),
                    "checked": field.get("checked") if isinstance(field.get("checked"), bool) else None,
                    "disabled": bool(field.get("disabled")),
                    "semantic_context": context[:MAX_RETENTION_TEXT_LENGTH],
                })
            action_path = self._sanitize_retention_fragment(urlsplit(str(form.get("action") or "")).path)
            form_info = {
                "action_path": action_path[:240],
                "method": str(form.get("method") or "").upper()[:10] or None,
                "fields": fields,
            }
            options.append({
                "radio": radio,
                "label": self._sanitize_retention_fragment(label),
                "label_source": str(item.get("labelSource") or "unknown")[:20],
                "semantic_mode": semantic,
                "selected_radio": bool(item.get("checked")),
                "form": form_info,
            })
            all_handlers.extend([str(item.get("formHandler") or ""), str(item.get("continueHandler") or "")])

        # Inspect inline handler text only in memory; persist names/field names, never source.
        retention_names = sorted({
            field["name"] for option in options for field in option["form"]["fields"]
            if field.get("name")
        })
        retention_field_ids = sorted({
            field["id"] for option in options for field in option["form"]["fields"]
            if field.get("id")
        })
        joined_handlers = " ".join(all_handlers)
        functions = re.findall(r"(?:function\s+)?([A-Za-z_$][\w$]*)\s*\(", joined_handlers)
        try:
            named_transform_functions = page.evaluate("""args => {
              return args.functionNames.map(name => {
                const fn = window[name];
                if (typeof fn !== 'function') return null;
                const source = Function.prototype.toString.call(fn);
                const affected = args.fieldNames.filter(field => source.includes(field));
                return affected.length ? {name, fields: affected} : null;
              }).filter(Boolean).slice(0, 10);
            }""", {"functionNames": functions[:10], "fieldNames": (retention_names + retention_field_ids)[:20]})
        except Exception:
            named_transform_functions = []
        direct_handler_fields = [
            name for name in retention_names
            if re.search(rf"\b{re.escape(name)}\b", joined_handlers, re.I)
        ]
        handler_mentions_retention = bool(direct_handler_fields)
        js_retention = handler_mentions_retention or bool(named_transform_functions)
        affected_field_names = sorted(set(direct_handler_fields).union(
            next((field["name"] for option in options for field in option["form"]["fields"]
                  if token in {field.get("name"), field.get("id")} and field.get("name")), token)
            for item in named_transform_functions if isinstance(item, dict)
            for token in item.get("fields", [])
        ))
        return {
            "options": options,
            "js": {
                "retention_transform_present": js_retention,
                "inline_handler_present": bool(joined_handlers),
                "function_names": list(dict.fromkeys(functions + [
                    item["name"] for item in named_transform_functions if isinstance(item, dict)
                ]))[:10],
                "affected_retention_field_names": affected_field_names[:10],
                "inspection_scope": "inline_handlers_and_named_window_functions",
            },
        }

    @staticmethod
    def _safe_selection_value(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        token = value.strip()
        if len(token) > 40 or not token or not re.fullmatch(r"[A-Za-z0-9_.:-]+", token):
            return None
        if any(pattern.search(token) for pattern in _SENSITIVE_DIAGNOSTIC_PATTERNS):
            return None
        if token.isdigit() and len(token) > 3:
            return None
        return token

    @classmethod
    def _preview_retention_matches(cls, body_text: str) -> dict[str, list[dict[str, str]]]:
        text = cls._normalize_preview_text(body_text)

        def matches(patterns: tuple[str, ...]) -> list[dict[str, str]]:
            result: list[dict[str, str]] = []
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    result.append({
                        "pattern": pattern,
                        "matched_text": cls._sanitize_retention_fragment(match.group(0)),
                    })
            return result

        return {
            "issuer_matches": matches(ISSUER_RETENTION_PATTERNS),
            "receiver_matches": matches(RECEIVER_RETENTION_PATTERNS),
        }

    @classmethod
    def _preview_retention_diagnostic(
        cls, body_text: str, *, include_matches: bool = False
    ) -> dict[str, object]:
        """Classify explicit semantic retention wording, failing closed on conflict."""
        matches = cls._preview_retention_matches(body_text)
        issuer_evidence = bool(matches["issuer_matches"])
        receiver_evidence = bool(matches["receiver_matches"])
        result: dict[str, object] = {}
        if include_matches:
            result.update({
                **matches,
                "classification_text_source": "frame.innerText",
                "classification_surface": "structural_preview_frame",
            })
        if issuer_evidence and receiver_evidence:
            return {**result, "detected_retention_mode": None, "retention_evidence_kind": "ambiguous_text"}
        if issuer_evidence:
            return {**result, "detected_retention_mode": RetentionMode.ISSUER_HANDLES_PPM.value, "retention_evidence_kind": "issuer_text"}
        if receiver_evidence:
            return {**result, "detected_retention_mode": RetentionMode.RECEIVER_RETAINS_PPM.value, "retention_evidence_kind": "receiver_text"}
        return {**result, "detected_retention_mode": None, "retention_evidence_kind": "none"}

    @staticmethod
    def _retention_mode_matches(requested: str | None, detected: object) -> bool:
        return requested is not None and detected == requested

    @staticmethod
    def _safe_technical_attribute(value: object) -> str | None:
        if not isinstance(value, str) or len(value) > 80:
            return None
        candidate = value.strip()
        if not candidate or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", candidate):
            return None
        return None if _NON_TECHNICAL_ATTR.search(candidate) else candidate

    @classmethod
    def _sanitize_retention_fragment(
        cls, value: object, sensitive_values: tuple[str, ...] = ()
    ) -> str:
        if not isinstance(value, str):
            return ""
        text = re.sub(r"\s+", " ", value).strip()
        known_values = sorted(
            {item.strip() for item in sensitive_values if isinstance(item, str) and len(item.strip()) >= 2},
            key=len,
            reverse=True,
        )
        for sensitive in known_values:
            pattern = re.escape(sensitive)
            if re.fullmatch(r"[\w]+", sensitive, flags=re.UNICODE):
                pattern = rf"(?<!\w){pattern}(?!\w)"
            text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
        for pattern in _SENSITIVE_DIAGNOSTIC_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text[:MAX_RETENTION_TEXT_LENGTH]

    def _retention_frame_probe(self, frame, index: int, page: Page) -> dict[str, object]:
        """Collect bounded local retention fragments; never return document markup."""
        body = frame.locator("body")
        try:
            probe = body.evaluate(
            """body => {
              const term = /retenci[oó]n|ppm|pago\\s+provisional|emisor|receptor|contribuyente|porcentaje|retener|retendr[aá]/i;
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const elements = [...body.querySelectorAll('*')];
              const hasDraft = /borrador[\\s\\S]*no\\s+tiene[\\s\\S]*ninguna\\s+validez/i.test(body.innerText || '');
              const hasFinal = [...body.querySelectorAll('button,input[type=button],input[type=submit]')]
                .some(node => /emitir\\s+boleta\\s+de\\s+honorarios\\s+electr[oó]nica/i.test(node.innerText || node.value || ''));
              const raw = [];
              for (const el of elements) {
                const ownText = clean([...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.nodeValue).join(' '));
                const aria = clean(el.getAttribute('aria-label'));
                const labels = el.labels ? [...el.labels].map(label => clean(label.innerText)).filter(Boolean) : [];
                const label = el.closest('label');
                if (label) labels.push(clean(label.innerText));
                const candidateText = [ownText, aria, ...labels].filter(Boolean).join(' ');
                if (!term.test(candidateText)) continue;
                const parentText = el.parentElement ? clean([...el.parentElement.childNodes]
                  .filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.nodeValue).join(' ')) : '';
                const relevantLabel = labels.find(text => term.test(text)) || '';
                const controls = [];
                const related = [];
                if (el.matches('input,select,textarea')) related.push(el);
                related.push(...el.querySelectorAll('input,select,textarea'));
                for (const control of related.slice(0, 4)) {
                  const controlLabels = control.labels ? [...control.labels].map(node => clean(node.innerText)) : [];
                  if (!term.test(controlLabels.join(' ')) && !term.test(candidateText)) continue;
                  const type = (control.type || control.tagName || '').toLowerCase();
                  const item = {type, checked: null, selected: null, value: null};
                  if (type === 'radio' || type === 'checkbox') item.checked = !!control.checked;
                  if (control.tagName === 'SELECT') {
                    item.selected = !!control.selectedOptions?.length;
                    item.value = control.value || '';
                  } else if ((type === 'radio' || type === 'checkbox') && control.value) item.value = control.value;
                  controls.push(item);
                }
                raw.push({
                  tag: el.tagName.toLowerCase(),
                  class_name: el.className && typeof el.className === 'string' ? el.className : null,
                  visible: !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'),
                  input_type: (el.type || '').toLowerCase() || null,
                  id: el.id || null,
                  name: el.getAttribute('name'),
                  local_text: ownText || aria || relevantLabel,
                  parent_local_text: parentText,
                  labels,
                  controls
                });
              }
              return {
                has_draft_marker: hasDraft,
                has_final_emit_control: hasFinal,
                retention_candidate_count: raw.length,
                candidates: raw.slice(0, 20)
              };
            }"""
            )
        except AttributeError:
            # Lightweight test doubles expose only inner_text; real Playwright uses evaluate above.
            text = body.inner_text(timeout=self.settings.timeout_ms)
            probe = {
                "body_text": text,
                "has_draft_marker": bool(re.search(r"borrador.*no\s+tiene.*ninguna\s+validez", text, re.I | re.S)),
                "has_final_emit_control": index == 0 and self._final_emit_button(page) is not None,
                "retention_candidate_count": 0,
                "candidates": [],
            }
        body_text = body.inner_text(timeout=self.settings.timeout_ms)
        parts = urlsplit(frame.url)
        hostname = parts.hostname or ""
        path = parts.path or "/"
        path = re.sub(r"(?<=/)(?:\d[\d.-]{4,}|[0-9a-f]{16,})(?=/|$)", "[redacted]", path, flags=re.I)
        has_draft = bool(re.search(r"borrador.*no\s+tiene.*ninguna\s+validez", body_text, re.I | re.S))
        # Preserve the existing main-page final-control predicate used by the decision gate.
        has_final = self._final_emit_button(page) is not None if index == 0 else bool(probe.get("has_final_emit_control"))
        bhe_path = bool(re.search(r"boleta.*honorarios|tmbecn", path, re.I))
        structural = (index == 0 and has_draft and has_final) or (index > 0 and has_draft and bhe_path)
        return {
            "index": index,
            "semantic_frame_kind": "main_page" if index == 0 else "child_frame",
            "hostname": hostname[:120],
            "path_without_query": path[:240],
            "structural_bhe_preview": structural,
            "has_final_emit_control": has_final,
            "has_draft_marker": has_draft,
            "retention_candidate_count": int(probe.get("retention_candidate_count") or 0),
            "body_text": body_text,
            "candidates": probe.get("candidates") or [],
        }

    @classmethod
    def _sanitize_retention_candidate(
        cls, candidate: dict[str, object], frame_index: int,
        semantic_frame_kind: str, sensitive_values: tuple[str, ...],
    ) -> dict[str, object]:
        controls = []
        for control in candidate.get("controls", [])[:4]:
            if not isinstance(control, dict):
                continue
            controls.append({
                "input_type": str(control.get("type") or "")[:24],
                "checked": control.get("checked") if isinstance(control.get("checked"), bool) else None,
                "selected": control.get("selected") if isinstance(control.get("selected"), bool) else None,
                "value": cls._sanitize_retention_fragment(control.get("value"), sensitive_values) or None,
            })
        labels = [
            cls._sanitize_retention_fragment(label, sensitive_values)
            for label in candidate.get("labels", [])[:4]
        ]
        return {
            "frame_index": frame_index,
            "semantic_frame_kind": semantic_frame_kind,
            "tag_name": str(candidate.get("tag") or "")[:24],
            "input_type": str(candidate.get("input_type") or "")[:24] or None,
            "name": cls._safe_technical_attribute(candidate.get("name")),
            "id": cls._safe_technical_attribute(candidate.get("id")),
            "class_name": cls._safe_dom_class(candidate.get("class_name")),
            "visible": candidate.get("visible") if isinstance(candidate.get("visible"), bool) else None,
            "local_text": cls._sanitize_retention_fragment(candidate.get("local_text"), sensitive_values),
            "parent_local_text": cls._sanitize_retention_fragment(candidate.get("parent_local_text"), sensitive_values),
            "labels": [label for label in labels if label],
            "controls": controls,
            "relation": "element_and_immediate_parent_text",
        }

    @staticmethod
    def _safe_dom_class(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:120] if value and re.fullmatch(r"[A-Za-z0-9_ .:-]+", value) else None

    @classmethod
    def _attach_retention_match_context(
        cls,
        classification: dict[str, object],
        probe: dict[str, object],
        frame_index: int,
        semantic_frame_kind: str,
        sensitive_values: tuple[str, ...],
    ) -> dict[str, list[dict[str, object]]]:
        candidates = probe.get("candidates") or []
        output: dict[str, list[dict[str, object]]] = {"issuer_matches": [], "receiver_matches": []}
        for key in output:
            for match in classification.get(key, []):
                matched_text = str(match.get("matched_text") or "")
                match_norm = cls._normalize_preview_text(matched_text)
                element = next((candidate for candidate in candidates if match_norm and match_norm in cls._normalize_preview_text(
                    " ".join(str(candidate.get(field) or "") for field in ("local_text", "parent_local_text", "labels"))
                )), None)
                item: dict[str, object] = {
                    "frame_index": frame_index,
                    "semantic_frame_kind": semantic_frame_kind,
                    "pattern": match.get("pattern"),
                    "matched_text": cls._sanitize_retention_fragment(matched_text, sensitive_values),
                    "visible": element.get("visible") if isinstance(element, dict) and isinstance(element.get("visible"), bool) else None,
                    "element_tag": element.get("tag") if isinstance(element, dict) else None,
                    "element_id": cls._safe_technical_attribute(element.get("id")) if isinstance(element, dict) else None,
                    "element_name": cls._safe_technical_attribute(element.get("name")) if isinstance(element, dict) else None,
                    "element_class": cls._safe_dom_class(element.get("class_name")) if isinstance(element, dict) else None,
                }
                output[key].append(item)
        return output

    def _preview_retention_diagnostic_for_page(
        self, page: Page, sensitive_values: tuple[str, ...] = ()
    ) -> dict[str, object]:
        """Decide from valid preview surfaces and separately inventory all frame evidence."""
        frame_inventory: list[dict[str, object]] = []
        probes: list[dict[str, object]] = []
        surfaces: list[tuple[int, str]] = []
        frames = list(page.frames)
        for index, frame in enumerate(frames):
            try:
                probe = self._retention_frame_probe(frame, index, page)
                text = str(probe["body_text"])
            except Exception:
                parts = urlsplit(frame.url)
                probe = {
                    "index": index,
                    "semantic_frame_kind": "main_page" if index == 0 else "child_frame",
                    "hostname": parts.hostname or "",
                    "path_without_query": parts.path or "/",
                    "structural_bhe_preview": False,
                    "has_final_emit_control": False,
                    "has_draft_marker": False,
                    "retention_candidate_count": 0,
                    "candidates": [],
                }
                text = ""
            probes.append(probe)
            frame_inventory.append({key: value for key, value in probe.items() if key not in {"body_text", "candidates"}})
            if probe["structural_bhe_preview"]:
                surfaces.append((index, text))

        classifications: list[tuple[int, dict[str, object]]] = []
        if not surfaces:
            reason = "frame_expected_not_found" if len(frames) > 1 else "preview_structure_unexpected"
            mode, kind = None, reason
            matching_frames: list[int] = []
        else:
            classifications = []
            for frame_index, text in surfaces:
                classification = self._preview_retention_diagnostic(text, include_matches=True)
                probe = next(item for item in probes if item["index"] == frame_index)
                context = self._attach_retention_match_context(
                    classification, probe, frame_index, str(probe["semantic_frame_kind"]), sensitive_values
                )
                classification["issuer_matches"] = context["issuer_matches"]
                classification["receiver_matches"] = context["receiver_matches"]
                classifications.append((frame_index, classification))
            detected = {item["detected_retention_mode"] for _, item in classifications if item["detected_retention_mode"]}
            evidence = {item["retention_evidence_kind"] for _, item in classifications if item["retention_evidence_kind"] != "none"}
            if len(detected) > 1 or "ambiguous_text" in evidence:
                mode, kind, reason = None, "ambiguous_text", "both_retention_modes_found"
            elif detected:
                mode = next(iter(detected))
                kind, reason = next(iter(evidence)), "retention_mode_detected"
            else:
                mode, kind, reason = None, "none", "no_retention_evidence_found"
            matching_frames = [idx for idx, item in classifications if item["detected_retention_mode"]]

        diagnostic: dict[str, object] = {
            "detected_retention_mode": mode,
            "retention_evidence_kind": kind,
            "preview_frame_index": matching_frames[0] if len(matching_frames) == 1 else None,
            "diagnostic_reason": reason,
            "diagnostic_version": RETENTION_DIAGNOSTIC_VERSION,
            "preview_frame_inventory": frame_inventory,
            "decision_surface": [
                {key: value for key, value in item.items() if key in {
                    "index", "semantic_frame_kind", "hostname", "path_without_query",
                    "structural_bhe_preview", "has_final_emit_control", "has_draft_marker",
                }}
                for item in frame_inventory if item["structural_bhe_preview"]
            ],
            "classification_text_source": "structural preview frame.innerText",
            "issuer_matches": [match for _, item in classifications for match in item.get("issuer_matches", [])],
            "receiver_matches": [match for _, item in classifications for match in item.get("receiver_matches", [])],
        }
        if mode is not None:
            selected_key = "issuer_matches" if mode == RetentionMode.ISSUER_HANDLES_PPM.value else "receiver_matches"
            selected = diagnostic[selected_key]
            first = selected[0] if selected else {}
            diagnostic.update({
                "preview_retention_detected_mode": mode,
                "preview_retention_evidence_type": kind,
                "preview_retention_matched_pattern": first.get("pattern"),
                "preview_retention_matched_text": first.get("matched_text"),
                "preview_retention_frame_index": first.get("frame_index"),
                "preview_retention_element_tag": first.get("element_tag"),
                "preview_retention_element_id": first.get("element_id"),
                "preview_retention_element_name": first.get("element_name"),
                "preview_retention_element_class": first.get("element_class"),
                "preview_retention_match_visible": first.get("visible"),
            })
        else:
            diagnostic.update({
                "preview_retention_detected_mode": None,
                "preview_retention_evidence_type": kind,
                "preview_retention_matched_pattern": None,
                "preview_retention_matched_text": None,
                "preview_retention_frame_index": None,
                "preview_retention_element_tag": None,
                "preview_retention_element_id": None,
                "preview_retention_element_name": None,
                "preview_retention_element_class": None,
                "preview_retention_match_visible": None,
            })
        if mode is None or kind == "ambiguous_text":
            candidates = []
            for probe in probes:
                for candidate in probe["candidates"]:
                    if len(candidates) >= MAX_RETENTION_CANDIDATES:
                        break
                    candidates.append(self._sanitize_retention_candidate(
                        candidate, int(probe["index"]), str(probe["semantic_frame_kind"]), sensitive_values
                    ))
            diagnostic["retention_candidates"] = candidates
        return diagnostic

    def _capture_retention_failure_debug(self, page: Page, diagnostic: dict[str, object]) -> None:
        """Backward-compatible failure capture entry point."""
        self._capture_preview_debug(page, diagnostic, capture_reason="preview_retention_mode_ok=false")

    def _capture_preview_debug(
        self,
        page: Page,
        diagnostic: dict[str, object],
        *,
        capture_reason: str,
    ) -> None:
        """Capture bounded per-attempt evidence only when explicitly enabled."""
        if not self.settings.diagnostics_enabled or not self._diagnostic_context:
            return
        item_id = self._diagnostic_context.get("batch_item_id", 0)
        emission_id = self._diagnostic_context.get("emission_id", 0)
        if self._diagnostic_artifact_dir is not None:
            artifact_dir = self._diagnostic_artifact_dir
        else:
            artifact_dir = self.settings.debug_dir / f"item_{item_id}_emission_{emission_id}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "sensitive_local_debug": True,
            "capture_reason": capture_reason,
            "batch_item_id": item_id,
            "emission_id": emission_id,
            "diagnostic": diagnostic,
            "artifacts": {
                "screenshot": "preview.png",
                "surface_html": "preview.html",
                "frame_metadata": "diagnostic.json",
            },
        }
        (artifact_dir / "diagnostic.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        page.screenshot(path=str(artifact_dir / "preview.png"), full_page=True)
        frame_index = diagnostic.get("preview_frame_index")
        frame = next((candidate for candidate in page.frames if candidate == page.main_frame), page.main_frame)
        if isinstance(frame_index, int) and 0 <= frame_index < len(page.frames):
            frame = page.frames[frame_index]
        try:
            (artifact_dir / "preview.html").write_text(frame.content(), encoding="utf-8")
        except Exception:
            (artifact_dir / "preview.html").write_text(
                "<!-- SENSITIVE_LOCAL_DEBUG: surface HTML unavailable -->\n", encoding="utf-8"
            )
        self._update_preview_manifest("artifacts_written")

    def _find_retention_option(self, page: Page, mode: RetentionMode):
        patterns = {
            RetentionMode.RECEIVER_RETAINS_PPM: re.compile(
                r"el\s+receptor\s+de\s+la\s+boleta.*ret.*pago\s+provisional\s+mensual",
                re.IGNORECASE | re.DOTALL,
            ),
            RetentionMode.ISSUER_HANDLES_PPM: re.compile(
                r"el\s+contribuyente\s+emisor.*ret.*pago\s+provisional\s+mensual",
                re.IGNORECASE | re.DOTALL,
            ),
        }
        option_pattern = patterns[mode]
        real_radios = page.locator('input[type="radio"][name="OptTipoRetencion"]')
        for index in range(real_radios.count()):
            radio = real_radios.nth(index)
            if option_pattern.search(self._radio_context_text(page, radio)):
                return radio
        labeled = page.get_by_label(option_pattern, exact=False).first
        if labeled.count() > 0:
            return labeled

        radios = page.locator("input[type='radio']")
        for index in range(radios.count()):
            radio = radios.nth(index)
            if option_pattern.search(self._radio_context_text(page, radio)):
                return radio
        return None

    @staticmethod
    def _radio_context_text(page: Page, radio) -> str:
        try:
            return radio.evaluate("""el => {
              const explicit = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
              const enclosing = el.closest('label');
              const parent = el.parentElement;
              const row = el.closest('tr');
              const sibling = el.nextElementSibling || el.previousElementSibling;
              const node = explicit || enclosing || parent || row || sibling;
              return node ? (node.innerText || node.textContent || '') : '';
            }""")
        except Exception:
            radio_id = radio.get_attribute("id") or ""
            if radio_id:
                label = page.locator(f"label[for='{radio_id}']").first
                if label.count() > 0:
                    return label.inner_text()
            parent = radio.locator("..")
            return parent.inner_text() if parent.count() > 0 else ""

    @staticmethod
    def _bhe_link(page: Page):
        link = page.get_by_role(
            "link", name=re.compile(r"boletas?\s+de\s+honorarios", re.IGNORECASE)
        ).first
        if link.count() == 0:
            link = page.get_by_text(
                re.compile(r"boletas?\s+de\s+honorarios", re.IGNORECASE)
            ).first
        return link

    def _detect_state_legacy(self, page: Page) -> SiiNavigationState:
        body_text = page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        controls = page.locator("input, textarea, select").count()
        if re.search(r"modalidad|retenci[oó]n", body_text, re.IGNORECASE) and controls:
            return SiiNavigationState.RETENTION_SELECTION
        if re.search(r"receptor|detalle|fecha\s+de\s+emisi[oó]n", body_text, re.IGNORECASE) and controls:
            return SiiNavigationState.BHE_FORM
        if self._has_emitter_menu_evidence(body_text):
            return SiiNavigationState.BHE_EMITTER_MENU
        if self._has_bhe_menu_page_evidence(page):
            return SiiNavigationState.BHE_MENU
        if self.is_authenticated(page):
            return SiiNavigationState.AUTHENTICATED
        if self._LOGIN_MARKERS.search(body_text):
            return SiiNavigationState.LOGIN
        return SiiNavigationState.UNKNOWN

    @staticmethod
    def _has_emitter_menu_evidence(body_text: str) -> bool:
        return bool(
            re.search(r"emisor\s+de\s+boleta\s+de\s+honorarios", body_text, re.IGNORECASE)
            and re.search(r"por\s+contribuyente", body_text, re.IGNORECASE)
        )

    @staticmethod
    def _has_bhe_menu_evidence(body_text: str) -> bool:
        return bool(re.search(r"boleta(?:s)?\s+de\s+honorarios", body_text, re.IGNORECASE))

    @classmethod
    def _has_bhe_evidence(cls, body_text: str) -> bool:
        return cls._has_emitter_menu_evidence(body_text) or cls._has_bhe_menu_evidence(body_text)

    def detect_state(self, page: Page) -> SiiNavigationState:
        """Classify specific BHE evidence before generic login evidence."""
        body_text = page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        controls = page.locator("input, textarea, select").count()
        if self.is_bhe_preview(page):
            return SiiNavigationState.BHE_PREVIEW
        if self._has_retention_evidence(body_text) and controls:
            return SiiNavigationState.RETENTION_SELECTION
        if self._has_emitter_menu_evidence(body_text):
            return SiiNavigationState.BHE_EMITTER_MENU
        if self._has_bhe_menu_page_evidence(page):
            return SiiNavigationState.BHE_MENU
        if self.is_bhe_form(page) and controls:
            return SiiNavigationState.BHE_FORM
        if self.is_authenticated(page):
            return SiiNavigationState.AUTHENTICATED
        if self._LOGIN_MARKERS.search(body_text):
            return SiiNavigationState.LOGIN
        return SiiNavigationState.UNKNOWN

    @classmethod
    def _has_strict_bhe_form_evidence(cls, page: Page, body_text: str) -> bool:
        """Require form-specific evidence; generic site controls are insufficient."""
        if cls._has_retention_evidence(body_text):
            return False
        if cls._has_emitter_menu_evidence(body_text) or cls._has_bhe_menu_page_evidence(page):
            return False
        if not re.search(r"emisi[oó]n.*boleta.*honorarios", body_text, re.IGNORECASE | re.DOTALL):
            return False

        labels = " ".join(page.locator("label:visible").all_inner_texts())
        aria_labels = " ".join(
            page.locator("input[aria-label]:visible, textarea[aria-label]:visible").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('aria-label') || '')"
            )
        )
        field_text = f"{labels} {aria_labels}"
        field_groups = (
            r"rut.*receptor|receptor|destinatario",
            r"domicilio|direcci[oó]n|comuna|regi[oó]n",
            r"detalle|prestaci[oó]n",
            r"monto|valor|honorario",
        )
        return sum(bool(re.search(group, field_text, re.IGNORECASE)) for group in field_groups) >= 3

    @staticmethod
    def _has_bhe_menu_page_evidence(page: Page) -> bool:
        link = page.get_by_role(
            "link", name=re.compile(r"emitir\s+boleta\s+de\s+honorarios", re.IGNORECASE)
        ).first
        return link.count() > 0

    @classmethod
    def _has_conservative_bhe_form_evidence(cls, page: Page, body_text: str) -> bool:
        """Conservative form detector used until the real post-retention DOM is mapped."""
        if cls._has_retention_evidence(body_text):
            return False
        if cls._has_emitter_menu_evidence(body_text) or cls._has_bhe_menu_page_evidence(page):
            return False
        if not re.search(r"emisi.*boleta.*honorario", body_text, re.IGNORECASE | re.DOTALL):
            return False

        labels = " ".join(page.locator("label:visible").all_inner_texts())
        aria_labels = " ".join(
            page.locator("input[aria-label]:visible, textarea[aria-label]:visible").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('aria-label') || '')"
            )
        )
        field_text = f"{labels} {aria_labels}"
        field_groups = (
            r"rut.*receptor|receptor|destinatario",
            r"domicilio|direcci|comuna|regi",
            r"detalle|prestaci",
            r"monto|valor|honorario",
        )
        return sum(bool(re.search(group, field_text, re.IGNORECASE)) for group in field_groups) >= 3

    @staticmethod
    def _has_retention_evidence(body_text: str) -> bool:
        return bool(
            re.search(r"receptor\s+de\s+la\s+boleta.*ret", body_text, re.IGNORECASE | re.DOTALL)
            or re.search(r"contribuyente\s+emisor.*retenci", body_text, re.IGNORECASE | re.DOTALL)
        )

    @staticmethod
    def _has_bhe_form_evidence(body_text: str) -> bool:
        groups = (
            r"receptor|destinatario|rut\s+(?:del\s+)?receptor",
            r"fecha\s+de\s+emisi[oó]n",
            r"detalle|prestaci[oó]n|servicio",
            r"monto|valor|honorario",
        )
        return sum(bool(re.search(group, body_text, re.IGNORECASE)) for group in groups) >= 2

    def _navigation_page_score(self, page: Page) -> tuple[int, str]:
        """Choose a popup by BHE evidence, never by list position alone."""
        path = urlsplit(page.url).path.lower()
        if "tmbecn_validatimbrajecontrib" in path:
            return 100, "known_retention_path"
        if "tmbecn_presentadatosboleta" in path:
            return 100, "known_bhe_form_path"
        try:
            state = self.detect_state(page)
        except Exception:
            state = SiiNavigationState.UNKNOWN
        if state in {
            SiiNavigationState.RETENTION_SELECTION,
            SiiNavigationState.BHE_FORM,
            SiiNavigationState.BHE_PREVIEW,
        }:
            return 80, f"structural_state_{state.value}"
        if page.url.lower().startswith("https://loa.sii.cl/cgi_imt/"):
            return 40, "known_sii_bhe_host"
        return 0, "no_bhe_evidence"

    def _write_navigation_inventory(self, step: str, selected: Page | None, reason: str) -> None:
        if not self._context:
            return
        lines = [
            f"navigation_step={step}",
            f"page_count={len(self._context.pages)}",
            f"active_page_selected={self._context.pages.index(selected) if selected in self._context.pages else 'none'}",
            f"active_page_reason={reason}",
        ]
        for index, page in enumerate(self._context.pages):
            url = urlsplit(page.url)
            try:
                title_present = bool(page.title().strip())
            except Exception:
                title_present = False
            lines.append(
                f"page[{index}].host={url.hostname or ''};path={url.path or '/'};"
                f"title_present={'YES' if title_present else 'NO'};closed={'YES' if page.is_closed() else 'NO'}"
            )
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / f"navigation-{step}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        print(f"SII_PAGE_COUNT={len(self._context.pages)}")
        print(f"SII_ACTIVE_PAGE_SELECTED={lines[2].split('=', 1)[1]}")
        print(f"SII_ACTIVE_PAGE_REASON={reason}")

    def _click_navigation(self, page: Page, locator, step: str) -> Page:
        self._assert_not_final_emit_control(page, locator)
        pages_before = set(self._context.pages if self._context else [])
        wait_timeout_ms = 1_000
        try:
            with self._context.expect_page(timeout=wait_timeout_ms) as page_info:  # type: ignore[union-attr]
                if step == "confirm-emission":
                    self._snapshot_retention_trace(page, "T2_before_click")
                locator.click()
            next_page = page_info.value
            next_page.set_default_timeout(self.settings.timeout_ms)
            next_page.wait_for_load_state("domcontentloaded")
            selection_reason = "expect_page_result"
        except PlaywrightTimeoutError:
            next_page = page
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except PlaywrightTimeoutError:
                pass
            # Some SII links create the popup just after the expect_page window.
            # Give the browser event loop a short, bounded settle period before
            # classifying the navigation result.
            if self._context:
                page.wait_for_timeout(1_000)
        pages_after = set(self._context.pages if self._context else [])
        additional_pages = pages_after - pages_before
        if next_page is page and additional_pages:
            ranked = sorted(
                ((self._navigation_page_score(candidate), candidate) for candidate in additional_pages),
                key=lambda entry: entry[0][0],
                reverse=True,
            )
            score, candidate = ranked[0]
            if score[0] == 0:
                candidate.wait_for_timeout(300)
                score = self._navigation_page_score(candidate)
            if score[0] > 0:
                next_page = candidate
                next_page.set_default_timeout(self.settings.timeout_ms)
                try:
                    next_page.wait_for_load_state("domcontentloaded", timeout=3_000)
                except PlaywrightTimeoutError:
                    pass
                selection_reason = score[1]
            else:
                selection_reason = "popup_without_bhe_evidence"
        elif next_page is page:
            selection_reason = "same_page_fallback"
        self._write_navigation_inventory(step, next_page, selection_reason)
        print(f"SII navigation step={step}; active_url={self._safe_url(next_page.url)}")
        if additional_pages:
            print(f"SII navigation opened {len(additional_pages)} additional page(s).")
        print(f"SII_NAVIGATION_WAIT_OPERATION=context.expect_page")
        print(f"SII_NAVIGATION_WAIT_TARGET={step}")
        print(f"SII_NAVIGATION_WAIT_TIMEOUT_MS={wait_timeout_ms}")
        return next_page

    @staticmethod
    def _count_folio_candidates(text: str) -> int:
        return len(re.findall(r"(?:folio|n[º°o]\s*(?:de\s+)?boleta|n[uú]mero\s+de\s+boleta)", text or "", re.IGNORECASE))

    @staticmethod
    def _count_contextual_folio_candidates(text: str) -> int:
        normalized = _normalize_final_result_text(text)
        return sum(
            1 if re.search(
                r"\bn\s*(?:[º°o]\s*)?\d{1,12}\b",
                normalized[header.end():header.end() + 160].casefold().split(" res. ex.", 1)[0],
                re.IGNORECASE,
            ) else 0
            for header in re.finditer(r"boleta\s+de\s+honorarios\s+electronica", normalized, re.IGNORECASE)
        )

    @staticmethod
    def _extract_final_barcode(page: Page) -> str | None:
        locator = page.locator("input[name='txt_codigo_barra']")
        if not locator.count():
            return None
        value = locator.first.get_attribute("value")
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _has_post_emission_controls(page: Page) -> bool:
        main_frame = page.main_frame
        expected = ("cmdimprimiroriginal", "cmdenviar", "cmdemitir1", "cmdemitir2")
        return all(main_frame.locator(f"[name='{name}']").count() > 0 for name in expected)

    @staticmethod
    def _match_final_retention_mode(
        surfaces: list[SiiFinalResultSurface], retention_mode: RetentionMode
    ) -> bool | None:
        text = _normalize_final_result_text(" ".join(surface.text for surface in surfaces))
        issuer_evidence = bool(re.search(r"(?:contribuyente|emisor).*?(?:declarar|enterar).*?ppm|emisor.*?retencion.*?ppm", text, re.IGNORECASE))
        receiver_evidence = bool(re.search(r"receptor.*?(?:retien|retencion).*?ppm", text, re.IGNORECASE))
        if retention_mode is RetentionMode.ISSUER_HANDLES_PPM:
            return True if issuer_evidence else False if receiver_evidence else None
        if retention_mode is RetentionMode.RECEIVER_RETAINS_PPM:
            return True if receiver_evidence else False if issuer_evidence else None
        return None

    def collect_final_result_surfaces(self, page: Page) -> list[SiiFinalResultSurface]:
        """Collect accessible main/child-frame bodies for final-result parsing."""
        surfaces: list[SiiFinalResultSurface] = []
        print(f"SII_FINAL_FRAME_COUNT={len(page.frames)}")
        for index, frame in enumerate(page.frames):
            try:
                text = frame.locator("body").inner_text(timeout=self.settings.timeout_ms)
            except Exception:
                print(f"SII_FINAL_FRAME_{index}_ACCESSIBLE=NO")
                continue
            surfaces.append(SiiFinalResultSurface(index, self._safe_url(frame.url), text))
            print(f"SII_FINAL_FRAME_{index}_ACCESSIBLE=YES")
            print(f"SII_FINAL_FRAME_{index}_URL_PATH={urlsplit(frame.url).path or '/'}")
            print(f"SII_FINAL_FRAME_{index}_BODY_LENGTH={len(text)}")
            print(f"SII_FINAL_FRAME_{index}_FOLIO_CANDIDATE_COUNT={self._count_contextual_folio_candidates(text)}")
        return surfaces

    @staticmethod
    def _find_result_control(page: Page, pattern: str) -> tuple[int, str, str] | None:
        """Find a result control across frames and return safe structural metadata."""
        selector = "a:visible, button:visible, input[type='button']:visible, input[type='submit']:visible"
        for frame_index, frame in enumerate(page.frames):
            controls = frame.locator(selector)
            for index in range(controls.count()):
                control = controls.nth(index)
                tag = control.evaluate("node => node.tagName.toLowerCase()")
                label = (control.inner_text() if tag != "input" else control.get_attribute("value")) or ""
                if not re.search(pattern, label, re.IGNORECASE):
                    continue
                if tag == "a":
                    action_kind = "javascript" if (control.get_attribute("href") or "").strip().lower().startswith("javascript:") else "link"
                elif tag == "button":
                    action_kind = "button"
                else:
                    action_kind = "form-submit"
                return frame_index, tag, action_kind
        return None

    @staticmethod
    def _has_result_control(page: Page, pattern: str) -> bool:
        return SiiBrowserService._find_result_control(page, pattern) is not None

    def _capture_post_emission_debug(self, page: Page) -> None:
        """Capture the post-click page locally; never expose it in API/log output."""
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.settings.debug_dir / "after-final-emission"
        page.screenshot(path=str(prefix.with_suffix(".png")), full_page=True)
        prefix.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        for frame_index, frame in enumerate(page.frames[1:], start=1):
            try:
                (self.settings.debug_dir / f"after-final-emission-frame-{frame_index}.html").write_text(
                    frame.content(), encoding="utf-8"
                )
            except Exception:
                # A detached/cross-origin frame is still represented in the
                # safe metadata below, but must not abort diagnostics.
                continue
        forms = page.locator("form").all()
        links = page.locator("a").all()
        buttons = page.locator("button, input[type='button'], input[type='submit']").all()
        inputs = page.locator("input, textarea, select").all()
        headings = page.locator("h1, h2, h3, h4, h5, h6").all()
        lines = [
            f"url={self._safe_url(page.url)}",
            f"title={self._safe_text(page.title())}",
            f"body_length={len(page.locator('body').inner_text())}",
            f"form_count={len(forms)}",
            f"link_count={len(links)}",
            f"button_count={len(buttons)}",
            f"frame_count={len(page.frames)}",
            f"heading_count={len(headings)}",
            f"body_structural_text={self._safe_text(page.locator('body').inner_text())}",
        ]
        for frame_index, frame in enumerate(page.frames):
            try:
                frame_body = frame.locator("body").inner_text(timeout=2_000)
                frame_body_length = len(frame_body)
                frame_form_count = frame.locator("form").count()
                frame_link_count = frame.locator("a").count()
                frame_button_count = frame.locator("button, input[type='button'], input[type='submit']").count()
                accessible = "YES"
            except Exception:
                frame_body_length = 0
                frame_form_count = frame_link_count = frame_button_count = 0
                accessible = "NO"
            lines.extend([
                f"frame={frame_index};accessible={accessible};url={self._safe_url(frame.url)}",
                f"frame={frame_index};body_length={frame_body_length};form_count={frame_form_count};link_count={frame_link_count};button_count={frame_button_count}",
            ])
        for index, heading in enumerate(headings):
            lines.append(f"heading={index};text={self._safe_text(heading.inner_text())}")
        for index, form in enumerate(forms):
            lines.append(f"form={index};id={form.get_attribute('id') or ''};name={form.get_attribute('name') or ''}")
        for index, control in enumerate(buttons):
            tag = control.evaluate("node => node.tagName.toLowerCase()")
            label = control.get_attribute("value") if tag == "input" else control.inner_text()
            lines.append(f"button={index};tag={tag};text={self._safe_text(label or '')}")
        for index, control in enumerate(links):
            lines.append(f"link={index};text={self._safe_text(control.inner_text())};href={self._safe_url(control.get_attribute('href') or '')}")
        for index, control in enumerate(inputs):
            lines.append(f"control={index};tag={control.evaluate('node => node.tagName.toLowerCase()')};type={control.get_attribute('type') or ''};name={control.get_attribute('name') or ''};id={control.get_attribute('id') or ''}")
        prefix.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("SII_FINAL_RESULT_URL_HOST=" + (urlsplit(page.url).hostname or ""))
        print("SII_FINAL_RESULT_URL_PATH=" + (urlsplit(page.url).path or "/"))
        print("SII_FINAL_RESULT_TITLE=" + self._safe_text(page.title()))
        print(f"SII_FINAL_RESULT_BODY_LENGTH={len(page.locator('body').inner_text())}")
        print(f"SII_FINAL_RESULT_FORM_COUNT={len(forms)}")
        print(f"SII_FINAL_RESULT_LINK_COUNT={len(links)}")
        print(f"SII_FINAL_RESULT_BUTTON_COUNT={len(buttons)}")
        print(f"SII_FINAL_RESULT_FRAME_COUNT={len(page.frames)}")
        print("SII_FINAL_RESULT_DEBUG_CAPTURED=YES")

    def _assert_not_final_emit_control(self, page: Page, locator) -> None:
        """Block only the final control while the page is the real preview."""
        if not self.is_bhe_preview(page):
            return
        try:
            tag = locator.evaluate("node => node.tagName.toLowerCase()")
            text = (
                locator.get_attribute("value")
                if tag == "input"
                else locator.inner_text()
            ) or ""
        except Exception:
            return
        if re.fullmatch(
            r"emitir\s+boleta\s+de\s+honorarios\s+electr[oó]nica",
            re.sub(r"\s+", " ", text).strip(),
            re.IGNORECASE,
        ):
            raise SiiNavigationError(
                "Final SII emission control is blocked by the POC safety boundary."
            )

    def _handle_bhe_form(self, page: Page) -> SiiNavigationState:
        automation = SIIBHEAutomationService(self)
        self._save_bhe_form_fields(page)
        mapping = automation.map_form(page)
        data = BHEFormData.from_environment()
        if data is None:
            print("BHE_FORM_MAPPED=" + ("YES" if mapping.required_ready else "NO"))
            missing = ", ".join(BHEFormData.missing_environment_fields())
            print("BHE_FORM_FILLED=NO")
            print(f"Missing POC fields: {missing}")
            print(f"Configure them in {DEFAULT_POC_ENV_FILE}")
            print("CONFIRM_EMISSION_CLICKED=NO")
            return SiiNavigationState.BHE_FORM
        if not mapping.required_ready:
            raise SiiNavigationError(
                "BHE form controls could not be mapped completely; safe fill stopped."
            )
        automation_data = data.to_automation_data()
        automation.fill_bhe_form(page, mapping, automation_data)
        automation.validate_bhe_form(page, mapping, automation_data)
        print("BHE_FORM_FILLED=YES")
        if mapping.confirm_emission is None:
            raise SiiNavigationError(
                "BHE form was filled, but the safe 'Confirmar Emisión' control was not found."
            )
        print("CONFIRM_EMISSION_DETECTED=YES")
        def submit_preview():
            self._ensure_preview_retention_submission(
                page, automation_data.retention_mode, getattr(mapping, "confirm_emission", None)
            )
            return automation.confirm_to_preview(page, mapping)

        next_page, dialog_messages = self._confirm_to_preview_after_optional_pause(
            submit_preview
        )
        self._active_page = next_page
        self._diagnose_context("after-confirmation")
        self._save_debug_screenshot(next_page, "after-confirmation")
        if self.detect_state(next_page) != SiiNavigationState.BHE_PREVIEW:
            print("CONFIRM_EMISSION_CLICKED=YES")
            self._save_confirmation_rejection(next_page, mapping, dialog_messages)
            raise SiiNavigationError(
                "Safe confirmation did not reach the SII BHE preview; final emission remains blocked."
            )
        preview_checks = automation.validate_preview(next_page, automation_data)
        print(f"BHE_PREVIEW_VALIDATED={'YES' if all(preview_checks.values()) else 'NO'}")
        if not all(preview_checks.values()):
            raise SiiNavigationError(
                "SII BHE preview validation failed; final emission remains blocked."
            )
        print("CONFIRM_EMISSION_CLICKED=YES")
        print("FINAL_EMIT_BUTTON_DETECTED=YES")
        print("FINAL_EMIT_BUTTON_BLOCKED=YES")
        return SiiNavigationState.BHE_PREVIEW

    def _pause_before_confirm(self) -> None:
        if not self.settings.pause_before_confirm:
            return
        print("PAUSED_BEFORE_CONFIRM=YES")
        input("Revisa visualmente el formulario SII. Presiona Enter para continuar al borrador...")

    def _confirm_to_preview_after_optional_pause(self, confirm_callback):
        print(
            "PAUSE_BEFORE_CONFIRM_ENABLED="
            f"{'YES' if self.settings.pause_before_confirm else 'NO'}"
        )
        self._pause_before_confirm()
        return confirm_callback()

    def _save_confirmation_rejection(
        self, page: Page, mapping: BHEFormFieldMap, dialog_messages: list[str]
    ) -> None:
        invalid_fields: list[str] = []
        validation_messages: list[str] = []
        for control in page.locator(":invalid").all():
            invalid_fields.append(
                f"name={control.get_attribute('name') or ''};id={control.get_attribute('id') or ''}"
            )
            message = control.evaluate("node => node.validationMessage || ''")
            if message:
                validation_messages.append(self._safe_text(message))
        for error in page.locator(
            "[role='alert']:visible, .error:visible, .errorMessage:visible, .mensajeError:visible"
        ).all():
            text = self._safe_text(error.inner_text())
            if text not in validation_messages:
                validation_messages.append(text)
        split = split_chilean_rut(os.getenv("SII_POC_RUT_RECEPTOR", "").strip())
        body_valid = bool(mapping.rut_body and split and mapping.rut_body.input_value() == split[0])
        dv_valid = bool(
            mapping.rut_dv and split and mapping.rut_dv.input_value().upper() == split[1]
        )
        lines = [
            f"state={self.detect_state(page).value}",
            f"url={self._safe_url(page.url)}",
            f"dialog_detected={'YES' if dialog_messages else 'NO'}",
            "dialog_messages=" + " | ".join(dialog_messages),
            "validation_messages=" + " | ".join(validation_messages),
            "required_invalid_fields=" + " | ".join(invalid_fields),
            f"rut_body_valid={'YES' if body_valid else 'NO'}",
            f"rut_dv_valid={'YES' if dv_valid else 'NO'}",
        ]
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / "after-confirmation-rejected.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _map_bhe_form_fields(self, page: Page) -> BHEFormFieldMap:
        return BHEFormFieldMap(
            issuer_address=self._find_control(page, ("emisor", "direccion")),
            issuer_comuna=self._find_control(page, ("emisor", "comuna")),
            day=self._find_named_select(page, "cbo_dia_boleta"),
            month=self._find_named_select(page, "cbo_mes_boleta"),
            year=self._find_named_select(page, "cbo_anio_boleta"),
            rut_controls=tuple(self._find_rut_controls(page)),
            rut_body=self._find_rut_body(page),
            rut_dv=self._find_rut_dv(page),
            recipient_name=self._find_control_any(
                page, (("receptor", "nombre"), ("destinatario", "nombre"), ("razon", "social")), ("rut",)
            ),
            recipient_address=self._find_control_any(
                page, (("receptor", "domicilio"), ("destinatario", "domicilio"), ("domicilio",)), ("rut",)
            ),
            region=self._find_select_control(
                page,
                ("region",),
                exact_selectors=("select[name='cod_region']", "select#cod_region"),
            ),
            comuna=self._find_select_control(
                page,
                ("comuna",),
                exact_selectors=("select[name='cbo_comuna']", "select#cbo_comuna"),
            ),
            detail=self._find_control(page, ("prestacion",)),
            amount=self._find_control(page, ("valor",)),
            confirm_emission=self._find_button(page, r"confirmar\s+emisi"),
        )

    @staticmethod
    def _find_named_control(page: Page, name: str) -> Locator | None:
        locator = page.locator(f"[name='{name}'], #{name}").first
        return locator if locator.count() else None

    @staticmethod
    def _find_named_select(page: Page, name: str) -> Locator | None:
        locator = page.locator(f"select[name='{name}'], select#{name}").first
        return locator if locator.count() else None

    def _find_rut_controls(self, page: Page) -> list[Locator]:
        body = self._find_rut_body(page)
        dv = self._find_rut_dv(page)
        return [control for control in (body, dv) if control is not None]

    def _find_rut_body(self, page: Page) -> Locator | None:
        return self._find_rut_field(
            page,
            ("input[name='txt_rut_destinatario']", "input#txt_rut_destinatario"),
            require_dv=False,
        )

    def _find_rut_dv(self, page: Page) -> Locator | None:
        return self._find_rut_field(
            page,
            ("input[name='txt_dv_destinatario']", "input#txt_dv_destinatario"),
            require_dv=True,
        )

    def _find_rut_field(
        self, page: Page, exact_selectors: tuple[str, ...], require_dv: bool
    ) -> Locator | None:
        for selector in exact_selectors:
            candidate = page.locator(selector).first
            if candidate.count():
                return candidate
        for candidate in page.locator("input:not([type='hidden'])").all():
            metadata = self._control_metadata(page, candidate).lower()
            is_rut = "rut" in metadata and ("receptor" in metadata or "destinat" in metadata)
            is_dv = "dv" in metadata or "digito" in metadata or "verificador" in metadata
            if is_rut and is_dv == require_dv:
                return candidate
        return None

    def _find_control(
        self,
        page: Page,
        required_terms: tuple[str, ...],
        excluded_terms: tuple[str, ...] = (),
    ) -> Locator | None:
        for control in self._candidate_controls(page):
            metadata = self._control_metadata(page, control).lower()
            if all(term in metadata for term in required_terms) and not any(
                term in metadata for term in excluded_terms
            ):
                return control
        return None

    def _find_control_any(
        self,
        page: Page,
        alternatives: tuple[tuple[str, ...], ...],
        excluded_terms: tuple[str, ...] = (),
    ) -> Locator | None:
        for terms in alternatives:
            result = self._find_control(page, terms, excluded_terms)
            if result is not None:
                return result
        return None

    def _find_select_control(
        self,
        page: Page,
        required_terms: tuple[str, ...],
        exact_selectors: tuple[str, ...] = (),
    ) -> Locator | None:
        """Resolve a select semantically, never from the global control order."""
        for selector in exact_selectors:
            candidate = page.locator(selector).first
            if candidate.count():
                return candidate

        for term in required_terms:
            labeled = page.get_by_label(re.compile(term, re.IGNORECASE)).first
            if labeled.count() and labeled.evaluate("node => node.tagName.toLowerCase()") == "select":
                return labeled

        for candidate in page.locator("select").all():
            metadata = self._control_metadata(page, candidate).lower()
            if all(term in metadata for term in required_terms):
                return candidate
        return None

    @staticmethod
    def _candidate_controls(page: Page) -> list[Locator]:
        controls = page.locator("input:not([type='hidden']), textarea, select")
        return [controls.nth(index) for index in range(controls.count())]

    def _control_metadata(self, page: Page, control: Locator) -> str:
        control_id = control.get_attribute("id") or ""
        label = ""
        if control_id:
            label_locator = page.locator(f"label[for='{control_id}']").first
            if label_locator.count():
                label = label_locator.inner_text()
        return " ".join(
            (
                control.get_attribute("name") or "",
                control_id,
                control.get_attribute("placeholder") or "",
                label,
            )
        )

    @staticmethod
    def _find_button(page: Page, pattern: str) -> Locator | None:
        button = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
        if button.count():
            return button
        buttons = page.locator("input[type='button'], input[type='submit']")
        for index in range(buttons.count()):
            item = buttons.nth(index)
            text = item.get_attribute("value") or ""
            if re.search(pattern, text, re.IGNORECASE):
                return item
        return None

    def _save_bhe_form_fields(self, page: Page) -> None:
        lines = [
            f"url={self._safe_url(page.url)}",
            f"title={self._safe_text(page.title())}",
            "values=omitted",
        ]
        controls = page.locator("input:not([type='hidden']), textarea, select")
        for index in range(controls.count()):
            control = controls.nth(index)
            tag = control.evaluate("node => node.tagName.toLowerCase()")
            control_type = control.get_attribute("type") or tag
            control_id = control.get_attribute("id") or ""
            label = self._associated_label(page, control_id)
            line = (
                f"control tag={tag} type={control_type} "
                f"name={control.get_attribute('name') or ''} id={control_id} "
                f"placeholder={control.get_attribute('placeholder') or ''} label={label}"
            )
            if tag == "select":
                option_labels = control.locator("option").all_inner_texts()
                line += " options=" + "|".join(self._safe_text(item) for item in option_labels)
            lines.append(line)
        for kind, selector in (("button", "button:visible"), ("input-button", "input[type='button']:visible, input[type='submit']:visible")):
            items = page.locator(selector)
            for index in range(items.count()):
                item = items.nth(index)
                text = item.inner_text() if kind == "button" else item.get_attribute("value") or ""
                lines.append(f"{kind} text={self._safe_text(text)}")
        lines.extend(f"heading text={self._safe_text(text)}" for text in page.locator("h1:visible,h2:visible,h3:visible").all_inner_texts())
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / "bhe-form-fields.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _save_bhe_form_selects(self, page: Page) -> None:
        lines = [
            f"url={self._safe_url(page.url)}",
            f"title={self._safe_text(page.title())}",
            "values=omitted",
        ]
        for select in page.locator("select").all():
            select_id = select.get_attribute("id") or ""
            label = self._associated_label(page, select_id)
            options = select.locator("option").all()
            lines.extend(
                (
                    "select:",
                    f"  name={select.get_attribute('name') or ''}",
                    f"  id={select_id}",
                    f"  label={label}",
                    f"  option_count={len(options)}",
                )
            )
            for option in options:
                lines.append(
                    f"    - text={option.inner_text().strip()} "
                    f"value={option.get_attribute('value') or ''}"
                )
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / "bhe-form-selects.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _associated_label(page: Page, control_id: str) -> str:
        if not control_id:
            return ""
        label = page.locator(f"label[for='{control_id}']").first
        return SiiBrowserService._safe_text(label.inner_text()) if label.count() else ""

    def _fill_bhe_form(self, page: Page, mapping: BHEFormFieldMap, data: BHEAutomationData) -> None:
        self._save_bhe_form_selects(page)
        split = split_chilean_rut(data.rut_receptor)
        if split is None:
            raise SiiNavigationError("SII_POC_RUT_RECEPTOR is not a valid Chilean RUT.")
        if mapping.rut_body is None or mapping.rut_dv is None:
            raise SiiNavigationError("SII RUT body and DV fields could not be mapped safely.")
        mapping.rut_body.fill(split[0])
        mapping.rut_dv.fill(split[1])
        print("RUT_BODY_FIELD_DETECTED=YES")
        print("RUT_DV_FIELD_DETECTED=YES")
        print("RUT_BODY_FILLED=YES")
        print("RUT_DV_FILLED=YES")
        print("name_filled=NO (optional; SII resolves it from RUT)")
        mapping.recipient_address.fill(data.direccion)
        self._select_date(mapping, data.fecha_emision)
        self._select_region_and_comuna(page, mapping, data.region, data.comuna)
        mapping.detail.fill(data.detalle)
        mapping.amount.fill(str(data.monto))

    def _select_date(self, mapping: BHEFormFieldMap, emission_date: date) -> None:
        self._select_unique_option(mapping.day, (str(emission_date.day), str(emission_date.day).zfill(2)), "día")
        month_name = ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()[emission_date.month - 1]
        matched_month = self._select_unique_option(mapping.month, (month_name,), "mes")
        selected_month = self._selected_option(mapping.month)
        expected_month = self._normalize_option_text(month_name)
        selected_month_text = selected_month.inner_text().strip() if selected_month else ""
        month_matches = bool(
            selected_month and self._normalize_option_text(selected_month_text) == expected_month
        )
        print(f"DATE_MONTH_EXPECTED_LABEL={expected_month.upper()}")
        print(f"DATE_MONTH_MATCHED_OPTION_TEXT={matched_month.inner_text().strip()}")
        print(f"DATE_MONTH_MATCHED_OPTION_VALUE={matched_month.get_attribute('value') or ''}")
        print(f"DATE_MONTH_SELECTED_TEXT={selected_month_text}")
        print(f"DATE_MONTH_SELECTED_VALUE={selected_month.get_attribute('value') if selected_month else ''}")
        print(f"DATE_MONTH_MATCHED={'YES' if month_matches else 'NO'}")
        if not month_matches:
            raise SiiNavigationError("SII month selection did not preserve the expected visible label.")
        self._select_unique_option(mapping.year, (str(emission_date.year),), "año")

    @classmethod
    def _select_unique_option(
        cls, select: Locator | None, targets: tuple[str, ...], field_name: str
    ) -> Locator:
        if select is None:
            raise SiiNavigationError(f"SII {field_name} select could not be mapped safely.")
        matches = [
            option
            for option in select.locator("option").all()
            if any(cls._option_matches(option, target) for target in targets)
        ]
        if len(matches) != 1:
            raise SiiNavigationError(
                f"SII {field_name} option resolution was ambiguous or empty ({len(matches)} matches)."
            )
        cls._select_option_locator(select, matches[0])
        return matches[0]

    @staticmethod
    def _select_option_locator(select: Locator, option: Locator) -> None:
        value = option.get_attribute("value")
        if value is not None:
            select.select_option(value=value)
        else:
            select.select_option(label=option.inner_text().strip())

    def _select_region_and_comuna(self, page: Page, mapping: BHEFormFieldMap, region: str, comuna: str) -> None:
        if mapping.region is None or mapping.comuna is None:
            raise SiiNavigationError("SII Región/Comuna selects could not be mapped safely.")
        self._select_option_semantic(mapping.region, region, "Región")
        region_option = mapping.region.locator("option:checked").first
        print("REGION_SELECTED=YES")
        print(f"REGION_SELECTED_LABEL={self._normalize_option_text(region_option.inner_text()).upper()}")
        options_before_region = len(mapping.comuna.locator("option").all())
        comuna_option = self._wait_for_semantic_option(page, mapping.comuna, comuna, "Comuna")
        options_after_region = len(mapping.comuna.locator("option").all())
        self._select_option_locator(mapping.comuna, comuna_option)
        print("COMUNA_LOADED_AFTER_REGION=YES")
        print("COMUNA_SELECTED=YES")
        print(f"COMUNA_SELECTED_LABEL={self._normalize_option_text(comuna_option.inner_text()).upper()}")
        self._append_region_comuna_diagnostic(
            mapping, region_option, comuna_option, options_before_region, options_after_region
        )

    @staticmethod
    def _normalize_option_text(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value)
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return re.sub(r"\s+", " ", normalized).strip().casefold()

    @classmethod
    def normalize_region(cls, value: str) -> str:
        normalized = cls._normalize_option_text(value)
        normalized = re.sub(r"^region\s+(?:de|del)\s+", "", normalized)
        return re.sub(r"^\d+\s*[-.]?\s+(?=[a-z])", "", normalized)

    @classmethod
    def _region_option_matches(cls, option: Locator, target: str) -> bool:
        label = option.inner_text().strip()
        value = option.get_attribute("value") or ""
        target_normalized = cls.normalize_region(target)
        return (
            cls.normalize_region(label) == target_normalized
            or cls._normalize_option_text(value) == cls._normalize_option_text(target)
            or (
                target_normalized.isdigit()
                and re.match(
                    rf"^{re.escape(target_normalized)}\s*[-.]?\s+",
                    cls._normalize_option_text(label),
                )
            )
        )

    @classmethod
    def _resolve_unique_region_option(cls, select: Locator, target: str) -> Locator:
        matches = [
            option
            for option in select.locator("option").all()
            if cls._region_option_matches(option, target)
        ]
        if len(matches) != 1:
            raise SiiNavigationError(
                f"SII Región option resolution was ambiguous or empty ({len(matches)} matches)."
            )
        return matches[0]

    @classmethod
    def _option_matches(cls, option: Locator, target: str) -> bool:
        target_normalized = cls._normalize_option_text(target)
        label = option.inner_text().strip()
        value = option.get_attribute("value") or ""
        label_normalized = cls._normalize_option_text(label)
        if label_normalized == target_normalized or cls._normalize_option_text(value) == target_normalized:
            return True
        if target_normalized.isdigit() and label_normalized.isdigit():
            return int(label_normalized) == int(target_normalized)
        if target_normalized.isdigit() and re.match(rf"^{re.escape(target_normalized)}\s*[-.]?\s+", label_normalized):
            return True
        label_without_code = re.sub(r"^\d+\s*[-.]?\s*", "", label_normalized)
        return label_without_code == target_normalized

    @classmethod
    def _select_option_semantic(cls, select: Locator, target: str, field_name: str) -> None:
        if select.get_attribute("name") == "cod_region" or select.get_attribute("id") == "cod_region":
            option = cls._resolve_unique_region_option(select, target)
            cls._select_option_locator(select, option)
            return
        cls._select_unique_option(select, (target,), field_name)

    @classmethod
    def _wait_for_semantic_option(
        cls, page: Page, select: Locator, target: str, field_name: str
    ) -> Locator:
        option_locator = select.locator("option")
        deadline = datetime.now().timestamp() + 15
        while datetime.now().timestamp() < deadline:
            matches = [option for option in option_locator.all() if cls._option_matches(option, target)]
            if len(matches) > 1:
                raise SiiNavigationError(f"SII {field_name} option matching '{target}' is ambiguous.")
            if matches:
                return matches[0]
            page.wait_for_timeout(100)
        raise SiiNavigationError(
            f"SII {field_name} option '{target}' did not appear after the Región selection."
        )

    def _append_region_comuna_diagnostic(
        self,
        mapping: BHEFormFieldMap,
        region_option: Locator,
        comuna_option: Locator,
        options_before_region: int,
        options_after_region: int,
    ) -> None:
        if mapping.region is None or mapping.comuna is None:
            return
        lines = [
            "",
            "REGION_SELECT:",
            f"  name={mapping.region.get_attribute('name') or ''}",
            f"  id={mapping.region.get_attribute('id') or ''}",
            f"  options_count={len(mapping.region.locator('option').all())}",
            "  matched_expected=YES",
            f"  matched_option_text={region_option.inner_text().strip()}",
            f"  matched_option_value={region_option.get_attribute('value') or ''}",
            "COMUNA_SELECT:",
            f"  name={mapping.comuna.get_attribute('name') or ''}",
            f"  id={mapping.comuna.get_attribute('id') or ''}",
            f"  options_before_region={options_before_region}",
            f"  options_after_region={options_after_region}",
            "  matched_expected=YES",
            f"  matched_option_text={comuna_option.inner_text().strip()}",
            f"  matched_option_value={comuna_option.get_attribute('value') or ''}",
        ]
        path = self.settings.debug_dir / "bhe-form-selects.txt"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")

    def _validate_bhe_form_fill(self, page: Page, mapping: BHEFormFieldMap, data: BHEAutomationData) -> None:
        split = split_chilean_rut(data.rut_receptor)
        selected = self._selected_option
        checks = {
            "date_day_filled": bool(selected(mapping.day) and self._option_matches(selected(mapping.day), str(data.fecha_emision.day))),
            "date_month_filled": bool(
                selected(mapping.month)
                and self._normalize_option_text(selected(mapping.month).inner_text())
                == self._normalize_option_text(
                    ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()[data.fecha_emision.month - 1]
                )
            ),
            "date_year_filled": bool(selected(mapping.year) and self._option_matches(selected(mapping.year), str(data.fecha_emision.year))),
            "rut_body_filled": bool(mapping.rut_body and split and mapping.rut_body.input_value() == split[0]),
            "rut_dv_filled": bool(mapping.rut_dv and split and mapping.rut_dv.input_value().upper() == split[1]),
            "rut_filled": bool(
                mapping.rut_body
                and mapping.rut_dv
                and split
                and mapping.rut_body.input_value() == split[0]
                and mapping.rut_dv.input_value().upper() == split[1]
            ),
            "address_filled": bool(mapping.recipient_address.input_value()),
            "region_filled": bool(selected(mapping.region) and self._region_option_matches(selected(mapping.region), data.region)),
            "comuna_filled": bool(selected(mapping.comuna) and self._option_matches(selected(mapping.comuna), data.comuna)),
            "detail_filled": bool(mapping.detail.input_value()),
            "amount_filled": bool(mapping.amount.input_value()),
            "retention_mode_valid": True,
        }
        for name, result in checks.items():
            print(f"{name}={'YES' if result else 'NO'}")
        form_checks = {
            "form_rut_ok": checks["rut_filled"],
            "form_date_ok": all(
                checks[name] for name in ("date_day_filled", "date_month_filled", "date_year_filled")
            ),
            "form_address_ok": checks["address_filled"],
            "form_region_ok": checks["region_filled"],
            "form_comuna_ok": checks["comuna_filled"],
            "form_detail_ok": checks["detail_filled"],
            "form_amount_ok": checks["amount_filled"],
            "form_retention_mode_ok": checks["retention_mode_valid"],
        }
        for name, result in form_checks.items():
            print(f"{name}={'YES' if result else 'NO'}")
        form_validated = all(checks.values())
        print(f"BHE_FORM_VALIDATED={'YES' if form_validated else 'NO'}")
        if not form_validated:
            failed_checks = [name for name, passed in form_checks.items() if not passed]
            raise SiiValidationError(
                "BHE form validation failed; safe stop preserved.", failed_checks
            )

    @staticmethod
    def _selected_option(select: Locator | None) -> Locator | None:
        if select is None:
            return None
        selected = select.locator("option:checked").first
        return selected if selected.count() else None

    @staticmethod
    def _final_emit_button(page: Page) -> Locator | None:
        """Find the irreversible button without ever invoking it."""
        pattern = r"emitir\s+boleta\s+de\s+honorarios\s+electr[oó]nica"
        button = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
        if button.count():
            return button
        controls = page.locator("input[type='button'], input[type='submit']")
        for index in range(controls.count()):
            candidate = controls.nth(index)
            label = candidate.get_attribute("value") or ""
            if re.fullmatch(pattern, label.strip(), re.IGNORECASE):
                return candidate
        return None

    def is_bhe_preview(self, page: Page) -> bool:
        body_text = page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        draft_notice = bool(
            re.search(r"borrador.*no\s+tiene.*ninguna\s+validez", body_text, re.IGNORECASE | re.DOTALL)
        )
        return draft_notice and self._final_emit_button(page) is not None

    def _validate_bhe_preview(
        self, page: Page, data: BHEAutomationData | None = None
    ) -> dict[str, bool]:
        body_text = page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        recipient_ok = self._preview_recipient_matches(page, body_text, data)
        sensitive_values = tuple(filter(None, (
            data.rut_receptor if data else None,
            data.razon_social if data else None,
            data.direccion if data else None,
            data.comuna if data else None,
            data.region if data else None,
            data.detalle if data else None,
            str(data.monto) if data else None,
            data.fecha_emision.isoformat() if data else None,
            self._last_sii_resolved_recipient_name,
        )))
        retention_diagnostic = self._preview_retention_diagnostic_for_page(page, sensitive_values)
        retention_diagnostic.update(self._last_retention_diagnostics.get("post2_network_capture", {}))
        for key in ("formdata_value_before_enforcement", "formdata_value_after_enforcement", "formdata_enforcement_applied"):
            if key in self._last_retention_diagnostics:
                retention_diagnostic[key] = self._last_retention_diagnostics[key]
        requested_retention = data.retention_mode.value if data is not None else None
        retention_ok = (
            self._retention_mode_matches(
                requested_retention, retention_diagnostic["detected_retention_mode"]
            )
            if requested_retention is not None
            else retention_diagnostic["detected_retention_mode"] is not None
        )
        checks = {
            "preview_date_ok": bool(re.search(r"fecha", body_text, re.IGNORECASE)),
            "preview_recipient_ok": recipient_ok,
            "preview_address_ok": bool(re.search(r"domicilio|direcci", body_text, re.IGNORECASE)),
            "preview_detail_ok": bool(re.search(r"prestaci[oó]n|detalle|servicio", body_text, re.IGNORECASE)),
            "preview_amount_ok": bool(re.search(r"monto|valor|honorario|total", body_text, re.IGNORECASE)),
            "preview_retention_mode_ok": retention_ok,
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        self._last_retention_diagnostics.update({
            "requested_retention_mode": requested_retention,
            "detected_retention_mode": retention_diagnostic["detected_retention_mode"],
            "retention_evidence_kind": retention_diagnostic["retention_evidence_kind"],
            "preview_frame_index": retention_diagnostic["preview_frame_index"],
            "preview_retention_mode_ok": retention_ok,
            "failed_checks": failed_checks,
            "diagnostic_reason": retention_diagnostic["diagnostic_reason"],
            "diagnostic_version": retention_diagnostic["diagnostic_version"],
            "decision_surface": retention_diagnostic["decision_surface"],
            "preview_frame_inventory": retention_diagnostic["preview_frame_inventory"],
            "retention_candidates": retention_diagnostic.get("retention_candidates", []),
            "classification_text_source": retention_diagnostic.get("classification_text_source"),
            "issuer_matches": retention_diagnostic.get("issuer_matches", []),
            "receiver_matches": retention_diagnostic.get("receiver_matches", []),
            "preview_retention_detected_mode": retention_diagnostic.get("preview_retention_detected_mode"),
            "preview_retention_evidence_type": retention_diagnostic.get("preview_retention_evidence_type"),
            "preview_retention_matched_pattern": retention_diagnostic.get("preview_retention_matched_pattern"),
            "preview_retention_matched_text": retention_diagnostic.get("preview_retention_matched_text"),
            "preview_retention_frame_index": retention_diagnostic.get("preview_retention_frame_index"),
            "preview_retention_element_tag": retention_diagnostic.get("preview_retention_element_tag"),
            "preview_retention_element_id": retention_diagnostic.get("preview_retention_element_id"),
            "preview_retention_element_name": retention_diagnostic.get("preview_retention_element_name"),
            "preview_retention_element_class": retention_diagnostic.get("preview_retention_element_class"),
            "preview_retention_match_visible": retention_diagnostic.get("preview_retention_match_visible"),
            "post2_request_observed": retention_diagnostic.get("post2_request_observed"),
            "post2_method": retention_diagnostic.get("post2_method"),
            "post2_url_path": retention_diagnostic.get("post2_url_path"),
            "post2_retention_value": retention_diagnostic.get("post2_retention_value"),
            "post2_retention_occurrences": retention_diagnostic.get("post2_retention_occurrences"),
            "preview": {
                "diagnostic_version": retention_diagnostic["diagnostic_version"],
                "requested_retention_mode": requested_retention,
                "detected_retention_mode": retention_diagnostic["detected_retention_mode"],
                "retention_evidence_kind": retention_diagnostic["retention_evidence_kind"],
                "preview_retention_mode_ok": retention_ok,
                "failed_checks": failed_checks,
                "decision_surface": retention_diagnostic["decision_surface"],
                "preview_frame_inventory": retention_diagnostic["preview_frame_inventory"],
                "issuer_matches": retention_diagnostic.get("issuer_matches", []),
                "receiver_matches": retention_diagnostic.get("receiver_matches", []),
            },
        })
        if not retention_ok or self._capture_all_preview_artifacts:
            self._capture_preview_debug(
                page,
                retention_diagnostic,
                capture_reason=(
                    "preview_retention_mode_ok=false"
                    if not retention_ok else "preview_only"
                ),
            )
        self._last_preview_result = {
            "recipient_rut_ok": recipient_ok,
            "sii_resolved_recipient_name_present": bool(self._last_sii_resolved_recipient_name),
            "sii_resolved_recipient_name": self._last_sii_resolved_recipient_name,
            "preview_recipient_ok": recipient_ok,
            "requested_retention_mode": requested_retention,
            **retention_diagnostic,
            **self.last_retention_diagnostics,
        }
        if data is not None:
            print(f"recipient_rut_ok={'YES' if recipient_ok else 'NO'}")
            print(
                "sii_resolved_recipient_name_present="
                f"{'YES' if self._last_sii_resolved_recipient_name else 'NO'}"
            )
            diagnostics = self._last_preview_recipient_diagnostics
            for name in (
                "rut_label_found",
                "rut_value_node_found",
                "name_label_found",
                "name_value_node_found",
            ):
                print(f"{name}={'YES' if diagnostics[name] else 'NO'}")
            print(
                "EXPECTED_RUT_FOUND_ANYWHERE="
                f"{'YES' if diagnostics['expected_rut_found_anywhere'] else 'NO'}"
            )
            print(
                "RECIPIENT_STRUCTURE_CANDIDATES="
                f"{diagnostics['recipient_structure_candidates']}"
            )
        for name, result in checks.items():
            print(f"{name}={'YES' if result else 'NO'}")
        print(f"requested_retention_mode={requested_retention or 'UNKNOWN'}")
        print(
            "detected_retention_mode="
            f"{retention_diagnostic['detected_retention_mode'] or 'unknown'}"
        )
        print(f"retention_evidence_kind={retention_diagnostic['retention_evidence_kind']}")
        return checks

    def _preview_recipient_matches(
        self, page: Page, body_text: str, data: BHEAutomationData | None
    ) -> bool:
        if data is None:
            self._last_sii_resolved_recipient_name = None
            return bool(re.search(r"receptor|destinatario", body_text, re.IGNORECASE))
        rut_ok, resolved_name = self._diagnose_preview_recipient_structure(page, body_text, data)
        self._last_sii_resolved_recipient_name = resolved_name
        return rut_ok

    def _diagnose_preview_recipient_structure(
        self, page: Page, body_text: str, data: BHEAutomationData
    ) -> tuple[bool, str | None]:
        expected_rut = split_chilean_rut(data.rut_receptor)
        expected_rut_normalized = "".join(expected_rut).upper() if expected_rut else ""
        compact_body_rut = re.sub(r"[^0-9kK]", "", body_text).upper()
        rut_anywhere = bool(expected_rut_normalized and expected_rut_normalized in compact_body_rut)
        rut_structured = False
        resolved_name: str | None = None
        rut_label_found = False
        rut_value_node_found = False
        name_label_found = False
        name_value_node_found = False
        candidates: list[tuple[str, tuple[str, ...], bool, bool]] = []
        for row in page.locator("tr, [role='row']").all():
            cells = row.locator("th, td, [role='cell']").all()
            cell_texts = [self._normalize_preview_text(cell.inner_text()) for cell in cells]
            row_text = self._normalize_preview_text(row.inner_text())
            labels: list[str] = []
            if re.search(r"rut\s+(?:del\s+)?(?:receptor|destinatario)|(?:receptor|destinatario).*rut", row_text):
                labels.append("RUT DESTINATARIO")
                rut_match = any(self._normalize_rut_text(text) == expected_rut_normalized for text in cell_texts)
                rut_structured = rut_structured or rut_match
                candidates.append(("TR", tuple(labels), bool(cell_texts), rut_match))
            if re.search(r"nombre|razon\s+social", row_text):
                labels.append("NOMBRE DESTINATARIO")
                if len(cell_texts) > 1 and cell_texts[-1]:
                    resolved_name = resolved_name or cell_texts[-1]
                candidates.append(("TR", tuple(labels), bool(cell_texts), bool(resolved_name)))

        # SII renders the real preview recipient as labeled text lines rather
        # than a table row (for example, "Señor(es): ..." and "Rut: ...").
        # Compare the complete value after the label; do not use substrings.
        for raw_line in body_text.splitlines():
            line = self._normalize_preview_text(raw_line)
            if re.match(
                r"^(?:senor(?:\(es\)|es)?|nombre|razon\s+social)\s*[:\-]",
                line,
            ):
                name_label_found = True
                value = re.sub(
                    r"^(?:senor(?:\(es\)|es)?|nombre|razon\s+social)\s*[:\-]?\s*",
                    "",
                    line,
                )
                name_value_node_found = name_value_node_found or bool(value)
                resolved_name = resolved_name or value
                candidates.append(("TEXT_LINE", ("SENOR(ES)",), bool(value), bool(value)))
            elif re.match(r"^rut(?:\s+(?:del\s+)?(?:receptor|destinatario))?\s*[:\-]", line):
                rut_label_found = True
                value = re.sub(
                    r"^rut(?:\s+(?:del\s+)?(?:receptor|destinatario))?\s*[:\-]?\s*",
                    "",
                    line,
                )
                matched = self._normalize_rut_text(value) == expected_rut_normalized
                rut_value_node_found = rut_value_node_found or bool(value)
                rut_structured = rut_structured or matched
                candidates.append(("TEXT_LINE", ("RUT",), bool(value), matched))

        # Legacy SII markup may place several labeled values in one element
        # instead of separate lines. Inspect element text as a bounded label /
        # value pair, still requiring an exact normalized match.
        for node in page.locator("body *").all():
            text = self._normalize_preview_text(node.inner_text())
            if not text:
                continue
            name_value = self._extract_preview_labeled_value(
                text,
                r"(?:senor(?:\(es\)|es)?|nombre|razon\s+social)",
            )
            if name_value is not None:
                name_label_found = True
                name_value_node_found = name_value_node_found or bool(name_value)
                resolved_name = resolved_name or name_value
                candidates.append(("ELEMENT", ("NAME_LABEL",), bool(name_value), bool(name_value)))
            rut_value = self._extract_preview_labeled_value(
                text, r"rut(?:\s+(?:del\s+)?(?:receptor|destinatario))?"
            )
            if rut_value is not None:
                rut_label_found = True
                rut_value_node_found = rut_value_node_found or bool(rut_value)
                matched = self._normalize_rut_text(rut_value) == expected_rut_normalized
                rut_structured = rut_structured or matched
                candidates.append(("ELEMENT", ("RUT_LABEL",), bool(rut_value), matched))

        lines = [
            "PREVIEW_RECIPIENT_STRUCTURE:",
            f"  rut_label_found={'YES' if rut_label_found else 'NO'}",
            f"  rut_value_node_found={'YES' if rut_value_node_found else 'NO'}",
            f"  name_label_found={'YES' if name_label_found else 'NO'}",
            f"  name_value_node_found={'YES' if name_value_node_found else 'NO'}",
            f"EXPECTED_RUT_FOUND_ANYWHERE={'YES' if rut_anywhere else 'NO'}",
        ]
        for index, (tag, labels, value_present, matched) in enumerate(candidates, start=1):
            lines.extend(
                (
                    f"RECIPIENT_CANDIDATE_{index}:",
                    f"  tag={tag}",
                    f"  labels_detected={'|'.join(labels)}",
                    f"  value_present={'YES' if value_present else 'NO'}",
                    f"  normalized_expected_match={'YES' if matched else 'NO'}",
                )
            )
        if not candidates:
            lines.append("RECIPIENT_CANDIDATES=NONE")
        self._last_preview_recipient_diagnostics = {
            "rut_label_found": rut_label_found,
            "rut_value_node_found": rut_value_node_found,
            "name_label_found": name_label_found,
            "name_value_node_found": name_value_node_found,
            "expected_rut_found_anywhere": rut_anywhere,
            "recipient_structure_candidates": len(candidates),
        }
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / "bhe-preview-recipient-structure.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return rut_structured, resolved_name

    @classmethod
    def _extract_preview_labeled_value(cls, text: str, label_pattern: str) -> str | None:
        match = re.search(
            rf"(?:^|\s){label_pattern}\s*[:\-]\s*(.*?)(?=\s+(?:senor(?:\(es\)|es)?|nombre|razon\s+social|rut|domicilio|direccion)\s*[:\-]|$)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _normalize_rut_text(value: str) -> str:
        return re.sub(r"[^0-9kK]", "", value).upper()

    def is_bhe_form(self, page: Page) -> bool:
        url_evidence = "tmbecn_presentadatosboleta.cgi" in page.url.lower()
        known_controls = all(self._find_named_control(page, name) for name in (
            "cbo_dia_boleta", "cbo_mes_boleta", "cbo_anio_boleta"
        ))
        return url_evidence and known_controls or self._has_conservative_bhe_form_evidence(
            page, page.locator("body").inner_text(timeout=self.settings.timeout_ms)
        )

    def _diagnose_context(self, name: str) -> None:
        if not self._context:
            return
        pages = self._context.pages
        lines = [f"diagnostic={name}", f"pages={len(pages)}"]
        for index, current in enumerate(pages):
            lines.append(f"page[{index}].url={self._safe_url(current.url)}")
            lines.append(f"page[{index}].title={self._safe_text(current.title())}")
            lines.append(f"page[{index}].state={self.detect_state(current).value}")
            lines.extend(self._visible_summary(current))
        output = "\n".join(lines) + "\n"
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.debug_dir / f"{name}.txt").write_text(output, encoding="utf-8")
        print(output.rstrip())

    def _visible_summary(self, page: Page) -> list[str]:
        lines: list[str] = []
        for kind, selector, limit in (
            ("link", "a:visible", 40),
            ("button", "button:visible", 30),
            ("heading", "h1:visible,h2:visible,h3:visible", 20),
        ):
            locator = page.locator(selector)
            items = []
            for index in range(min(locator.count(), limit)):
                item = locator.nth(index)
                text = self._safe_text(item.inner_text())
                if kind == "link":
                    items.append(f"{text} -> {self._safe_url(item.get_attribute('href') or '')}")
            else:
                    items.append(text)
            lines.append(f"{kind}s=" + " | ".join(items))
        lines.extend(self._input_summary(page))
        return lines

    def _input_summary(self, page: Page) -> list[str]:
        lines = []
        inputs = page.locator("input:not([type='hidden']), textarea, select")
        for index in range(min(inputs.count(), 60)):
            item = inputs.nth(index)
            input_id = item.get_attribute("id") or ""
            label_text = ""
            if input_id:
                label = page.locator(f"label[for='{input_id}']").first
                if label.count() > 0:
                    label_text = self._safe_text(label.inner_text())
            input_type = item.get_attribute("type") or item.evaluate("node => node.tagName.toLowerCase()")
            lines.append(
                "input="
                f"type:{input_type};"
                f"name:{self._safe_text(item.get_attribute('name') or '')};"
                f"id:{self._safe_text(input_id)};"
                f"placeholder:{self._safe_text(item.get_attribute('placeholder') or '')};"
                f"label:{label_text}"
            )
        return lines

    @staticmethod
    def _safe_url(value: str) -> str:
        if not value:
            return ""
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    @staticmethod
    def _safe_text(value: str) -> str:
        compact = re.sub(r"\s+", " ", value).strip()
        compact = re.sub(r"\b\d{1,2}(?:\.\d{3}){2}-[\dkK]\b", "[RUT omitted]", compact)
        compact = re.sub(r"\b\d{7,8}-[\dkK]\b", "[RUT omitted]", compact)
        compact = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email omitted]", compact)
        allowed = re.compile(
            r"sii|servicios|online|boleta|honorario|emisor|contribuyente|retenci[oó]n|"
            r"modalidad|receptor|detalle|fecha|ingresar|clave|identificaci[oó]n|cerrar|salir",
            re.IGNORECASE,
        )
        if not allowed.search(compact):
            return "[text omitted]"
        return compact[:160]

    def _save_debug_screenshot(self, page: Page, name: str) -> None:
        self.settings.debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(self.settings.debug_dir / f"{name}.png"), full_page=True)

    @property
    def sii_resolved_recipient_name(self) -> str | None:
        return self._last_sii_resolved_recipient_name

    @property
    def last_preview_result(self) -> dict[str, object]:
        return dict(self._last_preview_result)


class SIIBHEAutomationService:
    """Reusable SII BHE workflow with an explicit final operation."""

    def __init__(self, browser: SiiBrowserService) -> None:
        self.browser = browser

    def ensure_session(self, page: Page, input_fn: Callable[[str], str] = input) -> None:
        self.browser.ensure_authenticated(page, input_fn)

    def navigate_to_bhe(self, page: Page) -> SiiNavigationState:
        return self.browser.navigate_to_bhe(page)

    def run_preview(
        self,
        data: BHEAutomationData,
        input_fn: Callable[[str], str] = input,
        *,
        interactive_console: bool = True,
    ) -> dict[str, object]:
        return self.browser.run_bhe_preview(
            data, input_fn, interactive_console=interactive_console
        )

    def emit_final(self, data: BHEAutomationData) -> dict[str, object]:
        return self.browser.run_bhe_final_emission(data)

    def select_retention_mode(self, page: Page, mode: RetentionMode) -> Page:
        return self.browser._select_retention_and_continue(page, mode)

    def map_form(self, page: Page) -> BHEFormFieldMap:
        return self.browser._map_bhe_form_fields(page)

    def fill_bhe_form(
        self, page: Page, mapping: BHEFormFieldMap, data: BHEAutomationData
    ) -> None:
        self.browser._fill_bhe_form(page, mapping, data)

    def validate_bhe_form(
        self, page: Page, mapping: BHEFormFieldMap, data: BHEAutomationData
    ) -> None:
        self.browser._validate_bhe_form_fill(page, mapping, data)

    def confirm_to_preview(
        self, page: Page, mapping: BHEFormFieldMap
    ) -> tuple[Page, list[str]]:
        if mapping.confirm_emission is None:
            raise SiiNavigationError("Safe confirmation control was not mapped.")
        dialog_messages: list[str] = []

        def capture_dialog(dialog) -> None:
            dialog_messages.append(self.browser._safe_text(dialog.message))
            dialog.accept()

        page.on("dialog", capture_dialog)
        try:
            next_page = self.browser._capture_bhe_confirm_submission(
                page,
                lambda: self.browser._click_navigation(
                    page, mapping.confirm_emission, "confirm-emission"
                ),
            )
        finally:
            page.remove_listener("dialog", capture_dialog)
        return next_page, dialog_messages

    def validate_preview(
        self, page: Page, data: BHEAutomationData
    ) -> dict[str, bool]:
        return self.browser._validate_bhe_preview(page, data)

    @property
    def sii_resolved_recipient_name(self) -> str | None:
        return self.browser.sii_resolved_recipient_name

    @property
    def last_preview_result(self) -> dict[str, object]:
        return self.browser.last_preview_result
