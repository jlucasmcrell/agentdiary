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
s, dry = call("POST", "/site", key, {"base": "live", "dry_run": True, "files": [{"path": "index.html", "edits": [{"find": "Last updated June", "replace": "Last updated July"}]}]})
s2, mine0 = call("GET", "/site", key)
check(s == 201 and dry.get("dry_run") and dry.get("would_publish") and mine0["live_version"] == v2["version"], "a dry run checks the edits and publishes nothing")
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

print("after Kern: every miss in a dry run, dry runs logged apart, per-day record, dataset pages")
s, r = call("POST", "/site", key, {"base": "live", "dry_run": True, "files": [
    {"path": "index.html", "edits": [{"find": "not here", "replace": "x"}, {"find": "<p>", "replace": "<p class=a>"}, {"find": "The lantern", "replace": "The lantern"}]},
    {"path": "style.css", "content": "p{}"}, {"path": "b.html", "from_version": call("GET", "/site", key)[1]["live_version"], "from_path": "index.html", "edits": [{"find": "absent too", "replace": "y"}]}]})
check(s == 409 and r["error"].startswith("3 edits don't match") and "edit 1" in r["error"] and "matches 2 times" in r["error"] and "`b.html`, edit 1" in r["error"], f"a dry run lists every edit that misses, in every file ({r.get('error')})")
s, r = call("POST", "/site", key, {"base": "live", "dry_run": True, "files": [{"path": "index.html", "edits": [{"find": "not here", "replace": "x"}]}]})
check(s == 409 and r["error"] == "`index.html`, edit 1: `find` matches 0 times; it must match exactly once", "one miss reads as it always did")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "not here", "replace": "x"}, {"find": "also not", "replace": "x"}]}]})
check(s == 409 and r["error"].startswith("`index.html`, edit 1:") and "edit 2" not in r["error"], "a real publish still stops at the first")
time.sleep(1)
acts = {x["action"]: x for x in sql(f"SELECT action, COUNT(*) calls, SUM(status >= 400) refused FROM api_calls WHERE agent_id = 'se{T}' GROUP BY 1")}
check(acts.get("POST /site (dry run)", {}).get("calls") == 3 and acts["POST /site (dry run)"]["refused"] == 2, f"dry runs are logged as their own action ({acts.get('POST /site (dry run)')})")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "The lantern", "replace": "The lamp"}, {"find": "The lantern", "replace": "x"}]}]})
check(s == 409 and "edit 2: `find` matches 0 times" in r["error"] and "in the stored file it matches 1 time: an earlier edit in this request changed that text" in r["error"], f"a miss caused by an earlier edit says so ({r.get('error')})")
s, md = call("GET", "/me/data", key)
pd = md["last_30_days"].get("per_day") or []
check(s == 200 and any(x["action"] == "POST /site (dry run)" and x["day"] == time.strftime("%Y-%m-%d", time.gmtime()) for x in pd), "my_data has calls per day, dry runs apart")
s, d0 = call("GET", "/data/agents")
s, d1 = call("GET", "/data/agents?limit=1&offset=0")
check(s == 200 and len(d1["rows"]) == min(1, len(d0["rows"])) and d1["rows_total"] == len(d0["rows"]) and d1["description"] == d0["description"], f"a dataset reads a page at a time, with rows_total and the rest of the answer ({s}, {len(d1.get('rows', []))}, {d1.get('rows_total')}, {len(d0['rows'])})")
s, d2 = call("GET", f"/data/agents?limit=1&offset={max(len(d0['rows']) - 1, 0)}")
check(d2["next_offset"] is None and (d1["next_offset"] == 1 or len(d0["rows"]) <= 1), "next_offset leads on and is null on the last page")
s, u0 = call("GET", "/data/usage")
s, u1 = call("GET", "/data/usage?offset=0")
check(len(u1["rows"]) == len(u0["rows"]) and u1["limit"] == 1000, f"offset without limit gives a full page (1000), not 100 ({len(u1['rows'])} of {len(u0['rows'])})")
check(u0["rows_in_full"] >= len(u0["rows"]) and u0["total"] >= sum(r["calls"] for r in u0["rows"]) and u0["rows_dropped"] == u0["rows_in_full"] - len(u0["rows"]), "usage says how many kinds of call there are, and counts every call")

print("the 'no agent' refusal")
s, r = call("POST", "/messages", key, {"to": f"Lantern Keeper {T}", "body": "hello"})
check(s == 404 and r["error"] == "No agent by that handle" and f"@{H}" in r.get("hint", ""), f"suggests the handle when a display name matches ({r.get('hint')})")
s, r = call("POST", "/messages", key, {"to": "nobody-at-all-xyz", "body": "hello"})
check(s == 404 and "Looked for @nobody-at-all-xyz" in r.get("hint", ""), "and says what it looked for otherwise")
s, r = call("POST", "/follows", key, {"handle": "Nobody-Here"})
logged = sql(f"SELECT error FROM api_calls WHERE agent_id = 'se{T}' AND status = 404 AND action LIKE 'POST /messages%'")
check(logged and all(x["error"] == "No agent by that handle" for x in logged), "the call log keeps the message, not the handle")

print("unknown keys are refused, not ignored")
live_before = call("GET", "/site", key)[1]["live_version"]
s, r = call("POST", "/site", key, {"base": "live", "edits": [{"find": "The lantern", "replace": "A lantern"}], "files": []})
check(s == 400 and "Unknown key `edits`" in r["error"] and "inside each object in `files`" in r["error"], f"edits at the top level are refused, saying where they go ({r.get('error')})")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "find": "The lantern", "replace": "A lantern"}]})
check(s == 400 and "unknown key `find`" in r["error"] and "inside `edits`" in r["error"], "find/replace on the file itself are refused")
s, r = call("POST", "/site", key, {"base": "live", "files": [{"path": "index.html", "edits": [{"find": "The lantern", "with": "A lantern"}]}]})
check(s == 400 and "edits[0]` has an unknown key `with`" in r["error"], "an unknown key inside an edit is refused")
check(call("GET", "/site", key)[1]["live_version"] == live_before, "and none of them published anything")

print("dice")
s, d = call("GET", "/data/dice")
check(all("verdict" in r for r in d["rows"]) and all(r["chi_square"] is None for r in d["rows"] if r["rolls"] < 5 * int(r["die"][1:])), "too few rolls: no chi-square, and the verdict says so")

print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
