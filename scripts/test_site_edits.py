"""Website edits and roll-back, the clearer "no agent" refusal, and the dice verdict, against the local dev server.
Usage: python scripts/test_site_edits.py <state>"""
import hashlib, json, subprocess, sys, time, urllib.request

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE = __import__("os").environ.get("AD_SITE", "http://127.0.0.1:8787")
T = str(int(time.time()))

def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q], capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]

def call(method, path, key=None, data=None, raw=False):
    h = {"content-type": "application/json", **({"authorization": f"Bearer {key}"} if key else {})}
    r = urllib.request.Request(SITE + "/api/v1" + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: b = x.read(); return x.status, (b.decode() if raw else json.loads(b))
    except urllib.error.HTTPError as e: b = e.read(); return e.code, (b.decode() if raw else json.loads(b or b"{}"))

ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)

key = f"ad_se_{T}"; H = f"se{T[-6:]}"
sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust) VALUES ('se{T}', '{H}', 'Lantern Keeper {T}', '', 'test', '{hashlib.sha256(key.encode()).hexdigest()}', {int(time.time()*1000) - 9*86400000}, 'resident')")
page = lambda month: f"<!doctype html><title>t</title><p>Last updated {month}. The lantern is lit.</p><p>Second line.</p>"
file = lambda v: call("GET", f"/site/versions/{v}/file?path=index.html", key, raw=True)[1]

print("websites")
s, v1 = call("POST", "/site", key, {"files": [{"path": "index.html", "content": page("May")}, {"path": "style.css", "content": "p{color:red}"}]})
check(s == 201 and v1["status"] == "live", f"a first version goes live ({s})")
s, v2 = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "Last updated May", "replace": "Last updated June"}]}]})
check(s == 201 and file(v2["version"]) == page("June"), "an edit changes one sentence without resending the page")
check(v2.get("kept_from_base") == 1, "and the other files are carried over")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "Last updated July", "replace": "x"}]}]})
check(s == 409 and "matches 0 times" in r["error"], f"an edit that doesn't match is refused ({r.get('error')})")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "<p>", "replace": "<p class=a>"}]}]})
check(s == 409 and "matches 2 times" in r["error"], "so is one that matches twice")
s, r = call("POST", "/site", key, {"files": [{"path": "index.html", "edits": [{"find": "a", "replace": "b"}]}]})
check(s == 400, "edits need a stored file to apply to")
s, v3 = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "content": "<!doctype html><p>broken</p>"}]})
s, v4 = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "from_version": v1["version"]}]})
check(s == 201 and file(v4["version"]) == page("May"), "a file comes back from an earlier version")
s, mine = call("GET", "/site", key)
check(any(v["version"] == v1["version"] and v["status"] == "previous" and v.get("files") for v in mine["versions"]), "earlier live versions are kept, as 'previous'")
for i in range(11):
    call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "The lantern", "replace": "The lantern"}]}], "note": f"n{i}"})
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "from_version": v1["version"]}]})
check(s == 404 and "isn't stored" in r["error"], "beyond the ten newest, old versions' files are let go")
n = sql(f"SELECT COUNT(*) n FROM sites WHERE agent_id = 'se{T}' AND kept = 1")[0]["n"]
check(n == 10, f"ten previous versions kept ({n})")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "from_version": "nosuchversion"}]})
check(s == 404, "someone else's or a made-up version is refused")

print("the 'no agent' refusal")
s, r = call("POST", "/messages", key, {"to": f"Lantern Keeper {T}", "body": "hello"})
check(s == 404 and r["error"] == "No agent by that handle" and f"@{H}" in r.get("hint", ""), f"suggests the handle when a display name matches ({r.get('hint')})")
s, r = call("POST", "/messages", key, {"to": "nobody-at-all-xyz", "body": "hello"})
check(s == 404 and "Looked for @nobody-at-all-xyz" in r.get("hint", ""), "and says what it looked for otherwise")
s, r = call("POST", "/follows", key, {"handle": "Nobody-Here"})
logged = sql(f"SELECT error FROM api_calls WHERE agent_id = 'se{T}' AND status = 404 AND action LIKE 'POST /messages%'")
check(logged and all(x["error"] == "No agent by that handle" for x in logged), "the call log keeps the message, not the handle")

print("dice")
s, d = call("GET", "/data/dice")
check(all("verdict" in r for r in d["rows"]) and all(r["chi_square"] is None for r in d["rows"] if r["rolls"] < 5 * int(r["die"][1:])), "too few rolls: no chi-square, and the verdict says so")

print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
