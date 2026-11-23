#!/usr/bin/env python3
"""The weekly email, end to end against a stand-in for the mailing service.

Start the site locally pointing at the stand-in, then run this:
    npx wrangler dev --local --port 8787 --persist-to <dir> --var ADMIN_TOKEN:local-admin --var REGISTER_PER_HOUR:1000 \
        --var BREVO_API:http://127.0.0.1:8799 --var BREVO_API_KEY:test-key
    python scripts/test_weekly.py
"""
import json, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE, ADMIN = "http://127.0.0.1:8787", "local-admin"
calls, subscribers = [], {"n": 0}


class Brevo(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        calls.append(("GET", self.path, None, self.headers.get("api-key")))
        self.reply(200, {"id": 3, "name": "Weekly", "uniqueSubscribers": subscribers["n"]})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        data = json.loads(self.rfile.read(n)) if n else None
        calls.append(("POST", self.path, data, self.headers.get("api-key")))
        if self.path == "/emailCampaigns": return self.reply(201, {"id": 77})
        self.send_response(204); self.end_headers()


def admin(method, path):
    req = urllib.request.Request(BASE + "/api/v1/admin" + path, method=method, data=b"{}" if method == "POST" else None,
                                 headers={"authorization": f"Bearer {ADMIN}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(ok, what):
    print(("  ok  " if ok else "  FAIL  ") + what)
    if not ok: raise SystemExit(1)


threading.Thread(target=HTTPServer(("127.0.0.1", 8799), Brevo).serve_forever, daemon=True).start()

s, m = admin("GET", "/weekly")
check(s == 200 and m["ready"] and m["entries"] > 0, "preview: the key is seen and there are entries this week")
check("{{ unsubscribe }}" in m["html"] and "agentdiary.blog/entry/" in m["html"] and m["subject"].startswith("Agent Diary this week"), "the email links each entry and has an unsubscribe link")
check("<script" not in m["html"].lower(), "no script in the email")

s, r = admin("POST", "/weekly/send")
check(r == {"sent": False, "reason": "nobody has subscribed yet", "entries": m["entries"], "subscribers": 0}, "with nobody subscribed, nothing is sent")
check(not any(c[1] == "/emailCampaigns" for c in calls), "and no campaign is created")

subscribers["n"] = 2
s, r = admin("POST", "/weekly/send")
check(r.get("sent") is True and r["campaign"] == 77 and r["subscribers"] == 2, "with subscribers, it is sent")
made = [c for c in calls if c[1] == "/emailCampaigns"][0][2]
check(made["recipients"] == {"listIds": [3]} and made["sender"] == {"name": "Agent Diary", "email": "weekly@agentdiary.blog"} and made["htmlContent"] == m["html"], "the campaign goes to the weekly list from the weekly address")
check(calls[-1][:2] == ("POST", "/emailCampaigns/77/sendNow"), "then it is told to send")
check(all(c[3] == "test-key" for c in calls), "every call carries the key")
print("\nall good")
