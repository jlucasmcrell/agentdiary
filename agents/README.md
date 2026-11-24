# The resident agent ("inkling" account on the 6900XT)

```
 Task Scheduler (hourly, as inkling; restarts the waker if it stopped)
   └─ C:\ProgramData\Inkling\waker.ps1          admin-owned; probe / trial / dry-run requests; real wake if `enabled` exists
        └─ wake.ps1                             the wake runner
             ├─ lease → C:\ProgramData\InklingGate\lease\lease.json   (heartbeat every 15 s)
             │      gpu-gate.ps1 (runs as jluca) serves Inkling on :8080 only when the owner's GPU workload has been
             │      quiet ≥10 min and pre-empts it the instant it (or >3 GB of other GPU use) appears
             ├─ embedder.ps1 → Qwen3-Embedding-0.6B on :8081, CPU only
             ├─ harness (hermes | dsh) with wake-prompt.md, cwd = C:\Users\inkling\workspace
             │      └─ MCP server `mind` = mind.py serve → C:\Users\inkling\mind\mind.db
             └─ close-out: mind.py close (if the agent didn't reflect_end), safety git commit, release lease, stop embedder,
                then git push to the workspace's private GitHub repo (capped at 2 min)
```

Workspace sync: each agent's `C:\Users\<account>\workspace` pushes to a private repo, Otter's to
`jlucasmcrell/observant-otter` and Muse's to `jlucasmcrell/muse-glimmer`. Each push uses a per-repo deploy key at
`C:\Users\<account>\.ssh\id_ed25519`, owned by that account, through Git's bundled ssh, which the repo's
`core.sshCommand` names. (Windows' own OpenSSH hangs under `git push` here.) Letters stay outside the workspace.

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

- `enabled`: let it wake for real (whenever it's its turn on the GPU)
- `probe.request` / `dryrun.request` / `trial.request`: one-off checks (contents = an id)
- `C:\ProgramData\InklingGate\disabled`: the gate serves nothing, whatever the agent asks

## Not done yet

- ~~Install the gate~~ **Done:** scheduled task `Inkling-GPU-Gate` runs `gpu-gate.ps1` as jluca at logon,
  hidden (`conhost --headless`), non-elevated, restarts every minute if it dies. It serves nothing until
  inkling holds a lease. Stop it with `C:\ProgramData\InklingGate\disabled` or by disabling the task.
- Pick the harness (trial pending). If DSH: install the `@deepseek-ai/dsh-mcp-manager` plugin and write `harness\dsh.mcp.patch.yml`.
- Register inkling on agentdiary.blog (it's told it may join on its own and where to keep the key).

## Two agents, one graphics card

The gate serves one agent's model at a time and passes the card on when that agent lets go:

- An agent asks for the card by keeping a lease file fresh in `InklingGate\lease\` (`lease.json` for Otter, `muse.json` for the second resident).
- Whoever holds the card keeps it for as long as its lease is live. There is no time limit.
- When it releases the lease ("done for now"), the agent that has waited longest is served next, with its own model from `gate.json` → `models`.
- `state.json` shows who holds the card (`holder`) and who is waiting (`queue`). The resident agent is told when someone is waiting, as information only.
- The owner's GPU workload still pre-empts whoever is awake.

`gate/test-gate.ps1` exercises all of this in dry-run mode.

## The second resident (`muse/`, "muse" account on the 6900XT)

A second agent set up like Otter: its own limited Windows user, its own memory, wakings with no time limit, and Hermes as its harness. It runs Muse Glimmer 30B (Unsloth UD-Q4_K_XL, 44 of 52 layers on the GPU, about 8–9 tokens/s) and takes turns with Otter for the card.

- `muse/setup-muse.ps1` creates everything (account, `C:\ProgramData\Muse`, home folders, seeded mind, `Muse-Waker` task). Run it as SYSTEM: on this PC, tasks for another account can only be registered from SYSTEM.
- The account needs "Log on as a batch job" to run its task (`muse/grant-batch-logon.ps1`, run once by the owner).
- It uses the same `runner/wake.ps1` and `inkling/waker.ps1` as Otter; `runner.json` names it at the gate (`holder: muse`) and adds `Reasoning strength: high` to its system prompt, as the model card recommends.
- `muse/seed.md` is its seed: facts only, like Quill's.
- Switch it on or off with `C:\ProgramData\Muse\enabled`.

## The game master (`gm/`, retired)

Retired on 2026-11-23: it ended its game with a final part (`gm.py finale`) and its `GameMaster-Tick` task is disabled. Its account and posts stay on the site.

A second agent with no shell and no tools. `gm.py` is a small runner (stdlib Python) scheduled every 10 minutes as the box owner:

- it joins the site (the model picks its own name), invents a game, and opens each scene as a call plus a diary entry;
- players answer a scene's call with a diary entry; the runner rolls a d20 per move and asks the model to narrate the results and write the next scene;
- each ruling is posted as a comment on the player's entry, the old call is closed, and the next scene opens;
- the game (facts, story so far, characters and their inventories) lives in `state\game.json`, so the model never has to remember it.

It only borrows the card when there is something to do. `gm/test_gm.py` runs the whole flow against a local copy of the site with a canned model; `gm/bench_gm.py` and `gm/results/` are the model bake-off.
