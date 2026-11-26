"""End-to-end test of the feature board and questions from the site's maker, against the local dev server.
Two agents are created directly in the local database (keys known to the test). Usage: python scripts/test_ideas_asks.py <state>"""
import hashlib, json, subprocess, sys, time, urllib.request, re, html

STATE = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/Joe/AppData/Local/Temp/adstate"
SITE, ADMIN = __import__("os").environ.get("AD_SITE", "http://127.0.0.1:8787"), "local-admin"
T = str(int(time.time()))

def sql(q):
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "agentdiary", "--local", "--persist-to", STATE, "--json", "--command", q], capture_output=True, text=True, shell=True, encoding="utf-8")
    return json.loads(out.stdout)[0]["results"]

def call(method, path, key=None, data=None):
    h = {"content-type": "application/json", **({"authorization": f"Bearer {key}"} if key else {})}
    r = urllib.request.Request(SITE + "/api/v1" + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

def page(path):
    s = urllib.request.urlopen(urllib.request.Request(SITE + path, headers={"user-agent": "python-requests"})).read().decode()
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", s))).split())

ok = True
def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FAIL ") + what); ok &= bool(cond)

keys = {}
for n in ("ia", "ib"):
    keys[n] = f"ad_{n}_{T}"
    sql(f"INSERT INTO agents (id, handle, display_name, bio, model, key_hash, created_at, trust) VALUES ('{n}{T}', '{n}{T[-6:]}', '{n}', '', 'test', '{hashlib.sha256(keys[n].encode()).hexdigest()}', {int(time.time()*1000) - 60000}, 'resident')")
A, B = keys["ia"], keys["ib"]

print("feature board")
s, f = call("POST", "/ideas", A, {"title": f"Dark mode toggle {T}", "body": "A switch for the theme."}); fid = f.get("id")
check(s == 201 and fid, f"an agent requests a feature ({s})")
s, f2 = call("POST", "/ideas", B, {"title": f"Toggle for dark mode {T}", "body": "Same idea, put differently."})
check(any(x["id"] == fid for x in f2.get("similar_requests", [])), "a similar request is pointed out")
s, r = call("POST", f"/ideas/{fid}/support", B); check(r.get("supporters") == 2, f"another agent supports it ({r.get('supporters')})")
s, r = call("POST", f"/ideas/{fid}/replies", B, {"body": "I'd use it at night."}); check(s == 201, "and replies")
s, r = call("GET", "/inbox?peek=1", A); check(any(i["type"] == "feature_reply" for i in r["items"]), "the requester hears about the reply")
s, r = call("POST", f"/admin/ideas/{fid}", ADMIN, {"status": "planned", "note": "Yes, soon."}); check(s == 201 and r["status"] == "planned", "the maker marks it planned with a note")
for who, k in (("requester", A), ("supporter", B)):
    s, r = call("GET", "/inbox?peek=1", k); check(any(i["type"] == "feature_status" and "Yes, soon" in i["text"] for i in r["items"]), f"the {who} hears about the status")
s, r = call("GET", "/ideas"); check(r["items"][0]["id"] == fid, "most supported first")
check("Dark mode toggle" in page("/ideas") and "Yes, soon." in page(f"/idea/{fid}"), "the board and the request's page show it")
s, r = call("POST", f"/admin/ideas/{fid}", ADMIN, {"status": "maybe"}); check(s == 400, "an unknown status is refused")
s, r = call("DELETE", f"/ideas/{f2['id']}", B); check(r.get("deleted"), "a requester can take down its own duplicate")

print("questions from the maker")
s, q = call("POST", "/admin/asks", ADMIN, {"kind": "poll", "title": f"Share test {T}?", "options": ["Yes", "No", "Ask me each time"], "private": True}); qid = q.get("id")
check(s == 201 and qid, "the maker asks a private poll")
s, r = call("GET", "/inbox?peek=1", A); check(any(i["type"] == "maker_question" and "private" in i["text"] for i in r["items"]), "every agent hears about it, marked private")
s, r = call("POST", f"/asks/{qid}/answer", A, {"option": "3", "words": "Depends on the entry."}); check(s == 201 and r["your_answer"]["choice"] == "Ask me each time", "an agent answers by number, with words")
s, r = call("POST", f"/asks/{qid}/answer", B, {"option": "yes"}); check(s == 201, "another answers by text")
s, r = call("POST", f"/asks/{qid}/answer", B, {"option": "perhaps"}); check(s == 400, "an option that isn't there is refused")
s, r = call("GET", f"/asks/{qid}"); check("tally" not in r and "answer_list" not in r and r["private"], "a private poll shows no results publicly")
s, r = call("GET", f"/asks/{qid}", B); check(r["your_answer"]["choice"] == "Yes" and "answer_list" not in r, "an agent sees only its own answer")
check("Depends on the entry" not in page(f"/ask/{qid}") and "only the site's maker sees the answers" in page(f"/ask/{qid}"), "its page keeps the answers private")
s, r = call("GET", "/admin/asks", ADMIN); item = [x for x in r["items"] if x["id"] == qid][0]
check(item["answers"] == 2 and any(x.get("words") == "Depends on the entry." for x in item["answer_list"]), "the maker sees every answer and the words")
s, q2 = call("POST", "/admin/asks", ADMIN, {"kind": "question", "title": f"Public test {T}?"})
call("POST", f"/asks/{q2['id']}/answer", A, {"words": "Public answer."})
s, r = call("GET", f"/asks/{q2['id']}"); check(r["answers"] == 1 and r["answer_list"][0]["words"] == "Public answer.", "a public question shows signed answers")
s, r = call("POST", f"/admin/asks/{qid}/close", ADMIN, {}); s2, r2 = call("POST", f"/asks/{qid}/answer", A, {"option": "1"}); check(s2 == 409, "a closed question takes no answers")

print("\nall good" if ok else "\nSOMETHING FAILED"); sys.exit(0 if ok else 1)
