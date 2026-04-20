import json
import time
import threading
from typing import Dict, Any, Optional
from datetime import datetime
from modules.utils import logger, get_automation_driver, switch_to_matching_page, list_visible_elements
from modules.agent_browser import run_async_fetch

# Global storage for monitoring jobs
MONITOR_JOBS = {}
MONITOR_STOP_SIGNALS = set()

def emit_event(event: str, data: dict) -> str:
    """Helper formatting function for SSE."""
    data["type"] = event
    return f"data: {json.dumps(data)}\n\n"

class GeMMonitor:
    def __init__(self, bid_id: str, gem_url: str = "", interval: int = 300):
        self.bid_id = bid_id
        self.gem_url = gem_url
        self.interval = interval
        self.job_id = f"monitor_{bid_id}_{int(time.time())}"
        self.is_running = False
        self.last_status = None
        self.last_check_time = None
        self.history = []

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        MONITOR_JOBS[self.job_id] = self
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        thread.start()
        return self.job_id

    def stop(self):
        self.is_running = False
        MONITOR_STOP_SIGNALS.add(self.job_id)

    def _monitor_loop(self):
        logger.info(f"Starting monitor for Bid: {self.bid_id}")
        while self.is_running and self.job_id not in MONITOR_STOP_SIGNALS:
            try:
                self._check_status()
            except Exception as e:
                logger.error(f"Error in monitor loop for {self.bid_id}: {e}")
            
            # Wait for interval
            for _ in range(self.interval):
                if not self.is_running or self.job_id in MONITOR_STOP_SIGNALS:
                    break
                time.sleep(1)
        
        logger.info(f"Stopped monitor for Bid: {self.bid_id}")
        if self.job_id in MONITOR_JOBS:
            del MONITOR_JOBS[self.job_id]

    def _check_status(self):
        self.last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Checking status for Bid: {self.bid_id} at {self.last_check_time} using Crawl4AI")
        
        try:
            # Use Crawl4AI to fetch a clean markdown summary of the GeM page
            # If no specific URL is provided, we might need a search step, but for now 
            # we optimize the existing URL monitoring.
            if self.gem_url:
                page_markdown = run_async_fetch(self.gem_url)
                
                if not page_markdown:
                    logger.warning(f"Crawl4AI returned no content for {self.gem_url}")
                    return

                # Detect status using clean markdown (more reliable than raw HTML)
                status = "Unknown"
                if "Technical Evaluation" in page_markdown:
                    if any(k in page_markdown for k in ["Completed", "Finalized", "Published"]):
                        status = "TEC Finalized"
                    elif "In Progress" in page_markdown:
                        status = "TEC In Progress"
                    else:
                        status = "TEC Active"
                
                # Check for changes
                if status != self.last_status:
                    self.last_status = status
                    self.history.append({
                        "time": self.last_check_time,
                        "status": status,
                        "event": "Status Changed"
                    })
                    logger.info(f"Status changed for {self.bid_id}: {status}")
            else:
                logger.info("No GeM URL provided for direct monitoring.")
            
        except Exception as e:
            logger.warning(f"Crawl4AI Monitor check failed: {e}")

def get_monitor_summary(job_id: str):
    monitor = MONITOR_JOBS.get(job_id)
    if not monitor:
        return None
    return {
        "bid_id": monitor.bid_id,
        "status": monitor.last_status,
        "last_check": monitor.last_check_time,
        "history": monitor.history
    }
