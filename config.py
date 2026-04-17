"""
config.py — Loads MediaWiki connection settings from environment variables.

We use python-dotenv so developers can put credentials in a .env file
rather than hardcoding them or setting system env vars manually.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env relative to this file, not the cwd — the MCP server may be launched
# from an unrelated working directory by the Claude Code harness.
load_dotenv(Path(__file__).parent / ".env")

def get_config() -> dict:
    """
    Returns a dict of required config values.
    Raises a clear error if anything is missing, so the server fails fast
    at startup rather than mysteriously at runtime.
    """
    required = {
        "WIKI_URL":         os.getenv("WIKI_URL"),
        "WIKI_USERNAME":    os.getenv("WIKI_USERNAME"),
        "WIKI_PASSWORD":    os.getenv("WIKI_PASSWORD"),
        "WIKI_TOTP_SECRET": os.getenv("WIKI_TOTP_SECRET"),
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Create a .env file in the project root with these values."
        )

    return required
