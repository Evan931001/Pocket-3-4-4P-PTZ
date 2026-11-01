@echo off
chcp 65001 >nul
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Pocket3 Director.lnk" 2>nul
taskkill /IM Pocket3-Director.exe >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*Pocket3Director*EdgeProfile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo 已取消導播控制台自動啟動，並停止 Director 與專用控制視窗。
pause
