# One-off check of the Meta Marketing API (META_TOKEN): list the ad accounts the system user sees and total their spend
# by account, month and campaign. The report is encrypted to the public key in $META_TEST_PUB, so the public log shows nothing.
import base64, collections as C, json, os, urllib.parse, urllib.request, zlib
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TOKEN, API = os.environ["META_TOKEN"].strip(), "https://graph.facebook.com/v23.0/"
def get(path, **q):
    url = path if path.startswith("http") else API + path + "?" + urllib.parse.urlencode({**q, "access_token": TOKEN})
    while url:
        try: d = json.load(urllib.request.urlopen(url, timeout=120))
        except urllib.error.HTTPError as e: raise SystemExit("Meta API error %s: %s" % (e.code, json.load(e).get("error", {}).get("message", "")))
        yield from d.get("data", []); url = d.get("paging", {}).get("next")

OUT = []
accts = list(get("me/adaccounts", fields="name,account_id,currency,account_status,amount_spent", limit=100))
OUT.append("ACCOUNTS %d" % len(accts))
since = "2026-06-01"
for a in accts:
    OUT.append("ACCT %s | %s | %s | status %s" % (a["account_id"], a["name"], a.get("currency"), a.get("account_status")))
    rows = list(get("act_%s/insights" % a["account_id"], level="campaign", time_increment=1, limit=500,
                    time_range=json.dumps({"since": since, "until": "2026-11-02"}),
                    fields="campaign_name,spend,impressions,reach,clicks,actions"))
    mon, camp, days = C.Counter(), C.Counter(), set()
    for r in rows:
        s = float(r.get("spend", 0)); mon[r["date_start"][:7]] += s; camp[(r["date_start"][:7], r["campaign_name"])] += s; days.add(r["date_start"])
        leads = sum(int(x["value"]) for x in r.get("actions", []) if x["action_type"] in ("lead", "onsite_conversion.lead_grouped"))
        camp[(r["date_start"][:7], r["campaign_name"], "leads")] += leads
    OUT.append("  rows %d days %s..%s" % (len(rows), min(days) if days else "-", max(days) if days else "-"))
    for m in sorted(mon): OUT.append("  MONTH %s spend %d" % (m, round(mon[m])))
    for k in sorted(k for k in camp if len(k) == 2):
        OUT.append("  CAMP %s | %s | spend %d leads %d" % (k[0], k[1], round(camp[k]), camp[k + ("leads",)]))

print("accounts seen:", len(accts))
pub = serialization.load_pem_public_key(os.environ["META_TEST_PUB"].encode())
k, n = AESGCM.generate_key(bit_length=256), os.urandom(12)
wk = pub.encrypt(k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
b = base64.b64encode(wk + n + AESGCM(k).encrypt(n, zlib.compress("\n".join(OUT).encode(), 9), None)).decode()
for i in range(0, len(b), 4000): print("META", b[i:i + 4000])
