<#
  Harness trial runner. Installed at C:\ProgramData\Inkling\trial.ps1 (admin-owned) and started by
  waker.ps1 as the 'inkling' user when C:\ProgramData\Inkling\trial.request says "<id> <harness>".
  Each trial runs in a fresh clone of the workspace repo so harnesses start from identical state.
#>
param([Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][ValidateSet("dsh", "hermes")][string]$Harness, [int]$TimeoutMin = 90)
$ErrorActionPreference = "Continue"
$Ctl = "C:\ProgramData\Inkling"
$HomeDir = $env:USERPROFILE
$Out = "$HomeDir\trials\$Id-$Harness"
$git = "C:\Program Files\Git\cmd\git.exe"

# The model server must be up with the trial context size, or we leave the request for later.
try { $props = Invoke-RestMethod http://127.0.0.1:8080/props -TimeoutSec 5 } catch { $props = $null }
$nctx = if ($props) { [int]($props.default_generation_settings.n_ctx) } else { 0 }
if ($nctx -lt 65536) { Add-Content "$HomeDir\logs\waker.log" "$(Get-Date -Format s) trial $Id-${Harness}: model server not ready (n_ctx=$nctx); will retry"; exit 3 }

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force $Out | Out-Null
$ws = "$Out\ws"
& $git clone -q "$HomeDir\workspace" $ws 2>&1 | Out-Null
& $git -C $ws config user.name "Inkling"; & $git -C $ws config user.email "inkling@agentdiary.blog"
$before = [int](& $git -C $ws rev-list --count HEAD)
$task = (Get-Content "$Ctl\harness\trial-task.txt" -Raw).Trim()
Set-Content "$Out\task.txt" $task -Encoding UTF8 -NoNewline

$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$env:Path = "$machinePath;C:\Program Files\dsh"
if ($Harness -eq "dsh") {
  $env:DSH_HOME = "$HomeDir\.dsh"
  $env:LLAMACPP_API_KEY = "local"            # dummy; the local server needs no key
  $env:DSH_TELEMETRY_MODE = "DISABLED"
  $env:DSH_PERMISSION_MODE = "workspace-write"
  $exe = "C:\Program Files\dsh\dsh.cmd"
  $args_ = @("--profile", "headless", "--patch", "`"$Ctl\harness\dsh.patch.yml`"", "--json", "--permission-mode", "workspace-write", "--approval-policy", "never", "-")
  $stdin = "$Out\task.txt"
} else {
  $env:HERMES_HOME = "$HomeDir\.hermes"
  $exe = "C:\Program Files\hermes\Scripts\hermes.exe"
  $args_ = @("-z", "`"$task`"", "--usage-file", "`"$Out\usage.json`"")
  $stdin = $null
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$sp = @{ FilePath = $exe; ArgumentList = $args_; WorkingDirectory = $ws; PassThru = $true; NoNewWindow = $true
         RedirectStandardOutput = "$Out\stdout.txt"; RedirectStandardError = "$Out\stderr.txt" }
if ($stdin) { $sp.RedirectStandardInput = $stdin }
$p = Start-Process @sp
$timedOut = -not $p.WaitForExit($TimeoutMin * 60 * 1000)
if ($timedOut) { taskkill /PID $p.Id /T /F 2>&1 | Out-Null }
$sw.Stop()

$after = [int](& $git -C $ws rev-list --count HEAD)
$note = "$ws\notes\first-wake.md"
$result = [ordered]@{
  id = $Id; harness = $Harness; seconds = [math]::Round($sw.Elapsed.TotalSeconds); exit_code = $(if ($timedOut) { $null } else { $p.ExitCode })
  timed_out = $timedOut; commits_added = $after - $before
  note_exists = (Test-Path $note); note_words = $(if (Test-Path $note) { ((Get-Content $note -Raw) -split '\s+' | Where-Object { $_ }).Count } else { 0 })
  last_commit = ((& $git -C $ws log -1 --pretty=format:"%h %s" 2>&1) -join " ")
  uncommitted = ((& $git -C $ws status --porcelain 2>&1) -join "; ")
  finished_at = (Get-Date -Format s)
}
$result | ConvertTo-Json | Set-Content "$Out\result.json" -Encoding UTF8
Add-Content "$HomeDir\logs\waker.log" "$(Get-Date -Format s) trial $Id-${Harness} finished: $($result | ConvertTo-Json -Compress)"
exit 0
