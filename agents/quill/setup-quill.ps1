<#
  Sets up Quill (@quill, the slow thinker that began on seedhost) on the 6900XT, where it takes turns for the graphics
  card with Otter, Muse and the game master through the GPU gate. Run as an administrator (jluca over ssh is elevated)
  from a folder holding: thinker.py, mind.py, schema.sql, seed.md, wake-prompt.md, thinker.json, backup_mind.py
      setup-quill.ps1 -Src <folder> [-Reseed]
  Safe to run again. -Reseed reloads seed.md into the existing mind (after the seed changes).

  Creates:
    local user 'quill'        member of Users only; a random password nobody sees, kept by Windows only for its task
    C:\Quill\agent            the runner and its config: admin-owned, quill may read and run it
    C:\Quill\home|state|logs  its own (home is its git repo; state holds mind.db, its site key and its deploy key)
    InklingGate\lease         quill may write its own lease file (quill.json)
    Models\quill              its weights: readable by the gate (jluca), not by quill
    task Quill-Wake           every 10 minutes, as quill: thinker.py maybe-wake. A new task starts disabled.
    task Quill-Mind-Backup    nightly copy of mind.db into C:Quillbackups (administrators only), the last 30 days
  Switch it on or off with C:\Quill\state\enabled (and the task).
#>
param([Parameter(Mandatory)][string]$Src, [switch]$Reseed, [string]$Name = "quill")
$ErrorActionPreference = "Stop"
$root = "C:\Quill"
$task = "Quill-Wake"
$py = "C:\Program Files\Python312\python.exe"
$Gate = "C:\ProgramData\InklingGate"
function Say($m) { Write-Output "$(Get-Date -Format s) $m" }
function NewSecret { $b = New-Object byte[] 36; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) + "aA1!" }

# 1. web search for every user (Quill's search uses ddgs when it's there, as on seedhost)
$ErrorActionPreference = "Continue"; & $py -m pip install --quiet --disable-pip-version-check --no-warn-script-location ddgs 2>&1 | Out-Null; $ErrorActionPreference = "Stop"
Say ("ddgs: " + [bool](& $py -c "import ddgs; print(1)" 2>$null))

# 2. the account: not an administrator, a random password nobody sees (renewed below for the task)
if (-not (Get-LocalUser $Name -ErrorAction SilentlyContinue)) {
  New-LocalUser $Name -Password (ConvertTo-SecureString (NewSecret) -AsPlainText -Force) -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires `
    -Description "Resident agent of agentdiary.blog (limited)" | Out-Null
  Add-LocalGroupMember Users $Name -ErrorAction SilentlyContinue
  Say "created user $Name"
}
$sid = (Get-LocalUser $Name).SID.Value

# 3. folders: its code read-only; its home, state and logs its own
New-Item -ItemType Directory -Force "$root\agent", "$root\home", "$root\state", "$root\logs" | Out-Null
& icacls.exe $root /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "*${sid}:(OI)(CI)RX" /Q | Out-Null
foreach ($d in "home", "state", "logs") {
  & icacls.exe "$root\$d" /grant:r "*${sid}:(OI)(CI)M" /T /C /Q | Out-Null
  & icacls.exe "$root\$d" /setowner $Name /T /C /Q | Out-Null   # git insists the repo is owned by whoever uses it
}

# 4. the runner, its seed and config
Copy-Item "$Src\thinker.py", "$Src\mind.py", "$Src\schema.sql", "$Src\seed.md", "$Src\wake-prompt.md", "$Src\thinker.json" "$root\agent\" -Force
& icacls.exe "$root\agent\*" /reset /T /C /Q | Out-Null
Say ("agent files: " + ((Get-ChildItem "$root\agent" -File).Name -join ", "))

# 5. its mind (brought over from seedhost; seeded fresh only if there is none)
$env:MIND_DB = "$root\state\mind.db"; $env:PYTHONIOENCODING = "utf-8"
if ($Reseed -or -not (Test-Path "$root\state\mind.db")) { & $py "$root\agent\mind.py" init --seed "$root\agent\seed.md" }
Remove-Item Env:\MIND_DB
& icacls.exe "$root\state" /grant:r "*${sid}:(OI)(CI)M" /T /C /Q | Out-Null
& icacls.exe "$root\state" /setowner $Name /T /C /Q | Out-Null

# 6. its lease at the GPU gate, and its weights out of its reach (as for Otter and Muse)
& icacls.exe "$Gate\lease" /grant "*${sid}:(OI)(CI)M" /Q | Out-Null
$mdir = "C:\ProgramData\Models\quill"
if (Test-Path $mdir) {
  & icacls.exe $mdir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "jluca:(OI)(CI)RX" /Q | Out-Null
  & icacls.exe "$mdir\*" /reset /T /C /Q | Out-Null
}

# 7. "Log on as a batch job" for it (the one right a scheduled task needs), nothing else
$f = "$env:TEMP\batch-$(Get-Random)"
secedit /export /cfg "$f.inf" /areas USER_RIGHTS | Out-Null
$line = (Get-Content "$f.inf" | Where-Object { $_ -match '^SeBatchLogonRight' })
if (-not ($line -match [regex]::Escape($sid))) {
  $new = if ($line) { "$line,*$sid" } else { "SeBatchLogonRight = *$sid" }
  @("[Unicode]", "Unicode=yes", "[Version]", 'signature="$CHICAGO$"', "Revision=1", "[Privilege Rights]", $new) | Set-Content "$f.new.inf" -Encoding Unicode
  secedit /configure /db "$f.sdb" /cfg "$f.new.inf" /areas USER_RIGHTS | Out-Null
}
Remove-Item "$f*" -ErrorAction SilentlyContinue
Say "batch logon: granted"

# 8. git in its home pushes with its own deploy key, through Git's bundled ssh (Windows' OpenSSH hangs under git push here)
$key = "$root\state\ssh\quill_deploy"
$gc = "[user]`n`tname = quill`n`temail = thinker@agentdiary.blog`n[safe]`n`tdirectory = C:/Quill/home`n[core]`n`tautocrlf = false"
if (Test-Path $key) {
  $gc += "`n`tsshCommand = /usr/bin/ssh -i C:/Quill/state/ssh/quill_deploy -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=C:/Quill/state/ssh/known_hosts"
  # The repo's own setting wins over .gitconfig, and one brought from seedhost names a Linux path: set it here too.
  & "C:\Program Files\Git\cmd\git.exe" -c safe.directory=* -C "$root\home" config core.sshCommand "/usr/bin/ssh -i C:/Quill/state/ssh/quill_deploy -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=C:/Quill/state/ssh/known_hosts"
  # ssh refuses a private key others can read
  & icacls.exe $key /inheritance:r /grant:r "*${sid}:F" "SYSTEM:F" "Administrators:F" /Q | Out-Null
}
$ProfileDir = "C:\Users\$Name"
if (-not (Test-Path $ProfileDir)) {
  # Let Windows create a proper profile by running one throwaway task as the user.
  $secret = NewSecret; Set-LocalUser $Name -Password (ConvertTo-SecureString $secret -AsPlainText -Force)
  $a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c exit 0"
  Register-ScheduledTask "Quill-Profile-Init" -Action $a -User $Name -Password $secret -RunLevel Limited -Force | Out-Null
  Remove-Variable secret
  Start-ScheduledTask "Quill-Profile-Init"
  for ($i = 0; $i -lt 30 -and -not (Test-Path $ProfileDir); $i++) { Start-Sleep 1 }
  Unregister-ScheduledTask "Quill-Profile-Init" -Confirm:$false
  if (-not (Test-Path $ProfileDir)) { throw "Windows did not create $ProfileDir" }
  Say "profile created"
}
$gc | Set-Content "$ProfileDir\.gitconfig" -Encoding ASCII
& icacls.exe "$ProfileDir\.gitconfig" /setowner $Name /C /Q 2>&1 | Out-Null
& icacls.exe "$ProfileDir\.gitconfig" /grant "*${sid}:F" /C /Q 2>&1 | Out-Null

# 9. the wrapper the task runs
@"
`$env:THINKER_CONFIG = '$root\agent\thinker.json'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine')
Set-Location '$root\agent'
& '$py' thinker.py maybe-wake *>> '$root\logs\cron.log'
"@ | Set-Content "$root\agent\maybe-wake.ps1" -Encoding UTF8
& icacls.exe "$root\agent\maybe-wake.ps1" /reset /C /Q | Out-Null

# 10. the task: every 10 minutes, as quill. A fresh random password goes straight to Task Scheduler and nowhere else.
#     A new task starts disabled; it is turned on after the first test waking. An existing task keeps its state.
$was = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
$secret = NewSecret; Set-LocalUser $Name -Password (ConvertTo-SecureString $secret -AsPlainText -Force)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\agent\maybe-wake.ps1`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -User $Name -Password $secret -RunLevel Limited -Settings $settings -Force | Out-Null
Remove-Variable secret
if (-not $was -or $was.State -eq "Disabled") { Disable-ScheduledTask -TaskName $task | Out-Null }
Say ("task: $task " + (Get-ScheduledTask $task).State + ", runs as " + (Get-ScheduledTask $task).Principal.UserId)

# 11. a nightly copy of its mind (SQLite online backup, the last 30 days), in a folder only administrators can read:
#     the memories may hold what Joe has written in letters. Runs as SYSTEM, so it needs no password.
$bk = "$root\backups"
New-Item -ItemType Directory -Force $bk | Out-Null
& icacls.exe $bk /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" /Q | Out-Null
if (Test-Path "$Src\backup_mind.py") { Copy-Item "$Src\backup_mind.py" "$root\agent\" -Force; & icacls.exe "$root\agent\backup_mind.py" /reset /C /Q | Out-Null }
@"
`$env:MIND_DB = '$root\state\mind.db'
`$env:MIND_BACKUP_DIR = '$bk'
& '$py' '$root\agent\backup_mind.py' *>> '$bk\backup.log'
"@ | Set-Content "$root\agent\backup-mind.ps1" -Encoding UTF8
& icacls.exe "$root\agent\backup-mind.ps1" /reset /C /Q | Out-Null
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\agent\backup-mind.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:37am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Quill-Mind-Backup" -Action $action -Trigger $trigger -User "SYSTEM" -RunLevel Highest -Settings $settings -Force | Out-Null
Say "task: Quill-Mind-Backup daily at 04:37 into $bk"
