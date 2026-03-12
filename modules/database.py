"""
Noting Bot - Database Manager
Handles all SQLite database operations for case registry, EMD tracking, and reminders.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from modules.utils import CONFIG, DATA_ROOT, logger

DB_PATH = CONFIG.get("paths", {}).get("database") or str(DATA_ROOT / "cases.db")


def get_connection():
    """Return a SQLite connection with row_factory for dict-like access."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    # Production optimization: increased timeout for concurrent access
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Performance: Enable Write-Ahead Logging for better concurrent performance
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize_database():
    """Create all required tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── Cases Table ────────────────────────────────────────────────────────────
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

    # ── EMD / Performance Security Table ───────────────────────────────────────
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

    # ── Reminders Table ────────────────────────────────────────────────────────
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

    # ── Documents Table ────────────────────────────────────────────────────────
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

    # ── Bills Table ────────────────────────────────────────────────────────────
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

    # ── Email Log Table ────────────────────────────────────────────────────────
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

    # ── Noting History Table ───────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS noting_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT DEFAULT 'General',
            noting_type     TEXT,
            content         TEXT,
            ai_content      TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )
    """)

    # Backward-compatible migration for older installs
    noting_columns = [row["name"] for row in conn.execute("PRAGMA table_info(noting_history)").fetchall()]
    if "ai_content" not in noting_columns:
        conn.execute("ALTER TABLE noting_history ADD COLUMN ai_content TEXT")

    # ── Noting Learning Patterns Table (Continuous Learning) ──────────────────
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

    # ── Know How History Table ────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS know_how_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            question        TEXT NOT NULL,
            answer          TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── QA Feedback Table (Continuous Learning)  ──────────────────────────────
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
    logger.info("Database initialized successfully.")


# ── Case CRUD ──────────────────────────────────────────────────────────────────
def add_case(case_data: dict) -> bool:
    """Insert a new case into the database."""
    conn = get_connection()
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
    conn = get_connection()
    rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_case(case_id: str) -> dict:
    """Return a single case by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_case(case_id: str, updates: dict) -> bool:
    """Update case fields."""
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM documents WHERE case_id = ? ORDER BY uploaded_at DESC", (case_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Reminders ──────────────────────────────────────────────────────────────────
def add_reminder(reminder_data: dict) -> int:
    """Add a new reminder."""
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
    rows = conn.execute("""
        SELECT r.*, c.name as case_name FROM reminders r
        LEFT JOIN cases c ON r.case_id = c.id
        ORDER BY r.event_date ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Bill Registry ──────────────────────────────────────────────────────────────
def add_bill(bill_data: dict) -> int:
    conn = get_connection()
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
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bills WHERE case_id = ? ORDER BY created_at DESC", (case_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Email Log ──────────────────────────────────────────────────────────────────
def log_email(email_data: dict) -> int:
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO email_log (case_id, subject, sender, received_at, summary, is_urgent)
        VALUES (:case_id, :subject, :sender, :received_at, :summary, :is_urgent)
    """, email_data)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_emails(case_id: str = None, unread_only: bool = False) -> list:
    conn = get_connection()
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
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO noting_history (case_id, noting_type, content, ai_content)
        VALUES (:case_id, :noting_type, :content, :ai_content)
    """, {
        "case_id": case_id or "General",
        "noting_type": noting_type,
        "content": content,
        "ai_content": ai_content,
    })
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_noting_history(case_id: str = "General") -> list:
    conn = get_connection()
    if case_id == "General" or not case_id:
        rows = conn.execute(
            "SELECT * FROM noting_history ORDER BY created_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM noting_history WHERE case_id = ? ORDER BY created_at DESC", (case_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_noting_history(history_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM noting_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return True


# ── Know How History ──────────────────────────────────────────────────────────
def add_know_how_history(question: str, answer: str) -> int:
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO know_how_history (question, answer)
        VALUES (?, ?)
    """, (question, answer))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

def get_know_how_history() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM know_how_history ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_know_how_history(history_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM know_how_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return True

# ── QA Feedback ─────────────────────────────────────────────────────────────
def add_qa_feedback(question: str, answer: str, feedback: str) -> int:
    """Store user feedback on an AI answer for continuous learning."""
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
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
    conn = get_connection()
    rows = conn.execute("""
        SELECT source_phrase, preferred_phrase, use_count, case_id, noting_type, updated_at
        FROM noting_learning_patterns
        ORDER BY use_count DESC, updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Initialize on import ────────────────────────────────────────────────────────
if __name__ == "__main__":
    initialize_database()
    print("Database initialized successfully.")
