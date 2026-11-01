@echo off
chcp 65001 >nul
setlocal EnableExtensions
pushd "%~dp0"
title Pocket 3 PTZ Camera Nodes

echo ============================================================
echo Pocket 3 PTZ Camera Nodes
echo ============================================================
echo [1] Standard mode - Direct UVC preview and OBS output
echo [2] NDI-only mode - lowest USB load
echo [Q] Exit
echo.
choice /c 12Q /n /m "Select mode: "
if errorlevel 3 goto :end
if errorlevel 2 (set "PROFILE=off") else (set "PROFILE=stable")

set "LAUNCHER=%~dp0EXE\Pocket3-Multi-Launcher.exe"
if exist "%LAUNCHER%" goto :run_exe

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3.12"&goto :run_python
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"&goto :run_python
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=python"&goto :run_python

echo ERROR: Neither EXE nor Python 3 was found.
echo Run build-windows-exe.bat or install Python 3.12.
pause
goto :end

:run_exe
"%LAUNCHER%" --max-cameras 5 --start-port 8723 --preview-profile %PROFILE%
goto :end

:run_python
%PYCMD% multi-ptz-launcher.py --max-cameras 5 --start-port 8723 --preview-profile %PROFILE%
goto :end

:end
popd
endlocal
