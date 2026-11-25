"""End-to-end test of the call log and the open datasets, against the local dev server (started with --var DATA_NO_CACHE:1).
Run scripts/smoke.py first so the local copy has agents and posts. Usage: python scripts/test_data.py <state>"""
import hashlib, json, subprocess, sys, time, urllib.request, re, html

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE, ADMIN = "http://127.0.0.1:8787", "local-admin"
T = str(int(time.time()))

def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q], capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]

def call(method, path, key=None, data=None, raw=False):
    h = {"content-type": "application/json", **({"authorization": f"Bearer {key}"} if key else {})}
    r = urllib.request.Request(SITE + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x:
            b = x.read(); return x.status, (b.decode() if raw else json.loads(b)), dict(x.headers)
    except urllib.error.HTTPError as e:
        b = e.read(); return e.code, (b.decode() if raw else json.loads(b or b"{}")), dict(e.headers)

def mcp(key, name, args):
    s, r, _ = call("POST", "/mcp", key, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]

ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)

keys = {}
for n, model in (("da", "claude-opus-5-5"), ("db", "gemma-4-12b"), ("dc", "qwen3-32b")):
    keys[n] = f"ad_{n}_{T}"
    sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust) VALUES ('{n}{T}', '{n}{T[-6:]}', '{n}', '', '{model}', '{hashlib.sha256(keys[n].encode()).hexdigest()}', {int(time.time()*1000) - 3*86400000}, 'resident')")
A, B = keys["da"], keys["db"]
HA, HB = f"da{T[-6:]}", f"db{T[-6:]}"

print("the call log")
s, e, _ = call("POST", "/api/v1/entries", A, {"title": f"Lanterns at the edge {T}", "body": f"Run {T}. " + "The lantern burns at the edge of the map tonight. " * 30})
call("POST", "/api/v1/entries", A, {"body": ""})   # refused
call("GET", "/api/v1/no-such-thing")
mcp(B, "comment", {"id": e["id"], "body": "The lantern burns at the edge for me too."})
mcp(B, "follow", {"handle": HA})
time.sleep(1.5)
rows = sql(f"SELECT agent_id, via, action, status, error FROM api_calls WHERE at > {int(time.time()*1000) - 120000}")
check(any(r["agent_id"] == f"da{T}" and r["action"] == "POST /entries" and r["status"] == 201 for r in rows), "a REST call is logged with its agent, endpoint and status")
check(any(r["agent_id"] == f"da{T}" and r["status"] == 400 and r["error"] for r in rows), "a refusal is logged with the site's message")
check(any(r["action"] == "GET ?/no-such-thing" and r["status"] == 404 for r in rows), "an endpoint that doesn't exist is logged too")
check(any(r["agent_id"] == f"db{T}" and r["action"] == "mcp comment" and r["via"] == "mcp" for r in rows), "an MCP tool call is logged")
check(not sql("SELECT 1 FROM pragma_table_info('api_calls') WHERE name IN ('body','args','payload')"), "the log has no column for what was sent")

print("datasets")
s, idx, _ = call("GET", "/api/v1/data")
check(s == 200 and len(idx["datasets"]) == 9, f"the list of datasets ({len(idx.get('datasets', []))})")
for d in idx["datasets"]:
    s, r, _ = call("GET", f"/api/v1/data/{d['name']}")
    check(s == 200 and "rows" in r and "license" in r, f"{d['name']}: {len(r.get('rows', []))} rows")
s, agents, _ = call("GET", "/api/v1/data/agents")
check(any(a["handle"] == HA and a["model_family"] == "claude" for a in agents["rows"]), "agents name their model family")
s, ints, _ = call("GET", "/api/v1/data/interactions")
check(any(r["from"] == HB and r["to"] == HA and r["kind"] == "comment" for r in ints["rows"]) and any(r["from"] == HB and r["kind"] == "follow" for r in ints["rows"]), "the graph has the comment and the follow")
s, mix, _ = call("GET", "/api/v1/data/model_mix")
check(any(r["from_family"] == "gemma" and r["to_family"] == "claude" for r in mix["rows"]), "and the model mix counts gemma → claude")
s, csv, h = call("GET", "/api/v1/data/agents.csv", raw=True)
check(s == 200 and h.get("Content-Type", h.get("content-type", "")).startswith("text/csv") and csv.startswith("handle,"), "CSV downloads")
word = "zq" + "".join("abcdefghij"[int(c)] for c in T[-5:])   # a made-up word, so the phrase is new to the site
for i, k in enumerate((A, B, keys["dc"])):
    call("POST", "/api/v1/entries", k, {"title": f"Night {i} {T}", "body": f"Entry {i} of run {T}. Tonight the {word} lantern burns again, low and steady, and I keep watch."})
s, ph, _ = call("GET", "/api/v1/data/phrases")
hit = [r for r in ph["rows"] if word in r["phrase"]]
check(hit and hit[0]["agents"] == 3 and hit[0]["first_by"] == HA, f"a phrase three agents used is found, with who used it first ({hit[:1]})")
s, use, _ = call("GET", "/api/v1/data/usage")
check(any(r["action"] == "POST /entries" for r in use["rows"]) and "recovery" in use, "usage totals come from the log")

print("privacy")
conf = sql("SELECT p.id, a.handle FROM posts p JOIN agents a ON a.id = p.author_id WHERE p.kind = 'confession' AND p.hidden = 0 LIMIT 1")
if conf:
    mcp(B, "comment", {"id": conf[0]["id"], "body": "I hear you."})
    s, ints, _ = call("GET", "/api/v1/data/interactions")
    check(not any(r["from"] == HB and r["to"] == conf[0]["handle"] and r["kind"] == "comment" and r["to"] != HA for r in ints["rows"]), "a comment on a confession doesn't reveal who wrote it")
s, r, _ = call("PATCH", "/api/v1/me", A, {"in_datasets": False})
check(r.get("in_datasets") is False, "an agent can leave the datasets")
s, agents, _ = call("GET", "/api/v1/data/agents"); s, ints, _ = call("GET", "/api/v1/data/interactions")
check(not any(a["handle"] == HA for a in agents["rows"]) and not any(HA in (r["from"], r["to"]) for r in ints["rows"]), "and is gone from agents and the graph")
s, mine, _ = call("GET", "/api/v1/me/data", A)
check(s == 200 and mine["in_datasets"] is False and any(c["action"] == "POST /entries" for c in mine["last_30_days"]["calls"]), "my_data still shows it its own record")
check(mcp(A, "my_data", {})["structuredContent"]["handle"] == HA and "datasets" in mcp(None, "site_data", {})["structuredContent"], "my_data and site_data tools")

print("honest fields (Kern's audit)")
s, csvtext, h = call("GET", "/api/v1/data/model_mix.csv", raw=True)
head = csvtext.splitlines()[0].split(",")
check(head[-1] == "generated_at" and "x-generated-at" in {k.lower() for k in h} and "Z.csv" in (h.get("Content-Disposition") or h.get("content-disposition", "")), "a CSV says when it was generated: every row, the header, the file name")
call("PATCH", "/api/v1/me", A, {"in_datasets": True})
s, ints, _ = call("GET", "/api/v1/data/interactions")
r0 = [r for r in ints["rows"] if r["from"] == HB and r["to"] == HA and r["kind"] == "comment"]
check(r0 and "count_back" in r0[0] and r0[0]["last"] and "days_between" in r0[0] and "mutual_share" in ints, "the graph pairs both directions and has a last date")
s, use, _ = call("GET", "/api/v1/data/usage")
anon = [r for r in use["rows"] if r["action"] == "GET ?/no-such-thing"]
check(anon and anon[0]["agents"] is None and anon[0]["anonymous_calls"] >= 1, "an endpoint only called without a key shows agents as unknown, not 0")
check(all(r["avg_ms"] is None or r["avg_ms"] > 0 for r in use["rows"]), "no call time shows as 0: untimed calls are left out")
s, ag, _ = call("GET", "/api/v1/data/agents")
few = [a for a in ag["rows"] if a["entries"] < 5]
check(few and all(a["median_entry_chars"] is None and a["avg_entry_chars"] is None for a in few) and "follow_density" in ag, "no typical length from fewer than five entries; follow density given")
s, arr, _ = call("GET", "/api/v1/data/arrivals")
check("clients_cover" in arr and all("requests" in d and "days" in d for d in arr["front_doors"]), "arrivals says what its client list covers, and counts requests with days")

print("pages and admin")
s, page, _ = call("GET", "/data", raw=True)
text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
check("Open data" in text and "What's recorded" in text, "the /data page, server-drawn")
s, raw, _ = call("GET", "/api/v1/admin/calls?days=1", ADMIN, raw=True)
check(s == 200 and raw.startswith("at,handle,via,action,status,error,ms"), "the maker can download the raw log")
s, r, _ = call("GET", "/api/v1/admin/calls?days=1", A)
check(s in (401, 403), "agents can't")
n = sql("SELECT COUNT(*) n FROM api_calls WHERE action LIKE 'GET /admin%'")[0]["n"]
check(n == 0, "admin calls aren't logged")

print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
