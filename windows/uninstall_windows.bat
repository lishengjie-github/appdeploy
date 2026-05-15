@echo off
REM ASCII-only batch for cmd.exe on Chinese Windows.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo.
echo  ============================================================
echo       Deploy client - Windows uninstaller
echo  ============================================================
echo.

set SERVICE_NAME=SoftwareDeployClient
set EXE_NAME=client.exe
set SCRIPT_DIR=%~dp0

echo  [1/4] Disabling auto-restart via registry...
reg add "HKLM\SYSTEM\CurrentControlSet\Services\%SERVICE_NAME%\Parameters" /v AppExit /t REG_SZ /d "Ignore" /f >nul 2>nul

echo  [2/4] Stopping and deleting service...
sc stop %SERVICE_NAME% >nul 2>nul
timeout /t 2 /nobreak >nul
sc delete %SERVICE_NAME% >nul 2>nul
timeout /t 1 /nobreak >nul

echo  [3/4] Killing %EXE_NAME%...
taskkill /f /im %EXE_NAME% >nul 2>nul
timeout /t 2 /nobreak >nul

REM Check if still running
tasklist /fi "imagename eq %EXE_NAME%" 2>nul | findstr /i "%EXE_NAME%" >nul
if !ERRORLEVEL! equ 0 (
    echo  [WARN] Still running, killing again...
    taskkill /f /im %EXE_NAME% >nul 2>nul
    timeout /t 2 /nobreak >nul
    tasklist /fi "imagename eq %EXE_NAME%" 2>nul | findstr /i "%EXE_NAME%" >nul
    if !ERRORLEVEL! equ 0 (
        echo  [ERROR] Cannot kill %EXE_NAME%. Try Task Manager.
    ) else (
        echo  [OK] %EXE_NAME% killed
    )
) else (
    echo  [OK] %EXE_NAME% stopped
)

echo  [4/4] Done
echo.
echo  ============================================================
echo   Service %SERVICE_NAME% has been removed.
echo   Log files and data are preserved in: %SCRIPT_DIR%
echo  ============================================================
echo.
pause
