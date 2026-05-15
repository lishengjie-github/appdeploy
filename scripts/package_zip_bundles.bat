@echo off
REM Three ZIPs. Expects SCRIPT_DIR, DIST_DIR, PY_CMD, BUILD_TIME, BUILD_ID, DIST_EXE from caller.

REM ---------------------------------------------------------------------------

REM 1) Windows only

REM ---------------------------------------------------------------------------

set STAGE=%DIST_DIR%\_stage_windows

if exist "%STAGE%" rmdir /s /q "%STAGE%"

mkdir "%STAGE%"



copy /y "%DIST_EXE%\server.exe" "%STAGE%\" >nul

copy /y "%DIST_EXE%\client.exe" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%server_config.json" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%client_config.json" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\install_server.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\install_windows.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\uninstall_server.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\uninstall_windows.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\start_server.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\start_client.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%install_cfg_client.py" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\download_nssm.bat" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%windows\download_nssm.ps1" "%STAGE%\" >nul

if exist "%SCRIPT_DIR%windows\nssm.exe" copy /y "%SCRIPT_DIR%windows\nssm.exe" "%STAGE%\" >nul

if exist "%SCRIPT_DIR%nssm.exe" copy /y "%SCRIPT_DIR%nssm.exe" "%STAGE%\" >nul

if exist "%SCRIPT_DIR%windows\VC_redist.x64.exe" copy /y "%SCRIPT_DIR%windows\VC_redist.x64.exe" "%STAGE%\" >nul

if exist "%SCRIPT_DIR%VC_redist.x64.exe" copy /y "%SCRIPT_DIR%VC_redist.x64.exe" "%STAGE%\" >nul



(

echo SoftwareDeploy

echo Package: Windows x64 - server.exe and client.exe

echo Build time: %BUILD_TIME%

echo Build ID: %BUILD_ID%

) > "%STAGE%\VERSION.txt"



(

echo SoftwareDeploy - Windows

echo =====================================

echo Server ^(Admin^): install_server.bat -^> http://THIS-PC-IP:61234

echo Client ^(Admin^): install_windows.bat

echo Services: SoftwareDeployServer / SoftwareDeployClient

echo.

echo nssm: download_nssm.bat or place nssm.exe here

echo VC++ : VC_redist.x64.exe when included

) > "%STAGE%\README.txt"



set ZIP_W=%DIST_DIR%\SoftwareDeploy_Windows.zip

if exist "%ZIP_W%" del /f /q "%ZIP_W%"

echo  [..] ZIP: SoftwareDeploy_Windows.zip

%PY_CMD% "%SCRIPT_DIR%scripts\zip_exe_bundle.py" "%STAGE%" "%ZIP_W%"

if %ERRORLEVEL% neq 0 goto zip_fail_bundle

rmdir /s /q "%STAGE%"



REM ---------------------------------------------------------------------------

REM 2) Linux x86_64

REM ---------------------------------------------------------------------------

set STAGE=%DIST_DIR%\_stage_linux_x64

if exist "%STAGE%" rmdir /s /q "%STAGE%"

mkdir "%STAGE%"



copy /y "%SCRIPT_DIR%linux\zip_root\linux_resolve_bundle.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\install_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\install_client_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\start_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\start_client_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\uninstall_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\uninstall_client_linux.sh" "%STAGE%\" >nul



mkdir "%STAGE%\linux" 2>nul

copy /y "%SCRIPT_DIR%server.py" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%client.py" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%server_config.json" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%client_config.json" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%install_cfg_client.py" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\install_server.sh" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\install_linux.sh" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\uninstall_server.sh" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\uninstall_linux.sh" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\start_server.sh" "%STAGE%\linux\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\start_client.sh" "%STAGE%\linux\" >nul

if exist "%SCRIPT_DIR%linux\requirements-linux-server.txt" copy /y "%SCRIPT_DIR%linux\requirements-linux-server.txt" "%STAGE%\linux\" >nul

if exist "%SCRIPT_DIR%linux\README-offline.txt" copy /y "%SCRIPT_DIR%linux\README-offline.txt" "%STAGE%\linux\" >nul

if exist "%SCRIPT_DIR%linux\vendor" xcopy /E /I /Y "%SCRIPT_DIR%linux\vendor" "%STAGE%\linux\vendor\" >nul

if exist "%SCRIPT_DIR%linux\python" xcopy /E /I /Y "%SCRIPT_DIR%linux\python" "%STAGE%\linux\python\" >nul

copy /y "%SCRIPT_DIR%download_linux_offline_deps.py" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%download_linux_embedded_python.py" "%STAGE%\" >nul



(

echo SoftwareDeploy

echo Package: Linux x86_64 - folder linux/

echo Build time: %BUILD_TIME%

echo Build ID: %BUILD_ID%

) > "%STAGE%\VERSION.txt"



(

echo SoftwareDeploy - Linux x86_64

echo =====================================

echo sudo bash install_server_linux.sh / install_client_linux.sh

echo Offline: linux\README-offline.txt

) > "%STAGE%\README.txt"



set ZIP_L=%DIST_DIR%\SoftwareDeploy_Linux_x64.zip

if exist "%ZIP_L%" del /f /q "%ZIP_L%"

echo  [..] ZIP: SoftwareDeploy_Linux_x64.zip

%PY_CMD% "%SCRIPT_DIR%scripts\zip_exe_bundle.py" "%STAGE%" "%ZIP_L%"

if %ERRORLEVEL% neq 0 goto zip_fail_bundle

rmdir /s /q "%STAGE%"



REM ---------------------------------------------------------------------------

REM 3) Linux AArch64

REM ---------------------------------------------------------------------------

set STAGE=%DIST_DIR%\_stage_linux_arm64

if exist "%STAGE%" rmdir /s /q "%STAGE%"

mkdir "%STAGE%"



copy /y "%SCRIPT_DIR%linux\zip_root\linux_resolve_bundle.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\install_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\install_client_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\start_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\start_client_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\uninstall_server_linux.sh" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%linux\zip_root\uninstall_client_linux.sh" "%STAGE%\" >nul



mkdir "%STAGE%\linux_arm" 2>nul

copy /y "%SCRIPT_DIR%server.py" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%client.py" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%server_config.json" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%client_config.json" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%install_cfg_client.py" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\install_server.sh" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\install_linux.sh" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\uninstall_server.sh" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\uninstall_linux.sh" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\start_server.sh" "%STAGE%\linux_arm\" >nul

copy /y "%SCRIPT_DIR%linux\bundle\start_client.sh" "%STAGE%\linux_arm\" >nul

if exist "%SCRIPT_DIR%linux_arm\requirements-linux-server.txt" copy /y "%SCRIPT_DIR%linux_arm\requirements-linux-server.txt" "%STAGE%\linux_arm\" >nul

if exist "%SCRIPT_DIR%linux_arm\README-offline.txt" copy /y "%SCRIPT_DIR%linux_arm\README-offline.txt" "%STAGE%\linux_arm\" >nul

if exist "%SCRIPT_DIR%linux_arm\vendor" xcopy /E /I /Y "%SCRIPT_DIR%linux_arm\vendor" "%STAGE%\linux_arm\vendor\" >nul

if exist "%SCRIPT_DIR%linux_arm\python" xcopy /E /I /Y "%SCRIPT_DIR%linux_arm\python" "%STAGE%\linux_arm\python\" >nul

copy /y "%SCRIPT_DIR%download_linux_offline_deps.py" "%STAGE%\" >nul

copy /y "%SCRIPT_DIR%download_linux_embedded_python.py" "%STAGE%\" >nul



(

echo SoftwareDeploy

echo Package: Linux AArch64 - folder linux_arm/

echo Build time: %BUILD_TIME%

echo Build ID: %BUILD_ID%

) > "%STAGE%\VERSION.txt"



(

echo SoftwareDeploy - Linux ARM64

echo =====================================

echo sudo bash install_server_linux.sh / install_client_linux.sh

echo Offline: linux_arm\README-offline.txt

) > "%STAGE%\README.txt"



set ZIP_A=%DIST_DIR%\SoftwareDeploy_Linux_arm64.zip

if exist "%ZIP_A%" del /f /q "%ZIP_A%"

echo  [..] ZIP: SoftwareDeploy_Linux_arm64.zip

%PY_CMD% "%SCRIPT_DIR%scripts\zip_exe_bundle.py" "%STAGE%" "%ZIP_A%"

if %ERRORLEVEL% neq 0 goto zip_fail_bundle

rmdir /s /q "%STAGE%"



echo.

echo  ============================================================

echo   OK — 3 packages ^(VERSION.txt in each^):

echo     %ZIP_W%

echo     %ZIP_L%

echo     %ZIP_A%

echo   Build: %BUILD_TIME%  ID: %BUILD_ID%

echo  ============================================================

echo.

exit /b 0

:zip_fail_bundle
echo  [ERROR] zip_exe_bundle.py failed
if exist "%STAGE%" rmdir /s /q "%STAGE%" 2>nul
exit /b 1
