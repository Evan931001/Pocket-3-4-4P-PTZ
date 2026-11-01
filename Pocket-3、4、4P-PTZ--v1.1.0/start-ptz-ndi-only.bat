@echo off
setlocal EnableExtensions
pushd "%~dp0"
title Pocket 3 PTZ - NDI Only Stable Mode

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_py312
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_py3
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto :use_python
echo ERROR: Python 3 was not found.
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
echo Starting up to five PTZ nodes in NDI-only mode.
echo Direct UVC LAN preview is disabled to minimize USB bandwidth.
%PYCMD% multi-ptz-launcher.py --max-cameras 5 --start-port 8723 --preview-profile off
pause
popd
exit /b 0
