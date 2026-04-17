import os
import re
import sys
import subprocess
from pathlib import Path

# --- Configuration ---
VERSION_FILE = Path("VERSION")
DASHBOARD_FILE = Path("dashboard.py")
GITHUB_REPO = "vivekjui/personal_bot"
MAIN_BRANCH = "main"

def get_current_version():
    if not VERSION_FILE.exists():
        return "0.0.0"
    return VERSION_FILE.read_text().strip()

def update_version_files(new_version):
    # Update VERSION file
    VERSION_FILE.write_text(new_version + "\n")
    print(f"Updated {VERSION_FILE}")

    # Update dashboard.py
    if DASHBOARD_FILE.exists():
        content = DASHBOARD_FILE.read_text(encoding="utf-8")
        updated_content = re.sub(r'VERSION\s*=\s*"[^"]+"', f'VERSION = "{new_version}"', content)
        DASHBOARD_FILE.write_text(updated_content, encoding="utf-8")
        print(f"Updated {DASHBOARD_FILE}")

def run_command(cmd, check=True):
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()

def main():
    if not Path(".git").exists():
        print("Error: Not a git repository.")
        sys.exit(1)

    current_version = get_current_version()
    print(f"Current version: {current_version}")
    
    new_version = input(f"Enter new version (default: {current_version}): ").strip()
    if not new_version:
        new_version = current_version

    print(f"Releasing version {new_version}...")
    
    # 1. Update files
    update_version_files(new_version)

    # 2. Git flow
    current_branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    print(f"Detected branch: {current_branch}")
    
    run_command(["git", "add", "-A"])
    run_command(["git", "commit", "-m", f"Release v{new_version}"])
    run_command(["git", "push", "origin", current_branch])

    # 3. GitHub Release
    tag = f"v{new_version}"
    print(f"Creating GitHub Release {tag}...")
    
    # Check if gh cli is available
    try:
        run_command(["gh", "--version"])
        run_command([
            "gh", "release", "create", tag,
            "--title", f"Release {tag}",
            "--notes", f"Auto-generated release for version {new_version}",
            "--target", MAIN_BRANCH
        ])
        print("GitHub Release created successfully!")
    except Exception as e:
        print(f"Warning: Could not create GitHub Release via 'gh' CLI: {e}")
        print("Please create the release manually on GitHub if needed.")

if __name__ == "__main__":
    main()
