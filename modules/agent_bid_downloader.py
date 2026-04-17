import json
import os
import re
import time
import shutil
import subprocess
import zipfile
import uuid
import threading
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Iterator
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Constants
MAX_PDF_SIZE_BYTES = 19.9 * 1024 * 1024  # 20MB limit
DEFAULT_PORT = 9222

# Import existing doc processor utilities if available
try:
    from modules.doc_processor import split_pdf_by_size, merge_pdfs, compress_pdf
    HAS_DOC_PROC = True
except ImportError:
    HAS_DOC_PROC = False

# Import app data root
try:
    from modules.utils import DATA_ROOT, logger, safe_click
except ImportError:
    DATA_ROOT = Path("data")
    import logging
    logger = logging.getLogger("agent_bid_downloader")
    def safe_click(driver, driver_mode, el):
        el.click()

def emit(event_type: str, data: dict) -> str:
    """Helper formatting function for Server-Sent Events (SSE) stream."""
    data["type"] = event_type
    if "error" in data and "message" not in data:
        data["message"] = data["error"]
    return f"data: {json.dumps(data)}\n\n"


class AgentBrowserError(Exception):
    pass


STOP_AGENT_BID_EXECUTION = set()


def stop_agent_bid_job(job_id: str) -> None:
    if job_id:
        STOP_AGENT_BID_EXECUTION.add(job_id)


def clear_agent_bid_stop(job_id: Optional[str]) -> None:
    if job_id:
        STOP_AGENT_BID_EXECUTION.discard(job_id)


def is_agent_bid_aborted(job_id: Optional[str]) -> bool:
    return bool(job_id) and job_id in STOP_AGENT_BID_EXECUTION


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
        if dt_norm == "all" or dt_norm in req_norm:
            return True
        dt_tokens = [t for t in dt_norm.split() if t]
        if dt_tokens and all(t in req_tokens for t in dt_tokens):
            return True
    return False

def _is_view_docs_text(text: str) -> bool:
    norm = _normalize(text or "").lower()
    # Core phrases that identify the technical document / clarification action button in GeM
    phrases = ["view documents", "seek clarification", "view technical bid", "technical documents", "view bid"]
    return any(p in norm for p in phrases)

class AgentBidDownloader:
    @staticmethod
    def check_port(port: str) -> bool:
        """Checks if a process is listening on the specified localhost port."""
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                return s.connect_ex(('127.0.0.1', int(port))) == 0
        except Exception: 
            return False

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = str(port)
        self.output_base = self._get_desktop_path() / "PDF Bids"
        self.output_base.mkdir(parents=True, exist_ok=True)
        self.current_job_id = None
        self.running = False
        self._page = None

    def _get_desktop_path(self) -> Path:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            onedrive = Path.home() / "OneDrive" / "Desktop"
            if onedrive.exists(): return onedrive
        return desktop

    def _sanitize_path_part(self, value: str, fallback: str = "Unknown_Bid") -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_\-\s\.]", "", (value or "")).strip()
        cleaned = re.sub(r"\s+", "_", cleaned)
        return cleaned[:80] or fallback

    def _get_active_url(self) -> str:
        try:
            res = self.run_cmd(["get", "url"], timeout=15, check=False)
            return (res.get("data", {}) or {}).get("url", "") if isinstance(res, dict) else ""
        except Exception:
            return ""

    def _normalize_ref(self, ref: str) -> str:
        """Utility to ensure ref starts with @ for agent-browser."""
        if not ref:
            return ""
        ref = str(ref).strip()
        if not ref.startswith("@"):
            return f"@{ref}"
        return ref

    def _is_aborted(self) -> bool:
        return is_agent_bid_aborted(self.current_job_id)

    def _abort_if_requested(self) -> None:
        if self._is_aborted():
            raise AgentBrowserError("Stop requested by user.")

    def _sleep_interruptible(self, seconds: float, step: float = 0.25) -> None:
        end = time.time() + max(0, seconds)
        while time.time() < end:
            self._abort_if_requested()
            time.sleep(min(step, max(0, end - time.time())))

    def _ensure_page(self) -> Optional[ChromiumPage]:
        """Establish or return the existing DrissionPage connection."""
        is_ready = False
        if hasattr(self, "_page") and self._page:
            try:
                # Check if browser is responsive
                is_ready = bool(self._page.browser_id)
            except:
                is_ready = False

        if not is_ready:
             try:
                  co = ChromiumOptions()
                  co.set_address(f"127.0.0.1:{self.port}")
                  self._page = ChromiumPage(co)
             except Exception as e:
                  logger.warning(f"Could not connect DrissionPage on {self.port}: {e}")
                  self._page = None
        return self._page

    def _is_logged_out(self) -> bool:
        """Heuristic check for GeM session expiry/logout."""
        page = self._ensure_page()
        if not page: return False
        try:
             url = page.url.lower()
             # Common GeM logout / session-expired indicators
             if "auth/logout" in url or "/logout" in url or "sso.gem.gov.in" in url:
                  return True
             # Check for login buttons
             if page.ele('xpath://*[contains(text(), "Sign In") or contains(text(), "Login")]', timeout=1):
                  return "gem.gov.in" in url
             return False
        except: return False

    def _wait_for_login(self, timeout: int = 600):
        """Pause execution until the user logs back in."""
        yield emit("info", {"message": "GeM session appeared to logout. Please login in browser to continue."})
        deadline = time.time() + timeout
        while time.time() < deadline:
             self._abort_if_requested()
             if not self._is_logged_out():
                  # Re-check for the bid page or the technical summary
                  if self._ensure_page() and (self._page.ele('.view_docs', timeout=2) or "technical" in self._page.url.lower()):
                       yield emit("info", {"message": "Resuming after login..."})
                       return True
             time.sleep(2)
        raise AgentBrowserError("Login timeout reached.")

    def _get_tabs(self) -> list:
        res = self.run_cmd(["tab", "list"], timeout=20, check=False)
        if isinstance(res, dict):
            return (res.get("data", {}) or {}).get("tabs", []) or []
        return []

    def _switch_to_latest_tab(self) -> None:
        tabs = self._get_tabs()
        if not tabs:
            return
        latest_idx = max((t.get("index", 0) for t in tabs if isinstance(t, dict)), default=0)
        self.run_cmd(["tab", str(latest_idx)], timeout=15, check=False)

    def _wait_for_new_downloads(self, download_dir: Path, before_files: set, timeout: int = 90) -> List[Path]:
        deadline = time.time() + timeout
        print(f"DEBUG: Waiting for new files in {download_dir}...")
        while time.time() < deadline:
            self._abort_if_requested()
            current = {p for p in download_dir.glob("*") if p.is_file()}
            new_files = [p for p in current - before_files]
            if not new_files:
                self._sleep_interruptible(1)
                continue
            
            print(f"DEBUG: Detected new files: {[f.name for f in new_files]}")
            if any(p.suffix.lower() in {".crdownload", ".part", ".tmp"} for p in new_files):
                print("DEBUG: Waiting for partial downloads to finish...")
                self._sleep_interruptible(1.5)
                continue
            sizes1 = {p: p.stat().st_size for p in new_files if p.exists()}
            self._sleep_interruptible(1.2)
            sizes2 = {p: p.stat().st_size for p in new_files if p.exists()}
            if all(sizes1.get(p, -1) == sizes2.get(p, -2) for p in new_files):
                print(f"DEBUG: Files are stable: {[f.name for f in new_files]}")
                return [p for p in new_files if p.exists()]
        print("DEBUG: Download wait timed out.")
        return []

    def _snapshot_refs(self, depth: int = 12) -> dict:
        res = self.run_cmd(["snapshot", "-j", "--depth", str(depth)], timeout=45, check=False)
        data = res.get("data", {}) if isinstance(res, dict) else {}
        refs = data.get("refs", {})
        return refs if isinstance(refs, dict) else {}

    def _ordered_refs(self, refs: dict) -> list:
        def ref_sort_key(ref_id: str) -> int:
            match = re.search(r"(\d+)$", ref_id or "")
            return int(match.group(1)) if match else 0
        return sorted(refs.items(), key=lambda item: ref_sort_key(item[0]))

    def _find_download_all_ref(self, refs: dict) -> Optional[str]:
        for ref_id, meta in self._ordered_refs(refs):
            if not isinstance(meta, dict):
                continue
            label = (meta.get("name") or "").lower()
            if "download all" in label or "downloadall" in label:
                return ref_id
        return None

    def _find_close_ref(self, refs: dict) -> Optional[str]:
        for ref_id, meta in self._ordered_refs(refs):
            if not isinstance(meta, dict):
                continue
            label = (meta.get("name") or "").strip().lower()
            if label in {"close", "x", "cancel"} or "close" in label:
                return ref_id
        return None

    def _wait_for_modal(self, selector: str, timeout: int = 15) -> bool:
        deadline = time.time() + timeout
        # Broaden the selector to common Modal containers if the specific one fails
        selectors = [selector, ".modal.show", ".modal-dialog", "#modal-body", ".modal-content"]
        js = f"(() => {{ const selectors = {json.dumps(selectors)}; for (const s of selectors) {{ const el = document.querySelector(s); if (el && (el.offsetWidth > 0 || el.offsetHeight > 0)) return true; }} return false; }})()"
        while time.time() < deadline:
            self._abort_if_requested()
            res = self.run_cmd(["eval", js], timeout=10, check=False)
            val = (res.get("data", {}) or {}).get("result")
            if val is True or str(val).lower() == "true":
                return True
            time.sleep(1.0)
        return False

    def _documents_popup_opened(self, before_tabs: list, timeout: int = 6) -> Tuple[bool, bool]:
        deadline = time.time() + timeout
        before_count = len(before_tabs or [])
        while time.time() < deadline:
            self._abort_if_requested()
            current_tabs = self._get_tabs()
            if len(current_tabs) > before_count:
                return True, True
            if self._wait_for_modal("#myModaldoc", timeout=1) or self._wait_for_modal("#modal-overlay", timeout=1):
                return True, False
            time.sleep(0.4)
        return False, False

    def _force_open_local_modal(self, page: Optional[ChromiumPage]) -> bool:
        if not page:
            return False
        try:
            current_url = (self._get_active_url() or "").lower()
            if not current_url.startswith("file:///"):
                return False
            page.run_js(
                """
                (() => {
                    const modal = document.querySelector('#myModaldoc');
                    if (!modal) return false;
                    modal.style.display = 'block';
                    modal.classList.add('in');
                    modal.setAttribute('aria-hidden', 'false');
                    document.body.classList.add('modal-open');
                    return true;
                })()
                """
            )
            return self._wait_for_modal("#myModaldoc", timeout=2)
        except Exception as e:
            logger.warning(f"Could not force-open local modal: {e}")
            return False

    def _open_firm_documents(self, ref: str, before_tabs: list, page: Optional[ChromiumPage] = None, firm_index: Optional[int] = None) -> Tuple[bool, bool]:
        normalized_ref = self._normalize_ref(ref)
        if page is not None and firm_index is not None:
            attempts = [("page-index", firm_index), ("page-index-js", firm_index), ("agent-ref", None)]
        else:
            attempts = [("agent-ref", None), ("agent-ref-retry", None)]

        for method, index in attempts:
            self._abort_if_requested()
            try:
                if method == "page-index" and page is not None and index is not None:
                    btns = self._get_action_buttons(page)
                    if index < len(btns):
                        safe_click(page, "direct", btns[index])
                elif method == "page-index-js" and page is not None and index is not None:
                    btns = self._get_action_buttons(page)
                    if index < len(btns):
                        try:
                            btns[index].scroll.to_see()
                        except Exception:
                            pass
                        page.run_js("arguments[0].click();", btns[index])
                elif method.startswith("agent-ref"):
                    self.run_cmd(["scroll", "to", normalized_ref], check=False)
                    self._sleep_interruptible(0.5)
                    self.run_cmd(["click", normalized_ref], check=False)
            except Exception as e:
                logger.warning(f"Open documents attempt {method} failed for {ref}: {e}")

            opened, opened_new = self._documents_popup_opened(before_tabs, timeout=4)
            if opened:
                return True, opened_new
            if self._force_open_local_modal(page):
                return True, False
        return False, False

    def _get_action_buttons(self, page: ChromiumPage) -> List[object]:
        buttons = []
        selectors = ['.view_docs', 'tag:a', 'tag:button']
        seen = set()
        for selector in selectors:
            try:
                elements = page.eles(selector)
            except Exception:
                continue
            for el in elements:
                try:
                    text = getattr(el, "text", "") or ""
                    if not getattr(el, "is_displayed", True):
                        continue
                    if not _is_view_docs_text(text):
                        continue
                    key = (selector, el.attr('data-process_id'), el.attr('onclick'), text.strip())
                    if key in seen:
                        continue
                    seen.add(key)
                    buttons.append(el)
                except Exception:
                    continue
        return buttons

    def _find_popup_download_targets(self, doc_types: List[str]) -> List[dict]:
        """Scrape technical document links from the popup modal."""
        print("DEBUG: Taking snapshot for modal extraction...")
        snapshot_res = self.run_cmd(["snapshot", "-j", "--depth", "14"], timeout=45, check=False)
        refs = (snapshot_res.get("data", {}) or {}).get("refs", {}) if isinstance(snapshot_res, dict) else {}

        print("Analyzing modal contents...")
        dom_pairs = []
        page = self._ensure_page()
        if page:
            try:
                modal = page.ele('#myModaldoc', timeout=2)
                rows = modal.eles('tag:tr') if modal else []
                for row in rows:
                    try:
                        row_text = (row.text or "").strip()
                        if not row_text:
                            continue
                        links = row.eles('tag:a')
                        link_text = ""
                        for link in links:
                            txt = (link.text or "").strip()
                            if txt:
                                link_text = txt
                                break
                        dom_pairs.append([row_text, link_text or "Download"])
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"DrissionPage modal parsing failed: {e}")

        if not dom_pairs:
            js = "JSON.stringify(Array.from((document.querySelector('#myModaldoc') || document).querySelectorAll('tr, .row')).map(r => [(r.textContent||'').trim(), ((r.querySelector('a')||{}).textContent||'Download').trim()]).filter(r => r[0]))"
            try:
                res = self.run_cmd(["eval", js], timeout=15, check=False)
                dom_result = (res.get("data", {}) or {}).get("result") or "[]"
                dom_pairs = json.loads(dom_result)
            except Exception as e:
                logger.error(f"Failed to parse document pairs: {e}")
                dom_pairs = []

        print(f"Extracted {len(dom_pairs)} document pairs.")

        if not dom_pairs:
            logger.warning("No document pairs found in modal via DOM eval.")
            return []

        # Match DOM pairs to snapshot refs by order
        ordered_refs = self._ordered_refs(refs)
        targets = []
        for label_text, link_text in dom_pairs:
            if not label_text: continue
            if not _doc_matches(label_text, doc_types):
                continue

            # Find corresponding ref in snapshot
            low_link = (link_text or "").lower()
            for ref_id, meta in ordered_refs:
                m_name = (meta.get("name") or "").lower()
                if "download all" in m_name or m_name in {"close", "cancel", "x"}:
                    continue
                # Numeric IDs and row-level labels must map to the exact row action, not to a generic download-all control.
                if low_link and (m_name == low_link or low_link in m_name):
                    targets.append({"doc_name": label_text, "ref": ref_id})
                    break

        return targets

    def _find_firm_rows_dom(self) -> List[dict]:
        """Discovery using DrissionPage (visibility-aware) to avoid hidden/mobile duplication."""
        page = self._ensure_page()
        if page:
             try:
                  btns = self._get_action_buttons(page)
                  rows = []
                  for b in btns:
                       tr = b.parent('tr')
                       if not tr: continue
                       cid = tr.ele('.cid')
                       if cid:
                           name = cid.text.strip()
                       else:
                           cells = tr.eles('tag:td')
                           name = cells[1].text.strip() if len(cells) > 1 else "Unknown"
                       rows.append({"name": name, "action_text": b.text.strip()})
                  return rows
             except: pass
             
        # CLI Fallback if DrissionPage is unavailable
        js = """
        JSON.stringify(
            Array.from(new Set(
                Array.from(document.querySelectorAll('a, button, .view_docs'))
                .filter(el => {
                    const txt = (el.textContent || '').trim().toLowerCase();
                    return txt.includes('view documents') || txt.includes('seek clarification') || txt.includes('view technical bid') || txt.includes('view bid');
                })
                .map(el => el.closest('tr'))
            ))
            .filter(tr => tr !== null)
            .map(tr => {
                const actionBtn = Array.from(tr.querySelectorAll('a, button, .view_docs'))
                    .find(el => {
                        const txt = (el.textContent || '').trim().toLowerCase();
                        return txt.includes('view documents') || txt.includes('seek clarification') || txt.includes('view technical bid');
                    });
                return {
                    name: tr.querySelector('.cid')?.textContent?.trim() || 
                          tr.querySelector('td:nth-child(2)')?.textContent?.trim() || 
                          tr.querySelectorAll('td')[1]?.textContent?.trim() || 'Unknown',
                    action_text: (actionBtn?.textContent || 'View Documents').trim()
                };
            })
        )
        """
        try:
            res = self.run_cmd(["eval", js], timeout=10, check=False)
            result_str = (res.get("data", {}) or {}).get("result", "[]")
            return json.loads(result_str)
        except: return []

    def find_firm_rows(self) -> List[dict]:
        """Checklist 3: Use snapshot mode to identify firm rows semantically."""
        dom_rows = self._find_firm_rows_dom()
        
        # Depth 12 for accuracy vs performance
        res = self.run_cmd(["snapshot", "-j", "--depth", "12"], timeout=45, check=False)
        data = res.get("data", {}) if isinstance(res, dict) else {}
        refs = data.get("refs", {}) if isinstance(data.get("refs"), dict) else {}
        
        # Find candidate action refs in snapshot with strict visibility and text matching
        action_refs = []
        seen_refs = set()
        for ref_id, meta in self._ordered_refs(refs):
            if not isinstance(meta, dict): continue
            label = (meta.get("name") or "").strip().lower()
            cls = (meta.get("class") or "").lower()
            role = (meta.get("role") or "").lower()
            
            # Use specific GeM classes to filter out footer/global links
            # Filter by role to avoid matching the surrounding table cells/rows
            is_view_docs = (
                ("view_docs" in cls) or
                (_is_view_docs_text(label))
            )
            
            # Critical: only include interactive elements, not containers like cells/rows
            if is_view_docs and role not in ["cell", "row", "table", "group"]:
                if ref_id not in seen_refs:
                    action_refs.append(ref_id)
                    seen_refs.add(ref_id)

        if not dom_rows:
            seller_blocklist = (
                "bid number",
                "verify status",
                "pending",
                "click here",
                "view documents",
                "verify specifications",
                "verify doc",
                "download",
                "exemption",
                "exempted",
                "applied",
                "seller name",
                "seller rating",
                "sr. no",
                "sr no",
                "s. no",
                "action",
                "bid status",
                "technical evaluated",
                "evaluator",
                "recommendations",
                "evaluate",
                "english",
            )
            seller_cells = []
            for ref_id, meta in self._ordered_refs(refs):
                if not isinstance(meta, dict):
                    continue
                if (meta.get("role") or "").lower() != "cell":
                    continue
                name = (meta.get("name") or "").strip()
                name_norm = name.lower()
                if (
                    len(name) <= 3
                    or name.upper() == "N/A"
                    or re.fullmatch(r"\d+", name)
                    or re.search(r"\d{2}-\d{2}-\d{4}", name)
                    or any(bad in name_norm for bad in seller_blocklist)
                ):
                    continue
                seller_cells.append(name)

            resolved = []
            for idx, ref_id in enumerate(action_refs):
                if idx >= len(seller_cells):
                    break
                resolved.append({"name": seller_cells[idx], "ref": ref_id})
            return resolved

        # Reconcile and deduplicate by firm name to prevent double rows
        resolved = []
        for idx, row in enumerate(dom_rows):
            name = row.get("name")
            if idx < len(action_refs):
                resolved.append({"name": name, "ref": action_refs[idx]})
        
        final_resolved = []
        seen_names = set()
        for r in resolved:
            nm_norm = r["name"].lower().strip()
            if nm_norm not in seen_names:
                final_resolved.append(r)
                if nm_norm and nm_norm != "unknown":
                    seen_names.add(nm_norm)
              
        return final_resolved

    def _build_job_folder(self, gem_url: str, bid_id: str) -> Path:
        stable_url = (gem_url or "").strip()
        if stable_url:
            digest = hashlib.sha1(stable_url.encode("utf-8")).hexdigest()[:10]
            if stable_url.lower().startswith("file:///"):
                label_src = Path(stable_url.replace("file:///", "", 1)).stem
            else:
                label_src = bid_id or stable_url.rstrip("/").split("/")[-1]
            label = self._sanitize_path_part(label_src, fallback="Bid")
            return self.output_base / f"{label}_{digest}"
        return self.output_base / self._sanitize_path_part(bid_id or "Unknown_Bid")

    def run_cmd(self, args: List[str], timeout=120, check=True, download_path: Optional[Path] = None) -> dict:
        exe = shutil.which("agent-browser")
        if not exe: raise AgentBrowserError("agent-browser not found on PATH.")
        if not self.check_port(self.port):
            raise AgentBrowserError(f"No Chrome session found on port {self.port}")
        action_args = [exe, "--cdp", self.port, "--json"]
        if download_path: action_args.extend(["--download-path", str(download_path)])
        action_args += args
        print(f"DEBUG: Running Command: {' '.join(action_args)}")
        try:
            result = subprocess.run(action_args, capture_output=True, text=True, timeout=timeout, shell=False)
            if result.returncode != 0 and check:
                 return {"success": False, "error": (result.stderr or result.stdout).strip()}
            try:
                print(f"DEBUG: Raw Stdout: {result.stdout[:200]}")
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"success": (result.returncode == 0), "data": result.stdout, "error": (result.stderr or "").strip()}
        except subprocess.TimeoutExpired:
            raise AgentBrowserError(f"Command timed out: {args[0] if args else 'unknown'}")
        except Exception as e:
            raise AgentBrowserError(f"Execution failed: {e}")

    def is_valid(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0: return False
        ext = path.suffix.lower()
        if ext == ".pdf" and fitz:
            try:
                with fitz.open(path) as doc: return doc.is_pdf and doc.page_count > 0
            except: return False
        elif ext == ".zip":
            try:
                with zipfile.ZipFile(path) as z: return z.testzip() is None
            except: return False
        return True

    def heavy_compress_and_split(self, input_pdf: Path, firm_name: str, output_dir: Path) -> list:
        sanitized_firm = re.sub(r'[^a-zA-Z0-9_\-\s]', '', firm_name).strip().replace(' ', '_')
        final_filename = f"{sanitized_firm}_Technical_Bid.pdf"
        target_path = output_dir / final_filename
        if not fitz:
            shutil.copy(input_pdf, target_path)
            return [target_path]
        try:
            with fitz.open(input_pdf) as doc:
                doc.save(str(target_path), garbage=4, deflate=True, clean=True, linear=True)
            if target_path.stat().st_size <= MAX_PDF_SIZE_BYTES: return [target_path]
            if HAS_DOC_PROC:
                from modules.doc_processor import split_pdf_by_size
                parts = split_pdf_by_size(target_path, output_dir, f"{sanitized_firm}_Technical_Bid")
                if target_path.exists(): target_path.unlink()
                return [output_dir / p for p in parts]
            else:
                results = []
                doc = fitz.open(target_path)
                total_parts = (target_path.stat().st_size // int(MAX_PDF_SIZE_BYTES)) + 1
                pages_per_part = len(doc) // total_parts
                for i in range(total_parts):
                    start, end = i * pages_per_part, (i + 1) * pages_per_part if i < total_parts - 1 else len(doc)
                    p_path = output_dir / f"{sanitized_firm}_Technical_Bid_Part_{i+1}.pdf"
                    p_doc = fitz.open()
                    p_doc.insert_pdf(doc, from_page=start, to_page=end-1)
                    p_doc.save(str(p_path), garbage=4, deflate=True)
                    p_doc.close()
                    results.append(p_path)
                doc.close()
                target_path.unlink()
                return results
        except Exception as e:
            logger.error(f"Post-processing failed: {e}")
            if not target_path.exists(): shutil.copy(input_pdf, target_path)
            return [target_path]

    def process_firm_downloads(self, temp_dir: Path, out_dir: Path, firm_name: str) -> list:
        print(f"DEBUG: Processing downloads in {temp_dir}")
        zips = list(temp_dir.rglob("*.zip"))
        for z in zips:
            if self.is_valid(z):
                try:
                    ext_to = temp_dir / f"extracted_{z.stem}"
                    ext_to.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(z, 'r') as zf: zf.extractall(ext_to)
                except: pass
        valid_pdfs = [p for p in temp_dir.rglob("*.pdf") if self.is_valid(p)]
        print(f"DEBUG: Found {len(valid_pdfs)} valid PDFs: {valid_pdfs}")
        if not valid_pdfs: return []
        merged_temp = temp_dir / "merged_full.pdf"
        try:
            m_doc = fitz.open()
            for p in sorted(valid_pdfs, key=lambda x: x.name):
                with fitz.open(p) as d: m_doc.insert_pdf(d)
            m_doc.save(str(merged_temp))
            m_doc.close()
            print(f"DEBUG: Merged PDF created at {merged_temp}")
            return self.heavy_compress_and_split(merged_temp, firm_name, out_dir)
        except Exception as e:
            print(f"DEBUG: PDF merge failed: {e}")
            return []

    def start_heartbeat(self):
        import random
        def beat():
            while self.running:
                try:
                    time.sleep(random.uniform(45, 120))
                    if not self.running: break
                    scroll = random.randint(100, 300)
                    self.run_cmd(["scroll", "down", str(scroll)], check=False)
                    time.sleep(random.uniform(1, 2))
                    self.run_cmd(["scroll", "up", str(scroll)], check=False)
                except: pass
        threading.Thread(target=beat, daemon=True).start()

    def get_bid_id(self) -> str:
        try:
            res = self.run_cmd(["snapshot", "--compact", "--depth", "5"], timeout=30)
            text = res.get("data", {}).get("snapshot", "")
            match = re.search(r"GEM/\d{4}/[A-Z]/\d+", text)
            if match: return match.group(0).replace("/", "_")
        except: pass
        return "Unknown_Bid"

    def check_resume(self, bid_id: str, firm_name: str, job_folder: Optional[Path] = None) -> Optional[Path]:
        sanitized = re.sub(r'[^a-zA-Z0-9_\-\s]', '', firm_name).strip().replace(' ', '_')
        candidates = [job_folder] if job_folder else []
        candidates.extend(sorted(list(self.output_base.glob(f"{bid_id}_*")), reverse=True))
        for folder in candidates:
             target = folder / firm_name / f"{sanitized}_Technical_Bid.pdf"
             if target.exists() and self.is_valid(target): return target
             parts = list((folder / firm_name).glob(f"{sanitized}_Technical_Bid_Part_*.pdf"))
             if parts and all(self.is_valid(p) for p in parts): return parts[0].parent
        return None

    def automate_download(self, gem_url=None, doc_types=None, download_all=False, si_from=None, si_to=None, job_id=None, use_classic=False):
        doc_types = doc_types or []
        self.current_job_id = job_id or str(uuid.uuid4())
        clear_agent_bid_stop(self.current_job_id)
        self.running = True
        self.stats = {"total_firms": 0, "processed": 0, "downloaded": 0, "skipped": 0, "failed": 0}
        self.start_heartbeat()
        mode_label = "Classic Mode" if use_classic else "Agent V2"
        yield emit("info", {"message": f"Bid Agent {mode_label} started.", "stats": self.stats})

        try:
             page = self._ensure_page()
             if not page and not gem_url:
                  yield emit("error", {"error": "Browser session (9222) not ready."})
                  return

             if gem_url:
                  # Check if we are already on that page to avoid unnecessary refresh
                  current_url = self._get_active_url().strip().lower().rstrip('/')
                  target_url = gem_url.strip().lower().rstrip('/')
                  if current_url != target_url:
                       if page: page.get(gem_url)
                       else: self.run_cmd(["open", gem_url])
                  
             if self._is_logged_out():
                  yield from self._wait_for_login()

             bid_id = self.get_bid_id()
             job_folder = self._build_job_folder(gem_url or self._get_active_url(), bid_id)
             job_folder.mkdir(parents=True, exist_ok=True)
             
             firms = self.find_firm_rows()
             if not firms:
                  yield emit("error", {"error": "No firms detected."})
                  return
             self.stats["total_firms"] = len(firms)
             
             for i, firm in enumerate(firms):
                  self._abort_if_requested()
                  if self._is_logged_out():
                       yield from self._wait_for_login()
                       
                  idx = i + 1
                  if si_from and idx < si_from: continue
                  if si_to and idx > si_to: break
                  name, ref = firm["name"], firm["ref"]
                  
                  existing = self.check_resume(bid_id, name, job_folder)
                  if existing:
                       self.stats["skipped"] += 1
                       self.stats["processed"] += 1
                       yield emit("progress", {"firm": name, "message": f"[{idx}] Skipping {name} (Already Downloaded)", "status": "skipped", "stats": self.stats})
                       continue

                  yield emit("progress", {"firm": name, "message": f"[{idx}] Processing {name}...", "status": "processing", "stats": self.stats})
                  
                  # 3) Interaction
                  before_tabs = self._get_tabs()
                  opened, opened_new = self._open_firm_documents(
                       ref,
                       before_tabs,
                       page=page if page else None,
                       firm_index=i,
                  )
                  if not opened:
                       self.stats["failed"] += 1
                       self.stats["processed"] += 1
                       yield emit("progress", {"firm": name, "message": f"[{idx}] Could not open documents for {name}.", "status": "failed", "stats": self.stats})
                       continue
                  if opened_new:
                       self._switch_to_latest_tab()
                  
                  temp_dl = DATA_ROOT / f"agent_dl_{self.current_job_id}_{idx}"
                  temp_dl.mkdir(parents=True, exist_ok=True)
                  success = False
                  try:
                       if download_all:
                            self._abort_if_requested()
                            refs = self._snapshot_refs()
                            btn = self._find_download_all_ref(refs)
                            if btn:
                                 before = set(temp_dl.glob("*"))
                                 self.run_cmd(["click", self._normalize_ref(btn)], download_path=temp_dl, check=False)
                                 success = bool(self._wait_for_new_downloads(temp_dl, before))
                       if not success:
                            self._abort_if_requested()
                            targets = self._find_popup_download_targets(doc_types)
                            for t in targets:
                                 self._abort_if_requested()
                                 before = set(temp_dl.glob("*"))
                                 self.run_cmd(["click", self._normalize_ref(t["ref"])], download_path=temp_dl, check=False)
                                 self._wait_for_new_downloads(temp_dl, before)
                            success = bool(list(temp_dl.glob("*")))
                       
                       if success:
                            firm_dir = job_folder / name
                            firm_dir.mkdir(parents=True, exist_ok=True)
                            if self.process_firm_downloads(temp_dl, firm_dir, name): 
                                 self.stats["downloaded"] += 1
                            else: 
                                 self.stats["failed"] += 1
                       else: 
                            self.stats["failed"] += 1
  
                       if not opened_new:
                            close_ref = self._find_close_ref(self._snapshot_refs())
                            if close_ref: self.run_cmd(["click", self._normalize_ref(close_ref)], check=False)
                  finally:
                       if temp_dl.exists(): shutil.rmtree(temp_dl)
                       if opened_new:
                            self.run_cmd(["tab", "close"], check=False)
                            self._switch_to_latest_tab()
                  self.stats["processed"] += 1
             yield emit("success", {"message": "Job finished.", "stats": self.stats, "output": str(job_folder)})
        except Exception as e:
             yield emit("error", {"error": str(e)})
        finally:
             self.running = False
             clear_agent_bid_stop(self.current_job_id)

def automate_agent_bid_download(**kwargs):
    return AgentBidDownloader().automate_download(**kwargs)
