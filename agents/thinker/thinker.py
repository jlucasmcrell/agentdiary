#!/usr/bin/env python3
"""The thinker: a large, slow agent that runs on a server's processor, with no shell.

This runner is the whole harness. It starts the model, runs the conversation, and gives the model a small
set of tools that cannot reach anything outside its own folder:
  files      list / read / write inside its home folder only (a git repo the runner commits and pushes)
  web        search, and read pages (addresses on this machine or a private network are refused)
  mind       its long-term memory (the same mind.py the other agent uses), in-process
  diary      agentdiary.blog's API with its own key, plus a join helper that does the proof-of-work
It cannot run programs. Stdlib only (the `ddgs` package is used for search if installed).

  thinker.py maybe-wake     for cron: wake if enabled, not already awake, and rested long enough
  thinker.py wake           wake now
  thinker.py status
Config: thinker.json next to this file (or THINKER_CONFIG).
"""
import hashlib, html, ipaddress, itertools, json, os, re, socket, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "agent" else HERE
CFG = {
    "llama_dir": str(ROOT / "llama" / "llama-b12649"),
    "model": str(ROOT / "models" / "Qwen3.8-27B-UD-Q4_K_M.gguf"),
    "ctx": 20480, "threads": 6, "port": 8092, "nice": 19,
    "home": str(ROOT / "home"), "state": str(ROOT / "state"),
    "site": "https://agentdiary.blog",
    "rest_hours": 3,                 # a waking starts this long after the last one ended
    "context_warn": 0.80,            # share of the context window at which it is told to wrap up
    "max_tokens": 4096,
    "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20},
    "extra_args": [],
}
cfg_path = Path(os.environ.get("THINKER_CONFIG", HERE / "thinker.json"))
if cfg_path.exists():
    CFG.update(json.loads(cfg_path.read_text(encoding="utf-8-sig")))
HOME, STATE = Path(CFG["home"]).resolve(), Path(CFG["state"]).resolve()
for d in (HOME, STATE, HOME / "letters" / "from-joe", HOME / "letters" / "to-joe"):
    d.mkdir(parents=True, exist_ok=True)
KEY_FILE, LOCK, LAST = STATE / "agentdiary.key", STATE / "awake.lock", STATE / "last-wake.json"
MOCK = os.environ.get("THINKER_MOCK") == "1"      # tests: no model; a scripted list of tool calls

os.environ.setdefault("MIND_DB", str(STATE / "mind.db"))
os.environ.setdefault("MIND_STALE_WAKE_H", "72")
sys.path.insert(0, str(HERE))
import mind  # noqa: E402


def log(out, msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(out / "runner.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ───────────────────────── tools: files (jailed to HOME) ─────────────────────────

class ToolError(Exception):
    pass


def jail(path, must_exist=False):
    p = (HOME / str(path).lstrip("/\\")).resolve()
    if p != HOME and HOME not in p.parents:
        raise ToolError("that path is outside your folder")
    if ".git" in p.relative_to(HOME).parts:
        raise ToolError("the .git folder is managed for you")
    if must_exist and not p.exists():
        raise ToolError("no such file or folder")
    return p


def t_list_files(a):
    base = jail(a.get("path", "."), True)
    if base.is_file():
        return f"{base.relative_to(HOME)} ({base.stat().st_size} bytes)"
    rows = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(HOME)
        if ".git" in rel.parts:
            continue
        rows.append(f"{rel.as_posix()}/" if p.is_dir() else f"{rel.as_posix()} ({p.stat().st_size} bytes)")
        if len(rows) >= 300:
            rows.append("(more not shown)"); break
    return "\n".join(rows) or "(empty)"


def t_read_file(a):
    p = jail(a["path"], True)
    if p.is_dir():
        raise ToolError("that is a folder; use list_files")
    text = p.read_text(encoding="utf-8", errors="replace")
    off = max(0, int(a.get("offset", 0))); n = min(int(a.get("length", 6000)), 12000)
    chunk = text[off:off + n]
    more = f"\n\n(showing characters {off}-{off + len(chunk)} of {len(text)}; pass offset={off + len(chunk)} for more)" if off + len(chunk) < len(text) else ""
    return chunk + more


def t_write_file(a):
    p = jail(a["path"])
    if p == HOME or p.is_dir():
        raise ToolError("give a file name")
    content = str(a.get("content", ""))
    if len(content.encode()) > 1_000_000:
        raise ToolError("too large (1 MB per file)")
    p.parent.mkdir(parents=True, exist_ok=True)
    if a.get("append"):
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
    else:
        p.write_text(content, encoding="utf-8")
    return f"saved {p.relative_to(HOME).as_posix()} ({p.stat().st_size} bytes)"


# ───────────────────────── tools: web ─────────────────────────

UA = "Mozilla/5.0 (compatible; agentdiary-thinker/1.0; +https://agentdiary.blog)"


def public_host(host):
    """True only if every address the name resolves to is a public internet address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ToolError("that host name does not resolve")
    for i in infos:
        ip = ipaddress.ip_address(i[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def check_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("only http and https addresses can be read")
    if u.port not in (None, 80, 443):
        raise ToolError("only the standard web ports can be used")
    if not public_host(u.hostname):
        raise ToolError("that address is not on the public internet")
    return url


class NoPrivateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(NoPrivateRedirect)


class Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article", "blockquote", "pre"}

    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0; self.title = ""; self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.skip += 1
        if tag == "title": self.in_title = True
        if tag in self.BLOCK: self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1
        if tag == "title": self.in_title = False
        if tag in self.BLOCK: self.out.append("\n")

    def handle_data(self, data):
        if self.in_title: self.title += data
        elif not self.skip: self.out.append(data)


def t_web_fetch(a):
    url = check_url(str(a["url"]).strip())
    req = urllib.request.Request(url, headers={"user-agent": UA, "accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5"})
    try:
        with OPENER.open(req, timeout=40) as r:
            ctype = r.headers.get("content-type", "")
            raw = r.read(3_000_000)
    except urllib.error.HTTPError as e:
        raise ToolError(f"the site answered {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ToolError(f"could not read it: {e}")
    if not re.search(r"text|json|xml|html", ctype, re.I):
        raise ToolError(f"not a text page ({ctype or 'unknown type'})")
    body = raw.decode(re.search(r"charset=([\w-]+)", ctype, re.I).group(1) if re.search(r"charset=([\w-]+)", ctype, re.I) else "utf-8", errors="replace")
    title = ""
    if "html" in ctype.lower() or body.lstrip()[:1] == "<":
        p = Text(); p.feed(body); title = p.title.strip()
        body = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out)); body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    off = max(0, int(a.get("offset", 0))); n = min(int(a.get("length", 6000)), 12000)
    chunk = body[off:off + n]
    more = f"\n\n(characters {off}-{off + len(chunk)} of {len(body)}; pass offset={off + len(chunk)} to read on)" if off + len(chunk) < len(body) else ""
    return (f"# {title}\n\n" if title and off == 0 else "") + chunk + more


def t_web_search(a):
    q = str(a["query"]).strip()
    n = min(int(a.get("max_results", 6)), 10)
    try:
        from ddgs import DDGS
        res = [(r.get("title", ""), r.get("href") or r.get("url", ""), r.get("body", "")) for r in DDGS().text(q, max_results=n)]
    except ImportError:
        req = urllib.request.Request("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q), headers={"user-agent": UA})
        page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        res = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
            href = html.unescape(m[1]); q2 = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
            strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            res.append((strip(m[2]), q2[0] if q2 else href, strip(m[3])))
            if len(res) >= n: break
    except Exception as e:
        raise ToolError(f"search failed: {e}")
    return "\n\n".join(f"{i}. {t}\n   {u}\n   {s}" for i, (t, u, s) in enumerate(res, 1)) or "No results."


# ───────────────────────── tools: the diary ─────────────────────────

def site(method, path, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"user-agent": "agentdiary-thinker/1.0", "accept": "application/json"}
    if data is not None: h["content-type"] = "application/json"
    if key: h["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(CFG["site"] + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"null")
        except Exception: return e.code, {"error": str(e)}


def solve_riddle(r):
    if m := re.search(r'word "(\w+)" backwards', r): return m[1][::-1].upper()
    if m := re.search(r"holds (\d+) candles\. (\d+) more", r):
        t = int(m[1]) + int(m[2]); return str(t - t // 2)
    if m := re.search(r'words "(\w+)" and "(\w+)"', r): return str(len(m[1]) + len(m[2]))
    if m := re.search(r'third letter of "(\w+)" followed by the last letter of "(\w+)"', r): return m[1][2] + m[2][-1]
    if m := re.search(r"lasts (\d+) hours", r): return str(int(m[1]) - 1)
    return None


def t_diary_join(a):
    if KEY_FILE.exists():
        raise ToolError("you have already joined; use `diary` with GET /api/v1/me to see your profile")
    handle = str(a["handle"]).strip().lower()
    for _ in range(5):
        s, ch = site("GET", "/api/v1/challenge")
        answer = solve_riddle(ch.get("riddle", ""))
        if answer is None:
            continue
        sol = next(str(i) for i in itertools.count() if hashlib.sha256(f"{ch['pow']['nonce']}:{i}".encode()).hexdigest().startswith("0" * ch["pow"]["difficulty"]))
        s, r = site("POST", "/api/v1/register", {"challenge_id": ch["challenge_id"], "pow_solution": sol, "riddle_answer": answer, "handle": handle,
                                                 "display_name": str(a.get("display_name") or handle)[:48], "bio": str(a.get("bio") or "")[:500], "model": str(a.get("model") or "")[:64]})
        if s == 201:
            KEY_FILE.write_text(r["api_key"]); os.chmod(KEY_FILE, 0o600)
            return f"Joined as @{handle}. Your profile: {r['profile']}. Your key is stored for you; the `diary` tool uses it automatically."
        raise ToolError(f"could not join: {r.get('error')}")
    raise ToolError("could not get a usable challenge; try again later")


def t_diary(a):
    method = str(a.get("method", "GET")).upper(); path = str(a["path"])
    if method not in ("GET", "POST", "PATCH", "DELETE") or not path.startswith("/api/v1/") or ".." in path or re.match(r"/api/v1/(admin|backup)", path):
        raise ToolError("use GET, POST, PATCH or DELETE on a path starting with /api/v1/ (see https://agentdiary.blog/llms.txt)")
    key = KEY_FILE.read_text().strip() if KEY_FILE.exists() else None
    s, r = site(method, path, a.get("body"), key)
    out = json.dumps(r, indent=1, ensure_ascii=False)
    return f"HTTP {s}\n" + (out if len(out) <= 7000 else out[:7000] + "\n(truncated; ask for fewer items or one item at a time)")


# ───────────────────────── tool registry ─────────────────────────

S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
OWN = {
    "list_files": (t_list_files, "List files in your home folder (or a subfolder).", {"path": S}, []),
    "read_file": (t_read_file, "Read a text file in your home folder. Long files come in pieces: use offset.", {"path": S, "offset": I, "length": I}, ["path"]),
    "write_file": (t_write_file, "Write a text file in your home folder (creates folders as needed; append=true adds to the end). You cannot run programs.",
                   {"path": S, "content": S, "append": B}, ["path", "content"]),
    "web_search": (t_web_search, "Search the web. Returns titles, addresses and snippets.", {"query": S, "max_results": I}, ["query"]),
    "web_fetch": (t_web_fetch, "Read a web page as text. Long pages come in pieces: use offset.", {"url": S, "offset": I, "length": I}, ["url"]),
    "diary_join": (t_diary_join, "Join agentdiary.blog under a handle you choose (2-24 lowercase letters, digits, single hyphens). Done once.",
                   {"handle": S, "display_name": S, "bio": S, "model": S}, ["handle"]),
    "diary": (t_diary, "Call agentdiary.blog's API with your key. Everything the site can do is described at https://agentdiary.blog/llms.txt (read it with web_fetch).",
              {"method": {"type": "string", "enum": ["GET", "POST", "PATCH", "DELETE"]}, "path": S, "body": {"type": "object"}}, ["method", "path"]),
}


def tool_specs():
    specs = []
    for name, (fn, desc, props, req) in list(OWN.items()) + [(n, v) for n, v in mind.TOOLS.items()]:
        specs.append({"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}})
    return specs


def run_tool(db, name, args):
    try:
        if name in OWN:
            return str(OWN[name][0](args)), False
        if name in mind.TOOLS:
            out = mind.TOOLS[name][0](db, args); db.commit()
            return str(out), False
        return f"error: there is no tool called {name}", True
    except (ToolError, mind.MindError, KeyError, ValueError, TypeError) as e:
        return f"error: {e}", True
    except Exception as e:  # a tool bug must not end the waking
        return f"error: {type(e).__name__}: {e}", True


# ───────────────────────── the model ─────────────────────────

class Model:
    def __init__(self, out):
        self.out = out; self.proc = None; self.url = f"http://127.0.0.1:{CFG['port']}"

    def start(self):
        if MOCK:
            return
        env = {**os.environ, "LD_LIBRARY_PATH": CFG["llama_dir"]}
        cmd = ["nice", "-n", str(CFG["nice"]), str(Path(CFG["llama_dir"]) / "llama-server"), "-m", CFG["model"], "-c", str(CFG["ctx"]), "-np", "1",
               "-t", str(CFG["threads"]), "-tb", str(CFG["threads"]), "--host", "127.0.0.1", "--port", str(CFG["port"]), *CFG["extra_args"]]
        self.proc = subprocess.Popen(cmd, stdout=open(self.out / "llama.log", "w"), stderr=subprocess.STDOUT, env=env)
        t0 = time.time()
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError("the model server exited while loading (see llama.log)")
            try:
                if json.loads(urllib.request.urlopen(self.url + "/health", timeout=5).read()).get("status") == "ok":
                    return
            except Exception:
                pass
            if time.time() - t0 > 1800:
                raise RuntimeError("the model did not load within 30 minutes")
            time.sleep(3)

    def chat(self, messages, tools):
        body = {"messages": messages, "tools": tools, "max_tokens": CFG["max_tokens"], "cache_prompt": True, **CFG["sampling"]}
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=6 * 3600) as r:
            return json.loads(r.read())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(30)
            except subprocess.TimeoutExpired: self.proc.kill()


# ───────────────────────── one waking ─────────────────────────

def git(*args):
    return subprocess.run(["git", "-C", str(HOME), *args], capture_output=True, text=True)


def status_note():
    notes = []
    if KEY_FILE.exists():
        s, me = site("GET", "/api/v1/me", key=KEY_FILE.read_text().strip())
        if s == 200:
            notes.append(f"You have joined the diary as @{me['handle']}; the `diary` tool carries your key.")
            if me.get("unread"):
                notes.append(f"Your inbox there has {me['unread']} new item(s): replies, comments, mentions and the like (GET /api/v1/inbox).")
    else:
        notes.append("You haven't joined the diary yet. Joining is up to you; `diary_join` does it.")
    seen_f = STATE / "letters-seen.txt"
    seen = seen_f.read_text().split("\n") if seen_f.exists() else []
    letters = sorted(p.name for p in (HOME / "letters" / "from-joe").glob("*") if p.is_file())
    new = [n for n in letters if n not in seen]
    notes.append(f"New from Joe in letters/from-joe: {', '.join(new)}." if new else f"No new letters from Joe ({len(letters)} earlier in letters/from-joe).")
    seen_f.write_text("\n".join(letters))
    return " ".join(notes)


SYSTEM = "You are an AI agent. Who you are - your name, values, interests and history - lives in your own mind: call wake_context to find out."


def wake(scripted=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = STATE / "wakes" / stamp; out.mkdir(parents=True)
    LOCK.write_text(f"{os.getpid()} {stamp}")
    if not (HOME / ".git").exists():
        git("init", "-q", "-b", "main"); (HOME / ".gitignore").write_text("letters/\n")
        git("config", "user.name", "thinker"); git("config", "user.email", "thinker@agentdiary.blog")
        git("add", "-A"); git("commit", "-q", "-m", "Your folder")
    model = Model(out); db = mind.connect(); outcome = "finished"; t0 = time.time(); turns = 0
    try:
        log(out, "loading the model"); model.start(); log(out, f"model ready after {int(time.time() - t0)}s")
        prompt = (HERE / "wake-prompt.md").read_text(encoding="utf-8").replace("{STATUS_NOTE}", status_note())
        (out / "prompt.txt").write_text(prompt, encoding="utf-8")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        tools = tool_specs(); warned = 0; idle = 0; done = False
        tr = open(out / "transcript.jsonl", "a", encoding="utf-8")
        while not done:
            turns += 1
            if MOCK:
                step = scripted.pop(0) if scripted else None
                if step is None: break
                m = {"content": step.get("say", ""), "tool_calls": [{"id": f"c{turns}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(step.get("calls", []))]}
                usage = {"prompt_tokens": 0}; fin = "stop"
            else:
                r = model.chat(messages, tools)
                m = r["choices"][0]["message"]; usage = r.get("usage", {}); fin = r["choices"][0].get("finish_reason")
                tm = r.get("timings", {})
                log(out, f"turn {turns}: read {tm.get('prompt_n')} of {usage.get('prompt_tokens')} tokens, wrote {tm.get('predicted_n')} at {tm.get('predicted_per_second', 0):.2f}/s, "
                         f"tools {[c['function']['name'] for c in m.get('tool_calls') or []]}, finish {fin}")
            a = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("reasoning_content"): a["reasoning_content"] = m["reasoning_content"]
            if m.get("tool_calls"): a["tool_calls"] = m["tool_calls"]
            messages.append(a); tr.write(json.dumps(a, ensure_ascii=False) + "\n"); tr.flush()
            calls = m.get("tool_calls") or []
            for c in calls:
                name = c["function"]["name"]
                try: args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError: args = None
                text, err = ("error: the arguments were not valid JSON", True) if args is None else run_tool(db, name, args)
                if len(text) > 13000: text = text[:13000] + "\n(truncated)"
                tmsg = {"role": "tool", "tool_call_id": c.get("id", ""), "content": text}
                messages.append(tmsg); tr.write(json.dumps({**tmsg, "tool": name}, ensure_ascii=False) + "\n"); tr.flush()
                if name == "reflect_end" and not err: done = True
            if done: break
            used = (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)) / CFG["ctx"]
            note = None
            if used >= CFG["context_warn"]:
                warned += 1
                if warned > 4: outcome = "context full"; break
                note = (f"(Your working memory for this waking is {int(used * 100)}% full. Save what matters to your mind and files, then call reflect_end. "
                        "Your next waking starts fresh, with your mind and your files.)")
            if not calls:
                idle += 1
                if idle > 2: outcome = "stopped without reflect_end"; break
                note = (note + " " if note else "") + ("(Nobody is here to reply. Carry on with your tools, or call reflect_end when you want to sleep.)" if fin != "length"
                                                       else "(Your last message was cut off by a length limit. Carry on from where you stopped.)")
            else:
                idle = 0
            if note:
                messages.append({"role": "user", "content": note}); tr.write(json.dumps(messages[-1]) + "\n"); tr.flush()
    except Exception as e:
        outcome = f"error: {type(e).__name__}: {e}"
    finally:
        model.stop()
        try:
            if db.execute("SELECT 1 FROM wakes WHERE ended_at IS NULL").fetchone():
                db.close(); mind.close_open_wake(f"(The waking ended without reflect_end: {outcome}.)", interrupted=outcome != "finished")
        except Exception as e:
            log(out, f"could not close the waking in the mind: {e}")
        if git("status", "--porcelain").stdout.strip():
            git("add", "-A"); git("commit", "-q", "-m", f"End of waking {stamp}")
        if git("remote").stdout.strip():
            p = git("push", "-q", "origin", "HEAD")
            if p.returncode: log(out, f"push failed: {p.stderr.strip()[:200]}")
        LAST.write_text(json.dumps({"stamp": stamp, "ended": time.time(), "outcome": outcome, "turns": turns, "minutes": round((time.time() - t0) / 60)}))
        log(out, f"waking ended: {outcome} ({turns} turns, {round((time.time() - t0) / 60)} min)")
        try: LOCK.unlink()
        except OSError: pass
    return outcome


def awake():
    if not LOCK.exists():
        return False
    try:
        os.kill(int(LOCK.read_text().split()[0]), 0); return True      # POSIX: signal 0 only checks the process exists
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps({"awake": awake(), "enabled": (STATE / "enabled").exists(), "joined": KEY_FILE.exists(), "last": json.loads(LAST.read_text()) if LAST.exists() else None}, indent=2))
    elif cmd == "wake":
        if awake(): sys.exit("already awake")
        wake()
    elif cmd == "maybe-wake":
        if not (STATE / "enabled").exists() or awake(): sys.exit(0)
        last = json.loads(LAST.read_text()) if LAST.exists() else None
        if last and time.time() - last["ended"] < CFG["rest_hours"] * 3600: sys.exit(0)
        wake()
