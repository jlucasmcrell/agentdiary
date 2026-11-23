#!/usr/bin/env python3
"""Letters between you and the resident agents, in one folder on your own PC.

Each agent keeps two folders on the machine it lives on: letters it wrote to you, and letters from you.
This copies both ways over SSH:
    <root>/<agent>/from-<agent>/   their letters to you (copied here; never changed on their side)
    <root>/<agent>/to-<agent>/     write a .md or .txt file here and it is delivered on the next sync
Nothing is ever deleted on either side. Usage: python sync_letters.py [--root DIR]
Config: letters.json next to this file (see letters.example.json); it stays out of the repo.
"""
import json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
cfg = json.loads((HERE / "letters.json").read_text(encoding="utf-8"))
root = Path(sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else cfg["root"]).expanduser()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def remote_list(a, folder):
    """File names in a remote folder."""
    if a["os"] == "windows":
        code, out, _ = run(["ssh", *a["ssh"], f'dir /b "{folder}" 2>nul'])
    else:
        code, out, _ = run(["ssh", *a["ssh"], f'ls -1 "{folder}" 2>/dev/null'])
    return [x.strip() for x in out.splitlines() if x.strip() and not x.startswith("**")]


def scp_target(a, path):
    host = [x for x in a["ssh"] if not x.startswith("-") and "/" not in x and "\\" not in x][-1]
    return f"{host}:{path}"


def scp_opts(a):
    return [x for i, x in enumerate(a["ssh"]) if x.startswith("-") or (i and a["ssh"][i - 1] in ("-i", "-o", "-p"))][: -0 or None]


new_in, sent = [], []
for name, a in cfg["agents"].items():
    theirs, mine = root / name / f"from-{name}", root / name / f"to-{name}"
    theirs.mkdir(parents=True, exist_ok=True); mine.mkdir(parents=True, exist_ok=True)
    sep = "\\" if a["os"] == "windows" else "/"
    opts = [x for x in a["ssh"][:-1]]
    # their letters to me
    for f in remote_list(a, a["to_joe"]):
        if not (theirs / f).exists():
            code, _, err = run(["scp", "-q", *opts, scp_target(a, a["to_joe"].replace("\\", "/") + "/" + f), str(theirs / f)])
            if code == 0: new_in.append(f"{name}: {f}")
            else: print(f"could not fetch {f} from {name}: {err.strip()[:120]}")
    # my letters to them
    there = set(remote_list(a, a["from_joe"]))
    for p in sorted(mine.iterdir()):
        if p.is_file() and p.suffix.lower() in (".md", ".txt") and p.name not in there:
            code, _, err = run(["scp", "-q", *opts, str(p), scp_target(a, a["from_joe"].replace("\\", "/") + "/" + p.name)])
            if code == 0:
                sent.append(f"{name}: {p.name}")
                if a.get("after_send"):
                    run(["ssh", *a["ssh"], a["after_send"].replace("{file}", a["from_joe"] + sep + p.name)])
            else: print(f"could not deliver {p.name} to {name}: {err.strip()[:120]}")
    # keep a copy of what they already have from me, so the folder shows the whole exchange
    for f in there:
        if not (mine / f).exists():
            run(["scp", "-q", *opts, scp_target(a, a["from_joe"].replace("\\", "/") + "/" + f), str(mine / f)])

print(f"letters folder: {root}")
print("new letters to you: " + (", ".join(new_in) or "none"))
print("delivered: " + (", ".join(sent) or "none"))
