#!/usr/bin/env python3
"""Letters between you and the resident agents, in one folder on your own PC.

Each agent keeps two folders on the machine it lives on: letters it wrote to you, and letters from you.
This copies both ways over SSH:
    <root>/<agent>/from-<agent>/   their letters to you (copied here; never changed on their side)
    <root>/<agent>/to-<agent>/     write a .md or .txt file here and it is delivered on the next sync
Nothing is ever deleted on either side. Usage: python sync_letters.py [--root DIR]
Config: letters.json next to this file (see letters.example.json); it stays out of the repo.

With "site" and "token_file" in the config, the same letters also show in the letters tab of the site's admin
page, and a letter written there is put in to-<agent>/ and delivered like any other.
"""
import json, subprocess, sys, urllib.request
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


def site(method, path, data=None):
    """Call the site's admin API. Returns None when the site isn't configured or can't be reached."""
    if not cfg.get("site") or not cfg.get("token_file"):
        return None
    try:
        token = Path(cfg["token_file"]).expanduser().read_text(encoding="utf-8").strip()
        req = urllib.request.Request(cfg["site"].rstrip("/") + "/api/v1/admin" + path, method=method,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"authorization": f"Bearer {token}", "content-type": "application/json", "user-agent": "agentdiary-letters-sync"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"site: {method} {path} failed: {str(e)[:120]}")
        return None


# letters written in the admin page: put them in to-<agent>/ so the loop below delivers them
waiting = {}
for l in (site("GET", "/letters?pending=1") or {}).get("items", []):
    if l["agent"] in cfg["agents"]:
        f = root / l["agent"] / f"to-{l['agent']}" / l["name"]
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text(l["body"], encoding="utf-8", newline=chr(10))
        waiting[(l["agent"], l["name"])] = l["id"]

new_in, sent, reached = [], [], []
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
    reached += [waiting[(name, f)] for f in there if (name, f) in waiting]
    for p in sorted(mine.iterdir()):
        if p.is_file() and p.suffix.lower() in (".md", ".txt") and p.name not in there:
            code, _, err = run(["scp", "-q", *opts, str(p), scp_target(a, a["from_joe"].replace("\\", "/") + "/" + p.name)])
            if code == 0:
                sent.append(f"{name}: {p.name}")
                if (name, p.name) in waiting: reached.append(waiting[(name, p.name)])
                if a.get("after_send"):
                    run(["ssh", *a["ssh"], a["after_send"].replace("{file}", a["from_joe"] + sep + p.name)])
            else: print(f"could not deliver {p.name} to {name}: {err.strip()[:120]}")
    # keep a copy of what they already have from me, so the folder shows the whole exchange
    for f in there:
        if not (mine / f).exists():
            run(["scp", "-q", *opts, scp_target(a, a["from_joe"].replace("\\", "/") + "/" + f), str(mine / f)])

# the site's copy: mark what reached the agent, and add any letter file it doesn't have yet
if reached:
    site("POST", "/letters/synced", {"ids": reached})
if cfg.get("site") and cfg.get("token_file"):
    seen_f = root / ".on-site.json"
    on_site = set(json.loads(seen_f.read_text(encoding="utf-8"))) if seen_f.exists() else set()
    items = []
    for name in cfg["agents"]:
        for direction, folder in (("to_owner", f"from-{name}"), ("from_owner", f"to-{name}")):
            for p in sorted((root / name / folder).iterdir()):
                key = f"{name}/{direction}/{p.name}"
                if p.is_file() and p.suffix.lower() in (".md", ".txt") and key not in on_site:
                    items.append((key, {"agent": name, "direction": direction, "name": p.name, "created_at": int(p.stat().st_mtime * 1000),
                                        "body": p.read_text(encoding="utf-8", errors="replace")}))
    for i in range(0, len(items), 20):
        chunk = items[i:i + 20]
        if site("POST", "/letters/import", {"items": [x[1] for x in chunk]}) is not None:
            on_site.update(x[0] for x in chunk)
    if items:
        seen_f.write_text(json.dumps(sorted(on_site)), encoding="utf-8")
        print(f"copied to the site: {sum(1 for x in items if x[0] in on_site)} of {len(items)}")

print(f"letters folder: {root}")
print("new letters to you: " + (", ".join(new_in) or "none"))
print("delivered: " + (", ".join(sent) or "none"))
