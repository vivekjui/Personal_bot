"""
Noting Bot - Zip & PDF Document Processor
Handles extraction of GeM bid zips, merging PDFs, and splitting by size.
"""

import os
import zipfile
import shutil
import uuid
import fitz  # PyMuPDF
from pathlib import Path
from modules.utils import logger, sanitize_filename

MAX_SIZE_BYTES = 49.9 * 1024 * 1024  # 49.9 MB
TARGET_MIN_SIZE = 49.5 * 1024 * 1024  # 49.5 MB

def process_zip_bid(zip_path: Path, output_dir: Path) -> list:
    """
    Extracts a zip, merges all PDFs inside, and ensures output is < 19.9MB.
    Returns a list of generated file paths.
    """
    zip_name = sanitize_filename(zip_path.stem)
    temp_extract_dir = output_dir / f"temp_extract_{uuid.uuid4().hex[:8]}"
    temp_extract_dir.mkdir(parents=True, exist_ok=True)
    
    generated_files = []
    master_doc = None
    temp_output_pdf = None
    
    try:
        # 1. Extract Zip
        logger.info(f"Extracting {zip_path.name} to {temp_extract_dir}")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_dir)
        except Exception as e:
            logger.error(f"Failed to extract {zip_path.name}: {e}")
            return []
        
        # 2. Collect all PDFs (recursive)
        pdf_files = sorted(list(temp_extract_dir.rglob("*.pdf")))
        if not pdf_files:
            logger.warning(f"No PDFs found in {zip_path}")
            return []
        
        logger.info(f"Found {len(pdf_files)} PDFs in {zip_path.name}")
        
        # 3. Merge PDFs
        master_doc = fitz.open()
        for pdf in pdf_files:
            try:
                with fitz.open(pdf) as doc:
                    master_doc.insert_pdf(doc)
            except Exception as e:
                logger.error(f"Error reading {pdf}: {e}")
        
        if len(master_doc) == 0:
            logger.warning(f"No valid PDF pages found in {zip_path.name}")
            return []

        # 4. Save and Check Size
        base_output_name = f"{zip_name} Technical bid"
        temp_output_pdf = output_dir / f"{base_output_name}_full_temp.pdf"
        
        # Ensure target name is clean
        if temp_output_pdf.exists():
            try: temp_output_pdf.unlink()
            except: pass

        master_doc.save(temp_output_pdf, garbage=3, deflate=True)
        master_doc.close()
        master_doc = None # Mark as closed
        
        file_size = temp_output_pdf.stat().st_size
        logger.info(f"Merged PDF size: {file_size/1024/1024:.2f}MB")
        
        if file_size <= MAX_SIZE_BYTES:
            # Rename to final name
            final_path = output_dir / f"{base_output_name}.pdf"
            if final_path.exists():
                try: final_path.unlink()
                except Exception as e:
                    logger.error(f"Could not delete existing file {final_path.name}: {e}")
                    # Try a fallback name
                    final_path = output_dir / f"{base_output_name}_{uuid.uuid4().hex[:4]}.pdf"
            
            try:
                temp_output_pdf.rename(final_path)
                generated_files.append(final_path.name)
                logger.info(f"Success: Created {final_path.name}")
            except Exception as e:
                logger.error(f"Could not rename temp file to {final_path.name}: {e}")
                generated_files.append(temp_output_pdf.name)
        else:
            # Over 19.9MB, need to split
            logger.info(f"File is {file_size/1024/1024:.2f}MB, splitting...")
            generated_files = split_pdf_by_size(temp_output_pdf, output_dir, base_output_name)
            try:
                if temp_output_pdf.exists(): temp_output_pdf.unlink()
            except: pass
            
    except Exception as e:
        logger.error(f"Critical error in process_zip_bid for {zip_path.name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        if master_doc:
            try: master_doc.close()
            except: pass
        # Cleanup temp extraction
        if temp_extract_dir.exists():
            try: shutil.rmtree(temp_extract_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup {temp_extract_dir}: {e}")
            
    return generated_files

def process_zip_bid_multi(zip_paths: list, output_dir: Path) -> dict:
    """
    Wrapper for process_zip_bid that handles multiple files and consolidates results.
    """
    results = {
        "processed_count": 0,
        "failed_count": 0,
        "generated_files": [],
        "errors": []
    }
    
    for zp in zip_paths:
        try:
            files = process_zip_bid(Path(zp), output_dir)
            if files:
                results["processed_count"] += 1
                results["generated_files"].extend(files)
            else:
                results["failed_count"] += 1
                results["errors"].append(f"No PDFs found in {os.path.basename(zp)}")
        except Exception as e:
            results["failed_count"] += 1
            results["errors"].append(f"Error processing {os.path.basename(zp)}: {str(e)}")
            
    return results

def split_pdf_by_size(input_pdf: Path, output_dir: Path, base_name: str, pages_per_part: int = None) -> list:
    """
    Splits a PDF into multiple parts.
    Ensures each part is < 19.9MB, using compression if necessary.
    """
    doc = None
    try:
        doc = fitz.open(input_pdf)
        total_pages = len(doc)
        parts = []
        
        part_num = 1
        current_start = 0
        
        # Calculate initial estimate (conservative)
        file_size = input_pdf.stat().st_size
        avg_page_size = file_size / total_pages if total_pages > 0 else 0
        
        while current_start < total_pages:
            if pages_per_part:
                pages_to_add = min(pages_per_part, total_pages - current_start)
            else:
                # Safer heuristic: 80% of capacity to account for overhead
                pages_to_add = int((MAX_SIZE_BYTES * 0.8) / max(avg_page_size, 1)) or 1
                pages_to_add = min(pages_to_add, total_pages - current_start)

            end_page = current_start + pages_to_add
            
            final_part_filename = f"{base_name} part {part_num}.pdf"
            final_part_path = output_dir / final_part_filename
            
            # Ensure path is clean
            if final_part_path.exists(): 
                try: final_part_path.unlink()
                except: pass
            
            part_doc = fitz.open()
            try:
                part_doc.insert_pdf(doc, from_page=current_start, to_page=end_page - 1)
                # First pass: standard optimization
                part_doc.save(final_part_path, garbage=3, deflate=True)
                
                # Check if still exceeds limit
                actual_size = final_part_path.stat().st_size
                if actual_size > MAX_SIZE_BYTES:
                    if pages_to_add > 1:
                        # Case A: Too many pages. Reduce page count for this part and retry.
                        logger.info(f"Part {part_num} is {actual_size/1024/1024:.2f}MB, reducing page count...")
                        pages_to_add = max(1, int(pages_to_add * (MAX_SIZE_BYTES / actual_size) * 0.9))
                        continue # Re-run loop with smaller pages_to_add
                    else:
                        # Case B: Single page is too big. Compress it.
                        logger.info(f"Single page part {part_num} is {actual_size/1024/1024:.2f}MB, applying compression...")
                        final_part_path = compress_pdf(final_part_path, final_part_path, mode="maximum")
                
                parts.append(final_part_filename)
                current_start += pages_to_add
                part_num += 1
            finally:
                if part_doc: part_doc.close()

        return parts
    except Exception as e:
        logger.error(f"Error in split_pdf_by_size: {e}")
        return []
    finally:
        if doc: doc.close()

def merge_pdfs(input_paths: list, output_path: Path):
    """
    Merges multiple PDFs into one.
    """
    result = fitz.open()
    for pdf_path in input_paths:
        with fitz.open(pdf_path) as m_doc:
            result.insert_pdf(m_doc)
    
    result.save(str(output_path))
    result.close()
    return output_path

def compress_pdf(input_path: Path, output_path: Path, mode: str = "medium"):
    """
    Compresses a PDF file.
    Modes: heavy, medium, maximum, super
    """
    # Use a safer temp path that doesn't conflict
    temp_path = output_path.parent / f"tmp_comp_{uuid.uuid4().hex[:8]}.pdf"
    
    try:
        if not input_path.exists():
            logger.error(f"Compression failed: Input file not found: {input_path}")
            return input_path

        logger.info(f"Compressing {input_path.name} (mode={mode})")
        doc = fitz.open(input_path)
        
        # Basic optimization settings
        save_args = {"garbage": 3, "deflate": True}
        
        if mode == "heavy":
            save_args["garbage"] = 4
            save_args["clean"] = True
        
        if mode in ["super", "maximum"]:
            save_args.update({
                "garbage": 4,
                "clean": True,
                "compress_images": True,
                "expand": 0,
                "linear": True
            })

        # Save to temp path first
        doc.save(str(temp_path), **save_args)
        doc.close()
        
        # If mode is maximum, do a second pass for extra optimization
        if mode == "maximum":
            temp_path2 = output_path.parent / f"tmp_comp_v2_{uuid.uuid4().hex[:8]}.pdf"
            try:
                doc2 = fitz.open(temp_path)
                doc2.save(str(temp_path2), garbage=4, deflate=True, clean=True)
                doc2.close()
                if temp_path.exists(): temp_path.unlink()
                temp_path = temp_path2
            except Exception as e2:
                logger.warning(f"Second pass of maximum compression failed, using first pass: {e2}")

        # Final move to output_path
        if temp_path.exists():
            try:
                if output_path.exists() and output_path.resolve() != input_path.resolve():
                    output_path.unlink()
                
                # Ensure output directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Move/Rename
                shutil.move(str(temp_path), str(output_path))
                logger.info(f"Compression complete: {output_path.name}")
                return output_path
            except Exception as e:
                logger.error(f"Failed to finalize compressed PDF (rename/move): {e}")
                # If rename fails, return the temp path so the caller still has a file
                return temp_path
        else:
            logger.error("Compression failed: Temp file was not created.")
            return input_path

    except Exception as e:
        logger.error(f"Compression error for {input_path.name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        if temp_path.exists():
            try: temp_path.unlink()
            except: pass
        return input_path
    finally:
        pass
