@echo off
REM Daman Claim Decision Engine - single-command launcher (Windows)
REM Starts the FastAPI backend, which serves the built React frontend (web\dist)
REM and the JSON API on the same origin. Close the API window to stop.

cd /d "%~dp0"
netstat -ano | findstr ":8000 .*LISTENING" >nul
if %errorlevel%==0 (
  echo API is already running at http://127.0.0.1:8000 - opening it.
) else (
  start "API" cmd /c "C:\Users\sayyed.imran\AppData\Local\Python\pythoncore-3.14-64\python.exe -m uvicorn rd_engine.api:app --port 8000"
  timeout /t 3 /nobreak >nul
  echo API started at http://127.0.0.1:8000
)
start http://127.0.0.1:8000
echo.
echo To rebuild the frontend after editing web\src:
echo   cd web ^&^& node node_modules\vite\bin\vite.js build
