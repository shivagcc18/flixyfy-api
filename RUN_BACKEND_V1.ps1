Set-Location $PSScriptRoot

python -m venv .venv

.\.venv\Scripts\pip install -r requirements.txt

Write-Host "FLIXYFY_BACKEND_REPOSITORY_BOOTSTRAP_V1_READY"
Write-Host "Run:"
Write-Host ".\.venv\Scripts\uvicorn app.main:app --reload"
