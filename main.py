"""
Noting Bot - Main Orchestrator
Entry point: starts the web dashboard and standalone app window.
"""

import os
# Disable ChromaDB anonymous telemetry to avoid PostHog connection errors
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import sys
import threading
import time
import atexit
from pathlib import Path
import waitress

# --- Environment Check ---
# Ensure we are running in the .venv/Python 3.12 to avoid 3.14 breaking pydantic/chromadb
if "venv" not in sys.prefix.lower() and not sys.prefix.endswith(".venv"):
    print("=" * 60)
    print("[WARNING] You are running this bot outside of the virtual environment.")
    print(f"Current Python: {sys.version.split()[0]}")
    print("Please use 'start.bat' for a stable experience.")
    print("=" * 60)
    print()

if sys.version_info >= (3, 13):
    print("[CRITICAL] Python 3.14 detected. Some libraries like ChromaDB/Pydantic v1 may crash.")
    print("Please use Python 3.12 if possible.")
# Python 3.14 breaks pydantic v1 typing introspection on Optional/Union.
# This prevents ChromaDB from crashing on import.
try:
    import pydantic.v1.fields
    from typing import Any
    _old_set = pydantic.v1.fields.ModelField._set_default_and_type
    def _new_set(self):
        try:
            _old_set(self)
        except pydantic.v1.errors.ConfigError:
            self.type_ = Any
            self.outer_type_ = Any
    pydantic.v1.fields.ModelField._set_default_and_type = _new_set
except Exception:
    pass
# --------------------------------------------------------

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from modules.utils import CONFIG, logger, find_free_port

def cleanup_resources():
    """Release memory and shut down background processes on exit."""
    print("\nShutting down Noting Bot / eOffice Assistant... Releasing memory and closing ports.")
    try:
        logger.info("Application shutting down, releasing resources.")
        # Force garbage collection to free any lingering models/dataframes
        import gc
        gc.collect()
    except Exception:
        pass

# Register the cleanup function to run right before the script dies
atexit.register(cleanup_resources)


def start_dashboard():
    """Start the Flask dashboard server."""
    from dashboard import app
    cfg = CONFIG["dashboard"]
    logger.info(f"Starting Noting Bot Dashboard via Waitress at http://{cfg['host']}:{cfg['port']}")
    waitress.serve(app, host=cfg["host"], port=cfg["port"], threads=10, _quiet=True)


def main():
    print("=" * 60)
    print("  Smart bot by Vivek Jui — Procurement Management Assistant")
    print("=" * 60)
    print()

    # Prevent multiple instances by checking if the configured port is already bound.
    cfg = CONFIG["dashboard"]
    target_port = cfg["port"]
    
    # Check if port is available
    final_port = find_free_port(target_port)
    
    if final_port != target_port:
        # If we had to shift, double check if it was specifically port 5006
        if final_port == 5006:
            final_port = find_free_port(5007)
        
        print(f"Note: Port {target_port} was busy. Shifting to {final_port}...")
        logger.info(f"Port shift: {target_port} -> {final_port}")
        # Update config in memory for this session
        CONFIG["dashboard"]["port"] = final_port

    # Start dashboard in a background thread
    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()

    # Launch Standalone App Window (pywebview)
    import webview
    
    # Wait longer for Flask to bind (especially on slower systems or first run)
    time.sleep(3.0)
    
    url = f"http://127.0.0.1:{CONFIG['dashboard']['port']}"
    logger.info(f"Opening standalone window at {url}")
    
    # Create the window
    window = webview.create_window(
        'Smart bot by Vivek Jui', 
        url, 
        width=1280, 
        height=850,
        min_size=(1000, 700)
    )
    
    # Start the webview loop (blocks the main thread)
    webview.start()


if __name__ == "__main__":
    main()
