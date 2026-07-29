$ErrorActionPreference = "Stop"
$Repo = "C:\Users\USER\Desktop\flixyfy-clean-stack-v2\repos\flixyfy-api"
$Bundle = Join-Path $Repo "launch_contract_stabilization_20260727_221500"
$Results = Join-Path $Bundle "runner_results.json"
Set-Location $Repo
$Python = "python"
$env:FLIXYFY_SERVING_DB = "C:\Users\USER\Desktop\DB\flixyfy_launch_serving_v3.db"
$env:FLIXYFY_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
$env:FLIXYFY_PRODUCTION_CORS_ORIGINS = "https://app.flixyfy.example"
$env:FLIXYFY_SQLITE_QUERY_TIMEOUT_MS = "30000"
$env:PYTHONWARNINGS = "ignore"
$port = 8017
$base = "http://127.0.0.1:$port"
$commands = New-Object System.Collections.Generic.List[object]
function Run-Step($Name, $ScriptBlock) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $output = & $ScriptBlock 2>&1 | Out-String
    $sw.Stop()
    $commands.Add([pscustomobject]@{ name=$Name; status="PASS"; elapsed_ms=$sw.ElapsedMilliseconds; output=$output.Trim() }) | Out-Null
  } catch {
    $sw.Stop()
    $commands.Add([pscustomobject]@{ name=$Name; status="FAIL"; elapsed_ms=$sw.ElapsedMilliseconds; output=($_ | Out-String).Trim() }) | Out-Null
    throw
  }
}
Run-Step "compileall" { & $Python -m compileall -q app }
Run-Step "import" { & $Python -c "import app.main; print('import_ok')" }
Run-Step "updated_smoke" { & $Python smoke_test.py }
Run-Step "endpoint_contract_test" { & $Python endpoint_contract_test.py }
$proc = Start-Process -FilePath $Python -ArgumentList @("-m","uvicorn","app.main:app","--host","127.0.0.1","--port",$port,"--log-level","warning") -PassThru -WindowStyle Hidden
try {
  $ready = $false
  for ($i=0; $i -lt 40; $i++) {
    try { Invoke-RestMethod -UseBasicParsing -Uri "$base/health" -TimeoutSec 2 | Out-Null; $ready = $true; break } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $ready) { throw "local HTTP server did not become ready" }
  Run-Step "http_health" { Invoke-RestMethod -UseBasicParsing -Uri "$base/health" -TimeoutSec 10 | ConvertTo-Json -Depth 20 }
  $current = Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies?domain=current&limit=1&payload=compact" -TimeoutSec 10
  $historical = Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies?domain=historical&limit=1&payload=compact" -TimeoutSec 10
  $histId = [uri]::EscapeDataString($historical.items[0].canonical_movie_id)
  Run-Step "http_search" { Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/search?q=RRR&limit=5&payload=compact" -TimeoutSec 10 | ConvertTo-Json -Depth 20 }
  Run-Step "http_current_details" { Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies/$($current.items[0].tmdb_id)" -TimeoutSec 10 | ConvertTo-Json -Depth 20 }
  Run-Step "http_historical_details" { Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies/by-canonical/$histId" -TimeoutSec 10 | ConvertTo-Json -Depth 20 }
  Run-Step "http_providers" { Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/providers" -TimeoutSec 10 | ConvertTo-Json -Depth 20 }
  Run-Step "http_cors" {
    $headers = @{ Origin="https://app.flixyfy.example"; "Access-Control-Request-Method"="GET" }
    $response = Invoke-WebRequest -UseBasicParsing -Method Options -Uri "$base/health" -Headers $headers -TimeoutSec 10
    if ($response.Headers["Access-Control-Allow-Origin"] -ne "https://app.flixyfy.example") { throw "production CORS origin not allowed" }
    "production CORS PASS"
  }
  Run-Step "http_compact_full_compare" {
    $full = Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies?domain=current&limit=1" -TimeoutSec 10
    $compact = Invoke-RestMethod -UseBasicParsing -Uri "$base/api/v1/movies?domain=current&limit=1&payload=compact" -TimeoutSec 10
    $fullBytes = ($full.items[0] | ConvertTo-Json -Depth 20).Length
    $compactBytes = ($compact.items[0] | ConvertTo-Json -Depth 20).Length
    if ($compactBytes -ge $fullBytes) { throw "compact payload is not smaller" }
    [pscustomobject]@{ full_item_bytes=$fullBytes; compact_item_bytes=$compactBytes } | ConvertTo-Json
  }
} finally {
  if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
$commands | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Results -Encoding UTF8
Write-Output "runner_results=$Results"






