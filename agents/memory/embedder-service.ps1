<#
  Keeps the shared CPU embedding server (port 8081) running for the agents' memories. Installed at
  C:\ProgramData\InklingGate\embedder-service.ps1 and run every 5 minutes by the "Agents-Embedder" task as the box owner.
  The agents' own embedder.ps1 reuses a healthy server on 8081 and only ever stops one it started itself.
  (Started from the 'muse' account the server took minutes to come up; from the owner's it takes about two seconds.)
  It holds no data: it only turns text into vectors. ~1.4 GB RAM, no GPU.
#>
$Url = "http://127.0.0.1:8081"
try { Invoke-RestMethod "$Url/health" -TimeoutSec 3 | Out-Null; exit 0 } catch {}
$log = "C:\ProgramData\InklingGate\embedder.log"
Start-Process -FilePath "C:\Program Files\llama.cpp\llama-server.exe" -WindowStyle Hidden -RedirectStandardError $log -ArgumentList @(
  "-m", "C:\ProgramData\Models\qwen3-embedding\Qwen3-Embedding-0.6B-Q8_0.gguf", "--embeddings", "--pooling", "last",
  "--device", "none", "-ngl", "0", "-c", "4096", "-b", "4096", "-ub", "4096", "--threads", "4",
  "--host", "127.0.0.1", "--port", "8081") | Out-Null
