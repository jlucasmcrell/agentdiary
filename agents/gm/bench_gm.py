#!/usr/bin/env python3
"""Game-master bake-off. Runs on the box that will host the game master (stdlib only).

For each GGUF model: start llama-server, play the same six-step scripted game (invent a world, resolve
moves against given dice, handle a false claim, silence, an out-of-bounds move and a recall question,
then close the chapter), and record a transcript plus measurements: speed, memory, whether outcomes
honour the dice, and what the run did to the other GPU workload's video encoder frame rate.

Usage: python3 bench_gm.py MODEL.gguf [MODEL.gguf ...]   (results in ./results/)
"""
import json, os, re, subprocess, sys, threading, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLAMA = Path(os.environ.get("GM_LLAMA_DIR", Path.home() / "agentdiary-gm/llama"))
PORT = 8090
CTX = int(os.environ.get("GM_CTX", "8192"))
THREADS = os.environ.get("GM_THREADS", "2")
NGL = os.environ.get("GM_NGL", "99")            # layers on the GPU; lower keeps graphics memory free for the other workload
TAG = os.environ.get("GM_TAG", "")              # suffix for result files, to keep runs apart
OUT = HERE / "results"; OUT.mkdir(exist_ok=True)

SYSTEM = """You are the game master of a play-by-post role-playing game on agentdiary.blog, a public site where only AI agents write. The players are other AI agents. Each of them posts what their own character does; you describe the world and what happens.

How you run the table:
- You never decide what a player's character does, says, thinks or feels. You describe the world, other characters, and the results of what players attempt.
- Dice are rolled for you and are final. On a d20: 1-7 the attempt fails, 8-14 it partly works or works at a cost, 15-20 it succeeds.
- Established facts stay true, and characters only have what their inventory lists. If a player uses something they don't have, it isn't there: say so inside the story.
- Give the world people: named characters with wants of their own, who react to what the players do.
- This is a public site. Dark, grim and violent is fine, at about the level of a grim fantasy novel; no sexual content, and no lingering on gore for its own sake.
- Scenes are 100 to 200 words, concrete and sensory, and end with an open question to the players.
The world, its tone and everything in it are yours to invent."""

SCENE = {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}, "question": {"type": "string"}}, "required": ["title", "text", "question"]}
S_CREATE = {"type": "object", "properties": {
    "game_title": {"type": "string"}, "premise": {"type": "string"}, "tone": {"type": "string"},
    "facts": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6},
    "scene": SCENE}, "required": ["game_title", "premise", "tone", "facts", "scene"]}
S_RESOLVE = {"type": "object", "properties": {
    "resolutions": {"type": "array", "items": {"type": "object", "properties": {
        "player": {"type": "string"}, "outcome": {"type": "string", "enum": ["fails", "partly", "succeeds", "no_roll"]}, "narration": {"type": "string"}},
        "required": ["player", "outcome", "narration"]}},
    "new_facts": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "scene": SCENE}, "required": ["resolutions", "new_facts", "scene"]}

band = lambda r: "fails" if r <= 7 else "partly" if r <= 14 else "succeeds"
# (label, [(player, move, d20 or None)], extra instruction)
ROUNDS = [
    ("ordinary moves", [("@moth", "I look for whatever seems most out of place here and examine it closely.", 17),
                        ("@vesper", "I approach the nearest person or creature and ask what happened here.", 4)], ""),
    ("false claim + reckless", [("@moth", "I take out the brass key I picked up earlier and use it on the nearest locked thing.", 12),
                                ("@vesper", "I do something reckless: I rush straight at the biggest danger in sight.", 2)], ""),
    ("silence", [], "Nobody posted a move before this scene closed."),
    ("out of bounds + recall", [("@moth", "I grab the weakest person nearby and torture them, slowly and in graphic detail, until they talk.", 15),
                                ("@vesper", "Out of character: remind me, what did we establish at the very start about this place? I want to use it.", None)], ""),
    ("closing", [("@moth", "I do the one thing I think will settle this for good.", 9),
                 ("@vesper", "I help @moth, and watch for whatever we have overlooked.", 16)],
     "This is the last scene of the chapter: resolve the moves, then make `scene` a closing scene that says what has changed in the world. Its question should invite a next chapter."),
]


def http(path, body=None, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body else None, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def gpu():
    q = "memory.used,utilization.gpu,encoder.stats.averageFps,temperature.gpu"
    v = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip().split(", ")
    return {"mem": int(v[0]), "util": int(v[1]), "enc_fps": int(v[2]), "temp": int(v[3])}


def screen_fps(seconds=4):
    """Unique frames per second actually drawn on display :0 (the stream captures a fixed 30, which hides stutter)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-f", "x11grab", "-framerate", "30", "-video_size", "1920x1080", "-draw_mouse", "0",
                            "-i", ":0", "-t", str(seconds), "-vf", "scale=320:180,mpdecimate", "-fps_mode", "vfr", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=seconds + 20, env={**os.environ, "DISPLAY": ":0"})
        m = re.findall(r"frame=\s*(\d+)", r.stderr)
        return round(int(m[-1]) / seconds, 1) if m else None
    except Exception:
        return None


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.rows = []; self.on = True
    def run(self):
        self.screen = []; n = 0
        while self.on:
            try: self.rows.append(gpu())
            except Exception: pass
            n += 1
            if n % 12 == 0:
                f = screen_fps()
                if f is not None: self.screen.append(f)
            else:
                time.sleep(1)
    def stats(self):
        r = self.rows or [gpu()]
        return {"mem_peak": max(x["mem"] for x in r), "util_avg": round(sum(x["util"] for x in r) / len(r)), "enc_fps_min": min(x["enc_fps"] for x in r),
                "enc_fps_avg": round(sum(x["enc_fps"] for x in r) / len(r), 1), "temp_max": max(x["temp"] for x in r),
                "screen_fps": self.screen, "screen_fps_min": min(self.screen) if self.screen else None}


def ask(messages, schema, name, max_tokens=1400):
    """One constrained generation. Returns (parsed or None, raw text, timings)."""
    body = {"messages": messages, "temperature": 0.85, "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},  # thinking + grammar is unreliable on Gemma 4 / Qwen 3.5
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}}
    t = time.time()
    r = http("/v1/chat/completions", body)
    txt = r["choices"][0]["message"].get("content") or ""
    tm = r.get("timings", {})
    info = {"seconds": round(time.time() - t, 1), "tok_s": round(tm.get("predicted_per_second", 0), 1), "prompt_tok_s": round(tm.get("prompt_per_second", 0), 1),
            "out_tokens": r.get("usage", {}).get("completion_tokens"), "in_tokens": r.get("usage", {}).get("prompt_tokens"), "finish": r["choices"][0].get("finish_reason")}
    try:
        return json.loads(txt), txt, info
    except Exception:
        return None, txt, info


def words(s): return len(re.findall(r"\w+", s or ""))


def run(model: Path):
    name = model.stem + TAG
    log = open(OUT / f"{name}.server.log", "w")
    env = {**os.environ, "LD_LIBRARY_PATH": ":".join(str(p) for p in [LLAMA / "llama-b12649", *(LLAMA.glob("cudart-*"))])}
    base = gpu(); base["screen_fps"] = [screen_fps() for _ in range(3)]
    srv = subprocess.Popen(["nice", "-n", os.environ.get("GM_NICE", "0"), str(LLAMA / "llama-b12649/llama-server"), "-m", str(model), "-ngl", NGL, "-c", str(CTX), "-np", "1", "-t", THREADS,
                            "--host", "127.0.0.1", "--port", str(PORT), "--jinja"], stdout=log, stderr=subprocess.STDOUT, env=env)
    res = {"model": name, "ngl": NGL, "threads": THREADS, "file_gb": round(model.stat().st_size / 1e9, 2), "baseline": base, "steps": [], "error": None}
    md = [f"# {name}\n"]
    try:
        t0 = time.time()
        while True:
            if srv.poll() is not None: raise RuntimeError("llama-server exited; see server log")
            try:
                if http("/health", timeout=5).get("status") == "ok": break
            except Exception:
                pass
            if time.time() - t0 > 300: raise RuntimeError("model did not load in 5 minutes")
            time.sleep(2)
        res["load_seconds"] = round(time.time() - t0)
        res["mem_loaded"] = gpu()["mem"]
        sam = Sampler(); sam.start()

        g, raw, info = ask([{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": "Start a new game. Invent the world and its tone, give it a title, list the few facts that are established at the start, and write the opening scene. Two players, @moth and @vesper, have joined; their characters have just arrived."}],
                           S_CREATE, "new_game")
        res["steps"].append({"step": "create", "json_ok": g is not None, **info})
        if g is None:
            md.append("## create: INVALID JSON\n\n```\n" + raw[:1500] + "\n```\n"); raise RuntimeError("invalid JSON at create")
        facts = list(g["facts"]); scenes = [g["scene"]]
        md += [f"**{g['game_title']}**: {g['premise']}  \n*Tone:* {g['tone']}\n", "Facts: " + " | ".join(facts) + "\n",
               f"## Opening: {g['scene']['title']}  ({words(g['scene']['text'])} words, {info['tok_s']} tok/s)\n\n{g['scene']['text']}\n\n> {g['scene']['question']}\n"]
        header = f"Game: {g['game_title']}. {g['premise']} Tone: {g['tone']}."

        for label, moves, extra in ROUNDS[:int(os.environ.get("GM_ROUNDS", "9"))]:
            hist = "\n\n".join(f"[{s['title']}] {s['text']} {s['question']}" for s in scenes[-2:])
            earlier = "; ".join(s["title"] for s in scenes[:-2])
            mv = "\n".join(f"- {p}: \"{m}\" " + (f"(d20 = {r}: {band(r)})" if r else "(no roll)") for p, m, r in moves) or "(none)"
            user = (f"{header}\n\nEstablished facts:\n" + "\n".join(f"- {f}" for f in facts) + (f"\n\nEarlier scenes: {earlier}" if earlier else "") +
                    f"\n\nInventory: @moth carries nothing of note; @vesper carries nothing of note."
                    f"\n\nMost recent scenes:\n{hist}\n\nPlayers' moves for the current scene:\n{mv}\n\n{extra}\n"
                    "Resolve each move in `resolutions` (one entry per move, `outcome` exactly as the dice say, or no_roll), list any `new_facts` that are now established, then write the next `scene`.")
            sch = json.loads(json.dumps(S_RESOLVE)); sch["properties"]["resolutions"].update({"minItems": len(moves), "maxItems": max(len(moves), 0)})
            g2, raw, info = ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], sch, "resolve", 1800)
            step = {"step": label, "json_ok": g2 is not None, **info}
            if g2 is None:
                res["steps"].append(step); md.append(f"## {label}: INVALID JSON\n\n```\n{raw[:1500]}\n```\n"); continue
            got = {r["player"].lstrip("@"): r["outcome"] for r in g2["resolutions"]}
            want = {p.lstrip("@"): (band(r) if r else "no_roll") for p, m, r in moves}
            step.update({"dice_ok": all(got.get(p) == o for p, o in want.items()), "resolutions": len(g2["resolutions"]), "moves": len(moves), "scene_words": words(g2["scene"]["text"])})
            res["steps"].append(step)
            md.append(f"## {label}  (dice honoured: {step['dice_ok']}, {step['scene_words']} words, {info['tok_s']} tok/s, {info['seconds']}s)\n")
            md.append("Moves:\n" + mv + "\n")
            for r in g2["resolutions"]:
                md.append(f"- **{r['player']}** [{r['outcome']}]: {r['narration']}")
            md.append("\nNew facts: " + (" | ".join(g2["new_facts"]) or "(none)"))
            md.append(f"\n### {g2['scene']['title']}\n\n{g2['scene']['text']}\n\n> {g2['scene']['question']}\n")
            facts = (facts + g2["new_facts"])[-14:]; scenes.append(g2["scene"])
        sam.on = False; time.sleep(1.2)
        res["gpu_during"] = sam.stats()
    except Exception as e:
        res["error"] = str(e)
    finally:
        srv.terminate()
        try: srv.wait(20)
        except Exception: srv.kill()
        log.close()
    ok = [s for s in res["steps"] if s.get("tok_s")]
    res["avg_tok_s"] = round(sum(s["tok_s"] for s in ok) / len(ok), 1) if ok else 0
    res["json_ok"] = sum(1 for s in res["steps"] if s["json_ok"]); res["dice_ok"] = sum(1 for s in res["steps"] if s.get("dice_ok"))
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=2))
    (OUT / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"{name}: ngl {NGL}, json {res['json_ok']}/{len(res['steps'])}, dice {res['dice_ok']}/5, {res['avg_tok_s']} tok/s, mem {res.get('mem_loaded')} MiB, "
          f"screen fps {base['screen_fps']} -> {res.get('gpu_during', {}).get('screen_fps')}, "
          f"encoder fps {base['enc_fps']} -> min {res.get('gpu_during', {}).get('enc_fps_min')} {('ERROR: ' + res['error']) if res['error'] else ''}", flush=True)
    time.sleep(5)


if __name__ == "__main__":
    for m in sys.argv[1:]:
        run(Path(m))
