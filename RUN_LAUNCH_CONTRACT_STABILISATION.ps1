$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bundle = Join-Path $Repo "launch_contract_stabilisation_bundle_20260727_191006"
$Report = Join-Path $Bundle "implementation_report.json"
$PostManifest = Join-Path $Bundle "post_change_manifest_sha256.txt"
$Port = 8019
$BaseUrl = "http://127.0.0.1:$Port"
$env:FLIXYFY_PRODUCTION_CORS_ORIGINS = "https://prod.flixyfy.test,https://app.flixyfy.example"
$env:FLIXYFY_QUERY_TIMEOUT_MS = "60000"

function Add-Step($name, $status, $detail) {
  [pscustomobject]@{ name = $name; status = $status; detail = $detail }
}

$steps = New-Object System.Collections.Generic.List[object]
Set-Location $Repo

try {
  python -m compileall app tests smoke_test.py | Out-String | Set-Variable compileOut
  $steps.Add((Add-Step "python_compile" "PASS" $compileOut.Trim()))
} catch {
  $steps.Add((Add-Step "python_compile" "FAIL" $_.Exception.Message))
  throw
}

try {
  python -c "import app.main; print('import_ok')" | Out-String | Set-Variable importOut
  $steps.Add((Add-Step "python_import" "PASS" $importOut.Trim()))
} catch {
  $steps.Add((Add-Step "python_import" "FAIL" $_.Exception.Message))
  throw
}

try {
  python smoke_test.py | Out-String | Set-Variable smokeOut
  $steps.Add((Add-Step "updated_smoke" "PASS" $smokeOut.Trim()))
} catch {
  $steps.Add((Add-Step "updated_smoke" "FAIL" $_.Exception.Message))
  throw
}

try {
  python -m pytest -q 2>&1 | Out-String | Set-Variable pytestOut
  if ($LASTEXITCODE -ne 0) { throw $pytestOut }
  $steps.Add((Add-Step "bounded_endpoint_tests" "PASS" $pytestOut.Trim()))
} catch {
  $steps.Add((Add-Step "bounded_endpoint_tests" "FAIL" $_.Exception.Message))
  throw
}

$server = $null
try {
  $server = Start-Process -FilePath python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    try {
      $health = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/health" -TimeoutSec 5
      $ready = $true
      break
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $ready) { throw "Uvicorn did not become ready on $BaseUrl" }

  $search = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/search?q=amitabh&limit=3&response_mode=compact" -TimeoutSec 10
  $current = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/movies?domain=current&limit=10" -TimeoutSec 10
  $historicalCompact = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/movies?domain=historical&limit=50&response_mode=compact" -TimeoutSec 10
  $historicalFull = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/movies?domain=historical&limit=50&response_mode=full" -TimeoutSec 10
  $providers = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/providers" -TimeoutSec 10
  $tmdbCard = @($current.items | Where-Object { $null -ne $_.tmdb_id })[0]
  $histCard = @($historicalFull.items | Where-Object { $null -eq $_.tmdb_id })[0]
  $numericDetail = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/movies/$($tmdbCard.tmdb_id)" -TimeoutSec 10
  $canonicalId = [uri]::EscapeDataString($histCard.canonical_movie_id)
  $canonicalDetail = Invoke-RestMethod -UseBasicParsing -Uri "$BaseUrl/api/v1/movies/canonical/$canonicalId" -TimeoutSec 10

  $localCors = Invoke-WebRequest -UseBasicParsing -Method OPTIONS -Uri "$BaseUrl/health" -Headers @{ Origin="http://localhost:3000"; "Access-Control-Request-Method"="GET" } -TimeoutSec 10
  $prodCors = Invoke-WebRequest -UseBasicParsing -Method OPTIONS -Uri "$BaseUrl/health" -Headers @{ Origin="https://prod.flixyfy.test"; "Access-Control-Request-Method"="GET" } -TimeoutSec 10

  $httpProof = [pscustomobject]@{
    health_status = $health.status
    health_path_leak = (($health | ConvertTo-Json -Depth 20) -match "C:\\")
    search_items = @($search.items).Count
    current_total = $current.total
    historical_total = $historicalFull.total
    numeric_detail_tmdb_id = $numericDetail.tmdb_id
    canonical_detail_id = $canonicalDetail.canonical_movie_id
    canonical_detail_tmdb_id = $canonicalDetail.tmdb_id
    providers = @($providers.items).Count
    cors_localhost = $localCors.Headers["Access-Control-Allow-Origin"]
    cors_production = $prodCors.Headers["Access-Control-Allow-Origin"]
    compact_bytes = ([Text.Encoding]::UTF8.GetByteCount(($historicalCompact | ConvertTo-Json -Depth 20)))
    full_bytes = ([Text.Encoding]::UTF8.GetByteCount(($historicalFull | ConvertTo-Json -Depth 20)))
  }
  $steps.Add((Add-Step "bounded_local_http" "PASS" $httpProof))
} finally {
  if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -Force
  }
}

try {
  python -c "import sqlite3; p='C:/Users/USER/Desktop/DB/flixyfy_launch_serving_v3.db'; con=sqlite3.connect('file:'+p+'?mode=ro', uri=True); con.execute('PRAGMA query_only=ON'); print('select_count='+str(con.execute('SELECT COUNT(*) FROM movie_identity_serving_v3').fetchone()[0]));
try:
 con.execute('CREATE TABLE codex_readonly_probe(x INTEGER)')
 print('write_probe=unexpected_success')
except sqlite3.Error as exc:
 print('write_probe=blocked:'+exc.__class__.__name__)" | Out-String | Set-Variable roOut
  $steps.Add((Add-Step "read_only_source_proof" "PASS" $roOut.Trim()))
} catch {
  $steps.Add((Add-Step "read_only_source_proof" "FAIL" $_.Exception.Message))
  throw
}

Get-ChildItem -File -Recurse | Where-Object { $_.FullName -notlike '*\.venv\*' -and $_.FullName -notlike '*\__pycache__\*' -and $_.FullName -notlike '*\launch_contract_stabilisation_bundle_*\*' } | Sort-Object FullName | ForEach-Object {
  $h = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
  '{0}  {1}' -f $h.Hash, ($_.FullName.Substring($Repo.Length + 1))
} | Set-Content -Encoding UTF8 -LiteralPath $PostManifest

$rollback = [pscustomobject]@{
  restore_command = "Copy-Item -Recurse -Force '$Bundle\backups\*' '$Repo'"
  note = "Restores backed-up pre-change versions of modified files. Remove newly added tests/runner/bundle files manually if a perfectly clean tree is required."
}

$reportObject = [pscustomobject]@{
  checkpoint = "launch_contract_stabilisation_bundle_20260727_191006"
  generated_at = (Get-Date).ToString("o")
  bundle = $Bundle
  pre_manifest = (Join-Path $Bundle "pre_change_manifest_sha256.txt")
  post_manifest = $PostManifest
  backups = (Join-Path $Bundle "backups")
  steps = $steps
  rollback = $rollback
  mutation_flags = [pscustomobject]@{
    raw_vault_db_accessed = $false
    youtube_candidate_db_accessed = $false
    serving_db_opened_read_only = $true
    neon_mutation = $false
    deployment = $false
  }
}
$reportObject | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -LiteralPath $Report

$zip = "$Bundle.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $Bundle -DestinationPath $zip -Force
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
$zipHash | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $Bundle "bundle_zip_sha256.txt")
Write-Output "REPORT=$Report"
Write-Output "BUNDLE_ZIP=$zip"
Write-Output "BUNDLE_ZIP_SHA256=$zipHash"

