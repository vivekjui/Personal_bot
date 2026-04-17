"""
Noting Bot - Text Extraction Module
Handles PDF and image to text/HTML extraction using Vision LLMs.
Also handles Word (.docx) document generation from extracted content.
"""

import os
import io
import base64
import json
import logging
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from bs4 import BeautifulSoup
import pytesseract
import pdfplumber
from google import genai
from google.genai import types
from modules.utils import ask_llm, CONFIG, logger, get_llm_status

# Configure Tesseract path for Windows
TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]
for candidate in TESSERACT_CANDIDATES:
    if os.path.exists(candidate):
        pytesseract.pytesseract.tesseract_cmd = candidate
        logger.info(f"Using Tesseract executable at {candidate}")
        break

def extract_text_from_file(file_path: Path = None, image_bytes: bytes = None, method: str = "standard") -> dict:
    """
    Extracts text from a PDF or Image.
    'standard' method uses local Tesseract (for images) or pdfplumber (for PDFs).
    'vision' method uses Gemini Vision.
    """
    try:
        # 1. Standard Method (Local / Hybrid)
        if method == "standard":
            if file_path and file_path.suffix.lower() == ".pdf":
                logger.info(f"Extracting text from PDF (Standard): {file_path.name}")
                text_content = []
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_content.append(page_text)
                        else:
                            # Scanned page - fallback to OCR
                            logger.info(f"Page {page.page_number} appears scanned. Falling back to OCR.")
                            import fitz
                            doc = fitz.open(file_path)
                            page_fitz = doc.load_page(page.page_number - 1)
                            pix = page_fitz.get_pixmap()
                            img_data = pix.tobytes("png")
                            from PIL import Image
                            img = Image.open(io.BytesIO(img_data))
                            try:
                                text_content.append(pytesseract.image_to_string(img, lang='eng+hin'))
                            except pytesseract.TesseractNotFoundError:
                                return {
                                    "success": False,
                                    "error": "Tesseract is not installed or not found. Install Tesseract OCR and ensure it is on PATH, or set pytesseract.pytesseract.tesseract_cmd to the executable path.",
                                }
                            doc.close()
                
                return {"success": True, "text": "\n\n".join(text_content), "method": "standard_pdf"}

            # Image handling (file or bytes)
            img_data = image_bytes if image_bytes else file_path.read_bytes()
            from PIL import Image
            img = Image.open(io.BytesIO(img_data))
            logger.info("Performing Local Tesseract OCR...")
            
            # Try English + Hindi
            try:
                text = pytesseract.image_to_string(img, lang='eng+hin')
            except pytesseract.TesseractNotFoundError as ex:
                return {
                    "success": False,
                    "error": "Tesseract is not installed or not found. Install Tesseract OCR and ensure it is on PATH, or set pytesseract.pytesseract.tesseract_cmd to the correct executable path.",
                }
            except Exception:
                try:
                    text = pytesseract.image_to_string(img)
                except pytesseract.TesseractNotFoundError as ex:
                    return {
                        "success": False,
                        "error": "Tesseract is not installed or not found. Install Tesseract OCR and ensure it is on PATH, or set pytesseract.pytesseract.tesseract_cmd to the correct executable path.",
                    }

            return {"success": True, "text": text.strip(), "method": "standard_ocr"}

        # 2. Vision Method (LLM)
        else:
            api_key = CONFIG.get("gemini_api_key")
            if not api_key:
                return {"success": False, "error": "Gemini API key not configured."}

            logger.info("Vision Method: Gemini LLM configured.")
            client = genai.Client(api_key=api_key)
            model_name = CONFIG.get("llm", {}).get("gemini_model", "gemini-2.0-flash")
            
            # Use utility normalization
            from modules.utils import _normalize_gemini_model_name
            model_name = _normalize_gemini_model_name(model_name)

            logger.info(f"Using Gemini model: {model_name}")
            file_bytes = image_bytes if image_bytes else file_path.read_bytes()
            # Determine mime type
            mime_type = "image/png"
            if file_path:
                if file_path.suffix.lower() == ".pdf": mime_type = "application/pdf"
                elif file_path.suffix.lower() in [".jpg", ".jpeg"]: mime_type = "image/jpeg"

            prompt = "Transcribe all text from this document into a structured digital format. Preserve the structural layout using Markdown tables if needed. Return ONLY the extracted text content."

            logger.info(f"Extracting text via Vision LLM ({model_name})...")
            content_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            
            # Relax safety settings to prevent false-positive blocks
            safety_settings = [
                types.SafetySetting(category="HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="CIVIC_INTEGRITY", threshold="BLOCK_NONE"),
            ]

            # Using a slightly more robust calling pattern
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, content_part],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        top_p=0.95,
                        safety_settings=safety_settings
                    )
                )

            except Exception as api_err:
                logger.error(f"Gemini API Error: {api_err}")
                return {"success": False, "error": f"Gemini API Error: {str(api_err)}"}

            # Log safety feedback if available
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                logger.warning(f"Gemini Prompt Feedback: {response.prompt_feedback}")

            # Check if we have candidates
            if not response.candidates or len(response.candidates) == 0:
                return {"success": False, "error": "Vision model returned no candidates. This usually happens if the content was blocked by safety filters."}

            candidate = response.candidates[0]
            if candidate.finish_reason and candidate.finish_reason != 'STOP':
                logger.warning(f"Gemini Finish Reason: {candidate.finish_reason}")
                
                # Automatic Fallback for RECITATION or SAFETY
                if candidate.finish_reason in ['SAFETY', 'RECITATION']:
                    reason_map = {
                        'SAFETY': "Safety Filters",
                        'RECITATION': "Recitation Check (Copyright)"
                    }
                    logger.info(f"Vision model blocked by {reason_map.get(candidate.finish_reason)}. Falling back to Standard OCR...")
                    fallback_res = extract_text_from_file(file_path, method="standard", image_bytes=image_bytes)
                    if fallback_res.get("success"):
                        fallback_res["method"] = "standard_ocr_fallback"
                        fallback_res["fallback_reason"] = candidate.finish_reason
                        return fallback_res
                    else:
                        return {"success": False, "error": f"Vision model blocked by {reason_map.get(candidate.finish_reason)} and OCR fallback failed: {fallback_res.get('error')}"}

            if not response.text:
                # Fallback check for parts
                parts_text = ""
                if candidate.content and candidate.content.parts:
                    parts_text = "".join([p.text for p in candidate.content.parts if hasattr(p, 'text') and p.text])
                
                if not parts_text:
                    return {"success": False, "error": "Vision model returned empty text response."}
                content = parts_text.strip()
            else:
                content = response.text.strip()

            # Clean up markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                if len(lines) > 2:
                    # Remove first line if it's ``` or ```html/markdown
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    # Remove last line if it's ```
                    if lines[-1].strip() == "```":
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                else:
                    content = content.replace("```", "").strip()

            return {
                "success": True, 
                "text": content,
                "model_used": model_name
            }

    except Exception as e:
        logger.error(f"Extraction failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}

def generate_docx_from_html(html_content: str, output_stream: io.BytesIO):
    """
    Converts HTML/RTF content from the Quill editor into a formatted .docx file.
    """
    doc = Document()
    
    # Basic Page Setup
    section = doc.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)

    # Parse HTML
    soup = BeautifulSoup(html_content, "html.parser")

    def process_node(node, paragraph=None):
        if node.name is None:  # Text node
            text = node.strip()
            if text and paragraph:
                run = paragraph.add_run(text)
                return
            elif text:
                paragraph = doc.add_paragraph(text)
                return

        if node.name in ['h1', 'h2', 'h3']:
            style = 'Heading 1' if node.name == 'h1' else ('Heading 2' if node.name == 'h2' else 'Heading 3')
            doc.add_heading(node.get_text().strip(), level=int(node.name[1]))
            return

        if node.name == 'p':
            p = doc.add_paragraph()
            for child in node.children:
                if child.name == 'strong' or child.name == 'b':
                    p.add_run(child.get_text()).bold = True
                elif child.name == 'em' or child.name == 'i':
                    p.add_run(child.get_text()).italic = True
                else:
                    p.add_run(child.get_text() if child.name else str(child))
            return

        if node.name in ['ul', 'ol']:
            for li in node.find_all('li', recursive=False):
                p = doc.add_paragraph(li.get_text().strip(), style='List Bullet' if node.name == 'ul' else 'List Number')
            return
        
        # Recursive processing for other tags
        for child in node.children:
            process_node(child, paragraph)

    # Fallback to simple text if parsing is too complex or fails
    if not soup.find():
        doc.add_paragraph(html_content)
    else:
        # Better: iterate top level items
        for element in soup.contents:
            if element.name:
                if element.name == 'p':
                    p = doc.add_paragraph()
                    for r in element.children:
                        if r.name == 'strong' or r.name == 'b': p.add_run(r.get_text()).bold = True
                        elif r.name == 'em' or r.name == 'i': p.add_run(r.get_text()).italic = True
                        elif r.name == 'u': p.add_run(r.get_text()).underline = True
                        elif r.name == 'br': p.add_run("\n")
                        else: p.add_run(r.get_text() if r.name else str(r))
                elif element.name in ['h1', 'h2', 'h3', 'h4']:
                    doc.add_heading(element.get_text().strip(), level=int(element.name[1]))
                elif element.name in ['ul', 'ol']:
                    for li in element.find_all('li'):
                        doc.add_paragraph(li.get_text().strip(), style='List Bullet' if element.name == 'ul' else 'List Number')
                elif element.name == 'li':
                    doc.add_paragraph(element.get_text().strip(), style='List Bullet')
                elif element.name == 'table':
                    rows = element.find_all('tr')
                    if rows:
                        col_count = max([len(r.find_all(['td', 'th'])) for r in rows], default=0)
                        if col_count > 0:
                            table = doc.add_table(rows=len(rows), cols=col_count)
                            table.style = 'Table Grid'
                            for i, row in enumerate(rows):
                                cells = row.find_all(['td', 'th'])
                                for j, cell in enumerate(cells):
                                    if j < col_count:
                                        table.cell(i, j).text = cell.get_text().strip()
                else:
                    doc.add_paragraph(element.get_text().strip())
            elif isinstance(element, str) and element.strip():
                doc.add_paragraph(element.strip())

    doc.save(output_stream)
    return output_stream


def analyze_extracted_content(text: str, context: str) -> str:
    """Uses LLM to perform smart analysis or summarization on extracted text."""
    from modules.utils import ask_llm
    from modules.database import get_prompt_settings
    
    if not context.strip():
        context = "Summarize the document concisely while preserving all key information."

    prompt_settings = get_prompt_settings()
    master_prompt = prompt_settings.get("summarization_master_prompt")
    
    if master_prompt:
        try:
            full_prompt = master_prompt.format(
                user_requirement=context,
                document_text=text
            )
        except Exception as e:
            logger.warning(f"Failed to format summarization prompt: {e}")
            full_prompt = f"{master_prompt}\n\nUSER REQUIREMENT: {context}\n\nTEXT:\n{text}"
    else:
        # Fallback to hardcoded logic if DB fails
        full_prompt = f"""
        TASK: Analyze the following extracted document text.
        USER REQUIREMENT: {context}
        DOCUMENT TEXT:
        {text}
        """
    
    try:
        result = ask_llm(full_prompt)
        return result
    except Exception as e:
        logger.error(f"AI Analysis failed: {e}")
        raise e
