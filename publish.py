"""Encrypt the audit data once per login into docs/d-*.enc, each holding only that login's campuses.
Logins: logins.json in this repo (see logins.py; admins manage them in the dashboard's Users tab). Each login has an RSA key pair;
its data file = RSA-OAEP(SHA-256)-wrapped AES key (256 bytes) + IV (12) + AES-256-GCM(gzip(JSON)), so this needs no passwords.
docs/users.json maps a hash of each lower-cased username to its password-sealed private key and file; the page never lists names.

Usage: python publish.py audit.json "22 Oct 2026"   (the second argument is the refresh date)
"""
import base64, glob, gzip, hashlib, json, os, secrets, sys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, "docs")
LOGINS = json.load(open(os.path.join(HERE, "logins.json")))
src = dict(json.load(open(sys.argv[1])), sheet_taken=sys.argv[2])


def subset(d, allowed):
    """Only the allowed campuses' records, re-indexed so nothing about other campuses (or their campaign names) is left."""
    cmap = {i: n for n, i in enumerate(i for i, c in enumerate(d["campuses"]) if allowed == "all" or c in allowed)}
    labels, lmap = [], {}
    lab = lambda i: -1 if i < 0 else lmap.setdefault(i, labels.append(d["labels"][i]) or len(labels) - 1)
    out = dict(d, campuses=[d["campuses"][i] for i in cmap], labels=labels)
    out["leads"] = [x[:1] + [cmap[x[1]]] + x[2:10] + [lab(v) for v in x[10:14]] + x[14:] for x in d["leads"] if x[1] in cmap]
    out["students"] = [[cmap[x[0]]] + x[1:4] + [lab(v) for v in x[4:]] for x in d["students"] if x[0] in cmap]
    out["fees"] = [x[:1] + [cmap[x[1]]] + x[2:4] + [lab(v) for v in x[4:]] for x in d["fees"] if x[1] in cmap]
    out["appfees"] = [x[:1] + [cmap[x[1]]] + x[2:4] + [lab(v) for v in x[4:]] for x in d["appfees"] if x[1] in cmap]
    out["billing"] = [x[:1] + [cmap[x[1]]] + x[2:4] + [lab(v) for v in x[4:]] for x in d.get("billing", []) if x[1] in cmap]
    out["spend"] = [x[:1] + [cmap[x[1]]] + x[2:8] + [lab(x[8])] for x in d["spend"] if x[1] in cmap]
    out["voice"] = [x[:1] + [cmap[x[1]]] + x[2:6] + [lab(x[6])] + x[7:] for x in d.get("voice", []) if x[1] in cmap]   # agencies see parents' quotes too (user's call, 24 Oct)
    out["personas"] = [[cmap[x[0]]] + x[1:] for x in d.get("personas", []) if x[0] in cmap]
    return out


index = {}
for f in glob.glob(os.path.join(DOCS, "d-*.enc")): os.remove(f)
for u, e in LOGINS.items():
    data = dict(subset(src, e["campuses"]), role="corporate" if e["campuses"] == "all" else "agency")
    k, iv = secrets.token_bytes(32), secrets.token_bytes(12)
    pub = serialization.load_der_public_key(base64.b64decode(e["pub"]))
    wrapped = pub.encrypt(k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0)
    open(os.path.join(DOCS, e["file"]), "wb").write(wrapped + iv + AESGCM(k).encrypt(iv, blob, None))
    index[u] = {k2: e[k2] for k2 in ("iter", "salt", "key", "file", "admin", "gh") if k2 in e}
    print(f"{u[:8]}: {len(data['campuses'])} campuses, {len(data['leads'])} leads, {len(data['fees'])} fees, {len(data['spend'])} spend rows")
json.dump(index, open(os.path.join(DOCS, "users.json"), "w"), indent=1)
for old in ("data.enc", "key.json"):
    if os.path.exists(os.path.join(DOCS, old)): os.remove(os.path.join(DOCS, old))
