"""Encrypt the audit data once per login into docs/d-*.enc, each holding only that login's campuses.
Logins: ~/.gems_marketing_audit_users.json {"Username": {"password": ..., "campuses": "all" | [territory, ...]}} (kept off the repo).
docs/users.json maps a hash of each lower-cased username to its salt and file, so the page never lists the usernames.
Key = PBKDF2-SHA256(password, per-user salt, 600k) -> AES-256-GCM over gzipped JSON.

Usage: python publish.py audit.json "22 Oct 2026"   (the second argument is the admissions sheet's date)
"""
import base64, glob, gzip, hashlib, json, os, secrets, sys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, "docs")
USERS = json.load(open(os.path.expanduser("~/.gems_marketing_audit_users.json")))
src = dict(json.load(open(sys.argv[1])), sheet_taken=sys.argv[2])
uid = lambda name: hashlib.sha256(("gems-audit:" + name.strip().lower()).encode()).hexdigest()[:32]


def subset(d, allowed):
    """Only the allowed campuses' records, re-indexed so nothing about other campuses (or their campaign names) is left."""
    cmap = {i: n for n, i in enumerate(i for i, c in enumerate(d["campuses"]) if allowed == "all" or c in allowed)}
    labels, lmap = [], {}
    lab = lambda i: -1 if i < 0 else lmap.setdefault(i, labels.append(d["labels"][i]) or len(labels) - 1)
    out = dict(d, campuses=[d["campuses"][i] for i in cmap], labels=labels)
    out["leads"] = [x[:1] + [cmap[x[1]]] + x[2:10] + [lab(v) for v in x[10:14]] + x[14:] for x in d["leads"] if x[1] in cmap]
    out["students"] = [[cmap[x[0]]] + x[1:4] + [lab(v) for v in x[4:]] for x in d["students"] if x[0] in cmap]
    out["fees"] = [x[:1] + [cmap[x[1]]] + x[2:4] + [lab(v) for v in x[4:]] for x in d["fees"] if x[1] in cmap]
    out["spend"] = [x[:1] + [cmap[x[1]]] + x[2:8] + [lab(x[8])] for x in d["spend"] if x[1] in cmap]
    # parents' words (what they want, quotes) stay with the corporate login; agencies see the topic counts only
    out["voice"] = [x[:1] + [cmap[x[1]]] + x[2:6] + [lab(x[6])] + [x[7] if allowed == "all" else None] + x[8:] for x in d.get("voice", []) if x[1] in cmap]
    out["personas"] = [[cmap[x[0]]] + x[1:] for x in d.get("personas", []) if x[0] in cmap]
    return out


index = {}
for f in glob.glob(os.path.join(DOCS, "d-*.enc")): os.remove(f)
for name, u in USERS.items():
    salt, file = secrets.token_bytes(16), "d-" + secrets.token_hex(8) + ".enc"
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(u["password"].encode())
    data = dict(subset(src, u["campuses"]), user=name, role="corporate" if u["campuses"] == "all" else "agency")
    iv = secrets.token_bytes(12)
    open(os.path.join(DOCS, file), "wb").write(iv + AESGCM(key).encrypt(iv, gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0), None))
    index[uid(name)] = {"salt": base64.b64encode(salt).decode(), "iter": 600000, "file": file}
    print(f"{name}: {len(data['campuses'])} campuses, {len(data['leads'])} leads, {len(data['fees'])} fees, {len(data['spend'])} spend rows")
json.dump(index, open(os.path.join(DOCS, "users.json"), "w"), indent=1)
for old in ("data.enc", "key.json"):
    if os.path.exists(os.path.join(DOCS, old)): os.remove(os.path.join(DOCS, old))
