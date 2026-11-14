# Dependencies and third-party terms

React, FastAPI, SQLAlchemy, Alembic, Pydantic, Playwright and their dependencies retain their own licenses. Requirements and package-lock.json are preserved; no environment, package sources, browser software or compiled runtime is vendored in this source candidate.

The previous audit observed primarily MIT/BSD/Apache terms for runtime packages. PyInstaller has an executable-bundling exception; its GPL metadata does not automatically impose GPL on this application. Chromium, FFmpeg and other future packaged components require their full notices and redistribution review. The owner has authorized MIT for original project code; third-party rights and redistribution obligations remain separate.

The direct development dependency Vite is patched from 6.4.3 to 6.4.6, and transitive source-map-js from 1.2.1 to 1.2.2. Their upstream license notices remain in their packages. No new direct dependency is introduced. React, TypeScript and application sources are unchanged.

Vitest is now pinned to 4.1.12, the lowest stable release in the 4.x line that includes the required mocker fixes. This replaces 2.1.9 without changing test sources, configuration or application logic. The previous trial of 3.2.7 was insufficient; npm audit's recommendation of 5.1.0 was not proof that 5.x was required. Evaluating the patched 4.x line resolved the blocker with a smaller migration.

The updated lockfile removes tinypool, vite-node, nested Vite 5.4.21 and nested esbuild 0.21.5. The complete frontend npm audit reports zero known vulnerabilities as of 2026-11-14. This is scoped to the resolved npm tree and advisory database, not a claim that the application, Python dependencies or Windows installer are free of vulnerabilities. Runtime React dependencies, Vite 6.4.6 and TypeScript 5.9.3 are unchanged. No new direct dependency is added; required Vitest transitive packages retain their own licenses.

Use a supported Node release: Vitest 4.1.12 declares ^20.0.0 || ^22.0.0 || >=24.0.0; validation used Node 24.14.0. Vite 6.4.6 satisfies its Vite >=6 requirement. The v3/v4 migration guides were reviewed for mock resets/restores, fake timers, removed pool/coverage/browser settings, test options and discovery changes. The existing jsdom/globals/setupFiles/css configuration and all test sources work unchanged. No coverage, browser mode, custom pools or workspace configuration is used. Keep development/test servers private and do not execute untrusted code.

Security references and relevant fixes:

| Component | Advisory | Corrected version / resolution |
| --- | --- | --- |
| Vitest | GHSA-5xrq-8626-4rwp / CVE-2026-47429 | 4.1.0 in 4.x; adopted 4.1.12 |
| Vitest / mocker | GHSA-82fw-gwwq-j7x9 / CVE-2026-84373 | 4.1.11; adopted 4.1.12 |
| mocker | GHSA-qxg6-89mg-rqv8 / CVE-2026-112949 | 4.1.12 |
| tinypool | GHSA-5gmw-xhrv-c9v3 / CVE-2026-104848 | 2.1.1; dependency removed |
| tinypool | GHSA-85c8-ppgw-ccpr / CVE-2026-104849 | 2.1.2; dependency removed |
| esbuild | GHSA-67mh-4wv8-2f99 | >=0.25.0; old nested version removed |

These affected development/test infrastructure; UI/API/browser-server advisories depend on their enabled configuration. Such tools can compromise development/CI environments, so their exclusion from production JavaScript is not a complete mitigation. No listed affected nested versions remain in the new lockfile. Detailed affected/fixed ranges, additional nested Vite advisories and audit output are retained in the private preparation evidence.

Official references: https://v4.vitest.dev/guide/migration ; https://v3.vitest.dev/guide/migration ; https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8 ; https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9 ; https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp ; https://github.com/advisories/GHSA-85c8-ppgw-ccpr ; https://github.com/evanw/esbuild/security/advisories/GHSA-67mh-4wv8-2f99 .

Backend requirements use ranges, not a complete reproducible lock. The prior backend tests reused existing tools; the frontend patch validation installed the approved lockfile into an isolated runtime, with lifecycle scripts disabled. This is not exhaustive legal/security certification. Official sources: https://pyinstaller.org/en/stable/license.html and the respective GitHub security advisory pages.
