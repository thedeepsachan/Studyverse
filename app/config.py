import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("STUDYVERSE_DB_PATH", str(BASE_DIR / "studyverse.db")))
UPLOADS_DIR = Path(os.environ.get("STUDYVERSE_UPLOADS_DIR", str(BASE_DIR / "uploads")))
STATIC_DIR = BASE_DIR / "static"

# Load .env file if present (without overriding real environment variables)
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

IST = ZoneInfo("Asia/Kolkata")

# Secret key for HMAC session signing (auto-persisted locally if not provided in env)
_secret_file = BASE_DIR / ".secret_key"
def _get_secret_key() -> str:
    env_key = os.environ.get("STUDYVERSE_SECRET_KEY")
    if env_key:
        return env_key
    if _secret_file.exists():
        return _secret_file.read_text(encoding="utf-8").strip()
    import secrets
    key = secrets.token_hex(32)
    _secret_file.write_text(key, encoding="utf-8")
    return key

SECRET_KEY = _get_secret_key()

# Super Administrator credentials from environment variables only (never hardcoded in frontend or DB-readable files)
def get_configured_admins() -> list[tuple[str, str]]:
    admins = []
    for idx in (1, 2):
        u = os.environ.get(f"ADMIN_{idx}_USERNAME", "").strip()
        p = os.environ.get(f"ADMIN_{idx}_PASSWORD", "").strip()
        if u and p:
            admins.append((u, p))
    return admins

DEFAULT_DAILY_TARGET_MINUTES = 360  # 6 hours
SESSION_TTL_DAYS = 14
