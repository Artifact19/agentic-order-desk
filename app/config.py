import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("AGENT_DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "static"
MODELS_DIR = Path(os.getenv("AGENT_MODELS_DIR", BASE_DIR / "models"))
DATABASE_PATH = DATA_DIR / "agent.db"
MEMORY_DIR = DATA_DIR / "long_term_memory"


def model_source(model_id: str) -> str:
    """Use a copy in ./models/<name> when present (works offline), otherwise the Hugging Face id."""
    local = MODELS_DIR / model_id.split("/")[-1]
    return str(local) if (local / "config.json").exists() else model_id


LLM_MODEL_ID = os.getenv("AGENT_LLM_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
EMBEDDING_MODEL_ID = os.getenv("AGENT_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
USE_LLM = os.getenv("AGENT_USE_LLM", "1") == "1"
USE_EMBEDDINGS = os.getenv("AGENT_USE_EMBEDDINGS", "1") == "1"

HOST = os.getenv("AGENT_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_PORT", "8000"))

# Search results and menus read from Zomato are reused for five minutes, so follow-ups such as "something cheaper"
# do not browse again. "refresh" or a new dish always reads the website again.
CACHE_TTL_SECONDS = {"search": 300, "menu": 300, "embedding": 3600, "parse": 1800, "session": 1800}
CACHE_MAX_ENTRIES = {"search": 64, "menu": 512, "embedding": 1024, "parse": 512, "session": 1000}

CONTEXT_WINDOW_TURNS = 6
MAX_MESSAGE_LENGTH = 500

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.5

RECALL_TOP_K = 5
RECALL_MIN_SIMILARITY = 0.30
MEMORY_WEIGHTS = {"approved_item": 6.0, "approved_restaurant": 2.0, "rejected_item": -8.0}
MEMORY_ADJUSTMENT_RANGE = (-15.0, 10.0)

# Used only when neither the message, the conversation nor past orders say otherwise. There is no default dish.
DEFAULT_REQUEST = {"is_veg": None, "max_price": 500.0, "max_eta_minutes": 45}
DEFAULT_ADDRESS = "Delivery address chosen on Zomato"

AGENT_ID = "ordering_agent"

# Empty: learned from the delivery address on the signed-in Zomato account.
ZOMATO_CITY = os.getenv("AGENT_ZOMATO_CITY", "")
MAX_RESTAURANTS = int(os.getenv("AGENT_RESTAURANTS", "5"))
MAX_DISHES_PER_RESTAURANT = 4
CHROME_DEBUG_PORT = int(os.getenv("AGENT_CHROME_PORT", "9333"))
CHROME_PROFILE_DIR = DATA_DIR / "chrome_profile"
SCREENS_DIR = DATA_DIR / "screens"
# Before the first search, wait for the user to sign in to Zomato and pick a delivery address in the agent's window.
REQUIRE_ZOMATO_LOGIN = os.getenv("AGENT_REQUIRE_ZOMATO_LOGIN", "1") == "1"
REQUIRE_ZOMATO_ADDRESS = os.getenv("AGENT_REQUIRE_ZOMATO_ADDRESS", "1") == "1"
ZOMATO_WAIT_SECONDS = int(os.getenv("AGENT_ZOMATO_WAIT_SECONDS", "600"))
