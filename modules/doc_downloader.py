"""
Noting Bot - Module 2: Document Downloader & Organizer
Downloads tender/NIT documents from URLs and organizes them into case folders.
"""

import os
import re
import time
import requests
from pathlib import Path
from urllib.parse import urlparse, unquote
from modules.utils import CONFIG, logger, get_case_folder, sanitize_filename
from modules.database import register_document


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

DOC_TYPES = {
    "NIT": "NIT",
    "BOQ": "BOQ",
    "TENDER": "Tender Docs",
    "BID": "Bids",
    "DRAWING": "Drawings",
    "CORRIGENDUM": "Corrigendum",
    "OTHER": "Misc"
}


def _guess_filename(url: str, response: requests.Response) -> str:
    """Try to guess filename from URL or Content-Disposition header."""
    # Try Content-Disposition header
    cd = response.headers.get("Content-Disposition", "")
    match = re.findall(r'filename[*]?=["\']?([^"\';\n]+)', cd)
    if match:
        return sanitize_filename(unquote(match[0].strip()))

    # Fall back to URL path
    path = unquote(urlparse(url).path)
    name = path.split("/")[-1]
    if name and "." in name:
        return sanitize_filename(name)

    # Default name with timestamp
    return f"document_{int(time.time())}.pdf"


def download_document(
    url: str,
    case_id: str,
    doc_type: str = "OTHER",
    custom_filename: str = None
) -> dict:
    """
    Download a document from a URL and save it to the correct case subfolder.

    Returns a dict with 'success', 'file_path', and 'filename'.
    """
    subfolder = DOC_TYPES.get(doc_type.upper(), "Misc")
    save_dir = get_case_folder(case_id, subfolder)

    try:
        logger.info(f"Downloading: {url}")
        session = requests.Session()
        session.headers.update(HEADERS)

        response = session.get(url, timeout=60, stream=True)
        response.raise_for_status()

        filename = custom_filename or _guess_filename(url, response)
        file_path = save_dir / filename

        # Write file in chunks
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size = file_path.stat().st_size
        logger.info(f"Downloaded: {filename} ({file_size:,} bytes) → {file_path}")

        # Register in database
        register_document({
            "case_id": case_id,
            "doc_type": doc_type,
            "filename": filename,
            "file_path": str(file_path),
            "source_url": url,
            "notes": f"Auto-downloaded. Size: {file_size:,} bytes"
        })

        return {"success": True, "file_path": str(file_path), "filename": filename}

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error downloading {url}: {e}")
        return {"success": False, "error": str(e), "url": url}
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        return {"success": False, "error": str(e), "url": url}


def download_multiple(urls_with_types: list, case_id: str) -> list:
    """
    Download multiple documents.
    urls_with_types: list of dicts with keys 'url' and optionally 'doc_type', 'filename'

    Returns list of result dicts.
    """
    results = []
    for item in urls_with_types:
        url = item.get("url", "")
        doc_type = item.get("doc_type", "OTHER")
        filename = item.get("filename", None)
        if url:
            result = download_document(url, case_id, doc_type, filename)
            results.append(result)
            time.sleep(1)  # polite delay between downloads
    return results


def list_case_documents(case_id: str) -> list:
    """List all files in a case's folder recursively."""
    case_path = get_case_folder(case_id)
    files = []
    for f in case_path.rglob("*"):
        if f.is_file():
            files.append({
                "name": f.name,
                "path": str(f),
                "subfolder": f.parent.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": f.stat().st_mtime
            })
    return sorted(files, key=lambda x: x["modified"], reverse=True)


def open_case_folder(case_id: str):
    """Open the case folder in Windows Explorer."""
    case_path = get_case_folder(case_id)
    os.startfile(str(case_path))


if __name__ == "__main__":
    # Quick test
    result = download_document(
        url="https://eprocure.gov.in/epublish/app",
        case_id="TEST_001",
        doc_type="NIT"
    )
    print(result)
