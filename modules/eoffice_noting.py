"""
Noting Bot - Module 1: E-Office Noting Assistant
Drafts official noting files based on case context using AI.

Two-step flow:
  1. generate_noting_text() → returns plain text for user review / editing
  2. save_noting_to_docx()  → saves the (possibly modified) text to a DOCX file
  draft_noting()            → convenience wrapper that does both in one shot
"""

import os
import re
import json
from difflib import SequenceMatcher
from datetime import datetime
from html import unescape
from pathlib import Path
from modules.utils import (
    CONFIG,
    DEFAULT_NOTING_MASTER_PROMPT,
    logger,
    BUNDLE_ROOT,
    DATA_ROOT,
    PROCUREMENT_STAGES_PATH,
    STANDARD_LIBRARY_PATH,
    ask_gemini,
    get_case_folder,
    create_docx_from_text,
    sanitize_filename,
    today_str,
)

# Paths used by the email drafting feature. Stored in the user's data directory.
EMAIL_CATEGORIES_PATH = DATA_ROOT / "email_categories.json"
EMAIL_LIBRARY_PATH = DATA_ROOT / "email_library.json"
from modules.database import get_noting_learning_patterns, upsert_noting_learning_pattern


NOTING_TYPES = [
    "NIT Approval",
    "Acceptance of NIT / Opening of Bids",
    "Tender Evaluation Committee Recommendation",
    "Approval of Award / Work Order",
    "Payment / Running Account Bill",
    "Extension of Time",
    "LD Waiver (Liquidated Damages)",
    "Rescinding of Contract",
    "Refund of Security Deposit / EMD",
    "Final Bill Sanction",
    "Custom Noting"
]

WORDISH_BOUNDARY = r"[\w\u0900-\u097F]"
TABLE_BLOCK_RE = re.compile(r"<table\b[\s\S]*?</table>", re.IGNORECASE)

def get_noting_master_prompt() -> str:
    """Return the configurable noting master prompt."""
    return CONFIG.get("llm", {}).get("noting_master_prompt") or DEFAULT_NOTING_MASTER_PROMPT


def _normalize_learning_phrase(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text.strip(".,;:!?\"'()[]{}")


def _normalize_style_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _contains_devanagari(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _is_valid_learning_phrase(text: str) -> bool:
    if not text:
        return False
    if len(text) < 2 or len(text) > 40:
        return False
    if "\n" in text or "\r" in text:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if len(text.split()) > 4:
        return False
    return bool(re.search(r"[A-Za-z\u0900-\u097F]", text))


def extract_learning_patterns_from_edit(original_text: str, final_text: str, max_patterns: int = 12) -> list:
    """
    Learn stable phrase replacements from the user's edits.
    Example: 'बोली' -> 'निविदा'
    """
    original_tokens = (original_text or "").split()
    final_tokens = (final_text or "").split()
    if not original_tokens or not final_tokens:
        return []

    matcher = SequenceMatcher(a=original_tokens, b=final_tokens, autojunk=False)
    patterns = []
    seen = set()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue

        src = _normalize_learning_phrase(" ".join(original_tokens[i1:i2]))
        dst = _normalize_learning_phrase(" ".join(final_tokens[j1:j2]))

        if not (_is_valid_learning_phrase(src) and _is_valid_learning_phrase(dst)):
            continue
        if src.casefold() == dst.casefold():
            continue

        key = (src.casefold(), dst.casefold())
        if key in seen:
            continue
        seen.add(key)
        patterns.append({
            "source_phrase": src,
            "preferred_phrase": dst,
        })
        if len(patterns) >= max_patterns:
            break

    return patterns


def learn_from_noting_edit(
    original_text: str,
    final_text: str,
    case_id: str = "General",
    noting_type: str = "Noting",
) -> int:
    """Persist safe phrase replacements inferred from user edits."""
    learned = 0
    for pattern in extract_learning_patterns_from_edit(original_text, final_text):
        upsert_noting_learning_pattern(
            pattern["source_phrase"],
            pattern["preferred_phrase"],
            case_id=case_id,
            noting_type=noting_type,
        )
        learned += 1
    return learned


def get_noting_learning_instructions(limit: int = 12) -> str:
    """Format learned preferences as prompt instructions for future drafts."""
    patterns = get_noting_learning_patterns(limit=limit)
    if not patterns:
        return ""

    lines = []
    seen_sources = set()
    for item in patterns:
        src = item.get("source_phrase", "").strip()
        dst = item.get("preferred_phrase", "").strip()
        if not src or not dst:
            continue
        src_key = src.casefold()
        if src_key in seen_sources:
            continue
        seen_sources.add(src_key)
        lines.append(f"- Prefer '{dst}' instead of '{src}'.")

    if not lines:
        return ""

    return "\n=== USER TERMINOLOGY PREFERENCES ===\nApply these learned wording preferences whenever relevant:\n" + "\n".join(lines) + "\n"


def _score_style_noting(item: dict, query_words: set[str]) -> int:
    text = (item.get("text") or "")
    keyword = (item.get("keyword") or "").lower()
    stage = (item.get("stage") or "").lower()
    lowered_text = text.lower()

    score = 0
    if item.get("is_custom"):
        score += 120
    if _contains_devanagari(text):
        score += 40
    if item.get("updated_at"):
        score += 10

    for word in query_words:
        if word in keyword:
            score += 30
        if word in stage:
            score += 15
        if word in lowered_text:
            score += 6

    return score


def get_user_style_summary(context: str = "", limit: int = 12) -> str:
    """
    Build lightweight style instructions from the library itself so future drafts
    reflect the user's stored language and phrasing patterns.
    """
    examples = get_user_style_examples(limit=limit, context=context)
    if not examples:
        return ""

    hindi_count = sum(1 for ex in examples if _contains_devanagari(ex))
    preferred_language = "Prefer formal Rajbhasha Hindi wording and sentence construction." if hindi_count >= max(1, len(examples) // 2) else "Prefer the formal bilingual administrative tone reflected in the library examples."

    sentence_counts = {}
    for ex in examples:
        for sentence in re.split(r"[\n।]+", ex):
            cleaned = _normalize_style_text(sentence).strip(" -")
            if len(cleaned) < 12 or len(cleaned) > 120:
                continue
            sentence_counts[cleaned] = sentence_counts.get(cleaned, 0) + 1

    repeated_phrases = [
        sentence for sentence, count in sorted(
            sentence_counts.items(),
            key=lambda pair: (-pair[1], -len(pair[0]))
        )
        if count > 1
    ][:3]

    lines = [
        "=== LIBRARY STYLE LEARNING ===",
        "Learn the user's drafting language and writing pattern from the noting library.",
        preferred_language,
        "Prefer concise, official file-note paragraphs with procurement terminology aligned to the library."
    ]
    for phrase in repeated_phrases:
        lines.append(f"- Reuse phrasing patterns similar to: {phrase}")

    return "\n" + "\n".join(lines) + "\n"


def apply_learned_noting_patterns(text: str, limit: int = 20) -> str:
    """Apply learned phrase replacements to AI output so repeated user fixes stick."""
    if not text:
        return text

    patterns = get_noting_learning_patterns(limit=limit)
    if not patterns:
        return text

    resolved = []
    seen_sources = set()
    for item in patterns:
        src = (item.get("source_phrase") or "").strip()
        dst = (item.get("preferred_phrase") or "").strip()
        if not src or not dst:
            continue
        src_key = src.casefold()
        if src_key in seen_sources:
            continue
        seen_sources.add(src_key)
        resolved.append((src, dst))

    resolved.sort(key=lambda pair: len(pair[0]), reverse=True)
    updated = text
    for src, dst in resolved:
        pattern = re.compile(rf"(?<!{WORDISH_BOUNDARY}){re.escape(src)}(?!{WORDISH_BOUNDARY})")
        updated = pattern.sub(dst, updated)
    return updated


def generate_noting_text(
    additional_context: str = "",
) -> str:
    """
    Step 1: Generate noting text using AI.
    Returns the full noting as a plain-text / markdown string
    WITHOUT saving to disk — ready for user review and editing.
    """
    logger.info("Generating noting suggestion based on provided context.")

    # Optionally augment with RAG context
    rag_context = ""
    try:
        from modules.rag_engine import retrieve_context
        rag_context, _sources = retrieve_context(
            additional_context,
            n_results=5,
            category_filter=None
        )
    except Exception:
        pass

    # --- CONTINUOUS LEARNING: Learn style from drafted noting library ---
    user_style_examples = ""
    try:
        examples = get_user_style_examples(limit=4, context=additional_context)
        if examples:
            user_style_examples = "\n=== नोटिंग लाइब्रेरी से सीखी गई उपयोगकर्ता की लेखन शैली (Style Examples from Library) ===\n"
            for i, ex in enumerate(examples):
                user_style_examples += f"उदाहरण {i+1}:\n{ex}\n---\n"
    except Exception as e:
        logger.warning(f"Failed to fetch user style examples: {e}")

    learning_instructions = get_noting_learning_instructions()
    style_summary = get_user_style_summary(context=additional_context)
    master_template = get_noting_master_prompt()
    try:
        prompt = master_template.format(
            additional_context=additional_context,
            rag_context=rag_context,
            user_style_examples=user_style_examples
        )
    except Exception as e:
        logger.warning(f"Invalid noting master prompt template. Falling back to default. Error: {e}")
        prompt = DEFAULT_NOTING_MASTER_PROMPT.format(
            additional_context=additional_context,
            rag_context=rag_context,
            user_style_examples=user_style_examples
        )
    extra_prompt_blocks = "".join(block for block in [style_summary, learning_instructions] if block)
    if extra_prompt_blocks:
        prompt = f"{prompt.rstrip()}\n{extra_prompt_blocks}"
    ai_body = ask_gemini(prompt)

    return apply_learned_noting_patterns(ai_body.strip())


def save_noting_to_docx(
    text: str,
    case_id: str,
    noting_type: str,
    filename: str = "",
) -> str:
    """
    Step 2: Save (possibly user-edited) noting text to a DOCX file.
    Returns the output file path.
    """
    output_dir = get_case_folder(case_id, "Generated")
    safe_name  = filename or sanitize_filename(f"Noting_{noting_type}_{today_str()}.docx")
    output_path = str(output_dir / safe_name)
    create_docx_from_text(text, output_path, title="")
    logger.info(f"Noting saved: {output_path}")
    return output_path


def draft_noting(
    additional_context: str = "",
) -> str:
    """
    Simplified draft_noting that only uses context.
    """
    text = generate_noting_text(additional_context=additional_context)
    return text


def load_standard_notings() -> list:
    """Read the categorized notings from the new JSON library file."""
    library_file = STANDARD_LIBRARY_PATH
    
    if not library_file.exists():
        logger.warning(f"Standard library file not found: {library_file}")
        # Try fallback to old JSONL for safety if needed, but we expect the new one
        return []
    
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading standard library: {e}")
        return []

def update_library_noting(noting_id: int, updates: dict) -> bool:
    """Update specific fields of a library noting."""
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists(): return False
    
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        updated = False
        for item in data:
            if item["id"] == noting_id:
                for key, val in updates.items():
                    item[key] = val
                item["updated_at"] = datetime.now().isoformat()
                item["is_custom"] = True
                updated = True
                break
        
        if updated:
            with open(library_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to update library noting: {e}")
    return False

def add_library_noting(stage: str, keyword: str, text: str) -> bool:
    """Append a new noting to the library file."""
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists(): return False

    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Determine new ID
        new_id = max([item["id"] for item in data] + [0]) + 1
        
        new_item = {
            "id": new_id,
            "stage": stage,
            "keyword": keyword,
            "text": text,
            "updated_at": datetime.now().isoformat(),
            "is_custom": True
        }
        data.append(new_item)
        
        with open(library_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to add library noting: {e}")
    return False


def delete_library_notings_by_stages(stages: list) -> int:
    """Remove all library notings whose 'stage' is in the provided list.

    Returns number of entries deleted.
    """
    if not stages:
        return 0
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists():
        return 0
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        initial = len(data)
        data = [item for item in data if item.get("stage") not in stages]
        removed = initial - len(data)
        if removed > 0:
            with open(library_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return removed
    except Exception as e:
        logger.error(f"Failed to delete library notings by stages: {e}")
        return 0

def move_library_noting(noting_id: int, new_stage: str) -> bool:
    """Change the stage of an existing noting."""
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists(): return False
    
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        updated = False
        for item in data:
            if item["id"] == noting_id:
                item["stage"] = new_stage
                updated = True
                break
        
        if updated:
            with open(library_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to move library noting: {e}")
    return False

def delete_library_noting(noting_id: int) -> bool:
    """Remove a noting from the library file."""
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists(): return False
    
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        initial_count = len(data)
        data = [item for item in data if item["id"] != noting_id]
        
        if len(data) < initial_count:
            with open(library_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to delete library noting: {e}")
    return False

def get_procurement_stages() -> list:
    """Load the list of procurement stages."""
    stages_file = PROCUREMENT_STAGES_PATH
    if not stages_file.exists():
        # Fallback to hardcoded defaults if file missing
        return [
            "Indent received", "custom bid approval", "bid preparation", "bid vetting",
            "bid publication approval", "bid publication", "bid end date extension",
            "Technical bid opening", "TEC (T) evaluation", "Representation",
            "Review TEC", "Price bid opening", "Budget confirmation",
            "Contract award", "DP (Delivery period) Extension",
            "Performance security", "Bill"
        ]
    
    import json
    try:
        with open(stages_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading stages: {e}")
        return []


# ---------------------------------------------------------------------------
# Email drafting helpers
# ---------------------------------------------------------------------------

def load_email_categories() -> list:
    """Return the list of email categories configured by the user."""
    if not EMAIL_CATEGORIES_PATH.exists():
        return ["General"]
    try:
        with open(EMAIL_CATEGORIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                cleaned = [str(item).strip() for item in data if str(item).strip()]
                return cleaned or ["General"]
    except Exception as e:
        logger.error(f"Error loading email categories: {e}")
    return ["General"]


def save_email_categories(cats: list) -> bool:
    """Persist the list of email categories to disk."""
    try:
        cleaned = []
        seen = set()
        for item in cats or []:
            value = str(item).strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(value)
        if not cleaned:
            cleaned = ["General"]
        EMAIL_CATEGORIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EMAIL_CATEGORIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving email categories: {e}")
        return False


def load_email_library() -> list:
    """Load email templates from the JSON library file."""
    if not EMAIL_LIBRARY_PATH.exists():
        return []
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading email library: {e}")
        return []


def save_email_library(data: list) -> bool:
    """Save the email library list to disk."""
    try:
        EMAIL_LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving email library: {e}")
        return False


def update_library_email(item_id: int, updates: dict) -> bool:
    """Update fields of a specific email library entry."""
    if not EMAIL_LIBRARY_PATH.exists():
        return False
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        updated = False
        for item in data:
            if item.get("id") == item_id:
                for key, val in updates.items():
                    item[key] = val
                item["updated_at"] = datetime.now().isoformat()
                item["is_custom"] = True
                updated = True
                break
        if updated:
            with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to update email library item: {e}")
    return False


def add_library_email(category: str, keyword: str, text: str) -> bool:
    """Add a new template to the email library."""
    existing = []
    if EMAIL_LIBRARY_PATH.exists():
        try:
            with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            logger.error(f"Error reading email library: {e}")
            existing = []
    try:
        new_id = max([item.get("id", 0) for item in existing] + [0]) + 1
        new_item = {
            "id": new_id,
            "stage": category,
            "keyword": keyword,
            "text": text,
            "updated_at": datetime.now().isoformat(),
            "is_custom": True,
        }
        existing.append(new_item)
        with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to add email library item: {e}")
    return False


def move_library_email(item_id: int, new_category: str) -> bool:
    """Change the category of an email template."""
    if not EMAIL_LIBRARY_PATH.exists():
        return False
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        moved = False
        for item in data:
            if item.get("id") == item_id:
                item["stage"] = new_category
                moved = True
                break
        if moved:
            with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to move email library item: {e}")
    return False


def delete_library_email(item_id: int) -> bool:
    """Remove an email template by its ID."""
    if not EMAIL_LIBRARY_PATH.exists():
        return False
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        initial = len(data)
        data = [it for it in data if it.get("id") != item_id]
        if len(data) < initial:
            with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        logger.error(f"Failed to delete email library item: {e}")
    return False


def delete_library_emails_by_categories(categories: list) -> int:
    """Bulk remove templates belonging to any of the given categories."""
    if not categories:
        return 0
    if not EMAIL_LIBRARY_PATH.exists():
        return 0
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        initial = len(data)
        data = [it for it in data if it.get("stage") not in categories]
        removed = initial - len(data)
        if removed > 0:
            with open(EMAIL_LIBRARY_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return removed
    except Exception as e:
        logger.error(f"Failed to bulk delete email templates: {e}")
        return 0

# -----------------------------------------------------

def update_procurement_stages(stages_list: list) -> bool:
    """Save the updated list of procurement stages."""
    stages_file = PROCUREMENT_STAGES_PATH
    import json
    try:
        stages_file.parent.mkdir(parents=True, exist_ok=True)
        with open(stages_file, "w", encoding="utf-8") as f:
            json.dump(stages_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save stages: {e}")
        return False

def batch_translate_library(limit: int = None):
    """
    Utility to translate and SANITIZE the entire library.
    Step 1: Sanitize English text (replace PII with placeholders)
    Step 2: Translate sanitized text to Hindi
    """
    base_file = DATA_ROOT / "Vivek_Kumar_Notings_Categorized.jsonl"
    full_file = DATA_ROOT / "Vivek_Kumar_Notings_Full.jsonl"
    if not base_file.exists():
        bundled_base = BUNDLE_ROOT / "Vivek_Kumar_Notings_Categorized.jsonl"
        if bundled_base.exists():
            base_file = bundled_base
    
    import json
    existing_data = {}
    if full_file.exists() and full_file.stat().st_size > 0:
        with open(full_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        item = json.loads(line)
                        existing_data[item["id"]] = item
                    except: continue

    data = []
    with open(base_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    data.append(json.loads(line))
                except: continue

    if limit:
        data = data[:limit]

    count = 0
    with open(full_file, "w", encoding="utf-8") as f:
        for item in data:
            # We re-process if 'is_sanitized' is missing or False
            needs_processing = True
            if item["id"] in existing_data:
                old_item = existing_data[item["id"]]
                if old_item.get("is_sanitized") and old_item.get("text_hindi"):
                    item = old_item
                    needs_processing = False

            if needs_processing:
                logger.info(f"Sanitizing & Translating noting ID {item['id']}...")
                # 1. Sanitize
                original_text = item["text"]
                clean_text = sanitize_noting_llm(original_text)
                item["text"] = clean_text
                item["is_sanitized"] = True
                
                # 2. Translate Cleaned Text
                item["text_hindi"] = translate_noting_llm(clean_text, "hindi")
                count += 1
            
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
            
    logger.info(f"Batch processing complete. Sanitized & Translated {count} items.")
    return count


def sanitize_noting_llm(text: str) -> str:
    """
    Use LLM to remove specific details (Firm Names, GeM IDs, File Nos, Dates) 
    and replace with [Placeholders].
    """
    from modules.utils import ask_llm
    
    prompt = f"""
    You are an expert e-Office Noting Sanitizer for the Geological Survey of India (GSI).
    Your task is to take the following e-office noting and remove all case-specific details, replacing them with generic smart placeholders.

    REPLACE THE FOLLOWING:
    1. Specific Firm/Company Names -> [Firm Name]
    2. GeM Contract/Bid Numbers -> [GeM Contract No.]
    3. File/Case Numbers (e.g. D-28013/...) -> [Case Details]
    4. Dates (e.g. 03-03-2025) -> [Date]
    5. Specific amounts (e.g. रु.45000/-) -> [Amount]
    6. Personal names (e.g. VIVEK KUMAR) -> [Official Name]
    7. Specific Division Names (if very specific) -> [Division/Section]
    8. Remove headers like "Note # 1" or timestamp footers.

    KEEP:
    - The overall professional logic, procurement terminology, and structure.
    - Important technical specs that are generic.

    Original Noting:
    {text}

    OUTPUT ONLY THE SANITIZED ENGLISH TEXT. DO NOT ADD ANY HINDI OR COMMENTS.
    """
    
    try:
        sanitized = ask_llm(prompt)
        if sanitized:
            # Basic cleanup if LLM adds markdown or fluff
            sanitized = sanitized.replace("```text", "").replace("```", "").strip()
            return sanitized
    except Exception as e:
        logger.error(f"Sanitization failed: {e}")
    
    return text  # Fallback to original


def translate_noting_llm(text: str, target_lang: str = "hindi") -> str:
    """
    High-quality translation using Gemini LLM.
    Handles English-to-Hindi and Hindi-to-English with context awareness.
    """
    if not text:
        return ""

    prompt = f"""You are an expert translator specializing in Indian Government official noting and correspondence.
Translate the following text into high-quality, formal {target_lang}.

**Constraints:**
1. Maintain the official, administrative tone.
2. Ensure technical procurement terms (e.g., GeM, EMD, NIT, TEC, Sanction) are translated or used correctly in context.
3. If translating to Hindi, use Rajbhasha (Official Hindi) style.
4. If translating to English, use standard bureaucratic English.
5. Provide ONLY the translated text without any preamble or notes.

Text to translate:
{text}
"""
    try:
        translation = ask_gemini(prompt)
        return translation.strip()
    except Exception as e:
        logger.error(f"LLM translation error: {e}")
        return "[Translation Error]"


def search_standard_notings(query: str) -> list:
    """Filter standard notings based on query keywords with word overlap."""
    all_notings = load_standard_notings()
    if not query:
        return all_notings
    
    # split on word characters; this will capture Hindi words too but
    # won't respect Devanagari-specific characters. we'll expand the list
    # afterwards by transliterating back and forth so English/Hinglish
    # queries can match native Hindi text (e.g. 'bill' -> 'बिल').
    query_words = set(re.findall(r'\w+', query.lower()))
    if not query_words:
        return []

    # attempt to transliterate each token in both directions using the
    # indic-transliteration library (ITRANS <-> DEVANAGARI). this gives
    # us extra variants that may appear in the stored notings.
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        def _normalize_dev(s: str) -> str:
            # remove halant characters and collapse duplicates
            s = s.replace("्", "")
            s = re.sub(r"(.)\1+", r"\1", s)
            return s

        augmented = set(query_words)
        for w in list(query_words):
            # ascii or roman input -> devanagari
            try:
                dev = transliterate(w, sanscript.ITRANS, sanscript.DEVANAGARI)
                if dev and dev != w:
                    devn = _normalize_dev(dev)
                    augmented.add(devn.lower())
                    # also back-transliterate to catch slightly different spellings
                    itr = transliterate(dev, sanscript.DEVANAGARI, sanscript.ITRANS)
                    if itr:
                        augmented.add(itr.lower())
            except Exception:
                pass
            # devanagari input -> itrans
            try:
                itr = transliterate(w, sanscript.DEVANAGARI, sanscript.ITRANS)
                if itr and itr != w:
                    augmented.add(itr.lower())
                    dev2 = transliterate(itr, sanscript.ITRANS, sanscript.DEVANAGARI)
                    if dev2:
                        augmented.add(_normalize_dev(dev2).lower())
            except Exception:
                pass
        query_words = augmented
    except ImportError:
        # transliteration library not installed; just use the original words
        pass

    results = []
    for item in all_notings:
        keyword = item.get("keyword", "").lower()
        text = item.get("text", "").lower()
        stage = item.get("stage", "").lower()
        is_custom = item.get("is_custom", False)
        
        # Scoring based on matches
        score = 0
        
        # Handle Hindi/Unicode word matching by checking substrings more robustly
        for word in query_words:
            if word in keyword: score += 15
            if word in stage: score += 5
            if word in text: score += 2
        
        # Direct substring matches (very high priority)
        query_str = query.lower()
        if query_str in keyword: score += 100
        if query_str in stage: score += 50
        if query_str in text: score += 30
        
        # Custom/Refined entries get a slight boost if they match
        if score > 0 and is_custom:
            score += 10
        
        if score > 0:
            results.append((score, item))
    
    # Sort by score descending, then by is_custom, then by updated_at
    results.sort(key=lambda x: (
        x[0], 
        x[1].get("is_custom", False), 
        x[1].get("updated_at", "")
    ), reverse=True)
    return [item for score, item in results]


def clean_noting_text(text: str) -> str:
    """
    Remove common headers, signature blocks, and dates for template display.
    """
    if not text:
        return ""
    
    # Remove Note# prefix (e.g. Note# 1, Note#12. )
    text = re.sub(r'(?i)Note\s*#\s*\d+[.\d]*\s*', '', text)
    
    # Remove signature placeholders and dates
    patterns = [
        r'\(हस्ताक्षर\)', r'\(नाम\)', r'\(पदनाम\)', r'\(विभाग\)',
        r'\(Signature\)', r'\(Name\)', r'\(Designation\)', r'\(Department\)',
        r'दिनांक\s*:?.*', r'Date\s*:?.*'
    ]
    for p in patterns:
        text = re.sub(p, '', text, flags=re.IGNORECASE)
    
    # Clean up trailing whitespace and multiple newlines
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


def retrieve_best_noting(context: str) -> list:
    """
    Search for multiple best matching notings from the library.
    Prioritize Standard Library (Keyword Match) then ChromaDB (Vector Search).
    """
    matches = []
    
    # 1. Search Standard Library by keywords/text
    std_notings = search_standard_notings(context)
    for item in std_notings[:12]: # Increased from 3 to 12 for better search coverage
        matches.append({
            "id": item["id"],
            "keyword": item.get("keyword", "N/A"),
            "text": item["text"],
            "source": f"Standard Library ({item.get('stage', 'Unknown')})"
        })
    
    # 2. Vector search in ChromaDB (Reference Notings)
    try:
        from modules.rag_engine import _get_chroma_collection
        col = _get_chroma_collection()
        res = col.query(
            query_texts=[context],
            n_results=3,
            where={"category": "Previous Noting (Reference)"}
        )
        
        if res["documents"] and res["documents"][0]:
            for i, doc_text in enumerate(res["documents"][0]):
                cleaned = clean_noting_text(doc_text)
                if cleaned:
                    matches.append({
                        "id": i,
                        "text": cleaned,
                        "category": "Retrieved Template",
                        "score": round(100 - (res["distances"][0][i] * 100), 1) if res.get("distances") else 0
                    })
    except Exception as e:
        logger.error(f"Semantic retrieval failed: {e}")
    
    # Fallback/Supplemental keyword search
    if len(matches) < 2:
        kw_results = search_standard_notings(context)
        for item in kw_results[:5]:
            cleaned = clean_noting_text(item.get("text", ""))
            # Avoid duplicates
            if cleaned and not any(m["text"] == cleaned for m in matches):
                matches.append({
                    "id": len(matches),
                    "text": cleaned,
                    "category": item.get("category", "Matching Example")
                })
                
    return matches


def _strip_markdown_fences(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:html|markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_html_fragment(html: str) -> str:
    cleaned = _strip_markdown_fences(html)
    cleaned = cleaned.replace("\r", "")
    cleaned = re.sub(r"(?i)<div><br></div>", "<br>", cleaned)
    cleaned = re.sub(r"(?i)<div>", "", cleaned)
    cleaned = re.sub(r"(?i)</div>", "<br>", cleaned)
    cleaned = re.sub(r"(?i)(<br\s*/?>\s*){3,}", "<br><br>", cleaned)
    return cleaned.strip()


def _html_to_plain_text(html: str) -> str:
    text = _normalize_html_fragment(html)
    text = re.sub(r"(?i)</(p|tr|li|h[1-6]|blockquote)>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(td|th)>", "\t", text)
    text = re.sub(r"(?i)<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _contains_table_html(html: str) -> bool:
    return bool(TABLE_BLOCK_RE.search(html or ""))


def _extract_table_blocks(html: str) -> list[str]:
    return TABLE_BLOCK_RE.findall(html or "")


def _is_markdown_table_block(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    if "|" not in lines[0] or "|" not in lines[1]:
        return False
    separator = lines[1].strip().strip("|").replace(":", "").replace("-", "").replace(" ", "")
    return separator == ""


def _convert_markdown_tables_to_html(text: str) -> str:
    raw = _strip_markdown_fences(text)
    if "<table" in raw.lower():
        return raw
    lines = [line.rstrip() for line in raw.splitlines()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        if "|" in lines[i]:
            block: list[str] = []
            j = i
            while j < len(lines) and "|" in lines[j]:
                block.append(lines[j].strip())
                j += 1
            if _is_markdown_table_block(block):
                rows = []
                for idx, line in enumerate(block):
                    if idx == 1:
                        continue
                    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                    tag = "th" if idx == 0 else "td"
                    rows.append("<tr>" + "".join(f"<{tag}>{cell or '&nbsp;'}</{tag}>" for cell in cells) + "</tr>")
                out.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
                i = j
                continue
        if lines[i].strip():
            out.append("<p>" + lines[i].strip() + "</p>")
        i += 1
    return "\n".join(out).strip()


def _reconstruct_table_html(source_html: str, refined_html: str, modifications: str, target_lang: str) -> str:
    prompt = f"""You are fixing an official noting HTML fragment.

Original HTML with correct table structure:
{source_html}

Refined content that accidentally flattened the table:
{refined_html}

Additional instructions:
{modifications if modifications else "[EMPTY]"}

Rules:
1. Return ONLY valid HTML.
2. Preserve the original table structure and keep it as real HTML <table>/<tr>/<td>/<th>.
3. Never convert table rows into paragraphs.
4. Refine/translate the cell text and paragraph text into {"formal Rajbhasha Hindi" if target_lang == "hindi" else "formal official English"}.
5. No markdown fences.
"""
    try:
        return _normalize_html_fragment(ask_gemini(prompt))
    except Exception as e:
        logger.warning(f"HTML table reconstruction failed: {e}")
        return ""


def refine_and_translate(text: str, modifications: str, target_lang: str = "hindi") -> str:
    """
    Take basic noting text + user modifications and produce a refined, formal official noting.
    Prompt: Translate into formal Hindi without Subject, refine and re-arrange the para.
    """
    from modules.utils import ask_gemini
    
    # --- CONTINUOUS LEARNING: Learn style from drafted noting library ---
    user_style_examples = ""
    try:
        style_context = " ".join(part for part in [text, modifications] if part).strip()
        examples = get_user_style_examples(limit=4, context=style_context)
        if examples:
            user_style_examples = "\n=== नोटिंग लाइब्रेरी से सीखी गई उपयोगकर्ता की लेखन शैली (Style Examples from Library) ===\n"
            for i, ex in enumerate(examples):
                user_style_examples += f"उदाहरण {i+1}:\n{ex}\n---\n"
    except Exception: pass
    style_summary = get_user_style_summary(context=" ".join(part for part in [text, modifications] if part).strip())
    learning_instructions = get_noting_learning_instructions()

    prompt = f"""You are an expert Indian Government Official (Dealing Hand) at GSI. 
I have a draft noting and some additional context/modifications. 

Draft/Template:
{text}

Additional Context/Instructions:
{modifications if modifications else "[EMPTY - DO NOT REPHRASE OR REARRANGE. ONLY TRANSLITERATE HINGLISH/ENGLISH WORDS]"}

{user_style_examples}
{style_summary}
{learning_instructions}

**CORE TASK:**
1. **Behavior based on Instructions**:
   - **If Instructions is "[EMPTY...]":** Stay **100% faithful** to the original sentence structure and word order provided in the "Draft/Template". DO NOT rephrase, do not rearrange paragraphs, and do not summarize. Only focus on **Transliterating/Translating** any Hinglish or English words into formal Hindi (e.g., if you see "utpann" transliterate it to "उत्पन्न", do not change it to "पैदा" or "शुरू").
   - **If Instructions are PROVIDED:** Incorporate the specific modifications/context naturally while following the tone from style examples.

2. **Style Mimicry**: Analyze the "उपयोगकर्ता की पसंदीदा लेखन शैली" (User's Preferred Style Examples). Adopt this tone and vocabulary.
3. **Translation**: Convert the noting into highly formal "Rajbhasha" (Official Hindi). 
4. **Hinglish/Transliteration Rule**: Look specifically for Hinglish words (Roman script Hindi) typed by the user. Convert them to the correct Devanagari equivalent without changing the meaning or surrounding structure.
5. **Clean Subject**: REMOVE any "Subject" or "विषय" line if present.
6. **Standardized Ending**: **FINALIZE** the noting with this exact phrase: "फाइल आपके अवलोकनार्थ प्रस्तुत है ।"
7. **NEGATIVE CONSTRAINTS**: 
   - NEVER use the phrase: **"कृपया आवश्यक संज्ञान लें। (GFR के अनुरूप कार्यवाही सुनिश्चित करें)"**.
   - NEVER use the word **"प्रकरण" (prakaran)**. Use "मामले", "विषय", or similar formal terms instead.

Provide ONLY the final refined Hindi text.

Refined Noting (Hindi):
"""
    try:
        result = ask_gemini(prompt)
        return apply_learned_noting_patterns(result.strip())
    except Exception as e:
        logger.error(f"Refinement failed: {e}")
        return text


def refine_and_translate_rich(
    text: str,
    modifications: str,
    target_lang: str = "hindi",
    source_html: str = "",
) -> tuple[str, str]:
    source_html = (source_html or "").strip()
    if not _contains_table_html(source_html):
        refined_text = refine_and_translate(text, modifications, target_lang)
        return refined_text, ""

    style_context = " ".join(part for part in [_html_to_plain_text(source_html), modifications] if part).strip()
    user_style_examples = ""
    try:
        examples = get_user_style_examples(limit=4, context=style_context)
        if examples:
            user_style_examples = "\n=== नोटिंग लाइब्रेरी से सीखी गई उपयोगकर्ता की लेखन शैली (Style Examples from Library) ===\n"
            for i, ex in enumerate(examples):
                user_style_examples += f"उदाहरण {i+1}:\n{ex}\n---\n"
    except Exception:
        pass
    style_summary = get_user_style_summary(context=style_context)
    learning_instructions = get_noting_learning_instructions()
    language_instruction = "highly formal Rajbhasha Hindi" if target_lang == "hindi" else "formal official English"

    prompt = f"""You are an expert Indian Government Official (Dealing Hand) at GSI.
I have a draft noting in HTML. It contains one or more tables that MUST remain as tables.

Draft/Template HTML:
{source_html}

Additional Context/Instructions:
{modifications if modifications else "[EMPTY - DO NOT REPHRASE OR REARRANGE. ONLY TRANSLITERATE/TRANSLATE WORDS AS NEEDED]"}

{user_style_examples}
{style_summary}
{learning_instructions}

CORE RULES:
1. Return ONLY valid HTML fragment. No markdown fences, no explanations.
2. Preserve all table content as real HTML tables using <table>, <tr>, <td>, <th>.
3. NEVER convert a table into paragraphs, bullet points, or plain lines.
4. You may refine/translate wording inside paragraph text and inside table cells.
5. Use {language_instruction}.
6. Remove any Subject/विषय line if present.
7. Finalize the noting with the exact phrase "फाइल आपके अवलोकनार्थ प्रस्तुत है ।" if the output is Hindi.
"""

    try:
        refined_html = _normalize_html_fragment(ask_gemini(prompt))
    except Exception as e:
        logger.error(f"HTML refinement failed: {e}")
        return text, source_html

    if not _contains_table_html(refined_html):
        converted = _convert_markdown_tables_to_html(refined_html)
        refined_html = _normalize_html_fragment(converted)

    if not _contains_table_html(refined_html):
        repaired = _reconstruct_table_html(source_html, refined_html, modifications, target_lang)
        if _contains_table_html(repaired):
            refined_html = repaired

    if not _contains_table_html(refined_html):
        logger.warning("Refinement output lost table structure; falling back to original HTML table layout.")
        refined_html = source_html

    refined_text = apply_learned_noting_patterns(_html_to_plain_text(refined_html))
    return refined_text, refined_html


def list_noting_types() -> list:
    """Return all available noting types."""
    return NOTING_TYPES

def get_user_style_examples(limit: int = 3, context: str = "") -> list:
    """
    Fetch style examples from the noting library.
    Prioritizes user-edited/custom entries and context-relevant drafted notes,
    then falls back to the wider library so the AI learns the user's drafting style.
    """
    all_notings = load_standard_notings()
    if not all_notings:
        return []

    valid_items = [item for item in all_notings if _normalize_style_text(item.get("text", ""))]
    if not valid_items:
        return []

    query_words = set(re.findall(r"\w+", (context or "").lower()))
    ranked = sorted(
        valid_items,
        key=lambda item: (
            _score_style_noting(item, query_words),
            item.get("updated_at", "")
        ),
        reverse=True
    )

    selected = []
    seen = set()
    for item in ranked:
        text = _normalize_style_text(item.get("text", ""))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(text)
        if len(selected) >= limit:
            break

    return selected


if __name__ == "__main__":
    # Quick test
    text = generate_noting_text(
        additional_context="Request for procurement of 5 laptops for the IT department due to aging hardware."
    )
    print(text[:500])
