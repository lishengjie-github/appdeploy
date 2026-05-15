@echo off
REM Download nssm.exe (win64) from nssm.cc into this folder. ASCII-only.
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo.
echo  Downloading NSSM -> "%~dp0nssm.exe"
echo  Source: https://nssm.cc/release/nssm-2.24.zip
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_nssm.ps1" "%~dp0"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Download failed. Check network / firewall / TLS.
    echo [INFO] Manual: unzip nssm-2.24.zip and copy win64\nssm.exe here.
    pause
    exit /b 1
)
echo.
echo Done. You can run install_windows.bat or install_server.bat now.
echo.
pause
