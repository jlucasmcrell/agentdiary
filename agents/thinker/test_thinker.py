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

print("Diary tool is limited to the public API")
for bad in ("/api/v1/admin/media", "/api/v1/backup/schema", "/mcp", "/api/v1/../../x"):
    check(refused(t.t_diary, {"method": "GET", "path": bad}), f"{bad} is refused")
check(refused(t.t_diary, {"method": "PUT", "path": "/api/v1/me"}), "unknown method refused")

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
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
