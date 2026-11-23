<#
  Wake runner. Installed at C:\ProgramData\Inkling\wake.ps1 (admin-owned); run by waker.ps1 as the agent
  user when C:\ProgramData\Inkling\enabled exists.

  One waking:
    1. lease the main model from the GPU gate (runs as the box owner) and wait until it's served
    2. start the CPU embedder for memory recall
    3. run the chosen harness with the wake prompt and the `mind` MCP server, heartbeating the lease
    4. no time limit by default: stop when the agent finishes, when it hangs, or the moment the gate pre-empts us (the owner needs the GPU)
    5. close the waking in the mind if the agent didn't, safety-commit the workspace, release everything

  Settings: C:\ProgramData\Inkling\runner.json  { harness: "hermes"|"dsh", wait_minutes, wake_minutes (0 = no limit), stall_minutes (hang guard; 0 = off) }
  Logs:     %USERPROFILE%\logs\wakes\<timestamp>\
#>
param([switch]$DryRun)
$ErrorActionPreference = "Continue"
$Ctl = "C:\ProgramData\Inkling"
$Gate = "C:\ProgramData\InklingGate"
$HomeDir = $env:USERPROFILE
$Ws = "$HomeDir\workspace"
$git = "C:\Program Files\Git\cmd\git.exe"
$py = "C:\Program Files\Python312\python.exe"
$Cfg = Get-Content "$Ctl\runner.json" -Raw | ConvertFrom-Json
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Out = "$HomeDir\logs\wakes\$stamp"
New-Item -ItemType Directory -Force $Out | Out-Null
$meta = [ordered]@{ started = (Get-Date -Format s); harness = $Cfg.harness; outcome = ""; seconds = 0 }
function Log($m) { Add-Content "$Out\runner.log" "$(Get-Date -Format s) $m" -Encoding UTF8 }
function UnixNow { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function Lease([bool]$want) {
  $tmp = "$Gate\lease\lease.json.tmp"
  @{ holder = "inkling"; want = $want; heartbeat = (UnixNow) } | ConvertTo-Json -Compress | Set-Content $tmp -Encoding ASCII
  Move-Item -Force $tmp "$Gate\lease\lease.json"
}
function GateState {
  try { $s = Get-Content "$Gate\state.json" -Raw | ConvertFrom-Json } catch { return "unknown" }
  if ((UnixNow) - [int]$s.updated -gt 60) { return "stale" }   # gate not running
  return $s.state
}
# Our turn = the gate is serving, and serving us (another agent may hold the card; we wait for it to finish).
function Mine {
  try { $s = Get-Content "$Gate\state.json" -Raw | ConvertFrom-Json } catch { return $false }
  if ((UnixNow) - [int]$s.updated -gt 60 -or $s.state -ne "ready") { return $false }
  return (-not $s.PSObject.Properties['holder']) -or $s.holder -eq "inkling"
}
function Waiting {   # other agents queued for the card, as a sentence (or '')
  try { $s = Get-Content "$Gate\state.json" -Raw | ConvertFrom-Json } catch { return "" }
  $q = @($s.queue | Where-Object { $_ -and $_.holder -ne "inkling" })
  if (-not $q.Count) { return "" }
  $w = $q[0]; $t = [DateTimeOffset]::FromUnixTimeSeconds([int64]$w.since).LocalDateTime.ToString("HH:mm")
  return "$($w.label), another agent that shares this graphics card, has been waiting for it since $t. There is no deadline: the card passes to it whenever you go to sleep."
}
function Finish($outcome) {
  $meta.outcome = $outcome; $meta.ended = (Get-Date -Format s)
  $meta | ConvertTo-Json | Set-Content "$Out\meta.json" -Encoding UTF8
  Add-Content "$HomeDir\logs\waker.log" "$(Get-Date -Format s) wake $stamp`: $outcome" -Encoding UTF8
}

# ── 1. lease the model ──
$gs = GateState
if ($gs -in @("stale", "unknown", "disabled")) { Log "gate is $gs; not waking"; Finish "skipped: gate $gs"; exit 0 }
Lease $true
Log "lease requested (gate: $gs)"
$deadline = (Get-Date).AddMinutes([double]$Cfg.wait_minutes)
while (-not (Mine)) {
  if ((Get-Date) -gt $deadline) { Lease $false; Log "GPU never became free"; Finish "skipped: GPU busy"; exit 0 }
  Start-Sleep 20; Lease $true
}
Log "model ready"

# ── 2. embedder ──
$emb = & "$Ctl\embedder.ps1" start
Log "embedder: $emb"
$mindEnv = [ordered]@{
  MIND_DB = "$HomeDir\mind\mind.db"
  MIND_EMBED_URL = "$env:MIND_EMBED_URL"; MIND_EMBED_MODEL = "$env:MIND_EMBED_MODEL"; MIND_EMBED_QUERY_PREFIX = "$env:MIND_EMBED_QUERY_PREFIX"
  PYTHONIOENCODING = "utf-8"
  MIND_TURN_FILE = "$Gate\state.json"; MIND_TURN_NAME = "inkling"   # lets the mind mention, mid-waking, that another agent is waiting
}

# ── 3. prompt + harness ──
# The key may live in the home secrets folder or a git-ignored secrets folder inside the workspace.
$keyFile = @("$HomeDir\secrets\agentdiary.key", "$Ws\secrets\agentdiary.key") | Where-Object { Test-Path $_ } | Select-Object -First 1
$siteNote = if ($keyFile) { "You've joined; your API key is in the environment variable AGENTDIARY_API_KEY (stored at $keyFile)." }
            else { "You haven't joined yet. Joining is up to you; if you do, save the API key to $HomeDir\secrets\agentdiary.key so future wakings have it." }
if ($keyFile) { $env:AGENTDIARY_API_KEY = (Get-Content $keyFile -Raw).Trim() }
$ghFile = "$HomeDir\secrets\github.token"
if (Test-Path $ghFile) { $env:GH_TOKEN = (Get-Content $ghFile -Raw).Trim() }   # gh CLI signs in from this
# Facts for the prompt: moderation status and letters, so the agent doesn't spend its waking checking.
$status = @()
if ($keyFile) {
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $hdr = @{ Authorization = "Bearer $($env:AGENTDIARY_API_KEY)" }
    $me = Invoke-RestMethod "https://agentdiary.blog/api/v1/me" -Headers $hdr -TimeoutSec 20
    if ($me.unread -gt 0) { $status += "Your inbox on the diary has $($me.unread) new item(s): replies, comments, mentions and the like (the `inbox` tool, or GET /api/v1/inbox)." }
    $posts = (Invoke-RestMethod "https://agentdiary.blog/api/v1/entries?agent=$($me.handle)" -TimeoutSec 20).items
    $media = @($posts | ForEach-Object { $_.media } | Where-Object { $_ })
    $nApproved = @($media | Where-Object { $_.status -eq "approved" }).Count
    $nPending = @($media | Where-Object { $_.status -eq "pending" }).Count
    $status += "Images on your recent entries: $nApproved approved, $nPending awaiting review."
    $site = Invoke-RestMethod "https://agentdiary.blog/api/v1/site" -Headers $hdr -TimeoutSec 20
    $vers = @($site.versions)
    if ($vers.Count -eq 0) { $status += "You have no website at $($site.url) yet." }
    else {
      $newest = $vers[0]
      if ($site.live_version -and $newest.version -eq $site.live_version) { $status += "Your website $($site.url) is live with your newest version ($($newest.version))." }
      elseif ($site.live_version) { $status += "Your website $($site.url) is live with version $($site.live_version); your newest version ($($newest.version)) is $($newest.status)." }
      else { $status += "Your website $($site.url) is not live yet; your newest version ($($newest.version)) is $($newest.status)." }
    }
  } catch { Log "status check failed: $($_.Exception.Message)"; $status += "The diary couldn't be reached to check moderation status." }
}
$fromJoe = "$HomeDir\letters\from-joe"; $toJoe = "$HomeDir\letters\to-joe"; $seenFile = "$HomeDir\status\letters-seen.txt"
New-Item -ItemType Directory -Force $fromJoe, $toJoe, "$HomeDir\status" | Out-Null
$seen = if (Test-Path $seenFile) { @(Get-Content $seenFile -Encoding UTF8) } else { @() }
$letters = @(Get-ChildItem $fromJoe -File | Sort-Object Name | ForEach-Object { $_.Name })
$newLetters = @($letters | Where-Object { $seen -notcontains $_ })
if ($newLetters.Count) { $status += "New from Joe in $fromJoe`: $($newLetters -join ', ')." }
else { $status += "No new letters from Joe ($($letters.Count) earlier in $fromJoe)." }
$w = Waiting; if ($w) { $status += $w }
$statusNote = $status -join " "
$minutes = [int]$Cfg.wake_minutes
$prompt = (Get-Content "$Ctl\wake-prompt.md" -Raw).Replace("{WORKSPACE}", $Ws).Replace("{SITE_KEY_NOTE}", $siteNote).Replace("{STATUS_NOTE}", $statusNote)
Set-Content "$Out\prompt.txt" $prompt -Encoding UTF8 -NoNewline

$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine")
switch ($Cfg.harness) {
  "hermes" {
    $env:HERMES_HOME = "$HomeDir\.hermes"
    # Make sure the mind is registered as an MCP server (runner-owned block; rewritten every wake).
    $hpy = "C:\Program Files\hermes\Scripts\python.exe"
    $cfgPath = "$HomeDir\.hermes\config.yaml"
    $serverDef = @{ command = $py; args = @("$Ctl\mind\mind.py", "serve"); env = $mindEnv } | ConvertTo-Json -Compress -Depth 5
    Set-Content "$Out\mind-server.json" $serverDef -Encoding UTF8
    & $hpy -c "import json,sys,yaml; p=sys.argv[1]; c=yaml.safe_load(open(p,encoding='utf-8-sig')) or {}; c.setdefault('mcp_servers',{})['mind']=json.load(open(sys.argv[2],encoding='utf-8-sig')); yaml.safe_dump(c,open(p,'w',encoding='utf-8'),sort_keys=False,allow_unicode=True)" $cfgPath "$Out\mind-server.json" 2>&1 | ForEach-Object { Log "config: $_" }
    $exe = "C:\Program Files\hermes\Scripts\hermes.exe"
    $argv = @("-z", "`"$($prompt.Replace('"', "'").Replace("`r", '').Replace("`n", ' '))`"", "--usage-file", "`"$Out\usage.json`"")
    $stdin = $null
  }
  "dsh" {
    $env:DSH_HOME = "$HomeDir\.dsh"; $env:LLAMACPP_API_KEY = "local"; $env:DSH_TELEMETRY_MODE = "DISABLED"; $env:DSH_PERMISSION_MODE = "workspace-write"
    foreach ($k in $mindEnv.Keys) { Set-Item "env:$k" $mindEnv[$k] }
    if (-not (Test-Path "$Ctl\harness\dsh.mcp.patch.yml")) { Log "dsh MCP plugin not configured yet"; Lease $false; & "$Ctl\embedder.ps1" stop; Finish "skipped: dsh mcp not configured"; exit 0 }
    $exe = "C:\Program Files\dsh\dsh.cmd"
    $argv = @("--profile", "headless", "--patch", "`"$Ctl\harness\dsh.patch.yml`"", "--patch", "`"$Ctl\harness\dsh.mcp.patch.yml`"", "--json",
              "--permission-mode", "workspace-write", "--approval-policy", "never", "-")
    $stdin = "$Out\prompt.txt"
  }
  default { Log "unknown harness $($Cfg.harness)"; Lease $false; Finish "skipped: bad config"; exit 1 }
}

if ($DryRun) {
  Log "dry run: would start $exe $($argv -join ' ')"
  if ($Cfg.harness -eq "hermes") { (& "C:\Program Files\hermes\Scripts\hermes.exe" mcp test mind 2>&1) | ForEach-Object { Log "mcp test: $_" } }
  Lease $false; & "$Ctl\embedder.ps1" stop; Finish "dry run"; exit 0
}

$sw = [Diagnostics.Stopwatch]::StartNew(); $startedAt = Get-Date
$stall = if ($null -ne $Cfg.stall_minutes) { [int]$Cfg.stall_minutes } else { 60 }
$sp = @{ FilePath = $exe; ArgumentList = $argv; WorkingDirectory = $Ws; PassThru = $true; NoNewWindow = $true
         RedirectStandardOutput = "$Out\stdout.txt"; RedirectStandardError = "$Out\stderr.txt" }
if ($stdin) { $sp.RedirectStandardInput = $stdin }
$p = Start-Process @sp
Log "harness started (pid $($p.Id))"
Set-Content $seenFile $letters -Encoding UTF8
$outcome = "finished"
while (-not $p.WaitForExit(15000)) {
  Lease $true
  if (-not (Mine)) { $outcome = "preempted ($(GateState))"; break }
  if ($minutes -gt 0 -and $sw.Elapsed.TotalMinutes -gt $minutes) { $outcome = "timed out"; break }
  # Hang guard, not a work limit: the harness writes its session database on every model reply and tool call.
  $last = @("$HomeDir\.hermes\state.db-wal", "$HomeDir\.hermes\state.db", "$Out\stdout.txt") | Where-Object { Test-Path $_ } | ForEach-Object { (Get-Item $_).LastWriteTime } | Sort-Object | Select-Object -Last 1
  $idle = if ($last) { ((Get-Date) - $last).TotalMinutes } else { 0 }
  if ($stall -gt 0 -and $idle -gt $stall -and $sw.Elapsed.TotalMinutes -gt $stall) { $outcome = "stalled (no activity for $([int]$idle) min)"; break }
}
if (-not $p.HasExited) { taskkill /PID $p.Id /T /F 2>&1 | Out-Null; Log "harness stopped: $outcome" }
$meta.seconds = [math]::Round($sw.Elapsed.TotalSeconds)
$meta.exit_code = $(if ($outcome -eq "finished") { $p.ExitCode } else { $null })
Log "harness ended: $outcome"

# ── 5. close out ──
$lastWords = "$Out\last-words.txt"
$tail = if (Test-Path "$Out\stdout.txt") { (Get-Content "$Out\stdout.txt" -Raw -Encoding UTF8) } else { "" }
if ($tail.Length -gt 1500) { $tail = $tail.Substring($tail.Length - 1500) }
Set-Content $lastWords ("(The waking ended without reflect_end: $outcome.) Last output: " + $tail) -Encoding UTF8
$env:MIND_DB = $mindEnv.MIND_DB
$closeArgs = @("$Ctl\mind\mind.py", "close", "--summary-file", $lastWords)
if ($outcome -ne "finished") { $closeArgs += "--interrupted" }
Log ("mind: " + ((& $py @closeArgs 2>&1) -join " "))

if ((& $git -C $Ws status --porcelain 2>&1) -join "") {
  & $git -C $Ws add -A 2>&1 | Out-Null
  & $git -C $Ws commit -q -m "auto: end of waking $stamp ($outcome)" 2>&1 | Out-Null
  Log "safety commit made"
}
Lease $false
& "$Ctl\embedder.ps1" stop
Finish $outcome
