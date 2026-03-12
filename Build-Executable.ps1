param(
    [string]$PythonExe = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistRoot = Join-Path $ProjectRoot "VivekBot_Release"
$AppOutput = Join-Path $DistRoot "app"

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe"
}

Write-Host "Installing/updating PyInstaller..."
& $PythonExe -m pip install --upgrade pyinstaller

Write-Host "Cleaning previous build artifacts..."
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "dist") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $DistRoot -ErrorAction SilentlyContinue

Write-Host "Collecting runtime assets for packaging..."
$runtimeDirs = @("templates_web", "static", "knowledge_base")
$runtimeFileExtensions = @(".json", ".db")
$pyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--windowed",
  "--name", "Vivek Bot",
  "--collect-submodules", "webview",
  "--collect-submodules", "chromadb",
  "--hidden-import", "pydantic.v1.fields"
)

foreach ($dirName in $runtimeDirs) {
    $dirPath = Join-Path $ProjectRoot $dirName
    if (Test-Path $dirPath) {
        $pyInstallerArgs += @("--add-data", "$dirPath;$dirName")
    }
}

$runtimeFiles = Get-ChildItem -Path $ProjectRoot -File | Where-Object {
    $runtimeFileExtensions -contains $_.Extension.ToLowerInvariant()
}

foreach ($file in $runtimeFiles) {
    $pyInstallerArgs += @("--add-data", "$($file.FullName);.")
}

$pyInstallerArgs += "main.py"

Write-Host "Building Vivek Bot executable..."
& $PythonExe @pyInstallerArgs

Write-Host "Preparing installable release folder..."
New-Item -ItemType Directory -Force -Path $AppOutput | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot "dist\Vivek Bot\*") -Destination $AppOutput -Recurse -Force
Copy-Item -Path (Join-Path $ProjectRoot "SmartBot_Installer.ps1") -Destination $DistRoot -Force
Copy-Item -Path (Join-Path $ProjectRoot "Setup.bat") -Destination $DistRoot -Force

$manifestPath = Join-Path $DistRoot "PACKAGE_MANIFEST.txt"
$manifestLines = @(
    "Vivek Bot package manifest",
    "",
    "Bundled directories:"
)
$manifestLines += $runtimeDirs | ForEach-Object { "- $_" }
$manifestLines += ""
$manifestLines += "Bundled root files:"
$manifestLines += $runtimeFiles | ForEach-Object { "- $($_.Name)" }
Set-Content -Path $manifestPath -Value $manifestLines -Encoding UTF8

Write-Host ""
Write-Host "Release prepared at: $DistRoot"
Write-Host "Distribute this folder or upload it as a GitHub Release asset."
