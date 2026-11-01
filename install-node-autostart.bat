@echo off
chcp 65001 >nul
setlocal
set "NODE=%~dp0EXE\Pocket3-PTZ-Node.exe"
set "LAUNCHER=%~dp0EXE\Pocket3-Multi-Launcher.exe"
if not exist "%NODE%" (
  echo 找不到 %NODE%
  echo 請先執行 build-windows-exe.bat。
  pause
  exit /b 1
)
if not exist "%LAUNCHER%" (
  echo 找不到 %LAUNCHER%
  echo 請先執行 build-windows-exe.bat。
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Startup'),'Pocket3 PTZ Node.lnk'));$s.TargetPath='%LAUNCHER%';$s.Arguments='--max-cameras 5 --start-port 8723 --no-pause';$s.WorkingDirectory='%~dp0EXE';$s.WindowStyle=7;$s.Save()"
start "" "%LAUNCHER%" --max-cameras 5 --start-port 8723 --no-pause
echo 已設定登入 Windows 後自動偵測最多五台 DJI，使用 8723 至 8727。
pause
