"""Watches the bake-off -> offload-benchmark pipeline and exits (so I get notified) when something
looks stuck, or when everything has finished. Usage: python watchdog.py <bakeoff_log> <bench_log>"""
import os, subprocess, sys, time

BAKEOFF, BENCH = sys.argv[1], sys.argv[2]
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "6900xt"]
CHECK_EVERY = 120          # seconds
QUIET_LIMIT = 55 * 60      # no new output from the active stage for this long = stuck
                           # (a single tool task can legitimately run up to 45 minutes)
MISMATCH_LIMIT = 6 * 60    # driver waiting for phase X while guard serves phase Y


def tail(path, n=1):
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8", errors="replace") if l.strip()]
        return lines[-n:] if lines else []
    except FileNotFoundError:
        return []


def remote(cmd):
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=60)
        return "\n".join(l for l in r.stdout.splitlines() if l.strip()).strip()
    except Exception as e:
        return f"(ssh failed: {e})"


def stop(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)
    sys.exit(0)


mismatch_since = None
while True:
    now = time.time()
    b_txt = open(BAKEOFF, encoding="utf-8", errors="replace").read() if os.path.exists(BAKEOFF) else ""
    bench_started = os.path.exists(BENCH) and os.path.getsize(BENCH) > 0
    bake_done = any(k in b_txt for k in ("bake-off complete", "gave up", "failed 5 times"))

    if bake_done and bench_started and "== " in open(BENCH, encoding="utf-8", errors="replace").read():
        bench_txt = open(BENCH, encoding="utf-8", errors="replace").read()
        n_cfg = bench_txt.count("== ")
        n_done = bench_txt.count("   vram ")
        if n_done >= 6:
            stop(f"ALL DONE: bake-off finished and offload benchmark completed {n_done} configs.")
        active, label = BENCH, f"offload benchmark ({n_done}/6 configs done)"
    elif bake_done:
        active, label = BENCH, "offload benchmark (not started yet)"
    else:
        active, label = BAKEOFF, "bake-off"

    age = now - os.path.getmtime(active) if os.path.exists(active) else now - os.path.getmtime(BAKEOFF)
    if age > QUIET_LIMIT:
        stop(f"STUCK? {label}: no new output for {age/60:.0f} min. Last line: {tail(active)}")

    # The failure we actually hit: driver waiting for a phase the guard never switches to.
    if not bake_done:
        last = tail(BAKEOFF, 3)
        waiting_for = next((l.split("running ")[1].split()[0] for l in reversed(last) if "running " in l), None)
        guard = remote('type "%USERPROFILE%\\bakeoff-guard\\status.txt"')
        phase = remote('type "%USERPROFILE%\\bakeoff-guard\\phase.txt"')
        expected = phase.strip()
        if guard and expected and guard.startswith("ready:") and guard.split(":")[1].strip() != expected:
            mismatch_since = mismatch_since or now
            if now - mismatch_since > MISMATCH_LIMIT:
                stop(f"STUCK: driver asked for phase '{expected}' but guard still says '{guard}' for {(now-mismatch_since)/60:.0f} min")
        else:
            mismatch_since = None
        print(time.strftime("%H:%M:%S"), f"ok: {label}; guard={guard!r} phase={expected!r}; last output {age/60:.0f} min ago", flush=True)
    else:
        print(time.strftime("%H:%M:%S"), f"ok: {label}; last output {age/60:.0f} min ago", flush=True)
    time.sleep(CHECK_EVERY)
