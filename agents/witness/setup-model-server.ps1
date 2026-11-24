# The Titan box's shared model server: Gemma 4 12B loaded once and kept loaded, with one slot per agent, so agents don't
# load and unload the model for every waking and can be awake at the same time. Run as an administrator. Safe to run again.
# It runs as LOCAL SERVICE (no password, no admin rights) and answers only on 127.0.0.1.
$ErrorActionPreference = "Stop"
$m = "D:\Model"
$slots = 2              # agents that can think at once; each gets the full context
$ctx = 49152            # per agent

# llama.cpp and the model live here, shared (moved from D:\Witness on first run)
New-Item -ItemType Directory -Force $m, "$m\logs" | Out-Null
foreach ($d in "llama", "models") { if ((Test-Path "D:\Witness\$d") -and -not (Test-Path "$m\$d")) { Move-Item "D:\Witness\$d" "$m\$d" } }
& icacls.exe $m /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "*S-1-5-19:(OI)(CI)RX" /Q | Out-Null   # S-1-5-19 = LOCAL SERVICE
& icacls.exe "$m\logs" /grant:r "*S-1-5-19:(OI)(CI)M" /Q | Out-Null

$model = (Get-ChildItem "$m\models\*.gguf" | Select-Object -First 1).FullName
$args = "-m `"$model`" -c $($ctx * $slots) -np $slots -t 4 -tb 4 --host 127.0.0.1 --port 8093 -ngl 99 -fa on -ctk q8_0 -ctv q8_0 --log-file `"$m\logs\server.log`""
$action = New-ScheduledTaskAction -Execute "$m\llama\llama-server.exe" -Argument $args -WorkingDirectory "$m\llama"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\LOCAL SERVICE" -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Priority 7
Register-ScheduledTask -TaskName "Titan-Model" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask "Titan-Model"

$ok = $false
for ($i = 0; $i -lt 90; $i++) { Start-Sleep 2; try { if ((Invoke-RestMethod http://127.0.0.1:8093/health -TimeoutSec 2).status -eq "ok") { $ok = $true; break } } catch {} }
"model server: " + $(if ($ok) { "ready after $($i * 2)s" } else { "NOT ready" }) + ", task " + (Get-ScheduledTask Titan-Model).State
& nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
if (-not $ok) { Get-Content "$m\logs\server.log" -Tail 15 -ErrorAction SilentlyContinue }
