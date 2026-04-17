@echo off
echo ======================================================
echo   Noting Bot - Nuitka Packaging Script
echo ======================================================

set ENTRY_POINT=main.py
set OUTPUT_NAME=NotingBot

REM Ensure we are in the right directory
cd /d "%~dp0.."

REM Activate venv if exists
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

echo [1/2] Compiling with Nuitka...
REM Note: Using --include-data-dir for Flask templates and static files.
REM Proxy (10.6.0.9:3128) is handled in modules/utils.py apply_proxy_settings().
python -m nuitka --standalone ^
    --onefile ^
    --assume-yes-for-downloads ^
    --enable-plugin=tk-inter ^
    --include-package=flask ^
    --include-package=waitress ^
    --include-package=chromadb ^
    --include-package=pydantic.v1 ^
    --include-data-dir=templates_web=templates_web ^
    --include-data-dir=static=static ^
    --include-data-file=noting_prompts.json=noting_prompts.json ^
    --include-data-file=procurement_dictionary.json=procurement_dictionary.json ^
    --include-data-file=procurement_stages.json=procurement_stages.json ^
    --include-data-file=standard_library.json=standard_library.json ^
    --windows-console-mode=disable ^
    --output-dir=build ^
    --output-filename=%OUTPUT_NAME% ^
    %ENTRY_POINT%

echo [2/2] Build Complete. Check the 'build' directory.
pause
