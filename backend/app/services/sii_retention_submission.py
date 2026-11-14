"""Enforce only retention on the native FormData of one confirmation form."""
from uuid import uuid4


class SiiRetentionSubmission:
    def __init__(self, expected: str) -> None:
        self.expected = expected
        self.key = f"__appboletasRetentionSubmission_{uuid4().hex}"

    def install(self, form, diagnostic_binding: str | None = None) -> None:
        form.evaluate(
            """(form, options) => {
                const token = value => ['RETCONTRIBUYENTE', 'RETRECEPTOR'].includes(value) ? value : null;
                const listener = event => {
                    const before = event.formData.getAll('OptTipoRetencion');
                    const controls = Array.from(form.elements).filter(element => element.name === 'OptTipoRetencion');
                    // Never conceal missing, duplicate or disabled fields by adding/collapsing entries.
                    const valid = form.isConnected && controls.length === 1 &&
                        controls[0].type === 'hidden' && !controls[0].matches(':disabled') && before.length === 1;
                    if (valid) event.formData.set('OptTipoRetencion', options.expected);
                    const after = event.formData.getAll('OptTipoRetencion');
                    if (options.binding && window[options.binding]) {
                        window[options.binding]({
                            formdata_value_before_enforcement: before.map(token),
                            formdata_value_after_enforcement: after.map(token),
                            formdata_enforcement_applied: valid,
                        }).catch(() => {});
                    }
                };
                form.addEventListener('formdata', listener);
                window[options.key] = () => {
                    form.removeEventListener('formdata', listener);
                    delete window[options.key];
                };
            }""",
            {"expected": self.expected, "key": self.key, "binding": diagnostic_binding},
        )

    def dispose(self, page) -> None:
        page.evaluate("key => { if (window[key]) window[key](); }", self.key)
