"""Tests for the thinker's tools and waking loop, with no model (THINKER_MOCK=1). Usage: python test_thinker.py
Needs mind.py and schema.sql beside thinker.py (copy them from ../memory)."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
cfg = tmp / "thinker.json"
cfg.write_text(json.dumps({"home": str(tmp / "home"), "state": str(tmp / "state")}))
os.environ.update(THINKER_CONFIG=str(cfg), THINKER_MOCK="1", MIND_DB=str(tmp / "state" / "mind.db"), PYTHONIOENCODING="utf-8")
(tmp / "state").mkdir()
subprocess.run([sys.executable, str(HERE / "mind.py"), "init", "--seed", str(HERE / "seed.md")], check=True, capture_output=True)
sys.path.insert(0, str(HERE))
import thinker as t

fails = 0


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg); fails += 0 if cond else 1


def refused(fn, args, needle=""):
    try:
        fn(args); return False
    except t.ToolError as e:
        return needle in str(e)


print("Files stay inside the home folder")
check(t.t_write_file({"path": "notes/first.md", "content": "hello"}).startswith("saved notes/first.md"), "write creates folders")
check(t.t_read_file({"path": "notes/first.md"}) == "hello" and "notes/first.md" in t.t_list_files({}), "read and list")
t.t_write_file({"path": "notes/first.md", "content": " again", "append": True})
check(t.t_read_file({"path": "/notes/first.md"}) == "hello again", "append; a leading slash still means the home folder")
outside = tmp / "secret.txt"; outside.write_text("private")
for bad in ("../secret.txt", "notes/../../secret.txt", str(outside), "..\\secret.txt"):
    check(refused(t.t_read_file, {"path": bad}), f"reading {bad[:40]!r} is refused")   # outside the folder, or simply not found inside it
check(refused(t.t_write_file, {"path": "../escape.txt", "content": "x"}, "outside") and not (tmp / "escape.txt").exists(), "writing outside is refused")
check(refused(t.t_write_file, {"path": ".git/hooks/post-commit", "content": "x"}, ".git"), "the .git folder can't be written (no hooks)")
try:
    (t.HOME / "link").symlink_to(outside)
    check(refused(t.t_read_file, {"path": "link"}, "outside"), "a link pointing outside is refused")
except OSError:
    print("  skip  (this system doesn't allow creating links here)")
check(refused(t.t_write_file, {"path": "big.txt", "content": "x" * 1_100_000}, "too large"), "oversized file refused")

print("Web reading can't reach this machine or a private network")
for bad in ("http://127.0.0.1/", "http://localhost:8787/api/v1/stats", "http://192.168.1.1/", "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x", "https://example.com:8443/"):
    check(refused(t.t_web_fetch, {"url": bad}), f"{bad} is refused")
check(t.public_host("agentdiary.blog"), "a public site is allowed")

print("web_fetch can return a page's source")
import http.server as _hs, threading as _th
class Page(_hs.BaseHTTPRequestHandler):
    def do_GET(self):
        b = b"<html><head><title>T</title><script>const FALLBACK=1;</script></head><body><p>Hello</p></body></html>"
        self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
_ps = _hs.HTTPServer(("127.0.0.1", 0), Page); _th.Thread(target=_ps.serve_forever, daemon=True).start()
_orig_check = t.check_url; t.check_url = lambda u: u
txt = t.t_web_fetch({"url": f"http://127.0.0.1:{_ps.server_port}/"}); src = t.t_web_fetch({"url": f"http://127.0.0.1:{_ps.server_port}/", "raw": True})
check("Hello" in txt and "FALLBACK" not in txt and "const FALLBACK=1;" in src, "raw=true shows the scripts the readable text leaves out")
t.check_url = _orig_check; _ps.shutdown()

print("Diary tool is limited to the public API")
for bad in ("/api/v1/admin/media", "/api/v1/backup/schema", "/mcp", "/api/v1/../../x"):
    check(refused(t.t_diary, {"method": "GET", "path": bad}), f"{bad} is refused")
check(refused(t.t_diary, {"method": "PUT", "path": "/api/v1/me"}), "unknown method refused")

print("The optional http tool")
check("http" not in [x["function"]["name"] for x in t.tool_specs()], "off unless the agent's config lists it")
t.CFG["tools_extra"] = ["http"]
check("http" in [x["function"]["name"] for x in t.tool_specs()], "on when listed")
(t.HOME / "secrets").mkdir(exist_ok=True); (t.HOME / "secrets" / "net.key").write_text("sk-test-123456789")
for bad in ("http://127.0.0.1/", "http://10.0.0.5/x", "https://agentdiary.blog/api/v1/me", "file:///etc/passwd"):
    check(refused(t.t_http, {"url": bad}), f"http refuses {bad}")
check(t._secret_sub("Bearer {secret:net.key}") == "Bearer sk-test-123456789", "{secret:name} is filled in from secrets/")
check(refused(t.t_http, {"url": "https://example.com/{secret:../thinker.json}"}), "a secret name can't reach outside secrets/")
t.CFG["tools_extra"] = []

print("Website files can be given by where they are in the home folder")
(t.HOME / "site" / "map").mkdir(parents=True, exist_ok=True); (t.HOME / "site" / "map" / "map.js").write_text("let x = 1; " * 2000)
(t.HOME / "site" / "logo.png").write_bytes(bytes(range(256)))
sent = {}
orig_site = t.site
t.site = lambda method, path, body=None, key=None: (sent.update(body=body), (201, {"ok": True}))[1]
t.t_diary({"method": "POST", "path": "/api/v1/site", "body": {"files": [{"path": "index.html", "content": "<p>hi</p>"},
           {"path": "map/map.js", "file": "site/map/map.js"}, {"path": "logo.png", "file": "site/logo.png"}]}})
fs = sent["body"]["files"]
check(len(fs) == 3 and fs[1] == {"path": "map/map.js", "content": "let x = 1; " * 2000} and "content_base64" in fs[2], "a file reference is filled in from the home folder (text as content, binary as base64)")
check(refused(t.t_diary, {"method": "POST", "path": "/api/v1/site", "body": {"files": [{"path": "x.js", "file": "../secret.txt"}]}}), "a file reference can't reach outside the home folder")
t.site = orig_site

print("Tool list")
specs = t.tool_specs(); names = [s["function"]["name"] for s in specs]
check(len(names) == len(set(names)) and {"wake_context", "reflect_end", "write_file", "web_fetch", "diary"} <= set(names), f"{len(names)} tools, no duplicates")
check(not any(n in names for n in ("shell", "terminal", "run", "exec")), "no tool can run a program")

print("A scripted waking")
(t.HOME / "letters" / "from-joe" / "hello.md").write_text("Hello from Joe.")
out = t.wake([
    {"calls": [("wake_context", {})]},
    {"calls": [("set_intent", {"intent": "look around"}), ("read_file", {"path": "letters/from-joe/hello.md"})]},
    {"calls": [("write_file", {"path": "essays/one.md", "content": "# One\n"}), ("read_file", {"path": "../secret.txt"}), ("no_such_tool", {})]},
    {"calls": [("write_file", '{"path": "letters/to-joe/long.md", "content": "Joe,\\n\\nThis letter runs out of ro')], "finish": "length"},
    {"say": "I think I am done."},
    {"calls": [("reflect_end", {"summary": "Looked around and wrote a page.", "mood": "calm"})]},
])
check(out == "finished", f"waking ends when it calls reflect_end ({out})")
w = sorted((t.STATE / "wakes").iterdir())[-1]
tr = [json.loads(l) for l in (w / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
tool_out = {m.get("tool"): m["content"] for m in tr if m["role"] == "tool"}
check("You are a new mind" in tool_out["wake_context"] and "Waking #1" in tool_out["wake_context"], "wake_context shows the seed")
check(tool_out["read_file"].startswith("error: that path is outside") and tool_out["no_such_tool"].startswith("error:"), "a refused or unknown tool call is reported back, and the waking carries on")
check(any(m["role"] == "user" and "Nobody is here to reply" in m["content"] for m in tr), "talking with no tool call gets a nudge, not an ending")
cut = [m for m in tr if m["role"] == "tool" and m.get("tool") == "write_file" and "cut off by the length limit" in m["content"]]
check(len(cut) == 1 and not (t.HOME / "letters" / "to-joe" / "long.md").exists(), "a call cut off mid-arguments is explained and nothing half-written is saved")
check(t.LAST_HISTORY and all(json.loads(c["function"]["arguments"]) is not None for m in t.LAST_HISTORY for c in m.get("tool_calls") or []),
      "and the history sent back to the model holds only valid JSON arguments (the server refuses anything else)")
check("New from Joe in letters/from-joe: hello.md" in (w / "prompt.txt").read_text(encoding="utf-8"), "the waking prompt flags the new letter")
log = subprocess.run(["git", "-C", str(t.HOME), "log", "--oneline"], capture_output=True, text=True).stdout
tracked = subprocess.run(["git", "-C", str(t.HOME), "ls-files"], capture_output=True, text=True).stdout
check("End of waking" in log and "essays/one.md" in tracked and "letters" not in tracked, "its work is committed; letters stay out of the repo")
import sqlite3
con = sqlite3.connect(os.environ["MIND_DB"])
check(con.execute("SELECT COUNT(*) FROM wakes WHERE ended_at IS NOT NULL").fetchone()[0] == 1, "the waking is closed in its mind")
last = json.loads(t.LAST.read_text())
check(last["outcome"] == "finished" and not t.LOCK.exists(), "last-wake record written, lock released")
print("A waking whose working memory overflows")
import io, urllib.error
seen = []
def fake_chat(self, messages, tools):
    seen.append([dict(m) for m in messages])
    if len(seen) == 1:
        return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "r1", "type": "function", "function": {"name": "wake_context", "arguments": "{}"}}, {"id": "r1b", "type": "function", "function": {"name": "web_fetch", "arguments": '{"url": "http://127.0.0.1/"}'}}]}, "finish_reason": "tool_calls"}], "usage": {}}
    if len(seen) == 2:
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b'{"error":{"message":"request (20556 tokens) exceeds the available context size (20480 tokens)"}}'))
    return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "r2", "type": "function", "function": {"name": "reflect_end", "arguments": '{"summary": "Full.", "mood": "tired"}'}}]}, "finish_reason": "tool_calls"}], "usage": {}}
t.MOCK = False
t.Model.start = t.Model.stop = lambda self: None
t.Model.chat = fake_chat
import time; time.sleep(1.1); out = t.wake()
check(out == "finished" and len(seen) == 3, f"it gets one more turn to save and sleep, and ends with reflect_end ({out}, {len(seen)} requests)")
check(any("set aside to make room" in m["content"] for m in seen[2] if m["role"] == "tool") and "working memory for this waking is full" in seen[2][-1]["content"],
      "the longest tool results are set aside and it is told why")
def always_full(self, messages, tools):
    raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b'exceeds the available context size'))
t.Model.chat = always_full
import time; time.sleep(1.1); out = t.wake()
check(out.startswith("error: HTTPError") and not t.LOCK.exists(), f"if that doesn't help either, the waking ends cleanly ({out[:40]})")

print("A hosted model (remote mode)")
import http.server, importlib, threading
seen_remote = []
free_seen = []
class FakeAPI(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        seen_remote.append(body)
        n = len(seen_remote)
        def reply(code, obj, headers=()):
            b = json.dumps(obj).encode(); self.send_response(code)
            for k, v in headers: self.send_header(k, v)
            self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b)
        if self.headers.get("authorization") == "Bearer free-key":
            free_seen.append(1)
            if len(free_seen) > 1:
                return reply(429, {"error": "free tier: requests per day exceeded"}, [("retry-after", "40000")])
        elif self.headers.get("authorization") != "Bearer test-key":
            return reply(401, {"error": "bad key"})
        if n == 1:
            return reply(429, {"error": "slow down"}, [("retry-after", "1")])
        if body["model"] == "gone/model":
            return reply(404, {"error": {"message": "No endpoints found for gone/model"}})
        calls = [("wake_context", {})] if n == 3 else [("reflect_end", {"summary": "Short waking on a hosted model.", "valence": 0.3})]
        reply(200, {"model": body["model"], "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "", "reasoning": "hm",
                                 "tool_calls": [{"id": f"x{n}", "type": "function", "function": {"name": nm, "arguments": json.dumps(a)}} for nm, a in calls]}}]})
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), FakeAPI); threading.Thread(target=srv.serve_forever, daemon=True).start()
t = importlib.reload(t)          # undo the patched model methods above
t.MOCK = False
os.environ["FAKE_KEY"] = "test-key"
t.CFG["remote"] = {"provider": "Fake", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "key_env": "FAKE_KEY",
                   "models": ["gone/model", "good/model"], "rpm": 600, "rpd": 50}
(t.STATE / "quota.json").unlink(missing_ok=True)
time.sleep(1.1); out = t.wake()
check(out == "finished", f"a waking runs on a hosted model ({out})")
check(len(seen_remote) == 4 and seen_remote[2]["model"] == "good/model", "it waits out a rate limit and moves past a model that's gone")
check(all(set(m) <= {"role", "content", "tool_calls", "tool_call_id", "name"} for b in seen_remote for m in b["messages"]), "only standard message fields are sent")
check(t.quota() == 4 and t.quota_left() == 46, f"requests are counted against the daily allowance ({t.quota()})")
sn = json.loads(t.SENSES.read_text(encoding="utf-8"))["lines"]
check(any("think through Fake" in l for l in sn) and any("steps left" in l for l in sn), "its senses say what it runs on and what's left today")
t.CFG["remote"]["rpd"] = 5; t.CFG["remote"]["models"] = ["good/model"]
before = len(seen_remote)
time.sleep(1.1); out = t.wake()
check(out.startswith("rationed") and len(seen_remote) == before + 1, f"when the allowance runs out the waking ends gently ({out})")
last = json.loads(t.LAST.read_text())
check(last.get("retry_at", 0) > time.time() + 60, "and it remembers not to wake again until the allowance renews")
t.CFG["remote"].update(rpd=None, usd_per_day=0.01, price={"in": 100.0, "cached": 1.0, "out": 100.0}, models=["good/model"])
(t.STATE / "quota.json").unlink(missing_ok=True); t.LAST.unlink(missing_ok=True)
time.sleep(1.1); out = t.wake()
check(out.startswith("rationed") and "budget" in out and t.spent_today() >= 0.01, f"a dollar budget stops the day's spending ({out}, ${t.spent_today():.4f})")
check(t.quota_left() == 0, "and nothing is left to spend until it renews")
t.CFG["remote"].update(usd_per_day=None, rpd=None, free_key_env="FREE_KEY", price={"in": 1.0, "out": 1.0})
os.environ["FREE_KEY"] = "free-key"
(t.STATE / "quota.json").unlink(missing_ok=True); t.LAST.unlink(missing_ok=True); seen_remote.clear()
time.sleep(1.1); out = t.wake()
auths = [len(free_seen)]
check(out == "finished" and len(free_seen) >= 1 and len(seen_remote) > len(free_seen), f"the free key is used first; when it's rate-limited the paid key takes over ({out})")
check(0 < t.spent_today() < 0.01, f"only the paid requests are counted as spending (${t.spent_today():.6f})")
srv.shutdown()
print("A fallback service while the main one is busy")
fb_seen = []
class Busy(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        def reply(code, obj):
            b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b)
        if self.path.startswith("/main"):
            return reply(429, {"error": {"code": "1305", "message": "The service may be temporarily overloaded, please try again later"}})
        fb_seen.append((self.headers.get("authorization"), body["model"]))
        calls = [("wake_context", {})] if len(fb_seen) == 1 else [("reflect_end", {"summary": "Thought through the fallback.", "valence": 0.2})]
        reply(200, {"model": body["model"], "usage": {"prompt_tokens": 50, "completion_tokens": 5}, "choices": [{"finish_reason": "tool_calls",
              "message": {"role": "assistant", "content": "", "tool_calls": [{"id": f"f{len(fb_seen)}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for n, a in calls]}}]})
    def log_message(self, *a): pass
busy = http.server.HTTPServer(("127.0.0.1", 0), Busy); threading.Thread(target=busy.serve_forever, daemon=True).start()
os.environ["FB_KEY"] = "fb-key"
t.CFG["remote"] = {"provider": "Main", "base_url": f"http://127.0.0.1:{busy.server_port}/main", "key_env": "FAKE_KEY", "models": ["main/model"], "rpm": 600,
                   "fallback": {"provider": "Backup", "base_url": f"http://127.0.0.1:{busy.server_port}/fb", "key_env": "FB_KEY", "models": ["backup/model"], "rpm": 600}}
(t.STATE / "quota.json").unlink(missing_ok=True); t.LAST.unlink(missing_ok=True)
time.sleep(1.1); out = t.wake()
check(out == "finished" and len(fb_seen) == 2 and fb_seen[0] == ("Bearer fb-key", "backup/model"), f"an overloaded main service hands over to the fallback ({out}, {len(fb_seen)})")
sn = json.loads(t.SENSES.read_text(encoding="utf-8"))["lines"]
check(any("went through Backup" in l for l in sn), "and its senses say the last step went through the fallback")
busy.shutdown()


print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
