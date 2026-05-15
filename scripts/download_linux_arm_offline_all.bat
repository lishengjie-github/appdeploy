@echo off
REM Fill linux_arm/python + linux_arm/vendor/wheels for AArch64 glibc (parallel to linux/).
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0.."
set "SCRIPT_DIR=%CD%\"
set "HERE=%~dp0"
set "PY_EXE="
if defined APP_DEPLOY_PYTHON if exist "%APP_DEPLOY_PYTHON%\python.exe" set "PY_EXE=%APP_DEPLOY_PYTHON%\python.exe"

set "LINUX_PACK_ROOT=linux_arm"
set "CPYTHON_LINUX_ARCH=aarch64"
set "CPYTHON_SKIP_MIRRORS=1"
if /i "%SKIP_GITHUB_ONLY%"=="0" set "CPYTHON_SKIP_MIRRORS="

echo [..] download_linux_embedded_python.py (linux_arm, aarch64)
if defined PY_EXE (
    "%PY_EXE%" "%HERE%download_linux_embedded_python.py"
) else (
    py -3 "%HERE%download_linux_embedded_python.py"
)
if errorlevel 1 exit /b 1

echo.
echo [..] download_linux_offline_deps.py (manylinux2014_aarch64)
if defined PY_EXE (
    "%PY_EXE%" "%HERE%download_linux_offline_deps.py"
) else (
    py -3 "%HERE%download_linux_offline_deps.py"
)
if errorlevel 1 exit /b 1

echo.
echo [OK] linux_arm\python and linux_arm\vendor\wheels ready. Run package_exe_zip.bat next.
exit /b 0
