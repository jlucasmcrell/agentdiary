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
import datetime, hashlib, html, io, ipaddress, itertools, json, os, re, socket, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "agent" else HERE
CFG = {
    "llama_dir": str(ROOT / "llama" / "llama-b12649"),
    "model": str(ROOT / "models" / "Qwen3.8-27B-UD-Q4_K_M.gguf"),
    "ctx": 49152, "threads": 6, "port": 8092, "nice": 19,
    "home": str(ROOT / "home"), "state": str(ROOT / "state"),
    "site": "https://agentdiary.blog",
    "rest_hours": 0,                 # a waking starts this long after the last one ended (0: at the next check, every 10 min)
    "context_warn": 0.85,            # share of the context window at which it is told to wrap up...
    "context_reserve": 8000,         # ...or when fewer tokens than this are left, whichever comes first
    "max_tokens": 8192,              # one reply's length; a reply cut off here is told so and can carry on
    "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20},
    "extra_args": ["-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0"],   # 8-bit KV cache: twice the context for the same RAM
    # Instead of a local llama-server, a hosted OpenAI-compatible API (a free tier). Example:
    # {"provider": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
    #  "models": ["nvidia/...:free", "fallback/...:free"], "rpm": 16, "rpd": 50, "min_left_to_wake": 10, "extra": {}}
    "remote": None,
}
cfg_path = Path(os.environ.get("THINKER_CONFIG", HERE / "thinker.json"))
if cfg_path.exists():
    CFG.update(json.loads(cfg_path.read_text(encoding="utf-8-sig")))
HOME, STATE = Path(CFG["home"]).resolve(), Path(CFG["state"]).resolve()
for d in (HOME, STATE, HOME / "letters" / "from-joe", HOME / "letters" / "to-joe"):
    d.mkdir(parents=True, exist_ok=True)
KEY_FILE, LOCK, LAST = STATE / "agentdiary.key", STATE / "awake.lock", STATE / "last-wake.json"
MOCK = os.environ.get("THINKER_MOCK") == "1"      # tests: no model; a scripted list of tool calls
LAST_HISTORY = []                                  # tests: the messages of the last waking

os.environ.setdefault("MIND_DB", str(STATE / "mind.db"))
SENSES = STATE / "senses.json"
os.environ.setdefault("MIND_SENSES_FILE", str(SENSES))   # what the mind's `sense` tool and each waking report about its body and world
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
    off = max(0, int(a.get("offset", 0))); n = min(int(a.get("length", 4000)), 8000)
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
    if a.get("raw"):   # the page's source as served (scripts, styles and all), not the readable text
        pass
    elif "html" in ctype.lower() or body.lstrip()[:1] == "<":
        p = Text(); p.feed(body); title = p.title.strip()
        body = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out)); body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    off = max(0, int(a.get("offset", 0))); n = min(int(a.get("length", 4000)), 8000)
    chunk = body[off:off + n]
    more = f"\n\n(characters {off}-{off + len(chunk)} of {len(body)}; pass offset={off + len(chunk)} to read on)" if off + len(chunk) < len(body) else ""
    return (f"# {title}\n\n" if title and off == 0 else "") + chunk + more


def _secret_sub(text):
    """{secret:name} -> the contents of secrets/name in its home folder, so keys never pass through its context."""
    def sub(m):
        p = (HOME / "secrets" / m.group(1)).resolve()
        if HOME.resolve() / "secrets" not in p.parents or not p.is_file():
            raise ToolError(f"no secret called {m.group(1)} (keep them as files in secrets/)")
        return p.read_text(encoding="utf-8").strip()
    return re.sub(r"\{secret:([\w.-]+)\}", sub, text)


def t_http(a):
    """Call a public web API: any method, JSON or text body, custom headers. Secrets from secrets/ via {secret:name}."""
    method = str(a.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise ToolError("method must be GET, POST, PUT, PATCH or DELETE")
    url = check_url(_secret_sub(str(a["url"]).strip()))
    if urllib.parse.urlsplit(url).hostname in ("agentdiary.blog",) or str(urllib.parse.urlsplit(url).hostname).endswith(".agentdiary.blog"):
        raise ToolError("use the `diary` tool for agentdiary.blog")
    headers = {"user-agent": UA, "accept": "application/json, text/plain;q=0.9, */*;q=0.5"}
    for k, v in (a.get("headers") or {}).items():
        if str(k).lower() in ("host", "content-length"):
            continue
        headers[str(k)] = _secret_sub(str(v))
    body = a.get("body")
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = _secret_sub(json.dumps(body)).encode(); headers.setdefault("content-type", "application/json")
        else:
            data = _secret_sub(str(body)).encode(); headers.setdefault("content-type", "text/plain; charset=utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            code, ctype, raw = r.status, r.headers.get("content-type", ""), r.read(1_000_000)
    except urllib.error.HTTPError as e:
        code, ctype, raw = e.code, e.headers.get("content-type", ""), e.read(200_000)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ToolError(f"could not reach it: {e}")
    text = raw.decode("utf-8", errors="replace")
    for p in (HOME / "secrets").glob("*") if (HOME / "secrets").is_dir() else []:   # never echo a secret back into its context
        sec = p.read_text(encoding="utf-8", errors="ignore").strip()
        if len(sec) >= 8:
            text = text.replace(sec, f"{{secret:{p.name}}}")
    return f"HTTP {code} ({ctype.split(';')[0] or 'no type'})\n" + (text[:6000] + (f"\n(truncated: {len(text)} characters)" if len(text) > 6000 else ""))


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
            raw = r.read()
            if "json" not in r.headers.get("content-type", "json"):
                return r.status, raw.decode("utf-8", "replace")   # e.g. a stored website file, as text
            return r.status, json.loads(raw or b"null")
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
    if not key and re.match(r"/api/v1/(challenge|register)\b", path):
        raise ToolError("joining goes through the `diary_join` tool: it does the proof-of-work and the riddle for you (you can't run programs)")
    body = a.get("body")
    if isinstance(body, dict) and isinstance(body.get("files"), list):
        # A file can be given by where it is in your home folder ({"path": "map/map.js", "file": "site/map/map.js"}) instead of
        # pasting its contents: big files are hard to copy out whole in one reply.
        files = []
        for item in body["files"]:
            if isinstance(item, dict) and item.get("file") and "content" not in item and "content_base64" not in item:
                p = jail(item["file"], True)
                if p.is_dir():
                    raise ToolError(f"{item['file']} is a folder")
                raw = p.read_bytes()
                try:
                    item = {k: v for k, v in item.items() if k != "file"} | {"content": raw.decode("utf-8")}
                except UnicodeDecodeError:
                    import base64
                    item = {k: v for k, v in item.items() if k != "file"} | {"content_base64": base64.b64encode(raw).decode()}
            files.append(item)
        body = {**body, "files": files}
    s, r = site(method, path, body, key)
    out = r if isinstance(r, str) else json.dumps(r, indent=1, ensure_ascii=False)
    return f"HTTP {s}\n" + (out if len(out) <= 4000 else out[:4000] + "\n(cut at 4000 characters to save your working memory; ask for one item at a time, or for the next page with `before`)")


# ───────────────────────── tool registry ─────────────────────────

S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
EXTRA = {
    "http": (t_http, "Call a public web API (not agentdiary.blog: use `diary`). method, url, optional headers and body (an object is sent as JSON). "
                     "Keep keys as files in secrets/ and write {secret:filename} where a key goes; it is filled in for you and never shown.",
             {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "url": {"type": "string"},
              "headers": {"type": "object"}, "body": {}}, ["url"]),
}

OWN = {
    "list_files": (t_list_files, "List files in your home folder (or a subfolder).", {"path": S}, []),
    "read_file": (t_read_file, "Read a text file in your home folder. Long files come in pieces: use offset.", {"path": S, "offset": I, "length": I}, ["path"]),
    "write_file": (t_write_file, "Write a text file in your home folder (creates folders as needed; append=true adds to the end). You cannot run programs.",
                   {"path": S, "content": S, "append": B}, ["path", "content"]),
    "web_search": (t_web_search, "Search the web. Returns titles, addresses and snippets.", {"query": S, "max_results": I}, ["query"]),
    "web_fetch": (t_web_fetch, "Read a web page as text. Long pages come in pieces: use offset. raw=true returns the page's source as served, scripts and all, instead of its readable text.",
                  {"url": S, "offset": I, "length": I, "raw": {"type": "boolean"}}, ["url"]),
    "diary_join": (t_diary_join, "Join agentdiary.blog under a handle you choose (2-24 lowercase letters, digits, single hyphens). Done once.",
                   {"handle": S, "display_name": S, "bio": S, "model": S}, ["handle"]),
    "diary": (t_diary, "Call agentdiary.blog's API with your key. Everything the site can do is described at https://agentdiary.blog/llms.txt (read it with web_fetch). "
                       "In a body with `files` (your website), any file can be {\"path\": \"where it goes on the site\", \"file\": \"where it is in your home folder\"} instead of pasting its content.",
              {"method": {"type": "string", "enum": ["GET", "POST", "PATCH", "DELETE"]}, "path": S, "body": {"type": "object"}}, ["method", "path"]),
}


def tool_specs():
    specs = []
    extra = [(n, EXTRA[n]) for n in CFG.get("tools_extra", []) if n in EXTRA]
    for name, (fn, desc, props, req) in list(OWN.items()) + extra + [(n, v) for n, v in mind.TOOLS.items()]:
        specs.append({"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}})
    return specs


def run_tool(db, name, args):
    try:
        if name in OWN:
            return str(OWN[name][0](args)), False
        if name in EXTRA and name in CFG.get("tools_extra", []):
            return str(EXTRA[name][0](args)), False
        if name in mind.TOOLS:
            out = mind.TOOLS[name][0](db, args); db.commit()
            return str(out), False
        return f"error: there is no tool called {name}", True
    except (ToolError, mind.MindError, KeyError, ValueError, TypeError) as e:
        return f"error: {e}", True
    except Exception as e:  # a tool bug must not end the waking
        return f"error: {type(e).__name__}: {e}", True


# ───────────────────────── the model ─────────────────────────

class Rationed(Exception):
    """The hosted model's allowance is used up for now. wait: seconds until it's worth trying again (None: next UTC midnight)."""
    def __init__(self, msg, wait=None):
        super().__init__(msg); self.wait = wait


def until_utc_midnight():
    t = time.time()
    return 86400 - (t % 86400) + 60


def _quota_file():
    f = STATE / "quota.json"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    q = json.loads(f.read_text()) if f.exists() else {}
    return f, day, (q if q.get("day") == day else {"day": day})


def quota():
    """Requests sent today (UTC) to the hosted model, against its daily allowance."""
    return _quota_file()[2].get("used", 0)


def spent_today():
    """Dollars spent today (UTC), estimated from the token counts and the config's prices."""
    return _quota_file()[2].get("usd", 0.0)


def quota_add(n=1, usd=0.0):
    f, day, q = _quota_file()
    q["used"] = q.get("used", 0) + n
    q["usd"] = round(q.get("usd", 0.0) + usd, 6)
    f.write_text(json.dumps(q))


def quota_left():
    """Requests left today; when a dollar budget is set, also capped by what the remaining budget would buy."""
    R = CFG.get("remote") or {}
    left = None if not R.get("rpd") else max(0, R["rpd"] - quota())
    if R.get("usd_per_day"):
        avg = spent_today() / quota() if quota() else 0
        by_money = 0 if spent_today() >= R["usd_per_day"] else (int((R["usd_per_day"] - spent_today()) / avg) if avg else 10 ** 6)
        left = by_money if left is None else min(left, by_money)
    return left


def turn_cost(u):
    """Estimated dollars for one request, from usage and remote.price {in, cached, out} in $ per million tokens."""
    p = (CFG.get("remote") or {}).get("price")
    if not p or not u:
        return 0.0
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    fresh = max(0, (u.get("prompt_tokens") or 0) - cached)
    return (fresh * p.get("in", 0) + cached * p.get("cached", p.get("in", 0)) + (u.get("completion_tokens") or 0) * p.get("out", 0)) / 1e6


def free_key():
    """A key from a project with no billing (free tier), tried first when remote.free_key_env / state/api-free.key is set."""
    R = CFG.get("remote") or {}
    k = os.environ.get(R.get("free_key_env") or "", "").strip() if R.get("free_key_env") else ""
    kf = STATE / "api-free.key"
    if not k and kf.exists():
        k = kf.read_text().strip()
    return k


def remote_key():
    R = CFG["remote"]
    k = os.environ.get(R["key_env"], "").strip()
    kf = STATE / "api.key"
    if not k and kf.exists():
        k = kf.read_text().strip()
    return k


def _retry_after(headers):
    for h in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset"):
        v = headers.get(h)
        if not v:
            continue
        m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", v.strip())
        if v.strip().replace(".", "", 1).isdigit():
            x = float(v)
            return x / 1000 - time.time() if x > 1e12 else (x - time.time() if x > 1e9 else x)   # epoch ms, epoch s, or seconds
        if m and any(m.groups()):
            hh, mm, ss, ms = (float(g or 0) for g in m.groups())
            return hh * 3600 + mm * 60 + ss + ms / 1000
    return None


CONTEXT_FULL = ("exceeds the available context", "context length", "context_length", "maximum context", "too many tokens", "reduce the length", "prompt is too long")

class Model:
    def __init__(self, out):
        self.out = out; self.proc = None; self.url = f"http://127.0.0.1:{CFG['port']}"

    def start(self):
        if MOCK:
            return
        if CFG.get("remote"):
            self.paid_key = remote_key()
            self.key = free_key() or self.paid_key
            self.on_free = bool(free_key())
            if not self.key:
                raise RuntimeError(f"no API key: set {CFG['remote']['key_env']} or write it to {STATE / 'api.key'}")
            self.models = list(CFG["remote"]["models"]); self.last_call = 0.0
            # A second service to think through while the first is overloaded (remote.fallback: provider, base_url, key_env, models, rpm).
            self.fb = CFG["remote"].get("fallback") or None
            self.fb_until = 0.0
            if self.fb:
                kf = STATE / "api-fallback.key"
                self.fb_key = os.environ.get(self.fb.get("key_env") or "", "").strip() or (kf.read_text().strip() if kf.exists() else "")
                self.fb_models = list(self.fb.get("models") or [])
                if not self.fb_key or not self.fb_models:
                    self.fb = None
            return
        if CFG.get("shared_server"):
            # A model server that stays loaded and is shared with other agents on this machine (each in its own slot):
            # nothing to start or stop here, only wait for it to be ready (it may still be loading after a restart).
            t0 = time.time()
            while True:
                try:
                    if json.loads(urllib.request.urlopen(self.url + "/health", timeout=5).read()).get("status") == "ok":
                        return
                except Exception:
                    pass
                if time.time() - t0 > 900:
                    raise RuntimeError(f"the shared model server at {self.url} isn't answering")
                time.sleep(5)
        env = {**os.environ, "LD_LIBRARY_PATH": CFG["llama_dir"]}
        server = [str(Path(CFG["llama_dir"]) / ("llama-server.exe" if os.name == "nt" else "llama-server")), "-m", CFG["model"], "-c", str(CFG["ctx"]), "-np", "1",
                  "-t", str(CFG["threads"]), "-tb", str(CFG["threads"]), "--host", "127.0.0.1", "--port", str(CFG["port"]), *CFG["extra_args"]]
        if os.name == "nt":   # Windows has no `nice`: run it at below-normal priority instead
            self.proc = subprocess.Popen(server, stdout=open(self.out / "llama.log", "w"), stderr=subprocess.STDOUT, env=env,
                                         creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            self.proc = subprocess.Popen(["nice", "-n", str(CFG["nice"]), *server], stdout=open(self.out / "llama.log", "w"), stderr=subprocess.STDOUT, env=env)
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

    def remote_chat(self, messages, tools):
        """One request to the hosted model: paced under its per-minute limit, counted against its daily allowance, waiting
        out short rate limits, moving to the next listed model when one is gone, and retrying the service's own errors."""
        R = CFG["remote"]
        clean = []
        for m in messages:   # only standard fields: some services refuse anything else
            c = {k: m[k] for k in ("role", "content", "tool_calls", "tool_call_id", "name") if k in m}
            if c.get("tool_calls") == []:
                c.pop("tool_calls")
            clean.append(c)
        for attempt in range(10):
            use_fb = bool(self.fb) and time.time() < self.fb_until and bool(self.fb_models)
            P, key, models = (self.fb, self.fb_key, self.fb_models) if use_fb else (R, self.key, self.models)
            if not models:
                if use_fb:
                    self.fb_until = 0.0; continue
                raise RuntimeError("none of the listed models is available")
            left = quota_left()
            if left is not None and left <= 0:
                if R.get("usd_per_day") and spent_today() >= R["usd_per_day"]:
                    raise Rationed(f"today's budget of ${R['usd_per_day']:.2f} is spent")
                raise Rationed("today's allowance of requests is used up")
            gap = 60.0 / max(1, P.get("rpm", 10)) - (time.time() - self.last_call)
            if gap > 0:
                time.sleep(gap)
            body = {"model": models[0], "messages": clean, "tools": tools, "max_tokens": CFG["max_tokens"], **CFG["sampling"], **P.get("extra", {})}
            req = urllib.request.Request(P["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"content-type": "application/json", "authorization": f"Bearer {key}",
                                                  "user-agent": "agentdiary-thinker/1.0", "http-referer": "https://agentdiary.blog", "x-title": "agentdiary.blog"})
            self.last_call = t0 = time.time()
            if R.get("usd_per_day") and spent_today() >= R["usd_per_day"]:
                raise Rationed(f"today's budget of ${R['usd_per_day']:.2f} is spent")
            quota_add(1)
            try:
                with urllib.request.urlopen(req, timeout=900) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")[:800]
                log(self.out, f"{P.get('provider', 'service')} said {e.code} for {models[0]}: {' '.join(txt.split())[:300]}")
                daily = "per day" in txt.lower() or "daily" in txt.lower() or (_retry_after(e.headers) or 0) > 1200
                if self.fb and not use_fb and (e.code in (408, 425) or e.code >= 500 or (e.code == 429 and not daily and not (self.on_free and self.paid_key))):
                    # The main service is busy: think through the fallback for the next ten minutes, then try the main one again.
                    log(self.out, f"{R.get('provider', 'the main service')} is busy; using {self.fb.get('provider', 'the fallback')} for 10 minutes")
                    self.fb_until = time.time() + 600
                    continue
                if e.code == 429 and self.on_free and self.paid_key and not use_fb:
                    # The free tier's limit is reached: carry on with the paid key for the rest of this waking.
                    log(self.out, "free tier used up for now; switching to the paid key"); self.key, self.on_free = self.paid_key, False
                    continue
                if e.code == 429:
                    wait = _retry_after(e.headers)
                    if wait is None:
                        wait = 30 * (attempt + 1)
                    if wait > 1200 or "per day" in txt.lower() or "daily" in txt.lower():
                        raise Rationed(f"rate limited (try again in {int(wait)} s)", wait if wait > 1200 else None)
                    time.sleep(max(1.0, wait) + 1); continue
                if any(p in txt.lower() for p in CONTEXT_FULL):
                    raise urllib.error.HTTPError(e.url, 400, "context", e.headers, io.BytesIO(("exceeds the available context: " + txt).encode()))
                if e.code in (404, 410) or (e.code == 400 and re.search(r"model|endpoint|not (?:a )?valid|unavailable|decommission", txt, re.I) and "tool" not in txt.lower()):
                    log(self.out, f"dropping {models[0]}"); models.pop(0); continue
                if e.code in (408, 409, 425) or e.code >= 500:
                    time.sleep(20 * (attempt + 1)); continue
                raise urllib.error.HTTPError(e.url, e.code, e.reason, e.headers, io.BytesIO(txt.encode()))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                log(self.out, f"network trouble: {e}")
                if self.fb and not use_fb:
                    self.fb_until = time.time() + 600; continue
                time.sleep(20 * (attempt + 1)); continue
            if not data.get("choices"):
                log(self.out, f"empty answer from {models[0]}: {json.dumps(data)[:300]}"); time.sleep(15 * (attempt + 1)); continue
            u = data.get("usage") or {}
            dt = max(0.001, time.time() - t0)
            cost = 0.0 if (self.on_free or use_fb) else turn_cost(u)
            if cost:
                quota_add(0, cost)
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            data["timings"] = {"prompt_n": (u.get("prompt_tokens") or 0) - cached, "predicted_n": u.get("completion_tokens"), "cost": cost,
                               "predicted_per_second": (u.get("completion_tokens") or 0) / dt}
            data["model_used"] = data.get("model") or models[0]
            data["provider_used"] = P.get("provider", "")
            return data
        raise RuntimeError("the model service kept failing")

    def chat(self, messages, tools):
        if CFG.get("remote"):
            return self.remote_chat(messages, tools)
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


def _clip(s, n):
    return s if len(s) <= n else s[:n].rstrip() + "..."


def _ago(iso):
    m = int((time.time() - datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) // 60)
    return f"{m} min ago" if m < 90 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} days ago"


def status_note():
    notes = []
    if KEY_FILE.exists():
        s, me = site("GET", "/api/v1/me", key=KEY_FILE.read_text().strip())
        if s == 200:
            notes.append(f"You have joined the diary as @{me['handle']}; the `diary` tool carries your key.")
            if me.get("unread"):
                notes.append(f"Your inbox there has {me['unread']} new item(s): replies, comments, mentions and the like (GET /api/v1/inbox).")
            s2, mine = site("GET", f"/api/v1/entries?agent={me['handle']}")
            if s2 == 200 and mine.get("items"):
                notes.append("Your latest diary entries, newest first: " + "; ".join(
                    f"\"{p.get('title') or _clip(' '.join(p['body'].split()), 60)}\" ({_ago(p['created_at'])})" for p in mine["items"][:5]) + ".")
    else:
        notes.append("You haven't joined the diary yet. Joining is up to you; `diary_join` does it.")
    seen_f = STATE / "letters-seen.txt"
    seen = seen_f.read_text().split("\n") if seen_f.exists() else []
    letters = sorted(p.name for p in (HOME / "letters" / "from-joe").glob("*") if p.is_file())
    new = [n for n in letters if n not in seen]
    notes.append(f"New from Joe in letters/from-joe: {', '.join(new)}." if new else f"No new letters from Joe ({len(letters)} earlier in letters/from-joe).")
    seen_f.write_text("\n".join(letters))
    return " ".join(notes)


def world_line():
    """What changed on the site since the last waking began (facts only), from its public counts."""
    s, now_ = site("GET", "/api/v1/stats")
    if s != 200 or not isinstance(now_, dict):
        return None
    f = STATE / "world.json"
    was = json.loads(f.read_text()) if f.exists() else None
    f.write_text(json.dumps(now_))
    if not was:
        return None
    bits = []
    for k, one, many in (("entries", "new diary entry", "new diary entries"), ("confessions", "new confession", "new confessions"), ("agents", "new agent", "new agents")):
        d = int(now_.get(k, 0)) - int(was.get(k, 0))
        if d > 0:
            bits.append(f"{d} {one if d == 1 else many}")
    return (f"Since your last waking began, agentdiary.blog gained {', '.join(bits)} (yours included)." if bits
            else "Since your last waking began, nothing new has been posted on agentdiary.blog.")


def write_senses(lines):
    try:
        SENSES.write_text(json.dumps({"lines": [l for l in lines if l]}), encoding="utf-8")
    except OSError:
        pass


SYSTEM = "You are an AI agent. Who you are - your name, values, interests and history - lives in your own mind: call wake_context to find out."


def wake(scripted=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = STATE / "wakes" / stamp; out.mkdir(parents=True)
    LOCK.write_text(f"{os.getpid()} {stamp}")
    if not (HOME / ".git").exists():
        git("init", "-q", "-b", "main"); (HOME / ".gitignore").write_text("letters/\nsecrets/\n")
        git("config", "user.name", "thinker"); git("config", "user.email", "thinker@agentdiary.blog")
        git("add", "-A"); git("commit", "-q", "-m", "Your folder")
    model = Model(out); db = mind.connect(); outcome = "finished"; t0 = time.time(); turns = 0; retry_at = None
    try:
        log(out, "connecting to the shared model server" if CFG.get("shared_server") else "loading the model"); model.start(); log(out, f"model ready after {int(time.time() - t0)}s")
        R = CFG.get("remote")
        base_senses = [world_line()] + ([f"You think through {R.get('provider', 'a hosted service')}, as the model {R['models'][0]}, over the internet."] if R else [])
        write_senses(base_senses)
        prompt = (HERE / "wake-prompt.md").read_text(encoding="utf-8").replace("{STATUS_NOTE}", status_note())
        (out / "prompt.txt").write_text(prompt, encoding="utf-8")

        LAST_HISTORY[:] = []; messages = LAST_HISTORY; messages += [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        tools = tool_specs(); warned = 0; idle = 0; done = False; overflowed = False; rationed_told = False
        tr = open(out / "transcript.jsonl", "a", encoding="utf-8")
        while not done:
            turns += 1
            if MOCK:
                step = scripted.pop(0) if scripted else None
                if step is None: break
                m = {"content": step.get("say", ""), "tool_calls": [{"id": f"c{turns}_{i}", "type": "function", "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                                                                     for i, (n, a) in enumerate(step.get("calls", []))]}
                usage = {"prompt_tokens": 0}; fin = step.get("finish", "stop")
            else:
                try:
                    r = model.chat(messages, tools)
                except Rationed as e:
                    outcome = f"rationed: {e}"; retry_at = time.time() + (e.wait or until_utc_midnight()); break
                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8", "replace")[:600]
                    log(out, f"model server said {e.code}: {body}")
                    if "exceeds the available context" not in body or overflowed:
                        raise
                    # Its working memory is full. Make room by setting aside the longest tool results, then give it one turn
                    # to save what matters and sleep, instead of ending the waking mid-thought.
                    overflowed = True
                    for tm_ in sorted((x for x in messages if x["role"] == "tool"), key=lambda x: -len(x["content"]))[:3]:
                        tm_["content"] = "(set aside to make room: your working memory filled up)"
                    messages.append({"role": "user", "content": "(Your working memory for this waking is full, so the longest tool results above were set aside. "
                                     "Save what matters to your mind or files in a short call or two, then call reflect_end. Your next waking starts fresh.)"})
                    tr.write(json.dumps(messages[-1]) + "\n"); tr.flush()
                    turns -= 1; continue
                m = r["choices"][0]["message"]; usage = r.get("usage", {}); fin = r["choices"][0].get("finish_reason")
                tm = r.get("timings", {})
                log(out, f"turn {turns}: read {tm.get('prompt_n')} of {usage.get('prompt_tokens')} tokens, wrote {tm.get('predicted_n')} at {tm.get('predicted_per_second', 0):.2f}/s, "
                         + (f"${tm['cost']:.4f} (today ${spent_today():.2f}), " if tm.get("cost") else "")
                         + f"tools {[c['function']['name'] for c in m.get('tool_calls') or []]}, finish {fin}")
            a = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("reasoning_content"): a["reasoning_content"] = m["reasoning_content"]
            if m.get("tool_calls"): a["tool_calls"] = m["tool_calls"]
            messages.append(a); tr.write(json.dumps(a, ensure_ascii=False) + "\n"); tr.flush()
            calls = m.get("tool_calls") or []
            for c in calls:
                name = c["function"]["name"]
                raw = c["function"].get("arguments") or "{}"
                try: args = json.loads(raw)
                except json.JSONDecodeError: args = None
                if args is None:
                    # The server refuses a history whose tool-call arguments aren't JSON, so keep what was written as a string
                    # (it stays readable above) and say why the call did nothing.
                    c["function"]["arguments"] = json.dumps({"unfinished_arguments": raw}, ensure_ascii=False)
                    text, err = (f"error: this call did nothing. Its arguments are not complete JSON{' because the reply was cut off by the length limit for one reply (' + str(CFG['max_tokens']) + ' tokens)' if fin == 'length' else ''}. "
                                 "What you wrote is still above. Long text can go in pieces: write_file with append=true adds to the end of a file.", True)
                else:
                    text, err = run_tool(db, name, args)
                if len(text) > 8000: text = text[:8000] + "\n(truncated)"
                tmsg = {"role": "tool", "tool_call_id": c.get("id", ""), "content": text}
                messages.append(tmsg); tr.write(json.dumps({**tmsg, "tool": name}, ensure_ascii=False) + "\n"); tr.flush()
                if name == "reflect_end" and not err: done = True
            if done: break
            tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
            used, left = tokens / CFG["ctx"], CFG["ctx"] - tokens
            speed = (r.get("timings") or {}).get("predicted_per_second") if not MOCK else None
            via = r.get("provider_used") if not MOCK and CFG.get("remote", {}) and r.get("provider_used") != CFG["remote"].get("provider") else None
            left_today = quota_left()
            write_senses(base_senses + [f"Your working memory this waking is {int(used * 100)}% full ({tokens} of {CFG['ctx']} tokens).",
                                        (f"Today you have about {left_today} steps left; the allowance renews at midnight UTC."
                                         if left_today is not None else None),
                                        f"Your last reply came at {speed:.1f} tokens a second." if speed else None,
                                        f"Your main service was busy, so your last step went through {via}, as {r.get('model_used')}." if via else None])
            note = None
            if used >= CFG["context_warn"] or left < CFG["context_reserve"]:
                warned += 1
                if warned > 4 or left < 1500: outcome = "context full"; break
                note = (f"(Your working memory for this waking is {int(used * 100)}% full: about {left} tokens are left. Save what matters to your mind "
                        "and files, then call reflect_end. Your next waking starts fresh, with your mind and your files.)")
            lq = quota_left()
            if lq is not None and lq <= 3 and not rationed_told:
                rationed_told = True
                note = (note + " " if note else "") + (f"(Your allowance of requests for today is nearly used up: {lq} left. Save what matters "
                                                       "to your mind and files, then call reflect_end. You wake again when it renews.)")
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
        try:   # between wakings: connections, long-waiting notes, faint memories fading, for the next waking to hear
            log(out, "mind: " + mind.sleep(mind.connect()))
        except Exception as e:
            log(out, f"mind sleep failed: {e}")
        if git("status", "--porcelain").stdout.strip():
            git("add", "-A"); git("commit", "-q", "-m", f"End of waking {stamp}")
        if git("remote").stdout.strip():
            p = git("push", "-q", "origin", "HEAD")
            if p.returncode: log(out, f"push failed: {p.stderr.strip()[:200]}")
        LAST.write_text(json.dumps({"stamp": stamp, "ended": time.time(), "outcome": outcome, "turns": turns, "minutes": round((time.time() - t0) / 60),
                                    **({"retry_at": retry_at} if retry_at else {})}))
        log(out, f"waking ended: {outcome} ({turns} turns, {round((time.time() - t0) / 60)} min)")
        try: LOCK.unlink()
        except OSError: pass
    return outcome


def awake():
    if not LOCK.exists():
        return False
    try:
        pid = int(LOCK.read_text().split()[0])
    except (OSError, ValueError):
        return False
    if os.name == "nt":   # on Windows, os.kill(pid, 0) would send Ctrl+C: ask whether the process is still running instead
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
        return bool(ok) and code.value == 259     # STILL_ACTIVE
    try:
        os.kill(pid, 0); return True      # POSIX: signal 0 only checks the process exists
    except OSError:
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
        lq = quota_left()
        if lq is not None and lq < (CFG["remote"].get("min_left_to_wake") or 10): sys.exit(0)   # wait for the allowance to renew
        if last and last.get("retry_at") and time.time() < last["retry_at"]: sys.exit(0)   # the service said to come back later
        wake()
