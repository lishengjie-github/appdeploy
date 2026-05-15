@echo off
REM Called from package_exe_zip.bat so FOR /f is not parsed together with long parenthesis blocks in that file.
for /f "tokens=*" %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd HH:mm:ss\")"') do set "BUILD_TIME=%%I"
for /f "tokens=*" %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd_HHmm\")"') do set "BUILD_ID=%%I"
