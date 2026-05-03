@echo off
REM Runs bundled offline Python without relying on PATH. Place next to python\ folder.
set "HERE=%~dp0"
"%HERE%python\python.exe" %*
