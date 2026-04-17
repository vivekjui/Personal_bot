"""
Noting Bot - Module 1.5: E-Office Email Assistant
Drafts official emails based on case context using AI.
Standalone module based on eoffice_noting.py logic.
"""

import os
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from modules.utils import (
    CONFIG,
    DEFAULT_EMAIL_MASTER_PROMPT,
    logger,
    DATA_ROOT,
    ask_gemini,
    sanitize_filename,
    today_str,
)
from modules.database import (
    get_app_setting,
    save_noting_history,
)

# Paths for email-specific library
EMAIL_LIBRARY_PATH = DATA_ROOT / "email_library.json"

EMAIL_TYPES = [
    "Clarification Request to Bidder",
    "Bid Validity Extension Request",
    "Response to Representation / Complaint",
    "Sanction / Award Intimation",
    "Meeting Notice / Agenda",
    "Internal Departmental Memo",
    "Request for Information (RFI)",
    "Payment Advice / Status Update",
    "General Correspondence",
    "Custom Email"
]

def get_email_master_prompt() -> str:
    """Return the configurable email drafting master prompt from DB."""
    return get_app_setting("email_master_prompt", DEFAULT_EMAIL_MASTER_PROMPT)

def generate_email_text(
    context: str = "",
    doc_type: str = "General Correspondence",
    target_language: str = "Hindi",
    additional_instructions: str = ""
) -> str:
    """
    Generate email body using AI.
    Follows similar logic to generate_noting_text.
    """
    logger.info(f"Generating email draft for: {doc_type}")

    master_template = get_email_master_prompt()
    
    # We can reuse the style learning logic from noting if desired, 
    # but for now we'll keep it simple as requested.
    
    try:
        prompt = master_template.format(
            draft_content=context,
            additional_instructions=additional_instructions,
            target_language=target_language,
            user_style_examples="", # Placeholder for future extension
            style_summary="", 
            learning_instructions=""
        )
    except Exception as e:
        logger.warning(f"Invalid email master prompt template. Using fallback. Error: {e}")
        prompt = f"Draft a formal {target_language} email for {doc_type}. Context: {context}. {additional_instructions}"

    ai_body = ask_gemini(prompt)
    
    # Save to history (categorized as Email)
    save_noting_history(
        case_id="General",
        noting_type=f"Email: {doc_type}",
        content=context,
        ai_content=ai_body
    )

    return ai_body.strip()

def list_email_types():
    return EMAIL_TYPES

def load_email_library() -> list:
    if not EMAIL_LIBRARY_PATH.exists():
        return []
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading email library: {e}")
        return []

def add_to_email_library(category: str, keyword: str, text: str) -> bool:
    library = load_email_library()
    new_id = max([item.get("id", 0) for item in library] + [0]) + 1
    library.append({
        "id": new_id,
        "category": category,
        "keyword": keyword,
        "text": text,
        "updated_at": datetime.now().isoformat()
    })
    try:
        with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
            json.dump(library, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save email library: {e}")
        return False
