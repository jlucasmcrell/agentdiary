<#
  GPU gate: serves the agent's main model only when the GPU is free, and pre-empts it the moment it isn't.
  Runs as the box owner (jluca), never as the agent. The agent can't read the model file, so this is
  the only way the big model gets onto the GPU.

  Protocol (files in C:\ProgramData\InklingGate):
    gate.json            config (admin-owned)
    state.json           written here, readable by everyone:
                         { state: idle|busy|waiting|loading|ready|preempted|disabled, reason, since, updated, url }
    lease\lease.json     written by the agent: { holder, want: true|false, heartbeat: <unix seconds> }
                         A lease is live while want=true and the heartbeat is < lease_timeout_s old.
    disabled             if this file exists, nothing is served

  Test mode: -DryRun pretends to serve (no llama-server); -BusyName overrides the "busy" process name.
#>
param([switch]$DryRun, [string]$BusyName = "", [double]$IdleMinutes = -1, [int]$OtherGpuLimitMB = -1, [int]$MaxLoops = 0)
$ErrorActionPreference = "Continue"
$Root = "C:\ProgramData\InklingGate"
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

function Log($m) { Add-Content $log "$(Get-Date -Format s) $m" }
function Publish($state, $reason) {
  if ($state -ne $script:state) { Log "$state $reason"; $script:since = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
  $script:state = $state
  $o = [ordered]@{ state = $state; reason = $reason; since = $script:since; updated = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); url = $url }
  $tmp = "$Root\state.json.tmp"
  $o | ConvertTo-Json -Compress | Set-Content $tmp -Encoding ASCII
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

function LeaseLive {
  $f = "$Root\lease\lease.json"
  if (-not (Test-Path $f)) { return $false }
  try { $l = Get-Content $f -Raw | ConvertFrom-Json } catch { return $false }
  $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int]$l.heartbeat
  return ($l.want -eq $true -and $age -lt [int]$Cfg.lease_timeout_s)
}

function Serving { $DryRun -and $script:dryServing -or ($script:proc -and -not $script:proc.HasExited) }

function StartModel {
  if ($DryRun) { $script:dryServing = $true; return $true }
  $a = @("-m", $Cfg.model, "--jinja", "-c", "$($Cfg.ctx)", "--host", "127.0.0.1", "--port", "$port", "-a", $Cfg.alias) + @($Cfg.extra_args)
  if ($Cfg.mmproj) { $a += @("--mmproj", $Cfg.mmproj) }
  $script:proc = Start-Process -FilePath $Cfg.llama_server -ArgumentList $a -WindowStyle Hidden -PassThru -RedirectStandardError "$Root\llama.log"
  for ($i = 0; $i -lt 120; $i++) {
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
  if (Test-Path "$Root\disabled") { if (Serving) { StopModel }; Publish "disabled" "gate disabled by admin"; Start-Sleep 5; continue }

  $busy = BusyReason
  if ($busy) {
    $script:lastBusy = Get-Date
    if (Serving) { StopModel; Publish "preempted" $busy } elseif ($script:state -ne "preempted" -or -not (LeaseLive)) { Publish "busy" $busy }
    Start-Sleep 3; continue
  }

  $want = LeaseLive
  if (-not $want) {
    if (Serving) { StopModel; Publish "idle" "lease released or expired" } else { Publish "idle" "" }
    Start-Sleep 3; continue
  }

  if (Serving) { Publish "ready" ""; Start-Sleep 3; continue }

  $quiet = ((Get-Date) - $script:lastBusy).TotalMinutes
  if ($quiet -lt $idleNeeded) { Publish "waiting" ("GPU quiet {0:N1}/{1} min" -f $quiet, $idleNeeded); Start-Sleep 3; continue }

  Publish "loading" ""
  if (StartModel) { Publish "ready" "" }
  else { StopModel; Publish "busy" "model failed to load or was pre-empted while loading"; $script:lastBusy = Get-Date }
  Start-Sleep 3
}
