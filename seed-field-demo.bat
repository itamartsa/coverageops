@echo off
REM CoverageOps - load the field-reception DEMO fleet into the local SQLite DB (same DB as run-local.bat)
REM Usage: seed-field-demo.bat          -> add demo devices and measurements (safe to run again)
REM        seed-field-demo.bat remove   -> remove the demo data
cd /d "%~dp0backend"
set "DATABASE_URL=sqlite:///./local.db"
set "SECRET_KEY=local-dev-only"
if not exist ".venv\Scripts\python.exe" (echo [!] backend\.venv missing & pause & exit /b 1)
.venv\Scripts\python -m pip install -q -r requirements.txt
if /i "%1"=="remove" (
  .venv\Scripts\python -m app.scripts.field_simulator --remove-demo
) else (
  .venv\Scripts\python -m app.scripts.field_simulator --seed-demo
)
echo.
echo Done. Start the system with run-local.bat and open http://localhost:3000/field
pause
