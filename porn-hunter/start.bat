@echo off
rem One-step launcher for Windows: sets everything up (first run only), then starts the web app
rem so you can open it from your phone. Just double-click this file.
setlocal
cd /d "%~dp0"
if "%PORN_HUNTER_PORT%"=="" (set "PORT=8765") else (set "PORT=%PORN_HUNTER_PORT%")

rem --- 1. Python 3.11+ ---------------------------------------------------------------------
if exist ".venv\Scripts\python.exe" goto have_venv
set "PY="
for %%V in (3.12 3.13 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo ERROR: Python 3.11 or newer was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/ and TICK the box
  echo "Add python.exe to PATH" in the installer. Then double-click this file again.
  echo.
  pause
  exit /b 1
)
echo.
echo == Creating a private Python environment (first run only)
%PY% -m venv .venv
if errorlevel 1 goto failed
:have_venv
set "VPY=.venv\Scripts\python.exe"

rem --- 2. Install packages (first run, or when the requirements changed) ------------------------
for /f "delims=" %%R in ('"%VPY%" -m porn_hunter.platform_requirements') do set "REQ=%%R"
echo.
echo == Package list: %REQ%
"%VPY%" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "%REQ%" > ".venv\want.sha"
set /p WANT=<".venv\want.sha"
set "HAVE="
if exist ".venv\.requirements.sha" set /p HAVE=<".venv\.requirements.sha"
if "%WANT%"=="%HAVE%" goto have_packages
echo.
echo == Installing packages. This takes a few minutes the first time (it downloads PyTorch).
"%VPY%" -m pip install --upgrade pip >nul 2>&1
"%VPY%" -m pip install -r "%REQ%"
if errorlevel 1 (
  echo.
  echo ---------- COPY EVERYTHING FROM THIS LINE DOWN AND SEND IT TO CLAUDE ----------
  "%VPY%" -m porn_hunter.platform_requirements --describe
  "%VPY%" -m pip --version
  echo package list: %REQ%
  echo ---------- END ----------
  goto failed
)
> ".venv\.requirements.sha" echo %WANT%
:have_packages

rem --- 3. A password for the web app (created once, kept in .web_token) ----------------------
if not exist ".web_token" "%VPY%" -c "import secrets; print(secrets.token_urlsafe(12))" > ".web_token"
set /p PORN_HUNTER_WEB_TOKEN=<".web_token"

rem --- 4. Start -------------------------------------------------------------------------------
echo.
echo ==========================================================================
echo   porn-hunter is starting.
echo.
echo   On your phone (same Wi-Fi as this computer) open the address printed below
echo   that starts with  http://192.168...  or  http://10...  and log in with:
echo.
echo       Password:  %PORN_HUNTER_WEB_TOKEN%
echo.
echo   If Windows asks whether to allow Python through the firewall, click
echo   "Allow" (private networks). The first start downloads the search model
echo   (about 600 MB), so give it a few minutes. Leave this window open while
echo   you use the app. Close it (or press Ctrl+C) to stop.
echo ==========================================================================
echo.
"%VPY%" -m porn_hunter web --host 0.0.0.0 --port %PORT%
goto end

:failed
echo.
echo ERROR: setup failed. Check your internet connection, then double-click this file again.
:end
echo.
pause
