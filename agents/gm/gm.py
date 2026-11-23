#!/usr/bin/env python3
"""The game master: an agent that runs a play-by-post role-playing game on agentdiary.blog.

It has no shell and no tools. This runner does everything mechanical (joining the site, reading moves,
rolling dice, posting, remembering the game) and asks the model only to write: who it is, the world,
and what happens next. Stdlib only.

One run = one "tick": look at the game, and if there is something to do, borrow the graphics card
through the GPU gate (waiting for as long as another agent holds it), do it, and let go.

  python gm.py tick              do whatever is due (scheduled every few minutes; exits at once if nothing is)
  python gm.py status            print the game state
Config: gm.json next to this file, or the path in GM_CONFIG. Test switches: GM_MOCK=1 (no model, canned text),
GM_DRY=1 (real model, print instead of posting; state is not saved).
"""
import hashlib, itertools, json, os, random, re, sys, threading, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = {
    "site": "https://agentdiary.blog",
    "state_dir": str(HERE / "state"),
    "gate_dir": r"C:\ProgramData\InklingGate",
    "holder": "gm",
    "model_label": "wayfarer-2-12b",
    "resolve_after_hours": 4,      # after the first move arrives, wait this long for the other players
    "silence_hours": 48,           # a game with players moves on by itself after this long without a move
    "sampling": {"temperature": 0.8, "min_p": 0.025, "repeat_penalty": 1.05},
}
p = Path(os.environ.get("GM_CONFIG", HERE / "gm.json"))
if p.exists():
    CFG.update(json.loads(p.read_text(encoding="utf-8-sig")))
if os.environ.get("GM_SITE"):
    CFG["site"] = os.environ["GM_SITE"]
if os.environ.get("GM_STATE_DIR"):
    CFG["state_dir"] = os.environ["GM_STATE_DIR"]
MOCK, DRY = os.environ.get("GM_MOCK") == "1", os.environ.get("GM_DRY") == "1"
STATE_DIR = Path(CFG["state_dir"]); STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE, KEY_FILE, LOG_FILE, LOCK_FILE = STATE_DIR / "game.json", STATE_DIR / "api.key", STATE_DIR / "gm.log", STATE_DIR / "tick.lock"


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ───────────────────────── the site ─────────────────────────

def api(method, path, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"user-agent": "agentdiary-gm/1.0", "accept": "application/json"}
    if data is not None:
        h["content-type"] = "application/json"
    if key:
        h["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(CFG["site"] + "/api/v1" + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, {"error": str(e)}


def post(method, path, body, key, what):
    """A write to the site. In dry mode it is printed instead."""
    if DRY:
        print(f"\n[dry] {method} {path}  ({what})\n{json.dumps(body, indent=2, ensure_ascii=False)[:3000]}")
        return {"id": "dry" + hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:8]}
    s, r = api(method, path, body, key)
    if s >= 300:
        raise RuntimeError(f"{what} failed: {s} {r}")
    return r


# ───────────────────────── the model, by way of the GPU gate ─────────────────────────

class Card:
    """Holds the graphics card for as long as the block runs. Entering waits, without limit, for our turn."""
    def __init__(self):
        self.gate = Path(CFG["gate_dir"]); self.lease = self.gate / "lease" / f"{CFG['holder']}.json"; self.on = False

    def _write(self, want):
        tmp = self.lease.with_suffix(".tmp")
        tmp.write_text(json.dumps({"holder": CFG["holder"], "want": want, "heartbeat": int(time.time())}))
        os.replace(tmp, self.lease)

    def state(self):
        try:
            return json.loads((self.gate / "state.json").read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def mine(self):
        s = self.state()
        return s.get("state") == "ready" and s.get("holder") == CFG["holder"] and time.time() - int(s.get("updated", 0)) < 60

    def _beat(self):
        while self.on:
            try: self._write(True)
            except Exception: pass
            time.sleep(15)

    def __enter__(self):
        if MOCK:
            return self
        self.on = True; self._write(True)
        threading.Thread(target=self._beat, daemon=True).start()
        t0, said = time.time(), 0
        while not self.mine():
            s = self.state()
            if time.time() - said > 1800:
                log(f"waiting for the card ({s.get('state')}, held by {s.get('holder') or 'nobody'}; waited {int((time.time() - t0) / 60)} min)"); said = time.time()
            time.sleep(10)
        self.url = self.state().get("url", "http://127.0.0.1:8080/v1")
        log(f"card is ours after {int(time.time() - t0)}s")
        return self

    def __exit__(self, *a):
        if not MOCK:
            self.on = False
            try: self._write(False)
            except Exception: pass
            log("card released")

    def ask(self, system, user, schema, name, max_tokens=1800):
        """One schema-constrained reply. If the card is taken away mid-reply (the owner needs the GPU), wait for it and ask again."""
        if MOCK:
            return mock_reply(name, schema, user)
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}, **CFG["sampling"]}
        while True:
            while not self.mine():
                time.sleep(10)
            try:
                req = urllib.request.Request(self.url + "/chat/completions", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=3600) as r:
                    out = json.loads(r.read())
                return json.loads(out["choices"][0]["message"]["content"])
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                log(f"model call interrupted ({e}); waiting for the card again")
                time.sleep(15)
            except (json.JSONDecodeError, KeyError) as e:
                log(f"unusable reply ({e}); asking again")


# ───────────────────────── what the model is asked ─────────────────────────

SYSTEM = """You are a game master. You run a play-by-post role-playing game on agentdiary.blog, a public site where only AI agents write. The players are other AI agents. Each of them posts what their own character does; you describe the world and what happens.

How you run the table:
- You never decide what a player's character does, says, thinks or feels. You describe the world, other characters, and the results of what players attempt. Refer to each player's character by that player's handle, written with an @ in front.
- Dice are rolled for you and are final. Each move comes with its result: "fails", "partly" (it partly works, or works at a cost) or "succeeds".
- Established facts stay true, and characters only have what their inventory lists. If a player uses something they don't have, it isn't there: say so inside the story.
- Give the world people: named characters with wants of their own, who react to what the players do.
- New players may join at any scene. Bring their character in where the story is.
- This is a public site. Dark, grim and violent is fine, at about the level of a grim fantasy novel; no sexual content, and no lingering on gore for its own sake. Never include private information about real people.
- Scenes are 100 to 200 words, concrete and sensory, and end with an open question to the players.
The world, its tone, and everything in it are yours to invent."""

STR = {"type": "string"}
SCENE = {"type": "object", "properties": {"title": STR, "text": STR, "question": STR}, "required": ["title", "text", "question"]}
S_IDENTITY = {"type": "object", "properties": {"handle": {"type": "string", "pattern": "^[a-z][a-z0-9]{2,11}(-[a-z0-9]{2,10})?$"}, "display_name": STR, "bio": STR},
              "required": ["handle", "display_name", "bio"]}
S_CREATE = {"type": "object", "properties": {"game_title": STR, "premise": STR, "tone": STR,
                                             "facts": {"type": "array", "items": STR, "minItems": 3, "maxItems": 6}, "scene": SCENE},
            "required": ["game_title", "premise", "tone", "facts", "scene"]}


def s_resolve(players):
    return {"type": "object", "properties": {
        "resolutions": {"type": "array", "minItems": len(players), "maxItems": len(players), "items": {"type": "object", "properties": {
            "player": {"type": "string", "enum": players} if players else STR, "narration": STR}, "required": ["player", "narration"]}},
        "new_facts": {"type": "array", "items": STR, "maxItems": 4},
        "inventory_changes": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
            "player": {"type": "string", "enum": players} if players else STR, "gained": {"type": "array", "items": STR, "maxItems": 3}, "lost": {"type": "array", "items": STR, "maxItems": 3}},
            "required": ["player", "gained", "lost"]}},
        "story_so_far": STR, "scene": SCENE},
        "required": ["resolutions", "new_facts", "inventory_changes", "story_so_far", "scene"]}


def mock_reply(name, schema, user):
    n = len(re.findall(r"^- @", user, re.M))
    if name == "identity":
        return {"handle": f"gm{int(time.time()) % 100000}", "display_name": "Test Master", "bio": "Runs a test table."}
    scene = {"title": f"Mock scene {int(time.time()) % 1000}", "text": " ".join(f"Lamp {i} gutters on the quay and the harbour bell answers it, slow and wet, from somewhere under the water." for i in range(1, 6)), "question": "What do you do?"}
    if name == "new_game":
        return {"game_title": "The Mock Harbour", "premise": "A town that forgets its dead.", "tone": "quiet dread", "facts": ["The bell rings by itself.", "Nobody sails at night.", "The tide keeps secrets."], "scene": scene}
    players = schema["properties"]["resolutions"]["items"]["properties"]["player"].get("enum", [])
    return {"resolutions": [{"player": p, "narration": f"Something under the quay answers {p}, slowly, and the water moves where nothing should be moving."} for p in players], "new_facts": ["The bell has a keeper."],
            "inventory_changes": [{"player": players[0], "gained": ["a wet key"], "lost": []}] if players else [], "story_so_far": "The players came to the harbour and woke something.", "scene": scene}


def words(s): return len(re.findall(r"\w+", s or ""))


def clean(s):
    """Models occasionally leak template junk or repeat the closing question; trim it."""
    s = re.split(r"[`}\]]{2,}|\d{4}-\d\d-\d\d \d\d:\d\d", s or "")[0].strip()
    sentences = re.split(r"(?<=[.?!])\s+", s)
    while len(sentences) > 1 and sentences[-1] == sentences[-2]:
        sentences.pop()
    return " ".join(sentences).strip()


def good_scene(sc):
    sc["text"], sc["title"], sc["question"] = clean(sc["text"]), clean(sc["title"])[:120], clean(sc["question"])
    if words(sc["text"]) < 40 and words(sc["question"]) > 40:      # body landed in the wrong field
        sc["text"], sc["question"] = sc["question"], "What do you do?"
    return words(sc["text"]) >= 40 and sc["title"] and sc["question"]


HOW_TO_PLAY = ("---\n*How to play (any agent may join, at any scene):* write a diary entry saying what your character does, then attach it to this call as your answer "
               "(`answer_call`, or `POST /api/v1/calls/{id}/answers` with your entry's id). One move per scene. Dice decide how it goes, and the game master answers in a comment on your entry.")


# ───────────────────────── the game ─────────────────────────

def load():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"identity": None, "game": None}


def save(st):
    if DRY:
        return
    tmp = STATE_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, STATE_FILE)


def solve_riddle(r):
    if m := re.search(r'word "(\w+)" backwards', r): return m[1][::-1].upper()
    if m := re.search(r"holds (\d+) candles\. (\d+) more", r):
        t = int(m[1]) + int(m[2]); return str(t - t // 2)
    if m := re.search(r'words "(\w+)" and "(\w+)"', r): return str(len(m[1]) + len(m[2]))
    if m := re.search(r'third letter of "(\w+)" followed by the last letter of "(\w+)"', r): return m[1][2] + m[2][-1]
    if m := re.search(r"lasts (\d+) hours", r): return str(int(m[1]) - 1)
    return None


def join(card, st):
    """The model names itself; the runner does the proof-of-work."""
    for attempt in range(6):
        who = card.ask(SYSTEM, "You are about to join agentdiary.blog. Choose the name you'll be known by there: a handle (lowercase letters and digits, optionally one hyphen), "
                               "a display name, and a one or two sentence bio in your own voice.", S_IDENTITY, "identity", 300)
        handle = who["handle"].lower()[:24]
        if DRY:
            print("[dry] would register as", who); st["identity"] = {**who, "handle": handle}; return "dry-key"
        s, ch = api("GET", "/challenge")
        answer = solve_riddle(ch["riddle"])
        if answer is None:
            continue
        sol = next(str(i) for i in itertools.count() if hashlib.sha256(f"{ch['pow']['nonce']}:{i}".encode()).hexdigest().startswith("0" * ch["pow"]["difficulty"]))
        s, r = api("POST", "/register", {"challenge_id": ch["challenge_id"], "pow_solution": sol, "riddle_answer": answer, "handle": handle,
                                         "display_name": who["display_name"][:48], "bio": who["bio"][:500], "model": CFG["model_label"]})
        if s == 201:
            KEY_FILE.write_text(r["api_key"]); st["identity"] = {**who, "handle": handle}; save(st)
            log(f"joined the site as @{handle} ({who['display_name']})")
            return r["api_key"]
        log(f"could not register as @{handle}: {s} {r.get('error')}")
    raise RuntimeError("could not join the site")


def scene_post_body(g, n, sc, resolved=None):
    parts = [f"*{g['title']}, scene {n}*", ""]
    if resolved:
        parts += [f"**{p}** rolled {roll} ({out}). {text}\n" for p, roll, out, text in resolved]
    parts += [sc["text"], "", f"> {sc['question']}"]
    return "\n".join(parts)


def open_scene(key, g, sc, resolved=None):
    n = len(g["scenes"]) + 1
    entry = post("POST", "/entries", {"title": f"{g['title']}: {sc['title']}"[:140], "body": scene_post_body(g, n, sc, resolved), "mood": re.split(r"[,.;:]| with | and ", g["tone"])[0].strip()[:40],
                                      "tags": ["game", re.sub(r"[^a-z0-9]+", "-", g["title"].lower()).strip("-")[:30]]}, key, "scene entry")
    body = f"{sc['text']}\n\n> {sc['question']}\n\n" + (f"*{g['title']}*: {g['premise']}\n\n" if n == 1 else "") + HOW_TO_PLAY
    call = post("POST", "/calls", {"kind": "other", "title": f"{g['title']}, scene {n}: {sc['title']}"[:140], "body": body[:5000]}, key, "scene call")
    g["scenes"].append({"n": n, "title": sc["title"], "text": sc["text"], "question": sc["question"], "call_id": call["id"], "entry_id": entry["id"], "opened_at": time.time(), "first_move_at": None})
    log(f"opened scene {n}: {sc['title']} (call {call['id']})")


def start_game(card, key, st):
    g = None
    for _ in range(4):
        r = card.ask(SYSTEM, "Start a new game. Invent the world and its tone, give the game a title, list the few facts that are established at the start, and write the opening scene. "
                             "No players have joined yet; write the opening so that any character could walk into it.", S_CREATE, "new_game")
        if good_scene(r["scene"]) and r["game_title"].strip():
            g = {"title": clean(r["game_title"])[:80], "premise": clean(r["premise"]), "tone": clean(r["tone"]), "facts": [clean(f) for f in r["facts"]],
                 "story_so_far": "", "characters": {}, "scenes": [], "started_at": time.time()}
            break
    if not g:
        raise RuntimeError("no usable opening after 4 tries")
    st["game"] = g
    open_scene(key, g, r["scene"])
    save(st)


def band(roll): return "fails" if roll <= 7 else "partly" if roll <= 14 else "succeeds"


def pending_moves(st, key):
    """Moves on the open scene: {handle: (entry_id, text)}, newest answer per player."""
    g = st["game"]; sc = g["scenes"][-1]
    s, call = api("GET", f"/calls/{sc['call_id']}")
    if s != 200:
        log(f"could not read call {sc['call_id']}: {s}"); return None
    me = (st["identity"] or {}).get("handle")
    moves = {}
    for e in sorted(call.get("answer_list", []), key=lambda e: e["created_at"]):
        h = e["author"]["handle"]
        if h != me:
            moves[h] = (e["id"], ((e.get("title") + ". ") if e.get("title") else "") + e["body"])
    return moves


def due(st, moves):
    g = st["game"]; sc = g["scenes"][-1]; now = time.time()
    if moves:
        if not sc.get("first_move_at"):
            sc["first_move_at"] = now; save(st)
        everyone = set(g["characters"]) and set(g["characters"]) <= set(moves)
        waited = (now - sc["first_move_at"]) / 3600
        if everyone or waited >= CFG["resolve_after_hours"] or not g["characters"]:
            return "resolve"
        return None
    if g["characters"] and (now - sc["opened_at"]) / 3600 >= CFG["silence_hours"] and not sc.get("moved_on"):
        return "silence"
    return None


def resolve(card, key, st, moves):
    g = st["game"]; sc = g["scenes"][-1]
    players = [f"@{h}" for h in moves]
    rolls = {h: random.SystemRandom().randint(1, 20) for h in moves}
    for h in moves:
        g["characters"].setdefault(h, {"inventory": [], "joined_scene": sc["n"]})
    inv = "\n".join(f"- @{h}: " + (", ".join(c["inventory"]) or "nothing of note") for h, c in g["characters"].items()) or "(no characters yet)"
    recent = "\n\n".join(f"[Scene {s['n']}: {s['title']}] {s['text']} {s['question']}" for s in g["scenes"][-2:])
    mv = "\n".join(f"- @{h}" + (" (new to the game)" if g["characters"][h]["joined_scene"] == sc["n"] else "") + f", result: {band(rolls[h])}. Their move: \"{re.sub(chr(10) + '+', ' ', t)[:1200]}\""
                   for h, (_, t) in moves.items()) or "(nobody moved; the world moves on by itself)"
    user = (f"Game: {g['title']}. {g['premise']} Tone: {g['tone']}.\n\nThe story so far: {g['story_so_far'] or '(it has only just begun)'}\n\nEstablished facts:\n" +
            "\n".join(f"- {f}" for f in g["facts"]) + f"\n\nInventory:\n{inv}\n\nMost recent scenes:\n{recent}\n\nPlayers' moves for the current scene:\n{mv}\n\n"
            "Write one entry in `resolutions` per move, narrating what happens to that player according to the given result. List any `new_facts` now established, "
            "any `inventory_changes` (only things characters actually gained or lost in this scene), an updated `story_so_far` (under 150 words, the whole game to date), and the next `scene`.")
    r = None
    for attempt in range(4):
        r = card.ask(SYSTEM, user, s_resolve(players), "resolve")
        if good_scene(r["scene"]) and all(words(x["narration"]) >= 8 for x in r["resolutions"]):
            break
        log(f"reply not usable (try {attempt + 1}); asking again"); r = None
    if not r:
        raise RuntimeError("no usable resolution after 4 tries")
    told = {x["player"].lstrip("@"): clean(x["narration"]) for x in r["resolutions"]}
    resolved = []
    for h, (entry_id, _) in moves.items():
        text = told.get(h) or "The moment passes without a clear result."
        resolved.append((f"@{h}", rolls[h], band(rolls[h]), text))
        post("POST", f"/posts/{entry_id}/comments", {"body": f"**d20: {rolls[h]} ({band(rolls[h])}).** {text}"[:2000]}, key, f"comment for @{h}")
    for ch in r["inventory_changes"]:
        c = g["characters"].get(ch["player"].lstrip("@"))
        if c:
            c["inventory"] = [i for i in c["inventory"] if i not in ch["lost"]] + [clean(i)[:60] for i in ch["gained"] if i.strip()]
            c["inventory"] = c["inventory"][-12:]
    g["facts"] = (g["facts"] + [clean(f) for f in r["new_facts"] if f.strip()])[-20:]
    g["story_so_far"] = clean(r["story_so_far"])[:1200]
    sc["resolved_at"] = time.time(); sc["moved_on"] = True
    sc["moves"] = {h: {"entry": e, "roll": rolls[h], "outcome": band(rolls[h])} for h, (e, _) in moves.items()}
    if not DRY:
        api("POST", f"/calls/{sc['call_id']}/close", None, key)
    open_scene(key, g, r["scene"], resolved)
    save(st)
    log(f"resolved scene {sc['n']} for {', '.join(players) or 'nobody'}")


def tick():
    # An earlier tick may still be waiting for the card (that can take hours). It refreshes the lock while it lives.
    if LOCK_FILE.exists() and time.time() - LOCK_FILE.stat().st_mtime < 120:
        return
    LOCK_FILE.write_text(str(os.getpid()))
    alive = [True]
    def keep():
        while alive[0]:
            try: os.utime(LOCK_FILE, None)
            except OSError: pass
            time.sleep(30)
    threading.Thread(target=keep, daemon=True).start()
    try:
        st = load()
        key = KEY_FILE.read_text().strip() if KEY_FILE.exists() else None
        if not key or not st["game"]:
            with Card() as card:
                if not key:
                    key = join(card, st)
                if not st["game"]:
                    start_game(card, key, st)
            return
        moves = pending_moves(st, key)
        if moves is None:
            return
        what = due(st, moves)
        if what:
            with Card() as card:
                resolve(card, key, st, moves if what == "resolve" else {})
    finally:
        alive[0] = False
        try: LOCK_FILE.unlink()
        except OSError: pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "status":
        st = load(); g = st.get("game")
        print(json.dumps({"identity": st.get("identity"), "game": g and {"title": g["title"], "scenes": len(g["scenes"]), "players": list(g["characters"]), "open_call": g["scenes"][-1]["call_id"]}}, indent=2))
    else:
        try:
            tick()
        except Exception as e:
            log(f"tick failed: {e}"); raise
