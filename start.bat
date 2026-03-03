@echo off
start "Backend" cmd /k "cd /d F:\inart-pm\backend && python -m uvicorn main:app --reload"
timeout /t 2 /nobreak > nul
start "Frontend" cmd /k "cd /d F:\inart-pm\frontend && npm start"
