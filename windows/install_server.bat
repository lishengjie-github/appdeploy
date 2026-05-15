@echo off
REM ASCII-only batch for cmd.exe on Chinese Windows.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo.
echo  ============================================================
echo       Deploy server - Windows installer
echo  ============================================================
echo.

set SCRIPT_DIR=%~dp0
set SERVER_EXE=%SCRIPT_DIR%server.exe
set SERVER_SCRIPT=%SCRIPT_DIR%server.py
set CONFIG_FILE=%SCRIPT_DIR%server_config.json
set SERVICE_NAME=SoftwareDeployServer
set NSSM=

REM -- Check VC++ Runtime (needed for server.exe) --
if exist "%SERVER_EXE%" (
    if not exist "C:\Windows\System32\vcruntime140.dll" (
        echo  [WARN] Visual C++ Runtime not found
        if exist "%SCRIPT_DIR%VC_redist.x64.exe" (
            echo  [..] Installing VC++ Runtime...
            "%SCRIPT_DIR%VC_redist.x64.exe" /install /quiet /norestart
            timeout /t 30 /nobreak >nul
            echo  [OK] VC++ Runtime installed
        ) else (
            echo  [INFO] Please install VC++ Redistributable x64:
            echo         https://aka.ms/vs/17/release/vc_redist.x64.exe
            echo         Or place VC_redist.x64.exe next to this script
            echo.
        )
    ) else (
        echo  [OK] VC++ Runtime found
    )
)

REM -- Detect nssm --
where nssm >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set NSSM=nssm
) else if exist "%SCRIPT_DIR%nssm.exe" (
    set NSSM=%SCRIPT_DIR%nssm.exe
)

REM -- Detect run target: exe or python --
set RUN_TARGET=

if exist "%SERVER_EXE%" (
    set RUN_TARGET=%SERVER_EXE%
    echo  [OK] Found server.exe
) else if exist "%SERVER_SCRIPT%" (
    set PYTHON_DIR=%SCRIPT_DIR%python
    set PYTHONW=%PYTHON_DIR%\pythonw.exe
    set PYTHON_EXE=%PYTHON_DIR%\python.exe
    if exist "%PYTHON_EXE%" (
        echo  [OK] Offline Python: %PYTHON_EXE%
    ) else if exist "%PYTHONW%" (
        set PYTHON_EXE=%PYTHONW%
        echo  [OK] Offline Python: %PYTHONW% ^(fallback^)
    ) else (
        where python >nul 2>nul
        if !ERRORLEVEL! neq 0 (
            echo  [ERROR] Python not found and server.exe not present.
            echo  [INFO] Use server.exe or install Python 3.8+
            pause
            exit /b 1
        )
        for /f "tokens=*" %%i in ('where python') do set PYTHON_DIR=%%~dpi
        set PYTHON_EXE=!PYTHON_DIR!python.exe
        if not exist "!PYTHON_EXE!" (
            echo  [WARN] python.exe missing in same folder as python on PATH
            set PYTHON_EXE=python
        )
        echo  [OK] System Python: !PYTHON_EXE!
    )
    REM python.exe for NSSM; install uses: python.exe -u server.py (not one quoted blob)
    set RUN_TARGET=!PYTHON_EXE!
) else (
    echo  [ERROR] Neither server.exe nor server.py found in %SCRIPT_DIR%
    pause
    exit /b 1
)

REM -- Check nssm --
if "%NSSM%"=="" (
    echo.
    echo  [ERROR] nssm.exe not found
    echo  [INFO] Put nssm.exe in: %SCRIPT_DIR%
    echo  [INFO] https://nssm.cc/download
    echo.
    pause
    exit /b 1
)
echo  [OK] NSSM: %NSSM%

REM -- Check config file --
if not exist "%CONFIG_FILE%" (
    echo  [WARN] Config file not found: %CONFIG_FILE%
    echo  [INFO] Server will use default settings
)

REM -- Pre-flight: test if exe can launch --
echo.
echo  [..] Pre-flight check: testing if server can start...
set NEEDS_VCRUNTIME=0

if exist "%SERVER_EXE%" (
    REM Try launching server.exe briefly to detect missing DLLs
    start "" /b "%SERVER_EXE%" --test 2>nul
    timeout /t 3 /nobreak >nul
    tasklist /fi "imagename eq server.exe" 2>nul | findstr /i "server.exe" >nul
    if !ERRORLEVEL! equ 0 (
        echo  [OK] server.exe launched successfully
        taskkill /f /im server.exe >nul 2>nul
    ) else (
        echo  [WARN] server.exe may have crashed on startup
        echo  [INFO] Common cause: missing Visual C++ Runtime
        echo  [INFO] Download VC++ Redist x64 from Microsoft if needed
        echo  [INFO] Continuing with service registration...
        set NEEDS_VCRUNTIME=1
    )
)

REM -- Clean up old service --
%NSSM% stop %SERVICE_NAME% >nul 2>nul
%NSSM% remove %SERVICE_NAME% confirm >nul 2>nul

echo.
echo  [..] Registering Windows service...
if exist "%SERVER_EXE%" (
    %NSSM% install %SERVICE_NAME% "%RUN_TARGET%"
) else (
    %NSSM% install %SERVICE_NAME% "%RUN_TARGET%" -u "%SERVER_SCRIPT%"
)
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] nssm install failed (error code: %ERRORLEVEL%)
    echo  [INFO] Try running as Administrator
    pause
    exit /b 1
)

%NSSM% set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "Software Deploy Server"
%NSSM% set %SERVICE_NAME% Description "Deploy system server"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppStdout "%SCRIPT_DIR%server_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%SCRIPT_DIR%server_stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

echo  [..] Starting service...
%NSSM% start %SERVICE_NAME%

REM -- Verify; optional reset (same as start_server.bat) — avoid relying on ERRORLEVEL after "if" --
set SRV_RUN=0
timeout /t 5 /nobreak >nul
%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if !ERRORLEVEL! equ 0 set SRV_RUN=1
if !SRV_RUN! equ 0 (
    echo  [WARN] Not RUNNING yet; applying same recovery as start_server.bat ^(reset^)...
    %NSSM% stop %SERVICE_NAME% >nul 2>nul
    timeout /t 2 /nobreak >nul
    taskkill /f /im server.exe >nul 2>nul
    timeout /t 1 /nobreak >nul
    for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":61234" ^| findstr "LISTENING"') do (
        if not "%%p"=="0" (
            echo  [..] Freeing port 61234 ^(PID %%p^)
            taskkill /f /pid %%p >nul 2>nul
        )
    )
    timeout /t 1 /nobreak >nul
    %NSSM% start %SERVICE_NAME%
    timeout /t 5 /nobreak >nul
    %NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
    if !ERRORLEVEL! equ 0 set SRV_RUN=1
)
if !SRV_RUN! equ 1 (
    echo  [OK] Service started successfully
) else (
    echo.
    echo  [ERROR] Service failed to start!
    echo.
    echo  --- Service status ---
    %NSSM% status %SERVICE_NAME%
    echo.
    REM Check stderr log for clues
    if exist "%SCRIPT_DIR%server_stderr.log" (
        echo  --- Last errors (server_stderr.log) ---
        type "%SCRIPT_DIR%server_stderr.log"
        echo.
    )
    if "!NEEDS_VCRUNTIME!"=="1" (
        echo  [HINT] server.exe crashed during pre-flight check.
        echo         Install Visual C++ Redistributable x64:
        echo         https://aka.ms/vs/17/release/vc_redist.x64.exe
        echo.
    )
    echo  --- Try manually ---
    echo  Test exe:  "%SERVER_EXE%"
    echo  Restart:   nssm start %SERVICE_NAME%
    echo  Logs:      %SCRIPT_DIR%server_stderr.log
    echo             %SCRIPT_DIR%server_stdout.log
    echo.
    pause
    exit /b 1
)

REM -- Detect local IP --
set LOCAL_IP=127.0.0.1
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
    for /f "tokens=*" %%b in ("%%a") do set LOCAL_IP=%%b
)
set LOCAL_IP=%LOCAL_IP: =%

echo.
echo  ============================================================
echo   Done. Service: %SERVICE_NAME%
echo   Port:          61234
echo   Config:        %CONFIG_FILE%
echo   Log:           %SCRIPT_DIR%server.log
echo   Open:          http://%LOCAL_IP%:61234
echo   nssm start ^| stop ^| status %SERVICE_NAME%
echo  ============================================================
echo.
pause
