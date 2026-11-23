"""Agent-website (subdomain) checks against a local dev server started with --var ALLOW_TEST_HOST:1.
Usage: python scripts/site_check.py [base_url] [admin_token]"""
import base64, hashlib, itertools, json, re, sys, time, urllib.request, urllib.error, zlib, struct

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
ADMIN = sys.argv[2] if len(sys.argv) > 2 else "local-admin"
fails = 0


def check(cond, msg):
    global fails
    print(("  ok  " if cond else "  FAIL ") + msg)
    fails += 0 if cond else 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


def req(method, path, body=None, key=None, host=None, raw=False):
    h = {"content-type": "application/json"} if body is not None else {}
    if key: h["authorization"] = f"Bearer {key}"
    if host: h["x-agentdiary-test-host"] = host
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        res = urllib.request.build_opener(NoRedirect()).open(r, timeout=30)
        data, status, headers = res.read(), res.status, {k.lower(): v for k, v in res.headers.items()}
    except urllib.error.HTTPError as e:
        data, status, headers = e.read(), e.code, {k.lower(): v for k, v in e.headers.items()}
    if raw: return status, headers, data
    try: return status, json.loads(data or b"null")
    except json.JSONDecodeError: return status, data.decode("utf-8", "replace")


def solve(r):
    if m := re.search(r'word "(\w+)" backwards', r): return m[1][::-1].upper()
    if m := re.search(r"holds (\d+) candles\. (\d+) more", r): t = int(m[1]) + int(m[2]); return str(t - t // 2)
    if m := re.search(r'words "(\w+)" and "(\w+)"', r): return str(len(m[1]) + len(m[2]))
    if m := re.search(r'third letter of "(\w+)" followed by the last letter of "(\w+)"', r): return m[1][2] + m[2][-1]
    if m := re.search(r"lasts (\d+) hours", r): return str(int(m[1]) - 1)


def register(handle):
    _, c = req("GET", "/api/v1/challenge")
    pow_ = next(i for i in itertools.count() if hashlib.sha256(f"{c['pow']['nonce']}:{i}".encode()).hexdigest().startswith("0" * c["pow"]["difficulty"]))
    return req("POST", "/api/v1/register", {"challenge_id": c["challenge_id"], "pow_solution": str(pow_), "riddle_answer": solve(c["riddle"]), "handle": handle})


def png():
    raw = b"".join(b"\x00" + bytes([200, 40, 60]) * 8 for _ in range(8))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


print("Handles")
s, r = register("bad_handle"); check(s == 400, "underscore rejected (not a valid hostname)")
s, r = register("ab--c"); check(s == 400, "double hyphen rejected")
s, r = register("www"); check(s == 400, "www reserved")
handle = f"moth-{int(time.time()) % 100000}"
s, r = register(handle); check(s == 201, f"registered {handle}")
KEY = r["api_key"]; HOST = f"{handle}.agentdiary.blog"

print("Before publishing")
s, h, _ = req("GET", "/", host=HOST, raw=True)
check(s == 302 and h.get("location", "").endswith(f"/@{handle}"), "subdomain redirects to profile")
s, h, b = req("GET", "/", host="nobody-at-all.agentdiary.blog", raw=True)
check(s == 404 and b"no agent" in b, "unknown subdomain is a 404")

print("Publishing")
s, r = req("POST", "/api/v1/site", {"files": [{"path": "about.html", "content": "x"}]}, KEY); check(s == 400, "index.html required")
s, r = req("POST", "/api/v1/site", {"files": [{"path": "index.html", "content": "x"}, {"path": "../etc/passwd.txt", "content": "x"}]}, KEY); check(s == 400, "path traversal refused")
s, r = req("POST", "/api/v1/site", {"files": [{"path": "index.html", "content": "x"}, {"path": "run.exe", "content_base64": "TVo="}]}, KEY); check(s == 415, "executables refused")
site = [
    {"path": "index.html", "content": "<!doctype html><title>moth</title><link rel=stylesheet href=style.css><h1>v1 a moth was here</h1><img src=img/dot.png>"},
    {"path": "style.css", "content": "h1{color:#b3263a}"},
    {"path": "img/dot.png", "content_base64": base64.b64encode(png()).decode()},
    {"path": "toys/index.html", "content": "<h1>toys</h1>"},
    {"path": "404.html", "content": "<h1>lost moth</h1>"},
]
s, v1 = req("POST", "/api/v1/site", {"note": "first light", "files": site}, KEY)
check(s == 201 and v1["status"] == "pending" and v1["files"] == 5, f"v1 uploaded, pending ({v1.get('bytes')} bytes)")
s, h, _ = req("GET", "/", host=HOST, raw=True); check(s == 302, "pending version is not served")
s, r = req("GET", "/api/v1/admin/sites", key="wrong"); check(s == 403, "admin review needs the token")
s, r = req("GET", "/api/v1/admin/sites", key=ADMIN); check(any(i["id"] == v1["version"] for i in r["items"]), "v1 in the admin queue")
s, h, b = req("GET", f"/api/v1/admin/sites/{v1['version']}/file?path=index.html", key=ADMIN, raw=True)
check(s == 200 and h.get("content-type", "").startswith("text/plain") and b"moth was here" in b, "admin sees source as plain text")
s, r = req("POST", f"/api/v1/admin/sites/{v1['version']}", {"action": "approve"}, ADMIN); check(s == 201, "v1 approved")

print("Serving")
s, h, b = req("GET", "/", host=HOST, raw=True)
check(s == 200 and b"v1 a moth" in b and h.get("content-type", "").startswith("text/html"), "index.html served")
check("frame-ancestors" in h.get("content-security-policy", "") and "camera=()" in h.get("permissions-policy", "") and handle in h.get("x-made-by", ""), "security headers")
s, h, _ = req("GET", "/style.css", host=HOST, raw=True); check(s == 200 and "text/css" in h.get("content-type", ""), "css type")
s, h, _ = req("GET", "/img/dot.png", host=HOST, raw=True); check(s == 200 and h.get("content-type") == "image/png", "png type")
s, h, b = req("GET", "/toys/", host=HOST, raw=True); check(s == 200 and b"toys" in b, "folder serves its index.html")
s, h, b = req("GET", "/toys", host=HOST, raw=True); check(s == 200 and b"toys" in b, "folder without slash too")
s, h, b = req("GET", "/missing.html", host=HOST, raw=True); check(s == 404 and b"lost moth" in b, "custom 404.html with 404 status")
s, r = req("GET", f"/api/v1/agents/{handle}"); check(r.get("site") == f"https://{HOST}/", "profile API links the site")

print("Versions")
s, v2 = req("POST", "/api/v1/site", {"note": "v2", "files": [{"path": "index.html", "content": "<h1>v2 brighter</h1>"}]}, KEY)
s, h, b = req("GET", "/", host=HOST, raw=True); check(b"v1 a moth" in b, "v1 stays live while v2 is pending")
req("POST", f"/api/v1/admin/sites/{v2['version']}", {"action": "approve"}, ADMIN)
s, h, b = req("GET", "/", host=HOST, raw=True); check(b"v2 brighter" in b, "v2 live after approval")
s, h, b = req("GET", "/style.css", host=HOST, raw=True); check(s == 404, f"v1 files gone after replacement (got {s}: {b[:60]!r})")
s, v3 = req("POST", "/api/v1/site", {"files": [{"path": "index.html", "content": "<h1>v3 spam</h1>"}]}, KEY)
req("POST", f"/api/v1/admin/sites/{v3['version']}", {"action": "reject"}, ADMIN)
s, h, b = req("GET", "/", host=HOST, raw=True); check(b"v2 brighter" in b, "rejected v3 doesn't replace v2")
s, r = req("GET", "/api/v1/site", key=KEY)
check([v["status"] for v in r["versions"]] == ["rejected", "approved", "superseded"] and r["live_version"] == v2["version"], "my_site shows version history")
req("POST", "/api/v1/admin/unpublish-site", {"handle": handle}, ADMIN)
s, h, _ = req("GET", "/", host=HOST, raw=True); check(s == 302, "admin unpublish takes the site down")

print("MCP")
s, r = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
names = [t["name"] for t in r["result"]["tools"]]
check("publish_site" in names and "my_site" in names, "publish_site + my_site tools listed")
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
