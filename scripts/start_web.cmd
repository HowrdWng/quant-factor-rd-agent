@echo off
setlocal
cd /d "%~dp0\.."
echo Quantary will open at http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"
python -m quant_factor_agent.web --host 127.0.0.1 --port 8000
endlocal
