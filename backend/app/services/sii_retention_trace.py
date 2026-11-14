"""Opt-in, read-only retention observations that survive document navigation."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4


RETENTION_VALUES = {"RETCONTRIBUYENTE", "RETRECEPTOR"}
TRACE_STAGES = {
    "T0_form_located", "T1_after_correction", "T2_before_click",
    "T3_click_capture", "T4_submit_capture", "formdata_event", "T5_network_request",
    "formdata_enforcement",
}


class SiiRetentionTrace:
    """Observe without preventing events or replacing any SII handler/prototype."""

    def __init__(self, artifact_dir: Path, expected: str) -> None:
        self.artifact_dir = artifact_dir
        self.expected = expected
        self.binding = f"__appboletasRetentionTrace_{uuid4().hex}"
        self.snapshots: list[dict[str, object]] = []

    @staticmethod
    def _value(value: object) -> str | None:
        # Unknown field contents could be sensitive; only retention tokens leave the page.
        return value if isinstance(value, str) and value in RETENTION_VALUES else None

    def record(self, stage: str, snapshot: dict) -> None:
        if stage not in TRACE_STAGES or not isinstance(snapshot, dict) or len(self.snapshots) >= 80:
            return
        elements = []
        for element in snapshot.get("elements", [])[:40]:
            if not isinstance(element, dict):
                continue
            elements.append({
                "index": element.get("index") if isinstance(element.get("index"), int) else None,
                "tag": element.get("tag") if element.get("tag") in {"input", "select", "textarea"} else None,
                "type": element.get("type") if element.get("type") in {"hidden", "radio", "text", "select-one", "select-multiple", "textarea"} else None,
                "value": self._value(element.get("value")),
                "attribute_value": self._value(element.get("attribute_value")),
                "form_index": element.get("form_index") if isinstance(element.get("form_index"), int) else None,
                "belongs_to_target": element.get("belongs_to_target") is True,
                "disabled": element.get("disabled") is True,
                "checked": element.get("checked") if isinstance(element.get("checked"), bool) else None,
            })
        self.snapshots.append({
            "sequence": len(self.snapshots), "stage": stage, "expected_value": self.expected,
            "element_count": snapshot.get("element_count") if isinstance(snapshot.get("element_count"), int) else None,
            "target_connected": snapshot.get("target_connected") if isinstance(snapshot.get("target_connected"), bool) else None,
            "elements": elements,
            "serialized_retention_values": [self._value(value) for value in snapshot.get("serialized_retention_values", [])[:40]],
            **({
                "formdata_value_before_enforcement": [self._value(value) for value in snapshot.get("formdata_value_before_enforcement", [])[:40]],
                "formdata_value_after_enforcement": [self._value(value) for value in snapshot.get("formdata_value_after_enforcement", [])[:40]],
            } if stage == "formdata_enforcement" else {}),
        })
        (self.artifact_dir / "retention_mutation_trace.json").write_text(
            json.dumps({
                "diagnostic_only": True,
                "click_capture_order": "window capture listener; earlier window listeners may already have run",
                "submit_capture_limit": "native form.submit() bypasses the submit event",
                "submit_capture_scope": "DOM snapshot; formdata_event observes the supplied FormData without constructing another instance",
                "formdata_event_limit": "may originate from inspection or native serialization",
                "snapshots": self.snapshots,
            }, indent=2), encoding="utf-8",
        )

    def install(self, page, form) -> None:
        page.expose_binding(
            self.binding, lambda _source, stage, snapshot: self.record(stage, snapshot)
        )
        form.evaluate(
            """(form, binding) => {
                const token = value => ['RETCONTRIBUYENTE', 'RETRECEPTOR'].includes(value) ? value : null;
                const snapshot = () => {
                    const elements = Array.from(document.querySelectorAll('[name="OptTipoRetencion"]'));
                    return {
                        element_count: elements.length, target_connected: form.isConnected,
                        elements: elements.slice(0, 40).map((element, index) => ({
                            index, tag: element.tagName.toLowerCase(), type: element.type,
                            value: token(element.value), attribute_value: token(element.getAttribute('value')),
                            form_index: Array.from(document.forms).indexOf(element.form),
                            belongs_to_target: element.form === form,
                            disabled: element.matches(':disabled'), checked: element.type === 'radio' ? element.checked : null
                        }))
                    };
                };
                window[binding + '_snapshot'] = snapshot;
                const report = (stage, values) => {
                    const data = snapshot();
                    data.serialized_retention_values = values ? Array.from(values).map(token) : [];
                    // The binding transfers data before navigation discards this document.
                    window[binding](stage, data).catch(() => {});
                };
                window.addEventListener('click', event => {
                    const control = event.target.closest && event.target.closest('button,input');
                    if (control && control.form === form) report('T3_click_capture');
                }, true);
                window.addEventListener('submit', event => {
                    if (event.target === form || event.target.querySelector('[name="OptTipoRetencion"]'))
                        report('T4_submit_capture');
                }, true);
                window.addEventListener('formdata', event => {
                    if (event.target === form || event.formData.has('OptTipoRetencion'))
                        report('formdata_event', event.formData.getAll('OptTipoRetencion'));
                }, true);
            }""", self.binding,
        )
        self.snapshot(page, "T0_form_located")

    def snapshot(self, page, stage: str, serialized_values: list[str] | None = None) -> None:
        data = page.evaluate(
            "binding => window[binding + '_snapshot'] ? window[binding + '_snapshot']() : null",
            self.binding,
        )
        if isinstance(data, dict):
            if serialized_values is not None:
                data["serialized_retention_values"] = serialized_values
            self.record(stage, data)
