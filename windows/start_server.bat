@echo off
REM Start / stop / restart / reset / status the deploy server Windows service.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

set SERVICE_NAME=SoftwareDeployServer
set PORT=61234
set EXE_NAME=server.exe

set NSSM=
where nssm >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set NSSM=nssm
) else if exist "%~dp0nssm.exe" (
    set NSSM=%~dp0nssm.exe
)

if "%NSSM%"=="" (
    echo  [ERROR] nssm.exe not found. Put it next to this script or add to PATH.
    pause
    exit /b 1
)

if "%1"=="stop" (
    echo  Stopping %SERVICE_NAME% ...
    %NSSM% stop %SERVICE_NAME%
    goto :done
)
if "%1"=="restart" (
    echo  Restarting %SERVICE_NAME% ...
    %NSSM% restart %SERVICE_NAME%
    timeout /t 3 /nobreak >nul
    %NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
    if !ERRORLEVEL! equ 0 (
        echo  [OK] Service is running
    ) else (
        echo  [WARN] Service may not have started, trying reset...
        goto :do_reset
    )
    goto :done
)
if "%1"=="reset" (
    goto :do_reset
)
if "%1"=="status" (
    %NSSM% status %SERVICE_NAME%
    goto :done
)

REM Default: start
echo  Starting %SERVICE_NAME% ...
%NSSM% start %SERVICE_NAME%
timeout /t 3 /nobreak >nul
%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if %ERRORLEVEL% equ 0 (
    echo  [OK] Service is running
) else (
    echo  [WARN] Service may not have started, try: %~nx0 reset
    %NSSM% status %SERVICE_NAME%
)
goto :done

:do_reset
echo.
echo  [..] Force reset %SERVICE_NAME% ...
echo  [..] Stopping service...
%NSSM% stop %SERVICE_NAME% >nul 2>nul
timeout /t 2 /nobreak >nul

echo  [..] Killing any lingering %EXE_NAME% processes...
taskkill /f /im %EXE_NAME% >nul 2>nul
timeout /t 1 /nobreak >nul

REM Kill any process holding the port
echo  [..] Checking port %PORT%...
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    if not "%%p"=="0" (
        echo  [..] Killing PID %%p on port %PORT%
        taskkill /f /pid %%p >nul 2>nul
    )
)
timeout /t 1 /nobreak >nul

echo  [..] Starting service...
%NSSM% start %SERVICE_NAME%
timeout /t 5 /nobreak >nul

%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if %ERRORLEVEL% equ 0 (
    echo  [OK] Service reset and running
) else (
    echo  [ERROR] Service still not running after reset
    %NSSM% status %SERVICE_NAME%
    if exist "%~dp0server_stderr.log" (
        echo.
        echo  --- server_stderr.log ---
        type "%~dp0server_stderr.log"
    )
)

:done
echo.
pause
