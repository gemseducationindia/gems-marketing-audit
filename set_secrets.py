# Copy credentials from this Mac's chmod-600 files into this repo's GitHub Actions secrets. Run again after changing a login or sheet.
#   pip install pynacl; python set_secrets.py
import base64, json, os, urllib.request
from nacl import encoding, public
REPO = "gemseducationindia/gems-marketing-audit"
home = lambda f: open(os.path.expanduser(f)).read().strip()
GH = home("~/.gems_github_token")
SECRETS = {"CRM_TOKEN": home("~/.gems_crm_token"), "MARKETING_SHEETS": home("~/.gems_marketing_audit_sheets.json"),
           "MARKETING_USERS": home("~/.gems_marketing_audit_users.json"), "CALL_AUDIT_TOKEN": GH}   # reads the private call audit repo
def api(path, body=None, method="GET"):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"token {GH}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r: return json.load(r) if r.status == 200 else None
key = api("actions/secrets/public-key"); box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
for name, value in SECRETS.items():
    api(f"actions/secrets/{name}", {"encrypted_value": base64.b64encode(box.encrypt(value.encode())).decode(), "key_id": key["key_id"]}, "PUT"); print("set", name)
