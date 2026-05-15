@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

REM Skip pause on errors/success when unattended (NO_PAUSE=1, CI, or caller sets SKIP_BUILD_PAUSE)
if /i "%~1"=="nopause" set NO_PAUSE=1
if /i "%~1"=="/nopause" set NO_PAUSE=1
if "%NO_PAUSE%"=="1" set SKIP_BUILD_PAUSE=1
if "%NONINTERACTIVE%"=="1" set SKIP_BUILD_PAUSE=1
if defined CI set SKIP_BUILD_PAUSE=1
if defined GITHUB_ACTIONS set SKIP_BUILD_PAUSE=1
if defined GITLAB_CI set SKIP_BUILD_PAUSE=1
if defined RUNNER_OS set SKIP_BUILD_PAUSE=1

echo.
echo  ============================================================
echo       Build server.exe and client.exe
echo  ============================================================
echo.

cd /d "%~dp0"
set "SCRIPT_DIR=%CD%\"
set BUILD_DIR=%CD%\build
set DIST_EXE=%CD%\dist\exe
set DLL_DIR=

REM Optional: fixed Python install, e.g. set APP_DEPLOY_PYTHON=F:\software\python3.12.1
set "PY="
if defined APP_DEPLOY_PYTHON (
    for %%I in ("%APP_DEPLOY_PYTHON%") do set "APP_DEPLOY_PYTHON=%%~fI"
    if exist "!APP_DEPLOY_PYTHON!\python.exe" (
        set "PY=!APP_DEPLOY_PYTHON!\python.exe"
        echo  [OK] Using APP_DEPLOY_PYTHON: !PY!
    ) else (
        echo  [WARN] APP_DEPLOY_PYTHON set but missing python.exe: "%APP_DEPLOY_PYTHON%"
    )
)
REM Prefer Windows Python Launcher ^(avoids Microsoft Store stub^)
if not defined PY (
    set "PY=py -3"
    py -3 --version >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        set "PY=python"
        where python >nul 2>nul
        if !ERRORLEVEL! neq 0 (
            echo  [ERROR] Python not found. Install Python 3.x, set APP_DEPLOY_PYTHON, or use "py -3"
            call :bp
            exit /b 1
        )
    )
)

set "_DLL_OUT=%TEMP%\swdeploy_dll_%RANDOM%.txt"
%PY% "%CD%\tools\print_dll_dir.py" > "%_DLL_OUT%" 2>nul
if exist "%_DLL_OUT%" (
    for /f "usebackq delims=" %%L in ("%_DLL_OUT%") do set "DLL_DIR=%%L"
    del /q "%_DLL_OUT%" 2>nul
)

if not exist "server.py" (
    echo  [ERROR] server.py not found in %CD%
    call :bp
    exit /b 1
)
if not exist "client.py" (
    echo  [ERROR] client.py not found in %CD%
    call :bp
    exit /b 1
)

%PY% -m PyInstaller --version >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] PyInstaller not installed. Run: pip install pyinstaller
    call :bp
    exit /b 1
)

if not exist "%DIST_EXE%" mkdir "%DIST_EXE%"

REM -- Check VC++ runtime DLLs --
set HAS_DLLS=0
if exist "%DLL_DIR%\vcruntime140.dll" if exist "%DLL_DIR%\vcruntime140_1.dll" set HAS_DLLS=1
if "!HAS_DLLS!"=="1" (
    echo  [OK] VC++ runtime DLLs found, will bundle into exe
) else (
    echo  [WARN] VC++ runtime DLLs not found, exe will need VC++ Redistributable
)

REM -- Clean --
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"

echo.
echo  [1/2] Building server.exe ...

if "!HAS_DLLS!"=="1" (
    %PY% -m PyInstaller --noconfirm --onefile --name server --distpath "%DIST_EXE%" --workpath "%BUILD_DIR%" --specpath "%CD%" --add-data "server_config.json;." --add-binary "%DLL_DIR%\vcruntime140.dll;." --add-binary "%DLL_DIR%\vcruntime140_1.dll;." --hidden-import flask --hidden-import werkzeug --hidden-import jinja2 --hidden-import markupsafe --hidden-import sqlite3 server.py
) else (
    %PY% -m PyInstaller --noconfirm --onefile --name server --distpath "%DIST_EXE%" --workpath "%BUILD_DIR%" --specpath "%CD%" --add-data "server_config.json;." --hidden-import flask --hidden-import werkzeug --hidden-import jinja2 --hidden-import markupsafe --hidden-import sqlite3 server.py
)

if %ERRORLEVEL% neq 0 (
    echo  [ERROR] server.exe build failed
    call :bp
    exit /b 1
)
echo  [OK] server.exe

echo.
echo  [2/2] Building client.exe ...

if "!HAS_DLLS!"=="1" (
    %PY% -m PyInstaller --noconfirm --onefile --name client --distpath "%DIST_EXE%" --workpath "%BUILD_DIR%" --specpath "%CD%" --add-data "client_config.json;." --add-binary "%DLL_DIR%\vcruntime140.dll;." --add-binary "%DLL_DIR%\vcruntime140_1.dll;." --hidden-import uuid client.py
) else (
    %PY% -m PyInstaller --noconfirm --onefile --name client --distpath "%DIST_EXE%" --workpath "%BUILD_DIR%" --specpath "%CD%" --add-data "client_config.json;." --hidden-import uuid client.py
)

if %ERRORLEVEL% neq 0 (
    echo  [ERROR] client.exe build failed
    call :bp
    exit /b 1
)
echo  [OK] client.exe

REM -- Clean build artifacts --
rmdir /s /q "%BUILD_DIR%"
del /q "%CD%\server.spec" 2>nul
del /q "%CD%\client.spec" 2>nul

echo.
echo  ============================================================
echo   Done. Output: %DIST_EXE%\
echo     server.exe  - server + Flask + VC++ runtime bundled
echo     client.exe  - client + VC++ runtime bundled
echo.
echo   Usage:
echo     Copy server.exe + server_config.json to server machine
echo     Copy client.exe + client_config.json to each client
echo  ============================================================
echo.
call :bp
goto :EOF

:bp
if "!SKIP_BUILD_PAUSE!"=="1" exit /b 0
if "%NO_PAUSE%"=="1" exit /b 0
if "%NONINTERACTIVE%"=="1" exit /b 0
if defined CI exit /b 0
if defined GITHUB_ACTIONS exit /b 0
if defined GITLAB_CI exit /b 0
if defined RUNNER_OS exit /b 0
pause
exit /b 0
