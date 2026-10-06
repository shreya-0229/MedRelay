@echo off
REM MedRelay full-stack — one-command start (Windows).
REM Installs backend deps into backend\.venv, builds the frontend if dist\
REM is missing, then starts the backend (API + dashboard) on port 8000.
set ROOT=%~dp0

echo ==^> Backend dependencies...
cd /d "%ROOT%backend"
if not exist ".venv" python -m venv .venv
call ".venv\Scripts\activate.bat"
pip install -q -r requirements.txt

echo ==^> Frontend (build only if dist\ is missing)...
cd /d "%ROOT%frontend"
if not exist "dist" (
  if not exist "node_modules" call npm install
  call npm run build
) else (
  echo     dist\ already present — skipping build.
)

echo ==^> Starting MedRelay: open http://localhost:8000
cd /d "%ROOT%backend"
".venv\Scripts\python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
