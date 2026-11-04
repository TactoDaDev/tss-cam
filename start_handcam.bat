@echo off
rem TSS Handcam: starts the AcHandCam receiver and our bridge, each in its own window (the desktop shortcut runs this).
rem Close both windows when you're done filming.
set "RECEIVER=D:\phone cam\AcHandCam-Receiver"
if not exist "%RECEIVER%\run_receiver.bat" (
  echo AcHandCam receiver not found in "%RECEIVER%". Edit RECEIVER at the top of %~f0.
  pause
  exit /b 1
)
start "AcHandCam receiver" /d "%RECEIVER%" cmd /c run_receiver.bat
start "TSS Handcam bridge" /d "%~dp0bridge" cmd /c run_bridge.bat
