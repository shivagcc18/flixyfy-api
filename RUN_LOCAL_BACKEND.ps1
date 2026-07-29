$ErrorActionPreference="Stop"
$Repo="C:\Users\USER\Desktop\flixyfy-clean-stack-v2\repos\flixyfy-api"
$Runtime="C:\Users\USER\Desktop\flixyfy-clean-stack-v2\runtime\local_launch_v1"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
Set-Content -LiteralPath (Join-Path $Runtime "backend.pid") -Value $PID -Encoding ASCII
Set-Location $Repo
$env:FLIXYFY_SERVING_DB="C:\Users\USER\Desktop\DB\flixyfy_launch_serving_v3.db"
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
