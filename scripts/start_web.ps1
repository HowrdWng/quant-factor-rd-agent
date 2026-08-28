$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
Write-Host "Quantary will open at http://127.0.0.1:8000"
Start-Process "http://127.0.0.1:8000"
python -m quant_factor_agent.web --host 127.0.0.1 --port 8000
