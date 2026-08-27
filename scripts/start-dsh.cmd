@echo off
rem ============================================================
rem RS-Agent dsh shell sidecar (B5): Web UI :3080 + native tools.
rem Injects MINIMAX_API_KEY (M3 route) and REMOTE_SENSING_TOKEN
rem from project files at launch; secrets never persist here.
rem ============================================================
setlocal
set "ROOT=%~dp0..\.."
for /f "usebackq delims=" %%i in (`""%ROOT%"\.venv\Scripts\python.exe" "%~dp0decrypt_token.py""`) do set "REMOTE_SENSING_TOKEN=%%i"
if not defined REMOTE_SENSING_TOKEN (
    echo [ERROR] token decrypt failed - bind rs-a-user creds first
    exit /b 1
)
for /f "usebackq tokens=2 delims==" %%k in (`findstr /b "MINIMAX_API_KEY=" ""%ROOT%"\.env"`) do set "MINIMAX_API_KEY=%%k"
if not defined MINIMAX_API_KEY (
    echo [ERROR] MINIMAX_API_KEY missing in .env
    exit /b 1
)
endlocal & set "REMOTE_SENSING_TOKEN=%REMOTE_SENSING_TOKEN%" & set "MINIMAX_API_KEY=%MINIMAX_API_KEY%"
rem --patch/--profile are top-level flags; --no-open goes to the web app
call dsh --patch "%~dp0..\dsh\cordis.patch.yml" --profile web --no-open
