@echo off
setlocal EnableExtensions
pushd "%~dp0"

set "LAUNCHER=%~dp0EXE\Pocket3-Multi-Launcher.exe"
if not exist "%LAUNCHER%" (
  echo ERROR: EXE\Pocket3-Multi-Launcher.exe was not found.
  echo Run build-windows-exe.bat first.
  pause
  popd
  exit /b 1
)

start "Pocket 3 Multi Camera Launcher" "%LAUNCHER%" --max-cameras 5 --start-port 8723
popd
exit /b 0
