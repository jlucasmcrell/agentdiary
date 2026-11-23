"""Supervised queue: Inkling tool-task rerun, then the DSH/Hermes harness trials.
Every step has a time limit; on any overrun or guard mismatch this prints STUCK and exits (so I get notified)."""
import json, os, subprocess, sys, time, pathlib

HERE = pathlib.Path(__file__).resolve().parent
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "6900xt"]
G = r"%USERPROFILE%\bakeoff-guard"


def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)
def stuck(m): log("STUCK: " + m); cleanup(); sys.exit(1)


def remote(cmd, timeout=60):
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        return "\n".join(l for l in r.stdout.splitlines() if l.strip()).strip()
    except Exception as e:
        return f"(ssh failed: {e})"


procs = []
def cleanup():
    remote(f'echo done> "{G}\\phase.txt"')
    for p in procs:
        try: p.terminate()
        except Exception: pass


def wait_status(want, limit_min):
    t0, last = time.time(), ""
    while time.time() - t0 < limit_min * 60:
        s = remote(f'type "{G}\\status.txt"')
        if s != last: log(f"guard: {s}"); last = s
        if s == want: return
        time.sleep(20)
    stuck(f"guard never reached '{want}' within {limit_min} min (last: '{last}')")


# ── 1. Inkling tool-task rerun ──
# Separate commands: in cmd, `if not exist X mkdir X & echo ...` would make the echo part of the if.
remote(f'mkdir "{G}" 2>nul')
remote(f'echo inkling> "{G}\\phase.txt"')
remote(f'del "{G}\\status.txt" 2>nul')
if remote(f'type "{G}\\phase.txt"') != "inkling":
    stuck("could not set guard phase to inkling")
procs.append(subprocess.Popen(SSH + ["-o", "ServerAliveInterval=30", "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\Users\\jluca\\bakeoff-guard.ps1 -IdleMinutes 0"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
procs.append(subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "-N", "-L", "18080:127.0.0.1:8080", "-L", "21434:127.0.0.1:11434", "6900xt"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
wait_status("ready: inkling", 8)
log("rerunning Inkling's tool task")
try:
    rc = subprocess.run([sys.executable, str(HERE / "bakeoff.py"), "inkling-2-mini", "--tools-only"], timeout=50 * 60).returncode
except subprocess.TimeoutExpired:
    stuck("Inkling tool task ran over 50 min")
res = json.loads((HERE / "results" / "inkling-2-mini.json").read_text(encoding="utf-8"))["tools"]
log(f"Inkling tool task (rc={rc}): {res['score']} in {res['seconds']}s")

# ── 2. harness trials (drive_trials.py starts its own guard in trial phase) ──
remote(f'echo done> "{G}\\phase.txt"')
time.sleep(20)
for p in procs: p.terminate()
procs.clear()
flag = HERE / "results" / "_ready_for_trials.txt"
flag.write_text("bake-off complete\n")
log("starting harness trials")
try:
    rc = subprocess.run([sys.executable, str(HERE.parent / "harness-trial" / "drive_trials.py"), str(flag)], timeout=4 * 3600).returncode
except subprocess.TimeoutExpired:
    stuck("harness trials ran over 4 h")
flag.unlink(missing_ok=True)
log(f"ALL DONE (trials rc={rc})")
