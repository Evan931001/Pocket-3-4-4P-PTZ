@echo off
setlocal EnableExtensions
pushd "%~dp0"
title Pocket 3 PTZ Multi Camera Launcher

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_py312
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_py3
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_python

echo ERROR: Python 3 was not found.
echo Install Python 3.12, then run this file again.
pause
popd
exit /b 1

:use_py312
set "PYCMD=py -3.12"
goto :launch

:use_py3
set "PYCMD=py -3"
goto :launch

:use_python
set "PYCMD=python"

:launch
echo ============================================================
echo Detecting and starting up to five independent DJI PTZ nodes...
echo PORT RANGE: 8723 - 8727
echo ============================================================
echo.
echo Camera nodes run hidden; status is shown in this master console.
echo OBS Virtual Camera and non-DJI webcams will be skipped.
echo.

%PYCMD% multi-ptz-launcher.py --max-cameras 5 --start-port 8723
echo.
echo Keep this MASTER CONSOLE open during the broadcast.
pause
popd
exit /b 0
