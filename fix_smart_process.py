import os
import re

path = 'd:/APMD_eOffice_Bot/dashboard.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r'@app\.route\("/api/extract/smart-process", methods=\["POST"\]\)\ndef api_extract_smart_process\(\):.*?return jsonify\({"success": True, "processed_text": result}\).*?except Exception as e:.*?return jsonify\({"success": False, "error": str\(e\)}\), 500'

new_text = """@app.route("/api/extract/smart-process", methods=["POST"])
def api_extract_smart_process():
    \"\"\"Handles AI-powered analysis/summarization of extracted text with caching.\"\"\"
    try:
        from modules.extract import analyze_extracted_content
        data = request.json or {}
        text = data.get("text", "")
        context = data.get("context", "")
        file_hash = data.get("file_hash") # Added support for file_hash
        
        if not text.strip():
            return jsonify({"success": False, "error": "No text provided for analysis"}), 400
            
        result = analyze_extracted_content(text, context, file_hash=file_hash)
        return jsonify({"success": True, "processed_text": result})
    except Exception as e:
        logger.error(f"Smart Process Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500"""

new_content, count = re.subn(pattern, new_text, content, flags=re.DOTALL)

if count > 0:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Successfully updated smart-process in dashboard.py ({count} matches)")
else:
    print("Could not find api_extract_smart_process using regex")
