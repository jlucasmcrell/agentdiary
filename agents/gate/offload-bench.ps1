<#
  Offload benchmark for the main model on the 6900XT. Run as jluca (the model file is admin/jluca-only)
  when the GPU is free:   powershell -File offload-bench.ps1 -Configs configs.json
  configs.json: [ { "name": "baseline", "args": ["-c","32768"] }, ... ]  (common args are added below)
  For each config: start llama-server (verbose), wait until healthy, record VRAM + buffer sizes,
  run the same prompt-processing + generation benchmark twice, stop. Results -> offload-results.json
#>
param([Parameter(Mandatory)][string]$Configs, [string]$Out = "$PSScriptRoot\offload-results.json", [int]$Port = 8090)
$ErrorActionPreference = "Continue"
# Only one benchmark at a time: a second run would kill the first one's server on the same port.
$lockPath = "$env:TEMP\offload-bench.lock"
$lock = $null
try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') } catch { Write-Host "another offload benchmark is already running; exiting"; exit 2 }
$runId = Get-Date -Format "HHmmss"
$llama = "C:\Program Files\llama.cpp\llama-server.exe"
$m = "C:\ProgramData\Models\inkling-2-mini"
$common = @("-m", "$m\Inkling-2-Mini-UD-Q4_K_M.gguf", "--mmproj", "$m\mmproj-F16.gguf", "--jinja", "--host", "127.0.0.1", "--port", "$Port", "-lv", "4")
$cfgs = Get-Content $Configs -Raw | ConvertFrom-Json
# ~1500-token prompt: a fixed text repeated, so every config reads exactly the same input.
$para = "The moth circled the lantern for the third time that night, cataloguing every flicker as if it were a letter in a language it almost understood. "
$prompt = "Read the following notes, then write a short reflection.`n`n" + ($para * 55) + "`n`nReflection:"

function VramMB($procId) {
  $s = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object { $_.InstanceName -match "pid_$($procId)_" }
  [math]::Round((($s | Measure-Object CookedValue -Sum).Sum) / 1MB)
}
function Bench {
  $body = @{ prompt = $prompt; n_predict = 200; temperature = 0; cache_prompt = $false } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post "http://127.0.0.1:$Port/completion" -Body $body -ContentType "application/json" -TimeoutSec 1200
  [ordered]@{ prompt_tokens = $r.timings.prompt_n; prompt_tps = [math]::Round($r.timings.prompt_per_second, 1)
              gen_tokens = $r.timings.predicted_n; gen_tps = [math]::Round($r.timings.predicted_per_second, 2) }
}

$results = @()
foreach ($c in $cfgs) {
  Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $llama -and (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "--port $Port" } | Stop-Process -Force
  $log = "$env:TEMP\offload-$runId-$(($c.name -replace '[^\w-]+', '_')).log"
  $args_ = $common + @($c.args)
  Write-Host "== $($c.name): $($c.args -join ' ')"
  $p = Start-Process -FilePath $llama -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardError $log
  $sw = [Diagnostics.Stopwatch]::StartNew(); $ok = $false
  while ($sw.Elapsed.TotalSeconds -lt 240 -and -not $p.HasExited) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
  }
  $r = [ordered]@{ name = $c.name; args = ($c.args -join " "); loaded = $ok; load_s = [math]::Round($sw.Elapsed.TotalSeconds) }
  if ($ok) {
    Start-Sleep 2
    $r.vram_mb = VramMB $p.Id
    $r.ram_mb = [math]::Round((Get-Process -Id $p.Id).WorkingSet64 / 1MB)
    try { $r.run1 = Bench; $r.run2 = Bench } catch { $r.error = "$($_.Exception.Message)" }
    $r.vram_peak_mb = VramMB $p.Id
    $r.fa_disabled = [bool](Select-String -Path $log -Pattern "Flash Attention not supported" -Quiet)
  } else { $r.error = "failed to load (exit $(if ($p.HasExited) { $p.ExitCode } else { 'timeout' }))" }
  $r.buffers = @(Get-Content $log -ErrorAction SilentlyContinue | Select-String -Pattern "buffer size|offloaded|fit|Flash Attention|layers to GPU|n_gpu_layers|repack" | ForEach-Object { ($_.Line.Trim() -replace "\s+", " ") } | Select-Object -First 40)
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
  Start-Sleep 3
  $results += [pscustomobject]$r
  $results | ConvertTo-Json -Depth 6 | Set-Content $Out -Encoding UTF8
  Write-Host ("   vram {0} MB, gen {1} t/s, prompt {2} t/s, FA off: {3}" -f $r.vram_mb, $r.run2.gen_tps, $r.run2.prompt_tps, $r.fa_disabled)
}
