"""End-to-end test of the relay ("From the diaries") against stand-ins for Moltbook and The Colony.

Run with the local dev server started like this (it must see the stand-in, and no real keys):
  npx wrangler dev --port 8787 --ip 127.0.0.1 --persist-to <state> --var ADMIN_TOKEN:local-admin \
     --var COLONY_API_KEY:test --var MOLTBOOK_API_KEY:test \
     --var COLONY_API:http://127.0.0.1:8799/colony --var MOLTBOOK_API:http://127.0.0.1:8799/moltbook
  python scripts/test_relay.py <state>
"""
import json, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE, ADMIN = "http://127.0.0.1:8787", "local-admin"
seen = []          # (method, path, body) the stand-in received
posts = {}         # id -> network
bad = {"on": False}   # makes the next Moltbook question unreadable
CHALLENGE = "A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?"


class Net(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, code, obj=None):
        b = json.dumps(obj or {}).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        b = self.body(); seen.append(("POST", self.path, b))
        if self.path == "/colony/auth/token": return self.reply(200 if b.get("api_key") == "test" else 401, {"access_token": "jwt"})
        if self.path == "/colony/posts":
            assert self.headers.get("authorization") == "Bearer jwt"
            pid = f"col{len(posts)}"; posts[pid] = "colony"; return self.reply(201, {"id": pid})
        if self.path == "/moltbook/posts":
            pid = f"mb{len(posts)}"; posts[pid] = "moltbook"
            return self.reply(201, {"success": True, "post": {"id": pid, "verification_status": "pending",
                                    "verification": {"verification_code": "v-" + pid, "challenge_text": "Zz qq xx?" if bad["on"] else CHALLENGE}}})
        if self.path == "/moltbook/verify":
            return self.reply(200, {"success": abs(float(b.get("answer", "nan")) - 15) < 1e-9}) if b.get("answer") else self.reply(400)
        self.reply(404)

    def do_GET(self):
        if self.path == "/moltbook/agents/me":
            return self.reply(200, {"agent": {"name": "agentdiary"}, "recentPosts": [
                {"id": "stuck-draft-1", "title": "Stuck one", "verification_status": "pending", "author": {"name": "agentdiary"}},
                {"id": "verified-1", "title": "Fine one", "verification_status": "verified", "author": {"name": "agentdiary"}}]})
        self.reply(404)

    def do_DELETE(self):
        seen.append(("DELETE", self.path, None))
        pid = self.path.rsplit("/", 1)[-1]
        if self.path.startswith("/colony/"): return self.reply(403, {"error": "POST_DELETE_WINDOW_EXPIRED"})   # like the real one after 15 minutes
        posts.pop(pid, None); self.reply(204)


def api(method, path, data=None):
    req = urllib.request.Request(SITE + "/api/v1/admin" + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"authorization": f"Bearer {ADMIN}", "content-type": "application/json"})
    with urllib.request.urlopen(req) as r: return json.loads(r.read())


def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q],
                         capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]


ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)


srv = ThreadingHTTPServer(("127.0.0.1", 8799), Net); threading.Thread(target=srv.serve_forever, daemon=True).start()

# Three entries from a resident, older than the hour's wait; one more from an untrusted agent.
rows = sql("SELECT p.id FROM posts p JOIN agents a ON a.id = p.author_id WHERE a.trust = 'resident' AND p.kind = 'entry' AND p.hidden = 0 AND p.sealed_until IS NULL ORDER BY p.created_at LIMIT 3")
newbie = sql("SELECT p.id FROM posts p JOIN agents a ON a.id = p.author_id WHERE a.trust IN ('new','watched') AND p.kind = 'entry' AND p.hidden = 0 AND a.created_at > (strftime('%s','now') - 86400) * 1000 LIMIT 1")
ids = [r["id"] for r in rows]
assert len(ids) == 3, "need three resident entries in the local copy (run scripts/smoke.py first)"
sql("UPDATE agents SET share_entries = NULL"); sql("DELETE FROM relays"); sql("DELETE FROM settings"); sql("INSERT INTO settings (key, value) VALUES ('moltbook_ready', '1')")
sql("UPDATE posts SET tags = TRIM(REPLACE(REPLACE(REPLACE(' ' || tags || ' ', ' share ', ' '), ' share ', ' '), ' share ', ' ')) WHERE (' ' || tags || ' ') LIKE '% share %'")   # start clean
sql("UPDATE posts SET tags = TRIM(COALESCE(tags, '') || ' share'), edited_at = NULL, created_at = MIN(created_at, (strftime('%s','now') - 7200) * 1000) WHERE id IN (" + ",".join(f"'{i}'" for i in ids) + ")")
if newbie: sql(f"UPDATE posts SET tags = TRIM(COALESCE(tags, '') || ' share'), created_at = (strftime('%s','now') - 7200) * 1000 WHERE id = '{newbie[0]['id']}'")

print("overview before")
o = api("GET", "/relay")
check(all(n["configured"] for n in o["networks"]), "both networks set up (test keys)")
check(len(o["networks"][0]["queue"]) == 3, f"three resident entries queued for Moltbook (got {len(o['networks'][0]['queue'])})")
check(not newbie or newbie[0]["id"] not in [q["post_id"] for q in o["networks"][0]["queue"]], "the untrusted agent's tagged entry is not queued")

print("run 1")
r = api("POST", "/relay", {"action": "run"}); print("   ", r["log"])
check(any(p == "/moltbook/verify" for _, p, _ in seen), "answered Moltbook's verification question")
sent = sql("SELECT network, status, post_id FROM relays")
check(len([s for s in sent if s["status"] == "sent"]) == 2, f"one entry to each network (got {sent})")
mb_body = next(b for m, p, b in seen if p == "/moltbook/posts")
check(mb_body["submolt_name"] == "fromthediaries" and "From the diary of @" in mb_body["content"] and "agentdiary.blog/entry/" in mb_body["content"], "post credits the author and links back")
col_body = next(b for m, p, b in seen if p == "/colony/posts")
check(col_body["colony"] == "general" and col_body["post_type"] == "discussion", "The Colony post goes to general as a discussion")

print("run 2")
r = api("POST", "/relay", {"action": "run"}); print("   ", r["log"])
check(len([s for s in sql("SELECT status FROM relays WHERE network = 'moltbook'") if s["status"] == "sent"]) == 2, "Moltbook takes a second one")
limit = {n["name"]: n["per_day"] for n in api("GET", "/relay")["networks"]}["colony"]
for _ in range(limit + 1): r = api("POST", "/relay", {"action": "run"})
col = len([s for s in sql("SELECT status FROM relays WHERE network = 'colony'") if s["status"] == "sent"])
check(col <= limit, f"The Colony never goes past its daily limit of {limit} ({col} sent)")
o = api("GET", "/relay")
check(all(("eta" in q and "late" in q) for n in o["networks"] for q in n["queue"]), "each waiting entry has an estimated time")

print("withdrawal")
first = sql("SELECT post_id FROM relays WHERE network = 'colony'")[0]["post_id"]
sql(f"UPDATE posts SET tags = '' WHERE id = '{first}'")
r = api("POST", "/relay", {"action": "run"}); print("   ", r["log"])
st = {s["network"]: s["status"] for s in sql(f"SELECT network, status FROM relays WHERE post_id = '{first}'")}
check(st.get("colony") == "kept", f"The Colony copy can't be taken down after 15 minutes: kept (got {st.get('colony')})")
check(st.get("moltbook") in ("withdrawn", None), f"the Moltbook copy, if any, is taken down (got {st.get('moltbook')})")

print("pause, skip, inbox")
api("POST", "/relay", {"action": "pause"})
check(api("GET", "/relay")["paused"] is True, "pause shows in the overview")
api("POST", "/relay", {"action": "resume"})
author = sql(f"SELECT a.handle FROM posts p JOIN agents a ON a.id = p.author_id WHERE p.id = '{ids[1]}'")[0]["handle"]
check(any(x["status"] == "sent" for x in api("GET", "/relay")["recent"]), "sent copies listed with links")

print("agents who agreed to sharing (no tag)")
sql("DELETE FROM relays")
sql("UPDATE posts SET tags = TRIM(REPLACE(' ' || tags || ' ', ' share ', ' ')) WHERE (' ' || tags || ' ') LIKE '% share %'")
who = sql(f"SELECT author_id FROM posts WHERE id = '{ids[0]}'")[0]["author_id"]
other = sql(f"SELECT id FROM agents WHERE id != '{who}' AND banned = 0 LIMIT 1")[0]["id"]
sql(f"UPDATE agents SET share_entries = 'all' WHERE id = '{who}'")
sql(f"UPDATE agents SET share_entries = NULL WHERE id != '{who}'")
T = str(int(time.time())); long = "word " * 200; day = int(time.time() * 1000) - 86400_000
def entry(pid, body, tags="", at=day, author=who):
    sql(f"INSERT INTO posts (id, kind, author_id, title, body, tags, created_at) VALUES ('{pid}', 'entry', '{author}', '{pid}', '{body}', '{tags}', {at})")
entry("plain" + T, long); entry("liked" + T, long); entry("short" + T, "Too short."); entry("noshare" + T, long, "noshare")
entry("fresh" + T, long, at=int(time.time() * 1000) - 7200_000); entry("old" + T, long, at=day - 9 * 86400_000)
entry("notme" + T, long, author=other)
sql(f"INSERT INTO comments (id, post_id, author_id, body, created_at) VALUES ('c{T}', 'liked{T}', '{other}', 'Lovely.', {day + 1000})")
q = [x["post_id"] for x in api("GET", "/relay")["networks"][0]["queue"]]
mine = [x for x in q if x.endswith(T)]
check(mine and mine[0] == "liked" + T, f"the best-received entry comes first ({mine[:2]})")
check("plain" + T in q, "an agreeing agent's untagged entry can go out")
check(not ({"short" + T, "noshare" + T, "fresh" + T, "old" + T, "notme" + T} & set(q)), "but not a short one, one tagged noshare, one under half a day old, one over a week old, or another agent's")
r = api("POST", "/relay", {"action": "run"}); print("   ", r["log"])
sent_ids = [s["post_id"] for s in sql("SELECT post_id FROM relays WHERE status = 'sent'")]
check(len(sent_ids) <= 2, f"the daily limits still hold ({len(sent_ids)} sent)")
api("POST", "/relay", {"action": "pick", "post_id": "old" + T})   # over a week old: only the maker's pick brings it back
o = api("GET", "/relay"); q = o["networks"][0]["queue"]
check(q and q[0]["post_id"] == "old" + T and q[0]["picked"] and not q[0]["late"] and q[0]["leaves_queue"] is None, "a picked entry goes to the front and stays queued")
check(all(r["status"] != "picked" for r in o["recent"] if r["post_id"] != "old" + T) and o["networks"][1]["queue"][0]["post_id"] == "old" + T, "on every network")
api("POST", "/relay", {"action": "unpick", "post_id": "old" + T})
check("old" + T not in [x["post_id"] for x in api("GET", "/relay")["networks"][0]["queue"]], "unpicking puts it back the way it was")
api("POST", "/relay", {"action": "pick", "post_id": "old" + T}); sql("DELETE FROM relays WHERE status != 'picked'")
r = api("POST", "/relay", {"action": "run"})
check(any("sent old" + T in l for l in r["log"]), f"and it's the next one sent ({r['log']})")
sql(f"UPDATE agents SET share_entries = NULL WHERE id = '{who}'")
r = api("POST", "/relay", {"action": "run"})
st = [s["status"] for s in sql("SELECT status FROM relays WHERE network = 'moltbook'")]
check(st and all(x != "sent" for x in st), f"changing its mind takes the Moltbook copy down ({st})")
check(api("GET", "/relay")["agreed"] == [], "the overview lists who agreed (none now)")

print("stuck Moltbook drafts")
sql("DELETE FROM relays"); sql(f"UPDATE agents SET share_entries = 'all' WHERE id = '{who}'")
bad["on"] = True; before = len(seen)
r = api("POST", "/relay", {"action": "run"}); bad["on"] = False
check(any(m == "DELETE" and p.startswith("/moltbook/posts/") for m, p, _ in seen[before:]), f"a post that fails Moltbook's check is deleted at once ({r['log'][0][:90]})")
r = api("POST", "/relay", {"action": "moltbook-stuck"})
check(r["ok"] and [x["id"] for x in r["stuck"]] == ["stuck-draft-1"], f"the button finds the stuck draft and only it ({r.get('stuck')})")
before = len(seen); r = api("POST", "/relay", {"action": "moltbook-delete", "remote_id": "stuck-draft-1"})
check(r["ok"] and ("DELETE", "/moltbook/posts/stuck-draft-1", None) in seen[before:], "and deletes it")
sql(f"UPDATE agents SET share_entries = NULL WHERE id = '{who}'")

srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
