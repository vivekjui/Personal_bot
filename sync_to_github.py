"""
Standalone GitHub sync utility.

This script runs separately from the app. It exports runtime-managed files from
the app's writable data area into `sync_exports/`, then commits and pushes the
repository when changes are detected.

Examples:
  python sync_to_github.py
  python sync_to_github.py --watch --interval 15
  python sync_to_github.py --remote origin --branch main
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parent
if os.name == "nt":
    DATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home()))) / "APMD_Bot"
else:
    DATA_ROOT = Path.home() / ".apmd_bot"

SYNC_EXPORT_DIR = BUNDLE_ROOT / "sync_exports"
CONFIG_PATH = DATA_ROOT / "config.json"
STANDARD_LIBRARY_PATH = DATA_ROOT / "standard_library.json"
PROCUREMENT_STAGES_PATH = DATA_ROOT / "procurement_stages.json"
EMAIL_CATEGORIES_PATH = DATA_ROOT / "email_categories.json"
EMAIL_LIBRARY_PATH = DATA_ROOT / "email_library.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export app state and push the repo to GitHub.")
    parser.add_argument("--watch", action="store_true", help="Keep watching and sync on an interval.")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval in seconds when --watch is used.")
    parser.add_argument("--remote", default="origin", help="Git remote name.")
    parser.add_argument("--branch", default="", help="Git branch to push. Defaults to current branch.")
    parser.add_argument("--repo-root", default=str(BUNDLE_ROOT), help="Path to the git repository root.")
    parser.add_argument("--git-exe", default=r"C:\Program Files\Git\cmd\git.exe", help="Path to git executable.")
    parser.add_argument("--reason", default="manual sync", help="Reason text for the auto-generated commit message.")
    return parser.parse_args()


def resolve_git_executable(configured: str) -> str:
    candidate = Path(configured)
    if configured and candidate.exists():
        return str(candidate)
    fallback = shutil.which("git")
    if fallback:
        return fallback
    raise RuntimeError(f"Git executable not found: {configured}")


def run_git(repo_root: Path, git_exe: str, args: list[str]) -> str:
    proc = subprocess.run(
        [git_exe, *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"git {' '.join(args)} failed")
    return (proc.stdout or "").strip()


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def mirror_file(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def load_runtime_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_database_path(config: dict) -> Path:
    raw_path = ((config.get("paths") or {}).get("database") or "").strip()
    if not raw_path:
        return DATA_ROOT / "cases.db"
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return DATA_ROOT / candidate.name


def get_prompt_settings_from_db(config: dict) -> dict:
    defaults = {
        "noting_master_prompt": ((config.get("llm") or {}).get("noting_master_prompt") or ""),
        "qa_system_prompt": ((config.get("llm") or {}).get("qa_system_prompt") or ""),
    }
    db_path = resolve_database_path(config)
    if not db_path.exists():
        return defaults

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key IN (?, ?)",
            ("noting_master_prompt", "qa_system_prompt"),
        ).fetchall()
        conn.close()
        for key, value in rows:
            defaults[key] = value or defaults.get(key, "")
    except Exception:
        pass
    return defaults


def export_runtime_state(reason: str) -> list[str]:
    SYNC_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    runtime_config = load_runtime_config()

    mirror_pairs = [
        (STANDARD_LIBRARY_PATH if STANDARD_LIBRARY_PATH.exists() else BUNDLE_ROOT / "standard_library.json", SYNC_EXPORT_DIR / "standard_library.json"),
        (PROCUREMENT_STAGES_PATH if PROCUREMENT_STAGES_PATH.exists() else BUNDLE_ROOT / "procurement_stages.json", SYNC_EXPORT_DIR / "procurement_stages.json"),
        (EMAIL_CATEGORIES_PATH, SYNC_EXPORT_DIR / "email_categories.json"),
        (EMAIL_LIBRARY_PATH, SYNC_EXPORT_DIR / "email_library.json"),
    ]
    for source, target in mirror_pairs:
        if source.exists():
            mirror_file(source, target)
            exported.append(str(target.relative_to(BUNDLE_ROOT)))

    prompt_snapshot = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "reason": reason,
        "prompts": get_prompt_settings_from_db(runtime_config),
    }
    prompt_file = SYNC_EXPORT_DIR / "llm_prompt_settings.json"
    write_json(prompt_file, prompt_snapshot)
    exported.append(str(prompt_file.relative_to(BUNDLE_ROOT)))
    return exported


def sync_once(repo_root: Path, git_exe: str, remote: str, branch: str, reason: str) -> str:
    if not (repo_root / ".git").exists():
        raise RuntimeError(f"Not a git repository: {repo_root}")

    exported = export_runtime_state(reason)
    current_branch = branch or run_git(repo_root, git_exe, ["rev-parse", "--abbrev-ref", "HEAD"])
    run_git(repo_root, git_exe, ["add", "-A"])
    status = run_git(repo_root, git_exe, ["status", "--porcelain"])
    if not status.strip():
        return f"No changes to sync. Exported: {', '.join(exported)}"

    commit_message = f"Auto-sync: {reason} [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    run_git(repo_root, git_exe, ["commit", "-m", commit_message])
    run_git(repo_root, git_exe, ["push", remote, current_branch])
    return f"Pushed to {remote}/{current_branch} with commit: {commit_message}"


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    git_exe = resolve_git_executable(args.git_exe)

    if not args.watch:
        print(sync_once(repo_root, git_exe, args.remote, args.branch, args.reason))
        return 0

    print(f"Watching {repo_root} every {args.interval}s. Press Ctrl+C to stop.")
    last_status = None
    try:
        while True:
            try:
                message = sync_once(repo_root, git_exe, args.remote, args.branch, "watch sync")
                if message != last_status:
                    print(message)
                    last_status = message
            except Exception as exc:
                print(f"Sync error: {exc}")
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
