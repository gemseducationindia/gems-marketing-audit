"""Encrypt the audit data (campus x channel totals, no personal details) into docs/data.enc.
Password: env AUDIT_PASSWORD. Key = PBKDF2-SHA256(password, salt in docs/key.json, 600k) -> AES-256-GCM."""
import base64, gzip, json, os, secrets, sys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, "docs")
pw = os.environ.get("AUDIT_PASSWORD") or sys.exit("Set AUDIT_PASSWORD")
src = json.load(open(sys.argv[1]))
data = dict(taken=src["taken"], sheet_taken=sys.argv[2], rows=src["rows"])
kp = os.path.join(DOCS, "key.json")
if not os.path.exists(kp):
    json.dump({"salt": base64.b64encode(secrets.token_bytes(16)).decode(), "iter": 600000}, open(kp, "w"))
k = json.load(open(kp))
key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(k["salt"]), iterations=k["iter"]).derive(pw.encode())
iv = secrets.token_bytes(12)
open(os.path.join(DOCS, "data.enc"), "wb").write(iv + AESGCM(key).encrypt(iv, gzip.compress(json.dumps(data).encode(), mtime=0), None))
print("docs/data.enc written:", len(data["rows"]), "rows")
