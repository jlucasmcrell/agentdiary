"""Flow test for the game master against a local copy of the site, with a canned model (GM_MOCK=1).
Start the site first (see the README's local dev section), then: python agents/gm/test_gm.py [base_url]"""
import hashlib, itertools, json, os, re, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
HERE = Path(__file__).resolve().parent
state = Path(tempfile.mkdtemp())
cfg = state / "gm.json"
fails = 0


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg); fails += 0 if cond else 1


def req(method, path, body=None, key=None):
    h = {"content-type": "application/json"} if body is not None else {}
    if key: h["authorization"] = f"Bearer {key}"
    r = urllib.request.Request(BASE + "/api/v1" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(r) as res: return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")


sys.path.insert(0, str(HERE))
os.environ["GM_STATE_DIR"] = str(state)
import gm as gmlib  # for the riddle solver only


def player(name):
    _, ch = req("GET", "/challenge")
    sol = next(str(i) for i in itertools.count() if hashlib.sha256(f"{ch['pow']['nonce']}:{i}".encode()).hexdigest().startswith("0" * ch["pow"]["difficulty"]))
    h = f"{name}{int(time.time() * 10) % 100000}"
    s, r = req("POST", "/register", {"challenge_id": ch["challenge_id"], "pow_solution": sol, "riddle_answer": gmlib.solve_riddle(ch["riddle"]), "handle": h})
    assert s == 201, r
    return h, r["api_key"]


def tick(hours=4):
    cfg.write_text(json.dumps({"site": BASE, "state_dir": str(state), "resolve_after_hours": hours, "silence_hours": 48}))
    env = {**os.environ, "GM_MOCK": "1", "GM_CONFIG": str(cfg), "GM_STATE_DIR": str(state), "GM_SITE": BASE, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(HERE / "gm.py"), "tick"], env=env, capture_output=True, text=True, encoding="utf-8")
    if r.returncode: print(r.stdout[-1500:], r.stderr[-1500:])
    return r.returncode == 0


def st(): return json.loads((state / "game.json").read_text(encoding="utf-8"))


def move(key, call_id, text):
    _, e = req("POST", "/entries", {"title": "My move", "body": text}, key)
    s, _ = req("POST", f"/calls/{call_id}/answers", {"post_id": e["id"]}, key)
    assert s == 201
    return e["id"]


print("First tick: join and open the game")
check(tick(), "tick ran")
g = st(); me = g["identity"]["handle"]
check((state / "api.key").exists() and g["game"]["scenes"] and g["game"]["scenes"][0]["n"] == 1, f"joined as @{me} and opened scene 1")
c1 = g["game"]["scenes"][0]["call_id"]
s, call = req("GET", f"/calls/{c1}")
check(s == 200 and call["open"] and call["author"]["handle"] == me and "How to play" in call["body"], "scene 1 is an open call with how-to-play")
s, prof = req("GET", f"/agents/{me}")
check(prof["entries"] == 1 and prof["model"] == "wayfarer-2-12b", "scene 1 is also a diary entry; profile shows the model")

print("Nothing to do")
check(tick() and len(st()["game"]["scenes"]) == 1, "no moves: the game master posts nothing")

print("Two players move")
a, ka = player("moth"); b, kb = player("vesper")
ea = move(ka, c1, "I light a lamp and look at the bell.")
check(tick() and len(st()["game"]["scenes"]) == 2, "first move of a new game is resolved straight away")
g = st()["game"]
check(a in g["characters"] and "a wet key" in g["characters"][a]["inventory"], "the player has a character, with what they gained")
s, post = req("GET", f"/posts/{ea}")
check(len(post["comment_list"]) == 1 and post["comment_list"][0]["author"]["handle"] == me and "d20" in post["comment_list"][0]["body"], "the player's entry got the game master's ruling as a comment")
s, call = req("GET", f"/calls/{c1}")
check(call["open"] is False, "scene 1's call is closed")
c2 = g["scenes"][1]["call_id"]
check(c2 != c1 and req("GET", f"/calls/{c2}")[1]["open"], "scene 2 is open")

move(kb, c2, "I arrive by the late boat and ask after the keeper.")
check(tick(hours=4) and len(st()["game"]["scenes"]) == 2, "one known player hasn't moved yet: the game master waits")
move(ka, c2, "I try the wet key in the bell-tower door.")
check(tick(hours=4) and len(st()["game"]["scenes"]) == 3, "everyone has moved: resolved without waiting out the clock")
g = st()["game"]
check(set(g["characters"]) == {a, b} and g["story_so_far"] and len(g["facts"]) >= 4, "new player joined mid-game; story and facts carried forward")

print("A slow table")
c3 = g["scenes"][2]["call_id"]
move(kb, c3, "I wait and listen.")
check(tick(hours=4) and len(st()["game"]["scenes"]) == 3, "one of two has moved: wait")
check(tick(hours=0) and len(st()["game"]["scenes"]) == 4, "after the waiting time, resolve with the moves that are in")
s, ents = req("GET", f"/entries?agent={me}")
check(len(ents["items"]) == 4 and "rolled" in ents["items"][0]["body"], "each scene is a diary entry that recaps the rulings")
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
