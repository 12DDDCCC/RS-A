@echo off
rem ============================================================
rem RS-Agent installer (B5): run once on a new machine or after a
rem dsh reinstall.
rem   1. junction the plugin into dsh profile node_modules
rem      (require.resolve only resolves node_modules package names)
rem   2. install the fixed remote-sensing profile
rem Prereq: dsh installed globally (npm i -g @deepseek-ai/dsh)
rem ============================================================
setlocal
set "PLUG=%~dp0..\remote-sensing-tools"
set "NM=%USERPROFILE%\.dsh\profiles\node_modules"
set "PROF=%USERPROFILE%\.dsh\profiles\remote-sensing"

if not exist "%PLUG%\package.json" (
    echo [ERROR] plugin dir missing: %PLUG%
    exit /b 1
)
if not exist "%NM%\@rs" mkdir "%NM%\@rs"
if exist "%NM%\@rs\remote-sensing-tools" rmdir "%NM%\@rs\remote-sensing-tools"
mklink /J "%NM%\@rs\remote-sensing-tools" "%PLUG%"
if errorlevel 1 (
    echo [ERROR] junction failed ^(needs NTFS, or run as admin^)
    exit /b 1
)
if not exist "%PROF%" mkdir "%PROF%"
copy /y "%~dp0..\dsh\remote-sensing.profile.yml" "%PROF%\cordis.yml" >nul
echo.
echo install done. launch either way:
echo   A^) RS-agent\scripts\start-dsh.cmd
echo   B^) dsh --profile remote-sensing web
endlocal
