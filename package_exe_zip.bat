@echo off

REM Build server.exe + client.exe, then three ZIPs: Windows / Linux x64 / Linux arm64 (fixed names).

REM Each zip contains VERSION.txt with build time and Build ID.

REM Usage: package_exe_zip.bat nopause

chcp 65001 >nul 2>&1

setlocal enabledelayedexpansion



if /i "%~1"=="nopause" set NO_PAUSE=1

if /i "%~1"=="/nopause" set NO_PAUSE=1

if "%NONINTERACTIVE%"=="1" set NO_PAUSE=1

if defined CI set NO_PAUSE=1

if defined GITHUB_ACTIONS set NO_PAUSE=1

if defined GITLAB_CI set NO_PAUSE=1

if defined RUNNER_OS set NO_PAUSE=1



set SCRIPT_DIR=%~dp0

set DIST_DIR=%SCRIPT_DIR%dist

set DIST_EXE=%DIST_DIR%\exe



set "PY_CMD=py -3"

if defined APP_DEPLOY_PYTHON (

    for %%I in ("%APP_DEPLOY_PYTHON%") do set "APP_DEPLOY_PYTHON=%%~fI"

    if exist "!APP_DEPLOY_PYTHON!\python.exe" set "PY_CMD=!APP_DEPLOY_PYTHON!\python.exe"

)

if /i not "%PY_CMD%"=="py -3" (

    echo  [OK] Using APP_DEPLOY_PYTHON: %PY_CMD%

)



echo.

echo  ============================================================

echo       Package: exe -^> SoftwareDeploy_Windows / Linux_x64 / Linux_arm64 .zip

echo  ============================================================

echo.



%PY_CMD% --version >nul 2>nul

if errorlevel 1 (

    where python >nul 2>nul

    if errorlevel 1 (

        echo  [ERROR] Python not found

        call :bp

        exit /b 1

    )

    set "PY_CMD=python"

)

%PY_CMD% -m PyInstaller --version >nul 2>nul

if errorlevel 1 (

    echo  [ERROR] PyInstaller missing. Install: pip install pyinstaller

    call :bp

    exit /b 1

)



call "%SCRIPT_DIR%scripts\set_build_time.bat"



set SKIP_BUILD_PAUSE=1

call "%SCRIPT_DIR%build.bat"

if %ERRORLEVEL% neq 0 (

    echo  [ERROR] build.bat failed

    call :bp

    exit /b 1

)



if not exist "%DIST_EXE%\server.exe" (

    echo  [ERROR] Missing %DIST_EXE%\server.exe

    call :bp

    exit /b 1

)

if not exist "%DIST_EXE%\client.exe" (

    echo  [ERROR] Missing %DIST_EXE%\client.exe

    call :bp

    exit /b 1

)




call "%SCRIPT_DIR%scripts\package_zip_bundles.bat"

if errorlevel 1 (
    echo  [ERROR] Zip bundle step failed
    call :bp
    exit /b 1
)

call :bp
exit /b 0

:bp

if "%NO_PAUSE%"=="1" exit /b 0
if "%NONINTERACTIVE%"=="1" exit /b 0
if defined CI exit /b 0
if defined GITHUB_ACTIONS exit /b 0
if defined GITLAB_CI exit /b 0
if defined RUNNER_OS exit /b 0

pause

exit /b 0


