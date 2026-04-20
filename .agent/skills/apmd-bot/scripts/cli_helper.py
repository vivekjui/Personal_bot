import sys
import argparse
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from modules.utils import CONFIG, logger, ask_gemini
from modules.database import get_connection

def run_tec_eval(case_id, file_path):
    print(f"Running TEC Evaluation for Case: {case_id} on File: {file_path}")
    from modules.tec_eval import extract_data_from_pdf, process_evaluations_llm
    
    df = extract_data_from_pdf(file_path)
    if df.empty:
        print("Error: No data extracted from PDF.")
        return
    
    # Load criteria from DB or default
    criteria = {} # This would normally come from the DB based on case_id
    
    results = process_evaluations_llm(df, criteria=criteria)
    print("Evaluation Results:")
    for res in results["results"]:
        status = "PASS" if res["is_qualified"] else "FAIL"
        print(f"- {res['firm_name']}: {status} ({res['comment'][:50]}...)")

def draft_noting(case_id, output_path):
    print(f"Drafting Noting for Case: {case_id}...")
    from modules.eoffice_noting import generate_noting_text
    
    # Placeholder for actual data fetching from cases.db
    context = "Tender for purchase of IT equipment"
    noting_text = generate_noting_text(additional_context=context)
    
    print(f"Noting drafted (preview): {noting_text[:100]}...")
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(noting_text)
        print(f"Saved to {output_path}")

def main():
    parser = argparse.ArgumentParser(description="APMD Bot CLI Helper")
    parser.add_argument("--task", choices=["tec", "noting"], required=True)
    parser.add_argument("--case_id", required=True)
    parser.add_argument("--file", help="Path to input file (for TEC)")
    parser.add_argument("--output", help="Path to output file (for Noting)")
    
    args = parser.parse_args()
    
    if args.task == "tec":
        if not args.file:
            print("Error: --file is required for TEC task.")
            return
        run_tec_eval(args.case_id, args.file)
    elif args.task == "noting":
        draft_noting(args.case_id, args.output)

if __name__ == "__main__":
    main()
