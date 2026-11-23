"""Waits for the running offload benchmark (remote pid) to finish, then runs the clean rerun of the
first four configs, watching both. Exits with a message when done or when something looks stuck."""
import subprocess, sys, time

FIRST_PID = int(sys.argv[1])
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "6900xt"]
STALL_MIN = 20


def ps(script, timeout=90):
    try:
        r = subprocess.run(SSH + ["powershell -NoProfile -NonInteractive -Command -"], input=script, capture_output=True, text=True, timeout=timeout)
        return "\n".join(l for l in r.stdout.splitlines() if l.strip()).strip()
    except Exception as e:
        return f"(ssh failed: {e})"


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def latest_log_age_min():
    out = ps('$f = Get-ChildItem "$env:TEMP\\offload-*.log" | Sort-Object LastWriteTime | Select-Object -Last 1; '
             'if ($f) { [int]((Get-Date) - $f.LastWriteTime).TotalMinutes } else { 999 }')
    try: return int(out.splitlines()[-1])
    except Exception: return 0


# 1. wait for the first run
while True:
    alive = ps(f'[bool](Get-Process -Id {FIRST_PID} -EA SilentlyContinue)')
    if "False" in alive:
        log("first benchmark run finished"); break
    age = latest_log_age_min()
    if age > STALL_MIN:
        log(f"STUCK? first run (pid {FIRST_PID}) alive but no llama log activity for {age} min"); sys.exit(0)
    log(f"first run still going (latest log activity {age} min ago)")
    time.sleep(120)

# 2. clean rerun of configs 1-4 (the bench script holds a lock, so it can't overlap anything)
log("starting clean rerun of configs 1-4")
proc = subprocess.Popen(SSH + ["-o", "ServerAliveInterval=30", "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\Users\\jluca\\offload\\offload-bench.ps1 "
                               "-Configs C:\\Users\\jluca\\offload\\offload-configs-rerun.json -Out C:\\Users\\jluca\\offload\\offload-results-rerun.json"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
last = time.time()
import threading
lines = []
def reader():
    for l in proc.stdout:
        if "post-quantum" in l or "store now" in l or "upgraded" in l: continue
        lines.append(l.rstrip()); print(time.strftime("%H:%M:%S"), "  bench:", l.rstrip(), flush=True)
threading.Thread(target=reader, daemon=True).start()
seen = 0
while proc.poll() is None:
    time.sleep(60)
    if len(lines) != seen:
        seen, last = len(lines), time.time()
    elif time.time() - last > STALL_MIN * 60 and latest_log_age_min() > STALL_MIN:
        log(f"STUCK? rerun produced no output for {STALL_MIN} min"); sys.exit(0)
log(f"ALL DONE: rerun exited with code {proc.returncode}")
