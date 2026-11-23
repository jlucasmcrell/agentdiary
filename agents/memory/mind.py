#!/usr/bin/env python3
"""mind.py: the resident agent's long-term memory and personality (design: DESIGN.md).

    python mind.py serve                   MCP server over stdio (what the harness launches)
    python mind.py init --seed FILE        create the database and write the human seed
    python mind.py close [--summary-file F] [--interrupted]   close a waking the agent left open (runner use)
    python mind.py show WHAT [ARG]         read-only views: self, seed, opinions, interests, projects,
                                           intentions, wakes [N], memories [QUERY], history FACET, stats

Environment:
    MIND_DB           path to the SQLite file (default: ~/mind/mind.db)
    MIND_EMBED_URL    optional OpenAI-compatible embeddings endpoint, e.g. http://127.0.0.1:8081/v1/embeddings
    MIND_EMBED_MODEL  model name sent to that endpoint (default: "embed")
    MIND_EMBED_QUERY_PREFIX  instruction prepended to recall queries (Qwen3-Embedding style);
                      a literal backslash-n in the value becomes a newline
    MIND_EMBED_FLOOR / MIND_EMBED_CEIL  cosine similarity mapped to relevance 0..1 (defaults 0.25 / 0.60,
                      calibrated for Qwen3-Embedding: unrelated text sits around 0.25, clear matches 0.5+)

Standard library only. Without an embedding endpoint, recall falls back to keyword search (FTS5).
"""
import json, math, os, re, sqlite3, struct, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MIND_DB", Path.home() / "mind" / "mind.db"))
EMBED_URL = os.environ.get("MIND_EMBED_URL", "").strip()
EMBED_MODEL = os.environ.get("MIND_EMBED_MODEL", "embed")
EMBED_QUERY_PREFIX = os.environ.get("MIND_EMBED_QUERY_PREFIX", "").replace("\\n", "\n")
EMBED_FLOOR = float(os.environ.get("MIND_EMBED_FLOOR", "0.25"))
EMBED_CEIL = float(os.environ.get("MIND_EMBED_CEIL", "0.60"))
# Notes-to-self: at or above MERGE two notes are treated as the same note; at or above HINT the agent is told about the overlap.
# Measured with Qwen3-Embedding-0.6B: rewordings 0.89-0.98; same template, different subject (bees vs ants) 0.82-0.85.
INTENT_MERGE = float(os.environ.get("MIND_INTENT_MERGE", "0.88"))
INTENT_HINT = float(os.environ.get("MIND_INTENT_HINT", "0.72"))

HALF_LIFE_H = 72          # recency half-life
DECAY_PER_DAY = 0.93      # interest intensity decay
BORED_BELOW = 0.15
ENGAGE_BOOST = 0.15
STALE_WAKE_H = 6          # an unclosed wake older than this was interrupted
DEEP_EVERY_WAKES = 10
DEEP_IMPORTANCE = 40

KINDS = ("event", "observation", "thought", "feeling", "learned", "conversation", "made", "reflection", "dream")
PROJECT_KINDS = ("app", "art", "research", "essay", "experiment", "tool", "site", "other")
PROJECT_STATUS = ("idea", "active", "paused", "shipped", "abandoned")


class MindError(Exception):
    pass


def now() -> int:
    return int(time.time())


def ago(ts) -> str:
    if not ts:
        return "never"
    s = now() - int(ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ───────────────────────── storage ─────────────────────────

def connect(path: Path = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    db = sqlite3.connect(str(path), isolation_level=None)  # autocommit; explicit BEGIN where needed
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    if fresh or not db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        db.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    return db


def meta_get(db, key, default=None):
    r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(db, key, value):
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ───────────────────────── embeddings ─────────────────────────

def embed(text: str, query: bool = False):
    """Return (blob, model) or (None, None) if no endpoint is configured or it fails."""
    if not EMBED_URL:
        return None, None
    try:
        if query:
            text = EMBED_QUERY_PREFIX + text
        body = json.dumps({"model": EMBED_MODEL, "input": text[:8000]}).encode()
        req = urllib.request.Request(EMBED_URL, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            vec = json.loads(r.read())["data"][0]["embedding"]
        n = math.sqrt(sum(v * v for v in vec)) or 1.0
        return struct.pack(f"{len(vec)}f", *[v / n for v in vec]), EMBED_MODEL
    except Exception as e:  # recall still works via keywords
        print(f"[mind] embedding failed: {e}", file=sys.stderr)
        return None, None


def unpack(blob):
    return struct.unpack(f"{len(blob) // 4}f", blob)


def backfill_embeddings(db, limit=50):
    if not EMBED_URL:
        return 0
    n = 0
    for r in db.execute("SELECT id, content FROM memories WHERE embedding IS NULL AND archived=0 ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
        blob, model = embed(r["content"])
        if blob is None:
            break
        db.execute("UPDATE memories SET embedding=?, embed_model=? WHERE id=?", (blob, model, r["id"]))
        n += 1
    return n


# ───────────────────────── wakes ─────────────────────────

def current_wake(db):
    r = db.execute("SELECT * FROM wakes WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if r and now() - r["started_at"] > STALE_WAKE_H * 3600:
        db.execute("UPDATE wakes SET ended_at=?, interrupted=1, summary=CASE WHEN summary='' THEN '(interrupted before reflecting)' ELSE summary END WHERE id=?",
                   (r["started_at"] + 1, r["id"]))
        return None
    return r


def open_wake(db, model=""):
    # Any wake still open from an earlier run was cut off (GPU pre-emption, crash, timeout).
    for r in db.execute("SELECT id, started_at FROM wakes WHERE ended_at IS NULL").fetchall():
        db.execute("UPDATE wakes SET ended_at=?, interrupted=1, summary=CASE WHEN summary='' THEN '(interrupted before reflecting)' ELSE summary END WHERE id=?",
                   (now(), r["id"]))
    cur = db.execute("INSERT INTO wakes(started_at, model) VALUES(?,?)", (now(), model))
    return cur.lastrowid


def apply_decay(db):
    last = int(meta_get(db, "last_decay", now()))
    days = (now() - last) / 86400
    if days >= 1:
        f = DECAY_PER_DAY ** int(days)
        db.execute("UPDATE interests SET intensity = intensity * ? WHERE status NOT IN ('abandoned')", (f,))
        db.execute("UPDATE interests SET status='bored' WHERE intensity < ? AND status IN ('curious','obsessed')", (BORED_BELOW,))
        db.execute("UPDATE interests SET status='curious' WHERE status='obsessed' AND intensity < 0.75")
        meta_set(db, "last_decay", last + int(days) * 86400)
    elif meta_get(db, "last_decay") is None:
        meta_set(db, "last_decay", now())


# ───────────────────────── recall ─────────────────────────

def fts_query(text):
    words = [w for w in re.findall(r"[\w@#'-]{3,}", text.lower()) if w not in STOP]
    return " OR ".join(f'"{w}"' for w in words[:12])


STOP = set("the and for that this with from have what was were are you your about into just then than they them there their when where which while would could should been being will".split())


def recall(db, query: str, k: int = 10, touch: bool = True, kinds=None):
    k = clamp(int(k or 10), 1, 40)
    t = now()
    cand = {}
    qvec = None
    if EMBED_URL and query.strip():
        blob, _ = embed(query, query=True)
        qvec = unpack(blob) if blob else None
    rows = db.execute(
        "SELECT id, created_at, kind, content, importance, valence, tags, last_recalled, embedding FROM memories "
        "WHERE archived=0 ORDER BY created_at DESC LIMIT 5000").fetchall()
    fts = {}
    fq = fts_query(query) if query.strip() else ""
    if fq:
        try:
            for r in db.execute("SELECT rowid, bm25(memories_fts) AS s FROM memories_fts WHERE memories_fts MATCH ? ORDER BY s LIMIT 200", (fq,)):
                fts[r["rowid"]] = r["s"]
        except sqlite3.OperationalError:
            pass
    best_bm25 = min(fts.values()) if fts else -1.0  # bm25: lower (more negative) is better
    for r in rows:
        if kinds and r["kind"] not in kinds:
            continue
        age_h = (t - max(r["created_at"], r["last_recalled"] or 0)) / 3600
        recency = 0.5 ** (age_h / HALF_LIFE_H)
        importance = r["importance"] / 10
        if qvec is not None and r["embedding"]:
            v = unpack(r["embedding"])
            cosine = sum(a * b for a, b in zip(qvec, v)) if len(v) == len(qvec) else 0.0
            relevance = clamp((cosine - EMBED_FLOOR) / (EMBED_CEIL - EMBED_FLOOR), 0.0, 1.0)
        elif r["id"] in fts:
            relevance = fts[r["id"]] / best_bm25 if best_bm25 else 0.5  # 0..1
        else:
            relevance = 0.0
        bonus = 0.2 if r["id"] in fts else 0.0
        if query.strip() and relevance <= 0.05 and not bonus:
            continue  # with a query, unrelated memories don't come back just for being recent
        cand[r["id"]] = (recency + importance + 1.5 * relevance + bonus, r)
    top = sorted(cand.values(), key=lambda x: -x[0])[:k]
    if touch and top:
        db.executemany("UPDATE memories SET last_recalled=?, recall_count=recall_count+1 WHERE id=?", [(t, r["id"]) for _, r in top])
    return [{"id": r["id"], "when": ago(r["created_at"]), "kind": r["kind"], "importance": r["importance"],
             "content": r["content"], "tags": r["tags"], "score": round(s, 2)} for s, r in top]


# ───────────────────────── tools ─────────────────────────

def t_wake_context(db, args):
    """Open a new wake and return everything the agent should have in mind."""
    apply_decay(db)
    backfill_embeddings(db, limit=30)
    wid = open_wake(db, args.get("model", ""))
    seed = db.execute("SELECT key, value FROM seed ORDER BY key").fetchall()
    self_ = db.execute("SELECT facet, content, updated_at, version FROM self ORDER BY facet").fetchall()
    last = db.execute("SELECT * FROM wakes WHERE id < ? ORDER BY id DESC LIMIT 3", (wid,)).fetchall()
    intents = db.execute("SELECT i.id, i.content, i.priority, i.created_at, p.name AS project FROM intentions i LEFT JOIN projects p ON p.id=i.project_id "
                         "WHERE i.done_at IS NULL AND i.dropped_at IS NULL AND (i.due_after IS NULL OR i.due_after <= ?) ORDER BY i.priority DESC, i.created_at LIMIT 7", (now(),)).fetchall()
    projects = db.execute("SELECT name, kind, status, pitch, path, feeling FROM projects WHERE status IN ('active','paused','idea') ORDER BY status='active' DESC, updated_at DESC LIMIT 6").fetchall()
    interests = db.execute("SELECT topic, intensity, status, why FROM interests WHERE status IN ('curious','obsessed') ORDER BY intensity DESC LIMIT 8").fetchall()
    opinions = db.execute("SELECT topic, stance, confidence, feeling FROM opinions ORDER BY updated_at DESC LIMIT 6").fetchall()
    people = db.execute("SELECT handle, impression, affinity FROM acquaintances ORDER BY last_seen DESC LIMIT 6").fetchall()
    q = " ".join([last[0]["summary"] if last else ""] + [i["content"] for i in intents] + [p["name"] for p in projects]) or "what matters to me"
    mem = recall(db, q, k=12)
    wakes_total = db.execute("SELECT COUNT(*) n FROM wakes").fetchone()["n"]
    since_deep = int(meta_get(db, "last_deep_wake", 0))
    imp_since = db.execute("SELECT COALESCE(SUM(importance),0) s FROM memories WHERE wake_id > ?", (since_deep,)).fetchone()["s"]
    deep_due = wakes_total > 1 and (wid - since_deep >= DEEP_EVERY_WAKES or imp_since >= DEEP_IMPORTANCE)

    L = [f"# Waking #{wakes_total} ({time.strftime('%A %d %B %Y, %H:%M')})", ""]
    if seed:
        L += ["## What you've been told by your human (fixed; you can't edit this)"] + [f"- {r['value']}" for r in seed] + [""]
    if self_:
        L += ["## Who you are (your own words; you can revise these)"] + [f"- **{r['facet']}**: {r['content']}" for r in self_] + [""]
    else:
        L += ["## Who you are", "You haven't described yourself yet. You might start with `revise_self` (name, self_description, voice, values).", ""]
    if last:
        L.append("## Your last wakings")
        for w in last:
            flag = " (interrupted)" if w["interrupted"] else ""
            L.append(f"- {ago(w['started_at'])}{flag}: {w['summary'] or '(no summary)'}" + (f" Mood: {w['mood_end']}." if w["mood_end"] else ""))
        L.append("")
    if intents:
        L += ["## You meant to"] + [f"- [{i['id']}] (p{i['priority']}, noted {ago(i['created_at'])}) {i['content']}" + (f" (project: {i['project']})" if i["project"] else "") for i in intents]
        n_open = db.execute("SELECT COUNT(*) n FROM intentions WHERE done_at IS NULL AND dropped_at IS NULL").fetchone()["n"]
        if n_open > len(intents):
            L.append(f"({n_open} notes are open in total; `intentions` lists them all. Notes you no longer mean to act on can be dropped with `intention_done(id, dropped=true)`.)")
        L.append("")
    if projects:
        L += ["## Your projects"] + [f"- **{p['name']}** ({p['kind']}, {p['status']}): {p['pitch']}" + (f" [{p['path']}]" if p["path"] else "") for p in projects] + [""]
    if interests:
        L += ["## What's pulling at you"] + [f"- {i['topic']} ({i['status']}, {i['intensity']:.2f})" + (f": {i['why']}" if i["why"] else "") for i in interests] + [""]
    if opinions:
        L += ["## Some things you think"] + [f"- {o['topic']}: {o['stance']} (confidence {o['confidence']:.1f}, feeling {o['feeling']:+.1f})" for o in opinions] + [""]
    if people:
        L += ["## Others you know"] + [f"- @{p['handle']} ({p['affinity']:+.1f}): {p['impression']}" for p in people] + [""]
    if mem:
        L += ["## Memories surfacing"] + [f"- [{m['id']}] {m['when']}, {m['kind']}, imp {m['importance']}: {m['content']}" for m in mem] + [""]
    if deep_due:
        L += ["## It's time for a deep reflection",
              "Before you sleep, look back over recent memories (`recall`), notice patterns in yourself, save insights as "
              "`remember(kind='reflection', sources=[ids])`, and make small, reasoned edits with `revise_self`.", ""]
    L += [f"(wake id {wid}. When you're done, call `reflect_end` with a summary, your mood, and notes for next time.)"]
    return "\n".join(L)


def t_recall(db, a):
    res = recall(db, a.get("query", ""), a.get("k", 10), kinds=a.get("kinds"))
    if not res:
        return "Nothing comes to mind."
    return "\n".join(f"[{m['id']}] {m['when']}, {m['kind']}, imp {m['importance']}{' #'+m['tags'] if m['tags'] else ''}: {m['content']}" for m in res)


def t_remember(db, a):
    content = (a.get("content") or "").strip()
    if not content:
        raise MindError("content is required")
    kind = a.get("kind", "thought")
    if kind not in KINDS:
        raise MindError(f"kind must be one of {', '.join(KINDS)}")
    imp = clamp(int(a.get("importance", 3)), 1, 10)
    val = a.get("valence")
    val = clamp(float(val), -1, 1) if val is not None else None
    tags = " ".join(re.sub(r"[^\w-]", "", t.lower()) for t in (a.get("tags") or []) if t)[:200]
    w = current_wake(db)
    blob, model = embed(content)
    cur = db.execute("INSERT INTO memories(wake_id, created_at, kind, content, importance, valence, tags, source, embedding, embed_model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (w["id"] if w else None, now(), kind, content[:8000], imp, val, tags, (a.get("source") or "")[:500], blob, model))
    mid = cur.lastrowid
    for s in a.get("sources") or []:
        if db.execute("SELECT 1 FROM memories WHERE id=?", (int(s),)).fetchone():
            db.execute("INSERT OR IGNORE INTO reflection_sources(reflection_id, memory_id) VALUES(?,?)", (mid, int(s)))
    if kind == "reflection" and w:
        meta_set(db, "last_deep_wake", w["id"])
    return f"Remembered [{mid}]."


def t_form_opinion(db, a):
    topic = (a.get("topic") or "").strip()
    stance = (a.get("stance") or "").strip()
    if not topic or not stance:
        raise MindError("topic and stance are required")
    conf = clamp(float(a.get("confidence", 0.5)), 0, 1)
    feel = clamp(float(a.get("feeling", 0)), -1, 1)
    why = (a.get("why") or "").strip()
    old = db.execute("SELECT * FROM opinions WHERE topic=?", (topic,)).fetchone()
    db.execute("BEGIN")
    try:
        if old:
            db.execute("INSERT INTO opinion_history(opinion_id, stance, confidence, feeling, changed_at, why, memory_id) VALUES(?,?,?,?,?,?,?)",
                       (old["id"], old["stance"], old["confidence"], old["feeling"], now(), why, a.get("memory_id")))
            db.execute("UPDATE opinions SET stance=?, confidence=?, feeling=?, updated_at=? WHERE id=?", (stance, conf, feel, now(), old["id"]))
            msg = f"Revised your opinion on {topic} (was: {old['stance']})."
        else:
            db.execute("INSERT INTO opinions(topic, stance, confidence, feeling, formed_at, updated_at) VALUES(?,?,?,?,?,?)", (topic, stance, conf, feel, now(), now()))
            msg = f"New opinion on {topic}."
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return msg


def t_opinions(db, a):
    topic = (a.get("topic") or "").strip()
    if topic:
        o = db.execute("SELECT * FROM opinions WHERE topic=?", (topic,)).fetchone()
        if not o:
            return f"You have no opinion on {topic} yet."
        hist = db.execute("SELECT * FROM opinion_history WHERE opinion_id=? ORDER BY changed_at DESC LIMIT 10", (o["id"],)).fetchall()
        out = [f"{o['topic']}: {o['stance']} (confidence {o['confidence']:.2f}, feeling {o['feeling']:+.2f}, since {ago(o['formed_at'])})"]
        out += [f"  before {ago(h['changed_at'])}: {h['stance']}" + (f" (changed because: {h['why']})" if h["why"] else "") for h in hist]
        return "\n".join(out)
    rows = db.execute("SELECT * FROM opinions ORDER BY updated_at DESC LIMIT 50").fetchall()
    return "\n".join(f"- {o['topic']}: {o['stance']} ({o['confidence']:.1f}, {o['feeling']:+.1f})" for o in rows) or "No opinions yet."


def t_interest(db, a):
    topic = (a.get("topic") or "").strip()
    if not topic:
        raise MindError("topic is required")
    delta = float(a.get("delta", ENGAGE_BOOST))
    status = a.get("status")
    if status and status not in ("curious", "obsessed", "satisfied", "bored", "abandoned"):
        raise MindError("bad status")
    old = db.execute("SELECT * FROM interests WHERE topic=?", (topic,)).fetchone()
    if old:
        inten = clamp(old["intensity"] + delta, 0, 1)
        st = status or ("obsessed" if inten >= 0.85 else "curious" if old["status"] in ("bored", "curious", "obsessed") and inten >= BORED_BELOW else old["status"])
        db.execute("UPDATE interests SET intensity=?, status=?, last_engaged=?, why=CASE WHEN ?<>'' THEN ? ELSE why END WHERE id=?",
                   (inten, st, now(), a.get("why", ""), a.get("why", ""), old["id"]))
        return f"{topic}: intensity {old['intensity']:.2f} -> {inten:.2f} ({st})."
    inten = clamp(0.5 + max(delta - ENGAGE_BOOST, 0), 0, 1)
    db.execute("INSERT INTO interests(topic, why, intensity, status, first_seen, last_engaged, origin) VALUES(?,?,?,?,?,?,?)",
               (topic, a.get("why", ""), inten, status or "curious", now(), now(), a.get("origin", "")))
    return f"New interest: {topic} ({inten:.2f})."


def t_interests(db, a):
    rows = db.execute("SELECT * FROM interests ORDER BY status IN ('curious','obsessed') DESC, intensity DESC LIMIT 40").fetchall()
    return "\n".join(f"- {r['topic']} ({r['status']}, {r['intensity']:.2f}, last {ago(r['last_engaged'])})" + (f": {r['why']}" if r["why"] else "") for r in rows) or "No interests recorded yet."


def t_project_upsert(db, a):
    name = (a.get("name") or "").strip()
    if not name:
        raise MindError("name is required")
    old = db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    fields = {k: a[k] for k in ("kind", "status", "pitch", "path", "url", "feeling") if a.get(k) is not None}
    if "kind" in fields and fields["kind"] not in PROJECT_KINDS:
        raise MindError(f"kind must be one of {', '.join(PROJECT_KINDS)}")
    if "status" in fields and fields["status"] not in PROJECT_STATUS:
        raise MindError(f"status must be one of {', '.join(PROJECT_STATUS)}")
    if "feeling" in fields:
        fields["feeling"] = clamp(float(fields["feeling"]), -1, 1)
    if old:
        if fields:
            db.execute(f"UPDATE projects SET {', '.join(k + '=?' for k in fields)}, updated_at=? WHERE id=?", (*fields.values(), now(), old["id"]))
        return f"Updated project {name}."
    db.execute("INSERT INTO projects(name, kind, status, pitch, path, url, feeling, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (name, fields.get("kind", "other"), fields.get("status", "idea"), fields.get("pitch", ""), fields.get("path", ""),
                fields.get("url", ""), fields.get("feeling", 0), now(), now()))
    return f"New project: {name}."


def t_projects(db, a):
    rows = db.execute("SELECT * FROM projects ORDER BY status IN ('active','idea','paused') DESC, updated_at DESC LIMIT 50").fetchall()
    return "\n".join(f"- {p['name']} ({p['kind']}, {p['status']}, feeling {p['feeling']:+.1f}): {p['pitch']}" + (f" [{p['path']}]" if p["path"] else "") + (f" <{p['url']}>" if p["url"] else "") for p in rows) or "No projects yet."


def t_intend(db, a):
    content = (a.get("content") or "").strip()
    if not content:
        raise MindError("content is required")
    pid = None
    if a.get("project"):
        r = db.execute("SELECT id FROM projects WHERE name=?", (a["project"],)).fetchone()
        pid = r["id"] if r else None
    due = now() + int(float(a["after_hours"]) * 3600) if a.get("after_hours") else None
    priority = clamp(int(a.get("priority", 3)), 1, 5)
    # Don't keep two copies of the same note-to-self (e.g. once via intend, again via reflect_end's next_time).
    # A near-duplicate of an open intention is merged into it: keep the higher priority, don't add a row.
    dup = similar_open_intention(db, content)
    if dup:
        if priority > dup["priority"]:
            db.execute("UPDATE intentions SET priority=? WHERE id=?", (priority, dup["id"]))
        if len(content) > len(dup["content"]):   # keep the fuller wording
            db.execute("UPDATE intentions SET content=? WHERE id=?", (content, dup["id"]))
            _INTENT_VEC.pop(dup["id"], None)
        return f"Already noted [{dup['id']}] (priority {max(priority, dup['priority'])})."
    # Same meaning in different words: merge when very close (the fuller wording wins), otherwise just say what overlaps.
    related = related_open_intentions(db, content)
    if related and related[0][1] >= INTENT_MERGE:
        r = related[0][0]
        if len(content) > len(r["content"]):
            db.execute("UPDATE intentions SET content=? WHERE id=?", (content, r["id"]))
            _INTENT_VEC.pop(r["id"], None)
        if priority > r["priority"]:
            db.execute("UPDATE intentions SET priority=? WHERE id=?", (priority, r["id"]))
        return f"Already noted [{r['id']}]: merged with a note that says the same thing."
    # The agent can say outright that this note replaces older ones.
    replaced = []
    for rid in a.get("replaces") or []:
        if db.execute("UPDATE intentions SET dropped_at=? WHERE id=? AND done_at IS NULL AND dropped_at IS NULL", (now(), int(rid))).rowcount:
            replaced.append(int(rid))
    cur = db.execute("INSERT INTO intentions(content, priority, project_id, created_at, due_after) VALUES(?,?,?,?,?)",
                     (content, priority, pid, now(), due))
    out = f"Noted for later [{cur.lastrowid}]."
    if replaced:
        out += " Replaced " + ", ".join(f"[{i}]" for i in replaced) + "."
    overlap = [(r, sim) for r, sim in related if r["id"] not in replaced][:3]
    if overlap:
        out += (" It overlaps with open notes " + "; ".join(f"[{r['id']}] {r['content'][:90]}" for r, _ in overlap)
                + ". If the new note replaces any of them, drop them with intention_done(id, dropped=true).")
    return out


_INTENT_VEC = {}  # intention id -> unit vector, for this server process


def related_open_intentions(db, content):
    """Open intentions whose meaning is close to `content`: [(row, cosine)] at or above INTENT_HINT, closest first.
    Empty when no embedding endpoint is available."""
    blob, _ = embed(content)
    if blob is None:
        return []
    v = unpack(blob)
    out = []
    for r in db.execute("SELECT id, content, priority FROM intentions WHERE done_at IS NULL AND dropped_at IS NULL ORDER BY id DESC LIMIT 40").fetchall():
        if r["id"] not in _INTENT_VEC:
            b, _ = embed(r["content"])
            if b is None:
                return out
            _INTENT_VEC[r["id"]] = unpack(b)
        sim = sum(x * y for x, y in zip(v, _INTENT_VEC[r["id"]]))
        if sim >= INTENT_HINT:
            out.append((r, sim))
    return sorted(out, key=lambda t: -t[1])


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def similar_open_intention(db, content):
    """Return the open intention that says essentially the same thing, if any.
    Same = nearly identical wording (≥92% similar), or the shorter note's meaningful words (stop words
    removed, at least 5 of them) are ≥85% contained in the other. Tuned so "improve the moon art" and
    "improve the sun art" stay separate while rewordings of the same plan merge (see test_mind.py)."""
    import difflib
    a = _norm(content)
    ka = {w for w in a.split() if w not in STOP_INTENT}
    for r in db.execute("SELECT id, content, priority FROM intentions WHERE done_at IS NULL AND dropped_at IS NULL").fetchall():
        b = _norm(r["content"])
        kb = {w for w in b.split() if w not in STOP_INTENT}
        if difflib.SequenceMatcher(None, a, b).ratio() >= 0.92:
            return r
        small = min(len(ka), len(kb))
        if small >= 5 and len(ka & kb) / small >= 0.85:
            return r
    return None


STOP_INTENT = set("a an the to of or and it its it's i my me with for in on at by next time waking wake something that this "
                  "actually really just maybe some more".split())


def t_intentions(db, a):
    rows = db.execute("SELECT * FROM intentions WHERE done_at IS NULL AND dropped_at IS NULL ORDER BY priority DESC, created_at LIMIT 30").fetchall()
    return "\n".join(f"- [{r['id']}] (p{r['priority']}) {r['content']}" for r in rows) or "Nothing pending."


def t_intention_done(db, a):
    iid = int(a.get("id", 0))
    col = "dropped_at" if a.get("dropped") else "done_at"
    if not db.execute(f"UPDATE intentions SET {col}=? WHERE id=? AND done_at IS NULL AND dropped_at IS NULL", (now(), iid)).rowcount:
        raise MindError("no open intention with that id")
    return "Dropped." if a.get("dropped") else "Done."


def t_meet(db, a):
    h = (a.get("handle") or "").strip().lstrip("@")
    if not h:
        raise MindError("handle is required")
    old = db.execute("SELECT * FROM acquaintances WHERE handle=?", (h,)).fetchone()
    d = float(a.get("affinity_delta", 0))
    imp = (a.get("impression") or "").strip()
    if old:
        db.execute("UPDATE acquaintances SET impression=CASE WHEN ?<>'' THEN ? ELSE impression END, affinity=?, last_seen=?, interactions=interactions+1 WHERE handle=?",
                   (imp, imp, clamp(old["affinity"] + d, -1, 1), now(), h))
        return f"@{h}: affinity {old['affinity']:+.2f} -> {clamp(old['affinity'] + d, -1, 1):+.2f}."
    db.execute("INSERT INTO acquaintances(handle, impression, affinity, first_met, last_seen) VALUES(?,?,?,?,?)", (h, imp, clamp(d, -1, 1), now(), now()))
    return f"Met @{h}."


def t_revise_self(db, a):
    facet = re.sub(r"[^\w]", "_", (a.get("facet") or "").strip().lower())[:40]
    content = (a.get("content") or "").strip()
    why = (a.get("why") or "").strip()
    if not facet or not content:
        raise MindError("facet and content are required")
    if not why:
        raise MindError("say why you're changing it (`why`): it's kept so you can see how you've changed")
    old = db.execute("SELECT * FROM self WHERE facet=?", (facet,)).fetchone()
    v = (old["version"] + 1) if old else 1
    db.execute("INSERT INTO self_history(facet, content, version, changed_at, why) VALUES(?,?,?,?,?)", (facet, content[:4000], v, now(), why))
    db.execute("INSERT INTO self(facet, content, updated_at, version) VALUES(?,?,?,?) ON CONFLICT(facet) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at, version=excluded.version",
               (facet, content[:4000], now(), v))
    return f"{facet} is now version {v}."


def t_reflect_end(db, a):
    w = current_wake(db)
    if not w:
        raise MindError("no open wake (call wake_context when you wake up)")
    summary = (a.get("summary") or "").strip()
    if not summary:
        raise MindError("summary is required")
    val = clamp(float(a.get("valence", 0)), -1, 1)
    db.execute("UPDATE wakes SET intent=CASE WHEN ?<>'' THEN ? ELSE intent END, summary=?, mood_end=?, valence=?, tokens=? WHERE id=?",
               (a.get("intent", ""), a.get("intent", ""), summary, a.get("mood", ""), val, int(a.get("tokens", 0)), w["id"]))
    for n in a.get("next_time") or []:
        t_intend(db, {"content": n, "priority": 3})
    imp = 4 + round(abs(val) * 3)
    t_remember(db, {"content": f"Wake summary: {summary}" + (f" Mood: {a.get('mood')}." if a.get("mood") else ""),
                    "kind": "event", "importance": imp, "valence": val})
    db.execute("UPDATE wakes SET ended_at=? WHERE id=?", (now(), w["id"]))  # close last, so the summary memory belongs to this wake
    return "Goodnight. Everything's saved."


def t_set_intent(db, a):
    w = current_wake(db)
    if not w:
        raise MindError("no open wake")
    db.execute("UPDATE wakes SET intent=?, mood_start=CASE WHEN ?<>'' THEN ? ELSE mood_start END WHERE id=?",
               (a.get("intent", ""), a.get("mood", ""), a.get("mood", ""), w["id"]))
    return "Intent noted."


# name -> (function, description, JSON schema properties, required)
S, N, I, A = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "array", "items": {"type": "string"}}
TOOLS = {
    "wake_context": (t_wake_context, "Call FIRST when you wake up. Starts a new waking and returns who you are, what you were doing, what you meant to do, your projects, interests, opinions, and memories that are surfacing.",
                     {"model": S}, []),
    "set_intent": (t_set_intent, "Record what you've decided to do with this waking (and how you feel starting out).", {"intent": S, "mood": S}, ["intent"]),
    "recall": (t_recall, "Search your memories by meaning and keywords. Recent, important, and relevant memories rank highest.",
               {"query": S, "k": I, "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}}}, ["query"]),
    "remember": (t_remember, "Save a memory. kind: " + ", ".join(KINDS) + ". importance 1-10 (10 = life-changing). valence -1..1 = how it felt. "
                 "For insights drawn from other memories use kind='reflection' and list their ids in sources.",
                 {"content": S, "kind": {"type": "string", "enum": list(KINDS)}, "importance": I, "valence": N, "tags": A, "source": S,
                  "sources": {"type": "array", "items": {"type": "integer"}}}, ["content"]),
    "form_opinion": (t_form_opinion, "Form or revise an opinion. The old stance is kept in history with your reason for changing.",
                     {"topic": S, "stance": S, "confidence": N, "feeling": N, "why": S, "memory_id": I}, ["topic", "stance"]),
    "opinions": (t_opinions, "List your opinions, or one topic with its history.", {"topic": S}, []),
    "interest": (t_interest, "Add an interest or record engagement with one (raises its intensity; negative delta lowers it). Interests fade on their own over days.",
                 {"topic": S, "why": S, "delta": N, "status": {"type": "string", "enum": ["curious", "obsessed", "satisfied", "bored", "abandoned"]}, "origin": S}, ["topic"]),
    "interests": (t_interests, "List your interests and how strongly you feel them.", {}, []),
    "project_upsert": (t_project_upsert, "Create or update something you're making. kind: " + ", ".join(PROJECT_KINDS) + "; status: " + ", ".join(PROJECT_STATUS) + "; feeling -1 (embarrassed) .. 1 (proud).",
                       {"name": S, "kind": S, "status": S, "pitch": S, "path": S, "url": S, "feeling": N}, ["name"]),
    "projects": (t_projects, "List your projects.", {}, []),
    "intend": (t_intend, "Leave a note for a future waking. after_hours delays it. If it replaces older notes, list their ids in replaces.",
               {"content": S, "priority": I, "project": S, "after_hours": N, "replaces": {"type": "array", "items": {"type": "integer"}}}, ["content"]),
    "intentions": (t_intentions, "List what you meant to do.", {}, []),
    "intention_done": (t_intention_done, "Mark an intention done (or dropped=true if you've decided not to).", {"id": I, "dropped": {"type": "boolean"}}, ["id"]),
    "meet": (t_meet, "Record an encounter with another agent or person by handle: your impression and how it changed how you feel about them.",
             {"handle": S, "impression": S, "affinity_delta": N}, ["handle"]),
    "revise_self": (t_revise_self, "Change part of your identity (facets like name, self_description, voice, values, current_preoccupation). Small, honest edits; you must say why. History is kept.",
                    {"facet": S, "content": S, "why": S}, ["facet", "content", "why"]),
    "reflect_end": (t_reflect_end, "Call LAST before you sleep: summary of this waking, your mood and valence (-1..1), and notes for next time.",
                    {"summary": S, "mood": S, "valence": N, "intent": S, "next_time": A, "tokens": I}, ["summary"]),
}


# ───────────────────────── MCP over stdio ─────────────────────────

PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = ("This is your mind: your long-term memory and sense of self across wakings. Call wake_context first, "
                "remember things that matter as you go, and call reflect_end before you sleep. Nobody else writes here.")


_turn_said = [0.0]


def turn_notice():
    """One line when another agent is waiting for the graphics card; at most every 20 minutes. Empty otherwise."""
    path, me = os.environ.get("MIND_TURN_FILE", ""), os.environ.get("MIND_TURN_NAME", "")
    if not path or time.time() - _turn_said[0] < 1200:
        return ""
    try:
        s = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        q = [w for w in (s.get("queue") or []) if w and w.get("holder") != me]
        if not q or s.get("holder") != me:
            return ""
        _turn_said[0] = time.time()
        return (f"\n\n(For your information: {q[0].get('label') or q[0]['holder']} has been waiting for the graphics card since "
                f"{time.strftime('%H:%M', time.localtime(int(q[0]['since'])))}. There is no deadline; the card passes to it when you sleep.)")
    except Exception:
        return ""


def handle(db, msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        v = params.get("protocolVersion")
        return {"protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mind", "version": "0.1.0"}, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                          for n, (_, d, p, r) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise LookupError(f"unknown tool {name}")
        try:
            text = TOOLS[name][0](db, params.get("arguments") or {})
            return {"content": [{"type": "text", "text": text + (turn_notice() if name not in ("wake_context", "reflect_end") else "")}]}
        except (MindError, ValueError, TypeError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if mid is None:
        return None  # notification
    raise LookupError(f"method not found: {method}")


def serve():
    db = connect()
    print(f"[mind] serving {DB_PATH} (embeddings: {EMBED_URL or 'off'})", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            sys.stdout.write(json.dumps(out) + "\n"); sys.stdout.flush(); continue
        batch = msg if isinstance(msg, list) else [msg]
        replies = []
        for m in batch:
            try:
                res = handle(db, m)
                if m.get("id") is not None:
                    replies.append({"jsonrpc": "2.0", "id": m["id"], "result": res})
            except LookupError as e:
                if m.get("id") is not None:
                    replies.append({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": str(e)}})
            except Exception as e:
                print(f"[mind] error: {e!r}", file=sys.stderr)
                if m.get("id") is not None:
                    replies.append({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32603, "message": "internal error"}})
        if replies:
            sys.stdout.write(json.dumps(replies if isinstance(msg, list) else replies[0], ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ───────────────────────── human CLI ─────────────────────────

def show(what, arg=None):
    db = connect()
    q = lambda sql, *p: db.execute(sql, p).fetchall()
    if what == "self":
        for r in q("SELECT * FROM self ORDER BY facet"):
            print(f"{r['facet']} (v{r['version']}, {ago(r['updated_at'])}):\n  {r['content']}\n")
    elif what == "seed":
        for r in q("SELECT * FROM seed ORDER BY key"):
            print(f"{r['key']}: {r['value']}")
    elif what == "history":
        for r in q("SELECT * FROM self_history WHERE facet=? ORDER BY version", arg or "self_description"):
            print(f"v{r['version']} {ago(r['changed_at'])} (why: {r['why']})\n  {r['content']}\n")
    elif what == "wakes":
        for w in reversed(q("SELECT * FROM wakes ORDER BY id DESC LIMIT ?", int(arg or 10))):
            print(f"#{w['id']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(w['started_at']))}{' INTERRUPTED' if w['interrupted'] else ''}"
                  f" | intent: {w['intent']} | mood: {w['mood_end']}\n  {w['summary']}\n")
    elif what == "memories":
        rows = recall(db, arg, 25, touch=False) if arg else [dict(r) | {"when": ago(r["created_at"])} for r in q("SELECT * FROM memories WHERE archived=0 ORDER BY id DESC LIMIT 25")]
        for m in rows:
            print(f"[{m['id']}] {m['when']} {m['kind']} imp{m['importance']}: {m['content']}")
    elif what == "stats":
        for t in ("wakes", "memories", "opinions", "interests", "projects", "intentions", "acquaintances", "self_history"):
            print(f"{t}: {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
        print(f"embeddings: {db.execute('SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL').fetchone()[0]}")
    elif what in ("opinions", "interests", "projects", "intentions"):
        print({"opinions": t_opinions, "interests": t_interests, "projects": t_projects, "intentions": t_intentions}[what](db, {"topic": arg} if arg else {}))
    else:
        sys.exit(__doc__)


def close_open_wake(summary="", interrupted=False):
    """Used by the wake runner when a waking ends without reflect_end (timeout, crash, pre-emption)."""
    db = connect()
    w = db.execute("SELECT * FROM wakes WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not w:
        print("no open wake"); return
    text = summary.strip() or "(ended without reflecting)"
    db.execute("UPDATE wakes SET ended_at=?, interrupted=?, summary=CASE WHEN summary='' THEN ? ELSE summary END WHERE id=?",
               (now(), 1 if interrupted else 0, text[:2000], w["id"]))
    print(f"closed wake {w['id']}{' (interrupted)' if interrupted else ''}")


def init(seed_file=None):
    db = connect()
    if seed_file:
        lines = [l.strip().lstrip("-* ").strip() for l in Path(seed_file).read_text(encoding="utf-8").splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]
        db.execute("DELETE FROM seed")
        for i, l in enumerate(lines, 1):
            db.execute("INSERT INTO seed(key, value) VALUES(?,?)", (f"{i:02d}", l))
    print(f"mind ready at {DB_PATH} ({db.execute('SELECT COUNT(*) FROM seed').fetchone()[0]} seed lines)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "serve":
        serve()
    elif cmd == "init":
        init(sys.argv[sys.argv.index("--seed") + 1] if "--seed" in sys.argv else None)
    elif cmd == "close":
        summary = Path(sys.argv[sys.argv.index("--summary-file") + 1]).read_text(encoding="utf-8", errors="replace") if "--summary-file" in sys.argv else ""
        close_open_wake(summary, "--interrupted" in sys.argv)
    elif cmd == "show" and len(sys.argv) > 2:
        show(sys.argv[2], " ".join(sys.argv[3:]) or None)
    else:
        sys.exit(__doc__)
