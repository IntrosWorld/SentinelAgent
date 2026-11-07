@echo off
setlocal
title SentinelAgent - Interview Demo
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-sentinel.ps1"
if errorlevel 1 (
  echo.
  echo Sentinel could not start. Read the message above.
  pause
  exit /b 1
)
endlocal
