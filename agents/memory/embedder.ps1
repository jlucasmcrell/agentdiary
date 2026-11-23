<#
  CPU-only embedding server for the agent's memory (Qwen3-Embedding-0.6B via llama.cpp, port 8081).
  Installed at C:\ProgramData\Inkling\embedder.ps1; run as the agent user around each waking:
      & embedder.ps1 start    # idempotent; ~2 s to ready, ~1.4 GB RAM, no GPU memory
      & embedder.ps1 stop
  Sets $env:MIND_* for the current process on start, so a memory server launched afterwards inherits them.
#>
param([ValidateSet("start", "stop", "status")][string]$Action = "start")
$Url = "http://127.0.0.1:8081"
$PidFile = "$env:USERPROFILE\status\embedder.pid"
function Healthy { try { Invoke-RestMethod "$Url/health" -TimeoutSec 2 | Out-Null; $true } catch { $false } }

if ($Action -eq "status") { if (Healthy) { "running" } else { "stopped" }; return }
if ($Action -eq "stop") {
  if (Test-Path $PidFile) {
    $p = Get-Process -Id ([int](Get-Content $PidFile)) -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq "llama-server") { Stop-Process -Id $p.Id -Force }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
  }
  return
}

if (-not (Healthy)) {
  New-Item -ItemType Directory -Force (Split-Path $PidFile) | Out-Null
  $p = Start-Process -FilePath "C:\Program Files\llama.cpp\llama-server.exe" -WindowStyle Hidden -PassThru `
    -RedirectStandardError "$env:USERPROFILE\logs\embedder.log" -ArgumentList @(
      "-m", "C:\ProgramData\Models\qwen3-embedding\Qwen3-Embedding-0.6B-Q8_0.gguf", "--embeddings", "--pooling", "last",
      "--device", "none", "-ngl", "0", "-c", "4096", "-b", "4096", "-ub", "4096", "--threads", "4",
      "--host", "127.0.0.1", "--port", "8081")
  Set-Content $PidFile $p.Id
  for ($i = 0; $i -lt 60 -and -not (Healthy); $i++) { Start-Sleep -Milliseconds 500 }
}
if (Healthy) {
  $env:MIND_EMBED_URL = "$Url/v1/embeddings"
  $env:MIND_EMBED_MODEL = "qwen3-embedding-0.6b"
  $env:MIND_EMBED_QUERY_PREFIX = "Instruct: Given a question or topic, retrieve the agent's relevant memories\nQuery: "
  "running"
} else { "failed to start (see logs\embedder.log)" }
