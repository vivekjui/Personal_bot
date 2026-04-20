import asyncio
import pandas as pd
from modules.fast_parsing import extract_tables_with_docling
from modules.tec_eval import process_evaluations_llm
from modules.agent_browser import fetch_markdown
from modules.utils import logger

async def test_all():
    logger.info("Starting Upgrade Verification...")

    # 1. Test Docling Parsing
    pdf_path = "test_bid.pdf"
    logger.info("--- Testing Docling Parsing ---")
    try:
        tables = extract_tables_with_docling(pdf_path)
        print(f"Docling found {len(tables)} tables.")
        if tables:
            print(f"Largest table shape: {tables[0].shape}")
    except Exception as e:
        print(f"Docling test failed: {e}")

    # 2. Test PydanticAI Evaluation
    logger.info("--- Testing PydanticAI Evaluation ---")
    try:
        # Mock dataframe for evaluation
        df = pd.DataFrame([
            {"Name of the Firm": "Test Firm A", "Technical Detail": "Meets all criteria", "IP Similarity": "No"},
            {"Name of the Firm": "Test Firm B", "Technical Detail": "Missing certificate", "IP Similarity": "No"}
        ])
        results = process_evaluations_llm(df)
        print(f"PydanticAI Results: {results['stats']}")
        for res in results['results']:
            print(f"  - {res['firm_name']}: Qualified={res['is_qualified']}, Reason={res['comment'][:50]}...")
    except Exception as e:
        print(f"PydanticAI test failed: {e}")

    # 3. Test AgentBrowser (Crawl4AI)
    logger.info("--- Testing AgentBrowser (Crawl4AI) ---")
    try:
        url = "https://example.com"
        md = await fetch_markdown(url)
        print(f"Crawl4AI fetched {len(md)} chars of markdown from {url}")
        if md:
            print(f"Snippet: {md[:100]}...")
    except Exception as e:
        print(f"Crawl4AI test failed: {e}")

    logger.info("Verification Complete.")

if __name__ == "__main__":
    asyncio.run(test_all())
