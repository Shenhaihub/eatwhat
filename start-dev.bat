@echo off
REM ============================================================
REM  EatWhat dev launcher (frontend + backend)
REM  Frontend : http://localhost:5173/
REM  Backend  : http://127.0.0.1:8000/   (/docs = Swagger UI)
REM  Close    : close both cmd windows, or Ctrl+C twice
REM ============================================================

setlocal
set "ROOT=%~dp0"

REM Fail before opening child windows when either fixed dev port is occupied.
for %%P in (8000 5173) do (
  netstat -ano -p TCP | findstr /R /C:":%%P .*LISTENING" >nul
  if not errorlevel 1 (
    echo ERROR: port %%P is already in use. Stop the owning process, then run this launcher again.
    netstat -ano -p TCP | findstr /R /C:":%%P .*LISTENING"
    exit /b 2
  )
)

echo [1/2] Starting backend FastAPI (uvicorn --reload) on :8000 ...
start "EatWhat Backend :8000" /d "%ROOT%backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --host 127.0.0.1"

set "BACKEND_READY="
for /l %%N in (1,1,10) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health/live' -TimeoutSec 1; if ($r.StatusCode -eq 200 -and $r.Content -match 'status.*ok') { exit 0 } } catch {} ; exit 1" >nul 2>nul
  if not errorlevel 1 set "BACKEND_READY=1"
  if defined BACKEND_READY goto :backend_ready
  timeout /t 1 /nobreak >nul
)
echo ERROR: backend did not become healthy at http://127.0.0.1:8000/health/live.
exit /b 3

:backend_ready

echo [2/2] Starting frontend Vite (hot reload) on :5173 ...
start "EatWhat Frontend :5173" /d "%ROOT%frontend" cmd /k "npx vite --host 127.0.0.1 --port 5173"

for /l %%N in (1,1,10) do (
  netstat -ano -p TCP | findstr /R /C:":5173 .*LISTENING" >nul
  if not errorlevel 1 goto :frontend_ready
  timeout /t 1 /nobreak >nul
)
echo ERROR: frontend did not start listening on http://localhost:5173/.
exit /b 4

:frontend_ready

echo.
echo Done. Open these in your browser:
echo   Frontend : http://localhost:5173/
echo   Backend  : http://127.0.0.1:8000/docs
echo.
echo Close by shutting down both cmd windows or pressing Ctrl+C.
endlocal
