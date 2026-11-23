@echo off
rem Start the AI Agent for Online Food Ordering on http://127.0.0.1:8000
cd /d "%~dp0"
python -c "import fastapi, websocket, faiss, transformers" 2>nul || (
  echo Installing requirements...
  python -m pip install -r requirements.txt || exit /b 1
)
if "%AGENT_PORT%"=="" set AGENT_PORT=8000
echo Customer app:  http://127.0.0.1:%AGENT_PORT%
echo Admin console: http://127.0.0.1:%AGENT_PORT%/admin
python run.py
