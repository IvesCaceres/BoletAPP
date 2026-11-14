# AppBoletas

Independent Windows application for preparing and managing Chilean electronic professional-fee receipts (Boletas de Honorarios Electrónicas, BHE). It is not an official SII application or endorsement.

**Sanitized public-code candidate, not published. The original private project and v0.4.0 installer are preserved separately. No legacy Git history, real records, credentials or installer binaries are included here. MIT is proposed, not yet adopted.**

## Architecture and features

React + TypeScript + Vite frontend; local Python/FastAPI backend; SQLAlchemy/Alembic and SQLite persistence; SII web automation using Playwright. The current provider is SII, not SimpleFactura.

Client management, individual receipts, monthly/manual batches and local emission history are preserved. Preparation/preview is separated from final confirmation. Uncertain outcomes require manual review rather than automatic resubmission. Real data and authenticated browser sessions stay private. Review recipient, amount, date, detail and retention before any real confirmation.

## Local development

Review and approve dependencies before installing them into an isolated environment:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\python -m pip install -r requirements-test.txt
Copy-Item .env.example .env
.\venv\Scripts\python -m alembic upgrade head
.\venv\Scripts\python -m app.local
```

In another terminal, from frontend:

```powershell
npm ci
Copy-Item .env.example .env
npm run dev
```

These commands create new local dependencies/database; do not point a demonstration at a real private database. Configure personal settings only in ignored local files. Real SII login and emission are not part of setup or tests and require deliberate user action.

## Offline tests and fictional examples

Backend tests use invented data and mocks; frontend tests use fictional fixtures. See backend/tests/fixtures/README.md. No user databases, real receipts, sessions or credentials are provided.

```powershell
cd backend
python -B -m pytest -p no:cacheprovider
cd ..\frontend
npm run test:run
npm run build
```

Playwright fixture tests also need a matching Chromium runtime. On a new machine, review and authorize the browser download before running `python -m playwright install chromium`; this preparation reused existing binaries and performed no download. The explicit test requirements include the async test plugin.

Run tests with a fresh private temporary directory, no real .env, production credentials or browser profiles, and outbound access blocked. Some positive validator examples have valid check digits; this is not authorization to submit them to SII or a guarantee that numbers are globally unassigned. See docs/VALIDATION.md for actual isolation, test results and limitations; detailed logs remain outside the candidate. Passing offline tests does not validate live SII compatibility.

## Data protection

Only an explicit reviewed source-file allowlist is used. Original Git objects, populated databases, tax exports, .env, logs, browser cookies/profiles, private backups, cached files and legacy binaries are excluded. .gitignore helps prevent future mistakes but does not sanitize tracked history. Review staged files before every commit.

Do not publish individual receipt data or debug captures. The previous history contained real identifiers: those commits are kept only in the private backup, never imported here. A clean new history does not erase copies distributed earlier.

## Windows and preserved v0.4.0

Packaging sources remain under packaging/ and scripts/. The original v0.4.0 tag, installer and manifests are privately backed up byte-for-byte. They are not included, rebuilt, installed, republished or replaced by this preparation.

Windows clean-machine installer validation remains PENDING. No real emission is performed. See docs/WINDOWS_VALIDATION.md; prior packaging documents describe earlier evidence/plans, not certification of this candidate.

## Dependencies and proposed license

See docs/THIRD_PARTY.md, docs/LICENSE_PROPOSAL.md and docs/PRIVACY.md. Libraries retain their own licenses; no third-party environment, node_modules or runtime is distributed. Known development-tool advisories and backend dependency-lock limitations are documented. Avoid test UI/API exposure and review dependency updates before publication.

MIT for original software is only a proposal until rights, copyright holder and exact license are approved. It does not grant access to private records or replace third-party terms. No claim of tax, clinical or government validation is made.
