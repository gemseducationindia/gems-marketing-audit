# GEMS marketing audit

Funnel from agency spend to enrollment and revenue for each campus and channel (Meta, Google, Website, Others), with drill-down to campaign and ad, junk/lost reasons, grades asked for, parents' voice and personas, and a week-on-week view. Each login sees only its own campuses: https://gemseducationindia.github.io/gems-marketing-audit/

**Refreshes every night at 06:00 IST** (GitHub Actions, `.github/workflows/nightly.yml`), after the call audit's 04:00 sync and Claude's 05:30 scoring. It can also be run by hand from the Actions tab.

- `build_audit.py` pulls leads, calls and visits from the GEMS CRM, the agencies' live spend sheets (Edelytics, EchoVME, Because), fee payments and applications recorded on CRM deals (admissions and revenue come from the CRM, no sheet or CSV) and the call audit's scored calls and personas (private repo gems-call-audit-data). It joins them by the parent's mobile number and writes `audit.json`: one record per lead, fee and spend row, with no names or numbers (parents' quotes from calls are the only free text).
- `publish.py audit.json "<date>"` writes one encrypted file per login (`docs/d-*.enc`, AES-256-GCM, key from that login's password via PBKDF2-SHA256, 600k iterations), each holding only that login's campuses.
- Secrets (`CRM_TOKEN`, `MARKETING_SHEETS`, `MARKETING_USERS`, `CALL_AUDIT_TOKEN`) live in the repo's Actions secrets; `set_secrets.py` copies them from the chmod-600 files on the Mac after a change.
