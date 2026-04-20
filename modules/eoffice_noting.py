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
from html import escape, unescape
from pathlib import Path
from modules.utils import (
    CONFIG,
    DEFAULT_EMAIL_MASTER_PROMPT,
    DEFAULT_NOTING_MASTER_PROMPT,
    logger,
    BUNDLE_ROOT,
    DATA_ROOT,
    PROCUREMENT_STAGES_PATH,
    STANDARD_LIBRARY_PATH,
    ask_gemini,
    get_case_folder,
    create_docx_from_html,
    sanitize_filename,
    today_str,
)

# Paths used by the email drafting feature. Stored in the user's data directory.
EMAIL_CATEGORIES_PATH = DATA_ROOT / "email_categories.json"
EMAIL_LIBRARY_PATH = DATA_ROOT / "email_library.json"
from modules.database import (
    get_app_setting,
    get_noting_learning_patterns,
    upsert_noting_learning_pattern,
    get_all_stages,
    set_stages,
    get_all_library_notings,
    add_noting_to_library,
    update_noting_in_library,
    delete_noting_from_library,
    delete_notings_by_stages,
    search_noting_library,
    get_all_email_categories,
    set_email_categories,
    get_all_library_emails,
    add_email_to_library,
    update_email_in_library,
    delete_email_from_library,
    delete_emails_by_categories,
    search_email_library,
)


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
EMAIL_FORBIDDEN_CLOSING_VARIANTS = (
    "\u092b\u093e\u0907\u0932 \u0906\u092a\u0915\u0947 \u0905\u0935\u0932\u094b\u0915\u0928\u093e\u0930\u094d\u0925 \u092a\u094d\u0930\u0938\u094d\u0924\u0941\u0924 \u0939\u0948 \u0964",
    "\u092b\u093e\u0907\u0932 \u0906\u092a\u0915\u0947 \u0905\u0935\u0932\u094b\u0915\u0928\u093e\u0930\u094d\u0925 \u092a\u094d\u0930\u0938\u094d\u0924\u0941\u0924 \u0939\u0948\u0964",
    "\u095e\u093e\u0907\u0932 \u0906\u092a\u0915\u0947 \u0905\u0935\u0932\u094b\u0915\u0928\u093e\u0930\u094d\u0925 \u092a\u094d\u0930\u0938\u094d\u0924\u0941\u0924 \u0939\u0948 \u0964",
    "\u095e\u093e\u0907\u0932 \u0906\u092a\u0915\u0947 \u0905\u0935\u0932\u094b\u0915\u0928\u093e\u0930\u094d\u0925 \u092a\u094d\u0930\u0938\u094d\u0924\u0941\u0924 \u0939\u0948\u0964",
)

def get_noting_master_prompt() -> str:
    """Return the configurable noting master prompt."""
    return get_app_setting("noting_master_prompt", DEFAULT_NOTING_MASTER_PROMPT)


def get_email_master_prompt() -> str:
    """Return the configurable email drafting master prompt."""
    return get_app_setting("email_master_prompt", DEFAULT_EMAIL_MASTER_PROMPT)


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

    return "\n<learned_terminology_preferences>\nApply these wording preferences ONLY to the refined text:\n" + "\n".join(lines) + "\n</learned_terminology_preferences>\n"


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
        "<library_style_learning_meta>",
        "Observe the user's drafting language and writing pattern from the noting library.",
        preferred_language,
        "Prefer concise, official file-note paragraphs with procurement terminology aligned to the library."
    ]
    for phrase in repeated_phrases:
        lines.append(f"- Reuse phrasing patterns similar to: {phrase}")
    lines.append("</library_style_learning_meta>")

    return "\n" + "\n".join(lines) + "\n"


def _build_refinement_style_context(text: str = "", modifications: str = "", source_html: str = "") -> tuple[str, str, str]:
    """Collect style examples, summary, and learned wording preferences for refinement prompts."""
    style_context = " ".join(
        part for part in [_html_to_plain_text(source_html), text, modifications] if part
    ).strip()

    user_style_examples = ""
    try:
        examples = get_user_style_examples(limit=4, context=style_context)
        if examples:
            user_style_examples = "\n<style_examples_for_reference_only>\n"
            for i, ex in enumerate(examples):
                user_style_examples += f"Example {i+1}:\n{ex}\n---\n"
            user_style_examples += "</style_examples_for_reference_only>\n"
    except Exception:
        pass

    style_summary = get_user_style_summary(context=style_context)
    learning_instructions = get_noting_learning_instructions()
    return user_style_examples, style_summary, learning_instructions


def _append_missing_prompt_blocks(prompt: str, *blocks: str) -> str:
    """Append learning/style blocks if the current template omitted their placeholders."""
    updated = prompt.rstrip()
    for block in blocks:
        if not block:
            continue
        block_text = block.strip()
        if block_text and block_text not in updated:
            updated = f"{updated}\n{block_text}"
    return updated


def _normalize_terminal_line(text: str) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    return compact.replace("।", "").replace(".", "").strip().casefold()


def _strip_forbidden_email_closing_text(text: str) -> str:
    """Remove file-note style closing lines from email output."""
    if not text:
        return text

    forbidden = {_normalize_terminal_line(item) for item in EMAIL_FORBIDDEN_CLOSING_VARIANTS}
    lines = text.splitlines()
    while lines and _normalize_terminal_line(lines[-1]) in forbidden:
        lines.pop()
    return "\n".join(lines).strip()


def _strip_forbidden_email_closing_html(html: str) -> str:
    """Remove paragraphs/divs that only contain the forbidden noting closing."""
    if not html:
        return html

    updated = html
    for phrase in EMAIL_FORBIDDEN_CLOSING_VARIANTS:
        pattern = re.compile(
            rf"<(p|div)>\s*{re.escape(phrase)}\s*</\1>",
            re.IGNORECASE,
        )
        updated = pattern.sub("", updated)
    return updated.strip()


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
    final_text = apply_learned_noting_patterns(ai_body.strip())
    
    # If the text contains markdown tables, convert them to HTML for the editor
    lines = final_text.splitlines()
    has_md_table = False
    for i in range(len(lines) - 1):
        if "|" in lines[i] and "|" in lines[i+1] and _is_markdown_table_block(lines[i:i+2]):
            has_md_table = True
            break
            
    if has_md_table:
        return _convert_markdown_tables_to_html(final_text)
        
    return final_text


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
    create_docx_from_html(text, output_path, title="")
    logger.info(f"Noting saved: {output_path}")
    return output_path


def load_standard_notings() -> list:
    """Read categorized notings from SQL database with one-time JSON migration."""
    sql_data = get_all_library_notings()
    if sql_data:
        # Re-map 'content' to 'text' for frontend compatibility if needed, 
        # or just ensure consistent naming.
        for item in sql_data:
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
        return sql_data

    # Migration from JSON
    library_file = STANDARD_LIBRARY_PATH
    if not library_file.exists():
        return []
    
    import json
    try:
        with open(library_file, "r", encoding="utf-8") as f:
            json_data = json.load(f)
            for item in json_data:
                add_noting_to_library(
                    stage=item.get("stage"),
                    keyword=item.get("keyword"),
                    content=item.get("text") or item.get("content") or "",
                    is_custom=item.get("is_custom", True)
                )
            return get_all_library_notings()
    except Exception as e:
        logger.error(f"Migration/Load failed: {e}")
        return []

def update_library_noting(noting_id: int, updates: dict) -> bool:
    """Update library noting in SQL."""
    # map 'text' to 'content' for DB
    if "text" in updates:
        updates["content"] = updates.pop("text")
    return update_noting_in_library(noting_id, updates)

def add_library_noting(stage: str, keyword: str, text: str) -> bool:
    """Add noting to SQL library."""
    return add_noting_to_library(stage, keyword, text)

def delete_library_notings_by_stages(stages: list) -> int:
    """Remove all library notings whose 'stage' is in the provided list from SQL."""
    if not stages:
        return 0
    return delete_notings_by_stages(stages)

def move_library_noting(noting_id: int, new_stage: str) -> bool:
    """Change the stage of an existing noting in SQL."""
    return update_noting_in_library(noting_id, {"stage": new_stage})

def delete_library_noting(noting_id: int) -> bool:
    """Remove a noting from the SQL library."""
    return delete_noting_from_library(noting_id)

def get_procurement_stages() -> list:
    """Load procurement stages from SQL with one-time JSON migration."""
    sql_stages = get_all_stages()
    if sql_stages:
        return sql_stages
    
    # Migration
    stages_file = PROCUREMENT_STAGES_PATH
    if not stages_file.exists():
        # returns seeded defaults from DB init if file also missing
        return get_all_stages()

    import json
    try:
        with open(stages_file, "r", encoding="utf-8") as f:
            json_stages = json.load(f)
            set_stages(json_stages)
            return json_stages
    except Exception as e:
        logger.error(f"Migration/Load stages failed: {e}")
        return get_all_stages()

def update_procurement_stages(stages: list) -> bool:
    """Update the set of procurement stages in the SQL database."""
    return set_stages(stages)


# ---------------------------------------------------------------------------
# Email drafting helpers
# ---------------------------------------------------------------------------

def load_email_categories() -> list:
    """Return the list of email categories from SQL."""
    return get_all_email_categories()


def save_email_categories(cats: list) -> bool:
    """Save the list of email categories to the SQL database."""
    return set_email_categories(cats)

def load_email_library() -> list:
    """Read email templates from SQL with migration from JSON."""
    sql_data = get_all_library_emails()
    if sql_data:
        for item in sql_data:
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
        return sql_data
    
    # Migration
    if not EMAIL_LIBRARY_PATH.exists():
        return []
    try:
        with open(EMAIL_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                add_email_to_library(
                    category=item.get("stage") or item.get("category"),
                    keyword=item.get("keyword"),
                    content=item.get("text") or item.get("content") or "",
                    is_custom=item.get("is_custom", True)
                )
            return get_all_library_emails()
    except Exception as e:
        logger.error(f"Email library migration failed: {e}")
        return []

def add_library_email(stage: str, keyword: str, text: str) -> bool:
    return add_email_to_library(stage, keyword, text)

def update_library_email(eid: int, updates: dict) -> bool:
    if "text" in updates:
        updates["content"] = updates.pop("text")
    return update_email_in_library(eid, updates)

def move_library_email(eid: int, new_stage: str) -> bool:
    return update_email_in_library(eid, {"category": new_stage})

def delete_library_email(eid: int) -> bool:
    return delete_email_from_library(eid)

def delete_library_emails_by_categories(categories: list) -> int:
    return delete_emails_by_categories(categories)


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


def search_standard_notings(query: str, stage: str = "", limit: int = 10, offset: int = 0, include_total: bool = False) -> any:
    """Filter standard notings based on query keywords with word overlap."""
    all_notings = load_standard_notings()
    
    # Optional stage filter
    if stage and stage != "ALL":
        all_notings = [item for item in all_notings if (item.get("stage") or "").lower() == stage.lower()]

    if not query:
        total = len(all_notings)
        items = all_notings[offset : offset + limit]
        if include_total:
            return items, total
        return items
    
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
    
    scored_items = [item for score, item in results]
    total = len(scored_items)
    items = scored_items[offset : offset + limit]
    
    if include_total:
        return items, total
    return items


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


def _escape_html_cell(value: str) -> str:
    cleaned = (value or "").strip()
    return escape(cleaned, quote=False) if cleaned else "&nbsp;"


def _plain_text_to_html_fragment(text: str) -> str:
    lines = [line.strip() for line in (text or "").replace("\r", "").splitlines()]
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(escape(line, quote=False) for line in paragraph) + "</p>")
            paragraph.clear()

    for line in lines:
        if not line:
            flush_paragraph()
            continue
        paragraph.append(line)

    flush_paragraph()
    return "\n".join(blocks).strip()


def _is_markdown_table_block(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    if "|" not in lines[0] or "|" not in lines[1]:
        return False
    separator = lines[1].strip().strip("|").replace(":", "").replace("-", "").replace(" ", "")
    return separator == ""


def _is_table_separator_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if not any(ch in stripped for ch in "-="):
        return False
    return re.sub(r"[\s|:+\-=]", "", stripped) == ""


def _split_plain_table_row(line: str) -> list[str]:
    stripped = (line or "").strip()
    if not stripped or _is_table_separator_line(stripped):
        return []

    if "\t" in stripped:
        cells = [cell.strip() for cell in stripped.split("\t")]
    elif "|" in stripped:
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    elif re.search(r" {2,}", stripped):
        cells = [cell.strip() for cell in re.split(r" {2,}", stripped)]
    else:
        return []

    while cells and not cells[0]:
        cells.pop(0)
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _plain_table_rows_from_block(block: list[str]) -> list[list[str]]:
    rows = [_split_plain_table_row(line) for line in block if not _is_table_separator_line(line)]
    rows = [row for row in rows if row]
    if len(rows) < 2:
        return []

    column_counts: dict[int, int] = {}
    for row in rows:
        column_counts[len(row)] = column_counts.get(len(row), 0) + 1
    expected_cols = max(column_counts, key=column_counts.get)
    if expected_cols < 2:
        return []

    normalized_rows: list[list[str]] = []
    for row in rows:
        if len(row) == expected_cols:
            normalized_rows.append(row)
        elif len(row) > expected_cols:
            normalized_rows.append(row[: expected_cols - 1] + [" ".join(row[expected_cols - 1:])])
        else:
            normalized_rows.append(row + [""] * (expected_cols - len(row)))
    return normalized_rows


def _rows_to_html_table(rows: list[list[str]], first_row_header: bool = True) -> str:
    rendered_rows: list[str] = []
    for idx, cells in enumerate(rows):
        tag = "th" if first_row_header and idx == 0 else "td"
        rendered_rows.append(
            "<tr>" + "".join(f"<{tag}>{_escape_html_cell(cell)}</{tag}>" for cell in cells) + "</tr>"
        )
    return "<table><tbody>" + "".join(rendered_rows) + "</tbody></table>"


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
                    rows.append("<tr>" + "".join(f"<{tag}>{_escape_html_cell(cell)}</{tag}>" for cell in cells) + "</tr>")
                out.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
                i = j
                continue
        if lines[i].strip():
            out.append("<p>" + escape(lines[i].strip(), quote=False) + "</p>")
        i += 1
    return "\n".join(out).strip()


def _convert_plain_text_tables_to_html(text: str) -> str:
    raw = _strip_markdown_fences(text)
    if "<table" in raw.lower():
        return raw

    lines = [line.rstrip() for line in raw.replace("\r", "").splitlines()]
    out: list[str] = []
    paragraph: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(escape(line, quote=False) for line in paragraph) + "</p>")
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if "|" in stripped:
            block: list[str] = []
            j = i
            while j < len(lines) and "|" in lines[j]:
                block.append(lines[j].strip())
                j += 1
            if _is_markdown_table_block(block):
                flush_paragraph()
                out.append(_convert_markdown_tables_to_html("\n".join(block)))
                i = j
                continue

        if _split_plain_table_row(stripped):
            block = []
            j = i
            while j < len(lines):
                current = lines[j].strip()
                if not current:
                    break
                if _split_plain_table_row(current) or _is_table_separator_line(current):
                    block.append(current)
                    j += 1
                    continue
                break

            rows = _plain_table_rows_from_block(block)
            if rows:
                flush_paragraph()
                out.append(_rows_to_html_table(rows, first_row_header=True))
                i = j
                continue

        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    return "\n".join(out).strip()


def _coerce_table_like_source_to_html(text: str, source_html: str) -> str:
    if _contains_table_html(source_html):
        return source_html

    candidates = []
    plain_from_html = _html_to_plain_text(source_html) if source_html else ""
    for candidate in (plain_from_html, text):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        converted = _convert_plain_text_tables_to_html(candidate)
        if _contains_table_html(converted):
            return converted

    return source_html or _plain_text_to_html_fragment(text)


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


def _build_email_refinement_prompt(
    draft_content: str,
    additional_instructions: str,
    target_lang: str,
    user_style_examples: str,
    style_summary: str,
    learning_instructions: str,
) -> str:
    """Build the email refinement prompt from the DB-backed master template."""
    master_template = get_email_master_prompt()
    prompt_values = {
        "draft_content": draft_content,
        "additional_instructions": additional_instructions or "[EMPTY]",
        "target_language": "formal Rajbhasha Hindi" if target_lang == "hindi" else "formal official English",
        "user_style_examples": user_style_examples,
        "style_summary": style_summary,
        "learning_instructions": learning_instructions,
    }
    try:
        prompt = master_template.format(**prompt_values)
    except Exception as e:
        logger.warning(f"Invalid email master prompt template. Falling back to default. Error: {e}")
        prompt = DEFAULT_EMAIL_MASTER_PROMPT.format(**prompt_values)
    return _append_missing_prompt_blocks(prompt, user_style_examples, style_summary, learning_instructions)


def refine_and_translate(
    text: str,
    modifications: str,
    target_lang: str = "hindi",
    document_type: str = "noting",
) -> str:
    """
    Refine either a noting or email draft using the appropriate master prompt.
    """
    from modules.utils import ask_gemini

    user_style_examples, style_summary, learning_instructions = _build_refinement_style_context(
        text=text,
        modifications=modifications,
    )

    if document_type == "email":
        prompt = _build_email_refinement_prompt(
            draft_content=text,
            additional_instructions=modifications,
            target_lang=target_lang,
            user_style_examples=user_style_examples,
            style_summary=style_summary,
            learning_instructions=learning_instructions,
        )
        try:
            result = ask_gemini(prompt)
            refined = apply_learned_noting_patterns(result.strip())
            return _strip_forbidden_email_closing_text(refined)
        except Exception as e:
            logger.error(f"Email refinement failed: {e}")
            return text

    prompt_template = """You are an expert Indian Government Official (Dealing Hand) at GSI.
I have a draft noting and some additional context/modifications. 

Draft/Template:
{text}

Additional Context/Instructions:
{modifications}

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

**CRITICAL OUTPUT RULE:**
- DO NOT include any headers like "=== ... ===" or meta-tags in your output.
- DO NOT return the "Style Examples" or "Learning Instructions" back to me.
- Return ONLY the final refined and translated noting.

Provide ONLY the final refined Hindi text.

Refined Noting (Hindi):
"""
    try:
        mod_instructions = modifications if modifications else "[EMPTY - DO NOT REPHRASE OR REARRANGE. ONLY TRANSLITERATE HINGLISH/ENGLISH WORDS]"
        prompt = prompt_template.format(
            text=text,
            modifications=mod_instructions,
            user_style_examples=user_style_examples,
            style_summary=style_summary,
            learning_instructions=learning_instructions
        )
        # Inject master prompt rules even for plain refinement
        full_prompt = f"{prompt}\n\nGLOBAL RULES:\n{get_noting_master_prompt()}"
        result = ask_gemini(full_prompt)
        return apply_learned_noting_patterns(result.strip())
    except Exception as e:
        logger.error(f"Refinement failed: {e}")
        return text


def refine_and_translate_rich(
    text: str,
    modifications: str,
    target_lang: str = "hindi",
    source_html: str = "",
    document_type: str = "noting",
) -> tuple[str, str]:
    source_html = (source_html or "").strip()
    normalized_source_html = _coerce_table_like_source_to_html(text, source_html)
    
    # If source has table, OR user explicitly asks for a table/grid/etc, use the rich path
    has_table = _contains_table_html(normalized_source_html)
    wants_table = any(x in (modifications or "").lower() for x in ["table", "grid", "column", "row", "list", "tabular", "excel", "comparison", "price", "statement"])
    
    if not has_table and not wants_table:
        refined_text = refine_and_translate(text, modifications, target_lang, document_type=document_type)
        return refined_text, ""

    source_html = normalized_source_html

    user_style_examples, style_summary, learning_instructions = _build_refinement_style_context(
        modifications=modifications,
        source_html=source_html,
    )
    language_instruction = "highly formal Rajbhasha Hindi" if target_lang == "hindi" else "formal official English"

    if document_type == "email":
        prompt = _build_email_refinement_prompt(
            draft_content=source_html,
            additional_instructions=(
                f"{modifications}\n\nReturn ONLY valid HTML fragment. Preserve any tables as real HTML tables."
                if modifications else
                "Return ONLY valid HTML fragment. Preserve any tables as real HTML tables."
            ),
            target_lang=target_lang,
            user_style_examples=user_style_examples,
            style_summary=style_summary,
            learning_instructions=learning_instructions,
        )
    else:
        logger.info(f"Refining {document_type} in {target_lang}. Source HTML len: {len(source_html)}, Text len: {len(text)}")
        prompt = f"""You are an expert Government Official and Rajbhasha Adhikari. Refine the following draft.
I have a draft noting in HTML. It contains one or more tables that MUST remain as tables.

Draft/Template HTML:
{source_html}

Additional Context/Instructions:
{modifications if modifications else "[EMPTY - DO NOT REPHRASE OR REARRANGE. ONLY TRANSLITERATE/TRANSLATE WORDS AS NEEDED]"}

{user_style_examples}
{style_summary}
{learning_instructions}

GLOBAL NOTING GUIDELINES (RESPECT THESE):
{get_noting_master_prompt()}

CORE RULES:
1. Return ONLY valid HTML fragment. No markdown fences, no explanations.
2. Preserve all table content as real HTML tables using <table>, <tr>, <td>, <th>.
3. NEVER convert a table into paragraphs, bullet points, or plain lines.
4. You may refine/translate wording inside paragraph text and inside table cells.
5. Use {language_instruction}.
6. Remove any Subject/विषय line if present.
7. Finalize the noting with the exact phrase "फाइल आपके अवलोकनार्थ प्रस्तुत है ।" if the output is Hindi.

**TASK:**
- If the instruction is to create or convert content into a table, you MUST generate a valid HTML <table>.
- If the source content is plain text but the user wants a table/grid, extract the entities/values and put them in a table.
- Return ONLY the final refined noting as an HTML fragment.
"""

    try:
        raw_response = ask_gemini(prompt)
        logger.debug(f"AI Response received ({len(raw_response)} bytes)")
        refined_html = _normalize_html_fragment(raw_response)
    except Exception as e:
        logger.error(f"HTML refinement failed: {e}")
        return text, source_html

    if not _contains_table_html(refined_html):
        converted = _convert_plain_text_tables_to_html(refined_html)
        refined_html = _normalize_html_fragment(converted)

    if not _contains_table_html(refined_html):
        repaired = _reconstruct_table_html(source_html, refined_html, modifications, target_lang)
        if _contains_table_html(repaired):
            refined_html = repaired

    if not _contains_table_html(refined_html):
        logger.warning("Refinement output lost table structure; falling back to original HTML table layout.")
        refined_html = source_html

    if document_type == "email":
        refined_html = _strip_forbidden_email_closing_html(refined_html)
        refined_text = _strip_forbidden_email_closing_text(
            apply_learned_noting_patterns(_html_to_plain_text(refined_html))
        )
        return refined_text, refined_html

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

# ── BRIDGE FUNCTIONS FOR DASHBOARD / DATABASE ──────────────────────────────────

def load_standard_notings() -> list:
    """Wrapper for DB-backed noting library."""
    try:
        data = get_all_library_notings()
        # Normalizing 'content' to 'text' for legacy compatibility
        for item in data:
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
        return data
    except Exception as e:
        logger.error(f"Error loading standard notings: {e}")
        return []

def search_standard_notings(
    query: str = "",
    stage: str = "",
    limit: int | None = None,
    offset: int = 0,
    include_total: bool = False,
):
    """Search the library by stage, keyword, or content using optimized SQL."""
    try:
        result = search_noting_library(
            query,
            stage=stage,
            limit=limit,
            offset=offset,
            include_total=include_total,
        )
        if include_total:
            data, total = result
        else:
            data = result
        # Normalizing 'content' to 'text' for legacy compatibility
        for item in data:
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
        if include_total:
            return data, total
        return data
    except Exception as e:
        logger.error(f"Search failed: {e}")
        if include_total:
            return [], 0
        return []

def retrieve_best_noting(context: str) -> list:
    """Find the most relevant templates based on context scoring."""
    all_notings = load_standard_notings()
    if not all_notings:
        return []
    
    query_words = set(re.findall(r"\w+", context.lower()))
    scored = []
    for item in all_notings:
        score = _score_style_noting(item, query_words)
        if score > 0:
            scored.append((score, item))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:5]]

def add_library_noting(stage: str, keyword: str, text: str) -> bool:
    return add_noting_to_library(stage, keyword, text)

def update_library_noting(noting_id: int, updates: dict) -> bool:
    # Rename 'text' to 'content' if present in updates for DB compatibility
    if "text" in updates:
        updates["content"] = updates.pop("text")
    return update_noting_in_library(noting_id, updates)

def move_library_noting(noting_id: int, new_stage: str) -> bool:
    return update_noting_in_library(noting_id, {"stage": new_stage})

def delete_library_noting(noting_id: int) -> bool:
    return delete_noting_from_library(noting_id)

def delete_library_notings_by_stages(stages: list) -> int:
    return delete_notings_by_stages(stages)

# Email Library
def load_email_library(query: str = "") -> list:
    """Wrapper for DB-backed email library with optimized SQL search."""
    try:
        data = search_email_library(query)
        for item in data:
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
            if "category" in item and "stage" not in item:
                item["stage"] = item["category"]
        return data
    except Exception as e:
        logger.error(f"Failed to load email library: {e}")
        return []

def add_library_email(category: str, keyword: str, text: str) -> bool:
    return add_email_to_library(category, keyword, text)

def update_library_email(item_id: int, updates: dict) -> bool:
    if "text" in updates:
        updates["content"] = updates.pop("text")
    return update_email_in_library(item_id, updates)

def move_library_email(item_id: int, new_category: str) -> bool:
    return update_email_in_library(item_id, {"category": new_category})

def delete_library_email(item_id: int) -> bool:
    return delete_email_from_library(item_id)

def delete_library_emails_by_categories(categories: list) -> int:
    return delete_emails_by_categories(categories)

# Stages & Categories
def get_procurement_stages() -> list:
    return get_all_stages()

def update_procurement_stages(stages: list) -> bool:
    return set_stages(stages)

def load_email_categories() -> list:
    return get_all_email_categories()

def save_email_categories(categories: list) -> bool:
    return set_email_categories(categories)

def translate_noting_llm(text: str, target_lang: str = "hindi") -> str:
    """Wrapper for translation logic."""
    from modules.eoffice_noting import translate_noting
    return translate_noting(text, target_lang)


if __name__ == "__main__":
    # Quick test
    text = generate_noting_text(
        additional_context="Request for procurement of 5 laptops for the IT department due to aging hardware."
    )
    print(text[:500])
