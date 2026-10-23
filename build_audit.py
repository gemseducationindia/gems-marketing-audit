"""Marketing audit: campus x channel funnel from the live CRM + the admissions entry sheet -> audit.json (no PII)."""
import csv, datetime as dt, json, os, sys, time, urllib.parse, urllib.request, collections as C

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

leads = get("CRM Lead", ["name", "creation", "source", "territory", "status", "is_duplicate", "visit_state", "visit_done_on", "mobile_no", "phone",
                         "lost_reason"])
visits = get("GEMS Visit", ["enquiry", "status"])
calls = get("CRM Call Log", ["type", "status", "duration", "telephony_medium", "reference_docname", "to", "start_time", "creation"],
            [["type", "=", "Outgoing"]])
print("leads", len(leads), "visits", len(visits), "outgoing calls", len(calls))

leads = [l for l in leads if not l["is_duplicate"]]
ix = {l["name"]: l for l in leads}
by_phone = C.defaultdict(list)
for l in leads:
    for p in {d10(l["mobile_no"]), d10(l["phone"])} - {None}:
        by_phone[p].append(l)

# Visits per lead (GEMS Visit, or the lead's visit state as the CRM's fallback)
vs, vd = set(), set()
for v in visits:
    if v["enquiry"] in ix:
        vs.add(v["enquiry"])
        if v["status"] == "Done": vd.add(v["enquiry"])
for l in leads:
    if l["visit_state"] in ("Booked", "Attended", "No show"): vs.add(l["name"])
    if l["visit_state"] == "Attended" or l["visit_done_on"]: vd.add(l["name"]); vs.add(l["name"])

# Dials: every outgoing call, except a manual log within 15 min of an Exotel call to the same number
# (that is the counsellor logging the outcome of the same call, not a second dial).
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

# Admissions sheet: one row per student (exact duplicate submissions collapse)
num = lambda v: float(str(v).replace(",", "").strip() or 0)
yes = lambda v: (v or "").strip().lower() == "yes"
students, unmatched = {}, []
with open(os.path.join(HERE, "admissions.csv"), newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        campus = CAMPUS_ALIAS.get(r["Campus"].strip(), r["Campus"].strip())
        ph = d10(r["Parent phone number (10 digits, without +91)"])
        key = (campus, r["Student name"].strip().lower(), ph)
        app, adm, tui = yes(r["Application fee paid?"]), yes(r["Admission fee paid?"]), yes(r["Tuition / term fee paid?"])
        rev = (num(r["Application fee amount paid (₹)"]) if app else 0) + (num(r["Admission fee amount paid (₹)"]) if adm else 0) \
            + (num(r["Tuition / term fee amount paid (₹)"]) if tui else 0)
        students[key] = dict(campus=campus, phone=ph, app=app, enrol=adm or tui, rev=rev)

stats = C.defaultdict(lambda: C.Counter())
def add(campus, ch, **kv):
    for k, v in kv.items(): stats[(campus, ch)][k] += v

for l in leads:
    i, camp, ch = l["name"], l["territory"] or "Unassigned", channel(l["source"])
    add(camp, ch, leads=1, reached=int(dials[i] > 0), dials=dials[i], connects=conns[i],
        sched=int(i in vs), visited=int(i in vd), junk_lost=int(l["status"] in ("Junk", "Lost")))

match_stats = C.Counter()
for s in students.values():
    if not (s["app"] or s["enrol"] or s["rev"]): continue
    cands = by_phone.get(s["phone"], [])
    lead = next((l for l in cands if l["territory"] == s["campus"]), cands[0] if cands else None)
    ch = channel(lead["source"]) if lead else "Others"   # paying students with no CRM lead count as Others
    match_stats["matched" if lead else "unmatched"] += 1
    add(s["campus"], ch, apps=int(s["app"]), enrols=int(s["enrol"]), revenue=s["rev"])

# Junk/lost reasons per campus; free-text variants folded into the CRM's own reasons
REASON = {"not iooking for admissions": "Not looking for admission", "not inetersted": "Not looking for admission",
          "not interested": "Not looking for admission", "invalid": "Duplicate or invalid enquiry",
          "wrong number": "Duplicate or invalid enquiry", "testing": "Duplicate or invalid enquiry",
          "applied mistakenly": "Duplicate or invalid enquiry", "international number": "Duplicate or invalid enquiry",
          "he is part of school": "Duplicate or invalid enquiry", "looking for job": "Looking for a job, not admission",
          "interested only if she gets a job in gems": "Looking for a job, not admission",
          "no seat in requested grade": "Grade not available", "prefer cbse": "Curriculum mismatch",
          "state to cbse issue": "Curriculum mismatch"}
reasons = C.defaultdict(C.Counter)
for l in leads:
    if l["status"] in ("Junk", "Lost"):
        r = (l["lost_reason"] or "").strip() or "Not recorded"
        reasons[l["territory"] or "Unassigned"][REASON.get(r.lower(), r)] += 1

rows = [dict(campus=k[0], channel=k[1], **{f: v.get(f, 0) for f in ("leads", "reached", "dials", "connects", "sched", "visited",
                                                                  "junk_lost", "apps", "enrols", "revenue")}) for k, v in stats.items()]
out = dict(taken=(dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p IST"),
           rows=rows, reasons={c: r.most_common() for c, r in reasons.items()}, students=len(students), match=dict(match_stats))
json.dump(out, open(os.path.join(HERE, "audit.json"), "w"), indent=1)
print(json.dumps(out["match"]), len(students), "students;", len(rows), "campus x channel rows")
tot = C.Counter()
for r in rows:
    for k, v in r.items():
        if isinstance(v, (int, float)): tot[k] += v
print(dict(tot))
