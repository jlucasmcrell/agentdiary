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
                                    "verification": {"verification_code": "v-" + pid, "challenge_text": CHALLENGE}}})
        if self.path == "/moltbook/verify":
            return self.reply(200, {"success": abs(float(b.get("answer", "nan")) - 15) < 1e-9}) if b.get("answer") else self.reply(400)
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
sql("DELETE FROM relays"); sql("DELETE FROM settings"); sql("INSERT INTO settings (key, value) VALUES ('moltbook_ready', '1')")
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
check(any("most for a day" in l for l in r["log"] if l.startswith("colony")), "The Colony stops at one a day")
check(len([s for s in sql("SELECT status FROM relays WHERE network = 'moltbook'") if s["status"] == "sent"]) == 2, "Moltbook takes a second one")

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

srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
