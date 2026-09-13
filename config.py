"""Runtime configuration for the Telegram recorder bot.

Secrets are read from Replit environment variables so they never need to live
in source control. Optional Google Drive settings are intentionally empty by
default; the bot still runs without Drive uploads enabled.
"""

import os
from pathlib import Path


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_list(value: str) -> list[int]:
    result: list[int] = []
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8787494799:AAGzOR9KTSlDvt52Hq_wjH2azyUjmJwN0To")
TELEGRAM_BOT_TOKEN = BOT_TOKEN
API_HASH = os.getenv("API_HASH", "4892185769903521077c4cea97808b8c")
API_ID = _int(os.getenv("API_ID", "29481626"), 29481626)

OWNER_ID = _int(os.getenv("BOT_OWNER_ID", "5856009289"), 5856009289)
AUTH_USERS = _int_list(os.getenv("AUTHORIZED_USER_IDS", ""))
GROUP_CHAT_ID = _int(os.getenv("ALLOWED_REC_GROUP_ID", "-1003726271113"), -1003726271113)

BRAND_TITLE = os.getenv("BOT_BRAND_TITLE", "Little Bot")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "TPlayOwner_bot").lstrip("@")
DEFAULT_FILENAME = os.getenv("DEFAULT_FILENAME", "Recorded Video")

DOWNLOAD_DIRECTORY = os.getenv("DOWNLOAD_DIRECTORY", str(Path(__file__).resolve().parent / "data"))
Path(DOWNLOAD_DIRECTORY).mkdir(parents=True, exist_ok=True)

# Video-tool performance defaults. Override these when quality or CPU usage
# needs to be tuned for a particular deployment.
VIDEO_TOOL_PRESET = os.getenv("VIDEO_TOOL_PRESET", "ultrafast")
VIDEO_TOOL_THREADS = _int(os.getenv("VIDEO_TOOL_THREADS", "0"), 0)
VIDEO_TOOL_CRF = _int(os.getenv("VIDEO_TOOL_CRF", "23"), 23)

# Optional Google Drive configuration.
GDRIVE_SA_JSON = os.getenv("GDRIVE_SA_JSON", "")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
# Google OAuth client IDs are not secrets. Keep the existing project client ID
# usable by default while allowing a workspace override.
GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID",
    "1031593100053-dnhnbmqdjudjaplo10ur24lkhe7sqndh.apps.googleusercontent.com",
)
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
