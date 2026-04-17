import subprocess
import sys
import os
from pathlib import Path

def build():
    print("Starting APMD Bot Standalone Build Process...")
    
    # Ensure we are in the root directory
    root = Path(__file__).parent.parent
    os.chdir(root)

    # Define the PyInstaller command
    # We use --onedir for an unzipped folder (better for installed apps)
    # We use --clean to clear cache
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir",
        "--clean",
        "--name", "APMD_Bot",
        # Use --windowed to hide the console in production
        "--windowed",
        # Add folders (format: Source;Destination)
        "--add-data", "static;static",
        "--add-data", "templates_web;templates_web",
        "--add-data", "config.json;.",
        "--add-data", "noting_prompts.json;.",
        "--add-data", "procurement_dictionary.json;.",
        "--add-data", "procurement_stages.json;.",
        "--add-data", "standard_library.json;.",
        # Hidden imports for RAG and AI
        "--hidden-import", "flask",
        "--hidden-import", "flask_cors",
        "--hidden-import", "chromadb",
        "--hidden-import", "sentence_transformers",
        "--hidden-import", "google.generativeai",
        "--hidden-import", "pydantic",
        "--hidden-import", "pydantic.v1",
        "--hidden-import", "pydantic.v1.fields",
        "--hidden-import", "pdfplumber",
        "--hidden-import", "fitz",
        "--hidden-import", "docx",
        "--hidden-import", "openpyxl",
        "--hidden-import", "webview",
        "--hidden-import", "clr",
        "--hidden-import", "pkg_resources.py2_warn",
        # Main entry point
        "main.py"
    ]

    print(f"Executing: {' '.join(cmd)}")
    
    try:
        subprocess.run(cmd, check=True)
        print("\n" + "="*60)
        print("  BUILD SUCCESSFUL!")
        print("  Your standalone executable is in: dist / APMD_Bot.exe")
        print("="*60)
    except subprocess.CalledProcessError as e:
        print(f"\nBUILD FAILED: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build()
