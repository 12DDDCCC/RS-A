@echo off
rem ============================================================
rem RS-Agent backend sidecar (B5): FastAPI :8000.
rem Uses the project .venv (self-contained). Restart this after
rem changing src/ (uvicorn has no --reload here).
rem ============================================================
setlocal
set "ROOT=%~dp0..\.."
pushd "%ROOT%"
".venv\Scripts\python.exe" -m uvicorn src.main:app --host 127.0.0.1 --port 8000
popd
endlocal
