# Skill: APMD eOffice Bot Operator

This skill allows Antigravity to operate the **APMD_eOffice_Bot** directly via terminal commands and Python scripts, enabling AI-assisted procurement automation.

## Instructions

### 1. Environment Management
Before running any bot command, ensure you are in the `.venv` virtual environment.
- **Windows**: `.venv\Scripts\activate`

### 2. Common Tasks

#### Ingest New Tenders
Use this when asked to "fetch new emails" or "ingest tender".
```bash
python auto_ingest/email_ingestor.py --auto
```

#### Run Technical Evaluation (TEC)
Use this to evaluate a specific bidder or case.
```bash
python .agent/skills/apmd-bot/scripts/cli_helper.py --task tec --case_id <ID> --file <PATH_TO_PDF>
```

#### Draft Noting in Hindi
Use this to generate a procurement noting.
```bash
python .agent/skills/apmd-bot/scripts/cli_helper.py --task noting --case_id <ID> --output <OUTPUT_DOCX>
```

### 3. Log Monitoring
Monitor the bot's health by checking:
- `logs/bot_activity.log`

### 4. LLM Configuration
The bot uses Gemini and Groq as defined in `config.json`. If a rate limit (HTTP 429) occurs, check the alternate provider's API key.

## Best Practices
- **Hindi Translation**: Always ensure the `target_language` is set to `hindi` for noting tasks.
- **GeM Automation**: If Selenium fails, use the `browser_subagent` to manually inspect the GeM portal page state.
- **Data Integrity**: Always verify that the `cases.db` is updated after an ingestion task.
