import os
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(dotenv_path=dotenv_path, override=True)

# Odoo Connection
ODOO_URL = os.getenv('ODOO_URL', '')
ODOO_DB = os.getenv('ODOO_DB', '')
ODOO_USERNAME = os.getenv('ODOO_USERNAME', '')
ODOO_API_KEY = os.getenv('ODOO_API_KEY', '')

# LLM Provider & Keys
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'mistral').strip().lower()
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY', '')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GROQ_API_KEY = os.getenv('GROQ_API_KEY') or os.getenv('groq_api_key', '')

# Win-Back Campaign Parameters (Overridden dynamically from Odoo)
INACTIVITY_THRESHOLD_DAYS = None
WINBACK_INTERVAL_DAYS = None
WINBACK_OFFER_EMAIL2 = None
MAX_WINBACK_EMAILS = None
FINAL_WAIT_DAYS = None
SEGMENT_BY_CATEGORY = None
AUTO_REPLY = None
RECIPIENT_OVERRIDE = None


# Execution Mode
AUTO_APPROVE = False
GMAIL_SMTP_USER = os.getenv('GMAIL_SMTP_USER', '')
GMAIL_SMTP_APP_PASSWORD = os.getenv('GMAIL_SMTP_APP_PASSWORD', '')
TEST_EMAIL_TO = os.getenv('TEST_EMAIL_TO', 'jatoimasab@gmail.com')
ODOO_SOCKET_TIMEOUT = int(os.getenv('ODOO_SOCKET_TIMEOUT', 90))

# Database path
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'win_back_agent.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Runtime processing limit helper (loads from env to avoid runtime module mutation)
def get_limit():
    """Retrieve the runtime processing limit dynamic value."""
    val = os.getenv('WINBACK_LIMIT', None)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return None


def validate():
    """Validates that all required environment variables are set."""
    missing = []
    required = [
        ("ODOO_URL", ODOO_URL),
        ("ODOO_DB", ODOO_DB),
        ("ODOO_USERNAME", ODOO_USERNAME),
        ("ODOO_API_KEY", ODOO_API_KEY),
    ]
    supported_providers = {'mistral', 'google', 'gemini', 'groq'}
    if LLM_PROVIDER not in supported_providers:
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{LLM_PROVIDER}'. "
            f"Supported providers are: {', '.join(sorted(supported_providers))}"
        )

    if LLM_PROVIDER == 'mistral':
        required.append(("MISTRAL_API_KEY", MISTRAL_API_KEY))
    elif LLM_PROVIDER in ['google', 'gemini']:
        required.append(("GEMINI_API_KEY", GEMINI_API_KEY))
    elif LLM_PROVIDER == 'groq':
        required.append(("groq_api_key", GROQ_API_KEY))
        
    for name, val in required:
        if not val:
            missing.append(name)
            
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables for Win-Back Agent: {', '.join(missing)}\n"
            "Please check your .env file."
        )
