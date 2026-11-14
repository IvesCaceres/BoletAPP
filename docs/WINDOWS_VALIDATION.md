# Windows validation limits

Unit tests and a frontend production build do not demonstrate clean Windows installation or real SII success. The legacy v0.4.0 installer/manifests are privately preserved, not included or rebuilt. End-to-end Windows installer validation is PENDING.

Future authorized packaging needs an isolated Windows environment, reviewed runtime notices, strict payload allowlist and checks excluding user data, databases, .env, sessions, logs and backups. Test startup/close/update/uninstall only with invented records and no SII access. Existing packaging documents remain historical evidence/plans, not a new certification.
