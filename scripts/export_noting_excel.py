import json
import re
import os
import sys
import time
from pathlib import Path
import pandas as pd

sys.path.append(os.getcwd())
from modules.utils import _ask_gemini_direct, logger

def clean_noting_text(text):
    if not text: return ""
    text = re.sub(r'(?i)Note\s*#\s*\d+[.\d]*\s*', '', text)
    patterns = [
        r'\(हस्ताक्षर\)', r'\(नाम\)', r'\(पदनाम\)', r'\(विभाग\)',
        r'\(Signature\)', r'\(Name\)', r'\(Designation\)', r'\(Department\)',
        r'दिनांक\s*:?.*', r'Date\s*:?.*',
        r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}\s+[AP]M',
    ]
    for p in patterns: text = re.sub(p, '', text, flags=re.IGNORECASE)
    text = text.replace("VIVEK KUMAR", "").replace("Store Keeper", "").strip()
    return text

def generate_keywords_mega_batch(text_list):
    if not text_list: return []
    prompt = "Below are multiple official noting snippets. Provide a 1-2 word keyword for each. Response format: JSON list of strings.\n\n"
    for i, t in enumerate(text_list): prompt += f"{i+1}: {t[:200]}\n"
    try:
        # Try gemini-flash-latest
        response = _ask_gemini_direct(prompt, override_model="models/gemini-flash-latest").strip()
        response = re.sub(r'```json|```', '', response).strip()
        keywords = json.loads(response)
        if isinstance(keywords, list):
            while len(keywords) < len(text_list): keywords.append("Misc")
            return keywords[:len(text_list)]
    except Exception: pass
    return ["Misc"] * len(text_list)

def export_to_excel():
    base_file = Path("d:/APMD_eOffice_Bot/Vivek_Kumar_Notings_Categorized.jsonl")
    output_path = "d:/APMD_eOffice_Bot/Vivek_Kumar_Notings_Clean_Table.xlsx"
    raw_data = []
    with open(base_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): raw_data.append(json.loads(line))
    
    items = []
    for x in raw_data:
        c = clean_noting_text(x.get("text", ""))
        if c: items.append({"Stage Label": x.get("category", "General"), "Cleaned Content": c})
    
    batch_size = 50 
    final = []
    print(f"Processing {len(items)} items...")
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        print(f"Batch {i//batch_size + 1}...")
        kw = generate_keywords_mega_batch([b["Cleaned Content"] for b in batch])
        for j, b in enumerate(batch):
            b["Keyword"] = kw[j]
            final.append(b)
        time.sleep(5)
    
    df = pd.DataFrame(final).sort_values(["Stage Label", "Keyword"])
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Notings')
        ws = writer.sheets['Notings']
        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 25
        ws.column_dimensions['C'].width = 100
        from openpyxl.styles import Alignment
        for r in ws.iter_rows(min_row=2):
            for c in r: c.alignment = Alignment(wrap_text=True, vertical='top')
    print("Done")

if __name__ == "__main__":
    export_to_excel()
