# Validation of this sanitized source candidate

- Backend: 230 passed, 1 skipped.
- Frontend: 70 passed, 1 skipped.
- TypeScript compilation: PASS.
- Frontend production build: PASS.
- No live SII contact, real emission, private database/session use or installer execution.

Tests used new temporary databases, synthetic records, mocks and offline HTML fixtures. Existing local Python packages and a separately copied matching Chromium runtime were reused without installations or downloads. Browser fixture processes used fresh contexts with request blocking and a non-listening proxy; persistent browser contexts were forbidden. Frontend tests/build ran in a separate copy whose source matches this candidate, with dependency versions matching package-lock.json. Python bytecode/cache and all test outputs stayed outside the public candidate.

The test that previously required the checkout directory to be named AppBoletas now checks the actual checkout-root path. No application runtime logic was changed by this portability adjustment. Numeric examples were removed from validator comments/docstrings without changing its executable AST. Demo contacts use safe example domains. Original project files were not modified.

This is software evidence for synthetic inputs, not proof of live SII compatibility, tax accuracy, or a completed Windows installer qualification. Legacy v0.4.0 assets are privately preserved and excluded. Clean-machine installer validation is PENDING. Known development dependency advisories and final project-license adoption remain open before publication.
