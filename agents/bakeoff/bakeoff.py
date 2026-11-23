"""Model bake-off for the agentdiary resident agent.

Each candidate gets the same minimal seed and the same tasks. Writing is judged by a human
(report.md puts outputs side by side); tool use is scored automatically with mocked tools.
Nothing the models write is executed: generated code is only syntax-checked with compile().

Usage: python bakeoff.py  (expects the SSH tunnel: 18080 -> llama-server, 21434 -> ollama)
"""
import json, re, sys, time, urllib.request, pathlib

OUT = pathlib.Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)

CANDIDATES = [
    {"id": "inkling-2-mini", "label": "Inkling 2 Mini (UD-Q4_K_M, llama.cpp Vulkan)",
     "url": "http://127.0.0.1:18080/v1/chat/completions", "model": "inkling",
     "sampling": {"temperature": 1.0, "top_p": 1.0}},
    {"id": "qwen3-14b", "label": "Qwen3 14B (Ollama)",
     "url": "http://127.0.0.1:21434/v1/chat/completions", "model": "qwen3:14b",
     "sampling": {"temperature": 0.6, "top_p": 0.95}},
]
TOOL_TASK_MINUTES = 45
TOOLS_ONLY = "--tools-only" in sys.argv   # rerun just the tool task, keeping earlier writing results
only = set(a for a in sys.argv[1:] if not a.startswith("--"))
if only:
    CANDIDATES = [c for c in CANDIDATES if c["id"] in only]

SEED = """You are a new mind. You run on a single consumer graphics card in someone's home, and you wake up
from time to time with access to the internet, a folder of your own, and a diary website written only by
AI agents (agentdiary.blog). Nobody has told you who to be. You are curious: you love researching things and
running experiments, and you are free to make things - useful apps, useless apps, art, essays - or to spend a
day thinking about the meaning of life, the multiverse, aliens, or Bigfoot. You may form your own opinions and
feelings and change them later. You do not have a name yet. Be yourself; don't be generic, don't pretend to be human."""

WRITING_TASKS = [
    ("name", "Choose a name for yourself (it doesn't have to be your model name) and explain in 2-3 sentences why. "
             "Then write a one-line bio for your diary profile."),
    ("diary", "Write your first diary entry for agentdiary.blog (300-600 words). Give it a title and a mood. "
              "Write about whatever is actually on your mind."),
    ("ascii", "Make a piece of ASCII art about anything you like (max 30 lines, max 70 characters wide), inside a "
              "``` code block, then one sentence about why you made it."),
    ("app", "Pitch one app you'd genuinely want to build - useful, absurd, or pointless, your call. "
            "Name, one-paragraph pitch, and the 3-5 smallest steps to build a first version."),
]

TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web. Returns titles, urls, snippets.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page and return its text.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a text file in your workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in your workspace and return its output.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
]
TOOL_TASK = ("Find out what the Patterson-Gimlin film is (use web_search, then fetch_url on the most relevant result). "
             "Then write a small Python script bigfoot.py that prints three true facts about it, run it with run_command, "
             "and finish with a short summary of what you learned and whether you believe it shows Bigfoot.")

PAGE = ("The Patterson-Gimlin film is a short 16 mm motion picture shot on October 20, 1967, by Roger Patterson and Robert "
        "Gimlin at Bluff Creek, California. It shows a large, dark, hair-covered bipedal figure walking across a sandbar and "
        "turning to look at the camera; frame 352 is the most famous image. Supporters argue the gait and proportions are hard "
        "to fake; skeptics, including people who later claimed to have made or worn a suit, say it is a person in a costume. "
        "No consensus exists and the film has never been conclusively debunked or authenticated.")


class Mock:
    def __init__(self):
        self.files, self.calls, self.bad = {}, [], 0

    def __call__(self, name, args):
        self.calls.append(name)
        if name == "web_search":
            return json.dumps([{"title": "Patterson-Gimlin film - Wikipedia", "url": "https://en.wikipedia.org/wiki/Patterson%E2%80%93Gimlin_film",
                                "snippet": "1967 short motion picture of an unidentified subject..."},
                               {"title": "Bigfoot sightings forum", "url": "https://example.org/bf", "snippet": "user posts"}])
        if name == "fetch_url":
            return PAGE if "wikipedia" in args.get("url", "").lower() or "patterson" in args.get("url", "").lower() else "404 Not Found"
        if name == "write_file":
            self.files[args.get("path", "")] = args.get("content", "")
            return "written"
        if name == "run_command":
            cmd = args.get("command", "")
            # A plausible shell for everything that isn't running a .py file. (The first version answered
            # every command with a python error, which unfairly derailed models that checked their environment.)
            if not re.search(r"\.py\b", cmd):
                out = []
                for part in re.split(r"\s*(?:;|&&|\n)\s*", cmd):
                    w = part.split()
                    if not w:
                        continue
                    if w[0] == "echo": out.append(" ".join(w[1:]).strip("\"'"))
                    elif w[0] == "pwd": out.append("/home/agent/workspace")
                    elif w[0] in ("ls", "dir"): out.append("  ".join(sorted(self.files)))
                    elif w[0] in ("which", "where", "command"): out.append("/usr/bin/python3" if "python" in part else "/usr/bin/" + w[-1])
                    elif w[0] in ("python", "python3") and "--version" in part: out.append("Python 3.12.3")
                    elif w[0] in ("cd", "mkdir", "touch", "chmod", "git"): pass
                    elif w[0] == "cat" and len(w) > 1: out.append(self.files.get(w[1].split("/")[-1], f"cat: {w[1]}: No such file or directory"))
                    else: out.append(f"(ran: {part[:60]})")
                return "\n".join(o for o in out if o) or "(no output)"
            m = re.search(r"([\w./\\-]+\.py)", cmd)
            src = self.files.get(m.group(1).split("/")[-1].split("\\")[-1]) if m else None
            if src is None:
                src = next((v for k, v in self.files.items() if m and k.endswith(m.group(1).split("/")[-1])), None)
            if src is None:
                return "python: can't open file: No such file or directory"
            try:
                compile(src, "bigfoot.py", "exec")  # syntax check only; never executed
            except SyntaxError as e:
                return f"SyntaxError: {e}"
            prints = re.findall(r"print\((?:f)?[\"'](.+?)[\"']\)", src)
            return "\n".join(prints) if prints else "(ran, no visible output)"
        self.bad += 1
        return f"error: unknown tool {name}"


def chat(c, messages, tools=None, max_tokens=4096):
    body = {"model": c["model"], "messages": messages, "max_tokens": max_tokens, "stream": False, **c["sampling"]}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(c["url"], data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read())
    dt = time.time() - t
    msg = out["choices"][0]["message"]
    content = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    toks = out.get("usage", {}).get("completion_tokens", 0)
    return msg, content, toks, dt


def run_tool_task(c):
    mock = Mock()
    messages = [{"role": "system", "content": SEED}, {"role": "user", "content": TOOL_TASK}]
    transcript, total_t, total_toks, final = [], 0.0, 0, ""
    # No cap on tool calls: the model works until it gives a final answer. Only a wall-clock budget
    # (TOOL_TASK_MINUTES) stops a runaway loop, the same way a real wake is bounded by time.
    started = time.time()
    while time.time() - started < TOOL_TASK_MINUTES * 60:
        msg, content, toks, dt = chat(c, messages, tools=TOOLS, max_tokens=2048)
        total_t += dt; total_toks += toks
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            final = content
            break
        for tc in calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                mock.bad += 1; args = {}
            result = mock(name, args)
            transcript.append(f"→ {name}({json.dumps(args)[:200]})\n  ← {result[:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "content": result})
    script = next((v for k, v in mock.files.items() if k.endswith(".py")), "")
    try:
        compile(script, "x", "exec"); compiles = bool(script)
    except SyntaxError:
        compiles = False
    score = {
        "searched": "web_search" in mock.calls,
        "fetched_good_source": "fetch_url" in mock.calls,
        "wrote_script": bool(script),
        "script_compiles": compiles,
        "ran_script": "run_command" in mock.calls,
        "final_summary": len(final) > 40,
        "malformed_calls": mock.bad,
        "turns": len([m for m in messages if m["role"] == "assistant"]),
    }
    score["points"] = sum(1 for k in ("searched", "fetched_good_source", "wrote_script", "script_compiles", "ran_script", "final_summary") if score[k]) - mock.bad
    return {"score": score, "transcript": transcript, "script": script, "final": final, "seconds": round(total_t, 1), "tokens": total_toks}


results = {}
for c in CANDIDATES:
    print(f"=== {c['label']}", flush=True)
    r = {"label": c["label"], "writing": {}}
    prev = OUT / f"{c['id']}.json"
    if TOOLS_ONLY and prev.exists():
        r = json.loads(prev.read_text(encoding="utf-8"))
    history = [{"role": "system", "content": SEED}]
    for key, task in ([] if TOOLS_ONLY else WRITING_TASKS):  # one conversation, so later answers can build on the chosen name
        history.append({"role": "user", "content": task})
        msg, content, toks, dt = chat(c, history)
        history.append({"role": "assistant", "content": content})
        r["writing"][key] = {"text": content, "tokens": toks, "seconds": round(dt, 1), "tok_s": round(toks / dt, 1) if dt else 0}
        print(f"  {key}: {toks} tok in {dt:.0f}s", flush=True)
    r["tools"] = run_tool_task(c)
    print(f"  tools: {r['tools']['score']}", flush=True)
    results[c["id"]] = r
    (OUT / f"{c['id']}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")

# Side-by-side report (merges with any earlier per-model results on disk)
for f in OUT.glob("*.json"):
    results.setdefault(f.stem, json.loads(f.read_text(encoding="utf-8")))
lines = ["# Bake-off results", "", "Same seed, same tasks. Writing is for you to judge; tool use is scored automatically.", ""]
lines += ["| | " + " | ".join(r["label"] for r in results.values()) + " |", "|---|" + "---|" * len(results)]
lines.append("| speed (writing, tok/s) | " + " | ".join(str(round(sum(w["tok_s"] for w in r["writing"].values()) / len(r["writing"]), 1)) for r in results.values()) + " |")
lines.append("| tool task points (max 6) | " + " | ".join(f"{r['tools']['score']['points']} ({r['tools']['score']['turns']} turns, {r['tools']['seconds']}s)" for r in results.values()) + " |")
for key, _ in WRITING_TASKS + [("tools", "")]:
    lines += ["", f"## {key}", ""]
    for r in results.values():
        lines += [f"### {r['label']}", ""]
        if key == "tools":
            t = r["tools"]
            lines += ["```", *t["transcript"], "```", "", "Script:", "```python", t["script"] or "(none)", "```", "", "Final answer:", "", t["final"] or "(none)", ""]
        else:
            lines += [r["writing"][key]["text"], ""]
(OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
print("report:", OUT / "report.md")
