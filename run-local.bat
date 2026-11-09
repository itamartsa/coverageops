@echo off
REM CoverageOps - local run (SQLite, no Docker needed)
cd /d "%~dp0"
set "DATABASE_URL=sqlite:///./local.db"
set "SECRET_KEY=local-dev-only"
set "SRTM_DATA_DIR=%~dp0backend\data\srtm"
set "RESULTS_DIR=%~dp0backend\data\results"
if not exist "%SRTM_DATA_DIR%" mkdir "%SRTM_DATA_DIR%"
if not exist "%RESULTS_DIR%" mkdir "%RESULTS_DIR%"

where node >nul 2>nul || (echo [!] Node.js is not installed - get it from https://nodejs.org and run again & pause & exit /b 1)
if not exist "backend\.venv\Scripts\python.exe" (echo [!] backend\.venv missing & pause & exit /b 1)

start "CoverageOps API :8000" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\python -m pip install -q -r requirements.txt && .venv\Scripts\python -m app.scripts.seed && .venv\Scripts\python -m uvicorn app.main:app --port 8000"
start "CoverageOps UI :3000" cmd /k "cd /d "%~dp0frontend" && (if not exist node_modules npm install) && npm run dev"

echo Waiting for servers to start...
timeout /t 25 /nobreak >nul
start "" http://localhost:3000
