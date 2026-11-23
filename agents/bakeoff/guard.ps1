# GPU guard for the bake-off (runs on the 6900XT as jluca, over SSH).
# Waits until the owner's GPU workload ($BusyProcess) has been absent for $IdleMinutes, serves models, and pre-empts
# (kills llama-server, unloads Ollama models) the moment it starts again.
# Control/status files in $Dir:  status.txt (written by guard)  phase.txt (inkling|qwen|done, written by driver)
param([int]$IdleMinutes = 10, [string]$BusyProcess = $env:GPU_BUSY_PROCESS)
$Dir = "$env:USERPROFILE\bakeoff-guard"
New-Item -ItemType Directory -Force $Dir | Out-Null
$StatusFile = "$Dir\status.txt"; $PhaseFile = "$Dir\phase.txt"   # distinct names: PowerShell variables are case-insensitive
function Say($s) { Set-Content $StatusFile $s; Add-Content "$Dir\guard.log" "$(Get-Date -Format s) $s" }
function Busy { $BusyProcess -and [bool](Get-Process $BusyProcess -ErrorAction SilentlyContinue) }
function StopModels {
  Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
  try { Invoke-RestMethod -Method Post http://127.0.0.1:11434/api/generate -Body '{"model":"qwen3:14b","keep_alive":0}' -ContentType application/json -TimeoutSec 10 | Out-Null } catch {}
}
$llama = "C:\Program Files\llama.cpp\llama-server.exe"
$m = "C:\ProgramData\Models\inkling-2-mini"
$serving = ""

while ($true) {
  $phase = if (Test-Path $PhaseFile) { (Get-Content $PhaseFile -Raw).Trim() } else { "inkling" }
  if ($phase -eq "done") { StopModels; Say "done"; break }

  if (Busy) {
    if ($serving) { StopModels; $serving = ""; Say "preempted" } else { Say "waiting: $BusyProcess running" }
    $idleSince = $null; Start-Sleep 5; continue
  }
  if (-not $serving) {
    if (-not $idleSince) { $idleSince = Get-Date }
    $idle = ((Get-Date) - $idleSince).TotalMinutes
    if ($idle -lt $IdleMinutes) { Say ("waiting: GPU idle {0:N1}/{1} min" -f $idle, $IdleMinutes); Start-Sleep 15; continue }
  }
  if ($phase -ne $serving) {
    StopModels
    if ($phase -eq "inkling" -or $phase -eq "trial") {
      $ctx = "65536"   # 8-bit KV cache makes 64K as fast as 32K (benchmarked); Hermes requires >= 64K
      Start-Process -FilePath $llama -WindowStyle Hidden -RedirectStandardError "$Dir\llama.log" -ArgumentList @(
        "-m", "$m\Inkling-2-Mini-UD-Q4_K_M.gguf", "--mmproj", "$m\mmproj-F16.gguf", "--jinja", "-c", $ctx,
        "--host", "127.0.0.1", "--port", "8080", "-a", "inkling", "--temp", "1.0", "--top-p", "1.0", "--top-k", "0", "--min-p", "0.0",
        # benchmarked offload: see agents/gate/gate.json
        "-np", "1", "-ngl", "99", "-ub", "256", "-b", "1024", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0",
        "-ot", 'blk\.([0-3]|2[0-3])\.ffn_.*_exps\.=CPU')
      for ($i = 0; $i -lt 60; $i++) { Start-Sleep 3; try { Invoke-RestMethod http://127.0.0.1:8080/health -TimeoutSec 3 | Out-Null; break } catch {} }
    }
    $serving = $phase
    Say "ready: $phase"
  }
  Start-Sleep 5
}
