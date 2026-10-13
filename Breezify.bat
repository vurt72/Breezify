@echo off
REM Drag tree .nif files or folders onto this file.
"%~dp0python\python.exe" "%~dp0breezify.py" %*
echo.
pause
