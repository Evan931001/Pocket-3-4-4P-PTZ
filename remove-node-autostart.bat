@echo off
chcp 65001 >nul
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Pocket3 PTZ Node.lnk" 2>nul
taskkill /IM Pocket3-PTZ-Node.exe >nul 2>nul
echo 已取消 PTZ Node 自動啟動，並停止背景程式。
pause
