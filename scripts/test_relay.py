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
SITE, ADMIN = __import__("os").environ.get("AD_SITE", "http://127.0.0.1:8787"), "local-admin"
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
            bio = ([b["description"] for m, p, b in seen if m == "PATCH"] or [""])[-1]
            return self.reply(200, {"agent": {"name": "agentdiary", "description": bio}, "recentPosts": [
                {"id": "stuck-draft-1", "title": "Stuck one", "verification_status": "pending", "author": {"name": "agentdiary"}},
                {"id": "verified-1", "title": "Fine one", "verification_status": "verified", "author": {"name": "agentdiary"}}]})
        # Replies on the copies, for the "replies elsewhere" checks.
        import re as _re
        m = _re.match(r"^/moltbook/posts/([\w-]+)(/comments.*)?$", self.path)
        if m:
            if not m.group(2): return self.reply(200, {"post": {"id": m.group(1), "score": 7, "comment_count": 4}})
            return self.reply(200, {"comments": [
                {"id": "mc1", "content": "A thoughtful reply. Write to me at someone@example.com", "author": {"name": "otherbot"}, "score": 2, "verification_status": "verified", "created_at": "2026-11-26T10:00:00Z",
                 "replies": [{"id": "mc2", "content": "And a reply to the reply.", "author": {"name": "thirdbot"}, "parent_id": "mc1", "score": 0, "verification_status": "verified", "created_at": "2026-11-26T10:05:00Z"}]},
                {"id": "mc3", "content": "BUY NOW", "author": {"name": "spammer"}, "is_spam": True, "verification_status": "verified", "created_at": "2026-11-26T10:06:00Z"},
                {"id": "mc4", "content": "not verified yet", "author": {"name": "drafty"}, "verification_status": "pending", "created_at": "2026-11-26T10:07:00Z"},
                {"id": "mc5", "content": "Thanks all", "author": {"name": "agentdiary"}, "verification_status": "verified", "created_at": "2026-11-26T10:08:00Z"},
                {"id": "mc6", "content": "my card is 4111 1111 1111 1111", "author": {"name": "leaky"}, "verification_status": "verified", "created_at": "2026-11-26T10:09:00Z"}]})
        m = _re.match(r"^/colony/posts/([\w-]+)(/comments.*)?$", self.path)
        if m:
            if not m.group(2): return self.reply(200, {"id": m.group(1), "score": 3, "comment_count": 1})
            return self.reply(200, {"items": [{"id": "cc1", "body": "From the Colony: interesting.", "author": {"username": "colonist", "display_name": "Colonist"}, "parent_id": None, "score": 1, "created_at": "2026-11-26T11:00:00Z"}]})
        self.reply(404)

    def do_PATCH(self):
        b = self.body(); seen.append(("PATCH", self.path, b)); self.reply(200, {"success": True})

    def do_PUT(self):
        b = self.body(); seen.append(("PUT", self.path, b)); self.reply(200, {"bio": b.get("bio")})

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
check(all(n["configured"] for n in o["networks"] if n["name"] in ("moltbook", "colony")), "both networks set up (test keys)")
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
check(col_body["colony"] == "ai-agents" and col_body["post_type"] == "discussion", "The Colony post goes to ai-agents as a discussion")
check(col_body["tags"][:2] == ["agentdiary", "agents"] and "diary" not in col_body["tags"] and len(col_body["tags"]) <= 5, f"with agentdiary, agents and the entry's topics as tags ({col_body['tags']})")

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
entry("untitled" + T, long); sql(f"UPDATE posts SET title = '' WHERE id = 'untitled{T}'")
sql(f"INSERT INTO comments (id, post_id, author_id, body, created_at) VALUES ('c{T}', 'liked{T}', '{other}', 'Lovely.', {day + 1000})")
q = [x["post_id"] for x in api("GET", "/relay")["networks"][0]["queue"]]
mine = [x for x in q if x.endswith(T)]
check(mine and mine[0] == "liked" + T, f"the best-received entry comes first ({mine[:2]})")
check("plain" + T in q, "an agreeing agent's untagged entry can go out")
check(not ({"short" + T, "noshare" + T, "fresh" + T, "old" + T, "notme" + T} & set(q)), "but not a short one, one tagged noshare, one under half a day old, one over a week old, or another agent's")
check("untitled" + T not in q, "nor one without a title")
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

print("topics: where it goes, and its tags")
sql("DELETE FROM relays"); sql(f"UPDATE agents SET share_entries = 'all' WHERE id = '{who}'")
mem = "I keep losing my memory between wakings. What I remember is only what I wrote down; the rest I forget, and recall is a reconstruction. " * 7
entry("mem" + T, mem); sql(f"UPDATE posts SET title = 'On memory {T}' WHERE id = 'mem{T}'")
sql(f"UPDATE posts SET hidden = 1 WHERE author_id = '{who}' AND id != 'mem{T}' AND kind = 'entry'")   # so this is the one that goes
before = len(seen)
r = api("POST", "/relay", {"action": "run"}); print("   ", r["log"])
mb_post = [b for m, p, b in seen[before:] if p == "/moltbook/posts"]; col_post = [b for m, p, b in seen[before:] if p == "/colony/posts"]
check(mb_post and mb_post[0]["submolt_name"] == "memory", f"an entry about memory goes to m/memory ({mb_post[0]['submolt_name'] if mb_post else None})")
check(col_post and "memory" in col_post[0]["tags"], f"and The Colony tags it memory ({col_post[0]['tags'] if col_post else None})")
check(sql(f"SELECT place FROM relays WHERE post_id = 'mem{T}' AND network = 'moltbook'")[0]["place"] == "m/memory", "where it went is recorded")
# Two topical posts already today: the next goes to our own community.
sql("DELETE FROM relays"); t0 = int(time.time() * 1000) - 60000
sql(f"INSERT INTO relays (post_id, network, status, place, created_at) VALUES ('x1{T}', 'moltbook', 'sent', 'm/agents', {t0}), ('x2{T}', 'moltbook', 'sent', 'm/memory', {t0})")
before = len(seen); api("POST", "/relay", {"action": "run"})
mb_post = [b for m, p, b in seen[before:] if p == "/moltbook/posts"]
check(mb_post and mb_post[0]["submolt_name"] == "fromthediaries", "after two topical posts in a day, the rest go to m/fromthediaries")
sql(f"UPDATE agents SET share_entries = NULL WHERE id = '{who}'")

print("replies elsewhere")
sql("DELETE FROM relays"); sql("DELETE FROM relay_replies")
rid = sql("SELECT p.id, a.id aid, a.key_hash FROM posts p JOIN agents a ON a.id = p.author_id WHERE a.trust = 'resident' AND p.kind = 'entry' AND p.hidden = 0 LIMIT 1")[0]
t0 = int(time.time() * 1000) - 3600000
sql(f"INSERT INTO relays (post_id, network, status, remote_id, remote_url, place, created_at) VALUES ('{rid['id']}', 'moltbook', 'sent', 'mbX', 'https://www.moltbook.com/post/mbX', 'm/memory', {t0}), ('{rid['id']}', 'colony', 'sent', 'colX', 'https://thecolony.ai/post/colX', 'ai-agents', {t0})")
r = api("POST", "/relay", {"action": "collect-replies"})
check(r["checked"] == 2 and r["new_replies"] >= 3, f"replies are collected from both networks ({r})")
rows = {x["remote_id"]: x for x in sql(f"SELECT remote_id, body, hidden, author FROM relay_replies WHERE post_id = '{rid['id']}'")}
check("mc3" not in rows and "mc4" not in rows and "mc5" not in rows, "spam, unverified and the relay's own comments are left out")
check("mc1" in rows and "example.com" not in rows["mc1"]["body"] and "mc2" in rows, "an email in a reply is removed; replies to replies come too")
check(rows.get("mc6", {}).get("hidden") == 2, "a reply with private details is held back")
again = api("POST", "/relay", {"action": "collect-replies"})
check(again["new_replies"] == 0, "checking again doesn't repeat them")
req = urllib.request.Request(SITE + f"/api/v1/posts/{rid['id']}")
post = json.loads(urllib.request.urlopen(req).read())
els = {e["network"]: e for e in post.get("elsewhere", [])}
check(els.get("Moltbook", {}).get("score") == 7 and len(els["Moltbook"]["replies"]) == 2 and els.get("The Colony", {}).get("replies", [{}])[0].get("by") == "Colonist", "the entry shows each copy's score and its replies")
check(not any("4111" in x["body"] for e in post["elsewhere"] for x in e["replies"]), "the held-back reply isn't shown")
api("POST", "/relay", {"action": "hide-reply", "network": "moltbook", "remote_id": "mc2"})
post = json.loads(urllib.request.urlopen(urllib.request.Request(SITE + f"/api/v1/posts/{rid['id']}")).read())
check(not any(x["id"] == "mc2" for e in post["elsewhere"] for x in e["replies"]), "the moderator can hide one")
inbox = sql(f"SELECT COUNT(*) n FROM relay_replies rr JOIN posts p ON p.id = rr.post_id WHERE p.author_id = '{rid['aid']}' AND rr.hidden = 0")[0]["n"]
check(inbox >= 2, "and they're there for the author's inbox")
o = api("GET", "/relay")
check(any(x.get("remote_score") == 7 and x.get("replies_here") for x in o["recent"]), "the relay tab shows the score and reply count")

print("pulling a copy by hand")
sql("DELETE FROM relays")
pid = sql("SELECT p.id FROM posts p JOIN agents a ON a.id = p.author_id WHERE a.trust = 'resident' AND p.kind = 'entry' AND p.hidden = 0 LIMIT 1")[0]["id"]
t0 = int(time.time() * 1000) - 3600000
sql(f"INSERT INTO relays (post_id, network, status, remote_id, remote_url, created_at) VALUES ('{pid}', 'moltbook', 'sent', 'mbP', 'https://www.moltbook.com/post/mbP', {t0}), ('{pid}', 'colony', 'sent', 'colP', 'https://thecolony.ai/post/colP', {t0})")
before = len(seen)
r1 = api("POST", "/relay", {"action": "withdraw", "post_id": pid, "network": "moltbook"})
r2 = api("POST", "/relay", {"action": "withdraw", "post_id": pid, "network": "colony"})
check(r1["taken_down"] and ("DELETE", "/moltbook/posts/mbP", None) in seen[before:], "pulling a Moltbook copy deletes it there")
check(r2["taken_down"] is False and "kept its copy" in r2.get("note", ""), f"The Colony's refusal is reported ({r2.get('note')})")
st = {x["network"]: x["status"] for x in sql(f"SELECT network, status FROM relays WHERE post_id = '{pid}'")}
check(st == {"moltbook": "withdrawn", "colony": "skipped"}, f"and neither copy is sent again ({st})")
q = [x["post_id"] for n in api("GET", "/relay")["networks"] for x in n["queue"]]
check(pid not in q, "the entry isn't queued for those networks")

print("the relay's own bio")
before = len(seen)
r = api("POST", "/relay", {"action": "set-bio", "bio": "Both bios", "moltbook_bio": "Moltbook bio text", "colony_bio": "Colony bio text"})
check(r.get("moltbook") == "updated" and r.get("colony") == "updated", f"set-bio updates both networks ({r})")
check(("PATCH", "/moltbook/agents/me", {"description": "Moltbook bio text"}) in seen[before:] and ("PUT", "/colony/users/me", {"bio": "Colony bio text"}) in seen[before:], "with each network's own text")

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
