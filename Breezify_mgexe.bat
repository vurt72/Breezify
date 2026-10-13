@echo off
REM MGE XE version: distant trees get leaves with real back faces (MGE XE ignores two-sided leaves at a distance).
REM Drag tree .nif files or folders onto this file.
"%~dp0python\python.exe" "%~dp0breezify.py" --mge %*
echo.
pause
