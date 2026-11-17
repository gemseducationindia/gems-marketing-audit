# temporary diagnostic (Kochi spend), removed after use; output encrypted to kochi_pub.pem
import os, json, urllib.parse, urllib.request, collections as C, io, datetime as dt, base64, zlib, openpyxl
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
OUT = []
def p(*a): OUT.append(" ".join(str(x) for x in a))
def mg(path, **q):
    url = "https://graph.facebook.com/v23.0/" + path + "?" + urllib.parse.urlencode({**q, "access_token": os.environ["META_TOKEN"].strip()})
    while url:
        try: d = json.load(urllib.request.urlopen(url, timeout=120))
        except urllib.error.HTTPError as e: p("ERR", path, e.code, e.read()[:300]); return
        yield from d.get("data", []); url = d.get("paging", {}).get("next")
accts = list(mg("me/adaccounts", fields="name,account_id,currency,amount_spent,account_status", limit=100))
for a in accts: p("ACCT", a["account_id"], a["name"], a.get("currency"), "lifetime_amount_spent", a.get("amount_spent"), "status", a.get("account_status"))
for a in accts:
    m, camps = C.Counter(), C.Counter()
    for r in mg("act_%s/insights" % a["account_id"], level="campaign", time_increment="monthly", limit=500,
                time_range=json.dumps({"since": "2026-01-01", "until": dt.date.today().isoformat()}), fields="campaign_name,spend"):
        s = float(r.get("spend") or 0)
        if not s: continue
        m[r["date_start"][:7]] += s
        if "kochi" in a["name"].lower() or "kochi" in r["campaign_name"].lower(): camps[(r["date_start"][:7], r["campaign_name"])] += s
    p("MONTHLY", a["name"], {k: round(v) for k, v in sorted(m.items())})
    for k, v in sorted(camps.items()): p("  KCAMP", a["name"], k[0], "|", k[1], round(v))
# Because sheets
S = json.loads(os.environ["MARKETING_SHEETS"])
for i, sid in enumerate([S["Because"]] if isinstance(S["Because"], str) else S["Because"]):
    wb = openpyxl.load_workbook(io.BytesIO(urllib.request.urlopen(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx", timeout=90).read()), data_only=True)
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        p("BEC sheet", i, "tab", ws.title, "rows", len(rows), "hdr", rows[0] if rows else None)
        for r in rows[1:4]: p("   sample", r)
        hdr = [str(v or "").strip().lower() for v in (rows[0] if rows else [])]
        ci = next((j for j, h in enumerate(hdr) if h.startswith("amount spent") or h == "spend" or h.startswith("cost")), None)
        di = next((j for j, h in enumerate(hdr) if h in ("day", "date")), None)
        si = next((j for j, h in enumerate(hdr) if h == "source"), None)
        if ci is None or di is None: continue
        m = C.Counter()
        for r in rows[1:]:
            if ci < len(r) and isinstance(r[ci], (int, float)):
                d = r[di]; k = d.strftime("%Y-%m") if isinstance(d, dt.datetime) else "nodate:" + str(d)[:12]
                m[(k, str(r[si]) if si is not None else "")] += r[ci]
        p("  BEC monthly", ws.title, {"%s %s" % k: round(v) for k, v in sorted(m.items())})
k, n = AESGCM.generate_key(bit_length=256), os.urandom(12)
pub = serialization.load_pem_public_key(open("kochi_pub.pem", "rb").read())
wk = pub.encrypt(k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
b = base64.b64encode(wk + n + AESGCM(k).encrypt(n, zlib.compress("\n".join(OUT).encode(), 9), None)).decode()
for i in range(0, len(b), 4000): print("KD", b[i:i + 4000])
