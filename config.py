"""Project paths and environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def get_google_api_key() -> str | None:
    key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not key or key == "your_gemini_api_key_here":
        return None
    return key
