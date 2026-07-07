import os
import sys

# Ensure the root directory is on the python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Helper to read .env file manually if it exists to avoid overwriting real API keys
def load_dotenv_manually():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k not in os.environ:
                        os.environ[k] = v

load_dotenv_manually()

# Set dummy defaults only if they are not defined in the environment or .env
os.environ.setdefault("ODOO_URL", "http://mock-odoo.local")
os.environ.setdefault("ODOO_DB", "mockdb")
os.environ.setdefault("ODOO_USERNAME", "mockuser")
os.environ.setdefault("ODOO_API_KEY", "mockkey")
os.environ.setdefault("LLM_PROVIDER", "mistral")
os.environ.setdefault("MISTRAL_API_KEY", "mock_mistral_key")
os.environ.setdefault("GROQ_API_KEY", "mock_groq_key")
os.environ.setdefault("GEMINI_API_KEY", "mock_gemini_key")
