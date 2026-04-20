import os
import re

path = 'd:/APMD_eOffice_Bot/dashboard.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r'def api_extract_text\(\):.*?finally:.*?if temp_path and temp_path\.exists\(\): temp_path\.unlink\(\)'

new_text = """def api_extract_text():
    method = request.form.get("method", "standard")
    image_base64 = request.form.get("image_base64")
    
    if "file" not in request.files and not image_base64:
        return jsonify({"success": False, "error": "No file or image data provided"}), 400
    
    job_id = str(uuid.uuid4())
    with _extraction_lock:
        _extraction_jobs[job_id] = {"status": "running", "result": None, "error": None}

    def _run_extraction(jid, file_data=None, img_data=None, mthd="standard", fname=None):
        try:
            from modules.extract import extract_text_from_file
            if file_data:
                temp_path = DATA_ROOT / "temp" / (fname or "upload.pdf")
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_path, "wb") as f:
                    f.write(file_data)
                res = extract_text_from_file(file_path=temp_path, method=mthd)
                if temp_path.exists(): temp_path.unlink()
            else:
                res = extract_text_from_file(image_bytes=img_data, method=mthd)
            
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "complete"
                _extraction_jobs[jid]["result"] = res
        except Exception as e:
            logger.error(f"Async Extraction Error: {e}")
            with _extraction_lock:
                _extraction_jobs[jid]["status"] = "failed"
                _extraction_jobs[jid]["error"] = str(e)

    if "file" in request.files:
        f = request.files["file"]
        threading.Thread(target=_run_extraction, args=(job_id, f.read(), None, method, f.filename)).start()
    else:
        if image_base64 and "base64," in image_base64:
            image_base64 = image_base64.split("base64,")[1]
        img_bytes = base64.b64decode(image_base64)
        threading.Thread(target=_run_extraction, args=(job_id, None, img_bytes, method)).start()

    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/extract/status/<job_id>", methods=["GET"])
def api_extract_status(job_id):
    with _extraction_lock:
        if job_id not in _extraction_jobs:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(_extraction_jobs[job_id])"""

new_content, count = re.subn(pattern, new_text, content, flags=re.DOTALL)

if count > 0:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Successfully updated dashboard.py ({count} matches)")
else:
    print("Could not find api_extract_text using regex")
    # Debug: show what it looks like
    match = re.search(r'def api_extract_text\(\):', content)
    if match:
        start = match.start()
        print(f"Found def at {start}. Excerpt: {content[start:start+100]!r}")
