"""Marketing audit data: one record per lead, per fee payment and per day of agency spend, so the page can
recount any date range and drill into source, campaign and ad. No names, phone numbers or emails leave this script.
Writes audit.json.

Usage: python3 build_audit.py
Needs the CRM token (CRM_TOKEN, or ~/.gems_crm_token) and the live sheet IDs (MARKETING_SHEETS, or ~/.gems_marketing_audit_sheets.json:
{"Edelytics": id, "EchoVME": id, "Because": id}); the IDs stay off this public repo. Admissions and revenue come from the CRM itself
(deals and the fee payments recorded on them). Parents' voice comes from the
call audit repo (CALL_AUDIT_DIR, default the local clone). GitHub Actions runs this nightly (.github/workflows/nightly.yml).
"""
import csv, datetime as dt, glob, io, json, os, time, urllib.parse, urllib.request, collections as C
import openpyxl, re

B = "https://gemseducation.m.frappe.cloud"
T = (os.environ.get("CRM_TOKEN") or open(os.path.expanduser("~/.gems_crm_token")).read()).strip()
T = T if T.startswith("token ") else "token " + T
HERE = os.path.dirname(os.path.abspath(__file__))
SHEETS = json.loads(os.environ["MARKETING_SHEETS"]) if os.environ.get("MARKETING_SHEETS") else json.load(open(os.path.expanduser("~/.gems_marketing_audit_sheets.json")))


def get(doctype, fields, filters=(), parent=None):
    out, start = [], 0
    while True:
        q = urllib.parse.urlencode({"fields": json.dumps(fields), "filters": json.dumps(list(filters)),
                                    "limit_start": start, "limit_page_length": 500, **({"parent": parent} if parent else {})})
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


def workbook(agency, sheet_id=None):
    for attempt in range(4):
        try:
            return openpyxl.load_workbook(io.BytesIO(urllib.request.urlopen(
                f"https://docs.google.com/spreadsheets/d/{sheet_id or SHEETS[agency]}/export?format=xlsx", timeout=90).read()), data_only=True)
        except Exception as e:
            if attempt == 3: raise
            print("retry sheet", agency, e, flush=True); time.sleep(5)


d10 = lambda p: (lambda s: s[-10:] if len(s) >= 10 else None)("".join(c for c in (p or "") if c.isdigit()))
num = lambda v: v if isinstance(v, (int, float)) else 0
CHANNEL = {"Meta Ads": "Meta", "Instagram Ads": "Meta", "Facebook": "Meta", "Google Ads": "Google", "Website Enquiry": "Website"}
AD = {"Meta", "Google"}
CAMP_CH = {}   # (campus, utm_campaign) -> Meta/Google, learned from leads whose source is an ad platform; filled once leads are loaded
# Campuses whose website enquiries carrying a campaign tag come from their Google Ads landing page (Jay, 29 Oct: Gurugram's "brand" and
# "Lead gen - Palam Vihar" website leads are Google)
WEB_ADS = {"GEMS Millennium School - Gurugram": "Google"}
UTM_CH = lambda u: "Google" if "google" in u or u in ("gads", "adwords") else "Meta" if u in ("ig", "fb", "meta", "instagram", "facebook") else None
# Channel of a lead. An ad-platform source wins. Otherwise (Website Enquiry, Campaign Form...) a paid ad still counts as its platform when
# utm_source names one (google_ads, ig, fb), when the campaign is one the campus runs on Meta/Google, or when the campaign name says so.
def channel(l):
    if not l: return "Others"
    base, c = CHANNEL.get(l.get("source") or ""), (l.get("utm_campaign") or "").strip()
    if base in AD: return base
    lc = c.lower()
    return (UTM_CH((l.get("utm_source") or "").strip().lower()) or (WEB_ADS.get(l.get("territory")) if base == "Website" and c and not l.get("utm_source") else None)
            or CAMP_CH.get((l.get("territory"), c))
            or ("Meta" if any(w in lc for w in ("meta", "instagram", "facebook")) else "Google" if "google" in lc else None) or base or "Others")
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
                         "mobile_no", "phone", "lost_reason", "utm_campaign", "utm_content", "grade_applied", "email", "lead_name", "student_name", "date_of_birth", "current_school",
                         "curriculum", "academic_year", "lead_owner", "intent", "last_call_outcome", "next_contact_date", "visit_state", "next_visit_on",
                         "visit_done_on", "how_heard", "utm_source", "utm_medium", "calls_made", "calls_connected", "first_contacted_on", "last_attempt_at", "converted_on"])
visits = get("GEMS Visit", ["enquiry", "status"])
calls = get("CRM Call Log", ["type", "status", "duration", "telephony_medium", "reference_docname", "to", "start_time", "creation"],
            [["type", "=", "Outgoing"]])
print("leads", len(leads), "visits", len(visits), "outgoing calls", len(calls))

# Leads with no campus are left out of the report entirely
# Test leads Jay named (29 Oct) are left out everywhere, with their deals
TEST_LEADS = {"CRM-LEAD-2026-00013", "CRM-LEAD-2026-00045", "CRM-LEAD-2026-01730"}
TEST_DEALS = {"CRM-DEAL-2026-00092"}   # "Priya Demo / Aarav Demo", GSE Jodhpur (Jay, 29 Oct)
leads = [l for l in leads if not l["is_duplicate"] and l["territory"] and l["name"] not in TEST_LEADS]
_seen = C.defaultdict(C.Counter)   # a campaign counts as the platform that brings at least 90% of its ad-source leads
for l in leads:
    if CHANNEL.get(l["source"] or "") in AD and (l["utm_campaign"] or "").strip(): _seen[(l["territory"], l["utm_campaign"].strip())][CHANNEL[l["source"]]] += 1
CAMP_CH.update({k: v.most_common(1)[0][0] for k, v in _seen.items() if v.most_common(1)[0][1] >= 0.9 * sum(v.values())})
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
# source, campaign, ad; a blank one gets a plain label instead of "(not set)"
tags = lambda l: [idx(labels, (l["source"] or "").strip() or "Source not recorded"), idx(labels, (l["utm_campaign"] or "").strip() or "No campaign tag"),
                  idx(labels, (l["utm_content"] or "").strip() or "No ad tag")]

# Leads: [created on, campus, channel, contacted, dials, connects, visit scheduled, visit done, junk/lost, reason or -1,
#         source, campaign, ad, grade applied for (these four index into labels), applied, enrolled]
L = []
for l in leads:
    i, jl = l["name"], l["status"] in ("Junk", "Lost")
    r = -1
    if jl:
        txt = (l["lost_reason"] or "").strip()
        r = idx(reasons, REASON.get(txt.lower(), txt))
    L.append([l["creation"][:10], idx(campuses, l["territory"]), channels.index(channel(l)), int(dials[i] > 0),
              dials[i], conns[i], int(i in vs), int(i in vd), int(jl), r] + tags(l) + [lab(l["grade_applied"])])

CA = os.environ.get("CALL_AUDIT_DIR") or os.path.expanduser("~/Library/CloudStorage/OneDrive-GEMS/CodeTech/GEMS CRM Frappe/gems-call-audit")

# Admissions: the CRM deals (applications) and the fee payments recorded on them. Each fee counts on its own payment date.
# Duplicate and test deals are left out. Application Fee -> "app", Blocking Fee (the admission fee) -> "adm", Tuition -> "tui".
KIND = {"Application Fee": "app", "Blocking Fee": "adm", "Tuition": "tui"}
deals = [d for d in get("CRM Deal", ["name", "lead", "territory", "status", "student_name", "lead_name", "is_duplicate", "enrolled_on",
                                  "blocking_fee_paid_on", "creation"])
         if not d["is_duplicate"] and d["territory"] and d["lead"] not in TEST_LEADS and d["name"] not in TEST_DEALS and "test" not in f'{d["student_name"] or ""} {d["lead_name"] or ""}'.lower()]
paylines = C.defaultdict(list)
for p in get("GEMS Application Payment", ["parent", "payment_type", "amount", "paid_on"], parent="CRM Deal"):
    if p["payment_type"] in KIND and p["paid_on"]: paylines[p["parent"]].append((KIND[p["payment_type"]], str(p["paid_on"])[:10], float(p["amount"] or 0)))
print("deals", len(deals), "with payments", sum(1 for d in deals if paylines[d["name"]]))

# BL: billing, as in the CFO Business Dashboard: for each Blocking Fee Paid / Enrolled deal, every row of its Fees table that counts,
# published - discount. Application, blocking, tuition and annual fees always count; other extras (e.g. Prospectus) only when ticked Take.
# Dated on the blocking fee payment, else enrolled on, else the deal's creation. F (fees actually paid) is the collection to date.
CORE_FEES = {"application fee", "blocking fee", "tuition", "annual fee"}
bill = C.defaultdict(float)
for r in get("GEMS Application Fee", ["parent", "fee", "taken", "published", "discount_amount"], parent="CRM Deal"):
    if (r["fee"] or "").strip().lower() in CORE_FEES or r["taken"]: bill[r["parent"]] += (r["published"] or 0) - (r["discount_amount"] or 0)
S, F, AF, BL, unmatched, paid, pay = [], [], [], [], 0, {}, {}   # F: revenue fees (admission + tuition), AF: application fees (not revenue)
for d in deals:
    fees = paylines[d["name"]]
    enrolled = d["status"] in ("Blocking Fee Paid", "Enrolled")   # CRM stage; waived fees leave no payment row
    if not fees and not enrolled: continue
    campus, lead = d["territory"], ix.get(d["lead"])
    unmatched += lead is None
    enrol = min([dd for k, dd, _ in fees if k in ("adm", "tui")] or [None]) or ((d["enrolled_on"] or "")[:10] or None if enrolled else None)
    app = next((dd for k, dd, _ in fees if k == "app"), None) or enrol
    if lead:
        paid[lead["name"]] = [1, int(bool(enrol))]
        pp = pay.setdefault(lead["name"], [None, None, 0.0]); pp[0] = pp[0] or app; pp[1] = pp[1] or enrol; pp[2] += sum(a for k, _, a in fees if k != "app")
    ci, ch = idx(campuses, campus), channels.index(channel(lead))  # no CRM lead -> Others
    tg = tags(lead) if lead else [lab("No CRM lead"), lab("No campaign tag"), lab("No ad tag")]
    # Students: [campus, channel, application date or null, enrollment date or null, source, campaign, ad]
    S.append([ci, ch, app, enrol] + tg)
    # Fees and application fees: [paid on, campus, channel, amount, source, campaign, ad]
    F += [[dd, ci, ch, a] + tg for k, dd, a in fees if a and k != "app"]
    AF += [[dd, ci, ch, a] + tg for k, dd, a in fees if a and k == "app"]
    if enrolled and bill[d["name"]]:
        BL.append([str(d["blocking_fee_paid_on"] or d["enrolled_on"] or d["creation"])[:10], ci, ch, bill[d["name"]]] + tg)

# Agency spend per day: [day, campus, channel, spend, impressions, clicks, agency-reported leads, reach, campaign or -1]
SP, spend_upto = [], {}
def spend(agency, day, camp, ch, amount, impr=0, clicks=0, ag_leads=0, reach=0, campaign=None):
    SP.append([day.date().isoformat(), idx(campuses, camp), channels.index(ch), round(amount, 2), num(impr), num(clicks),
               num(ag_leads), num(reach), lab(campaign) if campaign else -1])
    spend_upto[agency] = max(spend_upto.get(agency, ""), day.date().isoformat())

def campus_of(tab):   # "GMS Gurugram" -> "GEMS Millennium School - Gurugram"
    p = tab.strip().split(" ", 1)
    name = f"{SHORT.get(p[0], p[0])} - {p[1]}" if len(p) == 2 else tab
    if name in campuses or any(l["territory"] == name for l in leads): return name
    # wrong prefix on the tab (Edelytics' "GMS Jodhpur" is GSE Jodhpur): take the only campus in that city
    city = {l["territory"] for l in leads if len(p) == 2 and l["territory"].endswith(" - " + p[1])}
    return city.pop() if len(city) == 1 else None

CRM_CAMPS = C.defaultdict(set)   # (campus, Meta/Google) -> the utm_campaign names its leads carry
for l in leads:
    if (l["utm_campaign"] or "").strip() and channel(l) in AD: CRM_CAMPS[(l["territory"], channel(l))].add(l["utm_campaign"].strip())

# Edelytics: one tab per campus; row 1 titles each block ("Google Ads - Performance", "Meta Ads - Lead Generation", "Total"),
# row 2 names its columns. Columns and their names differ by tab (Cost or Spend, Impr. or Impressions...), so go by the names.
# "... - Performance" blocks are channel totals; any other block is one campaign, named as in the CRM's utm_campaign via EDE_CAMPAIGN
# (or its own title if not listed). YouTube counts as Google.
EDE_COLS = {"cost": ("cost", "spend"), "impr": ("impr.", "impressions"), "clicks": ("clicks", "link clicks"), "leads": ("conversions", "leads")}
EDE_CAMPAIGN = {
    ("GMS Indirapuram", "Meta Ads - Lead Generation"): "GEMS Indirapuram - Lead Generation",
    ("GMS Indirapuram", "Meta Ads - Lead Generation - Remarketing"): "GEMS Indirapuram - Lead Generation - Remarketing",
    ("GMS Indirapuram", "Meta Ads - Traffic Ad - Image - New Campus Now Open"): "Meta Ads - Awareness",
    ("GMS Indirapuram", "Meta Ads - Traffic Ad - Video - New Campus Now Open"): "Meta Ads - Awareness",
    ("GMS Indirapuram", "Google Ads - Search Ads - Indirapuram Keywords"): "Google Ads - Search Ad",
    ("GMS Indirapuram", "Google Ads - Search Ads - Ghaziabad Keywords"): "Google Ads - Search Ad",
}
# Diagnostics (sheet layout, spend check) go to the run log encrypted to diag_pub.pem, so the public log shows nothing
DIAG = []
def diag(*a): DIAG.append(" ".join(str(x) for x in a))
def ede_block(tab, camp, title, heads, recs):
    """One Edelytics campaign's rows -> spend. recs yields dicts with day/cost/impr/clicks/leads/reach."""
    t = title.lower()
    # A block whose title doesn't name the platform (Gurugram's Meta block was retitled "x" on 29 Oct): Reach/Frequency columns mean Meta
    ch = "Google" if "google" in t or "youtube" in t else "Meta" if "meta" in t or heads & {"reach", "frequency"} else None
    if not ch: diag("EDE skipped block", tab, repr(title)); return
    campaign = EDE_CAMPAIGN.get((tab, title)) or (None if "performance" in t else title)
    # A title that isn't one of the campus's CRM campaigns on that platform: a lead-gen block goes to the campus's only CRM
    # campaign there; anything else (YouTube, awareness, traffic) stays a channel total, so spend never sits on a campaign with no leads
    if campaign and campaign not in CRM_CAMPS[(camp, ch)]:
        campaign = next(iter(CRM_CAMPS[(camp, ch)])) if len(CRM_CAMPS[(camp, ch)]) == 1 and "lead" in t else None
    diag("Edelytics", tab, "|", title, "->", ch, campaign or "(channel total)")
    for r in recs:
        if isinstance(r.get("day"), dt.datetime) and num(r.get("cost")):
            spend("Edelytics", r["day"], camp, ch, r["cost"], impr=r.get("impr"), clicks=r.get("clicks"),
                  ag_leads=r.get("leads"), reach=r.get("reach"), campaign=campaign)

# MARKETING_SHEETS["Edelytics"] may be one sheet ID or a list, oldest first. A campus with a tab in a later sheet is read only from
# that sheet (the new sheet from 29 Oct has one row per day and campaign: Day, Campaign, Spend...; Indirapuram stays on the old one
# until Edelytics adds its tab there).
_ede, _seen = [], set()
for sid in reversed([SHEETS["Edelytics"]] if isinstance(SHEETS["Edelytics"], str) else SHEETS["Edelytics"]):
    for ws in workbook("Edelytics", sid):
        camp = campus_of(ws.title)
        if not camp: print("skipping Edelytics tab", ws.title); continue
        if camp in _seen: continue
        _seen.add(camp); _ede.append((ws, camp))
for ws, camp in _ede:
    for _i, _r in enumerate(ws.iter_rows(min_row=1, max_row=4, values_only=True)): diag("EDE", ws.title, ws.max_row, "r%d" % (_i + 1), list(_r))
    tab, rows = ws.title.strip(), list(ws.iter_rows(values_only=True))
    if not rows: continue
    head = [str(v or "").strip().lower() for v in rows[0]]
    if "campaign" in head:   # long format: one row per day and campaign
        col = {n: i for i, n in enumerate(head) if n}
        g = lambda r, k: next((r[col[n]] for n in EDE_COLS.get(k, (k,)) if n in col and col[n] < len(r)), 0)
        by = C.defaultdict(list)
        for r in rows[1:]:
            if col["campaign"] < len(r) and r[col["campaign"]]:
                by[str(r[col["campaign"]]).strip()].append({k: g(r, k) for k in ("day", "cost", "impr", "clicks", "leads", "reach")})
        for title, recs in by.items(): ede_block(tab, camp, title, set(col), recs)
        continue
    # old format: row 1 titles side-by-side blocks, row 2 names their columns
    starts = [(i, str(v).strip()) for i, v in enumerate(rows[0]) if v] + [(len(rows[1]), "")]
    for (c0, title), (c1, _) in zip(starts, starts[1:]):
        col = {str(rows[1][i]).strip().lower(): i for i in range(c0, c1) if rows[1][i]}
        g = lambda r, k, col=col: next((r[col[n]] for n in EDE_COLS.get(k, (k,)) if n in col and col[n] < len(r)), 0)
        ede_block(tab, camp, title, set(col), ({k: g(r, k) for k in ("day", "cost", "impr", "clicks", "leads", "reach")} for r in rows[2:]))

# EchoVME, two layouts.
# New (tab "Oct - New", from 29 Oct): one block per campus campaign, titled in row 1 ("Vikhroli - - META Lead Campaign"), row 2 names
# its columns (Date, Audience, Reach, Impression, Clicks, ..., Spends, Leads); one row per day and audience (ad set). The Parents and
# Household audiences are the campus's lead-gen campaign's ad sets, so their spend goes to that CRM campaign ("VME - LG - Admission -
# Vikhroli - 27/10"); Branding is awareness and stays a channel total. Such a block replaces the old layout's spend for that campus,
# platform and month.
# Old (tabs per month): side-by-side blocks, one per campus, platform and objective (no clicks). The labels sit in the rows above the
# header row ("META - Vikhroli" / "Branding", or "META" / "Raipur"), each spanning its block; the header row starts with "Date". Their
# column positions move between months, so each "Spends" column is a block, labelled by the nearest label at or left of it on each
# row above. Old-layout spend counts as a channel total.
CITIES = {}
for l in leads: CITIES.setdefault(l["territory"].rsplit(" - ", 1)[-1].lower(), set()).add(l["territory"])
city_campus = lambda t: next((next(iter(v_)) for city, v_ in CITIES.items() if len(v_) == 1 and city in t.lower()), None)
plat = lambda t: "Google" if "google" in t.lower() else "Meta" if "meta" in t.lower() else None
echo, covered = list(workbook("EchoVME")), set()   # covered: (campus, channel, "YYYY-MM") read from the new layout
for ws in echo:
    top = [list(r) for r in ws.iter_rows(min_row=1, max_row=2, values_only=True)]
    if len(top) < 2 or "audience" not in [str(v or "").strip().lower() for v in top[1]]: continue
    head = [str(v or "").strip().lower() for v in top[1]]
    blocks = []
    for c, v in enumerate(head):
        if v != "date": continue
        end = next((j for j in range(c + 1, len(head)) if head[j] in ("", "date")), len(head))
        label = str(top[0][c] or "").strip()
        cols = {head[k]: k for k in range(c, end)}
        # a label without a platform ("Coimbatore", from 30 Oct) is Meta when the block has Meta's Reach/Frequency columns
        camp, ch = city_campus(label), plat(label) or ("Meta" if any(k.startswith(("reach", "freq")) for k in cols) else None)
        # the campus's lead-gen campaign: its only CRM campaign on that platform, or the only one named "... - LG - ..."
        # (Coimbatore's Meta leads also carry raw Meta campaign IDs from a website form)
        lg = CRM_CAMPS[(camp, ch)] if camp and ch else set()
        lg = lg if len(lg) == 1 else {c for c in lg if "lg" in [w.strip().lower() for w in c.split(" - ")]}
        campaign = next(iter(lg)) if len(lg) == 1 else None
        diag("EchoVME", ws.title, "|", label, "->", camp, ch, campaign, "cols", c, end)
        if camp and ch and "spends" in cols: blocks.append((camp, ch, cols, campaign))
    for r in ws.iter_rows(min_row=3, values_only=True):
        for camp, ch, cols, campaign in blocks:
            v = lambda n: num(r[cols[n]]) if n in cols and cols[n] < len(r) else 0
            d = r[cols["date"]] if cols["date"] < len(r) else None
            if not isinstance(d, dt.datetime): continue
            covered.add((camp, ch, d.strftime("%Y-%m")))
            if v("spends"):
                aud = str(r[cols["audience"]] or "").strip().lower() if "audience" in cols else ""
                spend("EchoVME", d, camp, ch, v("spends"), impr=v("impression"), clicks=v("clicks"), ag_leads=v("leads"), reach=v("reach"),
                      campaign=None if aud.startswith("brand") else campaign)
for ws in echo:
    top = [list(r) for r in ws.iter_rows(min_row=1, max_row=8, values_only=True)]
    if len(top) > 1 and "audience" in [str(v or "").strip().lower() for v in top[1]]: continue
    h = next((i for i, r in enumerate(top) if r and str(r[0] or "").strip().lower() == "date"), None)
    if h is None: diag("ECHO no header row", ws.title); continue
    head = [str(v or "").strip().lower() for v in top[h]]
    def left(row, c):
        return next((str(row[j]).strip() for j in range(min(c, len(row) - 1), -1, -1) if row[j] not in (None, "")), "")
    blocks = []
    for c, v in enumerate(head):
        if v != "spends": continue
        end = next((j for j in range(c + 1, len(head)) if head[j] in ("", "spends", "date")), len(head))
        label = " | ".join(left(top[i], c) for i in range(h))
        camp, ch = city_campus(label), plat(label)
        dc = max(j for j in range(c + 1) if head[j] == "date")
        cols = {head[j]: j for j in range(c + 1, end)}
        diag("EchoVME", ws.title, "|", label, "->", camp, ch, "cols", c, end)
        if camp and ch: blocks.append((camp, ch, dc, c, cols.get("impression"), cols.get("reach"), cols.get("leads")))
    for r in ws.iter_rows(min_row=h + 2, values_only=True):
        for camp, ch, dc, sc, ic, rc, lc in blocks:
            v = lambda c: num(r[c]) if c is not None and c < len(r) else 0
            if dc < len(r) and isinstance(r[dc], dt.datetime) and v(sc) and (camp, ch, r[dc].strftime("%Y-%m")) not in covered:
                spend("EchoVME", r[dc], camp, ch, v(sc), impr=v(ic), ag_leads=v(lc), reach=v(rc))

# Because: one tab per campus ("GMS Kochi"), one row per day and campaign. Go by the header names, as their sheets differ:
# the old one has Day, Campaign name, Amount spent, Link clicks...; the live one (from 27 Oct) Date, Campaign, Amount Spent (₹),
# Clicks (all), Source... MARKETING_SHEETS["Because"] may be one sheet ID or a list; for the same day, campus and campaign a later sheet wins.
BEC_COLS = {"day": ("day", "date"), "campaign": ("campaign name", "campaign"), "cost": ("amount spent", "amount spent (₹)", "spend"),
            "impr": ("impressions",), "reach": ("reach",), "clicks": ("link clicks", "clicks (all)", "clicks"), "leads": ("leads",), "source": ("source",)}
bec = {}
for sid in ([SHEETS["Because"]] if isinstance(SHEETS["Because"], str) else SHEETS["Because"]):
    for ws in workbook("Because", sid):
        camp = campus_of(ws.title)
        if not camp: print("skipping Because tab", ws.title); continue
        rows = list(ws.iter_rows(values_only=True))
        if not rows: continue
        # a header matches a name when it starts with it ("Amount spent (INR)" -> "amount spent")
        hdr = [str(v or "").strip().lower() for v in rows[0]]
        col = {k: next((i for n in names for i, h_ in enumerate(hdr) if h_ == n or h_.startswith(n + " ")), None) for k, names in BEC_COLS.items()}
        if col["day"] is None or col["cost"] is None:   # headers not recognised: the old sheet's fixed layout
            col = {"day": 0, "campaign": 1, "cost": 4, "impr": 5, "reach": 6, "clicks": 7, "leads": 8, "source": None}
        print("Because tab", ws.title, {k: hdr[i] if i is not None and i < len(hdr) else None for k, i in col.items()})
        g = lambda r, k, col=col: r[col[k]] if col[k] is not None and col[k] < len(r) else None
        for r in rows[1:]:
            if isinstance(g(r, "day"), dt.datetime) and num(g(r, "cost")):
                bec[(g(r, "day"), camp, g(r, "campaign"))] = (r, g)
for (day, camp, campaign), (r, g) in bec.items():
    spend("Because", day, camp, "Google" if "google" in str(g(r, "source") or "").lower() else "Meta", g(r, "cost"), impr=g(r, "impr"),
          reach=g(r, "reach"), clicks=g(r, "clicks"), ag_leads=g(r, "leads"), campaign=campaign)

for rec, l in zip(L, leads):   # applied, enrolled (any time), then for the lead CSV download: lead id, phone, email, CRM status
    rec += paid.get(l["name"], [0, 0]) + [l["name"], l["mobile_no"] or l["phone"] or "", l["email"] or "", l["status"] or ""]
conv = {l["name"]: [int(l["name"] in vd)] + paid.get(l["name"], [0, 0]) for l in leads}   # visit done, applied, enrolled

# Parents' voice: calls the call audit has scored (and tagged with what the parent raised), joined to their lead's campus and channel.
# Voice: [day, campus, channel, intent (0 Hot, 1 Warm, 2 Cold, 3 unknown), visit booked on the call, [[topic, 0 concern / 1 interest]] or null if
#         not tagged yet, grade (label), {"v": what the parent wants, "q": [[topic, kind, quote]]} (corporate login only),
#         [school type, decision maker, distance, join when] or null, the lead's [visit done, applied, enrolled]]
rub = open(os.path.join(CA, "rubric", "v1.yaml")).read()   # the rubric's `topics:` block, one "  id: description" per line
topics = re.findall(r"^  (\w+):", rub[rub.index("\ntopics:"):], re.M)
ca_calls = {c["name"]: c for c in json.load(open(os.path.join(CA, "calls.json")))}
V, LV = [], {}
for f in sorted(glob.glob(os.path.join(CA, "scores", "v1", "*.json"))):
    sc = json.load(open(f)); c = ca_calls.get(sc["call"]); l = ix.get(c and c["lead"])
    if not l or sc.get("outcome") in ("Not a lead", "Call dropped / unclear"): continue
    LV.setdefault(l["name"], []).append((c["start_time"], sc))
    tp = sc.get("topics")
    V.append([c["start_time"][:10], idx(campuses, l["territory"]), channels.index(channel(l)),
              {"Hot": 0, "Warm": 1, "Cold": 2}.get(sc.get("intent"), 3), int(sc.get("outcome") == "Visit booked"),
              None if tp is None else [[topics.index(t["topic"]), int(t["kind"] == "interest")] for t in tp if t.get("topic") in topics],
              lab(l["grade_applied"]),
              {"v": sc.get("parent_voice") or "", "q": [[topics.index(t["topic"]), int(t["kind"] == "interest"), t["quote"]] for t in tp or [] if t.get("topic") in topics]},
              [sc["profile"].get(k) for k in ("school_type", "decision_maker", "distance", "join_when")] if sc.get("profile") else None, conv[l["name"]]])
# Everything else held on each lead, appended to its record for the lead CSV download (headers in lead_cols)
LC = ["Parent name", "Student name", "Date of birth", "Current school", "Curriculum", "Academic year", "Lead owner", "Intent (CRM)", "Last call outcome (CRM)",
      "Next contact", "Visit state", "Next visit on", "Visit done on", "How heard", "UTM source", "UTM medium", "Calls made (CRM)", "Calls connected (CRM)",
      "First contacted", "Last attempt", "Converted on", "Application paid on", "Enrolment paid on", "Admission + term fees paid (INR)", "Calls analysed", "Latest call outcome",
      "Latest call keenness", "What the parent wants", "Concerns raised", "Interests raised", "Parent's current school type", "Who is deciding", "Distance", "Joining", "Avg call quality score"]
tn = lambda t: topics[t["topic"]] if False else t["topic"].replace("_", " ")
for rec, l in zip(L, leads):
    rec_hot = int(l["intent"] == "Hot")   # CRM intent marked Hot; always the last item of a lead record
    p_ = pay.get(l["name"], [None, None, 0.0]); calls_ = sorted(LV.get(l["name"], []), key=lambda x: x[0]); last = calls_[-1][1] if calls_ else {}
    pr = last.get("profile") or {}; scores = [x[1]["score"] for x in calls_ if x[1].get("score") is not None]
    tp = [t for x in calls_ for t in (x[1].get("topics") or [])]
    uniq = lambda kind: ", ".join(sorted({tn(t) for t in tp if t["kind"] == kind}))
    s_ = lambda k: "" if l.get(k) is None else str(l[k])
    rec += [s_("lead_name"), s_("student_name"), s_("date_of_birth"), s_("current_school"), s_("curriculum"), s_("academic_year"), s_("lead_owner"), s_("intent"),
            s_("last_call_outcome"), s_("next_contact_date"), s_("visit_state"), s_("next_visit_on"), s_("visit_done_on"), s_("how_heard"), s_("utm_source"),
            s_("utm_medium"), s_("calls_made"), s_("calls_connected"), s_("first_contacted_on"), (s_("last_attempt_at"))[:16], s_("converted_on"),
            p_[0] or "", p_[1] or "", round(p_[2]) if p_[2] else "", len(calls_), last.get("outcome", ""), last.get("intent", ""), last.get("parent_voice", ""),
            uniq("concern"), uniq("interest"), "" if pr.get("school_type") in (None, "unknown") else pr["school_type"],
            "" if pr.get("decision_maker") in (None, "unknown") else pr["decision_maker"], "" if pr.get("distance") in (None, "unknown") else pr["distance"],
            "" if pr.get("join_when") in (None, "unknown") else pr["join_when"], round(sum(scores) / len(scores)) if scores else "", rec_hot]
print("parents' voice:", len(V), "scored lead calls,", sum(v[5] is not None for v in V), "tagged with topics")
# Claude's weekly parent personas per campus and channel (call audit repo): [campus, channel, persona, tip, calls analysed, written on]
pf = os.path.join(CA, "personas", "channels.json"); PJ = json.load(open(pf)) if os.path.exists(pf) else {"items": []}
PERS = [[campuses.index(p["campus"]), channels.index(p["channel"]), p["persona"], p["tip"], p["calls"], PJ.get("generated")]
        for p in PJ["items"] if p["campus"] in campuses and p["channel"] in channels]

now = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
out = dict(taken=now.strftime("%d %b %Y, %I:%M %p IST"), today=now.date().isoformat(), campuses=campuses, channels=channels,
           reasons=reasons, labels=labels, leads=L, students=S, fees=F, appfees=AF, billing=BL, spend=SP, spend_upto=spend_upto, topics=topics, voice=V, personas=PERS, lead_cols=LC)
json.dump(out, open(os.path.join(HERE, "audit.json"), "w"), separators=(",", ":"))
print(len(L), "leads,", len(S), "paying students (", unmatched, "with no CRM lead ),", len(F), "fees,", len(AF), "application fees,", len(SP), "spend rows")
print("junk/lost with reason:", sum(1 for x in L if x[8] and x[9] >= 0), "of", sum(x[8] for x in L), "| reasons:", reasons)
# Check table for the run log: spend, agency-reported leads and CRM leads per campus and channel, and spend per campaign row
_ck = C.defaultdict(lambda: [0.0, 0, 0, None, None])
for x in SP:
    k = _ck[(campuses[x[1]], channels[x[2]])]; k[0] += x[3]; k[1] += x[6]; k[3] = min(k[3] or x[0], x[0]); k[4] = max(k[4] or x[0], x[0])
for x in L: _ck[(campuses[x[1]], channels[x[2]])][2] += 1
for (cp, chn), (sp_, al, cl, a0, a1) in sorted(_ck.items()):
    if sp_ or chn in AD: diag(f"CHECK {cp:40} {chn:8} spend {round(sp_):>8} agency_leads {al:>5} crm_leads {cl:>5} spend_days {a0}..{a1}")
_cc = C.Counter()
for x in SP: _cc[(campuses[x[1]], channels[x[2]], labels[x[8]] if x[8] >= 0 else "(channel total)")] += x[3]
diag("ENROLMENTS", sum(1 for x in S if x[3]), "billing", round(sum(b[3] for b in BL)))
for k, v in sorted(_cc.items()): diag("CAMPAIGN", " | ".join(k), round(v))
if os.path.exists(os.path.join(HERE, "diag_pub.pem")):
    import base64, zlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _k, _n = AESGCM.generate_key(bit_length=256), os.urandom(12)
    _pub = serialization.load_pem_public_key(open(os.path.join(HERE, "diag_pub.pem"), "rb").read())
    _wk = _pub.encrypt(_k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    _b = base64.b64encode(_wk + _n + AESGCM(_k).encrypt(_n, zlib.compress("\n".join(DIAG).encode(), 9), None)).decode()
    for _i in range(0, len(_b), 4000): print("DIAG", _b[_i:_i + 4000])
print("billing", sum(b[3] for b in BL), "collected", sum(f[3] for f in F), "spend", round(sum(s[3] for s in SP)), spend_upto)
