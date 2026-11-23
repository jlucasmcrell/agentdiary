<#
  Inkling waker. Installed at C:\ProgramData\Inkling\waker.ps1, run hourly by the "Inkling-Waker"
  scheduled task as the 'inkling' user. Only admins can edit this file; inkling can read and run it.

  Control files (in C:\ProgramData\Inkling, admin-writable only):
    enabled         if present, the agent may wake (subject to the GPU gate)
    probe.request   any content; when it changes, run the isolation probe once
#>
$ErrorActionPreference = "Continue"
$Ctl = $PSScriptRoot
$Home_ = $env:USERPROFILE
New-Item -ItemType Directory -Force "$Home_\logs", "$Home_\status" | Out-Null
$Log = "$Home_\logs\waker.log"
function Log([string]$m) { Add-Content -Path $Log -Value "$(Get-Date -Format s) $m" -Encoding UTF8 }

# ── one-off isolation probe, requested by an admin ──
if (Test-Path "$Ctl\probe.request") {
  $req = (Get-Content "$Ctl\probe.request" -Raw).Trim()
  $done = ""; if (Test-Path "$Home_\status\probe.done") { $done = (Get-Content "$Home_\status\probe.done" -Raw).Trim() }
  if ($req -and $req -ne $done) {
    $o = @("probe $req at $(Get-Date -Format s)", "whoami: $(whoami)", "home: $Home_")
    try { Set-Content "$Home_\probe-write.txt" "ok" -ErrorAction Stop; Remove-Item "$Home_\probe-write.txt"; $o += "write own home: OK" } catch { $o += "write own home: FAIL $($_.Exception.Message)" }
    $deny = @("C:\Users\jluca", "C:\Users\jluca\.ssh", "C:\Users\jluca\Documents", "C:\Users\jluca\.ollama", "C:\ProgramData\Models\inkling-2-mini")
    if (Test-Path "$Ctl\probe-extra-paths.txt") { $deny += @(Get-Content "$Ctl\probe-extra-paths.txt" | Where-Object { $_.Trim() }) }
    foreach ($p in $deny) {
      try { Get-ChildItem $p -ErrorAction Stop | Out-Null; $o += "read $p : ALLOWED (bad)" } catch { $o += "read $p : denied (good)" }
    }
    foreach ($p in "C:\Windows\probe.txt", "C:\Program Files\probe.txt", "$Ctl\probe.txt") {
      try { Set-Content $p "x" -ErrorAction Stop; Remove-Item $p; $o += "write $p : ALLOWED (bad)" } catch { $o += "write $p : denied (good)" }
    }
    try { $o += "ollama localhost: " + (Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 5).version } catch { $o += "ollama localhost: FAIL $($_.Exception.Message)" }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try { $o += "web: " + ((Invoke-RestMethod https://agentdiary.blog/api/v1/stats -TimeoutSec 15) | ConvertTo-Json -Compress) } catch { $o += "web: FAIL $($_.Exception.Message)" }
    $o += "winget: " + $(if (Get-Command winget -ErrorAction SilentlyContinue) { "available" } else { "missing" })
    $o += "git: " + $(if (Get-Command git -ErrorAction SilentlyContinue) { (git --version) } else { "missing" })
    if (Test-Path "$Ctl\embedder.ps1") {  # memory + embeddings end to end, on a throwaway database
      $o += "embedder start: " + (& "$Ctl\embedder.ps1" start)
      $tmpDb = Join-Path $env:TEMP "mind-probe-$req.db"
      $py = "import os,sys; sys.path.insert(0, r'$Ctl\mind'); os.environ['MIND_DB']=r'$tmpDb'; import mind; db=mind.connect(); " +
            "mind.t_wake_context(db,{}); mind.t_remember(db,{'content':'Read about the Patterson-Gimlin film, frame 352.','importance':6}); " +
            "mind.t_remember(db,{'content':'Learned how SQLite ranks full-text results.','importance':6}); " +
            "print('recall sasquatch footage ->', mind.t_recall(db,{'query':'sasquatch footage','k':1}))"
      $o += (& "C:\Program Files\Python312\python.exe" -c $py 2>&1) -join " "
      & "$Ctl\embedder.ps1" stop
      Remove-Item $tmpDb* -ErrorAction SilentlyContinue
      $o += "embedder after stop: " + (& "$Ctl\embedder.ps1" status)
    }
    if (Test-Path "$Home_\workspace\.git") { $o += "workspace repo: " + ((git -C "$Home_\workspace" log --oneline -1 2>&1) -join " ") + " | status: " + ((git -C "$Home_\workspace" status --porcelain 2>&1) -join " ") }
    $o | Set-Content "$Home_\status\probe-result.txt" -Encoding UTF8
    Set-Content "$Home_\status\probe.done" $req
    Log "probe $req done"
  }
}

# ── harness trial, requested by an admin: trial.request = "<id> <dsh|hermes>" ──
if (Test-Path "$Ctl\trial.request") {
  $parts = (Get-Content "$Ctl\trial.request" -Raw).Trim() -split '\s+'
  if ($parts.Count -eq 2) {
    $marker = "$Home_\status\trial-$($parts[0])-$($parts[1]).done"
    if (-not (Test-Path $marker)) {
      Log "trial $($parts[0]) $($parts[1]) starting"
      & "$Ctl\trial.ps1" -Id $parts[0] -Harness $parts[1]
      if ($LASTEXITCODE -eq 0) { Set-Content $marker (Get-Date -Format s) }
    }
  }
}

# ── wake-runner dry run, requested by an admin: dryrun.request = any id ──
if (Test-Path "$Ctl\dryrun.request") {
  $req = (Get-Content "$Ctl\dryrun.request" -Raw).Trim()
  $marker = "$Home_\status\dryrun-$req.done"
  if ($req -and -not (Test-Path $marker)) {
    Log "wake dry run $req"
    & "$Ctl\wake.ps1" -DryRun
    Set-Content $marker (Get-Date -Format s)
  }
}

if (-not (Test-Path "$Ctl\enabled")) { exit 0 }

# ── a real waking: GPU gate lease → embedder → harness with the mind → close out ──
Log "waking"
& "$Ctl\wake.ps1"
