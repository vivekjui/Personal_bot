# Personal Bot 

This repository contains a versatile desktop/web automation assistant built in
Python with a Flask dashboard. Originally created for e‑office "notings",
it has since grown into a multi‑feature personal bot with modules for:

- **E‑Office Note Drafting** – generate, refine and store official notings
- **Knowledge Base (RAG)** – ingest documents and answer questions via ChromaDB
- **Document Processing** – compress and merge PDFs, process GE‑M bid ZIPs

Additional utilities include database history, category management, and
LLM integration for drafting/refinement using `google-generativeai`.

## Latest Updates

- Sidebar width and active-button styling fixed to avoid layout shifts.
- Rich noting editors now preserve tables in the editor, history, and copy flow.
- Repository update checks now pull the latest snapshot from `vivekjui/personal_bot`.

## Requirements

```text
# Python packages (see `requirements.txt` for pinned minimums)
flask>=3.0.0
flask-cors>=4.0.0
google-generativeai>=0.8.0
python-docx>=1.1.0
pdfplumber>=0.11.0
PyMuPDF>=1.24.0
openpyxl>=3.1.0
pandas>=2.2.0
requests>=2.31.0
selenium>=4.18.0
webdriver-manager>=4.0.0
schedule>=1.2.0
pywin32>=306
beautifulsoup4>=4.12.0
python-dateutil>=2.9.0
tabulate>=0.9.0
tqdm>=4.66.0
chromadb>=0.5.0
pywebview>=5.1.0
waitress>=3.0.0
```

Installation is typically done via:

```bash
python -m venv .venv
source .venv/bin/activate    # or .\.venv\Scripts\activate.ps1 on Windows
pip install -r requirements.txt
```

## Getting Started

1. Copy `config.example.json` to `config.json` and configure your local API keys and proxies.
2. Run `python dashboard.py` to start the Flask server.
3. Open `http://localhost:5000` in your browser or use the `start.bat` script.

## GitHub Repository

This project can be published under a new repository:

```bash
git init
git add .
git commit -m "Initial commit for personal_bot"
# replace <token> or configure SSH as needed
git remote add origin https://github.com/VivekJui/personal_bot.git
git push -u origin main
```

Be sure to set up your GitHub credentials (PAT/SSH) before pushing.

---

*Last updated: March 11, 2026*
