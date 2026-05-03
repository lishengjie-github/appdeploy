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
set SERVER_SCRIPT=%SCRIPT_DIR%server.py
set CONFIG_FILE=%SCRIPT_DIR%server_config.json
set SERVICE_NAME=SoftwareDeployServer

if not exist "%SERVER_SCRIPT%" (
    echo  [ERROR] server.py not found: %SERVER_SCRIPT%
    pause
    exit /b 1
)

set PYTHON_DIR=%SCRIPT_DIR%python
set PYTHONW=%PYTHON_DIR%\pythonw.exe
set PYTHON_EXE=%PYTHON_DIR%\python.exe

if exist "%PYTHONW%" (
    echo  [OK] Offline Python: %PYTHONW%
) else if exist "%PYTHON_EXE%" (
    set PYTHONW=%PYTHON_EXE%
    echo  [OK] Offline Python: %PYTHON_EXE%
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo  [ERROR] Python not found. Use offline bundle ^(python\^) or install Python 3.8+
        pause
        exit /b 1
    )
    for /f "tokens=*" %%i in ('where python') do set PYTHON_DIR=%%~dpi
    set PYTHONW=%PYTHON_DIR%pythonw.exe
    if not exist "%PYTHONW%" (
        echo  [WARN] pythonw.exe missing, using python.exe
        set PYTHONW=python.exe
    )
    echo  [OK] System Python
)

for /f "tokens=2 delims= " %%v in ('"%PYTHONW%" --version 2^>^&1') do set PY_VER=%%v
echo  [OK] Python version: %PY_VER%

"%PYTHONW%" -c "import flask" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [WARN] Flask import failed...
    if exist "%SCRIPT_DIR%python\Lib\site-packages\flask" (
        echo  [OK] Flask folder present in offline python
    ) else (
        echo  [ERROR] Install Flask or run package_offline.bat first
        pause
        exit /b 1
    )
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
    pause
    exit /b 1
)

%NSSM% stop %SERVICE_NAME% >nul 2>nul
%NSSM% remove %SERVICE_NAME% confirm >nul 2>nul

echo.
echo  [..] Registering Windows service...
%NSSM% install %SERVICE_NAME% "%PYTHONW%" "%SERVER_SCRIPT%"
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] nssm install failed
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

%NSSM% start %SERVICE_NAME%

REM ── Verify service started ──
timeout /t 3 /nobreak >nul
%NSSM% status %SERVICE_NAME% | findstr /i "SERVICE_RUNNING" >nul
if %ERRORLEVEL% equ 0 (
    echo  [OK] Service started successfully
) else (
    echo  [WARN] Service may not have started. Checking...
    %NSSM% status %SERVICE_NAME%
    echo.
    echo  Try manually: nssm start %SERVICE_NAME%
    echo  Check logs: %SCRIPT_DIR%server_stderr.log
)

if exist "%SCRIPT_DIR%python\python.exe" (
    echo.
    echo  [..] Adding bundled Python to User PATH ^(new CMD needed^)...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%add_python_user_path.ps1" "%SCRIPT_DIR%python"
    if exist "%SCRIPT_DIR%python.cmd" echo  [OK] You can also run: "%SCRIPT_DIR%python.cmd" server.py
)

echo.
echo  ============================================================
echo   Done. Service: %SERVICE_NAME%
echo   Port:          61234
echo   Config:        %CONFIG_FILE%
echo   Log:           %SCRIPT_DIR%server.log
echo   Open:          http://THIS-PC-IP:61234
echo   nssm start ^| stop ^| status %SERVICE_NAME%
echo  ============================================================
echo.
pause
