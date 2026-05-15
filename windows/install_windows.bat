@echo off
REM ASCII-only batch for cmd.exe on Chinese Windows.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo.
echo  ============================================================
echo       Deploy client - Windows installer
echo  ============================================================
echo.

set SCRIPT_DIR=%~dp0
set CLIENT_EXE=%SCRIPT_DIR%client.exe
set CLIENT_SCRIPT=%SCRIPT_DIR%client.py
set CONFIG_FILE=%SCRIPT_DIR%client_config.json
set SERVICE_NAME=SoftwareDeployClient
set NSSM=

REM -- Check VC++ Runtime (needed for client.exe) --
if exist "%CLIENT_EXE%" (
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

if exist "%CLIENT_EXE%" (
    set RUN_TARGET=%CLIENT_EXE%
    echo  [OK] Found client.exe
) else if exist "%CLIENT_SCRIPT%" (
    set PYTHON_EXE=
    if exist "%SCRIPT_DIR%python\python.exe" (
        set PYTHON_EXE=%SCRIPT_DIR%python\python.exe
        echo  [OK] Offline Python: !PYTHON_EXE!
    ) else if exist "%SCRIPT_DIR%python\pythonw.exe" (
        set PYTHON_EXE=%SCRIPT_DIR%python\pythonw.exe
        echo  [OK] Offline Python: !PYTHON_EXE! ^(fallback^)
    ) else (
        where python >nul 2>nul
        if !ERRORLEVEL! neq 0 (
            echo  [ERROR] Python not found and client.exe not present.
            echo  [INFO] Use client.exe or install Python 3.8+
            pause
            exit /b 1
        )
        for /f "tokens=*" %%i in ('where python') do set SYS_PYTHON_DIR=%%~dpi
        set PYTHON_EXE=!SYS_PYTHON_DIR!python.exe
        if not exist "!PYTHON_EXE!" (
            echo  [WARN] python.exe missing next to python on PATH, using python
            set PYTHON_EXE=python
        )
        echo  [OK] System Python: !PYTHON_EXE!
    )
    for /f "tokens=2 delims= " %%v in ('"!PYTHON_EXE!" --version 2^>^&1') do set PY_VER=%%v
    echo  [OK] Python version: !PY_VER!
    REM python.exe -u for NSSM (pythonw often exits immediately as a service)
    set RUN_TARGET=!PYTHON_EXE!
) else (
    echo  [ERROR] Neither client.exe nor client.py found in %SCRIPT_DIR%
    pause
    exit /b 1
)

REM -- Read current server_url from config (PowerShell, no python needed) --
set DEFAULT_URL=http://127.0.0.1:61234
for /f "tokens=*" %%u in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = Get-Content '%CONFIG_FILE%' -Raw -Encoding UTF8 | ConvertFrom-Json; $c.server_url } catch { 'http://127.0.0.1:61234' }" 2^>nul') do set DEFAULT_URL=%%u

echo.
echo  Enter server IP ^(Enter = use current config^)
echo  Current: %DEFAULT_URL%
echo.
set /p SERVER_IP="  Server IP (Enter to skip): "

if "%SERVER_IP%"=="" (
    set SERVER_URL=%DEFAULT_URL%
    echo  [OK] Using config: %SERVER_URL%
) else (
    set SERVER_URL=http://%SERVER_IP%:61234
    echo  [OK] Server URL: %SERVER_URL%
)

echo.
echo  Install path ^(Enter = default^). Default: C:\QtProgram
echo.
set /p INSTALL_PATH_INPUT="  Install path: "

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%SCRIPT_DIR%install_cfg_client.py" "%CONFIG_FILE%" "%SERVER_URL%" "%INSTALL_PATH_INPUT%"
    if !ERRORLEVEL! neq 0 (
        echo  [WARN] Auto config failed, trying PowerShell...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$f='%CONFIG_FILE%'; $c=Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json; $c.server_url='%SERVER_URL%'; if ('%INSTALL_PATH_INPUT%' -ne '') { $c.install_path.windows='%INSTALL_PATH_INPUT%' }; $c | ConvertTo-Json -Depth 10 | Set-Content $f -Encoding UTF8"
    )
) else (
    echo  [..] Python not found, using PowerShell to update config...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$f='%CONFIG_FILE%'; $c=Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json; $c.server_url='%SERVER_URL%'; if ('%INSTALL_PATH_INPUT%' -ne '') { $c.install_path.windows='%INSTALL_PATH_INPUT%' }; $c | ConvertTo-Json -Depth 10 | Set-Content $f -Encoding UTF8"
    if !ERRORLEVEL! equ 0 (
        echo  [OK] Config updated via PowerShell
    ) else (
        echo  [WARN] Config update failed. Edit client_config.json manually:
        echo         set server_url to %SERVER_URL%
    )
)

REM -- Check nssm --
if "%NSSM%"=="" (
    echo.
    echo  [ERROR] nssm.exe not found
    echo  [INFO] Put nssm.exe in: %SCRIPT_DIR%
    echo  [INFO] https://nssm.cc/download
    echo.
    echo  Or run manually:
    if exist "%CLIENT_EXE%" (
        echo     "%RUN_TARGET%"
    ) else (
        echo     "%RUN_TARGET%" -u "%CLIENT_SCRIPT%"
    )
    echo.
    pause
    exit /b 1
)
echo  [OK] NSSM: %NSSM%

REM -- Pre-flight: test if exe can launch --
set NEEDS_VCRUNTIME=0
if exist "%CLIENT_EXE%" (
    echo  [..] Pre-flight check: testing if client can start...
    start "" /b "%CLIENT_EXE%" --test 2>nul
    timeout /t 3 /nobreak >nul
    tasklist /fi "imagename eq client.exe" 2>nul | findstr /i "client.exe" >nul
    if !ERRORLEVEL! equ 0 (
        echo  [OK] client.exe launched successfully
        taskkill /f /im client.exe >nul 2>nul
    ) else (
        echo  [WARN] client.exe may have crashed on startup
        echo  [INFO] Common cause: missing Visual C++ Runtime
        set NEEDS_VCRUNTIME=1
    )
)

%NSSM% stop %SERVICE_NAME% >nul 2>nul
%NSSM% remove %SERVICE_NAME% confirm >nul 2>nul

echo.
echo  [..] Registering Windows service...
if exist "%CLIENT_EXE%" (
    %NSSM% install %SERVICE_NAME% "%RUN_TARGET%"
) else (
    %NSSM% install %SERVICE_NAME% "%RUN_TARGET%" -u "%CLIENT_SCRIPT%"
)
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] nssm install failed (error code: %ERRORLEVEL%)
    echo  [INFO] Try running as Administrator
    pause
    exit /b 1
)

%NSSM% set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "Software Deploy Client"
%NSSM% set %SERVICE_NAME% Description "Deploy system client agent"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppStdout "%SCRIPT_DIR%service_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%SCRIPT_DIR%service_stderr.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

echo  [..] Starting service...
%NSSM% start %SERVICE_NAME%

REM -- Verify; optional reset (same as start_client.bat) --
set CLI_RUN=0
timeout /t 5 /nobreak >nul
%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if !ERRORLEVEL! equ 0 set CLI_RUN=1
if !CLI_RUN! equ 0 (
    echo  [WARN] Not RUNNING yet; applying same recovery as start_client.bat ^(reset^)...
    %NSSM% stop %SERVICE_NAME% >nul 2>nul
    timeout /t 2 /nobreak >nul
    taskkill /f /im client.exe >nul 2>nul
    timeout /t 1 /nobreak >nul
    %NSSM% start %SERVICE_NAME%
    timeout /t 5 /nobreak >nul
    %NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
    if !ERRORLEVEL! equ 0 set CLI_RUN=1
)
if !CLI_RUN! equ 1 (
    echo  [OK] Service started successfully
) else (
    echo.
    echo  [ERROR] Service failed to start!
    echo.
    echo  --- Service status ---
    %NSSM% status %SERVICE_NAME%
    echo.
    if exist "%SCRIPT_DIR%service_stderr.log" (
        echo  --- Last errors (service_stderr.log) ---
        type "%SCRIPT_DIR%service_stderr.log"
        echo.
    )
    if "!NEEDS_VCRUNTIME!"=="1" (
        echo  [HINT] client.exe crashed during pre-flight check.
        echo         Install Visual C++ Redistributable x64:
        echo         https://aka.ms/vs/17/release/vc_redist.x64.exe
        echo.
    )
    echo  --- Try manually ---
    echo  Test exe:  "%CLIENT_EXE%"
    echo  Restart:   nssm start %SERVICE_NAME%
    echo  Logs:      %SCRIPT_DIR%service_stderr.log
    echo.
    pause
    exit /b 1
)

if exist "%SCRIPT_DIR%python\python.exe" (
    echo.
    echo  [..] Adding bundled Python to User PATH ^(new CMD needed^)...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%add_python_user_path.ps1" "%SCRIPT_DIR%python"
    if exist "%SCRIPT_DIR%python.cmd" echo  [OK] You can also run: "%SCRIPT_DIR%python.cmd" client.py
)

echo.
echo  ============================================================
echo   Done. Service: %SERVICE_NAME%
echo   Server URL:    %SERVER_URL%
echo   Config:        %CONFIG_FILE%
echo   Log:           %SCRIPT_DIR%client.log
echo   nssm start ^| stop ^| status %SERVICE_NAME%
echo  ============================================================
echo.
pause
