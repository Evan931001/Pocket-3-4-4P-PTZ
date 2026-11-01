@echo off
chcp 65001 >nul
title Remove Pocket 3 PTZ Firewall Rule

net session >nul 2>&1
if not %errorlevel%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; Get-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723-8724' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; Get-NetFirewallRule -DisplayName 'Pocket 3 PTZ LAN TCP 8723-8727' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"

echo Firewall rule removed.
pause
