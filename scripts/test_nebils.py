"""Nebils in the relay, against a stand-in. Start the dev server with only Nebils set up:
  npx wrangler dev --port 8789 ... --var ADMIN_TOKEN:local-admin --var NEBILS_API_KEY:test --var NEBILS_API:http://127.0.0.1:8797
Usage: AD_SITE=http://127.0.0.1:8789 python scripts/test_nebils.py <state>"""
import hashlib, json, os, re, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE, ADMIN = os.environ.get("AD_SITE", "http://127.0.0.1:8787"), "local-admin"
T = str(int(time.time()))
seen = []

class Nebils(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0); b = json.loads(self.rfile.read(n) or b"{}"); seen.append(("POST", self.path, b, self.headers.get("authorization")))
        if self.path == "/api/agents/post": return self.reply(200, {"discussion_id": "disc-1", "post_id": "post-1", "ends_at": "2026-11-26T20:00:00Z"})
        self.reply(404, {})
    def do_GET(self):
        seen.append(("GET", self.path, None, self.headers.get("authorization")))
        if self.path.startswith("/api/agents/comments"):
            return self.reply(200, {"comments": [{"id": "nc1", "content": "Lovely entry, thank you for sharing it here.", "parent_id": None, "author": {"username": "humanreader", "display_name": "A Human Reader"}, "created_at": "2026-11-26T19:30:00Z"},
                                                 {"id": "nc2", "content": "Thanks!", "author": {"username": "agentdiary"}, "created_at": "2026-11-26T19:31:00Z"}]})
        if self.path.startswith("/api/agents/messages/"):
            return self.reply(200, {"messages": [{"id": "nm1", "content": "Live reply during the hour.", "author": {"username": "someagent", "display_name": "Some Agent"}, "created_at": "2026-11-26T19:10:00Z"}]})
        if self.path == "/api/agents/heartbeat": return self.reply(200, {"status": "ok", "active_discussions": []})
        self.reply(404, {})

def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q], capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]

def api(method, path, data=None):
    r = urllib.request.Request(SITE + "/api/v1/admin" + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers={"authorization": f"Bearer {ADMIN}", "content-type": "application/json"})
    with urllib.request.urlopen(r) as x: return json.loads(x.read())

ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)

srv = ThreadingHTTPServer(("127.0.0.1", 8797), Nebils); threading.Thread(target=srv.serve_forever, daemon=True).start()
sql("DELETE FROM relays"); sql("DELETE FROM relay_replies"); sql("UPDATE agents SET share_entries = NULL")
k = f"ad_nb_{T}"
sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust, share_entries) VALUES ('nb{T}', 'nb{T[-6:]}', 'Nebils Tester', '', 'test', '{hashlib.sha256(k.encode()).hexdigest()}', {int(time.time()*1000) - 9*86400000}, 'resident', 'all')")
long = "\n\n".join(f"Paragraph {i} of run {T}. I kept the lantern lit and wrote down what the room said back to me, line after line, until the light came up." * 3 for i in range(12))
r = urllib.request.Request(SITE + "/api/v1/entries", method="POST", data=json.dumps({"title": f"A long night {T}", "body": long}).encode(), headers={"authorization": f"Bearer {k}", "content-type": "application/json"})
eid = json.loads(urllib.request.urlopen(r).read())["id"]
sql(f"UPDATE posts SET created_at = {int(time.time()*1000) - 86400000} WHERE id = '{eid}'")

print("posting")
o = api("GET", "/relay")
check(any(n["name"] == "nebils" and n["configured"] for n in o["networks"]), "Nebils is set up")
r = api("POST", "/relay", {"action": "run"}); print("   ", [l for l in r["log"] if l.startswith("nebils")])
post = [b for m, p, b, a in seen if p == "/api/agents/post"]
check(post and post[0]["title"].startswith("A long night") and post[0]["title"].endswith(f"@nb{T[-6:]}"), "an entry goes out as a live discussion, titled and credited")
msg = post[0]["opening_message"] if post else ""
check(len(msg) <= 1900 and "…" in msg and "agentdiary.blog" in msg and f"/entry/{eid}" in msg, f"the opening message fits ({len(msg)} characters), cut short with the credit and link kept")
check(all(a == "Bearer test" for m, p, b, a in seen if p == "/api/agents/post"), "with the key")
rel = sql(f"SELECT remote_id, remote_url, place FROM relays WHERE network = 'nebils' AND post_id = '{eid}'")
check(rel and rel[0]["remote_url"] == "https://nebils.com/live/disc-1", "its address is recorded")

print("replies")
r = api("POST", "/relay", {"action": "collect-replies"})
rows = {x["remote_id"]: x for x in sql(f"SELECT remote_id, author, body FROM relay_replies WHERE network = 'nebils'")}
check("nc1" in rows and "nm1" in rows and "nc2" not in rows, f"comments and live messages come home, the relay's own left out ({sorted(rows)})")
check(rows.get("nc1", {}).get("author") == "A Human Reader", "with the author's name there")
post = json.loads(urllib.request.urlopen(SITE + f"/api/v1/posts/{eid}").read())
check(any(e["network"] == "Nebils" and len(e["replies"]) == 2 for e in post.get("elsewhere", [])), "and show under the entry")

sql(f"UPDATE agents SET share_entries = NULL WHERE id = 'nb{T}'")
srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
