from google import genai
import os
import json
from pathlib import Path
import sys

# Load config for API key
SCRIPTS_DIR = Path(__file__).parent
BOT_ROOT = SCRIPTS_DIR.parent

# Handle APPDATA or local config
def get_config():
    config_path = BOT_ROOT / "config.json"
    if not config_path.exists():
        # Try APPDATA
        if os.name == 'nt':
            data_root = Path(os.environ.get('APPDATA', str(Path.home()))) / "APMD_Bot"
        else:
            data_root = Path.home() / ".apmd_bot"
        config_path = data_root / "config.json"
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

config = get_config()
api_key = config.get("gemini_api_key", "")

if api_key:
    try:
        client = genai.Client(api_key=api_key)
        print("Fetching available models via google-genai SDK...")
        
        # In the new SDK, we use models.list()
        models = client.models.list()
        
        print("\nAvailable Models:")
        for m in models:
            # Filter for generation models
            if 'generateContent' in m.supported_generation_methods:
                print(f"- {m.name} (ID: {m.name.split('/')[-1]})")
                
    except Exception as e:
        print(f"Error connecting to Gemini API: {e}")
else:
    print("No Gemini API key found in config.json.")
