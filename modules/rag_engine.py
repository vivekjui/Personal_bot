import os
import sys
import threading
import re
import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from modules.utils import (
    CONFIG,
    DEFAULT_QA_SYSTEM_PROMPT,
    logger,
    extract_text_from_pdf,
    BOT_ROOT,
    DB_ROOT,
)
from modules.database import get_app_setting
KB_DIR        = DB_ROOT / "knowledge_base"
CHROMA_DIR    = KB_DIR / "chroma_db"
META_DB_PATH  = KB_DIR / "kb_meta.db"
KB_CACHE_DIR  = KB_DIR / "text_cache"
KB_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
KB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Document categories for the Knowledge Base ─────────────────────────────────
DOC_CATEGORIES = [
    "Manual / Handbook",
    "Government Circular / OM",
    "Standard Guidelines / SOP",
    "Draft Noting (Template)",
    "Previous Noting (Reference)",
    "Tender / NIT Document",
    "Work Order / Contract",
    "Bill / Payment Document",
    "Court Judgment / Legal",
    "Other Reference"
]

# ── Chunk settings ─────────────────────────────────────────────────────────────
CHUNK_SIZE    = 800   # characters per chunk
CHUNK_OVERLAP = 150   # overlap between consecutive chunks


# ══════════════════════════════════════════════════════════════════════════════
# VECTOR DB LAYER  (ChromaDB with Gemini Cloud Embeddings)
# ══════════════════════════════════════════════════════════════════════════════

import threading

# Global references
_CHROMA_CLIENT = None
_CHROMA_COLLECTION = None
_chroma_init_lock = threading.Lock()

def _get_chroma_collection():
    """Return (or create) the ChromaDB collection using Gemini Cloud Embeddings."""
    global _CHROMA_CLIENT, _CHROMA_COLLECTION
    
    if _CHROMA_COLLECTION is not None:
        return _CHROMA_COLLECTION

    with _chroma_init_lock:
        if _CHROMA_COLLECTION is not None:
            return _CHROMA_COLLECTION

        import chromadb
        from chromadb.utils import embedding_functions

        if _CHROMA_CLIENT is None:
            # Disable ChromaDB telemetry explicitly in settings
            from chromadb.config import Settings
            _CHROMA_CLIENT = chromadb.PersistentClient(
                path=str(CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False)
            )

        # Switch to Gemini Cloud Embeddings (removes dependence on torch/sentence-transformers)
        api_key = CONFIG.get("gemini_api_key", "")
        rag_conf = CONFIG.get("rag", {}) or {}
        # Preferred method: use Gemini Cloud Embeddings if an API key is provided.
        if api_key and api_key != "YOUR_GEMINI_API_KEY_HERE":
            # allow override via config (for future-proofing or testing)
            model_name = rag_conf.get("embedding_model")
            # default to a known valid model name when not provided
            if not model_name:
                model_name = "textembedding-gecko-001"
            # translate any deprecated names automatically
            if model_name == "models/text-embedding-004":
                model_name = "textembedding-gecko-001"

            try:
                ef = embedding_functions.GoogleGenerativeAiEmbeddingFunction(
                    api_key=api_key,
                    model_name=model_name
                )
            except Exception as e:
                logger.error(f"Failed to initialize Gemini embedding model '{model_name}': {e}")
                logger.error("Falling back to default embedding function; RAG results may be poor.")
                ef = embedding_functions.DefaultEmbeddingFunction()
        else:
            # no Gemini key: fall back to local sentence-transformers model specified in rag config
            local_model = rag_conf.get("embedding_model")
            if local_model:
                try:
                    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name=local_model
                    )
                except Exception as e:
                    logger.error(f"Failed to load local embedding model '{local_model}': {e}")
                    ef = embedding_functions.DefaultEmbeddingFunction()
            else:
                logger.error("No Gemini API key or local embedding model configured; using placeholder embedding function.")
                ef = embedding_functions.DefaultEmbeddingFunction()

        _CHROMA_COLLECTION = _CHROMA_CLIENT.get_or_create_collection(
            name="apmd_knowledge_base",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"}
        )
    return _CHROMA_COLLECTION


def prewarm_vector_db():
    """Trigger vector DB initialization in a safe background thread."""
    def _run():
        try:
            logger.info("Pre-warming Vector Database (ChromaDB)...")
            _get_chroma_collection()
            logger.info("Vector Database ready.")
        except Exception as e:
            logger.warning(f"Vector Database pre-warm failed: {e}")
    
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def ingest_procurement_dictionary(json_path: str):
    """
    Ingest a JSON dictionary of procurement terms into the vector store.
    """
    if not os.path.exists(json_path):
        logger.error(f"Dictionary file not found: {json_path}")
        return False
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        terms = data.get("dictionary", [])
        if not terms:
            logger.warning("No terms found in dictionary.")
            return False
            
        col = _get_chroma_collection()
        
        ids = []
        documents = []
        metadatas = []
        
        for item in terms:
            term = item["term"]
            definition = item["definition"]
            cat = item.get("category", "Dictionary")
            
            doc_id = f"dict_{hashlib.md5(term.encode()).hexdigest()}"
            content = f"TERM: {term}\nDEFINITION: {definition}\nCATEGORY: {cat}"
            
            ids.append(doc_id)
            documents.append(content)
            metadatas.append({
                "source": "Procurement Dictionary",
                "category": cat,
                "term": term,
                "ingested_at": datetime.now().isoformat()
            })
            
        if ids:
            col.upsert(ids=ids, documents=documents, metadatas=metadatas)
            logger.info(f"Ingested {len(ids)} terms from procurement dictionary.")
            return True
            
    except Exception as e:
        logger.error(f"Failed to ingest dictionary: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# METADATA DATABASE  (SQLite — tracks ingested documents)
# ══════════════════════════════════════════════════════════════════════════════

def _init_meta_db():
    conn = sqlite3.connect(META_DB_PATH)
    # Add document_name if missing (migration)
    cursor = conn.execute("PRAGMA table_info(kb_documents)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "document_name" not in columns:
        try:
            conn.execute("ALTER TABLE kb_documents ADD COLUMN document_name TEXT")
            conn.commit()
            logger.info("Migrated kb_documents: added document_name column.")
        except Exception:
            # Table might not exist yet
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
            id              TEXT PRIMARY KEY,
            filename        TEXT NOT NULL,
            document_name   TEXT,
            filepath        TEXT NOT NULL,
            category        TEXT,
            description     TEXT,
            page_count      INTEGER DEFAULT 0,
            chunk_count     INTEGER DEFAULT 0,
            file_hash       TEXT,
            ingested_at     TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()

_init_meta_db()


def _file_hash(filepath: str) -> str:
    """MD5 hash of file — used to detect duplicates."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_all_kb_documents() -> List[Dict]:
    """Return all documents in the knowledge base metadata table."""
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM kb_documents ORDER BY ingested_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_kb_document(doc_id: str) -> bool:
    """Remove a document from ChromaDB and the metadata table."""
    try:
        col = _get_chroma_collection()
        # Delete all chunks belonging to this document
        results = col.get(where={"doc_id": doc_id})
        if results["ids"]:
            col.delete(ids=results["ids"])
        conn = sqlite3.connect(META_DB_PATH)
        conn.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted KB doc: {doc_id}")
        return True
    except Exception as e:
        logger.error(f"KB delete error: {e}")
        return False


def update_document_category(doc_id: str, new_category: str) -> bool:
    """Update the category of a document in both SQLite and ChromaDB."""
    try:
        # 1. Update SQLite
        conn = sqlite3.connect(META_DB_PATH)
        conn.execute("UPDATE kb_documents SET category = ? WHERE id = ?", (new_category, doc_id))
        conn.commit()
        conn.close()

        # 2. Update ChromaDB Metadata
        col = _get_chroma_collection()
        results = col.get(where={"doc_id": doc_id})
        
        if results["ids"] and results["metadatas"]:
            new_metadatas = []
            for meta in results["metadatas"]:
                updated = dict(meta) if meta else {}
                updated["category"] = new_category
                new_metadatas.append(updated)
                
            col.update(
                ids=results["ids"],
                metadatas=new_metadatas
            )
            
        logger.info(f"Updated KB doc {doc_id} category to '{new_category}'")
        return True
    except Exception as e:
        logger.error(f"KB category update error: {e}")
        return False

def _extract_document_name(text: str) -> str:
    """Use AI to identify the formal name/title of the document from its first page/paragraphs."""
    from modules.utils import ask_gemini
    
    # Take first 2000 chars for context
    sample = text[:2000]
    prompt = f"""Identify the formal official title or name of this document (e.g., 'GFR 2017', 'Manual for Procurement of Goods', 'GSI Store Manual'). 
Look for headers, subjects, or formal titles on the first page.
Provide ONLY the title as a short string (max 10 words). Do not add any explanation.

Document Sample:
{sample}

Title:"""
    try:
        title = ask_gemini(prompt)
        if title:
            return title.strip().replace('"', '').replace("'", "")
    except Exception:
        pass
    return "Unknown Document"


# ══════════════════════════════════════════════════════════════════════════════
# TEXT PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def _extract_text(filepath: str) -> str:
    """Extract text from PDF, DOCX, or TXT files."""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(filepath)
    elif ext in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            return ""
    elif ext in (".txt", ".md"):
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    else:
        logger.warning(f"Unsupported file type: {ext}")
        return ""


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split text into overlapping chunks for embedding.
    Splits on sentence boundaries where possible.
    """
    # Clean up
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return []

    # Split into sentences (basic)
    sentences = re.split(r'(?<=[.!?।])\s+', text)
    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > chunk_size:
            if current:
                chunks.append(current.strip())
            # Start new chunk with overlap from end of last chunk
            if chunks:
                overlap_text = chunks[-1][-overlap:] if len(chunks[-1]) > overlap else chunks[-1]
                current = overlap_text + " " + sentence
            else:
                current = sentence
        else:
            current += " " + sentence

    if current.strip():
        chunks.append(current.strip())

    # Filter out very short chunks
    return [c for c in chunks if len(c) > 50]


# ══════════════════════════════════════════════════════════════════════════════
# INGESTION
# ══════════════════════════════════════════════════════════════════════════════

def ingest_document(
    filepath: str,
    category: str = "Other Reference",
    description: str = "",
    force_reingest: bool = False
) -> Dict:
    """
    Ingest a document into the Knowledge Base.

    Steps:
      1. Extract text
      2. Chunk text
      3. Embed chunks via sentence-transformers
      4. Store in ChromaDB
      5. Record metadata in SQLite

    Returns result dict with success flag, chunk_count, doc_id.
    """
    filepath = str(filepath)
    filename = Path(filepath).name

    if not Path(filepath).exists():
        return {"success": False, "error": f"File not found: {filepath}"}

    fhash = _file_hash(filepath)
    doc_id = fhash[:16]  # use hash prefix as stable unique ID

    # Check for duplicate
    conn = sqlite3.connect(META_DB_PATH)
    existing = conn.execute(
        "SELECT id FROM kb_documents WHERE file_hash = ?", (fhash,)
    ).fetchone()
    conn.close()

    if existing and not force_reingest:
        logger.info(f"Document already ingested: {filename}")
        return {"success": True, "doc_id": doc_id, "skipped": True,
                "message": f"'{filename}' already in Knowledge Base."}

    # Extract + chunk
    logger.info(f"Ingesting: {filename} [{category}]")
    text = _extract_text(filepath)
    if not text.strip():
        return {"success": False, "error": f"No text extracted from {filename}. Is it a scanned (image-only) PDF?"}

    # Extract Formal Document Name (AI)
    doc_name = _extract_document_name(text)
    logger.info(f"Identified Document Name: {doc_name}")

    chunks = _chunk_text(text)
    if not chunks:
        return {"success": False, "error": "No usable text chunks found."}

    # Store chunks in ChromaDB
    col = _get_chroma_collection()
    chunk_ids   = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metadatas   = [{
        "doc_id":      doc_id,
        "filename":    filename,
        "doc_name":    doc_name,
        "category":    category,
        "description": description,
        "chunk_index": i,
        "chunk_total": len(chunks)
    } for i in range(len(chunks))]

    # Delete old chunks if re-ingesting
    if force_reingest:
        try:
            old = col.get(where={"doc_id": doc_id})
            if old["ids"]:
                col.delete(ids=old["ids"])
        except Exception:
            pass

    # Upsert in batches of 50 to avoid memory issues
    batch_size = 50
    for i in range(0, len(chunks), batch_size):
        col.upsert(
            ids=chunk_ids[i:i+batch_size],
            documents=chunks[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size]
        )

    # Save metadata
    conn = sqlite3.connect(META_DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO kb_documents
            (id, filename, document_name, filepath, category, description, chunk_count, file_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (doc_id, filename, doc_name, filepath, category, description, len(chunks), fhash))
    conn.commit()
    conn.close()

    logger.info(f"Ingested '{filename}': {len(chunks)} chunks into ChromaDB.")
    return {
        "success":     True,
        "doc_id":      doc_id,
        "filename":    filename,
        "category":    category,
        "chunk_count": len(chunks),
        "message":     f"✅ '{filename}' ingested successfully — {len(chunks)} knowledge chunks added."
    }


def ingest_folder(
    folder_path: str,
    category: str = "Other Reference",
    recursive: bool = False
) -> List[Dict]:
    """Ingest all PDFs/DOCXs/TXTs in a folder."""
    folder = Path(folder_path)
    pattern = "**/*" if recursive else "*"
    supported = {".pdf", ".docx", ".txt", ".md"}
    files = [f for f in folder.glob(pattern) if f.is_file() and f.suffix.lower() in supported]
    results = []
    for f in files:
        result = ingest_document(str(f), category=category)
        results.append(result)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# RETRIEVAL
# ══════════════════════════════════════════════════════════════════════════════

def retrieve_context(
    query: str,
    n_results: int = 5,
    category_filter: Optional[str] = None
    ) -> Tuple[str, List[str]]:
    """
    Semantic search the Knowledge Base for context relevant to `query`.

    Returns a tuple: (formatted_context_string, list_of_unique_source_names)
    """
    from typing import Tuple
    try:
        col = _get_chroma_collection()
        total = col.count()
        if total == 0:
            return "", []   # KB is empty

        where = {"category": category_filter} if category_filter else None
        results = col.query(
            query_texts=[query],
            n_results=min(n_results, total),
            where=where,
            include=["documents", "metadatas", "distances"]
        )

        docs      = results["documents"][0]
        metas     = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            return "", []

        # Format as readable context block
        context_parts = []
        for doc, meta, dist in zip(docs, metas, distances):
            relevance = round((1 - dist) * 100, 1)
            if relevance < 40:   # skip low-relevance results
                continue
            
            # Source resolution priority: doc_name > filename > source > '?'
            src_name = meta.get('doc_name') or meta.get('filename') or meta.get('source') or '?'
            src = f"{src_name} [{meta.get('category','?')}]"
            context_parts.append(
                f"--- Source: {src} | Relevance: {relevance}% ---\n{doc}"
            )

        if not context_parts:
            return "", []

        # Deduct unique sources
        seen_sites = set()
        for meta in metas:
            name = meta.get("doc_name") or meta.get("filename") or meta.get("source")
            if name:
                seen_sites.add(name)

        context = "\n\n".join(context_parts)
        logger.info(f"RAG: Retrieved {len(context_parts)} relevant passages for query.")
        return context, list(seen_sites)

    except Exception as e:
        logger.warning(f"RAG retrieval error: {e}")
        return "", []


def ask_gemini_with_rag(
    prompt: str,
    query_for_retrieval: str = "",
    n_results: int = 5,
    category_filter: Optional[str] = None
) -> Dict:
    """
    Enhanced Gemini call that first retrieves relevant KB context,
    then injects it into the prompt as grounding knowledge.
    Returns {"answer": str, "sources": list}.
    """
    from modules.utils import ask_gemini
    from modules.database import get_recent_qa_feedback

    retrieval_query = query_for_retrieval or prompt[:300]
    context, sources = retrieve_context(retrieval_query, n_results=n_results, category_filter=category_filter)

    # --- CONTINUOUS LEARNING: Fetch recent feedback ---
    learning_context = ""
    try:
        feedback_entries = get_recent_qa_feedback(limit=3)
        if feedback_entries:
            learning_context = "\n=== CONTINUOUS LEARNING (User Feedback) ===\n"
            for fb in feedback_entries:
                learning_context += f"Q: {fb['question']}\nCorrection/Feedback: {fb['feedback']}\n---\n"
    except Exception as e:
        logger.warning(f"Failed to fetch feedback for learning: {e}")

    prompt_template = get_app_setting("qa_system_prompt", DEFAULT_QA_SYSTEM_PROMPT)
    try:
        system_prompt = prompt_template.format(
            learning_context=learning_context,
            context=context,
            prompt=prompt
        )
    except Exception as e:
        logger.warning(f"Invalid Q&A system prompt template. Falling back to default. Error: {e}")
        system_prompt = DEFAULT_QA_SYSTEM_PROMPT.format(
            learning_context=learning_context,
            context=context,
            prompt=prompt
        )
    answer = ask_gemini(system_prompt)
    return {"answer": answer, "sources": sources}


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH (for UI)
# ══════════════════════════════════════════════════════════════════════════════

def search_kb(query: str, n_results: int = 8) -> List[Dict]:
    """
    Search the KB and return structured results for the UI.
    """
    try:
        col = _get_chroma_collection()
        if col.count() == 0:
            return []
        results = col.query(
            query_texts=[query],
            n_results=min(n_results, col.count()),
            include=["documents", "metadatas", "distances"]
        )
        out = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        ):
            out.append({
                "text":       doc[:400] + ("..." if len(doc) > 400 else ""),
                "filename":   meta.get("filename", "?"),
                "category":   meta.get("category", "?"),
                "relevance":  round((1 - dist) * 100, 1),
                "chunk":      f"{meta.get('chunk_index',0)+1}/{meta.get('chunk_total','?')}"
            })
        return sorted(out, key=lambda x: x["relevance"], reverse=True)
    except Exception as e:
        logger.warning(f"KB search error: {e}")
        return []


def kb_stats() -> Dict:
    """Return statistics about the knowledge base."""
    try:
        col = _get_chroma_collection()
        total_chunks = col.count()
    except Exception:
        total_chunks = 0

    docs = get_all_kb_documents()
    cats = {}
    for d in docs:
        c = d.get("category", "Other")
        cats[c] = cats.get(c, 0) + 1

    return {
        "total_documents": len(docs),
        "total_chunks":    total_chunks,
        "categories":      cats,
        "db_path":         str(CHROMA_DIR)
    }


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC BACKGROUND INGEST JOB QUEUE
# ══════════════════════════════════════════════════════════════════════════════
import threading
import uuid

# In-memory job status store  {job_id: {status, filename, result}}
_ingest_jobs: Dict[str, Dict] = {}
_active_ingest_files: Dict[str, str] = {} # {filepath_hash: job_id}
_jobs_lock = threading.Lock()


def ingest_document_async(
    filepath: str,
    category: str = "Other Reference",
    description: str = "",
    force_reingest: bool = False
) -> str:
    """
    Fire-and-forget wrapper around ingest_document().
    Returns a job_id immediately; the actual ingestion runs in a background thread.
    The job dict is updated with 'pct' (0-100) as chunks are embedded so the UI
    can poll /api/kb/ingest/status/<job_id> for a live progress bar.
    """
    job_id   = str(uuid.uuid4())[:8]
    filename = Path(filepath).name
    fhash    = _file_hash(filepath)
    doc_id   = fhash[:16]

    with _jobs_lock:
        if fhash in _active_ingest_files:
            active_id = _active_ingest_files[fhash]
            logger.info(f"[BG-ingest] File already being processed: {filename} (Job {active_id})")
            return active_id

        job_id = str(uuid.uuid4())[:8]
        _active_ingest_files[fhash] = job_id
        _ingest_jobs[job_id] = {
            "status":     "queued",
            "filename":   filename,
            "category":   category,
            "pct":        0,
            "pct_label":  "प्रतीक्षा में…",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "result":     None
        }

    def _set(update: dict):
        with _jobs_lock:
            if job_id in _ingest_jobs:
                _ingest_jobs[job_id].update(update)

    def _run():
        _set({"status": "running", "pct": 5, "pct_label": "फ़ाइल पढ़ी जा रही है…"})
        try:
            fp       = str(filepath)
            fname    = Path(fp).name
            
            # Check Meta DB for duplicate
            conn = sqlite3.connect(META_DB_PATH)
            existing = conn.execute(
                "SELECT id FROM kb_documents WHERE file_hash = ?", (fhash,)
            ).fetchone()
            conn.close()

            if existing and not force_reingest:
                logger.info(f"[BG-ingest] Job {job_id}: '{fname}' already ingested, skipping")
                _set({"status": "done", "pct": 100, "pct_label": "पहले से मौजूद (Skipped)",
                      "result": {"success": True, "skipped": True,
                                 "message": f"'{fname}' is already in the Knowledge Base."}})
                with _jobs_lock:
                    _active_ingest_files.pop(fhash, None)
                return

            # Robust Extraction: Check Cache first
            cache_file = KB_CACHE_DIR / f"{fhash}.txt"
            text = ""
            if cache_file.exists():
                _set({"pct": 10, "pct_label": "कैश से टेक्स्ट लोड हो रहा है…"})
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        text = f.read()
                    logger.debug(f"[BG-ingest] Loaded text from cache for {fname}")
                except Exception as ce:
                    logger.warning(f"[BG-ingest] Cache read error for {fname}: {ce}")

            if not text.strip():
                # Extract text
                _set({"pct": 15, "pct_label": "टेक्स्ट निकाला जा रहा है (इसमें समय लग सकता है)…"})
                text = _extract_text(fp)
                if text.strip():
                    # Save to cache
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            f.write(text)
                        logger.debug(f"[BG-ingest] Cached extracted text for {fname}")
                    except Exception as ce:
                        logger.warning(f"[BG-ingest] Cache write error for {fname}: {ce}")

            if not text.strip():
                _set({"status": "error", "pct": 0, "pct_label": "टेक्स्ट नहीं मिला",
                      "result": {"success": False, "error": "No text extracted"}})
                with _jobs_lock:
                    _active_ingest_files.pop(fhash, None)
                return

            # Chunk
            _set({"pct": 25, "pct_label": "टेक्स्ट के टुकड़े बनाए जा रहे हैं…"})
            chunks = _chunk_text(text)
            if not chunks:
                _set({"status": "error", "pct": 0, "pct_label": "Chunks नहीं बने",
                      "result": {"success": False, "error": "No text chunks found"}})
                return

            total_chunks = len(chunks)

            # Embed & store in batches
            col        = _get_chroma_collection()
            chunk_ids  = [f"{doc_id}_chunk_{i}" for i in range(total_chunks)]
            metadatas  = [{
                "doc_id": doc_id, "filename": fname, "category": category,
                "description": description, "chunk_index": i, "chunk_total": total_chunks
            } for i in range(total_chunks)]

            if force_reingest:
                try:
                    old = col.get(where={"doc_id": doc_id})
                    if old["ids"]:
                        col.delete(ids=old["ids"])
                except Exception:
                    pass

            batch_size = 50
            for batch_start in range(0, total_chunks, batch_size):
                end = min(batch_start + batch_size, total_chunks)
                col.upsert(
                    ids=chunk_ids[batch_start:end],
                    documents=chunks[batch_start:end],
                    metadatas=metadatas[batch_start:end]
                )
                # Map 30% → 90% across all batches
                pct = 30 + int((end / total_chunks) * 60)
                _set({"pct": pct,
                      "pct_label": f"Embeddings: {end}/{total_chunks} chunks"})

            # Save metadata
            _set({"pct": 95, "pct_label": "मेटाडेटा सहेजा जा रहा है…"})
            conn = sqlite3.connect(META_DB_PATH)
            conn.execute("""
                INSERT OR REPLACE INTO kb_documents
                    (id, filename, filepath, category, description, chunk_count, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, fname, fp, category, description, total_chunks, fhash))
            conn.commit()
            conn.close()

            result = {
                "success":     True,
                "doc_id":      doc_id,
                "filename":    fname,
                "category":    category,
                "chunk_count": total_chunks,
                "message":     f"✅ '{fname}' → {total_chunks} chunks ingested."
            }
            _set({"status": "done", "pct": 100,
                  "pct_label": f"✅ {total_chunks} chunks ready", "result": result})
            logger.info(f"[BG-ingest] Job {job_id} done: {fname} ({total_chunks} chunks)")

        except Exception as e:
            _set({"status": "error", "pct": 0, "pct_label": f"Error: {e}",
                  "result": {"success": False, "error": str(e)}})
            logger.error(f"[BG-ingest] Job {job_id} failed: {e}")
        finally:
            with _jobs_lock:
                _active_ingest_files.pop(fhash, None)

    threading.Thread(target=_run, daemon=True, name=f"ingest-{job_id}").start()
    return job_id


def get_ingest_job_status(job_id: str) -> Dict:
    """Return the status of a background ingest job."""
    with _jobs_lock:
        return dict(_ingest_jobs.get(job_id, {"status": "not_found"}))


def get_all_ingest_jobs() -> List[Dict]:
    """Return all ingest job statuses (latest first)."""
    with _jobs_lock:
        return [{"job_id": k, **v} for k, v in reversed(list(_ingest_jobs.items()))]


# ══════════════════════════════════════════════════════════════════════════════
# AUTO FOLDER WATCHER  (polls every 60s — works even when browser is closed)
# ══════════════════════════════════════════════════════════════════════════════

# Watched folder: drop any PDF/DOCX/TXT here and it auto-ingests
WATCH_FOLDER = BOT_ROOT / "auto_ingest"
WATCH_FOLDER.mkdir(parents=True, exist_ok=True)

_SUPPORTED_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}
_watcher_running = False

def _create_category_folders():
    """Ensure subfolders exist for each knowledge base category."""
    for cat in DOC_CATEGORIES:
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', cat).strip()
        cat_dir = WATCH_FOLDER / safe_name
        cat_dir.mkdir(exist_ok=True)
    # create the processed folder as well
    (WATCH_FOLDER / "processed").mkdir(exist_ok=True)

def _folder_watcher_loop(interval_sec: int = 60):
    """
    Background thread: polls WATCH_FOLDER every `interval_sec` seconds.
    Any new file in a category subfolder is auto-ingested with that category.
    Files are moved to WATCH_FOLDER/processed/ after ingestion.
    """
    _create_category_folders()
    processed_dir = WATCH_FOLDER / "processed"
    
    logger.info(f"[FolderWatcher] Monitoring: {WATCH_FOLDER} subdirectories (every {interval_sec}s)")
    import time
    while True:
        try:
            # Check files in all subdirectories matching category names
            for cat in DOC_CATEGORIES:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', cat).strip()
                cat_dir = WATCH_FOLDER / safe_name
                
                if not cat_dir.exists():
                    continue
                    
                new_files = [
                    f for f in cat_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTS
                ]
                
                for fp in new_files:
                    logger.info(f"[FolderWatcher] New file detected in '{cat}': {fp.name}")
                    job_id = ingest_document_async(
                        filepath=str(fp), 
                        category=cat,
                        description="Auto-ingested from Category watch folder"
                    )
                    logger.info(f"[FolderWatcher] Queued as job {job_id}")
                    # Move to processed so it won't be re-ingested
                    dest = processed_dir / fp.name
                    try:
                        fp.rename(dest)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[FolderWatcher] Error: {e}")
        time.sleep(interval_sec)


def start_folder_watcher(interval_sec: int = 60):
    """
    Launch the folder watcher in a daemon thread.
    DISABLED as per user request.
    """
    logger.info("[FolderWatcher] Disabled by configuration.")
    return

