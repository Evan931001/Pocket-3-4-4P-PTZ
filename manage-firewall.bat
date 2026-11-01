@echo off
chcp 65001 >nul
setlocal EnableExtensions
title Pocket 3 PTZ Firewall Manager

net session >nul 2>&1
if not %errorlevel%==0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo ============================================================
echo Pocket 3 PTZ Firewall Manager
echo ============================================================
echo [1] Allow LAN TCP 8723-8727
echo [2] Remove PTZ firewall rules
echo [Q] Exit
choice /c 12Q /n /m "Select action: "
if errorlevel 3 goto :end
if errorlevel 2 goto :remove

:install
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$names=@('Pocket 3 PTZ LAN TCP 8723','Pocket 3 PTZ LAN TCP 8723-8724','Pocket 3 PTZ LAN TCP 8723-8727');" ^
  "$names | ForEach-Object { Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue | Remove-NetFirewallRule };" ^
  "New-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723-8727' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8723-8727 -RemoteAddress LocalSubnet -Profile Any | Out-Null"
if errorlevel 1 (echo FAILED.) else (echo Firewall rule installed for LocalSubnet only.)
pause
goto :end

:remove
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$names=@('Pocket 3 PTZ LAN TCP 8723','Pocket 3 PTZ LAN TCP 8723-8724','Pocket 3 PTZ LAN TCP 8723-8727');" ^
  "$names | ForEach-Object { Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue | Remove-NetFirewallRule }"
echo Firewall rules removed.
pause

:end
endlocal
