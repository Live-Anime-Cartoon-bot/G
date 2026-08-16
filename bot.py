import os
import json
import gzip
import re
import asyncio
import time
import shutil
import tempfile
import secrets as _secrets
import hashlib
import requests
import base64
import logging
import threading
import atexit
import socket
import subprocess
import xml.etree.ElementTree as ET
import yt_dlp
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urljoin, urlsplit
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputFile,
    InputMediaPhoto,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, TypeHandler, filters, ContextTypes, ApplicationHandlerStop
)
from telegram.constants import ParseMode

# Load optional local configuration files before reading any environment values.
# Replit Secrets and workflow environment variables take precedence over .env.
# The bot has historically been stored both at the workspace root and inside a
# nested bot/ directory, so resolve the workspace from whichever layout exists.
_project_root = Path(__file__).resolve().parent
if not (_project_root / "attached_assets").exists() and (
    _project_root.parent / "attached_assets"
).exists():
    _project_root = _project_root.parent
load_dotenv(_project_root / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# PTB/httpx INFO logs include the Bot API URL, which contains the bot token.
# Keep request URLs out of workflow logs, especially when using the local API.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
BOT_OWNER_ID = os.environ.get("BOT_OWNER_ID")
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
DATA_FOLDER = "app/assets/data"
USER_DATA_FILE = "user_data.json"
ADMINS_FILE = "admins.json"
VERIFY_TOKENS_FILE = "verify_tokens.json"
PREMIUM_FILE = "premium_users.json"
AUDIO_TRACK_SETTINGS_FILE = "audio_track_settings.json"
BOT_MODE_FILE = "bot_mode_scheduled_premium.json"
SCHEDULED_RECORDINGS_FILE = os.environ.get(
    "SCHEDULED_RECORDINGS_FILE", "scheduled_recordings.json"
)
COOKIES_FOLDER = os.path.join(DATA_FOLDER, "cookies")
MAX_COOKIE_FILE_BYTES = 2 * 1024 * 1024
COOKIE_UPLOAD_TTL_SECONDS = 5 * 60
WATERMARK_URL = os.environ.get("WATERMARK_URL") or "https://iili.io/Cew1rV1.png"
# Second DishTV watermark — shown only in the last 2 minutes when last_2min is on.
DISHTV_WM2_URL = os.environ.get("DISHTV_WM2_URL") or "https://iili.io/CuMJCjn.md.png"
OTT_WATERMARK_URL = (
    os.environ.get("OTT_WATERMARK_URL")
    or "https://iili.io/CuMJCjn.md.png"
)
WATERMARK_CACHE_FILE = os.environ.get(
    "WATERMARK_CACHE_FILE", "/tmp/dishtv_smart_watermark.png"
)
WATERMARK_POSITION_FILE = "watermark_settings.json"
OTT_WATERMARK_POSITION_FILE = "ott_watermark_settings.json"
PLAYLIST_URL = os.environ.get(
    "PLAYLIST_URL",
    "https://raw.githubusercontent.com/Sflex0719/m3u/refs/heads/main/Zio.m3u"
)
AIRTEL_PLAYLIST_FILE = os.environ.get(
    "AIRTEL_PLAYLIST_FILE",
    "",
)
AIRTEL_PLAYLIST_URL = os.environ.get(
    "AIRTEL_PLAYLIST_URL",
    "https://raw.githubusercontent.com/LittleSingham1/M3u8/refs/heads/main/Airtel_Selected_Channel_Links.txt",
).strip()
SUNNXT_PLAYLIST_URL = os.environ.get(
    "SUNNXT_PLAYLIST_URL",
    "https://mahabuburbd.netlify.app/sun-next.m3u",
).strip()
AIRTEL_WATERMARK_FILE = os.environ.get(
    "AIRTEL_WATERMARK_FILE",
    str(_project_root / "attached_assets" / "airtel_watermark.png"),
)
# Image overlaid only in the last 2 minutes of every Airtel recording.
# Airtel watermark (airtel_watermark.png) is NOT added for Airtel channels.
AIRTEL_LAST2MIN_OVERLAY_URL = os.environ.get(
    "AIRTEL_LAST2MIN_OVERLAY_URL",
    "https://iili.io/CuMJCjn.md.png",
)
_tz = os.environ.get("TIMEZONE", "Asia/Kolkata")
IST = ZoneInfo(_tz)

REC_LIMIT_SECONDS = int(os.environ.get("REC_LIMIT_SECONDS", "3000"))
VERIFICATION_EXPIRY_SECONDS = int(os.environ.get("VERIFICATION_EXPIRY_SECONDS", "2400"))
RECORDING_TIMEOUT_GRACE_SECONDS = max(
    10, int(os.environ.get("RECORDING_TIMEOUT_GRACE_SECONDS", "20"))
)
RECORDING_STALL_TIMEOUT_SECONDS = max(
    8, int(os.environ.get("RECORDING_STALL_TIMEOUT_SECONDS", "15"))
)
SHORTLINK_URL = os.environ.get("SHORTLINK_URL", "https://shortxlinks.in")
SHORTLINK_API = os.environ.get("SHORTLINK_API", "")
WORKING_GROUP = os.environ.get("WORKING_GROUP") or "-1003726271113"
GROUP_LINK = os.environ.get("GROUP_LINK") or "https://t.me/+-IByJV2DtJhmODBl"
BOTUSERNAME = os.environ.get("BOTUSERNAME", "")
MAX_PROCESSES = int(os.environ.get("MAX_PROCESSES", "5"))
PAID_BOT_CONTACT = os.environ.get("PAID_BOT_CONTACT", "@LS_Ower_bot")
DEFAULT_AUDIO_MODE = "multi"

def _parse_group_ids(value):
    """Parse a comma/space-separated group allowlist into Telegram IDs."""
    return {
        int(item)
        for item in re.split(r"[\s,]+", value or "")
        if item and re.fullmatch(r"-?\d+", item)
    }


# Only configured working groups are allowed. The requested working group is
# the default when WORKING_GROUP is not set; private chats remain unaffected.
AUTHORIZED_GROUP_IDS = _parse_group_ids(WORKING_GROUP)
UNAUTHORIZED_GROUP_IDS = set()
_GROUP_LEAVE_IN_PROGRESS = set()

# Active process slot counter
_active_processes = 0

# Cancellable recordings: {cancel_id: proc}
ACTIVE_RECORDINGS = {}
PENDING_RECORDINGS = {}
QUALITY_PENDING = {}
QUALITY_PENDING_TTL = 15 * 60
MERGE_PENDING = {}
MERGE_PENDING_TTL = 15 * 60

# ── Recording progress tracking ───────────────
# {session_id: current proc} — constant across retries, keyed by session
RECORDING_SESSION_PROC: dict = {}
# {session_id: {...progress data...}} — updated live for ⚡ Progress popup
RECORDING_PROGRESS_INFO: dict = {}
# {session_id: asyncio.Task} — auto-updater tasks
ACTIVE_UPDATERS: dict = {}
MEDIA_USER_TASKS: dict = {}
MEDIA_MERGE_SESSIONS: dict = {}
STREAM_EXTRACTOR_PENDING: dict = {}
PENDING_COOKIE_UPLOADS: dict[int, float] = {}
SCREENSHOT_PENDING: dict[str, dict] = {}
DEFAULT_AUDIO_PENDING: dict[str, dict] = {}
SCHEDULED_RECORDINGS: list[dict] = []
SCHEDULE_RUNTIME_TASKS: dict[str, asyncio.Task] = {}
SCHEDULE_MANAGER_TASK = None

TELEGRAM_LOCAL_API_URL = os.environ.get("TELEGRAM_LOCAL_API_URL", "").rstrip("/")
TELEGRAM_LOCAL_API_ENABLED = bool(TELEGRAM_LOCAL_API_URL)
TELEGRAM_BOT_DOWNLOAD_LIMIT = (
    2 * 1024 * 1024 * 1024 if TELEGRAM_LOCAL_API_ENABLED else 20 * 1024 * 1024
)
TELEGRAM_BOT_UPLOAD_LIMIT = (
    2 * 1024 * 1024 * 1024 if TELEGRAM_LOCAL_API_ENABLED else 50 * 1024 * 1024
)
# 8081 is used by the existing mockup preview service in this workspace.
TELEGRAM_LOCAL_API_PORT = int(os.environ.get("TELEGRAM_LOCAL_API_PORT", "8090"))
_LOCAL_TELEGRAM_API_PROCESS = None


def _start_local_telegram_api():
    """Start the local Bot API server when API credentials are available.

    The official cloud Bot API cannot download files larger than 20 MB. The
    local server supports the 2 GB limits and is started as a child of this
    existing bot workflow, so no second user-managed workflow is required.
    Credentials are passed through the child environment rather than command
    arguments or logs.
    """
    global TELEGRAM_LOCAL_API_URL
    global TELEGRAM_LOCAL_API_ENABLED
    global TELEGRAM_BOT_DOWNLOAD_LIMIT
    global TELEGRAM_BOT_UPLOAD_LIMIT
    global _LOCAL_TELEGRAM_API_PROCESS

    if TELEGRAM_LOCAL_API_URL:
        return True
    if not API_ID or not API_HASH:
        logger.warning(
            "Local Telegram Bot API disabled: API_ID/API_HASH are not configured."
        )
        return False

    try:
        os.makedirs("/tmp/telegram-bot-api", exist_ok=True)
        os.makedirs("/tmp/telegram-bot-api-tmp", exist_ok=True)
        child_env = os.environ.copy()
        child_env["TELEGRAM_API_ID"] = str(API_ID)
        child_env["TELEGRAM_API_HASH"] = str(API_HASH)
        _LOCAL_TELEGRAM_API_PROCESS = subprocess.Popen(
            [
                "telegram-bot-api",
                "--local",
                f"--http-port={TELEGRAM_LOCAL_API_PORT}",
                "--http-ip-address=127.0.0.1",
                "--dir=/tmp/telegram-bot-api",
                "--temp-dir=/tmp/telegram-bot-api-tmp",
                "--verbosity=1",
            ],
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning("Local Telegram Bot API start failed: %s", exc)
        _LOCAL_TELEGRAM_API_PROCESS = None
        return False

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _LOCAL_TELEGRAM_API_PROCESS.poll() is not None:
            logger.warning(
                "Local Telegram Bot API exited during startup (code %s).",
                _LOCAL_TELEGRAM_API_PROCESS.returncode,
            )
            _LOCAL_TELEGRAM_API_PROCESS = None
            return False
        try:
            with socket.create_connection(
                ("127.0.0.1", TELEGRAM_LOCAL_API_PORT), timeout=0.5
            ):
                break
        except OSError:
            time.sleep(0.25)
    else:
        logger.warning("Local Telegram Bot API did not open its port in time.")
        _LOCAL_TELEGRAM_API_PROCESS.terminate()
        _LOCAL_TELEGRAM_API_PROCESS = None
        return False

    TELEGRAM_LOCAL_API_URL = (
        f"http://127.0.0.1:{TELEGRAM_LOCAL_API_PORT}"
    )
    TELEGRAM_LOCAL_API_ENABLED = True
    TELEGRAM_BOT_DOWNLOAD_LIMIT = 2 * 1024 * 1024 * 1024
    TELEGRAM_BOT_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024
    logger.info("Local Telegram Bot API enabled; file limit 2 GB.")
    return True


def _stop_local_telegram_api():
    process = _LOCAL_TELEGRAM_API_PROCESS
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


atexit.register(_stop_local_telegram_api)

_channel_cache = None          # list of channel dicts
_channel_cache_ts = 0          # unix timestamp of last fetch
_airtel_channel_cache = None
_airtel_channel_cache_ts = 0
_sunnxt_channel_cache = None
_sunnxt_channel_cache_ts = 0
_CHANNEL_CACHE_TTL = 48 * 60   # refresh playlist every 48 minutes
_authenticated_stream_cache = {}
_AUTHENTICATED_STREAM_CACHE_TTL = 90

def _load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else {}

def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


BOT_MODE = "public"


def load_bot_mode():
    """Load the persistent public/private bot mode and create its file if needed."""
    global BOT_MODE
    try:
        data = _load_json(BOT_MODE_FILE, {"bot_mode": "public"})
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        data = {"bot_mode": "public"}
    mode = str(data.get("bot_mode", "public")).strip().lower()
    if mode not in {"public", "private"}:
        mode = "public"
    BOT_MODE = mode
    _save_json(BOT_MODE_FILE, {"bot_mode": BOT_MODE})
    return BOT_MODE


def save_bot_mode(mode):
    """Persist the owner-selected public/private bot mode."""
    global BOT_MODE
    BOT_MODE = mode if mode in {"public", "private"} else "public"
    _save_json(BOT_MODE_FILE, {"bot_mode": BOT_MODE})


_DEFAULT_AUDIO_LABEL = "LittleSinghamChannel"
_DEFAULT_AUDIO_LANGUAGE_LABELS = {
    "english": "LittleSinghamChannel",
    "hindi": "LittleSinghamChannel",
    "telugu": "LittleSinghamChannel",
    "kannada": "LittleSinghamChannel",
    "tamil": "Anime Cartoon",
    "malayalam": "Anime Cartoon",
    "marathi": "Anime Cartoon",
}


def _clean_audio_label(value: object, fallback: str = _DEFAULT_AUDIO_LABEL) -> str:
    """Normalize a configured audio label before passing it to FFmpeg."""
    label = re.sub(r"\s+", " ", str(value or "").strip()).replace("\x00", "")
    return label[:100] or fallback


def get_audio_track_settings() -> dict:
    """Load persistent audio labels, retaining the requested language mapping."""
    settings = _load_json(AUDIO_TRACK_SETTINGS_FILE, {})
    labels = dict(_DEFAULT_AUDIO_LANGUAGE_LABELS)
    stored_labels = settings.get("language_labels")
    if isinstance(stored_labels, dict):
        for language, label in stored_labels.items():
            language_key = str(language).strip().lower()
            if language_key in labels:
                labels[language_key] = _clean_audio_label(
                    label, labels[language_key]
                )
    return {
        "default_label": _clean_audio_label(
            settings.get("default_label"), _DEFAULT_AUDIO_LABEL
        ),
        "apply_default_to_all": bool(settings.get("apply_default_to_all", False)),
        "language_labels": labels,
    }


def save_default_audio_label(label: str) -> None:
    """Persist the owner-selected fallback label for future media processing."""
    settings = get_audio_track_settings()
    settings["default_label"] = _clean_audio_label(label)
    settings["apply_default_to_all"] = True
    _save_json(AUDIO_TRACK_SETTINGS_FILE, settings)


def _audio_label_for_language(language_name: str) -> str:
    """Resolve the configured display label for one detected audio language."""
    settings = get_audio_track_settings()
    if settings["apply_default_to_all"]:
        return settings["default_label"]
    language_key = str(language_name or "").strip().lower()
    return _clean_audio_label(
        settings["language_labels"].get(language_key)
        or settings["default_label"]
    )


_AUDIO_TRACK_COMPATIBILITY_NOTE = (
    "🎵 Audio tracks are fully compatible with VLC, MX Player, Telegram, "
    "and other media players.\n\n"
    "📌 Track names are visible in:\n"
    "• VLC → Track Information\n"
    "• MX Player → Audio Tracks\n"
    "• Telegram → Audio Selector"
)


WATERMARK_PRESET_MODES = ("left", "right", "top_center", "center", "left_bottom")

def get_watermark_position():
    """Return the configured watermark mode and offset in pixels."""
    settings = _load_json(WATERMARK_POSITION_FILE, {})
    mode = settings.get("mode", "right")
    if mode not in WATERMARK_PRESET_MODES:
        mode = "right"
    try:
        offset = max(0, int(settings.get("offset", 70)))
    except (TypeError, ValueError):
        offset = 70
    return mode, offset

def save_watermark_position(mode, offset=0):
    settings = get_watermark_settings()
    settings["mode"] = mode
    settings["offset"] = int(offset)
    _save_json(WATERMARK_POSITION_FILE, settings)

def get_watermark_settings() -> dict:
    """Return full watermark settings with defaults."""
    s = _load_json(WATERMARK_POSITION_FILE, {})
    mode = s.get("mode", "right")
    if mode not in WATERMARK_PRESET_MODES:
        mode = "right"
    try:
        offset = max(0, int(s.get("offset", 70)))
    except (TypeError, ValueError):
        offset = 70
    return {
        "mode":       mode,
        "offset":     offset,
        "enabled":    bool(s.get("enabled", True)),
        "last_2min":  bool(s.get("last_2min", False)),
        "custom_url": str(s.get("custom_url", "")),
    }

def save_watermark_settings(**kwargs):
    """Persist one or more watermark settings fields."""
    settings = get_watermark_settings()
    for k, v in kwargs.items():
        if k in settings:
            settings[k] = v
    _save_json(WATERMARK_POSITION_FILE, settings)


def get_ott_watermark_settings() -> dict:
    """Return provider watermark settings without touching DishTV settings."""
    settings = _load_json(OTT_WATERMARK_POSITION_FILE, {})
    mode = settings.get("mode", "top_center")
    if mode not in WATERMARK_PRESET_MODES:
        mode = "top_center"
    try:
        offset = max(0, int(settings.get("offset", 70)))
    except (TypeError, ValueError):
        offset = 70
    return {
        "mode": mode,
        "offset": offset,
        "enabled": bool(settings.get("enabled", True)),
        "last_2min": bool(settings.get("last_2min", True)),
        "custom_url": str(settings.get("custom_url", "")),
    }


def save_ott_watermark_settings(**kwargs):
    """Persist provider watermark settings in an OTT-only file."""
    settings = get_ott_watermark_settings()
    for key, value in kwargs.items():
        if key in settings:
            settings[key] = value
    _save_json(OTT_WATERMARK_POSITION_FILE, settings)


# {user_id: selection_id} — tracks who is waiting to paste a new DishTV watermark URL
PENDING_URL_CHANGES: dict[int, str] = {}
# Airtel/Sun NXT use a separate pending map so the DishTV callback flow stays
# unchanged.
PENDING_OTT_URL_CHANGES: dict[int, str] = {}

def parse_pixel_value(value):
    """Parse a non-negative pixel value such as 70px or 70."""
    match = re.fullmatch(r"(\d+)\s*px?", value.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _build_watermark_menu(selection_id: str):
    """Return (text, InlineKeyboardMarkup) for the DishTV watermark settings screen."""
    ws = get_watermark_settings()
    mode       = ws["mode"]
    enabled    = ws["enabled"]
    last_2min  = ws["last_2min"]
    custom_url = ws["custom_url"]

    POS_LABELS = {
        "top_center":  "Top Center",
        "center":      "Center",
        "left_bottom": "Left",
        "right":       "Right",
        "left":        "Left Edge",
    }

    def _pos_btn(key, label):
        tick = "✅ " if mode == key else ""
        return InlineKeyboardButton(f"{tick}{label}", callback_data=f"rec_wm:pos:{key}:{selection_id}")

    pos_row = [
        _pos_btn("top_center",  "Top Center"),
        _pos_btn("center",      "Center"),
        _pos_btn("left_bottom", "Left"),
    ]
    toggle_row = [
        InlineKeyboardButton(
            "✅ Enable" if enabled else "Enable",
            callback_data=f"rec_wm:enable:{selection_id}",
        ),
        InlineKeyboardButton(
            "❌ Disable" if not enabled else "Disable",
            callback_data=f"rec_wm:disable:{selection_id}",
        ),
    ]
    timing_row = [
        InlineKeyboardButton(
            f"{'✅' if last_2min else '⬜'} Last 2 Min",
            callback_data=f"rec_wm:last2min:{selection_id}",
        ),
        InlineKeyboardButton(
            "🔗 Change Watermark Link",
            callback_data=f"rec_wm:changeurl:{selection_id}",
        ),
    ]
    nav_row = [
        InlineKeyboardButton("⬅️ Back",            callback_data=f"rec_wm:back:{selection_id}"),
        InlineKeyboardButton("▶️ Next Recording", callback_data=f"rec_wm:start:{selection_id}"),
    ]
    keyboard = InlineKeyboardMarkup([pos_row, toggle_row, timing_row, nav_row])

    pos_label   = POS_LABELS.get(mode, mode)
    url_display = custom_url if custom_url else "(default)"
    text = (
        "🎨 *WM2 Settings*\n\n"
        f"📍 Position : *{pos_label}*\n"
        f"🔘 Status   : {'✅ Enabled' if enabled else '❌ Disabled'}\n"
        f"⏱ Timing   : {'Last 2 min only' if last_2min else 'Off'}\n"
        f"🔗 URL      : `{url_display}`"
    )
    return text, keyboard


def _build_ott_watermark_menu(selection_id: str):
    """Build the provider watermark screen for Airtel and Sun NXT only."""
    ws = get_ott_watermark_settings()
    mode = ws["mode"]
    enabled = ws["enabled"]
    last_2min = ws["last_2min"]
    custom_url = ws["custom_url"]
    labels = {
        "top_center": "Top Center",
        "center": "Center",
        "left_bottom": "Left",
        "right": "Right",
        "left": "Left Edge",
    }

    def position_button(key, label):
        tick = "✅ " if mode == key else ""
        return InlineKeyboardButton(
            f"{tick}{label}",
            callback_data=f"ott_wm:pos:{key}:{selection_id}",
        )

    keyboard = InlineKeyboardMarkup([
        [
            position_button("top_center", "Top Center"),
            position_button("center", "Center"),
            position_button("left_bottom", "Left"),
        ],
        [
            InlineKeyboardButton(
                "✅ Enable" if enabled else "Enable",
                callback_data=f"ott_wm:enable:{selection_id}",
            ),
            InlineKeyboardButton(
                "🚫 Disable" if enabled else "✅ Disable",
                callback_data=f"ott_wm:disable:{selection_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{'✅' if last_2min else '⬜'} Last 2 Min",
                callback_data=f"ott_wm:last2min:{selection_id}",
            ),
            InlineKeyboardButton(
                "🔗 Change Watermark",
                callback_data=f"ott_wm:changeurl:{selection_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back", callback_data=f"ott_wm:back:{selection_id}"
            ),
            InlineKeyboardButton(
                "➡️ Next Recording", callback_data=f"ott_wm:start:{selection_id}"
            ),
        ],
    ])
    return (
        "🎨 *Watermark Settings*\n\n"
        f"📍 Position : *{labels.get(mode, mode)}*\n"
        f"🔘 Status   : {'✅ Enabled' if enabled else '🚫 Disabled'}\n"
        f"⏱ Timing   : {'Last 2 min only' if last_2min else 'Off'}\n"
        f"🔗 URL      : `{custom_url or '(Default)'}`",
        keyboard,
    )


def _ott_probe_text(source: str) -> str:
    platform = "Sun NXT" if source == "sunnxt" else "Airtel"
    return (
        "🔍 *Probing Stream...*\n\n"
        "• Detecting available audio tracks...\n"
        "• Detecting available video qualities...\n\n"
        f"*Platform:* {platform}\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def _ott_quality_keyboard(selection_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "480p", callback_data=f"ott_quality:{selection_id}:480"
        ),
        InlineKeyboardButton(
            "720p", callback_data=f"ott_quality:{selection_id}:720"
        ),
        InlineKeyboardButton(
            "1080p", callback_data=f"ott_quality:{selection_id}:1080"
        ),
    ]])


def _ott_quality_text(channel: dict, tracks: list, selected: str = "") -> str:
    platform = "Sun NXT" if channel.get("source") == "sunnxt" else "Airtel"
    selected_text = f"\n✅ Selected Quality: *{selected}*" if selected else ""
    return (
        "🔍 *Probing stream for Quality...*\n\n"
        "• Detecting available audio tracks...\n"
        "• Detecting available video qualities...\n\n"
        f"📡 Platform: *{platform}*\n\n"
        "🎞️ *Select video quality:*\n"
        "Choose your output quality:"
        f"{selected_text}\n\n"
        f"{_audio_statuses(tracks)}"
    )


def _ott_watermark_cache_file(url: str) -> str:
    """Return a local cache filename for a provider watermark URL."""
    digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:16]
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    return f"/tmp/ott_watermark_{digest}{suffix}"


def _get_watermark_input(watermark_url: str | None = None,
                         cache_file: str | None = None) -> str:
    """Return a local watermark path so FFmpeg does not fetch it repeatedly."""
    watermark_url = WATERMARK_URL if watermark_url is None else watermark_url
    cache_file = WATERMARK_CACHE_FILE if cache_file is None else cache_file
    if not watermark_url:
        return ""
    if not watermark_url.lower().startswith(("http://", "https://")):
        return watermark_url if os.path.exists(watermark_url) else ""
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1024:
        return cache_file

    response = requests.get(watermark_url, timeout=15)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("Watermark download returned an empty file.")
    cache_dir = os.path.dirname(cache_file)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    temp_path = f"{cache_file}.{os.getpid()}.tmp"
    with open(temp_path, "wb") as watermark_file:
        watermark_file.write(response.content)
    os.replace(temp_path, cache_file)
    return cache_file


def telegram_limit_text():
    """Return the active Telegram Bot API file limit for user-facing messages."""
    return "2 GB" if TELEGRAM_LOCAL_API_ENABLED else "20 MB"


def telegram_upload_limit_text():
    """Return the active Telegram upload limit for user-facing messages."""
    return "2 GB" if TELEGRAM_LOCAL_API_ENABLED else "50 MB"

def get_user_data():
    return _load_json(USER_DATA_FILE, {})

def save_user_data(data):
    _save_json(USER_DATA_FILE, data)

def get_admins():
    return _load_json(ADMINS_FILE, {})

def save_admins(data):
    _save_json(ADMINS_FILE, data)

def get_verify_tokens():
    return _load_json(VERIFY_TOKENS_FILE, {})

def save_verify_tokens(data):
    _save_json(VERIFY_TOKENS_FILE, data)

# ── Premium helpers ────────────────────────────

def get_premium_users():
    return _load_json(PREMIUM_FILE, {})

def save_premium_users(data):
    _save_json(PREMIUM_FILE, data)

def is_premium(user_id):
    users = get_premium_users()
    entry = users.get(str(user_id))
    if not entry:
        return False
    if str(entry.get("plan", "")).strip().lower() == "lifetime":
        return True
    expires_at = entry.get("expires_at")
    if not expires_at:
        return False
    try:
        return datetime.now(IST) < datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False

def parse_duration_str(duration_str):
    """Parse duration like 30m, 1h, 1D, 28D → timedelta."""
    duration_str = duration_str.strip()
    match = re.match(r"^(\d+)([mMhHdD])$", duration_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    return None

def is_verified(user_id):
    tokens = get_verify_tokens()
    entry = tokens.get(str(user_id))
    if not entry:
        return False
    verified_at = entry.get("verified_at")
    if not verified_at:
        return False
    elapsed = (datetime.now(IST) - datetime.fromisoformat(verified_at)).total_seconds()
    return elapsed < VERIFICATION_EXPIRY_SECONDS

def generate_verify_token(user_id):
    token = _secrets.token_hex(16)
    tokens = get_verify_tokens()
    tokens[str(user_id)] = {
        "token": token,
        "created_at": datetime.now(IST).isoformat(),
        "verified_at": None,
    }
    save_verify_tokens(tokens)
    return token

def mark_verified(user_id, token):
    tokens = get_verify_tokens()
    entry = tokens.get(str(user_id))
    if not entry or entry.get("token") != token:
        return False
    # Token must be used within 15 minutes of generation
    created_at = datetime.fromisoformat(entry["created_at"])
    if (datetime.now(IST) - created_at).total_seconds() > 900:
        return False
    entry["verified_at"] = datetime.now(IST).isoformat()
    save_verify_tokens(tokens)
    return True

def shorten_url(long_url):
    if not SHORTLINK_API:
        return long_url
    try:
        api_base = SHORTLINK_URL.rstrip("/")
        resp = requests.get(
            f"{api_base}/api",
            params={"api": SHORTLINK_API, "url": long_url},
            timeout=10
        )
        data = resp.json()
        # shortxlinks.in / ShrinkMe: try all common response fields
        short = (
            data.get("shortenedUrl")
            or data.get("link")
            or (data.get("shortenedUrl") if data.get("status") == "success" else None)
            or data.get("short_url")
            or data.get("url")
        )
        return short if short else long_url
    except Exception:
        return long_url

def is_owner(user_id):
    if not BOT_OWNER_ID:
        return False
    return str(user_id) == str(BOT_OWNER_ID)


def _is_bot_mode_command(update):
    message = update.effective_message
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text.startswith("/"):
        return False
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    return command in {"/public", "/private"}


def _is_verification_update(update):
    """Allow verification requests and their completion flow in private mode."""
    message = update.effective_message
    text = (
        getattr(message, "text", None)
        or getattr(message, "caption", None)
        or ""
    ).strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        if command == "/verify":
            return True
        if command == "/start" and len(parts) > 1:
            return parts[1].strip().startswith("verify_")

    query = update.callback_query
    return bool(query and query.data == "howto_verify")


async def bot_mode_access_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block non-owner users before any handler runs while private mode is enabled."""
    if (
        BOT_MODE != "private"
        or _is_bot_mode_command(update)
        or _is_verification_update(update)
    ):
        return
    user = update.effective_user
    if user and is_owner(user.id):
        return

    denial = (
        "🚫 Access Denied!\n\n"
        "🔒 The bot is currently running in Private Mode.\n\n"
        "Only the Bot Owner can use this bot."
    )
    query = update.callback_query
    if query:
        await query.answer("Access Denied!", show_alert=True)
        if query.message:
            await query.message.reply_text(denial)
    elif update.effective_message:
        await update.effective_message.reply_text(denial)
    raise ApplicationHandlerStop


def is_admin(user_id):
    admins = get_admins()
    return str(user_id) in admins

def get_user_role(user_id):
    if is_owner(user_id):
        return "owner"
    if is_admin(user_id):
        return "admin"
    return "user"


async def leave_unauthorized_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enforce group access and leave unauthorized groups automatically."""
    chat = update.effective_chat
    if not chat:
        return

    if chat.type == "private":
        if _is_verification_update(update):
            return
        if BOT_MODE == "public":
            return
        user = update.effective_user
        if (
            not user
            or is_owner(user.id)
            or is_admin(user.id)
            or is_premium(user.id)
        ):
            return

        display_name = f"@{user.username}" if user.username else (user.first_name or "User")
        join_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔗 Join Group", url=GROUP_LINK)
        ]])
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"👋 Hi {display_name}\n"
                f"User ID: `{user.id}`\n\n"
                "This bot works only in our official group.\n"
                "Owner, Admin, and Premium users can use the bot in private chat.\n"
                "Normal users must use the official group.\n\n"
                "👉 Please join the group below to use this bot."
            ),
            reply_markup=join_markup,
            parse_mode=ParseMode.MARKDOWN,
        )
        raise ApplicationHandlerStop

    if chat.type not in ("group", "supergroup"):
        return
    if chat.id in AUTHORIZED_GROUP_IDS and chat.id not in UNAUTHORIZED_GROUP_IDS:
        return

    if chat.id not in _GROUP_LEAVE_IN_PROGRESS:
        _GROUP_LEAVE_IN_PROGRESS.add(chat.id)
        try:
            logger.warning(
                "Unauthorized group %s detected; leaving automatically.",
                chat.id,
            )
            await context.bot.leave_chat(chat_id=chat.id)
            logger.info("Left unauthorized group %s.", chat.id)
        except Exception:
            logger.exception("Could not leave unauthorized group %s.", chat.id)
        finally:
            _GROUP_LEAVE_IN_PROGRESS.discard(chat.id)

    # Do not allow any command/message handler to process this group update.
    raise ApplicationHandlerStop

# ── Credential helpers ─────────────────────────

def load_credentials():
    creds_path = os.path.join(DATA_FOLDER, "creds.jtv")
    key_path = os.path.join(DATA_FOLDER, "credskey.jtv")
    if not os.path.exists(creds_path) or not os.path.exists(key_path):
        return None
    with open(key_path, "r") as f:
        key = int(f.read().strip())
    with open(creds_path, "r") as f:
        enc = f.read().strip()
    decoded = base64.b64decode(enc).decode("latin-1")
    decrypted = "".join(chr(ord(c) - key) for c in decoded)
    return json.loads(decrypted)

def save_credentials(jio_data, mobile):
    u_name = encrypt_data(mobile, "TS-JIOTV")
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(os.path.join(DATA_FOLDER, "creds.jtv"), "w") as f:
        f.write(encrypt_data(json.dumps(jio_data), u_name))
    with open(os.path.join(DATA_FOLDER, "credskey.jtv"), "w") as f:
        f.write(u_name)
    invalidate_stream_caches()


def invalidate_stream_caches():
    """Drop playlist and authenticated playback data after login/session changes."""
    global _channel_cache, _channel_cache_ts
    _channel_cache = None
    _channel_cache_ts = 0
    _authenticated_stream_cache.clear()

def encrypt_data(data, key):
    key = int(key)
    enc = "".join(chr(ord(c) + key) for c in data)
    return base64.b64encode(enc.encode("latin-1")).decode()

# ── JioTV API helpers ──────────────────────────

def parse_m3u(text, source="dishtv"):
    """Parse an M3U playlist into a list of channel dicts compatible with the rest of the bot."""
    channels = []
    lines = text.splitlines()
    i = 0
    pending_license_key = ""
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#KODIPROP:inputstream.adaptive.license_key="):
            pending_license_key = line.split("=", 1)[1]
            i += 1
            continue
        if line.startswith("#EXTINF"):
            # Extract tvg attributes from the EXTINF line
            channel_id = re.search(r'tvg-id="([^"]*)"', line)
            channel_name = re.search(r'tvg-name="([^"]*)"', line)
            logo = re.search(r'tvg-logo="([^"]*)"', line)
            group = re.search(r'group-title="([^"]*)"', line)

            channel_id = channel_id.group(1) if channel_id else ""
            channel_name = channel_name.group(1) if channel_name else ""
            if not channel_name:
                # Some provider playlists use the text after the final comma
                # instead of a tvg-name attribute.
                display_name = line.split(",", 1)[1].strip() if "," in line else ""
                channel_name = display_name
            logo = logo.group(1) if logo else ""
            group = group.group(1) if group else "Other"

            # Collect KODIPROP / EXTVLCOPT / EXTHTTP lines and stream URL
            license_key = pending_license_key
            pending_license_key = ""
            user_agent = ""
            cookie = ""
            stream_url = ""
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if nxt.startswith("#KODIPROP:inputstream.adaptive.license_key="):
                    license_key = nxt.split("=", 1)[1]
                elif nxt.startswith("#EXTVLCOPT:http-user-agent="):
                    user_agent = nxt.split("=", 1)[1]
                elif nxt.startswith("#EXTHTTP:"):
                    try:
                        http_data = json.loads(nxt[len("#EXTHTTP:"):])
                        cookie = http_data.get("cookie", "")
                    except Exception:
                        pass
                elif nxt and not nxt.startswith("#"):
                    stream_url = nxt
                    i += 1
                    break
                i += 1

            if channel_name and stream_url:
                channels.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "channelCategoryId": group,
                    "isCatchupAvailable": "False",
                    "logoUrl": logo,
                    "stream_url": stream_url,
                    "license_key": license_key,
                    "cenc_key": _license_to_cenc_key(license_key),
                    "user_agent": user_agent,
                    "cookie": cookie,
                    "source": source,
                })
        else:
            i += 1
    return channels


def _license_to_cenc_key(license_value: str) -> str:
    """Extract the hex ClearKey from a Kodi KODIPROP JSON value."""
    if not license_value:
        return ""
    try:
        value = json.loads(license_value)
        keys = value.get("keys") or []
        key_value = str(keys[0].get("k") or "").strip()
        if not key_value:
            return ""
        padding = "=" * (-len(key_value) % 4)
        return base64.urlsafe_b64decode(key_value + padding).hex()
    except (TypeError, ValueError, KeyError, IndexError):
        return ""


def parse_airtel_playlist(text):
    """Parse the uploaded Airtel category/name/URL text file."""
    channels = []
    pending_category = ""
    pending_name = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            pending_category = line[1:-1].strip()
            continue
        if line.startswith(("http://", "https://")):
            if pending_name:
                channels.append({
                    "channel_id": "",
                    "channel_name": pending_name,
                    "channelCategoryId": pending_category or "Airtel",
                    "isCatchupAvailable": "False",
                    "stream_url": line,
                    "license_key": "",
                    "user_agent": "",
                    "cookie": "",
                    "source": "airtel",
                    "airtel_overlay": True,
                })
            pending_name = ""
            continue
        pending_name = line
    return channels


def get_channels(force_refresh=False):
    global _channel_cache, _channel_cache_ts
    now = time.time()
    if not force_refresh and _channel_cache and (now - _channel_cache_ts) < _CHANNEL_CACHE_TTL:
        return _channel_cache
    resp = requests.get(PLAYLIST_URL, timeout=15)
    resp.raise_for_status()
    _channel_cache = parse_m3u(resp.text, source="dishtv")
    _channel_cache_ts = now
    return _channel_cache


def get_airtel_channels(force_refresh=False):
    global _airtel_channel_cache, _airtel_channel_cache_ts
    now = time.time()
    if (
        not force_refresh
        and _airtel_channel_cache
        and (now - _airtel_channel_cache_ts) < _CHANNEL_CACHE_TTL
    ):
        return _airtel_channel_cache
    playlist_text = ""
    if AIRTEL_PLAYLIST_URL:
        try:
            response = requests.get(AIRTEL_PLAYLIST_URL, timeout=15)
            response.raise_for_status()
            playlist_text = response.text
        except requests.RequestException as exc:
            logger.warning("Airtel remote playlist unavailable: %s", exc)
    if not playlist_text:
        if not AIRTEL_PLAYLIST_FILE:
            return []
        path = Path(AIRTEL_PLAYLIST_FILE)
        if path.exists():
            playlist_text = path.read_text(
                encoding="utf-8", errors="replace"
            )
        else:
            return []
    _airtel_channel_cache = parse_airtel_playlist(playlist_text)
    _airtel_channel_cache_ts = now
    return _airtel_channel_cache


def get_sunnxt_channels(force_refresh=False):
    global _sunnxt_channel_cache, _sunnxt_channel_cache_ts
    now = time.time()
    if (
        not force_refresh
        and _sunnxt_channel_cache
        and (now - _sunnxt_channel_cache_ts) < _CHANNEL_CACHE_TTL
    ):
        return _sunnxt_channel_cache
    if not SUNNXT_PLAYLIST_URL:
        return []
    response = requests.get(SUNNXT_PLAYLIST_URL, timeout=20)
    response.raise_for_status()
    _sunnxt_channel_cache = [
        channel for channel in parse_m3u(response.text, source="sunnxt")
        if channel.get("stream_url", "").startswith(("http://", "https://"))
    ]
    _sunnxt_channel_cache_ts = now
    return _sunnxt_channel_cache


def find_channel(name_query, force_refresh=False, source="dishtv"):
    if source == "airtel":
        channels = get_airtel_channels(force_refresh=force_refresh)
    elif source == "sunnxt":
        channels = get_sunnxt_channels(force_refresh=force_refresh)
    else:
        channels = get_channels(force_refresh=force_refresh)
    q = name_query.lower().strip()
    for c in channels:
        if c["channel_name"].lower() == q:
            return c
    for c in channels:
        if q in c["channel_name"].lower():
            return c
    return None

def refresh_channel(channel):
    """Refresh signed playlist data immediately before a recording."""
    if channel.get("source") in {"airtel", "sunnxt"}:
        # Remote provider playlists may be updated while the bot is running.
        fresh = find_channel(
            channel.get("channel_name", ""),
            force_refresh=True,
            source=channel.get("source"),
        )
        return fresh or channel
    try:
        fresh = find_channel(channel.get("channel_name", ""), force_refresh=True)
        fresh = fresh or channel
        authenticated = get_authenticated_live_stream(fresh)
        if authenticated and authenticated.get("stream_url"):
            fresh = dict(fresh)
            for key, value in authenticated.items():
                if value:
                    fresh[key] = value
        return fresh
    except Exception:
        return channel


def _airtel_stream_error(stream_url: str) -> str:
    """Return an upstream Airtel error, or empty for a valid HLS playlist."""
    if not stream_url or not stream_url.lower().startswith(("http://", "https://")):
        return "Airtel stream URL missing."
    try:
        response = requests.get(
            stream_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
            timeout=15,
            allow_redirects=True,
        )
        body = response.text.lstrip()
        if response.status_code >= 400:
            return f"Upstream HTTP {response.status_code}."
        if body.lower().startswith("error:"):
            return body[:240].strip()
        if "#EXTM3U" not in body:
            return "Upstream did not return a valid HLS playlist."
        return ""
    except requests.RequestException as exc:
        return f"Upstream request failed: {exc}"


def _dish_stream_error(channel: dict) -> str:
    """Return a DishTV CDN error, or empty when its DASH MPD is reachable."""
    stream_url = channel.get("stream_url", "")
    if not stream_url or not stream_url.lower().startswith(("http://", "https://")):
        return "DishTV MPD URL missing."
    headers = {}
    if channel.get("user_agent"):
        headers["User-Agent"] = channel["user_agent"]
    if channel.get("cookie"):
        headers["Cookie"] = channel["cookie"]
    try:
        response = requests.get(
            stream_url,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return f"DishTV CDN HTTP {response.status_code}."
        body = response.text.lstrip()
        if "<MPD" not in body[:2000] and "<?xml" not in body[:2000]:
            return "DishTV upstream did not return a valid DASH MPD."
        return ""
    except requests.RequestException as exc:
        return f"DishTV upstream request failed: {exc}"


def _sunnxt_stream_error(channel: dict) -> str:
    """Return a Sunnxt DASH error, or empty when its MPD is reachable."""
    stream_url = channel.get("stream_url", "")
    if not stream_url or not stream_url.lower().startswith(("http://", "https://")):
        return "Sunnxt MPD URL missing."
    try:
        response = requests.get(stream_url, timeout=15, allow_redirects=True)
        if response.status_code >= 400:
            return f"Sunnxt upstream HTTP {response.status_code}."
        body = response.text.lstrip()
        if "<MPD" not in body[:2000] and "<?xml" not in body[:2000]:
            return "Sunnxt upstream did not return a valid DASH MPD."
        return ""
    except requests.RequestException as exc:
        return f"Sunnxt upstream request failed: {exc}"


def _parse_dash_duration(value: str) -> int | None:
    """Parse the simple PT... ISO-8601 durations used by DASH MPDs."""
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
        str(value or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    return round(
        float(match.group("hours") or 0) * 3600
        + float(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )


def _inspect_dash_manifest(manifest: str) -> dict:
    """Summarize the DVR window and segment timelines in a DASH MPD."""
    namespace = {"dash": "urn:mpeg:dash:schema:mpd:2011"}
    root = ET.fromstring(manifest)
    timelines = root.findall(".//dash:SegmentTimeline", namespace)
    segment_counts = []
    open_repeat = False
    for timeline in timelines:
        count = 0
        for segment in timeline.findall("dash:S", namespace):
            repeat = int(segment.attrib.get("r", "0"))
            if repeat < 0:
                open_repeat = True
                count += 1
            else:
                count += repeat + 1
        segment_counts.append(count)
    return {
        "type": root.attrib.get("type", "unknown"),
        "minimum_update": root.attrib.get("minimumUpdatePeriod", ""),
        "timeshift_seconds": _parse_dash_duration(
            root.attrib.get("timeShiftBufferDepth", "")
        ),
        "timeline_count": len(timelines),
        "segment_count": max(segment_counts or [0]),
        "open_repeat": open_repeat,
        "has_segment_template": bool(
            root.findall(".//dash:SegmentTemplate", namespace)
        ),
        "has_segment_list": bool(root.findall(".//dash:SegmentList", namespace)),
    }


def _fetch_dash_manifest(channel: dict) -> str:
    headers = {}
    if channel.get("cookie"):
        headers["Cookie"] = channel["cookie"]
    if channel.get("user_agent"):
        headers["User-Agent"] = channel["user_agent"]
    response = requests.get(channel["stream_url"], headers=headers, timeout=15)
    response.raise_for_status()
    return response.text


def _expand_dash_timeline(timeline):
    """Expand DASH S/r entries into (timestamp, duration) pairs."""
    segments = []
    current_time = None
    entries = list(timeline.findall("{urn:mpeg:dash:schema:mpd:2011}S"))
    for index, entry in enumerate(entries):
        if entry.get("t") is not None:
            current_time = int(entry.get("t"))
        if current_time is None:
            current_time = 0
        duration = int(entry.get("d"))
        repeat = int(entry.get("r", "0"))
        if repeat < 0:
            next_time = None
            for future in entries[index + 1:]:
                if future.get("t") is not None:
                    next_time = int(future.get("t"))
                    break
            repeat = (
                max(0, (next_time - current_time) // duration - 1)
                if next_time is not None else 0
            )
        for _ in range(repeat + 1):
            segments.append((current_time, duration))
            current_time += duration
    return segments


def _format_dash_duration(seconds: float) -> str:
    seconds = max(0, seconds)
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis = 0
    if millis:
        return f"PT{whole}.{millis:03d}S"
    return f"PT{whole}S"


def _create_static_dash_mpd(
    channel: dict,
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
    output_path: str,
) -> tuple[str, int, str]:
    """Create a static MPD containing a requested portion of the DVR window."""
    manifest = _fetch_dash_manifest(channel)
    namespace_uri = "urn:mpeg:dash:schema:mpd:2011"
    namespace = {"dash": namespace_uri}
    root = ET.fromstring(manifest)
    now_local = datetime.now(IST)
    start_local = now_local.replace(
        hour=start_h, minute=start_m, second=0, microsecond=0
    )
    end_local = now_local.replace(
        hour=end_h, minute=end_m, second=0, microsecond=0
    )
    if end_local <= start_local:
        end_local += timedelta(days=1)
    requested_start = start_local.timestamp()
    requested_end = end_local.timestamp()

    timeline_templates = []
    for adaptation in root.findall(".//dash:AdaptationSet", namespace):
        template = adaptation.find("dash:SegmentTemplate", namespace)
        if template is None:
            continue
        for timeline in template.findall("dash:SegmentTimeline", namespace):
            timeline_templates.append((template, timeline))
    timelines = [timeline for _, timeline in timeline_templates]
    if not timelines:
        raise ValueError("This MPD does not contain a SegmentTimeline.")

    available_ranges = []
    selected_duration = 0.0
    for segment_template, timeline in timeline_templates:
        timescale = int(segment_template.get("timescale", "1"))
        segments = _expand_dash_timeline(timeline)
        if not segments:
            continue
        available_ranges.append((
            segments[0][0] / timescale,
            (segments[-1][0] + segments[-1][1]) / timescale,
        ))
        selected = [
            item for item in segments
            if item[0] / timescale < requested_end
            and (item[0] + item[1]) / timescale > requested_start
        ]
        if not selected:
            continue
        selected_duration = max(
            selected_duration,
            (selected[-1][0] + selected[-1][1] - selected[0][0]) / timescale,
        )
        timeline.clear()
        for index, (timestamp, duration) in enumerate(selected):
            attrs = {"d": str(duration)}
            if index == 0:
                attrs["t"] = str(timestamp)
            ET.SubElement(timeline, f"{{{namespace_uri}}}S", attrs)
        segment_template.set("presentationTimeOffset", str(selected[0][0]))

    if not available_ranges:
        raise ValueError("The MPD timeline is empty.")
    available_start = min(item[0] for item in available_ranges)
    available_end = max(item[1] for item in available_ranges)
    # If only the END is beyond the DVR edge → raise so caller can wait.
    # If the START is before the window → silently clamp; record what's available.
    if requested_end > available_end + 3:
        available_start_local = datetime.fromtimestamp(available_start, tz=IST)
        available_end_local = datetime.fromtimestamp(available_end, tz=IST)
        raise ValueError(
            "The requested time is outside the DVR window. Available range: "
            f"{available_start_local:%I:%M %p} - {available_end_local:%I:%M %p}."
        )
    if selected_duration <= 0:
        raise ValueError("No segments were found for the requested time.")

    root.set("type", "static")
    for attribute in (
        "availabilityStartTime", "publishTime", "minimumUpdatePeriod",
        "timeShiftBufferDepth",
    ):
        root.attrib.pop(attribute, None)
    root.set("mediaPresentationDuration", _format_dash_duration(selected_duration))
    base_url = urljoin(channel["stream_url"], "dash/")
    for base in root.findall(".//dash:BaseURL", namespace):
        base.text = base_url
    ET.register_namespace("", namespace_uri)
    tree = ET.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path, max(1, round(selected_duration)), _format_dash_duration(selected_duration)


def _dvr_manifest_wait_seconds(error, past_range):
    """Wait briefly when only the requested end is ahead of the live DVR edge."""
    match = re.search(
        r"Available range:\s*(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
        r"(\d{1,2}:\d{2}\s*[AP]M)",
        str(error),
        re.IGNORECASE,
    )
    if not match:
        return 0
    available_start = parse_time(match.group(1))
    available_end = parse_time(match.group(2))
    if available_start[0] is None or available_end[0] is None:
        return 0

    def minutes(hour, minute):
        return hour * 60 + minute

    requested_start = minutes(past_range[0], past_range[1])
    requested_end = minutes(past_range[2], past_range[3])
    if requested_end <= requested_start:
        requested_end += 24 * 60
    available_start_minutes = minutes(*available_start)
    available_end_minutes = minutes(*available_end)
    if available_end_minutes <= available_start_minutes:
        available_end_minutes += 24 * 60
    if requested_start < available_start_minutes:
        return 0

    end_gap = requested_end - available_end_minutes
    if 0 < end_gap <= 3:
        return min(180, end_gap * 60 + 8)
    return 0


class _DashProxyHandler(BaseHTTPRequestHandler):
    """Serve a trimmed local MPD and authenticated DASH children."""

    def do_GET(self):
        proxy = self.server.dash_proxy
        request_path = urlsplit(self.path).path
        if request_path == "/selected_dvr.mpd":
            try:
                with open(proxy["mpd_path"], "rb") as manifest_file:
                    payload = manifest_file.read()
                content_type = "application/dash+xml"
            except OSError:
                self.send_error(404)
                return
        elif request_path.startswith("/dash/"):
            relative_path = unquote(request_path[len("/dash/"):])
            upstream_url = urljoin(proxy["base_url"], relative_path)
            try:
                response = requests.get(
                    upstream_url,
                    headers=proxy["headers"],
                    timeout=(10, 45),
                )
                payload = response.content
                if response.status_code != 200:
                    self.send_response(response.status_code)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
            except requests.RequestException as exc:
                self.send_error(502, str(exc)[:160])
                return
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def _start_dash_proxy(mpd_path: str, channel: dict):
    """Start an authenticated local proxy for FFmpeg's nested DASH requests."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashProxyHandler)
    server.dash_proxy = {
        "mpd_path": mpd_path,
        "base_url": urljoin(channel["stream_url"], "dash/"),
        "headers": {
            key: value for key, value in {
                "Cookie": channel.get("cookie", ""),
                "User-Agent": channel.get("user_agent", ""),
            }.items() if value
        },
    }
    thread = threading.Thread(
        target=server.serve_forever,
        name="dash-recording-proxy",
        daemon=True,
    )
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/selected_dvr.mpd"


def _set_static_mpd_base_url(mpd_path: str, base_url: str):
    namespace_uri = "urn:mpeg:dash:schema:mpd:2011"
    namespace = {"dash": namespace_uri}
    tree = ET.parse(mpd_path)
    root = tree.getroot()
    for base in root.findall(".//dash:BaseURL", namespace):
        base.text = base_url
    ET.register_namespace("", namespace_uri)
    tree.write(mpd_path, encoding="utf-8", xml_declaration=True)


async def _test_live_start_index(channel: dict, start_index: int) -> tuple[str, str]:
    """Test the HLS-only option against the current channel input."""
    stream_url = channel.get("stream_url", "")
    cookie = channel.get("cookie", "")
    user_agent = channel.get("user_agent", "")
    license_url = channel.get("license_key", "")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if cookie or user_agent:
        headers = ""
        if cookie:
            headers += f"Cookie: {cookie}\r\n"
        if user_agent:
            headers += f"User-Agent: {user_agent}\r\n"
        cmd += ["-headers", headers]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if cookie:
        cmd += ["-cookies", cookie]
    if license_url:
        key_hex = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_cenc_key, stream_url, license_url, cookie, user_agent
        )
        if key_hex:
            cmd += ["-cenc_decryption_key", key_hex]
    cmd += [
        "-live_start_index", str(start_index),
        "-rw_timeout", "8000000",
        "-i", stream_url,
        "-map", "0:v:0",
        "-t", "1",
        "-f", "null",
        "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "timeout", "The FFmpeg test did not complete within 15 seconds."
    except OSError as exc:
        return "error", str(exc)

    error_text = stderr.decode(errors="replace").strip()
    if proc.returncode == 0:
        return "accepted", "FFmpeg ne option accept kiya."
    if "live_start_index" in error_text.lower():
        return "unsupported", error_text[-500:]
    return "failed", error_text[-500:] or f"FFmpeg exit code {proc.returncode}."


def get_epg(channel_id, offset=0):
    url = f"https://jiotvapi.cdn.jio.com/apis/v1.3/getepg/get?offset={offset}&channel_id={channel_id}&langId=6"
    headers = {"user-agent": "okhttp/4.12.13", "Accept-Encoding": "gzip"}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        try:
            data = gzip.decompress(resp.content)
            return json.loads(data)
        except Exception:
            try:
                return resp.json()
            except Exception:
                return None
    return None

def parse_time(time_str):
    time_str = time_str.strip().upper()
    for fmt in ["%I:%M%p", "%I:%M %p", "%H:%M"]:
        try:
            t = datetime.strptime(time_str, fmt)
            return t.hour, t.minute
        except ValueError:
            continue
    return None, None


def _load_scheduled_recordings():
    global SCHEDULED_RECORDINGS
    try:
        if not os.path.exists(SCHEDULED_RECORDINGS_FILE):
            SCHEDULED_RECORDINGS = []
            return
        with open(SCHEDULED_RECORDINGS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        SCHEDULED_RECORDINGS = data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        logger.exception("Scheduled recordings could not be loaded.")
        SCHEDULED_RECORDINGS = []


def _save_scheduled_recordings():
    temporary = f"{SCHEDULED_RECORDINGS_FILE}.{_secrets.token_hex(6)}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(SCHEDULED_RECORDINGS, handle, indent=2)
        os.replace(temporary, SCHEDULED_RECORDINGS_FILE)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass
        logger.exception("Scheduled recordings could not be saved.")


def _schedule_datetime(value: str) -> datetime:
    value = " ".join(str(value).strip().split())
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except ValueError:
        pass
    for fmt in (
        "%d/%m/%Y %I:%M%p",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(value.upper(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    raise ValueError("Date/time format must be DD/MM/YYYY HH:MMAM.")


def _schedule_display_datetime(value: str) -> str:
    return _schedule_datetime(value).strftime("%d/%m/%Y %I:%M%p")


class _ScheduledStatusMessage:
    """Small Message-compatible proxy used when a schedule survives a restart."""

    def __init__(self, bot, chat_id: int, message_id: int):
        self._bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id)

    async def edit_text(self, text, **kwargs):
        return await self._bot.edit_message_text(
            chat_id=self.chat_id,
            message_id=self.message_id,
            text=text,
            **kwargs,
        )


def _scheduled_recording_text(item: dict) -> str:
    source = {
        "airtel": "Airtel",
        "sunnxt": "Sunnxt",
    }.get(item.get("source"), "DishTV")
    return (
        "✅ *Recording Scheduled*\n\n"
        f"📡 Source: *{source}*\n"
        f"📺 Channel: *{item.get('channel_name', 'Unknown')}*\n\n"
        f"🟢 Start: `{_schedule_display_datetime(item['start'])}`\n"
        f"🔴 End: `{_schedule_display_datetime(item['end'])}`\n"
        f"⏱ Duration: `{_fmt_time(item.get('duration_seconds', 0))}`\n\n"
        f"🆔 `{item.get('id', '')}`"
    )


def _schedule_raw_arguments(update: Update) -> str:
    text = (getattr(update.effective_message, "text", "") or "").strip()
    return re.sub(r"^/schedule(?:@\w+)?\s*", "", text, flags=re.IGNORECASE).strip()


def _parse_schedule_arguments(update: Update) -> tuple[str, str, str, str]:
    raw = _schedule_raw_arguments(update)
    source = "dishtv"
    source_match = re.search(
        r"(?<!\S)-(dishtv|airtel|sunnxt)(?=\s|$)", raw, re.IGNORECASE
    )
    if source_match:
        source = source_match.group(1).lower()
        raw = (raw[:source_match.start()] + raw[source_match.end():]).strip()

    date_part = r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*[APap][Mm]"
    match = re.fullmatch(
        rf"-c\s+(?:\"([^\"]+)\"|(.+?))\s+-t\s+"
        rf"({date_part})\s*-\s*({date_part})",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "Usage: `/schedule [-dishtv|-airtel|-sunnxt] -c \"Channel Name\" "
            "-t DD/MM/YYYY HH:MMAM - DD/MM/YYYY HH:MMPM`"
        )
    channel_name = (match.group(1) or match.group(2) or "").strip()
    return source, channel_name, match.group(3).strip(), match.group(4).strip()


async def _run_scheduled_recording(application, item: dict):
    schedule_id = item["id"]
    status_message = None
    try:
        start = _schedule_datetime(item["start"])
        end = _schedule_datetime(item["end"])
        now = datetime.now(IST)
        if now < start:
            await asyncio.sleep((start - now).total_seconds())
        now = datetime.now(IST)
        if now >= end:
            item["status"] = "expired"
            _save_scheduled_recordings()
            return

        # Respect the same global processing limit as interactive recordings.
        # Owner/admin schedules are allowed to bypass that limit, matching the
        # existing command behavior.
        if not (is_owner(int(item["user_id"])) or is_admin(int(item["user_id"]))):
            while _active_processes >= MAX_PROCESSES:
                if datetime.now(IST) >= end:
                    item["status"] = "expired"
                    _save_scheduled_recordings()
                    return
                item["status"] = "waiting_for_slot"
                _save_scheduled_recordings()
                await asyncio.sleep(10)

        item["status"] = "running"
        item["started_at"] = now.isoformat()
        _save_scheduled_recordings()

        status_message = _ScheduledStatusMessage(
            application.bot, int(item["chat_id"]), int(item["message_id"])
        )
        try:
            await status_message.edit_text(
                "⏺ *Scheduled recording is starting…*\n\n"
                f"📺 {item['channel_name']}\n"
                f"📡 {item.get('source', 'dishtv').title()}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            status_message = await application.bot.send_message(
                chat_id=int(item["chat_id"]),
                text=(
                    "⏺ Scheduled recording is starting…\n\n"
                    f"📺 {item['channel_name']}"
                ),
            )
            item["message_id"] = status_message.message_id
            _save_scheduled_recordings()

        channel = find_channel(
            item["channel_name"], force_refresh=True, source=item["source"]
        )
        if not channel:
            raise RuntimeError(
                f"{item['source'].title()} channel `{item['channel_name']}` "
                "was not found in the playlist."
            )

        if item.get("source") in {"airtel", "sunnxt"}:
            await status_message.edit_text(
                _ott_probe_text(item["source"]),
                parse_mode=ParseMode.MARKDOWN,
            )

        remaining_seconds = max(1, int((end - datetime.now(IST)).total_seconds()))
        user = SimpleNamespace(
            id=int(item["user_id"]),
            username=item.get("username") or None,
            first_name=item.get("first_name") or "User",
        )
        chat = SimpleNamespace(id=int(item["chat_id"]))
        fake_message = status_message
        fake_update = SimpleNamespace(
            effective_user=user,
            effective_chat=chat,
            effective_message=fake_message,
            message=fake_message,
        )
        fake_context = SimpleNamespace(bot=application.bot, user_data={})
        await run_recording_job(
            fake_update,
            fake_context,
            channel,
            remaining_seconds,
            f"{_schedule_display_datetime(item['start'])} - "
            f"{_schedule_display_datetime(item['end'])}",
            "16:9",
            "576p",
            0,
            "multi",
            [],
            status_message,
            past_range=None,
        )
        item["status"] = "completed"
        item["completed_at"] = datetime.now(IST).isoformat()
    except asyncio.CancelledError:
        item["status"] = "cancelled"
        raise
    except Exception as exc:
        logger.exception("Scheduled recording %s failed.", schedule_id)
        item["status"] = "failed"
        item["error"] = str(exc)[:900]
        if status_message:
            try:
                await status_message.edit_text(
                    f"❌ Scheduled recording failed\n\n{str(exc)[:900]}"
                )
            except Exception:
                pass
    finally:
        item["finished_at"] = datetime.now(IST).isoformat()
        _save_scheduled_recordings()
        SCHEDULE_RUNTIME_TASKS.pop(schedule_id, None)


async def _scheduled_recording_manager(application):
    while True:
        now = datetime.now(IST)
        for item in list(SCHEDULED_RECORDINGS):
            if item.get("status") not in {"pending", "running"}:
                continue
            try:
                end = _schedule_datetime(item["end"])
            except (KeyError, ValueError):
                item["status"] = "failed"
                item["error"] = "Stored schedule has an invalid date/time."
                _save_scheduled_recordings()
                continue
            if now >= end and item["id"] not in SCHEDULE_RUNTIME_TASKS:
                item["status"] = "expired"
                _save_scheduled_recordings()
                continue
            if item["id"] not in SCHEDULE_RUNTIME_TASKS:
                task = asyncio.create_task(_run_scheduled_recording(application, item))
                SCHEDULE_RUNTIME_TASKS[item["id"]] = task
        await asyncio.sleep(2)


_load_scheduled_recordings()


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Persist a future recording window and let the scheduler run it."""
    uid = update.effective_user.id
    if BOT_MODE != "public" and not (
        is_owner(uid) or is_admin(uid) or is_premium(uid) or is_verified(uid)
    ):
        await update.message.reply_text(
            "🔐 *Verification is required to access this command.*\n\n"
            "Command: `/verify`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        source, channel_name, start_raw, end_raw = _parse_schedule_arguments(update)
        start = _schedule_datetime(start_raw)
        end = _schedule_datetime(end_raw)
    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {exc}\n\n"
            "Example:\n"
            "`/schedule -sunnxt -c \"Sun TV HD\" -t "
            "09/09/2026 10:29AM - 09/09/2026 11:16AM`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    now = datetime.now(IST)
    if start <= now:
        await update.message.reply_text(
            "❌ The start date/time must be in the future.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if end <= start:
        await update.message.reply_text(
            "❌ The end date/time must be after the start date/time.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    duration_seconds = int((end - start).total_seconds())
    if duration_seconds > REC_LIMIT_SECONDS:
        uid = update.effective_user.id
        if not (is_owner(uid) or is_premium(uid)):
            await update.message.reply_text(
                f"❌ Scheduled duration limit exceeded.\n\n"
                f"Maximum: *{REC_LIMIT_SECONDS // 60} minutes*",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    try:
        channel = find_channel(channel_name, force_refresh=True, source=source)
    except requests.RequestException:
        channel = None
    if not channel:
        provider = {
            "airtel": "Airtel Next",
            "sunnxt": "Sunnxt Next",
        }.get(source, "DishTV Next")
        await update.message.reply_text(
            f"❌ {provider} channel *{channel_name}* not found.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    schedule_id = _secrets.token_hex(6)
    item = {
        "id": schedule_id,
        "user_id": update.effective_user.id,
        "username": update.effective_user.username or "",
        "first_name": update.effective_user.first_name or "User",
        "chat_id": update.effective_chat.id,
        "message_id": 0,
        "source": source,
        "channel_name": channel["channel_name"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_seconds": duration_seconds,
        "status": "pending",
        "created_at": now.isoformat(),
    }
    SCHEDULED_RECORDINGS.append(item)
    _save_scheduled_recordings()
    status = await update.message.reply_text(
        _scheduled_recording_text(item),
        parse_mode=ParseMode.MARKDOWN,
    )
    item["message_id"] = status.message_id
    _save_scheduled_recordings()


def _requested_range_datetimes(time_range):
    """Resolve a parsed HH:MM range against today's local date."""
    now = datetime.now(IST)
    start_h, start_m, end_h, end_m = time_range
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    return start, end, now


def find_program_in_epg(channel_id, start_h, start_m, end_h, end_m):
    for offset in [0, -1, 1]:
        epg = get_epg(channel_id, offset)
        if not epg:
            continue
        for program in epg.get("epg", []):
            try:
                start_ts = int(program.get("startEpoch", 0))
                end_ts = int(program.get("endEpoch", 0))
                p_start = datetime.fromtimestamp(start_ts, tz=IST)
                p_end = datetime.fromtimestamp(end_ts, tz=IST)
                if p_start.hour == start_h and p_start.minute == start_m:
                    return program, p_start, p_end
                if p_end.hour == end_h and p_end.minute == end_m:
                    return program, p_start, p_end
            except Exception:
                continue
    return None, None, None

def jio_headers_from_creds(creds):
    return {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }

def send_jio_otp_api(mobile):
    url = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/send"
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }
    payload = {"number": base64.b64encode(f"+91{mobile}".encode()).decode()}
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    if resp.status_code == 204:
        return {"status": "success", "message": "OTP sent successfully"}
    try:
        data = resp.json()
        return {"status": "error", "message": data.get("message", f"Error code {resp.status_code}")}
    except Exception:
        return {"status": "error", "message": f"Unknown error: {resp.status_code}"}

def verify_jio_otp_api(mobile, otp):
    url = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/verify"
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }
    payload = {
        "number": base64.b64encode(f"+91{mobile}".encode()).decode(),
        "otp": otp,
        "deviceInfo": {
            "consumptionDeviceName": "RMX1945",
            "info": {
                "type": "android",
                "platform": {"name": "RMX1945"},
                "androidId": "tsjiotvbot123456"
            }
        }
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    try:
        data = resp.json()
    except Exception:
        return {"status": "error", "message": f"Parse error: {resp.status_code}"}
    if data.get("ssoToken"):
        save_credentials(data, mobile)
        return {"status": "success", "message": "Login successful!"}
    msg = data.get("message", "")
    if not msg and "errors" in data and data["errors"]:
        msg = data["errors"][-1].get("message", "")
    return {"status": "error", "message": msg or f"Verify failed: {resp.status_code}"}

# ── Stream URL builders ────────────────────────

def get_stream_url(channel_id, creds=None):
    """Return (stream_url, cookie, user_agent) for a channel from the M3U playlist."""
    channels = get_channels()
    for ch in channels:
        if str(ch["channel_id"]) == str(channel_id):
            return ch.get("stream_url"), ch.get("cookie", ""), ch.get("user_agent", "")
    return None, "", ""


def _playback_headers(creds, channel_id):
    user = creds.get("sessionAttributes", {}).get("user", {})
    subscriber_id = user.get("subscriberId", "")
    return {
        "Host": "jiotvapi.media.jio.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "appkey": "NzNiMDhlYzQyNjJm",
        "channel_id": str(channel_id),
        "userid": subscriber_id,
        "crmid": subscriber_id,
        "deviceId": creds.get("deviceId", ""),
        "devicetype": "phone",
        "isott": "true",
        "languageId": "6",
        "lbcookie": "1",
        "os": "android",
        "dm": "Xiaomi 22101316UP",
        "osversion": "14",
        "accesstoken": creds.get("authToken", ""),
        "subscriberid": subscriber_id,
        "uniqueId": user.get("unique", ""),
        "usergroup": "tvYR7NSNn7rymo3F",
        "User-Agent": "okhttp/4.12.13",
        "versionCode": "452",
    }


def _playback_value(result, *names):
    """Find a playback field in the API's occasionally varying response shape."""
    if isinstance(result, dict):
        for name in names:
            value = result.get(name)
            if isinstance(value, str) and value:
                return value
        for value in result.values():
            found = _playback_value(value, *names)
            if found:
                return found
    return ""


def get_authenticated_live_stream(channel):
    """Get a fresh signed live stream using the logged-in JioTV session."""
    creds = load_credentials()
    channel_id = channel.get("channel_id")
    if not creds or not creds.get("authToken") or not channel_id:
        return None

    cache_key = str(channel_id)
    cached = _authenticated_stream_cache.get(cache_key)
    if cached and time.time() - cached["created_at"] < _AUTHENTICATED_STREAM_CACHE_TTL:
        return dict(cached["stream"])

    user = creds.get("sessionAttributes", {}).get("user", {})
    payload = (
        "stream_type=Live"
        f"&channel_id={channel_id}"
        "&programId=0&showtime=000000&srno=0&begin=&end="
    )
    try:
        response = requests.post(
            "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6",
            data=payload,
            headers=_playback_headers(creds, channel_id),
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return None
        if str(data.get("code", 200)) not in ("200", "None"):
            return None
        result = data.get("result", data)
        stream_url = (
            result if isinstance(result, str)
            else _playback_value(
                result, "url", "streamUrl", "stream_url", "playbackUrl",
                "playback_url", "manifestUrl", "manifest_url",
            )
        )
        if not stream_url:
            return None

        cookie = (
            _playback_value(result, "cookie", "cookies", "httpCookie")
            or "; ".join(
                f"{key}={value}" for key, value in response.cookies.get_dict().items()
            )
        )
        user_agent = _playback_value(result, "userAgent", "user_agent") or channel.get(
            "user_agent", "JioTV.Plus/2.3.1_2041 (Linux;Android 14) AndroidXMedia3/1.4.0"
        )
        license_key = _playback_value(
            result, "licenseKey", "license_key", "licenseUrl", "license_url"
        )
        stream = {
            "stream_url": stream_url,
            "cookie": cookie,
            "user_agent": user_agent,
            "license_key": license_key or channel.get("license_key", ""),
        }
        _authenticated_stream_cache[cache_key] = {
            "created_at": time.time(),
            "stream": stream,
        }
        return dict(stream)
    except (requests.RequestException, ValueError, TypeError):
        return None


def get_catchup_url(channel_id, srno, begin, end, creds):
    access_token = creds.get("authToken", "")
    crm = creds.get("sessionAttributes", {}).get("user", {}).get("subscriberId", "")
    unique_id = creds.get("sessionAttributes", {}).get("user", {}).get("unique", "")
    device_id = creds.get("deviceId", "")
    post_data = f"stream_type=Catchup&channel_id={channel_id}&programId={srno}&showtime=000000&srno={srno}&begin={begin}&end={end}"
    headers = _playback_headers(creds, channel_id)
    headers["srno"] = str(srno)
    resp = requests.post(
        "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6",
        data=post_data, headers=headers, timeout=10
    )
    data = resp.json()
    if data.get("code") == 200:
        return data.get("result")
    return None

# ── Role decorators ────────────────────────────

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid):
            await update.message.reply_text("❌ Only the owner can use this command.")
            return
        return await func(update, context)
    return wrapper

def owner_admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid) and not is_admin(uid):
            await update.message.reply_text("❌ Only the owner or an admin can use this command.")
            return
        return await func(update, context)
    return wrapper

def require_login(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not load_credentials():
            await update.message.reply_text(
                "❌ JioTV login is not configured.\nFirst use `/login <mobile>` and verify the OTP.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        return await func(update, context)
    return wrapper

def require_verification(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if BOT_MODE == "public":
            return await func(update, context)
        if is_owner(uid) or is_admin(uid) or is_premium(uid):
            return await func(update, context)
        if not is_verified(uid):
            await update.message.reply_text(
                "🔐 *Verification is required to access this command.*\n\n"
                "Command: `/verify`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        return await func(update, context)
    return wrapper


def _user_cookies_path(user_id: int) -> str:
    return os.path.join(COOKIES_FOLDER, f"{user_id}.txt")


def _user_has_cookies(user_id: int) -> bool:
    path = _user_cookies_path(user_id)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _cookies_summary(user_id: int) -> str:
    """Return safe cookie metadata without exposing cookie values."""
    path = _user_cookies_path(user_id)
    if not os.path.isfile(path):
        return "❌ No cookies stored."
    try:
        size = os.path.getsize(path)
        uploaded = datetime.fromtimestamp(os.path.getmtime(path), tz=IST)
        cookie_lines = []
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                fields = stripped.split("\t")
                if len(fields) >= 7:
                    cookie_lines.append(fields)
        hosts = sorted({
            fields[0].lstrip(".")
            for fields in cookie_lines
            if fields[0].strip()
        })
        host_text = ", ".join(hosts[:8]) or "Unknown"
        if len(hosts) > 8:
            host_text += f" (+{len(hosts) - 8} more)"
        return (
            "✅ Cookies stored.\n\n"
            f"• Cookie lines: `{len(cookie_lines)}`\n"
            f"• File size: `{size:,} bytes`\n"
            f"• Hosts: `{host_text}`\n"
            f"• Uploaded: `{uploaded.strftime('%Y-%m-%d %H:%M %Z')}`"
        )
    except OSError:
        return "⚠️ Cookies are stored, but their status could not be read."


def _validate_netscape_cookie_file(path: str) -> tuple[bool, str]:
    """Validate the header and tab-separated fields of a Netscape cookie file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return False, "The cookie file could not be read."
    if not content.strip():
        return False, "The cookie file is empty."
    header = content[:4096]
    if "# Netscape HTTP Cookie File" not in header:
        return False, "The file must be in Netscape cookies.txt format."
    valid_cookie_lines = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.split("\t")) < 7:
            continue
        valid_cookie_lines += 1
    if not valid_cookie_lines:
        return False, "No valid Netscape cookie entries were found."
    return True, ""


@require_verification
async def set_cookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open a short-lived window for the user's cookies.txt upload."""
    user_id = update.effective_user.id
    PENDING_COOKIE_UPLOADS[user_id] = time.time()
    await update.message.reply_text(
        "🍪 *OTT Cookies Upload*\n\n"
        "Send your `cookies.txt` file now.\n"
        "It must be in Netscape HTTP Cookie File format.\n\n"
        "⏱ Upload window: 5 minutes",
        parse_mode=ParseMode.MARKDOWN,
    )


@require_verification
async def cookies_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        _cookies_summary(update.effective_user.id),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_verification
async def del_cookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    PENDING_COOKIE_UPLOADS.pop(update.effective_user.id, None)
    path = _user_cookies_path(update.effective_user.id)
    try:
        if os.path.exists(path):
            os.remove(path)
            await update.message.reply_text("✅ Stored cookies deleted.")
        else:
            await update.message.reply_text("❌ No cookies stored.")
    except OSError:
        logger.exception("Cookie deletion failed for user %s", update.effective_user.id)
        await update.message.reply_text("❌ Cookies could not be deleted.")


async def cookies_document_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Accept a cookies.txt document only after /set_cookies."""
    user = update.effective_user
    document = update.message.document if update.message else None
    if not user or not document:
        return
    if not (
        is_owner(user.id)
        or is_admin(user.id)
        or is_premium(user.id)
        or is_verified(user.id)
    ):
        return
    started_at = PENDING_COOKIE_UPLOADS.get(user.id)
    if started_at is None:
        return
    if time.time() - started_at > COOKIE_UPLOAD_TTL_SECONDS:
        PENDING_COOKIE_UPLOADS.pop(user.id, None)
        await update.message.reply_text(
            "⏱ Cookie upload window expired. Run `/set_cookies` again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    PENDING_COOKIE_UPLOADS.pop(user.id, None)

    filename = (document.file_name or "").lower()
    if not (filename.endswith(".txt") or filename.endswith(".cookies")):
        await update.message.reply_text(
            "❌ Please send a `cookies.txt` file in Netscape format.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if document.file_size and document.file_size > MAX_COOKIE_FILE_BYTES:
        await update.message.reply_text("❌ Cookie file is too large. Maximum size is 2 MB.")
        return

    os.makedirs(COOKIES_FOLDER, exist_ok=True)
    destination = _user_cookies_path(user.id)
    temporary = f"{destination}.{_secrets.token_hex(8)}.tmp"
    status = await update.message.reply_text("⬇️ Downloading and validating cookies...")
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(custom_path=temporary)
        if os.path.getsize(temporary) > MAX_COOKIE_FILE_BYTES:
            raise ValueError("Cookie file is too large. Maximum size is 2 MB.")
        valid, error = _validate_netscape_cookie_file(temporary)
        if not valid:
            raise ValueError(error)
        os.replace(temporary, destination)
        await status.edit_text(
            f"✅ Cookies saved successfully.\n\n{_cookies_summary(user.id)}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValueError as exc:
        try:
            os.remove(temporary)
        except OSError:
            pass
        await status.edit_text(f"❌ {exc}")
    except Exception:
        logger.exception("Cookie upload failed for user %s", user.id)
        try:
            os.remove(temporary)
        except OSError:
            pass
        await status.edit_text("❌ Cookie upload failed. Please try again.")


@require_verification
async def di_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnose a DASH manifest and optionally validate a DVR time range."""
    full_text = " ".join(context.args).strip()
    timerange_match = re.match(
        r"^(.+?)\s+-t\s+(\d{1,2}:\d{2}\s*[APap][Mm])\s*-\s*"
        r"(\d{1,2}:\d{2}\s*[APap][Mm])$",
        full_text,
    )
    selected_range = None
    if timerange_match:
        channel_name = timerange_match.group(1).strip()
        start_time_str = timerange_match.group(2).strip()
        end_time_str = timerange_match.group(3).strip()
        start_h, start_m = parse_time(start_time_str)
        end_h, end_m = parse_time(end_time_str)
        if start_h is None or end_h is None:
            await update.message.reply_text(
                "❌ Invalid time format. Example: `/di Pogo -t 07:15PM - 07:20PM`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        selected_range = (start_h, start_m, end_h, end_m)
        range_label = f"{start_time_str} - {end_time_str}"
    else:
        channel_name = full_text or "Pogo"
        range_label = ""
    channel = refresh_channel(find_channel(channel_name, force_refresh=True) or {})
    if not channel.get("stream_url"):
        await update.message.reply_text(
            f"❌ MPD not found for `{channel_name}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    status_message = await update.message.reply_text(
        f"🔎 *{channel['channel_name']} MPD diagnostic…*",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        manifest = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_dash_manifest, channel
        )
        info = _inspect_dash_manifest(manifest)
        test_status, test_detail = await _test_live_start_index(channel, -600)
        selected_seconds = None
        if selected_range:
            with tempfile.NamedTemporaryFile(suffix=".mpd") as selected_mpd:
                _, selected_seconds, _ = _create_static_dash_mpd(
                    channel, *selected_range, selected_mpd.name
                )
    except (ET.ParseError, requests.RequestException, ValueError) as exc:
        await status_message.edit_text(
            f"❌ MPD diagnostic failed:\n`{str(exc)[:800]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    timeshift = (
        f"{info['timeshift_seconds'] // 60} min"
        if info["timeshift_seconds"] is not None
        else "unknown"
    )
    if test_status == "accepted":
        option_result = "✅ `-live_start_index -600` accepted"
    elif test_status == "unsupported":
        option_result = (
            "❌ `-live_start_index` unsupported for this MPD "
            "(this is a DASH input, not HLS)"
        )
    elif test_status == "timeout":
        option_result = "⚠️ Option test timed out"
    else:
        option_result = f"❌ Option test failed: `{test_detail[:300]}`"

    range_result = ""
    if selected_range:
        range_result = (
            f"\n*DVR range test:*\n"
            f"🕐 `{range_label}`\n"
            f"✅ Selected segments: `{selected_seconds}s`\n"
        )

    await status_message.edit_text(
        f"📡 *{channel['channel_name']} MPD report*\n\n"
        f"Type: `{info['type']}`\n"
        f"DVR window: `{timeshift}`\n"
        f"Manifest update: `{info['minimum_update'] or 'unknown'}`\n"
        f"Timeline entries: `{info['timeline_count']}`\n"
        f"Available segments: `{info['segment_count']}`\n"
        f"SegmentTemplate: `{'yes' if info['has_segment_template'] else 'no'}`\n\n"
        f"💧 Watermark: `DishTV SMART+ (permanent default)`\n"
        f"{range_result}\n"
        f"*live_start_index test:*\n{option_result}\n\n"
        "Pogo uses an MPEG-DASH MPD source. "
        "`-live_start_index` is for HLS/M3U8; "
        "normal `/rec` records from the live edge. "
        "For a past range, use `/rec Pogo -t START - END`.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _auto_delete(bot, chat_id, message_id, delay):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def check_process_slot(update: Update) -> bool:
    """Return True if a processing slot is free, False (after sending busy msg) if all slots taken."""
    global _active_processes
    uid = update.effective_user.id
    target_message = update.effective_message
    if is_owner(uid) or is_admin(uid):
        return True
    if _active_processes >= MAX_PROCESSES:
        await target_message.reply_text(
            f"⚠️ *Server Busy ({_active_processes}/{MAX_PROCESSES} Processes Running)*\n\n"
            "All processing slots are currently in use.\n\n"
            "⏳ Please wait a few minutes and try again.\n\n"
            "💎 Want instant access with higher limits and no waiting?\n"
            f"Upgrade to the Paid Bot.\n\n"
            f"👉 Contact: {PAID_BOT_CONTACT}",
            parse_mode=ParseMode.MARKDOWN
        )
        return False
    return True

# ── Bot commands ───────────────────────────────

@owner_only
async def public_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_bot_mode("public")
    await update.message.reply_text(
        "🌍 Bot Mode Updated\n\n"
        "✅ Public Mode Enabled\n\n"
        "Current Mode:\n"
        "Public"
    )


@owner_only
async def private_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_bot_mode("private")
    await update.message.reply_text(
        "🔒 Bot Mode Updated\n\n"
        "✅ Private Mode Enabled\n\n"
        "Current Mode:\n"
        "Private"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Handle deep link verification: /start verify_<user_id>_<token>
    if context.args and context.args[0].startswith("verify_"):
        parts = context.args[0].split("_", 2)
        if len(parts) == 3:
            _, uid_str, token = parts
            if uid_str == str(user.id):
                if mark_verified(user.id, token):
                    expiry_mins = VERIFICATION_EXPIRY_SECONDS // 60
                    await update.message.reply_text(
                        f"✅ *Verification Successful!*\n\n"
                        f"Access granted for *{expiry_mins} minutes*.\n"
                        f"You can now use `/rec`.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await update.message.reply_text(
                        "❌ *Verification failed.*\n"
                        "The link has expired or is invalid. Run `/verify` again.",
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                await update.message.reply_text("❌ This verification link is not intended for you.")
            return

    role = get_user_role(user.id)
    role_icon = {"owner": "👑", "admin": "👨\u200d✈\ufe0f", "user": "👤"}[role]

    text = (
        f"{role_icon} *DishTV Airtel Sunnxt ReBorn Bot*\n"
        f"Role: `{role.upper()}` | User: `{user.first_name}`\n\n"
        "*Commands:*\n"
        "🎞️ `/Qualitymax` — Convert the quality of a replied video\n"
        "🛡 `/verify` — Unlock access for 40 minutes\n"
        "📼 `/rec <channel> MM:SS` or `-t HH:MMAM - HH:MMPM`\n"
        "📡 Airtel: `/rec -airtel <channel> HH:MM:SS`\n"
         "📡 Sunnxt: `/rec -sunnxt <channel> HH:MM:SS`\n"
        "📋 `/channels` — Channels list\n"
        "🔍 `/search <name>` — Channel search\n"
        "ℹ\ufe0f `/myinfo` — View your account information\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🍪 *Cookies (OTT Login)*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "❌ Free Users: Not Allowed\n"
        "✅ Verify / Premium / Admin / Owner: Allowed\n\n"
        "• `/set_cookies` — Upload cookies.txt (Netscape format)\n"
        "• `/cookies_status` — Show stored cookies\n"
        "• `/del_cookies` — Delete stored cookies\n"
    )
    if role in ("owner", "admin"):
        text += "📢 `/broadcast <msg>` — Send a message to all users\n"
    if role == "owner":
        text += (
            "\n*Owner Commands:*\n"
            "🔑 `/login <mobile>` — Owner DishTV login\n"
            "🔐 `/otp <code>` — Verify the owner OTP\n"
            "🔢 `/addadmin <user_id>` — Admin add\n"
            "🗑 `/removeadmin <user_id>` — Admin remove\n"
            "👥 `/adminlist` — Admin list\n"
            "🌐 `/proxy` — Proxy URL (hidden)\n"
            "💾 `/setowner <user_id>` — Owner set\n"
        )
    text += (
        "\n*Example:*\n"
        "`/rec Pogo 01:00` ya `/rec Pogo -t 12:00PM - 01:00PM`\n"
         "`/rec -airtel Pogo 00:00:30 -t 00:00:30`\n"
         "`/rec -sunnxt Sony SAB 00:00:30`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role = get_user_role(user.id)
    creds = load_credentials()

    jio_mobile = "❌ Not logged in"
    expiry = "N/A"
    if creds:
        try:
            mobile = creds.get("sessionAttributes", {}).get("user", {}).get("mobile", "")
            name = creds.get("sessionAttributes", {}).get("user", {}).get("commonName", "")
            jio_mobile = f"{name} ({mobile})"
            jwt = creds.get("authToken", "")
            if jwt:
                parts = jwt.split(".")
                if len(parts) > 1:
                    payload = json.loads(base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                    exp = payload.get("exp", 0)
                    exp_dt = datetime.fromtimestamp(exp, tz=IST)
                    expiry = exp_dt.strftime("%d-%b-%Y %I:%M %p")
        except Exception:
            pass

    text = (
        f"👤 *User Info*\n"
        f"Name: `{user.first_name}`\n"
        f"ID: `{user.id}`\n"
        f"Role: `{role.upper()}`\n"
        f"Username: @{user.username or 'N/A'}\n\n"
        f"📱 *DishTV Status*\n"
        f"Mobile: `{jio_mobile}`\n"
        f"Token Expiry: `{expiry}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@owner_only
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/login <10-digit mobile>`", parse_mode=ParseMode.MARKDOWN)
        return

    mobile = context.args[0].strip()
    if not re.match(r"^\d{10}$", mobile):
        await update.message.reply_text("❌ Enter a 10-digit mobile number. Example: `/login 9876543210`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text(f"🔑 Sending an OTP to *{mobile}*...", parse_mode=ParseMode.MARKDOWN)

    result = send_jio_otp_api(mobile)

    if result["status"] == "success":
        user_data = get_user_data()
        user_data[str(update.effective_user.id)] = {
            "mobile": mobile,
            "pending": True,
            "login_time": datetime.now(IST).isoformat()
        }
        save_user_data(user_data)
        await msg.edit_text(
            f"✅ OTP sent to *{mobile}*.\n"
            f"Now verify it with `/otp <6-digit code>`.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await msg.edit_text(f"❌ OTP fail: {result['message']}")


@owner_only
async def otp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/otp <6-digit code>`", parse_mode=ParseMode.MARKDOWN)
        return

    otp = context.args[0].strip()
    if not re.match(r"^\d{6}$", otp):
        await update.message.reply_text("❌ Enter a 6-digit OTP. Example: `/otp 123456`", parse_mode=ParseMode.MARKDOWN)
        return

    user_data = get_user_data()
    user_entry = user_data.get(str(update.effective_user.id))
    if not user_entry or not user_entry.get("pending"):
        await update.message.reply_text("❌ Request an OTP first with `/login <mobile>`.", parse_mode=ParseMode.MARKDOWN)
        return

    mobile = user_entry["mobile"]
    msg = await update.message.reply_text("🔐 Verifying OTP...")

    result = verify_jio_otp_api(mobile, otp)

    if result["status"] == "success":
        user_entry["pending"] = False
        user_entry["verified"] = True
        user_entry.pop("login_time", None)
        save_user_data(user_data)
        await msg.edit_text("✅ *JioTV login successful!*\nYou can now use `/live` or `/rec`.", parse_mode=ParseMode.MARKDOWN)
        # Remove the OTP command from the chat when Telegram allows deletion.
        try:
            await update.message.delete()
        except Exception:
            pass
    else:
        await msg.edit_text(f"❌ Verification failed: {result['message']}\nTry again with `/otp <code>`.", parse_mode=ParseMode.MARKDOWN)


def _quality_source_from_message(message):
    """Return Telegram file metadata for a replied video or video document."""
    if not message:
        return None
    if message.video:
        return {
            "file_id": message.video.file_id,
            "file_name": message.video.file_name or "video.mp4",
            "file_size": message.video.file_size or 0,
        }
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = message.document.file_name or "video.mp4"
        if mime.startswith("video/") or name.lower().endswith(
            (".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts")
        ):
            return {
                "file_id": message.document.file_id,
                "file_name": name,
                "file_size": message.document.file_size or 0,
            }
    return None


async def qualitymax_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show output-quality buttons for a replied Telegram video."""
    now = time.time()
    for token, item in list(QUALITY_PENDING.items()):
        if now - item.get("created_at", 0) > QUALITY_PENDING_TTL:
            QUALITY_PENDING.pop(token, None)
    source = _quality_source_from_message(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to a video or video document with `/Qualitymax`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if source["file_size"] and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        size_mb = source["file_size"] / (1024 * 1024)
        await update.message.reply_text(
            f"❌ Source video is {size_mb:.1f} MB.\n\n"
            f"In this Telegram Bot API setup, files larger than {telegram_limit_text()} cannot be downloaded.\n"
            f"Reduce the video below {telegram_limit_text()} and send it again, "
            "then reply to it with `/Qualitymax`.",
        )
        return

    token = _secrets.token_hex(6)
    QUALITY_PENDING[token] = {
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "file_id": source["file_id"],
        "file_name": source["file_name"],
        "created_at": time.time(),
    }
    keyboard = [
        [
            InlineKeyboardButton("140p", callback_data=f"qualitymax:{token}:140"),
            InlineKeyboardButton("240p", callback_data=f"qualitymax:{token}:240"),
            InlineKeyboardButton("480p", callback_data=f"qualitymax:{token}:480"),
        ],
        [
            InlineKeyboardButton("720p", callback_data=f"qualitymax:{token}:720"),
            InlineKeyboardButton("1080p", callback_data=f"qualitymax:{token}:1080"),
        ],
    ]
    await update.message.reply_text(
        "🎞️ *Select the output quality:*\n"
        f"`{source['file_name']}`\n\n"
        "The video will be converted to the selected quality and uploaded to Telegram.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


MERGE_AUDIO_EXTENSIONS = (
    ".opus", ".ogg", ".oga", ".mp2", ".mp3", ".aac", ".m4a", ".m4b",
    ".wma", ".ac3", ".eac3", ".ec3", ".wav", ".flac",
)


def _merge_video_source(message):
    """Return source metadata for a replied video/document."""
    return _quality_source_from_message(message)


def _merge_audio_source(message):
    """Return source metadata for an uploaded supported audio file."""
    if not message:
        return None
    if message.audio:
        audio = message.audio
        name = audio.file_name or "added_audio.mp3"
        return {
            "file_id": audio.file_id,
            "file_name": name,
            "file_size": audio.file_size or 0,
        }
    if message.document:
        document = message.document
        name = document.file_name or "added_audio"
        mime = (document.mime_type or "").lower()
        if mime.startswith("audio/") or name.lower().endswith(MERGE_AUDIO_EXTENSIONS):
            return {
                "file_id": document.file_id,
                "file_name": name,
                "file_size": document.file_size or 0,
            }
    return None


def _merge_menu(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎵 Merger 1", callback_data=f"merge:1:{token}"),
        InlineKeyboardButton("🎵 Merger 2", callback_data=f"merge:2:{token}"),
    ]])


async def merge_video_audio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the video/audio merger flow from a replied Telegram video."""
    now = time.time()
    for token, item in list(MERGE_PENDING.items()):
        if now - item.get("created_at", 0) > MERGE_PENDING_TTL:
            MERGE_PENDING.pop(token, None)

    source = _merge_video_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to a video or video document with `/Merge_video_And_Audio`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if source["file_size"] and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text(
            f"❌ The video exceeds the Telegram Bot API download limit of {telegram_limit_text()}.",
        )
        return

    token = _secrets.token_hex(8)
    MERGE_PENDING[token] = {
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "video_file_id": source["file_id"],
        "video_name": source["file_name"],
        "video_size": source["file_size"],
        "created_at": now,
    }
    await update.message.reply_text(
        "🎬 *Merge Video & Audio*\n\n"
        "Merger 1 :\n"
        "Replace all existing audio tracks with the uploaded audio.\n"
        "_(Single Audio)_\n\n"
        "Merger 2 :\n"
        "Keep all existing audio tracks and subtitles, and add the uploaded audio.\n"
        "_(Multi Audio)_\n\n"
        "🥰 Added audio should play correctly inside Telegram.\n\n"
        "Supported Audio:\n"
        "Opus • Vorbis • MP2 • MP3 • AAC • HE-AAC • WMA v1 • WMA v2 • AC3 • E-AC3",
        reply_markup=_merge_menu(token),
        parse_mode=ParseMode.MARKDOWN,
    )


async def merge_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or parts[1] not in {"1", "2"}:
        await query.answer("Invalid merger selection.", show_alert=True, cache_time=0)
        return
    mode, token = parts[1], parts[2]
    pending = MERGE_PENDING.get(token)
    if not pending or time.time() - pending.get("created_at", 0) > MERGE_PENDING_TTL:
        MERGE_PENDING.pop(token, None)
        await query.answer("This merger selection has expired.", show_alert=True)
        return
    if pending["user_id"] != query.from_user.id:
        await query.answer(
            "❌ Only the user who opened this menu can use it.",
            show_alert=True,
            cache_time=0,
        )
        return
    pending["mode"] = mode
    pending["state"] = "waiting_audio"
    pending["created_at"] = time.time()
    await query.answer("Waiting for the audio upload...", cache_time=0)
    await query.edit_message_text(
        "Now Send 🎵 Audio File To Merge",
        parse_mode=ParseMode.MARKDOWN,
    )


def _build_merge_status_text(filename: str, pct: float, speed_mbps: float | None,
                             status: str = "Merging...") -> str:
    speed_text = "Calculating..." if not speed_mbps else f"{speed_mbps:.2f} MB/s"
    return (
        "🎬 Video-Audio Merging...\n\n"
        f"📄 File:\n`{filename}`\n\n"
        "Progress:\n"
        f"`[{_progress_bar(int(pct))}] {pct:.2f}%`\n\n"
        f"⚡ Speed: `{speed_text}`\n\n"
        f"Status: {status}"
    )


def _build_merge_popup_text(task_id: str) -> str:
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        return "⚠️ Merge info not available."
    filename = str(info.get("filename") or "Unknown")
    pct = float(info.get("pct") or 0.0)
    elapsed = max(0.0, float(info.get("elapsed") or 0.0))
    total = max(0.0, float(info.get("total_duration") or 0.0))
    speed = float(info.get("speed_mbps") or 0.0)
    user_obj = info.get("user_obj")
    username = (
        f"@{user_obj.username}" if user_obj and user_obj.username
        else (user_obj.first_name if user_obj else "Unknown")
    )
    user_id = user_obj.id if user_obj else "Unknown"
    remaining = max(0.0, total - elapsed) if total else 0.0
    speed_text = "Calculating..." if not speed else f"{speed:.2f} MB/s"
    return (
        f"📄 {filename[:30]}\n"
        f"📊 [{_progress_bar(int(pct))}] {pct:.1f}%\n"
        f"{str(info.get('status') or '🎬 Merging')[:22]}\n"
        f"⏱ {_fmt_time(elapsed)}\n"
        f"⏳ {_fmt_time(remaining)}\n"
        f"⚡ {speed_text}\n"
        f"👤 {str(username)[:18]}\n"
        f"🆔 {str(user_id)[:12]}"
    )[:200]


class _ProgressUploadFile:
    """File-like wrapper that exposes upload bytes to the shared updater."""

    def __init__(self, file_obj, info):
        self._file = file_obj
        self._info = info

    def read(self, size=-1):
        data = self._file.read(size)
        self._info["upload_bytes"] = self._file.tell()
        return data

    def seek(self, offset, whence=0):
        result = self._file.seek(offset, whence)
        self._info["upload_bytes"] = self._file.tell()
        return result

    def tell(self):
        return self._file.tell()

    def fileno(self):
        return self._file.fileno()

    def readable(self):
        return self._file.readable()

    def seekable(self):
        return self._file.seekable()

    def writable(self):
        return False

    def __getattr__(self, name):
        return getattr(self._file, name)


async def _merge_ffmpeg(input_video: str, input_audio: str, output_path: str,
                        mode: str, task_id: str):
    progress_file = f"/tmp/ffmpeg_merge_{task_id}.txt"
    if mode == "1":
        maps = [
            "-map", "0:v:0", "-map", "0:s?", "-map", "0:t?",
            "-map", "1:a:0",
        ]
    else:
        maps = [
            "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?",
            "-map", "1:a:0",
        ]
    video_probe = await _stream_probe_file(input_video)
    video_audio_count = sum(
        1 for stream in video_probe["streams"]
        if stream.get("codec_type") == "audio"
    )
    video_metadata = await build_ffmpeg_metadata(
        input_video,
        selected_streams={
            "video": [0],
            "audio": [] if mode == "1" else None,
            "subtitle": None,
        },
    )
    added_audio_metadata = await build_ffmpeg_metadata(
        input_audio,
        selected_streams={"audio": [0]},
        stream_offsets={"audio": 0 if mode == "1" else video_audio_count},
        include_format_metadata=False,
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_video, "-i", input_audio,
        *maps,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-c:s", "copy",
        "-c:t", "copy",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-shortest",
        "-max_muxing_queue_size", "4096",
        "-movflags", "+faststart",
        "-progress", progress_file, "-nostats",
        output_path,
    ]
    cmd[-1:-1] = video_metadata + added_audio_metadata
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    RECORDING_SESSION_PROC[task_id] = proc
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if info:
        info["process"] = proc
        info["progress_file"] = progress_file
        info["status"] = "🎬 Merging"
    try:
        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            await asyncio.wait_for(proc.wait(), timeout=1800)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            return False, "Merge timed out."
        stderr = await stderr_task
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info and not info.get("running", True):
            return False, "Merge cancelled."
        if proc.returncode != 0:
            return False, stderr.decode(errors="replace")[-1500:]
        return True, ""
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return False, "Merge timed out."
    finally:
        RECORDING_SESSION_PROC.pop(task_id, None)
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["process"] = None
        try:
            os.remove(progress_file)
        except OSError:
            pass


def _build_upload_status_text(filename: str, pct: float, sent: int,
                              total: int, speed: float, remaining: int) -> str:
    filled = int(10 * max(0.0, min(100.0, pct)) / 100)
    dots = "●" * filled + "⬜" * (10 - filled)
    return (
        "📤 Uploading:\n"
        f"`{filename}`\n\n"
        f"[{dots}] {pct:.2f}%\n\n"
        f"{sent / 1024 / 1024:.1f} MB of {total / 1024 / 1024:.2f} MB\n\n"
        f"Speed:\n{speed:.2f} MB/s\n\n"
        f"Time Left:\n{remaining}s"
    )


async def merge_audio_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the audio after a merger mode has been selected."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    pending = next(
        (
            item for item in MERGE_PENDING.values()
            if item.get("user_id") == user_id
            and item.get("chat_id") == chat_id
            and item.get("state") == "waiting_audio"
            and time.time() - item.get("created_at", 0) <= MERGE_PENDING_TTL
        ),
        None,
    )
    if not pending:
        return
    audio = _merge_audio_source(update.message)
    if not audio:
        await update.message.reply_text(
            "❌ Send a supported audio file: Opus, Vorbis, MP2, MP3, AAC, "
            "HE-AAC, WMA, AC3 ya E-AC3.",
        )
        return
    if audio["file_size"] and audio["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text(
            f"❌ The audio exceeds the Telegram Bot API download limit of {telegram_limit_text()}.",
        )
        return
    if not await check_process_slot(update):
        return

    token = next((k for k, v in MERGE_PENDING.items() if v is pending), None)
    if token:
        MERGE_PENDING.pop(token, None)
    task_id = _secrets.token_hex(8)
    filename = pending["video_name"]
    status_message = await update.message.reply_text(
        _build_merge_status_text(filename, 0.0, None, "Merging..."),
        reply_markup=_build_rec_progress_inline(task_id),
        parse_mode=ParseMode.MARKDOWN,
    )
    asyncio.create_task(
        _run_merge_job(update, context, pending, audio, status_message, task_id)
    )


async def _run_merge_job(update, context, pending, audio, status_message, task_id):
    global _active_processes
    work_dir = f"/tmp/merge_{task_id}"
    os.makedirs(work_dir, exist_ok=True)
    input_video = os.path.join(work_dir, "video")
    input_audio = os.path.join(work_dir, "audio")
    extension = Path(pending["video_name"]).suffix or ".mkv"
    output_name = f"{Path(pending['video_name']).stem}_merged{extension}"
    output_path = os.path.join(work_dir, output_name)
    user_obj = update.effective_user
    start_time = time.time()
    RECORDING_PROGRESS_INFO[task_id] = {
        "process": None, "start_time": start_time, "duration": 0.0,
        "total_duration": 0.0, "filename": pending["video_name"],
        "file_name": pending["video_name"],
        "message_id": status_message.message_id, "chat_id": pending["chat_id"],
        "speed": 0.0, "speed_mbps": 0.0, "platform": "Video Merger",
        "channel": {"channelCategoryId": "Video Merger"}, "user_obj": user_obj,
        "user_id": user_obj.id, "pct": 0.0, "elapsed": 0.0,
        "status": "🎬 Merging", "running": True, "kind": "merge",
        "phase": "merge",
    }
    _active_processes += 1
    updater_task = asyncio.create_task(
        _auto_updater(task_id, status_message, None, filename, 0.0, start_time)
    )
    ACTIVE_UPDATERS[task_id] = updater_task
    try:
        video_file = await context.bot.get_file(pending["video_file_id"])
        await video_file.download_to_drive(custom_path=input_video)
        audio_file = await context.bot.get_file(audio["file_id"])
        await audio_file.download_to_drive(custom_path=input_audio)
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if not info or not info.get("running", True):
            await status_message.edit_text(
                "❌ Merge Cancelled\n\n⚠️ Partial Output Deleted",
            )
            return
        duration = await _media_duration_seconds(input_video)
        info["duration"] = duration
        info["total_duration"] = duration
        info["source_path"] = output_path
        ok, error = await _merge_ffmpeg(
            input_video, input_audio, output_path, pending["mode"], task_id
        )
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if not ok:
            cancelled = not info or not info.get("running", True)
            await status_message.edit_text(
                "❌ Merge Cancelled\n\n⚠️ Partial Output Deleted"
                if cancelled else
                f"❌ Merge Failed\n\n⚠️ Partial Output Deleted\n\n`{error[:500]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            await status_message.edit_text(
                "❌ Merge Failed\n\n⚠️ Partial Output Deleted",
            )
            return
        info["phase"] = "upload"
        info["status"] = "📤 Uploading"
        info["upload_bytes"] = 0
        info["upload_size"] = os.path.getsize(output_path)
        info["upload_start"] = time.time()
        info["pct"] = 0.0
        await status_message.edit_text(
            "✅ Merge Completed\n\n"
            f"📄 File:\n`{output_name}`\n\n"
            "Uploading...",
            reply_markup=_build_rec_progress_inline(task_id),
            parse_mode=ParseMode.MARKDOWN,
        )
        upload_file = None
        try:
            with open(output_path, "rb") as raw:
                upload_file = _ProgressUploadFile(raw, info)
                telegram_input = InputFile(
                    upload_file, filename=output_name, read_file_handle=False
                )
                await context.bot.send_video(
                    chat_id=pending["chat_id"], video=telegram_input,
                    caption=f"🎬 {output_name}", supports_streaming=True,
                    read_timeout=1800, write_timeout=1800,
                )
        except Exception:
            with open(output_path, "rb") as raw:
                upload_file = _ProgressUploadFile(raw, info)
                telegram_input = InputFile(
                    upload_file, filename=output_name, read_file_handle=False
                )
                await context.bot.send_document(
                    chat_id=pending["chat_id"], document=telegram_input,
                    caption=f"🎬 {output_name}",
                    read_timeout=1800, write_timeout=1800,
                )
        await status_message.edit_text(
            "✅ Upload Completed\n\n"
            f"📄 File:\n`{output_name}`\n\n"
            f"{_AUDIO_TRACK_COMPATIBILITY_NOTE}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Video/audio merge failed")
        try:
            await status_message.edit_text(
                "❌ Merge Failed\n\n⚠️ Partial Output Deleted",
            )
        except Exception:
            pass
    finally:
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["running"] = False
        RECORDING_SESSION_PROC.pop(task_id, None)
        task = ACTIVE_UPDATERS.pop(task_id, None)
        if task and not task.done():
            task.cancel()
        _active_processes = max(0, _active_processes - 1)
        shutil.rmtree(work_dir, ignore_errors=True)
        RECORDING_PROGRESS_INFO.pop(task_id, None)


async def _media_duration_seconds(path: str) -> float:
    """Read a media duration for the shared progress display."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return max(0.0, float(stdout.decode().strip()))
    except (asyncio.TimeoutError, OSError, ValueError):
        return 0.0


async def _media_video_dimensions(path: str) -> tuple[int, int]:
    """Read the first video stream dimensions for a media selector."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        streams = json.loads(stdout.decode(errors="replace")).get("streams", [])
        if streams:
            return int(streams[0].get("width") or 0), int(streams[0].get("height") or 0)
    except (asyncio.TimeoutError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return 0, 0


def _screenshot_menu(token: str, duration: float, width: int, height: int):
    duration_text = (
        f"{int(duration // 60):02d}:{int(duration % 60):02d}"
        if duration < 3600 else _fmt_time(duration)
    )
    resolution = f"{height}p" if height else "Unknown"
    keyboard = []
    row = []
    for count in range(1, 31):
        row.append(
            InlineKeyboardButton(
                str(count),
                callback_data=f"screenshot:{token}:{count}",
            )
        )
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"screenshot_cancel:{token}")
    ])
    text = (
        "📸 *Screenshot Generator*\n\n"
        f"*Source:* `{duration_text} • {resolution}`\n\n"
        "Select the number of screenshots\n\n"
        "✶ Click the Button of your choice 👇 *1 to 30*"
    )
    return text, InlineKeyboardMarkup(keyboard)


async def screenshot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid screenshot request.", show_alert=True)
        return
    token, count_text = parts[1], parts[2]
    try:
        count = int(count_text)
    except ValueError:
        await query.answer("Invalid screenshot count.", show_alert=True)
        return
    if not 1 <= count <= 30:
        await query.answer("Choose between 1 and 30 screenshots.", show_alert=True)
        return
    pending = SCREENSHOT_PENDING.get(token)
    if (
        not pending
        or time.time() - pending.get("created_at", 0) > QUALITY_PENDING_TTL
    ):
        SCREENSHOT_PENDING.pop(token, None)
        await query.answer("Screenshot menu expired.", show_alert=True)
        return
    if pending["user_id"] != query.from_user.id:
        await query.answer("Only the requesting user can use these buttons.", show_alert=True)
        return
    if not await check_process_slot(update):
        return
    SCREENSHOT_PENDING.pop(token, None)
    await query.answer(f"Generating {count} screenshot(s)...")
    await query.edit_message_text(
        f"📸 Screenshot Generator\n\n"
        f"Source: `{pending['duration_text']} • "
        f"{pending['resolution']}`\n\n"
        f"Selected screenshots: `{count}`\n"
        "Downloading...",
        parse_mode=ParseMode.MARKDOWN,
    )
    source = pending["source"]
    await _media_local_job(
        update, context, source, "screenshot", [str(count)],
        status_message=query.message,
    )


async def screenshot_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    token = parts[1] if len(parts) == 2 else ""
    pending = SCREENSHOT_PENDING.get(token)
    if pending and pending.get("user_id") != query.from_user.id:
        await query.answer("Only the requesting user can cancel this menu.", show_alert=True)
        return
    SCREENSHOT_PENDING.pop(token, None)
    await query.answer("Screenshot selection cancelled.")
    try:
        await query.edit_message_text("❌ Screenshot selection cancelled.")
    except Exception:
        pass


async def _transcode_video(input_path: str, output_path: str, height: int,
                           task_id: str | None = None):
    """Transcode a Telegram video while exposing a cancellable FFmpeg process."""
    progress_file = (
        f"/tmp/ffmpeg_quality_{task_id}.txt"
        if task_id else None
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-disposition:a:0", "default",
        "-map", "0:s?",
        "-map", "0:t?",
        "-c:s", "copy",
        "-c:t", "copy",
        "-map_chapters", "0",
        "-movflags", "+faststart",
        *(["-progress", progress_file, "-nostats"] if progress_file else []),
        output_path,
    ]
    metadata = await build_ffmpeg_metadata(
        input_path,
        selected_streams={"video": [0], "audio": None, "subtitle": None},
    )
    cmd[-1:-1] = metadata
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    if task_id:
        RECORDING_SESSION_PROC[task_id] = proc
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["process"] = proc
            info["progress_file"] = progress_file
            info["status"] = "🎥 Recording"
    try:
        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            await asyncio.wait_for(proc.wait(), timeout=1800)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            return False, "Conversion timed out."
        stderr = await stderr_task
        if task_id and not RECORDING_PROGRESS_INFO.get(task_id, {}).get("running", True):
            return False, "Conversion cancelled."
        if proc.returncode != 0:
            return False, stderr.decode(errors="replace")[-1200:]
        return True, ""
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return False, "Conversion timed out."
    finally:
        RECORDING_SESSION_PROC.pop(task_id, None) if task_id else None
        if task_id:
            info = RECORDING_PROGRESS_INFO.get(task_id)
            if info:
                info["process"] = None
        if progress_file:
            try:
                os.remove(progress_file)
            except OSError:
                pass


async def qualitymax_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid quality request.", show_alert=True)
        return
    token, height_text = parts[1], parts[2]
    try:
        height = int(height_text)
    except ValueError:
        await query.answer("Invalid quality.", show_alert=True)
        return
    if height not in (140, 240, 480, 720, 1080):
        await query.answer("Unsupported quality.", show_alert=True)
        return

    pending = QUALITY_PENDING.get(token)
    if not pending or time.time() - pending.get("created_at", 0) > QUALITY_PENDING_TTL:
        QUALITY_PENDING.pop(token, None)
        await query.answer("This quality selection has expired.", show_alert=True)
        return
    if pending["user_id"] != query.from_user.id:
        await query.answer("Only the user who opened this menu can use it.", show_alert=True)
        return
    if not await check_process_slot(update):
        return
    QUALITY_PENDING.pop(token, None)

    await query.answer(f"{height}p conversion is starting...")
    work_dir = f"/tmp/qualitymax_{_secrets.token_hex(8)}"
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, "source")
    output_path = os.path.join(work_dir, f"converted_{height}p.mp4")
    status_message = query.message
    task_id = _secrets.token_hex(8)
    source_filename = pending["file_name"]
    user_obj = query.from_user
    start_time = time.time()
    RECORDING_PROGRESS_INFO[task_id] = {
        "process": None,
        "start_time": start_time,
        "duration": 0.0,
        "total_duration": 0.0,
        "filename": source_filename,
        "file_name": source_filename,
        "message_id": status_message.message_id,
        "chat_id": pending["chat_id"],
        "speed": 0.0,
        "speed_mbps": 0.0,
        "platform": "Qualitymax",
        "channel": {"channelCategoryId": "Qualitymax"},
        "user_obj": user_obj,
        "user_id": user_obj.id,
        "pct": 0.0,
        "elapsed": 0.0,
        "status": "⬇️ Downloading",
        "running": True,
    }
    global _active_processes
    _active_processes += 1
    try:
        await status_message.edit_text(
            _build_rec_status_text(
                source_filename, 0.0, None, "⬇️ Downloading"
            ),
            reply_markup=_build_rec_progress_inline(task_id),
            parse_mode=ParseMode.MARKDOWN,
        )
        updater_task = asyncio.create_task(
            _auto_updater(
                task_id, status_message, None, source_filename, 0.0, start_time
            )
        )
        ACTIVE_UPDATERS[task_id] = updater_task
        try:
            telegram_file = await context.bot.get_file(pending["file_id"])
            await telegram_file.download_to_drive(custom_path=input_path)
        except Exception as exc:
            error_text = str(exc)
            if "file is too big" in error_text.lower():
                await status_message.edit_text(
                    f"❌ The source video exceeds the Telegram Bot API "
                    f"download limit of {telegram_limit_text()}.\n\n"
                    "Compress or shorten the video and send it again.",
                )
            else:
                await status_message.edit_text(
                    f"❌ Could not download the source video:\n`{error_text[:800]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if not info or not info.get("running", True):
            await status_message.edit_text(
                f"❌ Recording Cancelled\n\n"
                f"📄 File:\n`{source_filename}`\n\n"
                f"⚠️ Download cancelled before conversion.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        source_duration = await _media_duration_seconds(input_path)
        info["duration"] = source_duration
        info["total_duration"] = source_duration
        info["source_path"] = input_path
        info["source_size"] = os.path.getsize(input_path) if os.path.exists(input_path) else 0
        info["status"] = "🎥 Recording"
        await status_message.edit_text(
            _build_rec_status_text(
                source_filename, 0.0, None, "🎥 Recording"
            ),
            reply_markup=_build_rec_progress_inline(task_id),
            parse_mode=ParseMode.MARKDOWN,
        )
        ok, error = await _transcode_video(
            input_path, output_path, height, task_id=task_id
        )
        if not ok:
            info = RECORDING_PROGRESS_INFO.get(task_id)
            cancelled = not info or not info.get("running", True)
            partial_uploaded = False
            if os.path.exists(output_path) and os.path.getsize(output_path) >= 1024:
                partial_caption = f"📄 {source_filename} (partial)"
                try:
                    with open(output_path, "rb") as partial_file:
                        await context.bot.send_video(
                            chat_id=pending["chat_id"],
                            video=partial_file,
                            caption=partial_caption,
                            supports_streaming=True,
                            read_timeout=600,
                            write_timeout=600,
                        )
                    partial_uploaded = True
                except Exception:
                    try:
                        with open(output_path, "rb") as partial_file:
                            await context.bot.send_document(
                                chat_id=pending["chat_id"],
                                document=partial_file,
                                caption=partial_caption,
                                read_timeout=600,
                                write_timeout=600,
                            )
                        partial_uploaded = True
                    except Exception:
                        partial_uploaded = False
            await status_message.edit_text(
                f"{'❌ Recording Cancelled' if cancelled else '❌ Recording Failed'}\n\n"
                f"{'⚠️ Partial Recording Sent\n\n' if partial_uploaded else ''}"
                f"📄 File:\n`{source_filename}`\n\n"
                f"⏺ Recorded:\n`{_fmt_time(info.get('elapsed', 0.0) if info else 0.0)}`\n\n"
                f"{'📤 The recorded portion has been uploaded successfully.\n\n' if partial_uploaded else '⚠️ No partial recording was available to upload.\n\n'}"
                f"⏳ Server copy auto-deletes in 1 hour.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        valid, validation_error = await validate_recording_file(output_path)
        if not valid:
            await status_message.edit_text(
                f"❌ Recording Failed\n\n"
                f"📄 File:\n`{source_filename}`\n\n"
                f"⚠️ The converted video is corrupt; upload was stopped.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        if os.path.getsize(output_path) > TELEGRAM_BOT_UPLOAD_LIMIT:
            await status_message.edit_text(
                f"❌ Recording Failed\n\n"
                f"📄 File:\n`{source_filename}`\n\n"
                f"❌ The converted `{height}p` file is {size_mb:.1f} MB.\n\n"
                f"The Telegram Bot API upload limit is {telegram_upload_limit_text()}. "
                "Choose a lower quality for this video.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["pct"] = 100.0
            info["elapsed"] = info.get("total_duration", 0.0)
            info["status"] = "📤 Uploading"
        await status_message.edit_text(
            _build_rec_status_text(
                source_filename, 100.0,
                info.get("speed_mbps", 0.0) if info else 0.0,
                "📤 Uploading",
            ),
            reply_markup=_build_rec_progress_inline(task_id),
            parse_mode=ParseMode.MARKDOWN,
        )
        caption = f"🎞️ Converted Quality: *{height}p*\n📄 `{pending['file_name']}`"
        try:
            with open(output_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=pending["chat_id"],
                    video=video_file,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600,
                )
        except Exception:
            with open(output_path, "rb") as document_file:
                await context.bot.send_document(
                    chat_id=pending["chat_id"],
                    document=document_file,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    read_timeout=600,
                    write_timeout=600,
                )
        info = RECORDING_PROGRESS_INFO.get(task_id)
        duration_text = _fmt_time(info.get("total_duration", 0.0) if info else 0.0)
        await status_message.edit_text(
            f"✅ Recording Completed\n\n"
            f"📄 File:\n`{source_filename}`\n\n"
            f"⏺ Duration:\n`{duration_text}`\n\n"
            f"📤 Upload completed successfully.\n\n"
            f"⏳ Server copy auto-deletes in 3 hours.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.exception("Qualitymax conversion failed")
        try:
            await status_message.edit_text(
                f"❌ Recording Failed\n\n"
                f"📄 File:\n`{source_filename}`\n\n"
                f"⚠️ Conversion failed.",
            )
        except Exception:
            pass
    finally:
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["running"] = False
        RECORDING_SESSION_PROC.pop(task_id, None)
        task = ACTIVE_UPDATERS.pop(task_id, None)
        if task and not task.done():
            task.cancel()
        _active_processes -= 1
        shutil.rmtree(work_dir, ignore_errors=True)
        RECORDING_PROGRESS_INFO.pop(task_id, None)


async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Owner/admin/premium users don't need verification.
    if is_owner(user.id) or is_admin(user.id) or is_premium(user.id):
        await update.message.reply_text(
            "✅ Owner/Premium/Admin users do not need verification."
        )
        return

    # Already verified?
    if is_verified(user.id):
        tokens = get_verify_tokens()
        entry = tokens.get(str(user.id), {})
        verified_at = entry.get("verified_at")
        if verified_at:
            elapsed = (datetime.now(IST) - datetime.fromisoformat(verified_at)).total_seconds()
            remaining = int((VERIFICATION_EXPIRY_SECONDS - elapsed) / 60)
            await update.message.reply_text(
                f"✅ *You are already verified!*\n"
                f"⏳ Remaining access: *{remaining} minutes*",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    token = generate_verify_token(user.id)
    bot_username = BOTUSERNAME or context.bot.username
    deep_link = f"https://t.me/{bot_username}?start=verify_{user.id}_{token}"
    short_link = shorten_url(deep_link)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Verify", url=short_link)],
        [InlineKeyboardButton("❓ How to Verify", callback_data="howto_verify")],
    ])
    msg = await update.message.reply_text(
        "🔐 *Verification Required*\n\n"
        "Click the Verify button below to unlock access for 40 minutes.\n\n"
        "⚠️ This verification message will be automatically deleted after 10 minutes.",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )
    asyncio.create_task(_auto_delete(context.bot, update.effective_chat.id, msg.message_id, 600))


async def howto_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "❓ *How to Verify*\n\n"
        "1️⃣ Send the `/verify` command\n"
        "2️⃣ *✅ Verify* button dabao\n"
        "3️⃣ Skip the ad or complete the task on the page\n"
        "4️⃣ Open the bot link shown at the end\n"
        "5️⃣ Bot bolega *Verification Successful!*\n\n"
        f"✅ Iske baad *{VERIFICATION_EXPIRY_SECONDS // 60} minute* tak access milega.\n\n"
        f"📌 Group: {GROUP_LINK}" if GROUP_LINK else
        "❓ *How to Verify*\n\n"
        "1️⃣ Send the `/verify` command\n"
        "2️⃣ *✅ Verify* button dabao\n"
        "3️⃣ Skip the ad or complete the task on the page\n"
        "4️⃣ Open the bot link shown at the end\n"
        "5️⃣ Bot bolega *Verification Successful!*\n\n"
        f"✅ Iske baad *{VERIFICATION_EXPIRY_SECONDS // 60} minute* tak access milega.",
        parse_mode=ParseMode.MARKDOWN
    )


def _fetch_cenc_key(stream_url: str, license_url: str, cookie: str, user_agent: str) -> str:
    """Fetch the ClearKey decryption key for a CENC-encrypted DASH stream.
    Returns key_hex string, or empty string on failure."""
    try:
        import base64 as _b64
        padding = lambda s: s + "=" * (-len(s) % 4)
        hdrs = {}
        if cookie:
            hdrs["Cookie"] = cookie
        if user_agent:
            hdrs["User-Agent"] = user_agent
        mpd = requests.get(stream_url, headers=hdrs, timeout=10).text
        kid_match = re.search(r'default_KID="([^"]+)"', mpd)
        if not kid_match:
            return ""
        kid_uuid = kid_match.group(1).replace("-", "")
        kid_bytes = bytes.fromhex(kid_uuid)
        kid_b64 = _b64.urlsafe_b64encode(kid_bytes).rstrip(b"=").decode()
        lic = requests.post(
            license_url,
            data=json.dumps({"kids": [kid_b64], "type": "temporary"}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        ).json()
        key_b64 = lic["keys"][0]["k"]
        return _b64.urlsafe_b64decode(padding(key_b64)).hex()
    except Exception:
        return ""


# ── Progress helpers ──────────────────────────

def _read_ffmpeg_progress(progress_file: str) -> dict:
    """Read FFmpeg -progress file and return latest key=value pairs."""
    if not os.path.exists(progress_file):
        return {}
    try:
        with open(progress_file, "r") as f:
            data = {}
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip()
        return data
    except Exception:
        return {}


def _progress_int(value, default: int = 0) -> int:
    """Parse FFmpeg progress numbers; fields can legally be reported as N/A."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _progress_bar(pct: int, width: int = 10) -> str:
    pct = max(0, min(100, int(pct)))
    filled = int(width * pct / 100)
    return "■" * filled + "□" * (width - filled)

def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _build_progress_msg(filename: str, pct: int, elapsed: float,
                        speed_mbps: float, channel: dict,
                        user, cancel_id: str, total_duration: float = 0,
                        status: str = "Downloading") -> str:
    bar = _progress_bar(pct)
    username = f"@{user.username}" if user.username else user.first_name
    platform = channel.get("channelCategoryId", "Unknown")
    total_duration = max(0, float(total_duration))
    recorded = min(max(0, float(elapsed)), total_duration) if total_duration else max(0, float(elapsed))
    remaining = max(0, total_duration - recorded) if total_duration else 0
    time_line = (
        f"*Recorded:* `{_fmt_time(recorded)} / {_fmt_time(total_duration)}`\n"
        f"*Remaining:* `{_fmt_time(remaining)}`\n"
        if total_duration else
        f"*Elapsed:* `{_fmt_time(recorded)}`\n"
    )
    return (
        f"📄 *File:*\n`{filename}`\n\n"
        f"*Progress:*\n"
        f"`[{bar}] {pct}%`\n\n"
        f"*Status:* {status}\n"
        f"{time_line}"
        f"*Speed:* `{speed_mbps:.2f} MB/s`\n\n"
        f"*Platform:* {platform}\n"
        f"*User:* {username}\n"
        f"*User ID:* `{user.id}`\n\n"
        f"❌ *Cancel Command:*\n`/cancel {cancel_id}`"
    )


# ── New progress system helpers ───────────────────────────────────────────────

def _build_rec_status_text(filename: str, pct: float, speed_mbps: float | None,
                            status: str = "Recording...") -> str:
    """Build the single editable live-progress message."""
    bar = _progress_bar(int(pct))
    speed_text = "Calculating..." if not speed_mbps else f"{speed_mbps:.2f} MB/s"
    return (
        f"🎥 Your file is Recording...\n\n"
        f"📄 File:\n`{filename}`\n\n"
        f"Progress:\n"
        f"`[{bar}] {pct:.2f}%`\n\n"
        f"⚡ Speed: `{speed_text}`\n\n"
        f"Status: {status}"
    )


def _build_media_status_text(filename: str, pct: float, speed_mbps: float | None,
                             status: str = "Processing...") -> str:
    """Build a plain-text status message for local video operations."""
    bar = _progress_bar(int(pct))
    speed_text = "Calculating..." if not speed_mbps else f"{speed_mbps:.2f} MB/s"
    return (
        "🎬 Processing Video...\n\n"
        f"📄 File:\n{filename}\n\n"
        f"Progress:\n[{bar}] {pct:.2f}%\n\n"
        f"⚡ Speed:\n{speed_text}\n\n"
        f"Status:\n{status}"
    )


def _stream_label(stream: dict, ordinal: int) -> str:
    tags = stream.get("tags") or {}
    language = str(tags.get("language") or "").strip()
    title = str(tags.get("title") or tags.get("handler_name") or "").strip()
    language_names = {
        "eng": "English", "en": "English", "hin": "Hindi", "hi": "Hindi",
        "tam": "Tamil", "ta": "Tamil", "tel": "Telugu", "te": "Telugu",
        "kan": "Kannada", "kn": "Kannada", "mar": "Marathi", "mr": "Marathi",
        "mal": "Malayalam", "ml": "Malayalam", "ben": "Bengali", "bn": "Bengali",
    }
    language = language_names.get(language.lower(), language)
    if language and title and title.lower() not in language.lower():
        return f"{language} ({title})"
    return language or title or f"Stream {ordinal + 1}"


async def _stream_probe_file(path: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-1000:] or "ffprobe failed")
    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid metadata.") from exc
    streams = data.get("streams") or []
    format_duration = (data.get("format") or {}).get("duration")
    try:
        duration = max(0.0, float(format_duration or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return {"streams": streams, "duration": duration}


_FFMPEG_METADATA_OWNER = "@LittleSinghamChannel"
_FFMPEG_LANGUAGE_NAMES = {
    "hi": "Hindi",
    "hin": "Hindi",
    "en": "English",
    "eng": "English",
    "ta": "Tamil",
    "tam": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
    "kn": "Kannada",
    "kan": "Kannada",
    "ml": "Malayalam",
    "mal": "Malayalam",
    "mr": "Marathi",
    "mar": "Marathi",
    "gu": "Gujarati",
    "guj": "Gujarati",
    "pa": "Punjabi",
    "pan": "Punjabi",
    "bn": "Bengali",
    "ben": "Bengali",
    "or": "Odia",
    "ori": "Odia",
    "od": "Odia",
    "ur": "Urdu",
    "urd": "Urdu",
}
_FFMPEG_LANGUAGE_CODES = {
    "hindi": "hin",
    "english": "eng",
    "tamil": "tam",
    "telugu": "tel",
    "kannada": "kan",
    "malayalam": "mal",
    "marathi": "mar",
    "gujarati": "guj",
    "punjabi": "pan",
    "bengali": "ben",
    "odia": "ori",
    "urdu": "urd",
}
_FFMPEG_LANGUAGE_ISO3 = {
    "hi": "hin", "hin": "hin",
    "en": "eng", "eng": "eng",
    "ta": "tam", "tam": "tam",
    "te": "tel", "tel": "tel",
    "kn": "kan", "kan": "kan",
    "ml": "mal", "mal": "mal",
    "mr": "mar", "mar": "mar",
    "gu": "guj", "guj": "guj",
    "pa": "pan", "pan": "pan",
    "bn": "ben", "ben": "ben",
    "or": "ori", "ori": "ori", "od": "ori",
    "ur": "urd", "urd": "urd",
}


def _ffmpeg_language(language: object, title: object = "") -> tuple[str, str]:
    """Return an ISO code and readable name for a stream language tag."""
    raw = str(language or "").strip().lower()
    title_text = str(title or "").strip()
    code = _FFMPEG_LANGUAGE_ISO3.get(
        raw,
        _FFMPEG_LANGUAGE_CODES.get(raw, raw),
    )
    if code in _FFMPEG_LANGUAGE_NAMES:
        return code, _FFMPEG_LANGUAGE_NAMES[code]
    title_key = title_text.lower()
    for name, iso_code in _FFMPEG_LANGUAGE_CODES.items():
        if name in title_key:
            return iso_code, name.title()
    if not raw:
        return "und", "Und"
    return (raw if len(raw) == 3 else "und"), title_text or raw.upper()


async def build_ffmpeg_metadata(
    input_file: str,
    *,
    probe_args: list[str] | None = None,
    selected_streams: dict[str, list[int] | None] | None = None,
    stream_offsets: dict[str, int] | None = None,
    include_format_metadata: bool = True,
) -> list[str]:
    """Build dynamic FFmpeg metadata arguments from ffprobe JSON.

    ``selected_streams`` contains per-type ffprobe ordinal indexes that will
    actually be mapped to the output. When omitted, every detected stream is
    described. ``stream_offsets`` rebases output stream ordinals when metadata
    is assembled from more than one input.
    """
    cmd = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json",
        *(probe_args or []),
        input_file,
    ]
    streams: list[dict] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-700:] or "ffprobe failed")
        streams = (json.loads(stdout.decode(errors="replace")).get("streams") or [])
    except (asyncio.TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        logger.warning("Unable to probe %s for metadata: %s", input_file, exc)

    offsets = {"video": 0, "audio": 0, "subtitle": 0}
    offsets.update(stream_offsets or {})
    selected = selected_streams or {}
    output_args = []
    if include_format_metadata:
        output_args += [
            "-metadata", f"title={_FFMPEG_METADATA_OWNER}",
            "-metadata", f"artist={_FFMPEG_METADATA_OWNER}",
            "-metadata", f"comment={_FFMPEG_METADATA_OWNER}",
            "-metadata", f"encoder={_FFMPEG_METADATA_OWNER}",
        ]
    type_ordinals = {"video": 0, "audio": 0, "subtitle": 0}
    selected_positions = {
        stream_type: {
            source_index: output_index
            for output_index, source_index in enumerate(source_indexes)
        }
        for stream_type, source_indexes in selected.items()
        if source_indexes is not None
    }
    output_ordinals = {"video": 0, "audio": 0, "subtitle": 0}
    for stream in streams:
        stream_type = str(stream.get("codec_type") or "")
        if stream_type not in type_ordinals:
            continue
        source_ordinal = type_ordinals[stream_type]
        type_ordinals[stream_type] += 1
        if selected_streams is not None and stream_type not in selected:
            continue
        requested = selected.get(stream_type)
        if (
            stream_type in selected
            and requested is not None
            and source_ordinal not in requested
        ):
            continue
        output_ordinal = offsets[stream_type] + (
            selected_positions[stream_type][source_ordinal]
            if requested is not None
            else output_ordinals[stream_type]
        )
        output_ordinals[stream_type] += 1
        if stream_type == "video":
            for key in ("title", "artist", "comment", "encoder"):
                output_args += [
                    f"-metadata:s:v:{output_ordinal}",
                    f"{key}={_FFMPEG_METADATA_OWNER}",
                ]
        elif stream_type == "audio":
            tags = stream.get("tags") or {}
            language_code, language_name = _ffmpeg_language(
                tags.get("language"),
                tags.get("title") or tags.get("handler_name"),
            )
            audio_label = _audio_label_for_language(language_name)
            title = f"@{audio_label} {language_name}"
            output_args += [
                f"-metadata:s:a:{output_ordinal}", f"title={title}",
                f"-metadata:s:a:{output_ordinal}", f"handler_name={title}",
                f"-metadata:s:a:{output_ordinal}", f"language={language_code}",
            ]
        else:
            subtitle_title = f"{_FFMPEG_METADATA_OWNER} Subtitle"
            output_args += [
                f"-metadata:s:s:{output_ordinal}", f"title={subtitle_title}",
                f"-metadata:s:s:{output_ordinal}",
                f"handler_name={subtitle_title}",
            ]
    return output_args


def _stream_output_name(source_name: str, stream: dict, ordinal: int) -> str:
    kind = stream.get("codec_type") or "stream"
    codec = str(stream.get("codec_name") or "").lower()
    label = _media_safe_name(_stream_label(stream, ordinal), f"{kind}_{ordinal + 1}")
    label = re.sub(r"\s+", "_", label)
    if kind == "audio":
        extensions = {
            "mp3": ".mp3", "aac": ".aac", "opus": ".opus", "vorbis": ".ogg",
            "flac": ".flac", "ac3": ".ac3", "eac3": ".eac3", "wav": ".wav",
        }
        extension = extensions.get(codec, ".mka")
    elif kind == "subtitle":
        extension = {
            "ass": ".ass", "ssa": ".ssa", "subrip": ".srt", "srt": ".srt",
            "webvtt": ".vtt", "mov_text": ".srt",
        }.get(codec, ".mks")
    elif kind == "video":
        extension = ".mkv"
    else:
        extension = ".bin"
    stem = Path(source_name).stem or "extracted"
    return _media_safe_name(f"{stem}_{kind}_{ordinal + 1}_{label}{extension}")


def _build_stream_status_text(filename: str, pct: float, speed_mbps: float | None,
                              status: str, elapsed: float = 0.0,
                              remaining: float = 0.0) -> str:
    speed_text = "Calculating..." if not speed_mbps else f"{speed_mbps:.2f} MB/s"
    return (
        "🎬 Extracting Stream\n\n"
        f"📄 File:\n{filename}\n\n"
        f"Progress:\n[{_progress_bar(int(pct))}] {pct:.2f}%\n\n"
        f"⚡ Speed:\n{speed_text}\n\n"
        f"Status:\n{status}\n\n"
        f"Elapsed: {_fmt_time(elapsed)}\n"
        f"Remaining: {_fmt_time(remaining)}"
    )


def _build_stream_popup_text(task_id: str) -> str:
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        return "⚠️ Extraction info not available."
    user = info.get("user_obj")
    username = (
        f"@{user.username}" if user and user.username
        else (user.first_name if user else "Unknown")
    )
    filename = str(info.get("filename") or "Unknown")
    pct = float(info.get("pct") or 0.0)
    elapsed = float(info.get("elapsed") or 0.0)
    total = float(info.get("total_duration") or 0.0)
    remaining = max(0.0, total - elapsed) if total else 0.0
    speed = float(info.get("speed_mbps") or 0.0)
    speed_text = "Calculating..." if not speed else f"{speed:.2f} MB/s"
    current = int(info.get("current_item") or 0)
    count = int(info.get("item_count") or 1)
    status = str(info.get("status") or "🎬 Extracting")
    return (
        f"📄 {filename[:32]}\n"
        f"📊 [{_progress_bar(int(pct))}] {pct:.1f}%\n"
        f"{status[:22]}\n"
        f"⏱ {_fmt_time(elapsed)}\n"
        f"⏳ {_fmt_time(remaining)}\n"
        f"⚡ {speed_text}\n"
        f"📦 {current}/{count}\n"
        f"👤 {str(username)[:20]}"
    )[:200]


def _stream_button_text(stream: dict, display_number: int) -> str:
    codec = str(stream.get("codec_name") or "Unknown").upper()
    kind = str(stream.get("codec_type") or "Stream").title()
    label = _stream_label(stream, display_number).split(" (", 1)[0]
    if kind == "Video" and not (stream.get("tags") or {}).get("title"):
        label = "None"
    short_labels = {
        "English": "Eng", "Hindi": "Hin", "Tamil": "Tam",
        "Telugu": "Tel", "Malayalam": "Mal", "Kannada": "Kan",
        "Marathi": "Mar", "Bengali": "Ben",
    }
    label = short_labels.get(label, label or "None")
    return f"{display_number} - {kind} - {label} - {codec}"


def _stream_menu(token: str, streams: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            _stream_button_text(stream, ordinal),
            callback_data=f"stream_extract:one:{token}:{stream.get('index', ordinal)}",
        )]
        for ordinal, stream in enumerate(streams)
    ]
    buttons.append([
        InlineKeyboardButton(
            "🎵 All Audios", callback_data=f"stream_extract:audio:{token}"
        ),
        InlineKeyboardButton(
            "💬 All Subtitles", callback_data=f"stream_extract:subtitle:{token}"
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            "📦 Custom Streams", callback_data=f"stream_extract:custom:{token}"
        ),
        InlineKeyboardButton(
            "📦 All Streams", callback_data=f"stream_extract:all:{token}"
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            "❌ Cancel", callback_data=f"stream_extractor_cancel:{token}"
        )
    ])
    return InlineKeyboardMarkup(buttons)


def _stream_custom_menu(pending: dict) -> InlineKeyboardMarkup:
    token = pending["token"]
    selected = {int(item) for item in pending.get("custom_selected", [])}
    streams = pending.get("streams") or []
    buttons = []
    for ordinal, stream in enumerate(streams):
        index = int(stream.get("index", ordinal))
        mark = "✅ " if index in selected else ""
        buttons.append([InlineKeyboardButton(
            mark + _stream_button_text(stream, ordinal),
            callback_data=f"stream_custom_toggle:{token}:{index}",
        )])
    buttons.append([
        InlineKeyboardButton(
            "✅ Extract Selected", callback_data=f"stream_custom_done:{token}"
        ),
        InlineKeyboardButton(
            "↩️ Back", callback_data=f"stream_extract:back:{token}"
        ),
    ])
    return InlineKeyboardMarkup(buttons)


def _stream_selection_note() -> str:
    return (
        "⭐ *Note:* In All Streams option all streams will be uploaded "
        "except Video.\n\n"
        "For single audio/video/subtitle, click the button that appears.\n\n"
        "Select Your Required Option 👇"
    )


async def _start_stream_extraction_from_callback(
    update, context, query, pending: dict, mode: str
):
    global _active_processes
    task_id = _secrets.token_hex(8)
    context.user_data["stream_extractor_task_id"] = task_id
    context.user_data["stream_extractor_mode"] = mode
    _active_processes += 1
    info = {
        "kind": "stream_extractor",
        "task_id": task_id,
        "process": None,
        "start_time": time.time(),
        "duration": 0.0,
        "total_duration": 0.0,
        "filename": pending["file_name"],
        "file_name": pending["file_name"],
        "message_id": query.message.message_id,
        "chat_id": query.message.chat_id,
        "speed_mbps": 0.0,
        "pct": 0.0,
        "elapsed": 0.0,
        "status": "🎬 Extracting",
        "phase": "extract",
        "running": True,
        "platform": "Stream Extractor",
        "channel": {"channelCategoryId": "Stream Extractor"},
        "user_obj": query.from_user,
        "user_id": query.from_user.id,
        "item_count": 1,
        "completed_items": 0,
    }
    RECORDING_PROGRESS_INFO[task_id] = info
    MEDIA_USER_TASKS[query.from_user.id] = task_id
    context.user_data["stream_extractor_progress"] = info
    await query.answer("Extraction started.", cache_time=0)
    await query.edit_message_text(
        _build_stream_status_text(
            pending["file_name"], 0.0, None, "🎬 Extracting"
        ),
        reply_markup=_build_stream_progress_inline(),
    )
    updater = asyncio.create_task(
        _auto_updater(
            task_id,
            query.message,
            None,
            pending["file_name"],
            0.0,
            info["start_time"],
        )
    )
    ACTIVE_UPDATERS[task_id] = updater
    asyncio.create_task(
        _stream_extract_job(
            update, context, pending, query.message, task_id, mode
        )
    )


def _stream_extension_safe(codec_type: str, codec_name: str) -> bool:
    # Copying common elementary streams is lossless. Unknown streams are still
    # attempted with -c copy, but the output remains a container when needed.
    return codec_type in {"audio", "subtitle", "video"} and bool(codec_name)


async def _stream_upload_one(context, info: dict, status_message, output_path: str):
    info["phase"] = "upload"
    info["status"] = "📤 Uploading"
    info["filename"] = os.path.basename(output_path)
    info["file_name"] = os.path.basename(output_path)
    info["upload_bytes"] = 0
    info["upload_size"] = os.path.getsize(output_path)
    info["upload_start"] = time.time()
    info["pct"] = min(99.9, info.get("completed_items", 0) / max(1, info["item_count"]) * 100)
    await status_message.edit_text(
        f"✅ Extraction Completed\n\n"
        f"📄 File:\n{os.path.basename(output_path)}\n\n"
        "Uploading...",
        reply_markup=_build_stream_progress_inline(),
    )
    with open(output_path, "rb") as raw:
        wrapped = _ProgressUploadFile(raw, info)
        telegram_input = InputFile(
            wrapped, filename=os.path.basename(output_path), read_file_handle=False
        )
        await context.bot.send_document(
            chat_id=status_message.chat_id,
            document=telegram_input,
            caption=os.path.basename(output_path),
            read_timeout=1800,
            write_timeout=1800,
        )
    if not info.get("running", True):
        return False
    await status_message.edit_text(
        "✅ Upload Completed\n\n"
        f"📄 File:\n{os.path.basename(output_path)}",
        reply_markup=_build_stream_progress_inline(),
    )
    info["phase"] = "extract"
    info["completed_items"] = int(info.get("completed_items") or 0) + 1
    info["pct"] = info["completed_items"] / max(1, info["item_count"]) * 100
    return True


async def _stream_extract_job(update, context, pending: dict,
                              status_message, task_id: str, mode: str):
    global _active_processes
    work_dir = f"/tmp/stream_extract_{task_id}"
    os.makedirs(work_dir, exist_ok=True)
    user = update.effective_user
    info = RECORDING_PROGRESS_INFO.get(task_id)
    input_path = os.path.join(work_dir, _media_safe_name(pending["file_name"], "input.mkv"))
    try:
        telegram_file = await context.bot.get_file(pending["file_id"])
        await telegram_file.download_to_drive(custom_path=input_path)
        probe = await _stream_probe_file(input_path)
        requested_streams = pending.get("streams") or probe["streams"]
        if mode == "one":
            selected_index = int(pending.get("selected_stream_index", -1))
            streams = [
                item for item in requested_streams
                if int(item.get("index", -1)) == selected_index
            ]
        elif mode == "custom":
            selected = {
                int(item) for item in pending.get("custom_selected", [])
            }
            streams = [
                item for item in requested_streams
                if int(item.get("index", -1)) in selected
            ]
        elif mode == "all":
            # “All Streams” intentionally excludes video, matching the
            # StreamExtractor menu shown to users.
            streams = [
                item for item in requested_streams
                if item.get("codec_type") != "video"
            ]
        else:
            streams = [
                item for item in requested_streams
                if item.get("codec_type") == mode
            ]
        if not streams:
            await status_message.edit_text(
                "❌ No matching streams found.\n\n"
                f"Requested: {mode.title()}"
            )
            return
        # Keep every audio track together in one multi-track container. The
        # previous per-stream map (`-map 0:<index>`) produced one-track files,
        # which made Telegram show only a single audio track.
        if mode == "audio":
            streams = [{
                "codec_type": "audio",
                "codec_name": "multi",
                "tags": {"title": "All Audios"},
                "_combined_audio": True,
            }]
        info["item_count"] = len(streams)
        info["total_duration"] = probe["duration"]
        info["source_path"] = input_path
        info["source_size"] = os.path.getsize(input_path)
        for ordinal, stream in enumerate(streams):
            info = RECORDING_PROGRESS_INFO.get(task_id)
            if not info or not info.get("running", True):
                return
            stream_index = int(stream.get("index", ordinal))
            stream_kind = stream.get("codec_type") or "stream"
            stream_name = (
                _media_safe_name(
                    f"{Path(pending['file_name']).stem}_all_audios.mkv"
                )
                if stream.get("_combined_audio")
                else _stream_output_name(pending["file_name"], stream, ordinal)
            )
            output_path = os.path.join(work_dir, stream_name)
            info["current_item"] = ordinal + 1
            info["current_item_duration"] = probe["duration"]
            info["current_stream_index"] = stream_index
            info["filename"] = stream_name
            info["file_name"] = stream_name
            info["status"] = f"🎬 Extracting {stream_kind}"
            info["phase"] = "extract"
            info["pct"] = (ordinal / len(streams)) * 100
            progress_file = os.path.join(work_dir, f"progress_{ordinal}.txt")
            info["progress_file"] = progress_file
            if stream.get("_combined_audio"):
                metadata = await build_ffmpeg_metadata(
                    input_path,
                    selected_streams={"audio": None},
                )
                cmd = [
                    "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                    "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
                    "-c", "copy",
                    "-progress", progress_file, "-nostats", output_path,
                ]
            else:
                stream_type = str(stream.get("codec_type") or "")
                source_type_ordinal = sum(
                    1
                    for prior in requested_streams
                    if prior is stream
                    or (
                        prior.get("codec_type") == stream_type
                        and int(prior.get("index", -1)) < stream_index
                    )
                ) - 1
                metadata = await build_ffmpeg_metadata(
                    input_path,
                    selected_streams={stream_type: [source_type_ordinal]},
                )
                cmd = [
                    "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                    "-map", f"0:{stream_index}", "-c", "copy",
                    "-progress", progress_file, "-nostats", output_path,
                ]
            cmd[-1:-1] = metadata
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            info["process"] = proc
            RECORDING_SESSION_PROC[task_id] = proc
            stderr_task = asyncio.create_task(proc.stderr.read())
            await proc.wait()
            stderr = await stderr_task
            RECORDING_SESSION_PROC.pop(task_id, None)
            info["process"] = None
            if not info.get("running", True):
                return
            if proc.returncode != 0 or not os.path.exists(output_path):
                detail = stderr.decode(errors="replace")[-700:]
                raise RuntimeError(f"{stream_kind} extraction failed: {detail}")
            uploaded = await _stream_upload_one(
                context, info, status_message, output_path
            )
            if not uploaded:
                return
            info["phase"] = "extract"
            info["status"] = "🎬 Extracting"
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info and info.get("running", True):
            await status_message.edit_text("✅ Upload Completed\n\n📄 All requested streams uploaded.")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Stream extraction failed")
        info = RECORDING_PROGRESS_INFO.get(task_id)
        cancelled = not info or not info.get("running", True)
        try:
            await status_message.edit_text(
                "❌ Extraction Cancelled\n\n⚠️ Partial Output Deleted"
                if cancelled else
                f"❌ Extraction Failed\n\n⚠️ Partial Output Deleted\n\n{str(exc)[:700]}"
            )
        except Exception:
            pass
    finally:
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["running"] = False
        proc = RECORDING_SESSION_PROC.pop(task_id, None)
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        updater = ACTIVE_UPDATERS.pop(task_id, None)
        if updater and not updater.done():
            updater.cancel()
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        MEDIA_USER_TASKS.pop(user.id, None)
        context.user_data.pop("stream_extractor_task_id", None)
        context.user_data.pop("stream_extractor_pending", None)
        context.user_data.pop("stream_extractor_mode", None)
        context.user_data.pop("stream_extractor_progress", None)
        STREAM_EXTRACTOR_PENDING.pop(pending.get("token"), None)
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)


@require_verification
async def stream_extractor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source = _media_reply_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to a video or video document with `/StreamExtractor`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if source.get("file_size") and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text(
            f"❌ The video exceeds the Telegram Bot API download limit of {telegram_limit_text()}."
        )
        return
    if not await check_process_slot(update):
        return
    token = _secrets.token_hex(8)
    probe_dir = f"/tmp/stream_probe_{token}"
    os.makedirs(probe_dir, exist_ok=True)
    probe_path = os.path.join(
        probe_dir, _media_safe_name(source["file_name"], "input.mkv")
    )
    try:
        telegram_file = await context.bot.get_file(source["file_id"])
        await telegram_file.download_to_drive(custom_path=probe_path)
        probe = await _stream_probe_file(probe_path)
        streams = [
            stream for stream in probe["streams"]
            if stream.get("codec_type") in {"video", "audio", "subtitle"}
        ]
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Stream detection failed.\n\n{str(exc)[:700]}"
        )
        shutil.rmtree(probe_dir, ignore_errors=True)
        return
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    if not streams:
        await update.message.reply_text("❌ No extractable streams were found in the video.")
        return
    pending = {
        "token": token,
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "file_id": source["file_id"],
        "file_name": source["file_name"],
        "file_size": source["file_size"],
        "streams": streams,
        "custom_selected": [],
        "created_at": time.time(),
    }
    STREAM_EXTRACTOR_PENDING[token] = pending
    context.user_data["stream_extractor_pending"] = pending
    await update.message.reply_text(
        _stream_selection_note(),
        reply_markup=_stream_menu(token, streams),
        parse_mode=ParseMode.MARKDOWN,
    )


async def stream_extractor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if query.data.startswith("stream_extractor_cancel:"):
        if len(parts) != 2:
            await query.answer("Invalid extractor request.", show_alert=True, cache_time=0)
            return
        token = parts[1]
        pending = STREAM_EXTRACTOR_PENDING.get(token)
        if not pending or pending["user_id"] != query.from_user.id:
            await query.answer("Extractor menu expired.", show_alert=True, cache_time=0)
            return
        STREAM_EXTRACTOR_PENDING.pop(token, None)
        context.user_data.pop("stream_extractor_pending", None)
        await query.answer("Cancelled.", cache_time=0)
        await query.edit_message_text("❌ Stream extraction cancelled.")
        return
    if len(parts) < 3:
        await query.answer("Invalid extractor request.", show_alert=True, cache_time=0)
        return
    mode, token = parts[1], parts[2]
    pending = STREAM_EXTRACTOR_PENDING.get(token)
    if not pending or time.time() - pending.get("created_at", 0) > 15 * 60:
        STREAM_EXTRACTOR_PENDING.pop(token, None)
        await query.answer("Extractor menu expired.", show_alert=True, cache_time=0)
        return
    if pending["user_id"] != query.from_user.id:
        await query.answer("❌ This menu belongs to another user.", show_alert=True, cache_time=0)
        return
    if mode == "back":
        await query.answer(cache_time=0)
        await query.edit_message_text(
            _stream_selection_note(),
            reply_markup=_stream_menu(token, pending.get("streams") or []),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if mode == "custom":
        pending["custom_selected"] = []
        await query.answer(cache_time=0)
        await query.edit_message_text(
            "📦 *Custom Streams*\n\nSelect one or more streams:",
            reply_markup=_stream_custom_menu(pending),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if mode == "one" and len(parts) == 4:
        try:
            selected_index = int(parts[3])
        except ValueError:
            await query.answer("Invalid stream.", show_alert=True, cache_time=0)
            return
        pending["selected_stream_index"] = selected_index
        await _start_stream_extraction_from_callback(
            update, context, query, pending, "one"
        )
        return
    if mode not in {"audio", "subtitle", "all", "custom"}:
        await query.answer("Invalid stream type.", show_alert=True, cache_time=0)
        return
    if mode == "custom":
        selected = pending.get("custom_selected") or []
        if not selected:
            await query.answer(
                "Select at least one stream first.",
                show_alert=True,
                cache_time=0,
            )
            return
    await _start_stream_extraction_from_callback(
        update, context, query, pending, mode
    )


async def stream_custom_toggle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid stream selection.", show_alert=True, cache_time=0)
        return
    token = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await query.answer("Invalid stream selection.", show_alert=True, cache_time=0)
        return
    pending = STREAM_EXTRACTOR_PENDING.get(token)
    if not pending or pending["user_id"] != query.from_user.id:
        await query.answer("Extractor menu expired.", show_alert=True, cache_time=0)
        return
    selected = {int(item) for item in pending.get("custom_selected", [])}
    if index in selected:
        selected.remove(index)
    else:
        selected.add(index)
    pending["custom_selected"] = sorted(selected)
    await query.answer("Updated.", cache_time=0)
    await query.edit_message_reply_markup(
        reply_markup=_stream_custom_menu(pending)
    )


async def stream_custom_done_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 2:
        await query.answer("Invalid stream selection.", show_alert=True, cache_time=0)
        return
    token = parts[1]
    pending = STREAM_EXTRACTOR_PENDING.get(token)
    if not pending or pending["user_id"] != query.from_user.id:
        await query.answer("Extractor menu expired.", show_alert=True, cache_time=0)
        return
    if not pending.get("custom_selected"):
        await query.answer(
            "Select at least one stream first.",
            show_alert=True,
            cache_time=0,
        )
        return
    await _start_stream_extraction_from_callback(
        update, context, query, pending, "custom"
    )
def _build_rec_progress_inline(task_id: str) -> InlineKeyboardMarkup:
    """Build the requested ⚡ Progress | ❌ Cancel inline keyboard."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ Progress", callback_data=f"progress:{task_id}"),
        InlineKeyboardButton("❌ Cancel",   callback_data=f"cancel:{task_id}"),
    ]])


def _build_stream_progress_inline() -> InlineKeyboardMarkup:
    """StreamExtractor uses the requested bare callback names."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ Progress", callback_data="progress"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])


def _build_default_audio_progress_inline() -> InlineKeyboardMarkup:
    """Default-audio updates resolve bare callbacks by message identity."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ Progress", callback_data="progress"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])


def _stream_task_for_message(query):
    for task_id, info in RECORDING_PROGRESS_INFO.items():
        if (
            info.get("kind") in {"stream_extractor", "default_audio"}
            and info.get("chat_id") == query.message.chat_id
            and info.get("message_id") == query.message.message_id
        ):
            return task_id, info
    return None, None


def _default_audio_name(stream: dict, ordinal: int) -> str:
    tags = stream.get("tags") or {}
    _, language_name = _ffmpeg_language(
        tags.get("language"),
        tags.get("title") or tags.get("handler_name"),
    )
    if language_name == "Und":
        language_name = str(
            tags.get("title") or tags.get("handler_name") or f"Audio {ordinal + 1}"
        ).strip()
    return language_name or f"Audio {ordinal + 1}"


def _default_audio_button_text(stream: dict, ordinal: int) -> str:
    language_name = _default_audio_name(stream, ordinal)
    flags = {
        "English": "🇬🇧", "Hindi": "🇮🇳", "Tamil": "🇮🇳",
        "Telugu": "🇮🇳", "Kannada": "🇮🇳", "Malayalam": "🇮🇳",
        "Marathi": "🇮🇳", "Gujarati": "🇮🇳", "Punjabi": "🇮🇳",
        "Bengali": "🇮🇳", "Odia": "🇮🇳", "Urdu": "🇮🇳",
    }
    default = " (Default)" if (stream.get("disposition") or {}).get("default") else ""
    return f"{flags.get(language_name, '🎵')} {language_name}{default}"


def _default_audio_menu(token: str, streams: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            _default_audio_button_text(stream, ordinal),
            callback_data=f"default_audio_select:{token}:{ordinal}",
        )]
        for ordinal, stream in enumerate(streams)
    ]
    buttons.append([
        InlineKeyboardButton(
            "❌ Cancel", callback_data=f"default_audio_cancel:{token}"
        )
    ])
    return InlineKeyboardMarkup(buttons)


def _build_default_audio_status_text(
    filename: str,
    state: str,
    audio_name: str = "",
    pct: float = 0.0,
    uploaded: int = 0,
    total: int = 0,
    speed_mbps: float = 0.0,
    remaining: float = 0.0,
) -> str:
    if state == "scan":
        return (
            f"📄 File:\n{filename}\n\n"
            "🔍 Scanning audio tracks...\n\n"
            "Please wait..."
        )
    if state == "select":
        return f"📄 File:\n{filename}\n\n🎵 Select Default Audio 👇"
    if state == "updating":
        return (
            "🎬 Changing Default Audio...\n\n"
            f"📄 File:\n{filename}\n\n"
            "Status:\nUpdating Audio Flags...\n\n"
            "⚡ Speed:\nCalculating...\n\n"
            "⏳ Time Left:\nCalculating..."
        )
    if state == "upload":
        sent_mb = uploaded / 1024 / 1024
        total_mb = total / 1024 / 1024
        speed_text = "Calculating..." if not speed_mbps else f"{speed_mbps:.2f} MB/s"
        return (
            f"Uploading:\n{filename}\n\n"
            f"[{_progress_bar(int(pct))}] {pct:.2f}%\n\n"
            f"Uploaded:\n{sent_mb:.2f} MB / {total_mb:.2f} MB\n\n"
            f"⚡ Speed:\n{speed_text}\n\n"
            f"⏳ Time Left:\n{_fmt_time(remaining)}"
        )
    return (
        "✅ Default Audio Updated\n\n"
        f"📄 File:\n{filename}\n\n"
        f"🎵 New Default Audio:\n{audio_name}\n\n"
        "Uploading..."
    )


def _build_default_audio_popup_text(task_id: str) -> str:
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        return "⚠️ Default audio update is no longer active."
    user = info.get("user_obj")
    username = (
        f"@{user.username}" if user and user.username
        else (user.first_name if user else "Unknown")
    )
    filename = str(info.get("filename") or "Unknown")
    pct = float(info.get("pct") or 0.0)
    speed = float(info.get("speed_mbps") or 0.0)
    if info.get("phase") == "upload":
        uploaded = int(info.get("upload_bytes") or 0)
        total = int(info.get("upload_size") or 0)
        remaining = float(info.get("upload_remaining") or 0.0)
        text = (
            f"📄 {filename[:28]}\n"
            f"Progress:\n[{_progress_bar(int(pct))}] {pct:.1f}%\n"
            f"Uploaded:\n{uploaded / 1024 / 1024:.2f} / {total / 1024 / 1024:.2f} MB\n"
            f"⚡ {'Calculating...' if not speed else f'{speed:.2f} MB/s'}\n"
            f"⏳ {_fmt_time(remaining)}\n"
        )
    else:
        text = (
            f"📄 {filename[:28]}\n"
            f"Progress:\n[{_progress_bar(int(pct))}] {pct:.2f}%\n"
            f"Status:\n{str(info.get('status') or '🎬 Updating Audio Flags')[:24]}\n"
            f"Elapsed:\n{_fmt_time(float(info.get('elapsed') or 0.0))}\n"
            "Remaining:\nCalculating...\n"
            f"Speed:\n{'Calculating...' if not speed else f'{speed:.2f} MB/s'}\n"
        )
    return (text + f"👤 User:\n{str(username)[:18]}")[:200]


def _build_popup_text(task_id: str) -> str:
    """Build fresh live details within Telegram's 200-character alert limit."""
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        return "⚠️ Recording info not available."
    filename    = info.get("filename", "Unknown")
    pct         = info.get("pct", 0.0)
    elapsed     = info.get("elapsed", 0.0)
    total       = info.get("total_duration", 0.0)
    speed       = info.get("speed_mbps", 0.0)
    channel     = info.get("channel", {})
    user_obj    = info.get("user_obj")
    bar         = _progress_bar(int(pct), width=10)
    recorded    = min(max(0.0, elapsed), total) if total else max(0.0, elapsed)
    remaining   = max(0.0, total - recorded) if total else 0.0
    platform    = info.get("platform") or channel.get("channelCategoryId", "Unknown")
    status      = info.get("status", "🎥 Recording")
    if user_obj:
        username = f"@{user_obj.username}" if user_obj.username else user_obj.first_name
        user_id  = user_obj.id
    else:
        username = "Unknown"
        user_id  = "Unknown"

    # answerCallbackQuery alerts are limited to 200 characters by Telegram.
    # Keep the latest values, but bound user-controlled strings so a long
    # filename or username can never make the popup fail.
    def clip(value, limit):
        value = str(value)
        return value if len(value) <= limit else value[:limit - 1] + "…"

    speed_text = "Calculating..." if not speed else f"{speed:.2f} MB/s"
    total_text = _fmt_time(total) if total else "--:--:--"
    return (
        f"📄 {clip(filename, 30)}\n"
        f"📊 [{bar}] {pct:.1f}%\n"
        f"{clip(status, 22)}\n"
        f"⏺ {_fmt_time(recorded)} / {total_text}\n"
        f"⏳ {_fmt_time(remaining)}\n"
        f"⚡ {speed_text}\n"
        f"📡 {clip(platform, 14)} | 👤 {clip(username, 18)}\n"
        f"🆔 {clip(user_id, 12)}"
    )[:200]


async def _auto_updater(task_id: str, msg, progress_file: str | None,
                        filename: str, duration_seconds: float,
                        rec_start: float) -> None:
    """Auto-update the recording progress message every 5 seconds."""
    try:
        while True:
            await asyncio.sleep(5)
            info = RECORDING_PROGRESS_INFO.get(task_id)
            if not info or not info.get("running", True):
                break
            current_progress_file = info.get("progress_file") or progress_file
            pg          = _read_ffmpeg_progress(current_progress_file) if current_progress_file else {}
            wall_elapsed = time.time() - rec_start
            out_us      = _progress_int(pg.get("out_time_us"), 0)
            current_duration = float(info.get("total_duration") or duration_seconds or 0.0)
            source_path = info.get("source_path")
            source_size = _progress_int(info.get("source_size"), 0)
            source_bytes = (
                os.path.getsize(source_path)
                if source_path and os.path.exists(source_path)
                else 0
            )
            media_elapsed = out_us / 1_000_000 if out_us > 0 else 0.0
            if current_duration:
                media_elapsed = min(media_elapsed, current_duration)
            pct         = (
                min(99.0, media_elapsed / max(current_duration, 1) * 100)
                if current_duration else
                min(99.0, source_bytes / source_size * 100)
                if source_size else 0.0
            )
            total_bytes = _progress_int(pg.get("total_size"), 0)
            if not total_bytes:
                total_bytes = source_bytes
            speed_mbps  = (
                (total_bytes / 1024 / 1024) / max(wall_elapsed, 1)
                if total_bytes else info.get("speed_mbps", 0.0)
            )
            # Update shared info so the popup always has fresh values
            info["pct"]        = pct
            info["elapsed"]    = media_elapsed
            info["speed_mbps"] = speed_mbps
            info["process"]    = RECORDING_SESSION_PROC.get(task_id)
            if (
                info.get("phase") == "upload"
                and info.get("kind") in {
                    "merge", "media", "stream_extractor", "default_audio"
                }
            ):
                upload_total = int(info.get("upload_size") or 0)
                upload_sent = int(info.get("upload_bytes") or 0)
                upload_started = float(info.get("upload_start") or rec_start)
                upload_elapsed = max(0.1, time.time() - upload_started)
                upload_pct = (
                    min(99.9, upload_sent / upload_total * 100)
                    if upload_total else 0.0
                )
                upload_speed = upload_sent / 1024 / 1024 / upload_elapsed
                info["pct"] = upload_pct
                info["elapsed"] = upload_elapsed
                info["speed_mbps"] = upload_speed
                remaining = int(
                    max(0, (upload_total - upload_sent) / 1024 / 1024 / upload_speed)
                ) if upload_speed > 0 and upload_total else 0
                text = _build_upload_status_text(
                    info.get("filename") or filename, upload_pct, upload_sent, upload_total,
                    upload_speed, remaining,
                )
                if info.get("kind") == "default_audio":
                    info["upload_remaining"] = remaining
                    text = _build_default_audio_status_text(
                        info.get("filename") or filename,
                        "upload",
                        info.get("audio_name", ""),
                        upload_pct,
                        upload_sent,
                        upload_total,
                        upload_speed,
                        remaining,
                    )
            elif info.get("kind") == "stream_extractor":
                item_duration = float(
                    info.get("current_item_duration")
                    or info.get("total_duration")
                    or duration_seconds
                    or 0.0
                )
                item_pct = (
                    min(99.9, media_elapsed / max(item_duration, 1) * 100)
                    if item_duration else 0.0
                )
                completed = int(info.get("completed_items") or 0)
                item_count = max(1, int(info.get("item_count") or 1))
                pct = min(99.9, ((completed + item_pct / 100) / item_count) * 100)
                info["pct"] = pct
                info["elapsed"] = media_elapsed
                info["total_duration"] = item_duration
                text = _build_stream_status_text(
                    info.get("filename") or filename,
                    pct,
                    speed_mbps,
                    info.get("status", "🎬 Extracting"),
                    wall_elapsed,
                    max(0.0, item_duration - media_elapsed),
                )
            elif info.get("kind") == "merge":
                text = _build_merge_status_text(
                    filename, pct, speed_mbps, info.get("status", "🎬 Merging")
                )
            elif info.get("kind") == "media":
                text = _build_media_status_text(
                    filename, pct, speed_mbps, info.get("status", "Processing...")
                )
            elif info.get("kind") == "default_audio":
                text = _build_default_audio_status_text(
                    filename,
                    "updating",
                    info.get("audio_name", ""),
                    pct,
                    speed_mbps=speed_mbps,
                )
            else:
                text = _build_rec_status_text(
                    filename, pct, speed_mbps, info.get("status", "Recording...")
                )
            keyboard = (
                _build_default_audio_progress_inline()
                if info.get("kind") == "default_audio"
                else
                _build_stream_progress_inline()
                if info.get("kind") == "stream_extractor"
                else _build_rec_progress_inline(task_id)
            )
            try:
                await msg.edit_text(
                    text,
                    parse_mode=None
                    if info.get("kind") in {
                        "media", "stream_extractor", "default_audio"
                    }
                    else ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


# ── Progress / Cancel inline callbacks ───────────────────────────────────────

async def rec_progress_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚡ Progress button — show the latest details without cached alerts."""
    query = update.callback_query
    if query.data == "progress":
        task_id, info = _stream_task_for_message(query)
    else:
        task_id = query.data.split(":", 1)[1]
        info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        await query.answer("⚠️ Process already ended.", show_alert=True, cache_time=0)
        return
    popup = (
        _build_merge_popup_text(task_id)
        if info and info.get("kind") == "merge"
        else _build_stream_popup_text(task_id)
        if info and info.get("kind") == "stream_extractor"
        else _build_default_audio_popup_text(task_id)
        if info and info.get("kind") == "default_audio"
        else _build_popup_text(task_id)
    )
    await query.answer(text=popup, show_alert=True, cache_time=0)


async def rec_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """❌ Cancel button — terminate the active FFmpeg process immediately."""
    query = update.callback_query
    if query.data == "cancel":
        task_id, info = _stream_task_for_message(query)
    else:
        task_id = query.data.split(":", 1)[1]
        info = RECORDING_PROGRESS_INFO.get(task_id)
    if not info:
        await query.answer("⚠️ Process already ended.", show_alert=True, cache_time=0)
        return
    # Only the user who started it, or owner/admin, may cancel
    uid = query.from_user.id
    if uid != info.get("user_id") and not is_owner(uid) and not is_admin(uid):
        await query.answer(
            "❌ Only the user who started the recording can cancel it.",
            show_alert=True,
        )
        return
    info["running"] = False
    info["status"] = "❌ Cancelling..."
    # Kill current FFmpeg process
    proc = RECORDING_SESSION_PROC.get(task_id) or info.get("process")
    if proc and proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
    # Remove cancel_id from ACTIVE_RECORDINGS so the monitoring loop detects cancellation
    cancel_id = info.get("cancel_id", "")
    ACTIVE_RECORDINGS.pop(cancel_id, None)
    # Stop the auto-updater task
    task = ACTIVE_UPDATERS.pop(task_id, None)
    if task and not task.done():
        task.cancel()
    if info.get("kind") == "stream_extractor":
        try:
            await query.edit_message_text(
                "❌ Extraction Cancelled\n\n⚠️ Partial Output Deleted"
            )
        except Exception:
            pass
        await query.answer("Extraction cancelling...", cache_time=0)
        return
    if info.get("kind") == "default_audio":
        info["cancel_message_sent"] = True
        context.user_data["default_audio_cancelled"] = True
        pending = context.user_data.get("default_audio_pending") or {}
        job_task = pending.get("job_task")
        if job_task and not job_task.done():
            job_task.cancel()
        try:
            await query.edit_message_text(
                "❌ Default Audio Update Cancelled\n\n"
                "⚠️ Partial Output Deleted"
            )
        except Exception:
            pass
        await query.answer("Cancelling...", cache_time=0)
        return
    message = (
        "⏳ Cancelling merge..."
        if info.get("kind") == "merge"
        else "⏳ Cancelling recording..."
    )
    await query.answer(message, cache_time=0)
    # The owning job handles partial upload and the final message update.


# ── End new progress system ───────────────────────────────────────────────────


async def probe_audio_tracks(stream_url: str, cookie: str = "",
                             user_agent: str = "", cenc_key: str = "") -> list:
    """Probe audio streams and return their ordinal index plus readable labels."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a",
           "-show_entries", "stream_tags=language,title",
           "-of", "json"]
    if cookie or user_agent:
        headers = ""
        if cookie:
            headers += f"Cookie: {cookie}\r\n"
        if user_agent:
            headers += f"User-Agent: {user_agent}\r\n"
        cmd += ["-headers", headers]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if cookie:
        cmd += ["-cookies", cookie]
    if cenc_key:
        cmd += ["-cenc_decryption_key", cenc_key]
    cmd += [stream_url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        if proc.returncode != 0:
            return []
        streams = json.loads(stdout.decode(errors="replace")).get("streams", [])
    except (asyncio.TimeoutError, json.JSONDecodeError, OSError):
        return []

    tracks = []
    for ordinal, stream in enumerate(streams):
        tags = stream.get("tags") or {}
        language = str(tags.get("language", "")).lower()
        title = str(tags.get("title", "")).strip()
        text = f"{language} {title}".lower()
        name = next(
            (label for key, label in (
                ("hin", "Hindi"), ("hindi", "Hindi"),
                ("hi", "Hindi"),
                ("tam", "Tamil"), ("tamil", "Tamil"),
                ("ta", "Tamil"),
                ("tel", "Telugu"), ("telugu", "Telugu"),
                ("te", "Telugu"),
                ("kan", "Kannada"), ("kannada", "Kannada"),
                ("kn", "Kannada"),
                ("mal", "Malayalam"), ("ml", "Malayalam"),
                ("mar", "Marathi"), ("mr", "Marathi"),
                ("eng", "English"), ("english", "English"),
                ("en", "English"),
                ("ori", "Odia"), ("odia", "Odia"),
                ("or", "Odia"),
            ) if key in text),
            title or language.upper() or f"Audio {ordinal + 1}",
        )
        language_code = next(
            (
                code for code, label in (
                    ("hin", "Hindi"), ("tam", "Tamil"), ("tel", "Telugu"),
                    ("kan", "Kannada"), ("mal", "Malayalam"),
                    ("mar", "Marathi"), ("eng", "English"), ("ori", "Odia"),
                )
                if label.lower() == name.lower()
            ),
            language if len(language) in (2, 3) else "",
        )
        tracks.append({
            "index": ordinal,
            "name": name,
            "language_code": language_code,
        })
    return tracks


def _audio_statuses(tracks):
    """Build the requested language status line from ffprobe results."""
    names = {track["name"].lower() for track in tracks}
    return " ".join(
        f"{language} {'✅' if language.lower() in names else '❎'}"
        for language in (
            "Hindi", "Tamil", "Telugu", "Malayalam", "Kannada",
            "Marathi", "English", "Odia",
        )
    )


def _audio_index_for_language(tracks, language):
    language = language.lower()
    for track in tracks:
        if track["name"].lower() == language:
            return track["index"]
    return tracks[0]["index"] if tracks else 0


def _audio_options(audio_mode: str, audio_index: int,
                   audio_tracks: list | None = None) -> list:
    """Return FFmpeg audio maps for the requested output tracks."""
    if audio_mode == "multi" and not audio_tracks:
        return [
            "-map", "0:a?",
            "-disposition:a:0", "default",
        ]
    if audio_mode != "multi":
        return [
            "-map", f"0:a:{max(0, int(audio_index))}?",
            "-disposition:a:0", "default",
        ]

    options = []
    for output_index, track in enumerate(audio_tracks):
        source_index = max(0, int(track.get("index", output_index)))
        options += ["-map", f"0:a:{source_index}?"]
        options += [
            f"-disposition:a:{output_index}",
            "default" if output_index == 0 else "0",
        ]
    return options


def _recording_bitrates(duration_seconds: int, audio_mode: str,
                        audio_tracks: list | None = None) -> tuple[str, str]:
    """Choose duration-aware bitrates for 576p multi-audio recordings."""
    track_count = (
        len(audio_tracks) if audio_mode == "multi" and audio_tracks
        else 1
    )
    audio_bitrate = 64_000
    # Keep room for MP4 overhead and bitrate variance instead of spending the
    # entire file-size limit on nominal media bitrate.
    # Keep bitrate proportional to the requested duration while retaining
    # good 576p quality. The Telegram/local Bot API limit is enforced by the
    # upload client, not by an arbitrary 500 MB application cutoff.
    budget_bps = 2_800_000
    reserved_audio_bps = track_count * audio_bitrate
    video_bps = int(budget_bps - reserved_audio_bps)
    video_bps = max(350_000, min(2_500_000, video_bps))
    return f"{video_bps // 1000}k", f"{audio_bitrate // 1000}k"


async def _edit_callback_message(query, text, **kwargs):
    """Edit a callback message without logging harmless no-op edit errors."""
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as exc:
        if "Message is not modified" not in str(exc):
            raise


async def start_recording(stream_url: str, duration_seconds: int, output_path: str,
                          cancel_id: str, cookie: str = "", user_agent: str = "",
                          license_url: str = "", quality: str = "1080p",
                          aspect: str = "16:9", audio_index: int = 0,
                          audio_mode: str = DEFAULT_AUDIO_MODE,
                          audio_tracks: list | None = None,
                          cenc_key: str = "",
                          key_stream_url: str = "",
                          watermark_path: str = "",
                          airtel_overlay: bool = False,
                          ott_watermark: bool = False,
                          dishtv_channel: bool = True):
    """Start FFmpeg as a non-blocking subprocess. Returns (proc, progress_file)."""
    progress_file = f"/tmp/ffmpeg_prog_{cancel_id}.txt"
    # Live feeds can contain incomplete segments or temporarily corrupt packets.
    # FFmpeg must fail promptly; the outer recording job handles one fresh
    # signed-stream retry instead of reconnecting inside this process.
    live_input_options = [
        "-fflags", "+discardcorrupt+genpts",
        "-analyzeduration", "10M",
        "-probesize", "10M",
        "-thread_queue_size", "4096",
        # Do not let a stalled CDN segment hold a short recording for minutes.
        "-rw_timeout", "8000000",
    ]
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if cookie or user_agent:
        header_str = ""
        if cookie:
            header_str += f"Cookie: {cookie}\r\n"
        if user_agent:
            header_str += f"User-Agent: {user_agent}\r\n"
        cmd += ["-headers", header_str]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if cookie:
        cmd += ["-cookies", cookie]
    if cenc_key:
        cmd += ["-cenc_decryption_key", cenc_key]
    elif license_url:
        key_hex = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_cenc_key, key_stream_url or stream_url,
            license_url, cookie, user_agent
        )
        if key_hex:
            cmd += ["-cenc_decryption_key", key_hex]

    input_options = list(live_input_options)
    if os.path.isfile(stream_url):
        # A trimmed local MPD still references the signed HTTPS media
        # segments through BaseURL. Allow both the local manifest and its
        # remote DASH children.
        cmd += [
            "-protocol_whitelist",
            "file,https,tcp,tls,crypto,data",
        ]
    else:
        input_options = live_input_options

    watermark_input = ""   # WM1: Cew1rV1.png — DishTV only
    watermark2_input = ""  # WM2: CuMJCjn.md.png — last 2 min only, configurable position
    dishtv_last2min = False
    ott_watermark_input = ""
    ott_watermark_last2min = False
    # Airtel/Sun NXT use the shared watermark settings, without entering any
    # DishTV watermark branch below.
    if ott_watermark:
        # The OTT menu owns watermark behavior for both providers. In
        # particular, Disable must also suppress the legacy Airtel overlay.
        airtel_overlay = False
        ott_settings = get_ott_watermark_settings()
        if ott_settings.get("enabled", True):
            ott_url = ott_settings.get("custom_url") or OTT_WATERMARK_URL
            ott_watermark_last2min = bool(ott_settings.get("last_2min", True))
            ott_cache_file = _ott_watermark_cache_file(ott_url)
            ott_watermark_input = await asyncio.get_running_loop().run_in_executor(
                None, _get_watermark_input, ott_url, ott_cache_file
            )
    # Preserve the existing Airtel overlay path for callers that do not opt
    # into the new OTT watermark menu.
    elif airtel_overlay:
        overlay_local = await asyncio.get_running_loop().run_in_executor(
            None, _get_watermark_input, AIRTEL_LAST2MIN_OVERLAY_URL, AIRTEL_LAST2MIN_OVERLAY_URL
        )
    elif dishtv_channel:
        overlay_local = ""
        # WM1 is always downloaded for DishTV regardless of settings
        watermark_input = await asyncio.get_running_loop().run_in_executor(
            None, _get_watermark_input
        )
        # WM2 only when last_2min is enabled in settings
        wm_settings = get_watermark_settings()
        dishtv_last2min = wm_settings.get("last_2min", False)
        if dishtv_last2min:
            wm2_url = wm_settings.get("custom_url") or DISHTV_WM2_URL
            watermark2_input = await asyncio.get_running_loop().run_in_executor(
                None, _get_watermark_input, wm2_url, wm2_url
            )
    else:
        # Direct recordings without a known DishTV provider must not receive
        # the DishTV watermark or any provider-specific overlay.
        overlay_local = ""

    # DishTV keeps its existing 576p path. OTT quality buttons control only
    # Airtel/Sun NXT output dimensions.
    height = (
        {"480p": 480, "720p": 720, "1080p": 1080}.get(quality, 576)
        if ott_watermark else 576
    )
    video_bitrate, audio_bitrate = _recording_bitrates(
        duration_seconds, audio_mode, audio_tracks
    )
    if ott_watermark and ott_watermark_input:
        overlay_start = max(0, duration_seconds - 120)
        ott_settings = get_ott_watermark_settings()
        watermark_mode = ott_settings["mode"]
        watermark_offset = ott_settings["offset"]
        if watermark_mode == "top_center":
            watermark_xy = "(W-w)/2:60"
        elif watermark_mode == "center":
            watermark_xy = "(W-w)/2:(H-h)/2"
        elif watermark_mode == "left_bottom":
            watermark_xy = "20:H-h-20"
        elif watermark_mode == "right":
            watermark_xy = f"W-w-{watermark_offset}:H-h-20"
        else:
            watermark_xy = f"{watermark_offset}:H-h-20"
        if aspect == "4:6":
            base_filter = (
                f"[0:v:0]crop=ih*2/3:ih:(iw-ih*2/3)/2:0,"
                f"scale=-2:{height}[base]"
            )
        else:
            base_filter = f"[0:v:0]scale=-2:{height}[base]"
        audio_options = _audio_options(audio_mode, audio_index, audio_tracks)
        enable_expr = (
            f":enable='gte(t,{overlay_start})'"
            if ott_watermark_last2min else ""
        )
        cmd += [
            *input_options,
            "-i", stream_url,
            "-loop", "1", "-i", ott_watermark_input,
            "-t", str(duration_seconds),
            "-filter_complex",
            (
                f"{base_filter};"
                f"[1:v]scale=160:-1[wm];"
                f"[base][wm]overlay={watermark_xy}{enable_expr}[outv]"
            ),
            "-map", "[outv]",
            *audio_options,
            "-map", "0:s?", "-map", "0:t?",
            "-c:s", "copy", "-c:t", "copy",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "libx264", "-b:v", video_bitrate,
            "-maxrate", video_bitrate, "-bufsize", f"{int(video_bitrate[:-1]) * 2}k",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-r", "25",
            "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", progress_file,
            output_path,
        ]
    elif airtel_overlay and overlay_local:
        # Show the overlay image only during the last 2 minutes of the recording.
        # If the recording is ≤ 2 min the overlay shows for the whole clip.
        overlay_start = max(0, duration_seconds - 120)
        if aspect == "4:6":
            base_filter = (
                f"[0:v:0]crop=ih*2/3:ih:(iw-ih*2/3)/2:0,"
                f"scale=-2:{height}[base]"
            )
        else:
            base_filter = f"[0:v:0]scale=-2:{height}[base]"
        audio_options = _audio_options(audio_mode, audio_index, audio_tracks)
        cmd += [
            *input_options,
            "-i", stream_url,
            "-loop", "1",
            "-i", overlay_local,
            "-t", str(duration_seconds),
            "-filter_complex",
            (
                f"{base_filter};"
                f"[1:v]scale=240:-1[wm];"
                f"[base][wm]overlay=(W-w)/2:60:enable='gte(t,{overlay_start})'[outv]"
            ),
            "-map", "[outv]",
            *audio_options,
            "-map", "0:s?", "-map", "0:t?",
            "-c:s", "copy", "-c:t", "copy",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "libx264", "-b:v", video_bitrate,
            "-maxrate", video_bitrate, "-bufsize", f"{int(video_bitrate[:-1]) * 2}k",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-r", "25",
            "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", progress_file,
            output_path,
        ]
    elif watermark_input and watermark2_input:
        # DishTV — WM1 (Cew1rV1.png) full video fixed bottom-right
        #        + WM2 (CuMJCjn.md.png) last-2-min configurable position
        last2_start = max(0, duration_seconds - 120)
        watermark_mode, watermark_offset = get_watermark_position()
        if watermark_mode == "top_center":
            wm2_scale = 160
            wm2_xy = "(W-w)/2:60"
        elif watermark_mode == "center":
            wm2_scale = 160
            wm2_xy = "(W-w)/2:(H-h)/2"
        elif watermark_mode == "left_bottom":
            wm2_scale = 160
            wm2_xy = "20:H-h-20"
        elif watermark_mode == "right":
            wm2_scale = 100
            wm2_xy = f"main_w-overlay_w-{watermark_offset}:main_h-overlay_h-20"
        else:
            wm2_scale = 100
            wm2_xy = f"{watermark_offset}:main_h-overlay_h-20"
        if aspect == "4:6":
            base_filter = (
                f"[0:v:0]crop=ih*2/3:ih:(iw-ih*2/3)/2:0,"
                f"scale=-2:{height}[base]"
            )
        else:
            base_filter = f"[0:v:0]scale=-2:{height}[base]"
        audio_options = _audio_options(audio_mode, audio_index, audio_tracks)
        cmd += [
            *input_options,
            "-i", stream_url,
            "-loop", "1", "-i", watermark_input,
            "-loop", "1", "-i", watermark2_input,
            "-t", str(duration_seconds),
            "-filter_complex",
            (
                f"{base_filter};"
                f"[1:v]scale=100:-1[wm1];"
                f"[base][wm1]overlay=main_w-overlay_w-60:main_h-overlay_h-20[base_wm];"
                f"[2:v]scale={wm2_scale}:-1[wm2];"
                f"[base_wm][wm2]overlay={wm2_xy}:enable='gte(t,{last2_start})'[outv]"
            ),
            "-map", "[outv]",
            *audio_options,
            "-map", "0:s?", "-map", "0:t?",
            "-c:s", "copy", "-c:t", "copy",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "libx264", "-b:v", video_bitrate,
            "-maxrate", video_bitrate, "-bufsize", f"{int(video_bitrate[:-1]) * 2}k",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-r", "25",
            "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", progress_file,
            output_path,
        ]
    elif watermark_input:
        # DishTV — WM1 (Cew1rV1.png) only, full video, fixed 100px bottom-right
        if aspect == "4:6":
            base_filter = (
                f"[0:v:0]crop=ih*2/3:ih:(iw-ih*2/3)/2:0,"
                f"scale=-2:{height}[base]"
            )
        else:
            base_filter = f"[0:v:0]scale=-2:{height}[base]"
        audio_options = _audio_options(audio_mode, audio_index, audio_tracks)
        cmd += [
            *input_options,
            "-i", stream_url,
            "-loop", "1", "-i", watermark_input,
            "-t", str(duration_seconds),
            "-filter_complex",
            (
                f"{base_filter};"
                f"[1:v]scale=100:-1[wm1];"
                f"[base][wm1]overlay=main_w-overlay_w-60:main_h-overlay_h-20[outv]"
            ),
            "-map", "[outv]",
            *audio_options,
            "-map", "0:s?", "-map", "0:t?",
            "-c:s", "copy", "-c:t", "copy",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "libx264", "-b:v", video_bitrate,
            "-maxrate", video_bitrate, "-bufsize", f"{int(video_bitrate[:-1]) * 2}k",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-r", "25",
            "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", progress_file,
            output_path,
        ]
    else:
        audio_options = _audio_options(audio_mode, audio_index, audio_tracks)
        video_filter = (
            f"crop=ih*2/3:ih:(iw-ih*2/3)/2:0,scale=-2:{height}"
            if aspect == "4:6" else f"scale=-2:{height}"
        )
        cmd += [
            *input_options,
            "-i", stream_url,
            "-t", str(duration_seconds),
            "-map", "0:v:0",
            *audio_options,
            "-map", "0:s?", "-map", "0:t?",
            "-c:s", "copy", "-c:t", "copy",
            "-map_metadata", "0", "-map_chapters", "0",
            "-vf", video_filter,
            "-c:v", "libx264", "-b:v", video_bitrate,
            "-maxrate", video_bitrate, "-bufsize", f"{int(video_bitrate[:-1]) * 2}k",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-r", "25",
            "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", progress_file,
            output_path,
        ]

    probe_args = []
    if cookie or user_agent:
        probe_headers = ""
        if cookie:
            probe_headers += f"Cookie: {cookie}\r\n"
        if user_agent:
            probe_headers += f"User-Agent: {user_agent}\r\n"
        probe_args += ["-headers", probe_headers]
    if user_agent:
        probe_args += ["-user_agent", user_agent]
    if cookie:
        probe_args += ["-cookies", cookie]
    selected_audio = (
        [int(track.get("index", ordinal)) for ordinal, track in enumerate(audio_tracks)]
        if audio_mode == "multi" and audio_tracks
        else None
        if audio_mode == "multi"
        else [max(0, int(audio_index))]
    )
    metadata = await build_ffmpeg_metadata(
        stream_url,
        probe_args=probe_args,
        selected_streams={
            "video": [0],
            "audio": selected_audio,
            "subtitle": None,
        },
    )
    cmd[-1:-1] = metadata

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    return proc, progress_file


async def validate_recording_file(path: str) -> tuple[bool, str]:
    """Decode the complete output before upload so broken MP4s never reach Telegram."""
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        return False, "Output file missing or empty."
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error", "-xerror",
        "-i", path,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-f", "null", "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode(errors="replace")[-1200:]
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, "Output validation timed out."
    except OSError as exc:
        return False, str(exc)


async def _generate_recording_thumbnail(
    input_path: str, output_path: str, duration_seconds: int
) -> str:
    """Generate a small Telegram-compatible JPEG thumbnail from a recording."""
    seek_seconds = max(0, int(duration_seconds) - 5)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(seek_seconds),
        "-i", input_path,
        "-frames:v", "1",
        "-vf", "scale=640:-2",
        "-q:v", "2",
        "-an",
        output_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0 and os.path.exists(output_path):
            return output_path
        logger.warning(
            "Recording thumbnail generation failed: %s",
            stderr.decode(errors="replace")[-500:],
        )
    except (asyncio.TimeoutError, OSError) as exc:
        logger.warning("Recording thumbnail generation failed: %s", exc)
        try:
            proc.kill()
            await proc.wait()
        except (UnboundLocalError, ProcessLookupError):
            pass
    return ""


async def _run_recording_attempt(update, context, channel, duration_seconds,
                                 quality, aspect, audio_index, audio_mode,
                                 audio_tracks, msg,
                                 out_file, cancel_id, session_id):
    """Run one guarded FFmpeg attempt and return status, stderr, and media time."""
    proc = None
    progress_file = None
    stderr_task = None
    updater_task = None
    try:
        proc, progress_file = await start_recording(
            channel["stream_url"], duration_seconds, out_file, cancel_id,
            cookie=channel.get("cookie", ""),
            user_agent=channel.get("user_agent", ""),
            license_url=channel.get("license_key", ""),
            cenc_key=channel.get("cenc_key", ""),
            quality=quality, aspect=aspect, audio_index=audio_index,
            audio_mode=audio_mode,
            audio_tracks=audio_tracks,
            key_stream_url=channel.get("key_stream_url", ""),
            watermark_path=channel.get("watermark_path", ""),
            airtel_overlay=channel.get("airtel_overlay", False),
            ott_watermark=channel.get("source") in {"airtel", "sunnxt"},
            dishtv_channel=channel.get("source") == "dishtv",
        )
        ACTIVE_RECORDINGS[cancel_id] = proc
        RECORDING_SESSION_PROC[session_id] = proc
        stderr_task = asyncio.create_task(proc.stderr.read())

        user_obj  = update.effective_user
        filename  = os.path.basename(out_file)
        rec_start = time.time()

        # Initialise / refresh progress info for this attempt
        progress_info = RECORDING_PROGRESS_INFO.setdefault(session_id, {})
        progress_info.update({
            "process":         proc,
            "start_time":      progress_info.get("start_time", rec_start),
            "duration":        float(duration_seconds),
            "message_id":      getattr(msg, "message_id", None),
            "chat_id":         getattr(getattr(msg, "chat", None), "id", update.effective_chat.id),
            "speed":           0.0,
            "filename":       filename,
            "pct":            0.0,
            "elapsed":        0.0,
            "speed_mbps":     0.0,
            "total_duration": float(duration_seconds),
            "channel":        channel,
            "platform":       channel.get("channelCategoryId", "Unknown"),
            "user_obj":       user_obj,
            "user_id":        user_obj.id,
            "cancel_id":      cancel_id,
            "status":         "Recording...",
            "running":        True,
            "progress_file":  progress_file,
        })

        # Send initial progress message with inline keyboard
        keyboard = _build_rec_progress_inline(session_id)
        try:
            await msg.edit_text(
                _build_rec_status_text(filename, 0.0, None),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
        except Exception:
            pass

        # Start auto-updater asyncio task (updates every 5 s)
        updater_task = asyncio.create_task(
            _auto_updater(session_id, msg, progress_file,
                          filename, duration_seconds, rec_start)
        )
        ACTIVE_UPDATERS[session_id] = updater_task

        # ── Monitor FFmpeg — stall detection only (no message edits here) ──
        # Encoding can be slower than real time on a busy worker. Use a
        # generous hard ceiling, but stop promptly when neither media time nor
        # the output file advances.
        wall_deadline = rec_start + max(
            60, duration_seconds * 4 + RECORDING_TIMEOUT_GRACE_SECONDS
        )
        cancelled = False
        stalled   = False
        stall_reason  = ""
        media_elapsed = 0.0
        last_activity_time = rec_start
        last_progress_us   = 0
        last_output_size   = 0

        while True:
            remaining_wall_time = wall_deadline - time.time()
            if remaining_wall_time <= 0:
                stalled      = True
                stall_reason = "recording exceeded its maximum processing time"
                try:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                except ProcessLookupError:
                    pass
                break
            try:
                await asyncio.wait_for(
                    asyncio.shield(proc.wait()),
                    timeout=min(5.0, remaining_wall_time),
                )
                break
            except asyncio.TimeoutError:
                pass
            if (
                cancel_id not in ACTIVE_RECORDINGS
                or not RECORDING_PROGRESS_INFO.get(session_id, {}).get("running", True)
            ):
                cancelled = True
                break

            # Update stall detection counters and shared progress info
            pg           = _read_ffmpeg_progress(progress_file)
            out_us       = _progress_int(pg.get("out_time_us"), 0)
            wall_elapsed = time.time() - rec_start
            media_elapsed = out_us / 1_000_000 if out_us > 0 else wall_elapsed
            media_elapsed = min(media_elapsed, duration_seconds)
            total_bytes   = _progress_int(pg.get("total_size"), 0)
            speed_mbps    = (total_bytes / 1024 / 1024) / max(wall_elapsed, 1)
            output_size   = os.path.getsize(out_file) if os.path.exists(out_file) else 0

            if out_us > last_progress_us or output_size > last_output_size:
                last_activity_time  = time.time()
                last_progress_us    = max(last_progress_us, out_us)
                last_output_size    = max(last_output_size, output_size)

            # Keep shared info current for the ⚡ Progress popup
            info = RECORDING_PROGRESS_INFO.get(session_id)
            if info:
                info["pct"]        = min(99.0, media_elapsed / max(duration_seconds, 1) * 100)
                info["elapsed"]    = media_elapsed
                info["speed_mbps"] = speed_mbps
                info["speed"]      = speed_mbps
                info["process"]    = proc

            if time.time() - last_activity_time >= RECORDING_STALL_TIMEOUT_SECONDS:
                stalled      = True
                stall_reason = (
                    f"no media or output progress for "
                    f"{RECORDING_STALL_TIMEOUT_SECONDS} seconds"
                )
                try:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                except ProcessLookupError:
                    pass
                break

        stderr_bytes = await stderr_task
        if (
            cancelled
            or not RECORDING_PROGRESS_INFO.get(session_id, {}).get("running", True)
        ):
            return "cancelled", "", media_elapsed
        if stalled:
            return (
                "timeout",
                f"Stream stopped: {stall_reason} "
                f"(media reached {_fmt_time(media_elapsed)}).",
                media_elapsed,
            )
        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace")[-1200:]
            if "451" in stderr_text and (
                "Unavailable For Legal Reasons" in stderr_text
                or "HTTP error 451" in stderr_text
            ):
                return (
                    "failed",
                    "❌ The stream is currently unavailable (HTTP 451 — Legal block).\n"
                    "This is a geo or legal restriction from the CDN/broadcaster.\n"
                    "Please try again later.",
                    media_elapsed,
                )
            return "failed", stderr_text, media_elapsed
        valid, validation_error = await validate_recording_file(out_file)
        if not valid:
            return "invalid", validation_error, media_elapsed
        return "ok", "", media_elapsed
    except Exception as exc:
        # Never leave a failed FFmpeg process running while the outer retry
        # starts another attempt.
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        if stderr_task and not stderr_task.done():
            stderr_task.cancel()
        return "failed", str(exc), 0.0
    finally:
        # Stop updater for this attempt; session-level cleanup is in run_recording_job
        if updater_task and not updater_task.done():
            updater_task.cancel()
        ACTIVE_UPDATERS.pop(session_id, None)
        ACTIVE_RECORDINGS.pop(cancel_id, None)
        RECORDING_SESSION_PROC.pop(session_id, None)
        info = RECORDING_PROGRESS_INFO.get(session_id)
        if info:
            info["process"] = None
        if progress_file:
            try:
                os.remove(progress_file)
            except OSError:
                pass


@require_verification
async def rec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command_args = list(context.args)
    source = "dishtv"
    if command_args and command_args[0].strip().lower() in {
        "-airtel", "-sunnxt"
    }:
        source = command_args[0].strip().lower().lstrip("-")
        command_args = command_args[1:]
    full_text = " ".join(command_args)
    past_range = None

    def duration_value(raw):
        parts = raw.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(parts[0]) * 60 + int(parts[1])

    # Airtel/live aliases: /rec -airtel Channel 00:00:30 -t 00:00:30
    combined_duration_match = re.match(
        r"^(.+?)\s+(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})"
        r"\s+-t\s+(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})$",
        full_text.strip(),
    )
    # Format 1: /rec <channel> HH:MM:SS  or  MM:SS  (duration-based)
    duration_match = re.match(
        r"^(.+?)\s+(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})$",
        full_text.strip()
    )
    # Format 2: /rec <channel> -t HH:MMAM - HH:MMPM  (time-range-based)
    timerange_match = re.match(
        r"^(.+?)\s+-t\s+(\d{1,2}:\d{2}\s*[APap][Mm])\s*-\s*(\d{1,2}:\d{2}\s*[APap][Mm])$",
        full_text.strip()
    )

    if combined_duration_match:
        channel_name = combined_duration_match.group(1).strip()
        first_duration = combined_duration_match.group(2).strip()
        second_duration = combined_duration_match.group(3).strip()
        duration_seconds = duration_value(first_duration)
        if duration_seconds != duration_value(second_duration):
            await update.message.reply_text(
                "❌ Both duration values must be the same. Example: "
                "`/rec -airtel Channel 00:00:30 -t 00:00:30`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        time_range_label = first_duration
    elif duration_match:
        channel_name = duration_match.group(1).strip()
        dur_str_raw = duration_match.group(2).strip()
        duration_seconds = duration_value(dur_str_raw)
        time_range_label = dur_str_raw
    elif timerange_match:
        channel_name = timerange_match.group(1).strip()
        start_time_str = timerange_match.group(2).strip()
        end_time_str = timerange_match.group(3).strip()
        start_h, start_m = parse_time(start_time_str)
        end_h, end_m = parse_time(end_time_str)
        if start_h is None or end_h is None:
            await update.message.reply_text("❌ Invalid time format. Example: `12:00PM`", parse_mode=ParseMode.MARKDOWN)
            return
        start_total = start_h * 60 + start_m
        end_total = end_h * 60 + end_m
        if end_total < start_total:
            end_total += 24 * 60
        duration_seconds = (end_total - start_total) * 60
        time_range_label = f"{start_time_str} - {end_time_str}"
        past_range = (start_h, start_m, end_h, end_m)
    else:
        await update.message.reply_text(
            "❌ *Invalid format.*\n\n"
            "*Option 1* — Duration:\n`/rec <channel> MM:SS`\n`/rec <channel> HH:MM:SS`\n\n"
            "Example: `/rec Pogo 00:30` _(30 sec)_\n"
            "Example: `/rec Pogo 01:30:00` _(1.5 hrs)_\n\n"
            "*Option 2* — Time range:\n`/rec <channel> -t HH:MMAM - HH:MMPM`\n\n"
            "Example: `/rec Pogo -t 12:00PM - 01:00PM`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    limit_mins = REC_LIMIT_SECONDS // 60
    uid = update.effective_user.id
    unlimited_duration = is_owner(uid) or is_premium(uid)
    if duration_seconds > REC_LIMIT_SECONDS and not unlimited_duration:
        await update.message.reply_text(
            f"❌ *Recording limit exceeded!*\n\n"
            f"Maximum: *{limit_mins} minutes*\n"
            f"Requested: *{duration_seconds // 60} min {duration_seconds % 60} sec*\n\n"
            f"Use a time range of 50 minutes or less.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if not await check_process_slot(update):
        return

    # Signed DishTV playlist URLs can expire before the normal browse-cache
    # TTL. Always load the latest entry when a recording command is started.
    channel = find_channel(channel_name, force_refresh=True, source=source)
    if not channel:
        if source == "sunnxt":
            provider = "Sunnxt Next"
        elif source == "airtel":
            provider = "Airtel Next"
        else:
            provider = "DishTV Next"
        await update.message.reply_text(
            f"❌ {provider} channel *{channel_name}* not found.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not channel.get("stream_url"):
        await update.message.reply_text(
            f"❌ Stream URL not found for *{channel['channel_name']}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Aspect selection is intentionally removed from the recording flow.
    # Recordings use the default 16:9 layout without an extra menu/message.
    prep_message = await update.message.reply_text(
        _ott_probe_text(source)
        if source in {"airtel", "sunnxt"} else
        "🔍 Probing the stream…\n"
        f"📡 Source: *{'Airtel' if source == 'airtel' else 'Sunnxt' if source == 'sunnxt' else 'DishTV'}*\n"
        "📺 Aspect: *16:9* | Quality: *576p*",
        parse_mode=ParseMode.MARKDOWN,
    )
    channel = refresh_channel(channel)
    pending_stream_url = channel.get("stream_url", "")
    if source == "airtel":
        airtel_error = await asyncio.get_running_loop().run_in_executor(
            None, _airtel_stream_error, pending_stream_url
        )
        if airtel_error:
            await prep_message.edit_text(
                f"❌ Airtel {channel.get('channel_name', channel_name)} stream unavailable.\n\n"
                f"Provider response: {airtel_error}\n\n"
                "The uploaded Airtel playlist entry was found, but the upstream stream "
                "is not returning a valid HLS playlist.",
            )
            return
    elif source == "sunnxt":
        sunnxt_error = await asyncio.get_running_loop().run_in_executor(
            None, _sunnxt_stream_error, channel
        )
        if sunnxt_error:
            await prep_message.edit_text(
                f"❌ Sunnxt {channel.get('channel_name', channel_name)} stream unavailable.\n\n"
                f"Provider response: {sunnxt_error}",
            )
            return
    else:
        dish_error = await asyncio.get_running_loop().run_in_executor(
            None, _dish_stream_error, channel
        )
        if dish_error:
            if "451" in dish_error or "450" in dish_error:
                login_hint = (
                    "\n\nThe playlist and signed cookie are available, but the DishTV CDN "
                    "is rejecting this bot server's network/IP. "
                    "This is not an FFmpeg or playlist parsing error. "
                    "Recording cannot start without an India-based outbound proxy/network."
                )
            elif "403" in dish_error or "401" in dish_error:
                if load_credentials():
                    login_hint = (
                        "\n\nThe DishTV session has expired or is invalid. "
                        "The owner should use `/login <10-digit mobile>` "
                        "and verify again with an OTP to create a fresh session."
                    )
                else:
                    login_hint = (
                        "\n\nNo DishTV login session is available. "
                        "The owner should first use `/login <10-digit mobile>`, "
                        "then verify with `/otp <6-digit code>`.",
                    )
            else:
                login_hint = ""
            await prep_message.edit_text(
                f"❌ DishTV {channel.get('channel_name', channel_name)} stream unavailable.\n\n"
                f"Provider response: {dish_error}"
                f"{login_hint}",
            )
            return
    tracks = await probe_audio_tracks(
        pending_stream_url, channel.get("cookie", ""), channel.get("user_agent", ""),
        channel.get("cenc_key", ""),
    )
    selection_id = _secrets.token_hex(6)
    PENDING_RECORDINGS[selection_id] = {
        "user_id": update.effective_user.id,
        "channel": channel,
        "duration_seconds": duration_seconds,
        "time_range_label": time_range_label,
        "stream_url": pending_stream_url,
        "aspect": "16:9",
        "quality": "576p",
        "tracks": tracks,
        "past_range": past_range,
    }
    if source in {"airtel", "sunnxt"}:
        await prep_message.edit_text(
            _ott_quality_text(channel, tracks),
            reply_markup=_ott_quality_keyboard(selection_id),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    status = _audio_statuses(tracks)
    keyboard = [[
        InlineKeyboardButton(
            "✅ Use All Audio Tracks",
            callback_data=f"rec_audio:{selection_id}:multi",
        )
    ]]
    await prep_message.edit_text(
        f"📺 *{channel['channel_name']} recording ready*\n"
        f"📡 Source: *{'Airtel' if source == 'airtel' else 'Sunnxt' if source == 'sunnxt' else 'DishTV'}*\n"
        "🎞️ Quality: *576p* | Aspect: *16:9*\n\n"
        f"{status}\n\n"
        "Audio mode: *Multiple audio tracks*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


async def ott_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select output quality for Airtel/Sun NXT only."""
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or parts[2] not in {"480", "720", "1080"}:
        await query.answer("Invalid OTT quality.", show_alert=True)
        return
    await query.answer()
    selection_id, height = parts[1], parts[2]
    pending = PENDING_RECORDINGS.get(selection_id)
    if (
        not pending
        or pending.get("user_id") != query.from_user.id
        or pending.get("channel", {}).get("source") not in {"airtel", "sunnxt"}
    ):
        await _edit_callback_message(
            query, "❌ This OTT recording selection has expired."
        )
        return
    pending["quality"] = f"{height}p"
    tracks = pending.get("tracks", [])
    await query.edit_message_text(
        f"📺 *{pending['channel']['channel_name']} recording ready*\n"
        f"📡 Source: *{'Airtel' if pending['channel'].get('source') == 'airtel' else 'Sun NXT'}*\n"
        f"🎞️ Quality: *{pending['quality']}* | Aspect: *16:9*\n\n"
        f"{_audio_statuses(tracks)}\n\n"
        "Audio mode: *Multiple audio tracks*",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Use All Audio Tracks",
                callback_data=f"rec_audio:{selection_id}:multi",
            )
        ]]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def rec_aspect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Probing the stream...")
    parts = query.data.split(":")
    if len(parts) != 4:
        return
    selection_id, aspect = parts[1], f"{parts[2]}:{parts[3]}"
    pending = PENDING_RECORDINGS.get(selection_id)
    if not pending or pending["user_id"] != query.from_user.id:
        await _edit_callback_message(query, "❌ This recording selection has expired.")
        return
    pending["aspect"] = aspect
    channel = refresh_channel(pending["channel"])
    pending["channel"] = channel
    pending["stream_url"] = channel.get("stream_url", pending["stream_url"])
    tracks = await probe_audio_tracks(
        pending["stream_url"],
        channel.get("cookie", ""),
        channel.get("user_agent", ""),
        channel.get("cenc_key", ""),
    )
    pending["quality"] = "576p"
    pending["tracks"] = tracks
    status = _audio_statuses(tracks)
    keyboard = [[
        InlineKeyboardButton(
            "✅ Use All Audio Tracks",
            callback_data=f"rec_audio:{selection_id}:multi",
        )
    ]]
    await query.edit_message_text(
        "🔍 *Probing stream for audio tracks…*\n\n"
        f"{status}\n\n"
        "Audio mode: *Multiple audio tracks*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


async def rec_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3:
        return
    selection_id, language = parts[1], parts[2]
    pending = PENDING_RECORDINGS.get(selection_id)
    if not pending or pending["user_id"] != query.from_user.id:
        await _edit_callback_message(query, "❌ This recording selection has expired.")
        return
    tracks = pending.get("tracks", [])
    audio_mode = "multi" if language == "multi" else "single"
    audio_index = _audio_index_for_language(tracks, language)

    # ── DishTV: show watermark settings screen before recording ──────────
    if pending["channel"].get("source") == "dishtv":
        pending["audio_mode"]  = audio_mode
        pending["audio_index"] = audio_index
        text, keyboard = _build_watermark_menu(selection_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        return
    # ── Airtel/Sunnxt: use the OTT watermark settings before recording ─────
    if pending["channel"].get("source") in {"airtel", "sunnxt"}:
        pending["audio_mode"] = audio_mode
        pending["audio_index"] = audio_index
        text, keyboard = _build_ott_watermark_menu(selection_id)
        await query.edit_message_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )
        return

    # Keep the existing fallback for non-provider direct recordings unchanged.
    PENDING_RECORDINGS.pop(selection_id, None)
    try:
        await query.message.delete()
    except Exception:
        await query.edit_message_text(
            f"⏺ *Recording is starting…*\n"
            f"📺 {pending['channel']['channel_name']}\n"
            f"🎞️ {pending['quality']} | 🎧 "
            f"{'All Audio Tracks' if audio_mode == 'multi' else language.title()}",
            parse_mode=ParseMode.MARKDOWN,
        )
        status_message = query.message
    else:
        status_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"⏺ *Recording is starting…*\n"
                f"📺 {pending['channel']['channel_name']}\n"
                f"🎞️ {pending['quality']} | 🎧 "
                f"{'All Audio Tracks' if audio_mode == 'multi' else language.title()}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    await run_recording_job(
        update, context, pending["channel"], pending["duration_seconds"],
        pending["time_range_label"], pending["aspect"], pending["quality"],
        audio_index, audio_mode, tracks, status_message,
        past_range=pending.get("past_range"),
    )


async def ott_watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle watermark settings for Airtel and Sun NXT recordings only."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]

    if action == "pos":
        if len(parts) != 4:
            return
        position, selection_id = parts[2], parts[3]
    else:
        selection_id = parts[2]

    pending = PENDING_RECORDINGS.get(selection_id)
    if (
        not pending
        or pending.get("user_id") != query.from_user.id
        or pending.get("channel", {}).get("source") not in {"airtel", "sunnxt"}
    ):
        await _edit_callback_message(
            query, "❌ This OTT recording selection has expired."
        )
        return

    if action == "pos":
        save_ott_watermark_settings(mode=position)
    elif action == "enable":
        save_ott_watermark_settings(enabled=True)
    elif action == "disable":
        save_ott_watermark_settings(enabled=False)
    elif action == "last2min":
        settings = get_ott_watermark_settings()
        save_ott_watermark_settings(last_2min=not settings["last_2min"])
    elif action == "changeurl":
        PENDING_OTT_URL_CHANGES[query.from_user.id] = selection_id
        settings = get_ott_watermark_settings()
        await query.edit_message_text(
            "🔗 *Send the new watermark URL*\n\n"
            f"Current: `{settings['custom_url'] or '(Default)'}`\n\n"
            "Send `default` to restore the default URL.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    elif action == "back":
        tracks = pending.get("tracks", [])
        status = _audio_statuses(tracks)
        channel = pending["channel"]
        await query.edit_message_text(
            f"📺 *{channel['channel_name']} recording ready*\n"
            f"📡 Source: *{'Airtel' if channel.get('source') == 'airtel' else 'Sun NXT'}*\n"
            f"🎞️ Quality: *{pending.get('quality', '576p')}* | Aspect: *16:9*\n\n"
            f"{status}\n\n"
            "Audio mode: *Multiple audio tracks*",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Use All Audio Tracks",
                    callback_data=f"rec_audio:{selection_id}:multi",
                )
            ]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    elif action == "start":
        PENDING_RECORDINGS.pop(selection_id, None)
        audio_mode = pending.get("audio_mode", "multi")
        audio_index = pending.get("audio_index", 0)
        tracks = pending.get("tracks", [])
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(
                "⏺ *Recording is starting…*\n"
                f"📺 {pending['channel']['channel_name']}\n"
                f"📡 {'Airtel' if pending['channel'].get('source') == 'airtel' else 'Sun NXT'}\n"
                f"🎧 {'All Audio Tracks' if audio_mode == 'multi' else 'Selected Track'}",
                parse_mode=ParseMode.MARKDOWN,
            )
            status_message = query.message
        else:
            status_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "⏺ *Recording is starting…*\n"
                    f"📺 {pending['channel']['channel_name']}\n"
                    f"📡 {'Airtel' if pending['channel'].get('source') == 'airtel' else 'Sun NXT'}\n"
                    f"🎧 {'All Audio Tracks' if audio_mode == 'multi' else 'Selected Track'}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        await run_recording_job(
            update, context, pending["channel"], pending["duration_seconds"],
            pending["time_range_label"], pending["aspect"], pending["quality"],
            audio_index, audio_mode, tracks, status_message,
            past_range=pending.get("past_range"),
        )
        return

    text, keyboard = _build_ott_watermark_menu(selection_id)
    await query.edit_message_text(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )


async def rec_watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all rec_wm:* inline button presses from the DishTV watermark menu."""
    query = update.callback_query
    await query.answer()
    # callback_data format: rec_wm:{action}:{selection_id}
    #                  or:  rec_wm:pos:{position}:{selection_id}
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]

    if action == "pos":
        if len(parts) != 4:
            return
        position, selection_id = parts[2], parts[3]
        pending = PENDING_RECORDINGS.get(selection_id)
        if not pending or pending["user_id"] != query.from_user.id:
            await _edit_callback_message(query, "❌ This recording selection has expired.")
            return
        save_watermark_settings(mode=position)
        text, keyboard = _build_watermark_menu(selection_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        return

    selection_id = parts[2]
    pending = PENDING_RECORDINGS.get(selection_id)
    if not pending or pending["user_id"] != query.from_user.id:
        await _edit_callback_message(query, "❌ This recording selection has expired.")
        return

    if action == "enable":
        save_watermark_settings(enabled=True)
        text, keyboard = _build_watermark_menu(selection_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    elif action == "disable":
        save_watermark_settings(enabled=False)
        text, keyboard = _build_watermark_menu(selection_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    elif action == "last2min":
        ws = get_watermark_settings()
        save_watermark_settings(last_2min=not ws["last_2min"])
        text, keyboard = _build_watermark_menu(selection_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    elif action == "changeurl":
        PENDING_URL_CHANGES[query.from_user.id] = selection_id
        ws = get_watermark_settings()
        await query.edit_message_text(
            "🔗 *Send the new watermark URL*\n\n"
            f"Current: `{ws['custom_url'] or '(default)'}`\n\n"
            "Paste only the URL in your next message.\n"
            "Send `/setwatermark default` to restore the default URL.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "back":
        # Restore audio track selection menu
        tracks = pending.get("tracks", [])
        status = _audio_statuses(tracks)
        ch = pending["channel"]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Use All Audio Tracks",
                callback_data=f"rec_audio:{selection_id}:multi",
            )
        ]])
        await query.edit_message_text(
            f"📺 *{ch['channel_name']} recording ready*\n"
            f"📡 Source: *{'Airtel' if ch.get('source') == 'airtel' else 'DishTV'}*\n"
            "🎞️ Quality: *576p* | Aspect: *16:9*\n\n"
            f"{status}\n\n"
            "Audio mode: *Multiple audio tracks*",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "start":
        PENDING_RECORDINGS.pop(selection_id, None)
        audio_mode  = pending.get("audio_mode", "multi")
        audio_index = pending.get("audio_index", 0)
        tracks      = pending.get("tracks", [])
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(
                f"⏺ *Recording is starting…*\n"
                f"📺 {pending['channel']['channel_name']}\n"
                f"🎞️ {pending['quality']} | 🎧 "
                f"{'All Audio Tracks' if audio_mode == 'multi' else 'Selected Track'}",
                parse_mode=ParseMode.MARKDOWN,
            )
            status_message = query.message
        else:
            status_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"⏺ *Recording is starting…*\n"
                    f"📺 {pending['channel']['channel_name']}\n"
                    f"🎞️ {pending['quality']} | 🎧 "
                    f"{'All Audio Tracks' if audio_mode == 'multi' else 'Selected Track'}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        await run_recording_job(
            update, context, pending["channel"], pending["duration_seconds"],
            pending["time_range_label"], pending["aspect"], pending["quality"],
            audio_index, audio_mode, tracks, status_message,
            past_range=pending.get("past_range"),
        )


async def watermark_url_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture the watermark URL the user types after clicking Change Watermark Link."""
    uid = update.effective_user.id
    selection_id = PENDING_URL_CHANGES.get(uid)
    if not selection_id:
        return  # Not waiting for URL from this user
    PENDING_URL_CHANGES.pop(uid, None)
    text = (update.message.text or "").strip()
    if text.lower() in ("default", "/setwatermark default"):
        save_watermark_settings(custom_url="")
        reply = "✅ Watermark URL reset to the default."
    elif text.startswith(("http://", "https://")):
        save_watermark_settings(custom_url=text)
        reply = f"✅ Watermark URL set:\n`{text}`"
    else:
        reply = "❌ Invalid URL. It must start with `http://` or `https://`."
    pending = PENDING_RECORDINGS.get(selection_id)
    if pending:
        text_menu, keyboard = _build_watermark_menu(selection_id)
        sent = await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        # Re-send watermark menu so user can continue
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text_menu,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


async def ott_watermark_url_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Capture a watermark URL requested from an OTT recording menu."""
    uid = update.effective_user.id
    selection_id = PENDING_OTT_URL_CHANGES.pop(uid, None)
    if not selection_id:
        return
    pending = PENDING_RECORDINGS.get(selection_id)
    if not pending or pending.get("user_id") != uid:
        await update.message.reply_text("❌ This OTT recording selection has expired.")
        return
    text = (update.message.text or "").strip()
    if text.lower() in ("default", "/setwatermark default"):
        save_ott_watermark_settings(custom_url="")
        reply = "✅ Watermark URL reset to the default."
    elif text.startswith(("http://", "https://")):
        save_ott_watermark_settings(custom_url=text)
        reply = "✅ Watermark URL set."
    else:
        reply = "❌ Invalid URL. It must start with `http://` or `https://`."
    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    menu_text, keyboard = _build_ott_watermark_menu(selection_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=menu_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )


async def run_recording_job(update, context, channel, duration_seconds,
                            time_range_label, aspect, quality, audio_index,
                            audio_mode, audio_tracks, msg, past_range=None):
    global _active_processes
    _active_processes += 1
    work_dir   = None
    dash_proxy = None
    # session_id is constant for the lifetime of this recording (including retries)
    session_id = _secrets.token_hex(6)
    try:
        # The playlist is cached for browsing, but its signed CDN cookie must
        # be refreshed at recording time because it can expire sooner.
        channel = refresh_channel(channel)
        work_dir = f"/tmp/recording_{_secrets.token_hex(8)}"
        os.makedirs(work_dir, exist_ok=True)
        if past_range:
            range_start, range_end, range_now = _requested_range_datetimes(past_range)
            if range_start > range_now:
                wait_seconds = max(0, int((range_start - range_now).total_seconds()))
                await msg.edit_text(
                    "⏳ *Waiting for the live recording start time…*\n"
                    f"🕐 `{time_range_label}`\n"
                    f"▶️ Recording will start in {wait_seconds // 60}m {wait_seconds % 60}s.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await asyncio.sleep(wait_seconds)
                channel = refresh_channel(channel)
                past_range = None
                await msg.edit_text(
                    "⏺ *The live recording is starting…*\n"
                    f"📺 {channel['channel_name']}\n"
                    f"🕐 `{time_range_label}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            elif range_end > range_now:
                duration_seconds = max(
                    1, int((range_end - range_now).total_seconds()) + 1
                )
                channel = refresh_channel(channel)
                past_range = None
                await msg.edit_text(
                    "⏺ *The live recording will run from now until the end time…*\n"
                    f"📺 {channel['channel_name']}\n"
                    f"🕐 `{time_range_label}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
        if past_range:
            try:
                static_mpd = os.path.join(work_dir, "selected_dvr.mpd")
                selected_seconds = 0
                for dvr_attempt in range(3):
                    try:
                        _, selected_seconds, _ = _create_static_dash_mpd(
                            channel, *past_range, static_mpd
                        )
                        break
                    except ValueError as exc:
                        wait_seconds = _dvr_manifest_wait_seconds(exc, past_range)
                        if not wait_seconds or dvr_attempt == 2:
                            raise
                        await msg.edit_text(
                            "⏳ *Waiting for the DVR live edge…*\n"
                            f"🕐 `{time_range_label}`\n"
                            f"🔄 Manifest refresh {wait_seconds} sec baad…",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        await asyncio.sleep(wait_seconds)
                        channel = refresh_channel(channel)
                dash_proxy, proxy_mpd_url = _start_dash_proxy(static_mpd, channel)
                proxy_base_url = proxy_mpd_url.rsplit("/", 1)[0] + "/dash/"
                _set_static_mpd_base_url(static_mpd, proxy_base_url)
                channel = dict(channel)
                channel["key_stream_url"] = channel["stream_url"]
                channel["stream_url"] = proxy_mpd_url
                duration_seconds = selected_seconds
                await msg.edit_text(
                    "📼 *Selecting segments from the DVR window…*\n"
                    f"🕐 `{time_range_label}`\n"
                    "🔐 Preparing the signed DASH MPD…",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except (ET.ParseError, OSError, requests.RequestException, ValueError) as exc:
                await msg.edit_text(
                    f"❌ *DVR recording could not be started.*\n\n`{str(exc)[:900]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
        bot_uname = BOTUSERNAME or context.bot.username or "bot"
        safe_ch = re.sub(r'[\\/*?:"<>|]', "_", channel["channel_name"])
        source_label = {
            "airtel": "Airtel",
            "sunnxt": "Sunnxt",
        }.get(channel.get("source"), "Dishtv")
        filename = f"[{safe_ch}]. {source_label}-DL.AAC.2.0.H264.-@{bot_uname}.mp4"
        out_file = os.path.join(work_dir, filename)
        user_obj = update.effective_user
        status = "failed"
        last_error = ""
        cancel_id = ""

        # A live DASH stream can return corrupt segments. Retry once with a
        # freshly signed playlist entry, but never upload an unvalidated file.
        media_elapsed = 0.0
        for attempt in range(2):
            cancel_id = _secrets.token_hex(4)
            status, last_error, media_elapsed = await _run_recording_attempt(
                update, context, channel, duration_seconds,
                quality, aspect, audio_index, audio_mode, audio_tracks,
                msg, out_file, cancel_id, session_id,
            )
            if status in ("ok", "cancelled"):
                break
            if attempt == 0:
                if not past_range:
                    channel = refresh_channel(channel)
                try:
                    os.remove(out_file)
                except OSError:
                    pass
                await msg.edit_text(
                    "⚠️ *Stream segment corrupt/disconnected.*\n"
                    "Retrying the recording with a fresh stream…",
                    parse_mode=ParseMode.MARKDOWN,
                )

        # ── Partial-recording upload helper ──────────────────────────────
        async def _upload_partial(out_path, rec_elapsed):
            """Upload partial file if it exists and has content; return size_mb."""
            if not os.path.exists(out_path):
                return 0.0
            fsize = os.path.getsize(out_path)
            if fsize < 1024:
                return 0.0
            size_mb = fsize / (1024 * 1024)
            cap = (
                f"📼 *{channel['channel_name']}* (partial)\n"
                f"🕐 {time_range_label}\n"
                f"🎞️ {quality} | ⏱ {_fmt_time(rec_elapsed)}\n"
                f"📦 {size_mb:.1f} MB"
            )
            try:
                with open(out_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f, caption=cap, parse_mode=ParseMode.MARKDOWN,
                        supports_streaming=True, read_timeout=300, write_timeout=300,
                    )
            except Exception:
                try:
                    with open(out_path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=f, caption=cap, parse_mode=ParseMode.MARKDOWN,
                            read_timeout=300, write_timeout=300,
                        )
                except Exception:
                    return 0.0
            return size_mb

        # ── Cancelled ────────────────────────────────────────────────────
        if status == "cancelled":
            partial_size = await _upload_partial(out_file, media_elapsed)
            partial_uploaded = partial_size > 0
            cancel_text = (
                f"❌ Recording Cancelled\n\n"
                f"{'⚠️ Partial Recording Sent\n\n' if partial_uploaded else ''}"
                f"📄 File:\n`{filename}`\n\n"
                f"⏺ Recorded:\n`{_fmt_time(media_elapsed)}`\n\n"
            )
            if partial_uploaded:
                cancel_text += (
                    f"📤 The recorded portion has been uploaded successfully.\n\n"
                    f"⏳ Server copy auto-deletes in 1 hour."
                )
            else:
                cancel_text += "⚠️ No partial recording was available to upload."
            try:
                await msg.edit_text(cancel_text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
            return

        # ── Failed / timeout / invalid ───────────────────────────────────
        if status != "ok":
            partial_size = await _upload_partial(out_file, media_elapsed)
            partial_uploaded = partial_size > 0
            error_detail = re.sub(
                r"\s+", " ", str(last_error or "Unknown FFmpeg/stream error")
            ).strip()
            fail_text = (
                f"❌ Recording Failed\n\n"
                f"{'⚠️ Partial Recording Sent\n\n' if partial_uploaded else ''}"
                f"📄 File:\n`{filename}`\n\n"
                f"⏺ Recorded:\n`{_fmt_time(media_elapsed)}`\n\n"
            )
            if error_detail:
                fail_text += f"⚠️ Reason:\n`{error_detail[:700]}`\n\n"
            if partial_uploaded:
                fail_text += (
                    f"📤 The recorded portion has been uploaded successfully.\n\n"
                    f"⏳ Server copy auto-deletes in 1 hour."
                )
            else:
                fail_text += "⚠️ No partial recording was available to upload."
            try:
                await msg.edit_text(fail_text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
            return

        # ── Recording complete — show completion then upload ──────────────
        file_size = os.path.getsize(out_file)
        size_mb   = file_size / (1024 * 1024)
        mins, secs = divmod(int(duration_seconds), 60)
        hrs  = mins // 60
        mins = mins % 60
        dur_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"
        caption = (
            f"📼 *{channel['channel_name']}*\n"
            f"🕐 {time_range_label}\n"
            f"🎞️ {quality} | 🎧 "
            f"{'All Audio Tracks' if audio_mode == 'multi' else ('Hindi' if audio_index == 0 else 'Selected')}\n"
            f"⏱ {dur_str} | 📦 {size_mb:.1f} MB"
        )
        thumbnail_path = os.path.join(work_dir, "recording_thumbnail.jpg")
        try:
            await msg.edit_text(
                f"✅ Recording Completed\n\n"
                f"📄 File:\n`{filename}`\n\n"
                f"⏱ Duration:\n`{dur_str}`\n\n"
                f"📦 Size:\n`{size_mb:.1f} MB`\n\n"
                f"🖼️ Generating thumbnail...",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        thumbnail_path = await _generate_recording_thumbnail(
            out_file, thumbnail_path, duration_seconds
        )
        try:
            await msg.edit_text(
                f"✅ Recording Completed\n\n"
                f"📄 File:\n`{filename}`\n\n"
                f"⏱ Duration:\n`{dur_str}`\n\n"
                f"📦 Size:\n`{size_mb:.1f} MB`\n\n"
                f"📤 Uploading...",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        try:
            with open(out_file, "rb") as f:
                if thumbnail_path:
                    with open(thumbnail_path, "rb") as thumbnail_file:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=f, thumbnail=thumbnail_file,
                            caption=caption, parse_mode=ParseMode.MARKDOWN,
                            supports_streaming=True, read_timeout=300,
                            write_timeout=300,
                        )
                else:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f, caption=caption, parse_mode=ParseMode.MARKDOWN,
                        supports_streaming=True, read_timeout=300,
                        write_timeout=300,
                    )
        except Exception:
            try:
                with open(out_file, "rb") as f:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f, caption=caption, parse_mode=ParseMode.MARKDOWN,
                        supports_streaming=True, read_timeout=300,
                        write_timeout=300,
                    )
            except Exception:
                with open(out_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f, caption=caption, parse_mode=ParseMode.MARKDOWN,
                        read_timeout=300, write_timeout=300,
                    )
        try:
            await msg.edit_text(
                f"✅ Recording Completed\n\n"
                f"📄 File:\n`{filename}`\n\n"
                f"⏺ Duration:\n`{dur_str}`\n\n"
                f"📤 Upload completed successfully.\n\n"
                f"⏳ Server copy auto-deletes in 3 hours.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    finally:
        # Clean up session-level state
        RECORDING_PROGRESS_INFO.pop(session_id, None)
        RECORDING_SESSION_PROC.pop(session_id, None)
        task = ACTIVE_UPDATERS.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        if dash_proxy is not None:
            try:
                dash_proxy.shutdown()
                dash_proxy.server_close()
            except OSError:
                pass
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes -= 1


async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📋 Loading the channel list...")
    source_arg = (
        context.args[0].strip().lower()
        if context.args else ""
    )
    airtel = source_arg == "-airtel"
    sunnxt = source_arg == "-sunnxt"
    try:
        if airtel:
            channels = get_airtel_channels()
        elif sunnxt:
            channels = get_sunnxt_channels()
        else:
            channels = get_channels()
    except Exception:
        await msg.edit_text("❌ Could not load the channels. Please try again later.")
        return

    if airtel or sunnxt:
        if not channels:
            await msg.edit_text(
                f"❌ No {'Airtel' if airtel else 'Sunnxt'} channels are available. "
                "Check the remote playlist."
            )
            return
        provider = "Airtel" if airtel else "Sunnxt"
        lines = [f"📡 *{provider} Channels* ({len(channels)} total)\n"]
        for index, channel in enumerate(channels, start=1):
            lines.append(f"{index}. {channel['channel_name']}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    categories = {}
    for ch in channels:
        cat = ch.get("channelCategoryId", "Other")
        categories.setdefault(cat, []).append(ch["channel_name"])

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in sorted(categories.keys())]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await msg.edit_text(
        f"📺 *DishTV Channels* ({len(channels)} total)\n\nChoose a category:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.replace("cat_", "")
    channels = get_channels()
    cat_channels = [c for c in channels if c.get("channelCategoryId") == cat]

    lines = [f"📺 *{cat}* ({len(cat_channels)} channels)\n"]
    for ch in cat_channels:
        catchup = " 📼" if ch.get("isCatchupAvailable") == "True" else ""
        lines.append(f"• {ch['channel_name']}{catchup}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."

    keyboard = [[InlineKeyboardButton("« Back", callback_data="back_categories")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


async def back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    channels = get_channels()
    categories = {}
    for ch in channels:
        cat = ch.get("channelCategoryId", "Other")
        categories.setdefault(cat, []).append(ch["channel_name"])

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in sorted(categories.keys())]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📺 *DishTV Channels* ({len(channels)} total)\n\nChoose a category:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/search <channel name>`\n"
            "For Airtel: `/search -airtel <channel name>`\n"
            "For Sunnxt: `/search -sunnxt <channel name>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    search_args = list(context.args)
    source_arg = search_args[0].strip().lower()
    airtel = source_arg == "-airtel"
    sunnxt = source_arg == "-sunnxt"
    if airtel or sunnxt:
        search_args = search_args[1:]
        if not search_args:
            await update.message.reply_text(
                "❌ Usage: `/search -airtel <channel name>` or "
                "`/search -sunnxt <channel name>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    query = " ".join(search_args).lower()
    if airtel:
        channels = get_airtel_channels()
    elif sunnxt:
        channels = get_sunnxt_channels()
    else:
        channels = get_channels()
    results = [c for c in channels if query in c["channel_name"].lower()]

    if not results:
        source_label = "Airtel" if airtel else "Sunnxt" if sunnxt else "JioTV"
        await update.message.reply_text(
            f"❌ No {source_label} channel matched `{query}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    source_label = "Airtel" if airtel else "Sunnxt" if sunnxt else "JioTV"
    lines = [f"🔍 *{source_label} Search: {query}* ({len(results)} results)\n"]
    for ch in results[:20]:
        catchup = " 📼" if ch.get("isCatchupAvailable") == "True" else ""
        lines.append(f"• `{ch['channel_name']}`{catchup}")

    if len(results) > 20:
        lines.append(f"\n...and {len(results) - 20} more channels.")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Owner-only commands ────────────────────────

@owner_only
async def setdefaultaudio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan a replied video and change its default audio disposition."""
    if context.args:
        # Preserve the previously supported label-setting form.
        label = " ".join(context.args).strip()
        if len(label) > 100 or "\x00" in label:
            await update.message.reply_text(
                "❌ The audio label must be 100 characters or fewer.",
            )
            return
        save_default_audio_label(label)
        await update.message.reply_text(
            "✅ Default audio label updated.\n\n"
            f"Current Label:\n{get_audio_track_settings()['default_label']}",
        )
        return

    source = _media_reply_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to a video with `/setdefaultaudio`."
        )
        return
    if source.get("file_size") and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text(
            f"❌ The video exceeds the Telegram Bot API download limit of {telegram_limit_text()}."
        )
        return
    if not await check_process_slot(update):
        return

    token = _secrets.token_hex(8)
    filename = _media_safe_name(source["file_name"], "input.mkv")
    status_message = await update.message.reply_text(
        _build_default_audio_status_text(filename, "scan")
    )
    pending = {
        "token": token,
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "source": source,
        "filename": filename,
        "message_id": status_message.message_id,
        "created_at": time.time(),
    }
    DEFAULT_AUDIO_PENDING[token] = pending
    context.user_data["default_audio_pending"] = pending
    pending["scan_task"] = asyncio.create_task(
        _default_audio_scan_job(update, context, pending, status_message)
    )


async def _default_audio_scan_job(update, context, pending, status_message):
    token = pending["token"]
    work_dir = os.path.join("/tmp", f"default_audio_scan_{token}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        input_path = os.path.join(work_dir, pending["filename"])
        await _media_download_telegram(context, pending["source"], input_path)
        probe = await _stream_probe_file(input_path)
        audio_streams = [
            stream for stream in probe.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
        if not audio_streams:
            await status_message.edit_text(
                f"❌ No audio tracks found.\n\n📄 File:\n{pending['filename']}"
            )
            return
        pending["audio_streams"] = audio_streams
        pending["duration"] = probe.get("duration", 0.0)
        pending["input_path"] = input_path
        await status_message.edit_text(
            _build_default_audio_status_text(pending["filename"], "select"),
            reply_markup=_default_audio_menu(token, audio_streams),
        )
        # Keep the downloaded probe input until selection or cancellation.
        pending["work_dir"] = work_dir
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Default audio scan failed")
        try:
            await status_message.edit_text(
                f"❌ Audio scan failed.\n\n{str(exc)[:700]}"
            )
        except Exception:
            pass
    finally:
        if pending.get("work_dir") != work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        if pending.get("audio_streams") is None:
            DEFAULT_AUDIO_PENDING.pop(token, None)
            context.user_data.pop("default_audio_pending", None)


async def default_audio_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid audio selection.", show_alert=True, cache_time=0)
        return
    token = parts[1]
    try:
        selected_ordinal = int(parts[2])
    except ValueError:
        await query.answer("Invalid audio selection.", show_alert=True, cache_time=0)
        return
    pending = DEFAULT_AUDIO_PENDING.get(token)
    if (
        not pending
        or pending.get("user_id") != query.from_user.id
        or time.time() - pending.get("created_at", 0) > 15 * 60
    ):
        await query.answer("Audio selection expired.", show_alert=True, cache_time=0)
        return
    audio_streams = pending.get("audio_streams") or []
    if selected_ordinal < 0 or selected_ordinal >= len(audio_streams):
        await query.answer("Invalid audio track.", show_alert=True, cache_time=0)
        return
    selected_name = _default_audio_name(
        audio_streams[selected_ordinal], selected_ordinal
    )
    pending["selected_ordinal"] = selected_ordinal
    pending["audio_name"] = selected_name
    await query.answer("Updating default audio...", cache_time=0)
    await query.edit_message_text(
        _build_default_audio_status_text(
            pending["filename"], "updating", selected_name
        ),
        reply_markup=_build_default_audio_progress_inline(),
    )
    task_id = _secrets.token_hex(8)
    pending["task_id"] = task_id
    context.user_data["default_audio_task_id"] = task_id
    context.user_data["default_audio_process"] = None
    context.user_data["default_audio_upload_state"] = {
        "phase": "ffmpeg",
        "output_path": None,
    }
    global _active_processes
    _active_processes += 1
    RECORDING_PROGRESS_INFO[task_id] = {
        "kind": "default_audio",
        "process": None,
        "start_time": time.time(),
        "duration": float(pending.get("duration") or 0.0),
        "total_duration": float(pending.get("duration") or 0.0),
        "filename": pending["filename"],
        "file_name": pending["filename"],
        "message_id": query.message.message_id,
        "chat_id": query.message.chat_id,
        "speed": 0.0,
        "speed_mbps": 0.0,
        "platform": "Default Audio",
        "channel": {"channelCategoryId": "Default Audio"},
        "user_obj": query.from_user,
        "user_id": query.from_user.id,
        "pct": 0.0,
        "elapsed": 0.0,
        "status": "🎬 Updating Audio Flags",
        "running": True,
        "phase": "ffmpeg",
        "audio_name": selected_name,
        "work_dir": pending.get("work_dir", ""),
        "progress_file": os.path.join(
            pending.get("work_dir", "/tmp"), f"progress_{task_id}.txt"
        ),
    }
    MEDIA_USER_TASKS[query.from_user.id] = task_id
    pending["job_task"] = asyncio.create_task(
        _default_audio_update_job(update, context, pending, query.message, task_id)
    )


async def default_audio_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    parts = query.data.split(":")
    token = parts[1] if len(parts) == 2 else ""
    pending = DEFAULT_AUDIO_PENDING.get(token)
    if not pending or pending.get("user_id") != query.from_user.id:
        await query.answer("Audio update menu expired.", show_alert=True, cache_time=0)
        return
    pending["cancelled"] = True
    scan_task = pending.get("scan_task")
    if scan_task and not scan_task.done():
        scan_task.cancel()
    task_id = pending.get("task_id")
    info = RECORDING_PROGRESS_INFO.get(task_id) if task_id else None
    if info:
        info["running"] = False
        info["cancel_message_sent"] = True
        proc = RECORDING_SESSION_PROC.get(task_id) or info.get("process")
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        job_task = pending.get("job_task")
        if job_task and not job_task.done():
            job_task.cancel()
        await query.edit_message_text(
            "❌ Default Audio Update Cancelled\n\n"
            "⚠️ Partial Output Deleted"
        )
    else:
        await query.edit_message_text("❌ Default audio update cancelled.")
    DEFAULT_AUDIO_PENDING.pop(token, None)
    context.user_data.pop("default_audio_pending", None)
    await query.answer("Cancelled.", cache_time=0)


async def _default_audio_send_upload(context, status_message, output_path, info):
    info["phase"] = "upload"
    info["status"] = "📤 Uploading"
    info["upload_bytes"] = 0
    info["upload_size"] = os.path.getsize(output_path)
    info["upload_start"] = time.time()
    info["upload_remaining"] = 0.0
    await status_message.edit_text(
        _build_default_audio_status_text(
            info["filename"], "done", info.get("audio_name", "")
        ),
        reply_markup=_build_default_audio_progress_inline(),
    )
    with open(output_path, "rb") as raw:
        wrapped = _ProgressUploadFile(raw, info)
        telegram_input = InputFile(
            wrapped, filename=os.path.basename(output_path), read_file_handle=False
        )
        try:
            await context.bot.send_video(
                chat_id=status_message.chat_id,
                video=telegram_input,
                supports_streaming=True,
                read_timeout=1800,
                write_timeout=1800,
            )
        except Exception:
            raw.seek(0)
            retry_wrapped = _ProgressUploadFile(raw, info)
            telegram_input = InputFile(
                retry_wrapped,
                filename=os.path.basename(output_path),
                read_file_handle=False,
            )
            await context.bot.send_document(
                chat_id=status_message.chat_id,
                document=telegram_input,
                read_timeout=1800,
                write_timeout=1800,
            )


async def _default_audio_update_job(update, context, pending, status_message, task_id):
    global _active_processes
    info = RECORDING_PROGRESS_INFO.get(task_id)
    work_dir = pending.get("work_dir")
    output_path = None
    try:
        input_path = pending.get("input_path")
        if not input_path or not os.path.exists(input_path):
            raise RuntimeError("Scanned input file is no longer available.")
        output_path = os.path.join(
            work_dir, f"{Path(pending['filename']).stem}_default{Path(pending['filename']).suffix or '.mkv'}"
        )
        info["source_path"] = input_path
        info["source_size"] = os.path.getsize(input_path)
        info["output_path"] = output_path
        context.user_data["default_audio_upload_state"]["output_path"] = output_path
        progress_file = info["progress_file"]
        audio_count = len(pending.get("audio_streams") or [])
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-i", input_path,
            "-map", "0", "-c", "copy", "-map_chapters", "0",
        ]
        disposition_flags = {
            "forced", "hearing_impaired", "visual_impaired", "clean_effects",
            "attached_pic", "timed_thumbnails", "captions", "descriptions",
            "metadata", "dependent", "still_image", "commentary", "dub",
            "original", "lyrics", "karaoke",
        }
        for ordinal, stream in enumerate(pending.get("audio_streams") or []):
            existing = stream.get("disposition") or {}
            flags = [
                flag for flag in disposition_flags
                if existing.get(flag)
            ]
            if ordinal == pending["selected_ordinal"]:
                flags.append("default")
            cmd += [
                f"-disposition:a:{ordinal}",
                "+".join(flags) if flags else "0",
            ]
        cmd += ["-progress", progress_file, "-nostats", output_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        info["process"] = proc
        context.user_data["default_audio_process"] = proc
        RECORDING_SESSION_PROC[task_id] = proc
        ACTIVE_UPDATERS[task_id] = asyncio.create_task(
            _auto_updater(
                task_id, status_message, progress_file, info["filename"],
                info["total_duration"], info["start_time"]
            )
        )
        stderr_task = asyncio.create_task(proc.stderr.read())
        while proc.returncode is None:
            if not info.get("running", True):
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
                raise asyncio.CancelledError
            await asyncio.sleep(0.3)
        stderr = await stderr_task
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-800:] or "FFmpeg failed")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("FFmpeg produced no output.")
        info["phase"] = "upload"
        context.user_data["default_audio_upload_state"]["phase"] = "upload"
        await _default_audio_send_upload(context, status_message, output_path, info)
        if info.get("running", True):
            await status_message.edit_text(
                "✅ Upload Completed\n\n"
                f"📄 File:\n{pending['filename']}\n\n"
                f"🎵 Default Audio:\n{info['audio_name']}",
            )
    except asyncio.CancelledError:
        if info and not info.get("cancel_message_sent"):
            try:
                await status_message.edit_text(
                    "❌ Default Audio Update Cancelled\n\n"
                    "⚠️ Partial Output Deleted"
                )
            except Exception:
                pass
    except Exception as exc:
        logger.exception("Default audio update failed")
        if info and not info.get("cancel_message_sent"):
            try:
                await status_message.edit_text(
                    f"❌ Default Audio Update Failed\n\n{str(exc)[:700]}"
                )
            except Exception:
                pass
    finally:
        if info:
            info["running"] = False
        proc = RECORDING_SESSION_PROC.pop(task_id, None)
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        updater = ACTIVE_UPDATERS.pop(task_id, None)
        if updater and updater is not asyncio.current_task() and not updater.done():
            updater.cancel()
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        MEDIA_USER_TASKS.pop(pending["user_id"], None)
        DEFAULT_AUDIO_PENDING.pop(pending["token"], None)
        context.user_data.pop("default_audio_pending", None)
        context.user_data.pop("default_audio_task_id", None)
        context.user_data.pop("default_audio_process", None)
        context.user_data.pop("default_audio_upload_state", None)
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)


@owner_only
async def setowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/setowner <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    new_owner = context.args[0].strip()
    os.environ["BOT_OWNER_ID"] = new_owner
    await update.message.reply_text(f"✅ Owner set to `{new_owner}`", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0].strip()
    admins = get_admins()
    admins[uid] = {"added_by": update.effective_user.id, "time": datetime.now(IST).isoformat()}
    save_admins(admins)
    await update.message.reply_text(f"✅ Admin added: `{uid}`", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/removeadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0].strip()
    admins = get_admins()
    if uid in admins:
        del admins[uid]
        save_admins(admins)
        await update.message.reply_text(f"✅ Admin removed: `{uid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ `{uid}` is not in the admin list.", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = get_admins()
    if not admins:
        await update.message.reply_text("📌 There are no admins.")
        return
    lines = ["👥 *Admin List*\n"]
    for uid, info in admins.items():
        lines.append(f"• `{uid}` (Added: {info.get('time', 'N/A')[:10]})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@owner_only
async def proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxy = os.environ.get("JIOTV_PROXY_URL", "only_owner")
    await update.message.reply_text(f"🌐 Proxy URL: `{proxy}`\n\n(Visible to the owner only)", parse_mode=ParseMode.MARKDOWN)

# ── Watermark position commands ─────────────────

@owner_only
async def left_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/left <pixels>`\nExample: `/left 70px`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    offset = parse_pixel_value(context.args[0])
    if offset is None:
        await update.message.reply_text(
            "❌ Invalid value. Example: `/left 70px`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    save_watermark_position("left", offset)
    await update.message.reply_text(
        f"✅ Watermark set `{offset}px` from the left edge.",
        parse_mode=ParseMode.MARKDOWN,
    )

@owner_only
async def right_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/right <pixels>`\nExample: `/right 20px`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    offset = parse_pixel_value(context.args[0])
    if offset is None:
        await update.message.reply_text(
            "❌ Invalid value. Example: `/right 20px`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    save_watermark_position("right", offset)
    await update.message.reply_text(
        f"✅ Watermark set `{offset}px` from the right edge.",
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def refreshpl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force-refresh the DishTV M3U playlist cache from GitHub."""
    msg = await update.message.reply_text(
        "🔄 Refreshing the playlist…", parse_mode=ParseMode.MARKDOWN
    )
    try:
        channels = await asyncio.get_running_loop().run_in_executor(
            None, lambda: get_channels(force_refresh=True)
        )
        await msg.edit_text(
            f"✅ *Playlist updated!*\n"
            f"📺 Loaded `{len(channels)}` channels.\n"
            f"🔗 `{PLAYLIST_URL}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        await msg.edit_text(
            f"❌ Playlist refresh fail:\n`{exc}`",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── Cancel recording ──────────────────────────

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/cancel <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    cancel_id = context.args[0].strip()
    proc = ACTIVE_RECORDINGS.get(cancel_id)
    if not proc:
        await update.message.reply_text("❌ Recording not found or it has already ended.", parse_mode=ParseMode.MARKDOWN)
        return
    # Only allow the owner, admins, or the user who started it (checked via ACTIVE_RECORDINGS ownership)
    try:
        proc.kill()
    except Exception:
        pass
    ACTIVE_RECORDINGS.pop(cancel_id, None)
    await update.message.reply_text(f"✅ Recording `{cancel_id}` cancelled.", parse_mode=ParseMode.MARKDOWN)


# ── Premium commands (owner/admin only) ───────

PREMIUM_PLANS = {
    "free trial": "Free Trial",
    "basic": "Basic",
    "standard": "Standard",
    "pro": "Pro",
    "lifetime": "Lifetime",
}


def parse_premium_args(args):
    """Return (timedelta, plan, error) for /premium_add arguments.

    The user ID is handled by the command itself. With no duration, the
    requested default is 30 days on the Standard plan. Plan names may contain
    spaces, such as "Free Trial". Minutes and hours are also supported.
    """
    if not args:
        return None, None, "missing"

    duration = args[0].strip().lower()
    consumed = 1
    if len(args) >= 2 and args[0].isdigit():
        unit_aliases = {
            "m": "m", "min": "m", "mins": "m",
            "minut": "m", "minute": "m", "minutes": "m",
            "h": "h", "hr": "h", "hrs": "h",
            "hour": "h", "hours": "h",
            "d": "d", "day": "d", "days": "d",
        }
        separate_unit = unit_aliases.get(args[1].strip().lower())
        if separate_unit:
            duration = f"{duration}{separate_unit}"
            consumed = 2
    plan_text = " ".join(args[consumed:]).strip().lower()

    if duration in ("forever", "lifetime"):
        if plan_text and plan_text != "lifetime":
            return None, None, "lifetime_plan"
        return None, "Lifetime", None

    # Plain numbers mean days. Compact values support minutes, hours and days.
    # Keep the old 30D/1h format readable for existing admins.
    if re.fullmatch(r"\d+[mMhHdD]", duration):
        delta = parse_duration_str(duration)
        if not delta:
            return None, None, "duration"
    elif duration.isdigit():
        days = int(duration)
        delta = timedelta(days=days) if days >= 1 else None
    else:
        return None, None, "duration"

    if not delta or delta.total_seconds() < 60:
        return None, None, "duration"

    plan = PREMIUM_PLANS.get(plan_text or "standard")
    if not plan:
        return None, None, "plan"
    if plan == "Lifetime":
        return None, "Lifetime", None
    return delta, plan, None

@owner_admin_only
async def premium_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/premium_add <user_id> [duration] [plan_name]`\n\n"
            "Examples:\n"
            "`/premium_add 123456789` — 30 days Standard\n"
            "`/premium_add 123456789 59m` — 59 minutes Standard\n"
            "`/premium_add 123456789 30 minute` — 30 minutes Standard\n"
            "`/premium_add 123456789 1h` — 1 hour Standard\n"
            "`/premium_add 123456789 2h Pro` — 2 hours Pro\n"
            "`/premium_add 123456789 24h` — 24 hours Standard\n"
            "`/premium_add 123456789 7` — 7 days Standard\n"
            "`/premium_add 123456789 90 Pro` — 90 days Pro\n"
            "`/premium_add 123456789 forever` — Lifetime",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    uid = context.args[0].strip()
    if not uid.isdigit():
        await update.message.reply_text(
            "❌ Enter a valid numeric user ID.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration, plan, error = parse_premium_args(context.args[1:] or ["30"])
    if error == "lifetime_plan":
        await update.message.reply_text(
            "❌ Use only the `Lifetime` plan with `forever`/`Lifetime`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    if error == "duration":
        await update.message.reply_text(
            "❌ Enter a valid duration: `30m`, `59m`, `1h`, "
            "`2h`, `24h`, a whole number of days, or `forever`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    if error == "plan":
        await update.message.reply_text(
            "❌ Invalid plan. Available plans: "
            "`Free Trial`, `Basic`, `Standard`, `Pro`, `Lifetime`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    now = datetime.now(IST)
    expires_at = None if plan == "Lifetime" else now + duration
    users = get_premium_users()
    users[uid] = {
        "added_by": str(update.effective_user.id),
        "added_at": now.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "duration": "forever" if plan == "Lifetime" else str(duration),
        "plan": plan,
    }
    save_premium_users(users)

    expiry_text = "Lifetime (no expiry)" if plan == "Lifetime" else (
        f"{expires_at.strftime('%d-%b-%Y %I:%M %p')}"
    )
    if plan == "Lifetime":
        duration_text = "Lifetime"
    else:
        total_seconds = int(duration.total_seconds())
        if total_seconds % 86400 == 0:
            duration_text = f"{total_seconds // 86400} days"
        elif total_seconds % 3600 == 0:
            duration_text = f"{total_seconds // 3600} hours"
        else:
            duration_text = f"{total_seconds // 60} minutes"
    await update.message.reply_text(
        f"✅ *Premium Added!*\n\n"
        f"👤 User: `{uid}`\n"
        f"📦 Plan: `{plan}`\n"
        f"⏳ Duration: `{duration_text}`\n"
        f"📅 Expires: `{expiry_text}`",
        parse_mode=ParseMode.MARKDOWN
    )


@owner_admin_only
async def premium_expire_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/premium_expire <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0].strip()
    users = get_premium_users()
    if uid in users:
        del users[uid]
        save_premium_users(users)
        await update.message.reply_text(
            f"✅ Premium immediately expired for `{uid}`.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(f"❌ No premium record found for `{uid}`.", parse_mode=ParseMode.MARKDOWN)


# ── Owner + Admin commands ─────────────────────

@owner_admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/broadcast <message>`", parse_mode=ParseMode.MARKDOWN)
        return
    message = " ".join(context.args)
    user_data = get_user_data()
    sent = 0
    failed = 0
    for uid in user_data:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 *Broadcast*\n\n{message}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast sent: {sent} users\n❌ Failed: {failed} users")


def _media_reply_source(message):
    """Return a Telegram video source from a command's replied-to message."""
    if not message:
        return None
    if message.video:
        return {
            "file_id": message.video.file_id,
            "file_name": message.video.file_name or f"video_{message.message_id}.mp4",
            "file_size": message.video.file_size or 0,
        }
    if message.document:
        name = message.document.file_name or f"video_{message.message_id}.mkv"
        mime = (message.document.mime_type or "").lower()
        if mime.startswith("video/") or name.lower().endswith(
            (".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts")
        ):
            return {
                "file_id": message.document.file_id,
                "file_name": name,
                "file_size": message.document.file_size or 0,
            }
    return None


def _media_safe_name(name: str, fallback: str = "output.mp4") -> str:
    name = os.path.basename(str(name or "").strip())
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", name).strip(" .")
    return name or fallback


def _clean_stream_url(value: str) -> str:
    """Remove outer quotes commonly included when a URL is sent in commands."""
    url = str(value or "").strip()
    while len(url) >= 2 and url[0] == url[-1] and url[0] in {"'", '"', "`"}:
        url = url[1:-1].strip()
    return url


def _media_initial_text(filename: str, status: str = "Processing...") -> str:
    return _build_media_status_text(_media_safe_name(filename), 0.0, None, status)


def _media_register(task_id: str, status_message, user, filename: str,
                    duration: float = 0.0, status: str = "Processing...",
                    work_dir: str = ""):
    now = time.time()
    RECORDING_PROGRESS_INFO[task_id] = {
        "kind": "media",
        "process": None,
        "start_time": now,
        "duration": float(duration or 0),
        "total_duration": float(duration or 0),
        "filename": _media_safe_name(filename),
        "file_name": _media_safe_name(filename),
        "message_id": status_message.message_id,
        "chat_id": status_message.chat_id,
        "speed": 0.0,
        "speed_mbps": 0.0,
        "platform": "Media",
        "channel": {"channelCategoryId": "Media"},
        "user_obj": user,
        "user_id": user.id,
        "pct": 0.0,
        "elapsed": 0.0,
        "status": status,
        "running": True,
        "work_dir": work_dir,
    }


async def _media_start_updater(task_id: str, status_message):
    info = RECORDING_PROGRESS_INFO[task_id]
    task = asyncio.create_task(
        _auto_updater(
            task_id,
            status_message,
            info.get("progress_file"),
            info["filename"],
            info.get("total_duration", 0.0),
            info["start_time"],
        )
    )
    ACTIVE_UPDATERS[task_id] = task


async def _media_upload_output(context, chat_id: int, output_path: str,
                               caption: str = ""):
    with open(output_path, "rb") as media_file:
        try:
            await context.bot.send_video(
                chat_id=chat_id,
                video=media_file,
                caption=caption or None,
                read_timeout=600,
                write_timeout=600,
                supports_streaming=True,
            )
        except Exception:
            media_file.seek(0)
            await context.bot.send_document(
                chat_id=chat_id,
                document=media_file,
                caption=caption or None,
                read_timeout=600,
                write_timeout=600,
            )


async def _media_monitor_process(task_id: str, status_message, proc,
                                 output_path: str, work_dir: str,
                                 context, caption: str = "",
                                 upload_callback=None):
    """Monitor one cancellable FFmpeg process and edit its single status message."""
    info = RECORDING_PROGRESS_INFO.get(task_id)
    stderr_task = asyncio.create_task(proc.stderr.read())
    if info:
        info["process"] = proc
    RECORDING_SESSION_PROC[task_id] = proc
    try:
        while proc.returncode is None:
            info = RECORDING_PROGRESS_INFO.get(task_id)
            if not info or not info.get("running", True):
                if proc.returncode is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except Exception:
                        try:
                            proc.kill()
                            await proc.wait()
                        except Exception:
                            pass
                try:
                    await status_message.edit_text(
                        "❌ Process Cancelled\n\n⚠️ Partial Output Deleted"
                    )
                except Exception:
                    pass
                return False
            await asyncio.sleep(0.4)

        stderr = await stderr_task
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[-900:]
            logger.warning("Media FFmpeg failed for %s: %s", task_id, detail)
            try:
                await status_message.edit_text(
                    "❌ Processing Failed\n\n⚠️ Partial Output Deleted\n\n"
                    f"📄 File:\n{info.get('filename', 'output') if info else 'output'}\n\n"
                    f"⚠️ Reason:\n`{re.sub(r'\\s+', ' ', detail)[:700]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return False

        if (
            not upload_callback
            and (not os.path.exists(output_path) or os.path.getsize(output_path) == 0)
        ):
            try:
                await status_message.edit_text(
                    "❌ Processing Failed\n\n⚠️ Partial Output Deleted"
                )
            except Exception:
                pass
            return False

        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["pct"] = 100.0
            info["status"] = "Uploading..."
        try:
            await status_message.edit_text(
                f"✅ Processing Completed\n\n"
                f"📄 File:\n{_media_safe_name(os.path.basename(output_path))}\n\n"
                "📤 Uploading...",
                reply_markup=_build_rec_progress_inline(task_id),
            )
        except Exception:
            pass
        if upload_callback:
            await upload_callback()
        else:
            await _media_upload_output(
                context, status_message.chat_id, output_path, caption
            )
        try:
            await status_message.edit_text(
                f"✅ Upload Completed\n\n"
                f"📄 File:\n{_media_safe_name(os.path.basename(output_path))}\n\n"
                "⏳ Server copy auto-deletes in 3 hours."
            )
        except Exception:
            pass
        return True
    except asyncio.CancelledError:
        raise
    finally:
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["running"] = False
        RECORDING_SESSION_PROC.pop(task_id, None)
        ACTIVE_RECORDINGS.pop(task_id, None)
        updater = ACTIVE_UPDATERS.pop(task_id, None)
        if updater and not updater.done():
            updater.cancel()
        MEDIA_USER_TASKS.pop(info.get("user_id") if info else None, None)
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        global _active_processes
        _active_processes = max(0, _active_processes - 1)


async def _media_remux_with_metadata(
    task_id: str,
    status_message,
    input_path: str,
    output_path: str,
    work_dir: str,
) -> tuple[bool, str]:
    """Remux a completed media file with dynamic metadata and stream maps."""
    progress_file = os.path.join(work_dir, f"metadata_{task_id}.txt")
    metadata = await build_ffmpeg_metadata(input_path)
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", input_path,
        "-map", "0", "-map_metadata", "0", "-map_chapters", "0",
        "-c", "copy", "-progress", progress_file, "-nostats",
        *metadata, output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    RECORDING_SESSION_PROC[task_id] = proc
    info = RECORDING_PROGRESS_INFO.get(task_id)
    if info:
        info["process"] = proc
        info["progress_file"] = progress_file
        info["status"] = "📝 Adding metadata"
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        while proc.returncode is None:
            info = RECORDING_PROGRESS_INFO.get(task_id)
            if not info or not info.get("running", True):
                if proc.returncode is None:
                    proc.terminate()
                await proc.wait()
                return False, "Remux cancelled."
            await asyncio.sleep(0.4)
        stderr = await stderr_task
        if proc.returncode != 0:
            return False, stderr.decode(errors="replace")[-1200:]
        return True, ""
    finally:
        RECORDING_SESSION_PROC.pop(task_id, None)
        info = RECORDING_PROGRESS_INFO.get(task_id)
        if info:
            info["process"] = None
        try:
            os.remove(progress_file)
        except OSError:
            pass


async def _media_run_ffmpeg(update, context, status_message, filename: str,
                            cmd: list[str], output_path: str, work_dir: str,
                            duration: float = 0.0, status: str = "Processing...",
                            caption: str = "", upload_callback=None,
                            metadata_input: str | None = None,
                            metadata_streams: dict[str, list[int] | None] | None = None):
    global _active_processes
    user = update.effective_user
    task_id = _secrets.token_hex(8)
    MEDIA_USER_TASKS[user.id] = task_id
    _active_processes += 1
    _media_register(task_id, status_message, user, filename, duration, status, work_dir)
    progress_file = os.path.join(work_dir, f"progress_{task_id}.txt")
    info = RECORDING_PROGRESS_INFO[task_id]
    info["progress_file"] = progress_file
    if "-progress" not in cmd:
        cmd = list(cmd)
        insert_at = len(cmd) - 1
        cmd[insert_at:insert_at] = ["-progress", progress_file, "-nostats"]
    if metadata_input is None:
        try:
            input_position = cmd.index("-i")
            metadata_input = cmd[input_position + 1]
        except (ValueError, IndexError):
            metadata_input = None
    if metadata_input:
        metadata = await build_ffmpeg_metadata(
            metadata_input,
            selected_streams=metadata_streams,
        )
        cmd[-1:-1] = metadata
    await status_message.edit_text(
        _media_initial_text(filename, status),
        reply_markup=_build_rec_progress_inline(task_id),
    )
    await _media_start_updater(task_id, status_message)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        info["running"] = False
        await status_message.edit_text(f"❌ Processing Failed\n\n{str(exc)[:800]}")
        ACTIVE_UPDATERS.pop(task_id, None)
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        MEDIA_USER_TASKS.pop(user.id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)
        return
    await _media_monitor_process(
        task_id, status_message, proc, output_path, work_dir, context,
        caption, upload_callback,
    )


async def _media_download_telegram(context, source: dict, destination: str):
    telegram_file = await context.bot.get_file(
        source["file_id"],
        read_timeout=600,
        write_timeout=600,
        connect_timeout=60,
        pool_timeout=60,
    )
    await telegram_file.download_to_drive(
        custom_path=destination,
        read_timeout=1800,
        write_timeout=1800,
        connect_timeout=60,
        pool_timeout=60,
    )


def _parse_trim_range(args: list[str], duration: float) -> tuple[int, int]:
    """Parse START END while accepting natural separators such as `to`."""
    values = [
        str(value).strip()
        for value in args
        if str(value).strip().lower() not in {"to", "until", "-", "–", "—"}
    ]
    if len(values) < 2:
        raise ValueError("Usage: /trim START to END")

    def parse_clock(value: str) -> int:
        pieces = [int(part) for part in value.split(":")]
        if len(pieces) == 2:
            return pieces[0] * 60 + pieces[1]
        if len(pieces) == 3:
            return pieces[0] * 3600 + pieces[1] * 60 + pieces[2]
        raise ValueError("Invalid time.")

    start, end = parse_clock(values[0]), parse_clock(values[1])
    if start < 0 or end <= start or start >= duration:
        raise ValueError("Invalid trim range.")
    return start, min(end, int(duration))


def _media_video_input_args(source_path: str, output_path: str,
                            duration: float = 0.0) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-y", "-i", source_path,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        *(["-t", str(duration)] if duration else []),
        output_path,
    ]


async def _media_local_job(update, context, source: dict, operation: str,
                           args: list[str], status_message=None):
    user = update.effective_user
    target_message = update.effective_message
    if user.id in MEDIA_USER_TASKS:
        await target_message.reply_text("⏳ One of your media jobs is already running.")
        return
    if source.get("file_size") and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await target_message.reply_text("❌ The video exceeds the Telegram download limit.")
        return
    work_dir = f"/tmp/media_{_secrets.token_hex(8)}"
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, _media_safe_name(source["file_name"], "source.mp4"))
    status = status_message or await target_message.reply_text(
        _media_initial_text(source["file_name"], "Downloading...")
    )
    if status_message:
        await status.edit_text(
            _media_initial_text(source["file_name"], "Downloading..."),
        )
    try:
        await _media_download_telegram(context, source, input_path)
        duration = await _media_duration_seconds(input_path)
        output_name = f"{Path(source['file_name']).stem}_{operation}.mp4"
        output_path = os.path.join(work_dir, _media_safe_name(output_name))
        if operation in ("compress", "compressadvance"):
            if operation in ("compress", "compressadvance"):
            if operation == "compressadvance":
                # 576p source -> fake/upscaled 1080p.
                height = 1080

                # Target approximately 300–380 MB for the complete file.
                # Use the 380 MB ceiling and reserve muxing headroom.
                duration_sec = max(float(duration or 0), 1.0)
                target_bytes = 380 * 1024 * 1024
                audio_bps = 128000
                target_video_bps = int(
                    max(
                        300000,
                        (target_bytes * 8 * 0.92 / duration_sec) - audio_bps
                    )
                )
                target_video_kbps = max(300, target_video_bps // 1000)
            else:
                height = 720
                target_video_kbps = None
            cmd = [
                "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                "-map", "0:v:0", "-map", "0:a?",
                "-map", "0:s?", "-map", "0:t?",
                "-vf", (
                    "scale=1920:1080:flags=lanczos"
                    if operation == "compressadvance"
                    else f"scale=-2:{height}"
                ),
                "-c:v", "libx264",
                "-preset", "veryfast",
                *(
                    ["-b:v", f"{target_video_kbps}k",
                     "-maxrate", f"{target_video_kbps}k",
                     "-bufsize", f"{target_video_kbps * 2}k"]
                    if operation == "compressadvance"
                    else ["-crf", "24"]
                ),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-c:s", "copy", "-c:t", "copy", "-map_chapters", "0",
                "-movflags", "+faststart", output_path,
            ]
            await _media_run_ffmpeg(
                update, context, status, source["file_name"], cmd, output_path,
                work_dir, duration, "Compressing...",
                metadata_streams={"video": [0], "audio": None, "subtitle": None},
            )
        elif operation == "trim":
            try:
                start, end = _parse_trim_range(args, duration)
            except ValueError:
                await status.edit_text(
                    "❌ Usage: `/trim START to END`\n"
                    "Example: `/trim 00:00:10 to 00:01:00`"
                )
                shutil.rmtree(work_dir, ignore_errors=True)
                return
            cmd = [
                "ffmpeg", "-hide_banner", "-y", "-ss", str(start), "-to", str(end),
                "-i", input_path, "-map", "0", "-c", "copy",
                "-map_chapters", "0",
                "-avoid_negative_ts", "make_zero", output_path,
            ]
            await _media_run_ffmpeg(
                update, context, status, source["file_name"], cmd, output_path,
                work_dir, end - start, "Trimming...",
                metadata_streams={"video": None, "audio": None, "subtitle": None},
            )
        elif operation == "audiotrack":
            cmd = [
                "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
                "-map", "0:t?", "-c", "copy", "-map_chapters", "0",
                "-disposition:a:0", "default", output_path,
            ]
            await _media_run_ffmpeg(
                update, context, status, source["file_name"], cmd, output_path,
                work_dir, duration, "Updating audio tracks...",
                caption=_AUDIO_TRACK_COMPATIBILITY_NOTE,
                metadata_streams={"video": [0], "audio": None, "subtitle": None},
            )
        elif operation == "watermark":
            text = " ".join(args).strip() or "Dishtv Rec bot"
            escaped = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            cmd = [
                "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                "-vf", f"drawtext=text='{escaped}':x=w-tw-24:y=h-th-24:"
                       "fontsize=28:fontcolor=white:box=1:boxcolor=black@0.55",
                "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?",
                "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                "-b:a", "128k", "-c:s", "copy", "-c:t", "copy",
                "-map_chapters", "0", "-movflags", "+faststart", output_path,
            ]
            await _media_run_ffmpeg(
                update, context, status, source["file_name"], cmd, output_path,
                work_dir, duration, "Adding watermark...",
                metadata_streams={"video": [0], "audio": None, "subtitle": None},
            )
        elif operation == "screenshot":
            count = max(1, min(30, int(args[0])) if args and args[0].isdigit() else 5)
            pattern = os.path.join(work_dir, "shot_%02d.jpg")
            fps = count / max(duration, 1)
            cmd = [
                "ffmpeg", "-hide_banner", "-y", "-i", input_path,
                "-vf", f"fps={fps}", "-frames:v", str(count), pattern,
            ]
            async def upload_shots():
                paths = sorted(Path(work_dir).glob("shot_*.jpg"))
                media = []
                handles = []
                for path in paths:
                    handle = open(path, "rb")
                    handles.append(handle)
                    media.append(InputMediaPhoto(handle))
                if media:
                    await context.bot.send_media_group(
                        chat_id=status.chat_id, media=media
                    )
                for handle in handles:
                    handle.close()
            await _media_run_ffmpeg(
                update, context, status, source["file_name"], cmd,
                os.path.join(work_dir, "shot_01.jpg"), work_dir, duration,
                "Extracting screenshots...", upload_callback=upload_shots,
                metadata_streams={"video": [0], "audio": [], "subtitle": []},
            )
        else:
            raise ValueError("Unknown media operation.")
    except Exception as exc:
        logger.exception("Media %s setup failed", operation)
        MEDIA_USER_TASKS.pop(user.id, None)
        try:
            await status.edit_text(f"❌ {operation.title()} failed\n\n{str(exc)[:900]}")
        except Exception:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)


async def _media_source_command(update, context, operation: str):
    source = _media_reply_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            f"❌ Reply to a video or video document with `/{operation}`."
        )
        return
    await _media_local_job(update, context, source, operation, context.args)


@require_verification
async def compress_cmd(update, context):
    await _media_source_command(update, context, "compress")


@require_verification
async def compressadvance_cmd(update, context):
    await _media_source_command(update, context, "compressadvance")


@require_verification
async def screenshot_cmd(update, context):
    source = _media_reply_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to a video or video document with `/screenshot`."
        )
        return
    if context.args:
        await _media_local_job(update, context, source, "screenshot", context.args)
        return
    if source.get("file_size") and source["file_size"] > TELEGRAM_BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text("❌ The video exceeds the Telegram download limit.")
        return

    token = _secrets.token_hex(6)
    probe_dir = f"/tmp/screenshot_probe_{token}"
    probe_path = os.path.join(
        probe_dir, _media_safe_name(source["file_name"], "source.mp4")
    )
    os.makedirs(probe_dir, exist_ok=True)
    try:
        await _media_download_telegram(context, source, probe_path)
        duration = await _media_duration_seconds(probe_path)
        width, height = await _media_video_dimensions(probe_path)
    except Exception as exc:
        logger.exception("Screenshot source probe failed")
        await update.message.reply_text(
            f"❌ Screenshot source read failed.\n\n{str(exc)[:700]}"
        )
        return
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    for old_token, pending in list(SCREENSHOT_PENDING.items()):
        if time.time() - pending.get("created_at", 0) > QUALITY_PENDING_TTL:
            SCREENSHOT_PENDING.pop(old_token, None)
    resolution = f"{height}p" if height else "Unknown"
    SCREENSHOT_PENDING[token] = {
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "source": source,
        "duration": duration,
        "duration_text": (
            f"{int(duration // 60):02d}:{int(duration % 60):02d}"
            if duration < 3600 else _fmt_time(duration)
        ),
        "resolution": resolution,
        "created_at": time.time(),
    }
    text, markup = _screenshot_menu(token, duration, width, height)
    await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


@require_verification
async def trim_cmd(update, context):
    await _media_source_command(update, context, "trim")


@require_verification
async def watermark_cmd(update, context):
    await _media_source_command(update, context, "watermark")


@require_verification
async def audiotrack_cmd(update, context):
    await _media_source_command(update, context, "audiotrack")


@require_verification
async def download_cmd(update, context):
    global _active_processes
    if not context.args:
        await update.message.reply_text("❌ Usage: `/download <video URL> [filename]`")
        return
    user = update.effective_user
    if user.id in MEDIA_USER_TASKS:
        await update.message.reply_text("⏳ One of your media jobs is already running.")
        return
    url = context.args[0]
    requested_name = _media_safe_name(" ".join(context.args[1:]), "")
    work_dir = f"/tmp/media_dl_{_secrets.token_hex(8)}"
    os.makedirs(work_dir, exist_ok=True)
    status = await update.message.reply_text(
        _media_initial_text(requested_name or "Download", "Downloading...")
    )
    task_id = _secrets.token_hex(8)
    MEDIA_USER_TASKS[user.id] = task_id
    _active_processes += 1
    _media_register(task_id, status, user, requested_name or "Download", 0.0, "Downloading...", work_dir)
    info = RECORDING_PROGRESS_INFO[task_id]
    await _media_start_updater(task_id, status)

    def run_ytdlp():
        def hook(data):
            live_info = RECORDING_PROGRESS_INFO.get(task_id)
            if not live_info or not live_info.get("running", True):
                raise yt_dlp.utils.DownloadError("Cancelled by user.")
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                if total:
                    live_info["source_size"] = total
                live_info["status"] = "Downloading..."
                live_info["speed_mbps"] = (data.get("speed") or 0) / 1024 / 1024
        outtmpl = os.path.join(work_dir, "%(title).160s.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
        }
        cookies_path = _user_cookies_path(user.id)
        if _user_has_cookies(user.id):
            opts["cookiefile"] = cookies_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        await asyncio.to_thread(run_ytdlp)
        outputs = [
            p for p in Path(work_dir).iterdir()
            if p.is_file() and not p.name.startswith("progress_")
        ]
        if not outputs:
            raise RuntimeError("Downloader produced no output.")
        output = max(outputs, key=lambda p: p.stat().st_size)
        if requested_name:
            target = Path(work_dir) / requested_name
            if target.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
                target = target.with_suffix(output.suffix)
            output.rename(target)
            output = target
        metadata_output = Path(work_dir) / f"{output.stem}_metadata{output.suffix}"
        metadata_ok, metadata_error = await _media_remux_with_metadata(
            task_id,
            status,
            str(output),
            str(metadata_output),
            work_dir,
        )
        if not metadata_ok:
            raise RuntimeError(f"Metadata remux failed: {metadata_error}")
        output.unlink(missing_ok=True)
        metadata_output.rename(output)
        info["running"] = False
        updater = ACTIVE_UPDATERS.pop(task_id, None)
        if updater and not updater.done():
            updater.cancel()
        await status.edit_text(
            f"✅ Download Completed\n\n📄 File:\n{output.name}\n\n"
            "📤 Uploading..."
        )
        await _media_upload_output(context, status.chat_id, str(output))
        await status.edit_text(
            f"✅ Upload Completed\n\n📄 File:\n{output.name}\n\n"
            "⏳ Server copy auto-deletes in 3 hours."
        )
    except Exception as exc:
        cancelled = not RECORDING_PROGRESS_INFO.get(task_id, {}).get("running", True)
        await status.edit_text(
            "❌ Process Cancelled\n\n⚠️ Partial Output Deleted"
            if cancelled else f"❌ Download Failed\n\n{str(exc)[:900]}"
        )
    finally:
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        RECORDING_SESSION_PROC.pop(task_id, None)
        MEDIA_USER_TASKS.pop(user.id, None)
        updater = ACTIVE_UPDATERS.pop(task_id, None)
        if updater and not updater.done():
            updater.cancel()
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)


@require_verification
async def drec_cmd(update, context):
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/drec <stream URL> <duration> [filename]`\n"
            "Example: `/drec https://example/live.m3u8 00:00:30 demo`"
        )
        return
    try:
        parts = [int(x) for x in context.args[1].split(":")]
        duration = parts[-1] + (parts[-2] * 60 if len(parts) > 1 else 0) + (
            parts[-3] * 3600 if len(parts) > 2 else 0
        )
        if duration <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter the duration in HH:MM:SS or MM:SS format.")
        return
    if update.effective_user.id in MEDIA_USER_TASKS:
        await update.message.reply_text("⏳ One of your media jobs is already running.")
        return
    url = _clean_stream_url(context.args[0])
    if not url.lower().startswith(("http://", "https://")):
        await update.message.reply_text("❌ Enter a valid HTTP/HTTPS stream URL.")
        return
    name = _media_safe_name(" ".join(context.args[2:]) or "direct_recording.mp4")
    if not Path(name).suffix:
        name += ".mp4"
    work_dir = f"/tmp/media_rec_{_secrets.token_hex(8)}"
    os.makedirs(work_dir, exist_ok=True)
    output = os.path.join(work_dir, name)
    status = await update.message.reply_text(_media_initial_text(name, "Recording..."))
    task_id = _secrets.token_hex(8)
    MEDIA_USER_TASKS[update.effective_user.id] = task_id
    global _active_processes
    _active_processes += 1
    _media_register(task_id, status, update.effective_user, name, duration, "Recording...", work_dir)
    try:
        proc, progress_file = await start_recording(
            url, duration, output, task_id, quality="576p",
            audio_mode="multi", audio_tracks=[],
            dishtv_channel=False,
        )
        info = RECORDING_PROGRESS_INFO[task_id]
        info["progress_file"] = progress_file
        await _media_start_updater(task_id, status)
        await _media_monitor_process(task_id, status, proc, output, work_dir, context)
    except Exception as exc:
        await status.edit_text(f"❌ Recording Failed\n\n{str(exc)[:900]}")
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        MEDIA_USER_TASKS.pop(update.effective_user.id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)


@require_verification
async def merge_cmd(update, context):
    source = _media_reply_source(update.message.reply_to_message)
    if not source:
        await update.message.reply_text(
            "❌ Reply to the first video with `/merge`. "
            "Then send the second video."
        )
        return
    MEDIA_MERGE_SESSIONS[update.effective_user.id] = {
        "first": source,
        "chat_id": update.effective_chat.id,
        "created_at": time.time(),
    }
    await update.message.reply_text(
        "🎬 First video saved.\nSend the second video now; merging will start automatically."
    )


async def media_merge_video_handler(update, context):
    user = update.effective_user
    session = MEDIA_MERGE_SESSIONS.get(user.id)
    second = _media_reply_source(update.message) or _media_reply_source(
        update.message.reply_to_message
    )
    if not session or not second:
        return
    if time.time() - session.get("created_at", 0) > 30 * 60:
        MEDIA_MERGE_SESSIONS.pop(user.id, None)
        await update.message.reply_text("❌ Merge session expired. Start again with `/merge`.")
        return
    MEDIA_MERGE_SESSIONS.pop(user.id, None)
    source = session["first"]
    work_dir = f"/tmp/media_merge_{_secrets.token_hex(8)}"
    os.makedirs(work_dir, exist_ok=True)
    status = await update.message.reply_text(
        _media_initial_text("merged_video.mp4", "Downloading...")
    )
    task_id = _secrets.token_hex(8)
    MEDIA_USER_TASKS[user.id] = task_id
    global _active_processes
    _active_processes += 1
    _media_register(task_id, status, user, "merged_video.mp4", 0.0, "Merging...", work_dir)
    try:
        first_path = os.path.join(work_dir, "first")
        second_path = os.path.join(work_dir, "second")
        await _media_download_telegram(context, source, first_path)
        await _media_download_telegram(context, second, second_path)
        list_path = os.path.join(work_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for path in (first_path, second_path):
                escaped_path = path.replace("'", "'\\''")
                handle.write(f"file '{escaped_path}'\n")
        output = os.path.join(work_dir, "merged_video.mp4")
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-map", "0", "-c", "copy",
            "-map_chapters", "0", output,
        ]
        await _media_run_ffmpeg(
            update, context, status, "merged_video.mp4", cmd, output, work_dir,
            0.0, "Merging...",
            caption=_AUDIO_TRACK_COMPATIBILITY_NOTE,
            metadata_input=first_path,
            metadata_streams={"video": None, "audio": None, "subtitle": None},
        )
    except Exception as exc:
        await status.edit_text(f"❌ Merge Failed\n\n{str(exc)[:900]}")
        RECORDING_PROGRESS_INFO.pop(task_id, None)
        MEDIA_USER_TASKS.pop(user.id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        _active_processes = max(0, _active_processes - 1)


# ── Main ───────────────────────────────────────

def main():
    load_bot_mode()
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        return

    _start_local_telegram_api()
    app_builder = Application.builder().token(BOT_TOKEN)
    if TELEGRAM_LOCAL_API_ENABLED:
        local_api_base = f"{TELEGRAM_LOCAL_API_URL}/bot"
        local_file_base = f"{TELEGRAM_LOCAL_API_URL}/file/bot"
        app_builder = app_builder.base_url(local_api_base).base_file_url(local_file_base)
        logger.info(
            "Local Telegram Bot API enabled; file limit %s.",
            telegram_limit_text(),
        )
    else:
        logger.info(
            "Telegram cloud Bot API enabled; file limits are %s download / %s upload.",
            telegram_limit_text(),
            telegram_upload_limit_text(),
        )
    app = app_builder.concurrent_updates(True).build()
    logger.info(
        "Group access policy: %s authorized group(s); all other groups will be left automatically. "
        "Private chat access: owner/premium only.",
        len(AUTHORIZED_GROUP_IDS - UNAUTHORIZED_GROUP_IDS),
    )

    # Always inspect the chat before any command or callback handler.
    app.add_handler(TypeHandler(Update, bot_mode_access_guard), group=-2)
    app.add_handler(TypeHandler(Update, leave_unauthorized_group), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("login", login_cmd))
    app.add_handler(CommandHandler("otp", otp_cmd))
    app.add_handler(CommandHandler("qualitymax", qualitymax_cmd))
    app.add_handler(CommandHandler("merge_video_and_audio", merge_video_audio_cmd))
    app.add_handler(CommandHandler("download", download_cmd))
    app.add_handler(CommandHandler("drec", drec_cmd))
    app.add_handler(CommandHandler("compress", compress_cmd))
    app.add_handler(CommandHandler("compressadvance", compressadvance_cmd))
    app.add_handler(CommandHandler("screenshot", screenshot_cmd))
    app.add_handler(CommandHandler("trim", trim_cmd))
    app.add_handler(CommandHandler("merge", merge_cmd))
    app.add_handler(CommandHandler("watermark", watermark_cmd))
    app.add_handler(CommandHandler("audiotrack", audiotrack_cmd))
    app.add_handler(CommandHandler("streamextractor", stream_extractor_cmd))
    app.add_handler(MessageHandler(
        filters.Regex(re.compile(
            r"^/StreamExtractor(?:@\w+)?(?:\s|$)", re.IGNORECASE
        )),
        stream_extractor_cmd,
    ))
    app.add_handler(MessageHandler(
        filters.Regex(re.compile(
            r"^/merge_video_and_audio(?:@\w+)?(?:\s|$)",
            re.IGNORECASE,
        )),
        merge_video_audio_cmd,
    ))
    # Telegram command names are normally lowercase; accept the exact
    # user-facing `/Qualitymax` spelling as well.
    app.add_handler(MessageHandler(
        filters.Regex(r"^/Qualitymax(?:@\w+)?(?:\s|$)"),
        qualitymax_cmd,
    ))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("set_cookies", set_cookies_cmd))
    app.add_handler(CommandHandler("cookies_status", cookies_status_cmd))
    app.add_handler(CommandHandler("del_cookies", del_cookies_cmd))
    app.add_handler(CommandHandler("rec", rec_cmd))
    app.add_handler(CommandHandler("dl", rec_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("myinfo", myinfo))
    app.add_handler(CommandHandler("public", public_cmd))
    app.add_handler(CommandHandler("private", private_cmd))

    # Owner + Admin
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # Premium (owner/admin only)
    app.add_handler(CommandHandler("premium_add", premium_add_cmd))
    app.add_handler(CommandHandler("premium_expire", premium_expire_cmd))

    # Owner only
    app.add_handler(CommandHandler("setdefaultaudio", setdefaultaudio_cmd))
    app.add_handler(CommandHandler("setowner", setowner_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("proxy", proxy_cmd))
    app.add_handler(CommandHandler("left", left_cmd))
    app.add_handler(CommandHandler("right", right_cmd))
    app.add_handler(CommandHandler("refreshpl", refreshpl_cmd))

    app.add_handler(CallbackQueryHandler(category_callback, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(back_callback, pattern="^back_"))
    app.add_handler(CallbackQueryHandler(howto_verify_callback, pattern="^howto_verify$"))
    app.add_handler(CallbackQueryHandler(rec_aspect_callback, pattern=r"^rec_aspect:"))
    app.add_handler(CallbackQueryHandler(ott_quality_callback, pattern=r"^ott_quality:"))
    app.add_handler(CallbackQueryHandler(rec_audio_callback, pattern=r"^rec_audio:"))
    app.add_handler(CallbackQueryHandler(rec_watermark_callback, pattern=r"^rec_wm:"))
    app.add_handler(CallbackQueryHandler(ott_watermark_callback, pattern=r"^ott_wm:"))
    app.add_handler(CallbackQueryHandler(rec_progress_callback, pattern=r"^progress:"))
    app.add_handler(CallbackQueryHandler(rec_progress_callback, pattern=r"^progress$"))
    app.add_handler(CallbackQueryHandler(rec_cancel_callback,   pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(rec_cancel_callback,   pattern=r"^cancel$"))
    app.add_handler(CallbackQueryHandler(merge_mode_callback, pattern=r"^merge:"))
    app.add_handler(CallbackQueryHandler(
        stream_extractor_callback, pattern=r"^stream_extract:"
    ))
    app.add_handler(CallbackQueryHandler(
        stream_extractor_callback, pattern=r"^stream_extractor_cancel:"
    ))
    app.add_handler(CallbackQueryHandler(
        stream_custom_toggle_callback, pattern=r"^stream_custom_toggle:"
    ))
    app.add_handler(CallbackQueryHandler(
        stream_custom_done_callback, pattern=r"^stream_custom_done:"
    ))
    app.add_handler(CallbackQueryHandler(
        screenshot_callback, pattern=r"^screenshot:"
    ))
    app.add_handler(CallbackQueryHandler(
        screenshot_cancel_callback, pattern=r"^screenshot_cancel:"
    ))
    app.add_handler(CallbackQueryHandler(
        default_audio_select_callback, pattern=r"^default_audio_select:"
    ))
    app.add_handler(CallbackQueryHandler(
        default_audio_cancel_callback, pattern=r"^default_audio_cancel:"
    ))
    app.add_handler(MessageHandler(
        (filters.AUDIO | filters.Document.AUDIO) & ~filters.COMMAND,
        merge_audio_message_handler,
    ))
    app.add_handler(MessageHandler(
        (filters.VIDEO | filters.Document.VIDEO) & ~filters.COMMAND,
        media_merge_video_handler,
    ), group=1)
    # Capture watermark URL pasted by owner after clicking "Change Watermark Link"
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(int(BOT_OWNER_ID)) if BOT_OWNER_ID else filters.TEXT & ~filters.COMMAND,
        ott_watermark_url_message_handler,
    ), group=0)
    # Capture the DishTV watermark URL pasted by the owner after clicking
    # "Change Watermark Link".
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(int(BOT_OWNER_ID)) if BOT_OWNER_ID else filters.TEXT & ~filters.COMMAND,
        watermark_url_message_handler,
    ), group=1)
    # Cookie uploads are handled after existing media document handlers so
    # normal audio/video merge behavior is not intercepted.
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND,
        cookies_document_handler,
    ), group=2)
    app.add_handler(CallbackQueryHandler(qualitymax_callback, pattern=r"^qualitymax:"))

    # Auto-register bot commands with Telegram (shows in "/" menu)
    commands = [
        BotCommand("start",    "View bot information and commands"),
        BotCommand("help",     "View bot information and commands"),
        BotCommand("qualitymax", "Convert replied video quality"),
        BotCommand("merge_video_and_audio", "Merge video and audio"),
        BotCommand("download", "Download a video from a URL"),
        BotCommand("drec", "Record a direct stream"),
        BotCommand("compress", "Compress a replied video"),
        BotCommand("compressadvance", "Compress a replied video to 576p"),
        BotCommand("screenshot", "Take screenshots from a replied video"),
        BotCommand("trim", "Trim a replied video"),
        BotCommand("merge", "Merge two videos"),
        BotCommand("watermark", "Add a watermark to a replied video"),
        BotCommand("audiotrack", "Remux audio tracks in a replied video"),
        BotCommand("setdefaultaudio", "Set the default audio label (Owner)"),
        BotCommand("streamextractor", "Extract audio, subtitles, and streams"),
        BotCommand("verify",   "Unlock access for 40 minutes"),
        BotCommand("set_cookies", "Upload OTT cookies.txt"),
        BotCommand("cookies_status", "Show stored cookies"),
        BotCommand("del_cookies", "Delete stored cookies"),
        BotCommand("rec",      "Catchup/recording link lo"),
        BotCommand("dl",       "DVR recording/download lo"),
        BotCommand("schedule", "Schedule a future recording"),
        BotCommand("channels", "View the channel list"),
        BotCommand("search",   "Search for a channel"),
        BotCommand("myinfo",   "View your information"),
        BotCommand("public", "Enable Public Mode (Owner)"),
        BotCommand("private", "Enable Private Mode (Owner)"),
        BotCommand("broadcast","(Admin) Send a message to everyone"),
        BotCommand("cancel",   "Cancel a recording"),
        BotCommand("premium_add", "Owner/Admin premium add kare"),
        BotCommand("premium_expire", "Owner/Admin premium expire kare"),
        BotCommand("left",       "(Owner) Set the left watermark position"),
        BotCommand("right",      "(Owner) Set the right watermark position"),
        BotCommand("refreshpl",  "(Owner) Force-refresh the DishTV GitHub playlist"),
    ]
    async def post_init(application):
        global SCHEDULE_MANAGER_TASK
        await application.bot.set_my_commands(commands)
        if SCHEDULE_MANAGER_TASK is None or SCHEDULE_MANAGER_TASK.done():
            SCHEDULE_MANAGER_TASK = asyncio.create_task(
                _scheduled_recording_manager(application)
            )
            logger.info(
                "Scheduled recording manager started; %s saved schedule(s) loaded.",
                len(SCHEDULED_RECORDINGS),
            )
        logger.info("Bot commands registered with Telegram.")

    app.post_init = post_init

    logger.info("JioTV Telegram Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
