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
set CLIENT_SCRIPT=%SCRIPT_DIR%client.py
set CONFIG_FILE=%SCRIPT_DIR%client_config.json
set SERVICE_NAME=SoftwareDeployClient

set PYTHON_EXE=
set PYTHONW=

if exist "%SCRIPT_DIR%python\pythonw.exe" (
    set PYTHONW=%SCRIPT_DIR%python\pythonw.exe
    set PYTHON_EXE=%SCRIPT_DIR%python\python.exe
    echo  [OK] Offline Python: %PYTHONW%
) else if exist "%SCRIPT_DIR%python\python.exe" (
    set PYTHON_EXE=%SCRIPT_DIR%python\python.exe
    set PYTHONW=%PYTHON_EXE%
    echo  [OK] Offline Python: %PYTHON_EXE%
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo  [ERROR] Python not found
        echo  [INFO] Use offline bundle ^(folder python\^) or install Python 3.8+
        pause
        exit /b 1
    )
    for /f "tokens=*" %%i in ('where python') do set SYS_PYTHON_DIR=%%~dpi
    set PYTHON_EXE=python
    set PYTHONW=!SYS_PYTHON_DIR!pythonw.exe
    if not exist "!PYTHONW!" (
        echo  [WARN] pythonw.exe missing, using python.exe
        set PYTHONW=python.exe
    )
    echo  [OK] System Python
)

for /f "tokens=2 delims= " %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do set PY_VER=%%v
echo  [OK] Python version: %PY_VER%

if not exist "%CLIENT_SCRIPT%" (
    echo  [ERROR] client.py not found: %CLIENT_SCRIPT%
    pause
    exit /b 1
)

echo.
echo  Enter server IP ^(machine running server.py^)
echo  Example: 192.168.1.100
echo.
set /p SERVER_IP="  Server IP: "

if "%SERVER_IP%"=="" (
    echo  [ERROR] IP cannot be empty
    pause
    exit /b 1
)

set SERVER_URL=http://%SERVER_IP%:61234

echo.
echo  Install path ^(Enter = default^). Default: C:\QtProgram
echo.
set /p INSTALL_PATH_INPUT="  Install path: "

echo  [OK] Server URL: %SERVER_URL%

"%PYTHON_EXE%" "%SCRIPT_DIR%install_cfg_client.py" "%CONFIG_FILE%" "%SERVER_URL%" "%INSTALL_PATH_INPUT%"
if %ERRORLEVEL% neq 0 (
    echo  [WARN] Auto config failed. Set server_url in client_config.json to:
    echo         %SERVER_URL%
)

set NSSM=
where nssm >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set NSSM=nssm
) else if exist "%SCRIPT_DIR%nssm.exe" (
    set NSSM=%SCRIPT_DIR%nssm.exe
) else (
    echo.
    echo  [ERROR] nssm.exe not found
    echo  [INFO] Put nssm.exe in: %SCRIPT_DIR%
    echo  [INFO] https://nssm.cc/download
    echo.
    echo  Or run: "%PYTHON_EXE%" "%CLIENT_SCRIPT%"
    echo.
    pause
    exit /b 1
)

%NSSM% stop %SERVICE_NAME% >nul 2>nul
%NSSM% remove %SERVICE_NAME% confirm >nul 2>nul

echo.
echo  [..] Registering Windows service...
%NSSM% install %SERVICE_NAME% "%PYTHONW%" "%CLIENT_SCRIPT%"
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] nssm install failed
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

%NSSM% start %SERVICE_NAME%

REM ── 验证服务启动 ──
timeout /t 3 /nobreak >nul
%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if %ERRORLEVEL% equ 0 (
    echo  [OK] Service started successfully
) else (
    echo  [WARN] Service may not have started. Checking...
    %NSSM% status %SERVICE_NAME%
    echo.
    echo  Try manually: nssm start %SERVICE_NAME%
    echo  Check logs: %SCRIPT_DIR%service_stderr.log
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
