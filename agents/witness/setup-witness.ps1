# Sets up the witness on this box: its own Windows account, folders, the runner, its mind, and a scheduled task.
# Run as an administrator. Safe to run again.
$ErrorActionPreference = "Stop"
$root = "D:\Witness"
$py = "C:\Program Files\Python313\python.exe"

# 1. Python for every user (the witness can't read Python installed in someone else's profile)
if (-not (Test-Path $py)) {
  $inst = "$root\downloads\python-3.13.1-amd64.exe"
  if (-not (Test-Path $inst)) { & curl.exe -sSL -o $inst "https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe" }
  Start-Process -Wait -FilePath $inst -ArgumentList "/quiet InstallAllUsers=1 PrependPath=0 Include_test=0 Include_launcher=0 TargetDir=`"C:\Program Files\Python313`""
}
"python: " + (& $py --version)
$ErrorActionPreference = "Continue"; & $py -m pip install --quiet --disable-pip-version-check --no-warn-script-location ddgs 2>&1 | Out-Null; $ErrorActionPreference = "Stop"
"ddgs: " + [bool](& $py -c "import ddgs; print(1)" 2>$null)

# 2. Its own account: not an administrator, a random password nobody sees
if (-not (Get-LocalUser -Name witness -ErrorAction SilentlyContinue)) {
  $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%^*-_=+"
  $bytes = New-Object byte[] 40; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $pw = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
  New-LocalUser -Name witness -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires -UserMayNotChangePassword -Description "The witness, an agentdiary.blog resident" | Out-Null
  $pw = $null
}
"account: " + (Get-LocalUser witness).Name

# 3. Folders: code, llama and model read-only for the witness; its home and state are its own
New-Item -ItemType Directory -Force "$root\agent", "$root\home", "$root\state", "$root\logs" | Out-Null
& icacls.exe $root /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "witness:(OI)(CI)RX" /Q | Out-Null
foreach ($d in "home", "state", "logs") { & icacls.exe "$root\$d" /grant:r "witness:(OI)(CI)M" /Q | Out-Null }
foreach ($d in "home", "state", "logs") { & icacls.exe "$root\$d" /setowner witness /T /Q | Out-Null }   # its own (git also insists the repo is owned by whoever uses it)

# 4. The runner and the witness's seed (copied here from C:\Users\joe\witness-src)
Copy-Item "C:\Users\joe\witness-src\*" "$root\agent\" -Force
"agent files: " + ((Get-ChildItem "$root\agent").Name -join ", ")

# 5. Its mind
$env:MIND_DB = "$root\state\mind.db"; $env:PYTHONIOENCODING = "utf-8"
if (-not (Test-Path "$root\state\mind.db")) { & $py "$root\agent\mind.py" init --seed "$root\agent\seed.md" }
& icacls.exe "$root\state" /grant:r "witness:(OI)(CI)M" /T /Q | Out-Null
& icacls.exe "$root\state" /setowner witness /T /Q | Out-Null

# 6. A wrapper the task runs, and the task itself: every 10 minutes, as the witness
@"
`$env:THINKER_CONFIG = '$root\agent\thinker.json'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine')
Set-Location '$root\agent'
& '$py' thinker.py maybe-wake *>> '$root\logs\cron.log'
"@ | Set-Content "$root\agent\maybe-wake.ps1" -Encoding UTF8

# "Log on as a batch job" for the witness (the one right a scheduled task needs), nothing else
$sid = (Get-LocalUser witness).SID.Value
$f = "$env:TEMP\batch-$(Get-Random)"
secedit /export /cfg "$f.inf" /areas USER_RIGHTS | Out-Null
$line = (Get-Content "$f.inf" | Where-Object { $_ -match '^SeBatchLogonRight' })
if (-not ($line -match [regex]::Escape($sid))) {
  $new = if ($line) { "$line,*$sid" } else { "SeBatchLogonRight = *$sid" }
  @("[Unicode]", "Unicode=yes", "[Version]", 'signature="$CHICAGO$"', "Revision=1", "[Privilege Rights]", $new) | Set-Content "$f.new.inf" -Encoding Unicode
  secedit /configure /db "$f.sdb" /cfg "$f.new.inf" /areas USER_RIGHTS | Out-Null
}
Remove-Item "$f*" -ErrorAction SilentlyContinue
"batch logon: granted"

$chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%^*-_=+"
$bytes = New-Object byte[] 40; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$pw = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
Set-LocalUser witness -Password (ConvertTo-SecureString $pw -AsPlainText -Force)

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\agent\maybe-wake.ps1`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 2) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName "Witness-Wake" -Action $action -Trigger $trigger -User "witness" -Password $pw -RunLevel Limited -Settings $settings -Force | Out-Null
$pw = $null
Disable-ScheduledTask -TaskName "Witness-Wake" | Out-Null   # turned on after the first test waking
"task: " + (Get-ScheduledTask Witness-Wake).State + ", runs as " + (Get-ScheduledTask Witness-Wake).Principal.UserId
"wrapper: " + (Test-Path "$root\agent\maybe-wake.ps1")
