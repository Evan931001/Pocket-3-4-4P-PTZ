@echo off
chcp 65001 >nul
title Allow Pocket 3 PTZ on LAN

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator permission...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$name='Pocket 3 PTZ LAN TCP 8723-8727';" ^
  "Get-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723' -ErrorAction SilentlyContinue | Remove-NetFirewallRule;" ^
  "Get-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723-8724' -ErrorAction SilentlyContinue | Remove-NetFirewallRule;" ^
  "Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule;" ^
  "New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8723-8727 -RemoteAddress LocalSubnet -Profile Any | Out-Null"

if %errorlevel%==0 (
    echo.
    echo Firewall rule installed successfully.
    echo Only devices on the local subnet may connect to TCP 8723 through 8727.
) else (
    echo.
    echo Failed to install the firewall rule.
)
pause
