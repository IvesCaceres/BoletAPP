# Dependencies and third-party terms

React, FastAPI, SQLAlchemy, Alembic, Pydantic, Playwright and their dependencies retain their own licenses. Requirements and package-lock.json are preserved; no environment, package sources, browser software or compiled runtime is vendored in this source candidate.

The previous audit observed primarily MIT/BSD/Apache terms for runtime packages. PyInstaller has an executable-bundling exception; its GPL metadata does not automatically impose GPL on this application. Chromium, FFmpeg and other future packaged components require their full notices and redistribution review. Owner rights and final project licensing remain pending.

The retained lock contains Vite 6.4.3 and Vitest 2.1.9. Known advisories include GHSA-9jrq-w75r-8gcw / GHSA-rq7h-c2jc-7f22 (Vite dev-server configurations) and GHSA-5xrq-8626-4rwp (Vitest UI/API/browser configurations). They do not establish a vulnerability of the static frontend. Do not expose dev/test servers; compatible updates and retesting remain pending. Sanitization does not silently change dependency versions.

Backend requirements use ranges, not a complete reproducible lock. Tests here reuse existing installed tools without new installations. This is not exhaustive legal/security certification. Official sources: https://pyinstaller.org/en/stable/license.html and the respective GitHub security advisory pages.
