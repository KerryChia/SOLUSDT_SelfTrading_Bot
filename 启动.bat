@echo off
chcp 65001 >nul
title SOLUSDT Launcher Debug

cd /d "%~dp0"

echo ============================================
echo    SOLUSDT Trading System - Launcher Debug
echo ============================================
echo.
echo Starting launcher in DEBUG terminal mode...
echo Control panel: http://localhost:8890
echo Dashboard:     http://localhost:8888
echo.
echo The launcher will open two controlled terminals:
echo   - SOLUSDT Bot DEBUG
echo   - SOLUSDT Monitor DEBUG
echo.

python launcher.py --debug

echo.
echo Launcher exited.
pause
