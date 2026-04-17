@echo off
TITLE Smart bot by Vivek Jui
cd /d "%~dp0"
echo ==========================================
echo    Smart bot by Vivek Jui — eOffice AI
echo ==========================================
echo.

SET "PYTHON_EXE=.venv\Scripts\python.exe"
SET ANONYMIZED_TELEMETRY=False

IF NOT EXIST "%PYTHON_EXE%" (
    echo [NOTICE] Virtual environment not found. Attempting to repair/create...
    goto REPAIR
)

:: FAST DEPENDENCY CHECK
echo [NOTICE] Verifying dependencies...
"%PYTHON_EXE%" -c "import flask, webview, waitress, google.genai" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo [OK] All systems go.
    goto LAUNCH
) else (
    echo [NOTICE] Missing or broken dependencies detected.
)

:REPAIR
echo [NOTICE] Setting up environment...
py -3.12 -m venv .venv
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to create virtual environment with Python 3.12.
    pause
    exit /b 1
)
.venv\Scripts\pip install --upgrade pip --prefer-binary --quiet --no-compile
.venv\Scripts\pip install -r requirements.txt --prefer-binary --quiet --no-compile

:LAUNCH
echo [NOTICE] Starting Smart bot...
"%PYTHON_EXE%" main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] The bot crashed or failed to start.
    echo Please check the logs in %%APPDATA%%\APMD_Bot\logs
    pause
)
