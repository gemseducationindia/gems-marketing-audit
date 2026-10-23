"""Marketing audit data: one record per lead, per fee payment and per day of agency spend, so the page can
recount any date range. No names, phone numbers or emails leave this script. Writes audit.json.

Usage: python3 build_audit.py   (needs ~/.gems_crm_token, admissions.csv beside it; env EDELYTICS_SHEET for spend)
"""
import csv, datetime as dt, io, json, os, re, time, urllib.parse, urllib.request, collections as C
import openpyxl

B = "https://gemseducation.m.frappe.cloud"
T = open(os.path.expanduser("~/.gems_crm_token")).read().strip()
HERE = os.path.dirname(os.path.abspath(__file__))


def get(doctype, fields, filters=()):
    out, start = [], 0
    while True:
        q = urllib.parse.urlencode({"fields": json.dumps(fields), "filters": json.dumps(list(filters)),
                                    "limit_start": start, "limit_page_length": 500})
        for attempt in range(6):
            try:
                rows = json.load(urllib.request.urlopen(urllib.request.Request(
                    f"{B}/api/resource/{urllib.parse.quote(doctype)}?{q}", headers={"Authorization": T}), timeout=150))["data"]
                break
            except Exception as e:
                if attempt == 5: raise
                print("retry", doctype, e, flush=True); time.sleep(5 * (attempt + 1))
        out += rows
        if len(rows) < 500: return out
        start += 500


d10 = lambda p: (lambda s: s[-10:] if len(s) >= 10 else None)("".join(c for c in (p or "") if c.isdigit()))
CHANNEL = {"Meta Ads": "Meta", "Instagram Ads": "Meta", "Facebook": "Meta", "Google Ads": "Google", "Website Enquiry": "Website"}
channel = lambda src: CHANNEL.get(src or "", "Others")
CAMPUS_ALIAS = {"Cresmonde World School - Vikhroli": "GEMS Millennium School - Vikhroli",
                "Cresmonde World School - Kalyan": "GEMS Millennium School - Kalyan"}
SHORT = {"GMS": "GEMS Millennium School", "GMA": "GEMS Modern Academy", "GSE": "GEMS School of Excellence"}
REASON = {"not iooking for admissions": "Not looking for admission", "not inetersted": "Not looking for admission",
          "not interested": "Not looking for admission", "invalid": "Duplicate or invalid enquiry",
          "wrong number": "Duplicate or invalid enquiry", "testing": "Duplicate or invalid enquiry",
          "applied mistakenly": "Duplicate or invalid enquiry", "international number": "Duplicate or invalid enquiry",
          "he is part of school": "Duplicate or invalid enquiry", "looking for job": "Looking for a job, not admission",
          "interested only if she gets a job in gems": "Looking for a job, not admission",
          "no seat in requested grade": "Grade not available", "prefer cbse": "Curriculum mismatch",
          "state to cbse issue": "Curriculum mismatch"}

leads = get("CRM Lead", ["name", "creation", "source", "territory", "status", "is_duplicate", "visit_state", "visit_done_on",
                         "mobile_no", "phone", "lost_reason"])
visits = get("GEMS Visit", ["enquiry", "status"])
calls = get("CRM Call Log", ["type", "status", "duration", "telephony_medium", "reference_docname", "to", "start_time", "creation"],
            [["type", "=", "Outgoing"]])
print("leads", len(leads), "visits", len(visits), "outgoing calls", len(calls))

# Leads with no campus are left out of the report entirely
leads = [l for l in leads if not l["is_duplicate"] and l["territory"]]
ix = {l["name"]: l for l in leads}
by_phone = C.defaultdict(list)
for l in leads:
    for p in {d10(l["mobile_no"]), d10(l["phone"])} - {None}:
        by_phone[p].append(l)

vs, vd = set(), set()
for v in visits:
    if v["enquiry"] in ix:
        vs.add(v["enquiry"])
        if v["status"] == "Done": vd.add(v["enquiry"])
for l in leads:
    if l["visit_state"] in ("Booked", "Attended", "No show"): vs.add(l["name"])
    if l["visit_state"] == "Attended" or l["visit_done_on"]: vd.add(l["name"]); vs.add(l["name"])

# Dials: outgoing calls, except a manual outcome note logged within 15 min of an Exotel call to the same number
ts = lambda c: dt.datetime.fromisoformat((c["start_time"] or c["creation"])[:19])
exo_at = C.defaultdict(list)
for c in calls:
    if c["telephony_medium"] == "Exotel": exo_at[d10(c["to"])].append(ts(c))
dials, conns = C.Counter(), C.Counter()
for c in calls:
    if c["telephony_medium"] != "Exotel" and any(abs((ts(c) - t).total_seconds()) <= 900 for t in exo_at.get(d10(c["to"]), [])):
        continue
    ids = [c["reference_docname"]] if c["reference_docname"] in ix else [l["name"] for l in by_phone.get(d10(c["to"]), [])]
    for i in ids:
        dials[i] += 1
        if c["status"] == "Completed" and (c["duration"] or 0) > 0: conns[i] += 1

campuses, channels, reasons = [], ["Meta", "Google", "Website", "Others"], []
idx = lambda lst, v: lst.index(v) if v in lst else (lst.append(v) or len(lst) - 1)

# Leads: [created on, campus, channel, reached, dials, connects, visit scheduled, visit done, junk/lost, reason or -1]
L = []
for l in leads:
    i, jl = l["name"], l["status"] in ("Junk", "Lost")
    r = -1
    if jl:
        txt = (l["lost_reason"] or "").strip() or "Not recorded"
        r = idx(reasons, REASON.get(txt.lower(), txt))
    L.append([l["creation"][:10], idx(campuses, l["territory"]), channels.index(channel(l["source"])), int(dials[i] > 0),
              dials[i], conns[i], int(i in vs), int(i in vd), int(jl), r])

# Admissions sheet: one entry per student; each fee counts on its own payment date
def pdate(s):
    s = (s or "").strip()
    for f in ("%d/%m/%y", "%d/%m/%Y"):
        try: return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    return None
num = lambda v: float(str(v).replace(",", "").strip() or 0)
yes = lambda v: (v or "").strip().lower() == "yes"
students = {}
with open(os.path.join(HERE, "admissions.csv"), newline="", encoding="utf-8") as f:   # export of the admissions entry sheet; never committed
    for r in csv.DictReader(f):
        campus = CAMPUS_ALIAS.get(r["Campus"].strip(), r["Campus"].strip())
        ph = d10(r["Parent phone number (10 digits, without +91)"])
        students[(campus, r["Student name"].strip().lower(), ph)] = (campus, ph, r)

S, F, unmatched = [], [], 0
for campus, ph, r in students.values():
    fees = [(k, pdate(r[f"{label} fee payment date (DD/MM/YYYY)"]), num(r[f"{label} fee amount paid (₹)"]))
            for k, label in (("app", "Application"), ("adm", "Admission"), ("tui", "Tuition / term"))
            if yes(r[f"{label} fee paid?" if label != "Tuition / term" else "Tuition / term fee paid?"])]
    if not fees: continue
    cands = by_phone.get(ph, [])
    lead = next((l for l in cands if l["territory"] == campus), cands[0] if cands else None)
    unmatched += lead is None
    ci, ch = idx(campuses, campus), channels.index(channel(lead["source"]) if lead else "Others")  # no CRM lead -> Others
    app = next((d for k, d, _ in fees if k == "app"), None)
    enrol = min([d for k, d, _ in fees if k in ("adm", "tui") and d] or [None])
    # Students: [campus, channel, application date or null, enrollment date or null]
    S.append([ci, ch, app, enrol])
    # Fees: [paid on, campus, channel, amount]
    F += [[d, ci, ch, a] for _, d, a in fees if d and a]

# Agency spend per day. Edelytics: one tab per campus (e.g. "GMS Gurugram"), Google Ads in A-F, Meta Ads in H-M.
SP, spend_upto = [], {}
def campus_of(tab):
    p = tab.strip().split(" ", 1)
    name = f"{SHORT.get(p[0], p[0])} - {p[1]}" if len(p) == 2 else tab
    return name if name in campuses or any(l["territory"] == name for l in leads) else None
sheet = os.environ.get("EDELYTICS_SHEET")
if sheet:
    wb = openpyxl.load_workbook(io.BytesIO(urllib.request.urlopen(
        f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=xlsx", timeout=90).read()), data_only=True)
    for ws in wb:
        camp = campus_of(ws.title)
        if not camp: print("skipping Edelytics tab", ws.title); continue
        for r in ws.iter_rows(values_only=True):
            for ch, c in (("Google", 0), ("Meta", 7)):
                if len(r) > c + 3 and isinstance(r[c], dt.datetime) and isinstance(r[c + 3], (int, float)) and r[c + 3]:
                    n = lambda v: v if isinstance(v, (int, float)) else 0
                    # Spend: [day, campus, channel, spend, impressions, clicks, agency-reported conversions]
                    SP.append([r[c].date().isoformat(), idx(campuses, camp), channels.index(ch), round(r[c + 3], 2),
                               n(r[c + 1]), n(r[c + 2]), n(r[c + 4])])
                    spend_upto["Edelytics"] = max(spend_upto.get("Edelytics", ""), r[c].date().isoformat())

now = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
out = dict(taken=now.strftime("%d %b %Y, %I:%M %p IST"), today=now.date().isoformat(), campuses=campuses, channels=channels,
           reasons=reasons, leads=L, students=S, fees=F, spend=SP, spend_upto=spend_upto)
json.dump(out, open(os.path.join(HERE, "audit.json"), "w"), separators=(",", ":"))
print(len(L), "leads,", len(S), "paying students (", unmatched, "with no CRM lead ),", len(F), "fees,", len(SP), "spend days")
print("junk/lost with reason:", sum(1 for x in L if x[8] and x[9] >= 0), "of", sum(x[8] for x in L))
print("revenue", sum(f[3] for f in F), "spend", round(sum(s[3] for s in SP)), spend_upto)
