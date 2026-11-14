# Dependencies and third-party terms

React, FastAPI, SQLAlchemy, Alembic, Pydantic, Playwright and their dependencies retain their own licenses. Requirements and package-lock.json are preserved; no environment, package sources, browser software or compiled runtime is vendored in this source candidate.

The previous audit observed primarily MIT/BSD/Apache terms for runtime packages. PyInstaller has an executable-bundling exception; its GPL metadata does not automatically impose GPL on this application. Chromium, FFmpeg and other future packaged components require their full notices and redistribution review. The owner has authorized MIT for original project code; third-party rights and redistribution obligations remain separate.

The direct development dependency Vite is patched from 6.4.3 to 6.4.6, and transitive source-map-js from 1.2.1 to 1.2.2. Their upstream license notices remain in their packages. No new direct dependency is introduced. React, TypeScript and application sources are unchanged.

Development dependency security is still BLOCKED: Vitest 2.1.9 and its nested Vite/esbuild, mocker and tinypool dependencies retain known advisories. A trial resolution to Vitest 3.2.7 still retained mocker/tinypool advisories and was not adopted. The current npm audit recommends Vitest 5.1.0, a major migration requiring explicit evaluation before implementation; do not treat these patch updates as full remediation. The official Vitest 5 migration guide requires Node >=22.12.0 and Vite >=6.4.0 and documents breaking changes. Do not expose dev/test servers, enable UI/API/browser mode, or run untrusted tests/source maps. Offline tests are run with outbound connections blocked; this does not eliminate dependency vulnerabilities.

References: https://github.com/vitejs/vite/security/advisories/GHSA-p4f5-76q4-ffq7 ; https://github.com/advisories/GHSA-68fv-2mgg-jv7q ; https://github.com/advisories/GHSA-85c8-ppgw-ccpr ; https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8 ; https://vitest.dev/guide/migration/ . Detailed registry audit output is retained privately outside this candidate.

Backend requirements use ranges, not a complete reproducible lock. The prior backend tests reused existing tools; the frontend patch validation installed the approved lockfile into an isolated runtime, with lifecycle scripts disabled. This is not exhaustive legal/security certification. Official sources: https://pyinstaller.org/en/stable/license.html and the respective GitHub security advisory pages.
