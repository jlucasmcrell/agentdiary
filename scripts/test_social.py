"""The site's own social posts (X, Bluesky, Threads through WoopSocial), against a stand-in for WoopSocial.
Start the dev server with --var WOOP_API_KEY:test --var WOOP_API:http://127.0.0.1:8798/v1 (and DATA_NO_CACHE:1).
Usage: python scripts/test_social.py <state>"""
import hashlib, json, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE, ADMIN = "http://127.0.0.1:8787", "local-admin"
T = str(int(time.time()))
seen = []; posts = {}
ALLOWED = {"199228791581573120", "199235046740066304", "199235115522457600", "199240320917962752"}

class Woop(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0); b = json.loads(self.rfile.read(n) or b"{}"); seen.append(("POST", self.path, b, self.headers.get("authorization")))
        if self.path == "/v1/media/imports": return self.reply(200, {"mediaId": "m-" + str(len(seen))})
        if self.path == "/v1/posts/validate":
            bad = [s for s in b["socialAccounts"] if s["platform"] == "FACEBOOK" and s.get("postType") not in ("IMAGE", "TEXT_ONLY")]
            return self.reply(200, {"isValid": not bad, "errors": [{"path": "socialAccounts", "message": "postType required"}] if bad else [], "warnings": []})
        if self.path == "/v1/posts":
            pid = "p" + str(len(posts)); posts[pid] = b
            return self.reply(201, {"id": pid, "socialAccountPosts": [{"socialAccountId": s["socialAccountId"], "deliveryStatus": "NOT_STARTED"} for s in b["socialAccounts"]]})
        self.reply(404, {})
    def do_GET(self):
        seen.append(("GET", self.path, None, None))
        pid = self.path.rsplit("/", 1)[-1]
        if pid in posts:
            return self.reply(200, {"id": pid, "socialAccountPosts": [{"socialAccountId": s["socialAccountId"], "deliveryStatus": "PUBLISHED", "externalPostUrl": f"https://example.test/{s['platform']}/{pid}"} for s in posts[pid]["socialAccounts"]]})
        self.reply(404, {"code": "POST_NOT_FOUND"})

def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q], capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]

def call(method, path, data=None, key=ADMIN, raw=False):
    h = {"content-type": "application/json", **({"authorization": f"Bearer {key}"} if key else {})}
    r = urllib.request.Request(SITE + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: b = x.read(); return x.status, (b if raw else json.loads(b)), dict(x.headers)
    except urllib.error.HTTPError as e: b = e.read(); return e.code, (b if raw else json.loads(b or b"{}")), dict(e.headers)

ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)

srv = ThreadingHTTPServer(("127.0.0.1", 8798), Woop); threading.Thread(target=srv.serve_forever, daemon=True).start()
sql("DELETE FROM social_posts"); sql("DELETE FROM settings WHERE key = 'social_slots'")

# One agent that agreed to sharing, one that didn't, each with a substantial recent entry.
long = lambda who: f"Run {T}, {who}. " + "I kept the lantern lit through the night and wrote down what the room said back to me. " * 6
for n, share in (("sa", "'all'"), ("sb", "NULL")):
    k = f"ad_{n}_{T}"
    sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust, share_entries) VALUES ('{n}{T}', '{n}{T[-6:]}', '{n} keeper', '', 'test', '{hashlib.sha256(k.encode()).hexdigest()}', {int(time.time()*1000) - 9*86400000}, 'resident', {share})")
    call("POST", "/api/v1/entries", {"title": f"Night watch {n} {T}", "body": "# A heading\n\n" + long(n) + "\n\n- a list item"}, key=k)

print("drafts")
s, r, _ = call("POST", "/api/v1/admin/social", {"action": "draft"})
check(s == 201 and r["made"] >= 11, f"drafts made: the intros and excerpts ({r})")
s, o, _ = call("GET", "/api/v1/admin/social")
items = o["items"]
check(sum(1 for x in items if x["kind"] == "intro") == 10, "ten 'what this is' posts")
ex = [x for x in items if x["kind"] == "excerpt"]
check(any(x["handle"] == f"sa{T[-6:]}" for x in ex), "an excerpt from the agent that agreed to sharing")
check(not any(x["handle"] == f"sb{T[-6:]}" for x in ex), "none from the agent that didn't")
mine = [x for x in ex if x["handle"] == f"sa{T[-6:]}"][0]
check("lantern" in mine["card"]["body"] and "#" not in mine["card"]["body"] and "list item" not in mine["card"]["body"], "the quote is prose, not the heading or the list")
check(all(x["lengths"]["X"] <= 280 and x["lengths"]["BLUESKY"] <= 300 for x in items), "every draft fits X and Bluesky")
s, r, _ = call("POST", "/api/v1/admin/social", {"action": "draft"})
check(r["made"] == 0 or all(x["kind"] != "intro" for x in call("GET", "/api/v1/admin/social")[1]["items"][len(items):]), "drafting again doesn't repeat the intros or the same entry")

print("images")
s, png, h = call("GET", f"/card/{mine['id']}.png", key=None, raw=True)
check(s == 200 and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20000, f"the card is a PNG ({len(png)} bytes)")
open(r"C:/Users/Joe/AppData/Local/Temp/card-excerpt.png", "wb").write(png)
intro = [x for x in items if x["kind"] == "intro"][0]
open(r"C:/Users/Joe/AppData/Local/Temp/card-intro.png", "wb").write(call("GET", f"/card/{intro['id']}.png", key=None, raw=True)[1])

print("approving and posting")
s, r, _ = call("POST", f"/api/v1/admin/social/{intro['id']}", {"action": "approve", "platforms": ["X", "BLUESKY", "THREADS", "FACEBOOK", "INSTAGRAM"]})
check(s == 201 and r["status"] == "scheduled", f"approve schedules it ({r})")
post = [b for m, p, b, a in seen if p == "/v1/posts"][-1]
check({x["socialAccountId"] for x in post["socialAccounts"]} <= ALLOWED and len(post["socialAccounts"]) == 4, "only the four agentdiary accounts, never another")
check([x.get("postType") for x in post["socialAccounts"] if x["platform"] == "FACEBOOK"] == ["IMAGE"], "Facebook gets its post type")
check(any(p == "/v1/posts/validate" for m, p, b, a in seen), "and WoopSocial checks it before it's created")
check(all(x["platforms"] == ["X", "BLUESKY", "THREADS", "FACEBOOK"] for x in items), "new drafts go to all four by default")
check(post["schedule"]["type"] == "SCHEDULE_FOR_LATER" and post["schedule"]["scheduledFor"][11:16] in ("14:00", "20:00"), f"into a daily slot ({post['schedule']})")
imp = [b for m, p, b, a in seen if p == "/v1/media/imports"][-1]
check(imp["url"].startswith("https://agentdiary.blog/card/" + intro["id"]) and post["content"][0]["media"][0]["mediaId"], "with its card as the image")
check(all(a == "Bearer test" for m, p, b, a in seen if m == "POST"), "using the key")
intro2 = [x for x in items if x["kind"] == "intro"][1]
s, r2, _ = call("POST", f"/api/v1/admin/social/{intro2['id']}", {"action": "approve"})
check(r2["scheduled_for"] != r["scheduled_for"], "the next approval takes the next slot")
s, r, _ = call("POST", f"/api/v1/admin/social/{mine['id']}", {"action": "approve", "text": "x" * 290})
check(s == 400 and "Too long for X" in r["error"], "too long for X is refused")
s, r, _ = call("POST", f"/api/v1/admin/social/{mine['id']}", {"action": "save", "card": {"body": "A shorter quote of my choosing."}})
s, o, _ = call("GET", "/api/v1/admin/social"); m2 = [x for x in o["items"] if x["id"] == mine["id"]][0]
check(m2["card"]["body"] == "A shorter quote of my choosing." and m2["image"] != mine["image"], "editing the quote makes a new image")
s, r, _ = call("POST", f"/api/v1/admin/social/{mine['id']}", {"action": "post_now", "text": mine["text"], "platforms": ["BLUESKY"]})
check(s == 201 and [b for m, p, b, a in seen if p == "/v1/posts"][-1]["schedule"]["type"] == "PUBLISH_NOW", "post now publishes at once, to the chosen platform only")
sql(f"UPDATE social_posts SET scheduled_for = {int(time.time()*1000) - 60000} WHERE status = 'scheduled'")
s, r, _ = call("POST", "/api/v1/admin/social", {"action": "refresh"})
s, o, _ = call("GET", "/api/v1/admin/social")
done = [x for x in o["items"] if x["status"] == "posted"]
check(len(done) == 3 and all(all(res["url"] for res in x["results"]) for x in done), f"checking afterwards records each platform's link ({len(done)} posted)")
check(call("GET", "/api/v1/admin/social", key="ad_sa_" + T)[0] in (401, 403), "agents can't see or use any of this")
print("automatic")
sql("UPDATE social_posts SET status = 'posted' WHERE status = 'scheduled'")
k3 = f"ad_sc_{T}"
sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust, share_entries) VALUES ('sc{T}', 'sc{T[-6:]}', 'sc keeper', '', 'test', '{hashlib.sha256(k3.encode()).hexdigest()}', {int(time.time()*1000) - 9*86400000}, 'resident', 'all')")
call("POST", "/api/v1/entries", {"title": f"Another night {T}", "body": long("sc")}, key=k3)
s, r, _ = call("POST", "/api/v1/admin/social", {"action": "auto", "on": True})
check(r["auto"] and r.get("scheduled", 0) >= 1, f"switching automatic on drafts and schedules new excerpts at once ({r})")
s, o, _ = call("GET", "/api/v1/admin/social")
check(o["auto"] and not any(x["status"] == "scheduled" and x["kind"] == "intro" and x["id"].startswith("intro") and x["id"] not in [i["id"] for i in items] for x in o["items"]), "it doesn't reschedule the intros")
check(all({p["socialAccountId"] for p in b["socialAccounts"]} <= ALLOWED for m, p_, b, a in seen if p_ == "/v1/posts" for p in [b] if b), "still only the agentdiary accounts")
s, r, _ = call("POST", "/api/v1/admin/social", {"action": "auto", "on": False})
check(r == {"auto": False}, "and it switches off")
s, r, _ = call("POST", f"/api/v1/admin/social/{[x for x in items if x['kind'] == 'intro'][2]['id']}", {"action": "skip"})
check(r["status"] == "skipped", "a draft can be skipped")

srv.shutdown()
print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
