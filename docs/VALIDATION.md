# Validation of this sanitized source candidate

- Backend: 230 passed, 1 skipped.
- Frontend: 70 passed, 1 skipped.
- TypeScript compilation: PASS.
- Frontend production build: PASS.
- No live SII contact, real emission, private database/session use or installer execution.

Tests used new temporary databases, synthetic records, mocks and offline HTML fixtures. Existing local Python packages and a separately copied matching Chromium runtime were reused without installations or downloads. Browser fixture processes used fresh contexts with request blocking and a non-listening proxy; persistent browser contexts were forbidden. Frontend tests/build ran in a separate copy whose source matches this candidate, with dependency versions matching package-lock.json. Python bytecode/cache and all test outputs stayed outside the public candidate.

The test that previously required the checkout directory to be named AppBoletas now checks the actual checkout-root path. No application runtime logic was changed by this portability adjustment. Numeric examples were removed from validator comments/docstrings without changing its executable AST. Demo contacts use safe example domains. Original project files were not modified.

This is software evidence for synthetic inputs, not proof of live SII compatibility, tax accuracy, or a completed Windows installer qualification. Legacy v0.4.0 assets are privately preserved and excluded. Clean-machine installer validation is PENDING. MIT is now adopted for original code. Development dependency advisories remain a publication blocker; see THIRD_PARTY.md.

## Compatible development patches (2026-11-14)

Vite 6.4.6 and source-map-js 1.2.2 were installed from the lockfile in a new isolated frontend runtime with package lifecycle scripts disabled. Frontend: 70 passed, 1 skipped; TypeScript project compilation and noEmit check: PASS; production build: PASS. Outbound connections were blocked during tests/build. Application code, test sources, React and TypeScript versions were unchanged. Backend results above are the previous verified run; backend was not rerun because no backend files changed.

Vitest remains 2.1.9. A trial dependency resolution for 3.2.7 did not resolve all advisories and was discarded. npm audit still reports 6 affected package entries (3 moderate, 1 high, 2 critical), including nested development dependencies; this is not a clean security result. A larger Vitest migration requires separate review and is not implemented. No installer was rebuilt or redistributed.
