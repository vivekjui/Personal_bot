@echo off
setlocal

set APP_NAME=APMD_Bot
set INSTALL_DIR=%USERPROFILE%\%APP_NAME%
set BUILD_DIR=%~dp0dist\%APP_NAME%

echo ============================================================
echo   %APP_NAME% - LOCAL INSTALLER
echo ============================================================
echo.

if not exist "%BUILD_DIR%" (
    echo [ERROR] Build directory not found! 
    echo Please run 'python scripts\build_exe.py' first to generate the app.
    pause
    exit /b 1
)

:: --- PRE-REQUISITE CHECK: CHROME ---
echo [0/3] Checking for Google Chrome...
reg query "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1
if %errorlevel% neq 0 (
    reg query "HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo [WARNING] Google Chrome was not detected on this system.
        echo Google Chrome is required for GeM Bid Downloader features.
        echo Please install Chrome from: https://www.google.com/chrome/
        echo.
        set /p CHROME_CONFIRM="Do you want to continue anyway? (y/n): "
        if /i "%CHROME_CONFIRM%" neq "y" exit /b 1
    )
)
echo Chrome detected or skipped. proceeding...
echo.

:: 1. Copy files
echo [1/3] Copying application files to %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
xcopy /E /I /Y "%BUILD_DIR%\*" "%INSTALL_DIR%\" > nul

:: 2. Create Desktop Shortcut
echo [2/3] Creating Desktop shortcut...
set SHORTCUT_PATH=%USERPROFILE%\Desktop\%APP_NAME%.lnk
set TARGET_PATH=%INSTALL_DIR%\%APP_NAME%.exe
set ICON_PATH=%TARGET_PATH%

powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT_PATH%');$s.TargetPath='%TARGET_PATH%';$s.WorkingDirectory='%INSTALL_DIR%';$s.IconLocation='%ICON_PATH%';$s.Save()"

:: 3. Create Start Menu Shortcut
echo [3/3] Adding to Start Menu...
set START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%APP_NAME%
if not exist "%START_MENU_DIR%" mkdir "%START_MENU_DIR%"
set START_SHORTCUT=%START_MENU_DIR%\%APP_NAME%.lnk

powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%START_SHORTCUT%');$s.TargetPath='%TARGET_PATH%';$s.WorkingDirectory='%INSTALL_DIR%';$s.IconLocation='%ICON_PATH%';$s.Save()"

echo.
echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo You can now run the app using the shortcut on your Desktop.
echo Settings and History are stored safely in: %%APPDATA%%\%APP_NAME%
echo.
pause
