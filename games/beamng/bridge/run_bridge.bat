@echo off
rem TSS Handcam bridge: connects the AcHandCam receiver to BeamNG. Run it next to AcHandCam's run_receiver.bat.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% handcam_bridge.py
if errorlevel 1 pause
