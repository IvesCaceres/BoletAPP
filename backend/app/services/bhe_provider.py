"""Small provider boundary for individual BHE operations."""
import asyncio
import os
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import BHEProviderName, get_settings
from app.db.models import BHEEmission
from app.schemas.bhe import BHERequest
from app.services.bhe_emission_history_service import (
    begin_sii_emission, begin_sii_finalization, create_pending_emission,
    mark_emission_failed,
    mark_emission_status_unknown, mark_emission_success, revert_sii_finalization,
)
from app.services.sii_browser_service import (
    BHEAutomationData, RetentionMode, SIIBHEAutomationService,
    SiiBrowserService, SiiPocSettings, normalize_sii_region,
)


@dataclass
class SIIProviderResult:
    emission: BHEEmission
    status: str
    message: str
    preview_validated: bool
    recipient_rut_ok: bool
    sii_resolved_recipient_name_present: bool
    sii_resolved_recipient_name: str | None
    error: Exception | None = None


def _run_with_windows_policy(fn, *args):
    proactor_policy = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if os.name != "nt" or proactor_policy is None:
        return fn(*args)
    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(proactor_policy())
    try:
        return fn(*args)
    finally:
        asyncio.set_event_loop_policy(previous_policy)


def _run_sii_preview_with_windows_subprocess_support(automation, data):
    def run():
        return automation.run_preview(data, interactive_console=False)
    return _run_with_windows_policy(run)


def _run_sii_final_with_windows_subprocess_support(automation, data):
    return _run_with_windows_policy(automation.emit_final, data)


class SIIBHEProvider:
    name = BHEProviderName.SII.value

    @staticmethod
    def _record_browser_failure_diagnostics(
        browser: SiiBrowserService,
        diagnostics: dict[str, object],
        error: Exception,
    ) -> None:
        """Persist sanitized lifecycle facts for startup/runtime failures."""
        # Browser errors can include command lines, URLs and page contents.
        # Persist a technical allowlist rather than the unrestricted message.
        raw = str(error).casefold()
        message = "Browser lifecycle error; inspect exception type and stage."
        for marker, description in (
            ("closed", "Browser, context or page closed."),
            ("singleton", "Persistent browser profile is already in use."),
            ("executable doesn't exist", "Browser executable is missing."),
            ("timeout", "Browser lifecycle operation timed out."),
        ):
            if marker in raw:
                message = description
                break
        diagnostics.update({
            "failure_stage": browser.current_stage,
            "failure_exception_type": type(error).__name__,
            "failure_message": message[:500],
            "browser_session_active": bool(
                getattr(error, "browser_session_active", browser.session_active)
            ),
            "browser_launch_failed": browser.current_stage == "browser_starting",
            "browser_lifecycle_operation": getattr(error, "browser_lifecycle_operation", "execution"),
        })

    @staticmethod
    def _success_result(
        db: Session,
        emission: BHEEmission,
        result: dict[str, object],
        retention_diagnostics: dict[str, object],
    ) -> SIIProviderResult:
        emission.sii_retention_diagnostics = retention_diagnostics
        mark_emission_success(db, emission, result)
        return SIIProviderResult(
            emission=emission, status="success",
            message=str(result.get("provider_message") or "Boleta emitida correctamente."),
            preview_validated=True, recipient_rut_ok=True,
            sii_resolved_recipient_name_present=bool(result.get("sii_resolved_recipient_name")),
            sii_resolved_recipient_name=result.get("sii_resolved_recipient_name"),
        )

    @staticmethod
    def _failure_result(
        db: Session, emission: BHEEmission, browser: SiiBrowserService, error: Exception
    ) -> SIIProviderResult:
        print(f"SII_APP_FLOW_EXCEPTION_STAGE={browser.current_stage}")
        print(f"SII_APP_FLOW_EXCEPTION_TYPE={type(error).__name__}")
        print("SII_APP_FLOW_FAILED=YES")
        failed_checks = list(getattr(error, "failed_checks", []))
        diagnostics = browser.last_retention_diagnostics
        SIIBHEProvider._record_browser_failure_diagnostics(browser, diagnostics, error)
        if failed_checks:
            diagnostics["failed_checks"] = failed_checks
        if getattr(error, "preview_diagnostics", None):
            diagnostics.update({
                key: value for key, value in error.preview_diagnostics.items()
                if key in {
                    "requested_retention_mode", "selected_retention_mode", "selection_verified",
                    "detected_retention_mode", "retention_evidence_kind",
                    "preview_retention_mode_ok", "diagnostic_reason", "preview_frame_index",
                    "preview_stabilized", "failed_checks", "diagnostic_version",
                    "decision_surface", "preview_frame_inventory", "retention_candidates",
                    "selection_label_mode", "selection_diagnostics",
                    "selection", "preview",
                    "selection_action_succeeded", "semantic_selection_verified",
                }
            })
        emission.sii_retention_diagnostics = diagnostics
        if failed_checks:
            print(f"SII_APP_FLOW_FAILED_CHECKS={','.join(failed_checks)}")
        message = (
            f"{type(error).__name__} at stage {browser.current_stage}"
            + (f"; checks={','.join(failed_checks)}" if failed_checks else "")
        )
        error_diagnostics = getattr(error, "preview_diagnostics", {})
        if "preview_retention_mode_ok" in failed_checks and error_diagnostics:
            retention = (
                f"; retention_requested={error_diagnostics.get('requested_retention_mode') or 'unknown'}"
                f"; retention_detected={error_diagnostics.get('detected_retention_mode') or 'unknown'}"
                f"; retention_evidence={error_diagnostics.get('retention_evidence_kind') or 'none'}"
            )
            message += retention
            print(retention.lstrip("; "))
        if getattr(error, "click_performed", False):
            mark_emission_status_unknown(
                db, emission,
                f"Resultado SII posterior al click no verificable; stage={browser.current_stage}",
            )
            return SIIProviderResult(
                emission=emission, status="emission_status_unknown",
                message="El resultado en SII requiere verificación manual. No vuelvas a emitir esta boleta.",
                preview_validated=browser.current_stage != "preview_reached",
                recipient_rut_ok=bool(browser.last_preview_result.get("recipient_rut_ok")),
                sii_resolved_recipient_name_present=bool(browser.last_preview_result.get("sii_resolved_recipient_name_present")),
                sii_resolved_recipient_name=browser.last_preview_result.get("sii_resolved_recipient_name"),
                error=error,
            )
        mark_emission_failed(db, emission, message)
        return SIIProviderResult(
            emission=emission, status="failed",
            message=("No fue posible validar el borrador SII. "
                     + (f"Fallos: {', '.join(failed_checks)}" if failed_checks else "Revisar diagnóstico del backend.")),
            preview_validated=False, recipient_rut_ok=False,
            sii_resolved_recipient_name_present=False, sii_resolved_recipient_name=None, error=error,
        )

    def _execute_in_session(
        self,
        db: Session,
        request: BHERequest,
        browser: SiiBrowserService,
        emission: BHEEmission | None = None,
        client_id: int | None = None,
        batch_item_id: int | None = None,
    ) -> SIIProviderResult:
        """Run one full item while retaining the batch-owned context and thread."""
        if len(request.detalle) != 1:
            raise ValueError("El proveedor SII requiere exactamente un detalle para esta iteración.")
        emission = emission or create_pending_emission(
            db, request, client_id=client_id, provider=self.name, batch_item_id=batch_item_id
        )
        browser.set_diagnostic_context(batch_item_id=batch_item_id, emission_id=emission.id)
        browser._reset_retention_diagnostics(request.retention_mode)
        emission.sii_retention_diagnostics = browser.last_retention_diagnostics
        db.commit()
        try:
            claimed = begin_sii_emission(db, emission.id)
            if claimed is None:
                raise ValueError("La emisión SII ya no está disponible para comenzar.")
            result = SIIBHEAutomationService(browser).emit_final(self._automation_data(request))
            return self._success_result(db, claimed, result, browser.last_retention_diagnostics)
        except Exception as error:
            return self._failure_result(db, emission, browser, error)

    def execute_in_batch_session(
        self,
        db: Session,
        request: BHERequest,
        browser: SiiBrowserService,
        client_id: int | None = None,
        batch_item_id: int | None = None,
    ) -> SIIProviderResult:
        """Run one full item while retaining the batch-owned context and thread."""
        return self._execute_in_session(
            db, request, browser,
            client_id=client_id, batch_item_id=batch_item_id,
        )

    def _execute_individual_in_session(
        self,
        db: Session,
        request: BHERequest,
        browser: SiiBrowserService,
        emission: BHEEmission,
        client_id: int | None = None,
        batch_item_id: int | None = None,
    ) -> SIIProviderResult:
        """Run one individual item through the same lifecycle as a batch."""
        browser._set_stage("browser_starting")
        result = None
        try:
            with browser.batch_session():
                browser._set_stage("browser_started")
                result = self._execute_in_session(
                    db, request, browser, emission=emission,
                    client_id=client_id, batch_item_id=batch_item_id,
                )
        except Exception as error:
            if result is None:
                raise
            # A teardown failure must never overwrite a confirmed success or
            # turn an uncertain post-click result into a retryable failure.
            diagnostics = dict(emission.sii_retention_diagnostics or {})
            self._record_browser_failure_diagnostics(browser, diagnostics, error)
            emission.sii_retention_diagnostics = diagnostics
            db.commit()
        return result

    @staticmethod
    def _automation_data(request: BHERequest) -> BHEAutomationData:
        detail = request.detalle[0]
        return BHEAutomationData(
            fecha_emision=request.fecha_emision or date.today(), rut_receptor=request.rut_receptor,
            razon_social="", direccion=request.direccion,
            region=normalize_sii_region(request.region or 13), comuna=request.comuna,
            detalle=detail.descripcion, monto=detail.get_valor_int(),
            retention_mode=RetentionMode(request.retention_mode.value),
        )

    @staticmethod
    def automation_data_from_emission(emission: BHEEmission) -> BHEAutomationData:
        details = emission.detalle or []
        if len(details) != 1:
            raise ValueError("La emisión SII debe conservar exactamente un detalle.")
        detail = details[0]
        return BHEAutomationData(
            fecha_emision=emission.fecha_emision_solicitada, rut_receptor=emission.rut_receptor,
            razon_social="", direccion=emission.direccion,
            region=normalize_sii_region(emission.region), comuna=emission.comuna,
            detalle=str(detail.get("descripcion", "")),
            monto=int(round(float(detail.get("precio_unitario", emission.monto_total)))),
            retention_mode=RetentionMode(emission.retention_mode or RetentionMode.ISSUER_HANDLES_PPM.value),
        )

    async def execute(self, db: Session, request: BHERequest, client_id: int | None = None, batch_item_id: int | None = None) -> SIIProviderResult:
        if len(request.detalle) != 1:
            raise ValueError("El proveedor SII requiere exactamente un detalle para esta iteración.")
        print("SII_APP_STAGE=provider_initialized")
        emission = create_pending_emission(db, request, client_id=client_id, provider=self.name, batch_item_id=batch_item_id)
        settings = replace(SiiPocSettings.from_environment(), pause_before_confirm=False)
        browser = SiiBrowserService(settings)
        browser.set_diagnostic_context(batch_item_id=batch_item_id, emission_id=emission.id)
        browser._reset_retention_diagnostics(request.retention_mode)
        emission.sii_retention_diagnostics = browser.last_retention_diagnostics
        db.commit()
        try:
            result = await asyncio.to_thread(
                self._execute_individual_in_session,
                db, request, browser, emission, client_id, batch_item_id,
            )
            return result
        except Exception as error:
            print(f"SII_APP_FLOW_EXCEPTION_STAGE={browser.current_stage}")
            print(f"SII_APP_FLOW_EXCEPTION_TYPE={type(error).__name__}")
            print("SII_APP_FLOW_FAILED=YES")
            failed_checks = list(getattr(error, "failed_checks", []))
            diagnostics = browser.last_retention_diagnostics
            self._record_browser_failure_diagnostics(browser, diagnostics, error)
            if failed_checks:
                diagnostics["failed_checks"] = failed_checks
            if getattr(error, "preview_diagnostics", None):
                diagnostics.update({
                    key: value for key, value in error.preview_diagnostics.items()
                    if key in {
                        "requested_retention_mode", "selected_retention_mode", "selection_verified",
                        "detected_retention_mode", "retention_evidence_kind",
                        "preview_retention_mode_ok", "diagnostic_reason", "preview_frame_index",
                        "preview_stabilized", "failed_checks", "diagnostic_version",
                        "decision_surface", "preview_frame_inventory", "retention_candidates",
                        "selection_label_mode", "selection_diagnostics",
                        "selection", "preview",
                        "selection_action_succeeded", "semantic_selection_verified",
                    }
                })
            emission.sii_retention_diagnostics = diagnostics
            if failed_checks:
                print(f"SII_APP_FLOW_FAILED_CHECKS={','.join(failed_checks)}")
            failure_message = f"{type(error).__name__} at stage {browser.current_stage}"
            if failed_checks:
                failure_message += f"; checks={','.join(failed_checks)}"
            preview_diagnostics = getattr(error, "preview_diagnostics", {})
            if "preview_retention_mode_ok" in failed_checks and preview_diagnostics:
                failure_message += (
                    "; retention_requested="
                    f"{preview_diagnostics.get('requested_retention_mode') or 'unknown'}"
                    "; retention_detected="
                    f"{preview_diagnostics.get('detected_retention_mode') or 'unknown'}"
                    "; retention_evidence="
                    f"{preview_diagnostics.get('retention_evidence_kind') or 'none'}"
                )
            if getattr(error, "click_performed", False):
                mark_emission_status_unknown(
                    db, emission,
                    f"Resultado SII posterior al click no verificable; stage={browser.current_stage}",
                )
                return SIIProviderResult(
                    emission=emission, status="emission_status_unknown",
                    message="El resultado en SII requiere verificación manual. No vuelvas a emitir esta boleta.",
                    preview_validated=browser.current_stage != "preview_reached",
                    recipient_rut_ok=bool(browser.last_preview_result.get("recipient_rut_ok")),
                    sii_resolved_recipient_name_present=bool(browser.last_preview_result.get("sii_resolved_recipient_name_present")),
                    sii_resolved_recipient_name=browser.last_preview_result.get("sii_resolved_recipient_name"),
                    error=error,
                )
            mark_emission_failed(db, emission, failure_message)
            return SIIProviderResult(
                emission=emission, status="failed",
                message=("No fue posible validar el borrador SII. "
                         + (f"Fallos: {', '.join(failed_checks)}" if failed_checks else "Revisar diagnóstico del backend.")),
                preview_validated=False, recipient_rut_ok=False,
                sii_resolved_recipient_name_present=False, sii_resolved_recipient_name=None, error=error,
            )

    async def confirm_final(self, db: Session, emission: BHEEmission) -> dict[str, object]:
        claimed = begin_sii_finalization(db, emission.id)
        if claimed is None:
            raise ValueError("La emisión SII no está disponible para confirmación final.")
        try:
            automation = SIIBHEAutomationService(SiiBrowserService(replace(SiiPocSettings.from_environment(), pause_before_confirm=False)))
            result = await asyncio.to_thread(_run_sii_final_with_windows_subprocess_support, automation, self.automation_data_from_emission(claimed))
            mark_emission_success(db, claimed, result)
            return result
        except Exception as error:
            if getattr(error, "click_performed", False):
                mark_emission_status_unknown(db, claimed, "Resultado SII posterior al click no verificable.")
                raise RuntimeError("Resultado SII incierto; no reintentar automáticamente.") from error
            revert_sii_finalization(db, claimed, f"Finalización SII no iniciada: {type(error).__name__}")
            raise


def get_bhe_provider():
    return SIIBHEProvider()
    selected = BHEProviderName.SII
    try:
        selected_name = BHEProviderName.SII
    except ValueError as error:
        raise ValueError(f"Proveedor BHE inválido: {selected}") from error
    return SIIBHEProvider()
