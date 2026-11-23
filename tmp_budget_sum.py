# temporary: spend by campus x channel for the budget sheet, encrypted to tmp_budget_pub.pem (logs are public); delete after use
import json, runpy, base64, collections as C
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.fernet import Fernet
g = runpy.run_path("build_audit.py", run_name="build")
a = json.load(open("audit.json")); out = []
agg = C.defaultdict(float)
for r in a["spend"]:
    c, ch, camp = a["campuses"][r[1]], a["channels"][r[2]], a["labels"][r[8]]
    agg[(c, ch, "ALL")] += r[3]
    agg[(c, ch, "month " + r[0][:7])] += r[3]
    if any(w in camp.lower() for w in ("hiring", "recruit", "career", "job")): agg[(c, ch, "HIRING " + camp)] += r[3]
for k in sorted(agg): out.append(" | ".join(k) + " | %.2f" % agg[k])
days = C.defaultdict(list)
for r in a["spend"]: days[(a["campuses"][r[1]], a["channels"][r[2]])].append(r[0])
for k, v in sorted(days.items()): out.append("RANGE %s | %s | %s..%s" % (k[0], k[1], min(v), max(v)))
sk = g.get("_skip") or {}
for k, v in sorted(sk.items(), key=lambda x: -x[1]): out.append("SKIP %s | %.2f" % (" | ".join(map(str, k)), v))
out.append("taken " + str(a.get("taken")) + " spend_upto " + json.dumps(a.get("spend_upto")))
key = Fernet.generate_key()
pub = serialization.load_pem_public_key(open("tmp_budget_pub.pem", "rb").read())
print("KEY", base64.b64encode(pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode())
print("DATA", Fernet(key).encrypt("\n".join(out).encode()).decode())
