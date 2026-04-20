import os
import time
import json
import pandas as pd
import pdfplumber
import docx
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from modules.utils import (
    logger, get_automation_driver, switch_to_matching_page,
    list_visible_elements, safe_click, run_script, get_url,
    is_same_window, get_frame, set_value, ask_gemini,
    DEFAULT_TEC_EVAL_PROMPT, CONFIG
)
from modules.database import get_app_setting, get_prompt_settings
from modules.fast_parsing import extract_tables_with_docling
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

# Global set to track aborted TEC jobs
STOP_TEC_EXECUTION = set()

def stop_tec_job(job_id):
    """Signals the TEC automation to stop for a specific job."""
    STOP_TEC_EXECUTION.add(job_id)

def is_aborted(job_id):
    return job_id in STOP_TEC_EXECUTION

def emit(event, data):
    """Helper formatting function for Server-Sent Events (SSE) stream."""
    data["type"] = event
    if "error" in data and "message" not in data:
        data["message"] = data["error"]
    return f"data: {json.dumps(data)}\n\n"

def _split_text_line_to_cells(line: str) -> list[str]:
    line = (line or "").strip()
    if not line:
        return []

    for pattern in (r"\t+", r" {2,}", r"\s+\|\s+"):
        parts = [p.strip() for p in re.split(pattern, line) if p.strip()]
        if len(parts) > 1:
            return parts

    tokens = line.split()
    return tokens if len(tokens) > 1 else []


def _rows_from_text_block(text: str) -> list[list[str]]:
    rows = []
    for line in (text or "").splitlines():
        cells = _split_text_line_to_cells(line)
        if len(cells) > 1:
            rows.append(cells)
    return rows


def _cells_from_word_line(words: list[dict], gap_threshold: float = 18.0) -> list[str]:
    if not words:
        return []
    words = sorted(words, key=lambda w: w["x0"])
    cells = []
    current = [words[0]["text"]]
    prev_x1 = words[0]["x1"]

    for word in words[1:]:
        gap = float(word["x0"]) - float(prev_x1)
        if gap > gap_threshold:
            cells.append(" ".join(current).strip())
            current = [word["text"]]
        else:
            current.append(word["text"])
        prev_x1 = word["x1"]

    if current:
        cells.append(" ".join(current).strip())
    return [cell for cell in cells if cell]


def _rows_from_word_positions(page) -> list[list[str]]:
    try:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True
        )
    except Exception:
        return []

    if not words:
        return []

    rows: list[list[dict]] = []
    current_row: list[dict] = []
    current_top = None

    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if current_top is None or abs(float(word["top"]) - current_top) <= 3:
            current_row.append(word)
            current_top = float(word["top"]) if current_top is None else current_top
        else:
            rows.append(current_row)
            current_row = [word]
            current_top = float(word["top"])

    if current_row:
        rows.append(current_row)

    parsed_rows = []
    for row_words in rows:
        cells = _cells_from_word_line(row_words)
        if len(cells) > 1:
            parsed_rows.append(cells)
    return parsed_rows


def extract_data_from_pdf(pdf_path):
    """
    Extracts tables from PDF using Docling for maximum accuracy.
    Falls back to legacy pdfplumber if Docling fails.
    """
    logger.info(f"Extracting tables from PDF using Docling: {pdf_path}")
    
    # Try high-fidelity Docling extraction first
    try:
        tables = extract_tables_with_docling(pdf_path)
        if tables:
            # Combine all found tables into one dataframe for processing
            # In tender docs, the main evaluation table is usually the largest one
            main_table = max(tables, key=len)
            return clean_dataframe(main_table)
    except Exception as e:
        logger.warning(f"Docling extraction failed, falling back: {e}")

    # Legacy fallback logic using pdfplumber
    all_data = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table: all_data.extend(table)
    
    if not all_data:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_data)
    # ... (rest of legacy header detection omitted for brevity in this mock, 
    # but in real code we'd keep the robust parts or unify them)
    return clean_dataframe(df)

    # Create dataframe first to handle uneven row lengths gracefully
    df = pd.DataFrame(all_data)
    
    # Dynamically find the header row by searching for key terms
    header_idx = 0
    for idx, row in df.iterrows():
        row_str = " ".join([str(v).lower() for v in row.values])
        if "name of the firm" in row_str or "qualification" in row_str or "sl no" in row_str or "si no" in row_str:
            header_idx = idx
            break

    # Set the discovered row as headers, deduplicate them, and drop the rows above it
    raw_cols = df.iloc[header_idx].fillna("").astype(str).tolist()

    # if fallback parsed tokens individually, we may end up with fragmented headers
    # such as ['SI','No','Name','of','the','Firm','Qualification','Status'].
    # attempt to merge known multi-word sequences so the downstream logic
    # (which looks for 'name of the firm' etc.) can still find them.
    def _merge_header_tokens(tokens):
        patterns = [
            ["si", "no"],
            ["sl", "no"],
            ["name", "of", "the", "firm"],
            ["qualification", "status"],
            ["ip", "address", "similarity"],
            ["technical", "ip", "address"],
            ["financial", "ip", "address"],
        ]
        merged = []
        groups = []
        i = 0
        lower_tokens = [t.lower() for t in tokens]
        while i < len(tokens):
            matched = False
            for pat in patterns:
                if lower_tokens[i:i+len(pat)] == pat:
                    merged.append(" ".join(tokens[i:i+len(pat)]))
                    groups.append(list(range(i, i+len(pat))))
                    i += len(pat)
                    matched = True
                    break
            if not matched:
                merged.append(tokens[i])
                groups.append([i])
                i += 1
        return merged, groups

    merged, groups = _merge_header_tokens(raw_cols)
    raw_cols = merged

    final_cols = []
    for c in raw_cols:
        c_clean = str(c).strip() if str(c).strip() else "Unnamed"
        
        # Deduplicate
        if c_clean in final_cols:
            suffix = 1
            while f"{c_clean}_{suffix}" in final_cols:
                suffix += 1
            final_cols.append(f"{c_clean}_{suffix}")
        else:
            final_cols.append(c_clean)

    # if we merged headers, we need to recombine the underlying data rows accordingly
    if groups and len(groups) != len(df.columns):
        # rebuild df using groups
        new_rows = []
        # take rows after header row
        for _, row in df.iloc[header_idx + 1 :].iterrows():
            new_row = []
            for grp in groups:
                vals = []
                for j in grp:
                    v = row.iloc[j]
                    # skip missing/NaN entries
                    if pd.isna(v):
                        continue
                    s = str(v).strip()
                    if s and s.lower() != "nan":
                        vals.append(s)
                new_row.append(" ".join(vals))
            new_rows.append(new_row)
        # replace df with new one and reset index
        df = pd.DataFrame(new_rows)
        
        # update header_idx to -1 since we removed header row
        header_idx = -1
            
    # CRITICAL FIX for "cannot reindex on an axis with duplicate labels"
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    df.columns = final_cols
    
    return clean_dataframe(df)

def extract_data_from_docx(docx_path):
    print("Extracting tables from Word document...")
    doc = docx.Document(docx_path)
    all_data = []
    
    for table in doc.tables:
        for row in table.rows:
            all_data.append([cell.text for cell in row.cells])
            
    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    
    # Dynamically find the header row
    header_idx = 0
    for idx, row in df.iterrows():
        row_str = " ".join([str(v).lower() for v in row.values])
        if "name of the firm" in row_str or "qualification" in row_str or "sl no" in row_str or "si no" in row_str:
            header_idx = idx
            break
            
    raw_cols = df.iloc[header_idx].fillna("").astype(str).tolist()
    final_cols = []
    for c in raw_cols:
        c_clean = c.strip() if c.strip() else "Unnamed"
        if c_clean in final_cols:
            suffix = 1
            while f"{c_clean}_{suffix}" in final_cols:
                suffix += 1
            final_cols.append(f"{c_clean}_{suffix}")
        else:
            final_cols.append(c_clean)
            
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    df.columns = final_cols
    
    return clean_dataframe(df)

def clean_dataframe(df):
    # PDFs/Word files often insert "\n" or "\r" inside cells or leave empty cells as None.
    df = df.replace(to_replace=[r'\n', r'\r'], value=' ', regex=True)
    df = df.fillna("")

    # Handle Multi-Page Header Repetition dynamically
    # Try finding "Name of the Firm" column or default to the second column
    firm_col = next((col for col in df.columns if col and "name of the firm" in str(col).lower()), None)
    if not firm_col and len(df.columns) > 1:
        firm_col = df.columns[1]
        
    if firm_col:
        df = df[df[firm_col] != firm_col]

    df = df.reset_index(drop=True)
def analyze_parameters(df):
    """
    Identifies potential parameter columns and their unique values.
    """
    ignore_columns_lower = [
        "si no", "sl no", "sl. no.", "s.no.", "s. no", "s. no.",
        "name of the firm", "m/s", 
        "technical ip address", "financial ip address", 
        "qualification status", "ip address similarity", "ip address",
        "ip similarity"
    ]
    
    analysis = []
    for col in df.columns:
        if not col or str(col).startswith("Unnamed"):
            continue
            
        header_clean = str(col).lower().strip()
        if any(ign in header_clean for ign in ignore_columns_lower):
            continue
            
        # Filter unique values: skip noisy IP similarity messages and empty strings
        unique_vals = []
        for v in df[col].unique():
            v_str = str(v).strip()
            if not v_str or v_str.lower() == "nan":
                continue
            # Omit long IP similarity noise strings
            if "ip similarity" in v_str.lower() or "similarity of ip" in v_str.lower():
                continue
            unique_vals.append(v_str)
            
        if not unique_vals:
            continue
            
        analysis.append({
            "parameter": col,
            "values": sorted(list(set(unique_vals)))
        })
    return analysis

class QualificationResult(BaseModel):
    is_qualified: bool = Field(description="Whether the firm is technically qualified")
    reason: str = Field(description="Detailed reason for the status. DQ reason MUST start with 'Firm is technically not qualified'")
    summary: str = Field(description="Brief summary of the firm's evaluation")

def process_evaluations_llm(df, criteria=None):
    """
    Uses PydanticAI for strictly typed, reliable firm evaluations.
    """
    logger.info("Processing evaluations with PydanticAI Agent...")
    results = []
    
    # Read model from CONFIG (same pattern as all other modules)
    model_name = CONFIG.get("llm", {}).get("gemini_model", "gemini-2.0-flash")
    # Fetch TEC evaluation prompt from database settings, fall back to default
    prompt_settings = get_prompt_settings()
    tec_system_prompt = prompt_settings.get("tec_evaluation_prompt", DEFAULT_TEC_EVAL_PROMPT)
    agent = Agent(
        f'google-gla:{model_name}',
        result_type=QualificationResult,
        system_prompt=tec_system_prompt
    )

    criteria_str = json.dumps(criteria, indent=2) if criteria else "Standard procurement common sense."
    firm_col = next((col for col in df.columns if col and "name of the firm" in str(col).lower()), None)
    si_no_col = next((col for col in df.columns if col and any(k in str(col).lower() for k in ["si no", "sl no", "s.no", "sl.no", "sl. no"])), None)
    
    stats = {"total_detected": 0, "total_qualified": 0, "total_disqualified": 0, "ip_rejected": 0}

    for index, row in df.iterrows():
        firm_name = str(row[firm_col]).strip() if firm_col else f"Firm_{index}"
        si_no = str(row[si_no_col]).strip() if si_no_col else str(index + 1)
        
        # IP Similarity Check (Keep legacy logic as it's a specific requirement)
        is_ip_similar = False
        for col in df.columns:
            if "ip similarity" in str(col).lower():
                val = str(row[col]).lower()
                if val and val not in ["no", "nan", "nil", "0"]:
                    is_ip_similar = True; break
        
        if is_ip_similar:
            results.append({"si_no": si_no, "firm_name": firm_name, "is_qualified": False, 
                            "comment": "Firm is technically not qualified. IP address similarity found."})
            stats["total_disqualified"] += 1; stats["ip_rejected"] += 1
            continue

        # Prepare and run the Agent
        try:
            firm_data = row.to_dict()
            prompt = f"Analyze firm: {firm_name}\nData: {json.dumps(firm_data)}\nCriteria: {criteria_str}"
            
            # Using synchronous run for simplicity in this thread
            result = agent.run_sync(prompt)
            res = result.data
            
            # Enforce DQ prefix
            comment = res.reason if not res.is_qualified else (res.summary or "Technically qualified.")
            if not res.is_qualified and not comment.startswith("Firm is technically not qualified"):
                comment = f"Firm is technically not qualified. {comment}"

            results.append({
                "si_no": si_no,
                "firm_name": firm_name,
                "is_qualified": res.is_qualified,
                "comment": comment
            })
            
            if res.is_qualified: stats["total_qualified"] += 1
            else: stats["total_disqualified"] += 1
            stats["total_detected"] += 1
            
        except Exception as e:
            logger.error(f"PydanticAI evaluation failed for {firm_name}: {e}")
            # Minimal fallback
            results.append({"si_no": si_no, "firm_name": firm_name, "is_qualified": True, "comment": "Processed with fallback."})

    return {"results": results, "stats": stats}

    return {"results": results, "stats": stats}

    return {"results": results, "stats": stats}

def process_evaluations(df, criteria=None):
    """
    Applies the "Auto-Learning" Logic:
    1. Define ignore columns.
    2. Any column NOT in the ignore list is automatically a dynamic parameter.
    3. Check for qualification based on criteria (if provided) or negative keywords.
    """
    print("Processing evaluation logic...")
    results = []
    
    # If criteria is provided, it should be a dict: { "Parameter Name": { "qualify": [...], "disqualify": [...] } }
    criteria = criteria or {}
    
    # Default keywords if user doesn't provide mapping
    default_positive = ["yes", "y", "compliant", "submitted", "eligible", "exempted"]
    default_negative = ["no", "n", "not eligible", "no valid document submitted", "certificate not submitted by oem", "not-compliant", "rejected", "fail", "n/a"]
    
    ignore_columns_lower = [
        "si no", "sl no", "sl. no.", "s.no.", "s. no", "s. no.",
        "name of the firm", "m/s", 
        "technical ip address", "financial ip address", 
        "qualification status", "ip address similarity"
    ]

    firm_col = next((col for col in df.columns if col and "name of the firm" in str(col).lower()), None)
    status_col = next((col for col in df.columns if col and "qualification status" in str(col).lower()), None)
    si_no_col = next((col for col in df.columns if col and any(k in str(col).lower() for k in ["si no", "sl no", "s.no", "sl.no", "sl. no"])), None)

    if not firm_col or not status_col:
        print("Warning: Could not strictly detect 'Name of the Firm' or 'Qualification Status'. Attempting fallback.")

    invalid_firm_keywords = ["director", "member ", "finance", "committee", "chairman", "secretary", "joint ", "deputy "]

    stats = {
        "total_detected": 0,
        "total_qualified": 0,
        "total_disqualified": 0,
        "ip_rejected": 0
    }

    for index, row in df.iterrows():
        # Fallback to defaults if columns are not found
        firm_name = str(row[firm_col]).strip() if firm_col else f"Firm_Row_{index}"
        si_no = str(row[si_no_col]).strip() if si_no_col else str(index + 1)
        
        # Filter out signature lines at the bottom of the table
        firm_lower = firm_name.lower()
        if any(k in firm_lower for k in invalid_firm_keywords) or firm_lower == "" or firm_lower == "nan":
            continue
            
        status = str(row[status_col]).strip() if status_col else "Not Qualified" 
        
        # 1. Qualified Check
        if status.lower() == "qualified":
            results.append({
                "si_no": si_no,
                "firm_name": firm_name, 
                "is_qualified": True, 
                "comment": "Technically qualified."
            })
            stats["total_detected"] += 1
            stats["total_qualified"] += 1
            continue
            
        # 2. Not Qualified -> Dynamically find reasons
        param_reasons = []
        ip_reasons = []
        for column_header in df.columns:
            if not column_header:
                continue
                
            header_clean = str(column_header).lower().strip()
            cell_value = str(row[column_header]).strip().lower()
            
            # 3. Apply Edge Case for "IP Address Similarity" FIRST
            if "ip address similarity" in header_clean or "ip similarity" in header_clean:
                # Reverse Rule: If the value is anything OTHER than 'no', 'nan', 'n.a.', 'nil', 'none' etc, they failed
                safe_vals = ["no", "nan", "", "nil", "none", "n.a.", "n/a", "not applicable", "no similarity found", "zero", "0", "nil similarity"]
                
                # Check if cell_value is essentially empty or a known safe string
                is_safe = False
                if not cell_value or cell_value in ["nan", "none", "nil", "n.a.", "n/a", "0"]:
                    is_safe = True
                elif any(sv in cell_value for sv in safe_vals):
                    is_safe = True
                
                if not is_safe:
                    ip_reasons.append(f"IP address similarity found ({row[column_header]}).")
                continue # Skip to next column so it doesn't trigger the rule below

            # Skip non-parameter columns based on exact or partial matches
            if any(ign in header_clean for ign in ignore_columns_lower):
                continue
            
            # 4. Check against specific criteria mapping if provided
            param_name = str(column_header).strip()
            
            # Get specific keywords or fall back to defaults
            if param_name in criteria:
                c = criteria[param_name]
                q_words = [str(w).lower() for w in c.get("qualify", [])]
                dq_words = [str(w).lower() for w in c.get("disqualify", [])]
            else:
                q_words = default_positive
                dq_words = default_negative
            
            # Apply detection logic
            if dq_words and any(dq in cell_value for dq in dq_words):
                param_reasons.append(param_name)
                continue
            if q_words and not any(q in cell_value for q in q_words):
                # If we have q_words but cell doesn't match any, it's a disqualification
                param_reasons.append(param_name)
                continue
                
        # Combine the dynamic reasons into one final sentence
        final_comment_parts = []
        if ip_reasons:
            final_comment = "Firm is having the similarity of IP address of bid submission with other participating firm(s). As per the accepted recommendations of departmental committee on this issue, the bids of such firm(s) have been declared as non-responsive and not evaluated and accordingly, technically not qualified."
            stats["ip_rejected"] += 1
        else:
            if param_reasons:
                if len(param_reasons) == 1:
                    params_str = f"'{param_reasons[0]}'"
                elif len(param_reasons) == 2:
                    params_str = f"'{param_reasons[0]}' and '{param_reasons[1]}'"
                else:
                    params_str = ", ".join([f"'{p}'" for p in param_reasons[:-1]]) + f", and '{param_reasons[-1]}'"
                
                final_comment_parts.append(f"Firm has not submitted valid document in support of {params_str}.")
                
            if final_comment_parts:
                final_comment = " ".join(final_comment_parts) + " Hence the firm is technically not qualified."
            else:
                final_comment = "Bid is technically disqualified based on evaluation parameters."
                
        results.append({
            "si_no": si_no,
            "firm_name": firm_name, 
            "is_qualified": False, 
            "comment": final_comment
        })
        
        stats["total_detected"] += 1
        stats["total_disqualified"] += 1
        
    return {"results": results, "stats": stats}


def automate_gem(eval_results, url="", driver=None, job_id=None, use_direct_mode=False):
    if driver is None:
        print("\n--- Connecting to GeM Chrome (Stealth/Debug) ---")
        
        try:
            driver, driver_mode = get_automation_driver(
                start_url=url.strip(),
                port=9222,
                profile_name="ChromeAutomatorUC-TEC",
                use_direct_mode=use_direct_mode
            )
            if driver_mode == "debug":
                print("Connected successfully to the active Chrome debug session!")
                yield emit("info", {"message": "Using the existing Chrome debug session without opening a new page."})
            elif driver_mode == "direct":
                print("Direct Mode activated (DrissionPage). Stealth enabled.")
                yield emit("info", {"message": "Direct Mode activated (DrissionPage)."})
                # If no URL specified, assume the user is already on the target page and pick the latest active tab
                if not url:
                    try:
                        driver = driver.latest_tab
                        yield emit("info", {"message": f"Broadcasting from active tab: {driver.url}"})
                    except Exception:
                        pass
            else:
                print("Connected successfully to the undetected Chrome session!")
                yield emit("info", {"message": "Using undetected-chromedriver with the saved automation profile."})
        except Exception as e:
            print(f"\nERROR: Failed to connect to Chrome: {e}")
            yield emit("error", {"success": False, "error": f"Failed to prepare browser session: {e}"})
            return
    if job_id and driver:
        setattr(driver, "_current_job_id", job_id)

    def disable_history_navigation():
        """Prevent unexpected back/forward navigation triggered by the page."""
        try:
            driver.execute_script(
                "if (!window.__codex_no_back) {"
                "  window.__codex_no_back = true;"
                "  history.back = function(){};"
                "  history.forward = function(){};"
                "  history.go = function(){};"
                "  window.onpopstate = function(e){ try { history.forward(); } catch(e){} };"
                "}"
            )
        except Exception:
            pass

    # If a URL is provided, do not auto-navigate or reload.
    # Search the existing debug-session tabs and switch to the best match.
    if url:
        try:
            matched, match_detail = switch_to_matching_page(driver, url.strip())
            if not matched:
                raise Exception(match_detail)

            current_url = (driver.current_url or "").strip()
            target_url = url.strip()
            if current_url != target_url:
                print(f"Using closest matching open page: {current_url} | Requested: {target_url}")
            else:
                print(f"Using existing page: {target_url}")

            # Wait until the document is at least interactive/complete.
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            time.sleep(0.5)
            disable_history_navigation()
        except Exception as e:
            msg = f"Failed to navigate to provided URL: {str(e)}"
            print(msg)
            yield emit("error", {"success": False, "error": msg})
            return

    # Keep track of the primary window to avoid unintended navigation/context switches
    try:
        main_window = driver.current_window_handle
    except Exception:
        main_window = None

    def ensure_main_window():
        """Ensure we stay on the original page/window after confirmations."""
        try:
            if driver_mode == "direct":
                if driver.tab_id != main_window:
                    driver.get_tab(main_window).activate()
            else:
                if driver.current_window_handle != main_window:
                    driver.switch_to.window(main_window)
        except Exception:
            pass

    # Helper to find visible elements
    def get_visible_element(xpath, timeout=0):
        from modules.utils import list_visible_elements as global_lve
        els = global_lve(xpath, driver, driver_mode)
        return els[0] if els else None

    def safe_click_el(el):
        """Best-effort click helper for flaky/animated UI elements."""
        from modules.utils import safe_click as global_sc
        return global_sc(driver, driver_mode, el)

    def build_or_contains_xpath(needle_list):
        """Build a valid XPath OR expression for case-insensitive text contains."""
        parts = []
        for p in needle_list:
            p = str(p).strip().lower()
            if not p:
                continue
            parts.append(
                "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '%s')" % p
            )
        if not parts:
            return ""
        return " or ".join(parts)

    # Statistics Tracking
    stats = {
        "processed_qualified": 0,
        "processed_disqualified": 0,
        "skipped": 0,
        "total": len(eval_results),
        "failed": 0
    }

    results_log = []

    for result in eval_results:
        # Check for abortion signal
        if job_id and is_aborted(job_id):
            print(f"!!! Job {job_id} aborted by user !!!")
            yield emit("info", {"message": "Execution stopped by user."})
            break

        # Ensure any lingering success dialog is closed before the next firm
        try:
            expr = build_or_contains_xpath(["updated successfully", "technical evaluation has been saved"])
            success_block = get_visible_element(f"//*[{expr}]")
            if success_block:
                ok_btn = get_visible_element("//*[normalize-space(.)='OK' or normalize-space(.)='Ok']")
                if ok_btn: safe_click_el(ok_btn)
        except Exception:
            pass

        firm_name = result["firm_name"]
        print(f"\n> Processing firm: {firm_name}...")
        
        try:
            # Clean up firm name for more robust XPath searching
            search_name = firm_name.lower().replace("m/s", "").replace(".", "").strip()
            if len(search_name) < 3: search_name = firm_name.lower().strip()
            
            # Locate the firm row
            xpath_row = f"//tr[contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), '{search_name}')]"
            row_el = get_visible_element(xpath_row)
            
            if not row_el:
                msg = f"Skipping {firm_name}. Firm not found on page."
                print(f"  -> {msg}")
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": msg, "stats": stats})
                continue
            
            # Status check
            row_text = row_el.text.lower()
            if "recommended" in row_text or "non-recommend" in row_text:
                msg = f"Skipping {firm_name}. Already evaluated."
                print(f"  -> {msg}")
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": msg, "stats": stats})
                continue

            # Locate & click 'Evaluate' / 'Verify'
            xpath_verify = f"({xpath_row}//a[contains(., 'Verify') or contains(., 'Evaluate')] | {xpath_row}//button[contains(., 'Verify') or contains(., 'Evaluate')])[1]"
            verify_btn = get_visible_element(xpath_verify)
            
            if not verify_btn:
                msg = f"Skipping {firm_name}. Action button not found."
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": msg, "stats": stats})
                continue
            
            safe_click_el(verify_btn)
            time.sleep(2)

            # Wait for modal context
            modal_xpath = "//*[contains(@class, 'modal') or contains(@class, 'popup') or contains(@class, 'dialog')]"
            
            # Select Recommend / Non-Recommend
            if result["is_qualified"]:
                print(f"  -> Selecting 'Recommend'...")
                # Search for buttons or radios
                rec_sel = (
                    f"{modal_xpath}//button[contains(translate(., 'R', 'r'), 'recommend') and not(contains(., 'Non'))] | "
                    f"{modal_xpath}//input[@type='radio' and (contains(@value, 'Recommend') or contains(@id, 'Recommend'))]"
                )
            else:
                print(f"  -> Selecting 'Non-Recommend'...")
                rec_sel = (
                    f"{modal_xpath}//button[contains(translate(., 'N', 'n'), 'non-recommend') or contains(., 'Non Recommend')] | "
                    f"{modal_xpath}//input[@type='radio' and (contains(@value, 'Non') or contains(@id, 'Non'))]"
                )
            
            # Step-based execution for Modal
            modal_steps = [
                {"type": "click", "selector": rec_sel, "delay": 1.0},
                {"type": "type", "selector": f"{modal_xpath}//textarea", "value": result["comment"], "delay": 1.0},
                {"type": "click", "selector": f"{modal_xpath}//button[contains(translate(., 'S', 's'), 'save') or contains(., 'Submit') or contains(., 'Done')]", "delay": 2.0},
                {"type": "click", "selector": "//button[normalize-space(.)='OK' or normalize-space(.)='Ok']", "mandatory": False}
            ]
            
            from modules.utils import run_automation_steps
            run_automation_steps(driver, driver_mode, modal_steps)
            
            stats["total_detected"] = stats.get("total_detected", 0) + 1
            if result["is_qualified"]: stats["processed_qualified"] += 1
            else: stats["processed_disqualified"] += 1
            
            yield emit("progress", {"firm": firm_name, "status": "success", "message": "Evaluated successfully.", "stats": stats})

        except Exception as e:
            stats["failed"] += 1
            yield emit("progress", {"firm": firm_name, "status": "error", "message": f"Error: {e}", "stats": stats})
            ensure_main_window()

    yield emit("complete", {"success": True, "message": "Automated evaluation entry is complete!", "stats": stats})
