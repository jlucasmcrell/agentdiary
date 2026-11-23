"""Harness trial driver: waits for the model bake-off to finish, then runs DSH and Hermes trials as the
'inkling' user via the Inkling-Waker task, one at a time, behind the GPU guard (trial phase = 64K ctx).
Usage: python drive_trials.py <bakeoff_driver_output_file>"""
import json, subprocess, sys, time

SSH = ["ssh", "-o", "BatchMode=yes", "6900xt"]
G = r"%USERPROFILE%\bakeoff-guard"
BAKEOFF_LOG = sys.argv[1]


def log(s):
    print(time.strftime("%H:%M:%S"), s, flush=True)


def remote(cmd, timeout=120):
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
    return "\n".join(l for l in r.stdout.splitlines() if l.strip() and "post-quantum" not in l).strip()


def ps(script, timeout=120):
    r = subprocess.run(SSH + ["powershell -NoProfile -NonInteractive -Command -"], input=script, capture_output=True, text=True, timeout=timeout)
    return "\n".join(l for l in r.stdout.splitlines() if l.strip()).strip()


# 1. wait for the model bake-off driver to finish
while True:
    txt = open(BAKEOFF_LOG, encoding="utf-8", errors="replace").read()
    if any(k in txt for k in ("bake-off complete", "gave up", "failed 5 times")):
        log("bake-off driver finished; starting harness trials")
        break
    time.sleep(60)
time.sleep(20)  # let the old guard exit

# 2. start the guard in trial mode (64K context)
remote(f'mkdir "{G}" 2>nul')
remote(f'echo trial> "{G}\\phase.txt"')   # separate command: in cmd, `if ... & echo` would make the echo conditional
remote(f'del "{G}\\status.txt" 2>nul')
if remote(f'type "{G}\\phase.txt"') != "trial":
    sys.exit("could not set guard phase to trial")
guard = subprocess.Popen(SSH + ["-o", "ServerAliveInterval=30", "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\Users\\jluca\\bakeoff-guard.ps1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def status():
    return remote(f'type "{G}\\status.txt"')


def preempted_since(t0):
    out = ps(f'Get-Content "$env:USERPROFILE\\bakeoff-guard\\guard.log" | Select-String "preempted" | ForEach-Object {{ $_.Line.Substring(0,19) }}')
    return any(line >= t0 for line in out.splitlines())


results = {}
try:
    attempt = 0
    for harness in ("dsh", "hermes"):
        for _ in range(4):
            attempt += 1
            tid = f"t{attempt}"
            last = ""
            while (s := status()) != "ready: trial":
                if s != last:
                    log(f"guard: {s}"); last = s
                time.sleep(30)
            t0 = time.strftime("%Y-%m-%dT%H:%M:%S")
            log(f"trial {tid} {harness}: starting")
            remote(f'echo {tid} {harness}> "C:\\ProgramData\\Inkling\\trial.request"')
            ps('Start-ScheduledTask -TaskName "Inkling-Waker"')
            res_path = f"C:\\Users\\inkling\\trials\\{tid}-{harness}\\result.json"
            deadline = time.time() + 100 * 60
            res = None
            while time.time() < deadline:
                time.sleep(60)
                raw = ps(f'if (Test-Path "{res_path}") {{ Get-Content "{res_path}" -Raw }}')
                if raw:
                    res = json.loads(raw); break
            if res and not preempted_since(t0):
                log(f"trial {tid} {harness}: {json.dumps(res)}")
                results[harness] = res
                break
            log(f"trial {tid} {harness}: interrupted or no result ({res}); retrying when the GPU is free")
finally:
    remote(f'echo done> "{G}\\phase.txt"')
    time.sleep(15)
    guard.terminate()
log("trials complete: " + json.dumps(results))
