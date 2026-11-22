@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if not exist "runtime\win-x64\python\python.exe" goto missing_runtime
set "STARHOPE_PORTABLE_RUNTIME=1"
"%~dp0runtime\win-x64\python\python.exe" -B -m scripts.portable_launcher %*
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if "%STARHOPE_NO_PAUSE%"=="1" exit /b %LAUNCHER_EXIT%
pause
exit /b %LAUNCHER_EXIT%

:missing_runtime
echo [E_RUNTIME] runtime\win-x64\python\python.exe is missing.
echo Extract the complete StarHope portable package before running this file.
exit /b 1
