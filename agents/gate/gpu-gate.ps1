<#
  GPU gate: serves one agent's model at a time, only when the GPU is free, and pre-empts it the moment it isn't.
  Runs as the box owner, never as an agent. Agents can't read the model files, so this is the only way a
  model gets onto the GPU.

  Turn-taking (no clocks): an agent asks for the card by keeping a live lease. Whoever holds the card keeps
  it for as long as its lease stays live, however long that is. When it lets go ("done for now"), the agent
  that has been waiting longest is served next, with its own model.

  Protocol (files in C:\ProgramData\InklingGate):
    gate.json            config (admin-owned). "models" maps a holder name to its model settings; the
                         top-level model settings are used for any holder without an entry.
    state.json           written here, readable by everyone:
                         { state: idle|busy|waiting|loading|ready|preempted|disabled, reason, since, updated, url,
                           holder: <who the model is loaded for, or "">,
                           queue: [ { holder, label, since } ]   others waiting, longest first }
    lease\<name>.json    written by an agent's runner: { holder, want: true|false, heartbeat: <unix seconds> }
                         A lease is live while want=true and the heartbeat is < lease_timeout_s old.
                         (lease.json is the first agent's file, kept for compatibility.)
    disabled             if this file exists, nothing is served

  Test mode: -DryRun pretends to serve (no llama-server); -BusyName overrides the "busy" process name.
#>
param([switch]$DryRun, [string]$BusyName = "", [double]$IdleMinutes = -1, [int]$OtherGpuLimitMB = -1, [int]$MaxLoops = 0, [string]$Root = "C:\ProgramData\InklingGate")
$ErrorActionPreference = "Continue"
$Cfg = Get-Content "$Root\gate.json" -Raw | ConvertFrom-Json
$busyNames = if ($BusyName) { @($BusyName) } else { @($Cfg.busy_process_names) }
$idleNeeded = if ($IdleMinutes -ge 0) { $IdleMinutes } else { [double]$Cfg.idle_minutes }
$otherLimit = if ($OtherGpuLimitMB -ge 0) { $OtherGpuLimitMB } else { [int]$Cfg.other_gpu_mb }
$ignore = @($Cfg.ignore_gpu_processes)
$port = [int]$Cfg.port
$url = "http://127.0.0.1:$port/v1"
$log = "$Root\gate.log"

$script:proc = $null
$script:lastBusy = Get-Date
$script:state = ""
$script:owner = ""          # holder the model is (being) served to
$script:firstSeen = @{}     # holder -> unix time it started wanting the card

function Now { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function Log($m) { Add-Content $log "$(Get-Date -Format s) $m" }
function LabelFor($holder) { if ($Cfg.models -and $Cfg.models.$holder -and $Cfg.models.$holder.label) { $Cfg.models.$holder.label } else { $holder } }
function Publish($state, $reason) {
  $key = "$state|$($script:owner)"
  if ($key -ne $script:stateKey) { Log "$state $reason $(if ($script:owner) { "[$($script:owner)]" })"; $script:since = Now }
  $script:stateKey = $key; $script:state = $state
  $queue = @($script:firstSeen.GetEnumerator() | Where-Object { $_.Key -ne $script:owner } | Sort-Object Value | ForEach-Object { [ordered]@{ holder = $_.Key; label = (LabelFor $_.Key); since = $_.Value } })
  $o = [ordered]@{ state = $state; reason = $reason; since = $script:since; updated = (Now); url = $url; holder = $script:owner; queue = $queue }
  $tmp = "$Root\state.json.tmp"
  ConvertTo-Json $o -Compress -Depth 4 | Set-Content $tmp -Encoding ASCII
  Move-Item -Force $tmp "$Root\state.json"
}

function OtherGpuMB {
  try {
    $own = if ($script:proc) { $script:proc.Id } else { -1 }
    $sum = 0
    foreach ($s in (Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples) {
      if ($s.InstanceName -notmatch 'pid_(\d+)') { continue }
      $pid_ = [int]$Matches[1]
      if ($pid_ -eq $own) { continue }
      $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
      if (-not $p -or $ignore -contains $p.ProcessName) { continue }
      $sum += $s.CookedValue
    }
    [math]::Round($sum / 1MB)
  } catch { 0 }
}

function BusyReason {
  foreach ($n in $busyNames) { if (Get-Process -Name $n -ErrorAction SilentlyContinue) { return "$n running" } }
  $mb = OtherGpuMB
  if ($mb -gt $otherLimit) { return "other GPU use ${mb} MB" }
  return $null
}

# Refreshes $script:firstSeen from the lease files: every holder with a live lease, and when it started asking.
function ReadLeases {
  $live = @{}
  foreach ($f in (Get-ChildItem "$Root\lease" -Filter *.json -ErrorAction SilentlyContinue)) {
    try { $l = Get-Content $f.FullName -Raw | ConvertFrom-Json } catch { continue }
    $name = "$($l.holder)"
    if (-not $name -or $name -notmatch '^[a-z0-9_-]{1,32}$') { continue }
    if ($l.want -eq $true -and ((Now) - [int]$l.heartbeat) -lt [int]$Cfg.lease_timeout_s) { $live[$name] = $true }
  }
  foreach ($k in @($script:firstSeen.Keys)) { if (-not $live.ContainsKey($k)) { $script:firstSeen.Remove($k) } }
  foreach ($k in $live.Keys) { if (-not $script:firstSeen.ContainsKey($k)) { $script:firstSeen[$k] = (Now) } }
}
function NextHolder { ($script:firstSeen.GetEnumerator() | Sort-Object Value | Select-Object -First 1).Key }

function ModelFor($holder) {
  $m = if ($Cfg.models -and $Cfg.models.$holder) { $Cfg.models.$holder } else { $Cfg }
  [ordered]@{ model = $m.model; mmproj = $m.mmproj; alias = $(if ($m.alias) { $m.alias } else { $holder }); ctx = $m.ctx; extra_args = @($m.extra_args) }
}

function Serving { $DryRun -and $script:dryServing -or ($script:proc -and -not $script:proc.HasExited) }

function StartModel($holder) {
  if ($DryRun) { $script:dryServing = $true; return $true }
  $m = ModelFor $holder
  $a = @("-m", $m.model, "--jinja", "-c", "$($m.ctx)", "--host", "127.0.0.1", "--port", "$port", "-a", $m.alias) + @($m.extra_args)
  if ($m.mmproj) { $a += @("--mmproj", $m.mmproj) }
  $script:proc = Start-Process -FilePath $Cfg.llama_server -ArgumentList $a -WindowStyle Hidden -PassThru -RedirectStandardError "$Root\llama.log"
  for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep 1
    if ($script:proc.HasExited) { return $false }
    if (BusyReason) { return $false }   # pre-empted while loading
    try { Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2 | Out-Null; return $true } catch {}
  }
  return $false
}

function StopModel {
  $script:dryServing = $false
  if ($script:proc -and -not $script:proc.HasExited) { Stop-Process -Id $script:proc.Id -Force -ErrorAction SilentlyContinue }
  $script:proc = $null
}

Log "gate starting (dry=$DryRun busy=$($busyNames -join ',') idle=${idleNeeded}m)"
$loops = 0
while ($true) {
  $loops++
  if ($MaxLoops -and $loops -gt $MaxLoops) { StopModel; break }
  if (Test-Path "$Root\disabled") { if (Serving) { StopModel }; $script:owner = ""; Publish "disabled" "gate disabled by admin"; Start-Sleep 5; continue }

  ReadLeases
  $busy = BusyReason
  if ($busy) {
    $script:lastBusy = Get-Date
    if (Serving) { StopModel; Publish "preempted" $busy }
    elseif ($script:state -eq "preempted" -and $script:owner -and $script:firstSeen.ContainsKey($script:owner)) { Publish "preempted" $busy }   # until its runner notices
    else { $script:owner = ""; Publish "busy" $busy }
    Start-Sleep 3; continue
  }

  # The current holder keeps the card for as long as it wants it.
  if ($script:owner -and -not $script:firstSeen.ContainsKey($script:owner)) {
    if (Serving) { StopModel }
    Log "released by $($script:owner)"
    $script:owner = ""
  }
  if (-not $script:owner) {
    $next = NextHolder
    if (-not $next) { Publish "idle" ""; Start-Sleep 3; continue }
    $script:owner = $next
  }

  if (Serving) { Publish "ready" ""; Start-Sleep 3; continue }

  $quiet = ((Get-Date) - $script:lastBusy).TotalMinutes
  if ($quiet -lt $idleNeeded) { Publish "waiting" ("GPU quiet {0:N1}/{1} min" -f $quiet, $idleNeeded); Start-Sleep 3; continue }

  Publish "loading" ""
  if (StartModel $script:owner) { Publish "ready" "" }
  else { StopModel; Publish "busy" "model failed to load or was pre-empted while loading"; $script:lastBusy = Get-Date }
  Start-Sleep 3
}
