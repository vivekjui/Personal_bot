import os
import time
import socket
import json
import pandas as pd
import pdfplumber
import docx
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from modules.utils import launch_chrome_debug

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
    print("Extracting tables from PDF...")
    all_data = []
    fallback_used = False

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            table = page.extract_table()
            if table:
                all_data.extend(table)
                print(f"  -> Found table data on page {i + 1} (standard extraction)")
            else:
                # try pdfplumber's multi-table extractor
                tables = page.extract_tables()
                if tables:
                    for t in tables:
                        all_data.extend(t)
                    print(f"  -> Found {len(tables)} table(s) on page {i + 1} (multiple extraction)")
                else:
                    # fallback 1: use word positions to reconstruct row cells
                    word_rows = _rows_from_word_positions(page)
                    if word_rows:
                        fallback_used = True
                        all_data.extend(word_rows)
                        print(f"  -> Reconstructed {len(word_rows)} row(s) from positioned words on page {i + 1}")
                        continue

                    # fallback 2: attempt to parse raw text lines
                    text = page.extract_text() or ""
                    if text.strip():
                        fallback_used = True
                        print(f"  -> No table detected on page {i + 1}; using text fallback")
                        all_data.extend(_rows_from_text_block(text))

    if not all_data:
        # final fallback: OCR-aware PDF text extraction from utils, then parse lines
        try:
            from modules.utils import extract_text_from_pdf
            ocr_text = extract_text_from_pdf(pdf_path)
            ocr_rows = _rows_from_text_block(ocr_text)
            if ocr_rows:
                fallback_used = True
                all_data.extend(ocr_rows)
                print(f"  -> Reconstructed {len(ocr_rows)} row(s) from OCR/text fallback")
        except Exception as e:
            print(f"  -> OCR/text fallback failed: {e}")

    if not all_data:
        return pd.DataFrame()

    if fallback_used:
        print("Note: extraction relied on text heuristics; verify results carefully.")

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
    return df

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
            if "ip address similarity" in header_clean:
                # Reverse Rule: If the value is anything OTHER than 'no' or 'nan', they failed
                if cell_value != "no" and cell_value != "nan" and cell_value != "":
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

def automate_gem(eval_results, url="", driver=None, job_id=None):
    if driver is None:
        print("\n--- Connecting to GeM via Chrome (localhost:9222) ---")
        
        # Ensure Chrome is running in debug mode (Outside Browser)
        yield emit("info", {"message": "Checking/Launching Chrome in Debug mode..."})
        if not launch_chrome_debug(port=9222):
            yield emit("error", {"success": False, "error": "Failed to detect or launch Google Chrome in debug mode. Please ensure Chrome is installed."})
            return
        
        # Give it a moment to initialize
        time.sleep(2)

    # Keep track of the primary window to avoid unintended navigation/context switches
    try:
        main_window = driver.current_window_handle
    except Exception:
        main_window = None

        chrome_options = Options()
        chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        
        try:
            driver = webdriver.Chrome(options=chrome_options)
            print("Connected successfully to the active Chrome session!")
        except Exception as e:
            print("\nERROR: Failed to connect to Chrome.")
            print("Make sure you launched Chrome with '--remote-debugging-port=9222' and that it is fully open.")
            print(f"Exception Message: {e}")
            yield emit("error", {"success": False, "error": f"Failed to connect to Chrome: {e}"})
            return

    def ensure_main_window():
        """Ensure we stay on the original page/window after confirmations."""
        nonlocal main_window
        try:
            if main_window and driver.current_window_handle != main_window:
                if main_window in driver.window_handles:
                    driver.switch_to.window(main_window)
        except Exception:
            pass

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
    # Assume the user has already opened the correct page in the active browser.
    if url:
        try:
            current_url = (driver.current_url or "").strip()
            target_url = url.strip()
            if current_url != target_url:
                print(f"URL mismatch. Current: {current_url} | Expected: {target_url}")
                print("Auto-navigation disabled. Please open the target page manually.")
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

    # Helper to find visible elements
    def get_visible_element(xpath, timeout=0):
        if timeout > 0:
            try:
                WebDriverWait(driver, timeout).until(EC.visibility_of_any_elements_located((By.XPATH, xpath)))
            except:
                pass
        elements = driver.find_elements(By.XPATH, xpath)
        for el in elements:
            if el.is_displayed():
                return el
        return None

    def safe_click(el):
        """Best-effort click helper for flaky/animated UI elements."""
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", el)
            time.sleep(0.2)
            ActionChains(driver).move_to_element(el).click().perform()
            return
        except:
            pass
        try:
            el.click()
            return
        except:
            pass
        driver.execute_script("arguments[0].click();", el)

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
            expr = build_or_contains_xpath(
                ["updated successfully", "technical evaluation has been saved"]
            )
            if expr:
                success_block = get_visible_element(f"//*[{expr}]", timeout=1)
                if success_block:
                    ok_btn = get_visible_element(
                        "//*[normalize-space(.)='OK' or normalize-space(.)='Ok']",
                        timeout=1
                    )
                    if ok_btn:
                        safe_click(ok_btn)
        except Exception:
            pass

        firm_name = result["firm_name"]
        print(f"\n> Processing firm: {firm_name}...")
        
        try:
            # Clean up firm name for more robust XPath searching
            # Remove "M/s", "M/s.", excess spaces, and standard punctuation that might differ
            search_name = firm_name.lower().replace("m/s", "").replace(".", "").strip()
            # If the search name becomes too short or empty, fallback to original
            if len(search_name) < 3: search_name = firm_name.lower().strip()
            
            # 1. Click 'Verify Specification' / 'Evaluate' for the specific firm
            # Find the row containing the firm name (case-insensitive approximation) and locate the verify link/button in it.
            # Using normalize-space handles cases with multiple spaces or &nbsp;
            # Using translate(., ...) is safer than translate(text(), ...) for node-sets
            xpath_row = f"//tr[contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), '{search_name}')]"
            # try to match the full phrase first to avoid clicking unrelated links that simply contain the word
            xpath_verify = (
                f"({xpath_row}//a[contains(normalize-space(.), 'Verify Specification') or contains(normalize-space(.), 'Evaluate')] | "
                f"{xpath_row}//button[contains(normalize-space(.), 'Verify Specification') or contains(normalize-space(.), 'Evaluate')])[1]"
            )
            # in rare cases the visible text may just be 'Verify' so we'll fall back later if nothing is found
            
            try:
                # First check if the firm even exists on the page
                row_el = driver.find_element(By.XPATH, xpath_row)
                # Scroll it into view
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row_el)
                time.sleep(0.5)
                
                # --- NEW: Status Check Logic ---
                # Check if the firm has already been evaluated by looking at the row text
                row_text = row_el.text.lower()
                if "recommended" in row_text or "non-recommend" in row_text or "non recommend" in row_text:
                    # In some cases, the status might be "Verified" or "Evaluated" but let's stick to user's specific keywords
                    msg = f"Skipping {firm_name}. Already evaluated (Status: {row_text})."
                    print(f"  -> {msg}")
                    stats["skipped"] += 1
                    yield emit("progress", {"firm": firm_name, "status": "skipped", "message": msg, "stats": stats})
                    continue
                # --- END Status Check ---
                
            except Exception as e:
                if "continue" in str(e): # Handle the case where we 'continue' from inside try
                    continue
                msg = f"Skipping {firm_name}. Firm row finding issue: {e}"
                print(f"  -> {msg}")
                stats["skipped"] += 1
                results_log.append(msg)
                continue
                
            try:
                # Then check if the Evaluate action button is present and clickable
                # If the button isn't there, they might be marked as Recommended / Non-Recommended already
                WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, xpath_verify)))
            except:
                # strict lookup with 'Verify Specification' failed; try a looser fallback
                fallback_xpath = (
                    f"({xpath_row}//a[contains(normalize-space(.), 'Verify') or contains(normalize-space(.), 'Evaluate')] | "
                    f"{xpath_row}//button[contains(normalize-space(.), 'Verify') or contains(normalize-space(.), 'Evaluate')])[1]"
                )
                try:
                    WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.XPATH, fallback_xpath)))
                    xpath_verify = fallback_xpath
                    print(f"  -> using fallback verify locator for {firm_name}")
                except:
                    msg = f"Skipping {firm_name}. Firm is found but evaluation button is missing (Likely already evaluated)."
                    stats["skipped"] += 1
                    yield emit("progress", {"firm": firm_name, "status": "skipped", "message": msg, "stats": stats})
                    continue
            
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath_verify)))
            verify_btn = driver.find_element(By.XPATH, xpath_verify)
            
            # Robust click: scroll to element, move mouse, then click
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", verify_btn)
            time.sleep(0.5)
            try:
                actions = ActionChains(driver)
                actions.move_to_element(verify_btn).click().perform()
            except:
                driver.execute_script("arguments[0].click();", verify_btn)
            
            # Wait for popup modal to load (or for page navigation to happen)
            time.sleep(2)
            # quick check: if neither recommend nor non-recommend controls appear, do not navigate
            # back/refresh automatically. Handle manually if layout differs.
            controls_xpath = "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'recommend')] | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'non-recommend')]"
            if not get_visible_element(controls_xpath, timeout=3):
                raise Exception("Did not reach evaluation modal/page after clicking verify; check layout manually.")
            # 2. Select 'Recommend' or 'Non-Recommend'
            # We look globally for visible buttons since they appear in a modal
            # Added hyphen-agnostic matching and explicit modal context
            time.sleep(2) # Wait for modal animation
            
            # Modal container detection
            modal_xpath = "//*[contains(@class, 'modal') or contains(@class, 'popup') or contains(@class, 'dialog')][.//*[contains(., 'Evaluate') or contains(., 'Verify')]]"
            
            if result["is_qualified"]:
                print(f"  -> Selecting 'Recommend'...")
                # Try direct text match first (safest/fastest)
                rec_xpath = f"//button[normalize-space(.)='Recommend'] | //input[@value='Recommend']"
                recommend_btn = get_visible_element(rec_xpath, timeout=2)
                
                if not recommend_btn:
                    # Fallback to fuzzy case-insensitive matching
                    rec_xpath_fuzzy = f"({modal_xpath} | //body)//button[contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'recommend') and not(contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'non'))] | //input[@type='button' and contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'recommend')] | //input[@type='radio' and contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'recommend')]"
                    try: WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, rec_xpath_fuzzy)))
                    except: pass
                    recommend_btn = get_visible_element(rec_xpath_fuzzy)
                
                if recommend_btn:
                    driver.execute_script("arguments[0].click();", recommend_btn)
                else:
                    raise Exception("Recommend button not found or not visible")
            else:
                print(f"  -> Selecting 'Non-Recommend'...")
                # Try direct text match first
                non_rec_xpath = f"//button[normalize-space(.)='Non-Recommend'] | //button[normalize-space(.)='Non Recommend'] | //input[@value='Non-Recommend']"
                non_recommend_btn = get_visible_element(non_rec_xpath, timeout=2)
                
                if not non_recommend_btn:
                    # Fallback to fuzzy
                    non_rec_xpath_fuzzy = f"({modal_xpath} | //body)//button[contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'non') or contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'not recommend') or contains(normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'reject')] | //input[@type='button' and (contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'non') or contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'reject'))] | //input[@type='radio' and (contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'non') or contains(normalize-space(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'reject'))]"
                    try: WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, non_rec_xpath_fuzzy)))
                    except: pass
                    non_recommend_btn = get_visible_element(non_rec_xpath_fuzzy)
                
                if non_recommend_btn:
                    driver.execute_script("arguments[0].click();", non_recommend_btn)
                else:
                    raise Exception("Non-recommend button not found or not visible")
                
            # 3. Fill in the dynamically generated comment
            time.sleep(1)
            comment_xpath = f"({modal_xpath} | //body)//textarea[contains(normalize-space(translate(@id, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'comment') or contains(normalize-space(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'reason')]"
            comment_box = get_visible_element(comment_xpath)
            
            if not comment_box:
                raise Exception("Comment textarea not found or not visible")
                
            comment_box.clear()
            comment_box.send_keys(result["comment"])
            print(f"  -> Comment applied: {result['comment']}")
            
            # 4. Click 'Add Comment' or 'Save'
            save_xpath = f"({modal_xpath} | //body)//button[contains(normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'save') or contains(normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'add comment') or contains(normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')), 'submit')]"
            save_btn = get_visible_element(save_xpath)
            
            if save_btn:
                driver.execute_script("arguments[0].click();", save_btn)
            else:
                raise Exception("Save button not found or not visible")
            
            # 5. Handle success pop-up by clicking 'OK' and verifying text
            try:
                print("  -> Waiting for success confirmation popup...")
                alert_found = False
                success_patterns = ['success', 'saved', 'updated', 'submitted']

                # First try native browser alert
                try:
                    WebDriverWait(driver, 5).until(EC.alert_is_present())
                    alert = driver.switch_to.alert
                    alert_text = (alert.text or '').lower()
                    alert.accept()
                    print(f"  -> native alert accepted ('{alert_text}').")
                    alert_found = True
                except:
                    pass

                # If no native alert, wait for visible success dialog and click OK/Close there.
                if not alert_found:
                    end_at = time.time() + 3
                    while time.time() < end_at and not alert_found:
                        dialog_xpath = (
                            "//*[(contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'swal') or "
                            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'modal') or "
                            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'dialog') or "
                            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'toast') or "
                            "translate(@role, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='dialog')]"
                        )
                        dialogs = driver.find_elements(By.XPATH, dialog_xpath)
                        for dlg in dialogs:
                            if not dlg.is_displayed():
                                continue
                            dlg_text = (dlg.text or '').lower()
                            if not any(p in dlg_text for p in success_patterns):
                                continue

                            ok_candidates = dlg.find_elements(By.XPATH, ".//button | .//a | .//*[@role='button']")
                            clicked = False
                            for btn in ok_candidates:
                                if not btn.is_displayed():
                                    continue
                                label = ((btn.text or '') + ' ' + (btn.get_attribute('value') or '')).lower().strip()
                                if any(k in label for k in ['ok', 'close', 'done', 'confirm']):
                                    safe_click(btn)
                                    clicked = True
                                    break

                            if not clicked:
                                for btn in ok_candidates:
                                    if btn.is_displayed():
                                        safe_click(btn)
                                        clicked = True
                                        break

                            if clicked:
                                print("  -> DOM success dialog confirmed & closed.")
                                alert_found = True
                                break

                        if not alert_found:
                            time.sleep(0.4)

                if not alert_found:
                    # Target the exact success dialog text and click its OK button.
                    try:
                        expr = build_or_contains_xpath(
                            ["updated successfully", "technical evaluation has been saved"]
                        )
                        if expr:
                            success_block = get_visible_element(f"//*[{expr}]", timeout=2)
                            if success_block:
                                ok_btn = get_visible_element(
                                    "//*[normalize-space(.)='OK' or normalize-space(.)='Ok']",
                                    timeout=1
                                )
                                if ok_btn:
                                    safe_click(ok_btn)
                                    alert_found = True
                    except Exception as _:
                        pass

                if not alert_found:
                    # Final fallback: look for any visible element containing success keywords
                    # using a valid XPath "or" expression (not "OR").
                    try:
                        expr = build_or_contains_xpath(success_patterns)
                        if expr:
                            success_text_el = get_visible_element(f"//*[{expr}]", timeout=2)
                            if success_text_el:
                                # Try clicking a nearby OK/Close button if present
                                btn = get_visible_element(
                                    "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok') or "
                                    "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
                                    timeout=1
                                )
                                if btn:
                                    safe_click(btn)
                                    alert_found = True
                    except Exception as _:
                        pass

                if not alert_found:
                    # Hard fallback: click known OK/Close buttons even if no success text is present.
                    try:
                        ok_selectors = [
                            "//*[@id='closeSuccessBtn']",
                            "//button[normalize-space(.)='OK' or normalize-space(.)='Ok' or normalize-space(.)='Close']",
                            "//button[contains(translate(@id, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok') or "
                            "contains(translate(@id, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close') or "
                            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok') or "
                            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]"
                        ]
                        for sel in ok_selectors:
                            btn = get_visible_element(sel, timeout=1)
                            if btn:
                                safe_click(btn)
                                alert_found = True
                                break
                    except Exception as _:
                        pass

                if not alert_found:
                    raise Exception("Success popup did not appear within 10 seconds.")

                # Ensure success dialog is closed before continuing
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: not get_visible_element("//*[@id='closeSuccessBtn']", timeout=0)
                    )
                except:
                    pass

            except Exception as e:
                raise Exception(f"Failed to confirm success popup: {e}")

            # Ensure we remain on the same page/window after OK
            ensure_main_window()
            disable_history_navigation()
                
            # 6. Verify the status text actually changed in the table
            print("  -> Verifying status text change on the DOM...")
            try:
                def check_status_changed(d):
                    row = d.find_element(By.XPATH, xpath_row)
                    # Get the full text of the row to see if "Pending" evaporated and "Recommended" appeared
                    t = row.text.lower()
                    if "pending" not in t and ("recommended" in t or "non-recommended" in t):
                        return True
                    return False
                    
                WebDriverWait(driver, 10).until(check_status_changed)
                print("  -> Status change verified successfully.")
            except:
                raise Exception("Row status never changed from 'Pending' after submitting.")

            msg = f"Successfully processed {firm_name}."
            if result["is_qualified"]:
                stats["processed_qualified"] += 1
            else:
                stats["processed_disqualified"] += 1
                
            yield emit("progress", {"firm": firm_name, "status": "success", "message": msg, "stats": stats})
            time.sleep(1) # Brief pause before processing the next bidder
            
        except Exception as e:
            msg = f"ERROR processing {firm_name}. Please check manually. ({str(e)})"
            stats["failed"] += 1
            yield emit("progress", {"firm": firm_name, "status": "error", "message": msg, "stats": stats})
            pass
    yield emit("complete", {
        "success": True,
        "message": "Automated evaluation entry is complete!",
        "stats": stats
    })
