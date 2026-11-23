<#
  Lets the 'muse' account run scheduled tasks ("Log on as a batch job"), the same right Otter's 'inkling' account has.
  Run once in an administrator PowerShell on the 6900XT:
      powershell -ExecutionPolicy Bypass -File C:\ProgramData\muse-src\grant-batch-logon.ps1
  It adds muse to that one right and changes nothing else.
#>
$ErrorActionPreference = "Stop"
$sid = (Get-LocalUser muse).SID.Value
$f = "$env:TEMP\batch-$(Get-Random)"
secedit /export /cfg "$f.inf" /areas USER_RIGHTS | Out-Null
$line = (Get-Content "$f.inf" | Where-Object { $_ -match '^SeBatchLogonRight' })
if ($line -match [regex]::Escape($sid) -or $line -match '\bmuse\b') { "muse already has the right"; Remove-Item "$f.inf"; return }
@("[Unicode]", "Unicode=yes", "[Version]", 'signature="$CHICAGO$"', "Revision=1", "[Privilege Rights]", "$line,*$sid") | Set-Content "$f.new.inf" -Encoding Unicode
secedit /configure /db "$f.sdb" /cfg "$f.new.inf" /areas USER_RIGHTS | Out-Null
Remove-Item "$f.inf", "$f.new.inf", "$f.sdb" -ErrorAction SilentlyContinue
secedit /export /cfg "$f.chk.inf" /areas USER_RIGHTS | Out-Null
(Get-Content "$f.chk.inf" | Where-Object { $_ -match '^SeBatchLogonRight' })
Remove-Item "$f.chk.inf"
