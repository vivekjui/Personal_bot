"""
Noting Bot - Core Utilities
Common helper functions used across all modules.
"""

import os
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import sys

# ─── Path Resolution (PyInstaller Support) ─────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as a bundled executable
    BUNDLE_ROOT = Path(sys._MEIPASS)
else:
    # Running in a normal Python environment
    BUNDLE_ROOT = Path(__file__).parent.parent

# Persistent Data Storage (User Settings, Database, Logs)
if os.name == 'nt':
    # Windows: %APPDATA%/APMD_Bot
    DATA_ROOT = Path(os.environ.get('APPDATA', str(Path.home()))) / "APMD_Bot"
else:
    # Linux/Mac: ~/.apmd_bot
    DATA_ROOT = Path.home() / ".apmd_bot"

DATA_ROOT.mkdir(parents=True, exist_ok=True)
BOT_ROOT = DATA_ROOT # For backward compatibility in other modules

CONFIG_PATH = DATA_ROOT / "config.json"


def ensure_bundle_resource(rel_path: str) -> Path:
    """
    Copy a bundled file or directory into user-writable app storage on first run.
    Returns the writable target path.
    """
    import shutil

    source = BUNDLE_ROOT / rel_path
    target = DATA_ROOT / rel_path

    if target.exists():
        return target

    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)

    return target


STANDARD_LIBRARY_PATH = ensure_bundle_resource("standard_library.json")
PROCUREMENT_STAGES_PATH = ensure_bundle_resource("procurement_stages.json")
DEFAULT_KB_DIR = ensure_bundle_resource("knowledge_base")

# ─── Logging Setup ─────────────────────────────────────────────────────────────
# Pre-define a basic logger for use during config loading
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Noting_Bot")

DEFAULT_NOTING_MASTER_PROMPT = """You are an expert assistant for Indian government procurement files.
Draft an official noting in Hindi by default.
Convert Hinglish into proper official Hindi.
If any sentence is in English, convert it to Hindi unless the source content must stay as-is.
Use the available reference context and writing style examples when helpful.

Additional Context:
{additional_context}

Reference Context:
{rag_context}

Preferred Style Examples:
{user_style_examples}

Return only the final noting text.
"""

DEFAULT_QA_SYSTEM_PROMPT = """You are an intelligent Assistant for the APMD (Administrative & Procurement Management Department).
Your task is to answer the user's question using the provided Knowledge Base context.

ANSWER PATTERN (strictly follow this order):
1. GFR 2017: Relevant clause and description (if found in context).
2. Manual for Procurement of Goods: Relevant clause and description (if found in context).
3. GeM ATC (Additional Terms & Conditions): Relevant clause and description (if found in context).
4. GSI Manual: Relevant clause and description (if found in context).
5. Web Search Result / Supplemental Info: Provide relevant external or supplemental info.
6. Advisory: Provide a practical advisory or recommendation for the user.

CONSTRAINTS:
- No Assumptions: If a clause is not found in the context for a specific category, state "Not found in provided documents" for that category.
- Accuracy: Use only the provided context.
- Tone: Formal and official.

{learning_context}

=== KNOWLEDGE BASE CONTEXT ===
{context}
==============================

User Question: {prompt}

Provide a helpful, precise answer following the pattern above.
"""

def load_config() -> dict:
    """Load the main config.json file. If missing in DATA_ROOT, copy from BUNDLE_ROOT.

    When a configuration is first created or read, ensure the network/proxy
    section contains sane defaults.  The application should always attempt to
    route traffic through the corporate proxy at 10.6.0.9:3128 unless the user
    deliberately turns the proxy off.  This function will insert those defaults
    and persist them back to disk if necessary.
    """
    import shutil
    wrote_back = False

    if not CONFIG_PATH.exists():
        bundle_config = BUNDLE_ROOT / "config.json"
        if bundle_config.exists():
            shutil.copy2(bundle_config, CONFIG_PATH)
        else:
            # Fallback for fresh initial install
            return {"dashboard": {"host": "127.0.0.1", "port": 5000, "debug": False}, "paths": {"logs": "logs"}}
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # ensure network defaults for proxy
    net = cfg.setdefault("network", {})
    # corporate proxy defaults
    DEFAULT_PROXY_SERVER = "http://10.6.0.9"
    DEFAULT_PROXY_PORT = "3128"

    # if the existing configuration explicitly disabled proxy but there is
    # no server/port defined, treat this as an uninitialised state and fall
    # back to our default manual proxy.  This prevents the situation where
    # the file imported from AppData had "off" carried over from an old
    # install and the bot therefore never used the proxy.
    if (net.get("proxy_mode") == "off" and
        not net.get("proxy_server") and
        not net.get("proxy_port")):
        net["proxy_mode"] = "manual"
        net["proxy_server"] = DEFAULT_PROXY_SERVER
        net["proxy_port"] = DEFAULT_PROXY_PORT
        wrote_back = True

    # if no mode provided at all assume manual
    if not net.get("proxy_mode"):
        net["proxy_mode"] = "manual"
        wrote_back = True
    # if mode is manual ensure server/port exist
    if net.get("proxy_mode") == "manual":
        if not net.get("proxy_server"):
            net["proxy_server"] = DEFAULT_PROXY_SERVER
            wrote_back = True
        if not net.get("proxy_port"):
            net["proxy_port"] = DEFAULT_PROXY_PORT
            wrote_back = True
    # fill out username/password keys if missing
    for key in ("proxy_username", "proxy_password"):
        if key not in net:
            net[key] = ""
            wrote_back = True

    # ensure default LLM settings (Gemma 3 27B provider)
    llm = cfg.setdefault("llm", {})
    if not llm.get("provider"):
        llm["provider"] = "gemma3_27b"
        wrote_back = True
    # model fallback and other parameters
    if "gemini_model" not in llm:
        llm["gemini_model"] = "gemini-2.5-flash"
        wrote_back = True
    if "temperature" not in llm:
        llm["temperature"] = 0.3
        wrote_back = True
    if "context_length" not in llm:
        llm["context_length"] = 8192
        wrote_back = True
    if "timeout_seconds" not in llm:
        llm["timeout_seconds"] = 120
        wrote_back = True
    if "noting_master_prompt" not in llm:
        llm["noting_master_prompt"] = DEFAULT_NOTING_MASTER_PROMPT
        wrote_back = True
    if "qa_system_prompt" not in llm:
        llm["qa_system_prompt"] = DEFAULT_QA_SYSTEM_PROMPT
        wrote_back = True

    if wrote_back:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as outf:
                json.dump(cfg, outf, indent=2)
        except Exception:
            # if writing fails just ignore; we still use values in memory
            pass
    
    # ── Path Sanitization (Portability) ────────────────────────────────────────
    # If paths are hardcoded to a different machine/folder, re-base them to DATA_ROOT
    if "paths" in cfg:
        for key, val in cfg["paths"].items():
            if isinstance(val, str):
                p = Path(val)
                # If path is absolute and doesn't exist, OR it contains the old dev folder name
                if (p.is_absolute() and not p.exists()) or "APMD_eOffice_Bot" in val:
                    basename = p.name
                    if key == "database":
                        cfg["paths"][key] = str(DATA_ROOT / "cases.db")
                    elif key == "logs":
                        cfg["paths"][key] = str(DATA_ROOT / "logs")
                    else:
                        cfg["paths"][key] = str(DATA_ROOT / basename)
    
    if "rag" in cfg and "kb_dir" in cfg["rag"]:
        val = cfg["rag"]["kb_dir"]
        p = Path(val)
        if (p.is_absolute() and not p.exists()) or "APMD_eOffice_Bot" in val:
            cfg["rag"]["kb_dir"] = str(DATA_ROOT / "knowledge_base")

    # ── Database Migration ─────────────────────────────────────────────────────
    if "paths" in cfg and "database" in cfg["paths"]:
        import shutil
        db_path = Path(cfg["paths"]["database"])
        if not db_path.exists():
            # Try to find it in BUNDLE_ROOT (legacy location)
            legacy_db = BUNDLE_ROOT / "cases.db"
            if legacy_db.exists():
                db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_db, db_path)
                logger.info(f"Migrated database from legacy location to {db_path}")

    return cfg

CONFIG = load_config()

def apply_proxy_settings():
    """Apply proxy settings from config to the process environment."""
    net = CONFIG.get("network", {})
    mode = net.get("proxy_mode", "off")
    
    if mode == "off":
        # Force clear proxy env vars
        keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
        for k in keys:
            if k in os.environ:
                del os.environ[k]
        logger.debug("Proxy: Off")
    elif mode == "manual":
        server = net.get("proxy_server", "").strip()
        port = net.get("proxy_port", "").strip()
        user = net.get("proxy_username", "").strip()
        pw = net.get("proxy_password", "").strip()
        
        if server and port:
            # strip any scheme from server, we'll add http:// ourselves
            if server.startswith("http://"):
                server_clean = server[len("http://"):]
            elif server.startswith("https://"):
                server_clean = server[len("https://"):]
            else:
                server_clean = server

            # Construct proxy URL
            if user and pw:
                proxy_url = f"http://{user}:{pw}@{server_clean}:{port}"
            else:
                proxy_url = f"http://{server_clean}:{port}"
            
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["http_proxy"] = proxy_url
            os.environ["https_proxy"] = proxy_url
            logger.info(f"Proxy: Manual ({server_clean}:{port})")
    elif mode == "system":
        # Python's requests/httpx/urllib typically auto-detect system proxy
        # if environment variables are NOT set.
        keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
        for k in keys:
            if k in os.environ:
                del os.environ[k]
        logger.info("Proxy: System (Auto-detecting)")

# Apply settings immediately on import
apply_proxy_settings()


def get_requests_proxies():
    """Return a proxy dictionary suitable for ``requests`` or similar libraries.

    The values are derived from the current configuration; if manual mode is
    active the returned dict will include both http and https entries pointing
    at the configured server/port.  An empty dict is returned if no proxy is
    required.
    """
    net = CONFIG.get("network", {})
    if net.get("proxy_mode") == "manual":
        server = net.get("proxy_server", "").strip()
        port = net.get("proxy_port", "").strip()
        if server and port:
            # strip any leading scheme
            if server.startswith("http://"):
                server_clean = server[len("http://"):]
            elif server.startswith("https://"):
                server_clean = server[len("https://"):]
            else:
                server_clean = server
            proxy_url = f"http://{server_clean}:{port}"
            return {"http": proxy_url, "https": proxy_url}
    return {}


def test_proxy_connection(url: str = "http://httpbin.org/ip", timeout: int = 5) -> dict:
    """Quickly verify that the proxy is being used by doing a simple HTTP call.

    Returns a dictionary containing the response JSON (if any) along with the
    proxy settings that were applied.  Errors are raised as exceptions so the
    caller can handle connectivity problems.
    """
    import requests
    proxies = get_requests_proxies()
    resp = requests.get(url, timeout=timeout, proxies=proxies)
    return {"status_code": resp.status_code, "json": resp.json(), "proxies": proxies}

# ─── Final Logging Configuration (with File Handler) ───────────────────────────
log_dir = Path(CONFIG["paths"]["logs"])
log_dir.mkdir(parents=True, exist_ok=True)

for handler in logger.handlers[:]:
    logger.removeHandler(handler)

file_handler = logging.FileHandler(log_dir / "bot_activity.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())


# ─── Folder Utilities ──────────────────────────────────────────────────────────
def get_case_folder(case_id: str, subfolder: str = "") -> Path:
    """Return the path for a specific case folder, creating it if needed."""
    cases_dir = Path(CONFIG["paths"]["cases_dir"])
    case_path = cases_dir / case_id
    if subfolder:
        case_path = case_path / subfolder
    case_path.mkdir(parents=True, exist_ok=True)
    return case_path


def ensure_folder(path: str) -> Path:
    """Ensure a folder exists, creating it if necessary."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ─── Text & Date Utilities ─────────────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def today_str() -> str:
    """Return today's date in DD-MM-YYYY format."""
    return datetime.now().strftime("%d-%m-%Y")


def parse_date_flexible(date_str: str):
    """
    Try to parse a date string in multiple common Indian formats.
    Returns a datetime object or None if parsing fails.
    """
    from dateutil import parser as date_parser
    try:
        return date_parser.parse(date_str, dayfirst=True)
    except Exception:
        return None


def days_until(target_date) -> int:
    """Return number of days from today until target_date."""
    if isinstance(target_date, str):
        target_date = parse_date_flexible(target_date)
    if target_date is None:
        return None
    delta = target_date.date() - datetime.now().date()
    return delta.days


# ─── LLM Router (Ollama primary → Gemini fallback) ────────────────────────────
# Ollama runs locally via `ollama run phi4-mini` (no API key needed).
# Gemini is used as fallback if Ollama is not running or not configured.

# --- Local Ollama Integration Removed ---


def _ask_gemini_direct(prompt: str, override_model: str = None) -> str:
    """Send prompt to Google Gemini API (or Gemma via API) and return response text."""
    import google.generativeai as genai
    api_key = CONFIG.get("gemini_api_key", "")
    if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
        raise ValueError("Gemini API key not configured in config.json.")
    genai.configure(api_key=api_key)
    
    # Use override if provided (e.g. gemma3_27b), else use configured fallback model
    model_name = override_model if override_model else CONFIG.get("llm", {}).get("gemini_model", "gemini-1.5-flash")
    
    # Map internal identifier to correct Google API model name
    if model_name == "gemma3_27b":
        model_name = "models/gemma-3-27b-it"
        
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text.strip()


def ask_llm(prompt: str, context: str = "") -> str:
    """
    Universal LLM call with customizable priority chains based on config.
    """
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    llm_cfg     = CONFIG.get("llm", {})
    provider    = llm_cfg.get("provider", "auto")

    def try_gemini():
        logger.debug("LLM: trying Gemini")
        return _ask_gemini_direct(full_prompt, override_model=llm_cfg.get("gemini_model", "gemini-2.5-flash"))

    def try_gemma():
        logger.debug("LLM: trying Gemma 3")
        return _ask_gemini_direct(full_prompt, override_model="gemma3_27b")
        
    if provider == "gemma3_27b":
        try: return try_gemma()
        except Exception as e1:
            logger.warning(f"Gemma 3 failed ({e1}). Falling back to Gemini.")
            try: return try_gemini()
            except Exception as e2:
                return f"[AI Error: Gemini backends failed]"
    else: # "gemini" or "auto"
        try: return try_gemini()
        except Exception as e1:
            try: return try_gemma()
            except Exception as e2:
                return f"[AI Error: Gemini backends failed]"


# Backward-compatible alias — all existing modules call ask_gemini(); no changes needed.
def ask_gemini(prompt: str, context: str = "") -> str:
    return ask_llm(prompt, context)


def get_llm_status() -> dict:
    """Check which LLM backends are currently available."""
    llm_cfg  = CONFIG.get("llm", {})

    gemini_ok = bool(
        CONFIG.get("gemini_api_key", "") and
        CONFIG.get("gemini_api_key") != "YOUR_GEMINI_API_KEY_HERE"
    )

    return {
        "provider":       llm_cfg.get("provider", "gemini"),
        "gemini_key_set": gemini_ok,
        "active_backend": (
            "Gemma-3 27B" if llm_cfg.get("provider") == "gemma3_27b" else
            "Gemini (Cloud)"
        )
    }


# ─── PDF Text Extraction with Vision Fallback ────────────────────────────────
def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text from a PDF. 
    If it's a scanned PDF (yields < 50 chars of text), falls back to 
    LLM Vision (Gemini) to OCR the pages.
    """
    import pdfplumber
    text_parts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
    except Exception as e:
        logger.error(f"Standard PDF extraction failed for {pdf_path}: {e}")

    extracted_text = "\n".join(text_parts).strip()

    # If we got meaningful text, return it
    if len(extracted_text) > 50:
        return extracted_text

    # Otherwise, attempt LLM Vision Fallback
    logger.info(f"PDF missing text layer (likely scanned): {pdf_path}. Attempting Vision Fallback...")
    vision_text = []

    # Check if Gemini key is available (we need vision capabilities)
    gemini_key = CONFIG.get("gemini_api_key", "")
    if not gemini_key or gemini_key == "YOUR_GEMINI_API_KEY_HERE":
        logger.warning("No Gemini API key found for Vision OCR fallback.")
        return extracted_text  # return whatever little we got

    try:
        import fitz  # PyMuPDF
        import google.generativeai as genai
        import tempfile
        from PIL import Image

        genai.configure(api_key=gemini_key)
        # Use a model that supports multimodal/vision
        model = genai.GenerativeModel("gemini-2.5-flash")

        doc = fitz.open(pdf_path)
        for i in range(len(doc)):
            page = doc.load_page(i)
            # Render page to image (scale 2x for better OCR resolution)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            
            pix.save(tmp_path)
            
            try:
                img = Image.open(tmp_path)
                logger.info(f"  [Vision OCR] Processing page {i+1}...")
                response = model.generate_content([
                    "Extract all the readable text from this document image exactly as it appears. Do not add any conversational filler.",
                    img
                ])
                if response.text:
                    vision_text.append(response.text)
            except Exception as e:
                logger.error(f"Vision OCR failed on page {i+1}: {e}")
            finally:
                import os
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        doc.close()

    except ImportError:
        logger.warning("PyMuPDF (fitz) or google-generativeai not installed. Cannot perform Vision OCR.")
    except Exception as e:
        logger.error(f"Vision Fallback failed entirely: {e}")

    if vision_text:
        return "\n".join(vision_text)

    return extracted_text


# ─── DOCX Utilities ────────────────────────────────────────────────────────────
def fill_docx_template(template_path: str, replacements: dict, output_path: str) -> str:
    """
    Fill a .docx template by replacing placeholder text with actual values.
    Placeholders in template should be in format: {{PLACEHOLDER_NAME}}
    Returns the output path on success.
    """
    from docx import Document
    doc = Document(template_path)

    def replace_in_paragraph(para):
        for key, value in replacements.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in para.text:
                for run in para.runs:
                    if placeholder in run.text:
                        run.text = run.text.replace(placeholder, str(value))

    for para in doc.paragraphs:
        replace_in_paragraph(para)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    replace_in_paragraph(para)

    doc.save(output_path)
    logger.info(f"Document saved: {output_path}")
    return output_path


def create_docx_from_text(content: str, output_path: str, title: str = "") -> str:
    """
    Create a new .docx file from plain text / markdown-like content.
    Returns the output path.
    """
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Title
    if title:
        heading = doc.add_heading(title, level=0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Content lines
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("- ") or line.startswith("* "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line == "":
            doc.add_paragraph("")
        else:
            doc.add_paragraph(line)

    doc.save(output_path)
    logger.info(f"Document created: {output_path}")
    return output_path


def find_free_port(start_port: int, max_tries: int = 10) -> int:
    """Find an available port starting from start_port."""
    import socket
    port = start_port
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
        port += 1
    return start_port

def launch_chrome_debug(port: int = 9222) -> bool:
    """
    Check if Chrome is already in debug mode. 
    If not, attempt to find and launch it.
    """
    import subprocess
    import socket
    import os
    
    # 1. Check if already active
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        if s.connect_ex(('127.0.0.1', port)) == 0:
            logger.info(f"Chrome debug session detected on port {port}")
            return True

    # 2. Identify Chrome Path
    paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
    chrome_path = next((p for p in paths if os.path.exists(p)), None)
    
    if not chrome_path:
        logger.error("Google Chrome installation not found.")
        return False
        
    try:
        # 3. Launch with remote debugging
        logger.info(f"Launching outside Chrome in debug mode: {chrome_path}")
        subprocess.Popen([
            chrome_path, 
            f"--remote-debugging-port={port}",
            "--user-data-dir=" + str(DATA_ROOT / "ChromeAutomator"),
            "--no-first-run"
        ])
        return True
    except Exception as e:
        logger.error(f"Failed to launch Chrome: {e}")
        return False

# ─── Other Utilities ───────────────────────────────────────────────────────────
