@echo off
REM Must stay ASCII-only so cmd.exe parses correctly on Chinese Windows (GBK console).
REM No pause: set NO_PAUSE=1 / NONINTERACTIVE=1, or: package_offline.bat nopause
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

if /i "%~1"=="nopause" set NO_PAUSE=1
if /i "%~1"=="/nopause" set NO_PAUSE=1
if "%NONINTERACTIVE%"=="1" set NO_PAUSE=1
if defined CI set NO_PAUSE=1
if defined GITHUB_ACTIONS set NO_PAUSE=1
if defined GITLAB_CI set NO_PAUSE=1
if defined RUNNER_OS set NO_PAUSE=1

echo.
echo  ============================================================
echo       Offline package builder (embedded Python + Flask)
echo  ============================================================
echo.

cd /d "%~dp0.."
set "SCRIPT_DIR=%CD%\"
set DIST_DIR=%SCRIPT_DIR%dist
set PYTHON_VER=3.10.11
REM Embedded zip must be placed manually (offline): python-embed.zip OR python-%PYTHON_VER%-embed-amd64.zip
REM --- Require system Python for pip ---
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Need Python on this PC to build the offline bundle.
    echo  [INFO] Target machines will use the embedded Python inside the bundle.
    call :bp
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set SYS_PY_VER=%%v
echo  [OK] Host Python: %SYS_PY_VER%

REM --- nssm.exe (optional): PATH, else download from nssm.cc ---
if not exist "%SCRIPT_DIR%nssm.exe" if not exist "%SCRIPT_DIR%windows\nssm.exe" (
    echo.
    echo  [INFO] nssm.exe not in repo root or windows\, searching PATH...
    where nssm >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        for /f "tokens=*" %%i in ('where nssm') do copy "%%i" "%SCRIPT_DIR%windows\nssm.exe" >nul
        echo  [OK] Copied nssm.exe to windows\
    ) else (
        echo  [..] Downloading NSSM 2.24 ^(HTTPS^)...
        powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%windows\download_nssm.ps1" "%SCRIPT_DIR%windows"
        if exist "%SCRIPT_DIR%windows\nssm.exe" (
            echo  [OK] nssm.exe saved to windows\
        ) else if exist "%SCRIPT_DIR%nssm.exe" (
            echo  [OK] nssm.exe at repo root
        ) else (
            echo  [WARN] nssm.exe still missing; run windows\download_nssm.bat or copy win64\nssm.exe to windows\
        )
    )
)

set TEMP_DIR=%SCRIPT_DIR%build_temp
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
mkdir "%TEMP_DIR%"
mkdir "%DIST_DIR%"

set PYTHON_ZIP=%TEMP_DIR%\python-embed.zip
set PYTHON_EXTRACT=%TEMP_DIR%\python-embed

echo.
echo  [1/4] Embedded Python %PYTHON_VER% ^(local zip only, no download^)...

if exist "%SCRIPT_DIR%python-embed.zip" (
    echo  [OK] Using: python-embed.zip
    copy "%SCRIPT_DIR%python-embed.zip" "%PYTHON_ZIP%" >nul
) else if exist "%SCRIPT_DIR%python-%PYTHON_VER%-embed-amd64.zip" (
    echo  [OK] Using: python-%PYTHON_VER%-embed-amd64.zip
    copy "%SCRIPT_DIR%python-%PYTHON_VER%-embed-amd64.zip" "%PYTHON_ZIP%" >nul
) else (
    echo  [ERROR] No embed zip next to this script.
    echo  [INFO] Put one of these files here:
    echo         python-%PYTHON_VER%-embed-amd64.zip
    echo         or python-embed.zip ^(same file, shorter name^)
    echo  [INFO] Get from python.org or mirrors, then re-run this script.
    rmdir /s /q "%TEMP_DIR%"
    call :bp
    exit /b 1
)

echo  [..] Unzip Python...
mkdir "%PYTHON_EXTRACT%"
powershell -NoProfile -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_EXTRACT%' -Force"

echo.
echo  [2/4] Install Flask into embedded Python...

set SITE_PACKAGES=%PYTHON_EXTRACT%\Lib\site-packages
mkdir "%SITE_PACKAGES%"

for %%f in ("%PYTHON_EXTRACT%\python*._pth") do (
    (
        echo python310.zip
        echo .
        echo Lib/site-packages
        echo import site
    ) > "%%f"
)

set GET_PIP_PY=%TEMP_DIR%\get-pip.py
if exist "%SCRIPT_DIR%get-pip.py" (
    copy "%SCRIPT_DIR%get-pip.py" "%GET_PIP_PY%" >nul
) else (
    echo  [..] Download get-pip.py ^(retry / mirrors^)...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%download_get_pip.ps1" -OutFile "%GET_PIP_PY%"
    if %ERRORLEVEL% neq 0 (
        echo  [ERROR] get-pip.py download failed
        echo  [INFO] Save manually as: %SCRIPT_DIR%get-pip.py from https://bootstrap.pypa.io/get-pip.py
        rmdir /s /q "%TEMP_DIR%"
        call :bp
        exit /b 1
    )
    copy "%GET_PIP_PY%" "%SCRIPT_DIR%get-pip.py" >nul
)

echo  [..] pip...
"%PYTHON_EXTRACT%\python.exe" "%GET_PIP_PY%" --target "%SITE_PACKAGES%" --no-warn-script-location 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [WARN] pip into embed failed; trying host pip...
    pip install flask --target "%SITE_PACKAGES%" --no-deps
    pip install werkzeug jinja2 markupsafe itsdangerous click blinker --target "%SITE_PACKAGES%" --no-deps
)

echo  [..] Flask...
"%PYTHON_EXTRACT%\python.exe" -m pip install flask --target "%SITE_PACKAGES%" --no-warn-script-location 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [..] Host pip install Flask...
    pip install flask --target "%SITE_PACKAGES%"
)

echo  [OK] Flask ready

echo.
echo  [3/4] Pack single offline bundle ^(server + Windows client, one python\^)...

set BUNDLE_DIR=%DIST_DIR%\offline_bundle
if exist "%BUNDLE_DIR%" rmdir /s /q "%BUNDLE_DIR%"
mkdir "%BUNDLE_DIR%"
mkdir "%BUNDLE_DIR%\python"

xcopy "%PYTHON_EXTRACT%\*" "%BUNDLE_DIR%\python\" /e /i /q >nul

REM --- Server + client Windows (same root; shared embedded Python) ---
copy "%SCRIPT_DIR%server.py" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%server_config.json" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\install_server.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%client.py" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%client_config.json" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\install_windows.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\uninstall_server.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\uninstall_windows.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\start_client.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\start_server.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%install_cfg_client.py" "%BUNDLE_DIR%\" >nul
if exist "%SCRIPT_DIR%dist\exe\server.exe" copy "%SCRIPT_DIR%dist\exe\server.exe" "%BUNDLE_DIR%\" >nul
if exist "%SCRIPT_DIR%dist\exe\client.exe" copy "%SCRIPT_DIR%dist\exe\client.exe" "%BUNDLE_DIR%\" >nul

REM --- Helpers ---
copy "%SCRIPT_DIR%windows\add_python_user_path.ps1" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\python.cmd" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\pip.cmd" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\download_nssm.ps1" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%windows\download_nssm.bat" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%download_python_embed.ps1" "%BUNDLE_DIR%\" >nul
copy "%SCRIPT_DIR%download_get_pip.ps1" "%BUNDLE_DIR%\" >nul
if exist "%SCRIPT_DIR%windows\nssm.exe" copy "%SCRIPT_DIR%windows\nssm.exe" "%BUNDLE_DIR%\" >nul
if exist "%SCRIPT_DIR%nssm.exe" copy "%SCRIPT_DIR%nssm.exe" "%BUNDLE_DIR%\" >nul

REM --- Clean up files that should NOT be in the bundle ---
if exist "%BUNDLE_DIR%\packages" rmdir /s /q "%BUNDLE_DIR%\packages"
if exist "%BUNDLE_DIR%\dist" rmdir /s /q "%BUNDLE_DIR%\dist"
if exist "%BUNDLE_DIR%\build_temp" rmdir /s /q "%BUNDLE_DIR%\build_temp"
if exist "%BUNDLE_DIR%\*.log" del /q "%BUNDLE_DIR%\*.log"
if exist "%BUNDLE_DIR%\*.db" del /q "%BUNDLE_DIR%\*.db"
if exist "%BUNDLE_DIR%\client.log" del /q "%BUNDLE_DIR%\client.log"
if exist "%BUNDLE_DIR%\server.log" del /q "%BUNDLE_DIR%\server.log"
if exist "%BUNDLE_DIR%\deploy.db" del /q "%BUNDLE_DIR%\deploy.db"

if not exist "%BUNDLE_DIR%\python\python.exe" (
    echo  [ERROR] Missing python\python.exe - bundle incomplete
    call :bp
    exit /b 1
)

(
echo Deploy - offline bundle ^(Windows server + Windows client^)
echo ============================================================
echo.
echo One folder: copy to server PC and/or each Windows client ^(same files^).
echo Shared: python\ = embedded Python + Flask ^(for server only needs Flask^).
echo.
echo SERVER:
echo   1. Run install_server.bat ^(admin^)
echo   2. Open http://THIS-PC-IP:61234
echo   nssm: SoftwareDeployServer   Firewall: port 61234
echo   Uninstall: uninstall_server.bat
echo.
echo WINDOWS CLIENT:
echo   1. Run install_windows.bat ^(admin^)
echo   2. Enter server IP when asked
echo   nssm: SoftwareDeployClient
echo   Uninstall: uninstall_windows.bat
echo.
echo Python: after install_*.bat, User PATH includes python\ ^(new CMD^).
echo Or run python.cmd in this folder without PATH.
echo.
echo LINUX CLIENT: see subfolder client_linux\ ^(scripts only, uses system python3^).
) > "%BUNDLE_DIR%\README.txt"

echo  [OK] offline_bundle ready

echo.
echo  [4/4] Linux client scripts into client_linux\...

set LINUX_DIR=%BUNDLE_DIR%\client_linux
if exist "%LINUX_DIR%" rmdir /s /q "%LINUX_DIR%"
mkdir "%LINUX_DIR%"

copy "%SCRIPT_DIR%client.py" "%LINUX_DIR%\" >nul
copy "%SCRIPT_DIR%client_config.json" "%LINUX_DIR%\" >nul
copy "%SCRIPT_DIR%linux\bundle\install_linux.sh" "%LINUX_DIR%\" >nul
copy "%SCRIPT_DIR%linux\bundle\uninstall_linux.sh" "%LINUX_DIR%\" >nul
copy "%SCRIPT_DIR%linux\bundle\uninstall_server.sh" "%LINUX_DIR%\" >nul
copy "%SCRIPT_DIR%linux\bundle\start_client.sh" "%LINUX_DIR%\" >nul

(
echo Linux client only ^(copy this subfolder to Linux machines^)
echo ========================================
echo.
echo 1. sudo bash install_linux.sh
echo 2. Enter server IP
echo Requires: Python 3.8+, systemd
echo systemctl: swdeploy-client
echo Uninstall: sudo bash uninstall_linux.sh
) > "%LINUX_DIR%\README.txt"

echo  [OK] client_linux subfolder done

rmdir /s /q "%TEMP_DIR%"

echo.
echo  ============================================================
echo   DONE. Single bundle: %DIST_DIR%\offline_bundle
echo     - install_server.bat / install_windows.bat ^(same folder, one python\^)
echo     - client_linux\      Linux client scripts only
echo  ============================================================
echo.
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
