# GEMS marketing audit

Lead-to-enrollment funnel for each campus and channel (Meta, Google, Website, Others), password-protected.

- `build_audit.py` pulls leads, calls and visits from the GEMS CRM and joins the admissions entry sheet (`admissions.csv`, an export kept out of git) by the parent's mobile number. It writes `audit.json`: campus × channel totals only, no names or numbers.
- `publish.py audit.json "<sheet date>"` encrypts that into `docs/data.enc` (AES-256-GCM, key from the password via PBKDF2-SHA256, 600k iterations, salt in `docs/key.json`). The browser derives the same key, so the repo can be public while the numbers are not.
- Run: `python3 build_audit.py && AUDIT_PASSWORD=... python3 publish.py audit.json "22 Oct 2026"`, then commit `docs/`.
