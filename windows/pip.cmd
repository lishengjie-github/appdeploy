@echo off
REM pip via bundled python (-m pip). Place next to python\ folder.
set "HERE=%~dp0"
"%HERE%python\python.exe" -m pip %*
