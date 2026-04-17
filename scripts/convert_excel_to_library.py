import pandas as pd
import json
import os
from pathlib import Path

# Official stages requested by user
OFFICIAL_STAGES = [
    "Indent received",
    "custom bid approval",
    "bid preparation",
    "bid vetting",
    "bid publication approval",
    "bid publication",
    "bid end date extension",
    "Technical bid opening",
    "TEC (T) evaluation",
    "Representation",
    "Review TEC",
    "Price bid opening",
    "Budget confirmation",
    "Contract award",
    "DP (Delivery period) Extension",
    "Performance security",
    "Bill"
]

# Mapping from Excel 'Stage Label' to OFFICIAL_STAGES
MAPPING = {
    'Indent / Requirement Receiving': 'Indent received',
    'Pre-Award Phase / Approvals': 'custom bid approval',
    'Draft Bid Preparation': 'bid preparation',
    'Bid Publication & Management': 'bid vetting',
    'Bid publish': 'bid publication',
    'Bid end date / opening': 'Technical bid opening',
    'Technical Evaluation (TEC)': 'TEC (T) evaluation',
    'Representation Evaluation': 'Representation',
    'Financial Evaluation (TEC-F)': 'Price bid opening',
    'Award of Contract': 'Contract award',
    'DP Extension': 'DP (Delivery period) Extension',
    'PS Cofnrimation': 'Performance security',
    'Bill sent': 'Bill',
    'General / Administrative / Payment': 'Budget confirmation',
    'Bid': 'bid preparation',
    'Pre-bid meeting': 'bid vetting',
    'Post-Award Contract Management': 'Contract award'
}

def convert():
    excel_path = "d:/APMD_eOffice_Bot/Vivek_Kumar_Notings_Clean_Table.xlsx"
    output_path = "d:/APMD_eOffice_Bot/standard_library.json"
    
    if not os.path.exists(excel_path):
        print("Excel not found.")
        return

    df = pd.read_excel(excel_path)
    
    library = []
    for i, row in df.iterrows():
        original_label = str(row['Stage Label'])
        mapped_label = MAPPING.get(original_label, "General")
        
        # Fine-tune mapping with keywords if needed
        content = str(row['Cleaned Content']).lower()
        if mapped_label == "Technical bid opening" and "extension" in content:
            mapped_label = "bid end date extension"
        if mapped_label == "bid vetting" and "approved" in content:
            mapped_label = "bid publication approval"
        if mapped_label == "Representation" and ("review" in content or "technical evaluation committee" in content):
             mapped_label = "Review TEC"

        library.append({
            "id": i + 1,
            "stage": mapped_label,
            "keyword": str(row['Keyword']),
            "text": str(row['Cleaned Content'])
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)
    
    print(f"Converted {len(library)} notings to {output_path}")

if __name__ == "__main__":
    convert()
