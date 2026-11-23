<#
  Turn-taking test for gpu-gate.ps1, in dry-run mode against a temporary folder (no model, no GPU needed).
  Usage: powershell -ExecutionPolicy Bypass -File test-gate.ps1
#>
$ErrorActionPreference = "Stop"
$root = Join-Path $env:TEMP "gate-test-$PID"
New-Item -ItemType Directory -Force "$root\lease" | Out-Null
@{ busy_process_names = @("NoSuchProcess_$PID"); ignore_gpu_processes = @(); other_gpu_mb = 999999; idle_minutes = 0; lease_timeout_s = 6; port = 8099
   llama_server = "none"; model = "a.gguf"; alias = "a"; ctx = 1; extra_args = @(); models = @{ gm = @{ model = "b.gguf"; alias = "gm"; ctx = 1; extra_args = @() } } } |
  ConvertTo-Json -Depth 5 | Set-Content "$root\gate.json" -Encoding ASCII
$gate = Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSScriptRoot\gpu-gate.ps1`"", "-DryRun", "-Root", "`"$root`"" -PassThru -WindowStyle Hidden
$fails = 0
function Lease($file, $holder, $want) { @{ holder = $holder; want = $want; heartbeat = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } | ConvertTo-Json -Compress | Set-Content "$root\lease\$file" -Encoding ASCII }
function State { for ($i = 0; $i -lt 20; $i++) { try { return (Get-Content "$root\state.json" -Raw | ConvertFrom-Json) } catch { Start-Sleep -Milliseconds 200 } } }
function Check($cond, $msg) { if ($cond) { "  ok   $msg" } else { "  FAIL $msg  (state: $((State) | ConvertTo-Json -Compress -Depth 4))"; $script:fails++ } }
function Hold($sec, [scriptblock]$beat) { $end = (Get-Date).AddSeconds($sec); while ((Get-Date) -lt $end) { & $beat; Start-Sleep 1 } }
try {
  Hold 5 {}
  Check ((State).state -eq "idle" -and (State).holder -eq "") "idle with nobody asking"

  Hold 6 { Lease "lease.json" "inkling" $true }
  Check ((State).state -eq "ready" -and (State).holder -eq "inkling") "first agent asks and is served"

  Hold 6 { Lease "lease.json" "inkling" $true; Lease "gm.json" "gm" $true }
  $s = State
  Check ($s.state -eq "ready" -and $s.holder -eq "inkling") "second agent asks; the first keeps the card"
  Check (@($s.queue).Count -eq 1 -and $s.queue[0].holder -eq "gm") "the second agent is listed as waiting"

  Hold 12 { Lease "lease.json" "inkling" $true; Lease "gm.json" "gm" $true }
  Check ((State).holder -eq "inkling") "no time limit: still the first agent's after a long hold"

  Lease "lease.json" "inkling" $false
  Hold 7 { Lease "gm.json" "gm" $true }
  $s = State
  Check ($s.state -eq "ready" -and $s.holder -eq "gm" -and @($s.queue).Count -eq 0) "first agent says done; the card passes to the waiting agent"

  Hold 6 { Lease "gm.json" "gm" $true; Lease "lease.json" "inkling" $true }
  $s = State
  Check ($s.holder -eq "gm" -and $s.queue[0].holder -eq "inkling") "roles reversed: first agent now waits its turn"

  Hold 10 { Lease "lease.json" "inkling" $true }      # gm stops heart-beating without releasing (crashed)
  Check ((State).holder -eq "inkling") "a holder that goes silent loses the card after the lease timeout"

  Lease "lease.json" "inkling" $false
  Hold 12 {}
  Check ((State).state -eq "idle" -and (State).holder -eq "") "idle again when everyone is done"
  "--- gate log"; Get-Content "$root\gate.log" | ForEach-Object { "  $_" }
} finally {
  Stop-Process -Id $gate.Id -Force -ErrorAction SilentlyContinue
  Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
}
if ($fails) { "`n$fails FAILED"; exit 1 } else { "`nall good" }
