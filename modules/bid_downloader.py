import json
import re
import time
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from modules.utils import (
    logger, sanitize_filename, get_automation_driver, switch_to_matching_page,
    list_visible_elements, safe_click, run_script, get_url, is_same_window, get_frame,
    safe_get_tab_ids
)

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


def _is_row_download_candidate(label: str, href: str) -> bool:
    label = (label or "").strip().lower()
    href = (href or "").strip().lower()
    if "download all" in label or "downloadall" in label:
        return False
    if "download" in label or "download" in href or "view" in label:
        return True
    if re.fullmatch(r"\d{5,}", label):
        return True
    if href.endswith(".pdf") or href.endswith(".zip"):
        return True
    return False


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
    job_id: Optional[str] = None,
    use_direct_mode: bool = False
):
    import fitz  # PyMuPDF
    from modules.utils import DATA_ROOT, get_requests_proxies
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    doc_types = doc_types or []
    # Use a deterministic per-job download folder to avoid relying on the user's Downloads,
    # and to prevent cross-job ambiguity.
    job_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    downloads_folder = (DATA_ROOT / "bid_downloads" / f"job_{job_stamp}")
    downloads_folder.mkdir(parents=True, exist_ok=True)
    
    yield emit("info", {"message": "Initializing Bid Downloader..."})
    logger.info(f"Bid Download Job started. Target: {gem_url or 'Current Page'}")

    # 1) Reuse debug Chrome if available; otherwise launch a managed UC session.
    yield emit("info", {"message": "Preparing browser session..."})
    try:
        driver, driver_mode = get_automation_driver(
            start_url=gem_url.strip(),
            port=9222,
            profile_name="ChromeAutomatorUC-Bid",
            download_dir=downloads_folder,
            allow_browser_launch=not use_direct_mode,
            use_direct_mode=use_direct_mode,
            allow_debug=False
        )
    except Exception as e:
        # If Direct Mode is enabled but no debuggable Chrome exists, gracefully fallback
        # to Managed Mode (recommended) instead of failing the whole job.
        msg = str(e)
        if use_direct_mode and "Direct Mode Failed" in msg:
            yield emit("info", {"message": "Direct Mode could not attach to port 9222. Falling back to Managed Mode (recommended)..."})
            try:
                driver, driver_mode = get_automation_driver(
                    start_url=gem_url.strip(),
                    port=9222,
                    profile_name="ChromeAutomatorUC-Bid",
                    download_dir=downloads_folder,
                    allow_browser_launch=True,
                    use_direct_mode=False,
                    allow_debug=False,
                )
            except Exception as e2:
                yield emit("error", {"success": False, "error": f"Failed to prepare browser session: {e2}"})
                return
        else:
            yield emit("error", {"success": False, "error": f"Failed to prepare browser session: {e}"})
            return

    if driver_mode == "debug":
        yield emit("info", {"message": "Using the existing Chrome debug session."})
    elif driver_mode == "direct":
        yield emit("info", {"message": "Direct Mode activated (DrissionPage). Stealth enabled."})
        # If no URL specified, assume the user is already on the target page and pick the latest active tab
        if not gem_url:
            try:
                driver = driver.latest_tab
                yield emit("info", {"message": f"Broadcasting from active tab: {driver.url}"})
            except Exception:
                pass
    else:
        # Managed (undetected) mode: enforce download path via CDP where possible.
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(downloads_folder)},
            )
        except Exception:
            pass

    # 2) Validate URL (optional)
    if gem_url:
        try:
            matched, res = switch_to_matching_page(driver, gem_url.strip())
            if not matched:
                yield emit("error", {"success": False, "error": res})
                return
            
            # For DrissionPage, the result might be the specific tab object
            if driver_mode == "direct" and not isinstance(res, str):
                driver = res
                driver.wait.load_start()

            current_url = (driver.url or "").strip()
            target_url = gem_url.strip()
            if current_url != target_url:
                yield emit("info", {"message": f"Using the closest matching open page: {current_url}"})
            
            if driver_mode != "direct":
                WebDriverWait(driver, 15).until(
                    lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
                )
        except Exception as e:
            yield emit("error", {"success": False, "error": f"Failed to validate URL: {e}"})
            return
    else:
        if driver_mode != "direct":
            yield emit("info", {"message": "No GeM URL provided. Please login (if needed) and open the Bid Participation/Verify Doc page in the opened Chrome window."})

    if driver_mode == "direct":
        main_window = driver.tab_id
    else:
        main_window = driver.current_window_handle

    def _is_gem_logged_out() -> bool:
        """Heuristic detection: session expired / login page."""
        try:
            u = (get_url() or "").lower()
        except Exception:
            u = ""
        if "gem.gov.in" in u and any(x in u for x in ["auth/logout", "/logout", "login", "signin", "auth", "sso"]):
            return True
        try:
            # Only treat password fields as "logged out" when we're actually on GeM.
            # This prevents local dummy HTML pages from being misclassified.
            if "gem.gov.in" in u and get_visible_element("//input[@type='password']"):
                return True
            if get_visible_element(
                "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in') "
                "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'log in') "
                "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]"
            ):
                # Same: only if we're on GeM domain.
                return "gem.gov.in" in u
        except Exception:
            pass
        return False

    def _reset_gem_session():
        """Preserve the active GeM session; do not clear cookies or storage automatically."""
        return

    def _wait_for_user_login_and_bid_page(timeout_seconds: int = 15 * 60):
        """Pause automation and wait until we can see the firm list table again."""
        yield emit("info", {"message": "Waiting for you to login to GeM in the opened Chrome window, then open the Bid Participation/Verify Doc page..."})
        deadline = time.time() + timeout_seconds
        last_hint = 0.0
        while time.time() < deadline:
            if job_id and is_aborted(job_id):
                return False
            try:
                if gem_url:
                    try:
                        switch_to_matching_page(driver, gem_url.strip(), timeout=2)
                    except Exception:
                        pass
                t, _ = find_firm_table()
                if t:
                    return True
            except Exception:
                pass

            if time.time() - last_hint > 12:
                last_hint = time.time()
                if _is_gem_logged_out():
                    try:
                        u = (get_url() or "").lower()
                        if "gem.gov.in" in u and ("auth/logout" in u or "/logout" in u):
                            _reset_gem_session()
                    except Exception:
                        pass
                    yield emit("info", {"message": "GeM session appears logged out. Please login and return to the bid participation page."})
                else:
                    yield emit("info", {"message": "Still waiting for the bid participation page to be open..."})
            time.sleep(1.0)
        return False

    def ensure_main_window():
        try:
            if driver_mode == "direct":
                if driver.tab_id != main_window:
                    driver.get_tab(main_window).activate()
            else:
                if driver.current_window_handle != main_window and main_window in driver.window_handles:
                    driver.switch_to.window(main_window)
        except Exception:
            pass

    def get_visible_element(xpath, root=None, timeout=0):
        if driver_mode == "direct":
            scope = root if root is not None else driver
            try:
                el = scope.ele(f'xpath:{xpath}', timeout=timeout or 5)
                # DrissionPage .is_displayed is a property
                if el and hasattr(el, 'is_displayed') and el.is_displayed:
                    return el
                return el
            except Exception:
                pass
            return None

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

    def list_visible_elements(xpath, root=None):
        from modules.utils import list_visible_elements as global_lve
        return global_lve(xpath, driver, driver_mode, root=root)

    def get_url():
        from modules.utils import get_url as global_gu
        return global_gu(driver, driver_mode)

    def run_script(script, *args):
        from modules.utils import run_script as global_rs
        return global_rs(driver, driver_mode, script, *args)

    def is_same_window(handle):
        from modules.utils import is_same_window as global_isw
        return global_isw(driver, driver_mode, handle)

    def safe_click(el):
        from modules.utils import safe_click as global_sc
        return global_sc(driver, driver_mode, el)

    def get_frame(locator):
        from modules.utils import get_frame as global_gf
        return global_gf(driver, driver_mode, locator)

    def find_firm_table():
        def check_context():
            if driver_mode == "direct":
                tables = driver.eles('xpath://table')
                for tbl in tables:
                    try:
                        headers = [h.text.strip().lower() for h in tbl.eles('xpath:.//th')]
                        if not headers:
                            headers = [c.text.strip().lower() for c in tbl.eles('xpath:.//tr[1]/td')]
                        if not headers:
                            continue

                        has_sr = any(k in h for h in headers for k in ["sr", "sl", "s no", "s. no", "si no", "sl no"])
                        has_seller = any(k in h for h in headers for k in ["seller", "bidder", "firm", "vendor"])
                        if has_sr and has_seller:
                            return tbl, headers
                    except Exception:
                        continue
                return None, []

            tables = list_visible_elements("//table")
            for tbl in tables:
                try:
                    headers = [h.text.strip().lower() for h in list_visible_elements(".//th", root=tbl)]
                    if not headers:
                        headers = [c.text.strip().lower() for c in list_visible_elements(".//tr[1]/td", root=tbl)]
                    if not headers:
                        continue

                    has_sr = any(k in h for h in headers for k in ["sr", "sl", "s no", "s. no", "si no", "sl no"])
                    has_seller = any(k in h for h in headers for k in ["seller", "bidder", "firm", "vendor"])
                    if has_sr and has_seller:
                        return tbl, headers
                except Exception:
                    continue
            return None, []

        # 1. Check main content
        tbl, hdrs = check_context()
        if tbl:
            return tbl, hdrs

        # 2. Check frames recursively
        def search_frames():
            if driver_mode == "direct":
                # DrissionPage handles frames with .iframes
                for iframe in driver.iframes:
                    try:
                        logger.info(f"Checking iframe for firm table...")
                        # We can't easily recurse into frames with DrissionPage the same way with a simple list, 
                        # but DrissionPage's iframe objects are self-contained.
                        # For now, let's just check the first level of iframes.
                        # We can improve this if needed.
                        tables = iframe.eles('xpath://table')
                        for tbl in tables:
                            headers = [h.text.strip().lower() for h in tbl.eles('xpath:.//th')]
                            if not headers:
                                headers = [c.text.strip().lower() for c in tbl.eles('xpath:.//tr[1]/td')]
                            has_sr = any(k in h for h in headers for k in ["sr", "sl", "s no", "s. no", "si no", "sl no"])
                            has_seller = any(k in h for h in headers for k in ["seller", "bidder", "firm", "vendor"])
                            if has_sr and has_seller:
                                return tbl, headers
                    except Exception:
                        continue
                return None, []

            frames = list_visible_elements("//iframe | //frame")
            for i in range(len(frames)):
                try:
                    driver.switch_to.frame(i)
                    logger.info(f"Checking frame {i} for firm table...")
                    t, h = check_context()
                    if t:
                        return t, h
                    
                    # Nested search
                    t, h = search_frames()
                    if t:
                        return t, h
                    
                    driver.switch_to.parent_frame()
                except Exception as e:
                    logger.warning(f"Error switching/searching frame {i}: {e}")
                    driver.switch_to.parent_frame()
            return None, []

        if driver_mode != "direct":
            driver.switch_to.default_content()
        tbl, hdrs = search_frames()
        if tbl:
            logger.info("Found firm list table inside a frame.")
            return tbl, hdrs
            
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

    # Ensure we are logged in and on the firm list page (or wait for user to navigate).
    if _is_gem_logged_out():
        ok = yield from _wait_for_user_login_and_bid_page()
        if not ok:
            yield emit("error", {"success": False, "error": "Timed out waiting for GeM login / bid page."})
            return

    firm_table, headers = find_firm_table()
    if not firm_table:
        ok = yield from _wait_for_user_login_and_bid_page(timeout_seconds=3 * 60)
        if ok:
            firm_table, headers = find_firm_table()
        if not firm_table:
            yield emit("error", {"success": False, "error": "Could not locate firm list table. Make sure the bid participation page is open."})
            return

    sr_idx = header_index(headers, ["sr", "sl", "s no", "si no"])
    seller_idx = header_index(headers, ["seller", "bidder", "firm", "vendor"])

    rows = list_visible_elements(".//tbody/tr", root=firm_table)
    if not rows:
        rows = list_visible_elements(".//tr[position()>1]", root=firm_table)

    firms = []
    for i, row in enumerate(rows):
        if driver_mode == "direct":
            cells = row.eles('xpath:./td')
            if not cells:
                continue
            sr_text = cells[sr_idx].text.strip() if sr_idx is not None and sr_idx < len(cells) else str(i + 1)
            seller_text = cells[seller_idx].text.strip() if seller_idx is not None and seller_idx < len(cells) else ""
            if not seller_text:
                continue
            firms.append({"sr": sr_text, "seller": seller_text, "row": row})
        else:
            cells = list_visible_elements("./td", root=row)
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
        if driver_mode == "direct":
            candidates = row.eles('xpath:.//a|.//button')
        else:
            candidates = list_visible_elements(".//a|.//button", root=row)
            
        for c in candidates:
            if driver_mode == "direct":
                label = ((c.text or "") + " " + (c.attr("value") or "") + " " + (c.attr("title") or "")).lower()
                # Priority 1: Exact phrase "View Documents & seek clarification" or variations
                if "view document" in label and ("seek" in label or "clarification" in label):
                    try:
                        return c
                    except Exception:
                        continue
                # Priority 2: Technical/Verify keywords
                if any(k in label for k in ["verify doc", "verify", "technical", "action"]):
                    try:
                        return c
                    except Exception:
                        continue
            else:
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
        if driver_mode == "direct":
            tables = driver.eles('xpath://table')
            for tbl in tables:
                headers = [h.text.strip().lower() for h in tbl.eles('xpath:.//th')]
                if not headers:
                    headers = [c.text.strip().lower() for c in tbl.eles('xpath:.//tr[1]/td')]
                if not headers:
                    continue
                if any(k in h for h in headers for k in ["requirement", "document", "uploaded", "buyer", "file"]):
                    return tbl, headers
            return None, []

        tables = list_visible_elements("//table")
        for tbl in tables:
            headers = [h.text.strip().lower() for h in list_visible_elements(".//th", root=tbl)]
            if not headers:
                headers = [c.text.strip().lower() for c in list_visible_elements(".//tr[1]/td", root=tbl)]
            if not headers:
                continue
            if any(k in h for h in headers for k in ["requirement", "document", "uploaded", "buyer", "file"]):
                return tbl, headers
        return None, []

    def wait_for_downloads(snapshot, timeout=90):
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
            
            for _ in range(6): # Up to 6 heartbeat attempts
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

    def _download_via_requests(url: str, out_path: Path) -> bool:
        """Best-effort direct HTTP download using current browser cookies (Managed/Selenium mode)."""
        try:
            import requests
            if driver_mode == "direct":
                return False

            cookies = []
            try:
                cookies = driver.get_cookies() or []
            except Exception:
                cookies = []

            jar = requests.cookies.RequestsCookieJar()
            for c in cookies:
                try:
                    jar.set(
                        c.get("name"),
                        c.get("value"),
                        domain=c.get("domain"),
                        path=c.get("path") or "/",
                    )
                except Exception:
                    continue

            proxies = get_requests_proxies()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            }
            r = requests.get(
                url,
                stream=True,
                timeout=60,
                headers=headers,
                cookies=jar,
                proxies=proxies,
                allow_redirects=True,
            )
            if r.status_code != 200:
                return False
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
            return out_path.exists() and out_path.stat().st_size > 0
        except Exception:
            return False

    def _download_blob_url(blob_url: str, out_path: Path) -> bool:
        """Download a blob: URL by fetching it in-page and saving bytes locally (Managed/Selenium mode)."""
        if driver_mode == "direct":
            return False
        try:
            script = """
                const url = arguments[0];
                const cb = arguments[arguments.length - 1];
                fetch(url).then(r => r.arrayBuffer()).then(buf => {
                  const bytes = new Uint8Array(buf);
                  let binary = '';
                  const chunkSize = 0x8000;
                  for (let i = 0; i < bytes.length; i += chunkSize) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
                  }
                  cb(btoa(binary));
                }).catch(() => cb(null));
            """
            b64 = driver.execute_async_script(script, blob_url)
            if not b64:
                return False
            import base64
            data = base64.b64decode(b64)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            return out_path.exists() and out_path.stat().st_size > 0
        except Exception:
            return False

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

        if driver_mode == "direct":
            rows = tbl.eles('xpath:.//tbody/tr')
            if not rows:
                rows = tbl.eles('xpath:.//tr[position()>1]')
        else:
            rows = list_visible_elements(".//tbody/tr", root=tbl)
            if not rows:
                rows = list_visible_elements(".//tr[position()>1]", root=tbl)

        for row in rows:
            if driver_mode == "direct":
                cells = row.eles('xpath:./td')
                if not cells: continue
                req_text = cells[req_idx].text.strip() if req_idx is not None and req_idx < len(cells) else cells[0].text.strip()
                if not _doc_matches(req_text, doc_types):
                    continue

                # find download link/button
                link = None
                visible_actions = []
                for el in row.eles('xpath:.//a|.//button'):
                    label = ((el.text or "") + " " + (el.attr("title") or "") + " " + (el.attr("value") or "")).lower()
                    href = (el.attr("href") or "").lower()
                    if el.is_displayed:
                        visible_actions.append(el)
                    if _is_row_download_candidate(label, href):
                        if el.is_displayed:
                            link = el
                            break
                if not link and len(visible_actions) == 1:
                    link = visible_actions[0]
                if link:
                    before_tabs = set(safe_get_tab_ids(driver))
                    safe_click(link)
                    time.sleep(1)
                    after_tabs = set(safe_get_tab_ids(driver))
                    new_tabs = list(after_tabs - before_tabs)
                    if new_tabs:
                        try:
                            t = driver.get_tab(new_tabs[0])
                            t.activate()
                            u = (t.url or "").strip()
                            # Best-effort fallbacks when portal opens a viewer instead of saving.
                            if u.startswith("blob:"):
                                fname = f"{sanitize_filename(req_text) or 'document'}.pdf"
                                if _download_blob_url(u, downloads_folder / fname):
                                    downloads += 1
                            elif u.lower().startswith("http"):
                                fname = f"{sanitize_filename(req_text) or 'document'}.pdf"
                                if _download_via_requests(u, downloads_folder / fname):
                                    downloads += 1
                            try:
                                t.close()
                            except Exception:
                                pass
                            try:
                                driver.get_tab(main_window).activate()
                            except Exception:
                                pass
                            continue
                        except Exception:
                            pass
                    downloads += 1
                    time.sleep(1)
            else:
                cells = list_visible_elements("./td", root=row)
                if not cells: continue
                req_text = cells[req_idx].text.strip() if req_idx is not None and req_idx < len(cells) else cells[0].text.strip()
                if not _doc_matches(req_text, doc_types):
                    continue

                # find download link/button
                link = None
                visible_actions = []
                for el in list_visible_elements(".//a|.//button", root=row):
                    label = ((el.text or "") + " " + (el.get_attribute("title") or "") + " " + (el.get_attribute("value") or "")).lower()
                    href = (el.get_attribute("href") or "").lower()
                    if el.is_displayed():
                        visible_actions.append(el)
                    if _is_row_download_candidate(label, href):
                        if el.is_displayed():
                            link = el
                            break
                if not link and len(visible_actions) == 1:
                    link = visible_actions[0]
                if link:
                    before_handles = set(driver.window_handles)
                    safe_click(link)
                    time.sleep(1)
                    after_handles = set(driver.window_handles)
                    new_handles = list(after_handles - before_handles)
                    if new_handles:
                        try:
                            driver.switch_to.window(new_handles[0])
                            u = (driver.current_url or "").strip()
                            if u.startswith("blob:"):
                                fname = f"{sanitize_filename(req_text) or 'document'}.pdf"
                                if _download_blob_url(u, downloads_folder / fname):
                                    downloads += 1
                            elif u.lower().startswith("http"):
                                fname = f"{sanitize_filename(req_text) or 'document'}.pdf"
                                if _download_via_requests(u, downloads_folder / fname):
                                    downloads += 1
                            try:
                                driver.close()
                            except Exception:
                                pass
                            if main_window in driver.window_handles:
                                driver.switch_to.window(main_window)
                            continue
                        except Exception:
                            pass
                    downloads += 1
                    time.sleep(1)
        return downloads

    def close_popup(original_url=None):
        try:
            if driver_mode == "direct":
                 # DrissionPage handles tabs/windows
                 if driver.tab_id != main_window:
                     driver.close()
                     driver.get_tab(main_window).activate()
                     return
            else:
                if driver.current_window_handle != main_window:
                    driver.close()
                    if main_window in driver.window_handles:
                        driver.switch_to.window(main_window)
                    return
        except Exception: pass

        # Filter out buttons that look like navigation/back buttons
        exclude_nav = "and not(contains(translate(., 'ABC', 'abc'), 'back')) and not(contains(translate(., 'ABC', 'abc'), 'dash')) and not(contains(translate(., 'ABC', 'abc'), 'home'))"
        # We'll use a simpler exclusion for common nav terms
        expr = _build_or_contains_xpath(["close", "x", "cancel"])
        if expr:
            xpath = f"(//button[{expr}] | //a[{expr}] | //*[contains(@class, 'close')])"
            # Add exclusion logic for the text content
            xpath += "[not(contains(translate(text(), 'BACKDASHOME', 'backdashome'), 'back'))]"
            xpath += "[not(contains(translate(text(), 'BACKDASHOME', 'backdashome'), 'dash'))]"
            xpath += "[not(contains(translate(text(), 'BACKDASHOME', 'backdashome'), 'home'))]"
            
            btn = get_visible_element(xpath)
            if btn:
                logger.info(f"Clicking close button: {btn.text or 'X'}")
                safe_click(btn)
                time.sleep(1)
        
        # Avoid forcing history/navigation changes in the user's active GeM session.
        if original_url and is_same_window(main_window):
            curr_url = get_url()
            if curr_url != original_url and "gem.gov.in" in curr_url:
                 # We intentionally avoid back()/get() here to preserve cookies,
                 # browser history, and page state in the logged-in session.
                 if not find_firm_table()[0]:
                     logger.warning("Detected navigation away from firm list, but skipping forced back/navigation to preserve session state.")

        ensure_main_window()

    for firm in firms:
        if job_id and is_aborted(job_id):
            yield emit("info", {"message": "Download stopped by user."})
            break

        if not in_range(firm["sr"]):
            continue

        firm_name = firm["seller"]
        stats["processed"] += 1
        
        # If GeM logged out mid-run, pause and let the user login again.
        if _is_gem_logged_out():
            ok = yield from _wait_for_user_login_and_bid_page()
            if not ok:
                stats["failed"] += 1
                yield emit("progress", {"firm": firm_name, "status": "error", "message": "Timed out waiting for GeM login.", "stats": stats})
                break

        # Take a snapshot of the Downloads folder BEFORE clicking anything for this firm
        snapshot = set(downloads_folder.glob("*"))

        try:
            # 1. Open Popup
            list_page_url = get_url()
            action_btn = open_seller_popup(firm["row"])
            if not action_btn:
                stats["skipped"] += 1
                yield emit("progress", {"firm": firm_name, "status": "skipped", "message": "View Doc action not found.", "stats": stats})
                continue

            if driver_mode == "direct":
                before_tabs = set(safe_get_tab_ids(driver))
                safe_click(action_btn)
                time.sleep(2)
                # Success: assume one new tab
                after_tabs = set(safe_get_tab_ids(driver))
                new_tabs = list(after_tabs - before_tabs)
                if new_tabs:
                    driver.get_tab(new_tabs[0]).activate()
            else:
                before_handles = set(driver.window_handles)
                safe_click(action_btn)
                time.sleep(2)

                after_handles = set(driver.window_handles)
                new_handles = list(after_handles - before_handles)
                if new_handles:
                    driver.switch_to.window(new_handles[0])
            
            # Ensure we are in the correct context if it loaded in a frame or new window
            try:
                if driver_mode == "direct":
                    # DrissionPage wait_load
                    driver.wait.load_start()
                else:
                    WebDriverWait(driver, 15).until(
                        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
                    )
            except Exception: pass
            
            # If still on main window but URL changed, handle frame/modal or same-tab
            if driver_mode == "direct":
                if not new_tabs:
                    pass
            else:
                if not new_handles:
                    # It might have loaded in a frame on the same page, or replaced the content
                    # find_upload_table will search frames anyway
                    pass

            # 2. Extract Downloads
            download_clicked = download_from_popup()
            
            # 3. Wait and Merge
            new_files = wait_for_downloads(snapshot)
            merged_file = merge_firm_files(new_files, firm_name)
            
            close_popup(original_url=list_page_url)

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
