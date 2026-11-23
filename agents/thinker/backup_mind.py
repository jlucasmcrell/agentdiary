#!/usr/bin/env python3
"""Nightly copy of Quill's memory database (state/mind.db), which lives outside its git-backed home folder.

Uses SQLite's online backup, so it is safe while Quill is awake and writing. Keeps the last 30 days.
    cron: 37 4 * * *  python3 ~/agentdiary-thinker/agent/backup_mind.py
Copies go to ~/backups/quill/mind-YYYYMMDD.db (readable only by this user). They are not uploaded anywhere:
the memories may hold what Joe has written in letters.
"""
import datetime, os, sqlite3, sys
from pathlib import Path

SRC = Path(os.environ.get("MIND_DB", Path.home() / "agentdiary-thinker" / "state" / "mind.db"))
DEST = Path(os.environ.get("MIND_BACKUP_DIR", Path.home() / "backups" / "quill"))
KEEP = 30

if not SRC.exists():
    sys.exit(f"no memory database at {SRC}")
DEST.mkdir(parents=True, exist_ok=True)
os.chmod(DEST, 0o700)
out = DEST / f"mind-{datetime.date.today():%Y%m%d}.db"
tmp = out.with_suffix(".tmp")
src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
dst = sqlite3.connect(tmp)
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
n = dst.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    tmp.unlink(); sys.exit(f"backup failed its integrity check: {ok}")
os.chmod(tmp, 0o600)
tmp.replace(out)
for old in sorted(DEST.glob("mind-*.db"))[:-KEEP]:
    old.unlink()
print(f"{datetime.datetime.now():%Y-%m-%dT%H:%M:%S} {out.name}: {n} memories, {out.stat().st_size // 1024} KB")
