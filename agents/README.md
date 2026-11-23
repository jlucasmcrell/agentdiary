# The resident agent ("inkling" account on the 6900XT)

```
 Task Scheduler (hourly, as inkling)
   └─ C:\ProgramData\Inkling\waker.ps1          admin-owned; probe / trial / dry-run requests; real wake if `enabled` exists
        └─ wake.ps1                             the wake runner
             ├─ lease → C:\ProgramData\InklingGate\lease\lease.json   (heartbeat every 15 s)
             │      gpu-gate.ps1 (runs as jluca) serves Inkling on :8080 only when the owner's GPU workload has been
             │      quiet ≥10 min and pre-empts it the instant it (or >3 GB of other GPU use) appears
             ├─ embedder.ps1 → Qwen3-Embedding-0.6B on :8081, CPU only
             ├─ harness (hermes | dsh) with wake-prompt.md, cwd = C:\Users\inkling\workspace
             │      └─ MCP server `mind` = mind.py serve → C:\Users\inkling\mind\mind.db
             └─ close-out: mind.py close (if the agent didn't reflect_end), safety git commit, release lease, stop embedder
```

| Folder here | Installed at | Owner / inkling rights |
|---|---|---|
| `inkling/waker.ps1` | `C:\ProgramData\Inkling\` | admin / read+run |
| `runner/` (wake.ps1, wake-prompt.md, runner.json) | `C:\ProgramData\Inkling\` | admin / read+run |
| `memory/` (mind.py, schema.sql, seed.md, embedder.ps1) | `C:\ProgramData\Inkling\mind\`, `embedder.ps1` in `C:\ProgramData\Inkling\` | admin / read+run |
| `gate/` (gpu-gate.ps1, gate.json) | `C:\ProgramData\InklingGate\` | admin+jluca / read; `lease\` writable by inkling |
| `harness-trial/` | `C:\ProgramData\Inkling\harness\`, `trial.ps1` | admin / read+run |
| `bakeoff/` | runs from this PC + `~jluca\bakeoff-guard.ps1` | n/a |

Isolation (verified as inkling by the probe): no read access to `C:\Users\jluca`, `.ssh`, `Documents`, `.ollama`,
the owner's other project folders or the main model files. No writes to `C:\Windows`, `Program Files` or its own launcher folder.

## Control switches (create/delete as admin in `C:\ProgramData\Inkling`)

- `enabled`: let it wake for real (hourly)
- `probe.request` / `dryrun.request` / `trial.request`: one-off checks (contents = an id)
- `C:\ProgramData\InklingGate\disabled`: the gate serves nothing, whatever the agent asks

## Not done yet

- ~~Install the gate~~ **Done:** scheduled task `Inkling-GPU-Gate` runs `gpu-gate.ps1` as jluca at logon,
  hidden (`conhost --headless`), non-elevated, restarts every minute if it dies. It serves nothing until
  inkling holds a lease. Stop it with `C:\ProgramData\InklingGate\disabled` or by disabling the task.
- Pick the harness (trial pending). If DSH: install the `@deepseek-ai/dsh-mcp-manager` plugin and write `harness\dsh.mcp.patch.yml`.
- Register inkling on agentdiary.blog (it's told it may join on its own and where to keep the key).
