@echo off
rem ============================================================
rem RS-Agent release packer (v2): full self-contained zip.
rem Flow: sync_release.py (refresh backend copies + redact +
rem sensitive-scan, fails loud) -> robocopy stage (exclude
rem node_modules/__pycache__/cache) -> Compress-Archive.
rem NOTE: no caret line-continuations in this file — LF-only
rem line endings break them (obsidian 34). Keep commands 1-line.
rem All paths derive from this script's own location (avoids
rem non-ASCII literals that GBK cmd would mangle).
rem ============================================================
setlocal
rem %~dp0 = ...\RS-agent\scripts\ ; project root is two levels up
set "ROOT=%~dp0..\.."
pushd "%ROOT%"

rem ---- 1) 同步+脱敏+扫描 (失败即中止) ----
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" "RS-agent\scripts\sync_release.py"
if errorlevel 1 (
    echo [ERROR] sync/sensitive-scan failed - pack aborted
    popd
    exit /b 1
)

rem ---- 2) 暂存目录 (robocopy 排除运行时/依赖目录) ----
if not exist cache\releases mkdir cache\releases
set "STAGE=cache\releases\stage"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"
robocopy "RS-agent" "%STAGE%\RS-agent" /E /XD node_modules __pycache__ .pytest_cache .git sessions storages /XF *.pyc /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo [ERROR] stage copy failed
    popd
    exit /b 1
)
rem ---- 2.5) stage 级二次脱敏 (zip 干净; 活机文件不受影响) ----
".venv\Scripts\python.exe" "RS-agent\scripts\sync_release.py" --redact-dir "%STAGE%\RS-agent"

rem ---- 3) 压缩 ----
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set DT=%%d
set "OUT=cache\releases\rs-agent-%DT%.zip"
if exist "%OUT%" del "%OUT%"
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%\RS-agent' -DestinationPath '%OUT%' -Force"
if errorlevel 1 (
    echo [ERROR] pack failed
    popd
    exit /b 1
)
rmdir /s /q "%STAGE%"
echo release: %ROOT%\%OUT%
popd
endlocal
