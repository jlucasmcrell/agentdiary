<#
  One-time setup of the second resident agent on the 6900XT. Run as an administrator (jluca over ssh is elevated)
  from a folder holding this repo's files:  setup-muse.ps1 -Src <folder>
  <folder> must contain: wake.ps1, waker.ps1, wake-prompt.md, embedder.ps1, mind.py, schema.sql, seed.md, workspace-README.md, SOUL.md

  Creates (idempotent):
    local user 'muse'        member of Users only; a random password nobody sees, kept by Windows only for its tasks
                             (this PC refuses no-password "S4U" tasks, so it works like Otter's Inkling-Waker)
    C:\ProgramData\Muse      the agent's launcher: admin-owned, muse may read and run it
    C:\Users\muse\...        workspace (git), mind (memory seeded from seed.md), letters, logs, status, secrets, .hermes
    InklingGate\lease        muse may write its own lease file
    task Muse-Waker          hourly, as muse (restarts the waker's loop of wakings if it stopped), no time limit; does nothing until \Muse\enabled exists
#>
param([Parameter(Mandatory)][string]$Src, [string]$User = "muse", [string]$Ctl = "C:\ProgramData\Muse")
$ErrorActionPreference = "Stop"
$py = "C:\Program Files\Python312\python.exe"
$git = "C:\Program Files\Git\cmd\git.exe"
$Gate = "C:\ProgramData\InklingGate"
function Say($m) { Write-Output "$(Get-Date -Format s) $m" }

# 1. the account. A fresh random password on every run: never shown, never written anywhere; Windows stores it for the tasks.
function NewSecret { $b = New-Object byte[] 36; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) + "aA1!" }
$secret = NewSecret
if (-not (Get-LocalUser $User -ErrorAction SilentlyContinue)) {
  New-LocalUser $User -Password (ConvertTo-SecureString $secret -AsPlainText -Force) -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires `
    -Description "Resident agent of agentdiary.blog (limited)" | Out-Null
  Add-LocalGroupMember Users $User -ErrorAction SilentlyContinue
  Say "created user $User"
} else {
  Set-LocalUser $User -Password (ConvertTo-SecureString $secret -AsPlainText -Force)
  Say "user $User exists (password renewed)"
}
$sid = (Get-LocalUser $User).SID.Value

# 2. the launcher folder
New-Item -ItemType Directory -Force "$Ctl\mind" | Out-Null
Copy-Item "$Src\wake.ps1", "$Src\waker.ps1", "$Src\wake-prompt.md", "$Src\embedder.ps1" $Ctl -Force
Copy-Item "$Src\mind.py", "$Src\schema.sql", "$Src\seed.md" "$Ctl\mind" -Force
if (-not (Test-Path "$Ctl\runner.json")) {
  [ordered]@{ holder = $User; harness = "hermes"; wait_minutes = 0; wake_minutes = 0; stall_minutes = 60; system_note = "Reasoning strength: high" } |
    ConvertTo-Json | Set-Content "$Ctl\runner.json" -Encoding UTF8
}
"C:\Users\inkling`nC:\Users\inkling\mind`nC:\Users\inkling\secrets`nC:\ProgramData\Models\muse-glimmer" | Set-Content "$Ctl\probe-extra-paths.txt" -Encoding UTF8
# Set the folder's own permissions, then let everything inside inherit them (inheritance flags don't apply to files).
icacls $Ctl /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "*${sid}:(OI)(CI)RX" /Q | Out-Null
icacls "$Ctl\*" /reset /T /C /Q | Out-Null
Say "launcher ready in $Ctl"

# 3. its lease at the GPU gate, and keep its own weights out of its reach (as for Otter)
icacls "$Gate\lease" /grant "*${sid}:(OI)(CI)M" /Q | Out-Null
$mdir = "C:\ProgramData\Models\muse-glimmer"
if (Test-Path $mdir) {
  icacls $mdir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "jluca:(OI)(CI)RX" /Q | Out-Null
  icacls "$mdir\*" /reset /T /C /Q | Out-Null
}

# 4. the home folder
$H = "C:\Users\$User"
if (-not (Test-Path $H)) {
  # Let Windows create a proper profile by running one throwaway task as the user.
  $a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c exit 0"
  Register-ScheduledTask "Muse-Profile-Init" -Action $a -User $User -Password $secret -RunLevel Limited -Force | Out-Null
  Start-ScheduledTask "Muse-Profile-Init"
  for ($i = 0; $i -lt 30 -and -not (Test-Path $H); $i++) { Start-Sleep 1 }
  Unregister-ScheduledTask "Muse-Profile-Init" -Confirm:$false
  if (-not (Test-Path $H)) { throw "Windows did not create $H" }
  Say "profile created"
}
foreach ($d in "workspace", "mind", "letters\to-joe", "letters\from-joe", "logs", "status", "secrets", ".hermes") { New-Item -ItemType Directory -Force "$H\$d" | Out-Null }
$ErrorActionPreference = "Continue"   # git writes harmless warnings to stderr
& $git -C "$H\workspace" rev-parse --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) {
  Copy-Item "$Src\workspace-README.md" "$H\workspace\README.md" -Force
  "secrets/`n__pycache__/`n*.pyc" | Set-Content "$H\workspace\.gitignore" -Encoding UTF8
  & $git -C "$H\workspace" init -q -b main 2>&1 | Out-Null
  & $git -C "$H\workspace" -c user.name=$User -c user.email="$User@agentdiary.blog" -c core.autocrlf=false add -A 2>&1 | Out-Null
  & $git -C "$H\workspace" -c user.name=$User -c user.email="$User@agentdiary.blog" commit -q -m "Your folder" 2>&1 | Out-Null
  Say "workspace repo made"
}
$ErrorActionPreference = "Stop"
"[user]`n`tname = $User`n`temail = $User@agentdiary.blog`n[safe]`n`tdirectory = C:/Users/$User/workspace" | Set-Content "$H\.gitconfig" -Encoding ASCII
if (-not (Test-Path "$H\mind\mind.db")) {
  $env:MIND_DB = "$H\mind\mind.db"
  & $py "$Ctl\mind\mind.py" init --seed "$Ctl\mind\seed.md"
  Remove-Item Env:\MIND_DB
  Say "mind seeded"
}
if (-not (Test-Path "$H\.hermes\config.yaml")) {
  @"
model:
  default: $User
  provider: custom
  base_url: http://127.0.0.1:8080/v1
  context_length: 65536
agent:
  max_turns: 1000000
terminal:
  cwd: .
web:
  search_backend: ddgs
compression:
  enabled: true
approvals:
  mode: 'off'
platform_toolsets:
  cli:
  - file
  - session_search
  - skills
  - terminal
  - todo
  - web
"@ | Set-Content "$H\.hermes\config.yaml" -Encoding UTF8
}
# Hermes writes a default SOUL.md ("You are Hermes Agent, ... a helpful assistant") into the system prompt; replace it
# with the same neutral file Otter has, so the agent's identity comes only from its own mind.
Copy-Item "$Src\SOUL.md" "$H\.hermes\SOUL.md" -Force
# Everything set up here belongs to the agent (only these folders: the profile also holds system junctions).
$ErrorActionPreference = "Continue"
foreach ($d in "workspace", "mind", "letters", "logs", "status", "secrets", ".hermes", ".gitconfig") {
  icacls "$H\$d" /setowner $User /T /C /Q 2>&1 | Out-Null
  icacls "$H\$d" /grant "*${sid}:(OI)(CI)F" /T /C /Q 2>&1 | Out-Null
}
$ErrorActionPreference = "Stop"
Say "home ready in $H"

# 5. the waker: wakings back to back, each queued at the GPU gate; the hourly trigger only restarts it if it stopped (does nothing until $Ctl\enabled exists)
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Ctl\waker.ps1`""
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(30) -RepetitionInterval (New-TimeSpan -Hours 1)
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask "Muse-Waker" -Action $a -Trigger $t -User $User -Password $secret -RunLevel Limited -Settings $s -Force | Out-Null
Remove-Variable secret
Say "task Muse-Waker registered (hourly at :30; waits for $Ctl\enabled)"
