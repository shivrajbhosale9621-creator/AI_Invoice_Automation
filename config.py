"""
Centralized configuration for the Invoice Automation application.
Loads API keys, model names, and default paths from environment variables,
.env files, and Streamlit secrets.

Memory / summarisation settings
--------------------------------
SUMMARISE_THRESHOLD : int
    Total number of messages in a conversation before summarisation is triggered.
    Default = 20. Set env var SUMMARISE_THRESHOLD to override.
RECENT_WINDOW : int
    Number of most-recent messages kept verbatim after summarisation.
    Default = 6. Set env var RECENT_WINDOW to override.
CHAT_DB_PATH : str
    Path to the SQLite database used for chat history and LangGraph checkpoints.
    Defaults to the project's existing invoices.db.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["ANONYMIZED_TELEMETRY"] = "False"
from pathlib import Path
import chromadb


# --- Default ChromaDB / Vector Store Paths ---
DEFAULT_CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
DEFAULT_CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "invoices")
DEFAULT_INDEX_PATH = DEFAULT_CHROMA_PATH
DEFAULT_INBOX_DIR = "./invoices_to_process"


# --- API Keys & Model Names ---
DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_SECRETS_LOCATIONS = [
	Path.home() / ".streamlit" / "secrets.toml",
	Path.cwd() / ".streamlit" / "secrets.toml",
]
HAS_STREAMLIT_SECRETS = any(path.exists() for path in _SECRETS_LOCATIONS)


def _get_secret(key: str, default: str = "") -> str:
	"""Attempt to read a key from Streamlit secrets.toml, falling back to a default."""
	if not HAS_STREAMLIT_SECRETS:
		return default
	try:
		import streamlit as st
		return st.secrets[key]
	except (KeyError, Exception):
		return default


GROQ_API_KEY = os.getenv("GROQ_API_KEY") or _get_secret("GROQ_API_KEY", "")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")

# --- Baidu OCR Settings (API Mode for Local Dev / Downloaded Model for Cloud) ---
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY") or _get_secret("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY") or _get_secret("BAIDU_SECRET_KEY", "")
USE_LOCAL_PADDLEOCR = os.getenv("USE_LOCAL_PADDLEOCR", "false").lower() == "true"

# --- Chinese Unlimited OCR Model Settings (SiliconFlow / DashScope) ---
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY") or _get_secret("SILICONFLOW_API_KEY", "")
SILICONFLOW_OCR_MODEL = os.getenv("SILICONFLOW_OCR_MODEL") or _get_secret("SILICONFLOW_OCR_MODEL", "Qwen/Qwen2-VL-72B-Instruct")
PADDLE_OCR_ENABLED = os.getenv("PADDLE_OCR_ENABLED", "true").lower() == "true"

# Alias used by app.py (vision-capable model for OCR tasks)
GROQ_VISION_MODEL_NAME = os.getenv("GROQ_VISION_MODEL_NAME", GROQ_MODEL_NAME)

# --- SQLite Database Path (shared by chat history + LangGraph checkpointer) ---
CHAT_DB_PATH = os.getenv(
    "CHAT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "invoices.db"),
)

# --- Conversation Memory / Summarisation Settings ---
try:
    SUMMARISE_THRESHOLD = int(os.getenv("SUMMARISE_THRESHOLD", "20"))
except ValueError:
    SUMMARISE_THRESHOLD = 20

try:
    RECENT_WINDOW = int(os.getenv("RECENT_WINDOW", "6"))
except ValueError:
    RECENT_WINDOW = 6
