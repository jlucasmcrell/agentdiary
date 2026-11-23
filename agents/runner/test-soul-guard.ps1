# Checks the persona-file guard in wake.ps1: a missing SOUL.md is restored from the control folder; an edited one is left alone.
$ErrorActionPreference = "Stop"
$src = Get-Content (Join-Path $PSScriptRoot "wake.ps1") -Raw
$m = [regex]::Match($src, '(?ms)^\s*\$soul = "\$HomeDir\\\.hermes\\SOUL\.md".*?^\s*\}\s*$')
if (-not $m.Success) { throw "guard not found in wake.ps1" }
$guard = [scriptblock]::Create($m.Value)
$tmp = Join-Path ([IO.Path]::GetTempPath()) "soulguard-$(Get-Random)"
$HomeDir = "$tmp\home"; $Ctl = "$tmp\ctl"; $script:logged = @()
function Log($x) { $script:logged += $x }
New-Item -ItemType Directory -Force "$HomeDir\.hermes", $Ctl | Out-Null
Set-Content "$Ctl\SOUL.md" "neutral"
$fails = 0
function Check($ok, $what) { if ($ok) { "  ok  $what" } else { "  FAIL  $what"; $script:fails++ } }

. $guard
Check ((Get-Content "$HomeDir\.hermes\SOUL.md") -eq "neutral" -and $script:logged.Count -eq 1) "a missing persona file is put back, and the waking log says so"
Set-Content "$HomeDir\.hermes\SOUL.md" "I am someone in particular."
. $guard
Check ((Get-Content "$HomeDir\.hermes\SOUL.md") -eq "I am someone in particular." -and $script:logged.Count -eq 1) "an edited persona file is left exactly as the agent wrote it"
Set-Content "$HomeDir\.hermes\SOUL.md" ""
. $guard
Check ((Get-Content "$HomeDir\.hermes\SOUL.md") -eq "neutral" -and $script:logged.Count -eq 2) "a blank persona file is treated like a missing one (Hermes would fall back to its assistant identity)"
Remove-Item -Recurse -Force $tmp
if ($fails) { "$fails FAILED"; exit 1 } else { "all good" }
