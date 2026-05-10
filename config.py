"""Centralized configuration loaded from environment variables.

Validates all required variables at startup and writes Google OAuth artifacts
to ephemeral paths so calendar_tools.py can hand them to google-auth.
"""

import base64
import os
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()


class _Missing:
    pass


_MISSING = _Missing()


def _require(name: str, hint: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. {hint}"
        )
    return value


def _optional(name: str, default=_MISSING):
    value = os.getenv(name)
    if value:
        return value
    if isinstance(default, _Missing):
        return None
    return default


# --- Telegram --------------------------------------------------------------

TELEGRAM_BOT_TOKEN = _require(
    "TELEGRAM_BOT_TOKEN",
    "Get this from @BotFather on Telegram (/newbot).",
)

_allowed_user_raw = _require(
    "TELEGRAM_ALLOWED_USER_ID",
    "Message @userinfobot on Telegram to get your numeric user ID.",
)
try:
    TELEGRAM_ALLOWED_USER_ID = int(_allowed_user_raw)
except ValueError as exc:
    raise EnvironmentError(
        "TELEGRAM_ALLOWED_USER_ID must be an integer (your numeric Telegram user ID)."
    ) from exc


# --- Anthropic -------------------------------------------------------------

ANTHROPIC_API_KEY = _require(
    "ANTHROPIC_API_KEY",
    "Create one at https://console.anthropic.com/settings/keys",
)


# --- Google Calendar -------------------------------------------------------

GOOGLE_CALENDAR_ID = _require(
    "GOOGLE_CALENDAR_ID",
    "Usually your Gmail address (e.g., yourname@gmail.com). "
    "Find it in Google Calendar Settings -> Integrate calendar -> Calendar ID.",
)


# --- Timezone --------------------------------------------------------------

USER_TIMEZONE = _require(
    "USER_TIMEZONE",
    "Use a valid pytz timezone string (e.g., America/New_York, Europe/London).",
)
try:
    USER_TZ = pytz.timezone(USER_TIMEZONE)
except pytz.UnknownTimeZoneError as exc:
    raise EnvironmentError(
        f"USER_TIMEZONE '{USER_TIMEZONE}' is not a valid pytz timezone. "
        "See https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
    ) from exc


# --- App / runtime ---------------------------------------------------------

ENVIRONMENT = (_optional("ENVIRONMENT", default="development") or "development").lower()
if ENVIRONMENT not in {"development", "production"}:
    raise EnvironmentError(
        f"ENVIRONMENT must be 'development' or 'production', got '{ENVIRONMENT}'."
    )

PORT = int(_optional("PORT", default="8080"))

WEBHOOK_URL = _optional("WEBHOOK_URL")
if ENVIRONMENT == "production" and not WEBHOOK_URL:
    raise EnvironmentError(
        "WEBHOOK_URL is required when ENVIRONMENT=production. "
        "Set it to your Railway domain (e.g., https://your-app.railway.app)."
    )


# --- Google OAuth artifact resolution --------------------------------------
#
# In production, credentials.json and token.json are passed as base64-encoded
# env vars. We decode them and write to /tmp at startup. In local development,
# we fall back to reading the raw files from the project root.

_PROJECT_ROOT = Path(__file__).resolve().parent
_TMP_DIR = Path("/tmp")


def _materialize(env_var: str, local_filename: str, required: bool) -> str | None:
    """Resolve a Google OAuth artifact to a filesystem path.

    Priority:
      1. Base64-encoded env var -> decode and write to /tmp.
      2. Local file in project root (development convenience).
      3. None if not required.
    """
    encoded = os.getenv(env_var)
    if encoded:
        try:
            decoded = base64.b64decode(encoded)
        except Exception as exc:
            raise EnvironmentError(
                f"{env_var} is set but is not valid base64. "
                f"Re-encode the file with: base64 -i {local_filename} | tr -d '\\n'"
            ) from exc
        target = _TMP_DIR / local_filename
        target.write_bytes(decoded)
        return str(target)

    local_path = _PROJECT_ROOT / local_filename
    if local_path.exists():
        return str(local_path)

    if required:
        raise EnvironmentError(
            f"Missing Google OAuth artifact. Set {env_var} (base64-encoded) "
            f"or place {local_filename} in the project root for local dev. "
            f"For credentials.json: download from Google Cloud Console -> APIs & "
            f"Services -> Credentials -> your OAuth Client ID -> Download JSON."
        )
    return None


GOOGLE_CREDENTIALS_PATH = _materialize(
    "GOOGLE_CREDENTIALS_JSON", "credentials.json", required=True
)

# token.json is optional at first launch (run generate_token.py to create it).
# It becomes effectively required after first auth — calendar_tools will surface
# a clear error if it's missing at runtime.
GOOGLE_TOKEN_PATH = _materialize(
    "GOOGLE_TOKEN_JSON", "token.json", required=False
)


# --- Convenience ----------------------------------------------------------

DB_PATH = str(_PROJECT_ROOT / "assistant.db")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
