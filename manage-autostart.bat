@echo off
chcp 65001 >nul
setlocal EnableExtensions
pushd "%~dp0"
title Pocket 3 PTZ Autostart Manager
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "NODE_LINK=%STARTUP%\Pocket3 PTZ Node.lnk"
set "DIRECTOR_LINK=%STARTUP%\Pocket3 Director.lnk"
set "LAUNCHER=%~dp0EXE\Pocket3-Multi-Launcher.exe"
set "DIRECTOR=%~dp0EXE\Pocket3-Director.exe"

:menu
echo ============================================================
echo Pocket 3 PTZ Autostart Manager
echo ============================================================
if exist "%NODE_LINK%" (echo Node:     ENABLED) else (echo Node:     disabled)
if exist "%DIRECTOR_LINK%" (echo Director: ENABLED) else (echo Director: disabled)
echo.
echo [1] Enable camera-node autostart
echo [2] Disable camera-node autostart and stop nodes
echo [3] Enable Director autostart
echo [4] Disable Director autostart and stop Director
echo [Q] Exit
choice /c 1234Q /n /m "Select action: "
if errorlevel 5 goto :end
if errorlevel 4 goto :remove_director
if errorlevel 3 goto :install_director
if errorlevel 2 goto :remove_node

:install_node
if not exist "%LAUNCHER%" echo Build EXEs first.&pause&goto :menu
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%NODE_LINK%');$s.TargetPath='%LAUNCHER%';$s.Arguments='--max-cameras 5 --start-port 8723 --no-pause';$s.WorkingDirectory='%~dp0EXE';$s.WindowStyle=7;$s.Save()"
echo Node autostart enabled.
pause
goto :menu

:remove_node
del /q "%NODE_LINK%" 2>nul
taskkill /IM Pocket3-Multi-Launcher.exe /T /F >nul 2>nul
taskkill /IM Pocket3-PTZ-Node.exe /F >nul 2>nul
echo Node autostart disabled and nodes stopped.
pause
goto :menu

:install_director
if not exist "%DIRECTOR%" echo Build EXEs first.&pause&goto :menu
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%DIRECTOR_LINK%');$s.TargetPath='%DIRECTOR%';$s.WorkingDirectory='%~dp0EXE';$s.Save()"
echo Director autostart enabled.
pause
goto :menu

:remove_director
del /q "%DIRECTOR_LINK%" 2>nul
taskkill /IM Pocket3-Director.exe /F >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*Pocket3Director*EdgeProfile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Director autostart disabled and Director stopped.
pause
goto :menu

:end
popd
endlocal
