@echo off
rem The fignimble command line (render, check, export figures) on Windows, as bin/fignimble is on macOS and Linux.
rem Uses %FIGNIMBLE_PYTHON%, else the Python environment install_fignimble.ps1 made, else this checkout's .venv,
rem else python on the PATH.
setlocal
set "HERE=%~dp0"
set "ENGINE=%HERE%..\engine"
set "PY="
if defined FIGNIMBLE_PYTHON if exist "%FIGNIMBLE_PYTHON%" set "PY=%FIGNIMBLE_PYTHON%"
if not defined PY if exist "%LOCALAPPDATA%\Fignimble\python\Scripts\python.exe" set "PY=%LOCALAPPDATA%\Fignimble\python\Scripts\python.exe"
if not defined PY if exist "%HERE%..\.venv\Scripts\python.exe" set "PY=%HERE%..\.venv\Scripts\python.exe"
if not defined PY set "PY=python"
if defined PYTHONPATH (set "PYTHONPATH=%ENGINE%;%PYTHONPATH%") else (set "PYTHONPATH=%ENGINE%")
set "PYTHONUTF8=1"
"%PY%" -m fignimble.cli %*
exit /b %ERRORLEVEL%
