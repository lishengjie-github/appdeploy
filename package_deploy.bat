@echo off
REM ASCII-only for cmd.exe on Chinese Windows.
chcp 65001 >nul 2>&1
cd /d "%~dp0"
python package_deploy.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Need Python 3 and package_deploy.py in this folder
    pause
    exit /b 1
)
pause
