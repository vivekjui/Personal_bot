"""
Noting Bot - Flask Web Dashboard Backend
All API routes for the web UI.
"""

VERSION = "1.08"
GITHUB_REPO = "vivekjui/Personal_bot"
GITHUB_REPO_LABEL = "Personal Bot"

import os
# Disable ChromaDB anonymous telemetry to avoid PostHog connection errors
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import re
import json
import requests
import zipfile
import io
import shutil
import base64
import threading
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import uuid

# --- Python 3.14 + Pydantic v1 (ChromaDB) Monkeypatch ---
try:
    import pydantic.v1.fields
    from typing import Any
    _old_set = pydantic.v1.fields.ModelField._set_default_and_type
    def _new_set(self):
        try:
            _old_set(self)
        except pydantic.v1.errors.ConfigError:
            self.type_ = Any
            self.outer_type_ = Any
    pydantic.v1.fields.ModelField._set_default_and_type = _new_set
except Exception:
    pass
# --------------------------------------------------------

# ── Import all modules ─────────────────────────────────────────────────────────
from modules.utils import CONFIG, logger, get_llm_status, find_free_port, BUNDLE_ROOT, DATA_ROOT, launch_chrome_debug
from modules.database import (initialize_database, get_connection, # Ensure DB is initialized
                               save_noting_history, get_noting_history,
                               delete_noting_history, get_all_cases,
                               get_prompt_settings, set_app_setting)
# Ensure DB is initialized EXACTLY ONCE on module load
from modules.eoffice_noting import (generate_noting_text, list_noting_types,
                                   search_standard_notings, translate_noting_llm)
from modules.doc_processor import process_zip_bid, process_zip_bid_multi, compress_pdf, merge_pdfs, MAX_SIZE_BYTES
from modules.extract import extract_text_from_file, generate_docx_from_html
from modules.tec_minutes import (generate_tec_draft_prompt, create_tec_docx, 
                                load_learned_patterns, extract_entities_from_raw_text)
# (RAG Engine and Bid Downloader moved to local imports to save startup time)

# --- STARTUP: Deferred Initialization ---
# initialize_database() is handled in database.py background thread
def deferred_prewarm():
    try:
        from modules.rag_engine import prewarm_vector_db
        prewarm_vector_db()
    except Exception as e:
        logger.warning(f"Failed to trigger RAG pre-warm: {e}")

# Delay pre-warm by 5 seconds to let the main UI become responsive first
threading.Timer(5.0, deferred_prewarm).start()

# ── Initialize ─────────────────────────────────────────────────────────────────
app = Flask(__name__, 
            template_folder=str(BUNDLE_ROOT / "templates_web"), 
            static_folder=str(BUNDLE_ROOT / "static"))
CORS(app)

# initialize_database() removed (redundant)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB limit
logger.info(f"Noting Bot Dashboard (v{VERSION}) started.")
UPDATE_META_PATH = DATA_ROOT / "update_meta.json"


def _read_update_meta() -> dict:
    try:
        if UPDATE_META_PATH.exists():
            with open(UPDATE_META_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read update metadata: {e}")
    return {}


def _write_update_meta(meta: dict) -> None:
    try:
        with open(UPDATE_META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write update metadata: {e}")


def _version_tuple(value: str) -> tuple:
    text = str(value or "").strip().lstrip("vV")
    parts = []
    for part in re.findall(r"\d+|[A-Za-z]+", text):
        parts.append(int(part) if part.isdigit() else part.lower())
    return tuple(parts)


import uuid

# --- Job Management for Background Tasks ---
_zip_jobs = {}
_zip_jobs_lock = threading.Lock()

_tec_analyze_jobs = {}
_tec_analyze_lock = threading.Lock()

_tec_extract_jobs = {}
_tec_extract_lock = threading.Lock()

_extraction_jobs = {}
_extraction_lock = threading.Lock()

def _fetch_remote_version(default_branch: str) -> str:
    from modules.utils import get_requests_proxies
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{default_branch}/dashboard.py"
    r = requests.get(raw_url, timeout=8, proxies=get_requests_proxies())
    if r.status_code != 200:
        return ""
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', r.text)
    return m.group(1).strip() if m else ""


def _get_repo_update_snapshot() -> dict:
    from modules.utils import get_requests_proxies
    proxies = get_requests_proxies()

    # Priority 1: Check latest Release (Official)
    try:
        release_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        r = requests.get(release_url, timeout=5, proxies=proxies)
        if r.status_code == 200:
            rel = r.json()
            return {
                "default_branch": "main",
                "head_sha": rel.get("target_commitish") or rel.get("tag_name"),
                "short_sha": (rel.get("tag_name") or "")[:7],
                "remote_version": (rel.get("tag_name") or "").lstrip("vV") or VERSION,
                "zip_url": rel.get("zipball_url"),
                "repo_html_url": f"https://github.com/{GITHUB_REPO}",
                "commit_html_url": rel.get("html_url"),
                "commit_message": rel.get("name") or rel.get("body") or "Official Release",
                "pushed_at": rel.get("published_at", ""),
            }
    except Exception as e:
        logger.warning(f"Failed to fetch latest release: {e}")

    # Priority 2: Fallback to default branch commits
    repo_url = f"https://api.github.com/repos/{GITHUB_REPO}"
    repo_resp = requests.get(repo_url, timeout=8, proxies=proxies)
    repo_resp.raise_for_status()
    repo_data = repo_resp.json()

    default_branch = repo_data.get("default_branch") or "main"
    commit_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{default_branch}"
    commit_resp = requests.get(commit_url, timeout=8, proxies=proxies)
    commit_resp.raise_for_status()
    commit_data = commit_resp.json()

    remote_version = _fetch_remote_version(default_branch)
    head_sha = commit_data.get("sha", "")
    short_sha = head_sha[:7] if head_sha else ""
    commit_message = ((commit_data.get("commit") or {}).get("message") or "").strip()
    zip_url = f"https://api.github.com/repos/{GITHUB_REPO}/zipball/{default_branch}"

    return {
        "default_branch": default_branch,
        "head_sha": head_sha,
        "short_sha": short_sha,
        "remote_version": remote_version,
        "zip_url": zip_url,
        "repo_html_url": repo_data.get("html_url", f"https://github.com/{GITHUB_REPO}"),
        "commit_html_url": commit_data.get("html_url", f"https://github.com/{GITHUB_REPO}/commit/{head_sha}"),
        "commit_message": commit_message,
        "pushed_at": repo_data.get("pushed_at", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN / SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/admin/status", methods=["GET"])
def api_admin_status():
    return jsonify({
        "version": VERSION,
        "repo": GITHUB_REPO,
        "repo_label": GITHUB_REPO_LABEL,
        "status": "online"
    })

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        "success": False,
        "error": "The uploaded file is too large. Default limit is 16MB; we have increased it, but this still exceeds the 500MB max limit."
    }), 413

@app.route("/api/update/apply", methods=["POST"])
def apply_update():
    """Fulfill user request to auto-replace existing version with GitHub latest."""
    try:
        import subprocess
        import os
        # Check if it's a git repo
        if not os.path.exists(".git"):
            return jsonify({"success": False, "error": "Not a git repository. Manual update required."})
        
        # Pull latest changes
        result = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({"success": False, "error": f"Git pull failed: {result.stderr}"})
        
        return jsonify({"success": True, "message": "Update applied! Please restart the bot to see changes."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/admin/check-updates", methods=["GET"])
def api_check_updates():
    """
    Check the configured GitHub repository default branch for newer source updates.
    """
    try:
        snapshot = _get_repo_update_snapshot()
        local_meta = _read_update_meta()
        remote_version = snapshot.get("remote_version") or VERSION
        local_sha = local_meta.get("head_sha", "")
        remote_sha = snapshot.get("head_sha", "")

        has_update = False
        if local_sha and remote_sha:
            has_update = local_sha != remote_sha
        elif snapshot.get("remote_version"):
            has_update = _version_tuple(snapshot["remote_version"]) > _version_tuple(VERSION)

        latest_label = snapshot.get("remote_version") or f"{VERSION} ({snapshot.get('short_sha', 'unknown')})"
        notes = snapshot.get("commit_message") or f"Latest commit on {snapshot.get('default_branch', 'main')}."
        return jsonify({
            "success": True,
            "current": VERSION,
            "current_sha": local_sha or "unknown",
            "latest": latest_label,
            "latest_sha": remote_sha or "unknown",
            "branch": snapshot.get("default_branch", "main"),
            "has_update": has_update,
            "url": snapshot.get("commit_html_url") or snapshot.get("repo_html_url"),
            "notes": notes,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/admin/install-update", methods=["POST"])
def api_install_update():
    """
    Download the latest repository snapshot ZIP from GitHub and extract updated app files locally.
    """
    try:
        snapshot = _get_repo_update_snapshot()
        zip_url = snapshot.get("zip_url")
        if not zip_url:
            return jsonify({"success": False, "error": "No ZIP download URL found for repository snapshot."})

        # 2. Download the ZIP content
        logger.info(f"Downloading update from {zip_url}...")
        from modules.utils import get_requests_proxies
        zip_resp = requests.get(zip_url, timeout=30, proxies=get_requests_proxies())
        if zip_resp.status_code != 200:
            return jsonify({"success": False, "error": f"Failed to download ZIP: {zip_resp.status_code}"})

        # 3. Extract the ZIP
        from modules.utils import BOT_ROOT
        
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            # Find the common prefix (the root folder in the zip)
            # GitHub's zipballs have a root folder like 'user-repo-hash/'
            prefix_folder = z.namelist()[0].split('/')[0]
            skipped_prefixes = (
                ".git/",
                ".github/",
                ".venv/",
                "__pycache__/",
                "build/",
                "dist/",
                "logs/",
                "temp_",
                "VivekBot_Release/",
            )
            skipped_exact = {
                "cases.db",
                "config.json",
            }
            
            for member in z.infolist():
                if member.filename == f"{prefix_folder}/":
                    continue
                
                # Strip the top level folder from the path
                rel_path = member.filename[len(prefix_folder)+1:]
                if not rel_path:
                    continue
                rel_path_posix = rel_path.replace("\\", "/")
                if rel_path_posix in skipped_exact or any(rel_path_posix.startswith(pfx) for pfx in skipped_prefixes):
                    continue
                
                target_path = BOT_ROOT / rel_path
                
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

        _write_update_meta({
            "repo": GITHUB_REPO,
            "branch": snapshot.get("default_branch", "main"),
            "head_sha": snapshot.get("head_sha", ""),
            "version": snapshot.get("remote_version") or VERSION,
            "installed_at": datetime.utcnow().isoformat() + "Z",
        })
        logger.info("Update installed successfully. User must restart the bot.")
        return jsonify({
            "success": True, 
            "message": f"Updated files fetched from {GITHUB_REPO}. Please restart the bot to apply changes."
        })

    except Exception as e:
        logger.error(f"Update installation failed: {e}")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HOME
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")

@app.after_request
def add_header(response):
    """
    Add headers to both force latest IE rendering engine or chrome frame,
    and also to cache the rendered page for 0 seconds.
    """
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response


# ─── CASES REMOVED ───


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1 — E-OFFICE NOTING
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/noting/types", methods=["GET"])
def api_noting_types():
    return jsonify(list_noting_types())


@app.route("/api/noting/draft", methods=["POST"])
def api_draft_noting():
    """
    Step 1: Generate noting suggestion TEXT only (no file saved).
    Returns the noting text so the user can review and edit it in the UI.
    """
    d = request.json
    try:
        text = generate_noting_text(
            additional_context=d.get("context", "")
        )
        return jsonify({"success": True, "text": text})
    except Exception as e:
        logger.error(f"Noting draft error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/finalize", methods=["POST"])
def api_finalize_noting():
    """
    Step 2: Save the (possibly user-edited) noting text to the database history.
    """
    d = request.json
    try:
        case_id = (d.get("case_id") or "General").strip() or "General"
        final_text = d.get("text", "")
        final_html = d.get("html", "")
        original_text = d.get("original_text", "")
        learned_patterns = 0
        if original_text and original_text.strip() and original_text.strip() != final_text.strip():
            from modules.eoffice_noting import learn_from_noting_edit
            learned_patterns = learn_from_noting_edit(
                original_text=original_text,
                final_text=final_text,
                case_id=case_id,
                noting_type="Noting",
            )
        # collapse multiple blank lines to single
        cleaned = re.sub(r"(\r?\n){2,}", "\n", final_text).strip()
        content_to_save = (final_html or cleaned).strip()
        save_noting_history(
            case_id=case_id,
            noting_type="Noting",
            content=content_to_save,
            ai_content=original_text,
        )
        return jsonify({
            "success": True,
            "message": "Saved to history.",
            "learned_patterns": learned_patterns,
        })
    except Exception as e:
        logger.error(f"Noting finalize error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/standard", methods=["GET"])
def api_standard_notings():
    """Search or list standard notings."""
    query = request.args.get("query", "")
    paged = request.args.get("paged", "0") == "1"
    if not paged:
        return jsonify(search_standard_notings(query))

    stage = request.args.get("stage", "")
    limit = request.args.get("limit", default=10, type=int)
    offset = request.args.get("offset", default=0, type=int)
    items, total = search_standard_notings(
        query,
        stage=stage,
        limit=limit,
        offset=offset,
        include_total=True,
    )
    return jsonify({
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(items)) < total,
    })


@app.route("/api/noting/retrieve", methods=["POST"])
def api_retrieve_noting():
    """Step 1: Retrieve matching templates."""
    d = request.json
    context = d.get("context", "")
    if not context:
        return jsonify({"error": "Context required"}), 400
    try:
        from modules.eoffice_noting import retrieve_best_noting
        results = retrieve_best_noting(context)
        return jsonify({"success": True, "notings": results})
    except Exception as e:
        logger.error(f"Retrieve Noting error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/refine", methods=["POST"])
def api_refine_noting():
    """AI-powered refinement and translation of draft noting."""
    d = request.json
    try:
        from modules.eoffice_noting import refine_and_translate_rich
        text = d.get("text", "")
        source_html = d.get("html", "")
        modifications = d.get("modifications", "")
        target_lang = d.get("target_lang", "hindi")
        doc_type = d.get("document_type", "noting")
        
        refined_text, refined_html = refine_and_translate_rich(
            text=text,
            modifications=modifications,
            target_lang=target_lang,
            source_html=source_html,
            document_type=doc_type
        )
        
        # Check for AI failures that were caught and returned as strings
        if refined_text.startswith("[AI Error"):
            return jsonify({"success": False, "error": refined_text}), 200

        return jsonify({
            "success": True, 
            "refined_text": refined_text,
            "refined_html": refined_html
        })
    except Exception as e:
        logger.error(f"Noting refine error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/noting/library/update", methods=["POST"])
def api_update_library_noting():
    """Update specific library noting fields (text or keyword)."""
    d = request.json
    noting_id = d.get("id")
    if noting_id is None:
        return jsonify({"error": "ID required"}), 400
    
    # Extract any fields provided in the request
    updates = {}
    if "text" in d: updates["text"] = d["text"]
    if "keyword" in d: updates["keyword"] = d["keyword"]
    
    try:
        from modules.eoffice_noting import update_library_noting
        success = update_library_noting(int(noting_id), updates)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Library update error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/library/add", methods=["POST"])
def api_add_library_noting():
    """Add a new noting to the library."""
    d = request.json or {}
    stage = (d.get("stage") or "").strip()
    keyword = (d.get("keyword") or "").strip()
    text = d.get("text") or ""
    if not str(text).strip():
        return jsonify({"error": "Text is required"}), 400
    try:
        from modules.eoffice_noting import add_library_noting
        success = add_library_noting(stage, keyword, text)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Library add error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/noting/library/move", methods=["POST"])
def api_move_library_noting():
    """Move a noting to a different stage."""
    d = request.json
    noting_id = d.get("id")
    new_stage = (d.get("stage") or "").strip()
    if noting_id is None:
        return jsonify({"error": "ID is required"}), 400
    try:
        from modules.eoffice_noting import move_library_noting
        success = move_library_noting(int(noting_id), new_stage)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Library move error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/noting/library/delete/<int:nid>", methods=["DELETE"])
def api_delete_library_noting(nid):
    """Delete a noting from the library."""
    try:
        from modules.eoffice_noting import delete_library_noting
        success = delete_library_noting(nid)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Library delete error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/library/delete-stages", methods=["POST"])
def api_delete_library_by_stages():
    """Remove all library entries whose stage is in provided list.

    Used by the frontend for bulk library cleanup actions.
    """
    stages = request.json
    if not isinstance(stages, list):
        return jsonify({"error": "List of stages required"}), 400
    try:
        from modules.eoffice_noting import delete_library_notings_by_stages
        removed = delete_library_notings_by_stages(stages)
        return jsonify({"success": True, "removed": removed})
    except Exception as e:
        logger.error(f"Error deleting library by stages: {e}")
        return jsonify({"error": str(e)}), 500


# ====================================================
# EMAIL DRAFTING MODULE ENDPOINTS
# ====================================================

@app.route("/api/email/categories", methods=["GET"])
def api_get_email_categories():
    """Return the current list of email categories."""
    try:
        from modules.eoffice_noting import load_email_categories
        return jsonify(load_email_categories())
    except Exception as e:
        logger.error(f"Failed to fetch email categories: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/categories/update", methods=["POST"])
def api_update_email_categories():
    """Persist an updated list of email categories."""
    cats = request.json
    if not isinstance(cats, list):
        return jsonify({"error": "List of categories required"}), 400
    try:
        from modules.eoffice_noting import save_email_categories
        success = save_email_categories(cats)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Error saving email categories: {e}")
        return jsonify({"error": str(e)}), 500


# library routes mirror the noting library API but operate on email templates
@app.route("/api/email/library", methods=["GET"])
def api_get_email_library():
    from modules.eoffice_noting import load_email_library
    try:
        return jsonify(load_email_library())
    except Exception as e:
        logger.error(f"Failed to load email library: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/library/add", methods=["POST"])
def api_add_email_library():
    d = request.json or {}
    stage = (d.get("stage") or "").strip()
    keyword = (d.get("keyword") or "").strip()
    text = d.get("text") or ""
    if not str(text).strip():
        return jsonify({"error": "Text is required"}), 400
    try:
        from modules.eoffice_noting import add_library_email
        success = add_library_email(stage, keyword, text)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Email library add error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/library/update", methods=["POST"])
def api_update_email_library():
    d = request.json
    eid = d.get("id")
    if eid is None:
        return jsonify({"error": "ID required"}), 400
    updates = {}
    if "text" in d: updates["text"] = d["text"]
    if "keyword" in d: updates["keyword"] = d["keyword"]
    try:
        from modules.eoffice_noting import update_library_email
        success = update_library_email(int(eid), updates)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Email library update error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/library/move", methods=["POST"])
def api_move_email_library():
    d = request.json
    eid = d.get("id")
    new_stage = (d.get("stage") or "").strip()
    if eid is None:
        return jsonify({"error": "ID is required"}), 400
    try:
        from modules.eoffice_noting import move_library_email
        success = move_library_email(int(eid), new_stage)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Email library move error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/library/delete/<int:eid>", methods=["DELETE"])
def api_delete_email_library(eid):
    try:
        from modules.eoffice_noting import delete_library_email
        success = delete_library_email(eid)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Email library delete error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/email/library/delete-stages", methods=["POST"])
def api_delete_email_library_by_categories():
    cats = request.json
    if not isinstance(cats, list):
        return jsonify({"error": "List of categories required"}), 400
    try:
        from modules.eoffice_noting import delete_library_emails_by_categories
        removed = delete_library_emails_by_categories(cats)
        return jsonify({"success": True, "removed": removed})
    except Exception as e:
        logger.error(f"Error deleting email library by categories: {e}")
        return jsonify({"error": str(e)}), 500




@app.route("/api/noting/translate-high-quality", methods=["POST"])
def api_translate_high_quality():
    """High-quality translation using LLM."""
    d = request.json
    text = d.get("text", "").strip()
    target = d.get("target", "hindi")
    if not text:
        return jsonify({"error": "Text required"}), 400
    try:
        translated = translate_noting_llm(text, target)
        return jsonify({"success": True, "translated": translated})
    except Exception as e:
        logger.error(f"High-quality translation API error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/noting/stages", methods=["GET"])
def api_get_stages():
    """Get the current list of procurement stages."""
    from modules.eoffice_noting import get_procurement_stages
    return jsonify(get_procurement_stages())

@app.route("/api/noting/stages/update", methods=["POST"])
def api_update_stages():
    """Update the list/order of procurement stages."""
    stages = request.json
    if not isinstance(stages, list):
        return jsonify({"error": "List of stages required"}), 400
    try:
        from modules.eoffice_noting import update_procurement_stages
        success = update_procurement_stages(stages)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Error updating stages: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/noting/history/<case_id>", methods=["GET"])
def api_noting_history(case_id):
    """Return the noting history."""
    return jsonify(get_noting_history(case_id))


@app.route("/api/noting/history/<int:history_id>", methods=["DELETE"])
def api_delete_noting_history(history_id):
    """Delete a specific noting history entry."""
    success = delete_noting_history(history_id)
    return jsonify({"success": success})


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3 — KNOW HOW (RAG Q&A)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/kb/qa", methods=["POST"])
def api_kb_qa():
    d = request.json or {}
    question = d.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question required"}), 400
    
    try:
        from modules.rag_engine import ask_gemini_with_rag
        from modules.database import add_know_how_history
        
        # answer_data is now a dict: {"answer": "...", "sources": [...]}
        answer_data = ask_gemini_with_rag(question)
        
        # Save to history
        add_know_how_history(question, answer_data["answer"])
        
        return jsonify({
            "success": True,
            "answer": answer_data["answer"],
            "sources": answer_data["sources"]
        })
    except Exception as e:
        logger.error(f"KNOW HOW QA error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/kb/qa/translate", methods=["POST"])
def api_kb_qa_translate():
    d = request.json or {}
    text = d.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text required"}), 400
    try:
        from modules.eoffice_noting import translate_noting_llm
        hindi = translate_noting_llm(text, target="hindi")
        return jsonify({"success": True, "hindi": hindi})
    except Exception as e:
        logger.error(f"KNOW HOW Translate error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/kb/qa/feedback", methods=["POST"])
def api_kb_qa_feedback():
    d = request.json or {}
    q = d.get("question")
    a = d.get("answer")
    f = d.get("feedback")
    if not all([q, a, f]):
        return jsonify({"error": "Question, Answer and Feedback required"}), 400
    try:
        from modules.database import add_qa_feedback
        add_qa_feedback(q, a, f)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"KNOW HOW Feedback error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/know-how/history", methods=["GET"])
def api_know_how_history():
    try:
        from modules.database import get_know_how_history
        return jsonify(get_know_how_history())
    except Exception as e:
        logger.error(f"KNOW HOW History error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/know-how/history/<int:hid>", methods=["DELETE"])
def api_delete_know_how_history(hid):
    try:
        from modules.database import delete_know_how_history
        success = delete_know_how_history(hid)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"KNOW HOW Delete History error: {e}")
        return jsonify({"error": str(e)}), 500

# ── UTILS ────────────────────────────────────────────────────────────────────
@app.route("/api/utils/open-folder", methods=["POST"])
def api_open_folder():
    path_str = request.json.get("path")
    if not path_str: return jsonify({"error": "Path required"}), 400
    try:
        import subprocess
        # Using abspath to ensure it's absolute
        abs_path = os.path.abspath(path_str)
        if hasattr(os, 'startfile'):
            os.startfile(abs_path)
        else:
            subprocess.Popen(['explorer', abs_path])
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Open folder error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/utils/open-chrome", methods=["POST"])
def api_open_chrome():
    data = request.json or {}
    target_url = (data.get("url") or "").strip() or "https://gem.gov.in"
    try:
        debug_port, debug_error = launch_chrome_debug(port=9222)
        if not debug_port:
            return jsonify({"error": debug_error or "Could not start Chrome in debug mode."}), 500

        try:
            import urllib.parse
            import urllib.request

            encoded_url = urllib.parse.quote(target_url, safe=":/?&=%#")
            req = urllib.request.Request(
                f"http://127.0.0.1:{debug_port}/json/new?{encoded_url}",
                method="PUT",
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as nav_exc:
            logger.warning(f"Debug Chrome started on {debug_port}, but opening URL failed: {nav_exc}")

        return jsonify({"success": True, "url": target_url, "port": debug_port})
    except Exception as e:
        logger.error(f"Open chrome error: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2 — GE-M BID DOCUMENT PROCESSOR (ZIP/PDF)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/documents/process-zip", methods=["POST"])
def api_process_zip():
    """Option A: Browser Upload (Asynchronous)"""
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist("files")
    # Find a reliable Desktop path (considering OneDrive)
    output_dir = Path.home() / "Desktop"
    if not output_dir.exists():
        onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
        if onedrive_desktop.exists():
            output_dir = onedrive_desktop
            
    output_dir.mkdir(parents=True, exist_ok=True)
    
    job_id = str(uuid.uuid4())[:8]
    with _zip_jobs_lock:
        _zip_jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "total": len(files),
            "results": [],
            "output_dir": str(output_dir.absolute())
        }

    # Save files to temp location first
    temp_files = []
    temp_dir = DATA_ROOT / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    from modules.utils import sanitize_filename
    for f in files:
        if not f.filename.endswith(".zip"): continue
        # Sanitize filename
        safe_name = sanitize_filename(f.filename)
        temp_path = temp_dir / safe_name
        f.save(str(temp_path))
        temp_files.append(temp_path)

    def _run_zip_job():
        try:
            with _zip_jobs_lock:
                _zip_jobs[job_id]["status"] = "running"
            
            for i, temp_zip in enumerate(temp_files):
                try:
                    generated = process_zip_bid(temp_zip, output_dir)
                    with _zip_jobs_lock:
                        _zip_jobs[job_id]["results"].append({"original_zip": temp_zip.name, "output_files": generated})
                        _zip_jobs[job_id]["progress"] = i + 1
                except Exception as e:
                    import traceback
                    logger.error(f"Async process error {temp_zip.name}: {e}")
                    logger.error(traceback.format_exc())
                    with _zip_jobs_lock:
                        _zip_jobs[job_id]["results"].append({"original_zip": temp_zip.name, "error": str(e)})
        except Exception as catastrophic:
            import traceback
            logger.error(f"Catastrophic failure in _run_zip_job: {catastrophic}")
            logger.error(traceback.format_exc())
            with _zip_jobs_lock:
                _zip_jobs[job_id]["status"] = "failed"
                _zip_jobs[job_id]["error"] = str(catastrophic)
        finally:
            for temp_zip in temp_files:
                if temp_zip.exists(): 
                    try: temp_zip.unlink()
                    except: pass
            with _zip_jobs_lock:
                if _zip_jobs[job_id]["status"] != "failed":
                    _zip_jobs[job_id]["status"] = "complete"

    threading.Thread(target=_run_zip_job, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id, "message": f"Processing {len(files)} files in background."})


@app.route("/api/documents/process-zip-local", methods=["POST"])
def api_process_zip_local():
    """Option B: Local Folder (Asynchronous)"""
    d = request.json or {}
    folder_path_str = d.get("folder_path")
    if not folder_path_str:
        return jsonify({"error": "Folder path is required"}), 400
    
    input_dir = Path(folder_path_str)
    if not input_dir.exists() or not input_dir.is_dir():
        return jsonify({"error": f"Invalid folder path: {folder_path_str}"}), 400
    
    zip_files = list(input_dir.glob("*.zip"))
    if not zip_files:
        return jsonify({"success": True, "results": [], "message": "No ZIP files found."})
    
    job_id = str(uuid.uuid4())[:8]
    with _zip_jobs_lock:
        _zip_jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "total": len(zip_files),
            "results": [],
            "output_dir": str(input_dir.absolute())
        }

    def _run_local_zip_job():
        try:
            with _zip_jobs_lock:
                _zip_jobs[job_id]["status"] = "running"
            
            for i, zip_path in enumerate(zip_files):
                try:
                    generated = process_zip_bid(zip_path, input_dir)
                    with _zip_jobs_lock:
                        _zip_jobs[job_id]["results"].append({"original_zip": zip_path.name, "output_files": generated})
                        _zip_jobs[job_id]["progress"] = i + 1
                except Exception as e:
                    import traceback
                    logger.error(f"Local process error {zip_path.name}: {e}")
                    logger.error(traceback.format_exc())
                    with _zip_jobs_lock:
                        _zip_jobs[job_id]["results"].append({"original_zip": zip_path.name, "error": str(e)})
        except Exception as catastrophic:
            import traceback
            logger.error(f"Catastrophic failure in _run_local_zip_job: {catastrophic}")
            logger.error(traceback.format_exc())
            with _zip_jobs_lock:
                _zip_jobs[job_id]["status"] = "failed"
                _zip_jobs[job_id]["error"] = str(catastrophic)
        finally:
            with _zip_jobs_lock:
                if _zip_jobs[job_id]["status"] != "failed":
                    _zip_jobs[job_id]["status"] = "complete"

    threading.Thread(target=_run_local_zip_job, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id, "message": f"Processing {len(zip_files)} files in background."})


@app.route("/api/documents/zip-status/<job_id>", methods=["GET"])
def api_zip_status(job_id):
    with _zip_jobs_lock:
        job = _zip_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"success": True, **job})


@app.route("/api/tec/minutes/draft", methods=["POST"])
def api_tec_minutes_draft():
    """Draft TEC minutes using learned patterns and raw input."""
    d = request.json or {}
    tec_type = d.get("tec_type", "Technical")
    category = d.get("category", "General")
    indenting_member = d.get("indenting_member", ".....")
    raw_input = d.get("raw_input", "").strip()
    
    if not raw_input:
        return jsonify({"error": "No input provided"}), 400
        
    try:
        from modules.rag_pro import get_llm_client
        llm = get_llm_client()
        
        learned_kb = load_learned_patterns()
        # Ensure indenting member is passed to the prompt
        prompt = generate_tec_draft_prompt(tec_type, category, raw_input, learned_kb, indenting_member=indenting_member)
        
        # Simple extraction for pre-filling
        entities = extract_entities_from_raw_text(raw_input)
        
        # Generation
        response = llm.generate_content(prompt)
        draft_text = response.text
        
        # Convert plain text draft to basic HTML for the Quill editor
        draft_html = draft_text.replace('\n', '<br>')
        
        return jsonify({
            "success": True, 
            "draft_html": draft_html,
            "entities": entities
        })
    except Exception as e:
        logger.error(f"TEC drafting error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/tec/minutes/download", methods=["POST"])
def api_tec_minutes_download():
    """Download the drafted minutes as a Legal-sized DOCX."""
    d = request.json or {}
    content_html = d.get("html", "")
    title = d.get("title", "TEC Minutes")
    
    if not content_html:
        return jsonify({"error": "No content provided"}), 400
        
    try:
        out_path = DATA_ROOT / "temp" / f"TEC_Minutes_{datetime.now().strftime('%H%M%S')}.docx"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        create_tec_docx(str(out_path), content_html, title=title)
        
        return send_file(str(out_path), as_attachment=True, download_name=f"{title.replace(' ', '_')}.docx")
    except Exception as e:
        logger.error(f"TEC download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/documents/serve", methods=["GET"])
def api_serve_doc():
    path = request.args.get("path", "")
    if path and Path(path).exists():
        return send_file(path, as_attachment=True)
    return jsonify({"error": "File not found"}), 404

@app.route("/api/documents/merge-pdf", methods=["POST"])
def api_merge_pdf():
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files selected"}), 400

    # Create the 'PDF Tools' root folder on Desktop
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        onedrive = Path.home() / "OneDrive" / "Desktop"
        if onedrive.exists(): desktop = onedrive
        
    pdf_tools_root = desktop / "PDF Tools"
    pdf_tools_root.mkdir(parents=True, exist_ok=True)
    
    output_dir = pdf_tools_root / f"Merge_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_filename = "Merged_Document.pdf"
    output_path = output_dir / output_filename
    
    # Save temp files
    temp_dir = DATA_ROOT / "temp_merge"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_paths = []
    
    try:
        for f in files:
            path = temp_dir / f.filename
            f.save(str(path))
            temp_paths.append(path)
        
        merge_pdfs(temp_paths, output_path)
        return jsonify({"success": True, "message": f"Merged file saved to folder: {output_dir.name}", "output_path": str(output_path), "output_dir": str(output_dir)})
    except Exception as e:
        logger.error(f"Merge PDF error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        for p in temp_paths:
            if p.exists(): p.unlink()

@app.route("/api/documents/compress-pdf", methods=["POST"])
def api_compress_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    mode = request.form.get("mode", "medium")
    
    # Create the 'PDF Tools' root folder on Desktop
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        onedrive = Path.home() / "OneDrive" / "Desktop"
        if onedrive.exists(): desktop = onedrive
        
    pdf_tools_root = desktop / "PDF Tools"
    pdf_tools_root.mkdir(parents=True, exist_ok=True)
    
    output_dir = pdf_tools_root / f"Compress_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    orig_name = Path(file.filename).stem
    output_path = output_dir / f"{orig_name}_compressed.pdf"
    
    temp_dir = DATA_ROOT / "temp_compress"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_input = temp_dir / file.filename
    
    try:
        file.save(str(temp_input))
        original_size = temp_input.stat().st_size
        
        final_output_path = compress_pdf(temp_input, output_path, mode=mode)
        
        # Check if the file actually exists now
        if not final_output_path.exists():
            raise FileNotFoundError(f"Compressed file was not created: {final_output_path}")
            
        new_size = final_output_path.stat().st_size
        
        response_data = {
            "success": True, 
            "original_size": original_size,
            "new_size": new_size,
            "size_mb": round(new_size / (1024 * 1024), 2),
            "output_path": str(final_output_path),
            "output_dir": str(output_dir),
            "filename": final_output_path.name
        }

        if new_size > MAX_SIZE_BYTES:
            response_data["needs_split"] = True
            response_data["temp_path"] = str(final_output_path)
            response_data["message"] = f"File is still over 19.9MB ({round(new_size/(1024*1024), 2)}MB). Use Split tool to divide it."
        else:
            response_data["message"] = f"Compressed successfully to {round(new_size/(1024*1024), 2)}MB."

        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Compress PDF error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if temp_input.exists(): temp_input.unlink()


@app.route("/api/documents/split-pdf", methods=["POST"])
def api_split_pdf():
    """Splits a pre-existing PDF file (usually from compression) into < 20MB parts."""
    data = request.json or {}
    file_path_str = data.get("file_path")
    original_name = data.get("original_name", "Split_PDF")
    pages_per_part = data.get("pages_per_part")
    
    # Ensure pages_per_part is an int if provided
    if pages_per_part is not None:
        try:
            pages_per_part = int(pages_per_part)
        except (ValueError, TypeError):
            pages_per_part = None
    
    if not file_path_str:
        return jsonify({"success": False, "error": "Missing file_path"}), 400
        
    file_path = Path(file_path_str)
    if not file_path.exists():
        return jsonify({"success": False, "error": "File no longer exists"}), 404
        
    # Create the 'PDF Tools' root folder on Desktop
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        onedrive = Path.home() / "OneDrive" / "Desktop"
        if onedrive.exists(): desktop = onedrive
        
    pdf_tools_root = desktop / "PDF Tools"
    pdf_tools_root.mkdir(parents=True, exist_ok=True)
    
    output_dir = pdf_tools_root / f"Split_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
            
    base_name = Path(original_name).stem
    
    try:
        from modules.doc_processor import split_pdf_by_size
        parts = split_pdf_by_size(file_path, output_dir, base_name, pages_per_part=pages_per_part)
        
        return jsonify({
            "success": True, 
            "message": f"Successfully split into {len(parts)} parts in folder: {output_dir.name}",
            "parts": parts,
            "output_dir": str(output_dir)
        })
    except Exception as e:
        logger.error(f"Split PDF error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── TENDER SCRUTINY REMOVED ───
# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/dashboard/summary", methods=["GET"])
def api_dashboard_summary():
    try:
        cases = get_all_cases()
        from modules.database import get_expiring_deposits, get_due_reminders
        emd_alerts = get_expiring_deposits(days_ahead=30)
        reminders = get_due_reminders()
        
        return jsonify({
            "total_cases": len(cases),
            "active_cases": sum(1 for c in cases if c.get("status") == "Active"),
            "emd_alerts": len(emd_alerts),
            "upcoming_reminders": len(reminders),
            "alerts_detail": emd_alerts[:5],
            "reminders_detail": reminders[:5]
        })
    except Exception as e:
        logger.error(f"Dashboard summary error: {e}")
        return jsonify({
            "total_cases": 0,
            "active_cases": 0,
            "emd_alerts": 0,
            "upcoming_reminders": 0,
            "error": str(e)
        })



# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 9 — RAG KNOWLEDGE BASE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/kb/stats", methods=["GET"])
def api_kb_stats():
    from modules.rag_engine import kb_stats
    return jsonify(kb_stats())


@app.route("/api/kb/documents", methods=["GET"])
def api_kb_docs():
    from modules.rag_engine import get_all_kb_documents
    return jsonify(get_all_kb_documents())


@app.route("/api/kb/categories", methods=["GET"])
def api_kb_categories():
    from modules.rag_engine import DOC_CATEGORIES
    return jsonify(DOC_CATEGORIES)


@app.route("/api/kb/ingest", methods=["POST"])
def api_kb_ingest():
    """
    Ingest an uploaded file OR a local path into the Knowledge Base.
    Returns immediately with a job_id — actual ML work runs in a background thread
    so the browser can be closed and ingestion still completes.
    """
    try:
        category    = request.form.get("category") or (request.get_json(silent=True) or {}).get("category", "Other Reference")
        description = request.form.get("description") or (request.get_json(silent=True) or {}).get("description", "")

        # ── File Upload mode ──
        if "file" in request.files:
            file = request.files["file"]
            if not file.filename:
                return jsonify({"success": False, "error": "No file selected"}), 400
            from pathlib import Path as _PL
            inbox = _PL(CONFIG.get("rag", {}).get("kb_dir", str(DATA_ROOT / "knowledge_base"))) / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            save_path = inbox / file.filename
            file.save(str(save_path))
            from modules.rag_engine import ingest_document_async
            job_id = ingest_document_async(str(save_path), category=category, description=description)
            return jsonify({"success": True, "queued": True, "job_id": job_id,
                            "message": f"'{file.filename}' queued for background ingestion (job: {job_id})"})

        # ── Filepath mode ──
        d = request.get_json(silent=True) or {}
        filepath = d.get("filepath")
        if not filepath:
            return jsonify({"success": False, "error": "Provide 'file' upload or 'filepath' in JSON"}), 400
        from modules.rag_engine import ingest_document_async
        job_id = ingest_document_async(filepath, category=category, description=description,
                                       force_reingest=d.get("force", False))
        return jsonify({"success": True, "queued": True, "job_id": job_id,
                        "message": f"Queued for background ingestion (job: {job_id})"})
    except Exception as e:
        logger.error(f"Error in /api/kb/ingest: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/kb/ingest/status/<job_id>", methods=["GET"])
def api_kb_ingest_status(job_id):
    """Poll the status of a background ingest job."""
    from modules.rag_engine import get_ingest_job_status
    return jsonify(get_ingest_job_status(job_id))


@app.route("/api/kb/ingest/jobs", methods=["GET"])
def api_kb_ingest_jobs():
    """List all background ingest jobs (latest first)."""
    from modules.rag_engine import get_all_ingest_jobs
    return jsonify(get_all_ingest_jobs())


@app.route("/api/kb/watch-folder", methods=["GET"])
def api_kb_watch_folder():
    """Return path to the auto-ingest watch folder."""
    from modules.rag_engine import WATCH_FOLDER
    return jsonify({"path": str(WATCH_FOLDER)})


@app.route("/api/kb/documents/<doc_id>", methods=["DELETE"])
def api_kb_docs_delete(doc_id):
    from modules.rag_engine import delete_kb_document
    success = delete_kb_document(doc_id)
    return jsonify({"success": success})


@app.route("/api/kb/documents/<doc_id>", methods=["PUT"])
def api_kb_docs_update(doc_id):
    from modules.rag_engine import update_document_category
    data = request.json or {}
    new_category = data.get("category")
    if not new_category:
        return jsonify({"success": False, "error": "Missing 'category'"}), 400
        
    success = update_document_category(doc_id, new_category)
    return jsonify({"success": success})


@app.route("/api/kb/ingest-folder", methods=["POST"])
def api_kb_ingest_folder():
    from modules.rag_engine import ingest_folder
    d = request.json or {}
    results = ingest_folder(
        folder_path=d["folder_path"],
        category=d.get("category", "Other Reference"),
        recursive=d.get("recursive", False)
    )
    success = sum(1 for r in results if r.get("success"))
    return jsonify({"total": len(results), "success": success, "results": results})


@app.route("/api/kb/search", methods=["POST"])
def api_kb_search():
    from modules.rag_engine import search_kb
    d = request.json or {}
    results = search_kb(d.get("query", ""), n_results=d.get("n", 8))
    return jsonify(results)


@app.route("/api/kb/documents/<doc_id>", methods=["DELETE"])
def api_kb_delete(doc_id):
    from modules.rag_engine import delete_kb_document
    ok = delete_kb_document(doc_id)
    return jsonify({"success": ok})


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 10 — TEC EVALUATION BOT
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/tec/analyze", methods=["POST"])
def api_tec_analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
        
    ext = Path(file.filename).suffix.lower()
    if ext not in [".pdf", ".docx"]:
        return jsonify({"error": "Only .pdf and .docx files are supported"}), 400
        
    temp_dir = DATA_ROOT / "temp_tec"
    temp_dir.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())[:8]
    file_path = temp_dir / f"{file_id}{ext}"
    file.save(str(file_path))
    
    job_id = f"analyze_{file_id}"
    with _tec_analyze_lock:
        _tec_analyze_jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run_analyze():
        try:
            from modules.tec_eval import extract_data_from_pdf, extract_data_from_docx, analyze_parameters
            if ext == ".pdf":
                df = extract_data_from_pdf(str(file_path))
            else:
                df = extract_data_from_docx(str(file_path))
                
            if df.empty:
                error_msg = "Could not extract tabular data from the document."
                with _tec_analyze_lock:
                    _tec_analyze_jobs[job_id] = {"status": "failed", "error": error_msg}
                return
                
            params = analyze_parameters(df)
            with _tec_analyze_lock:
                _tec_analyze_jobs[job_id] = {
                    "status": "complete", 
                    "result": {
                        "file_id": file_id,
                        "extension": ext,
                        "parameters": params
                    }
                }
        except Exception as e:
            logger.error(f"Async TEC Analyze error: {e}")
            with _tec_analyze_lock:
                _tec_analyze_jobs[job_id] = {"status": "failed", "error": str(e)}

    threading.Thread(target=_run_analyze, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/tec/analyze-status/<job_id>", methods=["GET"])
def api_tec_analyze_status(job_id):
    with _tec_analyze_lock:
        job = _tec_analyze_jobs.get(job_id)
        if not job: return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

@app.route("/api/tec/extract", methods=["POST"])
def api_tec_extract():
    data = request.json or {}
    file_id = data.get("file_id")
    ext = data.get("extension")
    criteria = data.get("criteria", {})
    use_llm = data.get("use_llm", False)
    
    if not file_id:
        return jsonify({"error": "No file session found"}), 400
        
    file_path = DATA_ROOT / "temp_tec" / f"{file_id}{ext}"
    if not file_path.exists():
        return jsonify({"error": "File session expired or not found"}), 400
        
    job_id = f"extract_{file_id}"
    with _tec_extract_lock:
        _tec_extract_jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run_extract():
        try:
            from modules.tec_eval import extract_data_from_pdf, extract_data_from_docx, process_evaluations, process_evaluations_llm
            if ext == ".pdf":
                df = extract_data_from_pdf(str(file_path))
            else:
                df = extract_data_from_docx(str(file_path))
                
            if df.empty:
                error_msg = "Could not extract tabular data for evaluation."
                with _tec_extract_lock:
                    _tec_extract_jobs[job_id] = {"status": "failed", "error": error_msg}
                return

            if use_llm:
                eval_results = process_evaluations_llm(df, criteria=criteria)
            else:
                eval_results = process_evaluations(df, criteria=criteria)

            with _tec_extract_lock:
                _tec_extract_jobs[job_id] = {
                    "status": "complete",
                    "result": {
                        "results": eval_results["results"],
                        "stats": eval_results["stats"]
                    }
                }
        except Exception as e:
            logger.error(f"Async TEC Extract error: {e}")
            with _tec_extract_lock:
                _tec_extract_jobs[job_id] = {"status": "failed", "error": str(e)}
        finally:
            if file_path.exists(): file_path.unlink()

    threading.Thread(target=_run_extract, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/tec/extract-status/<job_id>", methods=["GET"])
def api_tec_extract_status(job_id):
    with _tec_extract_lock:
        job = _tec_extract_jobs.get(job_id)
        if not job: return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

@app.route("/api/tec/launch-chrome", methods=["POST"])
def api_tec_launch_chrome():
    try:
        debug_port, debug_error = launch_chrome_debug(port=9222)
        if not debug_port:
            return jsonify({"success": False, "error": debug_error or "Could not start a debuggable Chrome session on this PC."}), 500
        return jsonify({"success": True, "message": f"Chrome launched in debug mode on port {debug_port}.", "port": debug_port})
    except Exception as e:
        logger.error(f"Chrome Launch error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

import uuid

# Global dict to hold TEC evaluation jobs for streaming
TEC_JOBS = {}

# Global dict to hold Bid Downloader jobs for streaming
BID_JOBS = {}

@app.route("/api/tec/execute", methods=["POST"])
def api_tec_execute():
    data = request.json or {}
    eval_results = data.get("results", [])
    gem_url = data.get("gem_url", "")
    use_direct_mode = bool(data.get("use_direct_mode", False))
    
    if not eval_results:
        return jsonify({"error": "No evaluation data provided"}), 400
        
    try:
        # Generate a unique job ID and store the payload
        job_id = str(uuid.uuid4())
        TEC_JOBS[job_id] = {
            "eval_results": eval_results,
            "gem_url": gem_url,
            "use_direct_mode": use_direct_mode
        }
        
        # Return instantly so the frontend can open the EventSource stream
        return jsonify({"success": True, "job_id": job_id})
        
    except Exception as e:
        logger.error(f"TEC Execute queue error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/monitor/start", methods=["POST"])
def api_monitor_start():
    data = request.json or {}
    bid_id = data.get("bid_id")
    interval = int(data.get("interval", 300))
    gem_url = data.get("gem_url", "")
    
    if not bid_id:
        return jsonify({"success": False, "error": "Bid ID is required"}), 400
        
    try:
        from modules.gem_monitor import GeMMonitor
        monitor = GeMMonitor(bid_id, gem_url=gem_url, interval=interval)
        job_id = monitor.start()
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/monitor/stop/<job_id>", methods=["POST"])
def api_monitor_stop(job_id):
    from modules.gem_monitor import MONITOR_JOBS
    monitor = MONITOR_JOBS.get(job_id)
    if monitor:
        monitor.stop()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Job not found"}), 404

@app.route("/api/monitor/status/<job_id>", methods=["GET"])
def api_monitor_status(job_id):
    from modules.gem_monitor import get_monitor_summary
    summary = get_monitor_summary(job_id)
    if summary:
        return jsonify({"success": True, "summary": summary})
    return jsonify({"success": False, "error": "Job not found"}), 404

@app.route("/api/tec/stream/<job_id>", methods=["GET"])
def api_tec_stream(job_id):
    from flask import Response
    from modules.tec_eval import automate_gem
    
    if job_id not in TEC_JOBS:
        return jsonify({"error": "Job ID not found or already processed"}), 404
        
    job_data = TEC_JOBS.pop(job_id) # Remove from memory after starting
    eval_results = job_data["eval_results"]
    gem_url = job_data["gem_url"]
    
    # Return the generator directly to Flask as an Event-Stream response
    return Response(automate_gem(eval_results, url=gem_url, job_id=job_id, use_direct_mode=job_data.get("use_direct_mode", False)), mimetype="text/event-stream")

@app.route("/api/tec/stop", methods=["POST"])
def api_tec_stop():
    data = request.json or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Job ID required"}), 400
    
    from modules.tec_eval import stop_tec_job
    stop_tec_job(job_id)
    return jsonify({"success": True, "message": f"Job {job_id} stop signal sent."})


# ═══════════════════════════════════════════════════════════════════════════════
# BID DOWNLOADER
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/bid/launch-chrome", methods=["POST"])
def api_bid_launch_chrome():
    return jsonify({
        "success": False,
        "error": "Bid Downloader no longer launches Chrome in debug mode. Use Managed Mode (recommended) or Direct Mode if you explicitly started Chrome with --remote-debugging-port=9222."
    }), 400


@app.route("/api/bid/execute", methods=["POST"])
def api_bid_execute():
    data = request.json or {}
    doc_types = data.get("doc_types", []) or []
    download_all = bool(data.get("download_all"))
    gem_url = data.get("gem_url", "")
    use_direct_mode = bool(data.get("use_direct_mode", False))

    def _to_int(v):
        try:
            return int(v)
        except Exception:
            return None

    si_from = _to_int(data.get("si_from")) if data.get("si_from") is not None else None
    si_to = _to_int(data.get("si_to")) if data.get("si_to") is not None else None

    if not download_all and not doc_types:
        return jsonify({"error": "Provide document types or enable Download All."}), 400

    try:
        job_id = str(uuid.uuid4())
        BID_JOBS[job_id] = {
            "gem_url": gem_url,
            "doc_types": doc_types,
            "download_all": download_all,
            "si_from": si_from,
            "si_to": si_to,
            "use_direct_mode": use_direct_mode
        }
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        logger.error(f"Bid Execute queue error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bid/stream/<job_id>", methods=["GET"])
def api_bid_stream(job_id):
    from flask import Response
    from modules.bid_downloader import automate_bid_download

    if job_id not in BID_JOBS:
        return jsonify({"error": "Job ID not found or already processed"}), 404

    job_data = BID_JOBS.pop(job_id)
    return Response(
        automate_bid_download(
            gem_url=job_data.get("gem_url", ""),
            doc_types=job_data.get("doc_types", []),
            download_all=job_data.get("download_all", False),
            si_from=job_data.get("si_from"),
            si_to=job_data.get("si_to"),
            job_id=job_id,
            use_direct_mode=job_data.get("use_direct_mode", False)
        ),
        mimetype="text/event-stream"
    )


@app.route("/api/bid/stop", methods=["POST"])
def api_bid_stop():
    data = request.json or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Job ID required"}), 400

    from modules.bid_downloader import stop_bid_job
    stop_bid_job(job_id)
    return jsonify({"success": True, "message": f"Job {job_id} stop signal sent."})
# ═══════════════════════════════════════════════════════════════════════════════
# BID DOWNLOADER V2 (AGENT-BROWSER)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/bid_v2/execute", methods=["POST"])
def api_bid_v2_execute():
    data = request.json or {}
    doc_types = data.get("doc_types", []) or []
    download_all = bool(data.get("download_all"))
    gem_url = data.get("gem_url", "")
    
    def _to_int(v):
        try: return int(v)
        except Exception: return None

    si_from = _to_int(data.get("si_from"))
    si_to = _to_int(data.get("si_to"))

    if not download_all and not doc_types:
        return jsonify({"error": "Provide document types or enable Download All."}), 400

    try:
        job_id = str(uuid.uuid4())
        BID_JOBS[job_id] = {
            "gem_url": gem_url,
            "doc_types": doc_types,
            "download_all": download_all,
            "si_from": si_from,
            "si_to": si_to,
            "is_v2": True
        }
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        logger.error(f"Bid V2 Execute queue error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/bid_v2/stream/<job_id>", methods=["GET"])
def api_bid_v2_stream(job_id):
    from flask import Response
    from modules.agent_bid_downloader import automate_agent_bid_download
    if job_id not in BID_JOBS:
        return jsonify({"error": "Job ID not found"}), 404

    job_data = BID_JOBS.pop(job_id)
    return Response(
        automate_agent_bid_download(
            gem_url=job_data.get("gem_url", ""),
            doc_types=job_data.get("doc_types", []),
            download_all=job_data.get("download_all", False),
            si_from=job_data.get("si_from"),
            si_to=job_data.get("si_to"),
            job_id=job_id
        ),
        mimetype="text/event-stream"
    )


@app.route("/api/bid_v2/stop", methods=["POST"])
def api_bid_v2_stop():
    data = request.json or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Job ID required"}), 400

    from modules.agent_bid_downloader import stop_agent_bid_job
    stop_agent_bid_job(job_id)
    return jsonify({"success": True, "message": f"Job {job_id} stop signal sent."})

# ═══════════════════════════════════════════════════════════════════════════════
# LLM STATUS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/llm/status", methods=["GET"])
def api_llm_status():
    status = get_llm_status()
    status["network"] = CONFIG.get("network", {})
    llm_config = dict(CONFIG.get("llm", {}))
    llm_config.update(get_prompt_settings())
    # Ensure keys are included for pre-filling but masked if needed (masked by value check in UI usually)
    llm_config["gemini_api_key"] = CONFIG.get("gemini_api_key", "")
    status["llm_config"] = llm_config
    return jsonify(status)


@app.route("/api/llm/config", methods=["POST"])
def api_save_llm_config():
    """Update LLM provider settings in config and prompt templates in SQLite.

    Runtime model selection remains in config.json, but prompt templates now
    live in the main application database so they are no longer persisted in
    the AppData config file.
    """
    import json as _j
    from modules import utils

    d = request.json or {}
    cfg_path = utils.CONFIG_PATH

    prompt_errors = []
    for prompt_key in ["noting_master_prompt", "email_master_prompt", "qa_system_prompt", "summarization_master_prompt", "tec_evaluation_prompt", "quick_analysis_buttons"]:
        if prompt_key in d:
            try:
                val = d[prompt_key] or ""
                set_app_setting(prompt_key, val)
                logger.info(f"Setting updated: {prompt_key} (len: {len(val)})")
            except Exception as e:
                prompt_errors.append(f"{prompt_key}: {e}")

    llm_cfg = utils.CONFIG.setdefault("llm", {})
    config_changed = False
    for key in ["provider", "gemini_model", "groq_model", "temperature", "context_length", "groq_api_key"]:
        if key in d:
            llm_cfg[key] = d[key]
            config_changed = True

    if "gemini_api_key" in d and d["gemini_api_key"].strip():
        utils.CONFIG["gemini_api_key"] = d["gemini_api_key"].strip()
        config_changed = True

    if prompt_errors:
        logger.error(f"Failed to write prompt settings: {'; '.join(prompt_errors)}")
        return jsonify({"error": "; ".join(prompt_errors)}), 500

    if config_changed:
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                _j.dump(utils.CONFIG, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            return jsonify({"error": str(e)}), 500

    return jsonify({"success": True})


@app.route("/api/network/config", methods=["POST"])
def api_save_network_config():
    """Update Network/Proxy settings in user config and reapply immediately."""
    import json as _j
    from modules import utils

    d = request.json or {}
    cfg_path = utils.CONFIG_PATH

    net = utils.CONFIG.setdefault("network", {})
    for key in ["proxy_mode", "proxy_server", "proxy_port", "proxy_username", "proxy_password"]:
        if key in d:
            net[key] = d[key]

    # if the user cleared the server/port we don't want to accidentally leave
    # an empty string; enforce the known default for convenience
    if net.get("proxy_mode") == "manual":
        net.setdefault("proxy_server", "http://10.6.0.9")
        net.setdefault("proxy_port", "3128")

    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            _j.dump(utils.CONFIG, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write network config: {e}")
        return jsonify({"error": str(e)}), 500

    # update current process environment immediately (so requests uses it)
    try:
        from modules.utils import apply_proxy_settings
        apply_proxy_settings()
    except Exception:
        pass

    return jsonify({"success": True})


@app.route("/api/llm/test", methods=["POST"])
def api_llm_test():
    """Send a test prompt to whichever LLM is configured."""
    from modules.utils import ask_llm
    d = request.json or {}
    prompt = d.get("prompt", "Say hello and tell me your model name in one sentence.")
    try:
        answer = ask_llm(prompt)
        status = get_llm_status()
        return jsonify({"success": True, "response": answer, "backend": status["active_backend"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACT TEXT & MODEL PICKER
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/api/ai/models", methods=["GET"])
def api_ai_models():
    from modules.utils import list_available_models
    return jsonify(list_available_models())

# ─── MODULE 11: TEXT EXTRACTION ───
@app.route("/api/extract/text", methods=["POST"])
def api_extract_text():
    method = request.form.get("method", "standard")
    image_base64 = request.form.get("image_base64")
    
    if "file" not in request.files and not image_base64:
        return jsonify({"success": False, "error": "No file or image data provided"}), 400
    
    job_id = str(uuid.uuid4())
    with _extraction_lock:
        _extraction_jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run_extraction(jid, file_data=None, img_data=None, mthd="standard", fname=None):
        try:
            from modules.extract import extract_text_from_file
            if file_data:
                temp_path = DATA_ROOT / "temp" / (fname or "upload.pdf")
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_path, "wb") as f:
                    f.write(file_data)
                res = extract_text_from_file(file_path=temp_path, method=mthd)
                if temp_path.exists(): temp_path.unlink()
            else:
                res = extract_text_from_file(image_bytes=img_data, method=mthd)
            
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "complete"
                _extraction_jobs[jid]["result"] = res
        except Exception as e:
            logger.error(f"Async Extraction Error: {e}")
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "failed"
                _extraction_jobs[jid]["error"] = str(e)

    if "file" in request.files:
        f = request.files["file"]
        threading.Thread(target=_run_extraction, args=(job_id, f.read(), None, method, f.filename)).start()
    else:
        if image_base64 and "base64," in image_base64:
            image_base64 = image_base64.split("base64,")[1]
        img_bytes = base64.b64decode(image_base64)
        threading.Thread(target=_run_extraction, args=(job_id, None, img_bytes, method)).start()

    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/extract/status/<job_id>", methods=["GET"])
def api_extract_status(job_id):
    with _extraction_lock:
        if job_id not in _extraction_jobs:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(_extraction_jobs[job_id])

@app.route("/api/extract/smart-process", methods=["POST"])
def api_extract_smart_process():
    """Handles AI-powered analysis/summarization of extracted text with caching, asynchronously."""
    data = request.json or {}
    text = data.get("text", "")
    context = data.get("context", "")
    file_hash = data.get("file_hash")
    
    if not text.strip():
        return jsonify({"success": False, "error": "No text provided for analysis"}), 400
    
    job_id = str(uuid.uuid4())
    with _extraction_lock:
        _extraction_jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run_smart_process(jid, txt, ctx, hsh):
        try:
            from modules.extract import analyze_extracted_content
            result = analyze_extracted_content(txt, ctx, file_hash=hsh)
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "complete"
                _extraction_jobs[jid]["result"] = {"processed_text": result}
        except Exception as e:
            logger.error(f"Async Smart Process Error: {e}")
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "failed"
                _extraction_jobs[jid]["error"] = str(e)

    threading.Thread(target=_run_smart_process, args=(job_id, text, context, file_hash)).start()
    
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/extract/direct-analyze", methods=["POST"])
def api_extract_direct_analyze():
    """Combines extraction and analysis in a single background job."""
    method = request.form.get("method", "vision")
    context = request.form.get("context", "")
    image_base64 = request.form.get("image_base64")
    
    if "file" not in request.files and not image_base64:
        return jsonify({"success": False, "error": "No file or image provided"}), 400
        
    job_id = str(uuid.uuid4())
    with _extraction_lock:
        _extraction_jobs[job_id] = {"status": "running", "result": None, "error": None}
        
    def _run_direct_job(jid, file_data=None, img_data=None, ctx="", mthd="vision", fname=None):
        try:
            from modules.extract import analyze_file_directly
            if file_data:
                temp_path = DATA_ROOT / "temp" / (fname or "direct_upload.pdf")
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_path, "wb") as f:
                    f.write(file_data)
                result = analyze_file_directly(file_path=temp_path, context=ctx, method=mthd)
                if temp_path.exists(): temp_path.unlink()
            else:
                result = analyze_file_directly(image_bytes=img_data, context=ctx, method=mthd)
            
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "complete"
                _extraction_jobs[jid]["result"] = {"processed_text": result, "direct": True}
        except Exception as e:
            logger.error(f"Direct Analysis Job Error: {e}")
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "failed"
                _extraction_jobs[jid]["error"] = str(e)
                
    if "file" in request.files:
        f = request.files["file"]
        threading.Thread(target=_run_direct_job, args=(job_id, f.read(), None, context, method, f.filename)).start()
    else:
        if image_base64 and "base64," in image_base64:
            image_base64 = image_base64.split("base64,")[1]
        img_bytes = base64.b64decode(image_base64)
        threading.Thread(target=_run_direct_job, args=(job_id, None, img_bytes, context, method)).start()
        
    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/extract/download", methods=["POST"])
def api_extract_download():
    data = request.json or {}
    html = data.get("html", "")
    
    if not html:
        return jsonify({"success": False, "error": "No content to download"}), 400

    output = io.BytesIO()
    from modules.extract import generate_docx_from_html
    generate_docx_from_html(html, output)
    output.seek(0)
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=f"Extracted_Text_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    )

@app.route("/api/extract/download-to-desktop", methods=["POST"])
def api_extract_download_to_desktop():
    """Generates DOCX and saves it directly to the user's Desktop."""
    data = request.json or {}
    html = data.get("html", "")
    filename = data.get("filename", f"Extracted_Text_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    
    if not html:
        return jsonify({"success": False, "error": "No content to save"}), 400

    try:
        # Resolve Desktop path
        desktop_path = Path(os.path.join(os.path.expanduser("~"), "Desktop"))
        if not desktop_path.exists():
            # Fallback if Desktop doesn't exist (unlikely on Windows but safe)
            desktop_path = Path.home()
            
        target_file = desktop_path / f"{filename}.docx"
        
        # Save to file
        with open(target_file, "wb") as f:
            output = io.BytesIO()
            from modules.extract import generate_docx_from_html
            generate_docx_from_html(html, output)
            f.write(output.getvalue())
            
        # Open folder (Windows specific logic)
        try:
            if os.name == 'nt':
                os.startfile(str(desktop_path))
            else:
                # Basic support for other OS if needed
                import subprocess
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, str(desktop_path)])
        except Exception as oe:
            logger.warning(f"Could not open folder: {oe}")

        return jsonify({
            "success": True, 
            "message": f"Saved to Desktop: {target_file.name}",
            "path": str(target_file),
            "folder": str(desktop_path)
        })
    except Exception as e:
        logger.error(f"Failed to save to desktop: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/documents/process-zip-multi", methods=["POST"])
def api_process_zip_multi():
    if "zips" not in request.files:
        return jsonify({"success": False, "error": "No files uploaded"}), 400
    
    files = request.files.getlist("zips")
    temp_paths = []
    temp_dir = DATA_ROOT / "temp_zips"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    for f in files:
        if f.filename.endswith(".zip"):
            p = temp_dir / f.filename
            f.save(str(p))
            temp_paths.append(p)

    try:
        # Output to Desktop/GeM Bids
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            onedrive = Path.home() / "OneDrive" / "Desktop"
            if onedrive.exists(): desktop = onedrive
        target_dir = desktop / "GeM Bids"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        res = process_zip_bid_multi(temp_paths, target_dir)
        
        # Auto-ingest into KB if possible
        for gen_file in res.get("generated_files", []):
            full_p = target_dir / gen_file
            from modules.rag_engine import ingest_document_async
            ingest_document_async(str(full_p), category="Bid Document")
            
        return jsonify({"success": True, **res})
    finally:
        for p in temp_paths: 
            if p.exists(): p.unlink()


if __name__ == "__main__":
    cfg = CONFIG["dashboard"]
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 5000)
    debug = cfg.get("debug", False)

    # Force skip 5006 if it's known to be blocked by a protected PID
    final_port = find_free_port(port)
    if final_port == 5006:
        logger.warning("Port 5006 detected as busy by ghost process. Shifting to 5007...")
        final_port = find_free_port(5007)
    
    print("\n" + "="*60)
    print(f"  SMART BOT DASHBOARD (v{VERSION})")
    print(f"  URL: http://{host}:{final_port}")
    if final_port != port:
        print(f"  (Note: Shifted from {port} due to busy port)")
    print("="*60 + "\n")

    try:
        if debug:
            # Development mode
            app.run(host=host, port=final_port, debug=True)
        else:
            # Production mode using Waitress
            from waitress import serve
            print(f"Serving with Waitress on http://{host}:{final_port}")
            serve(app, host=host, port=final_port)
    except Exception as e:
        logger.error(f"Failed to start on port {final_port}: {e}")
        # Final emergency fallback
        emergency_port = find_free_port(final_port + 1)
        logger.warning(f"Emergency shifting to {emergency_port}...")
        if debug:
            app.run(host=host, port=emergency_port, debug=True)
        else:
            from waitress import serve
            serve(app, host=host, port=emergency_port)
