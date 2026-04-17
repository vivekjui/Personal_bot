"""
Noting Bot - Database Manager
Handles all SQLite database operations for case registry, EMD tracking, and reminders.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from modules.utils import (
    CONFIG,
    DATA_ROOT,
    DEFAULT_EMAIL_MASTER_PROMPT,
    DEFAULT_NOTING_MASTER_PROMPT,
    DEFAULT_QA_SYSTEM_PROMPT,
    DEFAULT_SUMMARIZATION_MASTER_PROMPT,
    LEGACY_LLM_PROMPT_VALUES,
    logger,
)
import threading
_db_init_lock = threading.Lock()

LEGACY_DB_PATH = Path(CONFIG.get("paths", {}).get("database") or str(DATA_ROOT / "cases.db"))


def _resolve_db_root() -> Path:
    parent = LEGACY_DB_PATH.parent
    if parent.name.lower() == "db":
        root = parent
    else:
        root = parent / "db"
    root.mkdir(parents=True, exist_ok=True)
    return root


DB_ROOT = _resolve_db_root()
# Modular DB paths for different functional areas
DB_PATHS = {
    "core": str(DB_ROOT / "core.db"),
    "noting": str(DB_ROOT / "noting.db"),
    "qa": str(DB_ROOT / "qa.db"),
}
# Legacy path for migration/fallback
LEGACY_MASTER_DB = str(DB_ROOT / "cases.db")
DB_TABLES = {
    "core": [
        "cases",
        "security_deposits",
        "reminders",
        "documents",
        "bills",
        "email_log",
    ],
    "noting": [
        "noting_history",
        "noting_learning_patterns",
        "procurement_stages",
        "noting_library",
        "email_categories",
        "email_library",
        "app_settings",
    ],
    "qa": [
        "know_how_history",
        "qa_feedback",
    ],
}
PROMPT_SETTINGS_DEFAULTS = {
    "noting_master_prompt": DEFAULT_NOTING_MASTER_PROMPT,
    "email_master_prompt": DEFAULT_EMAIL_MASTER_PROMPT,
    "qa_system_prompt": DEFAULT_QA_SYSTEM_PROMPT,
    "summarization_master_prompt": DEFAULT_SUMMARIZATION_MASTER_PROMPT,
    "quick_analysis_buttons": json.dumps([
        {"id": "mom", "label": "📝 MOM Summary", "prompt": "Summarize this Minute of Meeting (MOM) highlighting key action items, owners, and deadlines."},
        {"id": "tech", "label": "📊 Tech Eval Audit", "prompt": "Analyze this Technical Evaluation Report. List all firms/vendors and clearly state their Qualification or Disqualification status with brief reasons."},
        {"id": "simple", "label": "💡 Simple Recap", "prompt": "Explain this document in simple terms and list the top 5 most important points."},
        {"id": "entity", "label": "🔍 Entity Extraction", "prompt": "Extract all names of individuals, organizations, and specific monetary amounts mentioned."}
    ])
}


def get_connection(domain: str = "core"):
    """Return a SQLite connection with row_factory for dict-like access."""
    db_path = DB_PATHS.get(domain, DB_PATHS["core"])
    
    # Production optimization: increased timeout for concurrent access
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Performance: Enable Write-Ahead Logging for better concurrent performance
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    
    # Memory optimization: Use mmap for faster reads if OS supports it
    conn.execute("PRAGMA mmap_size = 268435456") # 256MB
    conn.execute("PRAGMA cache_size = -10000")    # 10MB approx
    
    return conn


def _table_columns(conn, table_name: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _create_core_schema() -> None:
    conn = get_connection("core")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            description     TEXT,
            estimated_cost  REAL,
            department      TEXT,
            status          TEXT DEFAULT 'Active',
            portal          TEXT,
            portal_url      TEXT,
            nit_no          TEXT,
            nit_date        TEXT,
            bid_due_date    TEXT,
            work_order_date TEXT,
            completion_date TEXT,
            dlp_end_date    TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            updated_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_deposits (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT NOT NULL,
            type            TEXT NOT NULL CHECK(type IN ('EMD','Performance Security')),
            contractor_name TEXT,
            instrument_type TEXT,
            instrument_no   TEXT,
            bank_name       TEXT,
            amount          REAL,
            validity_date   TEXT,
            status          TEXT DEFAULT 'Active',
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT,
            title           TEXT NOT NULL,
            description     TEXT,
            event_date      TEXT NOT NULL,
            alert_days_before INTEGER DEFAULT 7,
            repeat_days     INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'Active',
            last_alerted    TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT NOT NULL,
            doc_type        TEXT,
            filename        TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            source_url      TEXT,
            uploaded_at     TEXT DEFAULT (datetime('now','localtime')),
            notes           TEXT,
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT NOT NULL,
            bill_no         TEXT,
            bill_date       TEXT,
            contractor_name TEXT,
            gross_amount    REAL,
            net_amount      REAL,
            deductions      REAL DEFAULT 0,
            status          TEXT DEFAULT 'Under Scrutiny',
            remarks         TEXT,
            generated_docs  TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT,
            subject         TEXT,
            sender          TEXT,
            received_at     TEXT,
            summary         TEXT,
            is_urgent       INTEGER DEFAULT 0,
            read_status     TEXT DEFAULT 'Unread'
        )
    """)

    # Performance Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_deposits_case ON security_deposits(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_case ON reminders(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bills_case ON bills(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_log_case ON email_log(case_id)")

    conn.commit()
    conn.close()


def _create_noting_schema() -> None:
    conn = get_connection("noting")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS noting_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT DEFAULT 'General',
            noting_type     TEXT,
            content         TEXT,
            ai_content      TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    noting_columns = _table_columns(conn, "noting_history")
    if "ai_content" not in noting_columns:
        conn.execute("ALTER TABLE noting_history ADD COLUMN ai_content TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS noting_learning_patterns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_phrase       TEXT NOT NULL,
            preferred_phrase    TEXT NOT NULL,
            use_count           INTEGER DEFAULT 1,
            case_id             TEXT DEFAULT 'General',
            noting_type         TEXT DEFAULT 'Noting',
            created_at          TEXT DEFAULT (datetime('now','localtime')),
            updated_at          TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(source_phrase, preferred_phrase)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procurement_stages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL UNIQUE,
            seq     INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS noting_library (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stage       TEXT,
            keyword     TEXT NOT NULL,
            content     TEXT NOT NULL,
            is_custom   INTEGER DEFAULT 1,
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_categories (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL UNIQUE,
            seq     INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_library (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category    TEXT,
            keyword     TEXT NOT NULL,
            content     TEXT NOT NULL,
            is_custom   INTEGER DEFAULT 1,
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key             TEXT PRIMARY KEY,
            value           TEXT NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # Performance Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_noting_history_case ON noting_history(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_noting_library_stage ON noting_library(stage)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_library_cat ON email_library(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_noting_learning_patterns_case ON noting_learning_patterns(case_id)")

    conn.commit()
    conn.close()


def _create_qa_schema() -> None:
    conn = get_connection("qa")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS know_how_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            question        TEXT NOT NULL,
            answer          TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qa_feedback (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            question        TEXT NOT NULL,
            answer          TEXT NOT NULL,
            feedback        TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.commit()
    conn.close()





def _seed_noting_defaults() -> None:
    conn = get_connection("noting")
    cursor = conn.cursor()

    for key, default_value in PROMPT_SETTINGS_DEFAULTS.items():
        # Force restore if noting_master_prompt is blank or messed up (user requested)
        existing = get_app_setting(key)
        if not existing or (key == "noting_master_prompt" and len(existing.strip()) < 50):
            migrated_value = LEGACY_LLM_PROMPT_VALUES.get(key) or default_value
            set_app_setting(key, migrated_value)
            logger.info(f"Seed: Restored/Seeded {key}")
        else:
            # insertion already exists and looks valid
            pass

    cursor.execute("SELECT COUNT(*) FROM procurement_stages")
    if cursor.fetchone()[0] == 0:
        default_stages = [
            "Indent received", "custom bid approval", "bid preparation", "bid vetting",
            "bid publication approval", "bid publication", "bid end date extension",
            "Technical bid opening", "TEC (T) evaluation", "Representation",
            "Review TEC", "Price bid opening", "Budget confirmation",
            "Contract award", "DP (Delivery period) Extension",
            "Performance security", "Bill"
        ]
        for i, s in enumerate(default_stages):
            cursor.execute("INSERT INTO procurement_stages (name, seq) VALUES (?, ?)", (s, i))

    cursor.execute("SELECT COUNT(*) FROM email_categories")
    if cursor.fetchone()[0] == 0:
        for s in ["General", "Procurement", "Technical", "Finance"]:
            cursor.execute("INSERT INTO email_categories (name) VALUES (?)", (s,))

    cursor.execute("SELECT COUNT(*) FROM noting_library")
    if cursor.fetchone()[0] == 0:
        from modules.utils import BUNDLE_ROOT
        json_path = BUNDLE_ROOT / "standard_library.json"
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        stage = item.get("stage", "General")
                        keyword = item.get("keyword", "Uncategorized")
                        content = item.get("text", "")
                        is_custom = 1 if item.get("is_custom") else 0
                        updated_at = item.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute("""
                            INSERT INTO noting_library (stage, keyword, content, is_custom, updated_at)
                            VALUES (?, ?, ?, ?, ?)
                        """, (stage, keyword, content, is_custom, updated_at))
                    logger.info(f"Seed: Imported {len(data)} items into noting_library from JSON.")
            except Exception as e:
                logger.error(f"Failed to seed noting_library: {e}")

    conn.commit()
    conn.close()


def initialize_database():
    """Create all required tables if they don't exist."""
    with _db_init_lock:
        # Check if already initialized to avoid redundant schema creation/migration
        conn = get_connection("core")
        try:
            if _table_exists(conn, "app_settings"):
                # If app_settings exists, we likely already ran init once
                # We can still check for new tables/columns in individual schema functions if needed
                pass
        finally:
            conn.close()

        _create_core_schema()
        _create_noting_schema()
        _create_qa_schema()
        _seed_noting_defaults()
    logger.info("Database initialized successfully.")


# ── Case CRUD ──────────────────────────────────────────────────────────────────
def add_case(case_data: dict) -> bool:
    """Insert a new case into the database."""
    conn = get_connection("core")
    try:
        conn.execute("""
            INSERT INTO cases (id, name, description, estimated_cost, department,
                               portal, portal_url, nit_no, nit_date, bid_due_date,
                               work_order_date, completion_date, dlp_end_date)
            VALUES (:id, :name, :description, :estimated_cost, :department,
                    :portal, :portal_url, :nit_no, :nit_date, :bid_due_date,
                    :work_order_date, :completion_date, :dlp_end_date)
        """, case_data)
        conn.commit()
        logger.info(f"Case added: {case_data['id']} - {case_data['name']}")
        return True
    except sqlite3.IntegrityError as e:
        logger.error(f"Case already exists or integrity error: {e}")
        return False
    finally:
        conn.close()


def get_all_cases() -> list:
    """Return all cases as a list of dicts."""
    conn = get_connection("core")
    rows = conn.execute(
        "SELECT * FROM cases WHERE id <> 'General' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_case(case_id: str) -> dict:
    """Return a single case by ID."""
    conn = get_connection("core")
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_case(case_id: str, updates: dict) -> bool:
    """Update case fields."""
    conn = get_connection("core")
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates["id"] = case_id
    fields = ", ".join([f"{k} = :{k}" for k in updates if k != "id"])
    conn.execute(f"UPDATE cases SET {fields} WHERE id = :id", updates)
    conn.commit()
    conn.close()
    return True


# ── Security Deposit CRUD ──────────────────────────────────────────────────────
def add_security_deposit(deposit_data: dict) -> int:
    """Insert a new EMD/PS entry and return its ID."""
    conn = get_connection("core")
    cur = conn.execute("""
        INSERT INTO security_deposits
            (case_id, type, contractor_name, instrument_type, instrument_no,
             bank_name, amount, validity_date, notes)
        VALUES
            (:case_id, :type, :contractor_name, :instrument_type, :instrument_no,
             :bank_name, :amount, :validity_date, :notes)
    """, deposit_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_expiring_deposits(days_ahead: int = 30) -> list:
    """Return EMD/PS entries expiring within the next N days."""
    conn = get_connection("core")
    rows = conn.execute("""
        SELECT sd.*, c.name as case_name FROM security_deposits sd
        JOIN cases c ON sd.case_id = c.id
        WHERE sd.status = 'Active'
          AND julianday(sd.validity_date) - julianday('now') BETWEEN 0 AND ?
        ORDER BY sd.validity_date ASC
    """, (days_ahead,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_deposits(case_id: str = None) -> list:
    """Return all deposits, optionally filtered by case."""
    conn = get_connection("core")
    if case_id:
        rows = conn.execute(
            "SELECT * FROM security_deposits WHERE case_id = ?", (case_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM security_deposits").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Document Registry ──────────────────────────────────────────────────────────
def register_document(doc_data: dict) -> int:
    """Register a downloaded/uploaded document in the database."""
    conn = get_connection("core")
    cur = conn.execute("""
        INSERT INTO documents (case_id, doc_type, filename, file_path, source_url, notes)
        VALUES (:case_id, :doc_type, :filename, :file_path, :source_url, :notes)
    """, doc_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_documents(case_id: str) -> list:
    """Return all documents for a given case."""
    conn = get_connection("core")
    rows = conn.execute(
        "SELECT * FROM documents WHERE case_id = ? ORDER BY uploaded_at DESC", (case_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Reminders ──────────────────────────────────────────────────────────────────
def add_reminder(reminder_data: dict) -> int:
    """Add a new reminder."""
    conn = get_connection("core")
    cur = conn.execute("""
        INSERT INTO reminders (case_id, title, description, event_date, alert_days_before, repeat_days)
        VALUES (:case_id, :title, :description, :event_date, :alert_days_before, :repeat_days)
    """, reminder_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_due_reminders() -> list:
    """Return reminders that are due today (based on alert_days_before)."""
    conn = get_connection("core")
    rows = conn.execute("""
        SELECT r.*, c.name as case_name FROM reminders r
        LEFT JOIN cases c ON r.case_id = c.id
        WHERE r.status = 'Active'
          AND julianday(r.event_date) - julianday('now') <= r.alert_days_before
          AND julianday(r.event_date) >= julianday('now')
        ORDER BY r.event_date ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_reminders() -> list:
    conn = get_connection("core")
    rows = conn.execute("""
        SELECT r.*, c.name as case_name FROM reminders r
        LEFT JOIN cases c ON r.case_id = c.id
        ORDER BY r.event_date ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Bill Registry ──────────────────────────────────────────────────────────────
def add_bill(bill_data: dict) -> int:
    conn = get_connection("core")
    cur = conn.execute("""
        INSERT INTO bills (case_id, bill_no, bill_date, contractor_name,
                           gross_amount, net_amount, deductions, status, remarks)
        VALUES (:case_id, :bill_no, :bill_date, :contractor_name,
                :gross_amount, :net_amount, :deductions, :status, :remarks)
    """, bill_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_bills(case_id: str) -> list:
    conn = get_connection("core")
    rows = conn.execute(
        "SELECT * FROM bills WHERE case_id = ? ORDER BY created_at DESC", (case_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Email Log ──────────────────────────────────────────────────────────────────
def log_email(email_data: dict) -> int:
    conn = get_connection("core")
    cur = conn.execute("""
        INSERT INTO email_log (case_id, subject, sender, received_at, summary, is_urgent)
        VALUES (:case_id, :subject, :sender, :received_at, :summary, :is_urgent)
    """, email_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_emails(case_id: str = None, unread_only: bool = False) -> list:
    conn = get_connection("core")
    query = "SELECT * FROM email_log WHERE 1=1"
    params = []
    if case_id:
        query += " AND case_id = ?"
        params.append(case_id)
    if unread_only:
        query += " AND read_status = 'Unread'"
    query += " ORDER BY received_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Noting History ─────────────────────────────────────────────────────────────
def save_noting_history(
    case_id: str = "General",
    noting_type: str = "Noting",
    content: str = "",
    ai_content: str = "",
) -> int:
    normalized_case_id = (case_id or "").strip() or "General"

    conn = get_connection("noting")
    cur = conn.execute("""
        INSERT INTO noting_history (case_id, noting_type, content, ai_content)
        VALUES (:case_id, :noting_type, :content, :ai_content)
    """, {
        "case_id": normalized_case_id,
        "noting_type": noting_type,
        "content": content,
        "ai_content": ai_content,
    })
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_noting_history(case_id: str = "General") -> list:
    conn = get_connection("noting")
    normalized_case_id = (case_id or "").strip()
    if not normalized_case_id or normalized_case_id.lower() == "general":
        rows = conn.execute(
            """
            SELECT * FROM noting_history
            WHERE case_id IS NULL OR TRIM(case_id) = '' OR LOWER(case_id) = 'general'
            ORDER BY created_at DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM noting_history WHERE case_id = ? ORDER BY created_at DESC", (normalized_case_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_noting_history(history_id: int) -> bool:
    conn = get_connection("noting")
    conn.execute("DELETE FROM noting_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return True


# ── Know How History ──────────────────────────────────────────────────────────
def add_know_how_history(question: str, answer: str) -> int:
    conn = get_connection("qa")
    cur = conn.execute("""
        INSERT INTO know_how_history (question, answer)
        VALUES (?, ?)
    """, (question, answer))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_know_how_history() -> list:
    conn = get_connection("qa")
    rows = conn.execute("SELECT * FROM know_how_history ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_know_how_history(history_id: int) -> bool:
    conn = get_connection("qa")
    conn.execute("DELETE FROM know_how_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return True

# ── QA Feedback ─────────────────────────────────────────────────────────────
def add_qa_feedback(question: str, answer: str, feedback: str) -> int:
    """Store user feedback on an AI answer for continuous learning."""
    conn = get_connection("qa")
    cur = conn.execute("""
        INSERT INTO qa_feedback (question, answer, feedback)
        VALUES (?, ?, ?)
    """, (question, answer, feedback))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_recent_qa_feedback(limit: int = 5) -> list:
    """Fetch the most recent feedback to inject into the LLM prompt."""
    conn = get_connection("qa")
    rows = conn.execute("""
        SELECT question, answer, feedback FROM qa_feedback
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_noting_learning_pattern(
    source_phrase: str,
    preferred_phrase: str,
    case_id: str = "General",
    noting_type: str = "Noting",
) -> None:
    """Store or reinforce a user's terminology preference for future noting drafts."""
    conn = get_connection("noting")
    conn.execute("""
        INSERT INTO noting_learning_patterns
            (source_phrase, preferred_phrase, use_count, case_id, noting_type)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(source_phrase, preferred_phrase) DO UPDATE SET
            use_count = use_count + 1,
            case_id = excluded.case_id,
            noting_type = excluded.noting_type,
            updated_at = datetime('now','localtime')
    """, (source_phrase, preferred_phrase, case_id, noting_type))
    conn.commit()
    conn.close()


def get_noting_learning_patterns(limit: int = 20) -> list:
    """Return the strongest recently learned noting terminology preferences."""
    conn = get_connection("noting")
    rows = conn.execute("""
        SELECT source_phrase, preferred_phrase, use_count, case_id, noting_type, updated_at
        FROM noting_learning_patterns
        ORDER BY use_count DESC, updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_app_setting(key: str, default: str = "") -> str:
    """Return a single app setting from the database."""
    conn = get_connection("noting")
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
    ).fetchone()
    conn.close()
    if not row:
        return default
    return row["value"]


def set_app_setting(key: str, value: str) -> None:
    """Insert or update a single app setting."""
    conn = get_connection("noting")
    conn.execute("""
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now','localtime')
    """, (key, value))
    conn.commit()
    conn.close()


def get_prompt_settings() -> dict:
    """Return the DB-backed prompt templates with defaults."""
    return {
        key: get_app_setting(key, default_value)
        for key, default_value in PROMPT_SETTINGS_DEFAULTS.items()
    }


# ── PROCUREMENT STAGES ─────────────────────────────────────────────────────────
def get_all_stages() -> list:
    conn = get_connection("noting")
    rows = conn.execute("SELECT name FROM procurement_stages ORDER BY seq ASC").fetchall()
    conn.close()
    return [r["name"] for r in rows]

def set_stages(stages: list) -> bool:
    conn = get_connection("noting")
    try:
        conn.execute("DELETE FROM procurement_stages")
        for i, s in enumerate(stages):
            conn.execute("INSERT INTO procurement_stages (name, seq) VALUES (?, ?)", (s, i))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Stages error: {e}")
        return False
    finally:
        conn.close()

# ── NOTING LIBRARY ───────────────────────────────────────────────────────────
def get_all_library_notings() -> list:
    conn = get_connection("noting")
    rows = conn.execute("SELECT * FROM noting_library ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_noting_to_library(stage: str, keyword: str, content: str, is_custom: bool = True) -> bool:
    conn = get_connection("noting")
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO noting_library (stage, keyword, content, is_custom, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (stage, keyword, content, 1 if is_custom else 0, now))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Library Noting error: {e}")
        return False
    finally:
        conn.close()

def update_noting_in_library(noting_id: int, updates: dict) -> bool:
    conn = get_connection("noting")
    try:
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates["id"] = noting_id
        fields = ", ".join([f"{k} = :{k}" for k in updates if k != "id"])
        conn.execute(f"UPDATE noting_library SET {fields} WHERE id = :id", updates)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Library Update error: {e}")
        return False
    finally:
        conn.close()

def delete_noting_from_library(noting_id: int) -> bool:
    conn = get_connection("noting")
    try:
        conn.execute("DELETE FROM noting_library WHERE id = ?", (noting_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def delete_notings_by_stages(stages: list) -> int:
    if not stages: return 0
    conn = get_connection("noting")
    try:
        cursor = conn.cursor()
        query = f"DELETE FROM noting_library WHERE stage IN ({','.join(['?']*len(stages))})"
        cursor.execute(query, stages)
        removed = cursor.rowcount
        conn.commit()
        return removed
    finally:
        conn.close()

def search_noting_library(
    query: str = "",
    stage: str = "",
    limit: int | None = None,
    offset: int = 0,
    include_total: bool = False,
):
    """Efficient SQL-based search for the noting library."""
    conn = get_connection("noting")
    clauses = []
    params = []

    if query:
        q = f"%{query}%"
        clauses.append("(keyword LIKE ? OR stage LIKE ? OR content LIKE ?)")
        params.extend([q, q, q])

    if stage and stage.upper() != "ALL":
        clauses.append("stage = ?")
        params.append(stage)

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    total = None
    if include_total:
        total = conn.execute(
            f"SELECT COUNT(*) FROM noting_library{where_sql}",
            params,
        ).fetchone()[0]

    query_sql = f"""
        SELECT * FROM noting_library
        {where_sql}
        ORDER BY datetime(updated_at) DESC, id DESC
    """
    query_params = list(params)
    if limit is not None:
        query_sql += " LIMIT ? OFFSET ?"
        query_params.extend([max(int(limit), 0), max(int(offset), 0)])

    rows = conn.execute(query_sql, query_params).fetchall()
    conn.close()

    data = [dict(r) for r in rows]
    if include_total:
        return data, total
    return data

# ── EMAIL CATEGORIES ──────────────────────────────────────────────────────────
def get_all_email_categories() -> list:
    conn = get_connection("noting")
    rows = conn.execute("SELECT name FROM email_categories ORDER BY seq ASC").fetchall()
    conn.close()
    return [r["name"] for r in rows]

def set_email_categories(cats: list) -> bool:
    conn = get_connection("noting")
    try:
        conn.execute("DELETE FROM email_categories")
        for i, c in enumerate(cats):
            conn.execute("INSERT INTO email_categories (name, seq) VALUES (?, ?)", (c, i))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Email Cats error: {e}")
        return False
    finally:
        conn.close()

def search_email_library(query: str = "") -> list:
    """Efficient SQL-based search for the email library."""
    if not query:
        return get_all_library_emails()
    
    conn = get_connection("noting")
    q = f"%{query}%"
    rows = conn.execute("""
        SELECT * FROM email_library 
        WHERE keyword LIKE ? OR category LIKE ? OR content LIKE ?
        ORDER BY updated_at DESC
    """, (q, q, q)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── EMAIL LIBRARY ─────────────────────────────────────────────────────────────
def get_all_library_emails() -> list:
    conn = get_connection("noting")
    rows = conn.execute("SELECT * FROM email_library ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_email_to_library(category: str, keyword: str, content: str, is_custom: bool = True) -> bool:
    conn = get_connection("noting")
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO email_library (category, keyword, content, is_custom, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (category, keyword, content, 1 if is_custom else 0, now))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Library Email error: {e}")
        return False
    finally:
        conn.close()

def update_email_in_library(item_id: int, updates: dict) -> bool:
    conn = get_connection("noting")
    try:
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates["id"] = item_id
        fields = ", ".join([f"{k} = :{k}" for k in updates if k != "id"])
        conn.execute(f"UPDATE email_library SET {fields} WHERE id = :id", updates)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB Email Library Update error: {e}")
        return False
    finally:
        conn.close()

def delete_email_from_library(item_id: int) -> bool:
    conn = get_connection("noting")
    try:
        conn.execute("DELETE FROM email_library WHERE id = ?", (item_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def delete_emails_by_categories(categories: list) -> int:
    """Remove all email templates whose category is in the provided list."""
    if not categories:
        return 0
    conn = get_connection("noting")
    try:
        placeholders = ', '.join(['?'] * len(categories))
        res = conn.execute(f"DELETE FROM email_library WHERE category IN ({placeholders})", categories)
        affected = res.rowcount
        conn.commit()
        return affected
    finally:
        conn.close()


# ── Initialize on import ────────────────────────────────────────────────────────
initialize_database()

if __name__ == "__main__":
    print("Database diagnostics complete.")
