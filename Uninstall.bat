@echo off
setlocal
if "%~1" neq "" goto TEMP_UNINSTALL

set APP_NAME=Smart bot by Vivek Jui
set SHORTCUT_NAME=Smart bot by Vivek Jui
set "INSTALL_DIR=%~dp0"
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

echo ============================================================
echo   %APP_NAME% - UNINSTALLER
echo ============================================================
echo.
echo This will remove the application and its shortcuts.
set /p CONFIRM="Are you sure you want to uninstall? (y/n): "
if /i "%CONFIRM%" neq "y" exit /b 1

echo [1/3] Removing shortcuts...
if exist "%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk" (
    del "%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk"
)

set START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%SHORTCUT_NAME%
if exist "%%START_MENU_DIR%" (
    rd /s /q "%START_MENU_DIR%"
)

echo [2/3] Cleaning up application files...
echo (The uninstaller will close once finished)
timeout /t 2 > nul

:: We can't delete the directory we are currently in if the script is running from there.
:: But we can schedule a deletion of everything except the uninstaller, or just delete what we can.
:: Best practice for a batch uninstaller is to copy itself to temp and run from there.

copy "%~f0" "%TEMP%\smart_bot_uninstall.bat" > nul
start "" cmd /c "%TEMP%\smart_bot_uninstall.bat" "%INSTALL_DIR%"
exit /b

:TEMP_UNINSTALL
set "TARGET=%~1"
timeout /t 2 > nul
if exist "%TARGET%" (
    echo Deleting %TARGET%...
    rd /s /q "%TARGET%"
)
echo.
echo ============================================================
echo   UNINSTALL COMPLETE
echo ============================================================
echo You can now close this window.
pause
exit
