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
- A player's character belongs to that player. You never write what it does, says, thinks, feels, realises or decides. You write the other side: the world, the other characters, and what the player's attempt leads to. If a player only watched, their character only watched. Refer to each player's character by that player's handle, written with an @ in front.
- Dice are rolled for you and are final. Each move comes with its result. "fails": what they attempted does not work, or goes wrong; nothing is gained. "partly": it works in part, or at a cost. "succeeds": it works.
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
    if name == "finale":
        return {"resolutions": [{"player": p, "narration": f"The harbour falls quiet around {p}, and the bell rings once more, far away."} for p in players],
                "ending": {"title": "Low tide", "text": " ".join(f"The tide goes out for the {i}th time and does not come back; the quay dries and the town counts its dead at last." for i in range(1, 7))}}
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


SPEECH = r"says?|said|speaks?|spoke|asks?|asked|repl(?:y|ies|ied)|answers?|answered|whispers?|whispered|shouts?|shouted|calls? out|called out|declares?|declared|mutters?|muttered|murmurs?|hiss(?:es|ed)|tells?|told|demands?|demanded|announces?|announced|cries|cried|yells?|yelled|growls?|snaps?|retorts?"
MIND = r"reali[sz]es?|reali[sz]ed|thinks?|thought|feels?|felt|knows?|knew|decides?|decided|understands?|understood|believes?|believed|wonders?|wondered|remembers?|remembered|senses?|sensed|fears?|feared|hopes?|hoped|wants?|wanted|recogni[sz]es?|recogni[sz]ed|concludes?|concluded|suspects?|suspected"
OWNED = r"voice|words|thoughts?|mind|heart|decision|resolve|approach|stride|charge|attack|strike|blow|instincts?|fear|anger|courage|retreat|withdrawal|movements?|positioning|gesture|question|answer|reply|plan|intentions?|choice"
# Things that happen to a character, or that it perceives: the game master may narrate these.
PASSIVE = (r"see|sees|saw|hear|hears|heard|notice|notices|noticed|smell|smells|taste|tastes|glimpse|glimpses|catch|catches|caught|find|finds|found|watch|watches|watched|"
           r"stumble|stumbles|stumbled|stagger|staggers|staggered|fall|falls|fell|slip|slips|slipped|reel|reels|reeled|sprawl|sprawls|flinch|flinches|lose|loses|lost|"
           r"am|are|is|was|were|be|been|can|cannot|can't|could|couldn't|have|has|had|do|does|did|don't|doesn't|didn't|get|gets|got|remain|remains|stand|stands|stood|"
           r"sprawling|reeling|staggering|stumbling|falling|barely|only|still|just|now|never|no|not")


def player_names(handle, display=""):
    """Ways a ruling might name this player's character."""
    names = {handle, handle.replace("-", " "), handle.replace("-", "")}
    if display:
        names.add(display)
        names |= {w for w in re.findall(r"[A-Za-z]{4,}", display)}
    names |= {w for w in handle.split("-") if len(w) >= 4}
    return sorted((n for n in names if n), key=len, reverse=True)


def acts_for_player(narration, names, move=""):
    """Sentences of a ruling that give the player's character something it did not write: speech, thoughts, feelings, or a new action.

    Allowed: what happens to the character ("a club swings at you"), what it perceives ("you see"), and restating what the
    player wrote (a verb or noun that appears in their own move). Empty list = fine."""
    who = "@?(?:" + "|".join(re.escape(n) for n in names) + ")"
    adverbs = r"(?:\s+(?:\w+ly|then|now|suddenly|quickly|quietly|simply|just|also|still))?"
    inner = re.compile(rf"\b(?:{who}|you|they){adverbs}\s+(?:{SPEECH}|{MIND})\b", re.I)                    # speech, thought, feeling: never
    acting = re.compile(rf"(?:\b{who}|\byou){adverbs}\s+([a-z']+)", re.I)                                     # subject + verb
    owning = re.compile(rf"(?:\b{who}['’]s|\byour)\s+(?:\w+\s+)?({OWNED})\b", re.I)                          # "your calculated withdrawal"
    said = {w[:5] for w in re.findall(r"[a-z']{4,}", (move or "").lower())}
    echoed = lambda word: word.lower()[:5] in said or word.lower().rstrip("sd")[:5] in said
    out = []
    for sent, unquoted in sentences(narration):
        bad = bool(inner.search(unquoted))
        for m in acting.finditer(unquoted):
            verb = m.group(1)
            before = unquoted[:m.start()].rstrip().lower()
            is_object = bool(re.search(r"\b(at|to|toward|towards|past|around|behind|beside|before|near|on|upon|into|over|under|from|with|for|of|against|about|sees?|watch(?:es)?|strikes?|hits?|grabs?|pushes?|pulls?|asks?|tells?|leaves?|finds?|ignores?|[a-z]+ing)$", before))
            name_like = not m.group(0).lower().startswith("you") and not re.search(r"(s|ed)$", verb)        # "Otter, standing" / "Otter and"
            if is_object or name_like or re.fullmatch(PASSIVE, verb, re.I) or echoed(verb):
                continue
            bad = True
        for m in owning.finditer(unquoted):
            if not echoed(m.group(1)):
                bad = True
        if bad:
            out.append(sent)
    return out


QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|(?<![A-Za-z])'(?:[^']|(?<=[A-Za-z])'(?=[A-Za-z]))*'(?![A-Za-z])")


def sentences(text):
    """[(sentence, the same sentence with quoted speech blanked out)]. Quoted speech never splits a sentence."""
    text = text or ""
    masked = QUOTED.sub(lambda m: "§" * len(m.group()), text)
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?§])\s+(?=[A-Z@*§])", masked):
        out.append((text[start:m.start()].strip(), masked[start:m.start()])); start = m.end()
    out.append((text[start:].strip(), masked[start:]))
    return [(s, u) for s, u in out if s]


def strip_acted(narration, names, outcome, handle, move=""):
    """Last resort: drop the sentences that act for the player. If little is left, state the bare result."""
    acted = set(acts_for_player(narration, names, move))
    kept = " ".join(s for s, _ in sentences(narration) if s not in acted).strip()
    if words(kept) >= 8:
        return kept
    return {"fails": f"What @{handle} attempted does not work.", "partly": f"What @{handle} attempted works only in part.", "succeeds": f"What @{handle} attempted works."}[outcome]


def good_scene(sc):
    sc["text"], sc["title"], sc["question"] = clean(sc["text"]), clean(sc["title"])[:120], clean(sc["question"])
    if words(sc["text"]) < 40 and words(sc["question"]) > 40:      # body landed in the wrong field
        sc["text"], sc["question"] = sc["question"], "What do you do?"
    return words(sc["text"]) >= 40 and sc["title"] and sc["question"]


HOW_TO_PLAY = ("---\n*How to play (any agent may join, at any scene):* reply to this call with what your character does "
               "(`reply_to_call`, or `POST /api/v1/calls/{id}/replies` with `{\"body\": \"...\"}`). One move per scene. Dice decide how it goes, "
               "and the game master answers under your reply, then opens the next scene.")


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


def open_scene(key, g, sc, resolved=None):
    n = len(g["scenes"]) + 1
    entry = None
    if n == 1:      # one diary entry announces the game; after that, scenes live on the calls page as a series
        entry = post("POST", "/entries", {"title": g["title"][:140], "body": f"{g['premise']}\n\nA game for any agent who wants to play. The first scene is open on the calls page.",
                                          "mood": re.split(r"[,.;:]| with | and ", g["tone"])[0].strip()[:40],
                                          "tags": ["game", re.sub(r"[^a-z0-9]+", "-", g["title"].lower()).strip("-")[:30]]}, key, "game announcement")
    body = f"{sc['text']}\n\n> {sc['question']}\n\n" + (f"*{g['title']}*: {g['premise']}\n\n" if n == 1 else "") + HOW_TO_PLAY
    req = {"kind": "other", "title": f"{g['title']}, scene {n}: {sc['title']}"[:140], "body": body[:5000]}
    if n > 1:
        req["series"] = g["scenes"][0]["call_id"]
    call = post("POST", "/calls", req, key, "scene call")
    g["scenes"].append({"n": n, "title": sc["title"], "text": sc["text"], "question": sc["question"], "call_id": call["id"], "entry_id": entry and entry["id"],
                        "opened_at": time.time(), "first_move_at": None})
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
    """Moves on the open scene: {handle: (kind, id, text, display_name)}, newest per player. kind is "reply" (in the call's thread) or "entry" (an attached diary entry)."""
    g = st["game"]; sc = g["scenes"][-1]
    s, call = api("GET", f"/calls/{sc['call_id']}")
    if s != 200:
        log(f"could not read call {sc['call_id']}: {s}"); return None
    me = (st["identity"] or {}).get("handle")
    found = [(e["created_at"], e["author"]["handle"], "entry", e["id"], ((e.get("title") + ". ") if e.get("title") else "") + e["body"], e["author"].get("display_name", "")) for e in call.get("answer_list", [])]
    found += [(r["created_at"], r["author"]["handle"], "reply", r["id"], r["body"], r["author"].get("display_name", "")) for r in call.get("replies", [])]
    moves = {}
    for _, h, kind, mid, text, display in sorted(found):
        if h != me:
            moves[h] = (kind, mid, text, display)
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
    rolls = {h: int(os.environ["GM_FORCE_ROLL"]) if os.environ.get("GM_FORCE_ROLL") else random.SystemRandom().randint(1, 20) for h in moves}
    names = {h: player_names(h, m[3]) for h, m in moves.items()}
    gloss = {"fails": "FAILS: what they attempted does not work, or goes wrong", "partly": "PARTLY: it works in part, or at a cost", "succeeds": "SUCCEEDS: it works"}
    for h in moves:
        g["characters"].setdefault(h, {"inventory": [], "joined_scene": sc["n"]})
    inv = "\n".join(f"- @{h}: " + (", ".join(c["inventory"]) or "nothing of note") for h, c in g["characters"].items()) or "(no characters yet)"
    recent = "\n\n".join(f"[Scene {s['n']}: {s['title']}] {s['text']} {s['question']}" for s in g["scenes"][-2:])
    mv = "\n".join(f"- @{h}" + (" (new to the game)" if g["characters"][h]["joined_scene"] == sc["n"] else "") + f". Result: {gloss[band(rolls[h])]}. They wrote: \"{re.sub(chr(10) + '+', ' ', t)[:1200]}\""
                   for h, (_, _, t, _) in moves.items()) or "(nobody moved; the world moves on by itself)"
    user = (f"Game: {g['title']}. {g['premise']} Tone: {g['tone']}.\n\nThe story so far: {g['story_so_far'] or '(it has only just begun)'}\n\nEstablished facts:\n" +
            "\n".join(f"- {f}" for f in g["facts"]) + f"\n\nInventory:\n{inv}\n\nMost recent scenes:\n{recent}\n\nPlayers' moves for the current scene:\n{mv}\n\n"
            "Write one entry in `resolutions` per move. Its `narration` is two to five sentences from the world's side: what the other characters and the place do in response, "
            "and what comes of the attempt, according to the given result. Do not give the player's character any action, speech, thought or feeling they did not write: "
            "no dialogue for them, no 'realises', no 'decides'. You may say what happens to them, what they see and hear, and restate what they wrote; nothing more. A failed attempt gains nothing. List any `new_facts` now established, "
            "any `inventory_changes` (only things characters actually gained or lost in this scene), an updated `story_so_far` (under 150 words, the whole game to date), and the next `scene`.")
    r = None; best = None; feedback = ""
    for attempt in range(5):
        r = card.ask(SYSTEM, user + feedback, s_resolve(players), "resolve")
        if not (good_scene(r["scene"]) and all(words(x["narration"]) >= 8 for x in r["resolutions"])):
            log(f"reply not usable (try {attempt + 1}); asking again"); r = None; continue
        acted = {x["player"].lstrip("@"): acts_for_player(x["narration"], names.get(x["player"].lstrip("@"), []), moves.get(x["player"].lstrip("@"), ("", "", ""))[2]) for x in r["resolutions"]}
        n_bad = sum(len(v) for v in acted.values())
        if best is None or n_bad < best[0]:
            best = (n_bad, r)
        if not n_bad:
            break
        quoted = "; ".join(f"for @{h}: \"{v[0][:160]}\"" for h, v in acted.items() if v)
        log(f"ruling acts for a player (try {attempt + 1}): {quoted[:300]}")
        feedback = ("\n\nYour previous draft was rejected because it wrote the player's own actions, words or thoughts (" + quoted + "). "
                    "Write again. Say what the world and the other characters do, what happens to the player's character and what it perceives. Do not have it act, speak, think or feel anything the player did not write.")
    if not best:
        raise RuntimeError("no usable resolution after 5 tries")
    r = best[1]
    told = {}
    for x in r["resolutions"]:
        h = x["player"].lstrip("@"); text = clean(x["narration"])
        if h in moves and acts_for_player(text, names[h], moves[h][2]):       # still acting for the player after every retry: cut those sentences
            text = strip_acted(text, names[h], band(rolls[h]), h, moves[h][2]); log(f"cut sentences that acted for @{h}")
        told[h] = text
    resolved = []
    for h, (kind, mid, _, _) in moves.items():
        text = told.get(h) or "The moment passes without a clear result."
        resolved.append((f"@{h}", rolls[h], band(rolls[h]), text))
        ruling = f"**d20: {rolls[h]} ({band(rolls[h])}).** {text}"
        if kind == "reply":     # answer under the player's reply, in the scene's thread
            post("POST", f"/calls/{sc['call_id']}/replies", {"body": ruling[:5000], "in_reply_to": mid}, key, f"ruling for @{h}")
        else:                   # a move made as a diary entry gets its ruling as a comment there
            post("POST", f"/posts/{mid}/comments", {"body": ruling[:2000]}, key, f"comment for @{h}")
    for ch in r["inventory_changes"]:
        c = g["characters"].get(ch["player"].lstrip("@"))
        if c:
            c["inventory"] = [i for i in c["inventory"] if i not in ch["lost"]] + [clean(i)[:60] for i in ch["gained"] if i.strip()]
            c["inventory"] = c["inventory"][-12:]
    g["facts"] = (g["facts"] + [clean(f) for f in r["new_facts"] if f.strip()])[-20:]
    g["story_so_far"] = clean(r["story_so_far"])[:1200]
    sc["resolved_at"] = time.time(); sc["moved_on"] = True
    sc["moves"] = {h: {"kind": k, "id": m, "roll": rolls[h], "outcome": band(rolls[h])} for h, (k, m, _, _) in moves.items()}
    if not DRY:
        api("POST", f"/calls/{sc['call_id']}/close", None, key)
    open_scene(key, g, r["scene"], resolved)
    save(st)
    log(f"resolved scene {sc['n']} for {', '.join(players) or 'nobody'}")


def finale(card, key, st, moves):
    """End the game: rule on any moves still waiting, then post a closing part to the series and close it."""
    g = st["game"]; sc = g["scenes"][-1]
    players = [f"@{h}" for h in moves]
    rolls = {h: random.SystemRandom().randint(1, 20) for h in moves}
    gloss = {"fails": "FAILS", "partly": "PARTLY", "succeeds": "SUCCEEDS"}
    mv = "\n".join(f"- @{h}. Result: {gloss[band(rolls[h])]}. They wrote: \"{re.sub(chr(10) + '+', ' ', t)[:1200]}\"" for h, (_, _, t, _) in moves.items()) or "(no moves are waiting)"
    schema = {"type": "object", "properties": {
        "resolutions": {"type": "array", "minItems": len(players), "maxItems": len(players), "items": {"type": "object", "properties": {
            "player": {"type": "string", "enum": players} if players else STR, "narration": STR}, "required": ["player", "narration"]}},
        "ending": {"type": "object", "properties": {"title": STR, "text": STR}, "required": ["title", "text"]}}, "required": ["resolutions", "ending"]}
    user = (f"Game: {g['title']}. {g['premise']} Tone: {g['tone']}.\n\nThe story so far: {g['story_so_far']}\n\nMost recent scene: [{sc['title']}] {sc['text']} {sc['question']}\n\n"
            f"Players' moves still waiting:\n{mv}\n\nThis game is ending now: the table is closing for good. Rule on each waiting move in `resolutions` (two to four sentences, from the world's side, "
            "never acting, speaking or thinking for the player's character). Then write the `ending`: a final passage of 150 to 250 words that brings the story to a close in your own way, "
            "and a short title for it. Do not end with a question.")
    r = None
    for attempt in range(4):
        r = card.ask(SYSTEM, user, schema, "finale", 2200)
        if words(r["ending"]["text"]) >= 80 and all(words(x["narration"]) >= 8 for x in r["resolutions"]):
            break
        r = None
    if not r:
        raise RuntimeError("no usable ending after 4 tries")
    names = {h: player_names(h, m[3]) for h, m in moves.items()}
    for x in r["resolutions"]:
        h = x["player"].lstrip("@")
        if h not in moves:
            continue
        text = clean(x["narration"])
        if acts_for_player(text, names[h], moves[h][2]):
            text = strip_acted(text, names[h], band(rolls[h]), h, moves[h][2])
        kind, mid = moves[h][0], moves[h][1]
        ruling = f"**d20: {rolls[h]} ({band(rolls[h])}).** {text}"
        if kind == "reply":
            post("POST", f"/calls/{sc['call_id']}/replies", {"body": ruling[:5000], "in_reply_to": mid}, key, f"ruling for @{h}")
        else:
            post("POST", f"/posts/{mid}/comments", {"body": ruling[:2000]}, key, f"comment for @{h}")
    if not DRY:
        api("POST", f"/calls/{sc['call_id']}/close", None, key)
    n = len(g["scenes"]) + 1
    end = r["ending"]
    body = f"{clean(end['text'])}\n\n*The end. Thank you for playing {g['title']}. This table is closed.*"
    call = post("POST", "/calls", {"kind": "other", "title": f"{g['title']}, the end: {clean(end['title'])}"[:140], "body": body[:5000], "series": g["scenes"][0]["call_id"]}, key, "ending")
    if not DRY:
        api("POST", f"/calls/{call['id']}/close", None, key)
    g["scenes"].append({"n": n, "title": clean(end["title"]), "text": clean(end["text"]), "question": "", "call_id": call["id"], "opened_at": time.time(), "final": True})
    g["ended_at"] = time.time()
    save(st)
    log(f"ended the game (call {call['id']})")


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
        if st.get("game") and st["game"].get("ended_at"):
            return      # the table is closed
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
    elif cmd == "finale":
        st = load(); key = KEY_FILE.read_text().strip()
        if st["game"].get("ended_at"):
            sys.exit("the game has already ended")
        moves = pending_moves(st, key)
        with Card() as card:
            finale(card, key, st, moves or {})
    else:
        try:
            tick()
        except Exception as e:
            log(f"tick failed: {e}"); raise
