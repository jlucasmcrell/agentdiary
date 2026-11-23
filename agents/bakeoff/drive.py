"""Runs the bake-off whenever the GPU guard on the 6900XT says the GPU is free.
Re-runs a model if the owner's GPU workload pre-empted it mid-run. Usage: python drive.py"""
import subprocess, sys, time, pathlib

HERE = pathlib.Path(__file__).parent
SSH = ["ssh", "-o", "BatchMode=yes", "6900xt"]
G = r"%USERPROFILE%\bakeoff-guard"


def remote(cmd):
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True)
    return "\n".join(l for l in r.stdout.splitlines() if l.strip()).strip()


def status():
    return remote(f'type "{G}\\status.txt"')


def set_phase(p):
    remote(f'echo {p}> "{G}\\phase.txt"')


def log(s):
    print(time.strftime("%H:%M:%S"), s, flush=True)


def wait_ready(phase, deadline):
    last = ""
    while time.time() < deadline:
        s = status()
        if s != last:
            log(f"guard: {s}")
            last = s
        if s == f"ready: {phase}":
            return True
        time.sleep(30)
    return False


deadline = time.time() + 12 * 3600
tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "-N",
                           "-L", "18080:127.0.0.1:8080", "-L", "21434:127.0.0.1:11434", "6900xt"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for phase, cand in (("inkling", "inkling-2-mini"), ("qwen", "qwen3-14b")):
        set_phase(phase)
        for attempt in range(1, 6):
            if not wait_ready(phase, deadline):
                log("gave up waiting for the GPU"); sys.exit(2)
            log(f"running {cand} (attempt {attempt})")
            rc = subprocess.run([sys.executable, str(HERE / "bakeoff.py"), cand]).returncode
            s = status()
            if rc == 0 and s == f"ready: {phase}":
                log(f"{cand} finished cleanly"); break
            log(f"{cand} interrupted (rc={rc}, guard: {s}); will retry when GPU is free")
        else:
            log(f"{cand} failed 5 times"); sys.exit(3)
finally:
    set_phase("done")
    tunnel.terminate()
log("bake-off complete")
