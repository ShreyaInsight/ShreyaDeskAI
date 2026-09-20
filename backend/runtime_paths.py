"""Private runtime state lives outside the source checkout."""
import os
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("SHREYADESK_RUNTIME_DIR", Path.home() / ".local" / "share" / "shreyadesk")).expanduser()
ENV_PATH = Path(os.environ.get("SHREYADESK_ENV_FILE", RUNTIME_DIR / "shreyadesk.env")).expanduser()
TOKEN_PATH = RUNTIME_DIR / ".kite_token.json"
SCANNER_DB_PATH = RUNTIME_DIR / "scanner.sqlite3"
TELEGRAM_DB_PATH = RUNTIME_DIR / "telegram.sqlite3"
