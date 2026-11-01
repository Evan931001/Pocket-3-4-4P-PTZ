@echo off
chcp 65001 >nul
setlocal
set "APP=%~dp0EXE\Pocket3-Director.exe"
if not exist "%APP%" (
  echo 找不到 %APP%
  echo 請先執行 build-windows-exe.bat。
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Startup'),'Pocket3 Director.lnk'));$s.TargetPath='%APP%';$s.WorkingDirectory='%~dp0EXE';$s.Save()"
start "" "%APP%"
echo 已設定登入 Windows 後自動開啟導播控制台，並已立即啟動。
pause
