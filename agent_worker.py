import time
import json
import traceback
from modules.utils import logger, ask_llm_with_review
from modules.database import get_pending_tasks, update_task_status

def process_tec_task(task):
    payload = json.loads(task["payload"])
    file_path = payload.get("file_path")
    case_id = task["target_id"]
    
    from modules.tec_eval import extract_data_from_pdf, process_evaluations_llm
    
    logger.info(f"AgentWorker: Processing TEC for {case_id}")
    df = extract_data_from_pdf(file_path)
    if df.empty:
        return None, "No data extracted from PDF"
        
    results = process_evaluations_llm(df)
    return json.dumps(results), None

def process_noting_task(task):
    payload = json.loads(task["payload"])
    context = payload.get("context")
    case_id = task["target_id"]
    
    from modules.eoffice_noting import generate_noting_text
    
    logger.info(f"AgentWorker: Drafting Noting for {case_id}")
    # Use the new agentic review loop for noting
    noting_text = ask_llm_with_review(
        prompt=f"Draft a formal noting for: {context}",
        context=f"Case ID: {case_id}"
    )
    return noting_text, None

TASK_HANDLERS = {
    "tec": process_tec_task,
    "noting": process_noting_task
}

def run_worker():
    logger.info("AgentWorker: Starting autonomous background loop...")
    while True:
        try:
            tasks = get_pending_tasks(limit=1)
            if not tasks:
                time.sleep(10) # Wait for new tasks
                continue
                
            task = tasks[0]
            task_id = task["id"]
            task_type = task["task_type"]
            
            update_task_status(task_id, "Running")
            
            handler = TASK_HANDLERS.get(task_type)
            if not handler:
                update_task_status(task_id, "Failed", error=f"No handler for task type: {task_type}")
                continue
                
            result, error = handler(task)
            
            if error:
                update_task_status(task_id, "Failed", error=error, retry=(task["retry_count"] < 3))
            else:
                update_task_status(task_id, "Completed", result=result)
                
        except Exception as e:
            logger.error(f"AgentWorker Loop Error: {e}\n{traceback.format_exc()}")
            time.sleep(30)

if __name__ == "__main__":
    run_worker()
