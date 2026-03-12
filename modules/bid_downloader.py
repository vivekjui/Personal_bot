import json
import re
import socket
import time
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from typing import List, Optional

from modules.utils import logger, sanitize_filename, launch_chrome_debug

STOP_BID_EXECUTION = set()


def stop_bid_job(job_id: str) -> None:
    """Signals the Bid Downloader automation to stop for a specific job."""
    STOP_BID_EXECUTION.add(job_id)


def is_aborted(job_id: Optional[str]) -> bool:
    return bool(job_id) and job_id in STOP_BID_EXECUTION


def emit(event: str, data: dict) -> str:
    """Helper formatting function for Server-Sent Events (SSE) stream."""
    data["type"] = event
    if "error" in data and "message" not in data:
        data["message"] = data["error"]
    return f"data: {json.dumps(data)}\n\n"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _doc_matches(req_text: str, doc_types: List[str]) -> bool:
    if not doc_types:
        return True
    req_norm = _normalize(req_text)
    if not req_norm:
        return False
    req_tokens = set(req_norm.split())
    for dt in doc_types:
        dt_norm = _normalize(dt)
        if not dt_norm:
            continue
        if dt_norm in req_norm:
            return True
        dt_tokens = [t for t in dt_norm.split() if t]
        if dt_tokens and all(t in req_tokens for t in dt_tokens):
            return True
    return False


def _build_or_contains_xpath(needle_list: List[str]) -> str:
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


def get_default_downloads_folder():
    """Find the user's Downloads folder."""
    downloads = Path.home() / "Downloads"
    return downloads


def automate_bid_download(
    gem_url: str = "",
    doc_types: Optional[List[str]] = None,
    download_all: bool = False,
    si_from: Optional[int] = None,
    si_to: Optional[int] = None,
    job_id: Optional[str] = None
):
    import fitz  # PyMuPDF
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    doc_types = doc_types or []
    downloads_folder = get_default_downloads_folder()
    
    yield emit("info", {"message": "Initializing Bid Downloader..."})
    logger.info(f"Bid Download Job started. Target: {gem_url or 'Current Page'}")

    # 1) Ensure Chrome is running in debug mode (Outside Browser)
    yield emit("info", {"message": "Checking/Launching Chrome in Debug mode..."})
    if not launch_chrome_debug(port=9222):
        yield emit("error", {"success": False, "error": "Failed to detect or launch Google Chrome in debug mode. Please ensure Chrome is installed."})
        return
    
    # Give it a moment to initialize if just launched
    time.sleep(2)

    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        yield emit("error", {"success": False, "error": f"Failed to connect to Chrome: {e}"})
        return

    main_window = driver.current_window_handle

    def ensure_main_window():
        try:
            if driver.current_window_handle != main_window and main_window in driver.window_handles:
                driver.switch_to.window(main_window)
        except Exception:
            pass

    # 2) Validate URL (optional)
    if gem_url:
        try:
            current_url = (driver.current_url or "").strip()
            target_url = gem_url.strip()
            if current_url != target_url:
                yield emit("info", {"message": f"URL mismatch. Open the target page manually. Current: {current_url}"})
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
        except Exception as e:
            yield emit("error", {"success": False, "error": f"Failed to validate URL: {e}"})
            return

    def get_visible_element(xpath, root=None, timeout=0):
        scope = root if root is not None else driver
        if timeout > 0:
            try:
                WebDriverWait(driver, timeout).until(EC.visibility_of_any_elements_located((By.XPATH, xpath)))
            except Exception:
                pass
        elements = scope.find_elements(By.XPATH, xpath)
        for el in elements:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                continue
        return None

    def safe_click(el):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", el)
            time.sleep(0.3)
            # Try pointer interaction first
            ActionChains(driver).move_to_element(el).click().perform()
            return
        except Exception:
            pass
        try:
            el.click()
            return
        except Exception:
            pass
        driver.execute_script("arguments[0].click();", el)

    def find_firm_table():
        tables = driver.find_elements(By.XPATH, "//table")
        for tbl in tables:
            headers = [h.text.strip().lower() for h in tbl.find_elements(By.XPATH, ".//th")]
            if not headers:
                headers = [c.text.strip().lower() for c in tbl.find_elements(By.XPATH, ".//tr[1]/td")]
            if not headers:
                continue

            has_sr = any(k in h for h in headers for k in ["sr", "sl", "s no", "s. no", "si no", "sl no"])
            has_seller = any(k in h for h in headers for k in ["seller", "bidder", "firm", "vendor"])
            if has_sr and has_seller:
                return tbl, headers
        return None, []

    def header_index(headers, keywords):
        for i, h in enumerate(headers):
            for k in keywords:
                if k in h:
                    return i
        return None

    # Try to find the Bid ID / Number on the page for folder naming
    bid_id = "Unknown_Bid"
    try:
        bid_element = get_visible_element("//*[contains(text(), 'Bid Number') or contains(text(), 'GEM/')]")
        if bid_element:
            bid_text = bid_element.text
            match = re.search(r"GEM/\d{4}/[A-Z]/\d+", bid_text)
            if match:
                bid_id = sanitize_filename(match.group(0))
            else:
                bid_id = sanitize_filename(bid_text.split(":")[-1].strip())
    except Exception:
        pass

    # Setup Destination Folder: Desktop / PDF Bids / [Bid_ID]
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        onedrive = Path.home() / "OneDrive" / "Desktop"
        if onedrive.exists(): desktop = onedrive
    
    pdf_bids_root = desktop / "PDF Bids"
    pdf_bids_root.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = pdf_bids_root / f"{bid_id}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    firm_table, headers = find_firm_table()
    if not firm_table:
        yield emit("error", {"success": False, "error": "Could not locate firm list table. Make sure the bid participation page is open."})
        return

    sr_idx = header_index(headers, ["sr", "sl", "s no", "si no"])
    seller_idx = header_index(headers, ["seller", "bidder", "firm", "vendor"])

    rows = firm_table.find_elements(By.XPATH, ".//tbody/tr")
    if not rows:
        rows = firm_table.find_elements(By.XPATH, ".//tr[position()>1]")

    firms = []
    for i, row in enumerate(rows):
        cells = row.find_elements(By.XPATH, "./td")
        if not cells:
            continue
        sr_text = cells[sr_idx].text.strip() if sr_idx is not None and sr_idx < len(cells) else str(i + 1)
        seller_text = cells[seller_idx].text.strip() if seller_idx is not None and seller_idx < len(cells) else ""
        if not seller_text:
            continue
        firms.append({"sr": sr_text, "seller": seller_text, "row": row})

    stats = {
        "total_firms": len(firms),
        "processed": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0
    }

    yield emit("info", {"message": f"Bid ID: {bid_id}. Detected {stats['total_firms']} firms."})

    def in_range(sr_text):
        if si_from is None and si_to is None:
            return True
        try:
            sr_val = int(re.sub(r"[^0-9]", "", sr_text) or "0")
        except Exception:
            sr_val = None
        if sr_val is None or sr_val == 0:
            return True
        if si_from is not None and sr_val < si_from:
            return False
        if si_to is not None and sr_val > si_to:
            return False
        return True

    def open_seller_popup(row):
        # Specific keywords for technical bid document viewing
        candidates = row.find_elements(By.XPATH, ".//a|.//button")
        for c in candidates:
            label = ((c.text or "") + " " + (c.get_attribute("value") or "") + " " + (c.get_attribute("title") or "")).lower()
            # Priority 1: Exact phrase "View Documents & seek clarification" or variations
            if "view document" in label and ("seek" in label or "clarification" in label):
                try:
                    if c.is_displayed():
                        return c
                except Exception:
                    continue
            # Priority 2: Technical/Verify keywords
            if any(k in label for k in ["verify doc", "verify", "technical", "action"]):
                try:
                    if c.is_displayed():
                        return c
                except Exception:
                    continue
        return None

    def find_upload_table():
        tables = driver.find_elements(By.XPATH, "//table")
        for tbl in tables:
            headers = [h.text.strip().lower() for h in tbl.find_elements(By.XPATH, ".//th")]
            if not headers:
                headers = [c.text.strip().lower() for c in tbl.find_elements(By.XPATH, ".//tr[1]/td")]
            if not headers:
                continue
            if any(k in h for h in headers for k in ["requirement", "document", "uploaded", "buyer", "file"]):
                return tbl, headers
        return None, []

    def wait_for_downloads(snapshot, timeout=60):
        """Wait for new files not in the snapshot and ensure they are finished."""
        logger.info("Waiting for new file downloads...")
        start_wait = time.time()
        end_time = start_wait + timeout
        
        while time.time() < end_time:
            current_files = set(downloads_folder.glob("*"))
            # Identify NEW files that weren't there before the click
            new_files = [f for f in current_files if f not in snapshot]
            
            if not new_files:
                time.sleep(1)
                continue
                
            # Check for temporary/incomplete downloads
            if any(f.suffix in [".crdownload", ".part", ".tmp"] for f in new_files):
                time.sleep(2)
                continue
            
            # Multi-stage stability check for slow/large files (ZIPs)
            # We wait for the size to remain constant for 3 consecutive seconds
            stable_count = 0
            prev_total_size = -1
            
            for _ in range(5): # Up to 5 heartbeat attempts
                curr_total_size = sum(f.stat().st_size for f in new_files if f.exists())
                if curr_total_size > 0 and curr_total_size == prev_total_size:
                    stable_count += 1
                else:
                    stable_count = 0
                    prev_total_size = curr_total_size
                
                if stable_count >= 2: # Stable for 2 intervals
                    logger.info(f"Detected {len(new_files)} new stable files.")
                    return new_files
                time.sleep(1.5)
                
            time.sleep(1)
            
        logger.warning("Download wait timed out or files didn't stabilize.")
        return []

    def merge_firm_files(files, firm_name):
        """Merge PDFs into a single firm-specific file, handling ZIPs if present."""
        if not files: return None
        
        final_pdfs = []
        temp_extract_dir = output_dir / f"temp_{int(time.time())}"
        
        # 1. Identify and process any ZIP files (from "Download all")
        for f in files:
            if f.suffix.lower() == ".zip":
                try:
                    import zipfile
                    temp_extract_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(f, 'r') as zip_ref:
                        zip_ref.extractall(temp_extract_dir)
                    # Add unzipped PDFs to the list
                    final_pdfs.extend(list(temp_extract_dir.rglob("*.pdf")))
                except Exception as e:
                    logger.error(f"Error extracting ZIP {f}: {e}")
            elif f.suffix.lower() == ".pdf":
                final_pdfs.append(f)
        
        if not final_pdfs:
            # If still no PDFs, just move everything raw to avoid data loss
            for f in files:
                try: shutil.move(str(f), str(output_dir / f.name))
                except Exception: pass
            return None
            
        # 2. Merge all collected PDFs
        # Sort to maintain some order
        final_pdfs.sort(key=lambda x: x.name)
        
        merged_filename = f"{sanitize_filename(firm_name)}_Technical_Bid.pdf"
        merged_path = output_dir / merged_filename
        
        result_pdf = fitz.open()
        for pdf in final_pdfs:
            try:
                doc = fitz.open(pdf)
                result_pdf.insert_pdf(doc)
                doc.close()
            except Exception as e:
                logger.error(f"Error merging {pdf}: {e}")
                
        result_pdf.save(str(merged_path))
        result_pdf.close()
        
        # 3. Cleanup
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        for f in files:
            try: f.unlink()
            except Exception: pass
                
        return merged_path

    def download_from_popup():
        downloads = 0
        
        # If user explicitly asked for 'Download All', prioritize the "Download all" button/zip
        if download_all:
            # Look for "Download all" or "Downloadall"
            expr = _build_or_contains_xpath(["download all", "downloadall"])
            if expr:
                btn = get_visible_element(f"//*[{expr}]")
                if btn:
                    logger.info("Found 'Download all' button. Clicking...")
                    safe_click(btn)
                    time.sleep(3) # Give it time to start the ZIP download
                    return 1 # Return 1 to indicate something was clicked

        # Fallback to individual downloads if "Download All" button wasn't found or not requested
        tbl, hdrs = find_upload_table()
        if not tbl:
            return 0

        req_idx = header_index(hdrs, ["requirement", "document", "buyer", "criteria"])
        uploaded_idx = header_index(hdrs, ["uploaded", "document", "file", "attachment"])

        rows = tbl.find_elements(By.XPATH, ".//tbody/tr")
        if not rows:
            rows = tbl.find_elements(By.XPATH, ".//tr[position()>1]")

        for row in rows:
            cells = row.find_elements(By.XPATH, "./td")
            if not cells: continue
            req_text = cells[req_idx].text.strip() if req_idx is not None and req_idx < len(cells) else cells[0].text.strip()
            if not _doc_matches(req_text, doc_types):
                continue

            # find download link/button
            link = None
            for el in row.find_elements(By.XPATH, ".//a|.//button"):
                label = ((el.text or "") + " " + (el.get_attribute("title") or "") + " " + (el.get_attribute("value") or "")).lower()
                href = (el.get_attribute("href") or "").lower()
                if "download" in label or "download" in href or "view" in label:
                    if el.is_displayed():
                        link = el
                        break
            if link:
                safe_click(link)
                downloads += 1
                time.sleep(1)
        return downloads

    def close_popup():
        try:
            if driver.current_window_handle != main_window:
                driver.close()
                if main_window in driver.window_handles:
                    driver.switch_to.window(main_window)
                return
        except Exception: pass

        expr = _build_or_contains_xpath(["close", "x", "cancel"])
        if expr:
            btn = get_visible_element(f"//button[{expr}] | //a[{expr}] | //*[contains(@class, 'close')]")
            if btn:
                safe_click(btn)
                time.sleep(0.5)
        ensure_main_window()

    for firm in firms:
        if job_id and is_aborted(job_id):
            yield emit("info", {"message": "Download stopped by user."})
            break

        if not in_range(firm["sr"]):
            continue

        firm_name = firm["seller"]
        stats["processed"] += 1
        
        # Take a snapshot of the Downloads folder BEFORE clicking anything for this firm
        snapshot = set(downloads_folder.glob("*"))

        try:
            # 1. Open Popup
            action_btn = open_seller_popup(firm["row"])
            if not action_btn:
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": "View Doc action not found.", "stats": stats})
                continue

            before_handles = set(driver.window_handles)
            safe_click(action_btn)
            time.sleep(2)

            after_handles = set(driver.window_handles)
            new_handles = list(after_handles - before_handles)
            if new_handles:
                driver.switch_to.window(new_handles[0])

            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            time.sleep(1)

            # 2. Extract Downloads
            download_clicked = download_from_popup()
            
            # 3. Wait and Merge
            new_files = wait_for_downloads(snapshot)
            merged_file = merge_firm_files(new_files, firm_name)
            
            close_popup()

            if merged_file:
                stats["downloaded"] += 1
                yield emit("progress", {"firm": firm_name, "status": "success", "message": f"Merged technical bid created: {merged_file.name}", "stats": stats})
            elif download_clicked > 0:
                 yield emit("progress", {"firm": firm_name, "status": "success", "message": f"Downloaded {download_clicked} files (Merge skipped/no PDFs).", "stats": stats})
            else:
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": "No technical documents found.", "stats": stats})

        except Exception as e:
            stats["failed"] += 1
            yield emit("progress", {"firm": firm_name, "status": "error", "message": str(e), "stats": stats})
            ensure_main_window()

    yield emit("complete", {
        "success": True,
        "message": f"Bid document download completed. Files organized in '{output_dir.name}'",
        "output_dir": str(output_dir),
        "stats": stats
    })
