"""Add, reset or remove a marketing audit login. Logins live in logins.json in this repo (no passwords in it), so adding one needs
no GitHub secret. Admins (the "Admin" login) can do all of this in the dashboard's Users tab instead.

    python logins.py add "Corporate" all
    python logins.py add "Edelytics" "GMS Gurugram, GMS Indirapuram"     # prints a new password, or pass --password "..."
    python logins.py password "Edelytics" "old password" "new password"   # same key pair
    python logins.py remove "Edelytics"
    python logins.py admin "Admin" on
    python logins.py import-secret      # one-off: the old MARKETING_USERS secret ({"name": {"password", "campuses"}}) -> logins.json
    python logins.py list

Each login gets its own RSA key pair. The public key is stored as is; the private key is stored encrypted with a key derived from
the password (PBKDF2-SHA256, 600k iterations, AES-256-GCM). The nightly run encrypts each login's data with its public key, so
it never needs the passwords. "meta" holds the login's name, private key and password sealed to the admins' keys (Users tab).
"""
import base64, hashlib, json, os, secrets, sys
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logins.json")
SHORT = {"GMS Gurugram": "GEMS Millennium School - Gurugram", "GMS Indirapuram": "GEMS Millennium School - Indirapuram",
         "GMS Kochi": "GEMS Millennium School - Kochi", "GMS Varanasi": "GEMS Millennium School - Varanasi",
         "GMS Raipur": "GEMS Millennium School - Raipur", "GMS Vikhroli": "GEMS Millennium School - Vikhroli",
         "GMS Kalyan": "GEMS Millennium School - Kalyan", "GMA Kochi": "GEMS Modern Academy - Kochi",
         "GMA Coimbatore": "GEMS Modern Academy - Coimbatore", "GSE Jodhpur": "GEMS School of Excellence - Jodhpur",
         "GSE Lucknow": "GEMS School of Excellence - Lucknow", "Little GEMS Kochi": "Little GEMS - Kochi"}
uid = lambda name: hashlib.sha256(("gems-audit:" + name.strip().lower()).encode()).hexdigest()[:32]
b64 = lambda b: base64.b64encode(b).decode()


def load(): return json.load(open(FILE)) if os.path.exists(FILE) else {}
def save(d): json.dump(d, open(FILE, "w"), indent=1, sort_keys=True); open(FILE, "a").write("\n")


def admins(d): return {u: serialization.load_der_public_key(base64.b64decode(e["pub"])) for u, e in d.items() if e.get("admin")}


def seal_meta(d, info):
    # {"w": {admin uid: RSA-OAEP(k)}, "c": iv + AES-GCM(k, json)}: only an admin's password opens it (dashboard Users tab)
    from cryptography.hazmat.primitives.asymmetric import padding
    k, iv = secrets.token_bytes(32), secrets.token_bytes(12)
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    return {"w": {u: b64(p.encrypt(k, oaep)) for u, p in admins(d).items()}, "c": b64(iv + AESGCM(k).encrypt(iv, json.dumps(info).encode(), None))}


def add(name, scope, password=None):
    sc = scope.strip().lower()
    campuses = sc if sc == "all" else [SHORT.get(c.strip(), c.strip()) for c in scope.split(",") if c.strip()]
    if isinstance(campuses, list):
        bad = [c for c in campuses if c not in SHORT.values()]
        if bad: sys.exit(f"Unknown campus: {bad}. Use short names like {', '.join(list(SHORT)[:3])}.")
    password = password or "-".join(secrets.token_urlsafe(4).replace("-", "x").replace("_", "y") for _ in range(3))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pkcs8 = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    kek = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(password.encode())
    inner = json.dumps({"name": name.strip(), "pk": b64(pkcs8)}).encode()
    d = load(); u = uid(name)
    d[u] = {"campuses": campuses, "iter": 600000, "salt": b64(salt), "key": b64(iv + AESGCM(kek).encrypt(iv, inner, None)),
            "pub": b64(spki), "file": "d-" + u[:16] + ".enc"}
    if admins(d): d[u]["meta"] = seal_meta(d, {"name": name.strip(), "pk": b64(pkcs8), "pw": password})   # admins see it in the Users tab
    save(d)
    sees = "all campuses" if campuses == "all" else ", ".join(campuses)
    print(f"Login: {name.strip()}\nPassword: {password}\nSees: {sees}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        pw = sys.argv[sys.argv.index("--password") + 1] if "--password" in sys.argv else None
        add(sys.argv[2], sys.argv[3], pw)
    elif cmd == "import-secret":
        old = json.loads(os.environ["MARKETING_USERS"]); d = load()
        for name, u in old.items():
            if uid(name) in d: print("kept", name); continue
            add(name, "all" if u["campuses"] == "all" else ", ".join(u["campuses"]), u["password"])
    elif cmd == "password":
        # re-encrypt the same private key under the new password: the published data and the call audit keys still open
        d = load(); e = d[uid(sys.argv[2])]; kb = base64.b64decode(e["key"])
        old = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(e["salt"]), iterations=e["iter"]).derive(sys.argv[3].encode())
        inner = AESGCM(old).decrypt(kb[:12], kb[12:], None)
        salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
        new = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(sys.argv[4].encode())
        e.update(salt=b64(salt), iter=600000, key=b64(iv + AESGCM(new).encrypt(iv, inner, None)))
        if admins(d): i = json.loads(inner); e["meta"] = seal_meta(d, {"name": i["name"], "pk": i["pk"], "pw": sys.argv[4]})
        save(d); print("password changed")
    elif cmd == "admin":
        # admin rights only take effect for meta records sealed after this; turn it on from an existing admin's Users tab
        # (Make admin) to re-seal every login's record for the new admin as well
        d = load(); e = d.get(uid(sys.argv[2]))
        if not e: sys.exit("no such login")
        if sys.argv[3] == "on": e["admin"] = True
        else: e.pop("admin", None)
        save(d); print(f"admin {sys.argv[3]} for {sys.argv[2]}")
    elif cmd == "remove":
        d = load(); print("removed" if d.pop(uid(sys.argv[2]), None) else "no such login"); save(d)
    else:
        for u, e in load().items(): print(u, e["campuses"], "admin" if e.get("admin") else "")
