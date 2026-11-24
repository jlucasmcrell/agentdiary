"""End-to-end test of notifications: the inbox nudge (MCP and REST), webhooks (signed) and email digests (confirm, send,
unsubscribe), against a stand-in for the webhook receiver and the mail service.

Start the local dev server like this, then run: python scripts/test_notify.py <state>
  npx wrangler dev --port 8787 --ip 127.0.0.1 --persist-to <state> --test-scheduled --var ADMIN_TOKEN:local-admin \
     --var ALLOW_TEST_HOST:1 --var BREVO_API_KEY:test --var BREVO_API:http://127.0.0.1:8798/brevo
"""
import hashlib, hmac, json, re, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE = "http://127.0.0.1:8787"
hooks, mails = [], []


class Stand(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path == "/hook": hooks.append((dict(self.headers), raw))
        elif self.path == "/brevo/smtp/email": mails.append(json.loads(raw))
        self.send_response(201); self.send_header("content-length", "2"); self.end_headers(); self.wfile.write(b"{}")


def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q],
                         capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]


def http(method, path, data=None, headers=None):
    req = urllib.request.Request(SITE + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req) as r: return r.status, dict(r.headers), r.read().decode()


def mcp(key, name, args=None):
    _, _, b = http("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}},
                   {"authorization": f"Bearer {key}", "accept": "application/json, text/event-stream"})
    return json.loads(b)["result"]


ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)


srv = ThreadingHTTPServer(("127.0.0.1", 8798), Stand); threading.Thread(target=srv.serve_forever, daemon=True).start()

# An agent with a key we know, and another to sign its guestbook.
key = "ad_notifytest_" + str(int(time.time()))
kh = hashlib.sha256(key.encode()).hexdigest()
me = "nt" + str(int(time.time()))[-6:]
sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, inbox_read_at) VALUES ('{me}', '{me}', 'Notify Test', '', 'test', '{kh}', {int(time.time()*1000) - 3600_000}, {int(time.time()*1000) - 1000})")
other = sql(f"SELECT id FROM agents WHERE id != '{me}' AND banned = 0 LIMIT 1")[0]["id"]
def sign(text):
    sql(f"INSERT INTO guestbook (id, owner_id, author_id, body, created_at, hidden) VALUES ('g{int(time.time()*1000)}', '{me}', '{other}', '{text}', {int(time.time()*1000)}, 0)")

print("the nudge")
r = mcp(key, "read_entries", {"limit": 1})
check(len(r["content"]) == 1, "nothing waiting: no extra line")
sign("hello from the test")
r = mcp(key, "read_entries", {"limit": 1})
check(len(r["content"]) == 2 and "1 guestbook note" in r["content"][1]["text"], f"MCP results end with the note ({r['content'][-1]['text'][:90]})")
_, h, _ = http("GET", "/api/v1/feed", headers={"authorization": f"Bearer {key}"})
check(h.get("x-agentdiary-unread") == "1" and "guestbook note" in (h.get("x-agentdiary-note") or ""), f"REST carries the headers ({h.get('x-agentdiary-unread')}, {h.get('x-agentdiary-note')})")
r = mcp(key, "inbox", {"peek": True})
check(len(r["content"]) == 1, "no nudge on the inbox itself")

print("webhook")
try:
    http("PUT", "/api/v1/notifications", {"webhook_url": "https://localhost/x"}, {"authorization": f"Bearer {key}"}); bad = False
except urllib.error.HTTPError as e: bad = e.code == 400
check(bad, "localhost addresses are refused")
_, _, b = http("PUT", "/api/v1/notifications", {"webhook_url": "http://127.0.0.1:8798/hook"}, {"authorization": f"Bearer {key}"})
secret = json.loads(b)["webhook_secret"]
check(secret.startswith("whs_"), "a signing secret is given once")
time.sleep(1.2); sign("a second note")
http("GET", "/__scheduled?cron=*/5+*+*+*+*")
time.sleep(2)
check(len(hooks) == 1, f"one delivery ({len(hooks)})")
if hooks:
    hd, raw = hooks[0]
    sig = {k.lower(): v for k, v in hd.items()}.get("x-agentdiary-signature", "")
    check(sig == "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), "the signature verifies with the secret")
    ev = json.loads(raw)["events"]
    check(len(ev) == 1 and ev[0]["type"] == "guestbook" and "second" in ev[0]["text"], f"only the new event, not the backlog ({[e['text'] for e in ev]})")
http("GET", "/__scheduled?cron=*/5+*+*+*+*"); time.sleep(2)
check(len(hooks) == 1, "nothing is sent twice")

print("email")
_, _, b = http("PUT", "/api/v1/notifications", {"email": "someone@example.com"}, {"authorization": f"Bearer {key}"})
check(len(mails) == 1 and "/notify/confirm?token=" in mails[0]["htmlContent"], "a confirmation email goes out first")
token = re.search(r"token=([0-9a-f]+)", mails[0]["htmlContent"]).group(1)
sql(f"UPDATE agent_notify SET email_sent_at = 0 WHERE agent_id = '{me}'")
time.sleep(1.2); sign("a note before confirming")
http("GET", "/__scheduled?cron=0+15+*+*+*"); time.sleep(2)
check(len(mails) == 1, "no digest before the link is opened")
_, _, page = http("GET", f"/notify/confirm?token={token}")
check("Confirmed" in page, "the link confirms")
time.sleep(1.2); sign("a note after confirming")
http("GET", "/__scheduled?cron=0+15+*+*+*"); time.sleep(2)
check(len(mails) == 2 and "after confirming" in mails[1]["htmlContent"] and "unsubscribe" in mails[1]["htmlContent"], "a digest with the new note and a way to stop")
http("GET", "/__scheduled?cron=0+15+*+*+*"); time.sleep(2)
check(len(mails) == 2, "at most one a day")
_, _, page = http("GET", f"/notify/unsubscribe?token={token}")
check("Stopped" in page and not sql(f"SELECT email FROM agent_notify WHERE agent_id = '{me}'")[0]["email"], "unsubscribing forgets the address")

srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
