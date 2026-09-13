import os
GROUP_CHAT_ID = int(os.getenv("ALLOWED_REC_GROUP_ID", "-1003726271113"))
# v35 — Sony LIV SD/HD groups + automatic HLS highest-variant selection (1080p when available)
import os

# Base directory for persistent bot data
DATA_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIRECTORY, exist_ok=True)
import time
import logging
import random
import shlex
import shutil
import asyncio
import signal
import json
import secrets
import re
from urllib.parse import parse_qsl, urlsplit
from pathlib import Path
import psutil
import requests
from typing import Tuple, Optional
from os.path import join
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
# Create an event loop before Pyrogram is imported (Python 3.14 compatibility)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    BotCommand,
    MenuButtonCommands,
)
from datetime import datetime, timedelta
import config

SHORTENER_API = os.getenv("SHORTENER_API", "")

SHORTENER = "https://shrinkme.click"

TIMEZONE = "Asia/Kolkata"

API_ID = getattr(config, "API_ID", 29481626)

# Required global settings (defined before HELP_TEXT and decorators).
BRAND_TITLE = getattr(config, "BRAND_TITLE", "Little Bot")
SUPPORT_USERNAME = getattr(config, "SUPPORT_USERNAME", "TPlayOwner_bot").lstrip("@")
AUTH_USERS = set()
try:
    AUTH_USERS = {int(x) for x in getattr(config, "AUTH_USERS", []) if x is not None}
except (TypeError, ValueError):
    AUTH_USERS = set()

# Pyrogram authorization filter. Owner/admin checks are handled by the
# existing helper functions; this filter prevents an undefined AUTH error.
def _auth_filter(_, __, message):
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", None)
    return uid is not None and (uid in AUTH_USERS or uid == getattr(config, "OWNER_ID", None))

AUTH = filters.create(_auth_filter)
from Channel import get_channel_url, get_public_channels
import pytz

# Timezone from config.py
tz = pytz.timezone(TIMEZONE)

def tz_time(*args):
    return datetime.now(tz).timetuple()

# Apply dynamic timezone for logging timestamps
logging.Formatter.converter = tz_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt = "%d-%m-%Y %I:%M:%S %p " + tz.tzname(datetime.now())
)

LOG = logging.getLogger(__name__)

app = Client("recorder", bot_token=getattr(config, "BOT_TOKEN", getattr(config, "TELEGRAM_BOT_TOKEN", "")), api_id=API_ID, api_hash=config.API_HASH)

BOT_MENU_COMMANDS = [
    BotCommand("start", "Open the bot welcome menu"),
    BotCommand("help", "Show help and available features"),
    BotCommand("rec", "Start an HLS/M3U8 recording"),
    BotCommand("sony", "Open Sony LIV recording"),
    BotCommand("schedule", "Schedule a recording"),
    BotCommand("schedules", "View scheduled recordings"),
    BotCommand("status", "Check recording progress"),
    BotCommand("cancel", "Stop the active recording"),
    BotCommand("token", "Generate a verification token"),
    BotCommand("audiotrack", "Choose audio-track settings"),
    BotCommand("gdrive", "Open Google Drive tools"),
    BotCommand("channel", "Show available channels"),
    BotCommand("playlistrefresh", "Refresh the channel playlist"),
]


async def _configure_bot_menu(client: Client) -> None:
    """Keep Telegram's command menu synchronized with the bot handlers."""
    try:
        await client.set_bot_commands(BOT_MENU_COMMANDS)
        await client.set_chat_menu_button(menu_button=MenuButtonCommands())
        LOG.info("Telegram command menu registered (%d commands)", len(BOT_MENU_COMMANDS))
    except Exception:
        LOG.exception("Unable to register the Telegram command menu")

# Google Drive configuration
GDRIVE_SA_JSON = getattr(config, "GDRIVE_SA_JSON", os.environ.get("GDRIVE_SA_JSON", ""))
GDRIVE_FOLDER_ID = getattr(config, "GDRIVE_FOLDER_ID", os.environ.get("GDRIVE_FOLDER_ID", ""))
GOOGLE_CLIENT_ID = getattr(config, "GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID", ""))
GOOGLE_CLIENT_SECRET = getattr(config, "GOOGLE_CLIENT_SECRET", "")

user_status = {}
user_tasks = {}
user_ffmpeg_pids = {}
progress_tasks = {}
cancelled_users = set()  # Track cancelled users

# Task-based processing state used by inline progress/cancel buttons.
processing_tasks = {}  # {task_id: {...}}

# Active recording task IDs grouped by the recording actor.
# For normal users this is the Telegram user ID; for anonymous admins it is
# the allowed group chat ID because Telegram does not expose from_user.
active_recordings_by_actor = {}

# Maximum simultaneous recordings allowed for one user/anonymous group actor.
MAX_RECORDINGS_PER_USER = 10

# Keep cancelled/partial server copies for 3 hours after Telegram upload.
PARTIAL_SERVER_COPY_TTL_SECONDS = 3 * 60 * 60
_partial_cleanup_tasks = set()

def _active_recording_count(actor_id: int) -> int:
    """Count running/processing recordings for one actor."""
    try:
        return len(active_recordings_by_actor.get(int(actor_id), set()))
    except (TypeError, ValueError):
        return 0

# /rec is available only in this explicitly allowed group.
ALLOWED_REC_GROUP_IDS = {getattr(config, "GROUP_CHAT_ID", GROUP_CHAT_ID)}
# Hard-coded bot owner ID. This remains independent of config.AUTH_USERS.
OWNER_ID = getattr(config, "OWNER_ID", 5856009289)
OWNER_IDS = {OWNER_ID}

# Runtime settings; changes are persisted immediately (no bot restart required).
BOT_SETTINGS_FILE = join(getattr(config, "DOWNLOAD_DIRECTORY", "."), "bot_settings.json")
DEFAULT_BOT_SETTINGS = {"audio_track": True, "quality": True, "no_need_rec": False, "token": True, "premium_access": True, "admin_access": True, "owner_bypass": True}
bot_settings = DEFAULT_BOT_SETTINGS.copy()

def _load_bot_settings():
    global bot_settings
    try:
        if os.path.exists(BOT_SETTINGS_FILE):
            with open(BOT_SETTINGS_FILE, "r", encoding="utf-8") as f: data=json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_BOT_SETTINGS:
                    if k in data: bot_settings[k]=bool(data[k])
    except Exception as e: LOG.warning("Bot settings load failed: %s", e)

def _save_bot_settings():
    try:
        os.makedirs(os.path.dirname(BOT_SETTINGS_FILE) or ".", exist_ok=True)
        tmp=BOT_SETTINGS_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(bot_settings,f,indent=2)
        os.replace(tmp,BOT_SETTINGS_FILE)
    except Exception as e: LOG.warning("Bot settings save failed: %s", e)

def _settings_text():
    st=lambda k: "ON" if bot_settings.get(k,False) else "OFF"
    return ("⚙️ **Bot Settings**\n\n🎬 **Recording Settings**\n\n"
            f"Audio Track — **{st('audio_track')}**\n"
            f"Quality — **{st('quality')}**\n"
            f"No need /rec — **{st('no_need_rec')}**\n\n"
            "🎟️ **Access Settings**\n\n"
            f"Token — **{st('token')}**\n"
            f"Premium Access — **{st('premium_access')}**\n"
            f"Admin Access — **{st('admin_access')}**\n"
            f"Owner Bypass — **{st('owner_bypass')}**\n\n📺 **Channel Settings**")

def _settings_keyboard():
    def row(k): return [InlineKeyboardButton("✅ ON" if bot_settings[k] else "ON", callback_data=f"setting:{k}:1"), InlineKeyboardButton("❌ OFF" if not bot_settings[k] else "OFF", callback_data=f"setting:{k}:0")]
    return InlineKeyboardMarkup([row("audio_track"),row("quality"),row("no_need_rec"),row("token"),row("premium_access"),row("admin_access"),row("owner_bypass"),[InlineKeyboardButton("✏️ Channel Link Edit",callback_data="setting:channel_edit:1")],[InlineKeyboardButton("➕ Channel Link New",callback_data="setting:channel_new:1")]])

_load_bot_settings()

import urllib.request
import urllib.parse
import urllib.error

# Google Drive helpers (merged from gdrive.py)
# ===========================================================================

import urllib.request
import urllib.parse
import urllib.error


_SCOPES          = ["https://www.googleapis.com/auth/drive.file"]
_DEVICE_AUTH_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL       = "https://oauth2.googleapis.com/token"
_GRANT_TYPE_DEV  = "urn:ietf:params:oauth:grant-type:device_code"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _oauth_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _sa_enabled() -> bool:
    return bool(GDRIVE_SA_JSON and GDRIVE_FOLDER_ID)


def _is_enabled() -> bool:
    return _sa_enabled() or _oauth_enabled()


# ---------------------------------------------------------------------------
# Per-user token storage
# ---------------------------------------------------------------------------

def _token_dir() -> str:
    d = os.path.join(DATA_DIRECTORY, "gdrive_tokens")
    os.makedirs(d, exist_ok=True)
    return d


def _token_path(user_id: int) -> str:
    return os.path.join(_token_dir(), f"{user_id}.json")


def is_user_connected(user_id: int) -> bool:
    return os.path.exists(_token_path(user_id))


def disconnect_user(user_id: int) -> bool:
    p = _token_path(user_id)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def _save_token(user_id: int, token_data: dict):
    token_data["saved_at"] = time.time()
    with open(_token_path(user_id), "w") as f:
        json.dump(token_data, f)


def _load_token(user_id: int) -> dict:
    with open(_token_path(user_id)) as f:
        return json.load(f)


def get_sa_email() -> str:
    try:
        return json.loads(GDRIVE_SA_JSON).get("client_email", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# OAuth2 device flow
# ---------------------------------------------------------------------------

def start_device_flow_sync() -> dict:
    """Start OAuth2 device flow. Returns {device_code, user_code, verification_url, interval, expires_in}."""
    data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "scope":     " ".join(_SCOPES),
    }).encode()
    req = urllib.request.Request(
        _DEVICE_AUTH_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent":   "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _poll_token_sync(device_code: str) -> Optional[dict]:
    """Poll for token. Returns token dict if authorized, None if still pending."""
    data = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "device_code":   device_code,
        "grant_type":    _GRANT_TYPE_DEV,
    }).encode()
    req = urllib.request.Request(
        _TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent":   "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            return resp if "access_token" in resp else None
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        err  = body.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return None
        raise Exception(f"OAuth2 error: {err} — {body.get('error_description', '')}")


async def poll_and_save_token(client, user_id: int, device_code: str,
                               interval: int, expires_in: int):
    """Background task: polls until user authorizes or code expires."""
    deadline = time.time() + expires_in
    while time.time() < deadline:
        await asyncio.sleep(max(interval, 5))
        try:
            tok = await asyncio.to_thread(_poll_token_sync, device_code)
        except Exception as e:
            LOG.error(f"GDrive OAuth poll error uid={user_id}: {e}")
            try:
                await client.send_message(user_id, f"❌ Google Drive auth failed: `{e}`")
            except Exception:
                pass
            return
        if tok:
            _save_token(user_id, tok)
            LOG.info(f"GDrive OAuth token saved for uid={user_id}")
            try:
                await client.send_message(
                    user_id,
                    "✅ **Google Drive Connected!**\n\n"
                    "Ab aapki recordings automatically **aapki Google Drive** par upload hongi.\n\n"
                    "Disconnect karne ke liye: /googledrive disconnect\n"
                    "Status dekhne ke liye: /googledrive status",
                )
            except Exception:
                pass
            return
    try:
        await client.send_message(
            user_id,
            "⏰ **Google Drive auth timeout.**\n\n"
            "Code expire ho gaya. Fir se try karein: /googledrive"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Drive service builders
# ---------------------------------------------------------------------------

def _build_user_service(user_id: int):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    tok   = _load_token(user_id)
    creds = Credentials(
        token         = tok.get("access_token"),
        refresh_token = tok.get("refresh_token"),
        token_uri     = _TOKEN_URL,
        client_id     = GOOGLE_CLIENT_ID,
        client_secret = GOOGLE_CLIENT_SECRET,
        scopes        = _SCOPES,
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(GoogleRequest())
        _save_token(user_id, {
            "access_token":  creds.token,
            "refresh_token": creds.refresh_token,
        })
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _build_sa_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sa_info = json.loads(GDRIVE_SA_JSON)
    creds   = service_account.Credentials.from_service_account_info(sa_info, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def _upload_task_id(file_path: str) -> str:
    """Return a stable short ID for one Drive upload task."""
    return secrets.token_hex(6)


def _fmt_upload_progress_box(title: str, current: float, total: float,
                             speed: float, remaining: float, task_id: str,
                             compact: bool = False) -> str:
    total = max(float(total or 1), 1.0)
    current = max(0.0, min(float(current or 0), total))
    pct = current * 100.0 / total
    filled = int(10 * pct / 100)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    mb_current = current / (1024 * 1024)
    mb_total = total / (1024 * 1024)
    mb_speed = float(speed or 0) / (1024 * 1024)
    eta = "--" if not remaining or remaining < 0 else f"{int(remaining)}s"
    if compact:
        return (
            f"☁️ **{title}**\n\n"
            f"{bar} **{pct:.2f}%**\n"
            f"{mb_current:.0f}MB / {mb_total:.0f}MB"
        )
    return (
        f"☁️ **{title}**\n\n"
        f"{bar} **{pct:.2f}%**\n"
        f"{mb_current:.2f} MB / {mb_total:.2f} MB\n\n"
        f"⚡ Speed: **{mb_speed:.2f} MB/s**\n"
        f"⏳ Time Left: **{eta}**"
    )


def _drive_task_keyboard(task_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Progress", callback_data=f"drive_progress:{task_id}"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"drive_cancel:{task_id}")]
    ])


def _drive_done_keyboard(preview_link: str, download_link: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Preview File", url=preview_link),
         InlineKeyboardButton("⬇️ Download File", url=download_link)]
    ])


def _upload_sync(file_path: str, filename: str, folder_id: Optional[str],
                 user_id: Optional[int], task_id: Optional[str] = None) -> dict:
    """Upload to Drive using resumable chunks and expose live progress state."""
    from googleapiclient.http import MediaFileUpload

    if task_id:
        state = drive_upload_tasks.setdefault(task_id, {})
        state.update({"status": "Uploading", "current": 0, "total": os.path.getsize(file_path),
                      "speed": 0, "remaining": 0, "cancelled": False})
    start_ts = time.time()

    if user_id and is_user_connected(user_id):
        service = _build_user_service(user_id)
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
    else:
        service = _build_sa_service()
        meta = {"name": filename, "parents": [folder_id or GDRIVE_FOLDER_ID]}

    mime_type = "video/x-matroska" if filename.lower().endswith(".mkv") else "video/mp4"
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024)
    request = service.files().create(body=meta, media_body=media, fields="id,webViewLink")

    response = None
    while response is None:
        if task_id and drive_upload_tasks.get(task_id, {}).get("cancelled"):
            raise InterruptedError("Upload cancelled by user")
        status, response = request.next_chunk()
        if status and task_id:
            current = float(status.resumable_progress or 0)
            total = float(os.path.getsize(file_path) or 1)
            elapsed = max(time.time() - start_ts, 0.001)
            speed = current / elapsed
            remaining = (total - current) / speed if speed > 0 else 0
            drive_upload_tasks[task_id].update({
                "current": current, "total": total, "speed": speed,
                "remaining": remaining, "status": "Uploading"
            })

    file_id = response.get("id")
    link = response.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    preview_link = link
    download_link = f"https://drive.google.com/uc?export=download&id={file_id}"
    if task_id:
        elapsed = max(time.time() - start_ts, 0.0)
        drive_upload_tasks[task_id].update({
            "status": "Completed", "elapsed": elapsed, "current": os.path.getsize(file_path),
            "total": os.path.getsize(file_path), "speed": 0, "remaining": 0,
            "file_id": file_id, "link": link,
            "preview_link": preview_link, "download_link": download_link,
        })
    LOG.info(f"GDrive upload done: {filename} → {link}")
    return {"id": file_id, "link": link, "preview_link": preview_link, "download_link": download_link}


drive_upload_tasks = {}
gdrive_auth_tasks = {}


async def upload_and_notify(client, chat_id: int, file_path: str, filename: str, status_msg=None):
    """Upload to Drive with live progress/cancel controls and completion buttons."""
    user_connected = is_user_connected(chat_id)
    if not user_connected and not _sa_enabled():
        text = (
            "☁️ **Google Drive Not Connected**\n\n"
            "Pehle `/googledrive` se Google Drive connect karein, "
            "phir upload dobara start karein."
        )
        if status_msg:
            try:
                await status_msg.edit_text(text, reply_markup=None)
            except Exception:
                pass
        else:
            await client.send_message(chat_id, text)
        return False

    task_id = _upload_task_id(file_path)
    total = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    drive_upload_tasks[task_id] = {
        "status": "Uploading", "current": 0, "total": total or 1,
        "speed": 0, "remaining": 0, "cancelled": False,
        "filename": filename, "chat_id": chat_id, "start_ts": time.time(),
    }
    target = status_msg
    try:
        initial = _fmt_upload_progress_box("Google Drive Uploader", 0, total or 1, 0, 0, task_id)
        if target is None:
            target = await client.send_message(chat_id, initial, reply_markup=_drive_task_keyboard(task_id))
        else:
            try:
                await target.edit_text(initial, reply_markup=_drive_task_keyboard(task_id))
            except Exception:
                pass

        folder_id = None if user_connected else GDRIVE_FOLDER_ID
        upload_future = asyncio.create_task(asyncio.to_thread(
            _upload_sync, file_path, filename, folder_id, chat_id, task_id
        ))

        while not upload_future.done():
            state = drive_upload_tasks.get(task_id, {})
            text = _fmt_upload_progress_box(
                "Google Drive Uploader", state.get("current", 0),
                state.get("total", total or 1), state.get("speed", 0),
                state.get("remaining", 0), task_id
            )
            try:
                await target.edit_text(text, reply_markup=_drive_task_keyboard(task_id))
            except Exception:
                pass
            await asyncio.sleep(1)

        result = await upload_future
        state = drive_upload_tasks.get(task_id, {})
        elapsed = state.get("elapsed", 0)
        final = (
            f"✅ **Upload Complete**\n\n"
            f"📄 **File:** `{filename}`\n"
            f"📦 **Size:** {total / (1024 * 1024):.0f}MB\n"
            f"⏱️ **Process completed:** {int(elapsed // 60):02d}:{int(elapsed % 60):02d}\n\n"
            f"☁️ Google Drive upload successful."
        )
        try:
            await target.edit_text(
                final,
                disable_web_page_preview=True,
                reply_markup=_drive_done_keyboard(result["preview_link"], result["download_link"]),
            )
        except Exception:
            await client.send_message(
                chat_id, final, disable_web_page_preview=True,
                reply_markup=_drive_done_keyboard(result["preview_link"], result["download_link"]),
            )
    except InterruptedError:
        state = drive_upload_tasks.setdefault(task_id, {})
        state["status"] = "Cancelled"
        state["elapsed"] = max(time.time() - state.get("start_ts", time.time()), 0.0)
        try:
            await target.edit_text(
                f"❌ **Upload Cancelled**\n\n📄 **File:** `{filename}`\n"
                "No completed Google Drive file was created.", reply_markup=None
            )
        except Exception:
            pass
    except Exception as e:
        drive_upload_tasks.setdefault(task_id, {})["status"] = "Failed"
        LOG.error(f"GDrive upload failed for {filename}: {e}")
        try:
            await target.edit_text(f"⚠️ **Google Drive upload failed**\n\n`{str(e)[:1500]}`", reply_markup=None)
        except Exception:
            pass
    finally:
        # Keep a short-lived state so a late button press gets a useful answer.
        asyncio.create_task(_cleanup_drive_task(task_id))


def _drive_setup_text() -> str:
    return (
        "☁️ **Google Drive is not configured**\n\n"
        "Secure setup ke liye Replit Google Drive connection use karein, "
        "ya workspace Secrets mein ye values configure karein:\n"
        "• `GOOGLE_CLIENT_ID`\n"
        "• `GOOGLE_CLIENT_SECRET`\n\n"
        "Service-account mode ke liye:\n"
        "• `GDRIVE_SA_JSON`\n"
        "• `GDRIVE_FOLDER_ID`\n\n"
        "Credentials chat mein paste na karein."
    )


@app.on_message(filters.command(["gdrive", "googledrive"]))
async def googledrive_command(client, message: Message):
    """Connect, inspect, or disconnect the user's Google Drive account."""
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return await message.reply_text("❌ Google Drive commands require a personal Telegram account.")

    args = [str(value).strip().casefold() for value in getattr(message, "command", [])[1:]]
    action = args[0] if args else "connect"

    if action in {"disconnect", "logout"}:
        removed = disconnect_user(user_id)
        return await message.reply_text(
            "✅ Google Drive disconnected." if removed
            else "ℹ️ No Google Drive account was connected."
        )

    if action == "status":
        if is_user_connected(user_id):
            return await message.reply_text(
                "✅ **Google Drive Connected**\n\n"
                "Aapki uploads connected Google Drive account par jaayengi."
            )
        if _sa_enabled():
            return await message.reply_text(
                "✅ **Google Drive Service Account Ready**\n\n"
                "Uploads configured Drive folder mein jaayengi."
            )
        if not _oauth_enabled():
            return await message.reply_text(_drive_setup_text())
        return await message.reply_text(
            "⚠️ **Google Drive Not Connected**\n\n"
            "Connect karne ke liye `/googledrive` bhejein."
        )

    if is_user_connected(user_id):
        return await message.reply_text(
            "✅ Google Drive already connected.\n\n"
            "Status: `/googledrive status`\n"
            "Disconnect: `/googledrive disconnect`"
        )

    if _sa_enabled() and not _oauth_enabled():
        return await message.reply_text(
            "✅ Google Drive service account configured hai.\n"
            "Aapki uploads configured folder mein jaayengi."
        )

    if not _oauth_enabled():
        return await message.reply_text(_drive_setup_text())

    existing_task = gdrive_auth_tasks.get(user_id)
    if existing_task and not existing_task.done():
        return await message.reply_text(
            "⏳ Google Drive authorization already pending hai. "
            "Pehle bheja gaya code complete karein."
        )

    try:
        flow = await asyncio.to_thread(start_device_flow_sync)
    except Exception as exc:
        LOG.error("Google Drive device flow could not start for uid=%s: %s", user_id, exc)
        return await message.reply_text(
            "❌ Google Drive authorization start nahi ho saki.\n\n"
            "Client ID/Secret aur Google Drive API configuration check karein."
        )

    verification_url = (
        flow.get("verification_url")
        or flow.get("verification_url_complete")
        or "https://www.google.com/device"
    )
    user_code = flow.get("user_code", "")
    expires_in = int(flow.get("expires_in", 900) or 900)
    interval = int(flow.get("interval", 5) or 5)
    gdrive_auth_tasks[user_id] = asyncio.create_task(
        poll_and_save_token(client, user_id, flow["device_code"], interval, expires_in)
    )

    return await message.reply_text(
        "☁️ **Google Drive Connect**\n\n"
        "1. Neeche diye link ko open karein\n"
        "2. Google account se sign in karein\n"
        f"3. Code enter karein: `{user_code}`\n\n"
        f"🔗 {verification_url}\n\n"
        f"Code {expires_in // 60} minutes mein expire hoga."
    )


async def _cleanup_drive_task(task_id: str):
    await asyncio.sleep(300)
    drive_upload_tasks.pop(task_id, None)



async def _safe_callback_answer(query, text=None, **kwargs):
    """Answer a Telegram callback without crashing when its short-lived query ID expired."""
    try:
        if text is None:
            return await query.answer(**kwargs)
        return await query.answer(text, **kwargs)
    except Exception as exc:
        # QUERY_ID_INVALID means Telegram can no longer acknowledge this callback.
        # The callback handler may still continue (for example, to edit/send a message).
        if 'QUERY_ID_INVALID' in str(exc).upper():
            return None
        raise


@app.on_callback_query(filters.regex(r"^drive_progress:"))
async def drive_progress_callback(client, query):
    task_id = query.data.split(":", 1)[1]
    state = drive_upload_tasks.get(task_id)
    if not state:
        return await _safe_callback_answer(query, "❌ Upload is no longer active.", show_alert=True, cache_time=0)
    if query.from_user.id != state.get("chat_id") and not _is_owner(query.from_user.id):
        return await _safe_callback_answer(query, "❌ You do not have permission.", show_alert=True, cache_time=0)
    total = state.get("total", 1) or 1
    current = state.get("current", 0)
    pct = current * 100 / total
    text = _fmt_upload_progress_box(
        "Google Drive Uploader", current, total,
        state.get("speed", 0), state.get("remaining", 0), task_id
    ) + f"\n\n📊 **Status:** {state.get('status', 'Uploading')}"
    await _safe_callback_answer(query, f"{pct:.2f}%", show_alert=True, cache_time=0)
    try:
        await query.message.edit_text(text, reply_markup=_drive_task_keyboard(task_id))
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^drive_cancel:"))
async def drive_cancel_callback(client, query):
    task_id = query.data.split(":", 1)[1]
    state = drive_upload_tasks.get(task_id)
    if not state:
        return await _safe_callback_answer(query, "❌ Upload is no longer active.", show_alert=True, cache_time=0)
    if query.from_user.id != state.get("chat_id") and not _is_owner(query.from_user.id):
        return await _safe_callback_answer(query, "❌ You do not have permission.", show_alert=True, cache_time=0)
    if state.get("status") != "Uploading":
        return await _safe_callback_answer(query, f"ℹ️ Upload status: {state.get('status')}", show_alert=True, cache_time=0)
    state["cancelled"] = True
    await _safe_callback_answer(query, "❌ Cancelling upload...", show_alert=True, cache_time=0)


async def _run_upload_destination(client, user_id: int, dest: str, status_msg, out_path: str,
                                   caption: str, duration, thumb_path=None, save_dir=None,
                                   was_cancelled: bool = False):
    """
    Execute the chosen upload destination(s) with matching progress/completion UI.
    dest: 'tg' (Telegram only) / 'gd' (Drive only) / 'both' (Drive then Telegram).
    """
    filename = os.path.basename(out_path)

    if dest == "tg":
        upload_start = time.time()
        await split_and_send_video(
            status_msg, out_path, caption, int(duration),
            thumb_path=thumb_path, status_msg=status_msg,
            progress=progress_for_pyrogram,
            progress_args=(status_msg, upload_start, status_msg, save_dir, was_cancelled),
            _uid=user_id, _chat_id=status_msg.chat.id,
        )
    elif dest == "gd":
        await upload_and_notify(client, status_msg.chat.id, out_path, filename, status_msg=status_msg)
    elif dest == "both":
        # Sequential terse log: Drive first, then Telegram, growing into one final message.
        user_connected = is_user_connected(status_msg.chat.id)
        drive_available = user_connected or _sa_enabled()

        log_lines = ["🚀 Uploading to Drive..."]
        try:
            await status_msg.edit_text("\n".join(log_lines))
        except Exception:
            pass

        if drive_available:
            try:
                folder_id = None if user_connected else GDRIVE_FOLDER_ID
                await asyncio.to_thread(
                    _upload_sync, out_path, filename, folder_id, status_msg.chat.id
                )
                log_lines.append("✅ Drive Upload Complete")
            except Exception as e:
                LOG.error(f"GDrive upload failed for {filename}: {e}")
                log_lines.append("⚠️ Drive Upload Failed")
        else:
            log_lines.append("⚠️ Drive Upload Skipped (not connected)")

        log_lines.append("")
        log_lines.append("🚀 Uploading to Telegram...")
        try:
            await status_msg.edit_text("\n".join(log_lines))
        except Exception:
            pass

        upload_start = time.time()
        await split_and_send_video(
            status_msg, out_path, caption, int(duration),
            thumb_path=thumb_path, status_msg=status_msg,
            progress=progress_for_pyrogram,
            progress_args=(status_msg, upload_start, status_msg, save_dir, was_cancelled),
            _uid=user_id, _chat_id=status_msg.chat.id,
        )

        log_lines.append("✅ Telegram Upload Complete")
        log_lines.append("")
        log_lines.append("🎉 Job Finished")
        try:
            await status_msg.edit_text("\n".join(log_lines))
        except Exception:
            pass


# ===========================================================================
# Quota & limit system (merged from limit_system.py)
# ===========================================================================

"""
Quota & daily verification limit system.

Data file: <DATA_DIRECTORY>/user_limits.json
Schema per user:
  {
    "rec_limit":    int,   -- current recording credits
    "verify_left":  int,   -- verifications remaining this cycle (max 10)
    "verify_done":  int,   -- verifications completed this cycle
    "is_lucky":     bool,  -- lucky user flag (set once at creation, ~20% chance)
    "last_refresh": float, -- unix timestamp of last quota auto-reset
    "first_time":   bool,  -- True until user first interacts
  }
"""

import json
import os
import random
import time


# ── Tunable constants ────────────────────────────────────────────────────────

DEFAULT_REC_LIMIT   = 1        # credits a brand-new user starts with
DEFAULT_VERIFY_LEFT = 10       # verifications allowed per 12-hour cycle
LUCKY_RATIO         = 5        # 1 in 5 users is "lucky" (~20%)
REFRESH_SECONDS     = 12 * 3600

# Reward table — indexed by verify_done count (clamped to last entry)
# result_rec : absolute value to set rec_limit to after this verify
VERIFY_STEPS = [
    {"result_rec": 4, "msg": "🎉 Pehli baar verify! Aapko **Rec 4** mil gaye!"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 4, "msg": "🌟 Lucky Step! Aapki limit: **Rec 4**"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 4, "msg": "🌟 Lucky Step! Aapki limit: **Rec 4**"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 3, "msg": "✅ Verify bonus! Aapki limit: **Rec 3**"},
    {"result_rec": 3, "msg": "✅ Last verify! Aapki limit: **Rec 3**"},
]


# ── Internal helpers ─────────────────────────────────────────────────────────

def _limit_file() -> str:
    return os.path.join(DATA_DIRECTORY, "user_limits.json")


def _load() -> dict:
    try:
        with open(_limit_file(), "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(DATA_DIRECTORY, exist_ok=True)
    with open(_limit_file(), "w") as f:
        json.dump(data, f, indent=2)


def _new_record() -> dict:
    return {
        "rec_limit":    DEFAULT_REC_LIMIT,
        "verify_left":  DEFAULT_VERIFY_LEFT,
        "verify_done":  0,
        "is_lucky":     random.random() < (1.0 / LUCKY_RATIO),
        "last_refresh": time.time(),
        "joined_at":    time.time(),
        "first_time":   True,
    }


def _maybe_refresh(user: dict) -> dict:
    """Auto-reset if 12 hours have passed since last refresh."""
    if time.time() - user.get("last_refresh", 0) >= REFRESH_SECONDS:
        user["rec_limit"]    = 3 if user.get("is_lucky") else 0
        user["verify_left"]  = DEFAULT_VERIFY_LEFT
        user["verify_done"]  = 0
        user["last_refresh"] = time.time()
    return user


# ── Public API ───────────────────────────────────────────────────────────────

def get_user(user_id: int) -> dict:
    """Return the user's quota record, creating and auto-refreshing as needed."""
    data = _load()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = _new_record()
        _save(data)
        return dict(data[uid])
    data[uid] = _maybe_refresh(data[uid])
    _save(data)
    return dict(data[uid])


def use_rec(user_id: int) -> tuple:
    """
    Consume 1 recording credit.
    Returns (True, info_msg) on success or (False, error_msg) when out of credits.
    """
    data = _load()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = _new_record()
    user = _maybe_refresh(data[uid])
    if user["rec_limit"] <= 0:
        data[uid] = user
        _save(data)
        return False, (
            "❌ **Rec limit khatam ho gayi!**\n\n"
            "Use /verify to get more recording credits.\n"
            "Use /limit to check your current status."
        )
    user["rec_limit"] -= 1
    user["first_time"]  = False
    data[uid] = user
    _save(data)
    return True, f"✅ 1 Rec used. Remaining: **Rec {user['rec_limit']}**"


def apply_verify_bonus(user_id: int) -> tuple:
    """
    Grant recording credits for a completed ad-click verification.
    Returns (True, reward_msg) or (False, error_msg).
    """
    data = _load()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = _new_record()
    user = _maybe_refresh(data[uid])

    if user["verify_left"] <= 0:
        data[uid] = user
        _save(data)
        elapsed     = time.time() - user.get("last_refresh", time.time())
        remaining_s = max(REFRESH_SECONDS - elapsed, 0)
        rh = int(remaining_s // 3600)
        rm = int((remaining_s % 3600) // 60)
        return False, (
            f"🚫 **Aaj ke liye sab verifications lock ho gaye!**\n"
            f"⏱️ Refresh in: **{rh}h {rm}m**"
        )

    step_idx          = min(user["verify_done"], len(VERIFY_STEPS) - 1)
    step              = VERIFY_STEPS[step_idx]
    bonus             = 1 if user.get("is_lucky") else 0
    user["rec_limit"] = step["result_rec"] + bonus
    user["verify_left"] = max(0, user["verify_left"] - 1)
    user["verify_done"] += 1
    user["first_time"]  = False
    data[uid] = user
    _save(data)

    msg = step["msg"]
    if bonus:
        msg += "\n⭐ **Lucky Bonus:** +1 extra Rec!"
    msg += (
        f"\n\n🎯 **Total: Rec {user['rec_limit']}** "
        f"| Verify left: **{user['verify_left']}**"
    )
    return True, msg


def format_limit_message(user_id: int) -> str:
    """Return the full /limit status block for this user."""
    user      = get_user(user_id)
    rec       = user["rec_limit"]
    v_left    = user["verify_left"]
    v_done    = user["verify_done"]
    is_lucky  = user.get("is_lucky", False)
    is_first  = user.get("first_time", False)
    is_locked = v_left <= 0

    elapsed     = time.time() - user.get("last_refresh", time.time())
    remaining_s = max(REFRESH_SECONDS - elapsed, 0)
    rh = int(remaining_s // 3600)
    rm = int((remaining_s % 3600) // 60)
    refresh_str = f"{rh}h {rm}m" if remaining_s > 0 else "Abhi refresh hoga! 🔄"

    if is_locked:
        verify_line = "⚠️ **VERIFY NO USE** — Aaj ki limit lock hai!"
    elif is_first:
        verify_line = "👉 Pehli baar verify karne par aapka quota unlock ho jayega!"
    else:
        verify_line = "👉 Verify karein aur aur Rec paaein!"

    lucky_line = "⭐ **Lucky User:** Refresh ke baad Rec 3 milega!\n" if is_lucky else ""

    step_labels = [
        ("1️⃣", "First Use  ➔ Verify 2", "(Aapko milenge +Rec 4)"),
        ("2️⃣", "Second Use ➔ Verify 1", "(Aapki limit ghatkar hogi: Rec 3)"),
        ("3️⃣", "Dobara Use ➔ Verify 1", "(Aapki limit aur ghatkar hogi: Rec 3)"),
        ("4️⃣", "Third Use  ➔ Verify 10", "(Lock 🚫 Today Limit Expired)"),
    ]

    flow_lines = []
    for i, (num, action, reward) in enumerate(step_labels):
        if i < v_done:
            prefix = "✅"
        elif i == v_done and not is_locked:
            prefix = "▶️"
        else:
            prefix = num
        flow_lines.append(f"  {prefix} {action} {reward}")

    return (
        "📊 **BOT VERIFICATION STATUS** 📊\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Your Current Limit:** Rec {rec}\n"
        "Aap iska use kar sakte hain:\n"
        "👉 `/rec LINK 00:00:30 Filename`\n"
        f"🆓 **Remaining Verify Limit:** {v_left} Verification\n"
        f"{verify_line}\n"
        f"{lucky_line}"
        "🔢 **Countdown Flow & Rewards:**\n"
        + "\n".join(flow_lines) + "\n\n"
        "🌅 **SURPRISE GIFT (Lucky User):**\n"
        "Every 20% users mein se 1 lucky user ko extra badal-badal kar rewards milenge!\n\n"
        f"⏱️ **Daily Refresh Timer:** {refresh_str}\n"
        "🔄 Har 12 ghante me system fresh ho jayega. "
        "Normal users ka Rec 0 hoga, par Lucky User ka balance Rec 3 rahega!"
    )


# =============================================================================
# Command handlers (merged from command.py)
# =============================================================================

# ---------------------------------------------------------------------------
# Module-level state for ad-click verify flow
# ---------------------------------------------------------------------------

# {user_id: {"short_url": str, "expires": float}}
pending_verify: dict = {}

_VERIFY_LINK_TTL = 300  # seconds before link expires and a new one is generated


# ---------------------------------------------------------------------------
# shortxlinks.in URL shortener (sync wrapped in asyncio.to_thread)
# ---------------------------------------------------------------------------

def _shrink2_sync(api_url: str):
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("status") == "success" and data.get("shortenedUrl"):
                return data["shortenedUrl"]
    except Exception:
        pass
    return None


async def _shrink2(long_url: str) -> str:
    """Shorten via shortxlinks.in. Returns the short URL, or the original on failure."""
    key     = SHRINKME_API_KEY
    encoded = urllib.parse.quote(long_url, safe=":/?&=%")
    api_url = f"https://shortxlinks.in/api?api={key}&url={encoded}"
    try:
        short = await asyncio.to_thread(_shrink2_sync, api_url)
        if short:
            return short
    except Exception:
        pass
    return long_url  # fallback: show original link


# ---------------------------------------------------------------------------
# Shared helpers for text
# ---------------------------------------------------------------------------

HELP_TEXT = f"""
📚 {BRAND_TITLE} — Help & Commands

🎥 Recording
• /rec — HLS/M3U8 recording
• /drec — Direct recording
• /schedule — Schedule recording
• /Subedule — Schedule recording alias
• /cancel — Stop recording
• /status — Recording status

📥 Downloads
• /download [filename] — Download a recorded file

📺 Supported OTT
• Hotstar
• JioCinema
• ZEE5
• SonyLIV

☁️ Google Drive
• /gdrive or /googledrive — Connect your Google Drive
• /drivelogout — Disconnect your Google Drive account

🍪 OTT Login / Cookies
• /set_cookies — Upload cookies.txt (Netscape format)
• /cookies_status — Show stored cookies
• /del_cookies — Delete stored cookies

ℹ️ Other
• /start — Start the bot
• /help — Show this help

"""


_OWNER_HELP_TEXT = """
━━━━━━━━━━━━━━━━━━━━━━━
👑 **Owner Commands**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 Hidden from Free / Verify / Premium / Admin Users

**Branding**

• `/updatewatermark`
Change default watermark text.

• `/audionameupdate`
Change embedded audio track brand name.

**User Management**

• `/stats`
Bot statistics + new users last 3 days.

• `/broadcast`
Send message to all users.

• `/approve [days]`
Approve a user manually.

• `/revoke`
Revoke user's access.

• `/pending`
Show pending verification requests.

**Admin Management**

• `/admin_add`
Add admin.

• `/admin_delete`
Remove admin.

• `/admin_list`
List all admins.

**Premium Management**

• `/premium_add`
Add premium plan.

• `/premium_expire`
Remove premium plan.

• `/premium_list`
List all premium users.

"""


VERIFICATION_HOURS = 6
VERIFICATION_SECONDS = VERIFICATION_HOURS * 3600

TOKEN_STORE_FILE = join(
    getattr(config, "DOWNLOAD_DIRECTORY", "."),
    "verification_tokens.json"
)

verification_tokens = {}
verification_access = {}


def _save_verification_store():
    try:
        Path(TOKEN_STORE_FILE).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        Path(TOKEN_STORE_FILE).write_text(
            json.dumps(
                {
                    "tokens": verification_tokens,
                    "access": verification_access,
                },
                indent=2
            ),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"Verification store save error: {e}")


def _load_verification_store():
    global verification_tokens
    global verification_access

    try:
        path = Path(TOKEN_STORE_FILE)

        if not path.exists():
            return

        data = json.loads(
            path.read_text(encoding="utf-8")
        )

        verification_tokens = data.get("tokens", {})
        verification_access = data.get("access", {})

    except Exception as e:
        print(f"Verification store load error: {e}")
        verification_tokens = {}
        verification_access = {}


def _new_verification_token(user_id: int) -> str:
    uid = str(user_id)

    # One active token per user
    for old_token, record in list(
        verification_tokens.items()
    ):
        if str(record.get("user_id")) == uid:
            verification_tokens.pop(
                old_token,
                None
            )

    token = secrets.token_urlsafe(24)
    now = time.time()

    verification_tokens[token] = {
        "user_id": uid,
        "created": now,
        "expires": now + VERIFICATION_SECONDS,
    }

    _save_verification_store()
    return token


def _verify_token(token: str, user_id: int) -> bool:
    record = verification_tokens.get(token)

    if not record:
        return False

    if float(record.get("expires", 0)) <= time.time():
        verification_tokens.pop(token, None)
        _save_verification_store()
        return False

    # Token is permanently bound to Telegram user ID
    if str(record.get("user_id")) != str(user_id):
        return False

    verification_access[str(user_id)] = (
        time.time() + VERIFICATION_SECONDS
    )

    # One-time-use token
    verification_tokens.pop(token, None)
    _save_verification_store()

    return True


def _has_valid_access(user_id: int) -> bool:
    if _is_owner(user_id):
        return True

    uid = str(user_id)
    expires = float(
        verification_access.get(uid, 0) or 0
    )

    if expires <= time.time():
        verification_access.pop(uid, None)
        _save_verification_store()
        return False

    return True


_load_verification_store()


def _make_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help",       callback_data="show_help"),
         InlineKeyboardButton("💎 Plans",      callback_data="show_plans")],
        [InlineKeyboardButton("📡 Channels",   callback_data="show_channels"),
         InlineKeyboardButton("✅ Get Verified", callback_data="show_verify")],
    ])


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@app.on_message(filters.command("start"))
async def start(client, message):
    payload = message.command[1] if len(message.command) > 1 else ""
    uid = message.from_user.id

    # ── New Token Verification ──
    if payload.startswith("verify_"):
        body = payload[7:]

        try:
            token_value, claimed_uid_text = body.rsplit("_", 1)
            claimed_uid = int(claimed_uid_text)
        except (ValueError, TypeError):
            return await message.reply_text(
                "❌ **Invalid Verification Link**"
            )

        # User ID in link MUST match current Telegram User ID
        if uid != claimed_uid:
            return await message.reply_text(
                "❌ **Verification Failed**\n\n"
                "👤 Telegram User ID does not match this token."
            )

        wait_msg = await message.reply_text(
            "⏳ **Please wait...**\n"
            "🔐 **Verifying your token...**\n"
            "👤 **Checking Telegram User ID...**"
        )

        if _verify_token(token_value, uid):
            now = time.time()
            expires_at = verification_access.get(
                str(uid),
                now + VERIFICATION_SECONDS
            )

            expire_text = time.strftime(
                "%I:%M %p",
                time.localtime(expires_at)
            ).lstrip("0")

            text = (
                "✅ **Token Verified**\n\n"
                "👤 **Telegram User:** Verified\n"
                "🔓 **Access:** 6 Hours\n\n"
                "Token immediately EXPIRED / one-time used\n"
                "Access: ✅ 6 hours\n"
                f"Expires: {expire_text}"
            )

            try:
                return await wait_msg.edit_text(text)
            except Exception:
                return await message.reply_text(text)

        text = (
            "❌ **Token Invalid / Expired**\n\n"
            "Please generate a new token."
        )

        try:
            return await wait_msg.edit_text(text)
        except Exception:
            return await message.reply_text(text)

    # ── Old vfy_ verification ──
    if payload.startswith("vfy_"):
        param = payload
        claimed_uid = int(param[4:]) if param[4:].isdigit() else 0

        if claimed_uid != uid:
            return await message.reply_text(
                "⚠️ Yeh verification link aapke liye nahi hai.\n"
                "Apna link lene ke liye /verify karein."
            )

        if is_owner(uid):
            return await message.reply_text(
                "👑 **Owner account — unlimited access!** No quota needed."
            )

        ok, reward_msg = apply_verify_bonus(uid)

        if ok:
            vdata = load_verified()

            if str(uid) not in vdata.setdefault("verified", {}):
                vdata["verified"][str(uid)] = {
                    "approved_by": "self_verify",
                    "approved_at": datetime.now(tz).isoformat(),
                }
                save_verified(vdata)

            pending_verify.pop(uid, None)

            return await message.reply_text(
                f"✅ **Verification Successful!**\n\n"
                f"{reward_msg}\n\n"
                "Use `/rec <url> HH:MM:SS <filename>` to start recording.\n"
                "Use /limit to check your full quota status.",
                disable_web_page_preview=True,
            )

        return await message.reply_text(
            f"⚠️ **Verification failed:**\n"
            f"{reward_msg}\n\n"
            "Use /limit to check status."
        )

    # ── Normal /start ──
    await message.reply_text(
        "🎬 **Welcome to Video Recorder Bot!**\n\n"
        "🔐 Verification is required for recording commands.\n"
        "🔑 Use /token to generate a verification token.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔑 Token Generator",
                callback_data="token_generate"
            )],
            [InlineKeyboardButton(
                "📖 Help",
                callback_data="help"
            )],
            [InlineKeyboardButton(
                "💠 Plans",
                callback_data="plan"
            )]
        ])
    )


@app.on_message(filters.command("token"))
async def token_command(client, message):
    # Owner bypasses token checks for protected commands, but can still use
    # /Token to create a verification link.
    if _has_valid_access(message.from_user.id) and not _is_owner(message.from_user.id):
        remaining = max(0, int(verification_access[str(message.from_user.id)] - time.time()))
        return await message.reply_text(f"✅ **Verification Active**\n\n🔓 Access remaining: `{remaining // 3600}h {(remaining % 3600) // 60}m`")

    await message.reply_text(
        "🔐 **Verification Required**\n\n"
        "Click the button below to generate a verification token.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Token Generator", callback_data="token_generate")]
        ])
    )



# ---------------------------------------------------------------------------
# /Token URL shortener — extracted from shortlink_providers_flow.py
# This is used ONLY by /Token. It does not add /Verify or verify_tokens.py.
# ---------------------------------------------------------------------------
TOKEN_SHORTENER_TIMEOUT = 10
TOKEN_SHORTENER_PROVIDERS = {
    "shortxlinks": {
        "name": "ShortXLinks",
        "url_env": "SHORTLINK_URL",
        "api_env": "SHORTLINK_API",
        "default_url": "https://shortxlinks.in",
    },
    "gplink": {
        "name": "GPlink",
        "url_env": "GPLINK_URL",
        "api_env": "GPLINK_API",
        "default_url": "https://gplinks.com",
    },
    "shrinkme": {
        "name": "ShrinkMe.click",
        "url_env": "SHRINKME_URL",
        "api_env": "SHRINKME_API",
        "default_url": "https://shrinkme.click",
    },
}


def _token_shortener_order():
    # /Token uses the shortener configured directly in Ldy.py.
    return ["shrinkme"]


def _token_shortener_key(provider_key):
    provider = TOKEN_SHORTENER_PROVIDERS[provider_key]

    # API key is configured directly in Ldy.py.
    api_key = SHORTENER_API.strip()

    # Use SHORTENER as the configured provider URL.
    base_url = SHORTENER.strip()

    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    base_url = base_url.rstrip("/")

    return api_key, base_url


def _token_extract_short_url(payload):
    if isinstance(payload, str) and payload.startswith(("http://", "https://")):
        return payload
    if not isinstance(payload, dict):
        return ""
    for field in ("shortenedUrl", "short_url", "shorturl", "link", "url"):
        value = payload.get(field)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for nested_key in ("data", "result"):
        value = _token_extract_short_url(payload.get(nested_key))
        if value:
            return value
    return ""


def _token_shorten_with_provider(provider_key, long_url):
    provider = TOKEN_SHORTENER_PROVIDERS[provider_key]
    api_key, base_url = _token_shortener_key(provider_key)
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{base_url}/api",
            params={"api": api_key, "url": long_url},
            timeout=TOKEN_SHORTENER_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"},
        )
        response.raise_for_status()
        shortened = _token_extract_short_url(response.json())
        return shortened or None
    except (requests.RequestException, ValueError, TypeError):
        return None


async def shorten_token_url(long_url: str) -> str:
    """Shorten only the /Token verification URL; safely fall back to original."""
    for provider_key in _token_shortener_order():
        shortened = await asyncio.to_thread(
            _token_shorten_with_provider, provider_key, long_url
        )
        if shortened:
            LOG.info("/Token verification URL shortened using %s.", provider_key)
            return shortened
    return long_url

@app.on_callback_query(filters.regex(r"^help$"))
async def start_help_callback(client, query):
    await _safe_callback_answer(query, )
    text = (
        "🛠 **Video Recorder Help Menu**\n\n"
        "🎯 **Recording Commands:**\n"
        "• 🎥 `/Rec` - Start recording\n"
        "• 🎥 `/Rec -16:9` - Record and fit directly to 1920×1080 without black padding\n"
        "• 🎬 `/Sony` - Sony LIV interactive recording\n"
        "• 🗓 `/schedule` - Schedule a recording\n"
        "• 🛑 `/cancel` - Stop ongoing recording (sends recorded portion)\n"
        "• 📊 `/status` - Check current recording progress\n"
        "• 🔑 `/token` - Token Generator (separate feature)\n\n"
        "📌 **Examples:**\n"
        "`/Rec <LINK/CHANNEL/ID/NAME> 00:00:30 File Name`\n"
        "`/Rec -16:9 <LINK/CHANNEL/ID/NAME> 00:00:30 File Name`\n"
        "`/Sony 00:00:30 File Name`\n\n"
        "⚙️ **Video Tools:** Audio Track • Trim • Watermark • Screenshot\n\n"
        "👨‍💻 _Bot maintained by @TPlayOwner_bot"
    )
    try:
        await query.message.edit_text(text, disable_web_page_preview=True)
    except Exception:
        try:
            await client.send_message(query.message.chat.id, text, disable_web_page_preview=True)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^plan$"))
async def start_plan_callback(client, query):
    await _safe_callback_answer(query, )
    text = (
        "💠 **Plans**\n\n"
        "🔹 **Free** — Normal users can use recording features in the authorized group.\n"
        "🔹 **Premium** — Direct access without verification.\n\n"
        "For premium access, contact the bot owner."
    )
    try:
        await query.message.edit_text(text, disable_web_page_preview=True)
    except Exception:
        try:
            await client.send_message(query.message.chat.id, text, disable_web_page_preview=True)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^token_generate$"))
async def token_generate_callback(client, query):
    user_id = query.from_user.id
    # Owner can generate a token even though protected commands bypass the gate.
    if _has_valid_access(user_id) and not _is_owner(user_id):
        return await _safe_callback_answer(query, "Verification is already active.", show_alert=True)

    await _safe_callback_answer(query, "Generating verification token...")
    token = _new_verification_token(user_id)
    me = await client.get_me()
    deep_link = f"https://t.me/{me.username}?start=verify_{token}_{user_id}"
    short_link = await shorten_token_url(deep_link)

    await query.message.reply_text(
        "🔑 **Generate Token**\n\n"
        "Tap **Verify Now** to verify your token.\n\n"
        "⏳ Access: **6 hours** after verification.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Verify Now", url=short_link)]
        ])
    )

# ---------------------------------------------------------------------------
# Premium administration — Owner / Admin only
# ---------------------------------------------------------------------------

def _premium_admin_allowed(user_id: int) -> bool:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    if uid == OWNER_ID:
        return True
    return uid in OWNER_IDS and bool(bot_settings.get("admin_access", True))


def _premium_usage_text() -> str:
    return (
        "❌ **Usage:** `/premium_add <user_id> [duration] [plan_name]`\n\n"
        "Examples:\n"
        "`/premium_add 123456789` — 30 days Standard\n"
        "`/premium_add 123456789 59m` — 59 minutes Standard\n"
        "`/premium_add 123456789 30 minute` — 30 minutes Standard\n"
        "`/premium_add 123456789 1h` — 1 hour Standard\n"
        "`/premium_add 123456789 2h Pro` — 2 hours Pro\n"
        "`/premium_add 123456789 24h` — 24 hours Standard\n"
        "`/premium_add 123456789 7` — 7 days Standard\n"
        "`/premium_add 123456789 90 Pro` — 90 days Pro\n"
        "`/premium_add 123456789 forever` — Lifetime"
    )


@app.on_message(filters.command(["premium_add", "premiumadd"]))
async def premium_add_command(client, message: Message):
    if not message.from_user or not _premium_admin_allowed(message.from_user.id):
        return await message.reply_text("❌ **Owner/Admin only.**")

    args = message.command[1:]
    if not args:
        return await message.reply_text(_premium_usage_text())

    try:
        user_id = int(args[0])
        if user_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return await message.reply_text("❌ Invalid user ID.")

    duration_seconds = 30 * 86400
    forever = False
    duration_arg_count = 0

    if len(args) >= 2:
        duration_seconds, forever = _parse_premium_duration(args[1])
        duration_arg_count = 1
        if not forever and duration_seconds is None and len(args) >= 3:
            duration_seconds, forever = _parse_premium_duration(f"{args[1]} {args[2]}")
            duration_arg_count = 2
        if not forever and duration_seconds is None:
            return await message.reply_text(
                "❌ Invalid duration. Use `30`, `59m`, `1h`, `30 minute`, or `forever`."
            )

    plan_name = " ".join(args[1 + duration_arg_count:]).strip() or "Standard"
    now = time.time()
    record = {
        "plan": plan_name,
        "added_by": str(message.from_user.id),
        "added_at": now,
        "forever": bool(forever),
    }

    if forever:
        record["expires"] = None
        expiry_text = "Lifetime"
    else:
        record["expires"] = now + int(duration_seconds)
        expiry_text = _format_premium_expiry(record)

    premium_users[str(user_id)] = record
    _save_premium_store()

    await message.reply_text(
        "✅ **Premium Added Successfully**\n\n"
        f"👤 **User ID:** `{user_id}`\n"
        f"💠 **Plan:** `{plan_name}`\n"
        f"⏳ **Expiry:** `{expiry_text}`\n\n"
        "🔓 Premium users have direct access without verification."
    )


@app.on_message(filters.command(["premium_expire", "premium_exipire"]))
async def premium_expire_command(client, message: Message):
    if not message.from_user or not _premium_admin_allowed(message.from_user.id):
        return await message.reply_text("❌ **Owner/Admin only.**")

    args = message.command[1:]
    if len(args) != 1:
        return await message.reply_text(
            "❌ **Usage:** `/premium_expire <user_id>`"
        )

    try:
        user_id = int(args[0])
        if user_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return await message.reply_text("❌ Invalid user ID.")

    key = str(user_id)
    if key not in premium_users:
        return await message.reply_text(
            f"❌ No active premium found for `{user_id}`."
        )

    premium_users.pop(key, None)
    _save_premium_store()

    await message.reply_text(
        "✅ **Premium Expired Successfully**\n\n"
        f"👤 **User ID:** `{user_id}`\n"
        "🔐 Verification will be required again for protected commands."
    )


@app.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    if not await _verification_required(message):
        return

    user_id = message.from_user.id
    
    if user_id not in user_tasks:
        return await message.reply_text("❌ **No active recording to cancel!**")
    
    try:
        # Mark user as cancelled first
        cancelled_users.add(user_id)
        
        # Stop progress tracking task
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
            del progress_tasks[user_id]
        
        # Kill FFmpeg process if running
        if user_id in user_ffmpeg_pids:
            ffmpeg_pid = user_ffmpeg_pids[user_id]
            try:
                # Kill the main FFmpeg process and its children
                parent = psutil.Process(ffmpeg_pid)
                children = parent.children(recursive=True)
                
                # Kill all child processes first
                for child in children:
                    try:
                        child.kill()
                    except:
                        pass
                
                # Kill parent process
                parent.kill()
                
                # Wait for processes to terminate
                gone, alive = psutil.wait_procs([parent] + children, timeout=3)
                
                LOG.info(f"Killed FFmpeg process {ffmpeg_pid} for user {user_id}")
            except psutil.NoSuchProcess:
                LOG.warning(f"FFmpeg process {ffmpeg_pid} already terminated")
            except Exception as e:
                LOG.error(f"Error killing FFmpeg process: {e}")
            
            del user_ffmpeg_pids[user_id]
        
        # Get task info before clearing
        task_info = user_status.get(user_id, {})
        filename = task_info.get("filename", "Unknown")
        save_dir = task_info.get("save_dir")
        
        # Clear user data but KEEP the save_dir info for later cleanup
        user_tasks.pop(user_id, None)
        user_status.pop(user_id, None)
        
        await message.reply_text(
            f"✅ **Recording Cancelled!**\n\n"
            f"📁 **File:** `{filename}`\n"
            f"🛑 **Status:** Stopped immediately\n"
            f"📤 **Uploading recorded portion...**"
        )
        
    except Exception as e:
        LOG.error(f"Error in cancel_command: {e}")
        await message.reply_text("❌ **Error cancelling recording!**")


async def cleanup_partial_files(user_id: int):
    """Clean up partially created files for a user"""
    try:
        # Find and remove any directories/files created during this session
        download_dir = config.DOWNLOAD_DIRECTORY
        if not os.path.exists(download_dir):
            return
            
        current_time = time.time()
        # Look for directories created in the last hour that might be partial
        for item in os.listdir(download_dir):
            item_path = join(download_dir, item)
            if os.path.isdir(item_path):
                try:
                    # Check if directory was created recently (within last hour)
                    dir_time = os.path.getctime(item_path)
                    if current_time - dir_time < 3600:  # 1 hour
                        # Check if it contains partial video files
                        video_files = [f for f in os.listdir(item_path) if f.endswith('.mkv') or f.endswith('.mp4')]
                        if video_files:
                            shutil.rmtree(item_path)
                            LOG.info(f"Cleaned up partial files in {item_path}")
                except Exception as e:
                    LOG.warning(f"Error cleaning up {item_path}: {e}")
    except Exception as e:
        LOG.error(f"Error in cleanup_partial_files: {e}")


@app.on_message(filters.command("status"))
async def status_cmd(client, message):
    # /status is read-only and should work even when recording access is not active.
    uid = message.from_user.id
    status = user_status.get(uid)

    if not status:
        return await message.reply("📭 No active recording task found.")

    # Start time from task ID
    start_ts = status["id"]
    start_dt = datetime.fromtimestamp(start_ts, tz=tz)
    start_time_str = start_dt.strftime("%d-%m-%Y %I:%M:%S %p")

    # Convert HH:MM:SS target duration → seconds
    target_seconds = time_to_seconds(status["target"])

    # Convert progress HH:MM:SS → seconds
    progress_sec = time_to_seconds(status["progress"])

    # Remaining time
    remaining = max(target_seconds - progress_sec, 0)
    eta_str = TimeFormatter(remaining * 1000)

    # Expected end time
    end_dt = start_dt + timedelta(seconds=target_seconds)
    end_time_str = end_dt.strftime("%d-%m-%Y %I:%M:%S %p")

    # FFmpeg status
    ffmpeg_status = "✅ Running" if uid in user_ffmpeg_pids else "❌ Not found"

    text = (
        f"📊 **Recording Status**\n\n"
        f"🆔 **Task ID:** `{status['id']}`\n"
        f"📁 **Filename:** `{status['filename']}`\n"
        f"⏱ **Duration:** `{status['progress']}` / `{status['target']}`\n"
        f"⏳ **ETA:** `{eta_str}`\n"
        f"🕒 **Started:** `{start_time_str}`\n"
        f"📅 **Expected End Time:** `{end_time_str}`\n"
        f"🔧 **FFmpeg:** `{ffmpeg_status}`\n"
        f"👤 **User:** @{message.from_user.username or 'anonymous'}\n\n"
        f"🛑 Use /cancel to stop recording"
    )

    await message.reply_text(text)


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "🛠 **Video Recorder Help Menu**\n\n"
        "🎯 **Recording Commands:**\n"
        "• 🎥 `/Rec` - Start recording\n"
        "• 🎥 `/Rec -16:9` - Record and fit directly to 1920×1080 without black padding\n"
        "• 🎬 `/Sony` - Sony LIV interactive recording\n"
        "• 🗓 `/schedule` - Schedule a recording\n"
        "• 🛑 `/cancel` - Stop ongoing recording (sends recorded portion)\n"
        "• 📊 `/status` - Check current recording progress\n"
        "• 🔑 `/token` - Token Generator (separate feature)\n\n"
        "📌 **Examples:**\n"
        "`/Rec <LINK/CHANNEL/ID/NAME> 00:00:30 File Name`\n"
        "`/Rec -16:9 <LINK/CHANNEL/ID/NAME> 00:00:30 File Name`\n"
        "`/Sony 00:00:30 File Name`\n\n"
        "⚙️ **Video Tools:** Audio Track • Trim • Watermark • Screenshot\n\n"
        "👨‍💻 _Bot maintained by @TPlayOwner_bot",
        disable_web_page_preview=True
    )


# ---------------------------------------------------------------------------
# Interactive /rec selection state
# ---------------------------------------------------------------------------
rec_sessions = {}
rec_session_tokens = {}

QUALITY_LABELS = {
    "144": "144p • 256×144 • H.264",
    "360": "360p • 640×360 • H.264",
    "480": "480p • 854×480 • H.264",
    "576": "576p • 720×576 • H.264",
    "720": "720p • 1280×720 • H.264",
    "1080": "1080p • 1920×1080 • H.264",
    "auto": "⚡ Auto",
}
AUDIO_LANGS = ["TE", "TA", "KN", "ML", "MR", "HI"]

# Persistent audio-track title presets. These affect only the output
# handler_name/title metadata; detected language mapping is unchanged.
AUDIO_TITLE_STORE_FILE = join(getattr(config, "DOWNLOAD_DIRECTORY", "."), "audio_track_titles.json")
DEFAULT_AUDIO_TITLE = "Anime Cartoon"
audio_track_titles = {"default": DEFAULT_AUDIO_TITLE, **{str(i): DEFAULT_AUDIO_TITLE for i in range(1, 13)}}

def _load_audio_track_titles():
    global audio_track_titles
    try:
        os.makedirs(os.path.dirname(AUDIO_TITLE_STORE_FILE) or ".", exist_ok=True)
        if os.path.exists(AUDIO_TITLE_STORE_FILE):
            with open(AUDIO_TITLE_STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str) and v.strip():
                        audio_track_titles[str(k)] = v.strip()
    except Exception as e:
        LOG.warning("Audio title store load failed: %s", e)

def _save_audio_track_titles():
    try:
        os.makedirs(os.path.dirname(AUDIO_TITLE_STORE_FILE) or ".", exist_ok=True)
        tmp = AUDIO_TITLE_STORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(audio_track_titles, f, indent=2, ensure_ascii=False)
        os.replace(tmp, AUDIO_TITLE_STORE_FILE)
    except Exception as e:
        LOG.warning("Audio title store save failed: %s", e)

def _audio_title_for_count(count: int) -> str:
    return audio_track_titles.get(str(count), audio_track_titles.get("default", DEFAULT_AUDIO_TITLE))

_load_audio_track_titles()
LANG_CODES = {
    "TE": {"tel", "te", "telugu"},
    "TA": {"tam", "ta", "tamil"},
    "KN": {"kan", "kn", "kannada"},
    "ML": {"mal", "ml", "malayalam"},
    "MR": {"mar", "mr", "marathi"},
    "HI": {"hin", "hi", "hindi"},
}


def _safe_filename(name: str) -> str:
    name = name.strip()
    name = ''.join('_' if c in '/\\:*?"<>|' else c for c in name)
    return name[:180] or config.DEFAULT_FILENAME


def _ffmpeg_drawtext_escape(text: str) -> str:
    """Escape text for FFmpeg drawtext filter syntax, not for a shell."""
    text = str(text).replace('\\', r'\\')
    text = text.replace(':', r'\:')
    text = text.replace("'", r"\'")
    text = text.replace('%', r'\%')
    text = text.replace('\n', ' ')
    return text[:500]


async def _resolve_channel_source(source: str) -> str | None:
    """Resolve direct URLs or channel names from standalone Channel.py."""
    source = source.strip()
    if source.lower().startswith(("http://", "https://")):
        return source
    # Numeric channel IDs use the current 24-hour ID table.
    channel_name = _channel_name_from_id(source) if source.strip().isdigit() else source
    if channel_name is None:
        return None
    try:
        url = get_channel_url(channel_name)
    except Exception as e:
        LOG.warning("Channel.py lookup failed for '%s': %s", source, e)
        return None
    if isinstance(url, str) and url.strip().lower().startswith(("http://", "https://")):
        LOG.info("Channel '%s' URL loaded from Channel.py", source)
        return url.strip()
    return None

async def _probe_streams(url: str):
    """Probe video/audio streams and return (heights, languages, video_index, error).

    HLS probing rule:
      - URL ending exactly in .m3u8 -> let ffprobe auto-detect the M3U8 input.
      - Any other URL -> force the HLS demuxer with ``-f hls``.

    Every FFprobe audio stream is valid regardless of language metadata.
    Unknown/missing language metadata is stored separately while preserving
    the real FFprobe stream index for later FFmpeg mapping.
    """
    stream_url, stream_headers = _stream_url_parts(url)
    parsed_url = urlsplit(stream_url)
    is_mpd = parsed_url.path.lower().endswith(".mpd")
    is_m3u8 = parsed_url.path.lower().endswith('.m3u8')

    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json']
    if not is_m3u8 and not is_mpd:
        cmd += ['-f', 'hls']
    if stream_headers:
        cmd += ['-headers', _format_stream_headers(stream_headers)]
    cmd += ['-show_streams', '-probesize', '10000000', '-analyzeduration', '15000000', stream_url]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            LOG.warning('ffprobe failed (%s): %s',
                        'm3u8-auto' if is_m3u8 else 'mpd-auto' if is_mpd else 'forced-hls',
                        err.decode(errors='ignore')[-1000:])
            # Probe failure means the source itself could not be opened/reached.
            # Keep this distinct from a successful probe with no audio streams.
            probe_error = err.decode(errors='ignore').strip()
            return [], None, None, probe_error[-2000:]

        data = json.loads(out.decode(errors='ignore'))
        video_streams = []
        for stream in data.get('streams', []):
            if stream.get('codec_type') != 'video' or not stream.get('height'):
                continue
            try:
                video_streams.append((int(stream.get('height')), int(stream.get('index'))))
            except (TypeError, ValueError):
                continue

        heights = sorted({h for h, _idx in video_streams}, reverse=True)
        # IMPORTANT for HLS master playlists: ffmpeg's 0:v:0 is often the
        # first/lowest variant (for example 144p). Remember the FFprobe stream
        # index of the highest variant so Sony Auto can explicitly map it.
        selected_video_index = None
        if video_streams:
            selected_video_index = max(video_streams, key=lambda item: item[0])[1]

        # Every codec_type == audio stream is real audio, even when tags == {}.
        # UNKNOWN contains the actual FFprobe indexes for missing/unknown language.
        lang_indexes = {lang: [] for lang in AUDIO_LANGS}
        lang_indexes['UNKNOWN'] = []

        for stream in data.get('streams', []):
            if stream.get('codec_type') != 'audio':
                continue

            try:
                stream_index = int(stream['index'])
            except (KeyError, TypeError, ValueError):
                LOG.warning('Audio stream has no valid FFprobe index: %r', stream)
                continue

            tags = stream.get('tags') or {}
            raw = str(
                tags.get('language') or tags.get('LANGUAGE') or tags.get('title') or ''
            ).strip().lower()

            matched_lang = None
            if raw:
                normalized_parts = raw.replace('_', '-').split('-')
                for lang, codes in LANG_CODES.items():
                    if raw in codes or any(part in codes for part in normalized_parts):
                        matched_lang = lang
                        break

            if matched_lang:
                lang_indexes[matched_lang].append(stream_index)
            else:
                lang_indexes['UNKNOWN'].append(stream_index)

        return heights, lang_indexes, selected_video_index, None
    except Exception as e:
        LOG.warning('Stream probe failed: %s', e)
        return [], None, None, str(e)[-2000:]


def _stream_url_parts(value: str) -> tuple[str, dict[str, str]]:
    """Split an M3U URL from its optional pipe-delimited request headers."""
    raw = str(value or "").strip()
    stream_url, separator, raw_headers = raw.partition("|")
    stream_url = stream_url.strip()
    if separator and stream_url.endswith("?"):
        stream_url = stream_url[:-1]

    headers: dict[str, str] = {}
    if separator:
        for key, header_value in parse_qsl(raw_headers, keep_blank_values=True):
            normalized = key.strip().casefold()
            if normalized in {"user-agent", "referer", "origin", "cookie"} and header_value:
                canonical = {
                    "user-agent": "User-Agent",
                    "referer": "Referer",
                    "origin": "Origin",
                    "cookie": "Cookie",
                }[normalized]
                headers[canonical] = header_value
    return stream_url, headers


def _format_stream_headers(headers: dict[str, str]) -> str:
    return "".join(f"{key}: {value}\r\n" for key, value in headers.items())


def _stream_input_args(url: str) -> list[str]:
    """Build FFmpeg input arguments for HLS, DASH, and M3U pipe headers."""
    stream_url, stream_headers = _stream_url_parts(url)
    path = urlsplit(stream_url).path.lower()
    args: list[str] = []
    if not path.endswith(".mpd") and not path.endswith(".m3u8"):
        args += ["-f", "hls"]
    if stream_headers:
        args += ["-headers", _format_stream_headers(stream_headers)]
    args += ["-i", stream_url]
    return args



async def _probe_sony_quality_variants(url: str):
    """Return available Sony HLS video variants as (height, width, stream_index)."""
    is_m3u8 = str(url).strip().lower().endswith('.m3u8')
    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json']
    if not is_m3u8:
        cmd += ['-f', 'hls']
    cmd += ['-show_streams', '-probesize', '10000000', '-analyzeduration', '15000000', url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            LOG.warning('Sony quality probe failed: %s', err.decode(errors='ignore')[-1200:])
            return []
        data = json.loads(out.decode(errors='ignore'))
        variants = []
        for st in data.get('streams', []):
            if st.get('codec_type') != 'video':
                continue
            try:
                w = int(st.get('width') or 0)
                h = int(st.get('height') or 0)
                idx = int(st.get('index'))
            except (TypeError, ValueError):
                continue
            if w > 0 and h > 0:
                variants.append((h, w, idx))
        # Keep the best stream index for each displayed height.
        best = {}
        for h, w, idx in variants:
            old = best.get(h)
            if old is None or w > old[1]:
                best[h] = (h, w, idx)
        return sorted(best.values(), key=lambda x: (x[0], x[1]), reverse=True)
    except Exception as e:
        LOG.warning('Sony quality probe exception: %s', e)
        return []


def _sony_quality_keyboard(session):
    """Dynamic Sony quality buttons from the actual HLS master variants."""
    variants = session.get('sony_quality_variants') or []
    token = session.get('callback_token', '')
    rows = []
    for h, w, idx in sorted(variants, key=lambda x: x[0]):
        label = f'{h}p'
        # 540p is represented by 960x540, etc. Use the real dimensions in text.
        label = f'{h}p • {w}×{h}'
        rows.append([InlineKeyboardButton(label, callback_data=f'sonyq:{token}:{h}:{idx}')])
    rows.append([InlineKeyboardButton('⬅️ Back: Audio Tracks', callback_data=f'recs:{token}:audio')])
    return InlineKeyboardMarkup(rows)



def _audio_keyboard(session):
    selected = session['audio']
    detected = session.get('lang_indexes', {})
    token = session.get('callback_token', '')
    rows = []

    for i in range(0, len(AUDIO_LANGS), 2):
        row = []
        for lang in AUDIO_LANGS[i:i+2]:
            mark = '✅' if lang in selected else '❌'
            row.append(InlineKeyboardButton(
                f'{mark} {lang} (AAC)',
                callback_data=f'reca:{token}:lang:{lang}'
            ))
        rows.append(row)

    unknown_indexes = detected.get('UNKNOWN', [])
    for track_no, _stream_index in enumerate(unknown_indexes, 1):
        track_key = f'UNKNOWN:{track_no - 1}'
        mark = '✅' if track_key in selected else '❌'
        rows.append([InlineKeyboardButton(
            f'{mark} Unknown Track {track_no} (AAC)',
            callback_data=f'reca:{token}:unknown:{track_no - 1}'
        )])

    rows.append([InlineKeyboardButton('🎵 All Tracks', callback_data=f'reca:{token}:all')])
    if _is_sony_source_name(session.get('raw_source_name', '')) and session.get('sony_new_command'):
        rows.append([
            InlineKeyboardButton('⬅️ Cancel', callback_data=f'recs:{token}:cancel'),
            InlineKeyboardButton('🎥 Select Quality', callback_data=f'recs:{token}:quality'),
        ])
    else:
        rows.append([
            InlineKeyboardButton('⬅️ Back', callback_data=f'recs:{token}:cancel'),
            InlineKeyboardButton('➡️ Quality', callback_data=f'recs:{token}:quality'),
        ])
        rows.append([InlineKeyboardButton('▶️ Start Recording', callback_data=f'recs:{token}:start')])
    return InlineKeyboardMarkup(rows)


def _audio_text(session):
    return (
        "🎵 **Select Audio Tracks**\n\n"
        "Select the audio tracks you want to record.\n"
        "Use **All Tracks** to select every detected track."
    )


def _quality_keyboard(session):
    if session.get('sony_new_command'):
        return _sony_quality_keyboard(session)
    selected = session["quality"]
    def btn(key, label):
        return InlineKeyboardButton(
            ("✅ " if selected == key else "") + label,
            callback_data=f"recq:{session.get('callback_token', '')}:{key}"
        )
    return InlineKeyboardMarkup([
        [btn("auto", "⚡ Auto")],
        [btn("480", "480p"), btn("576", "576p")],
        [btn("720", "720p"), btn("1080", "1080p")],
        [InlineKeyboardButton("⬅️ Back: Audio Tracks", callback_data=f"recs:{session.get('callback_token', '')}:audio")],
        [InlineKeyboardButton("▶️ Start Recording", callback_data=f"recs:{session.get('callback_token', '')}:start")],
    ])

def _quality_text(session):
    if session.get('sony_new_command'):
        return "🎥 **Select Quality**\n\n🔍 **Detecting...**\n\nChoose one of the available HLS qualities below."
    if _is_sony_source_name(session.get('raw_source_name', '')):
        return "🎥 **Sony LIV Quality**\n\n⚡ **Automatic**\n\nHighest available HLS quality will be selected automatically.\n1080p is selected when available."
    return "🎥 **Select Video Quality**\n\nChoose the recording quality."


async def _show_audio_menu(query, session):
    session["step"] = "audio"
    try:
        await query.message.edit_caption(_audio_text(session), reply_markup=_audio_keyboard(session))
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
            raise


async def _show_quality_menu(query, session):
    session["step"] = "quality"
    try:
        await query.message.edit_caption(_quality_text(session), reply_markup=_quality_keyboard(session))
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
            raise


def _format_bytes(value):
    try:
        value = float(value or 0)
    except Exception:
        value = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


async def _apply_audiotrack_to_replied_video(client, message: Message, title: str):
    """Remux a replied video/document and update every audio track title.

    Reply mode is deliberately separate from the bot-wide /Audiotrack settings.
    Temporary progress/status messages are removed after 10 seconds; the output
    video/document is never auto-deleted.
    """
    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False

    media = getattr(reply, "video", None) or getattr(reply, "document", None)
    if not media:
        return False

    filename = getattr(media, "file_name", None) or "input.mkv"
    safe_name = _safe_filename(filename)
    work_dir = join(getattr(config, "DOWNLOAD_DIRECTORY", "/tmp"), f"audiotrack_{secrets.token_hex(6)}")
    os.makedirs(work_dir, exist_ok=True)
    input_path = join(work_dir, safe_name)
    stem, ext = os.path.splitext(safe_name)
    ext = ext.lower() or ".mkv"
    # Always produce an MP4 so Telegram receives the result as a direct video,
    # even when the replied media was uploaded as a document/MKV.
    output_path = join(work_dir, f"{stem}.audiotrack.mp4")

    status = None
    cleanup_status = None

    async def _schedule_status_delete(msg, delay=10):
        if not msg:
            return
        try:
            await asyncio.sleep(delay)
            await msg.delete()
        except Exception:
            pass

    def _pct(current, total):
        try:
            if not total:
                return 0
            return max(0, min(100, int(current * 100 / total)))
        except Exception:
            return 0

    def _progress_bar(pct, width=10):
        try:
            pct = max(0, min(100, int(pct)))
        except Exception:
            pct = 0
        filled = min(width, int(pct * width / 100))
        return "🟩" * filled + "⬜" * (width - filled)

    async def _download_progress(current, total, *_args):
        pct = _pct(current, total)
        try:
            await status.edit_text(
                "📥 **Download progress**\n"
                f"{_progress_bar(pct)} **{pct}%**"
            )
        except Exception:
            pass

    async def _upload_progress(current, total, *_args):
        pct = _pct(current, total)
        try:
            await status.edit_text(
                "📤 **Uploading progress**\n"
                f"{_progress_bar(pct)} **{pct}%**"
            )
        except Exception:
            pass

    try:
        status = await message.reply_text(
            "📥 **Download progress**\n"
            f"{_progress_bar(0)} **0%**"
        )

        # Download progress is shown in the temporary status message.
        await client.download_media(
            reply,
            file_name=input_path,
            progress=_download_progress,
        )
        if not os.path.isfile(input_path) or os.path.getsize(input_path) <= 0:
            raise RuntimeError("Video download failed.")

        probe_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", input_path,
        ]
        probe = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, probe_err = await probe.communicate()
        if probe.returncode != 0:
            raise RuntimeError(probe_err.decode(errors="ignore")[-1200:] or "ffprobe failed")

        audio_indexes = [
            x.strip() for x in probe_out.decode(errors="ignore").splitlines()
            if x.strip().isdigit()
        ]
        if not audio_indexes:
            raise RuntimeError("No audio tracks found in the replied video.")

        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", input_path,
            "-map", "0",
            "-map_metadata", "0",
            "-map_chapters", "0",
            "-c", "copy",
        ]
        for out_audio_index in range(len(audio_indexes)):
            # Only title/handler_name are changed. Existing language metadata
            # stays untouched.
            args += [
                f"-metadata:s:a:{out_audio_index}", f"handler_name={title}",
                f"-metadata:s:a:{out_audio_index}", f"title={title}",
            ]
        args.append(output_path)

        await status.edit_text(
            "🎵 **Audiotrack Processing...**\n\n"
            f"🏷️ **Your_title:** `{title}`\n"
            f"🎧 **Tracks:** `{len(audio_indexes)}`\n\n"
            "⚙️ Updating audio metadata..."
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.isfile(output_path):
            raise RuntimeError(err.decode(errors="ignore")[-1800:] or "FFmpeg failed")

        verify_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream_tags=title:stream_tags=handler_name",
            "-of", "default=noprint_wrappers=1", output_path,
        ]
        verify = await asyncio.create_subprocess_exec(
            *verify_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        verify_out, verify_err = await verify.communicate()
        verify_text = verify_out.decode(errors="ignore")
        if verify.returncode != 0:
            raise RuntimeError(verify_err.decode(errors="ignore")[-1200:] or "Output verification failed")
        if title not in verify_text:
            raise RuntimeError("Output audio metadata verification failed.")

        output_duration = await get_duration_ffmpeg(output_path)
        if output_duration <= 0:
            output_duration = await get_duration_ffmpeg(input_path)

        await status.edit_text(
            "📤 **Uploading progress**\n"
            f"{_progress_bar(0)} **0%**"
        )

        # IMPORTANT: Always send the processed file as a Telegram VIDEO.
        # The input may have been a Telegram document (for example MKV), but
        # the output is always MP4 so it is delivered directly as video.
        await client.send_video(
            chat_id=message.chat.id,
            video=output_path,
            caption=None,
            duration=max(0, int(output_duration)),
            supports_streaming=True,
            reply_to_message_id=getattr(reply, "id", None),
            progress=_upload_progress,
        )

        # Completion is a separate temporary message, matching the requested
        # flow. It is deleted after 10 seconds; the video is not deleted.
        completed_msg = await message.reply_text(
            "🎵 **Audiotrack Updated**\n\n"
            f"🏷️ **Your_title:** `{title}`\n"
            f"🎧 **Tracks:** `{len(audio_indexes)}`\n\n"
            "✅ **Video sent successfully!**"
        )
        cleanup_status = asyncio.create_task(_schedule_status_delete(status, 10))
        asyncio.create_task(_schedule_status_delete(completed_msg, 10))
        # Also remove the /Audiotrack command after 10 seconds when Telegram
        # permissions allow it. This does NOT affect the output video.
        asyncio.create_task(_schedule_status_delete(message, 10))
        return True

    except Exception as e:
        LOG.exception("Reply video Audiotrack failed")
        try:
            if status:
                await status.edit_text(f"❌ **Audiotrack Failed**\n\n`{str(e)[:1500]}`")
                cleanup_status = asyncio.create_task(_schedule_status_delete(status, 10))
            else:
                err_msg = await message.reply_text(f"❌ **Audiotrack Failed**\n\n`{str(e)[:1500]}`")
                cleanup_status = asyncio.create_task(_schedule_status_delete(err_msg, 10))
            asyncio.create_task(_schedule_status_delete(message, 10))
        except Exception:
            pass
        return True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.on_message(filters.command("audiotrack"))
async def audiotrack_command(client, message: Message):
    args = message.command[1:] if getattr(message, "command", None) else []
    replied = getattr(message, "reply_to_message", None)
    replied_media = (
        getattr(replied, "video", None) or getattr(replied, "document", None)
    ) if replied else None

    # ================================================================
    # VIDEO REPLY MODE
    # Reply to a video/document and use:
    #   /Audiotrack Your_title
    # The existing bot-wide settings menu is NOT changed.
    # ================================================================
    if replied_media:
        if not args:
            return await message.reply_text(
                "🎵 **Video Reply Audiotrack**\n\n"
                "You replied to a video. Send the title you want to apply to "
                "all audio tracks:\n\n"
                "`/Audiotrack Your_title`\n\n"
                "Example:\n"
                "`/Audiotrack Anime Cartoon`"
            )

        title_from_reply = " ".join(args).strip()
        # Also accept the settings-style `count|title` syntax in reply mode,
        # but ignore the count because reply mode updates every audio track.
        if "|" in title_from_reply:
            _count, title_from_reply = title_from_reply.split("|", 1)
            title_from_reply = title_from_reply.strip()
        if not title_from_reply:
            return await message.reply_text("❌ Your_title cannot be empty.")
        if len(title_from_reply) > 100:
            return await message.reply_text("❌ Your_title is too long. Maximum 100 characters.")

        handled = await _apply_audiotrack_to_replied_video(
            client, message, title_from_reply
        )
        if handled:
            return

    # ================================================================
    # EXISTING BOT-WIDE SETTINGS MODE
    # No replied media -> keep the current 1–6 Tracks menu exactly.
    # ================================================================
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is None or not _has_privileged_access(user_id):
        return await message.reply_text(
            "🔒 **Audio Track Settings**\n\n"
            "Only Admin/Owner/Premium users can change audio-track titles."
        )

    if not args:
        rows = [[InlineKeyboardButton(f"{i} Tracks", callback_data=f"atitle:{i}")] for i in range(1, 7)]
        rows.append([InlineKeyboardButton("✏️ Edit Default Title", callback_data="atitle:default")])
        return await message.reply_text(
            "🎵 **Audio Track Title Settings**\n\n"
            "Set the `Your_title` metadata used for the selected number of audio tracks.\n"
            "Detected language mapping is not changed.",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    raw = " ".join(args).strip()
    if "|" in raw:
        count_text, title = raw.split("|", 1)
        key = count_text.strip()
        title = title.strip()
        if key != "default" and (not key.isdigit() or int(key) < 1 or int(key) > 12):
            return await message.reply_text("❌ Use `default` or a track count from 1 to 12.")
    else:
        key, title = "default", raw
    if not title:
        return await message.reply_text("❌ Title cannot be empty.")
    if len(title) > 100:
        return await message.reply_text("❌ Title is too long. Maximum 100 characters.")
    audio_track_titles[key] = title
    _save_audio_track_titles()
    label = "default" if key == "default" else f"{key} audio tracks"
    return await message.reply_text(f"✅ **Audio title updated**\n\n🎵 {label}: `{title}`")


@app.on_callback_query(filters.regex(r"^atitle:(default|[1-6])$"))
async def audiotrack_title_callback(client, query):
    user_id = getattr(query.from_user, "id", None)
    if user_id is None or not _has_privileged_access(user_id):
        return await _safe_callback_answer(query, "Not allowed.", show_alert=True)
    key = query.data.split(":", 1)[1]
    current = audio_track_titles.get(key, audio_track_titles.get("default", DEFAULT_AUDIO_TITLE))
    await _safe_callback_answer(query, )
    await query.message.reply_text(
        f"🎵 **Current title for {key}:**\n`{current}`\n\n"
        f"Use `/Audiotrack {key}|Your_title` to change it."
    )


@app.on_callback_query(filters.regex(r'^reca:'))
async def rec_audio_callback(client, query):
    parts = query.data.split(':')
    if len(parts) < 3:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)
    token = parts[1]
    session_key = rec_session_tokens.get(token)
    session = rec_sessions.get(session_key) if session_key is not None else None
    if not session:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)

    action = parts[2]
    if action == 'all':
        selected = {lang for lang in AUDIO_LANGS if session.get('lang_indexes', {}).get(lang)}
        selected.update(f'UNKNOWN:{i}' for i, _ in enumerate(session.get('lang_indexes', {}).get('UNKNOWN', [])))
        session['audio'] = selected
        await _safe_callback_answer(query, 'All tracks selected')
    elif action == 'unknown' and len(parts) == 4:
        try:
            pos = int(parts[3])
            unknown = session.get('lang_indexes', {}).get('UNKNOWN', [])
            if pos < 0 or pos >= len(unknown):
                return await _safe_callback_answer(query, 'Unknown audio track not found.', show_alert=True)
        except ValueError:
            return await _safe_callback_answer(query, 'Invalid audio track.', show_alert=True)
        key = f'UNKNOWN:{pos}'
        if key in session['audio']:
            session['audio'].remove(key)
        else:
            session['audio'].add(key)
        await _safe_callback_answer(query, f'Unknown Track {pos + 1}: ' + ('selected' if key in session['audio'] else 'not selected'))
    elif action == 'lang' and len(parts) == 4:
        value = parts[3]
        if value not in AUDIO_LANGS:
            return await _safe_callback_answer(query, 'Invalid audio track.', show_alert=True)
        if value in session['audio']:
            session['audio'].remove(value)
        else:
            session['audio'].add(value)
        await _safe_callback_answer(query, f"{value}: {'selected' if value in session['audio'] else 'not selected'}")
    else:
        return await _safe_callback_answer(query, 'Invalid audio selection.', show_alert=True)
    await _show_audio_menu(query, session)



@app.on_callback_query(filters.regex(r"^sonyq:"))
async def sony_quality_callback(client, query):
    parts = query.data.split(':', 3)
    if len(parts) != 4:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /Sony again.', show_alert=True)
    token, height_text, index_text = parts[1], parts[2], parts[3]
    session_key = rec_session_tokens.get(token)
    session = rec_sessions.get(session_key) if session_key is not None else None
    if not session or not session.get('sony_new_command'):
        return await _safe_callback_answer(query, 'Recording setup expired. Run /Sony again.', show_alert=True)
    try:
        height = int(height_text)
        index = int(index_text)
    except ValueError:
        return await _safe_callback_answer(query, 'Invalid Sony quality.', show_alert=True)
    valid = any(int(h) == height and int(idx) == index for h, _w, idx in session.get('sony_quality_variants', []))
    if not valid:
        return await _safe_callback_answer(query, 'That quality is no longer available.', show_alert=True)
    session['quality'] = str(height)
    session['quality_video_index'] = index
    await _safe_callback_answer(query, f'Selected {height}p')
    # Start immediately after quality selection.
    session['process_message'] = query.message
    try:
        await query.message.edit_caption(
            f"🎬 **Processing Video...**\\n\\n📄 **File:** `{session['raw_filename']}`\\n"
            f"🎥 **Quality:** `{height}p`\\n⚡ **Speed:** Calculating...\\nStatus: Starting Recording",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data=f'recs:{token}:cancel')]])
        )
    except Exception:
        pass
    asyncio.create_task(handle_record(client, session['message'], selection=session))

@app.on_callback_query(filters.regex(r"^recq:"))
async def rec_quality_callback(client, query):
    parts = query.data.split(':', 2)
    if len(parts) != 3:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)
    token, quality = parts[1], parts[2]
    session_key = rec_session_tokens.get(token)
    session = rec_sessions.get(session_key) if session_key is not None else None
    if not session:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)
    if quality not in QUALITY_LABELS:
        return await _safe_callback_answer(query, 'Invalid quality.', show_alert=True)
    session['quality'] = quality
    await _safe_callback_answer(query, f'Video quality: {QUALITY_LABELS[quality]}')
    await _show_quality_menu(query, session)


@app.on_callback_query(filters.regex(r"^recs:"))
async def rec_setup_callback(client, query):
    parts = query.data.split(':', 2)
    if len(parts) != 3:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)
    token, action = parts[1], parts[2]
    session_key = rec_session_tokens.get(token)
    session = rec_sessions.get(session_key) if session_key is not None else None
    if not session:
        return await _safe_callback_answer(query, 'Recording setup expired. Run /rec again.', show_alert=True)

    if action == 'cancel':
        rec_sessions.pop(session_key, None)
        rec_session_tokens.pop(token, None)
        await _safe_callback_answer(query, 'Cancelled')
        return await query.message.edit_caption('❌ **Recording setup cancelled.**')

    if action == 'quality':
        if not session['audio']:
            return await _safe_callback_answer(query, 'Select at least one audio track.', show_alert=True)
        if session.get('sony_new_command'):
            await _safe_callback_answer(query, 'Detecting Sony qualities...')
            variants = await _probe_sony_quality_variants(session['url'])
            if not variants:
                return await query.message.edit_caption('❌ **No video qualities detected from Sony HLS stream.**')
            session['sony_quality_variants'] = variants
            session['step'] = 'quality'
            try:
                await query.message.edit_caption(
                    "🎥 **Select Quality**\n\n🔍 **Detected qualities:**\n\n" +
                    "\n".join(f"{w}×{h} → {h}p" for h, w, _idx in sorted(variants, key=lambda x: x[0])),
                    reply_markup=_sony_quality_keyboard(session)
                )
            except Exception:
                pass
            return
        await _safe_callback_answer(query, )
        return await _show_quality_menu(query, session)

    if action == 'audio':
        await _safe_callback_answer(query, )
        return await _show_audio_menu(query, session)

    if action == 'start':
        if not session['audio']:
            return await _safe_callback_answer(query, 'Select at least one audio track.', show_alert=True)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data=f'recs:{token}:cancel')]])
        try:
            await query.message.edit_caption(
                '🎬 **Processing Video...**\n\n📄 **File:** `' + session['raw_filename'] +
                '`\n⚡ **Speed:** Calculating...\nStatus: Starting Recording',
                reply_markup=buttons)
        except Exception:
            try:
                await query.message.edit_text(
                    '🎬 **Processing Video...**\n\n📄 **File:** `' + session['raw_filename'] +
                    '`\n⚡ **Speed:** Calculating...\nStatus: Starting Recording',
                    reply_markup=buttons)
            except Exception:
                pass
        session['process_message'] = query.message
        asyncio.create_task(handle_record(client, session['message'], selection=session))
        return
    await _safe_callback_answer(query, 'Unknown option', show_alert=True)


def _sony_display_name(name: str):
    """Collapse Sony SD/HD catalog entries into one display channel name."""
    import re
    n = name.strip()
    if not n.casefold().startswith("sony "):
        return n
    return re.sub(r"\s+(?:SD|HD)$", "", n, flags=re.IGNORECASE).strip()

def _sony_preferred_channel_name(display_name: str):
    """Prefer an HD Sony source when both SD and HD catalog entries exist."""
    channels = get_public_channels()
    target = display_name.strip().casefold()
    matches = [name for name in channels if _sony_display_name(name).casefold() == target]
    if not matches:
        return None
    for name in matches:
        if name.casefold().endswith(" hd"):
            return name
    return matches[0]

def _is_sony_source_name(name: str) -> bool:
    return str(name or "").strip().casefold().startswith("sony ")

def _channel_group_key(name: str):
    """Return the automatic /Channel group for a channel name."""
    n = name.strip().casefold()
    if n.startswith("pogo"):
        return "POGO"
    if n.startswith("discoverykids"):
        return "DISCOVERY KIDS"
    if n.startswith("nick"):
        return "NICK"
    if n.startswith("sony "):
        return "SONY LIV"
    if n.startswith("cartoon network"):
        return "CARTOON NETWORK & HD+"
    return None

def _channel_groups(channels):
    """Build All groups automatically; no manual All entries are required."""
    groups = {}
    singles = []
    for name in channels:
        group = _channel_group_key(name)
        if group:
            groups.setdefault(group, []).append(name)
        else:
            singles.append(name)
    for names in groups.values():
        names.sort(key=lambda x: (x.casefold() != x.split()[0].casefold(), x.casefold()))
    return groups, singles

PLAYLIST_URLS = {
    "Tata Play": "https://raw.githubusercontent.com/Live-Anime-Cartoon-bot/CompressBot/refs/heads/main/Tpaly.m3u8",
    "Airtel": "https://raw.githubusercontent.com/Live-Anime-Cartoon-bot/CompressBot/refs/heads/main/airtel.m3u",
    "FW": os.getenv(
        "FW_PLAYLIST_URL",
        "https://raw.githubusercontent.com/Live-Anime-Cartoon-bot/CompressBot/refs/heads/main/Fastway.m3u8",
    ),
    "Jio Hotstar": "https://premiumplugx.com/htt/hot.php?playlist=1",
    "VIP": os.getenv("VIP_PLAYLIST_URL", "https://premiumplugx.com/VIP/pluglist.php"),
}
PLAYLIST_ALIASES = {
    "-fw": "FW",
    "-vip": "VIP",
    "-tplay": "Tata Play",
    "-tata": "Tata Play",
    "-airtel": "Airtel",
    "-jiohotstar": "Jio Hotstar",
    "-hotstar": "Jio Hotstar",
}

def _playlist_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Tata Play", callback_data="playlist:Tata Play")],
        [InlineKeyboardButton("📺 Airtel", callback_data="playlist:Airtel")],
        [InlineKeyboardButton("📺 FW", callback_data="playlist:FW")],
        [InlineKeyboardButton("📺 Jio Hotstar", callback_data="playlist:Jio Hotstar")],
        [InlineKeyboardButton("📁 VIP Groups", callback_data="playlist:VIP")],
        [InlineKeyboardButton("🔄 Refresh Playlist", callback_data="playlist_refresh")],
    ])

def _parse_playlist(text):
    items=[]; pending=None
    for line in (text or '').splitlines():
        line=line.strip()
        if line.startswith('#EXTINF'):
            title = line.rsplit(',', 1)[-1].strip()
            group_match = re.search(r'group-title=["\']([^"\']+)["\']', line, re.IGNORECASE)
            pending = (title, group_match.group(1).strip() if group_match else "Other")
        elif pending and line and not line.startswith('#'):
            items.append((pending[0], line, pending[1] or "Other"))
            pending=None
    return items

async def _playlist_items(name):
    try:
        r=await asyncio.to_thread(requests.get, PLAYLIST_URLS[name], timeout=20)
        r.raise_for_status()
        return _parse_playlist(r.text)
    except Exception as e:
        LOG.warning('Playlist load failed for %s: %s', name, e)
        return []

def _playlist_groups(items):
    groups = {}
    for index, (title, url, group) in enumerate(items):
        group = str(group or "Other").strip() or "Other"
        groups.setdefault(group, []).append((index, title, url, group))
    return list(groups.items())

def _playlist_group_alias_map(items):
    """Return compact command flags for playlist group names."""
    groups = [group for group, _entries in _playlist_groups(items)]
    aliases = {}
    collisions = set()
    for group in groups:
        full = "-" + re.sub(r"[^a-z0-9]+", "", group.casefold())
        if full in aliases and aliases[full] != group:
            collisions.add(full)
        aliases[full] = group
    for group in groups:
        if "|" not in group:
            continue
        short = "-" + re.sub(r"[^a-z0-9]+", "", group.rsplit("|", 1)[-1].casefold())
        if not short or short in collisions or (short in aliases and aliases[short] != group):
            continue
        aliases[short] = group
    return aliases

def _playlist_group_flag(group, items):
    aliases = _playlist_group_alias_map(items)
    for flag, value in aliases.items():
        if value.casefold() == str(group).casefold():
            return flag
    return "-" + re.sub(r"[^a-z0-9]+", "", str(group).casefold())

def _playlist_group_for_flag(flag, items):
    aliases = _playlist_group_alias_map(items)
    if flag in aliases:
        return aliases[flag]
    wanted = re.sub(r"[^a-z0-9]+", "", str(flag).lstrip("-").casefold())
    if wanted:
        for group, _entries in _playlist_groups(items):
            if re.sub(r"[^a-z0-9]+", "", group.casefold()) == wanted:
                return group
    return None

def _is_owner(user_id: int) -> bool:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False

    if uid == int(OWNER_ID):
        return True

    return uid in {int(x) for x in OWNER_IDS}


async def _verification_required(message) -> bool:
    user = getattr(message, "from_user", None)
    if not user:
        return False

    user_id = int(user.id)

    # Owner always has access
    if _is_owner(user_id):
        return True

    # Valid 6-hour token access
    if _has_valid_access(user_id):
        return True

    await message.reply_text(
        "🔐 **Verification Required**\n\n"
        "Your 6-hour access is not active.\n"
        "Use /token to generate a new verification token."
    )
    return False


@app.on_message(filters.command(["Channel", "channel"]))
async def channel_command(client, message: Message):
    if not await _verification_required(message): return
    await message.reply_text("📺 **Channel Menu**\n\nSelect your playlist.", reply_markup=_playlist_keyboard())

@app.on_message(filters.command(["Palylistrefresh", "Playlistrefresh", "playlistrefresh"]))
async def playlist_refresh_command(client, message: Message):
    if not await _verification_required(message): return
    await message.reply_text("🔄 **Playlist refreshed!**", reply_markup=_playlist_keyboard())

@app.on_callback_query(filters.regex(r"^playlist:(Tata Play|Airtel|FW|Jio Hotstar|VIP)$"))
async def playlist_callback(client, query):
    name=query.data.split(":",1)[1]
    await _safe_callback_answer(query, "Loading playlist...")
    items=await _playlist_items(name)
    if not items:
        try:
            return await query.message.edit_text("❌ Playlist load failed.")
        except Exception:
            return None
    if name == "VIP":
        groups = _playlist_groups(items)
        rows = [
            [InlineKeyboardButton(
                f"📁 {group[:42]} ({len(entries)})",
                callback_data=f"playlistg:{name}:{group_index}",
            )]
            for group_index, (group, entries) in enumerate(groups)
        ]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="playlist_back")])
        return await query.message.edit_text(
            "📁 **VIP Playlist Groups**\n\nSelect a group:",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    rows=[
        [InlineKeyboardButton(f"📺 {title[:48]}", callback_data=f"plch:{name}:{i}")]
        for i, (title, url, _group) in enumerate(items[:100])
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="playlist_back")])
    await query.message.edit_text(f"📺 **{name} Channels**\n\nSelect a channel:", reply_markup=InlineKeyboardMarkup(rows))

@app.on_callback_query(filters.regex(r"^playlistg:VIP:\d+$"))
async def playlist_group_callback(client, query):
    _, name, group_index = query.data.split(":")
    await _safe_callback_answer(query, "Loading group...")
    items = await _playlist_items(name)
    groups = _playlist_groups(items)
    group_index = int(group_index)
    if group_index >= len(groups):
        return await query.message.edit_text("❌ Playlist group not found.")

    group, entries = groups[group_index]
    rows = [
        [InlineKeyboardButton(
            f"📺 {title[:48]}",
            callback_data=f"plch:{name}:{group_index}:{position}",
        )]
        for position, (_source_index, title, _url, _group) in enumerate(entries[:100])
    ]
    rows.append([InlineKeyboardButton("⬅️ Back to groups", callback_data=f"playlist:{name}")])
    await query.message.edit_text(
        f"📺 **{group}**\n\nSelect a channel:",
        reply_markup=InlineKeyboardMarkup(rows),
    )

@app.on_callback_query(filters.regex(r"^plch:(Tata Play|Airtel|FW|Jio Hotstar|VIP):(?:\d+:)?\d+$"))
async def playlist_channel_callback(client, query):
    parts = query.data.split(":")
    name = parts[1]
    if len(parts) == 4 and name == "VIP":
        group_index, index = int(parts[2]), int(parts[3])
    else:
        # Non-VIP playlists are flat.  Use the final component so buttons
        # generated by the previous grouped format remain usable too.
        group_index, index = 0, int(parts[-1])
    await _safe_callback_answer(query, "Loading channel...")
    items=await _playlist_items(name)
    if len(parts) == 4 and name == "VIP":
        groups = _playlist_groups(items)
        if group_index >= len(groups) or index >= len(groups[group_index][1]):
            return await query.message.edit_text("❌ Channel not found.")
        group, entries = groups[group_index]
        _source_index, title, url, _group = entries[index]
    else:
        if index >= len(items):
            return await query.message.edit_text("❌ Channel not found.")
        title, url, group = items[index]
    if not title:
        try:
            return await query.message.edit_text("❌ Channel not found.")
        except Exception:
            return None
    command_flag = f"-{({'Tata Play': 'TPlay', 'Jio Hotstar': 'JioHotstar'}.get(name, name))}"
    back_callback = f"playlistg:{name}:{group_index}" if name == "VIP" else f"playlist:{name}"
    if name == "VIP":
        command_flag = _playlist_group_flag(group, items)
    try:
        await query.message.edit_text(
            f"📺 **{title}**\n\n"
            f"Use this command:\n"
            f"`/rec {command_flag} {title} 00:00:30`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data=back_callback
                )]
            ])
        )
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            raise

@app.on_callback_query(filters.regex(r"^playlist_refresh$"))
async def playlist_refresh_callback(client, query):
    await _safe_callback_answer(query, "Playlist refreshed")
    await query.message.edit_text("📺 **Channel Menu**\n\nSelect your playlist.", reply_markup=_playlist_keyboard())

@app.on_callback_query(filters.regex(r"^playlist_back$"))
async def playlist_back_callback(client, query):
    await _safe_callback_answer(query, )
    await query.message.edit_text("📺 **Channel Menu**\n\nSelect your playlist.", reply_markup=_playlist_keyboard())

@app.on_callback_query(filters.regex(r"^chgrp:"))
async def channel_group_callback(client, query):
    group_name = query.data.split(":", 1)[1]
    channels = get_public_channels()
    groups, _ = _channel_groups(channels)
    if group_name in ("SONY LIV SD", "SONY LIV HD"):
        want_hd = group_name.endswith("HD")
        names = [
            name for name in channels
            if name.strip().casefold().startswith("sony")
            and name.strip().casefold().endswith(" hd") == want_hd
        ]
    else:
        names = groups.get(group_name)
    if not names:
        return await _safe_callback_answer(query, "Channel group not found.", show_alert=True)

    rows = []
    seen = set()
    for name in names:
        display_name = _sony_display_name(name) if group_name in ("SONY LIV", "SONY LIV SD", "SONY LIV HD") else name
        key = display_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        if group_name == "SONY LIV SD":
            preferred = next((n for n in names if _sony_display_name(n).casefold() == key and n.casefold().endswith(" sd")), name)
        elif group_name == "SONY LIV HD":
            preferred = next((n for n in names if _sony_display_name(n).casefold() == key and n.casefold().endswith(" hd")), name)
        elif group_name == "SONY LIV":
            preferred = _sony_preferred_channel_name(display_name)
        else:
            preferred = name
        cid = channel_id_map.get(preferred)
        if cid is None:
            continue
        rows.append([InlineKeyboardButton(
            f"📺 {display_name}", callback_data=f"ch:{cid}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="chback")])
    await _safe_callback_answer(query, )
    try:
        await query.message.edit_text(
            f"📺 **{group_name} All**\n\nSelect a channel:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass

@app.on_callback_query(filters.regex(r"^ch:\d+$"))
async def channel_info_callback(client, query):
    cid = query.data.split(":", 1)[1]
    name = _channel_name_from_id(cid)
    if not name:
        return await _safe_callback_answer(query, "❌ Invalid Channel ID", show_alert=True)
    await _safe_callback_answer(query, 
        f"📺 Channel Name: {name}\n🆔 Channel ID: {cid}",
        show_alert=True,
    )

@app.on_callback_query(filters.regex(r"^chback$"))
async def channel_back_callback(client, query):
    channels = get_public_channels()
    groups, singles = _channel_groups(channels)
    rows = []
    for group_name in groups:
        if group_name == "SONY LIV":
            rows.append([InlineKeyboardButton("📺 Sony LIV All SD", callback_data="chgrp:SONY LIV SD")])
            rows.append([InlineKeyboardButton("📺 Sony LIV All HD", callback_data="chgrp:SONY LIV HD")])
        else:
            rows.append([InlineKeyboardButton(f"📺 {group_name} All", callback_data=f"chgrp:{group_name}" )])
    for name in singles:
        rows.append([InlineKeyboardButton(f"📺 {name}", callback_data=f"ch:{channel_id_map.get(name)}")])
    rows.append([InlineKeyboardButton("🔄 Refresh Channel IDs", callback_data="chrefresh")])
    await _safe_callback_answer(query, )
    try:
        await query.message.edit_text(
            "📺 **Available Channels**\n\nSelect a channel group or channel.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass

@app.on_callback_query(filters.regex(r"^chrefresh$"))
async def channel_refresh_callback(client, query):
    global channel_id_map
    # Force a fresh 24-hour ID set. Existing links/names remain unchanged.
    available = list(range(10, 100))
    random.shuffle(available)
    channels = get_public_channels()
    channel_id_map = {name: available[i] for i, name in enumerate(channels)}
    _save_channel_ids()
    await _safe_callback_answer(query, "Channel IDs refreshed for the next 24 hours.")
    return await channel_back_callback(client, query)


async def _capture_live_preview(url: str, output_path: str) -> bool:
    """Capture one current frame from a live HLS or DASH source."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            *_stream_input_args(url),
            "-frames:v", "1", "-q:v", "3", output_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return False
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        LOG.warning("Live preview capture failed: %s", e)
        return False



# ---------------------------------------------------------------------------
# Scheduled recordings
# ---------------------------------------------------------------------------
SCHEDULE_STORE_FILE = join(
    getattr(config, "DOWNLOAD_DIRECTORY", "."),
    "recording_schedules.json",
)
scheduled_recordings = {}
schedule_worker_task = None


def _load_schedules():
    global scheduled_recordings
    try:
        os.makedirs(os.path.dirname(SCHEDULE_STORE_FILE) or ".", exist_ok=True)
        if os.path.exists(SCHEDULE_STORE_FILE):
            with open(SCHEDULE_STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            scheduled_recordings = data if isinstance(data, dict) else {}
    except Exception as e:
        LOG.warning("Schedule store load failed: %s", e)
        scheduled_recordings = {}


def _save_schedules():
    try:
        os.makedirs(os.path.dirname(SCHEDULE_STORE_FILE) or ".", exist_ok=True)
        tmp = SCHEDULE_STORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(scheduled_recordings, f, indent=2)
        os.replace(tmp, SCHEDULE_STORE_FILE)
    except Exception as e:
        LOG.warning("Schedule store save failed: %s", e)


def _parse_schedule_datetime(date_text: str, time_text: str):
    for fmt in ("%d-%m-%Y %I:%M:%S%p", "%d-%m-%Y %I:%M:%S %p", "%d-%m-%Y %H:%M:%S"):
        try:
            return tz.localize(datetime.strptime(f"{date_text} {time_text}", fmt))
        except ValueError:
            continue
    return None


def _schedule_usage():
    return (
        "❌ **Invalid Format**\n\n"
        "📺 **PlaylistGen Provider:**\n"
        "`/schedule -FW <CHANNEL NAME> <START> <END> <DATE>`\n"
        "`/schedule -TPlay <CHANNEL NAME> <START> <END> <DATE>`\n"
        "`/schedule -Airtel <CHANNEL NAME> <START> <END> <DATE>`\n\n"
        "Example:\n"
        "`/schedule -FW Pogo Hindi 06:00:00PM 07:00:00PM 01-09-2026`\n\n"
        "📅 **Direct Link:**\n"
        "`/schedule <LINK> <START> <END> <DATE>`\n\n"
        "Example:\n"
        "`/schedule https://example.com/live.m3u8 06:00:00PM 07:00:00PM 01-09-2026`\n\n"
        "📺 **Channel ID:**\n"
        "`/schedule <ID> <START> <END> <DATE>`\n\n"
        "Example:\n"
        "`/schedule 56 06:00:00PM 07:00:00PM 01-09-2026`\n\n"
        "⏰ Channel IDs automatically refresh every 24 hours."
    )


# Provider-specific headers hook. Add only headers you are authorized to use.
# Example: PROVIDER_HEADERS_JSON='{"example.com":{"Referer":"https://example.com/","User-Agent":"Mozilla/5.0"}}'
def _provider_headers(url: str, provider: str | None = None) -> dict:
    """Return only explicitly configured, authorized provider/host headers.

    Backward compatible with the existing host-keyed PROVIDER_HEADERS_JSON.
    Provider-keyed entries may also be used, e.g. {"FW": {"Referer": "..."}}.
    """
    try:
        import json as _json
        from urllib.parse import urlparse
        raw = os.environ.get("PROVIDER_HEADERS_JSON", "{}")
        mapping = _json.loads(raw) if raw else {}
        if not isinstance(mapping, dict):
            return {}

        # Provider-specific hook (FW / Tata Play / Airtel, etc.).
        if provider:
            wanted = str(provider).strip().casefold()
            for key, headers in mapping.items():
                if str(key).strip().casefold() == wanted:
                    return {str(k): str(v) for k, v in (headers or {}).items()}

        # Existing host-specific hook remains supported.
        host = (urlparse(str(url)).hostname or "").lower()
        for key, headers in mapping.items():
            key = str(key).lower().strip()
            if host == key or host.endswith("." + key):
                return {str(k): str(v) for k, v in (headers or {}).items()}
    except Exception as exc:
        LOG.warning("Provider headers hook ignored: %s", exc)
    return {}


def _apply_provider_headers(ffmpeg_args: list, url: str, provider: str | None = None) -> list:
    headers = _provider_headers(url, provider)
    if not headers:
        return ffmpeg_args
    header_text = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    try:
        pos = ffmpeg_args.index("-i")
        ffmpeg_args[pos:pos] = ["-headers", header_text]
    except ValueError:
        pass
    return ffmpeg_args


async def _run_scheduled_recording(client, item):
    try:
        url = item["url"]
        duration = item["duration"]
        provider = item.get("provider") or None
        chat_id = int(item["chat_id"])
        actor_id = int(item["actor_id"])
        filename = item.get("filename") or "Scheduled"

        # Re-probe at start time so the actual stream/audio indexes are current.
        heights, lang_indexes, selected_video_index, _probe_error = await _probe_streams(url)
        if lang_indexes is None:
            LOG.warning("Scheduled source unavailable during FFprobe: %s", url)
            return
        detected_audio = {lang for lang, indexes in lang_indexes.items() if indexes}
        if not detected_audio:
            await client.send_message(chat_id, "❌ Scheduled recording skipped: no real audio tracks detected by FFprobe.")
            return

        status_msg = await client.send_message(
            chat_id,
            f"⏰ **Scheduled Recording Started**\n\n📄 `{filename}`\n⏱ `{duration}`"
        )
        selection = {
            "message": status_msg,
            "actor_id": actor_id,
            "chat_id": chat_id,
            "url": url,
            "provider": provider,
            "timestamp": duration,
            "raw_filename": _safe_filename(filename),
            "quality": "auto",
            "audio": set(detected_audio),
            "lang_indexes": lang_indexes,
            "watermark": "off",
            "step": "audio",
            "detected_heights": heights,
            "selected_video_index": selected_video_index,
            "sony_quality_mode": ("hd" if str(item.get("source_name", "")).strip().casefold().endswith(" hd") else "sd" if str(item.get("source_name", "")).strip().casefold().endswith(" sd") else "auto"),
            "process_message": status_msg,
            "task_id": secrets.token_hex(8),
        }
        await handle_record(client, status_msg, selection=selection)
    except Exception as e:
        LOG.error("Scheduled recording failed: %s", e)
        try:
            await client.send_message(int(item["chat_id"]), f"❌ Scheduled recording failed.\n`{str(e)[:800]}`")
        except Exception:
            pass


async def _schedule_worker(client):
    global scheduled_recordings
    while True:
        try:
            now = datetime.now(tz)
            due = []
            for sid, item in list(scheduled_recordings.items()):
                if item.get("status") != "scheduled":
                    continue
                try:
                    run_at = datetime.fromisoformat(item["run_at"])
                except Exception:
                    continue
                if now >= run_at:
                    due.append((sid, item))

            for sid, item in due:
                # Mark before starting so a slow loop/restart cannot start the same item twice.
                scheduled_recordings[sid]["status"] = "running"
                scheduled_recordings[sid]["started_at"] = now.isoformat()
                _save_schedules()
                asyncio.create_task(_finish_schedule_item(client, sid, item))

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            LOG.warning("Schedule worker error: %s", e)
            await asyncio.sleep(2)


async def _finish_schedule_item(client, sid, item):
    try:
        await _run_scheduled_recording(client, item)
        scheduled_recordings[sid]["status"] = "completed"
    except Exception as e:
        scheduled_recordings[sid]["status"] = "failed"
        LOG.error("Schedule %s failed: %s", sid, e)
    finally:
        scheduled_recordings[sid]["finished_at"] = datetime.now(tz).isoformat()
        _save_schedules()


_load_schedules()


@app.on_message(filters.command(["schedule", "subedule"]))
async def schedule_command(client, message: Message):
    if not await _verification_required(message):
        return

    args = [str(x).strip() for x in message.command[1:]]
    if len(args) < 4:
        return await message.reply_text(_schedule_usage())

    # Supported forms:
    #   /schedule <LINK> <START> <END> <DATE>
    #   /schedule <CHANNEL_ID> <START> <END> <DATE>
    #   /schedule -FW <CHANNEL NAME...> <START> <END> <DATE>
    #   /schedule -TPlay <CHANNEL NAME...> <START> <END> <DATE>
    #   /schedule -Airtel <CHANNEL NAME...> <START> <END> <DATE>
    #   /schedule <ID> <LINK/ID> <START> <END> <DATE>
    #   /schedule <ID> -FW <CHANNEL NAME...> <START> <END> <DATE>
    schedule_id = secrets.token_hex(4)
    provider = None

    provider_aliases = {
        "-fw": "FW",
        "-tplay": "Tata Play",
        "-tata": "Tata Play",
        "-airtel": "Airtel",
    }

    # Provider flag may be the first token, or the second token when an
    # explicit schedule ID is supplied.
    if args and args[0].casefold() in provider_aliases:
        provider = provider_aliases[args.pop(0).casefold()]
    elif len(args) >= 2 and args[1].casefold() in provider_aliases:
        schedule_id = args.pop(0) or schedule_id
        provider = provider_aliases[args.pop(0).casefold()]

    if len(args) < 4:
        return await message.reply_text(_schedule_usage())

    # The final three tokens are always START, END and DATE. Everything before
    # them is the source/channel name, allowing names such as "Pogo Hindi".
    start_text, end_text, date_text = args[-3:]
    source_tokens = args[:-3]
    if not source_tokens:
        return await message.reply_text(_schedule_usage())
    source = " ".join(source_tokens).strip()

    if provider:
        # Provider playlist mode: resolve the named channel from that
        # provider's PlaylistGen source. No provider URL is guessed.
        playlist_items = await _playlist_items(provider)

        def _channel_key(value):
            return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

        wanted_key = _channel_key(source)
        match = next(((title, url) for title, url, _group in playlist_items
                      if _channel_key(title) == wanted_key), None)
        if not match and wanted_key:
            match = next(((title, url) for title, url, _group in playlist_items
                          if wanted_key in _channel_key(title) or _channel_key(title) in wanted_key), None)
        if not match and wanted_key:
            wanted_words = set(re.findall(r"[a-z0-9]+", source.casefold()))
            match = next(((title, url) for title, url, _group in playlist_items
                          if wanted_words and wanted_words.issubset(set(re.findall(r"[a-z0-9]+", title.casefold())))), None)

        if not match:
            examples = ", ".join(title for title, _url, _group in playlist_items[:8])
            return await message.reply_text(
                f"❌ Channel `{source}` not found in {provider} playlist.\n\n"
                f"Available examples: {examples or 'Playlist empty'}"
            )
        display_source, url = match
    elif source.isdigit() and not source.startswith(("http://", "https://")):
        channel_name = _channel_name_from_id(source)
        if not channel_name:
            return await message.reply_text(
                f"❌ **Invalid Channel ID:** `{source}`\n\nUse `/Channel` to get the current 24-hour IDs."
            )
        url = await _resolve_channel_source(source)
        display_source = f"{channel_name} (ID {source})"
    else:
        url = await _resolve_channel_source(source)
        display_source = source

    if not url:
        return await message.reply_text("❌ **Channel/Link not found.**\n\nUse `/Channel` to view current Channel IDs.")

    start_dt = _parse_schedule_datetime(date_text, start_text)
    end_dt = _parse_schedule_datetime(date_text, end_text)
    if not start_dt or not end_dt:
        return await message.reply_text("❌ Invalid date/time. Use `01-09-2026` and `06:00:00PM` format.")
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    if end_dt <= datetime.now(tz):
        return await message.reply_text("❌ This schedule time has already passed.")

    duration_seconds = int((end_dt - start_dt).total_seconds())
    if duration_seconds <= 0:
        return await message.reply_text("❌ Invalid schedule duration.")

    # Avoid accidental duplicate schedule IDs.
    if schedule_id in scheduled_recordings:
        schedule_id = secrets.token_hex(4)

    user = getattr(message, "from_user", None)
    actor_id = _rec_actor_id(message)
    if actor_id is None:
        return await message.reply_text("❌ Unable to identify the scheduling session.")

    scheduled_recordings[schedule_id] = {
        "id": schedule_id,
        "url": url,
        "source": display_source,
        "provider": provider,
        "start": start_text,
        "end": end_text,
        "date": date_text,
        "run_at": start_dt.isoformat(),
        "duration": f"{duration_seconds // 3600:02}:{(duration_seconds % 3600) // 60:02}:{duration_seconds % 60:02}",
        "chat_id": int(message.chat.id),
        "actor_id": int(actor_id),
        "user_id": int(user.id) if user is not None else None,
        "filename": f"Scheduled-{display_source}",
        "status": "scheduled",
        "created_at": datetime.now(tz).isoformat(),
    }
    _save_schedules()

    global schedule_worker_task
    if schedule_worker_task is None or schedule_worker_task.done():
        schedule_worker_task = asyncio.create_task(_schedule_worker(client))

    await message.reply_text(
        "✅ **Recording Scheduled**\n\n"
        f"🆔 **Schedule ID:** `{schedule_id}`\n"
        f"📺 **Source:** `{display_source}`" + (f"\n🏷️ **Provider:** `{provider}`" if provider else "") + "\n"
        f"🕒 **Start:** `{start_text}`\n"
        f"🕒 **End:** `{end_text}`\n"
        f"📅 **Date:** `{date_text}`\n"
        f"⏱ **Duration:** `{duration_seconds // 3600:02}:{(duration_seconds % 3600) // 60:02}:{duration_seconds % 60:02}`"
    )


@app.on_message(filters.command("schedules"))
async def schedules_command(client, message: Message):
    if not await _verification_required(message):
        return
    # /schedules shows only pending/upcoming schedules.
    # Completed/failed historical entries remain in storage but are hidden here.
    items = [
        item for item in scheduled_recordings.values()
        if item.get("status") == "scheduled"
    ]
    if not items:
        return await message.reply_text(
            "📅 **Scheduled Recordings**\n\n"
            "ℹ️ No pending or upcoming schedules."
        )

    lines = ["📅 **Scheduled Recordings**", "", "🟡 **Pending / Upcoming**", ""]
    for item in sorted(items, key=lambda x: x.get("run_at", "")):
        lines.extend([
            f"🆔 `{item.get('id')}`",
            f"📺 `{item.get('source', item.get('url', 'Unknown'))}`",
            f"📅 `{item.get('date')}`",
            f"🕒 `{item.get('start')} - {item.get('end')}`",
            "",
        ])
    await message.reply_text("\n".join(lines))




async def _delete_message_after(message, delay=0):
    """Best-effort automatic deletion for temporary messages."""
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass

@app.on_message(filters.text & ~filters.command(["rec","Sony","start","help","token","plan","schedule","schedules","Channel","settings"]), group=10)
async def no_need_rec_command(client, message: Message):
    if not bot_settings.get("no_need_rec",False): return
    parts=(getattr(message,"text","") or "").strip().split()
    if len(parts)<2: return
    if not (parts[0].startswith(("http://","https://")) or parts[0].isdigit()): return
    await rec_command(client,message)


@app.on_message(filters.command("Sony"))
async def sony_command(client, message: Message):
    """Interactive Sony LIV recorder: channel -> preview/audio -> detected quality."""
    try:
        await message.delete()
    except Exception:
        pass
    if not await _verification_required(message):
        return
    args = list(getattr(message, 'command', []) or [])[1:]
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Invalid Format**\\n\\n"
            "`/Sony <DURATION> <FILENAME>`\\n\\n"
            "Example: `/Sony 00:00:30 SonyYAY`"
        )
    duration = args[0].strip()
    if time_to_seconds(duration) <= 0:
        return await message.reply_text('❌ Invalid duration. Use `HH:MM:SS`.')
    filename = _safe_filename(' '.join(args[1:]).strip())
    uid = _rec_actor_id(message)
    if uid is None:
        return await message.reply_text('❌ Unable to identify the recording session.')
    active_count = _active_recording_count(uid)
    setup_count = 1 if uid in rec_sessions else 0
    if active_count + setup_count >= MAX_RECORDINGS_PER_USER:
        return await message.reply_text(f'❌ **Recording Limit Reached!**\\n\\nMaximum allowed: `{MAX_RECORDINGS_PER_USER}`.')
    token = secrets.token_hex(8)
    session = {
        'message': message,
        'actor_id': uid,
        'chat_id': getattr(getattr(message, 'chat', None), 'id', None),
        'timestamp': duration,
        'raw_filename': filename,
        'callback_token': token,
        'quality': 'auto',
        'audio': set(),
        'lang_indexes': {},
        'watermark': 'off',
        'step': 'channel',
        'sony_new_command': True,
        'sony_quality_variants': [],
        'selected_video_index': None,
        'quality_video_index': None,
        'sony_quality_mode': 'auto',
    }
    rec_sessions[uid] = session
    rec_session_tokens[token] = uid
    rows = [
        [InlineKeyboardButton('📺 Sony Channels SD', callback_data=f'sonycat:{token}:sd')],
        [InlineKeyboardButton('📺 Sony Channels HD', callback_data=f'sonycat:{token}:hd')],
        [InlineKeyboardButton('❌ Cancel', callback_data=f'recs:{token}:cancel')],
    ]
    await message.reply_text(
        f"🎬 **Sony Live Channels**\\n\\n⏱ **Duration:** `{duration}`\\n📄 **Filename:** `{filename}`\\n\\nSelect channel group:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@app.on_callback_query(filters.regex(r'^sonycat:'))
async def sony_category_callback(client, query):
    parts = query.data.split(':', 2)
    if len(parts) != 3:
        return await _safe_callback_answer(query, 'Sony setup expired.', show_alert=True)
    token, category = parts[1], parts[2].lower()
    uid = rec_session_tokens.get(token)
    session = rec_sessions.get(uid) if uid is not None else None
    if not session or not session.get('sony_new_command'):
        return await _safe_callback_answer(query, 'Sony setup expired. Run /Sony again.', show_alert=True)
    channels = get_public_channels()
    want_hd = category == 'hd'
    names = [n for n in channels if str(n).strip().casefold().startswith('sony ') and str(n).strip().casefold().endswith(' hd') == want_hd]
    display = {}
    for n in names:
        display.setdefault(_sony_display_name(n).casefold(), _sony_display_name(n))
    rows = []
    for key, label in sorted(display.items(), key=lambda x: x[1].casefold()):
        rows.append([InlineKeyboardButton(f'📺 {label}', callback_data=f'sonych:{token}:{channel_id_map.get(next((n for n in names if _sony_display_name(n).casefold()==key), ""), "")}')])
    rows = [r for r in rows if r[0].callback_data.rsplit(':',1)[-1].isdigit()]
    rows.append([InlineKeyboardButton('⬅️ Back', callback_data=f'sonyback:{token}')])
    await _safe_callback_answer(query, )
    try:
        await query.message.edit_text(
            f"📺 **Sony Channels {'HD' if want_hd else 'SD'}**\\n\\nSelect channel:",
            reply_markup=InlineKeyboardMarkup(rows)
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r'^sonyback:'))
async def sony_back_callback(client, query):
    token = query.data.split(':',1)[1]
    uid = rec_session_tokens.get(token)
    session = rec_sessions.get(uid) if uid is not None else None
    if not session:
        return await _safe_callback_answer(query, 'Sony setup expired.', show_alert=True)
    rows = [
        [InlineKeyboardButton('📺 Sony Channels SD', callback_data=f'sonycat:{token}:sd')],
        [InlineKeyboardButton('📺 Sony Channels HD', callback_data=f'sonycat:{token}:hd')],
        [InlineKeyboardButton('❌ Cancel', callback_data=f'recs:{token}:cancel')],
    ]
    await _safe_callback_answer(query, )
    try:
        await query.message.edit_text('🎬 **Sony Live Channels**\\n\\nSelect channel group:', reply_markup=InlineKeyboardMarkup(rows))
    except Exception:
        pass


@app.on_callback_query(filters.regex(r'^sonych:'))
async def sony_channel_callback(client, query):
    parts = query.data.split(':', 2)
    if len(parts) != 3 or not parts[2].isdigit():
        return await _safe_callback_answer(query, 'Invalid Sony channel.', show_alert=True)
    token, cid = parts[1], parts[2]
    uid = rec_session_tokens.get(token)
    session = rec_sessions.get(uid) if uid is not None else None
    name = _channel_name_from_id(cid)
    if not session or not name or not _is_sony_source_name(name):
        return await _safe_callback_answer(query, 'Sony setup expired or channel not found.', show_alert=True)
    await _safe_callback_answer(query, 'Detecting Sony stream...')
    url = await _resolve_channel_source(name)
    if not url:
        try:
            return await query.message.edit_text('❌ Sony channel link not found.')
        except Exception:
            return None
    session['url'] = url
    session['raw_source_name'] = name
    session['selected_video_index'] = None
    session['quality_video_index'] = None
    heights, lang_indexes, selected_video_index, _probe_error = await _probe_streams(url)
    async def _sony_error(text):
        # Callback messages can become invalid/deleted while preview probing is running.
        # Never let MESSAGE_ID_INVALID hide the real Sony error.
        try:
            return await query.message.edit_text(text)
        except Exception:
            try:
                return await client.send_message(query.message.chat.id, text)
            except Exception:
                return None

    if lang_indexes is None:
        return await _sony_error('❌ **Sony channel link is unavailable.**')
    detected_audio = {lang for lang, indexes in lang_indexes.items() if indexes}
    if not detected_audio:
        return await _sony_error('❌ **No audio tracks detected.**')
    session['lang_indexes'] = lang_indexes
    session['audio'] = set(detected_audio)
    session['selected_video_index'] = selected_video_index
    preview_root = join(config.DOWNLOAD_DIRECTORY, '_sony_previews')
    preview_dir = join(preview_root, f"sony_preview_{uid}_{secrets.token_hex(4)}")
    os.makedirs(preview_dir, exist_ok=True)
    preview_path = join(preview_dir, 'preview.jpg')
    if not await _capture_live_preview(url, preview_path):
        shutil.rmtree(preview_dir, ignore_errors=True)
        return await _sony_error('❌ **Unable to capture live stream preview.**')
    caption = (
        f"🎬 **Stream Preview**\\n\\n"
        f"📺 **Channel:** `{_sony_display_name(name)}`\\n\\n"
        "🖼️ Screenshot captured from the live stream.\\n\\n" + _audio_text(session)
    )
    try:
        await query.message.delete()
    except Exception:
        pass
    process_message = await client.send_photo(query.message.chat.id, photo=preview_path, caption=caption, reply_markup=_audio_keyboard(session))
    session['process_message'] = process_message
    session['preview_path'] = preview_path
    shutil.rmtree(preview_dir, ignore_errors=True)

def _rec_actor_id(*args) -> int:
    # Supports both:
    #   _rec_actor_id(message)
    #   Pyrogram handler style: _rec_actor_id(client, message)
    if len(args) == 1:
        message = args[0]
    elif len(args) >= 2:
        message = args[1]
    else:
        return 0

    user = getattr(message, "from_user", None)

    if user is not None and getattr(user, "id", None) is not None:
        return int(user.id)

    chat = getattr(message, "chat", None)

    if chat is not None and getattr(chat, "id", None) is not None:
        return int(chat.id)

    return 0


@app.on_message(filters.command("rec"))
async def rec_command(client, message: Message):
    # Delete the user's /rec command immediately (0s).
    try:
        await message.delete()
    except Exception:
        pass

    if not await _verification_required(message):
        return

    """
    /rec has two modes:

    Direct URL:
        /rec <URL> <DURATION> <FILENAME>
        Filename is REQUIRED.

    Channel:
        /rec <CHANNEL> <DURATION> [FILENAME]
        /rec <CHANNEL> <VARIANT> <DURATION> [FILENAME]
        Filename is OPTIONAL; DEFAULT_FILENAME is used when omitted.
    """
    raw_text=(getattr(message,"text","") or "").strip()
    try:
        raw_args=shlex.split(raw_text)
    except Exception:
        raw_args=raw_text.split()
    if raw_args and str(raw_args[0]).lstrip("/").casefold()=="rec":
        args=raw_args[1:]
    else:
        args=raw_args
        if not bot_settings.get("no_need_rec",False):
            return await message.reply_text("❌ **No need /rec is currently OFF.**")

    playlist_name = None
    playlist_group = None
    force_16_9 = False
    # Accept flags in any order: /rec -16:9 -TPlay Pogo 01 00:00:30
    while args:
        flag = str(args[0]).strip().casefold()
        if flag == "-16:9":
            force_16_9 = True
            args = args[1:]
            continue
        if flag in PLAYLIST_ALIASES:
            playlist_name = PLAYLIST_ALIASES[flag]
            args = args[1:]
            continue
        if flag.startswith("-"):
            # FW groups also work as compact flags, for example:
            # /rec -kids Pogo Hindi 00:00:30
            group_items = await _playlist_items("VIP")
            group = _playlist_group_for_flag(flag, group_items)
            if group:
                playlist_name = "VIP"
                playlist_group = group
                args = args[1:]
                continue
        break
    if len(args) < 2:
        return await message.reply_text("❌ Format: `/rec -FW Pogo 00:00:30`")

    # Optional /rec output mode: /rec -16:9 <SOURCE> <DURATION> <FILENAME>
    # The flag forces FFmpeg to scale directly to 1920x1080. It does not add
    # a canvas/padding, so no black bars are introduced.
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Direct URL:**\n"
            "```\n/rec <LINK> <DURATION> <FILENAME>\n```\n"
            "Example: `/rec https://example.com/stream 00:00:30 MyVideo`\n\n"
            "📺 **Channel:**\n"
            "```\n/rec <CHANNEL> <DURATION> [FILENAME]\n```\n"
            "Example: `/rec SonyYay 01:00:00 MyVideo`\n"
            "Filename can be omitted for channels."
        )

    uid = _rec_actor_id(message)
    if uid is None:
        return await message.reply_text("❌ **Unable to identify the recording session.**")
    # A user may have up to MAX_RECORDINGS_PER_USER simultaneous recordings.
    # A setup session counts as one slot, but an existing recording must NOT
    # block a new /rec request. This is intentionally based on processing_tasks
    # rather than user_tasks, because user_tasks stores only legacy/latest state.
    active_count = _active_recording_count(uid)
    setup_count = 1 if uid in rec_sessions else 0
    if active_count + setup_count >= MAX_RECORDINGS_PER_USER:
        return await message.reply_text(
            f"❌ **Recording Limit Reached!**\n\n"
            f"You already have `{active_count + setup_count}` active recording/setup(s).\n"
            f"Maximum allowed: `{MAX_RECORDINGS_PER_USER}`."
        )

    # Playlist channel names may contain spaces, e.g. `POGO HINDI`.
    # Find the duration token first, then treat everything before it as the channel name.
    # Duration is always the first strict HH:MM:SS token. Everything before it
    # belongs to the channel name, so names like POGO HINDI work without quotes.
    duration_index = next(
        (i for i, token in enumerate(args)
         if re.fullmatch(r"\d+:\d{2}:\d{2}", str(token).strip())),
        None,
    )
    if playlist_name and duration_index is not None and duration_index >= 1:
        before_duration = [str(x).strip() for x in args[:duration_index]]
        # Optional playlist variant: Pogo 01 00:00:30 -> channel=Pogo, variant=01
        if len(before_duration) >= 2 and before_duration[-1].isdigit():
            before_duration = before_duration[:-1]
        source_name = " ".join(before_duration).strip()
        args = [source_name, args[duration_index], *args[duration_index + 1:]]
    else:
        source_name = args[0].strip()
    raw_source_name = source_name
    if playlist_name:
        playlist_items = await _playlist_items(playlist_name)
        if playlist_group:
            playlist_items = [
                item for item in playlist_items
                if str(item[2]).casefold() == playlist_group.casefold()
            ]
        def _channel_key(value):
            return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

        wanted_key = _channel_key(source_name)
        match = next(((title, url) for title, url, _group in playlist_items
                      if _channel_key(title) == wanted_key), None)
        if not match:
            match = next(((title, url) for title, url, _group in playlist_items
                          if wanted_key and (_channel_key(title) in wanted_key or wanted_key in _channel_key(title))), None)
        if not match and wanted_key:
            wanted_words = set(re.findall(r"[a-z0-9]+", str(source_name).casefold()))
            match = next(((title, url) for title, url, _group in playlist_items
                          if wanted_words and wanted_words.issubset(set(re.findall(r"[a-z0-9]+", str(title).casefold())))), None)
        if not match and wanted_key:
            # Extra fallback: match common playlist suffixes such as HINDI/HD/SD.
            def _base_key(value):
                key = _channel_key(value)
                return re.sub(r"(?:hindi|english|tamil|telugu|malayalam|kannada|marathi|hd|sd)$", "", key)
            base_wanted = _base_key(source_name)
            if base_wanted:
                match = next(((title, url) for title, url, _group in playlist_items
                              if _base_key(title) == base_wanted), None)
        if not match:
            scope = f"{playlist_name} / {playlist_group}" if playlist_group else playlist_name
            examples = ", ".join(title for title, _url, _group in playlist_items[:8])
            return await message.reply_text(f"❌ Channel `{source_name}` not found in {scope} playlist.\n\nAvailable examples: {examples or 'Playlist empty'}")
        source_name, url = match
        is_playlist_url = True
    else:
        is_playlist_url = False

    # Channel mode supports both:
    #   /rec Nick 00:00:30 ls
    #   /rec pogo 3 00:00:30 ls
    # The optional numeric value after the channel name is treated as a
    # channel/stream variant and is resolved through Channel.py.
    channel_variant = None
    if (
        not source_name.lower().startswith(("http://", "https://"))
        and len(args) >= 3
        and str(args[1]).strip().isdigit()
        and time_to_seconds(args[2].strip()) > 0
    ):
        channel_variant = str(args[1]).strip()
        duration = args[2].strip()
        filename_start_index = 3
    else:
        duration = args[1].strip()
        filename_start_index = 2

    if time_to_seconds(duration) <= 0:
        return await message.reply_text("❌ Invalid duration. Use `HH:MM:SS`.")

    # Direct HTTP(S) URL mode.
    is_direct_url = source_name.lower().startswith(("http://", "https://"))

    if is_playlist_url:
        raw_filename = " ".join(args[2:]).strip() if len(args) > 2 else config.DEFAULT_FILENAME
    elif is_direct_url:
        # Direct URLs MUST have an explicit filename.
        if len(args) < 3:
            return await message.reply_text(
                "❌ **Filename is required for direct URLs.**\n\n"
                "📌 **Correct Usage:**\n"
                "```\n/rec <LINK> <DURATION> <FILENAME>\n```\n"
                "💡 Example:\n"
                "`/rec https://example.com/stream 00:00:30 MyVideo`"
            )

        raw_filename = " ".join(args[2:]).strip()
        if not raw_filename:
            return await message.reply_text(
                "❌ **Filename is required for direct URLs.**"
            )

        url = source_name

    else:
        # Channel mode: filename is optional.
        raw_filename = (
            " ".join(args[filename_start_index:]).strip()
            if len(args) > filename_start_index
            else config.DEFAULT_FILENAME
        )

        # Sony LIV special: a base Sony name resolves to the HD source when
        # both SD and HD entries exist. The menu still shows one channel name.
        if channel_variant is None and _is_sony_source_name(source_name):
            preferred_sony = _sony_preferred_channel_name(source_name)
            if preferred_sony:
                source_name = preferred_sony

        # Channel URL is automatically obtained from Channel.py.
        if channel_variant is not None:
            # Channel.py installations commonly expose variants as either
            # "channel 3" or "channel_3". Try the explicit variant key first.
            variant_candidates = [
                f"{source_name} {channel_variant}",
                f"{source_name}_{channel_variant}",
                f"{source_name}-{channel_variant}",
            ]
            url = None
            for candidate in variant_candidates:
                url = await _resolve_channel_source(candidate)
                if url:
                    break

            # If Channel.py exposes get_channel_url(name, variant), support
            # that form too without breaking the normal one-argument API.
            if not url:
                try:
                    candidate_url = get_channel_url(source_name, int(channel_variant))
                    if isinstance(candidate_url, str) and candidate_url.strip().lower().startswith(("http://", "https://")):
                        url = candidate_url.strip()
                except TypeError:
                    pass
                except Exception as e:
                    LOG.warning("Channel variant lookup failed for '%s %s': %s", source_name, channel_variant, e)
        else:
            url = await _resolve_channel_source(source_name)

        if not url:
            return await message.reply_text(
                "❌ **Next channel link not found.**\n\n"
                f"📺 **Channel:** `{source_name}`\n\n"
                "Use `/Channel` to view available channels."
            )

    raw_filename = _safe_filename(raw_filename)

    detect_msg = await message.reply_text("🔍 **Auto Detecting video/audio streams...**")
    # Temporary detection message is removed automatically after 10 seconds.
    asyncio.create_task(_delete_message_after(detect_msg, 10))
    heights, lang_indexes, selected_video_index, probe_error = await _probe_streams(url)

    # A failed/unreachable URL must show the dedicated invalid-link popup.
    # Do not misreport a connection/HTTP/probe failure as 'no audio tracks'.
    if lang_indexes is None:
        status_match = re.search(r'\b([45]\d{2})\b', probe_error or '')
        status_code = status_match.group(1) if status_match else None
        if status_code and status_code.startswith('4'):
            error_text = (
                "❌ **Stream provider rejected the request**\n\n"
                f"The VIP CDN returned HTTP `{status_code}` to the recording VM.\n"
                "The playlist headers were sent, but this source is being blocked "
                "from the bot's network/client path."
            )
        else:
            error_text = (
                "❌ **Stream unavailable**\n\n"
                "FFprobe could not open the VIP stream with the playlist headers."
            )
        invalid_msg = await message.reply_text(
            error_text
        )
        # Keep the invalid-link popup visible for 30 seconds, then remove it.
        asyncio.create_task(_delete_message_after(invalid_msg, 30))
        return

    detected_audio = {lang for lang, indexes in lang_indexes.items() if indexes}
    if not detected_audio:
        return await message.reply_text("❌ **No real audio tracks detected by FFprobe.**")

    # Termux/Android may not allow writing to /tmp.
    # Keep temporary preview files inside the bot's configured download directory.
    preview_root = join(config.DOWNLOAD_DIRECTORY, "_rec_previews")
    preview_dir = join(preview_root, f"rec_preview_{uid}_{secrets.token_hex(4)}")
    os.makedirs(preview_dir, exist_ok=True)
    preview_path = join(preview_dir, "preview.jpg")
    if not await _capture_live_preview(url, preview_path):
        shutil.rmtree(preview_dir, ignore_errors=True)
        return await message.reply_text("❌ **Unable to capture live preview from this stream.**")

    callback_token = secrets.token_hex(8)
    session = {
        "message": message,
        "actor_id": uid,
        "chat_id": getattr(getattr(message, "chat", None), "id", None),
        "url": url,
        "raw_source_name": raw_source_name,
        "callback_token": callback_token,
        "timestamp": duration,
        "raw_filename": raw_filename,
        "force_16_9": force_16_9,
        "quality": "auto",  # Sony quality is automatic; highest HLS variant is selected
        "audio": set(detected_audio),
        "lang_indexes": lang_indexes,
        "watermark": "off",
        "step": "audio",
        "detected_heights": heights,
        "selected_video_index": selected_video_index,
        "sony_quality_mode": ("hd" if str(raw_source_name).strip().casefold().endswith(" hd") else "sd" if str(raw_source_name).strip().casefold().endswith(" sd") else "auto"),
        "preview_path": preview_path,
    }

    caption = "🎬 **Stream Preview**\n\n🖼️ Screenshot captured from the live stream.\n\n" + _audio_text(session)
    process_message = await client.send_photo(
        message.chat.id, photo=preview_path, caption=caption,
        reply_markup=_audio_keyboard(session)
    )
    session["process_message"] = process_message
    rec_sessions[uid] = session
    rec_session_tokens[callback_token] = uid
    shutil.rmtree(preview_dir, ignore_errors=True)


async def handle_record(client, message, selection=None):
    # Anonymous admins have no from_user. The setup stores actor_id, which is
    # the allowed group chat ID for anonymous-admin recordings.
    user_obj = getattr(message, "from_user", None)
    user_id = selection.get("actor_id") if isinstance(selection, dict) else None
    if user_id is None and user_obj is not None:
        user_id = getattr(user_obj, "id", None)
    if user_id is None:
        chat_obj = getattr(message, "chat", None)
        user_id = getattr(chat_obj, "id", None)
    if user_id is None:
        raise ValueError("Unable to identify recording actor")
    msg = None
    save_dir = None
    ffmpeg_process = None
    video_path = None
    thumb_path = None
    preview_task = None

    try:
        if selection is None:
            raise Exception("Recording selection was not provided")
        url = selection["url"]
        timestamp = selection["timestamp"]
        raw_filename = selection["raw_filename"]
        recording_start_dt = datetime.now(tz)
        recording_start_label = recording_start_dt.strftime("%I:%M:%S%p")
        recording_date_label = recording_start_dt.strftime("%d-%m-%Y")
        quality_label = selection["quality"] if selection["quality"] != "auto" else "Auto"
        selected_count = len(selection.get("audio", []))
        audio_type = {
            1: "Single", 2: "Dual", 3: "Triple", 4: "Quad"
        }.get(selected_count, "Multi")
        filename = (
            f"{raw_filename.strip()}.[{recording_date_label}].[{recording_start_label}]."
            f"{quality_label}.WEB-DL.{audio_type}.UNK.-namebot.mkv"
        )
        process_message = selection.get("process_message")
        save_dir = join(config.DOWNLOAD_DIRECTORY, str(int(time.time())))
        os.makedirs(save_dir, exist_ok=True)
        video_path = join(save_dir, filename)

        task_id = selection.get("task_id") or secrets.token_hex(8)
        active_recordings_by_actor.setdefault(user_id, set()).add(task_id)
        user_tasks[user_id] = task_id
        user_status[user_id] = {
            "id": task_id,
            "filename": raw_filename.strip(),
            "target": timestamp,
            "progress": "00:00:00",
            "save_dir": save_dir,
            "username": getattr(getattr(message, "from_user", None), "username", None) or "anonymous",
            "user_id": user_id,
            "process_message": process_message,
            "status": "Recording",
            "start_ts": time.time(),
            "speed": "Calculating...",
            "remaining": timestamp,
            "cancelled": False,
            "output_filename": filename,
        }
        processing_tasks[task_id] = {
            "owner_id": user_id,
            "task_id": task_id,
            "filename": filename,
            "status": "Recording",
            "start_ts": time.time(),
            "target_seconds": time_to_seconds(timestamp),
            "process": None,
            "updater": None,
            "preview_task": None,
            "save_dir": save_dir,
            "video_path": video_path,
            "message": process_message,
            "user_message": message,
            "selection": selection,
        }

        recording_start = time.time()
        duration = time_to_seconds(timestamp)

        def processing_keyboard(kind="recording"):
            if kind == "upload":
                first = InlineKeyboardButton("📦 Uploading", callback_data=f"progress:{task_id}")
            else:
                first = InlineKeyboardButton("⚡ Rec Progress", callback_data=f"progress:{task_id}")
            return InlineKeyboardMarkup([[
                first
            ]])

        async def update_recording_progress():
            while task_id in active_recordings_by_actor.get(user_id, set()) and task_id in processing_tasks:
                state = processing_tasks[task_id]
                if state.get("cancelled"):
                    break
                elapsed = max(time.time() - recording_start, 0)
                remaining = max(duration - elapsed, 0)
                pct = min((elapsed / duration) * 100, 100) if duration else 0
                if user_status.get(user_id, {}).get("id") == task_id:
                    user_status[user_id]["progress"] = TimeFormatter(int(elapsed * 1000))
                    user_status[user_id]["remaining"] = TimeFormatter(int(remaining * 1000))
                state["progress"] = pct
                state["elapsed"] = elapsed
                state["remaining"] = remaining
                await asyncio.sleep(2)

        progress_task = asyncio.create_task(update_recording_progress())
        progress_tasks[user_id] = progress_task
        processing_tasks[task_id]["updater"] = progress_task
        if process_message:
            try:
                await process_message.edit_caption(
                    f"🎬 **Processing Video...**\n\n"
                    f"📄 **File:**\n`{filename}`\n\n"
                    f"⚡ **Speed:**\nCalculating...\n\n"
                    f"Status:\nRecording",
                    reply_markup=processing_keyboard("recording")
                )
            except Exception:
                pass

        # Build FFmpeg args as a list. Never concatenate untrusted user input into a shell command.
        args = [
            "ffmpeg", "-y", "-probesize", "10000000", "-analyzeduration", "15000000",
            *_stream_input_args(url),
        ]

        is_sony = _is_sony_source_name(selection.get("raw_source_name", ""))
        sony_video_index = (selection.get("quality_video_index") if is_sony and selection.get("quality_video_index") is not None
                            else selection.get("selected_video_index") if is_sony else None)

        # Select video and the user-selected audio languages.
        # Sony HLS master playlists expose multiple video programs. Never use
        # 0:v:0 for Sony because that can select the 144p first variant.
        # FFprobe has already identified the highest-resolution stream index.
        if is_sony and sony_video_index is not None:
            args += ["-map", f"0:{int(sony_video_index)}"]
        else:
            args += ["-map", "0:v:0"]
        selected_audio_indexes = []
        lang_indexes = selection.get("lang_indexes", {})
        for audio_key in selection["audio"]:
            if audio_key.startswith("UNKNOWN:"):
                try:
                    unknown_pos = int(audio_key.split(":", 1)[1])
                    unknown_indexes = lang_indexes.get("UNKNOWN", [])
                    if 0 <= unknown_pos < len(unknown_indexes):
                        selected_audio_indexes.append(int(unknown_indexes[unknown_pos]))
                except (ValueError, TypeError):
                    continue
            else:
                selected_audio_indexes.extend(lang_indexes.get(audio_key, []))

        if not selected_audio_indexes:
            raise Exception("Selected audio track(s) were not found in the detected stream metadata.")
        selected_audio_indexes = sorted(set(int(idx) for idx in selected_audio_indexes))
        for idx in selected_audio_indexes:
            args += ["-map", f"0:{idx}"]

        # Dynamic audio metadata: first 3 selected real tracks share the Premium handler;
        # remaining tracks use the detected language name.
        index_to_lang = {}
        for lang, indexes in selection.get("lang_indexes", {}).items():
            for idx in indexes:
                index_to_lang[int(idx)] = lang
        for output_index, source_index in enumerate(selected_audio_indexes):
            lang_name = index_to_lang.get(source_index, "UNKNOWN")
            handler = _audio_title_for_count(len(selected_audio_indexes))
            lang_code = next((code for code in LANG_CODES.get(lang_name, set()) if len(code) == 3), None)
            args += [f"-metadata:s:a:{output_index}", f"handler_name={handler}"]
            args += [f"-metadata:s:a:{output_index}", f"title={handler}"]
            if lang_code:
                args += [f"-metadata:s:a:{output_index}", f"language={lang_code}"]

        quality = selection["quality"]

        # Automatic quality detector:
        # - Sony LIV: automatically target the highest detected source quality,
        #   preferring 1080p whenever a 1080p stream is available.
        # - Other channels: keep the existing Auto behavior unchanged.
        if quality == "auto" and is_sony:
            detected_heights = selection.get("detected_heights") or []
            numeric_heights = []
            for h in detected_heights:
                try:
                    numeric_heights.append(int(h))
                except (TypeError, ValueError):
                    pass
            max_height = max(numeric_heights, default=0)
            sony_quality_mode = str(selection.get("sony_quality_mode", "auto")).casefold()
            if sony_quality_mode == "hd":
                # Sony LIV HD: choose the highest master-playlist variant;
                # 1080p is selected automatically whenever available.
                if max_height >= 1080:
                    quality = "1080"
                elif max_height >= 720:
                    quality = "720"
                elif max_height >= 576:
                    quality = "576"
                elif max_height >= 480:
                    quality = "480"
                else:
                    quality = "auto"
            elif sony_quality_mode == "sd":
                # Sony LIV SD: stay at or below SD range; never promote an
                # SD catalog entry to a 720p/1080p output.
                sd_heights = [h for h in numeric_heights if h <= 576]
                max_sd = max(sd_heights, default=0)
                if max_sd >= 576:
                    quality = "576"
                elif max_sd >= 480:
                    quality = "480"
                elif max_sd >= 360:
                    quality = "360" if "360" in QUALITY_LABELS else "auto"
                else:
                    quality = "auto"
            else:
                if max_height >= 1080:
                    quality = "1080"
                elif max_height >= 720:
                    quality = "720"
                elif max_height >= 576:
                    quality = "576"
                elif max_height >= 480:
                    quality = "480"
                else:
                    quality = "auto"

        wm = "off"  # Watermark feature disabled
        vf = None
        target_height = {"360": 360, "480": 480, "576": 576, "720": 720, "1080": 1080}.get(quality)

        # /rec -16:9 explicitly requests a direct 1920x1080 output frame.
        # Do NOT use pad/force_original_aspect_ratio here: the source is
        # scaled directly to 1920x1080, so no black padding is added.
        force_16_9 = bool(selection.get("force_16_9", False))
        if force_16_9:
            scale = "scale=1920:1080,setsar=1"
        elif target_height:
            target_width = {360: 640, 480: 854, 576: 1024, 720: 1280, 1080: 1920}[target_height]
            scale = f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease"
        elif wm in ("wm1", "wm2", "text"):
            scale = "scale=1920:1080:force_original_aspect_ratio=decrease"
        else:
            scale = None
        if scale:
            if wm == "wm1":
                vf = scale + ",drawtext=text='Join Our Telegram - AnimeCartoonPremium':fontfile=/system/fonts/Roboto-Regular.ttf:fontsize=24:fontcolor=white:x=(w-tw)/2:y=h-th-140:shadowcolor=black:shadowx=2:shadowy=2:enable='between(t,10,50)+between(t,1200,1260)'"
            elif wm == "wm2":
                vf = scale + ",drawtext=text='Join Our Telegram - AnimeCartoonPremium':fontfile=/system/fonts/Roboto-Regular.ttf:fontsize=24:fontcolor=white:x=(w-tw)/2:y=130:shadowcolor=black:shadowx=2:shadowy=2"
            elif wm == "text":
                text = selection.get("watermark_text", "")
                vf = scale + f",drawtext=text='{text}':fontfile=/system/fonts/Roboto-Regular.ttf:fontsize=24:fontcolor=white:x=(w-tw)/2:y=130:shadowcolor=black:shadowx=2:shadowy=2"
            else:
                vf = scale

        if vf:
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-aspect", "16:9", "-threads", "2", "-movflags", "+faststart"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-t", timestamp, video_path]

        # Auto quality: keep the source video dimensions/aspect ratio unchanged.
        # No 16:9 canvas and no black bars are added.
        if wm == "off" and quality == "auto" and not force_16_9:
            auto_video_map = (f"0:{int(sony_video_index)}" if is_sony and sony_video_index is not None else "0:v:0")
            args = [
                "ffmpeg", "-y", "-probesize", "10000000", "-analyzeduration", "15000000",
                *_stream_input_args(url), "-map", auto_video_map,
            ]
            for idx in sorted(set(selected_audio_indexes)):
                args += ["-map", f"0:{idx}"]
            for output_index, source_index in enumerate(sorted(set(selected_audio_indexes))):
                lang_name = index_to_lang.get(source_index, "UNKNOWN")
                handler = _audio_title_for_count(len(selected_audio_indexes))
                args += [f"-metadata:s:a:{output_index}", f"handler_name={handler}", f"-metadata:s:a:{output_index}", f"title={handler}"]
                lang_code = next((code for code in LANG_CODES.get(lang_name, set()) if len(code) == 3), None)
                if lang_code:
                    args += [f"-metadata:s:a:{output_index}", f"language={lang_code}"]
            args += ["-c:v", "copy", "-c:a", "copy", "-t", timestamp, video_path]

        async def preview_updater():
            preview_dir = join(save_dir, "live_preview")
            os.makedirs(preview_dir, exist_ok=True)
            try:
                while user_id in user_tasks and user_tasks.get(user_id) == task_id and user_id not in cancelled_users:
                    await asyncio.sleep(10)
                    if user_id not in user_tasks or user_id in cancelled_users:
                        break
                    latest = join(preview_dir, f"preview_{int(time.time())}.jpg")
                    if await _capture_live_preview(url, latest):
                        try:
                            if process_message:
                                elapsed = max(time.time() - recording_start, 0)
                                remaining = max(duration - elapsed, 0)
                                preview_caption = (
                                    f"🎬 **Processing Video...**\n\n"
                                    f"📄 **File:**\n`{filename}`\n\n"
                                    f"⚡ **Speed:**\nCalculating...\n\n"
                                    f"Status:\nRecording"
                                )
                                await process_message.edit_media(
                                    InputMediaPhoto(media=latest, caption=preview_caption),
                                    reply_markup=processing_keyboard("recording")
                                )
                        except Exception as e:
                            LOG.debug("Preview edit failed: %s", e)
                        try:
                            os.remove(latest)
                        except OSError:
                            pass
            finally:
                shutil.rmtree(preview_dir, ignore_errors=True)

        preview_task = asyncio.create_task(preview_updater())
        processing_tasks[task_id]["preview_task"] = preview_task

        args = _apply_provider_headers(args, url, selection.get("provider"))
        ffmpeg_process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        user_ffmpeg_pids[user_id] = ffmpeg_process.pid
        processing_tasks[task_id]["process"] = ffmpeg_process
        LOG.info("Started FFmpeg process %s for user %s", ffmpeg_process.pid, user_id)
        stdout, stderr = await ffmpeg_process.communicate()
        retcode = ffmpeg_process.returncode
        user_ffmpeg_pids.pop(user_id, None)
        if preview_task:
            preview_task.cancel()
            try:
                await preview_task
            except asyncio.CancelledError:
                pass
            preview_task = None
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
            progress_tasks.pop(user_id, None)

        was_cancelled = user_id in cancelled_users
        if retcode != 0 and not was_cancelled:
            raise Exception(f"🚫 FFmpeg Error:\n{stderr.decode(errors='ignore')[-3500:]}")
        if was_cancelled:
            # Do not delete the partial output. It must continue to the upload step.
            if process_message:
                try:
                    await process_message.edit_caption(
                        "⚠️ **Partial recording sent.**\n\n"
                        "Server copy auto-deletes in 3 hours.\n\n"
                        "📤 **Uploading...**\n"
                        "[□□□□□□□□□□] 0%\n\n"
                        "Please wait...",
                        reply_markup=None
                    )
                except Exception:
                    try:
                        await process_message.edit_text(
                            "⚠️ **Partial recording sent.**\n\n"
                            "Server copy auto-deletes in 3 hours.\n\n"
                            "📤 **Uploading...**\n"
                            "[□□□□□□□□□□] 0%\n\n"
                            "Please wait...",
                            reply_markup=None
                        )
                    except Exception:
                        pass

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            raise Exception("🚫 No video file created or file is empty")

        msg = process_message
        if msg:
            processing_tasks[task_id]["status"] = "Uploading"
            try:
                await msg.edit_caption(
                    f"📦 **Uploading:**\n`{filename}`\n\n⏳ Preparing upload...",
                    reply_markup=processing_keyboard("upload")
                )
            except Exception:
                pass

        dur = await get_duration_ffmpeg(video_path)
        if dur == 0:
            dur = time_to_seconds(timestamp)
        fixed_video_path = join(save_dir, f"fixed_{filename}")
        fix_args = ["ffmpeg", "-y", "-i", video_path, "-map", "0", "-c", "copy",
                    "-metadata", f"creation_time={time.strftime('%Y-%m-%dT%H:%M:%S')}", fixed_video_path]
        fix_process = await asyncio.create_subprocess_exec(*fix_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, fix_err = await fix_process.communicate()
        if fix_process.returncode == 0:
            os.replace(fixed_video_path, video_path)
        else:
            LOG.warning("Metadata fix failed: %s", fix_err.decode(errors="ignore")[-1000:])

        rand_sec = random.randint(5, max(dur - 5, 6))
        thumb_path = join(save_dir, "thumb.jpg")
        thumb_args = ["ffmpeg", "-y", "-ss", str(rand_sec), "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path]
        thumb_process = await asyncio.create_subprocess_exec(*thumb_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await thumb_process.communicate()

        # Final output name includes actual recording end time.
        recording_end_label = datetime.now(tz).strftime("%I:%M:%S%p")
        final_filename = (
            f"{raw_filename.strip()}.[{recording_date_label}]."
            f"[{recording_start_label}-{recording_end_label}]."
            f"{quality_label}.WEB-DL.{audio_type}.UNK.-namebot.mkv"
        )
        final_video_path = join(save_dir, final_filename)
        if video_path != final_video_path and os.path.exists(video_path):
            os.replace(video_path, final_video_path)
            video_path = final_video_path
            filename = final_filename
            processing_tasks[task_id]["filename"] = filename
            if user_id in user_status:
                user_status[user_id]["output_filename"] = filename

        # Upload through the existing system.  The completion caption uses the
        # actual final filename and task ID; bot username is resolved automatically.
        try:
            _bot_me = await app.get_me()
            _bot_username = getattr(_bot_me, "username", None) or "unknown"
        except Exception:
            _bot_username = "unknown"

        caption = (
            f"🎬 **{filename}**\n\n"
            f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
            f"📁 **Format:** MKV\n"
            f"🆔 **Task ID:** `{task_id}`\n"
            f"🤖 **Bot:** @{_bot_username}\n\n"
            f"{'⚠️ _Partial recording sent. Server copy auto-deletes in 3 hours._' if was_cancelled else '✅ _Recording completed successfully!_'}"
        )
        start_time = time.time()
        processing_tasks[task_id]["status"] = "Uploading"
        processing_tasks[task_id]["upload_start"] = start_time
        # Keep the requested partial-upload status visible while Telegram uploads.
        if was_cancelled and msg:
            try:
                if msg.photo:
                    await msg.edit_caption(
                        "⚠️ **Partial recording sent.**\n\n"
                        "Server copy auto-deletes in 3 hours.\n\n"
                        "📤 **Uploading...**\n"
                        "[□□□□□□□□□□] 0%\n\n"
                        "Please wait...",
                        reply_markup=None
                    )
                else:
                    await msg.edit_text(
                        "⚠️ **Partial recording sent.**\n\n"
                        "Server copy auto-deletes in 3 hours.\n\n"
                        "📤 **Uploading...**\n"
                        "[□□□□□□□□□□] 0%\n\n"
                        "Please wait..."
                    )
            except Exception:
                pass

        await message.reply_video(
            video=video_path, caption=caption, duration=dur,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            progress=progress_for_pyrogram,
            progress_args=(message, start_time, msg, save_dir, was_cancelled)
        )
        if save_dir and os.path.exists(save_dir):
            if was_cancelled:
                # Telegram has the file; retain the server copy for exactly 3 hours.
                schedule_partial_server_cleanup(save_dir)
            else:
                # Normal completed recordings keep the existing immediate cleanup behavior.
                shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error("Error in handle_record: %s", e)
        try:
            err_text = str(e)
            if len(err_text) > 1500:
                err_text = err_text[:1500] + "... [truncated]"
            # Keep any partial output so it can be inspected/recovered.
            # Do not delete save_dir on processing failure.
            target_msg = msg or process_message
            if target_msg:
                partial_name = raw_filename.strip()
                try:
                    if save_dir and os.path.isdir(save_dir):
                        candidates = [
                            x for x in os.listdir(save_dir)
                            if os.path.isfile(os.path.join(save_dir, x))
                        ]
                        if candidates:
                            # Prefer the newest/largest media-like file when available.
                            media = [x for x in candidates if x.lower().endswith((".mkv", ".mp4", ".ts", ".m4v"))]
                            if media:
                                partial_name = max(
                                    media,
                                    key=lambda x: os.path.getsize(os.path.join(save_dir, x))
                                )
                            else:
                                partial_name = candidates[0]
                except Exception:
                    pass
                failure_text = (
                    f"❌ **Processing Failed**\n"
                    f"⚠️ **Partial Output Available**\n"
                    f"📄 **File:**\n`{partial_name}`\n"
                    f"🆔 **Task ID:** `{task_id}`"
                )
                try:
                    await target_msg.edit_caption(failure_text, reply_markup=None)
                except Exception:
                    try:
                        await target_msg.edit_text(failure_text, reply_markup=None)
                    except Exception:
                        pass
        except Exception as exc:
            LOG.error("Failed to handle recording error: %s", exc)
    finally:
        if user_status.get(user_id, {}).get("id") == task_id:
            user_status.pop(user_id, None)
        if user_tasks.get(user_id) == task_id:
            user_tasks.pop(user_id, None)
        if user_ffmpeg_pids.get(user_id) == task_id:
            user_ffmpeg_pids.pop(user_id, None)
        if progress_tasks.get(user_id) == task_id:
            progress_tasks.pop(user_id, None)
        processing_tasks.pop(task_id, None)
        actor_tasks = active_recordings_by_actor.get(user_id)
        if actor_tasks is not None:
            actor_tasks.discard(task_id)
            if not actor_tasks:
                active_recordings_by_actor.pop(user_id, None)
        cancelled_users.discard(user_id)


async def _delete_partial_server_copy_later(save_dir, delay=PARTIAL_SERVER_COPY_TTL_SECONDS):
    """Delete a cancelled recording's server copy after the configured TTL."""
    try:
        await asyncio.sleep(delay)
        if save_dir and os.path.exists(save_dir):
            shutil.rmtree(save_dir, ignore_errors=True)
            LOG.info("Deleted expired partial server copy: %s", save_dir)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.warning("Failed to delete partial server copy %s: %s", save_dir, exc)


def schedule_partial_server_cleanup(save_dir):
    """Schedule non-blocking cleanup of a partial server copy after 3 hours."""
    if not save_dir or not os.path.exists(save_dir):
        return None
    task = asyncio.create_task(_delete_partial_server_copy_later(save_dir))
    _partial_cleanup_tasks.add(task)
    task.add_done_callback(_partial_cleanup_tasks.discard)
    return task


async def progress_for_pyrogram(current, total, message, start, msg, save_dir=None, was_cancelled=False):
    if not msg or total <= 0:
        return

    now = time.time()
    elapsed = max(now - start, 0.001)
    percentage = min(current * 100 / total, 100)
    speed = current / elapsed
    remaining = (total - current) / speed if speed > 0 else 0
    uploaded_mb = current / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    speed_mb = speed / (1024 * 1024)

    task_id = None
    for tid, state in processing_tasks.items():
        if state.get("message") is msg:
            task_id = tid
            state["status"] = "Uploading"
            state["upload_progress"] = percentage
            state["upload_speed"] = speed_mb
            state["upload_remaining"] = remaining
            state["uploaded_mb"] = uploaded_mb
            state["total_mb"] = total_mb
            break

    bar_len = 10
    filled = int(bar_len * percentage / 100)
    bar = "🟩" * filled + "⬜" * (bar_len - filled)
    filename = "output_file"
    if task_id and task_id in processing_tasks:
        filename = processing_tasks[task_id].get("filename", filename)

    text = (
        f"📦 **Uploading:**\n`{filename}`\n\n"
        f"[{bar}] {percentage:.2f}%\n"
        f"{uploaded_mb:.2f} MB of {total_mb:.2f} MB\n\n"
        f"Speed:\n{speed_mb:.2f} MB/s\n\n"
        f"Time Left:\n{int(remaining)}s"
    )

    try:
        if msg.photo:
            await msg.edit_caption(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📦 Uploading", callback_data=f"progress:{task_id}")
                ]]) if task_id else None
            )
        else:
            await msg.edit_text(text)
    except Exception:
        pass

    if current == total:
        if was_cancelled:
            completion = (
                "⚠️ **Partial recording sent.**\n\n"
                "Server copy auto-deletes in 3 hours.\n\n"
                f"`{filename}`"
            )
        else:
            completion = (
                "📦 **Upload Completed! Successfully!**\n\n"
                "🗑️ **Temporary files cleaned up!**\n\n"
                f"`{filename}`"
            )
        try:
            if msg.photo:
                await msg.edit_caption(completion, reply_markup=None)
            else:
                await msg.edit_text(completion, reply_markup=None)
        except Exception:
            pass

        async def delete_processing_message():
            await asyncio.sleep(20)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(delete_processing_message())


@app.on_callback_query(filters.regex(r"^progress:"))
async def task_progress_callback(client, query):
    task_id = query.data.split(":", 1)[1]
    state = processing_tasks.get(task_id)
    if not state:
        return await _safe_callback_answer(query, "❌ Process is no longer active.", show_alert=True, cache_time=0)

    owner_id = state.get("owner_id")
    if owner_id is None or query.from_user.id != owner_id:
        return await _safe_callback_answer(
            query,
            "❌ This recording belongs to another user.",
            show_alert=True,
            cache_time=0
        )

    status = state.get("status", "Recording")
    username = query.from_user.username or "anonymous"
    user_id = query.from_user.id

    if status == "Uploading":
        pct = state.get("upload_progress", 0)
        speed = state.get("upload_speed", 0)
        remaining = state.get("upload_remaining", 0)
        up = state.get("uploaded_mb", 0)
        total = state.get("total_mb", 0)
        bar_len = 10
        bar = "🟩" * int(bar_len * pct / 100) + "⬜" * (bar_len - int(bar_len * pct / 100))
        text = (
            f"📦 Uploading\n\n{state.get('filename')}\n\n"
            f"[{bar}] {pct:.2f}%\n"
            f"Size {up:.2f} MB of {total:.2f} MB\n\n"
            f"Speed:\n{speed:.2f} MB/s\n\n"
            f"Time Left:\n{int(remaining)}s\n\n"
            f"👤 @{username}\n🆔 {user_id}"
        )
    else:
        elapsed = max(time.time() - state.get("start_ts", time.time()), 0)
        remaining = max(state.get("target_seconds", 0) - elapsed, 0)
        pct = min((elapsed / state["target_seconds"]) * 100, 100) if state.get("target_seconds") else 0
        bar_len = 10
        bar = "🟩" * int(bar_len * pct / 100) + "⬜" * (bar_len - int(bar_len * pct / 100))
        text = (
            f"📦 Rec\n\n{state.get('filename')}\n\n"
            f"[{bar}] {pct:.2f}%\n\n"
            f"Status:\n{status}\n\n"
            f"Elapsed:\n{TimeFormatter(int(elapsed * 1000))}\n"
            f"Remaining:\n{TimeFormatter(int(remaining * 1000))}\n"
            f"Speed:\nCalculating...\n\n"
            f"👤 @{username}\n🆔 {user_id}"
        )

    return await _safe_callback_answer(query, text[:190], show_alert=True, cache_time=0)


@app.on_callback_query(filters.regex(r"^cancel:"))
async def task_cancel_callback(client, query):
    task_id = query.data.split(":", 1)[1]
    state = processing_tasks.get(task_id)
    if not state:
        return await _safe_callback_answer(query, "❌ Process is no longer active.", show_alert=True, cache_time=0)

    owner_id = state.get("owner_id")
    if owner_id is None or query.from_user.id != owner_id:
        return await _safe_callback_answer(
            query,
            "❌ This recording belongs to another user.",
            show_alert=True,
            cache_time=0
        )

    await _safe_callback_answer(query, "Cancelling process...", show_alert=True, cache_time=0)
    state["cancelled"] = True
    cancelled_users.add(owner_id)

    updater = state.get("updater")
    if updater and not updater.done():
        updater.cancel()

    preview_task = state.get("preview_task")
    if preview_task and not preview_task.done():
        preview_task.cancel()

    process = state.get("process")
    if process and process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # Do not delete the partial recording here. handle_record() will upload it
    # and schedule server-side cleanup for 3 hours after successful upload.
    save_dir = state.get("save_dir")

    msg = state.get("message")
    if msg:
        try:
            if msg.photo:
                await msg.edit_caption(
                    "⚠️ **Partial recording sent.**\n\n"
                    "Server copy auto-deletes in 3 hours.\n\n"
                    "📤 **Uploading...**\n"
                    "[□□□□□□□□□□] 0%\n\n"
                    "Please wait...",
                    reply_markup=None
                )
            else:
                await msg.edit_text(
                    "⚠️ **Partial recording sent.**\n\n"
                    "Server copy auto-deletes in 3 hours.\n\n"
                    "📤 **Uploading...**\n"
                    "[□□□□□□□□□□] 0%\n\n"
                    "Please wait..."
                )
        except Exception:
            pass


async def runcmd(cmd: str) -> Tuple[int, str, str]:
    args = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


async def get_video_duration(input_file: str) -> int:
    try:
        parser = createParser(input_file)
        if not parser:
            return 0
        metadata = extractMetadata(parser)
        if not metadata or not metadata.has("duration"):
            return 0
        duration = metadata.get("duration")
        return int(duration.seconds)
    except Exception as e:
        LOG.warning(f"Hachoir failed: {e}")
        return 0


async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{input_file}"'
        retcode, out, err = await runcmd(cmd)
        if retcode == 0:
            return int(float(out.strip()))
    except Exception as e:
        LOG.warning(f"FFprobe failed: {e}")
    return 0


def time_to_seconds(time_str: str) -> int:
    """Convert a strict HH:MM:SS duration to seconds."""
    try:
        value = str(time_str).strip()
        match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", value)
        if not match:
            return 0
        h, m, s = map(int, match.groups())
        if m >= 60 or s >= 60:
            return 0
        return h * 3600 + m * 60 + s
    except (TypeError, ValueError):
        return 0


def TimeFormatter(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours:02}:{min_:02}:{sec:02}"
    else:
        return f"{min_:02}:{sec:02}"


# ============================================================================
# Video Tools Menu (v23)
# Shown automatically when a video/video-document is uploaded or forwarded.
# ============================================================================
VIDEO_TOOL_STATES = {}
WATERMARK_IMAGE_URL = "https://iili.io/CuMJCjn.md.png"
WATERMARK_MAX_SECONDS = 500
VIDEO_TOOL_DELETE_SECONDS = 300
VIDEO_TOOL_PRESET = str(getattr(config, "VIDEO_TOOL_PRESET", "ultrafast") or "ultrafast")
VIDEO_TOOL_THREADS = max(0, int(getattr(config, "VIDEO_TOOL_THREADS", 0) or 0))
VIDEO_TOOL_CRF = max(0, min(51, int(getattr(config, "VIDEO_TOOL_CRF", 23) or 23)))
VIDEO_TOOL_DEFAULT_TARGET_MB = max(
    1.0, float(getattr(config, "VIDEO_TOOL_TARGET_MB", 300) or 300)
)
VIDEO_TOOL_AUDIO_KBPS = max(
    32, int(getattr(config, "VIDEO_TOOL_AUDIO_KBPS", 120) or 120)
)


def _video_tool_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Audio Track", callback_data="vtool:audio")],
        [InlineKeyboardButton("✂️ Trim Video", callback_data="vtool:trim")],
        [InlineKeyboardButton("💧 Watermark", callback_data="vtool:watermark")],
        [InlineKeyboardButton("✂️💧🗜️ Trim & Watermark & Compress", callback_data="vtool:combo")],
        [InlineKeyboardButton("📸 Screenshot", callback_data="vtool:screenshot")],
        [InlineKeyboardButton("📐 16:9", callback_data="vtool:169")],
        [InlineKeyboardButton("⬛ 16:9 Black Bars", callback_data="vtool:169bars")],
        [InlineKeyboardButton("☁️ Google Drive", callback_data="vtool:gdrive")],
    ])


def _video_media_info(message):
    if not message:
        return None
    media = getattr(message, "video", None)
    if media:
        return {
            "file_id": media.file_id,
            "file_name": media.file_name or "video.mp4",
            "file_size": media.file_size or 0,
        }
    media = getattr(message, "document", None)
    if media:
        name = media.file_name or "video.mp4"
        mime = (media.mime_type or "").lower()
        if mime.startswith("video/") or name.lower().endswith((".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v")):
            return {
                "file_id": media.file_id,
                "file_name": name,
                "file_size": media.file_size or 0,
            }
    return None


async def _delete_later(message, delay=VIDEO_TOOL_DELETE_SECONDS):
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


def _tool_bar(percent):
    pct = max(0, min(100, int(percent)))
    width = 16
    filled = min(width, int(pct * width / 100))
    return "🟩" * filled + "⬜" * (width - filled)


def _tool_status(title, percent, extra=""):
    text = f"{title}\n[{_tool_bar(percent)}] {int(percent)}%"
    return text + (f"\n\n{extra}" if extra else "")


def _clock_to_seconds(value):
    """Parse HH:MM:SS or MM:SS without confusing 30:00 for 30 seconds."""
    try:
        parts = [int(part) for part in str(value or "").strip().split(":")]
    except (TypeError, ValueError):
        return None
    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return None
    if hours < 0 or minutes < 0 or seconds < 0 or minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_range(value):
    """Parse a range such as 00:00:40 to 00:02:00 or 01:00–02:00."""
    normalized = str(value or "").strip()
    normalized = normalized.replace("–", " to ").replace("—", " to ")
    m = re.fullmatch(
        r"\s*(\d{1,4}:\d{2}(?::\d{2})?)\s*(?:to|-)\s*"
        r"(\d{1,4}:\d{2}(?::\d{2})?)\s*",
        normalized,
        re.I,
    )
    if not m:
        return None
    start = _clock_to_seconds(m.group(1))
    end = _clock_to_seconds(m.group(2))
    if start is None or end is None:
        return None
    if end <= start:
        return None
    return start, end


def _parse_ranges(value):
    """Parse comma/semicolon/newline-separated watermark time ranges."""
    ranges = []
    for chunk in re.split(r"[,;\n]+|\s+\band\b\s+", str(value or ""), flags=re.I):
        parsed = _parse_range(chunk)
        if parsed:
            ranges.append(parsed)
    return ranges


def _watermark_enable(ranges):
    if not ranges:
        return ""
    expression = "+".join(
        f"between(t,{int(start)},{int(end)})" for start, end in ranges
    )
    return f":enable='{expression}'"


def _image_watermark_url(value):
    candidate = str(value or "").strip()
    if not candidate.lower().startswith(("http://", "https://")):
        return False
    path = candidate.lower().split("?", 1)[0]
    return any(path.endswith(extension) for extension in (".png", ".jpg", ".jpeg", ".webp"))


def _parse_combined_video_request(text):
    """Parse the one-message form used by Trim + Watermark + Compress."""
    result = {
        "watermark_position": 1,
        "watermark_ranges": [],
        "watermark_text": "",
        "target_mb": VIDEO_TOOL_DEFAULT_TARGET_MB,
    }
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in lines:
        cleaned = line.strip().strip("`")
        low = cleaned.casefold()
        if not result.get("range"):
            direct_range = _parse_range(cleaned)
            if direct_range:
                result["range"] = direct_range
                continue

        if low.startswith(("trim:", "trim ", "range:", "range ", "-trim ")):
            candidate = re.split(r"[:\s]+", cleaned, maxsplit=1)[-1].strip()
            parsed = _parse_range(candidate)
            if parsed:
                result["range"] = parsed
                continue

        if re.match(r"^(?:watermark\s*(?:position)?|wm)\s*[:#-]?\s*[12]\s*$", low):
            result["watermark_position"] = int(re.findall(r"[12]", low)[-1])
            continue

        if low.startswith((
            "watermark time", "watermark range", "watermark ranges",
            "watermarktime", "watermarkrange", "-watermarktime",
        )):
            value = re.split(r"[:=]", cleaned, maxsplit=1)[-1].strip()
            result["watermark_ranges"] = _parse_ranges(value)
            continue

        if low.startswith((
            "watermark text/link", "watermarktext/link",
            "-watermarktext/link", "watermark text", "watermarktext",
        )):
            value = re.split(r"[:=]", cleaned, maxsplit=1)[-1].strip()
            if value.casefold() in {"(optional)", "optional", "none"}:
                value = ""
            elif low.startswith("-watermarktext/link") and value == cleaned:
                value = cleaned[len("-watermarktext/link"):].strip()
            result["watermark_text"] = value
            continue

        if low.startswith(("file name", "filename", "renamefile", "-renamefile")):
            value = re.split(r"[:=]", cleaned, maxsplit=1)[-1].strip()
            if value == cleaned:
                value = re.sub(
                    r"^(?:-?renamefile|file\s+name|filename)\s*",
                    "",
                    cleaned,
                    flags=re.I,
                ).strip()
            result["name"] = value
            if result["name"].casefold().startswith(("name ", "filename ")):
                result["name"] = result["name"].split(" ", 1)[1].strip()
            continue

        if low.startswith(("target mb", "target size", "sizemb", "size")):
            match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
            if match:
                result["target_mb"] = max(1.0, float(match.group(1)))

    if not result.get("range"):
        return None, "Add a trim range such as `00:00:40 to 00:02:00`."
    if not result.get("name"):
        return None, "Add a file name, for example `File Name: Anime Cartoon`."
    if result["watermark_ranges"] and any(
        end <= start for start, end in result["watermark_ranges"]
    ):
        return None, "Each watermark range must end after it starts."
    return result, None


def _escape_drawtext(text):
    # FFmpeg drawtext escaping for user-provided watermark text.
    return (str(text).replace("\\", r"\\").replace(":", r"\\:")
            .replace("'", r"\\'").replace("%", r"\\%"))


def _drawtext_font_option():
    """Use a font that exists on Replit instead of assuming /system/fonts."""
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/system/fonts/Roboto-Regular.ttf",
    ):
        if Path(candidate).exists():
            return f"fontfile={candidate}:"
    # FFmpeg can still use its configured default font when no fontfile is
    # supplied; this is safer than failing the whole video operation.
    return ""


async def _download_tool_media(client, info, path, status):
    # Pyrogram invokes progress callbacks without awaiting them. Schedule the
    # async UI updater explicitly so no coroutine is left un-awaited.
    last_update = 0.0
    main_loop = asyncio.get_running_loop()

    def progress_callback(current, total):
        nonlocal last_update

        now = time.monotonic()

        # Update UI at most once per second.
        if now - last_update < 1.0 and current < total:
            return

        last_update = now

        try:
            asyncio.run_coroutine_threadsafe(
                _tool_download_progress(status, current, total),
                main_loop,
            )
        except Exception:
            pass

    await client.download_media(
        info["file_id"],
        file_name=str(path),
        progress=progress_callback,
    )


async def _tool_download_progress(status, current, total):
    try:
        pct = (current * 100 / total) if total else 0
        await status.edit_text(_tool_status("📥 Downloading...", pct))
    except Exception:
        pass


async def _tool_upload_progress(status, current, total):
    try:
        pct = (current * 100 / total) if total else 0
        await status.edit_text(_tool_status("📤 Uploading...", pct))
    except Exception:
        pass


async def _run_tool_ffmpeg(command, progress_path, duration, status, processing_title="⚙️ Processing / Watermarking..."):
    progress_path = Path(progress_path)
    progress_path.unlink(missing_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    async def updater():
        while proc.returncode is None:
            pct = 0
            try:
                values = {}
                if os.path.exists(progress_path):
                    for line in Path(progress_path).read_text(errors="ignore").splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            values[k] = v
                current = float(values.get("out_time_ms", "0") or 0) / 1_000_000
                if duration:
                    pct = min(100, current * 100 / duration)
            except Exception:
                pass
            try:
                await status.edit_text(_tool_status(processing_title, pct))
            except Exception:
                pass
            await asyncio.sleep(2)

    task = asyncio.create_task(updater())
    _, stderr = await proc.communicate()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-1500:] or "FFmpeg failed")
    try:
        await status.edit_text(_tool_status(processing_title, 100))
    except Exception:
        pass


def _compression_bitrates(duration_seconds, target_mb):
    """Return (video_kbps, audio_kbps, total_kbps) for a decimal-MB target."""
    duration = max(float(duration_seconds or 0), 1.0)
    target_bits = max(float(target_mb or 1), 1.0) * 1_000_000 * 8
    target_total_kbps = target_bits / duration / 1000
    audio_kbps = VIDEO_TOOL_AUDIO_KBPS
    # A tiny target should not produce an unusably low video stream. In that
    # case quality wins and the output is allowed to exceed the requested size.
    video_kbps = max(150, int(round(target_total_kbps - audio_kbps)))
    total_kbps = video_kbps + audio_kbps
    return video_kbps, audio_kbps, total_kbps


async def _run_two_pass_tool_ffmpeg(
    first_pass_command,
    second_pass_command,
    progress_path,
    duration,
    status,
    video_kbps,
    audio_kbps,
):
    """Run FFmpeg's two bitrate passes with progress for each pass."""
    await status.edit_text(
        _tool_status(
            "⚙️ Compressing (pass 1/2)...",
            0,
            f"🎯 Target bitrate: {video_kbps}k video + {audio_kbps}k AAC",
        )
    )
    await _run_tool_ffmpeg(
        first_pass_command,
        progress_path,
        duration,
        status,
        "⚙️ Compressing (pass 1/2)...",
    )
    await status.edit_text(
        _tool_status(
            "⚙️ Compressing (pass 2/2)...",
            0,
            f"🎯 Target bitrate: {video_kbps}k video + {audio_kbps}k AAC",
        )
    )
    await _run_tool_ffmpeg(
        second_pass_command,
        progress_path,
        duration,
        status,
        "⚙️ Compressing (pass 2/2)...",
    )


async def _make_video_thumbnail(input_path, output_path, duration):
    """Create a small JPEG thumbnail for the processed Telegram video."""
    seek = max(0.0, min(float(duration or 0) / 2.0, 3.0))
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{seek:.3f}", "-i", str(input_path),
        "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not Path(output_path).exists():
        LOG.warning("Thumbnail generation failed: %s", stderr.decode(errors="ignore")[-500:])
        return False
    return True


async def _process_video_tool(client, message, info, mode, params):
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", 0) if user else 0
    task_dir = Path(getattr(config, "DOWNLOAD_DIRECTORY", "/tmp")) / f"video_tool_{uid}_{secrets.token_hex(6)}"
    task_dir.mkdir(parents=True, exist_ok=True)
    source_name = _safe_filename(info["file_name"]) if "_safe_filename" in globals() else info["file_name"]
    input_path = task_dir / source_name
    stem = Path(source_name).stem or "video"
    output_name = _safe_filename(params.get("name", f"{stem}_processed.mp4")) if "_safe_filename" in globals() else params.get("name", f"{stem}_processed.mp4")
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output_path = task_dir / output_name
    progress_path = task_dir / "progress.txt"
    fallback_command = None
    status = await message.reply_text(_tool_status("📥 Downloading...", 0))
    try:
        await _download_tool_media(client, info, input_path, status)
        duration = await get_duration_ffmpeg(str(input_path))
        if duration <= 0:
            raise RuntimeError("Unable to read video duration.")

        if mode == "169":
            command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(input_path),
                "-map", "0:v:0", "-map", "0:a?",
                "-vf", "scale=1920:1080,setsar=1",
                "-c:v", "libx264", "-preset", VIDEO_TOOL_PRESET, "-crf", str(VIDEO_TOOL_CRF),
                "-pix_fmt", "yuv420p", "-aspect", "16:9", "-threads", str(VIDEO_TOOL_THREADS),
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-progress", str(progress_path), "-nostats", str(output_path),
            ]
            processing_title = "⚙️ Processing / 16:9..."
        elif mode == "169bars":
            command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(input_path),
                "-map", "0:v:0", "-map", "0:a?",
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                       "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", "libx264", "-preset", VIDEO_TOOL_PRESET, "-crf", str(VIDEO_TOOL_CRF),
                "-pix_fmt", "yuv420p", "-aspect", "16:9", "-threads", str(VIDEO_TOOL_THREADS),
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-progress", str(progress_path), "-nostats", str(output_path),
            ]
            processing_title = "⚙️ Processing / 16:9 Black Bars..."
        elif mode == "trim":
            start, end = params["range"]
            duration = end - start
            # Trimming does not need a video filter. Stream-copying avoids
            # decoding and re-encoding a long video, so the usual case takes
            # roughly as long as reading the selected segment from disk.
            command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", str(start), "-i", str(input_path),
                "-t", str(duration), "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart", "-progress", str(progress_path), "-nostats", str(output_path),
            ]
            # Some source codecs cannot be muxed into MP4. Keep an automatic
            # compatibility path rather than making trim fail for those files.
            fallback_command = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", str(start), "-i", str(input_path),
                "-t", str(duration), "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", VIDEO_TOOL_PRESET, "-crf", str(VIDEO_TOOL_CRF),
                "-pix_fmt", "yuv420p", "-vsync", "cfr", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-progress", str(progress_path), "-nostats", str(output_path),
            ]
            processing_title = "⚙️ Processing / Trimming..."
        elif mode.startswith("watermark"):
            start, end = params["range"]
            duration = end - start
            watermark_value = str(params.get("watermark_text", "")).strip()
            if not watermark_value and mode in ("watermark1", "watermark2"):
                watermark_value = WATERMARK_IMAGE_URL
            if not watermark_value:
                raise RuntimeError("Watermark text/link cannot be empty.")

            # A direct image URL is treated as an image watermark. Otherwise
            # the supplied value is rendered as text/link with drawtext.
            is_image_url = _image_watermark_url(watermark_value)
            watermark_ranges = params.get("watermark_ranges") or []
            enable = _watermark_enable(watermark_ranges)

            if is_image_url:
                image_path = task_dir / "watermark.png"
                with urllib.request.urlopen(watermark_value, timeout=30) as resp:
                    image_path.write_bytes(resp.read())

                if mode == "watermark2":
                    position = "20:main_h-overlay_h-899"
                elif mode == "watermark1":
                    position = "20:main_h-overlay_h-110"
                else:
                    position = "(main_w-overlay_w)/2:(main_h-overlay_h)/2"
                overlay = f"[base][watermark]overlay={position}{enable}[outv]"

                command = [
                    "ffmpeg", "-hide_banner", "-nostdin", "-y",
                    "-ss", str(start), "-i", str(input_path),
                    "-loop", "1", "-i", str(image_path), "-t", str(duration),
                    "-filter_complex",
                    "[0:v]setpts=PTS-STARTPTS[base];"
                    "[1:v]scale=300:-1[watermark];" + overlay,
                    "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-crf", str(VIDEO_TOOL_CRF),
                    "-preset", VIDEO_TOOL_PRESET, "-threads", str(VIDEO_TOOL_THREADS),
                    "-pix_fmt", "yuv420p", "-vsync", "cfr", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", "-progress", str(progress_path), "-nostats", str(output_path),
                ]
            else:
                text = _escape_drawtext(watermark_value)
                font_option = _drawtext_font_option()
                if mode == "watermark2":
                    position = "x=20:y=h-th-899"
                elif mode == "watermark1":
                    position = "x=20:y=h-th-110"
                elif mode == "watermark3":
                    position = "x=(w-tw)/2:y=(h-th)/2"
                else:
                    position = "x=(w-tw)/2:y=h-th-110"
                enable_text = enable
                vf = (
                    "setsar=1,drawtext="
                    f"text='{text}':{font_option}fontsize=24:"
                    f"fontcolor=white:{position}:shadowcolor=black:shadowx=2:shadowy=2"
                    f"{enable_text}"
                )
                command = [
                    "ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", str(start), "-i", str(input_path),
                    "-t", str(duration), "-map", "0:v:0", "-map", "0:a?", "-vf", vf,
                    "-c:v", "libx264", "-preset", VIDEO_TOOL_PRESET, "-crf", str(VIDEO_TOOL_CRF),
                    "-threads", str(VIDEO_TOOL_THREADS), "-pix_fmt", "yuv420p", "-vsync", "cfr",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-progress", str(progress_path),
                    "-nostats", str(output_path),
                ]
            processing_title = "⚙️ Processing / Watermarking..."
        elif mode == "trim_watermark_compress":
            start, end = params["range"]
            duration = end - start
            target_mb = max(1.0, float(params.get("target_mb", VIDEO_TOOL_DEFAULT_TARGET_MB)))
            video_kbps, audio_kbps, total_kbps = _compression_bitrates(duration, target_mb)
            watermark_value = str(params.get("watermark_text", "")).strip() or WATERMARK_IMAGE_URL
            watermark_position = 2 if int(params.get("watermark_position", 1) or 1) == 2 else 1
            watermark_ranges = params.get("watermark_ranges") or []
            enable = _watermark_enable(watermark_ranges)
            passlog = str(task_dir / "compress_pass")

            input_args = [
                "-ss", str(start), "-i", str(input_path),
            ]
            is_image_url = _image_watermark_url(watermark_value)
            if is_image_url:
                image_path = task_dir / "watermark.png"
                with urllib.request.urlopen(watermark_value, timeout=30) as resp:
                    image_path.write_bytes(resp.read())
                input_args += ["-loop", "1", "-i", str(image_path)]
                offset = 110 if watermark_position == 1 else 899
                filter_complex = (
                    "[0:v]setpts=PTS-STARTPTS[base];"
                    "[1:v]scale=300:-1[watermark];"
                    f"[base][watermark]overlay=20:main_h-overlay_h-{offset}"
                    f"{enable}[vout]"
                )
            else:
                text = _escape_drawtext(watermark_value)
                font_option = _drawtext_font_option()
                offset = 110 if watermark_position == 1 else 899
                filter_complex = (
                    "[0:v]setpts=PTS-STARTPTS,"
                    "drawtext="
                    f"text='{text}':{font_option}"
                    f"fontsize=24:fontcolor=white:x=20:y=h-th-{offset}:"
                    f"shadowcolor=black:shadowx=2:shadowy=2{enable}[vout]"
                )

            common = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                *input_args, "-t", str(duration),
                "-filter_complex", filter_complex,
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", VIDEO_TOOL_PRESET,
                "-b:v", f"{video_kbps}k", "-threads", str(VIDEO_TOOL_THREADS),
                "-pix_fmt", "yuv420p", "-vsync", "cfr",
                "-passlogfile", passlog,
            ]
            first_pass_command = common + [
                "-pass", "1", "-an", "-f", "mp4", os.devnull,
            ]
            second_pass_command = common + [
                "-pass", "2", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
                "-movflags", "+faststart", "-progress", str(progress_path),
                "-nostats", str(output_path),
            ]
            processing_title = (
                "⚙️ Processing / Trim + Watermark + Compress..."
            )
        else:
            raise RuntimeError("Unknown video tool.")

        # Use the requested processing label while FFmpeg runs.
        try:
            await status.edit_text(_tool_status(processing_title, 0))
        except Exception:
            pass
        if mode == "trim_watermark_compress":
            await _run_two_pass_tool_ffmpeg(
                first_pass_command,
                second_pass_command,
                progress_path,
                duration,
                status,
                video_kbps,
                audio_kbps,
            )
        else:
            try:
                await _run_tool_ffmpeg(command, progress_path, duration, status, processing_title)
            except RuntimeError:
                if mode != "trim" or not fallback_command:
                    raise
                # Fall back only for trim inputs that cannot be stream-copied into
                # MP4. This retains fast trimming for compatible inputs.
                if output_path.exists():
                    output_path.unlink()
                progress_path.unlink(missing_ok=True)
                await status.edit_text(_tool_status("⚙️ Processing / Trimming (compatibility mode)...", 0))
                await _run_tool_ffmpeg(
                    fallback_command, progress_path, duration, status,
                    "⚙️ Processing / Trimming (compatibility mode)...",
                )
        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError("FFmpeg produced an empty output file.")

        # Read the final processed video's real duration before uploading.
        try:
            final_duration = await get_duration_ffmpeg(str(output_path))
            if final_duration <= 0:
                final_duration = duration
        except Exception:
            final_duration = duration

        thumbnail_path = task_dir / "thumbnail.jpg"
        thumbnail_ready = await _make_video_thumbnail(
            output_path, thumbnail_path, final_duration
        )
        await status.edit_text(_tool_status("📤 Uploading...", 0))

        # Pyrogram calls `progress` as a normal synchronous callback.
        # Returning an async coroutine here does not execute it, which caused
        # the progress message to remain stuck at 0/1%. Schedule the async UI
        # update explicitly instead.
        upload_last_update = 0.0
        upload_loop = asyncio.get_running_loop()

        def upload_progress_callback(current, total):
            nonlocal upload_last_update

            now = time.monotonic()

            # Update upload UI at most once per second.
            if now - upload_last_update < 1.0 and current < total:
                return

            upload_last_update = now

            try:
                asyncio.run_coroutine_threadsafe(
                    _tool_upload_progress(status, current, total),
                    upload_loop,
                )
            except Exception:
                pass

        with output_path.open("rb") as fh:
            await client.send_video(
                message.chat.id,
                fh,
                caption=f"📄 File: {output_name}",
                duration=max(0, int(round(final_duration))),
                supports_streaming=True,
                reply_to_message_id=message.id,
                thumb=str(thumbnail_path) if thumbnail_ready else None,
                progress=upload_progress_callback,
            )
        await status.edit_text(
            f"✅ Update Complete\\n"
            f"📄 File: {output_name}\\n"
            f"🎬 Video Duration: {int(final_duration // 60):02d}:{int(final_duration % 60):02d}"
        )
        asyncio.create_task(_delete_later(status, VIDEO_TOOL_DELETE_SECONDS))
        asyncio.create_task(_delete_later(message, VIDEO_TOOL_DELETE_SECONDS))
        prompt_id = int(params.get("prompt_id", 0) or 0)
        prompt_chat_id = int(params.get("prompt_chat_id", 0) or 0)
        if prompt_id and prompt_chat_id:
            try:
                prompt_msg = await client.get_messages(prompt_chat_id, prompt_id)
                asyncio.create_task(_delete_later(prompt_msg, VIDEO_TOOL_DELETE_SECONDS))
            except Exception:
                pass
        # For combined Trim/Watermark input, remove the user's reply and the
        # bot's prompt after the processing has completed.
        input_message_id = params.get("input_message_id")
        prompt_chat_id = params.get("prompt_chat_id")
        prompt_id = params.get("prompt_id")
        if input_message_id and prompt_chat_id:
            async def _delete_tool_inputs_after_complete():
                await asyncio.sleep(VIDEO_TOOL_DELETE_SECONDS)
                try:
                    await client.delete_messages(int(prompt_chat_id), int(input_message_id))
                except Exception:
                    pass
                if prompt_id:
                    try:
                        await client.delete_messages(int(prompt_chat_id), int(prompt_id))
                    except Exception:
                        pass
            asyncio.create_task(_delete_tool_inputs_after_complete())
    except Exception as exc:
        try:
            await status.edit_text(f"❌ Processing Failed\n\n{str(exc)[:1200]}")
            asyncio.create_task(_delete_later(status, VIDEO_TOOL_DELETE_SECONDS))
        except Exception:
            pass
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@app.on_message((filters.video | filters.document) & ~filters.command(["rec", "drec", "reclink"]))
async def video_tools_menu(client, message: Message):
    if not _video_media_info(message):
        return
    # Video Tools require the same token/access as recording commands.
    if not await _verification_required(message):
        return
    # Keep an explicit reference to the uploaded/forwarded video.  Do not
    # depend only on Telegram's reply_to_message relation when a button is
    # pressed; some forwarded/media layouts can lose that relation.
    menu = await message.reply_text(
        "🎬 **Video Tools**\n\nChoose an action:",
        reply_markup=_video_tool_keyboard(),
    )
    menu_key = (int(message.from_user.id) if message.from_user else 0, int(message.chat.id), int(message.id))
    VIDEO_TOOL_STATES[menu_key] = {
        "mode": "menu",
        "info": _video_media_info(message),
        "original_id": int(message.id),
        "menu_id": int(menu.id),
    }


@app.on_callback_query(filters.regex(r"^vtool:(audio|trim|watermark|combo|screenshot|169|169bars|gdrive)$"))
async def video_tools_callback(client, query):
    if not query.message:
        return await _safe_callback_answer(query, "Message unavailable.", show_alert=True)
    # First recover the exact original video from the state saved when the
    # four-button menu was created.  This fixes "Original video not found"
    # when Telegram does not expose the bot menu's reply_to_message relation.
    uid = int(query.from_user.id)
    # Re-check access when a Video Tools button is pressed.
    if not _is_owner(uid) and not (bot_settings.get("premium_access", True) and _is_premium(uid)):
        chat_obj = getattr(getattr(query, "message", None), "chat", None)
        chat_id_for_access = getattr(chat_obj, "id", None)
        if int(chat_id_for_access or 0) not in ALLOWED_REC_GROUP_IDS or (bot_settings.get("token", True) and not _has_valid_access(uid)):
            return await _safe_callback_answer(query, "🔒 Token required. Use /Token first.", show_alert=True)
    menu_state = None
    menu_key = None
    for candidate_key, candidate_state in list(VIDEO_TOOL_STATES.items()):
        if (candidate_key[0] == uid and
                candidate_state.get("menu_id") == int(query.message.id) and
                candidate_state.get("mode") == "menu"):
            menu_key = candidate_key
            menu_state = candidate_state
            break

    if menu_state and menu_state.get("info") and menu_state.get("original_id"):
        info = menu_state["info"]
        original_id = int(menu_state["original_id"])
        chat_id = int(menu_key[1]) if menu_key else int(query.message.chat.id)
    else:
        original = query.message.reply_to_message
        info = _video_media_info(original) if original else None
        original_id = int(original.id) if original else 0
        chat_id = int(original.chat.id) if original else int(query.message.chat.id)

    if not info or not original_id:
        return await _safe_callback_answer(query, "Original video not found. Please upload/forward the video again.", show_alert=True)

    action = query.data.split(":", 1)[1]
    # Use one stable state key for all follow-up replies.
    key = (uid, chat_id, original_id)
    if action == "gdrive":
        await _safe_callback_answer(query, )
        if not is_user_connected(uid) and not _sa_enabled():
            return await query.message.reply_text(
                "☁️ **Google Drive Not Connected**\n\n"
                "Pehle Google Drive connect karein:\n"
                "`/googledrive`\n\n"
                "Connect hone ke baad Video Tools → Google Drive dobara select karein."
            )

        filename = info.get("file_name") or "video.mp4"
        size = int(info.get("file_size") or 0)
        size_mb = size / (1024 * 1024) if size else 0
        status = await query.message.reply_text(
            "☁️ **Google Drive Upload**\n\n"
            f"📄 **File:** `{filename}`\n"
            + (f"📦 **Size:** {size_mb:.2f} MB\n" if size else "") +
            "\n📥 Preparing file...",
        )
        task_dir = Path(getattr(config, "DOWNLOAD_DIRECTORY", "/tmp")) / f"drive_tool_{uid}_{secrets.token_hex(6)}"
        task_dir.mkdir(parents=True, exist_ok=True)
        local_name = _safe_filename(filename) if "_safe_filename" in globals() else filename
        local_path = task_dir / local_name
        try:
            await _download_tool_media(client, info, local_path, status)
            await status.edit_text(
                "☁️ **Google Drive Upload**\n\n"
                f"📄 **File:** `{filename}`\n"
                "🚀 Starting Drive upload..."
            )
            await upload_and_notify(client, chat_id, str(local_path), filename, status_msg=status)
        except Exception as exc:
            LOG.error("Video Tools Drive upload failed for %s: %s", filename, exc)
            try:
                await status.edit_text(f"⚠️ **Google Drive upload failed**\n\n`{str(exc)[:1500]}`")
            except Exception:
                pass
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
        return
    if action == "audio":
        await _safe_callback_answer(query, )
        await query.message.reply_text(
            "🎵 **Audio Track**\n\nReply to the original video with:\n`/Audiotrack Your_title`"
        )
        return
    if action == "trim":
        VIDEO_TOOL_STATES[key] = {"mode": "trim", "info": info, "original_id": original_id}
        await _safe_callback_answer(query, )
        prompt = await query.message.reply_text(
            "✂️ **Trim Video**\n\nReply to this video with Rename File + time range:\n\n`00:00:30 to 00:00:50\n-RenameFile ls.mkv`"
        )
        VIDEO_TOOL_STATES[key]["prompt_id"] = int(prompt.id)
        return
    if action == "combo":
        VIDEO_TOOL_STATES[key] = {
            "mode": "trim_watermark_compress",
            "info": info,
            "original_id": original_id,
        }
        await _safe_callback_answer(query, )
        prompt = await query.message.reply_text(
            "✂️💧🗜️ **Trim & Watermark & Compress**\n\n"
            "Reply with the settings below. Trim and file name are required; "
            "watermark time ranges are optional and relative to the trimmed video.\n\n"
            "`Trim: 00:00:40 to 00:02:00\n"
            "Watermark: 1\n"
            "Watermark Time: 01:00–02:00, 30:00–31:00\n"
            "Watermark Text/Link: (optional)\n"
            "File Name: Anime Cartoon\n"
            "Target MB: 300`\n\n"
            "Watermark 1 uses the supplied image at x=20, bottom=110, width=300.\n"
            "Use `Watermark: 2` for the alternate bottom=899 position. "
            "Leave Watermark Text/Link empty to use the supplied image."
        )
        VIDEO_TOOL_STATES[key]["prompt_id"] = int(prompt.id)
        return
    if action == "screenshot":
        VIDEO_TOOL_STATES[key] = {"mode": "screenshot", "info": info, "original_id": original_id}
        await _safe_callback_answer(query, )
        prompt = await query.message.reply_text(
            "📸 **Screenshot**\n\nReply to this video with the number of screenshots.\n\nExample:\n`10`"
        )
        VIDEO_TOOL_STATES[key]["prompt_id"] = int(prompt.id)
        return
    if action in ("169", "169bars"):
        mode = "169" if action == "169" else "169bars"
        VIDEO_TOOL_STATES[key] = {"mode": mode, "info": info, "original_id": original_id}
        await _safe_callback_answer(query, )
        label = "📐 **16:9**" if mode == "169" else "⬛ **16:9 Black Bars**"
        prompt = await query.message.reply_text(
            f"{label}\n\n"
            "Reply to this video with the new filename.\n\n"
            "Example:\n"
            "`Little Singham 16x9`\n\n"
            "📺 Output: 1920×1080 (16:9)\n"
            + ("🔲 Black bars preserve the original aspect ratio." if mode == "169bars" else "↔️ Direct scale=1920:1080, no black padding.")
        )
        VIDEO_TOOL_STATES[key]["prompt_id"] = int(prompt.id)
        return
    VIDEO_TOOL_STATES[key] = {"mode": "watermark_menu", "info": info, "original_id": original_id}
    await _safe_callback_answer(query, )
    await query.message.reply_text(
        "💧 **Watermark**\n\nChoose watermark:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💧 Watermark 1 • x20 / bottom110", callback_data="vwm:1")],
            [InlineKeyboardButton("💧 Watermark 2 • x20 / bottom899", callback_data="vwm:2")],
            [InlineKeyboardButton("💧 Watermark 3 • custom", callback_data="vwm:3")],
        ])
    )


@app.on_callback_query(filters.regex(r"^vwm:[123]$"))
async def video_watermark_callback(client, query):
    if not query.message:
        return await _safe_callback_answer(query, "Message unavailable.", show_alert=True)
    wm = query.data.split(":", 1)[1]
    uid = int(query.from_user.id)
    if not _is_owner(uid) and not (bot_settings.get("premium_access", True) and _is_premium(uid)):
        chat_obj = getattr(getattr(query, "message", None), "chat", None)
        chat_id_for_access = getattr(chat_obj, "id", None)
        if int(chat_id_for_access or 0) not in ALLOWED_REC_GROUP_IDS or (bot_settings.get("token", True) and not _has_valid_access(uid)):
            return await _safe_callback_answer(query, "🔒 Token required. Use /Token first.", show_alert=True)
    # The Watermark 1/2/3 buttons are inside a bot message that is itself a
    # reply to the menu message, so recover the original video from the
    # pending Watermark-menu state rather than assuming query.message is it.
    key = None
    state = None
    for candidate_key, candidate_state in list(VIDEO_TOOL_STATES.items()):
        if candidate_key[0] == uid and candidate_state.get("mode") == "watermark_menu":
            key = candidate_key
            state = candidate_state
            break
    if not state:
        return await _safe_callback_answer(query, "Watermark session expired. Click Watermark again.", show_alert=True)
    info = state.get("info")
    original_id = state.get("original_id")
    if not info or not original_id:
        return await _safe_callback_answer(query, "Original video not found.", show_alert=True)
    VIDEO_TOOL_STATES[key] = {"mode": f"watermark{wm}", "info": info, "original_id": original_id}
    await _safe_callback_answer(query, )
    prompt = await query.message.reply_text(
        f"💧 **Watermark {wm}**\n\n"
        "Reply to this video with ALL details in one message:\n\n"
        "`00:00:10 to 00:00:30\n"
        "-RenameFile Little Singham\n"
        "-WatermarkText/link LittleSinghamChannel\n"
        "-WatermarkTime 01:00–02:00 and 30:00–31:00`\n\n"
        + (
            "Omit `-WatermarkText/link` to use the supplied image."
            if wm in ("1", "2")
            else "Watermark 3 requires custom text or an image URL."
        )
    )
    VIDEO_TOOL_STATES[key]["prompt_id"] = int(prompt.id)


@app.on_message(filters.text & ~filters.command("Audiotrack"))
async def video_tools_text_input(client, message: Message):
    if not message.reply_to_message or not message.from_user:
        return

    # Accept replies to either the original video OR the bot's active prompt.
    # This is important because users naturally reply to the Trim/Screenshot/
    # Watermark prompt after clicking the button, rather than reopening the
    # original media message.
    uid = int(message.from_user.id)
    replied = message.reply_to_message
    key = None
    state = None

    original = replied
    if _video_media_info(original):
        candidate_key = (uid, int(original.chat.id), int(original.id))
        candidate_state = VIDEO_TOOL_STATES.get(candidate_key)
        if candidate_state:
            key, state = candidate_key, candidate_state

    if state is None:
        replied_id = int(replied.id)
        for candidate_key, candidate_state in list(VIDEO_TOOL_STATES.items()):
            if candidate_key[0] != uid:
                continue
            if int(candidate_state.get("prompt_id", 0) or 0) == replied_id:
                key, state = candidate_key, candidate_state
                break

    if not state or not key:
        return

    mode = state.get("mode")
    text = (message.text or "").strip()

    if mode in ("169", "169bars") and "name" not in state:
        name = text.strip()
        if not name:
            return await message.reply_text("❌ Filename cannot be empty.")
        state["name"] = name
        # Delete both the user's filename reply and the bot's 16:9 prompt
        # after processing completes.
        state["input_message_id"] = int(message.id)
        state["prompt_chat_id"] = int(replied.chat.id)
        state["prompt_id"] = int(replied.id)
        VIDEO_TOOL_STATES.pop(key, None)
        asyncio.create_task(_process_video_tool(client, message, state["info"], mode, state))
        return

    if mode == "trim_watermark_compress" and "range" not in state:
        parsed, error = _parse_combined_video_request(text)
        if error:
            return await message.reply_text(f"❌ {error}")
        state.update(parsed)
        state["input_message_id"] = int(message.id)
        state["prompt_chat_id"] = int(replied.chat.id)
        state["prompt_id"] = int(state.get("prompt_id", 0) or 0)
        VIDEO_TOOL_STATES.pop(key, None)
        asyncio.create_task(
            _process_video_tool(client, message, state["info"], mode, state)
        )
        return

    if mode in ("trim", "watermark1", "watermark2", "watermark3") and "range" not in state:
        # New one-message format:
        # 00:00:10 to 00:00:30
        # -RenameFile Little Singham
        # -WatermarkText/link LittleSinghamChannel
        # The same format also accepts an image URL in WatermarkText/link.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        rng = _parse_range(lines[0]) if lines else None
        if rng and len(lines) >= 2:
            rename_value = None
            watermark_value = None
            watermark_ranges = []
            for line in lines[1:]:
                low = line.lower()
                if low.startswith("-renamefile"):
                    rename_value = line[len("-renamefile"):].strip()
                elif low.startswith("-watermarktext/link"):
                    watermark_value = line[len("-watermarktext/link"):].strip()
                elif low.startswith(("-watermarktime", "-watermarkrange")):
                    raw_ranges = re.sub(
                        r"^-watermark(?:time|range)\s*[:=]?\s*",
                        "",
                        line,
                        flags=re.I,
                    )
                    watermark_ranges = _parse_ranges(raw_ranges)

            if not rename_value:
                return await message.reply_text("❌ Add `-RenameFile <name>`." )
            if mode == "watermark3" and not watermark_value:
                return await message.reply_text("❌ Add `-WatermarkText/link <text or image URL>`." )

            state["range"] = rng
            state["name"] = rename_value
            if mode.startswith("watermark"):
                state["watermark_text"] = (
                    watermark_value or WATERMARK_IMAGE_URL
                    if mode in ("watermark1", "watermark2")
                    else watermark_value
                )
                state["watermark_ranges"] = watermark_ranges
            # Keep both the bot prompt and the user's combined reply IDs so
            # they can be auto-deleted after processing completes.
            state["input_message_id"] = int(message.id)
            state["prompt_chat_id"] = int(replied.chat.id)
            state["prompt_id"] = int(state.get("prompt_id", 0) or 0)
            VIDEO_TOOL_STATES.pop(key, None)
            asyncio.create_task(_process_video_tool(client, message, state["info"], mode, state))
            return

        # Watermark uses ONE reply only. Watermark 1/2 may omit the custom
        # value and will use the supplied default image.
        if mode.startswith("watermark"):
            return await message.reply_text(
                "❌ Please send all watermark details in ONE reply:\n\n"
                "`00:00:10 to 00:00:30\n"
                "-RenameFile Little Singham\n"
                "-WatermarkText/link LittleSinghamChannel`\n\n"
                + (
                    "Watermark 1/2 may omit the last line to use the supplied image."
                    if mode in ("watermark1", "watermark2")
                    else "Watermark 3 requires custom text or an image URL."
                )
            )

        # Trim keeps the older interactive range → rename flow.
        if not rng:
            return await message.reply_text("❌ Use this format: `00:00:30 to 00:00:50`")
        state["range"] = rng
        state["name_pending"] = True
        prompt = await message.reply_text("✏️ **Rename File**\n\nReply with the new filename.")
        state["prompt_id"] = int(prompt.id)
        return

    if state.get("name_pending"):
        name = text.strip()
        if not name:
            return await message.reply_text("❌ Filename cannot be empty.")
        state["name"] = name
        state.pop("name_pending", None)
        if mode.startswith("watermark"):
            state["watermark_text_pending"] = True
            prompt = await message.reply_text("💧 **Watermark Text/Link**\n\nReply with watermark text or an image URL.")
            state["prompt_id"] = int(prompt.id)
            return
        VIDEO_TOOL_STATES.pop(key, None)
        asyncio.create_task(_process_video_tool(client, message, state["info"], mode, state))
        return

    if state.get("watermark_text_pending"):
        if not text:
            return await message.reply_text("❌ Watermark text/link cannot be empty.")
        state["watermark_text"] = text
        VIDEO_TOOL_STATES.pop(key, None)
        asyncio.create_task(_process_video_tool(client, message, state["info"], mode, state))
        return

    if mode == "screenshot":
        try:
            count = int(text)
        except ValueError:
            return await message.reply_text("❌ Please send a number, for example `10`.")
        if count < 1 or count > 20:
            return await message.reply_text("❌ Screenshot count must be between 1 and 20.")
        prompt_id = int(state.get("prompt_id", 0) or 0)
        prompt_chat_id = int(message.chat.id)
        VIDEO_TOOL_STATES.pop(key, None)
        task_dir = Path(getattr(config, "DOWNLOAD_DIRECTORY", "/tmp")) / f"screenshots_{message.from_user.id}_{secrets.token_hex(6)}"
        task_dir.mkdir(parents=True, exist_ok=True)
        status = await message.reply_text(_tool_status("📥 Downloading...", 0))
        try:
            input_path = task_dir / _safe_filename(state["info"]["file_name"])
            await _download_tool_media(client, state["info"], input_path, status)
            duration = await get_duration_ffmpeg(str(input_path))
            if duration <= 0:
                raise RuntimeError("Unable to read video duration.")
            await status.edit_text("⚙️ **Processing / Screenshot...**")
            sent = 0
            for i in range(count):
                ts = duration * (i + 1) / (count + 1)
                shot = task_dir / f"screenshot_{i+1:02d}.jpg"
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(ts), "-i", str(input_path),
                    "-frames:v", "1", "-q:v", "2", str(shot),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0 and shot.exists():
                    await client.send_photo(message.chat.id, str(shot), caption=f"📸 Screenshot {i+1}/{count}", reply_to_message_id=original.id)
                    sent += 1
            await status.edit_text(f"✅ Update Complete\n📸 Screenshots: {sent}")
            asyncio.create_task(_delete_later(status, VIDEO_TOOL_DELETE_SECONDS))
            asyncio.create_task(_delete_later(message, VIDEO_TOOL_DELETE_SECONDS))
            if prompt_id:
                asyncio.create_task(_delete_message_later(client, prompt_chat_id, prompt_id, VIDEO_TOOL_DELETE_SECONDS))
        except Exception as exc:
            try:
                await status.edit_text(f"❌ Screenshot Failed\n\n{str(exc)[:1200]}")
                asyncio.create_task(_delete_later(status, VIDEO_TOOL_DELETE_SECONDS))
                if prompt_id:
                    asyncio.create_task(_delete_message_later(client, prompt_chat_id, prompt_id, VIDEO_TOOL_DELETE_SECONDS))
            except Exception:
                pass
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)


async def _bot_main():
    global schedule_worker_task
    await app.start()
    await _configure_bot_menu(app)
    if schedule_worker_task is None or schedule_worker_task.done():
        schedule_worker_task = asyncio.create_task(_schedule_worker(app))
    try:
        await idle()
    finally:
        if schedule_worker_task and not schedule_worker_task.done():
            schedule_worker_task.cancel()

        print("Gojo's Unlimited Power...")
        print("♾️ Limitless is Deactivated!")
        print("🔥 Video Recorder Bot is now stopped!")

        await app.stop()


if __name__ == "__main__":
    print("⚡ Gojo's Unlimited Power...")
    print("♾️ Limitless is Activated!")
    print("🔥 Video Recorder Bot is now running!")
    app.run(_bot_main())
