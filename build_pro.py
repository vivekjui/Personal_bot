import os
import sys
import shutil
import subprocess
from pathlib import Path

# --- Configuration ---
def get_version():
    try:
        return Path("VERSION").read_text().strip()
    except:
        return "1.0.0"

VERSION = get_version()
APP_NAME = f"vivek Bot Pro v{VERSION}"
MAIN_SCRIPT = "main.py"
ICON_PATH = "static/favicon.ico" # Adjust if you have an .ico file

INCLUDE_DIRS = [
    "templates_web",
    "static",
    # "modules", # REMOVED: So Nuitka compiles .py files into binary (hidden) instead of copying source
]

INCLUDE_FILES = [
    "noting_prompts.json",
    "procurement_dictionary.json",
    "procurement_stages.json",
    "standard_library.json",
    "VERSION",
    "config.json.example",
    "cases.db", # Include consolidated database file schema/data
]

def run_command(cmd):
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        sys.exit(1)

def main():
    if not Path(MAIN_SCRIPT).exists():
        print(f"Error: {MAIN_SCRIPT} not found.")
        sys.exit(1)

    print(f"Starting Pro Build for {APP_NAME}...")

    # --- Virtual Environment Detection ---
    # Preferred python is the one in .venv
    venv_python = Path(".venv") / "Scripts" / "python.exe"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    print(f"Using Python: {python_exe}")

    # Ensured all project dependencies are already installed in the target environment
    # print("Ensuring all requirements are installed...")
    # subprocess.run([python_exe, "-m", "pip", "install", "-r", "requirements.txt", "nuitka", "zstandard"], check=False)

    # Base Nuitka command
    cmd = [
        python_exe, "-m", "nuitka",
        "--standalone",            # Switched from --onefile for better stability
        "--assume-yes-for-downloads",
        "--output-filename=" + APP_NAME,
        "--windows-console-mode=disable", # Hide console for Pro version
        "--enable-plugin=pywebview",
        "--enable-plugin=tk-inter",
        "--low-memory",            # Crucial for preventing MemoryError on build
        "--jobs=1",               # Limit to 1 job to save RAM during compilation
        "--show-progress",        # Tracking progress in the log
        "--show-scons",           # Tracking compilation status
    ]

    # Include packages frequently used
    packages = ["flask", "waitress", "chromadb", "pydantic", "pydantic.v1", "requests", "DrissionPage", "PIL"]
    for pkg in packages:
        cmd.extend(["--include-package=" + pkg])

    # Optimization: Exclude heavy, unnecessary sub-packages to save build RAM
    excluded = ["matplotlib", "notebook", "IPython", "ipykernel"]
    for ex in excluded:
        cmd.extend(["--nofollow-import-to=" + ex])

    # Include directories
    for d in INCLUDE_DIRS:
        if Path(d).exists():
            cmd.extend(["--include-data-dir=" + d + "=" + d])

    # Include individual files
    for f in INCLUDE_FILES:
        if Path(f).exists():
            cmd.extend(["--include-data-file=" + f + "=" + f])

    # Add icon if exists
    if Path(ICON_PATH).exists():
        cmd.append(f"--windows-icon-from-ico={ICON_PATH}")

    # Final script
    cmd.append(MAIN_SCRIPT)

    run_command(cmd)

    print(f"\nBuild complete! Check the 'dist' folder or current directory for {APP_NAME}.exe")

if __name__ == "__main__":
    main()
