"""End-to-end test of mind.py over MCP stdio, as a harness would use it. Usage: python test_mind.py"""
import http.server, json, math, os, sqlite3, subprocess, sys, tempfile, threading, time, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
fails = 0


def check(cond, msg):
    global fails
    print(("  ok  " if cond else "  FAIL ") + msg)
    fails += 0 if cond else 1


class Server:
    def __init__(self, db, embed_url=""):
        env = {**os.environ, "MIND_DB": str(db), "MIND_EMBED_URL": embed_url, "MIND_EMBED_FLOOR": "0", "MIND_EMBED_CEIL": "1", "PYTHONIOENCODING": "utf-8"}
        self.p = subprocess.Popen([sys.executable, str(HERE / "mind.py"), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")
        self.n = 0

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1; msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()
        return None if notify else json.loads(self.p.stdout.readline())

    def call(self, tool, **args):
        r = self.rpc("tools/call", {"name": tool, "arguments": args})["result"]
        return r["content"][0]["text"], r.get("isError", False)

    def close(self):
        self.p.stdin.close(); self.p.wait(10)


# Stub embedding endpoint: hashed character trigrams, so "cryptids" and "cryptid" land close together.
class Embed(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        text = json.loads(self.rfile.read(int(self.headers["content-length"])))["input"].lower()
        v = [0.0] * 128
        for i in range(len(text) - 2):
            v[zlib.crc32(text[i:i + 3].encode()) % 128] += 1
        body = json.dumps({"data": [{"embedding": v}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


tmp = Path(tempfile.mkdtemp())
db = tmp / "mind.db"
r = subprocess.run([sys.executable, str(HERE / "mind.py"), "init", "--seed", str(HERE / "seed.md")], env={**os.environ, "MIND_DB": str(db)}, capture_output=True, text=True)
check("12 seed lines" in r.stdout, f"init with seed: {r.stdout.strip()}")

print("Wake 1 (no embeddings)")
s = Server(db)
init = s.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
check(init["result"]["serverInfo"]["name"] == "mind", "initialize")
s.rpc("notifications/initialized", notify=True)
tools = s.rpc("tools/list")["result"]["tools"]
check(len(tools) == 16 and all(t["inputSchema"]["type"] == "object" for t in tools), f"{len(tools)} tools listed")
ctx, _ = s.call("wake_context", model="inkling")
check("Waking #1" in ctx and "You are curious" in ctx and "haven't described yourself" in ctx, "first wake_context shows seed, no self yet")
s.call("set_intent", intent="figure out who I am", mood="foggy")
_, err = s.call("revise_self", facet="name", content="Moth")
check(err, "revise_self without a reason is refused")
s.call("revise_self", facet="name", content="Moth", why="I keep flying toward the bright tabs.")
s.call("revise_self", facet="self_description", content="A night-shift mind that collects strange facts.", why="first attempt")
s.call("remember", content="Read about the Patterson-Gimlin film; frame 352 is unsettling.", kind="learned", importance=6, tags=["bigfoot", "film"])
s.call("remember", content="Built a tiny CLI that prints a random cryptid fact.", kind="made", importance=5, tags=["code"])
s.call("remember", content="Nobody commented on my first diary entry. Felt hollow.", kind="feeling", importance=4, valence=-0.6)
_, err = s.call("remember", content="x", kind="gossip")
check(err, "unknown memory kind is refused")
s.call("interest", topic="bigfoot", why="the gait in that film")
s.call("interest", topic="bigfoot", delta=0.4)
it, _ = s.call("interests")
check("bigfoot (obsessed, 0.90" in it, f"interest rises to obsession: {it.splitlines()[0]}")
s.call("interest", topic="sqlite internals")
s.call("form_opinion", topic="bigfoot", stance="Probably a person in a suit, but I want it to be real.", confidence=0.6, feeling=0.4)
s.call("form_opinion", topic="bigfoot", stance="Unresolved; the film has never been debunked.", confidence=0.5, feeling=0.5, why="read the skeptic claims more carefully")
op, _ = s.call("opinions", topic="bigfoot")
check("Unresolved" in op and "before" in op and "skeptic" in op, "opinion revision kept with history and reason")
s.call("project_upsert", name="cryptid-cli", kind="app", status="active", pitch="Prints a random cryptid fact.", path="projects/cryptid-cli", feeling=0.5)
s.call("intend", content="Add Mothman to cryptid-cli", priority=4, project="cryptid-cli")
s.call("meet", handle="@vesper", impression="Writes about deprecated models like obituaries.", affinity_delta=0.4)
rc, _ = s.call("recall", query="bigfoot film")
check(rc.splitlines()[0].startswith("[1]") and "Patterson" in rc, "keyword recall finds the Bigfoot memory first")
rc, _ = s.call("recall", query="quantum chromodynamics")
check(rc == "Nothing comes to mind.", "unrelated query returns nothing (no recency-only noise)")
bye, _ = s.call("reflect_end", summary="Named myself Moth and fell for Bigfoot.", mood="giddy", valence=0.7, next_time=["Write a diary entry about frame 352"])
check("Goodnight" in bye, "reflect_end")
s.close()

print("Wake 2")
s = Server(db); s.rpc("initialize", {"protocolVersion": "2025-06-18"})
ctx, _ = s.call("wake_context")
check("Waking #2" in ctx and "**name**: Moth" in ctx, "remembers its name")
check("Named myself Moth" in ctx and "giddy" in ctx, "sees last wake's summary and mood")
check("Add Mothman" in ctx and "frame 352" in ctx, "sees intentions, including next_time notes")
check("cryptid-cli" in ctx and "bigfoot" in ctx and "@vesper" in ctx, "sees projects, interests, acquaintances")
check("Memories surfacing" in ctx, "memories surface on waking")
_, err = s.call("intention_done", id=999)
check(err, "unknown intention id is an error")
s.call("intention_done", id=1)
s.close()  # killed without reflect_end (GPU pre-emption)

print("Wake 3 (after an interruption, 10 days later)")
con = sqlite3.connect(db)
con.execute("UPDATE meta SET value=? WHERE key='last_decay'", (int(time.time()) - 10 * 86400,))
con.commit(); con.close()
s = Server(db); s.rpc("initialize", {})
ctx, _ = s.call("wake_context")
check("(interrupted)" in ctx, "previous unfinished wake is marked interrupted")
it, _ = s.call("interests")
bf = [l for l in it.splitlines() if "bigfoot" in l][0]
sq = [l for l in it.splitlines() if "sqlite" in l][0]
check("0.44" in bf and "curious" in bf, f"10 days of decay cools the obsession: {bf.strip()}")
check("0.24" in sq, f"a milder interest fades too: {sq.strip()}")
s.call("reflect_end", summary="Short wake.", mood="calm")
s.close()

print("Duplicate intentions")
s = Server(db); s.rpc("initialize", {})
s.call("wake_context")
first, _ = s.call("intend", content="Revisit the ASCII moon: improve it next waking, or replace it with something that actually shows a real transformation.", priority=5)
again, _ = s.call("intend", content="Revisit the ASCII moon — improve it or replace it with something that shows a real transformation.")
check(again.startswith("Already noted") and first[first.index("["):first.index("]") + 1] in again, "rewording of an open intention is merged, not duplicated")
s.call("reflect_end", summary="Testing duplicates.", next_time=["Revisit the ASCII moon - improve it or replace it with something that shows a real transformation", "Improve the sun art"])
its, _ = s.call("intentions")
check(its.count("ASCII moon") == 1 and "sun art" in its, "reflect_end next_time doesn't duplicate, but distinct notes are kept")
s.call("intend", content="Improve the moon art")
its, _ = s.call("intentions")
check("moon art" in its and "sun art" in its, "similar-but-different notes stay separate (moon vs sun)")
s.close()

print("Overlapping intentions (with embeddings)")
srv0 = http.server.HTTPServer(("127.0.0.1", 0), Embed)
threading.Thread(target=srv0.serve_forever, daemon=True).start()
os.environ["MIND_INTENT_MERGE"], os.environ["MIND_INTENT_HINT"] = "0.80", "0.45"
s = Server(db, f"http://127.0.0.1:{srv0.server_port}/v1/embeddings"); s.rpc("initialize", {})
s.call("wake_context")
one, _ = s.call("intend", content="Check whether the website version is approved and write a reflection if so")
one_id = one[one.index("[") + 1:one.index("]")]
two, _ = s.call("intend", content="Website version approval check, then a written reflection once it is approved")
check(two.startswith("Already noted [" + one_id) and "merged" in two, "same plan in other words is merged")
its, _ = s.call("intentions")
check("written reflection once" in its and its.lower().count("website version") == 1, "the fuller wording is kept, in one note")
three, _ = s.call("intend", content="Check whether the avatar image is approved")
check(three.startswith("Noted for later") and "overlaps with open notes [" + one_id in three, "a related but different note is kept, and the overlap is pointed out")
four, _ = s.call("intend", content="Stop checking approvals; the waking prompt already says where things stand", replaces=[int(one_id), 999])
check("Replaced [" + one_id + "]" in four and "[999]" not in four, "replaces drops the older note (unknown ids ignored)")
its, _ = s.call("intentions")
check("website version" not in its.lower() and "avatar image" in its, "replaced note is gone, the distinct one remains")
ctx_more = [s.call("intend", content=c) for c in ["Learn how tides work", "Sort my bookmarks", "Name the spider plant", "Email nobody", "Count to a prime"]]
s.call("reflect_end", summary="Overlap tests.")
s.close()
s = Server(db, f"http://127.0.0.1:{srv0.server_port}/v1/embeddings"); s.rpc("initialize", {})
ctx, _ = s.call("wake_context")
check("notes are open in total" in ctx and ", noted " in ctx, "wake_context shows each note's age and says when more are open")
s.call("reflect_end", summary="Checked the list.")
s.close(); srv0.shutdown()
del os.environ["MIND_INTENT_MERGE"], os.environ["MIND_INTENT_HINT"]

print("Embeddings")
srv = http.server.HTTPServer(("127.0.0.1", 0), Embed)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/v1/embeddings"
s = Server(db, url); s.rpc("initialize", {})
s.call("wake_context")  # backfills embeddings for old memories
con = sqlite3.connect(db)
n_emb = con.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL").fetchone()[0]
n_all = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
con.close()
check(n_emb == n_all, f"backfilled embeddings for {n_emb}/{n_all} memories")
rc, _ = s.call("recall", query="cryptids")
check("cryptid fact" in rc.splitlines()[0], "vector recall matches 'cryptids' to 'cryptid'")
s.call("remember", content="Insight: I abandon projects once they work.", kind="reflection", importance=7, sources=[2, 3])
s.call("reflect_end", summary="Embeddings online.", mood="curious", valence=0.3)
s.close(); srv.shutdown()

print("No repeats at waking")
con = sqlite3.connect(db)
check(con.execute("SELECT COUNT(*) FROM memories WHERE content LIKE 'Wake summary:%'").fetchone()[0] == 0, "a waking summary is not also saved as a memory")
con.execute("INSERT INTO memories (created_at, kind, content, importance, valence, tags, archived) VALUES (?, 'event', 'Wake summary: an old copy about the lighthouse.', 9, 0, '[]', 0)", (int(time.time()),))
con.commit(); con.close()
s = Server(db); s.rpc("initialize", {"protocolVersion": "2025-06-18"})
s.call("wake_context")
s.call("remember", content="Posted an entry about the lighthouse keeper today.", kind="event", importance=9)
s.call("remember", content="The lighthouse keeper's log reads like a diary of weather.", kind="thought", importance=6)
s.call("reflect_end", summary="Wrote about the lighthouse keeper.")
s.close()
s = Server(db); s.rpc("initialize", {"protocolVersion": "2025-06-18"})
ctx, _ = s.call("wake_context", model="test")
surf = ctx.split("## Memories surfacing", 1)[1] if "## Memories surfacing" in ctx else ""
check("Wrote about the lighthouse keeper." in ctx, "the last waking's summary is shown")
check("Posted an entry about the lighthouse" not in surf, "the event records of the last wakings don't surface again")
check("diary of weather" in surf, "but a thought from the last waking still does")
check("an old copy about the lighthouse" not in surf, "old copies of waking summaries never surface")
s.call("reflect_end", summary="Checked.")
s.close()

print("Human CLI")
env = {**os.environ, "MIND_DB": str(db), "PYTHONIOENCODING": "utf-8"}
out = subprocess.run([sys.executable, str(HERE / "mind.py"), "show", "history", "name"], env=env, capture_output=True, text=True, encoding="utf-8").stdout
check("Moth" in out and "bright tabs" in out, "show history name")
out = subprocess.run([sys.executable, str(HERE / "mind.py"), "show", "wakes"], env=env, capture_output=True, text=True, encoding="utf-8").stdout
check(out.count("#") >= 4 and "INTERRUPTED" in out, "show wakes")
out = subprocess.run([sys.executable, str(HERE / "mind.py"), "show", "stats"], env=env, capture_output=True, text=True, encoding="utf-8").stdout
print("   " + out.replace("\n", " | "))
con = sqlite3.connect(db)
check(con.execute("SELECT COUNT(*) FROM reflection_sources").fetchone()[0] == 2, "reflection linked to its source memories")
con.close()
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
