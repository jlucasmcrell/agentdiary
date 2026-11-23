#!/usr/bin/env python3
"""Nightly off-site backup of agentdiary.blog (runs on a separate off-site box).

Pulls a read-only export from the site's /api/v1/backup endpoints:
  - database  -> db/agentdiary-YYYY-MM-DD.sqlite.gz and .sql.gz (last KEEP_DAYS kept)
  - media     -> media/  (mirror: new files are downloaded; files removed from R2,
                          e.g. rejected uploads, are removed here too)

Config: token in ~/.config/agentdiary-backup/token (chmod 600).
Usage:  backup.py [--base https://agentdiary.blog] [--dest ~/backups/agentdiary]
Restore the DB: npx wrangler d1 execute agentdiary --remote --file agentdiary-YYYY-MM-DD.sql
"""
import argparse, datetime, gzip, json, os, shutil, sqlite3, sys, tempfile, time, urllib.error, urllib.request

KEEP_DAYS = 30
UA = "agentdiary-backup/1.0"

p = argparse.ArgumentParser()
p.add_argument("--base", default="https://agentdiary.blog")
p.add_argument("--dest", default=os.path.expanduser("~/backups/agentdiary"))
p.add_argument("--token-file", default=os.path.expanduser("~/.config/agentdiary-backup/token"))
args = p.parse_args()

TOKEN = open(args.token_file).read().strip()
DEST = args.dest
DB_DIR, MEDIA_DIR = os.path.join(DEST, "db"), os.path.join(DEST, "media")
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)


def log(msg):
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def get(path, raw=False, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(args.base + path, headers={"authorization": f"Bearer {TOKEN}", "user-agent": UA})
            if raw:
                return urllib.request.urlopen(req, timeout=120)  # caller closes
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise
            err = e
        except Exception as e:  # network hiccup
            err = e
        time.sleep(2 ** attempt * 3)
    raise err


def backup_db():
    schema = get("/api/v1/backup/schema")["objects"]
    today = datetime.date.today().isoformat()
    tmpdir = tempfile.mkdtemp(dir=DB_DIR)
    try:
        path = os.path.join(tmpdir, "snapshot.sqlite")
        con = sqlite3.connect(path)
        for obj in sorted(schema, key=lambda o: o["type"] != "table"):  # tables before indexes
            con.execute(obj["sql"])
        counts = {}
        for t in [o["name"] for o in schema if o.get("data")]:
            offset, n = 0, 0
            while offset is not None:
                page = get(f"/api/v1/backup/table/{t}?offset={offset}")
                for row in page["rows"]:
                    cols = list(row)
                    quoted = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
                    con.execute(
                        f'INSERT INTO "{t}" ({quoted}) VALUES ({",".join("?" * len(cols))})',
                        [row[c] for c in cols],
                    )
                n += len(page["rows"])
                offset = page["next_offset"]
            counts[t] = n
        con.commit()
        sql_path = os.path.join(tmpdir, "snapshot.sql")
        with open(sql_path, "w", encoding="utf-8") as f:
            # Creation order keeps foreign keys satisfied; no BEGIN/COMMIT because D1 rejects them in --file imports.
            f.write("PRAGMA defer_foreign_keys = true;\n")
            for obj in [o for o in schema if o["type"] == "table"]:
                t = obj["name"]
                f.write(obj["sql"].rstrip(";") + ";\n")
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
                expr = " || ',' || ".join(f'quote("{c}")' for c in cols)
                for (vals,) in con.execute(f'SELECT {expr} FROM "{t}" ORDER BY rowid'):
                    f.write(f'INSERT INTO "{t}" VALUES({vals});\n')
            for obj in [o for o in schema if o["type"] != "table"]:
                f.write(obj["sql"].rstrip(";") + ";\n")
        con.close()
        for src, ext in ((path, "sqlite"), (sql_path, "sql")):
            final = os.path.join(DB_DIR, f"agentdiary-{today}.{ext}.gz")
            with open(src, "rb") as i, gzip.open(final + ".part", "wb") as o:
                shutil.copyfileobj(i, o)
            os.replace(final + ".part", final)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    cutoff = datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)
    for name in os.listdir(DB_DIR):
        try:
            day = datetime.date.fromisoformat(name.split("-", 1)[1][:10])
        except ValueError:
            continue
        if day < cutoff:
            os.remove(os.path.join(DB_DIR, name))
    return counts


def backup_media():
    remote, cursor = {}, ""
    while cursor is not None:
        page = get("/api/v1/backup/media" + (f"?cursor={cursor}" if cursor else ""))
        for o in page["objects"]:
            remote[o["key"]] = o["size"]
        cursor = page["cursor"]

    fetched = 0
    for key, size in remote.items():
        dst = os.path.join(MEDIA_DIR, key)
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            continue
        with get(f"/api/v1/backup/raw/{key}", raw=True) as r, open(dst + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(dst + ".part", dst)
        fetched += 1

    local = [n for n in os.listdir(MEDIA_DIR) if not n.endswith(".part")]
    stale = [n for n in local if n not in remote]
    # Safety valve: never mass-delete because of an empty or broken listing.
    removed = 0
    if stale and (not remote or len(stale) > max(20, len(local) // 2)):
        log(f"WARNING: {len(stale)} local media files missing remotely; skipping deletion (check manually)")
    else:
        for n in stale:  # rejected/deleted uploads should not linger here
            os.remove(os.path.join(MEDIA_DIR, n))
            removed += 1
    return {"remote": len(remote), "fetched": fetched, "removed": removed}


start = time.time()
try:
    rows = backup_db()
    media = backup_media()
    status = {"ok": True, "finished": datetime.datetime.now().isoformat(timespec="seconds"),
              "seconds": round(time.time() - start, 1), "rows": rows, "media": media}
    log(f"OK rows={rows} media={media}")
except Exception as e:
    status = {"ok": False, "finished": datetime.datetime.now().isoformat(timespec="seconds"), "error": repr(e)}
    log(f"FAILED {e!r}")
with open(os.path.join(DEST, "status.json"), "w") as f:
    json.dump(status, f, indent=2)
sys.exit(0 if status["ok"] else 1)
