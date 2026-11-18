# temporary: Kochi spend breakdown, encrypted to tmp_kochi_pub.pem (logs are public)
import json, os, io, base64, urllib.request, collections as C, datetime as dt, openpyxl
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.fernet import Fernet
a = json.load(open("audit.json")); out = []
K = [i for i, c in enumerate(a["campuses"]) if "Kochi" in c]
agg = C.defaultdict(float)
for r in a["spend"]:
    if r[1] not in K: continue
    c, ch, camp = a["campuses"][r[1]], a["channels"][r[2]], a["labels"][r[8]]
    per = "<=17Nov" if r[0] <= "2026-11-17" else r[0]
    agg[(c, ch, camp, per)] += r[3]
    agg[(c, ch, "ALL", per)] += r[3]; agg[(c, "ALL", "ALL", per)] += r[3]
    agg[(c, ch, "ALL", "month " + r[0][:7])] += r[3]
for k in sorted(agg): out.append(" | ".join(k) + " | %.2f" % agg[k])
out.append("taken " + a["taken"] + " spend_upto " + json.dumps(a["spend_upto"]))
S = json.loads(os.environ["MARKETING_SHEETS"])["Because"]
for sid in ([S] if isinstance(S, str) else S):
    wb = openpyxl.load_workbook(io.BytesIO(urllib.request.urlopen(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx", timeout=90).read()), data_only=True)
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True)); t = C.defaultdict(float); n = 0
        for r in rows[1:]:
            v = [x for x in r]
            d = v[0]
            amt = None
            for x in v[2:5]:
                try: amt = float(str(x).replace("₹", "").replace(",", "")); break
                except Exception: pass
            if isinstance(d, dt.datetime) and amt is not None:
                n += 1; t["all"] += amt; t["<=17Nov" if d <= dt.datetime(2026, 11, 17) else "after"] += amt
                t["min " + d.date().isoformat()] = 0
        mins = sorted(k for k in t if k.startswith("min "))
        out.append("SHEET %s | %s | rows %d | %s | first %s last %s | hdr %s" % (sid[:6], ws.title, n, {k: round(v, 2) for k, v in t.items() if not k.startswith("min ")}, mins[0] if mins else "", mins[-1] if mins else "", rows[0][:5] if rows else ""))
key = Fernet.generate_key()
pub = serialization.load_pem_public_key(open("tmp_kochi_pub.pem", "rb").read())
print("KEY", base64.b64encode(pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode())
print("DATA", Fernet(key).encrypt("\n".join(out).encode()).decode())
