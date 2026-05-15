@echo off
REM ASCII-only for cmd.exe on Chinese Windows.
REM No pause: set NO_PAUSE=1 / NONINTERACTIVE=1, or: package_deploy.bat nopause
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
setlocal enabledelayedexpansion

if /i "%~1"=="nopause" set NO_PAUSE=1
if /i "%~1"=="/nopause" set NO_PAUSE=1
if "%NONINTERACTIVE%"=="1" set NO_PAUSE=1
if defined CI set NO_PAUSE=1
if defined GITHUB_ACTIONS set NO_PAUSE=1
if defined GITLAB_CI set NO_PAUSE=1
if defined RUNNER_OS set NO_PAUSE=1

python "%~dp0package_deploy.py"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Need Python 3 and scripts\package_deploy.py
    call :bp
    exit /b 1
)
call :bp
goto :EOF

:bp
if "%NO_PAUSE%"=="1" exit /b 0
if "%NONINTERACTIVE%"=="1" exit /b 0
if defined CI exit /b 0
if defined GITHUB_ACTIONS exit /b 0
if defined GITLAB_CI exit /b 0
if defined RUNNER_OS exit /b 0
pause
exit /b 0
