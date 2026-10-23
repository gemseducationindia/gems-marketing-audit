# GEMS marketing audit

Funnel from agency spend to enrollment and revenue for each campus and channel (Meta, Google, Website, Others), with drill-down to campaign and ad, junk/lost reasons by channel, and a week-on-week view. Each login sees only its own campuses.

- `build_audit.py` pulls leads, calls and visits from the GEMS CRM, the agencies' live spend sheets (Edelytics, EchoVME, Because; IDs in `~/.gems_marketing_audit_sheets.json`, kept off this repo) and the admissions entry sheet (`admissions.csv`, an export kept out of git), joined by the parent's mobile number. It writes `audit.json`: one record per lead, fee and spend row, with no names or numbers.
- `publish.py audit.json "<sheet date>"` writes one encrypted file per login (`docs/d-*.enc`, AES-256-GCM, key from that login's password via PBKDF2-SHA256, 600k iterations), each holding only that login's campuses. Logins live in `~/.gems_marketing_audit_users.json`; `docs/users.json` only maps a hash of each username to its salt and file.
- Run: `python3 build_audit.py && python publish.py audit.json "22 Oct 2026"`, then commit `docs/`.
