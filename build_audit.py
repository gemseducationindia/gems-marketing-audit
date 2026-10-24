"""Marketing audit data: one record per lead, per fee payment and per day of agency spend, so the page can
recount any date range and drill into source, campaign and ad. No names, phone numbers or emails leave this script.
Writes audit.json.

Usage: python3 build_audit.py
Needs the CRM token (CRM_TOKEN, or ~/.gems_crm_token) and the live sheet IDs (MARKETING_SHEETS, or ~/.gems_marketing_audit_sheets.json:
{"Edelytics": id, "EchoVME": id, "Because": id, "Admissions": id}); the IDs stay off this public repo. Admissions come from the latest CSV
export the user shares, kept in the private call audit repo as admissions/admissions.csv. Parents' voice comes from the
call audit repo (CALL_AUDIT_DIR, default the local clone). GitHub Actions runs this nightly (.github/workflows/nightly.yml).
"""
import csv, datetime as dt, glob, io, json, os, time, urllib.parse, urllib.request, collections as C
import openpyxl, re

B = "https://gemseducation.m.frappe.cloud"
T = (os.environ.get("CRM_TOKEN") or open(os.path.expanduser("~/.gems_crm_token")).read()).strip()
T = T if T.startswith("token ") else "token " + T
HERE = os.path.dirname(os.path.abspath(__file__))
SHEETS = json.loads(os.environ["MARKETING_SHEETS"]) if os.environ.get("MARKETING_SHEETS") else json.load(open(os.path.expanduser("~/.gems_marketing_audit_sheets.json")))


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


def workbook(agency):
    for attempt in range(4):
        try:
            return openpyxl.load_workbook(io.BytesIO(urllib.request.urlopen(
                f"https://docs.google.com/spreadsheets/d/{SHEETS[agency]}/export?format=xlsx", timeout=90).read()), data_only=True)
        except Exception as e:
            if attempt == 3: raise
            print("retry sheet", agency, e, flush=True); time.sleep(5)


d10 = lambda p: (lambda s: s[-10:] if len(s) >= 10 else None)("".join(c for c in (p or "") if c.isdigit()))
num = lambda v: v if isinstance(v, (int, float)) else 0
CHANNEL = {"Meta Ads": "Meta", "Instagram Ads": "Meta", "Facebook": "Meta", "Google Ads": "Google", "Website Enquiry": "Website"}
channel = lambda src: CHANNEL.get(src or "", "Others")
CAMPUS_ALIAS = {"Cresmonde World School - Vikhroli": "GEMS Millennium School - Vikhroli",
                "Cresmonde World School - Kalyan": "GEMS Millennium School - Kalyan"}
SHORT = {"GMS": "GEMS Millennium School", "GMA": "GEMS Modern Academy", "GSE": "GEMS School of Excellence"}
INVALID = "Invalid lead"
# Lost/junk reasons clubbed into short labels; anything not listed shows as recorded
_R = {INVALID: ["duplicate or invalid enquiry", "invalid", "wrong number", "testing", "applied mistakenly", "international number", "he is part of school"],
      "Not looking now": ["not looking for admission", "not iooking for admissions", "not inetersted", "not interested",
                          "continuing in the same previous school", "deferred to next academic year", "moving to another country"],
      "Too far / out of zone": ["distance / transport not viable", "out of zone"],
      "Job seeker": ["looking for job", "interested only if she gets a job in gems"],
      "Grade not available": ["grade not available", "no seat in requested grade"],
      "Curriculum mismatch": ["prefer cbse", "state to cbse issue"],
      "Fees too high": ["fee too high"], "Didn't clear interaction": ["did not clear interaction"], "No reason given": ["not recorded", ""]}
REASON = {raw: label for label, raws in _R.items() for raw in raws}

leads = get("CRM Lead", ["name", "creation", "source", "territory", "status", "is_duplicate", "visit_state", "visit_done_on",
                         "mobile_no", "phone", "lost_reason", "utm_campaign", "utm_content", "grade_applied"])
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

campuses, channels, reasons, labels = [], ["Meta", "Google", "Website", "Others"], [], []
idx = lambda lst, v: lst.index(v) if v in lst else (lst.append(v) or len(lst) - 1)
lab = lambda v: idx(labels, (v or "").strip() or "(not set)")
tags = lambda l: [lab(l["source"]), lab(l["utm_campaign"]), lab(l["utm_content"])]   # source, campaign, ad

# Leads: [created on, campus, channel, contacted, dials, connects, visit scheduled, visit done, junk/lost, reason or -1,
#         source, campaign, ad, grade applied for (these four index into labels), applied, enrolled]
L = []
for l in leads:
    i, jl = l["name"], l["status"] in ("Junk", "Lost")
    r = -1
    if jl:
        txt = (l["lost_reason"] or "").strip()
        r = idx(reasons, REASON.get(txt.lower(), txt))
    L.append([l["creation"][:10], idx(campuses, l["territory"]), channels.index(channel(l["source"])), int(dials[i] > 0),
              dials[i], conns[i], int(i in vs), int(i in vd), int(jl), r] + tags(l) + [lab(l["grade_applied"])])

# Admissions sheet: one entry per student; each fee counts on its own payment date
def pdate(s):
    s = (s or "").strip()
    for f in ("%d/%m/%y", "%d/%m/%Y"):
        try: return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    return None
amt = lambda v: float(str(v).replace(",", "").strip() or 0)
yes = lambda v: (v or "").strip().lower() == "yes"
students = {}
# Admissions entry sheet: the latest CSV export the user shares, kept in the private call audit repo (admissions/admissions.csv);
# before the first one, the Google Sheet copy, or a local admissions.csv.
CA = os.environ.get("CALL_AUDIT_DIR") or os.path.expanduser("~/Library/CloudStorage/OneDrive-GEMS/CodeTech/GEMS CRM Frappe/gems-call-audit")
ADM = os.path.join(CA, "admissions", "admissions.csv")
if os.path.exists(ADM): adm = open(ADM, encoding="utf-8-sig").read()
elif SHEETS.get("Admissions"):
    adm = urllib.request.urlopen(f"https://docs.google.com/spreadsheets/d/{SHEETS['Admissions']}/export?format=csv", timeout=90).read().decode("utf-8")
else: adm = open(os.path.join(HERE, "admissions.csv"), encoding="utf-8-sig").read()
if True:
    for r in csv.DictReader(io.StringIO(adm)):
        if not (r.get("Campus") or "").strip(): continue
        campus = CAMPUS_ALIAS.get(r["Campus"].strip(), r["Campus"].strip())
        ph = d10(r["Parent phone number (10 digits, without +91)"])
        students[(campus, r["Student name"].strip().lower(), ph)] = (campus, ph, r)

S, F, unmatched, paid = [], [], 0, {}
for campus, ph, r in students.values():
    fees = [(k, pdate(r[f"{label} fee payment date (DD/MM/YYYY)"]), amt(r[f"{label} fee amount paid (₹)"]))
            for k, label in (("app", "Application"), ("adm", "Admission"), ("tui", "Tuition / term"))
            if yes(r[f"{label} fee paid?" if label != "Tuition / term" else "Tuition / term fee paid?"])]
    if not fees: continue
    cands = by_phone.get(ph, [])
    lead = next((l for l in cands if l["territory"] == campus), cands[0] if cands else None)
    unmatched += lead is None
    if lead: paid[lead["name"]] = [max(paid.get(lead["name"], [0, 0])[0], int(any(k == "app" for k, _, _ in fees))),
                                   max(paid.get(lead["name"], [0, 0])[1], int(any(k in ("adm", "tui") for k, _, _ in fees)))]
    ci, ch = idx(campuses, campus), channels.index(channel(lead["source"]) if lead else "Others")  # no CRM lead -> Others
    tg = tags(lead) if lead else [lab("No CRM lead"), lab(None), lab(None)]
    app = next((d for k, d, _ in fees if k == "app"), None)
    enrol = min([d for k, d, _ in fees if k in ("adm", "tui") and d] or [None])
    # Students: [campus, channel, application date or null, enrollment date or null, source, campaign, ad]
    S.append([ci, ch, app, enrol] + tg)
    # Fees: [paid on, campus, channel, amount, source, campaign, ad]
    F += [[d, ci, ch, a] + tg for _, d, a in fees if d and a]

# Agency spend per day: [day, campus, channel, spend, impressions, clicks, agency-reported leads, reach, campaign or -1]
SP, spend_upto = [], {}
def spend(agency, day, camp, ch, amount, impr=0, clicks=0, ag_leads=0, reach=0, campaign=None):
    SP.append([day.date().isoformat(), idx(campuses, camp), channels.index(ch), round(amount, 2), num(impr), num(clicks),
               num(ag_leads), num(reach), lab(campaign) if campaign else -1])
    spend_upto[agency] = max(spend_upto.get(agency, ""), day.date().isoformat())

def campus_of(tab):   # "GMS Gurugram" -> "GEMS Millennium School - Gurugram"
    p = tab.strip().split(" ", 1)
    name = f"{SHORT.get(p[0], p[0])} - {p[1]}" if len(p) == 2 else tab
    return name if name in campuses or any(l["territory"] == name for l in leads) else None

# Edelytics: one tab per campus; row 1 titles each block ("Google Ads - Performance", "Meta Ads - Performance", "Total"),
# row 2 names its columns (Day, Impr., Reach, Clicks, Cost, Conversions...). Columns differ by tab, so go by the names.
for ws in workbook("Edelytics"):
    camp = campus_of(ws.title)
    if not camp: print("skipping Edelytics tab", ws.title); continue
    rows = list(ws.iter_rows(values_only=True))
    starts = [(i, str(v)) for i, v in enumerate(rows[0]) if v] + [(len(rows[1]), "")]
    for (c0, title), (c1, _) in zip(starts, starts[1:]):
        ch = "Google" if "google" in title.lower() else "Meta" if "meta" in title.lower() else None
        if not ch: continue
        col = {str(rows[1][i]).strip().lower(): i for i in range(c0, c1) if rows[1][i]}
        g = lambda r, k: r[col[k]] if k in col and col[k] < len(r) else 0
        for r in rows[2:]:
            if isinstance(g(r, "day"), dt.datetime) and num(g(r, "cost")):
                spend("Edelytics", g(r, "day"), camp, ch, g(r, "cost"), impr=g(r, "impr."), clicks=g(r, "clicks"),
                      ag_leads=g(r, "conversions"), reach=g(r, "reach"))

# EchoVME: a tab per month with side-by-side blocks per campus/platform/objective (no clicks). Same blocks as the
# leadership report's agencies/echovme.py: tab -> [(campus, channel, date, spend, impressions, reach, leads columns)].
RAIPUR, CBE = "GEMS Millennium School - Raipur", "GEMS Modern Academy - Coimbatore"
ECHO_BLOCKS = {
    "Sept": [(RAIPUR, "Meta", 0, 1, 3, 2, None), (CBE, "Meta", 0, 6, 8, 7, None),
             (RAIPUR, "Google", 11, 12, 13, None, None), (CBE, "Google", 11, 16, 17, None, None)],
    "Oct": [(RAIPUR, "Meta", 0, 1, 3, 2, None), (CBE, "Meta", 0, 6, 8, 7, None), (CBE, "Meta", 0, 11, None, None, 12),
            ("GEMS Millennium School - Vikhroli", "Meta", 0, 15, 17, 16, None), ("GEMS Millennium School - Kalyan", "Meta", 0, 20, 22, 21, None),
            (CBE, "Google", 25, 26, 27, None, None), (CBE, "Google", 25, 29, None, None, 30)],
}
wb = workbook("EchoVME")
for tab, blocks in ECHO_BLOCKS.items():
    if tab not in wb.sheetnames: print("missing EchoVME tab", tab); continue
    for r in wb[tab].iter_rows(min_row=5, values_only=True):
        for camp, ch, dc, sc, ic, rc, lc in blocks:
            v = lambda c: num(r[c]) if c is not None and c < len(r) else 0
            if dc < len(r) and isinstance(r[dc], dt.datetime) and v(sc):
                spend("EchoVME", r[dc], camp, ch, v(sc), impr=v(ic), ag_leads=v(lc), reach=v(rc))

# Because: one tab per campus ("GMS Kochi"), Meta only, one row per day and campaign:
# Day, Campaign name, Objective, Delivery, Amount spent, Impressions, Reach, Link clicks, Leads, CTR, Frequency
for ws in workbook("Because"):
    camp = campus_of(ws.title)
    if not camp: print("skipping Because tab", ws.title); continue
    for r in ws.iter_rows(min_row=2, values_only=True):
        if len(r) > 8 and isinstance(r[0], dt.datetime) and num(r[4]):
            spend("Because", r[0], camp, "Meta", r[4], impr=r[5], reach=r[6], clicks=r[7], ag_leads=r[8], campaign=r[1])

for rec, l in zip(L, leads): rec += paid.get(l["name"], [0, 0])   # applied, enrolled (any time)
conv = {l["name"]: [int(l["name"] in vd)] + paid.get(l["name"], [0, 0]) for l in leads}   # visit done, applied, enrolled

# Parents' voice: calls the call audit has scored (and tagged with what the parent raised), joined to their lead's campus and channel.
# Voice: [day, campus, channel, intent (0 Hot, 1 Warm, 2 Cold, 3 unknown), visit booked on the call, [[topic, 0 concern / 1 interest]] or null if
#         not tagged yet, grade (label), {"v": what the parent wants, "q": [[topic, kind, quote]]} (corporate login only),
#         [school type, decision maker, distance, join when] or null, the lead's [visit done, applied, enrolled]]
rub = open(os.path.join(CA, "rubric", "v1.yaml")).read()   # the rubric's `topics:` block, one "  id: description" per line
topics = re.findall(r"^  (\w+):", rub[rub.index("\ntopics:"):], re.M)
ca_calls = {c["name"]: c for c in json.load(open(os.path.join(CA, "calls.json")))}
V = []
for f in sorted(glob.glob(os.path.join(CA, "scores", "v1", "*.json"))):
    sc = json.load(open(f)); c = ca_calls.get(sc["call"]); l = ix.get(c and c["lead"])
    if not l or sc.get("outcome") in ("Not a lead", "Call dropped / unclear"): continue
    tp = sc.get("topics")
    V.append([c["start_time"][:10], idx(campuses, l["territory"]), channels.index(channel(l["source"])),
              {"Hot": 0, "Warm": 1, "Cold": 2}.get(sc.get("intent"), 3), int(sc.get("outcome") == "Visit booked"),
              None if tp is None else [[topics.index(t["topic"]), int(t["kind"] == "interest")] for t in tp if t.get("topic") in topics],
              lab(l["grade_applied"]),
              {"v": sc.get("parent_voice") or "", "q": [[topics.index(t["topic"]), int(t["kind"] == "interest"), t["quote"]] for t in tp or [] if t.get("topic") in topics]},
              [sc["profile"].get(k) for k in ("school_type", "decision_maker", "distance", "join_when")] if sc.get("profile") else None, conv[l["name"]]])
print("parents' voice:", len(V), "scored lead calls,", sum(v[5] is not None for v in V), "tagged with topics")
# Claude's weekly parent personas per campus and channel (call audit repo): [campus, channel, persona, tip, calls analysed, written on]
pf = os.path.join(CA, "personas", "channels.json"); PJ = json.load(open(pf)) if os.path.exists(pf) else {"items": []}
PERS = [[campuses.index(p["campus"]), channels.index(p["channel"]), p["persona"], p["tip"], p["calls"], PJ.get("generated")]
        for p in PJ["items"] if p["campus"] in campuses and p["channel"] in channels]

now = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
out = dict(taken=now.strftime("%d %b %Y, %I:%M %p IST"), today=now.date().isoformat(), campuses=campuses, channels=channels,
           reasons=reasons, labels=labels, leads=L, students=S, fees=F, spend=SP, spend_upto=spend_upto, topics=topics, voice=V, personas=PERS)
json.dump(out, open(os.path.join(HERE, "audit.json"), "w"), separators=(",", ":"))
print(len(L), "leads,", len(S), "paying students (", unmatched, "with no CRM lead ),", len(F), "fees,", len(SP), "spend rows")
print("junk/lost with reason:", sum(1 for x in L if x[8] and x[9] >= 0), "of", sum(x[8] for x in L), "| reasons:", reasons)
print("revenue", sum(f[3] for f in F), "spend", round(sum(s[3] for s in SP)), spend_upto)
