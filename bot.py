import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
import os
import json
import math
import time
import logging
import random
import secrets as pysecrets
import re
import shlex
import shutil
import asyncio
import hashlib
import zipfile
import base64
import requests
from typing import Tuple, Optional
from os.path import join
from datetime import datetime, timedelta
import psutil
import pytz
import yt_dlp
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, StopPropagation
from pyrogram.errors import FloodWait
from pyrogram.file_id import FileId
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)


# ---------------------------------------------------------------------------
# Configuration (merged from config.py)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _load_dotenv
    import os as _os_tmp
    _env_file = _os_tmp.path.join(_os_tmp.path.dirname(__file__), ".env")
    if _os_tmp.path.exists(_env_file):
        _load_dotenv(_env_file)
    del _os_tmp, _env_file
except ImportError:
    pass

from os import environ as _environ

def _parse_id_list(name: str, raw: str) -> list:
    ids, bad = [], []
    for tok in (raw or "").replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        try:
            ids.append(int(tok))
        except ValueError:
            bad.append(tok)
    if bad:
        import logging as _lg
        _lg.getLogger(__name__).warning(
            "%s contains non-numeric values that were skipped: %s.", name, bad)
    return ids

def _parse_int(name: str, raw: str) -> int:
    try:
        return int((raw or "0").strip())
    except ValueError:
        import logging as _lg
        _lg.getLogger(__name__).error("%s must be an integer, got: %r", name, raw)
        return 0

# Keep Telegram credentials in Replit Secrets, not in the source file.
API_ID        = "29481626"
API_HASH      = "4892185769903521077c4cea97808b8c"
BOT_TOKEN     = "8903930756:AAEFHfLMk85zx656MZBGDHGJWjbEOT4kzTs"
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise RuntimeError("Missing required Replit Secrets: API_ID, API_HASH, BOT_TOKEN")
AUTH_USERS    = _parse_id_list("AUTH_USERS", _environ.get("AUTH_USERS", ""))
OWNER_IDS     = {5856009289}

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_dir(env_key: str, *rel_parts: str) -> str:
    """Return env var if set, else script-relative path, else /tmp fallback.
    Falls back to /tmp automatically on read-only filesystems (e.g. Railway)."""
    from_env = _environ.get(env_key, "")
    if from_env:
        return from_env
    primary = os.path.join(_SCRIPT_DIR, *rel_parts)
    try:
        os.makedirs(primary, exist_ok=True)
        _t = os.path.join(primary, ".write_test")
        open(_t, "w").close()
        os.remove(_t)
        return primary
    except (OSError, IOError):
        fallback = os.path.join("/tmp", "ls_bot", *rel_parts)
        import logging as _lg
        _lg.getLogger(__name__).warning(
            "%s: primary path %r is not writable, using /tmp fallback: %r",
            env_key, primary, fallback,
        )
        return fallback


DOWNLOAD_DIRECTORY  = _resolve_dir("DOWNLOAD_DIRECTORY",  "bot", "downloads")
DATA_DIRECTORY      = _resolve_dir("DATA_DIRECTORY",       "bot", "data")
COOKIES_DIRECTORY   = _resolve_dir("COOKIES_DIRECTORY",    "bot", "data", "cookies")

RETENTION_HOURS     = _parse_int("RETENTION_HOURS", _environ.get("RETENTION_HOURS", "3"))
MAX_CONCURRENT_TRANSMISSIONS = max(
    1,
    min(
        _parse_int(
            "MAX_CONCURRENT_TRANSMISSIONS",
            _environ.get("MAX_CONCURRENT_TRANSMISSIONS", "8"),
        ),
        8,
    ),
)
MAX_CONCURRENT_FRAGMENT_DOWNLOADS = max(
    1,
    min(
        _parse_int(
            "MAX_CONCURRENT_FRAGMENT_DOWNLOADS",
            _environ.get("MAX_CONCURRENT_FRAGMENT_DOWNLOADS", "8"),
        ),
        16,
    ),
)
TELEGRAM_DOWNLOAD_WORKERS = max(
    1,
    min(
        _parse_int(
            "TELEGRAM_DOWNLOAD_WORKERS",
            _environ.get("TELEGRAM_DOWNLOAD_WORKERS", "16"),
        ),
        MAX_CONCURRENT_TRANSMISSIONS,
    ),
)

DEFAULT_METADATA    = _environ.get("DEFAULT_METADATA",    "")
DEFAULT_FILENAME    = "Little Singham"
DEFAULT_REC_DURATION = "01:00:00"
BRAND_TITLE         = "𝗟𝗜𝗧𝗧𝗟𝗘 𝗦𝗜𝗡𝗚𝗛𝗔𝗠 𝗥𝗘𝗖 𝗕𝗢𝗧"
TIMEZONE = "Asia/Kolkata"
SUPPORT_USERNAME    = "LS_Owner_bot"
SUPPORT_CHANNEL     = "LS_Owner_bot"
GROUP_CHAT_ID       = "3726271113"
# Official group used for Telegram Anonymous Admin authorization.
OFFICIAL_ANONYMOUS_GROUP_ID = -1003726271113
GROUP_INVITE_LINK   = "https://t.me/+ww77CDQwoigzYjk1"
SHORTENER_API_LINK = "https://shrinkme.io/api"
SHRINKME_API_KEY    =  "9503d9bf87c90aa9e0aab35d4dec7d1ce24c0a23"
BOT_USERNAME        = "LittlesinghamMovie_Bot"

GDRIVE_SA_JSON      = _environ.get("GDRIVE_SA_JSON",      "")
GDRIVE_FOLDER_ID    = _environ.get("GDRIVE_FOLDER_ID",    "")
GOOGLE_CLIENT_ID = "1031593100053-dnhnbmqdjudjaplo10ur24lkhe7sqndh.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

# ---------------------------------------------------------------------------

tz = pytz.timezone(TIMEZONE)


def tz_time(*args):
    return datetime.now(tz).timetuple()


logging.Formatter.converter = tz_time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%m-%Y %I:%M:%S %p " + tz.tzname(datetime.now()),
)
LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

os.makedirs(DATA_DIRECTORY, exist_ok=True)
os.makedirs(DOWNLOAD_DIRECTORY, exist_ok=True)
FAILED_UPLOADS_DIR = join(DATA_DIRECTORY, "failed_uploads")
os.makedirs(FAILED_UPLOADS_DIR, exist_ok=True)
os.makedirs(COOKIES_DIRECTORY, exist_ok=True)

RETENTION_SECONDS = max(int(RETENTION_HOURS), 0) * 3600

# ---------------------------------------------------------------------------
# Retention helpers
# ---------------------------------------------------------------------------

def _retention_label() -> str:
    h = RETENTION_HOURS
    if h <= 0:
        return "immediately"
    if h == 1:
        return "1 hour"
    return f"{h} hours"


async def _delete_message_later(message, delay: float) -> None:
    """Delete a bot message after *delay* seconds; ignore Telegram/API errors."""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


def _schedule_message_delete(message, delay: float) -> None:
    """Schedule safe auto-deletion of a Telegram message."""
    try:
        asyncio.create_task(_delete_message_later(message, delay))
    except Exception:
        pass

async def _reply_auto_delete(message, delay: float, text: str, **kwargs):
    """Reply and automatically delete the bot reply after *delay* seconds."""
    sent = await message.reply_text(text, **kwargs)
    _schedule_message_delete(sent, delay)
    return sent

AUTO_DELETE_5MIN = 5 * 60
AUTO_DELETE_30SEC = 30


def _safe_rmtree(path: str) -> None:
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path)
            LOG.info(f"Auto-deleted recording directory: {path}")
    except Exception as e:
        LOG.warning(f"Failed to remove {path}: {e}")


async def _schedule_cleanup(path: str, delay_seconds: int) -> None:
    if not path:
        return
    if delay_seconds <= 0:
        _safe_rmtree(path)
        return
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return
    _safe_rmtree(path)


def schedule_two_hour_cleanup(path: str) -> None:
    """Keep command working files on the server for exactly two hours."""
    if not path:
        return
    try:
        asyncio.get_event_loop().create_task(_schedule_cleanup(path, 2 * 60 * 60))
        LOG.info("Scheduled cleanup of %s in 2 hours", path)
    except RuntimeError:
        _safe_rmtree(path)


def schedule_retention_cleanup(path: str) -> None:
    if not path:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_schedule_cleanup(path, RETENTION_SECONDS))
        LOG.info(
            f"Scheduled cleanup of {path} in {_retention_label()}"
            if RETENTION_SECONDS > 0
            else f"Scheduled immediate cleanup of {path}"
        )
    except RuntimeError:
        _safe_rmtree(path)


def sweep_old_downloads() -> None:
    try:
        if not os.path.isdir(DOWNLOAD_DIRECTORY):
            return
        cutoff = time.time() - RETENTION_SECONDS
        removed = 0
        for entry in os.listdir(DOWNLOAD_DIRECTORY):
            full = join(DOWNLOAD_DIRECTORY, entry)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if mtime < cutoff:
                if os.path.isdir(full):
                    _safe_rmtree(full)
                else:
                    try:
                        os.remove(full)
                    except OSError as e:
                        LOG.warning(f"Failed to remove {full}: {e}")
                removed += 1
        if removed:
            LOG.info(f"Startup sweep removed {removed} expired recording entries")
    except Exception as e:
        LOG.error(f"sweep_old_downloads failed: {e}")


# ---------------------------------------------------------------------------
# JSON storage helpers
# ---------------------------------------------------------------------------

VERIFIED_FILE    = join(DATA_DIRECTORY, "verified.json")
PLANS_FILE       = join(DATA_DIRECTORY, "plans.json")
CHANNELS_FILE    = join(DATA_DIRECTORY, "channels.json")

# Static hidden-URL channel map — only the display names are ever shown to
# users; the underlying stream URL is looked up server-side and passed
# straight to FFmpeg. Never expose these values in any reply text.
CHANNEL_PLAYLIST_URL = "https://raw.githubusercontent.com/Live-Anime-Cartoon-bot/CompressBot/refs/heads/main/m3u"

# Runtime playlist cache. The playlist is fetched when /channel is opened, so
# channel names/URLs can be changed without restarting the bot.
BOT_CHANNELS = {}
CHANNEL_GROUPS = {}
CHANNEL_ENTRIES = {}
CHANNEL_PLAYLIST_LAST_ERROR = ""


def _parse_m3u_playlist(text: str) -> tuple[dict, dict, dict]:
    """Parse M3U/#EXTINF entries, preserving duplicate names and URLs.

    A URL may contain VLC-style properties after a pipe, e.g.
    URL|User-Agent=VLC/3.0.20(Android/15). Properties are retained internally
    for recording and are never shown in the channel UI.
    """
    channels = {}
    groups = {}
    entries = {}
    current = None
    used_names = {}
    index = 0
    attr_re = re.compile(r'([\w-]+)=(?:"([^"]*)"|([^\s]+))')
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.startswith("#EXTINF"):
            info = line[len("#EXTINF:"): ]
            if "," in info:
                attrs_part, display = info.rsplit(",", 1)
            else:
                attrs_part, display = info, "Channel"
            attrs = {}
            for m in attr_re.finditer(attrs_part):
                attrs[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
            name = (attrs.get("tvg-name") or display.strip() or "Channel").strip()
            group = (attrs.get("group-title") or "Other").strip() or "Other"
            current = (name, group)
            continue
        if line.startswith("#"):
            continue
        if current is None:
            continue
        raw_url = line
        name, group = current
        used_names[name] = used_names.get(name, 0) + 1
        n = used_names[name]
        unique_name = name if n == 1 else f"{name} ({n})"
        while unique_name in channels:
            used_names[name] += 1
            unique_name = f"{name} ({used_names[name]})"
        index += 1
        channels[unique_name] = raw_url
        groups.setdefault(group, []).append(unique_name)
        entries[str(index)] = {"name": unique_name, "group": group, "url": raw_url}
        current = None
    return channels, groups, entries


def _split_stream_properties(raw_url: str) -> tuple[str, dict]:
    """Split URL|Key=Value properties; currently supports User-Agent."""
    raw_url = (raw_url or "").strip()
    if "|" not in raw_url:
        return raw_url, {}
    url, prop_text = raw_url.split("|", 1)
    props = {}
    for item in re.split(r"[&;]", prop_text):
        if "=" in item:
            key, value = item.split("=", 1)
            props[key.strip().lower()] = value.strip()
    return url.strip(), props


async def _refresh_channel_playlist() -> bool:
    """Fetch a fresh playlist and never fall back to an older playlist."""
    global BOT_CHANNELS, CHANNEL_GROUPS, CHANNEL_ENTRIES, CHANNEL_PLAYLIST_LAST_ERROR
    # Always clear the previous cache first. If the new playlist cannot be
    # loaded, /channel must NOT show stale/old channels.
    BOT_CHANNELS = {}
    CHANNEL_GROUPS = {}
    CHANNEL_ENTRIES = {}
    CHANNEL_PLAYLIST_LAST_ERROR = ""
    try:
        import urllib.request
        req = urllib.request.Request(CHANNEL_PLAYLIST_URL, headers={"User-Agent": "Mozilla/5.0"})
        def _fetch():
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        text = await asyncio.to_thread(_fetch)
        channels, groups, entries = _parse_m3u_playlist(text)
        if not channels:
            raise ValueError("No #EXTINF channels found in playlist")
        BOT_CHANNELS = channels
        CHANNEL_GROUPS = groups
        CHANNEL_ENTRIES = entries
        CHANNEL_PLAYLIST_LAST_ERROR = ""
        return True
    except Exception as e:
        CHANNEL_PLAYLIST_LAST_ERROR = str(e)
        LOG.warning("Channel playlist refresh failed: %s", e)
        return False


def _channel_root_kb() -> InlineKeyboardMarkup:
    """Show playlist groups; URLs and User-Agent properties are never displayed."""
    rows, row = [], []
    for idx, group in enumerate(CHANNEL_GROUPS):
        row.append(InlineKeyboardButton(f"📁 {group}", callback_data=f"chgrp:{idx}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    if not rows:
        rows = [[InlineKeyboardButton("No channels configured", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def _channel_group_kb(group: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for idx, item in CHANNEL_ENTRIES.items():
        if item["group"] != group:
            continue
        row.append(InlineKeyboardButton(f"📺 {item['name']}", callback_data=f"ch:{idx}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="chback")])
    return InlineKeyboardMarkup(rows)

ADMIN_FILE       = join(DATA_DIRECTORY, "admins.json")
ANON_ADMIN_FILE  = join(DATA_DIRECTORY, "anonymous_admin.json")
PREMIUM_FILE     = join(DATA_DIRECTORY, "premium.json")
AUDIO_NAME_FILE     = join(DATA_DIRECTORY, "audio_brand_name.txt")
WATERMARK_NAME_FILE    = join(DATA_DIRECTORY, "watermark_name.txt")
WATERMARK_IMG_FILE     = join(DATA_DIRECTORY, "watermark_img.txt")
WATERMARK_SIZE_FILE    = join(DATA_DIRECTORY, "watermark_size.txt")
WATERMARK_IMG_CACHE    = join(DATA_DIRECTORY, "watermark_logo.png")
DEFAULT_WATERMARK_IMG_URL = "https://iili.io/CuMJCjn.md.png"
GROUP_CONFIG_FILE   = join(DATA_DIRECTORY, "group_config.json")

# Runtime-mutable group ID (persists to GROUP_CONFIG_FILE)
# File overrides the env var so owner can update without redeploying.
try:
    with open(GROUP_CONFIG_FILE, "r") as _gcf:
        _gcd = json.load(_gcf)
        if _gcd.get("group_chat_id"):
            GROUP_CHAT_ID = int(_gcd["group_chat_id"])
        if _gcd.get("group_invite_link"):
            GROUP_INVITE_LINK = _gcd["group_invite_link"]
except Exception:
    pass   # use env-var values already set above


def _save_group_config():
    """Persist current GROUP_CHAT_ID and GROUP_INVITE_LINK to disk."""
    try:
        with open(GROUP_CONFIG_FILE, "w") as _f:
            json.dump({
                "group_chat_id":   GROUP_CHAT_ID,
                "group_invite_link": GROUP_INVITE_LINK,
            }, _f)
    except Exception as _e:
        pass


def get_default_watermark() -> str:
    """Return saved default watermark text, fallback to BRAND_TITLE."""
    try:
        with open(WATERMARK_NAME_FILE, "r", encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    except FileNotFoundError:
        pass
    return BRAND_TITLE


def set_default_watermark(name: str) -> None:
    """Persist default watermark text to disk (takes effect on next recording)."""
    with open(WATERMARK_NAME_FILE, "w", encoding="utf-8") as fh:
        fh.write(name.strip())


def get_default_watermark_img_url() -> str:
    try:
        with open(WATERMARK_IMG_FILE, "r", encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    except FileNotFoundError:
        pass
    return DEFAULT_WATERMARK_IMG_URL


def set_default_watermark_img_url(url: str) -> None:
    with open(WATERMARK_IMG_FILE, "w", encoding="utf-8") as fh:
        fh.write(url.strip())
    if os.path.exists(WATERMARK_IMG_CACHE):
        try:
            os.remove(WATERMARK_IMG_CACHE)
        except Exception:
            pass


def get_watermark_size() -> int:
    """Return saved watermark logo width in pixels, default 250."""
    try:
        with open(WATERMARK_SIZE_FILE, "r", encoding="utf-8") as fh:
            v = fh.read().strip()
            if v.isdigit():
                return max(20, min(int(v), 500))
    except FileNotFoundError:
        pass
    return 250


def set_watermark_size(px: int) -> None:
    """Persist watermark logo width to disk."""
    with open(WATERMARK_SIZE_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(max(20, min(px, 500))))


def _watermark_img_path() -> str | None:
    """Return local cache path of watermark image if it exists on disk, else None."""
    return WATERMARK_IMG_CACHE if os.path.exists(WATERMARK_IMG_CACHE) else None


async def _async_ensure_watermark_img() -> bool:
    """Download watermark image to local cache (idempotent). Returns True if available."""
    if os.path.exists(WATERMARK_IMG_CACHE) and os.path.getsize(WATERMARK_IMG_CACHE) > 0:
        return True
    url = get_default_watermark_img_url()
    rc, _, _err = await runcmd(
        f'curl -L -s --max-time 20 -o {shlex.quote(WATERMARK_IMG_CACHE)} {shlex.quote(url)}'
    )
    ok = rc == 0 and os.path.exists(WATERMARK_IMG_CACHE) and os.path.getsize(WATERMARK_IMG_CACHE) > 0
    if not ok:
        LOG.warning("Watermark image download failed: %s", _err[:300])
    return ok


def get_audio_brand_name() -> str:
    """Return saved audio brand name, fallback to BRAND_TITLE."""
    try:
        with open(AUDIO_NAME_FILE, "r", encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    except FileNotFoundError:
        pass
    return BRAND_TITLE


def set_audio_brand_name(name: str) -> None:
    """Persist audio brand name to disk (takes effect on next recording)."""
    with open(AUDIO_NAME_FILE, "w", encoding="utf-8") as fh:
        fh.write(name.strip())

def _audio_title_parts(template: str = "", language: str = "", fallback_title: str = "") -> tuple[str, str, str]:
    """Build the requested audio metadata format: brand|audio_language|title.

    A supplied three-part template may use the literal audio_language placeholder;
    otherwise the detected language is used for the middle field.
    """
    raw = (template or "").strip()
    if "|" in raw:
        parts = [part.strip() for part in raw.split("|", 2)]
        while len(parts) < 3:
            parts.append("")
        brand = parts[0] or get_audio_brand_name()
        lang = parts[1]
        title = parts[2] or fallback_title or "Video"
        if not lang or lang.casefold() in {"audio_language", "language", "lang"}:
            lang = language or "Untagged"
        return brand, lang, title
    # Backward compatible: a single argument is the channel/brand name.
    return raw or get_audio_brand_name(), language or "Untagged", fallback_title or "Video"


def _audio_display_language(track: dict | None, fallback: str = "") -> str:
    """Return a human-readable language label from a probed audio track."""
    track = track or {}
    raw = str(track.get("lang") or track.get("language") or fallback or "und").strip()
    code = raw.lower()[:3]
    return LANG_LABEL.get(code, raw if raw and raw.lower() != "und" else "Untagged")


OWNER_AUDIO_BRAND = "@LittleSinghamchannel"
OWNER_AUDIO_TITLE = "Anime Cartoon"
USER_AUDIO_BRAND = "𝗟𝗜𝗧𝗧𝗟𝗘 𝗦𝗜𝗡𝗚𝗛𝗔𝗠 𝗥𝗘𝗖 𝗕𝗢𝗧"


def _audio_metadata_args(
    track_count: int,
    tracks: list | None,
    brand_template: str,
    fallback_title: str,
    user_id: int | None = None,
) -> list[str]:
    """Build role-specific audio metadata for each output audio stream.

    Owner: @LittleSinghamchannel|Language|Anime Cartoon
    Other users: 𝗟𝗜𝗧𝗧𝗟𝗘 𝗦𝗜𝗡𝗚𝗛𝗔𝗠 𝗥𝗘𝗖 𝗕𝗢𝗧|Language
    """
    result: list[str] = []
    tracks = tracks or []
    is_actual_owner = user_id is not None and int(user_id) in OWNER_IDS
    for out_idx in range(max(0, int(track_count))):
        track = tracks[out_idx] if out_idx < len(tracks) and isinstance(tracks[out_idx], dict) else {}
        language = _audio_display_language(track)
        if is_actual_owner:
            combined = f"{OWNER_AUDIO_BRAND}|{language}|{OWNER_AUDIO_TITLE}"
        else:
            combined = f"{USER_AUDIO_BRAND}|{language}"
        result += [f"-metadata:s:a:{out_idx}", f"title={combined}",
                   f"-metadata:s:a:{out_idx}", f"handler_name={combined}"]
        lang_code = str(track.get("lang") or track.get("language") or "").strip().lower()
        if lang_code and lang_code != "und":
            result += [f"-metadata:s:a:{out_idx}", f"language={lang_code[:3]}"]
    return result


def _load_json(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        LOG.warning(f"Failed to load {path}: {e}")
        return default


def _save_json(path: str, data) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        LOG.error(f"Failed to save {path}: {e}")


def load_verified() -> dict:
    return _load_json(VERIFIED_FILE, {"verified": {}, "pending": {}})


def save_verified(data: dict) -> None:
    _save_json(VERIFIED_FILE, data)


def is_verified(user_id: int) -> bool:
    # Anonymous Admin uses the official group ID as its internal actor ID.
    # Treat every owner-like actor consistently so commands such as
    # /download do not pass recording but fail the verification gate.
    if is_owner(user_id):
        return True
    if is_admin(user_id):
        return True
    data = load_verified()
    entry = data.get("verified", {}).get(str(user_id))
    if not entry:
        return False
    expires = entry.get("expires_at")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires)
            if datetime.now(tz) > exp_dt:
                return False
        except Exception:
            return True
    return True


def load_plans() -> list:
    default = [
        {"name": "Free Trial",  "price": "Free",          "duration": "3 days",
         "features": ["Up to 3 recordings", "Max 30 minutes per recording", "Standard quality (MKV)"]},
        {"name": "Basic",       "price": "$5 / month",    "duration": "30 days",
         "features": ["Unlimited recordings", "Max 2 hours per recording", "Original quality preserved", "Email support"]},
        {"name": "Pro",         "price": "$12 / month",   "duration": "30 days",
         "features": ["Unlimited recordings", "Max 6 hours per recording", "Original quality + auto-thumbnails", "Priority support", "Early access to new channels"]},
        {"name": "Lifetime",    "price": "$99 one-time",  "duration": "Forever",
         "features": ["Everything in Pro", "Lifetime access", "Custom channel requests", "Direct support line"]},
    ]
    return _load_json(PLANS_FILE, default)


def load_channels() -> dict:
    return _load_json(CHANNELS_FILE, {"categories": {}})


# ---------------------------------------------------------------------------
# Pyrogram client
# ---------------------------------------------------------------------------

app = Client(
    "recorder",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=DATA_DIRECTORY,
    sleep_threshold=60,               # auto-sleep on FloodWait ≤60s
    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
)

# ---------------------------------------------------------------------------
# Shared runtime state
# ---------------------------------------------------------------------------

user_status:        dict = {}

user_tasks:         dict = {}

# Dedicated multi-job tracking for selected commands only.
# /download /compress /Compressadvance /screenshot /audiotrack /merge
SIX_JOB_MAX_PER_USER = 5
SIX_JOB_MAX_GLOBAL = 6
SPECIAL_JOB_TYPES = {"audiotrack", "watermark"}
SPECIAL_JOB_MAX_PER_USER = 10
SPECIAL_JOB_MAX_GLOBAL = 10

_six_active_jobs = {}       # job_id -> {"user_id": int, "type": str, "task": asyncio.Task}
_six_user_active = {}       # user_id -> set(job_id)

def _six_prune_finished():
    """Remove slots whose owning async handler is missing or has finished."""
    for _job_id, _info in list(_six_active_jobs.items()):
        _task = _info.get("task")
        if _task is None or _task.done():
            _six_release(_job_id)

def _six_job_count(user_id, job_types=None):
    _six_prune_finished()
    job_ids = _six_user_active.get(int(user_id), set())
    if job_types is None:
        return len(job_ids)
    return sum(
        1 for job_id in job_ids
        if _six_active_jobs.get(job_id, {}).get("type") in job_types
    )

def _six_global_count(job_types=None):
    _six_prune_finished()
    if job_types is None:
        return len(_six_active_jobs)
    return sum(
        1 for info in _six_active_jobs.values()
        if info.get("type") in job_types
    )

def _six_acquire(user_id, job_type):
    import uuid

    uid = int(user_id)
    is_special = job_type in SPECIAL_JOB_TYPES
    limit_per_user = SPECIAL_JOB_MAX_PER_USER if is_special else SIX_JOB_MAX_PER_USER
    limit_global = SPECIAL_JOB_MAX_GLOBAL if is_special else SIX_JOB_MAX_GLOBAL
    bucket = SPECIAL_JOB_TYPES if is_special else None
    user_count = _six_job_count(uid, bucket)

    if user_count >= limit_per_user:
        return None, (
            f"⛔ **Maximum {limit_per_user} active jobs allowed per user for this command group.**\n"
            f"Your active jobs: `{user_count}/{limit_per_user}`"
        )

    global_count = _six_global_count(bucket)

    if global_count >= limit_global:
        return None, (
            "⏳ **Server busy.**\n"
            f"Global active jobs: `{global_count}/{limit_global}`\n"
            f"Your active jobs: `{user_count}/{limit_per_user}`\n"
            "Please try again when a slot is free."
        )

    job_id = f"{job_type}:{uid}:{uuid.uuid4().hex}"

    try:
        _owner_task = asyncio.current_task()
    except RuntimeError:
        _owner_task = None

    _six_active_jobs[job_id] = {
        "user_id": uid,
        "type": job_type,
        "task": _owner_task,
        "started_at": time.time(),
    }
    _six_user_active.setdefault(uid, set()).add(job_id)

    return job_id, None

def _six_release(job_id):
    if not job_id:
        return

    info = _six_active_jobs.pop(job_id, None)
    if not info:
        return

    uid = int(info["user_id"])
    jobs = _six_user_active.get(uid)

    if jobs:
        jobs.discard(job_id)

        if not jobs:
            _six_user_active.pop(uid, None)

rec_setup_sessions: dict = {}   # user_id -> setup dict
_wm_text_pending:   set  = set()
user_ffmpeg_pids:   dict = {}
progress_tasks:     dict = {}
cancelled_users:    set  = set()

MAX_CONCURRENT_REC  = 5
active_recs:        dict = {}   # {user_id: {rec_id: {"status", "ffmpeg_pid", "progress_task", "start"}}}
cancelled_recs:     set  = set()  # set of (user_id, rec_id)
pending_uploads:    dict = {}   # {(user_id, rec_id): upload state dict}
wm3_quality_waiters: dict = {}  # (user_id, rec_id) -> Future


def _recording_elapsed(entry: dict, duration: float) -> float:
    """Return captured time only; exclude stream probing and FFmpeg startup."""
    captured = max(0.0, float(entry.get("recorded_seconds", 0.0) or 0.0))
    segment_started_at = entry.get("segment_started_at")
    if segment_started_at is not None:
        captured += max(0.0, time.monotonic() - segment_started_at)
    return min(max(0.0, float(duration)), captured)


pending_cookies_users: dict = {}
pending_cookie_archives: dict = {}  # user_id -> temporary ZIP selection state
ott_progress:       dict = {}
ott_sessions:       dict = {}   # {user_id: {url, filename, fmt, audio_lang, msg}}
user_dl_prefs:      dict = {}   # {user_id: {"default_audio_lang": "tamil"/"hindi"/"eng"/…}}
pending_upload_state: dict = {}  # {user_id: upload-choice state for /trim /merge /watermark /download}
_wm_pos_sessions:   dict = {}   # {(user_id, session_id): watermark setup}
compress_jobs:      dict = {}
reclink_jobs:       dict = {}
ss_jobs:            dict = {}
ffmpeg_jobs:        dict = {}   # task_id -> job state for /ffmpeg task mode
merge_sessions:     dict = {}
title_jobs:         dict = {}
relay_map:          dict = {}   # {forwarded_msg_id_in_owner_chat: original_user_id}
relay_enabled:      bool = True # owner can toggle via /relay on|off
relay_blocked:      set  = set() # user IDs blocked from relay via /DM <id> delete

_BOT_START_TIME: float = time.time()  # for uptime display

# ---------------------------------------------------------------------------
# Auth filter
# ---------------------------------------------------------------------------

def _is_official_anonymous_admin(message) -> bool:
    """Allow Anonymous Admin messages from the official group only."""
    if not is_anonymous_admin_enabled():
        return False

    chat = getattr(message, "chat", None)
    sender_chat = getattr(message, "sender_chat", None)

    chat_id = getattr(chat, "id", None)
    sender_chat_id = getattr(sender_chat, "id", None)

    if chat_id != OFFICIAL_ANONYMOUS_GROUP_ID:
        return False

    chat_type = str(getattr(chat, "type", "")).lower()
    if chat_type not in ("group", "supergroup", "chattype.supergroup"):
        return False

    # Telegram Anonymous Admin messages normally expose the group
    # itself as sender_chat and do not provide from_user.
    if sender_chat_id == OFFICIAL_ANONYMOUS_GROUP_ID:
        return True

    # Some Pyrogram message variants may omit sender_chat, while
    # from_user is also None for Anonymous Admin.
    if sender_chat is None and getattr(message, "from_user", None) is None:
        return True

    return False


def _message_actor_id(message) -> int:
    """Return a stable actor ID for normal and Anonymous Admin messages."""
    if _is_official_anonymous_admin(message):
        return OFFICIAL_ANONYMOUS_GROUP_ID
    return int(getattr(getattr(message, "from_user", None), "id", 0) or 0)


def _callback_actor_matches(cq, uid: int) -> bool:
    """Authorize normal users and the official Anonymous Admin wizard."""
    clicker_id = getattr(getattr(cq, "from_user", None), "id", 0) or 0
    if clicker_id == uid:
        return True
    if uid == OFFICIAL_ANONYMOUS_GROUP_ID and is_anonymous_admin_enabled():
        msg = getattr(cq, "message", None)
        chat = getattr(msg, "chat", None)
        return bool(chat and getattr(chat, "id", None) == OFFICIAL_ANONYMOUS_GROUP_ID)
    return False


def _is_control_actor(message) -> bool:
    """Allow owners, configured admins, and enabled Anonymous Admin."""
    uid = _message_actor_id(message)
    return bool(
        _is_official_anonymous_admin(message)
        or is_owner(uid)
        or is_admin(uid)
    )


def _is_official_group_message(message) -> bool:
    chat = getattr(message, "chat", None)
    return bool(chat and getattr(chat, "id", None) == OFFICIAL_ANONYMOUS_GROUP_ID)


def _admin_auth_filter(_filter, _client, message):
    """Allow configured users/admins and the official Anonymous Admin path."""
    uid = getattr(getattr(message, "from_user", None), "id", 0)
    if uid and (is_owner(uid) or is_admin(uid)):
        return True
    if _is_official_anonymous_admin(message):
        return True
    return False


def _auth_filter():
    admin_filter = filters.create(_admin_auth_filter, name="AdminAuth")
    if AUTH_USERS:
        return filters.user(AUTH_USERS) | filters.user(OWNER_IDS or []) | admin_filter
    return filters.all | admin_filter


AUTH = _auth_filter()


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS or (
        user_id == OFFICIAL_ANONYMOUS_GROUP_ID
        and is_anonymous_admin_enabled()
    )


# ---------------------------------------------------------------------------
# Admin system
# ---------------------------------------------------------------------------

def load_admins() -> list:
    return _load_json(ADMIN_FILE, [])


def save_admins(data: list) -> None:
    _save_json(ADMIN_FILE, data)


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


def is_anonymous_admin_enabled() -> bool:
    """Return the persisted Anonymous Admin mode state."""
    data = _load_json(ANON_ADMIN_FILE, {"enabled": True})
    return bool(data.get("enabled", False)) if isinstance(data, dict) else False


def set_anonymous_admin(enabled: bool) -> None:
    """Persist Anonymous Admin mode state."""
    _save_json(ANON_ADMIN_FILE, {"enabled": bool(enabled)})


def anonymous_admin_label(user_id: int | None = None) -> str:
    """Display-safe label for bot-generated admin UI/messages."""
    if is_anonymous_admin_enabled() and user_id is not None:
        return "🛡 Anonymous Admin"
    return "🛡 Admin"


def add_admin(user_id: int) -> bool:
    """Add user to admin list. Returns False if already admin."""
    admins = load_admins()
    if user_id in admins:
        return False
    admins.append(user_id)
    save_admins(admins)
    return True


def del_admin(user_id: int) -> bool:
    """Remove user from admin list. Returns False if not found."""
    admins = load_admins()
    if user_id not in admins:
        return False
    admins.remove(user_id)
    save_admins(admins)
    return True


# ---------------------------------------------------------------------------
# Premium system  (bot/data/premium.json)
# ---------------------------------------------------------------------------
# Schema:
#   { "users": { "<user_id>": { "plan", "added_by", "added_at", "expires_at" } } }
# expires_at = None  →  lifetime / no expiry
# ---------------------------------------------------------------------------

_PREMIUM_PLANS = ["Free Trial", "Basic", "Standard", "Pro", "Lifetime"]


def load_premium() -> dict:
    return _load_json(PREMIUM_FILE, {"users": {}})


def save_premium(data: dict) -> None:
    _save_json(PREMIUM_FILE, data)


def is_premium(user_id: int) -> bool:
    """Return True when user has an active (non-expired) premium entry."""
    if user_id in OWNER_IDS or is_admin(user_id):
        return True
    data = load_premium()
    entry = data.get("users", {}).get(str(user_id))
    if not entry:
        return False
    exp = entry.get("expires_at")
    if exp is None:
        return True   # lifetime
    try:
        return datetime.now(tz) < datetime.fromisoformat(exp)
    except Exception:
        return True


def add_premium(user_id: int, days: int | None, plan: str, added_by: int) -> dict:
    """
    Add / update premium for user_id.
    days=None  →  lifetime (no expiry).
    Returns the saved entry dict.
    """
    data  = load_premium()
    now   = datetime.now(tz)
    entry = {
        "plan":       plan,
        "added_by":   added_by,
        "added_at":   now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat() if days is not None else None,
    }
    data.setdefault("users", {})[str(user_id)] = entry
    save_premium(data)
    return entry


def remove_premium(user_id: int) -> bool:
    """Expire a user's premium immediately. Returns False if not found."""
    data = load_premium()
    key  = str(user_id)
    if key not in data.get("users", {}):
        return False
    # Set expires_at to now (expired) instead of deleting — keeps history
    data["users"][key]["expires_at"] = datetime.now(tz).isoformat()
    data["users"][key]["expired_by"] = "manual"
    save_premium(data)
    return True


def _premium_status_line(uid_str: str, entry: dict) -> str:
    """One-line status for premium list output."""
    exp = entry.get("expires_at")
    plan = entry.get("plan", "—")
    if exp is None:
        exp_str = "♾️ Lifetime"
    else:
        try:
            exp_dt  = datetime.fromisoformat(exp)
            now     = datetime.now(tz)
            if now >= exp_dt:
                exp_str = "❌ Expired"
            else:
                remaining = (exp_dt - now).days
                exp_str   = f"✅ {exp_dt.strftime('%Y-%m-%d')} (+{remaining}d)"
        except Exception:
            exp_str = str(exp)
    return f"• `{uid_str}` — **{plan}** | {exp_str}"


# ---------------------------------------------------------------------------
# Group membership gate
# ---------------------------------------------------------------------------

# In-memory cache: {user_id: (is_member, expires_at)}
_member_cache: dict = {}
_MEMBER_CACHE_TTL = 180  # seconds


async def is_group_member(client, user_id: int) -> bool:
    """
    Return True if user_id is a member of GROUP_CHAT_ID.
    Owners and admins always return True.
    Returns True when GROUP_CHAT_ID is not configured (gate disabled).
    """
    if not GROUP_CHAT_ID:
        return True
    if is_owner(user_id) or is_admin(user_id):
        return True

    cached = _member_cache.get(user_id)
    if cached and cached[1] > time.time():
        return cached[0]

    try:
        from pyrogram.enums import ChatMemberStatus
        member = await client.get_chat_member(GROUP_CHAT_ID, user_id)
        result = member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        result = True   # fail-open: don't block user if Telegram API has a hiccup

    _member_cache[user_id] = (result, time.time() + _MEMBER_CACHE_TTL)
    return result


def invalidate_member_cache(user_id: int) -> None:
    """Force re-check on next request (e.g. after admin add/remove)."""
    _member_cache.pop(user_id, None)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def time_to_seconds(time_str: str) -> int:
    try:
        parts = time_str.split(":")
        parts = [int(p) for p in parts]
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]
        else:
            return 0
        return h * 3600 + m * 60 + s
    except Exception:
        return 0


def TimeFormatter(milliseconds: int) -> str:
    seconds, _ms = divmod(int(milliseconds), 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_  = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02}:{min_:02}:{sec:02}"
    return f"{min_:02}:{sec:02}"


def _parse_duration_token(tok: str) -> int:
    tok = (tok or "").strip().lower()
    if not tok:
        return 0
    if ":" in tok:
        parts = tok.split(":")
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            return 0
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]
        else:
            return 0
        return h * 3600 + m * 60 + s
    m = re.fullmatch(r"(\d+)([smh]?)", tok)
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def _seconds_to_hms(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ---------------------------------------------------------------------------
# Stream probe
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_M3U8_RE = re.compile(
    r"""(?xi)
    (?P<url>
        (?:https?:)?//[^\s'"<>()\\]+?\.m3u8(?:\?[^\s'"<>()\\]*)?
        |
        /[^\s'"<>()\\]+?\.m3u8(?:\?[^\s'"<>()\\]*)?
    )
    """
)


async def probe_stream(url: str, timeout: float = 8.0, _depth: int = 0) -> dict:
    from urllib.parse import urljoin, urlparse
    from urllib.request import Request, urlopen

    def _fetch(target_url: str, page_referer: str = "") -> dict:
        parsed = urlparse(target_url)
        host_referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme else ""
        req = Request(
            target_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": page_referer or host_referer, "Accept": "*/*"},
            method="GET",
        )
        with urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl() or target_url
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body  = resp.read(512 * 1024)
            return {"final_url": final_url, "ctype": ctype, "body": body}

    def _probe(target_url: str) -> dict:
        parsed = urlparse(target_url)
        host_referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme else ""
        result = {"is_hls": False, "final_url": target_url, "referer": host_referer,
                  "user_agent": DEFAULT_USER_AGENT, "extracted_from": None}
        try:
            fetched   = _fetch(target_url)
            final_url = fetched["final_url"]
            ctype     = fetched["ctype"]
            body      = fetched["body"]
            result["final_url"] = final_url
            final_parsed = urlparse(final_url)
            if final_parsed.scheme and final_parsed.netloc:
                result["referer"] = f"{final_parsed.scheme}://{final_parsed.netloc}/"
            head_text = body[:2048].decode("utf-8", errors="ignore").lstrip()
            if "mpegurl" in ctype or "m3u8" in ctype or head_text.startswith("#EXTM3U"):
                result["is_hls"] = True
                return result
            looks_textual = ("html" in ctype or "javascript" in ctype or "json" in ctype
                             or "text" in ctype or not ctype)
            if not looks_textual:
                return result
            text  = body.decode("utf-8", errors="ignore")
            match = _M3U8_RE.search(text)
            if not match:
                return result
            raw = match.group("url")
            if raw.startswith("//"):
                scheme    = final_parsed.scheme or "https"
                extracted = f"{scheme}:{raw}"
            elif raw.startswith("/"):
                extracted = urljoin(final_url, raw)
            else:
                extracted = raw
            LOG.info(f"Extracted m3u8 from page {final_url}: {extracted[:100]}")
            result["extracted_from"]  = final_url
            result["_extracted_url"]  = extracted
            return result
        except Exception as e:
            LOG.warning(f"Stream probe failed for {target_url}: {e}")
            return result

    first     = await asyncio.to_thread(_probe, url)
    extracted = first.pop("_extracted_url", None)
    if extracted and _depth == 0:
        page_url = first["final_url"]
        nested   = await probe_stream(extracted, timeout=timeout, _depth=1)
        if nested["is_hls"]:
            nested["extracted_from"] = page_url
            nested["referer"]        = page_url
            return nested
    return first


# ---------------------------------------------------------------------------
# Shell / FFprobe helpers
# ---------------------------------------------------------------------------

async def runcmd(cmd: str) -> Tuple[int, str, str]:
    args    = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(errors="ignore"), stderr.decode(errors="ignore")


async def _parallel_download_telegram_media(
    client: Client,
    media,
    file_path: str,
    progress=None,
    progress_args: tuple = (),
) -> str:
    """Download Telegram media using Pyrogram's native downloader.

    The previous custom range downloader could finish a range with zero bytes
    on some Telegram file/CDN responses. Pyrogram's native downloader handles
    file references, offsets, CDN redirects and media sessions itself.
    """
    file_id_text = getattr(media, "file_id", None)
    download_target = file_id_text or media

    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    downloaded = await client.download_media(
        download_target,
        file_name=file_path,
        progress=progress,
        progress_args=progress_args,
    )

    if not downloaded or not os.path.exists(downloaded):
        raise IOError("Pyrogram could not download the Telegram media.")

    actual_size = os.path.getsize(downloaded)
    expected_size = int(getattr(media, "file_size", 0) or 0)
    if actual_size <= 0:
        raise IOError("Telegram media download returned an empty file.")
    if expected_size and actual_size != expected_size:
        raise IOError(
            f"Telegram media download incomplete: expected {expected_size} bytes, "
            f"received {actual_size} bytes"
        )

    return downloaded


async def get_video_duration(input_file: str) -> int:
    try:
        parser   = createParser(input_file)
        if not parser:
            return 0
        metadata = extractMetadata(parser)
        if not metadata or not metadata.has("duration"):
            return 0
        return int(metadata.get("duration").seconds)
    except Exception as e:
        LOG.warning(f"Hachoir failed: {e}")
        return 0


async def take_stream_snapshot(
    url: str,
    out_path: str,
    is_hls: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
    referer: str = "",
    cookie: str = "",
) -> bool:
    """Capture one live frame using a safe FFmpeg argument array."""
    try:
        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            "-user_agent", user_agent or DEFAULT_USER_AGENT,
            "-probesize", "5000000", "-analyzeduration", "5000000",
        ]
        headers = []
        if cookie:
            headers.append(f"Cookie: {cookie}")
        if referer:
            headers.append(f"Referer: {referer}")
        if headers:
            args += ["-headers", "".join(h + "\r\n" for h in headers)]
        if is_hls:
            args += ["-f", "hls", "-allowed_extensions", "ALL"]
        args += ["-i", url, "-an", "-frames:v", "1", "-q:v", "2", out_path]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=25)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.communicate()
            return False
        return proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        LOG.debug("Live snapshot failed: %s", e)
        return False


async def _stop_live_preview(setup: dict) -> None:
    """Stop and clean preview captures without deleting the shared status message."""
    setup["preview_active"] = False

    preview_msg = setup.pop("preview_msg", None)

    task = setup.pop("preview_task", None)
    if task and task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    preview_dir = setup.get("preview_dir")
    if preview_dir and os.path.isdir(preview_dir):
        try:
            shutil.rmtree(preview_dir)
        except OSError:
            pass


async def _start_live_preview(client: Client, setup: dict) -> bool:
    """Capture one preview only when the user presses Gen Preview.

    The uploaded temporary JPEG and its directory are removed immediately
    after Telegram finishes sending/editing the preview. No background
    10-second refresh task is created.
    """
    if setup.get("preview_starting"):
        return True

    start_lock = setup.setdefault("preview_start_lock", asyncio.Lock())
    async with start_lock:
        if setup.get("preview_starting"):
            return True
        setup["preview_starting"] = True

    chat_id = setup["chat_id"]
    preview_dir = join(
        DATA_DIRECTORY, "live_previews", str(setup["user_id"]),
        str(int(time.time() * 1000))
    )
    os.makedirs(preview_dir, exist_ok=True)
    setup["preview_dir"] = preview_dir
    path = join(preview_dir, "preview.jpg")

    try:
        target = setup.get("effective_url") or setup["url"]
        hls = bool(setup.get("is_hls", ".m3u8" in target.lower()))
        ok = await take_stream_snapshot(
            target, path, hls,
            user_agent=setup.get("flag_ua") or DEFAULT_USER_AGENT,
            referer=setup.get("flag_referer") or "",
            cookie=setup.get("flag_cookie") or "",
        )
        if not ok:
            return False

        status_msg = setup.get("status_msg")
        if status_msg is None:
            try:
                status_msg = await client.get_messages(chat_id, setup.get("setup_msg_id"))
            except Exception as e:
                LOG.warning("Could not load the status message for preview: %s", e)
                return False
        if not status_msg:
            return False

        status_lock = setup.setdefault("status_edit_lock", asyncio.Lock())
        async with status_lock:
            caption = setup.get("current_status_text") or _audio_step_text(setup)
            markup = setup.get("current_reply_markup")
            if getattr(status_msg, "photo", None):
                await client.edit_message_media(
                    chat_id, status_msg.id,
                    InputMediaPhoto(path, caption=caption),
                    reply_markup=markup,
                )
            else:
                replacement_msg = await client.send_photo(
                    chat_id, path, caption=caption, reply_markup=markup
                )
                try:
                    await status_msg.delete()
                except Exception:
                    LOG.warning("Preview card sent, but the old text status could not be deleted.")
                status_msg = replacement_msg

            setup["status_msg"] = status_msg
            setup["setup_msg_id"] = status_msg.id
            setup["preview_msg"] = status_msg
            setup["preview_active"] = False
            active_entry = setup.get("active_rec_entry")
            if active_entry is not None:
                active_entry["is_photo_msg"] = True
                active_entry["status_msg"] = status_msg
        return True
    except Exception as e:
        LOG.warning("One-shot preview generation failed: %s", e)
        return False
    finally:
        setup["preview_starting"] = False
        try:
            if os.path.isdir(preview_dir):
                shutil.rmtree(preview_dir)
        except OSError as e:
            LOG.warning("Could not auto-delete temporary preview files: %s", e)


async def _edit_message_content(message, text: str, reply_markup=None, edit_lock=None):
    """Edit the text or caption of the same Telegram status message."""
    async def _edit():
        try:
            if getattr(message, "photo", None):
                return await message.edit_caption(text, reply_markup=reply_markup)
            return await message.edit_text(text, reply_markup=reply_markup)
        except Exception as exc:
            error_id = getattr(exc, "ID", "")
            error_text = str(exc).upper()
            if (
                error_id == "MESSAGE_NOT_MODIFIED"
                or exc.__class__.__name__ == "MessageNotModified"
                or "MESSAGE_NOT_MODIFIED" in error_text
            ):
                return None
            raise

    if edit_lock is None:
        return await _edit()
    async with edit_lock:
        return await _edit()


async def _edit_setup_message(message, setup: dict, text: str, reply_markup=None):
    """Keep wizard text, buttons, and an optional preview on one message."""
    lock = setup.setdefault("status_edit_lock", asyncio.Lock())
    async with lock:
        status_msg = setup.get("status_msg") or message
        setup["current_status_text"] = text
        setup["current_reply_markup"] = reply_markup
        await _edit_message_content(status_msg, text, reply_markup=reply_markup)
        setup["status_msg"] = status_msg
        setup["setup_msg_id"] = status_msg.id


async def _update_live_preview_url(setup: dict, effective_url: str, is_hls: bool) -> None:
    """Switch the refresh loop to the probed/effective stream URL."""
    setup["effective_url"] = effective_url
    setup["is_hls"] = is_hls


def _rec_progress_kb(user_id: int, rec_id: int) -> InlineKeyboardMarkup:
    entry = active_recs.get(user_id, {}).get(rec_id, {})
    rows = []
    if entry.get("ffmpeg_pid"):
        rows.append([
            InlineKeyboardButton(
                "🎬 Gen Preview",
                callback_data=f"rec_prev:{user_id}:{rec_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "🔄 Refresh Progress",
            callback_data=f"rec_ref:{user_id}:{rec_id}",
        ),
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"rec_cxl:{user_id}:{rec_id}",
        ),
    ])
    return InlineKeyboardMarkup(rows)


async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = (f'ffprobe -v error -show_entries format=duration '
               f'-of default=noprint_wrappers=1:nokey=1 "{input_file}"')
        retcode, out, _err = await runcmd(cmd)
        if retcode == 0 and out.strip():
            return int(float(out.strip()))
    except Exception as e:
        LOG.warning(f"FFprobe failed: {e}")
    return 0


async def _ffprobe_video(path: str) -> dict:
    probe_cmd = (f'ffprobe -v error -hide_banner -print_format json '
                 f'-show_format -show_streams {shlex.quote(path)}')
    rc, out, err = await runcmd(probe_cmd)
    if rc != 0:
        raise Exception(f"ffprobe failed: {err.strip() or 'no stderr'}")
    data         = json.loads(out or "{}")
    duration     = float(data.get("format", {}).get("duration") or 0)
    video_height = 0
    audio_streams: list = []
    subtitle_streams: list = []
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not video_height:
            video_height = int(s.get("height") or 0)
        elif s.get("codec_type") == "audio":
            tags = s.get("tags") or {}
            lang = (tags.get("language") or "und").lower()[:3]
            audio_streams.append({
                "index":    s["index"],
                "lang":     lang,
                "codec":    s.get("codec_name", "?"),
                "channels": s.get("channels", 2),
            })
        elif s.get("codec_type") == "subtitle":
            tags = s.get("tags") or {}
            lang = (tags.get("language") or "und").lower()[:3]
            subtitle_streams.append({
                "index": s["index"],
                "lang": lang,
                "codec": s.get("codec_name", "?"),
            })
    return {
        "duration": duration,
        "video_height": video_height,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
    }


async def _validate_video_decode(path: str) -> tuple[bool, str]:
    """Reject partial files that probe as containers but cannot decode video."""
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return False, "missing or empty file"

    try:
        info = await _ffprobe_video(path)
    except Exception:
        return False, "ffprobe failed"

    if info.get("video_height", 0) <= 0 or info.get("duration", 0) <= 0:
        return False, "no usable video stream"

    command = (
        f'ffmpeg -hide_banner -loglevel error -nostats -xerror '
        f'-err_detect explode -i {shlex.quote(path)} '
        f'-map 0:v:0 -an -sn -dn -f null -'
    )
    rc, _stdout, _stderr = await runcmd(command)
    if rc != 0:
        return False, "video decode errors"
    return True, "video decoded successfully"


async def _recover_partial_recording(path: str) -> tuple[bool, str]:
    """Validate a cancelled recording and re-encode damaged video frames.

    Audio, subtitle, and other streams are copied when possible; only video
    is re-encoded. The original is replaced only after the new file passes a
    full video decode check.
    """
    is_valid, reason = await _validate_video_decode(path)
    if is_valid:
        return True, reason

    recovered_path = os.path.join(
        os.path.dirname(path),
        f"recovered_{os.path.basename(path)}",
    )
    command = (
        f'ffmpeg -hide_banner -loglevel error -nostats -y '
        f'-fflags +discardcorrupt+genpts -err_detect ignore_err '
        f'-i {shlex.quote(path)} -map 0 -map_metadata 0 '
        f'-c copy -c:v libx264 -preset veryfast -crf 23 '
        f'-pix_fmt yuv420p -max_muxing_queue_size 4096 '
        f'-avoid_negative_ts make_zero {shlex.quote(recovered_path)}'
    )
    rc, _stdout, _stderr = await runcmd(command)
    if rc != 0 or not os.path.isfile(recovered_path):
        try:
            if os.path.exists(recovered_path):
                os.remove(recovered_path)
        except OSError:
            pass
        return False, "re-encoding failed"

    recovered_valid, _recovered_reason = await _validate_video_decode(recovered_path)
    if not recovered_valid:
        try:
            os.remove(recovered_path)
        except OSError:
            pass
        return False, "re-encoded video still has decode errors"

    os.replace(recovered_path, path)
    return True, "damaged video was re-encoded and verified"

# ---------------------------------------------------------------------------
# Plan / Channel helpers
# ---------------------------------------------------------------------------

def render_plans_text() -> str:
    plans = load_plans()
    out   = ["**Subscription Plans**\n"]
    for p in plans:
        feats = "\n".join([f"  • {f}" for f in p.get("features", [])])
        out.append(f"**{p['name']}** — `{p['price']}`\nDuration: `{p.get('duration', '-')}`\n{feats}")
    out.append(f"\nTo subscribe, contact @{SUPPORT_USERNAME}.")
    return "\n\n".join(out)


def _channel_root_kb() -> InlineKeyboardMarkup:
    """Show playlist groups; URLs and User-Agent properties are never displayed."""
    rows, row = [], []
    for idx, group in enumerate(CHANNEL_GROUPS):
        row.append(InlineKeyboardButton(f"📁 {group}", callback_data=f"chgrp:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if not rows:
        rows = [[InlineKeyboardButton("No channels configured", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def _channel_group_kb(group: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for idx, item in CHANNEL_ENTRIES.items():
        if item["group"] != group:
            continue
        row.append(InlineKeyboardButton(f"📺 {item['name']}", callback_data=f"ch:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="chback")])
    return InlineKeyboardMarkup(rows)

# ---------------------------------------------------------------------------
# Pre-recording Setup Wizard helpers — Audio → Watermark → Video Size → Start
# ---------------------------------------------------------------------------

_QUALITY_BITRATE_KBPS = {"480": 600, "576": 820, "640": 1000, "720": 1500, "1080": 2500}
REC_LARGE_FILE_THRESHOLD_MB = 9


def _est_size_value_mb(duration_sec: int, quality: str) -> float:
    """Estimate recording size in MB using the same bitrate table as the menu."""
    br = _QUALITY_BITRATE_KBPS.get(str(quality), 0)
    if not br or duration_sec <= 0:
        return 0.0
    return duration_sec * br / 8 / 1024


def _est_size_mb(duration_sec: int, quality: str) -> str:
    size_mb = _est_size_value_mb(duration_sec, quality)
    if size_mb <= 0:
        return "?"
    return f"~{size_mb:.0f} MB"


def _setup_summary(s: dict) -> str:
    q = s["quality"]
    q_str = f"{q}p" if q != "original" else "Original"
    dimensions = s.get("detected_video_dimensions") or []
    selected_index = s.get("video_stream_index")
    selected_stream = next(
        (stream for stream in s.get("detected_video_streams", [])
         if stream.get("video_index") == selected_index),
        None,
    )
    if selected_stream:
        source_size = f"Selected {selected_stream['width']}×{selected_stream['height']}"
    else:
        source_size = "Auto • " + " / ".join(f"{w}×{h}" for w, h in dimensions) if dimensions else "Unavailable"
    wm_preset = s.get("watermark_preset", "Watermark 1")
    wm_pos = s.get("watermark_pos", "bottom_center").replace("_", " ").title()
    wm_size = s.get("watermark_size", 150)
    wm_times = s.get("watermark_times", [])
    if isinstance(wm_times, str):
        wm_times = [] if wm_times in ("", "none") else [wm_times]
    time_labels = []
    if "5_6" in wm_times:
        time_labels.append("00:05:00 → 00:06:00")
    if "10_11" in wm_times:
        time_labels.append("00:10:00 → 00:11:00")
    wm_time = " + ".join(time_labels) if time_labels else "Full Recording"
    wm = (f"{wm_preset} • {wm_pos} • {wm_size}px • {wm_time}"
          if s.get("watermark_on") else "OFF")

    at = s["audio_track"]
    tracks = s.get("detected_audio_tracks", [])
    if not at:
        audio_s = "All Tracks"
    elif len(at) == 1:
        idx = at[0]
        audio_s = _audio_track_label(tracks[idx - 1]) if tracks and idx <= len(tracks) else f"Track {idx}"
    else:
        parts = []
        for idx in sorted(at):
            track = tracks[idx - 1] if tracks and idx <= len(tracks) else None
            lang = track.get("lang", "?") if track else f"T{idx}"
            parts.append(LANG_LABEL.get(lang, lang.upper()))
        audio_s = ", ".join(parts)

    return (
        f"📋 **Recording Setup**\n\n"
        f"⏱ Duration: `{s['timestamp']}`\n"
        f"📁 Filename: `{s['filename']}`\n"
        f"🎙 Audio: `{audio_s}`\n"
        f"📺 Source video: `{source_size}`\n"
        f"💧 Watermark: `{wm}`\n"
        f"📐 Size: `{q_str}`\n\n"
        f"👇 Choose an option:"
    )


def _kb_step1(s: dict) -> InlineKeyboardMarkup:
    uid = s["user_id"]
    time_sel = s.get("watermark_times", [])
    if isinstance(time_sel, str):
        time_sel = [] if time_sel in ("", "none") else [time_sel]

    rows = []
    # Watermark controls are OWNER-ONLY. They are hidden from admins/users.
    if is_owner(uid):
        rows.extend([
            [InlineKeyboardButton("💧 Watermark 1 • 150px • 16:9", callback_data=f"rs:{uid}:wm_preset:1"),
             InlineKeyboardButton("⬛ Watermark 2 • BlackBars", callback_data=f"rs:{uid}:wm_preset:2")],
            [InlineKeyboardButton("💧 Normal Watermark 3", callback_data=f"rs:{uid}:wm_preset:3")],
            [InlineKeyboardButton("🕐 00:05:00 → 00:06:00" + (" ✅" if "5_6" in time_sel else ""),
                                  callback_data=f"rs:{uid}:wm_time:5_6"),
             InlineKeyboardButton("🕐 00:10:00 → 00:11:00" + (" ✅" if "10_11" in time_sel else ""),
                                  callback_data=f"rs:{uid}:wm_time:10_11")],
        ])

    rows.extend([
        [InlineKeyboardButton("🎞 Gen Preview", callback_data=f"rs:{uid}:gen_preview")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"rs:{uid}:back_audio"),
         InlineKeyboardButton("📐 Next: Video Size →", callback_data=f"rs:{uid}:next_quality")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"rs:{uid}:cancel")],
    ])
    return InlineKeyboardMarkup(rows)


def _kb_step2(s: dict) -> InlineKeyboardMarkup:
    uid = s["user_id"]
    dur = s["duration_sec"]
    dimensions = s.get("detected_video_dimensions") or []
    source_label = " / ".join(f"{w}×{h}" for w, h in dimensions)
    rows = []

    # For long recordings whose estimated 720p output is at least 9 MB,
    # offer only the two compression-friendly resolutions side by side.
    # Shorter/smaller recordings retain the original full resolution menu.
    estimated_720p_mb = _est_size_value_mb(dur, "720")
    if estimated_720p_mb >= REC_LARGE_FILE_THRESHOLD_MB:
        # Put the two choices on the same row, as a compact resolution menu.
        rows.append([
            InlineKeyboardButton(
                f"{'✅ ' if s['quality'] == '576' else ''}🖥️ 576p (~{_est_size_value_mb(dur, '576'):.0f} MB)",
                callback_data=f"rs:{uid}:quality:576",
            ),
            InlineKeyboardButton(
                f"{'✅ ' if s['quality'] == '720' else ''}🖥️ 720p (~{_est_size_value_mb(dur, '720'):.0f} MB)",
                callback_data=f"rs:{uid}:quality:720",
            ),
        ])
    else:
        for q, label, icon in [
            ("480", "480p", "🖥️"), ("576", "576p", "🖥️"), ("640", "640p", "🖥️"),
            ("720", "720p", "🖥️"), ("1080", "1080p", "🔵"), ("original", "Original", "🔒"),
        ]:
            sel = "✅ " if s["quality"] == q else ""
            button_label = (
                f"Source {source_label}" if q == "original" and source_label
                else label
            )
            size_label = "original size" if q == "original" else _est_size_mb(dur, q)
            rows.append([InlineKeyboardButton(f"{sel}{icon} {button_label} ({size_label})",
                                              callback_data=f"rs:{uid}:quality:{q}")])

    rows.append([InlineKeyboardButton("🎞 Gen Preview", callback_data=f"rs:{uid}:gen_preview")])
    rows.append([InlineKeyboardButton("◀️ Back to Watermark", callback_data=f"rs:{uid}:back_step1")])
    rows.append([InlineKeyboardButton("▶️ Start Recording", callback_data=f"rs:{uid}:start"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"rs:{uid}:cancel")])
    return InlineKeyboardMarkup(rows)


def _build_vf_and_codec(setup: dict) -> tuple[list[str], list[str], bool]:
    """
    Returns (extra_inputs, post_args, needs_encode).
    extra_inputs: additional -i args inserted after the main stream input (e.g. ["-i", logo_path]).
    post_args: everything after all inputs — filters, maps, codec args.
    """
    quality      = setup["quality"]
    wm_on        = setup["watermark_on"]
    needs_encode = quality != "original" or wm_on
    selected_video_index = setup.get("video_stream_index")
    input_video_map = (
        f"0:v:{selected_video_index}?" if selected_video_index is not None
        else "0:v?"
    )
    input_video_filter = (
        f"[0:v:{selected_video_index}]" if selected_video_index is not None
        else "[0:v]"
    )
    vf: list[str] = []
    custom_scale = setup.get("custom_scale")
    preserve_source_resolution = bool(setup.get("preserve_source_resolution"))
    if custom_scale:
        vf.append(f"scale={custom_scale}")
    custom_label = setup.get("custom_video_label")
    if custom_label:
        safe_label = str(custom_label).replace("\\", "\\\\").replace("'", "\\'")
        vf.append(
            f"drawtext=text='{safe_label}':fontsize=24:fontcolor=white:"
            "borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-th-20"
        )


    # Watermark presets define the frame treatment:
    #   Watermark 1 (150px) -> true 16:9 output with NO black padding.
    #                         Keep aspect ratio and crop excess sides/top-bottom.
    #   Watermark 2 (BlackBars) -> fit the source inside 16:9 and add black bars.
    #   Watermark 3 -> normal sizing.
    wm_preset = setup.get("watermark_preset", "Watermark 1")
    target_map = {
        "480": "854:480", "576": "1024:576", "640": "1138:640",
        "720": "1280:720", "1080": "1920:1080",
    }
    target = target_map.get(quality, "854:480")
    if wm_on and wm_preset == "Watermark 1" and not custom_scale and not preserve_source_resolution:
        # Watermark 1: exact 16:9 output, no crop.
        # Complete source frame is retained by direct scaling.
        vf += [
            f"scale={target}"
        ]
    elif wm_on and wm_preset == "Watermark 2" and not custom_scale and not preserve_source_resolution:
        # BlackBars: preserve the complete source picture and fill the
        # remaining 16:9 canvas with black.
        vf += [
            f"scale={target}:force_original_aspect_ratio=decrease",
            f"pad={target}:(ow-iw)/2:(oh-ih)/2:black",
        ]
    elif wm_on and wm_preset == "Normal Watermark 3":
        # Watermark 3: custom 1536x1228 frame treatment.
        vf += ["scale=1536:1228", "setsar=1"]
    elif quality in target_map:
        vf.append(f"scale={target}")

    extra_inputs: list[str] = []

    if wm_on:
        dur_sec  = setup.get("duration_sec", 0) or time_to_seconds(setup.get("timestamp", "0"))
        wm_times = setup.get("watermark_times", [])
        if isinstance(wm_times, str):
            wm_times = [] if wm_times in ("", "none") else [wm_times]
        wm_img   = _watermark_img_path()

        if wm_img:
            # ── Image overlay via filter_complex ─────────────────────────────
            needs_encode = True
            extra_inputs = ["-i", wm_img]
            pos_map_img  = {
                "top_left":      "19:80",
                "top_center":    "(W-w)/2:46",
                "top_right":     "W-w-20:80",
                "center":        "(W-w)/2:(H-h)/2",
                "bottom_left":   "20:H-h-60",
                "bottom_center":     "(W-w)/2:H-h-80",  # Watermark 1 moved 50px upward
                "wm2_bottom_center": "(W-w)/2:H-h-60",
                "bottom_right":      "W-w-20:H-h-60",
            }
            is_wm3 = wm_preset == "Normal Watermark 3"
            img_pos = ("20:main_h-overlay_h-170" if is_wm3 else
                       pos_map_img.get(setup["watermark_pos"], "W-w-10:H-h-10"))
            _wm_sz = 300 if is_wm3 else int(setup.get("watermark_size") or get_watermark_size())
            if is_wm3:
                wm_enable = ("between(t,0,30)+between(t,540,600)+"
                             "between(t,1200,1260)+between(t,2400,2460)")
            elif wm_preset == "Watermark 1":
                # Normal Watermark 1 image intervals requested by the owner:
                # 00:00:00-00:00:10, 00:00:15-00:00:19, 00:00:19-00:00:50.
                wm_enable = ("between(t,0,10)+between(t,15,19)+"
                             "between(t,19,50)")
            else:
                wm_ranges = []
                if "5_6" in wm_times:
                    wm_ranges.append("between(t,300,360)")
                if "10_11" in wm_times:
                    wm_ranges.append("between(t,600,660)")
                wm_enable = "+".join(wm_ranges) if wm_ranges else "1"
            if vf:
                fc = (f"{input_video_filter}{','.join(vf)}[_vs];"
                      f"[1:v]scale={_wm_sz}:-1[_wm];"
                      f"[_vs][_wm]overlay={img_pos}:enable='{wm_enable}'[_out]")
            else:
                fc = (f"[1:v]scale={_wm_sz}:-1[_wm];"
                      f"{input_video_filter}[_wm]overlay={img_pos}:enable='{wm_enable}'[_out]")

            sel_tracks = setup["audio_track"]
            post: list[str] = ["-filter_complex", fc, "-map", "[_out]"]
            if not sel_tracks:
                post += ["-map", "0:a?"]
            else:
                for t in sorted(sel_tracks):
                    post += ["-map", f"0:a:{t-1}?"]
            crf = str(setup.get("custom_crf") or ("24" if (is_wm3 or quality in ("480", "576", "640")) else "23"))
            abr = "192k" if quality == "1080" else "128k"
            post += ["-c:v", "libx264", "-preset", "ultrafast" if is_wm3 else "veryfast",
                     "-crf", crf]
            if is_wm3:
                post += ["-pix_fmt", "yuv420p", "-threads", "2"]
            post += ["-c:a", "aac", "-b:a", abr]
            return extra_inputs, post, needs_encode

        else:
            # ── Fallback: text watermark (drawtext) ───────────────────────────
            pos_map_txt = {
                "top_left":      "x=19:y=80",
                "top_center":    "x=(w-tw)/2:y=46",
                "top_right":     "x=w-tw-20:y=80",
                "center":        "x=(w-tw)/2:y=(h-th)/2",
                "bottom_left":   "x=20:y=h-th-60",
                "bottom_center":     "x=(w-tw)/2:y=h-th-60",  # Watermark 1 moved 50px upward
                "wm2_bottom_center": "x=(w-tw)/2:y=h-th-40",
                "bottom_right":      "x=w-tw-20:y=h-th-60",
            }
            xy   = pos_map_txt.get(setup["watermark_pos"], "x=10:y=10")
            safe = ((setup.get("watermark_text") or get_default_watermark())
                    .replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:"))
            if wm_preset == "Normal Watermark 3":
                wm_enable = ("between(t,0,30)+between(t,540,600)+"
                             "between(t,1200,1260)+between(t,2400,2460)")
            elif wm_preset == "Watermark 1":
                # Match Normal Watermark 1 image timing if image cache is unavailable.
                wm_enable = ("between(t,0,10)+between(t,15,19)+"
                             "between(t,19,50)")
            else:
                wm_ranges = []
                if "5_6" in wm_times:
                    wm_ranges.append("between(t,300,360)")
                if "10_11" in wm_times:
                    wm_ranges.append("between(t,600,660)")
                wm_enable = "+".join(wm_ranges) if wm_ranges else "1"
            vf.append(f"drawtext=text='{safe}':fontsize=28:fontcolor=white"
                      f":box=1:boxcolor=black@0.4:boxborderw=4:{xy}"
                      f":enable='{wm_enable}'")

    post: list[str] = []
    sel_tracks = setup["audio_track"]
    if not sel_tracks:
        post += ["-map", input_video_map, "-map", "0:a?"]
    else:
        post += ["-map", input_video_map]
        for t in sorted(sel_tracks):
            post += ["-map", f"0:a:{t - 1}?"]

    if needs_encode:
        if vf:
            post += ["-vf", ",".join(vf)]
        crf = str(setup.get("custom_crf") or ("24" if quality in ("480", "576", "640") else "23"))
        abr = "192k" if quality == "1080" else "128k"
        post += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                 "-c:a", "aac", "-b:a", abr]
    else:
        post += ["-c:v", "copy", "-c:a", "copy"]
    return extra_inputs, post, needs_encode

# ---------------------------------------------------------------------------
# Audio track probe (for the wizard's first step)
# ---------------------------------------------------------------------------

async def _probe_audio_tracks(url: str, timeout_sec: int = 15) -> list:
    """Probe a stream URL and return list of audio track dicts."""
    cmd = shlex.split(
        f'ffprobe -v quiet -hide_banner -print_format json '
        f'-show_streams -select_streams a '
        f'-user_agent "{DEFAULT_USER_AGENT}" '
        f'-probesize 5000000 -analyzeduration 5000000 '
        f'-i {shlex.quote(url)}'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        data = json.loads(stdout.decode(errors="ignore") or "{}")
        tracks = []
        for s in data.get("streams", []):
            tags  = s.get("tags") or {}
            lang  = (tags.get("language") or "und").lower()[:3]
            title = tags.get("title") or tags.get("handler_name") or ""
            tracks.append({
                "stream_idx": len(tracks),   # 0-based position among audio streams
                "lang":       lang,
                "title":      title,
                "channels":   s.get("channels", 2),
                "codec":      s.get("codec_name", "?"),
            })
        return tracks
    except asyncio.TimeoutError:
        LOG.warning(f"Audio probe timed out for {url}")
        return []
    except Exception as e:
        LOG.warning(f"Audio probe failed for {url}: {e}")
        return []


def _audio_track_label(track: dict) -> str:
    lang  = track["lang"]
    label = LANG_LABEL.get(lang, lang.upper())
    title = (track.get("title") or "").strip()
    ch    = track.get("channels", 2)
    ch_s  = "stereo" if ch == 2 else ("mono" if ch == 1 else f"{ch}ch")
    if title and title.lower() != label.lower():
        return f"{label} ({title}) [{ch_s}]"
    return f"{label} [{ch_s}]"


def _kb_audio_step(setup: dict) -> InlineKeyboardMarkup:
    uid    = setup["user_id"]
    tracks = setup.get("detected_audio_tracks", [])
    sel    = setup["audio_track"]   # [] = all tracks, [1,2,...] = specific 1-based indices
    all_selected = (not sel)
    rows   = []

    buttons = []
    for i, t in enumerate(tracks, 1):
        lang  = t.get("lang", "?")
        codec = t.get("codec", "?").upper()
        label = LANG_LABEL.get(lang, lang.upper())
        icon  = "✅ " if (all_selected or i in sel) else ""
        buttons.append(InlineKeyboardButton(
            f"{icon}{label} ({codec})",
            callback_data=f"rs:{uid}:audio_toggle:{i}"
        ))
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    all_icon = "✅" if all_selected else "🔄"
    rows.append([InlineKeyboardButton(
        f"{all_icon} Select All Tracks",
        callback_data=f"rs:{uid}:audio_select_all"
    )])

    video_streams = setup.get("detected_video_streams", [])
    if video_streams:
        selected_index = setup.get("video_stream_index")
        auto_label = "✅ Auto video stream" if selected_index is None else "Auto video stream"
        rows.append([InlineKeyboardButton(
            auto_label, callback_data=f"rs:{uid}:video_stream:auto"
        )])
        stream_buttons = []
        for stream in video_streams:
            index = stream["video_index"]
            icon = "✅ " if index == selected_index else ""
            stream_buttons.append(InlineKeyboardButton(
                f"{icon}{stream['width']}×{stream['height']}",
                callback_data=f"rs:{uid}:video_stream:{index}",
            ))
        for i in range(0, len(stream_buttons), 2):
            rows.append(stream_buttons[i:i + 2])

    rows.append([
        InlineKeyboardButton("◀️ Back",              callback_data=f"rs:{uid}:cancel"),
        InlineKeyboardButton("✅ Next: Watermark →",  callback_data=f"rs:{uid}:next_wm"),
    ])
    rows.append([InlineKeyboardButton("🎞 Gen Preview", callback_data=f"rs:{uid}:gen_preview")])
    return InlineKeyboardMarkup(rows)


def _audio_step_text(setup: dict) -> str:
    tracks = setup.get("detected_audio_tracks", [])
    lines  = [
        "**🎙 Step 1 — Audio Track**\n",
        f"Duration: `{setup['timestamp']}`  |  File: `{setup['filename']}`\n",
    ]
    if tracks:
        lines.append(f"Found **{len(tracks)}** audio track(s):\n")
        for i, t in enumerate(tracks, 1):
            lines.append(f"`{i}.` {_audio_track_label(t)}")
    else:
        lines.append("_No audio track info (stream will include all audio)._")
    dimensions = setup.get("detected_video_dimensions") or []
    if dimensions:
        lines.append("\n📺 Source video: " + " / ".join(f"`{w}×{h}`" for w, h in dimensions))
    else:
        lines.append("\n📺 Source video quality: _not detected_")
    video_streams = setup.get("detected_video_streams", [])
    selected_index = setup.get("video_stream_index")
    if selected_index is None:
        lines.append("🎞 Video stream: `Auto`")
    else:
        selected = next(
            (stream for stream in video_streams
             if stream.get("video_index") == selected_index),
            None,
        )
        if selected:
            lines.append(
                f"🎞 Video stream: `{selected['width']}×{selected['height']}`"
            )
    lines.append("\n👇 Choose audio and video streams, then tap **Next**:")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Flag parser for /rec and /drec inline flags
# ---------------------------------------------------------------------------

def _parse_rec_flags(tokens: list) -> dict:
    """Parse optional recording flags from a list of CLI-style tokens.

    Supported flags:
      -aes  <32-char hex>   AES-128 decryption key (HLS)
      -cookie <value>       HTTP Cookie header value
      -ua / -user_agent     User-Agent string
      -referer <value>      Referer header
      -headers <value>      ffprobe-style multi-header string (Key: Value\\r\\nKey: Value...)
      -i <url>              Input URL (ffprobe/ffmpeg style)
      -t <HH:MM:SS>         Duration / timestamp (ffprobe style)
      -license <url>        ClearKey DRM license URL (for DASH/MPD streams)
      -drm  <scheme>        DRM scheme hint (clearkey / none)
      -aio                  (no-op / allow-input-override marker)
    """
    flags: dict = {}
    i = 0
    while i < len(tokens):
        t = tokens[i].lstrip("-").lower()
        if t in ("aes", "key") and i + 1 < len(tokens):
            flags["aes_key"] = tokens[i + 1].strip()
            i += 2
        elif t in ("cookie", "cookies", "c") and i + 1 < len(tokens):
            flags["cookie"] = tokens[i + 1]
            i += 2
        elif t in ("ua", "user-agent", "useragent", "user_agent") and i + 1 < len(tokens):
            flags["user_agent"] = tokens[i + 1]
            i += 2
        elif t in ("referer", "ref") and i + 1 < len(tokens):
            flags["referer"] = tokens[i + 1]
            i += 2
        elif t in ("origin", "org") and i + 1 < len(tokens):
            flags["origin"] = tokens[i + 1]
            i += 2
        elif t in ("headers", "headers_str") and i + 1 < len(tokens):
            # ffprobe-style: "Cookie: val\r\nOrigin: val\r\nReferer: val\r\n"
            # Handle both literal \r\n (from shell $'...') and actual CRLF/LF
            raw = tokens[i + 1]
            raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
            for line in raw.split("\n"):
                line = line.strip()
                if ":" not in line:
                    continue
                hk, hv = line.split(":", 1)
                hk = hk.strip().lower()
                hv = hv.strip()
                if hk == "cookie" and not flags.get("cookie"):
                    flags["cookie"] = hv
                elif hk == "origin" and not flags.get("origin"):
                    flags["origin"] = hv
                elif hk == "referer" and not flags.get("referer"):
                    flags["referer"] = hv
            i += 2
        elif t in ("i", "input") and i + 1 < len(tokens):
            # ffprobe/ffmpeg: -i <url>
            flags["input_url"] = tokens[i + 1]
            i += 2
        elif t in ("t", "time", "duration") and i + 1 < len(tokens):
            # ffprobe/ffmpeg duration: -t HH:MM:SS
            flags["timestamp"] = tokens[i + 1]
            i += 2
        elif t in ("license", "lic", "licurl", "license_url") and i + 1 < len(tokens):
            flags["license_url"] = tokens[i + 1]
            i += 2
        elif t in ("drm", "drmscheme", "drm_scheme") and i + 1 < len(tokens):
            flags["drm_scheme"] = tokens[i + 1].lower()
            i += 2
        elif t == "aio":
            flags["aio"] = True
            i += 1
        else:
            i += 1
    return flags


def _parse_cloudplay_format(text: str):
    """Parse the multi-line CloudPlay/JSON-ish format used by streaming apps.

    Supported formats:

    Format A (with -- separator):
        /rec
            "user_agent": "...",
        --
            "mpd_url": "https://...index.mpd|drmScheme=clearkey",
            "license_url": "https://...",  -t HH:MM:SS filename

    Format B (single block, with nested "headers": {}):
        /rec
            "user_agent": "...",
            "m3u8_url": "https://...",
            "headers": {
              "Cookie": "hdntl=...",
              "Origin": "https://www.hotstar.com",
              "Referer": "https://www.hotstar.com/"  -t 00:30:00 filename
            }

    Returns (url, timestamp, filename, flags_dict) or None if not this format.
    """
    import re as _re

    _URL_KEYS = ("mpd_url", "m3u8_url", "hls_url", "stream_url", "url")

    # Quick bail: must look like a JSON-ish block (at least one quoted key)
    if not _re.search(r'"[a-z_A-Z]+"\\s*:', text):
        # Try bare check — at least one of the URL keys must be present
        has_url_key = any(f'"{k}"' in text for k in _URL_KEYS)
        if not has_url_key:
            return None

    def _kv_flat(section: str) -> dict:
        """Extract top-level "key": "value" pairs (ignores nested blocks)."""
        pairs: dict = {}
        for m in _re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', section):
            pairs[m.group(1).lower()] = m.group(2)
        return pairs

    def _kv_nested(block: str) -> dict:
        """Extract key-value pairs from inside a { } block (case-preserved keys)."""
        pairs: dict = {}
        for m in _re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', block):
            pairs[m.group(1)] = m.group(2)
        return pairs

    # ── Split on -- if present (Format A) ───────────────────────────────────
    if "--" in text:
        parts      = text.split("--", 1)
        header_raw = parts[0]
        body_raw   = parts[1]
        top_kv     = _kv_flat(header_raw)
        top_kv.update(_kv_flat(body_raw))
        remaining_src = body_raw
    else:
        # Format B — whole text is one block
        top_kv        = _kv_flat(text)
        remaining_src = text

    # ── Extract nested "headers": { ... } block ─────────────────────────────
    nested_headers: dict = {}
    hdr_match = _re.search(r'"[Hh]eaders"\s*:\s*\{([^}]*)\}', text, _re.DOTALL)
    if hdr_match:
        nested_headers = _kv_nested(hdr_match.group(1))
        # Remove headers block from remaining text so -t parsing isn't confused
        remaining_src = text[:hdr_match.start()] + " " + hdr_match.group(1) + " "

    # ── Resolve stream URL ───────────────────────────────────────────────────
    raw_url = ""
    for k in _URL_KEYS:
        raw_url = top_kv.get(k, "")
        if raw_url:
            break
    if not raw_url:
        return None

    # Strip pipe-separated params:  url|drmScheme=clearkey|...
    url        = raw_url.split("|")[0].strip()
    drm_scheme = ""
    for seg in raw_url.split("|")[1:]:
        if seg.lower().startswith("drmscheme="):
            drm_scheme = seg.split("=", 1)[1].lower()

    # ── Collect flags ────────────────────────────────────────────────────────
    license_url = top_kv.get("license_url", "")
    user_agent  = top_kv.get("user_agent", "") or top_kv.get("useragent", "")

    # Cookie / Origin / Referer — prefer nested headers block, fall back to top-level
    def _nget(key: str) -> str:
        """Case-insensitive lookup in nested_headers dict."""
        for k, v in nested_headers.items():
            if k.lower() == key.lower():
                return v
        return ""

    cookie  = _nget("cookie")  or top_kv.get("cookie", "")
    referer = _nget("referer") or top_kv.get("referer", "")
    origin  = _nget("origin")  or top_kv.get("origin", "")

    # ── Find -t timestamp and optional filename in trailing text ─────────────
    # Strip all "key": "value" pairs from remaining to isolate free text
    remaining = _re.sub(r'"[^"]*"\s*:\s*"[^"]*"\s*,?\s*', "", remaining_src)
    remaining = _re.sub(r'\s+', ' ', remaining).strip()

    timestamp = ""
    filename  = DEFAULT_FILENAME
    t_m = _re.search(r'-t\s+(\d{1,2}:\d{2}:\d{2})', remaining)
    if not t_m:
        t_m = _re.search(r'\b(\d{1,2}:\d{2}:\d{2})\b', remaining)
    if t_m:
        timestamp = t_m.group(1)
        after     = remaining[t_m.end():].strip()
        fn_tokens = [tok for tok in after.split() if not tok.startswith("-")]
        if fn_tokens:
            filename = fn_tokens[0]

    flags: dict = {}
    if license_url: flags["license_url"] = license_url
    if drm_scheme:  flags["drm_scheme"]  = drm_scheme
    if user_agent:  flags["user_agent"]  = user_agent
    if cookie:      flags["cookie"]      = cookie
    if referer:     flags["referer"]     = referer
    if origin:      flags["origin"]      = origin

    return url, timestamp, filename, flags


def _fetch_clearkey_keys_sync(license_url: str, extra_headers: dict = {}) -> str:
    """Synchronously fetch ClearKey decryption key(s) from a license URL.

    Returns a string for FFmpeg's -decryption_key:
      - "kid_hex:key_hex"  when the server returns a JWK set
      - "key_hex"          when the server returns a raw or simplified key
    """
    import urllib.request
    import base64 as _b64
    import json as _json

    def _b64url_hex(s: str) -> str:
        pad = 4 - (len(s) % 4)
        s  += "=" * (pad if pad != 4 else 0)
        return _b64.urlsafe_b64decode(s).hex()

    req_headers = {"User-Agent": "Mozilla/5.0", **extra_headers}
    req = urllib.request.Request(license_url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()

    # ── Try JSON / JWK ClearKey format ───────────────────────────────────────
    try:
        data = _json.loads(body)

        # Standard ClearKey JWK  {"keys": [{"kty":"oct","k":"...","kid":"..."}], ...}
        if "keys" in data and data["keys"]:
            entry   = data["keys"][0]
            key_b64 = entry.get("k", "")
            kid_b64 = entry.get("kid", "")
            if key_b64:
                key_hex = _b64url_hex(key_b64)
                if kid_b64:
                    return f"{_b64url_hex(kid_b64)}:{key_hex}"
                return key_hex

        # Flat dict  {"key": "hexstring"}  or {"content_key": "..."}
        for field in ("key", "content_key", "decryption_key", "aes_key"):
            val = data.get(field, "")
            if isinstance(val, str) and val.strip():
                return val.strip()

        # {"data": {"key": "..."}}
        nested = data.get("data") or data.get("result") or {}
        if isinstance(nested, dict):
            for field in ("key", "content_key"):
                val = nested.get(field, "")
                if isinstance(val, str) and val.strip():
                    return val.strip()

    except (ValueError, TypeError):
        pass

    # ── Plain hex / base64 body ───────────────────────────────────────────────
    raw = body.decode("utf-8", errors="ignore").strip().replace(" ", "").replace("\n", "")
    # Hex key (16 or 32 bytes = 32 or 64 hex chars)
    if len(raw) in (32, 64) and all(c in "0123456789abcdefABCDEF" for c in raw):
        return raw
    # Base64 key (16 bytes → 24 chars with padding, 32 bytes → 44 chars)
    try:
        decoded = _b64.urlsafe_b64decode(raw + "==")
        if len(decoded) in (16, 32):
            return decoded.hex()
    except Exception:
        pass

    raise Exception(f"Unrecognised ClearKey license response: {body[:120]}")


async def _prepare_aes_input(url: str, hex_key: str, extra_headers: dict,
                              save_dir: str) -> str:
    """Fetch HLS manifest, write key to a local bin file, patch the manifest
    to use that local key, and return the path to the patched .m3u8 file."""
    import urllib.request
    from urllib.parse import urljoin
    import re as _re

    key_bytes = bytes.fromhex(hex_key.replace(" ", ""))
    key_path  = join(save_dir, "hls_key.bin")
    with open(key_path, "wb") as kf:
        kf.write(key_bytes)

    def _fetch(target_url: str) -> str:
        req = urllib.request.Request(target_url, headers=extra_headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="ignore")

    content = _fetch(url)

    # If master playlist, follow first variant
    if "#EXT-X-STREAM-INF" in content:
        base = url.rsplit("/", 1)[0] + "/"
        for line in content.splitlines():
            if line.strip() and not line.startswith("#"):
                variant_url = line.strip() if line.startswith("http") else urljoin(base, line.strip())
                content     = _fetch(variant_url)
                url         = variant_url
                break

    base_url = url.rsplit("/", 1)[0] + "/"
    patched_lines = []
    for line in content.splitlines():
        if "#EXT-X-KEY" in line and "AES-128" in line:
            line = _re.sub(r'URI="[^"]*"', f'URI="file://{key_path}"', line)
        elif line.strip() and not line.startswith("#") and not line.lower().startswith("http"):
            line = urljoin(base_url, line.strip())
        patched_lines.append(line)

    patched_path = join(save_dir, "patched_input.m3u8")
    with open(patched_path, "w") as mf:
        mf.write("\n".join(patched_lines))
    return patched_path


# ---------------------------------------------------------------------------
# handle_record — parse params, probe audio, show pre-recording setup wizard
# ---------------------------------------------------------------------------

async def handle_record(client: Client, message: Message, extra_flags: dict | None = None):
    user_id = _message_actor_id(message)
    params  = " ".join(message.command[1:])
    parts   = params.split(" ", 2)
    if len(parts) < 2:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "Bad arguments. Use `/rec <link> HH:MM:SS <filename>`.")
    url          = parts[0]
    timestamp    = parts[1]
    raw_filename = parts[2].strip() if len(parts) > 2 else DEFAULT_FILENAME
    for bad in '/\\:*?"<>|':
        raw_filename = raw_filename.replace(bad, "_")

    dur_sec = time_to_seconds(timestamp)
    if dur_sec <= 0:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "❌ Invalid duration. Use a positive `HH:MM:SS` value, for example `00:10:00`.",
        )

    # reclink supplies request headers captured by Chromium.  Keep this
    # separate from message.command so quoted User-Agent/Cookie values are not
    # broken when they contain spaces.
    flags = dict(extra_flags or {})
    setup: dict = {
        "user_id":        user_id,
        "chat_id":        message.chat.id,
        "orig_msg":       message,
        "url":            url,
        "timestamp":      timestamp,
        "duration_sec":   dur_sec,
        "filename":       raw_filename,
        "watermark_on":   False,
        "watermark_pos":  "bottom_center",
        "watermark_size": 150,
        "watermark_text": get_default_watermark(),
        "audio_track":    [],
        "auto_mode":      False,
        "quality":        "original",
        "aspect":         "none",
        "step":           0,
        "detected_audio_tracks": [],
        "detected_video_dimensions": [],
        "detected_video_streams": [],
        "video_stream_index": None,
        "aes_key":        flags.get("aes_key", ""),
        "flag_cookie":    flags.get("cookie", ""),
        "flag_ua":        flags.get("user_agent", ""),
        "flag_referer":   flags.get("referer", ""),
        "flag_origin":    flags.get("origin", ""),
        "license_url":    flags.get("license_url", ""),
        "drm_scheme":     flags.get("drm_scheme", ""),
    }
    rec_setup_sessions[user_id] = setup

    # User command is temporary too: remove /rec after 5 minutes.
    _schedule_message_delete(message, AUTO_DELETE_5MIN)

    # Keep one status message through stream probing, setup, recording, and upload.
    probe_msg = await message.reply_text("🔍 **Probing stream for audio tracks…**")
    setup["status_msg"] = probe_msg
    setup["setup_msg_id"] = probe_msg.id
    setup["current_status_text"] = "🔍 **Probing stream for audio tracks…**"
    setup["current_reply_markup"] = None

    # Effective URL after redirect/page extraction.  A failed HTTP probe must
    # not kill the wizard: FFmpeg is often able to open a live URL that a
    # Python probe cannot (signed URLs, strict TLS, or anti-bot headers).
    try:
        probe = await probe_stream(url)
    except Exception as exc:
        LOG.warning("Recording probe failed for %s: %s", url, exc)
        probe = {
            "is_hls": ".m3u8" in url.lower(),
            "final_url": url,
            "referer": "",
            "user_agent": DEFAULT_USER_AGENT,
            "extracted_from": None,
        }
    effective_url = probe.get("final_url") or url
    tracks = await _probe_audio_tracks(effective_url)
    setup["detected_audio_tracks"] = tracks
    setup["effective_url"]         = effective_url
    setup["is_hls"]                = bool(probe.get("is_hls")) or ".m3u8" in effective_url.lower()
    await _update_live_preview_url(setup, effective_url, setup["is_hls"])

    audio_names = ", ".join(_audio_track_label(track) for track in tracks) or "No tagged tracks; all audio will be kept"
    await _edit_setup_message(
        probe_msg, setup,
        f"✅ **Audio tracks probed**\n{audio_names}\n\n"
        "🔍 **Probing stream for Video Quality…**",
    )
    video_streams = await _probe_video_streams(effective_url)
    setup["detected_video_streams"] = video_streams
    setup["detected_video_dimensions"] = [
        (stream["width"], stream["height"]) for stream in video_streams
    ]
    await _edit_setup_message(
        probe_msg, setup, _audio_step_text(setup), _kb_audio_step(setup)
    )

# ---------------------------------------------------------------------------
# do_record — actual FFmpeg recording (called after wizard confirmation)
# ---------------------------------------------------------------------------

async def do_record(client: Client, query: CallbackQuery, setup: dict):
    user_id   = setup["user_id"]
    chat_id   = setup["chat_id"]
    url       = setup["url"]
    timestamp = setup["timestamp"]
    filename  = setup["filename"]
    orig_msg  = setup.get("orig_msg")
    rec_id    = int(time.time() * 1000) % 10**9   # unique per-recording slot

    # ── Quota check — non-owners must have Rec credits ───────────────────────
    if not is_owner(user_id):
        ok, quota_msg = use_rec(user_id)
        if not ok:
            return await client.send_message(chat_id, quota_msg)

    save_dir: Optional[str]   = None
    video_path: Optional[str] = None

    msg = setup.get("status_msg")
    if msg is None and setup.get("setup_msg_id"):
        try:
            msg = await client.get_messages(chat_id, setup["setup_msg_id"])
        except Exception:
            msg = None
    if msg is None:
        msg = await client.send_message(chat_id, "⚙️ Initializing recording...")
    setup["status_msg"] = msg
    status_lock = setup.setdefault("status_edit_lock", asyncio.Lock())

    async def update_status(text: str, reply_markup=None):
        nonlocal msg
        async with status_lock:
            msg = setup.get("status_msg") or msg
            setup["current_status_text"] = text
            setup["current_reply_markup"] = reply_markup
            await _edit_message_content(msg, text, reply_markup=reply_markup)

    try:
        await update_status("⚙️ Initializing recording...")
        raw_filename = filename
        for bad in '/\\:*?"<>|':
            raw_filename = raw_filename.replace(bad, "_")
        mkv_filename = f"{raw_filename}.mkv"
        save_dir     = join(DOWNLOAD_DIRECTORY, str(int(time.time())))
        os.makedirs(save_dir, exist_ok=True)
        video_path   = join(save_dir, mkv_filename)

        duration        = time_to_seconds(timestamp)

        rec_entry = {
            "segment_started_at": None,
            "recorded_seconds": 0.0,
            "capture_sample_at": time.monotonic(),
            "capture_sample_bytes": 0,
            "capture_speed_bps": 0.0,
            "status":        {
                "filename": raw_filename, "target": timestamp,
                "progress": "00:00:00", "save_dir": save_dir,
            },
            "ffmpeg_pid":    None,
            "progress_task": None,
            "effective_url": None,
            "is_hls":        False,
            "is_photo_msg":  bool(getattr(msg, "photo", None)),
            "snap_path":     None,
            "status_msg":    msg,
            "setup":         setup,
        }
        active_recs.setdefault(user_id, {})[rec_id] = rec_entry
        setup["active_rec_entry"] = rec_entry

        def _build_progress_text() -> str:
            elapsed = _recording_elapsed(rec_entry, duration)
            pct     = min((elapsed / duration) * 100, 100) if duration > 0 else 0
            bar     = "●" * int(10 * pct // 100) + "⬜" * (10 - int(10 * pct // 100))
            task_id = hex(rec_id)[2:10]
            sample_at = time.monotonic()
            try:
                current_bytes = os.path.getsize(video_path) if os.path.exists(video_path) else 0
            except OSError:
                current_bytes = 0
            previous_bytes = rec_entry.get("capture_sample_bytes", 0)
            previous_at = rec_entry.get("capture_sample_at", sample_at)
            sample_elapsed = sample_at - previous_at
            if current_bytes >= previous_bytes and sample_elapsed > 0.1:
                rec_entry["capture_speed_bps"] = (
                    current_bytes - previous_bytes
                ) / sample_elapsed
            else:
                rec_entry["capture_speed_bps"] = 0.0
            rec_entry["capture_sample_bytes"] = current_bytes
            rec_entry["capture_sample_at"] = sample_at
            capture_speed = rec_entry["capture_speed_bps"]
            capture_rate = (
                f"{capture_speed / (1024 * 1024):.2f} MB/s"
                if capture_speed > 0 else "waiting for stream data"
            )
            active_recs[user_id][rec_id]["status"]["progress"] = TimeFormatter(int(elapsed * 1000))
            q_str  = f"{setup['quality']}p" if setup["quality"] != "original" else "Original"
            wm_str = setup["watermark_pos"].replace("_", " ").title() if setup["watermark_on"] else "Off"
            slot_n = list(active_recs.get(user_id, {}).keys()).index(rec_id) + 1
            return (
                f"🎬 **Recording #{slot_n} in Progress...**\n\n"
                f"📡 Stream Capture\n"
                f"[{bar}]  {pct:.1f}%\n"
                f"⏱ Time  : {TimeFormatter(int(elapsed*1000))} / {TimeFormatter(duration*1000)}\n"
                f"⬇️ Capture rate: `{capture_rate}` | `{current_bytes / (1024 * 1024):.1f} MB`\n"
                f"🆔 Task  : {task_id}\n\n"
                f"📺 Quality: `{q_str}` | 💧 WM: `{wm_str}`\n"
                f"_Press **Gen Preview** for a live thumbnail_"
            )

        async def update_recording_progress():
            nonlocal msg
            while rec_id in active_recs.get(user_id, {}):
                if (user_id, rec_id) in cancelled_recs:
                    break
                kb = _rec_progress_kb(user_id, rec_id)
                text = _build_progress_text()
                try:
                    async with status_lock:
                        msg = setup.get("status_msg") or msg
                        setup["current_status_text"] = text
                        setup["current_reply_markup"] = kb
                        await _edit_message_content(msg, text, reply_markup=kb)
                        rec_entry["status_msg"] = msg
                        rec_entry["is_photo_msg"] = bool(getattr(msg, "photo", None))
                except Exception:
                    pass
                await asyncio.sleep(5)

        progress_task = asyncio.create_task(update_recording_progress())
        rec_entry["progress_task"] = progress_task

        # Detect MPD/DASH early (skip HLS probe for DASH streams)
        is_mpd = ".mpd" in url.lower() or (setup.get("drm_scheme", "") in ("clearkey", "widevine"))

        _pkb = _rec_progress_kb(user_id, rec_id)   # keyboard shorthand for probe phase

        # Re-use probe result from wizard if available (avoids double-probe)
        if setup.get("effective_url"):
            effective_url  = setup["effective_url"]
            # Use probe-detected is_hls from wizard; fall back to .m3u8 URL check.
            # Do NOT use (effective_url != url) — a redirect to a .ts endpoint is
            # raw MPEG-TS, not HLS, and -f hls would cause "Invalid data" errors.
            is_hls         = setup.get("is_hls", False) or ".m3u8" in effective_url.lower()
            extracted_from = None
            await update_status("▶️ Starting recording...", reply_markup=_pkb)
        elif is_mpd:
            # DASH/MPD — skip probe, use URL directly
            effective_url  = url
            is_hls         = False
            extracted_from = None
            await update_status("📡 DASH stream detected — starting recording...", reply_markup=_pkb)
        else:
            await update_status("🔍 Probing stream...", reply_markup=_pkb)
            probe          = await probe_stream(url)
            effective_url  = probe["final_url"]
            is_hls         = probe["is_hls"]
            extracted_from = probe.get("extracted_from")
            # Force HLS if URL ends with .m3u8 regardless of probe content-type
            if not is_hls and ".m3u8" in effective_url.lower():
                is_hls = True
                LOG.info(f"Probe uid={user_id}: forcing HLS=True (url has .m3u8), changed={'yes' if effective_url!=url else 'no'}")
            else:
                LOG.info(f"Probe uid={user_id}: hls={is_hls}, changed={'yes' if effective_url!=url else 'no'}")
            if extracted_from:
                await update_status("Found embedded HLS stream — starting recording...", reply_markup=_pkb)
            else:
                await update_status("▶️ Starting recording...", reply_markup=_pkb)

        # Store stream info in rec_entry for Gen Preview callback
        rec_entry["effective_url"] = effective_url
        rec_entry["is_hls"]        = is_hls
        setup["effective_url"] = effective_url
        setup["is_hls"] = is_hls

        # User-specified flags override probe-detected values
        probe_obj  = probe if not setup.get("effective_url") and not is_mpd else {}
        referer    = setup.get("flag_referer") or probe_obj.get("referer", "")
        user_agent = setup.get("flag_ua") or probe_obj.get("user_agent", DEFAULT_USER_AGENT)

        # Build combined extra headers (cookie + referer + origin)
        extra_headers: dict = {}
        if setup.get("flag_cookie"):
            extra_headers["Cookie"] = setup["flag_cookie"]
        if referer:
            extra_headers["Referer"] = referer
        # Use explicit origin flag, or auto-derive from referer (scheme+host only)
        origin = setup.get("flag_origin", "")
        if not origin and referer:
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(referer)
            if _p.scheme and _p.netloc:
                origin = f"{_p.scheme}://{_p.netloc}"
        if origin:
            extra_headers["Origin"] = origin

        # ── AES key (HLS): patch m3u8 manifest with local key file ─────────
        ffmpeg_input  = effective_url
        clearkey_arg  = ""   # for DASH ClearKey
        if setup.get("aes_key") and not is_mpd:
            try:
                await update_status("🔑 Patching AES key into manifest…", reply_markup=_pkb)
                ffmpeg_input = await _prepare_aes_input(
                    url, setup["aes_key"], extra_headers, save_dir
                )
                is_hls = True
                rec_entry["effective_url"] = ffmpeg_input
                LOG.info(f"AES patch OK uid={user_id} patched={ffmpeg_input}")
            except Exception as e:
                LOG.warning(f"AES patch failed: {e} — falling back to original URL")
                ffmpeg_input = effective_url
            await update_status("▶️ Starting recording…", reply_markup=_pkb)

        # ── ClearKey DRM (DASH/MPD): fetch keys from license URL ───────────
        if setup.get("license_url"):
            try:
                await update_status("🔑 Fetching ClearKey DRM license…", reply_markup=_pkb)
                ck = await asyncio.to_thread(
                    _fetch_clearkey_keys_sync, setup["license_url"], extra_headers
                )
                if ck:
                    clearkey_arg = ck
                    LOG.info(f"ClearKey key fetched uid={user_id}: {ck[:8]}…")
                await update_status("▶️ Starting recording…", reply_markup=_pkb)
            except Exception as e:
                LOG.warning(f"ClearKey license fetch failed: {e} — recording without decryption key")
                await update_status(f"⚠️ ClearKey fetch failed: `{e}`\n▶️ Continuing without key…", reply_markup=_pkb)

        args: list[str] = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            "-user_agent", user_agent,
            # Auto-reconnect on network drops
            "-reconnect",          "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max","15",
            "-reconnect_on_network_error", "1",
            # TCP buffer
            "-rw_timeout",         "10000000",   # 10 s I/O timeout (µs)
        ]
        # Combine all extra HTTP headers into one -headers block
        if extra_headers:
            hdr_str = "".join(f"{k}: {v}\r\n" for k, v in extra_headers.items())
            args += ["-headers", hdr_str]

        # ClearKey decryption key for DASH
        if clearkey_arg:
            args += ["-decryption_key", clearkey_arg]

        # Stream format / demuxer
        if is_mpd:
            args += ["-allowed_extensions", "ALL"]
        elif is_hls:
            args += ["-f", "hls", "-allowed_extensions", "ALL"]
        args += [
            "-probesize",          "20000000",   # 20 MB
            "-analyzeduration",    "8000000",    # 8 s
            "-thread_queue_size",  "512",
            "-i", ffmpeg_input,
        ]
        if setup.get("watermark_on"):
            await _async_ensure_watermark_img()
        extra_inputs, extra_post, re_encodes = _build_vf_and_codec(setup)
        args += extra_inputs
        args += extra_post

        # ── Audio track metadata branding ──────────────────────────────────
        # Embeds channel name in every audio track so it survives re-upload /
        # forward. Visible in VLC → Track Info, MX Player audio selector, and
        # Telegram's audio track dropdown.
        _brand = get_audio_brand_name()
        _detected_tracks = setup.get("detected_audio_tracks") or []
        _selected_audio = setup.get("audio_track") or []
        if _selected_audio and _detected_tracks:
            # The wizard stores selected audio tracks as 1-based source positions;
            # metadata must follow the actual output order after selection.
            _metadata_tracks = [
                _detected_tracks[idx - 1]
                for idx in sorted(_selected_audio)
                if 1 <= idx <= len(_detected_tracks)
            ]
        else:
            _metadata_tracks = _detected_tracks
        # Apply brand|language|filename to every output audio stream.
        args += _audio_metadata_args(
            max(len(_metadata_tracks), 1),
            _metadata_tracks,
            _brand,
            raw_filename,
            user_id=setup.get("user_id"),
        )

        args += [
            # H264 resilience: ignore decode errors in live streams
            "-fflags", "+discardcorrupt+genpts",
            "-err_detect", "ignore_err",
            "-t", str(duration), video_path,
        ]

        # Temporary playback URL retry system.
        # The original channel URL is re-probed after a temporary URL expires.
        STREAM_REFRESH_TIMEOUT = 120.0  # seconds
        retry_count = 0
        refresh_started_at = None
        recorded_seconds = 0.0
        rec_entry["recorded_seconds"] = recorded_seconds
        segment_paths = []
        last_ffmpeg_error = ""
        recording_finished = False

        try:
            input_pos = args.index("-i") + 1
        except ValueError:
            raise Exception("FFmpeg input argument is missing.")

        while not recording_finished:
            if (user_id, rec_id) in cancelled_recs:
                break

            remaining = max(float(duration) - recorded_seconds, 0.5)

            segment_path = join(
                save_dir,
                f".rec_{rec_id}_{len(segment_paths):03d}.mkv"
            )

            attempt_args = list(args)
            attempt_args[input_pos] = ffmpeg_input

            try:
                t_pos = attempt_args.index("-t")
                attempt_args[t_pos + 1] = f"{remaining:.3f}"
            except ValueError:
                pass

            # Write every retry into a separate temporary segment.
            attempt_args[-1] = segment_path

            LOG.info(
                f"Recording attempt rec={rec_id} "
                f"retry={retry_count} "
                f"remaining={remaining:.2f}s "
                f"url={ffmpeg_input[:160]}"
            )

            ffmpeg_process = await asyncio.create_subprocess_exec(
                *attempt_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            rec_entry["ffmpeg_pid"] = ffmpeg_process.pid
            rec_entry["segment_started_at"] = time.monotonic()
            setup["recording_started"] = True

            _stdout, stderr = await ffmpeg_process.communicate()
            rec_entry["segment_started_at"] = None
            retcode = ffmpeg_process.returncode

            rec_entry.pop("ffmpeg_pid", None)

            err_tail = stderr.decode(errors="ignore").strip()
            last_ffmpeg_error = err_tail

            segment_duration = 0.0

            if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                try:
                    segment_duration = float(
                        await get_duration_ffmpeg(segment_path) or 0
                    )
                except Exception:
                    segment_duration = 0.0

            if segment_duration > 0:
                segment_paths.append(segment_path)
                recorded_seconds += min(segment_duration, remaining)
                rec_entry["recorded_seconds"] = recorded_seconds

                LOG.info(
                    f"Recording segment rec={rec_id}: "
                    f"{segment_duration:.2f}s captured, "
                    f"total={recorded_seconds:.2f}/{duration:.2f}s"
                )

            # Target duration reached.
            if recorded_seconds >= float(duration) - 0.25:
                recording_finished = True
                break

            # User cancelled.
            if (user_id, rec_id) in cancelled_recs:
                break

            # Remove unusable empty segment.
            if segment_duration <= 0 and os.path.exists(segment_path):
                try:
                    os.remove(segment_path)
                except Exception:
                    pass

            # First failure/early EOF = retry.
            # Start/continue the fresh-URL recovery window.
            if refresh_started_at is None:
                refresh_started_at = time.monotonic()

            refresh_elapsed = time.monotonic() - refresh_started_at

            if refresh_elapsed >= STREAM_REFRESH_TIMEOUT:
                LOG.error(
                    f"Fresh playback URL was not recovered within "
                    f"{STREAM_REFRESH_TIMEOUT:.0f}s rec={rec_id}"
                )
                break

            retry_count += 1

            retry_msg = (
                f"🔄 **Refreshing stream...**\n\n"
                f"Retry: `{retry_count}/∞`\n"
                f"Refresh window: `{int(refresh_elapsed)}/{int(STREAM_REFRESH_TIMEOUT)}s`\n"
                f"Recorded: `{TimeFormatter(int(recorded_seconds * 1000))}` / "
                f"`{TimeFormatter(int(duration * 1000))}`"
            )

            try:
                await update_status(retry_msg, reply_markup=_pkb)
            except Exception:
                pass

            # MPD URLs do not use the HLS refresh mechanism.
            if is_mpd:
                fresh_url = ffmpeg_input
                fresh_url_changed = False
            else:
                fresh_url = ffmpeg_input
                fresh_url_changed = False

                try:
                    fresh_probe = await probe_stream(url)

                    candidate_url = fresh_probe.get("final_url") or ""

                    # For HLS, the public playback URL may stay identical
                    # while the live playlist/segments are refreshed.
                    # A successful HLS probe therefore counts as a fresh
                    # playback recovery even when candidate_url is unchanged.
                    if candidate_url and candidate_url != ffmpeg_input:
                        fresh_url = candidate_url
                        fresh_url_changed = True
                    elif fresh_probe.get("is_hls") and candidate_url:
                        fresh_url = candidate_url
                        fresh_url_changed = True

                    # If the original URL contains/returns an HLS URL,
                    # keep the HLS input mode.
                    if fresh_probe.get("is_hls"):
                        is_hls = True

                    LOG.info(
                        f"Stream refresh rec={rec_id}: "
                        f"{ffmpeg_input[:100]} -> {candidate_url[:100]} "
                        f"changed={fresh_url_changed}"
                    )

                except Exception as refresh_error:
                    LOG.warning(
                        f"Stream refresh failed rec={rec_id}: "
                        f"{refresh_error}"
                    )

            if fresh_url_changed:
                ffmpeg_input = fresh_url

                # A genuinely new playback URL was recovered.
                # Start a new 120-second recovery window only if this
                # newly recovered URL later fails again.
                refresh_started_at = None

                LOG.info(
                    f"Fresh playback URL recovered rec={rec_id}; "
                    f"starting FFmpeg immediately with new URL."
                )

            else:
                LOG.info(
                    f"No new playback URL yet rec={rec_id}; "
                    f"keeping current refresh window."
                )

            # Very small yield; this is NOT a fixed refresh delay.
            await asyncio.sleep(0.2)

        # Stop the progress updater only after the complete recording session.
        pt = rec_entry.pop("progress_task", None)
        if pt:
            pt.cancel()

        was_cancelled = (user_id, rec_id) in cancelled_recs

        if was_cancelled:
            # Keep captured segments for partial recording upload.
            LOG.info(
                f"Keeping {len(segment_paths)} captured segment(s) "
                f"for cancelled recording rec={rec_id}"
            )

        if not was_cancelled and (not recording_finished or not segment_paths):
            for _segment in segment_paths:
                try:
                    os.remove(_segment)
                except Exception:
                    pass

            if last_ffmpeg_error:
                if len(last_ffmpeg_error) > 1500:
                    last_ffmpeg_error = "..." + last_ffmpeg_error[-1500:]
                raise Exception(
                    "FFmpeg error: fresh playback URL was not recovered "
                    "within the 120-second refresh window:\n"
                    f"{last_ffmpeg_error}"
                )

            raise Exception(
                "Recording failed: no fresh playback URL was recovered "
                "within 120 seconds."
            )

        if was_cancelled and not segment_paths:
            schedule_retention_cleanup(save_dir)
            await update_status(
                "⏹ Recording cancelled — no usable partial video was captured."
            )
            return

        # Build the final MKV from all successfully captured segments.
        if len(segment_paths) == 1:
            os.replace(segment_paths[0], video_path)
        else:
            concat_file = join(
                save_dir,
                f".rec_{rec_id}_concat.txt"
            )

            def _concat_quote(path: str) -> str:
                return path.replace("'", r"'\''")

            with open(concat_file, "w", encoding="utf-8") as cf:
                for _segment in segment_paths:
                    cf.write(
                        "file '" + _concat_quote(os.path.abspath(_segment)) + "'\n"
                    )

            concat_process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                "-t", str(duration),
                video_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _concat_stdout, concat_stderr = await concat_process.communicate()

            if concat_process.returncode != 0:
                err = concat_stderr.decode(errors="ignore").strip()
                if len(err) > 1500:
                    err = "..." + err[-1500:]
                raise Exception(
                    f"FFmpeg segment merge error:\n{err}"
                )

            try:
                os.remove(concat_file)
            except Exception:
                pass

        # Remove temporary recording segments.
        for _segment in segment_paths:
            try:
                if os.path.exists(_segment):
                    os.remove(_segment)
            except Exception:
                pass

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            if was_cancelled:
                await update_status("⏹ Recording cancelled — no partial file available.")
                return
            raise Exception("No video file created or file is empty.")

        # ── Validate/finalize cancelled partial recordings before upload ────
        if was_cancelled:
            try:
                await update_status(
                    "⏹ **Recording cancelled.**\n"
                    "🔍 Checking the partial video before upload..."
                )
                partial_ok, partial_result = await _recover_partial_recording(video_path)
                if not partial_ok:
                    LOG.warning(
                        "Cancelled partial recording was not uploaded: %s",
                        partial_result,
                    )
                    schedule_retention_cleanup(save_dir)
                    await update_status(
                        "⏹ Recording cancelled. The captured partial video is "
                        "damaged and could not be repaired, so it was not uploaded."
                    )
                    return
                LOG.info("Cancelled partial recording check: %s", partial_result)
            except Exception as partial_finalize_error:
                LOG.warning(
                    "Partial recording validation/recovery failed (%s).",
                    type(partial_finalize_error).__name__,
                )
                schedule_retention_cleanup(save_dir)
                await update_status(
                    "⏹ Recording cancelled. The partial video could not be "
                    "validated, so it was not uploaded."
                )
                return

        await _stop_live_preview(setup)
        await update_status("🖼 Generating thumbnail...")
        dur = await get_duration_ffmpeg(video_path) or time_to_seconds(timestamp)

        fixed = join(save_dir, f"fixed_{mkv_filename}")
        rc, _o, err = await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-i {shlex.quote(video_path)} -map 0 -c copy '
            f'-metadata creation_time="{time.strftime("%Y-%m-%dT%H:%M:%S")}" '
            f'{shlex.quote(fixed)}'
        )
        if rc == 0:
            os.replace(fixed, video_path)
        else:
            LOG.warning(f"Metadata fix failed: {err}")

        rand_sec   = random.randint(5, max(dur - 5, 6))
        thumb_path = join(save_dir, "thumb.jpg")
        await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-ss {rand_sec} -i {shlex.quote(video_path)} '
            f'-vframes 1 -q:v 2 {shlex.quote(thumb_path)}'
        )
        thumb_ok = os.path.exists(thumb_path)

        retention_note = f"_Auto-deleted from server after 2 hours._"
        q_str = f"{setup['quality']}p" if setup["quality"] != "original" else "Original"
        audio_note = "All tracks" if setup["audio_track"] == 0 else f"Track {setup['audio_track']}"
        wm_note    = (f"💧 Watermark: `{setup['watermark_pos'].replace('_',' ').title()}`"
                      if setup["watermark_on"] else "")

        if was_cancelled:
            caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                       f"Duration: `{TimeFormatter(dur * 1000)}`\nFormat: `MKV (partial)`\n"
                       f"_Recording was cancelled — partial file attached._\n{retention_note}")
        else:
            caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                       f"Duration: `{TimeFormatter(dur * 1000)}`\n"
                       f"Quality: `{q_str}`\n"
                       f"Audio: `{audio_note}`\n"
                       + (f"{wm_note}\n" if wm_note else "")
                       + f"{retention_note}")

        send_target  = orig_msg or (query.message if query else msg)
        size_bytes   = os.path.getsize(video_path)
        size_str     = (f"{size_bytes / (1024**3):.2f} GB" if size_bytes >= 1024**3
                        else f"{size_bytes / (1024**2):.1f} MB")

        # WM3 gets its own post-recording quality selection.
        wm3_mode = setup.get("watermark_preset") == "Normal Watermark 3"

        if wm3_mode and not was_cancelled:
            quality_future = asyncio.get_running_loop().create_future()
            wm3_quality_waiters[(user_id, rec_id)] = quality_future

            quality_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "720p", callback_data=f"wm3q:{user_id}:{rec_id}:720"
                    ),
                    InlineKeyboardButton(
                        "576p", callback_data=f"wm3q:{user_id}:{rec_id}:576"
                    ),
                ]
            ])

            try:
                await update_status(
                    f"🎉 Recording completed!\\n\\n"
                    f"🎬 File: `{mkv_filename}`\\n"
                    f"📦 Size: `{size_str}`\\n"
                    f"⏱ Duration: `{TimeFormatter(dur * 1000)}`\\n\\n"
                    "Choose quality for compression:",
                    reply_markup=quality_keyboard,
                )

                try:
                    selected_quality = await asyncio.wait_for(
                        quality_future, timeout=900
                    )
                except asyncio.TimeoutError:
                    selected_quality = None

            finally:
                wm3_quality_waiters.pop((user_id, rec_id), None)

            if selected_quality:
                await update_status(
                    f"⚙️ Compressing Watermark 3 video to {selected_quality}p..."
                )

                compressed_path = join(
                    save_dir,
                    f"wm3_{selected_quality}p_{int(time.time())}.mkv"
                )

                command = (
                    f'ffmpeg -hide_banner -loglevel error -nostats -y '
                    f'-i {shlex.quote(video_path)} '
                    f'-map 0:v:0 -map 0:a? '
                    f'-vf "scale=-2:{selected_quality}" '
                    f'-c:v libx264 -preset ultrafast -crf 24 '
                    f'-pix_fmt yuv420p -c:a copy -map_metadata 0 -sn '
                    f'{shlex.quote(compressed_path)}'
                )

                rc, _out, compress_error = await runcmd(command)

                if (
                    rc == 0
                    and os.path.exists(compressed_path)
                    and os.path.getsize(compressed_path) > 0
                ):
                    video_path = compressed_path
                    mkv_filename = os.path.basename(video_path)
                    q_str = f"{selected_quality}p"
                    caption = caption.replace(
                        f"Quality: `{setup['quality']}p`",
                        f"Quality: `{selected_quality}p`"
                    )
                    caption = caption.replace(
                        "Quality: `Original`",
                        f"Quality: `{selected_quality}p`"
                    )

                    # Generate the thumbnail from the compressed video.
                    thumb_path = join(save_dir, "thumb.jpg")
                    rand_sec = random.randint(5, max(int(dur) - 5, 6))
                    await runcmd(
                        f'ffmpeg -hide_banner -loglevel error -nostats -y '
                        f'-ss {rand_sec} -i {shlex.quote(video_path)} '
                        f'-vframes 1 -q:v 2 {shlex.quote(thumb_path)}'
                    )
                    thumb_ok = (
                        os.path.exists(thumb_path)
                        and os.path.getsize(thumb_path) > 0
                    )
                else:
                    LOG.error("WM3 compression failed: %s", compress_error)
                    try:
                        await update_status(
                            "⚠️ Compression failed. Uploading the original recording."
                        )
                    except Exception:
                        pass
            else:
                try:
                    await update_status(
                        "⌛ No quality selected. Uploading the original recording."
                    )
                except Exception:
                    pass

         # ── Auto-compress recordings >= 2 GB; keep original resolution ───────
        # Runs silently before showing upload buttons. All audio tracks (multi-
        # language) are preserved. Skipped for cancelled/partial recordings.
        if not was_cancelled and not wm3_mode:
            video_path = await auto_compress_large_video(
                video_path, save_dir, dur, msg, user_id
            )
            # Recalculate size in case compression ran
            size_bytes = os.path.getsize(video_path)
            size_str   = (f"{size_bytes / (1024**3):.2f} GB" if size_bytes >= 1024**3
                          else f"{size_bytes / (1024**2):.1f} MB")
            mkv_filename = os.path.basename(video_path)
            # Regenerate thumbnail from potentially new file
            thumb_path = join(save_dir, "thumb.jpg")
            rand_sec   = random.randint(5, max(dur - 5, 6))
            await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-ss {rand_sec} -i {shlex.quote(video_path)} '
                f'-vframes 1 -q:v 2 {shlex.quote(thumb_path)}'
            )
            thumb_ok   = os.path.exists(thumb_path)
        # ────────────────────────────────────────────────────────────────────

        partial_note = "\n_⚠️ Partial recording (cancelled)_" if was_cancelled else ""

        # Auto-upload directly to Telegram — no button prompt
        try:
            await update_status(
                f"{'⏹ Cancelled — uploading recovered partial' if was_cancelled else '🎉 Recording Successfully Completed!'}\n\n"
                f"🎬 File Name: `{mkv_filename}`\n"
                f"📦 Size: `{size_str}`\n"
                f"⏱ Duration: `{TimeFormatter(dur * 1000)}`"
                f"{partial_note}\n\n"
                "⬆️ Uploading to Telegram…"
            )
        except Exception:
            pass

        upload_start = time.time()
        await split_and_send_video(
            send_target, video_path, caption, dur,
            thumb_path=thumb_path if thumb_ok else None,
            status_msg=msg,
            progress=progress_for_pyrogram,
            progress_args=(send_target, upload_start, msg, save_dir, was_cancelled, status_lock),
            _uid=user_id, _chat_id=send_target.chat.id,
        )

        if setup.get("auto_mode") and not was_cancelled and dur > 120:
            try:
                await update_status("✂️ Auto mode: generating last 2-minute clip…")
            except Exception:
                pass
            clip_dir  = join(save_dir, "auto_clips")
            os.makedirs(clip_dir, exist_ok=True)
            last_clip = join(clip_dir, "last_2min.mkv")
            last_start = max(0, dur - 120)
            await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-ss {last_start} -to {dur} -i {shlex.quote(video_path)} '
                f'-c copy {shlex.quote(last_clip)}'
            )
            if os.path.exists(last_clip) and os.path.getsize(last_clip) > 0:
                try:
                    await send_target.reply_video(
                        video=last_clip,
                        caption=(f"🎬 **{BRAND_TITLE}** — ⏭ Last 2 minute"),
                        supports_streaming=True,
                    )
                except Exception as ce:
                    LOG.warning(f"Auto clip upload failed: {ce}")

        schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"do_record error uid={user_id}: {e}")
        try:
            if (user_id, rec_id) not in cancelled_recs:
                if is_owner(user_id) or is_admin(user_id):
                    # Admins/owners see full technical error
                    err_text = str(e)
                    if len(err_text) > 3500:
                        err_text = "...[truncated]...\n" + err_text[-3500:]
                    await update_status(f"**Recording failed.**\n\n`{err_text}`")
                else:
                    # Normal users see a clean message — no FFmpeg internals
                    await update_status(
                        "❌ **Recording failed.**\n\n"
                        "Stream could not be recorded. Please check the link and try again.\n"
                        "Use /contact if the problem persists."
                    )
            if (user_id, rec_id) not in cancelled_recs and save_dir and os.path.exists(save_dir):
                _safe_rmtree(save_dir)
        except Exception as exc:
            LOG.error(f"Failed to edit error message: {exc}")
    finally:
        try:
            await _stop_live_preview(setup)
        except Exception:
            pass
        if user_id in active_recs:
            active_recs[user_id].pop(rec_id, None)
            if not active_recs[user_id]:
                del active_recs[user_id]
        cancelled_recs.discard((user_id, rec_id))

# ---------------------------------------------------------------------------
# OTT downloader helpers
# ---------------------------------------------------------------------------

_NETSCAPE_HEADER       = "# Netscape HTTP Cookie File"
_MAX_COOKIE_FILE_BYTES = 2 * 1024 * 1024
_MAX_COOKIE_ARCHIVE_BYTES = 8 * 1024 * 1024
_MAX_COOKIE_ARCHIVE_MEMBERS = 32
_COOKIE_PROMPT_TTL_SEC = 5 * 60


def _user_cookies_path(user_id: int) -> str:
    return join(COOKIES_DIRECTORY, f"{user_id}.txt")


def _user_has_cookies(user_id: int) -> bool:
    path = _user_cookies_path(user_id)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _cookies_summary(user_id: int) -> str:
    path = _user_cookies_path(user_id)
    if not os.path.exists(path):
        return "No cookies on file."
    try:
        size  = os.path.getsize(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=pytz.timezone(TIMEZONE))
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln for ln in f if ln.strip() and not ln.startswith("#")]
        hosts = sorted({ln.split("\t", 1)[0].lstrip(".") for ln in lines if "\t" in ln})
        host_preview = ", ".join(hosts[:6]) + ("…" if len(hosts) > 6 else "")
        return (f"Cookies are set.\n• Cookie lines: `{len(lines)}`\n"
                f"• File size: `{size} bytes`\n• Hosts: `{host_preview or 'unknown'}`\n"
                f"• Uploaded: `{mtime.strftime('%Y-%m-%d %H:%M %Z')}`")
    except Exception as e:
        return f"Cookies are set, but couldn't be read ({e})."


def _normalize_netscape_cookie_file(path: str) -> int:
    """Normalize browser/exporter cookie text without exposing cookie values.

    Some exporters prepend account metadata and omit the Netscape header.
    Keep only structurally valid seven-column Netscape cookie rows.
    """
    with open(path, "rb") as f:
        raw = f.read(_MAX_COOKIE_FILE_BYTES + 1)
    if len(raw) > _MAX_COOKIE_FILE_BYTES:
        raise ValueError("Cookie file is larger than 2 MB.")

    rows = []
    for raw_line in raw.decode("utf-8", errors="ignore").splitlines():
        line = raw_line.strip("\r\n")
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, include_subdomains, cookie_path, secure, expires, name, value = fields
        domain = domain.strip()
        if domain.startswith("#HttpOnly_"):
            domain = domain[len("#HttpOnly_"):]
        if (
            not domain
            or "." not in domain
            or include_subdomains not in {"TRUE", "FALSE"}
            or not cookie_path.startswith("/")
            or secure not in {"TRUE", "FALSE"}
            or not expires.isdigit()
            or not name.strip()
        ):
            continue
        if domain.startswith("#HttpOnly_"):
            domain = domain[len("#HttpOnly_"):]
        rows.append("\t".join([
            domain, include_subdomains, cookie_path, secure, expires, name, value
        ]))

    if not rows:
        raise ValueError(
            "No valid Netscape cookie rows found. Export cookies.txt again."
        )

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_NETSCAPE_HEADER + "\n")
        f.write("\n".join(rows))
        f.write("\n")
    return len(rows)


def _cookie_zip_entries(archive_path: str) -> list:
    """Return safe candidate cookie members from a ZIP archive."""
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_COOKIE_ARCHIVE_MEMBERS:
            raise ValueError("ZIP contains too many files.")

        total_size = 0
        candidates = []
        for info in infos:
            member_name = info.filename.replace("\\", "/")
            normalized = os.path.normpath(member_name)
            if (
                normalized in {".", ""}
                or normalized.startswith("..")
                or os.path.isabs(normalized)
                or member_name.startswith("/")
                or re.match(r"^[A-Za-z]:/", member_name)
                or any(part == ".." for part in member_name.split("/"))
            ):
                raise ValueError("ZIP contains an unsafe file path.")
            if info.is_dir():
                continue
            total_size += max(0, int(info.file_size))
            if total_size > _MAX_COOKIE_ARCHIVE_BYTES:
                raise ValueError("ZIP contents are too large.")
            if member_name.lower().endswith((".txt", ".cookies")):
                candidates.append(info)

        if not candidates:
            raise ValueError("ZIP must contain a .txt or .cookies file.")
        for info in candidates:
            if info.file_size > _MAX_COOKIE_FILE_BYTES:
                raise ValueError("Cookie file inside ZIP is larger than 2 MB.")
        return [(info.filename, int(info.file_size)) for info in candidates]


def _extract_cookie_file_from_zip(
    archive_path: str, output_path: str, member_name: str = ""
) -> None:
    """Extract one selected, safe cookie member from a ZIP archive."""
    candidates = _cookie_zip_entries(archive_path)
    if not member_name:
        if len(candidates) != 1:
            raise ValueError("ZIP must contain one selected cookie text file.")
        member_name = candidates[0][0]
    if member_name not in {name for name, _ in candidates}:
        raise ValueError("Selected cookie file is not available in this ZIP.")

    with zipfile.ZipFile(archive_path) as archive:
        selected = archive.getinfo(member_name)
        with archive.open(selected, "r") as source, open(output_path, "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)


def _fmt_bytes(n) -> str:
    if n is None: return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_eta(s) -> str:
    if s is None or s < 0: return "?"
    s = int(s)
    h, rem = divmod(s, 3600); m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _make_encode_progress_text(cmd_name: str, pct: float,
                                size_bytes: int = 0, total_bytes: int = 0,
                                speed_x: float = 0.0, eta_sec: float = 0.0) -> str:
    """Unified ffmpeg-encoding progress block used by all commands."""
    bar_len = 20
    filled  = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
    bar     = "●" * filled + "○" * (bar_len - filled)
    size_mb = size_bytes / (1024 * 1024)
    tot_mb  = total_bytes / (1024 * 1024) if total_bytes else 0
    size_str = f"`{size_mb:.1f} MB`" + (f" / `{tot_mb:.1f} MB`" if tot_mb else "")
    spd_str  = f"`{speed_x:.2f}x`" if speed_x else "`?`"
    eta_str  = _fmt_eta(int(eta_sec)) if eta_sec else "`?`"
    if not eta_str.startswith("`"):
        eta_str = f"`{eta_str}`"
    return (f"📡 **{cmd_name}**\n\n"
            f"Status: `encoding`\n"
            f"`{bar}` `{pct:5.1f}%`\n"
            f"💾 Size: {size_str}\n"
            f"⚡ Speed: {spd_str}\n"
            f"⏳ ETA: {eta_str}")


def _upload_dest_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Legacy upload keyboard; retained for commands that use it."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Telegram", callback_data=f"upl_ch:{uid}:tg")],
        [InlineKeyboardButton("❌ Cancel",    callback_data=f"upl_ch:{uid}:cancel")],
    ])


async def _await_upload_choice(uid: int, status_msg, info_text: str = "") -> str:
    """Default destination for existing commands (unchanged)."""
    return "tg"


def _watermark_upload_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Three destination choices shown only by /Watermark."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Telegram", callback_data=f"upl_ch:{uid}:tg")],
        [InlineKeyboardButton("☁️ Google Drive", callback_data=f"upl_ch:{uid}:gd")],
        [InlineKeyboardButton("📤 + ☁️ Telegram aur Drive", callback_data=f"upl_ch:{uid}:both")],
    ])


async def _await_watermark_upload_choice(uid: int, status_msg, info_text: str = "") -> str:
    """Wait for the user's Telegram / Drive / both choice for /Watermark."""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    dest = [None]
    pending_upload_state[uid] = {"dest": dest, "ev": event, "command": "watermark"}
    prompt = (
        "🎨 **Watermark processing complete**\\n\\n"
        f"{info_text}\\n\\n"
        "**Choose upload destination:**"
    )
    await status_msg.edit_text(prompt, reply_markup=_watermark_upload_keyboard(uid))
    try:
        await asyncio.wait_for(event.wait(), timeout=300)
        return dest[0] or "cancel"
    except asyncio.TimeoutError:
        try:
            await status_msg.edit_text("⌛ **Upload selection expired.** Run /Watermark again.")
        except Exception:
            pass
        return "cancel"
    finally:
        current = pending_upload_state.get(uid)
        if current and current.get("ev") is event:
            pending_upload_state.pop(uid, None)


def _upload_task_id(seed) -> str:
    """Derive a short 8-char hex task id from any hashable seed (path, id, etc)."""
    return hashlib.md5(str(seed).encode()).hexdigest()[:8]


def _fmt_upload_progress_box(title: str, current: int, total: int,
                              speed: float, eta_sec: int, task_id: str,
                              compact: bool = False) -> str:
    """Render the box-style upload progress card shared by Telegram/Drive uploads.

    compact=True renders the shorter Drive-style card (no Speed/ETA rows,
    whole-number percentage) matching the ☁️ Drive-only upload flow.
    """
    pct     = (current * 100 / total) if total else 0.0
    bar_len = 10
    filled  = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
    bar     = "⬢" * filled + "⬡" * (bar_len - filled)
    size_str = f"{current/(1024**3):.1f} / {total/(1024**3):.1f} GB" if total >= 1024**3 else \
               f"{current/(1024**2):.1f} / {total/(1024**2):.1f} MB"
    if compact:
        return (
            f"🚀 {title}\n\n"
            f"┌ 📊 Upload Progress\n"
            f"├ [{bar}] {pct:.0f}%\n"
            f"├ 💾 Size : {size_str}\n"
            f"└ 🆔 Task : {task_id}"
        )
    speed_str = f"{speed/(1024*1024):.1f} MB/s" if speed and speed > 0 else "-- MB/s"
    eta_str   = TimeFormatter(eta_sec * 1000) if eta_sec and eta_sec > 0 else "--:--"
    return (
        f"🚀 {title}\n\n"
        f"┌ 📊 Upload Progress\n"
        f"├ [{bar}] {pct:.1f}%\n"
        f"├ 💾 Size  : {size_str}\n"
        f"├ ⚡ Speed : {speed_str}\n"
        f"├ ⏳ ETA   : {eta_str}\n"
        f"└ 🆔 Task  : {task_id}"
    )


def _ott_progress_text(state: dict) -> str:
    pct     = state.get("percent", 0.0)
    bar_len = 20
    filled  = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
    bar     = "●" * filled + "○" * (bar_len - filled)
    speed   = state.get("speed")
    title   = state.get("title") or "Downloading"
    return (f"📡 **{title[:80]}**\n\nStatus: `{state.get('status', '?')}`\n"
            f"`{bar}` `{pct:5.1f}%`\n"
            f"💾 Size: `{_fmt_bytes(state.get('downloaded'))}` / `{_fmt_bytes(state.get('total'))}`\n"
            f"⚡ Speed: `{f'{_fmt_bytes(speed)}/s' if speed else '?'}`\n"
            f"⏳ ETA: `{_fmt_eta(state.get('eta'))}`")


# ---------------------------------------------------------------------------
# /download — manifest probe helpers
# ---------------------------------------------------------------------------

_LANG_DISPLAY = {
    "hin": "Hindi", "tam": "Tamil", "tel": "Telugu", "kan": "Kannada",
    "eng": "English", "mal": "Malayalam", "ben": "Bengali", "mar": "Marathi",
    "pun": "Punjabi", "urd": "Urdu", "und": "Unknown", "mul": "Multi",
}
_ACODEC_LABEL = {
    "mp4a": "AAC", "ec-3": "DD+", "ac-3": "DD",
    "opus": "Opus", "vorbis": "OGG", "flac": "FLAC",
}
_VCODEC_LABEL = {
    "avc1": "H.264", "hvc1": "H.265", "hev1": "H.265",
    "vp9": "VP9", "av01": "AV1",
}


def _fmt_codec(codec: str, table: dict) -> str:
    if not codec or codec == "none":
        return "?"
    c = codec.lower()
    for k, v in table.items():
        if c.startswith(k):
            return v
    return codec[:6]


def _lang_display(lang: str) -> str:
    return _LANG_DISPLAY.get((lang or "und").lower()[:3], (lang or "UND").upper()[:6])


def _probe_url_formats(url: str, cookies_path: str = "") -> dict:
    """Run yt-dlp extract_info(download=False) and return video/audio format lists."""
    opts: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "geo_bypass": True, "geo_bypass_country": "IN",
        "nocheckcertificate": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 11; SM-G973F) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        },
    }
    if cookies_path and os.path.exists(cookies_path):
        opts["cookiefile"] = cookies_path

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        info = entries[0] if entries else info

    title   = info.get("title") or ""
    formats = info.get("formats") or []

    # ── Video formats ──────────────────────────────────────────────────────
    def _vscore(f): return (f.get("height") or 0, f.get("vbr") or f.get("tbr") or 0)
    seen_v: set = set()
    video_fmts: list = []
    for muxed_pass in (False, True):     # prefer video-only; fall back to muxed
        for f in sorted(formats, key=_vscore, reverse=True):
            vc = f.get("vcodec", "none")
            ac = f.get("acodec", "none")
            if vc == "none":
                continue
            if not muxed_pass and ac != "none":
                continue
            if muxed_pass and ac == "none":
                continue
            h = int(f.get("height") or 0)
            if h < 100 or h in seen_v:
                continue
            seen_v.add(h)
            video_fmts.append({
                "id":     f["format_id"],
                "height": h,
                "vbr":    int(f.get("vbr") or f.get("tbr") or 0),
                "vcodec": f.get("vcodec", ""),
                "muxed":  ac != "none",
            })
        if video_fmts:
            break

    # ── Audio formats ──────────────────────────────────────────────────────
    seen_a: set = set()
    audio_fmts: list = []
    for f in sorted(formats, key=lambda x: (x.get("abr") or 0), reverse=True):
        ac = f.get("acodec", "none")
        vc = f.get("vcodec", "none")
        if ac == "none" or vc != "none":
            continue
        lang   = (f.get("language") or "und").lower()[:3]
        clabel = _fmt_codec(ac, _ACODEC_LABEL)
        abr    = int(f.get("abr") or 0)
        key    = (lang, clabel)
        if key in seen_a:
            continue
        seen_a.add(key)
        audio_fmts.append({
            "id":          f["format_id"],
            "lang":        lang,
            "lang_name":   _lang_display(lang),
            "abr":         abr,
            "acodec":      ac,
            "codec_label": clabel,
        })

    return {"title": title, "video_formats": video_fmts, "audio_formats": audio_fmts}


def _dl_status_text(sess: dict) -> str:
    url    = sess.get("url") or ""
    title  = sess.get("probe_title") or ""
    vfmts  = sess.get("video_formats") or []
    afmts  = sess.get("audio_formats") or []
    sel_v  = sess.get("sel_video_id") or "best"
    sel_a  = sess.get("sel_audio_ids") or []
    v_lbl  = ("🏆 Best" if sel_v == "best"
               else next((f"{f['height']}p {_fmt_codec(f['vcodec'], _VCODEC_LABEL)}"
                          for f in vfmts if f["id"] == sel_v), sel_v))
    a_lbl  = (", ".join(
                  next((f"{f['lang_name']} {f['codec_label']}"
                        for f in afmts if f["id"] == aid), aid)
                  for aid in sel_a
              ) or "🏆 Best")
    return (
        f"🎬 **Download Setup**\n\n"
        f"🔗 `{url[:55]}{'…' if len(url)>55 else ''}`\n"
        + (f"📌 **{title[:60]}**\n\n" if title else "\n")
        + f"🎥 Video : `{v_lbl}`\n"
        f"🔊 Audio : `{a_lbl}`\n\n"
        f"📺 {len(vfmts)} video quality option(s) found\n"
        f"🔈 {len(afmts)} audio track(s) found"
    )


def _dl_video_keyboard(uid: int, video_fmts: list, sel_vid: str) -> InlineKeyboardMarkup:
    rows: list = []
    for i in range(0, len(video_fmts), 2):
        row = []
        for f in video_fmts[i: i + 2]:
            tick  = "✅ " if f["id"] == sel_vid else ""
            codec = _fmt_codec(f["vcodec"], _VCODEC_LABEL)
            kbps  = f" {f['vbr']}k" if f["vbr"] else ""
            row.append(InlineKeyboardButton(
                f"{tick}{f['height']}p {codec}{kbps}",
                callback_data=f"dl:{uid}:v:{f['id']}"
            ))
        rows.append(row)
    tick = "✅ " if sel_vid == "best" else ""
    rows.append([InlineKeyboardButton(f"{tick}🏆 Best Available", callback_data=f"dl:{uid}:v:best")])
    rows.append([InlineKeyboardButton("🎵 Next: Audio Tracks →", callback_data=f"dl:{uid}:phase:audio")])
    rows.append([
        InlineKeyboardButton("⬇️ Skip → Download", callback_data=f"dl:{uid}:go"),
        InlineKeyboardButton("❌ Cancel",            callback_data=f"dl:{uid}:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def _dl_audio_keyboard(uid: int, audio_fmts: list, sel_ids: list,
                        default_lang: str = "") -> InlineKeyboardMarkup:
    rows: list = []
    for f in audio_fmts:
        is_sel    = f["id"] in sel_ids
        is_default = (default_lang and
                      default_lang.lower() in (f.get("lang_name") or "").lower())
        tick  = "✅" if is_sel else "○"
        def_tag = " 🏷" if is_default else ""
        kbps  = f" {f['abr']}k" if f["abr"] else ""
        rows.append([InlineKeyboardButton(
            f"{tick} {f['lang_name']} • {f['codec_label']}{kbps}{def_tag}",
            callback_data=f"dl:{uid}:a:{f['id']}"
        )])
    rows.append([
        InlineKeyboardButton("✅ Select All", callback_data=f"dl:{uid}:aall"),
        InlineKeyboardButton("🏆 Best Only",  callback_data=f"dl:{uid}:abest"),
    ])
    rows.append([
        InlineKeyboardButton("◀ Video Quality", callback_data=f"dl:{uid}:phase:video"),
        InlineKeyboardButton("⬇️ Download Now", callback_data=f"dl:{uid}:go"),
    ])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"dl:{uid}:cancel")])
    return InlineKeyboardMarkup(rows)


# Fallback keyboard when manifest probing fails
_DL_QUALITY_OPTS = {
    "best":  ("🏆 Best Quality", "bv*+ba/b"),
    "2160":  ("🎬 4K  (2160p)", "bestvideo[height<=2160]+bestaudio/best[height<=2160]"),
    "1080":  ("🖥  1080p",       "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
    "720":   ("📺 720p",         "bestvideo[height<=720]+bestaudio/best[height<=720]"),
    "480":   ("📱 480p",         "bestvideo[height<=480]+bestaudio/best[height<=480]"),
    "360":   ("🔹 360p",         "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    "audio": ("🎵 Audio Only",   "bestaudio/best"),
}


def _dl_fallback_keyboard(uid: int, sel_q: str = "best") -> InlineKeyboardMarkup:
    rows = []
    q_items = list(_DL_QUALITY_OPTS.items())
    for i in range(0, len(q_items), 2):
        row = []
        for key, (label, _) in q_items[i: i + 2]:
            tick = "✅ " if key == sel_q else ""
            row.append(InlineKeyboardButton(f"{tick}{label}", callback_data=f"dl:{uid}:q:{key}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("⬇️ Download", callback_data=f"dl:{uid}:go"),
        InlineKeyboardButton("❌ Cancel",   callback_data=f"dl:{uid}:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


async def handle_ott_download(client: Client, message: Message,
                               url: str = "",
                               filename: str = "",
                               video_id: str = "best",
                               audio_ids: list = None,
                               video_formats: list = None,
                               audio_formats: list = None,
                               status_msg=None,
                               # Legacy params kept for backward compat
                               fmt: str = "",
                               audio_lang: str = ""):
    user_id  = _message_actor_id(message)
    msg      = status_msg
    save_dir: Optional[str] = None
    if audio_ids is None:
        audio_ids = []
    try:
        if msg is None:
            msg = await _reply_auto_delete(message, AUTO_DELETE_5MIN, "⬇️ Initializing download...")
        else:
            try:
                await msg.edit_text("⬇️ Initializing download...")
            except Exception:
                pass
        # URL / filename: prefer explicit params, fall back to parsing message.text
        if not url:
            parts        = message.text.split(maxsplit=2)
            url          = parts[1].strip()
            raw_filename = parts[2].strip() if len(parts) > 2 else ""
        else:
            raw_filename = filename
        for bad in '/\\:*?"<>|':
            raw_filename = raw_filename.replace(bad, "_")

        save_dir = join(DOWNLOAD_DIRECTORY, f"ott_{int(time.time())}")
        os.makedirs(save_dir, exist_ok=True)
        user_tasks[user_id]  = time.time()
        user_status[user_id] = {"id": int(user_tasks[user_id]), "user_id": user_id,
                                "filename": raw_filename or "(auto)", "duration_str": "—",
                                "channel_name": "OTT", "url": url, "progress": "0%"}
        state: dict = {"status": "starting", "percent": 0.0, "downloaded": 0,
                       "total": None, "speed": None, "eta": None, "title": "Resolving..."}
        ott_progress[user_id] = state

        def _hook(d: dict):
            if user_id in cancelled_users:
                raise yt_dlp.utils.DownloadCancelled("Cancelled by user.")
            st = d.get("status")
            if st == "downloading":
                state["status"]     = "downloading"
                state["downloaded"] = d.get("downloaded_bytes") or 0
                state["total"]      = d.get("total_bytes") or d.get("total_bytes_estimate")
                if state["total"]:
                    state["percent"] = state["downloaded"] * 100 / state["total"]
                state["speed"] = d.get("speed")
                state["eta"]   = d.get("eta")
                info = d.get("info_dict") or {}
                if info.get("title"):
                    state["title"] = info["title"]
            elif st == "finished":
                state["status"]  = "finalizing"
                state["percent"] = 100.0

        async def watcher():
            last_text = ""
            while user_id in user_tasks:
                if user_id in cancelled_users:
                    return
                txt = _ott_progress_text(state)
                if txt != last_text:
                    try:
                        await msg.edit_text(txt)
                        last_text = txt
                    except Exception:
                        pass
                if user_status.get(user_id):
                    user_status[user_id]["progress"] = f"{state['percent']:.1f}%"
                await asyncio.sleep(10)

        watcher_task           = asyncio.create_task(watcher())
        progress_tasks[user_id] = watcher_task

        outtmpl  = join(save_dir, (raw_filename or "%(title).200B") + ".%(ext)s")

        # ── Build yt-dlp format string from selected IDs ─────────────────────
        if fmt:
            _fmt_str = fmt   # legacy caller provided fmt directly
            _extra_audio_ids: list = []
        elif video_id == "best":
            _fmt_str = "bv*+ba/b"
            _extra_audio_ids = audio_ids[1:] if len(audio_ids) > 1 else []
            if audio_ids:
                _fmt_str = f"bv*+{audio_ids[0]}/bv*+ba/b"
                _extra_audio_ids = list(audio_ids[1:])
        else:
            _primary_audio = audio_ids[0] if audio_ids else "ba"
            _fmt_str = f"{video_id}+{_primary_audio}/{video_id}/b"
            _extra_audio_ids = list(audio_ids[1:]) if len(audio_ids) > 1 else []

        # Multi-audio: MKV preserves multiple tracks; switch output if needed
        _has_multi_audio = bool(_extra_audio_ids)
        _merge_fmt = "mkv" if _has_multi_audio else "mp4"

        ydl_opts = {
            # ── Output ──────────────────────────────────────────────────────
            "outtmpl":              outtmpl,
            "trim_file_name":       200,
            "merge_output_format":  _merge_fmt,

            # ── Format selection ─────────────────────────────────────────────
            "format":  _fmt_str,
            # Prefer h264/aac in mp4 — best device compatibility
            "format_sort": ["res", "ext:mp4:m4a", "codec:h264:aac", "size", "br"],

            # ── Reliability ─────────────────────────────────────────────────
            "noplaylist":               True,
            "retries":                  5,
            "fragment_retries":         5,
            "file_access_retries":       3,
            "extractor_retries":         3,
            "concurrent_fragment_downloads": MAX_CONCURRENT_FRAGMENT_DOWNLOADS,
            "socket_timeout":           30,
            "hls_use_mpegts":           True,
            "noprogress":               True,

            # ── Metadata & thumbnail ─────────────────────────────────────────
            "writethumbnail":           False,
            "embedthumbnail":           False,
            "add_metadata":             True,

            # ── Logging ──────────────────────────────────────────────────────
            "quiet":                    True,
            "no_warnings":              True,
            "verbose":                  False,

            # ── Geo & identity ───────────────────────────────────────────────
            "geo_bypass":               True,
            "geo_bypass_country":       "IN",
            "nocheckcertificate":       True,

            # ── FFmpeg postprocessor ─────────────────────────────────────────
            "prefer_ffmpeg":            True,
            "postprocessor_args": {
                "default": ["-threads", "0"],           # use all CPU cores
                "merger": [
                    "-c:v", "copy", "-c:a", "copy",
                    "-movflags", "+faststart",           # web-optimised MP4
                ],
            },

            # ── Progress hook ────────────────────────────────────────────────
            "progress_hooks": [_hook],

            # ── Site-specific extractor args ──────────────────────────────────
            "extractor_args": {
                "hotstar":   {"video_resolution": ["max"]},
                "sonyliv":   {"prefer_subs_lang": ["hi"], "device_id": ["default"]},
                "jiosaavn":  {"quality": ["320kbps"]},
                "youtube":   {
                    "player_client": ["ios", "android", "mweb", "web"],
                    "skip": ["hls", "dash"],
                },
            },
            "http_headers": {
                "User-Agent":      "Mozilla/5.0 (Linux; Android 11; SM-G973F) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Mobile Safari/537.36",
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
                "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        }
        if _user_has_cookies(user_id):
            ydl_opts["cookiefile"] = _user_cookies_path(user_id)

        def _run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if "requested_downloads" in info and info["requested_downloads"]:
                    info["_final_filepath"] = info["requested_downloads"][0]["filepath"]
                else:
                    info["_final_filepath"] = ydl.prepare_filename(info)
                return info

        try:
            info = await asyncio.to_thread(_run_ydl)
        except yt_dlp.utils.DownloadCancelled:
            await msg.edit_text("Download cancelled.")
            return

        watcher_task.cancel()
        progress_tasks.pop(user_id, None)

        video_path = info.get("_final_filepath")
        if not video_path or not os.path.exists(video_path):
            raise Exception("yt-dlp finished but the output file is missing.")

        # ── Extra audio tracks (multi-audio) ─────────────────────────────────
        if _extra_audio_ids:
            await msg.edit_text("🔊 Downloading extra audio tracks…")
            extra_audio_paths = []
            _base_opts = {k: v for k, v in ydl_opts.items()
                          if k not in ("format", "outtmpl", "merge_output_format",
                                       "progress_hooks", "postprocessor_args")}
            for _aid in _extra_audio_ids:
                _extra_tmpl = join(save_dir, f"extra_audio_{_aid}.%(ext)s")
                _extra_opts = {**_base_opts, "format": _aid, "outtmpl": _extra_tmpl,
                               "quiet": True, "no_warnings": True}
                try:
                    def _dl_extra(opts=_extra_opts, _url=url):
                        with yt_dlp.YoutubeDL(opts) as _ydl:
                            _ydl.download([_url])
                    await asyncio.to_thread(_dl_extra)
                    for _fn in os.listdir(save_dir):
                        if _fn.startswith(f"extra_audio_{_aid}"):
                            extra_audio_paths.append(join(save_dir, _fn))
                            break
                except Exception as _ex:
                    LOG.warning(f"Extra audio {_aid} failed: {_ex}")

            if extra_audio_paths:
                await msg.edit_text("🧩 Merging audio tracks…")
                _merged = join(save_dir, f"multitrack_{int(time.time())}.mkv")
                _in_args = f'-i {shlex.quote(video_path)} ' + \
                           " ".join(f'-i {shlex.quote(p)}' for p in extra_audio_paths)
                _map_args = "-map 0 " + \
                            " ".join(f"-map {i+1}:a" for i in range(len(extra_audio_paths)))

                # ── Set disposition: mark user's preferred language as default ──
                # Total audio streams = 1 (primary) + len(extra_audio_paths)
                _total_audio = 1 + len(extra_audio_paths)
                _pref_lang_d = (user_dl_prefs.get(user_id) or {}).get("default_audio_lang", "")
                _default_idx = 0  # fallback: first audio stream = default
                if _pref_lang_d and audio_formats:
                    # Find which audio_id matches the preferred lang
                    for _aidx, _aid in enumerate(audio_ids):
                        _af = next((f for f in audio_formats if f["id"] == _aid), None)
                        if _af and _pref_lang_d.lower() in (_af.get("lang_name") or "").lower():
                            _default_idx = _aidx
                            break
                _disp_args = " ".join(
                    f"-disposition:a:{_i} {'default' if _i == _default_idx else 'none'}"
                    for _i in range(_total_audio)
                )

                _rc, _, _err = await runcmd(
                    f'ffmpeg -hide_banner -loglevel error -nostats -y '
                    f'{_in_args} {_map_args} -c copy {_disp_args} {shlex.quote(_merged)}'
                )
                if _rc == 0 and os.path.exists(_merged):
                    video_path = _merged
                else:
                    LOG.warning(f"Multi-audio merge failed: {_err.strip()[:300]}")

        await msg.edit_text("Download finished — preparing upload...")
        title    = info.get("title") or os.path.basename(video_path)
        duration = int(info.get("duration") or 0)

        thumb_path = None
        if duration > 6:
            ts         = random.randint(2, max(duration - 2, 3))
            cand_thumb = join(save_dir, "thumb.jpg")
            rc, _o, _e = await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-ss {ts} -i "{video_path}" -vframes 1 -q:v 2 "{cand_thumb}"')
            if rc == 0 and os.path.exists(cand_thumb):
                thumb_path = cand_thumb

        retention_note = (f"_The video is automatically deleted from the server after "
                          f"{_retention_label()}._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"Duration: `{TimeFormatter(duration * 1000)}`\n"
                   f"Source: `{(info.get('extractor_key') or info.get('extractor') or 'OTT')}`\n"
                   f"{retention_note}")

        _dest = await _await_upload_choice(
            user_id, msg,
            f"Size: `{os.path.getsize(video_path)/(1024*1024):.1f} MB` | Duration: `{TimeFormatter(duration*1000)}`"
        )
        if _dest != "cancel":
            if _dest in ("tg", "both"):
                start_time = time.time()
                await split_and_send_video(
                    message, video_path, caption, duration or 0,
                    thumb_path=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                    status_msg=msg,
                    progress=progress_for_pyrogram,
                    progress_args=(message, start_time, msg, save_dir, False),
                    _uid=user_id, _chat_id=message.chat.id,
                )
            if _dest in ("gd", "both"):
                await upload_and_notify(client, message.chat.id, video_path, os.path.basename(video_path))
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Error in handle_ott_download: {e}", exc_info=True)
        try:
            err_text  = str(e)
            err_lower = err_text.lower()
            hints = []
            if any(k in err_lower for k in ("drm", "widevine", "playready", "encrypted")):
                hints.append("🔒 **DRM-protected content**. No tool can download this — try free episodes only.")
            if any(k in err_lower for k in ("login required", "subscription", "premium", "sign in",
                                             "registered users", "cookies")):
                hints.append("🔑 **Login needed.** Run /set_cookies with a fresh `cookies.txt`.")
            if any(k in err_lower for k in ("geo", "not available in your", "403", "forbidden")):
                hints.append("🌐 **Geo-blocked** — server IP is outside India.")
            if any(k in err_lower for k in ("expired", "session", "invalid token", "401")):
                hints.append("⏱ **Cookies expired.** Re-export `cookies.txt` and run /set_cookies again.")
            if "epdblocked" in err_lower:
                hints.append(
                    "📛 **SonyLIV API blocked** — SonyLIV is blocking yt-dlp requests.\n"
                    "✅ **Fix:** Upload fresh SonyLIV cookies using /set_cookies and retry.\n"
                    "• Open SonyLIV in browser → export cookies as `cookies.txt` → send to bot.\n"
                    "• Free / non-login content may still be unavailable due to geo-blocking."
                )
            hint_block = ("\n\n" + "\n\n".join(hints)) if hints else ""

            if is_owner(user_id) or is_admin(user_id):
                if len(err_text) > 2500:
                    err_text = "...[truncated]...\n" + err_text[-2500:]
                fail_text = f"**Download failed.**\n\n`{err_text}`{hint_block}"
            else:
                fail_text = (
                    "❌ **Download failed.**\n\n"
                    "Could not download this video. Please check the link and try again."
                    f"{hint_block}\n\nUse /contact if the problem persists."
                )

            # msg may be None if reply_text itself failed — fall back to a fresh reply
            if msg:
                await msg.edit_text(fail_text)
            else:
                await _reply_auto_delete(message, AUTO_DELETE_5MIN, fail_text)
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        ott_progress.pop(user_id, None)
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)

# ---------------------------------------------------------------------------
# Compress helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-compression: files >= 2 GB → target ~470 MB (keep under 500 MB)
# ---------------------------------------------------------------------------
AUTO_COMPRESS_MIN_MB    = 2048      # only trigger at 2 GB or larger
AUTO_COMPRESS_MAX_MB    = 102400    # upper limit 100 GB; protects against bad settings
AUTO_COMPRESS_TARGET_MB = 470       # headroom so final file is usually under 500 MB
AUTO_COMPRESS_HEIGHTS   = []  # keep each source video at its original resolution

# Persistent toggle — stored in bot_settings.json
_BOT_SETTINGS_FILE = join(DATA_DIRECTORY, "bot_settings.json")


def _load_bot_settings() -> dict:
    try:
        if os.path.exists(_BOT_SETTINGS_FILE):
            with open(_BOT_SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_bot_settings(data: dict) -> None:
    try:
        with open(_BOT_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        LOG.warning("Could not save bot_settings: %s", e)


def _auto_compress_enabled() -> bool:
    """Return True (default) unless owner explicitly disabled auto-compression."""
    return _load_bot_settings().get("auto_compress", True)


def _set_auto_compress(enabled: bool) -> None:
    """Persist the auto-compress toggle."""
    settings = _load_bot_settings()
    settings["auto_compress"] = enabled
    _save_bot_settings(settings)


def _get_compress_settings() -> dict:
    """Return current auto-compress thresholds with hard-coded defaults."""
    s = _load_bot_settings()
    return {
        "min_mb":    s.get("ac_min_mb",    AUTO_COMPRESS_MIN_MB),
        "max_mb":    s.get("ac_max_mb",    AUTO_COMPRESS_MAX_MB),
        "target_mb": s.get("ac_target_mb", AUTO_COMPRESS_TARGET_MB),
    }


def _update_compress_settings(**kwargs) -> None:
    """Persist one or more of: min_mb, max_mb, target_mb."""
    settings = _load_bot_settings()
    for key, value in kwargs.items():
        settings[f"ac_{key}"] = value
    _save_bot_settings(settings)


COMPRESS_SIZE_OPTIONS_MB = [300, 400, 500, 600, 800]
COMPRESS_RES_OPTIONS = [
    ("140p", "h140"), ("240p", "h240"), ("360p", "h360"), ("480p", "h480"),
    ("576p", "h576"), ("640p", "h640"), ("720p", "h720"),
    ("1080p HD", "h1080hevc"), ("1080p", "h1080"), ("HQ", "hq"), ("2K", "h1440"), ("3K", "h2160"),
]
COMPRESS_RES_CONFIG = {
    "h140":      {"height": 140,  "codec": "libx264", "label": "140p"},
    "h240":      {"height": 240,  "codec": "libx264", "label": "240p"},
    "h360":      {"height": 360,  "codec": "libx264", "label": "360p"},
    "h480":      {"height": 480,  "codec": "libx264", "label": "480p"},
    "h576":      {"height": 576,  "codec": "libx264", "label": "576p"},
    "h640":      {"height": 640,  "codec": "libx264", "label": "640p"},
    "h720":      {"height": 720,  "codec": "libx264", "label": "720p"},
    "h1080hevc": {"height": 1080, "codec": "libx265", "label": "1080p HD (HEVC)"},
    "h1080":     {"height": 1080, "codec": "libx264", "label": "1080p"},
    "h1440":     {"height": 1440, "codec": "libx264", "label": "2K"},
    "h2160":     {"height": 2160, "codec": "libx264", "label": "3K"},
    "hq":        {"height": 0,    "codec": "libx264", "label": "HQ (original)"},
}
LANG_LABEL = {
    "hin": "Hindi", "tam": "Tamil", "tel": "Telugu", "mal": "Malayalam",
    "kan": "Kannada", "mar": "Marathi", "ben": "Bengali", "guj": "Gujarati",
    "pan": "Punjabi", "ori": "Odia", "asm": "Assamese", "urd": "Urdu",
    "eng": "English", "und": "Untagged", "multi": "Multi (all)",
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "kn": "Kannada", "mr": "Marathi", "bn": "Bengali", "gu": "Gujarati",
    "pa": "Punjabi", "or": "Odia", "as": "Assamese", "ur": "Urdu", "en": "English",
}
COMPRESS_LANG_PRESET = ["hin", "tam", "tel", "mal", "kan", "mar", "eng", "multi"]


def _compress_menu(state: dict) -> InlineKeyboardMarkup:
    rows    = []
    sel_size = state.get("size_mb")
    rows.append([InlineKeyboardButton(f"{'✓ ' if sel_size == s else ''}{s} MB",
                                      callback_data=f"cmp:size:{s}")
                 for s in COMPRESS_SIZE_OPTIONS_MB])
    sel_res     = state.get("res_key")
    res_buttons = [InlineKeyboardButton(f"{'✓ ' if sel_res == k else ''}{lbl}",
                                        callback_data=f"cmp:res:{k}")
                   for lbl, k in COMPRESS_RES_OPTIONS]
    for i in range(0, len(res_buttons), 4):
        rows.append(res_buttons[i:i + 4])
    sel_langs = set(state.get("langs", []))
    available = state.get("available_langs", [])
    visible   = [l for l in COMPRESS_LANG_PRESET if l == "multi" or l in available]
    for extra in available:
        if extra not in COMPRESS_LANG_PRESET and extra not in visible:
            visible.append(extra)
    if not visible:
        visible = ["multi"]
    lang_buttons = [InlineKeyboardButton(f"{'✓ ' if l in sel_langs else ''}{LANG_LABEL.get(l, l.upper())}",
                                         callback_data=f"cmp:lang:{l}")
                    for l in visible]
    for i in range(0, len(lang_buttons), 3):
        rows.append(lang_buttons[i:i + 3])
    rows.append([InlineKeyboardButton("▶ Start", callback_data="cmp:start"),
                 InlineKeyboardButton("✖ Cancel", callback_data="cmp:cancel")])
    return InlineKeyboardMarkup(rows)


def _compress_status_text(state: dict) -> str:
    duration   = state.get("duration", 0)
    src_h      = state.get("video_height", 0)
    avail      = state.get("available_langs", [])
    avail_text = (", ".join(LANG_LABEL.get(l, l.upper()) for l in avail)
                  if avail else "(no language tags)")
    sel_size   = state.get("size_mb")
    sel_res    = state.get("res_key")
    res_label  = COMPRESS_RES_CONFIG[sel_res]["label"] if sel_res else "—"
    sel_langs  = state.get("langs") or []
    langs_text = ", ".join(LANG_LABEL.get(l, l.upper()) for l in sel_langs) or "—"
    return (f"**🗜 Video Compressor**\n\nSource: `{TimeFormatter(int(duration * 1000))}`"
            f" • `{src_h}p` • `{len(state.get('audio_streams', []))}` audio track(s)\n"
            f"Available audio langs: {avail_text}\n\n**Choose options:**\n"
            f"• Target size: `{sel_size or '—'} MB`\n• Resolution / codec: `{res_label}`\n"
            f"• Audio: `{langs_text}`\n\n"
            f"_Default audio is **Hindi** when present. Tap **Multi** to keep all tracks._")


def _compress_progress_text(pct, done_sec, dur_sec, size_bytes, target_mb, speed_mult):
    remaining_sec = max(0.0, (dur_sec - done_sec) / max(0.05, speed_mult))
    return _make_encode_progress_text(
        "Compressing", pct,
        size_bytes=int(size_bytes),
        total_bytes=int(target_mb * 1024 * 1024),
        speed_x=speed_mult,
        eta_sec=remaining_sec,
    )


async def auto_compress_large_video(
    video_path: str, save_dir: str, duration: float,
    status_msg, user_id: int,
) -> str:
    """
    Automatically compress recordings >= 2 GB toward a 470 MB target, leaving
    headroom for a final file under 500 MB. Keeps the source resolution unchanged
    (e.g. 576p stays 576p, 720p stays 720p, and 1080p stays 1080p); bitrate/encoding
    quality may decrease to reduce file size. ALL audio tracks are preserved with
    -map 0:a? so multi-language recordings stay intact. Subtitle tracks are removed.
    Returns the path to the compressed file, or the original path on failure.
    """
    # Respect the owner toggle — skip silently if disabled
    if not _auto_compress_enabled():
        return video_path

    cs = _get_compress_settings()
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if not (cs["min_mb"] <= size_mb <= cs["max_mb"]):
        return video_path

    target_mb  = cs["target_mb"]
    cpu_threads = str(min(os.cpu_count() or 1, 4))

    # Probe the original video height and retain the source resolution. Do not
    # downscale 1080p to 720p/576p or upscale lower-resolution sources.
    try:
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=height", "-of", "default=noprint_wrappers=1:nokey=1",
            video_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await probe.communicate()
        source_height = int(probe_out.decode().strip().splitlines()[0])
        if source_height <= 0:
            raise ValueError("invalid source height")
    except Exception as e:
        LOG.warning("auto_compress: could not detect source resolution: %s", e)
        return video_path

    out_path = join(save_dir, f"autocomp_original_{int(time.time())}.mkv")
    try:
        probe_a = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", video_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        audio_out, _ = await probe_a.communicate()
        audio_count = len([x for x in audio_out.decode().splitlines() if x.strip()])
    except Exception:
        audio_count = 1

    # Account for every audio track in the target budget; preserve all tracks.
    target_total_kbps = (target_mb * 8 * 1024) / max(1, duration)
    audio_budget_kbps = max(128, audio_count * 128)
    video_kbps = max(100, int(target_total_kbps - audio_budget_kbps - 48))
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
        "-threads", cpu_threads,
        "-progress", "pipe:1", "-y",
        "-i", video_path,
        "-map", "0:v:0",
        "-map", "0:a?",       # ALL audio tracks — multi-language safe
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{int(video_kbps * 1.4)}k",
        "-bufsize", f"{int(video_kbps * 2.5)}k",
        "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        out_path,
    ]

    try:
        await status_msg.edit_text(
            f"🗜 **Auto-Compressing…**\n\n"
            f"File size: `{size_mb:.0f} MB` (2 GB+ auto-compression)\n"
            f"Target: `~{target_mb} MB` | Original resolution: `{source_height}p`\n"
            f"_Resolution unchanged; all audio/language tracks preserved. Subtitles are removed._"
        )
    except Exception:
        pass

    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    user_ffmpeg_pids[user_id] = proc.pid
    progress_state = {"out_time_us": 0, "total_size": 0, "speed": 1.0}

    async def _read_prog(_proc=proc):
        while True:
            line = await _proc.stdout.readline()
            if not line:
                return
            line_text = line.decode("utf-8", errors="ignore").strip()
            if "=" not in line_text:
                continue
            k, v = line_text.split("=", 1)
            if k == "out_time_us":
                try: progress_state["out_time_us"] = int(v)
                except ValueError: pass
            elif k == "total_size":
                try: progress_state["total_size"] = int(v)
                except ValueError: pass
            elif k == "speed" and v not in ("N/A", ""):
                try: progress_state["speed"] = float(v.rstrip("x"))
                except ValueError: pass

    async def _render_prog(_proc=proc):
        while _proc.returncode is None:
            done_sec = progress_state["out_time_us"] / 1_000_000
            pct = min(100.0, done_sec / max(1, duration) * 100)
            cur_mb = progress_state["total_size"] / (1024 * 1024)
            spd = progress_state["speed"]
            bar_filled = int(pct / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            try:
                await status_msg.edit_text(
                    f"🗜 **Auto-Compressing…** {pct:.1f}%\n`{bar}`\n\n"
                    f"Resolution: `{source_height}p` (original) | Target: `{target_mb} MB`\n"
                    f"Current size: `{cur_mb:.1f} MB` | Speed: `{spd:.1f}x`\n"
                    f"_All audio tracks preserved ✓ | Subtitles removed_"
                )
            except Exception:
                pass
            await asyncio.sleep(8)

    reader_task = asyncio.create_task(_read_prog())
    renderer_task = asyncio.create_task(_render_prog())
    rc = await proc.wait()
    reader_task.cancel()
    renderer_task.cancel()
    user_ffmpeg_pids.pop(user_id, None)

    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        err = (await proc.stderr.read()).decode(errors="ignore")
        LOG.warning("auto_compress original-resolution failed rc=%s: %s", rc, err[-500:])
        try: os.remove(out_path)
        except Exception: pass
        try:
            await status_msg.edit_text(f"⚠️ Auto-compression failed — uploading original `{size_mb:.0f} MB` file.")
        except Exception: pass
        await asyncio.sleep(2)
        return video_path

    out_mb = os.path.getsize(out_path) / (1024 * 1024)
    LOG.info("auto_compress: %s MB → %.1f MB @ original %sp uid=%s", int(size_mb), out_mb, source_height, user_id)
    try:
        await status_msg.edit_text(
            f"✅ **Auto-Compression Done!**\n\n"
            f"Original: `{size_mb:.0f} MB` → Compressed: `{out_mb:.1f} MB`\n"
            f"Resolution: `{source_height}p` (unchanged) | All audio tracks kept ✓\n\n"
            f"_Preparing upload options…_"
        )
    except Exception:
        pass
    await asyncio.sleep(2)
    return out_path

    # All resolutions failed — warn and fall back to the original file
    LOG.warning(f"auto_compress: all resolutions failed, using original uid={user_id}")
    try:
        await status_msg.edit_text(
            f"⚠️ Auto-compression failed — uploading original "
            f"`{size_mb:.0f} MB` file."
        )
    except Exception:
        pass
    await asyncio.sleep(2)
    return video_path


async def run_compress(client: Client, status_msg: Message, state: dict):
    user_id  = state["user_id"]
    save_dir = state["save_dir"]
    src      = state["src_path"]
    duration = state["duration"]
    target_mb = state["size_mb"]
    res_cfg  = COMPRESS_RES_CONFIG[state["res_key"]]
    langs    = state["langs"]
    out_path = join(save_dir, f"compressed_{int(time.time())}.mkv")

    user_tasks[user_id]  = time.time()
    user_status[user_id] = {"id": int(user_tasks[user_id]), "user_id": user_id,
                            "filename": os.path.basename(out_path),
                            "duration_str": TimeFormatter(int(duration * 1000)),
                            "channel_name": "Compress", "url": "(local)", "progress": "0%"}

    if "multi" in langs:
        kept_audio = list(state["audio_streams"])
    else:
        kept_audio = [s for s in state["audio_streams"] if s["lang"] in langs]
    audio_kbps_per   = 128
    audio_total_kbps = audio_kbps_per * max(1, len(kept_audio) or 1)
    target_total_kbps = (target_mb * 8 * 1024) / max(1, duration)
    video_kbps       = max(80, int(target_total_kbps - audio_total_kbps - 32))

    cpu_threads = str(min(os.cpu_count() or 1, 4))   # modest cap to speed encoding without saturating the host
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-threads", cpu_threads,
            "-progress", "pipe:1", "-y", "-i", src, "-map", "0:v:0"]
    if "multi" in langs or not kept_audio:
        args += ["-map", "0:a?"]
    else:
        for s in kept_audio:
            args += ["-map", f"0:{s['index']}"]
    if res_cfg["height"] > 0:
        args += ["-vf", f"scale=-2:{res_cfg['height']}:flags=lanczos"]
    if state["res_key"] == "hq":
        args += ["-c:v", res_cfg["codec"], "-crf", "18", "-preset", "fast",
                 "-tune", "film"]
    elif res_cfg["codec"] == "libx265":
        args += ["-c:v", "libx265", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2.5)}k",
                 "-preset", "fast", "-x265-params", "log-level=error:aq-mode=3",
                 "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "libx264", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2.5)}k",
                 "-preset", "fast", "-tune", "film"]
    args += ["-c:a", "aac", "-b:a", f"{audio_kbps_per}k", "-ar", "48000",
             "-movflags", "+faststart",   # MP4 optimised for streaming/Telegram
             out_path]

    try:
        await status_msg.edit_text("Compressing — preparing...", reply_markup=None)
    except Exception:
        pass

    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    user_ffmpeg_pids[user_id] = proc.pid
    progress_state = {"out_time_us": 0, "total_size": 0, "speed": 1.0}

    async def read_progress():
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="ignore").strip()
            if "=" not in text:
                continue
            k, v = text.split("=", 1)
            if k == "out_time_us":
                try: progress_state["out_time_us"] = int(v)
                except ValueError: pass
            elif k == "total_size":
                try: progress_state["total_size"] = int(v)
                except ValueError: pass
            elif k == "speed" and v not in ("N/A", ""):
                try: progress_state["speed"] = float(v.rstrip("x"))
                except ValueError: pass

    async def render():
        last = ""
        while proc.returncode is None:
            if user_id in cancelled_users:
                return
            done_sec = progress_state["out_time_us"] / 1_000_000
            pct      = min(100.0, done_sec / max(1, duration) * 100)
            txt      = _compress_progress_text(pct, done_sec, duration,
                                               progress_state["total_size"], target_mb,
                                               progress_state["speed"])
            if txt != last:
                try:
                    await status_msg.edit_text(txt)
                    last = txt
                    if user_status.get(user_id):
                        user_status[user_id]["progress"] = f"{pct:.1f}%"
                except Exception:
                    pass
            await asyncio.sleep(10)

    progress_reader   = asyncio.create_task(read_progress())
    progress_renderer = asyncio.create_task(render())
    progress_tasks[user_id] = progress_renderer

    try:
        rc = await proc.wait()
        progress_reader.cancel()
        progress_renderer.cancel()
        user_ffmpeg_pids.pop(user_id, None)

        if user_id in cancelled_users:
            try: await status_msg.edit_text("Compress cancelled.")
            except Exception: pass
            _safe_rmtree(save_dir)
            return

        if rc != 0:
            err  = (await proc.stderr.read()).decode(errors="ignore")
            tail = err[-1500:] if len(err) > 1500 else err
            raise Exception(f"FFmpeg exit {rc}\n{tail}")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise Exception("Output file missing or empty.")

        thumb     = join(save_dir, "thumb.jpg")
        thumb_at  = max(1, min(int(duration / 2), int(duration) - 1))
        await runcmd(f'ffmpeg -hide_banner -loglevel error -nostats -y '
                     f'-ss {thumb_at} -i {shlex.quote(out_path)} '
                     f'-vframes 1 -q:v 2 {shlex.quote(thumb)}')
        thumb_path   = thumb if os.path.exists(thumb) else None
        out_size_mb  = os.path.getsize(out_path) / (1024 * 1024)
        retention_note = (f"_The video is automatically deleted from the server after "
                          f"{_retention_label()}._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"Compressed: `{out_size_mb:.1f} MB` (target `{target_mb} MB`)\n"
                   f"Duration: `{TimeFormatter(int(duration * 1000))}`\n"
                   f"Resolution / codec: `{res_cfg['label']}`\n"
                   f"Audio: `{', '.join(LANG_LABEL.get(l, l.upper()) for l in langs)}`\n"
                   f"{retention_note}")

        _dest = await _await_upload_choice(
            user_id, status_msg,
            f"Compressed: `{out_size_mb:.1f} MB` (target `{target_mb} MB`)"
        )
        if _dest != "cancel":
            await _run_upload_destination(
                client, user_id, _dest, status_msg, out_path, caption, duration,
                thumb_path=thumb_path, save_dir=save_dir,
            )
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)
    except Exception as e:
        LOG.error(f"Compress failed uid={user_id}: {e}")
        try:
            err_text = str(e)
            if len(err_text) > 3500: err_text = "...[truncated]...\n" + err_text[-3500:]
            await status_msg.edit_text(f"❌ **Compress failed.**\n\n`{err_text}`")
        except Exception: pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        compress_jobs.pop(user_id, None)
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        user_ffmpeg_pids.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)

# ---------------------------------------------------------------------------
# Reclink (headless Chromium)
# ---------------------------------------------------------------------------

def _resolve_chromium_path() -> Optional[str]:
    env_path = os.environ.get("CHROMIUM_PATH") or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _stream_request_flags(headers: dict | None) -> dict:
    """Keep only recording-relevant headers captured from a player request."""
    result: dict = {}
    for key, value in (headers or {}).items():
        name = str(key).lower().replace("_", "-")
        value = str(value or "").strip()
        if not value:
            continue
        if name == "user-agent":
            result["user_agent"] = value
        elif name == "cookie":
            result["cookie"] = value
        elif name == "referer":
            result["referer"] = value
        elif name == "origin":
            result["origin"] = value
    return result


def _looks_like_master_playlist(url: str) -> bool:
    u = url.lower()
    return ".m3u8" in u and any(k in u for k in ("master", "index", "playlist", "manifest"))


async def _extract_streams_with_chromium(page_url: str, timeout_sec: int = 30, log_cb=None) -> dict:
    from playwright.async_api import async_playwright
    log: list = []
    def L(msg: str):
        log.append(msg)
        if log_cb:
            try: log_cb(msg)
            except Exception: pass

    chromium_path = _resolve_chromium_path()
    L(f"Using Chromium: `{chromium_path or 'playwright default'}`")
    seen: dict = {}

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                     "--disable-blink-features=AutomationControlled", "--mute-audio"],
        }
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            raise Exception(f"Could not launch Chromium: {e}")

        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 720}, ignore_https_errors=True,
        )
        page = await context.new_page()

        def on_request(req):
            try:
                u = req.url
                if ".m3u8" in u.lower() or ".mpd" in u.lower():
                    if u not in seen:
                        seen[u] = (dict(req.headers), _looks_like_master_playlist(u))
                        L(f"📡 captured `{u[:90]}{'…' if len(u) > 90 else ''}`")
            except Exception: pass

        page.on("request", on_request)
        try:
            L(f"Opening page (timeout {timeout_sec}s)...")
            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            except Exception as nav_err:
                L(f"goto warn: {nav_err}")
            await page.wait_for_timeout(3500)
            for sel in ["button[aria-label*='play' i]", "button[title*='play' i]",
                        ".vjs-big-play-button", ".plyr__control--overlaid", ".jw-icon-display",
                        ".play-button", ".play-btn", "[class*='play' i][class*='button' i]", "video"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click(timeout=1500, force=True)
                        L(f"clicked `{sel}`")
                        await page.wait_for_timeout(1500)
                        if seen: break
                except Exception: pass
            await page.wait_for_timeout(2500)
            page_title = await page.title()
            final_url  = page.url
        finally:
            try: await browser.close()
            except Exception: pass

    streams = [{"url": u, "headers": h, "is_master": m} for u, (h, m) in seen.items()]
    streams.sort(key=lambda s: (not s["is_master"], len(s["url"])))
    L(f"Done. Found {len(streams)} stream(s).")
    return {"streams": streams, "page_title": page_title, "final_url": final_url, "log": log}

# ---------------------------------------------------------------------------
# Screenshot helpers
# ---------------------------------------------------------------------------

SS_MIN, SS_MAX, SS_PER_ROW = 1, 30, 5


def _ss_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(str(n), callback_data=f"ss:n:{n}")
               for n in range(SS_MIN, SS_MAX + 1)]
    rows = [buttons[i:i + SS_PER_ROW] for i in range(0, len(buttons), SS_PER_ROW)]
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data="ss:cancel")])
    return InlineKeyboardMarkup(rows)


def _ss_menu_text(state: dict) -> str:
    duration = state.get("duration", 0)
    h        = state.get("video_height", 0)
    return (f"**📸 Screenshot Generator**\n\nSource: `{TimeFormatter(int(duration * 1000))}` • `{h}p`\n\n"
            f"**Select the number of screenshots**\n\n"
            f"✶ Click the Button of your choice 👇 {SS_MIN} to {SS_MAX}")


async def run_screenshots(client: Client, status_msg: Message, state: dict, n: int):
    user_id  = state["user_id"]
    save_dir = state["save_dir"]
    src      = state["src_path"]
    duration = state["duration"]

    user_tasks[user_id]  = time.time()
    user_status[user_id] = {"id": int(user_tasks[user_id]), "user_id": user_id,
                            "filename": f"screenshots-{n}",
                            "duration_str": TimeFormatter(int(duration * 1000)),
                            "channel_name": "Screenshots", "url": "(local)", "progress": "0%"}
    try:
        try: await status_msg.edit_text(f"📸 Generating **{n}** screenshot{'s' if n != 1 else ''}...",
                                        reply_markup=None)
        except Exception: pass

        edge      = max(1.0, duration * 0.02)
        usable    = max(1.0, duration - 2 * edge)
        timestamps = ([duration / 2] if n == 1
                      else [edge + i * (usable / (n - 1)) for i in range(n)])

        produced: list = []
        for idx, ts in enumerate(timestamps, 1):
            if user_id in cancelled_users: break
            out = join(save_dir, f"shot_{idx:02d}.jpg")
            cmd = (f"ffmpeg -hide_banner -loglevel error -nostats -y "
                   f"-ss {ts:.2f} -i {shlex.quote(src)} -vframes 1 -q:v 2 {shlex.quote(out)}")
            rc, _o, err = await runcmd(cmd)
            if rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                produced.append((out, ts))
            else:
                LOG.warning(f"ss frame {idx} failed: {err.strip()[:200]}")
            pct = idx / n * 100
            if user_status.get(user_id): user_status[user_id]["progress"] = f"{pct:.0f}%"
            if idx % max(1, n // 6) == 0 or idx == n:
                try: await status_msg.edit_text(f"📸 Generating **{n}** screenshot{'s' if n != 1 else ''}...\n"
                                                f"`{idx}` / `{n}` done")
                except Exception: pass

        if user_id in cancelled_users:
            try: await status_msg.edit_text("Screenshot job cancelled.")
            except Exception: pass
            _safe_rmtree(save_dir)
            return
        if not produced:
            raise Exception("FFmpeg produced no images.")

        try: await status_msg.edit_text(f"📤 Uploading {len(produced)} image(s)...")
        except Exception: pass

        # Upload screenshots individually so one failed photo
        # cannot cause the remaining screenshots to be lost.
        first = True
        uploaded_count = 0

        for global_idx, (path, ts) in enumerate(produced, 1):
            if user_id in cancelled_users:
                break

            cap = (
                f"🎬 **{BRAND_TITLE}**\\n\\n"
                f"📸 `{len(produced)}` screenshot"
                f"{'s' if len(produced) != 1 else ''} • "
                f"video `{TimeFormatter(int(duration * 1000))}`"
                if first
                else f"`{global_idx:02d}` • `{TimeFormatter(int(ts * 1000))}`"
            )

            try:
                await client.send_photo(
                    chat_id=status_msg.chat.id,
                    photo=path,
                    caption=cap,
                )
                uploaded_count += 1
                first = False

                try:
                    await status_msg.edit_text(
                        f"📤 Uploading screenshots... "
                        f"`{uploaded_count}` / `{len(produced)}`"
                    )
                except Exception:
                    pass

            except Exception as upload_err:
                LOG.error(
                    f"Screenshot upload failed "
                    f"{global_idx}/{len(produced)}: {upload_err}"
                )

        if uploaded_count == 0:
            raise Exception("No screenshots could be uploaded.")

        try: await status_msg.edit_text(f"✅ Done — sent `{uploaded_count}` screenshot"
                                        f"{'s' if len(produced) != 1 else ''}.\n"
                                        f"Server copy auto-deletes in {_retention_label()}.")
        except Exception: pass
        if save_dir and os.path.exists(save_dir): schedule_retention_cleanup(save_dir)
    except Exception as e:
        LOG.error(f"Screenshot job failed uid={user_id}: {e}")
        try:
            err_text = str(e)
            if len(err_text) > 2500: err_text = "...[truncated]...\n" + err_text[-2500:]
            await status_msg.edit_text(f"❌ **Screenshot failed.**\n\n`{err_text}`")
        except Exception: pass
        if save_dir and os.path.exists(save_dir): _safe_rmtree(save_dir)
    finally:
        ss_jobs.pop(user_id, None)
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)

# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

MERGE_MAX_VIDEOS  = 20
MERGE_SESSION_TTL = 30 * 60


def _merge_session_status(sess: dict) -> str:
    parts     = sess["videos"]
    total_dur = sum(p["duration"] for p in parts)
    lines = [f"🧩 **Merge session active** — `{len(parts)}` / `{MERGE_MAX_VIDEOS}` videos collected.",
             f"Total so far: `{TimeFormatter(int(total_dur * 1000))}`", ""]
    for i, p in enumerate(parts, 1):
        lines.append(f"`{i:02d}.` `{TimeFormatter(int(p['duration'] * 1000))}` • "
                     f"`{p.get('height') or '?'}p` • {p['codec_v']}")
    lines += ["", "Send more videos in order, then `/merge_done` to combine.",
              "Use `/merge_cancel` to discard."]
    return "\n".join(lines)


def _all_streams_compatible(videos: list) -> bool:
    if not videos: return False
    base = videos[0]
    for v in videos[1:]:
        if (v["codec_v"] != base["codec_v"] or v["codec_a"] != base["codec_a"]
                or v["height"] != base["height"] or v["width"] != base["width"]):
            return False
    return True


async def run_merge(client: Client, message: Message, sess: dict):
    user_id   = _message_actor_id(message)
    save_dir  = sess["save_dir"]
    videos    = sess["videos"]
    out_path  = join(save_dir, f"merged_{int(time.time())}.mkv")
    total_dur = sum(v["duration"] for v in videos)

    user_tasks[user_id]  = time.time()
    user_status[user_id] = {"id": int(user_tasks[user_id]), "user_id": user_id,
                            "filename": os.path.basename(out_path),
                            "duration_str": TimeFormatter(int(total_dur * 1000)),
                            "channel_name": "Merge", "url": "(local)", "progress": "0%"}
    status = await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
        f"🧩 **Merging `{len(videos)}` videos** (`{TimeFormatter(int(total_dur * 1000))}` total)..."
    )
    try:
        compatible  = _all_streams_compatible(videos)
        used_method = None

        if compatible:
            list_path = join(save_dir, "concat_list.txt")
            with open(list_path, "w") as f:
                for v in videos:
                    safe = v["path"].replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")
            await status.edit_text("🧩 Streams are compatible — using **fast** concat (lossless)...")
            rc, _o, err = await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-f concat -safe 0 -i {shlex.quote(list_path)} -c copy {shlex.quote(out_path)}'
            )
            if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                used_method = "fast (stream copy)"
            else:
                LOG.warning(f"concat demuxer failed, falling back: {err.strip()[:300]}")

        if not used_method:
            await status.edit_text(f"🧩 Re-encoding `{len(videos)}` videos (slower but always works)...")
            inputs = []
            for v in videos: inputs += ["-i", v["path"]]
            n = len(videos)
            # Use minimum audio stream count across all videos for safe concat
            num_audio = min(
                (max(1, len(v.get("audio_streams", []))) for v in videos),
                default=1
            )
            seg_inputs = "".join(
                f"[{i}:v:0]" + "".join(f"[{i}:a:{j}]" for j in range(num_audio))
                for i in range(n)
            )
            out_labels = "[outv]" + "".join(f"[outa{j}]" for j in range(num_audio))
            filter_complex = (seg_inputs
                              + f"concat=n={n}:v=1:a={num_audio}"
                              + out_labels)
            map_args = ["-map", "[outv]"] + [item for j in range(num_audio) for item in ("-map", f"[outa{j}]")]
            args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
                    *inputs, "-filter_complex", filter_complex,
                    *map_args,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-c:a", "aac", "-b:a", "128k", out_path]
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            user_ffmpeg_pids[user_id] = proc.pid
            rc = await proc.wait()
            user_ffmpeg_pids.pop(user_id, None)
            err_out = (await proc.stderr.read()).decode(errors="ignore")
            if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                raise Exception(f"FFmpeg merge (re-encode) failed.\n{err_out[-1500:]}")
            used_method = "re-encode (h264/aac)"

        thumb    = join(save_dir, "thumb.jpg")
        thumb_at = max(1, min(int(total_dur / 2), int(total_dur) - 1))
        await runcmd(f'ffmpeg -hide_banner -loglevel error -nostats -y '
                     f'-ss {thumb_at} -i {shlex.quote(out_path)} '
                     f'-vframes 1 -q:v 2 {shlex.quote(thumb)}')
        thumb_path   = thumb if os.path.exists(thumb) else None
        out_size_mb  = os.path.getsize(out_path) / (1024 * 1024)
        retention_note = (f"_The video is automatically deleted from the server after "
                          f"{_retention_label()}._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"🧩 Merged `{len(videos)}` videos\nDuration: `{TimeFormatter(int(total_dur * 1000))}`\n"
                   f"Size: `{out_size_mb:.1f} MB`\nMethod: `{used_method}`\n"
                   f"{retention_note}")

        _dest = await _await_upload_choice(user_id, status, f"Merged: `{out_size_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, user_id, _dest, status, out_path, caption, total_dur,
                thumb_path=thumb_path, save_dir=save_dir,
            )
        if save_dir and os.path.exists(save_dir): schedule_retention_cleanup(save_dir)
    except Exception as e:
        LOG.error(f"Merge failed: {e}")
        try:
            if is_owner(user_id) or is_admin(user_id):
                err_text = str(e)
                if len(err_text) > 2500: err_text = "...[truncated]...\n" + err_text[-2500:]
                await status.edit_text(f"**Merge failed.**\n\n`{err_text}`")
            else:
                await status.edit_text("❌ **Merge failed.**\n\nCould not merge the videos. Please try again.")
        except Exception: pass
        if save_dir and os.path.exists(save_dir): _safe_rmtree(save_dir)
    finally:
        merge_sessions.pop(user_id, None)
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        user_ffmpeg_pids.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)

# ---------------------------------------------------------------------------
# Upload progress callback (shared by all upload calls)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /title — burn a text overlay onto a replied video
# ---------------------------------------------------------------------------

TITLE_POS_MAP = {
    "tl": ("↖️ Top-Left",     "x=10:y=10"),
    "tc": ("⬆️ Top-Center",   "x=(w-tw)/2:y=10"),
    "tr": ("↗️ Top-Right",    "x=w-tw-10:y=10"),
    "cc": ("⊙ Center",        "x=(w-tw)/2:y=(h-th)/2"),
    "bl": ("↙️ Bottom-Left",  "x=10:y=h-th-10"),
    "bc": ("⬇️ Bottom-Center","x=(w-tw)/2:y=h-th-10"),
    "br": ("↘️ Bottom-Right", "x=w-tw-10:y=h-th-10"),
}

# Videos >= this many seconds get "no title in last 3 minutes" treatment.
_TITLE_LONG_VIDEO_SEC  = 46 * 60   # 2760 s
_TITLE_FADE_BEFORE_SEC = 3  * 60   # 180 s


def _title_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Top-Left",     callback_data=f"ti:{uid}:pos:tl"),
         InlineKeyboardButton("⬆️ Top-Center",   callback_data=f"ti:{uid}:pos:tc"),
         InlineKeyboardButton("↗️ Top-Right",    callback_data=f"ti:{uid}:pos:tr")],
        [InlineKeyboardButton("⊙ Center",        callback_data=f"ti:{uid}:pos:cc")],
        [InlineKeyboardButton("↙️ Bottom-Left",  callback_data=f"ti:{uid}:pos:bl"),
         InlineKeyboardButton("⬇️ Bottom-Center",callback_data=f"ti:{uid}:pos:bc"),
         InlineKeyboardButton("↘️ Bottom-Right", callback_data=f"ti:{uid}:pos:br")],
        [InlineKeyboardButton("❌ Cancel",        callback_data=f"ti:{uid}:cancel")],
    ])


def _title_menu_text(state: dict) -> str:
    dur   = state["duration"]
    h     = state.get("video_height", 0)
    text  = state["title_text"]
    note  = ""
    if dur >= _TITLE_LONG_VIDEO_SEC:
        note = (f"\n\n⚠️ Video is `{TimeFormatter(int(dur*1000))}` long — "
                f"title will **not** appear in the last **3 minutes**.")
    return (f"**🔤 Title Overlay**\n\n"
            f"Source: `{TimeFormatter(int(dur*1000))}` • `{h}p`\n"
            f"Text: `{text[:60]}{'…' if len(text)>60 else ''}`{note}\n\n"
            f"**Choose text position:**")


async def run_title(client: Client, status_msg: Message, state: dict, pos_key: str):
    user_id  = state["user_id"]
    save_dir = state["save_dir"]
    src      = state["src_path"]
    duration = state["duration"]
    raw_text = state["title_text"]
    out_path = join(save_dir, f"titled_{int(time.time())}.mkv")

    user_tasks[user_id]  = time.time()
    user_status[user_id] = {
        "id":            int(user_tasks[user_id]),
        "user_id":       user_id,
        "filename":      os.path.basename(out_path),
        "duration_str":  TimeFormatter(int(duration * 1000)),
        "channel_name":  "Title",
        "url":           "(local)",
        "progress":      "0%",
    }

    pos_label, xy = TITLE_POS_MAP[pos_key]

    # Escape text for FFmpeg drawtext (backslash → \\, colon → \:, quote → \')
    safe_text = (raw_text
                 .replace("\\", "\\\\")
                 .replace(":",   "\\:")
                 .replace("'",   "\\'"))

    # For long videos: title disappears 3 minutes before the end
    if duration >= _TITLE_LONG_VIDEO_SEC:
        end_ts     = max(0.0, duration - _TITLE_FADE_BEFORE_SEC)
        enable_str = f":enable='lt(t,{end_ts:.1f})'"
    else:
        enable_str = ""

    vf = (f"drawtext=text='{safe_text}'"
          f":fontsize=36:fontcolor=white"
          f":box=1:boxcolor=black@0.45:boxborderw=5"
          f":{xy}{enable_str}")

    try:
        try:
            await status_msg.edit_text(
                f"🔤 Burning title overlay…\n\n"
                f"Position: {pos_label}\n"
                f"Text: `{raw_text[:60]}`\n"
                f"Re-encoding video — this may take a while.",
                reply_markup=None,
            )
        except Exception:
            pass

        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1", "-y",
            "-i", src,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        user_ffmpeg_pids[user_id] = proc.pid

        progress_state = {"out_time_us": 0, "speed": 1.0}

        async def _read_prog():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                txt = line.decode(errors="ignore").strip()
                if "=" not in txt:
                    continue
                k, v = txt.split("=", 1)
                if k == "out_time_us":
                    try: progress_state["out_time_us"] = int(v)
                    except ValueError: pass
                elif k == "speed" and v not in ("N/A", ""):
                    try: progress_state["speed"] = float(v.rstrip("x"))
                    except ValueError: pass

        async def _render_prog():
            last = ""
            while proc.returncode is None:
                if user_id in cancelled_users:
                    return
                done_sec  = progress_state["out_time_us"] / 1_000_000
                pct       = min(100.0, done_sec / max(1, duration) * 100)
                bar_len   = 20
                filled    = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
                bar       = "●" * filled + "○" * (bar_len - filled)
                spd       = progress_state["speed"]
                remaining = max(0.0, (duration - done_sec) / max(0.05, spd))
                txt       = (f"🔤 **Burning title…**\n\n"
                             f"`{bar}` `{pct:5.1f}%`\n"
                             f"⚡ Speed: `{spd:.2f}x`\n"
                             f"⏳ ETA: `{TimeFormatter(int(remaining * 1000))}`")
                if txt != last:
                    try:
                        await status_msg.edit_text(txt)
                        last = txt
                        if user_status.get(user_id):
                            user_status[user_id]["progress"] = f"{pct:.1f}%"
                    except Exception:
                        pass
                await asyncio.sleep(10)

        prog_reader   = asyncio.create_task(_read_prog())
        prog_renderer = asyncio.create_task(_render_prog())
        progress_tasks[user_id] = prog_renderer

        rc = await proc.wait()
        prog_reader.cancel()
        prog_renderer.cancel()
        user_ffmpeg_pids.pop(user_id, None)

        if user_id in cancelled_users:
            try: await status_msg.edit_text("Title overlay cancelled.")
            except Exception: pass
            _safe_rmtree(save_dir)
            return

        if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            err = (await proc.stderr.read()).decode(errors="ignore")
            tail = err[-1500:] if len(err) > 1500 else err
            raise Exception(f"FFmpeg exit {rc}\n{tail}")

        # Thumbnail
        thumb    = join(save_dir, "thumb.jpg")
        thumb_at = max(1, min(int(duration / 2), int(duration) - 1))
        await runcmd(f'ffmpeg -hide_banner -loglevel error -nostats -y '
                     f'-ss {thumb_at} -i {shlex.quote(out_path)} '
                     f'-vframes 1 -q:v 2 {shlex.quote(thumb)}')
        thumb_path  = thumb if os.path.exists(thumb) else None
        out_size_mb = os.path.getsize(out_path) / (1024 * 1024)

        long_note = (f"\n_Title disappears in the last 3 min (video > 46 min)._"
                     if duration >= _TITLE_LONG_VIDEO_SEC else "")
        retention_note = (f"_Auto-deleted from server after 2 hours._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"🔤 Title: `{raw_text[:80]}`\n"
                   f"📌 Position: `{pos_label}`\n"
                   f"⏱ Duration: `{TimeFormatter(int(duration * 1000))}`\n"
                   f"💾 Size: `{out_size_mb:.1f} MB`\n"
                   f"{long_note}\n\n"
                   f"{retention_note}")

        upload_start = time.time()
        await split_and_send_video(
            status_msg, out_path, caption, int(duration),
            thumb_path=thumb_path,
            status_msg=status_msg,
            progress=progress_for_pyrogram,
            progress_args=(status_msg, upload_start, status_msg, save_dir, False),
            _uid=user_id, _chat_id=status_msg.chat.id,
        )
        asyncio.create_task(upload_and_notify(
            client, status_msg.chat.id, out_path, os.path.basename(out_path)
        ))
        try:
            await status_msg.edit_text(
                f"✅ Title overlay done — uploaded `{out_size_mb:.1f} MB`.\n"
                f"Server copy auto-deletes in {_retention_label()}."
            )
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"run_title failed uid={user_id}: {e}")
        err_text = str(e)
        if len(err_text) > 2500:
            err_text = "...[truncated]...\n" + err_text[-2500:]
        try: await status_msg.edit_text(f"**Title overlay failed.**\n\n`{err_text}`")
        except Exception: pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        title_jobs.pop(user_id, None)
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        user_ffmpeg_pids.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)


_progress_last_edit: dict = {}   # msg_id -> last edit timestamp
_PROGRESS_THROTTLE_SEC = 8      # minimum seconds between progress edits

# Per-message edit queues — ensures edits land in order, only latest pending wins.
# Key: id(msg)  Value: asyncio.Queue of (text | None) — None is a sentinel to stop the worker.
_edit_queues: dict = {}


def _get_edit_queue(msg, edit_lock=None) -> "asyncio.Queue[str | None]":
    """Return (or create) the single-writer edit queue for this status message."""
    key = id(msg)
    if key not in _edit_queues:
        q: asyncio.Queue = asyncio.Queue()
        _edit_queues[key] = q

        async def _worker():
            latest = None
            while True:
                item = await q.get()
                if item is None:           # sentinel: done
                    _edit_queues.pop(key, None)
                    break
                # Drain all queued items — keep only the latest one
                while not q.empty():
                    item = q.get_nowait()
                    if item is None:
                        _edit_queues.pop(key, None)
                        return
                latest = item
                try:
                    await _edit_message_content(msg, latest, edit_lock=edit_lock)
                except Exception:
                    pass

        asyncio.create_task(_worker())
    return _edit_queues[key]


def _fire_edit(msg, text: str, edit_lock=None) -> None:
    """Enqueue a message edit; the per-message worker serializes & deduplicates them."""
    _get_edit_queue(msg, edit_lock=edit_lock).put_nowait(text)


def _close_edit_queue(msg) -> None:
    """Send sentinel to stop the worker after all queued edits finish."""
    q = _edit_queues.get(id(msg))
    if q:
        q.put_nowait(None)


_download_progress_last_edit: dict = {}


async def progress_for_telegram_download(
    current, total, message, start, filename="source"
):
    """Render progress for a parallel Telegram media download."""
    if not total:
        return
    now = time.time()
    key = id(message)
    if current != total and now - _download_progress_last_edit.get(key, 0) < 8:
        return
    _download_progress_last_edit[key] = now
    elapsed = max(now - start, 1.0)
    speed = current / elapsed
    eta = (total - current) / speed if speed > 0 else 0
    if current == total:
        _close_edit_queue(message)
        try:
            await message.edit_text(
                f"✅ **Source download complete.**\n\n"
                f"📁 `{filename}`\n"
                f"⚡ Speed: `{speed / (1024 * 1024):.1f} MB/s`\n"
                "Preparing watermark/audio processing…"
            )
        except Exception:
            pass
        _download_progress_last_edit.pop(key, None)
        return
    percent = max(0.0, min(100.0, current * 100 / total))
    filled = min(20, int(percent / 5))

    progress_emojis = {
        0: "🥵",
        5: "🥵",
        10: "🔥",
        15: "😵‍💫",
        20: "❤️",
        25: "😱",
        30: "😇",
        35: "😡",
        40: "🤯",
        45: "😈",
        50: "🔥",
        55: "👑",
        60: "😰",
        65: "🙄",
        70: "😙",
        75: "👊",
        80: "😆",
        85: "😵",
        90: "😵",
        95: "🤐",
        100: "💯",
    }

    level = min(100, int(percent // 5) * 5)
    emoji = progress_emojis.get(level, "🥵")
    bar = "▓" * filled + "░" * (20 - filled)

    try:
        await message.edit_text(
            f"⬇️ **Downloading from Telegram…**\n\n"
            f"{emoji} `{bar}` `{percent:5.1f}%`\n"
            f"💾 `{current / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB`\n"
            f"⚡ Speed: `{speed / (1024 * 1024):.1f} MB/s`\n"
            f"⏳ ETA: `{TimeFormatter(int(eta * 1000))}`"
        )
    except Exception:
        pass


async def progress_for_pyrogram(current, total, message, start, msg,
                                 save_dir=None, was_cancelled=False, edit_lock=None):
    if not total:
        return
    now      = time.time()
    msg_key  = id(msg)

    if current == total:
        # Completion — close the in-progress queue and await the final card directly
        # so the caller is sure the success message has landed before it returns.
        _close_edit_queue(msg)
        label    = _retention_label()
        filename = os.path.basename(save_dir) if save_dir else "File"
        size_str = f"{total/(1024**3):.1f} GB" if total >= 1024**3 else f"{total/(1024**2):.1f} MB"
        task_id  = _upload_task_id(msg_key)
        elapsed_sec = max(now - start, 1.0)
        average_speed = total / elapsed_sec
        final    = (
            f"✅ Uploading complete\n\n"
            f"📁 File Name:\n{filename}\n\n"
            f"📦 Size:\n{size_str}\n\n"
            f"⚡ Average speed:\n{average_speed / (1024 * 1024):.2f} MB/s\n\n"
            f"📤 Destination:\nTelegram\n\n"
            f"🆔 Task:\n{task_id}\n\n"
        )
        if was_cancelled:
            final += "⚠️ Partial recording sent.\n"
        final += f"Server copy auto-deletes in {label}."
        try:
            await _edit_message_content(msg, final, edit_lock=edit_lock)
        except Exception:
            pass
        _progress_last_edit.pop(msg_key, None)
        return

    # Throttle: only enqueue an update every _PROGRESS_THROTTLE_SEC seconds
    last = _progress_last_edit.get(msg_key, 0)
    if now - last < _PROGRESS_THROTTLE_SEC:
        return
    _progress_last_edit[msg_key] = now

    diff    = now - start or 1
    speed   = current / diff if diff > 0 else 0
    eta_sec = int((total - current) / speed) if speed > 0 else 0
    task_id = _upload_task_id(msg_key)
    title   = "Uploading partial recording to Telegram" if was_cancelled else "Uploading to Telegram"
    text    = _fmt_upload_progress_box(title, current, total, speed, eta_sec, task_id)
    _fire_edit(msg, text, edit_lock=edit_lock)   # non-blocking; worker serialises & deduplicates


# ---------------------------------------------------------------------------
# 2 GB auto-split helper
# ---------------------------------------------------------------------------

TG_MAX_BYTES = 1_980_000_000  # 1.98 GB — safe margin under Telegram's 2 GB hard limit


# ---------------------------------------------------------------------------
# Failed-upload persistence (so /resend can retry after a bot restart)
# ---------------------------------------------------------------------------

def _save_failed_upload(uid: int, chat_id: int, file_path: str,
                        caption: str, duration: int, thumb_path) -> None:
    try:
        with open(join(FAILED_UPLOADS_DIR, f"{uid}.json"), "w") as _f:
            json.dump({"file_path": file_path, "caption": caption,
                       "duration": duration, "thumb_path": thumb_path,
                       "chat_id": chat_id, "ts": time.time()}, _f)
    except Exception as _e:
        LOG.warning("_save_failed_upload: %s", _e)


def _load_failed_upload(uid: int) -> dict | None:
    try:
        with open(join(FAILED_UPLOADS_DIR, f"{uid}.json"), "r") as _f:
            return json.load(_f)
    except Exception:
        return None


def _clear_failed_upload(uid: int) -> None:
    try:
        os.remove(join(FAILED_UPLOADS_DIR, f"{uid}.json"))
    except Exception:
        pass


_NETWORK_ERR_KEYWORDS = (
    "timeout", "timed out", "connection", "network", "reset", "broken",
    "eof", "read error", "502", "503", "peer", "ssl", "unreachable",
)

def _is_network_error(exc: Exception) -> bool:
    return any(kw in str(exc).lower() for kw in _NETWORK_ERR_KEYWORDS)


async def split_and_send_video(
    send_target,
    video_path: str,
    caption: str,
    duration: int,
    thumb_path=None,
    status_msg=None,
    progress=None,
    progress_args=None,
    _uid: int = 0,
    _chat_id: int = 0,
):
    """
    Send a video to Telegram with automatic retry (up to 3 attempts) and
    FloodWait handling. Falls back to reply_document if reply_video keeps
    failing. On final failure the upload details are persisted to disk so the
    user can run /resend after a bot restart.

    If the file exceeds TG_MAX_BYTES (1.95 GB) it is automatically split into
    equal-duration parts using FFmpeg (-c copy, no re-encoding) and each part
    is uploaded separately with '📂 Part X / Y' appended to the caption.
    """
    _MAX_ATTEMPTS = 3
    status_edit_lock = (
        progress_args[-1]
        if progress_args and isinstance(progress_args[-1], asyncio.Lock)
        else None
    )
    size = os.path.getsize(video_path)

    if size <= TG_MAX_BYTES:
        last_err: Exception | None = None

        for attempt in range(_MAX_ATTEMPTS):
            try:
                await send_target.reply_video(
                    video=video_path,
                    caption=caption,
                    duration=duration or None,
                    thumb=thumb_path,
                    progress=progress,
                    progress_args=progress_args,
                    supports_streaming=True,
                )
                return
            except FloodWait as fw:
                LOG.warning("FloodWait %ds (attempt %d/%d)", fw.value, attempt + 1, _MAX_ATTEMPTS)
                await asyncio.sleep(fw.value + 3)
                last_err = fw
                continue
            except Exception as _err:
                last_err = _err
                if _is_network_error(_err) and attempt < _MAX_ATTEMPTS - 1:
                    wait = 20 * (attempt + 1)
                    LOG.warning("Upload attempt %d/%d failed (network) — retry in %ds: %s",
                                attempt + 1, _MAX_ATTEMPTS, wait, _err)
                    if status_msg:
                        try:
                            await _edit_message_content(
                                status_msg,
                                f"⚠️ Upload interrupted (attempt {attempt + 1}/{_MAX_ATTEMPTS})."
                                f" Retrying in {wait}s…",
                                edit_lock=status_edit_lock,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(wait)
                    continue
                # Non-network error or last attempt — try as document fallback
                LOG.warning("reply_video failed (attempt %d), trying as document: %s",
                            attempt + 1, _err)
                try:
                    await send_target.reply_document(
                        document=video_path,
                        caption=caption,
                        thumb=thumb_path,
                        progress=progress,
                        progress_args=progress_args,
                    )
                    return
                except Exception as _doc_err:
                    LOG.error("reply_document fallback also failed: %s", _doc_err)
                    last_err = _doc_err
                    break

        # All attempts exhausted — persist so user can /resend
        if _uid:
            _save_failed_upload(_uid, _chat_id, video_path, caption, duration, thumb_path)
            LOG.error("Upload failed after %d attempts — saved for /resend uid=%d", _MAX_ATTEMPTS, _uid)
        if last_err:
            raise last_err
        return

    num_parts  = math.ceil(size / TG_MAX_BYTES)
    size_gb    = size / (1024 ** 3)
    LOG.info("Auto-split: %.2f GB → %d parts  file=%s", size_gb, num_parts, video_path)

    if status_msg:
        try:
            await _edit_message_content(
                status_msg,
                f"📦 File is {size_gb:.2f} GB — exceeds Telegram's 2 GB limit.\n"
                f"Splitting into {num_parts} parts… please wait.",
                edit_lock=status_edit_lock,
            )
        except Exception:
            pass

    base_dir   = os.path.dirname(video_path)
    base_name  = os.path.splitext(os.path.basename(video_path))[0]
    ext        = os.path.splitext(video_path)[1] or ".mkv"
    dur_int    = int(duration) if duration else 0
    part_dur   = max(1, dur_int // num_parts) if dur_int else None
    parts_sent = 0

    for i in range(num_parts):
        part_path = join(base_dir, f"{base_name}_part{i + 1:02d}{ext}")

        if part_dur:
            ss      = i * part_dur
            to      = (i + 1) * part_dur if i < num_parts - 1 else dur_int
            part_sec = to - ss
            cmd = (
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-ss {ss} -to {to} '
                f'-i {shlex.quote(video_path)} '
                f'-map 0 -c copy {shlex.quote(part_path)}'
            )
        else:
            part_sec = None
            cmd = (
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-i {shlex.quote(video_path)} '
                f'-map 0 -c copy -f segment -segment_size {TG_MAX_BYTES} '
                f'-reset_timestamps 1 '
                f'{shlex.quote(part_path)}'
            )

        rc, _out, err = await runcmd(cmd)
        if rc != 0 or not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
            LOG.error("Split part %d/%d failed: %s", i + 1, num_parts, err[-500:])
            continue

        part_caption = caption + f"\n\n📂 **Part {i + 1}**"
        try:
            await send_target.reply_video(
                video=part_path,
                caption=part_caption,
                duration=part_sec,
                thumb=thumb_path if i == 0 else None,
                supports_streaming=True,
            )
            parts_sent += 1
        except Exception as split_err:
            LOG.error("Send part %d/%d failed: %s", i + 1, num_parts, split_err)
        finally:
            try:
                os.remove(part_path)
            except Exception:
                pass

    LOG.info("Auto-split done: %d/%d parts sent", parts_sent, num_parts)


# ===========================================================================
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
        raw = e.read()
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            body = {}
        err = body.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return None
        desc = body.get("error_description", "")
        if not err:
            err = f"HTTP {e.code}"
        raise Exception(f"OAuth2 error: {err}" + (f" — {desc}" if desc else ""))


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
                sent = await client.send_message(user_id, f"❌ Google Drive auth failed: `{e}`")
                _schedule_message_delete(sent, AUTO_DELETE_5MIN)
            except Exception:
                pass
            return
        if tok:
            _save_token(user_id, tok)
            LOG.info(f"GDrive OAuth token saved for uid={user_id}")
            try:
                sent = await client.send_message(
                    user_id,
                    "✅ **Google Drive Connected!**\n\n"
                    "Ab aapki recordings automatically **aapki Google Drive** par upload hongi.\n\n"
                    "Disconnect karne ke liye: /googledrive disconnect\n"
                    "Status dekhne ke liye: /googledrive status",
                )
                _schedule_message_delete(sent, AUTO_DELETE_5MIN)
            except Exception:
                pass
            return
    try:
        sent = await client.send_message(
            user_id,
            "⏰ **Google Drive auth timeout.**\n\n"
            "Code expire ho gaya. Fir se try karein: /googledrive"
        )
        _schedule_message_delete(sent, AUTO_DELETE_5MIN)
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

def _upload_sync(file_path: str, filename: str, folder_id: Optional[str],
                 user_id: Optional[int]) -> str:
    from googleapiclient.http import MediaFileUpload

    if user_id and is_user_connected(user_id):
        service = _build_user_service(user_id)
        meta    = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]
    else:
        service = _build_sa_service()
        meta    = {"name": filename, "parents": [folder_id or GDRIVE_FOLDER_ID]}

    mime_type = "video/x-matroska" if filename.endswith(".mkv") else "video/mp4"
    media     = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
    request = service.files().create(
        body=meta, media_body=media, fields="id,webViewLink"
    )

    response = None
    while response is None:
        status, response = request.next_chunk()

        if status is not None:
            try:
                percent = status.progress() * 100
                LOG.info(
                    f"GDrive upload progress: "
                    f"{filename} {percent:.1f}%"
                )
            except Exception:
                pass

    link = response.get("webViewLink") or (
        f"https://drive.google.com/file/d/{response['id']}/view"
    )
    LOG.info(f"GDrive upload done: {filename} → {link}")
    return link


async def upload_and_notify(client, chat_id: int, file_path: str, filename: str, status_msg=None):
    """Upload to Drive (user tokens preferred, SA as fallback) and send the link."""
    user_connected = is_user_connected(chat_id)
    if not user_connected and not _sa_enabled():
        return
    task_id = _upload_task_id(file_path)
    try:
        total = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        progress_text = _fmt_upload_progress_box(
            "Uploading to Google Drive", 0, total or 1, 0, 0, task_id, compact=True
        )
        target = status_msg or await client.send_message(chat_id, progress_text)
        try:
            await target.edit_text(progress_text)
        except Exception:
            pass

        folder_id = None if user_connected else GDRIVE_FOLDER_ID
        link = await asyncio.to_thread(
            _upload_sync, file_path, filename, folder_id, chat_id
        )
        short_link = await _shrink2(link)
        link_label = (
            "ShrinkMe Link"
            if short_link != link
            else "Drive Link Ready (unshortened)"
        )
        final = (
            f"✅ Uploaded to Google Drive\n"
            f"🔗 [{link_label}]({short_link})"
        )
        try:
            await target.edit_text(final, disable_web_page_preview=True)
        except Exception:
            await client.send_message(chat_id, final, disable_web_page_preview=True)
    except Exception as e:
        LOG.error(f"GDrive upload failed for {filename}: {e}")
        try:
            await client.send_message(chat_id, f"⚠️ Google Drive upload failed: `{e}`")
        except Exception:
            pass


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
        drive_link = None
        try:
            await status_msg.edit_text("\n".join(log_lines))
        except Exception:
            pass

        if drive_available:
            try:
                folder_id = None if user_connected else GDRIVE_FOLDER_ID
                drive_link = await asyncio.to_thread(
                    _upload_sync, out_path, filename, folder_id, status_msg.chat.id
                )
                log_lines.append("✅ Drive Upload Complete")
            except Exception as e:
                LOG.error(f"GDrive upload failed for {filename}: {e}")
                log_lines.append("⚠️ Drive Upload Failed")
        else:
            log_lines.append("⚠️ Drive Upload Skipped (not connected)")

        if drive_link:
            short_drive_link = await _shrink2(drive_link)
            link_label = (
                "ShrinkMe Drive link"
                if short_drive_link != drive_link
                else "Drive link (unshortened)"
            )
            log_lines.append(f"🔗 [{link_label}]({short_drive_link})")

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
# ShrinkMe.io URL shortener (sync wrapped in asyncio.to_thread)
# ---------------------------------------------------------------------------

def _shrink2_sync(api_url: str):
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            print("SHORTENER HTTP:", resp.status)
            print("SHORTENER RESPONSE:", raw)

            data = json.loads(raw)

            if data.get("status") == "success" and data.get("shortenedUrl"):
                return data["shortenedUrl"]

            print("SHORTENER PARSE FAILED:", data)

    except Exception as e:
        print("SHORTENER ERROR:", repr(e))

    return None


SHORTENER_API_LINK = "https://shrinkme.io/api"

async def _shrink2(long_url: str) -> str:
    """Shorten using ShrinkMe API."""
    key = SHRINKME_API_KEY.strip()
    api_url = SHORTENER_API_LINK.strip()

    if not key or not api_url:
        return long_url

    encoded = urllib.parse.quote(long_url, safe=":/?&=%")
    separator = "&" if "?" in api_url else "?"
    request_url = f"{api_url}{separator}api={key}&url={encoded}"

    try:
        short = await asyncio.to_thread(_shrink2_sync, request_url)
        if short:
            return short
    except Exception as e:
        print("SHORTENER CALL ERROR:", repr(e))

    return long_url


# ---------------------------------------------------------------------------
# Shared helpers for text
# ---------------------------------------------------------------------------

HELP_TEXT = f"""
**{BRAND_TITLE} — Help & Commands**

━━━━━━━━━━━━━━━━━━━━━━━
🎬 **FFmpeg Tools**
━━━━━━━━━━━━━━━━━━━━━━━

❌ Free Users: Not Allowed
✅ Premium / Admin / Owner: Allowed

• `/ffmpeg` — Direct FFmpeg command.
• `/ffprobe` — Direct FFprobe command.

━━━━━━━━━━━━━━━━━━━━━━━
🎞️ **Video Tools**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 **AUTH Protected**

• `/compress` — Reply to a video to compress it.
• `/screenshot` or `/ss` — Reply to a video to extract screenshots.
• `/trim HH:MM:SS HH:MM:SS` — Trim a clip.

Example: `/trim 00:01:00 00:03:30`

• `/merge` — Start multi-video merge session.
• `/merge_done` — Finish merge session.
• `/watermark` — Reply to a video → Burn watermark (last 2 min, bottom-right).
• `/audiotrack` — Reply to a video → Lock audio track metadata instantly (No re-encode).

━━━━━━━━━━━━━━━━━━━━━━━
📹 **Recording**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 **AUTH Protected**

• `/rec HH:MM:SS`
Record HLS/M3U8/DASH stream with wizard.

• `/drec HH:MM:SS [filename] [flags]`
Direct record without wizard.

• `/reclink HH:MM:SS`
Auto extract stream from any webpage.

• Send any `.m3u8` URL directly for quick recording.

**Optional Flags**

`-aes`       AES-128 decryption key (HLS)
`-cookie`    Cookie header
`-ua`        User-Agent
`-referer`   Referer header
`-origin`    Origin header
`-license`   ClearKey DRM license URL (MPD/DASH)

CloudPlay multi-line format is also supported.

━━━━━━━━━━━━━━━━━━━━━━━
📥 **OTT Download**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 **AUTH Protected**

• `/download [filename]`

Supported OTT:
• Hotstar
• JioCinema
• ZEE5
• SonyLIV

━━━━━━━━━━━━━━━━━━━━━━━
☁️ **Google Drive**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 **AUTH Protected**

• `/gdrive` or `/googledrive`
Connect your Google Drive.

• `/drivelogout`
Disconnect your Drive account.

━━━━━━━━━━━━━━━━━━━━━━━
🍪 **Cookies (OTT Login)**
━━━━━━━━━━━━━━━━━━━━━━━

🔒 **AUTH Protected**

• `/set_cookies`
Upload cookies.txt (Netscape format).

• `/cookies_status`
Show stored cookies.

• `/del_cookies`
Delete stored cookies.

━━━━━━━━━━━━━━━━━━━━━━━
📊 **Status & Control**
━━━━━━━━━━━━━━━━━━━━━━━

❌ Free Users: Not Allowed
✅ Verify / Premium / Admin / Owner: Allowed

• `/status` or `/statusme`
Show current recording/job status.

• `/cancel` or `/cancelme`
Cancel active recording/job.

━━━━━━━━━━━━━━━━━━━━━━━
👥 **General Commands**
━━━━━━━━━━━━━━━━━━━━━━━

✅ Free / Verify / Premium / Admin / Owner: Allowed

• `/start` — Welcome message.
• `/help` — Show help menu.
• `/plan` — Subscription plans.
• `/contact` — Support contact.
• `/channel` — Browse available channels.
• `/rec <name|url> HH:MM:SS` — Record via wizard.
• `/drec <name|url> HH:MM:SS` — Record instantly, no wizard.
• `/search` — Search channels.
• `/verify` — Request verification.
• `/limit` — Check recording quota.

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


def _make_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help",       callback_data="show_help"),
         InlineKeyboardButton("💎 Plans",      callback_data="show_plans")],
        [InlineKeyboardButton("📡 Channels",   callback_data="show_channels"),
         InlineKeyboardButton("📥 Download",   callback_data="show_download")],
        [InlineKeyboardButton("✅ Get Verified", callback_data="show_verify")],
    ])


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@app.on_message(filters.command("start") & AUTH)
async def start_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    name = (
        message.from_user.first_name
        if message.from_user and message.from_user.first_name
        else "Anonymous Admin" if _is_official_anonymous_admin(message)
        else "User"
    )

    # ── Deep-link: /start vfy_{uid}  (user came via shortxlinks.in ad click) ──
    param = message.command[1] if len(message.command) > 1 else ""
    if param.startswith("vfy_"):
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
            # Auto-add to verified.json so other commands work
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
        else:
            return await message.reply_text(
                f"⚠️ **Verification failed:**\n{reward_msg}\n\nUse /limit to check status."
            )

    # ── Normal /start ─────────────────────────────────────────────────────────
    await message.reply_text(
        f"👋 Hello, **{name}**!\n\n"
        f"Welcome to **{BRAND_TITLE}**.\n\n"
        f"I can record HLS / M3U8 streams and download from OTT platforms.\n\n"
        f"📧 Support: @{SUPPORT_USERNAME}\n\n"
        f"Use the buttons below to get started:",
        reply_markup=_make_start_kb(),
    )


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /myid — universal debug command, bypasses ALL gates (group=-2)
# Anyone can use this to find their Telegram user ID
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["myid", "id", "whoami"]), group=-2)
async def myid_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    name = (message.from_user.first_name or "User") if message.from_user else "Unknown"
    role = "👑 Owner" if is_owner(uid) else ("🛡 Admin" if is_admin(uid) else ("✅ Auth User" if uid in (AUTH_USERS or []) else "👤 Regular User"))
    await message.reply_text(
        f"👤 **{name}**\n"
        f"🆔 Your Telegram ID: `{uid}`\n"
        f"🔑 Role: {role}\n\n"
        f"📋 OWNER_IDS loaded: `{len(OWNER_IDS)}` ID(s)\n"
        f"📋 AUTH_USERS loaded: `{len(AUTH_USERS)}` ID(s)"
    )
    message.stop_propagation()


# ---------------------------------------------------------------------------
# Group membership gate — runs BEFORE all other handlers (group=-1)
# ---------------------------------------------------------------------------

@app.on_chat_member_updated()
async def _official_group_membership_guard(client: Client, update):
    """Leave every group except the single official group immediately after join."""
    chat = getattr(update, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None or chat_id == OFFICIAL_ANONYMOUS_GROUP_ID:
        return

    new_member = getattr(update, "new_chat_member", None)
    member_user = getattr(new_member, "user", None)
    me = getattr(client, "me", None)
    me_id = getattr(me, "id", None)
    if me_id is None or getattr(member_user, "id", None) != me_id:
        return

    try:
        await client.leave_chat(chat_id)
        LOG.info("Automatically left unauthorized group %s", chat_id)
    except Exception as e:
        LOG.warning("Automatic leave failed for group %s: %s", chat_id, e)


@app.on_message(filters.group, group=-1)
async def _group_chat_gate(client: Client, message: Message):
    """Only the official group is allowed; all other groups are left automatically."""
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id != OFFICIAL_ANONYMOUS_GROUP_ID:
        try:
            await client.leave_chat(chat_id)
            LOG.info("Left unauthorized group %s", chat_id)
        except Exception as e:
            LOG.warning("Could not leave unauthorized group %s: %s", chat_id, e)
        message.stop_propagation()
        return

    # Official group is allowed. Anonymous Admin uses the group ID only as
    # an internal authorization identity, never as a human Telegram user.
    return


@app.on_message(filters.private, group=-1)
async def _group_gate(client: Client, message: Message):
    uid = _message_actor_id(message)

    # Owner / Admin — full DM access
    if is_owner(uid) or is_admin(uid):
        return

    # Always let these pass regardless of role (informational / auth commands)
    text = (message.text or message.caption or "").strip().lower()
    _DM_ALLOWED_PREFIXES = (
        "/start", "/help",                          # welcome & help
        "/verify", "/vfy",                          # OTP verification
        "/plan", "/limit",                          # subscription info
        "/statusme", "/cancelme",                   # job status
        "/gdrive", "/drivelogout",                  # Drive OAuth flow
        "/set_cookies", "/cookies_status", "/del_cookies",  # cookies mgmt
    )
    if any(text.startswith(p) for p in _DM_ALLOWED_PREFIXES):
        return

    # All other users (including AUTH_USERS) → DM blocked, redirect to Group
    join_link = GROUP_INVITE_LINK or "https://t.me/+ww77CDQwoigzYjk1"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 Group Mein Jao", url=join_link)
    ]])
    await message.reply_text(
        "🚫 **DM mein commands allowed nahi hain.**\n\n"
        "Yeh bot sirf **Group** mein use hota hai.\n"
        "Neeche button se group join karein aur wahan commands chalayein.",
        reply_markup=kb,
    )
    message.stop_propagation()


# ---------------------------------------------------------------------------
# /Group <ID> [update] — owner sets/updates the GROUP_CHAT_ID at runtime
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["Group", "group", "setgroup"]))
async def set_group_cmd(client: Client, message: Message):
    """Owner-only: /Group <chat_id>  — set the bot's group at runtime."""
    global GROUP_CHAT_ID, GROUP_INVITE_LINK
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use this command.")

    args = message.command
    # /Group  (no args) — show current
    if len(args) < 2:
        gid  = GROUP_CHAT_ID or "not set"
        link = GROUP_INVITE_LINK or "not set"
        return await message.reply_text(
            f"📌 **Current Group Config**\n\n"
            f"🆔 Group ID : `{gid}`\n"
            f"🔗 Invite link: {link}\n\n"
            f"**To set:** `/Group -100123456789`\n"
            f"**To set with link:** `/Group -100123456789 https://t.me/+invite`\n"
            f"**To clear:** `/Group 0`"
        )

    # /Group <id> [invite_link]
    try:
        new_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid group ID. Use a numeric ID like `-100123456789`.")

    new_link = GROUP_INVITE_LINK  # keep old link unless provided
    if len(args) >= 3:
        new_link = args[2]

    GROUP_CHAT_ID   = new_id
    GROUP_INVITE_LINK = new_link
    _save_group_config()

    if new_id == 0:
        return await message.reply_text("✅ Group gate **disabled** — bot responds to everyone.")

    # Try to fetch group title for confirmation
    try:
        chat = await client.get_chat(new_id)
        title = chat.title or str(new_id)
    except Exception:
        title = str(new_id)

    await message.reply_text(
        f"✅ **Group updated!**\n\n"
        f"📌 Group : **{title}**\n"
        f"🆔 ID     : `{new_id}`\n"
        f"🔗 Link   : {new_link}\n\n"
        f"Normal users must now be a member of this group to use the bot."
    )


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@app.on_message(filters.command("help") & AUTH)
async def help_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    text = HELP_TEXT
    if is_owner(uid) or is_admin(uid):
        text = text + _OWNER_HELP_TEXT
    await message.reply_text(text, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /googledrive — per-user Google Drive OAuth2 connect / disconnect
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# /Driveserver — find an existing server file, then upload to Drive + Telegram
# ---------------------------------------------------------------------------

@app.on_message(filters.private & filters.command(["Driveserver", "driveserver"]))
async def driveserver_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # This command exposes files already stored on the server; keep it owner/admin only.
    if not (is_owner(user_id) or is_admin(user_id)):
        return await message.reply_text("❌ This command is available to the bot owner/admin only.")

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text(
            "**Usage:** `/Driveserver filename.mkv`\n\n"
            "Example: `/Driveserver Anime Cartoon Chhota Bheem Damyaan's Legacy Part 1AnimeCartoon.mkv`"
        )

    requested_name = parts[1].strip().strip('"').strip("'")
    # Accept a filename only, never an arbitrary path supplied in the command.
    if requested_name != os.path.basename(requested_name) or requested_name in (".", ".."):
        return await message.reply_text("❌ Please provide only the filename, not a file path.")

    if not os.path.isdir(DOWNLOAD_DIRECTORY):
        return await message.reply_text(
            f"❌ Server downloads folder not found: `{DOWNLOAD_DIRECTORY}`"
        )

    exact_matches = []
    folded_matches = []
    for root, _dirs, files in os.walk(DOWNLOAD_DIRECTORY):
        for entry in files:
            if entry == requested_name:
                exact_matches.append(os.path.join(root, entry))
            elif entry.casefold() == requested_name.casefold():
                folded_matches.append(os.path.join(root, entry))

    matches = exact_matches or folded_matches
    if not matches:
        return await message.reply_text(
            f"❌ **File not found on server**\n\n"
            f"📁 Searched: `{DOWNLOAD_DIRECTORY}`\n"
            f"📄 File: `{requested_name}`"
        )

    # If the same filename exists in multiple job folders, use the newest copy.
    file_path = max(matches, key=lambda item: os.path.getmtime(item))
    if len(matches) > 1:
        LOG.warning("/Driveserver found %d copies of %s; using newest: %s",
                    len(matches), requested_name, file_path)

    file_size = os.path.getsize(file_path)
    if file_size <= 0:
        return await message.reply_text(f"❌ File is empty: `{requested_name}`")

    status = await message.reply_text(
        "🔎 **File found on server**\n"
        f"📄 `{requested_name}`\n"
        f"💾 `{file_size / (1024 ** 2):.1f} MB`\n\n"
        "🚀 Starting Google Drive + Telegram upload..."
    )

    try:
        duration = await get_video_duration(file_path)
        await _run_upload_destination(
            client=client,
            user_id=user_id,
            dest="both",
            status_msg=status,
            out_path=file_path,
            caption=f"{requested_name}\nUploaded via /Driveserver",
            duration=duration,
            thumb_path=None,
            save_dir=os.path.dirname(file_path),
            was_cancelled=False,
        )
    except Exception as exc:
        LOG.error("/Driveserver upload failed for %s: %s", file_path, exc, exc_info=True)
        try:
            await status.edit_text(
                f"❌ **Upload failed**\n\n`{str(exc)[-1800:]}`\n\n"
                "The server file has been kept; it was not deleted."
            )
        except Exception:
            await message.reply_text(f"❌ Upload failed: `{str(exc)[-1200:]}`")


# ---------------------------------------------------------------------------
# /DriveUpload and /Griveupload — Reply to Telegram media and upload it to Drive
# ---------------------------------------------------------------------------

@app.on_message(filters.private & filters.command(["DriveUpload", "driveupload", "Griveupload", "griveupload"]))
async def griveupload_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    reply = message.reply_to_message
    if not reply:
        return await message.reply_text(
            "❌ Reply to a video/document/file and use `/Griveupload`."
        )

    media = (
        reply.video
        or reply.document
        or reply.audio
        or reply.animation
    )

    if not media:
        return await message.reply_text(
            "❌ Reply to a video, document, audio or file."
        )

    if not (_sa_enabled() or is_user_connected(user_id)):
        return await message.reply_text(
            "☁️ **Google Drive is not configured.**\n\n"
            "Connect/configure Google Drive first with `/gdrive`."
        )

    original_name = (
        getattr(media, "file_name", None)
        or getattr(reply, "file_name", None)
        or f"telegram_{reply.id}.bin"
    )

    save_dir = os.path.join(
        DOWNLOAD_DIRECTORY,
        f"griveupload_{int(time.time())}_{reply.id}"
    )
    os.makedirs(save_dir, exist_ok=True)

    local_path = os.path.join(save_dir, original_name)

    status = await message.reply_text(
        f"📥 **Downloading Telegram**\n"
        f"📁 File: `{original_name}`\n\n"
        f"📊 **Download Progress**\n"
        f"`░░░░░░░░░░░░░░░░░░░░` `0.0%/100.0%`\n"
        f"💾 Size  : `0 B`\n"
        f"⚡ Speed : `0 B/s`\n"
        f"⏳ ETA   : `--:--`"
    )

    download_start = time.time()

    async def _download_progress(current, total):
        try:
            now = time.time()
            elapsed = max(now - download_start, 0.001)
            speed = current / elapsed
            percent = (current / total * 100) if total else 0

            filled = min(20, int(percent / 5))
            bar = "▓" * filled + "░" * (20 - filled)

            eta = (
                (total - current) / speed
                if speed > 0 and total
                else 0
            )

            def _size(n):
                units = ["B", "KB", "MB", "GB", "TB"]
                n = float(n)
                for unit in units:
                    if n < 1024 or unit == units[-1]:
                        return f"{n:.1f} {unit}"
                    n /= 1024

            def _eta(sec):
                sec = int(max(sec, 0))
                return f"{sec // 60:02d}:{sec % 60:02d}"

            text = (
                f"📥 **Downloading Telegram**\n"
                f"📁 File: `{original_name}`\n\n"
                f"📊 **Download Progress**\n"
                f"`{bar}` `{percent:.1f}%/100.0%`\n"
                f"💾 Size  : `{_size(current)} / {_size(total)}`\n"
                f"⚡ Speed : `{_size(speed)}/s`\n"
                f"⏳ ETA   : `{_eta(eta)}`"
            )

            if not hasattr(_download_progress, "_last"):
                _download_progress._last = 0

            if now - _download_progress._last >= 2:
                _download_progress._last = now
                try:
                    await status.edit_text(text)
                except Exception:
                    pass

        except Exception:
            pass

    try:
        downloaded = await _parallel_download_telegram_media(
            client,
            media,
            local_path,
            progress=_download_progress,
            progress_args=(),
        )

        if not downloaded or not os.path.exists(downloaded):
            raise RuntimeError("Telegram download failed.")

        # Use the actual downloaded filename while preserving the original name.
        local_path = downloaded

        # ------------------------------------------------------------------
        # Real Google Drive resumable upload
        # ------------------------------------------------------------------
        from googleapiclient.http import MediaFileUpload

        user_connected = is_user_connected(user_id)
        folder_id = None if user_connected else GDRIVE_FOLDER_ID

        if user_connected:
            service = _build_user_service(user_id)
        else:
            service = _build_sa_service()

        meta = {"name": original_name}
        if folder_id:
            meta["parents"] = [folder_id]

        mime_type = (
            "video/x-matroska"
            if original_name.lower().endswith(".mkv")
            else "video/mp4"
            if original_name.lower().endswith((".mp4", ".m4v"))
            else "application/octet-stream"
        )

        total_size = os.path.getsize(local_path)

        drive_status = (
            f"☁️ **Uploading to Google Drive…**\n"
            f"📁 File: `{original_name}`\n\n"
            f"📊 **Upload Progress**\n"
            f"`░░░░░░░░░░░░░░░░░░░░` `0.0%/100.0%`\n"
            f"💾 Size  : `0 B / {total_size / (1024**2):.1f} MB`\n"
            f"⚡ Speed : `0 B/s`"
        )

        try:
            await status.edit_text(drive_status)
        except Exception:
            pass

        media_upload = MediaFileUpload(
            local_path,
            mimetype=mime_type,
            resumable=True,
            chunksize=8 * 1024 * 1024,
        )

        request = service.files().create(
            body=meta,
            media_body=media_upload,
            fields="id,webViewLink",
        )

        upload_start = time.time()
        response = None
        last_update = 0

        while response is None:
            status_obj, response = await asyncio.to_thread(request.next_chunk)

            if status_obj:
                now = time.time()
                if now - last_update >= 1 or response:
                    last_update = now

                    uploaded = int(status_obj.resumable_progress or 0)
                    percent = (
                        uploaded / total_size * 100
                        if total_size else 100
                    )
                    percent = min(100.0, percent)

                    elapsed = max(now - upload_start, 0.001)
                    speed = uploaded / elapsed
                    eta = (
                        (total_size - uploaded) / speed
                        if speed > 0
                        else 0
                    )

                    filled = min(20, int(percent / 5))
                    bar = "▓" * filled + "░" * (20 - filled)

                    def _fmt(n):
                        units = ["B", "KB", "MB", "GB", "TB"]
                        n = float(n)
                        for unit in units:
                            if n < 1024 or unit == units[-1]:
                                return f"{n:.1f} {unit}"
                            n /= 1024

                    eta_i = int(max(eta, 0))

                    drive_status = (
                        f"☁️ **Uploading to Google Drive…**\n"
                        f"📁 File: `{original_name}`\n\n"
                        f"📊 **Upload Progress**\n"
                        f"`{bar}` `{percent:.1f}%/100.0%`\n"
                        f"💾 Size  : `{_fmt(uploaded)} / {_fmt(total_size)}`\n"
                        f"⚡ Speed : `{_fmt(speed)}/s`\n"
                        f"⏳ ETA   : `{eta_i // 60:02d}:{eta_i % 60:02d}`"
                    )

                    try:
                        await status.edit_text(drive_status)
                    except Exception:
                        pass

        file_id = response["id"]
        link = (
            response.get("webViewLink")
            or f"https://drive.google.com/file/d/{file_id}/view"
        )
        short_link = await _shrink2(link)
        link_label = (
            "ShrinkMe Link"
            if short_link != link
            else "Drive Link Ready (unshortened)"
        )

        final = (
            f"✅ **Uploaded to Google Drive**\n\n"
            f"📁 File: `{original_name}`\n"
            f"🔗 [{link_label}]({short_link})"
        )

        try:
            await status.edit_text(
                final,
                disable_web_page_preview=True,
            )
        except Exception:
            await client.send_message(
                message.chat.id,
                final,
                disable_web_page_preview=True,
            )

    except Exception as e:
        LOG.exception("Griveupload failed")
        try:
            await status.edit_text(
                f"❌ **Google Drive upload failed**\n\n"
                f"`{str(e)[:2000]}`"
            )
        except Exception:
            pass

    finally:
        try:
            if os.path.isdir(save_dir):
                import shutil
                shutil.rmtree(save_dir, ignore_errors=True)
        except Exception:
            pass


@app.on_message(filters.private & filters.command(["googledrive", "gdrive"]))
async def googledrive_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    args    = message.command[1:]
    sub     = args[0].lower() if args else ""

    # ── disconnect ────────────────────────────────────────────────────────
    if sub == "disconnect":
        if disconnect_user(user_id):
            return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
                "✅ **Google Drive disconnected.**\n\n"
                "Aapki future recordings Telegram par upload hongi.\n"
                "Dobara connect karne ke liye: /googledrive"
            )
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "⚠️ Aapka Google Drive abhi connected nahi tha."
        )

    # ── status ────────────────────────────────────────────────────────────
    if sub in ("status", "info"):
        connected = is_user_connected(user_id)
        sa_ready  = _sa_enabled()
        lines = ["☁️ **Google Drive Status**\n"]
        lines.append(f"👤 Aapka account: {'✅ Connected' if connected else '❌ Not connected'}")
        if sa_ready:
            lines.append("🤖 Shared (service) account: ✅ Active (fallback)")
        if not connected and not sa_ready:
            lines.append("\n_Koi bhi Drive account configured nahi hai._")
        if connected:
            lines.append("\nDisconnect: /googledrive disconnect")
        else:
            lines.append("\nConnect: /googledrive")
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "\n".join(lines))

    # ── already connected ─────────────────────────────────────────────────
    if is_user_connected(user_id):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "✅ **Google Drive Already Connected!**\n\n"
            "Aapki recordings automatically **aapki Drive** par upload hongi.\n\n"
            "🔹 Status: /googledrive status\n"
            "🔹 Disconnect: /googledrive disconnect"
        )

    # ── OAuth2 not configured — show service-account info or error ────────
    if not _oauth_enabled():
        if _sa_enabled():
            sa_email = get_sa_email()
            return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
                "🤖 **Google Drive (Shared Account)**\n\n"
                "Bot ek shared service account use karta hai uploads ke liye.\n\n"
                f"📧 Service Account Email:\n`{sa_email}`\n\n"
                "Apni Drive folder share karne ke liye:\n"
                "1. Google Drive open karein\n"
                "2. Folder par right-click → Share\n"
                f"3. Upar wali email add karein as **Editor**\n\n"
                "_Individual OAuth2 login ke liye owner se "
                "`GOOGLE_CLIENT_ID` aur `GOOGLE_CLIENT_SECRET` set karne ko bolein._"
            )
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "⚠️ **Google Drive abhi configure nahi hai.**\n\n"
            "Owner se yeh secrets set karne ko bolein:\n"
            "• `GDRIVE_SA_JSON` + `GDRIVE_FOLDER_ID` (shared account)\n"
            "• `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (per-user login)"
        )

    # ── start OAuth2 device flow ──────────────────────────────────────────
    wait_msg = await _reply_auto_delete(message, AUTO_DELETE_5MIN, "🔗 Google Drive se connect kar raha hoon...")
    try:
        flow = await asyncio.to_thread(start_device_flow_sync)
    except Exception as e:
        LOG.error(f"GDrive device flow start failed uid={user_id}: {e}")
        return await wait_msg.edit_text(f"❌ Google Drive connect nahi ho saka: `{e}`")
    verification_url = flow.get("verification_url", "https://www.google.com/device")
    user_code        = flow.get("user_code", "")
    device_code      = flow.get("device_code", "")
    interval         = int(flow.get("interval", 5))
    expires_in       = int(flow.get("expires_in", 1800))

    drive_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 Open Google Authorization", url=verification_url)
    ]])
    await wait_msg.edit_text(
        "🤖 **Google Drive Connect Karein**\n\n"
        "**Step 1:** Neeche **Open Google Authorization** dabao.\n\n"
        f"🔑 **Code:** `{user_code}`\n\n"
        "**Step 3:** Apna Google account select karein aur **Allow** karein.\n\n"
        f"⏰ Code **{expires_in // 60} minutes** mein expire hoga.\n"
        "_Allow karte hi bot automatically connection detect karega._",
        reply_markup=drive_kb,
        disable_web_page_preview=True,
    )

    _oauth_device_code = flow.get("device_code", "")
    if not _oauth_device_code:
        LOG.error("Google OAuth response did not contain device_code: %r", flow)
        return await wait_msg.edit_text(
            "❌ Google Drive auth failed: `device_code missing from Google response`"
        )

    asyncio.create_task(
        poll_and_save_token(
            client,
            user_id,
            _oauth_device_code,
            interval,
            expires_in,
        )
    )


# ---------------------------------------------------------------------------
# /Drivelogout — quick alias to disconnect Google Drive
# ---------------------------------------------------------------------------

@app.on_message(filters.private & filters.command(["Drivelogout", "drivelogout", "gdrive_logout", "drivelog"]))
async def drivelogout_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if disconnect_user(user_id):
        await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "✅ **Google Drive Disconnected!**\n\n"
            "Aapki agli recordings Telegram par upload hongi.\n\n"
            "Dobara connect karne ke liye: /googledrive"
        )
    else:
        await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "⚠️ Aapka Google Drive pehle se connected nahi tha.\n\n"
            "Connect karne ke liye: /googledrive"
        )


# ---------------------------------------------------------------------------
# /verify — ad-click self-service verification (shortxlinks.in)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("verify") & AUTH)
async def verify_cmd(client: Client, message: Message):
    # Anonymous Admin is a privileged group actor and must never enter the
    # user verification/quota flow. Verification is explicitly unavailable
    # for Anonymous Admin; recording commands bypass verification instead.
    if _is_official_anonymous_admin(message):
        return await message.reply_text(
            "🚫 **Verification is not allowed for Anonymous Admin.**\n\n"
            "🛡 Anonymous Admin already has direct recording access.\n"
            "Use `/rec`, `/drec`, or `/reclink` directly."
        )

    uid = _message_actor_id(message)

    if is_owner(uid):
        return await message.reply_text(
            "👑 **Owner account — unlimited recording access!**\n\n"
            "Quota system does not apply to owners."
        )

    user = get_user(uid)

    # Locked for today?
    if user["verify_left"] <= 0:
        elapsed     = time.time() - user.get("last_refresh", time.time())
        remaining_s = max(REFRESH_SECONDS - elapsed, 0)
        rh = int(remaining_s // 3600)
        rm = int((remaining_s % 3600) // 60)
        return await message.reply_text(
            "🚫 **Aaj ke liye sab verifications lock ho gaye!**\n\n"
            f"⏱️ Next refresh in: **{rh}h {rm}m**\n\n"
            "Use /limit to check your full status."
        )

    # Reuse unexpired pending link or generate a fresh one
    existing = pending_verify.get(uid)
    if existing and existing.get("expires", 0) > time.time():
        short_url    = existing["short_url"]
        is_shortened = existing.get("is_shortened", False)
    else:
        target    = f"https://t.me/{BOT_USERNAME}?start=vfy_{uid}"
        short_url = await _shrink2(target)
        is_shortened = (short_url != target)
        pending_verify[uid] = {
            "short_url":    short_url,
            "expires":      time.time() + _VERIFY_LINK_TTL,
            "is_shortened": is_shortened,
        }

    v_left   = user["verify_left"]
    step_idx = min(user["verify_done"], len(VERIFY_STEPS) - 1)
    next_rec = VERIFY_STEPS[step_idx]["result_rec"]
    bonus    = 1 if user.get("is_lucky") else 0

    shrink_note = "" if is_shortened else (
        "\n\n⚠️ _Ad-link generate nahi ho saka. Neeche diye link se seedha verify karein._"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Click to Verify", url=short_url)],
    ])

    await message.reply_text(
        "🔐 **Verification Required**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Aage bot ka istemal karne aur **+Rec {next_rec + bonus}** ka quota unlock karne ke\n"
        "liye neeche diye gaye **Click to Verify** button par click karein.\n\n"
        "Ad dekhe ke baad aap bot par wapas aa jayenge aur automatically verify ho jayenge.\n"
        "Agar automatic verify na ho to **I've Verified** button dabayein.\n\n"
        "⚠️ _Yeh link sirf aapke liye hai — dusron ko share mat karein._\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆓 Remaining verifications today: **{v_left}** / {DEFAULT_VERIFY_LEFT}"
        f"{shrink_note}",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


@app.on_callback_query(filters.regex(r"^vrf:(\d+):done$"))
async def vrf_done_cb(client: Client, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your verification.", show_alert=True)

    pending_verify.pop(uid, None)

    ok, reward_msg = apply_verify_bonus(uid)

    if ok:
        # Auto-add to verified.json so other commands work without separate approval
        vdata = load_verified()
        if str(uid) not in vdata.setdefault("verified", {}):
            vdata["verified"][str(uid)] = {
                "approved_by": "self_verify",
                "approved_at": datetime.now(tz).isoformat(),
            }
            save_verified(vdata)

        await cq.answer("✅ Verified!", show_alert=False)
        try:
            await cq.message.edit_text(
                f"✅ **Verification Successful!**\n\n"
                f"{reward_msg}\n\n"
                "Use `/rec <url> HH:MM:SS <filename>` to start recording.",
                reply_markup=None,
            )
        except Exception:
            try:
                await client.send_message(
                    uid,
                    f"✅ **Verified!**\n\n{reward_msg}\n\n"
                    "Use /rec to start recording.",
                )
            except Exception:
                pass
    else:
        await cq.answer("🚫 Limit expired!", show_alert=True)
        try:
            await cq.message.edit_text(
                f"{reward_msg}\n\nUse /limit to check your status.",
                reply_markup=None,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Recording progress callbacks — Gen Preview / Refresh / Cancel
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^rec_prev:(\d+):(\d+)$"))
async def rec_preview_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    rec_id = int(parts[2])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your recording.", show_alert=True)
    entry = active_recs.get(uid, {}).get(rec_id)
    if not entry:
        return await cq.answer("Recording finished or not found.", show_alert=True)
    if not entry.get("ffmpeg_pid"):
        return await cq.answer(
            "Gen Preview will be available after recording starts.",
            show_alert=True,
        )
    setup = entry.get("setup")
    if not setup:
        return await cq.answer("Preview session is unavailable.", show_alert=True)
    eff_url = entry.get("effective_url")
    if not eff_url:
        return await cq.answer("Stream URL not ready yet — try again in a moment.", show_alert=True)
    setup["effective_url"] = eff_url
    setup["is_hls"] = entry.get("is_hls", True)
    setup["status_msg"] = entry.get("status_msg") or cq.message
    setup["setup_msg_id"] = setup["status_msg"].id
    setup.setdefault(
        "current_status_text",
        getattr(cq.message, "caption", None) or getattr(cq.message, "text", None) or "🎬 Recording in Progress...",
    )
    setup.setdefault("current_reply_markup", getattr(cq.message, "reply_markup", None))
    if not await _start_live_preview(client, setup):
        return await cq.answer("Could not capture a frame right now. Try again.", show_alert=True)
    await cq.answer("✅ Preview generated. Tap Gen Preview again to update it.")


@app.on_callback_query(filters.regex(r"^rec_ref:(\d+):(\d+)$"))
async def rec_refresh_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    rec_id = int(parts[2])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your recording.", show_alert=True)
    entry = active_recs.get(uid, {}).get(rec_id)
    if not entry:
        return await cq.answer("Recording finished or not found.", show_alert=True)
    duration        = time_to_seconds(entry["status"]["target"])
    elapsed         = _recording_elapsed(entry, duration)
    pct             = min((elapsed / duration) * 100, 100) if duration > 0 else 0
    bar             = "●" * int(10 * pct // 100) + "⬜" * (10 - int(10 * pct // 100))
    task_id         = hex(rec_id)[2:10]
    slot_n          = list(active_recs.get(uid, {}).keys()).index(rec_id) + 1
    text = (
        f"🎬 **Recording #{slot_n} in Progress...**\n\n"
        f"📡 Stream Capture\n"
        f"[{bar}]  {pct:.1f}%\n"
        f"⏱ Time  : {TimeFormatter(int(elapsed*1000))} / {TimeFormatter(duration*1000)}\n"
        f"🆔 Task  : {task_id}\n\n"
        f"_Press **Gen Preview** for a live thumbnail_"
    )
    kb = _rec_progress_kb(uid, rec_id)
    try:
        if entry.get("is_photo_msg"):
            await cq.message.edit_caption(text, reply_markup=kb)
        else:
            await cq.message.edit_text(text, reply_markup=kb)
        await cq.answer("✅ Refreshed!")
    except Exception:
        await cq.answer("Already up to date.", show_alert=False)


@app.on_callback_query(filters.regex(r"^rec_cxl:(\d+):(\d+)$"))
async def rec_cancel_btn_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    rec_id = int(parts[2])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your recording.", show_alert=True)
    if uid not in active_recs or rec_id not in active_recs.get(uid, {}):
        return await cq.answer("Recording already finished.", show_alert=True)
    cancelled_recs.add((uid, rec_id))
    pid = active_recs[uid][rec_id].get("ffmpeg_pid")
    if pid:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    await cq.answer("⏹ Cancel signal sent.")
    try:
        await cq.message.edit_caption("⏹ **Recording cancelled.**\nPartial file will be uploaded if available.")
    except Exception:
        try:
            await cq.message.edit_text("⏹ **Recording cancelled.**\nPartial file will be uploaded if available.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Upload destination choice  (upl:{uid}:{rec_id}:tg|gd|both)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^upl:(\d+):(\d+):(tg|gd|both)$"))
async def upload_choice_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    rec_id = int(parts[2])
    choice = parts[3]

    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your recording.", show_alert=True)

    state = pending_uploads.pop((uid, rec_id), None)
    if not state:
        await cq.answer("Session expired — file may have been cleaned up.", show_alert=True)
        try:
            await cq.message.edit_text("⚠️ Upload session expired.")
        except Exception:
            pass
        return

    # If Drive selected but not connected → restore state and show alert
    if choice in ("gd", "both"):
        gd_ok = _gdrive_is_enabled() or is_user_connected(uid)
        if not gd_ok:
            pending_uploads[(uid, rec_id)] = state   # restore so user can retry
            return await cq.answer(
                "☁️ Google Drive linked nahi hai!\n/DriveAuth se pehle connect karein.",
                show_alert=True,
            )

    await cq.answer("⬆️ Uploading…")
    try:
        await cq.message.edit_text("⬆️ Uploading… please wait.", reply_markup=None)
    except Exception:
        pass

    video_path    = state["video_path"]
    thumb_path    = state["thumb_path"]
    caption       = state["caption"]
    dur           = state["dur"]
    save_dir      = state["save_dir"]
    was_cancelled = state["was_cancelled"]
    setup         = state["setup"]
    send_target   = state["send_target"]
    status_msg    = state["status_msg"]
    filename      = state["filename"]

    if choice in ("tg", "both"):
        upload_start = time.time()
        await split_and_send_video(
            send_target, video_path, caption, dur,
            thumb_path=thumb_path,
            status_msg=status_msg,
            progress=progress_for_pyrogram,
            progress_args=(send_target, upload_start, status_msg, save_dir, was_cancelled),
            _uid=uid, _chat_id=send_target.chat.id,
        )
        if setup.get("auto_mode") and not was_cancelled and dur > 120:
            try:
                await status_msg.edit_text("✂️ Auto mode: generating last 2-minute clip…")
            except Exception:
                pass
            clip_dir   = join(save_dir, "auto_clips")
            os.makedirs(clip_dir, exist_ok=True)
            last_clip  = join(clip_dir, "last_2min.mkv")
            last_start = max(0, dur - 120)
            await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-ss {last_start} -to {dur} -i {shlex.quote(video_path)} '
                f'-c copy {shlex.quote(last_clip)}'
            )
            if os.path.exists(last_clip) and os.path.getsize(last_clip) > 0:
                try:
                    await send_target.reply_video(
                        video=last_clip,
                        caption=(f"🎬 **{BRAND_TITLE}** — ⏭ Last 2 minute"),
                        supports_streaming=True,
                    )
                except Exception as ce:
                    LOG.warning(f"Auto clip upload failed: {ce}")

    if choice in ("gd", "both"):
        await upload_and_notify(client, uid, video_path, filename)

    if save_dir and os.path.exists(save_dir):
        schedule_retention_cleanup(save_dir)


# ---------------------------------------------------------------------------
# /limit — show daily quota status
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["limit", "Limit"]) & AUTH)
async def limit_cmd(_, message: Message):
    uid = _message_actor_id(message)
    if is_owner(uid):
        return await message.reply_text(
            "👑 **Owner Account — Unlimited Access**\n\n"
            "You have unrestricted recording access.\n"
            "Use /rec anytime without quota limits."
        )
    await message.reply_text(
        format_limit_message(uid),
        disable_web_page_preview=True,
    )


@app.on_callback_query(filters.regex(r"^approve:(\d+):(\d+)$"))
async def cb_approve(client: Client, cq: CallbackQuery):
    if not is_owner(cq.from_user.id):
        return await cq.answer("Not authorized.", show_alert=True)
    uid  = int(cq.data.split(":")[1])
    days = int(cq.data.split(":")[2])
    data = load_verified()
    data["pending"].pop(str(uid), None)
    entry: dict = {"approved_by": cq.from_user.id, "approved_at": datetime.now(tz).isoformat()}
    if days > 0:
        exp = datetime.now(tz) + timedelta(days=days)
        entry["expires_at"] = exp.isoformat()
        label = f"{days} days"
    else:
        label = "permanent"
    data.setdefault("verified", {})[str(uid)] = entry
    save_verified(data)
    await cq.answer(f"Approved ({label})!")
    try:
        await cq.message.edit_text(
            cq.message.text + f"\n\n✅ Approved ({label}) by {cq.from_user.first_name}",
            reply_markup=None,
        )
    except Exception:
        pass
    try:
        await client.send_message(
            uid,
            f"✅ **You are now verified!**\n\nAccess: `{label}`\n\n"
            f"You can now use `/rec`, `/download`, and other commands.",
        )
    except Exception as e:
        LOG.warning(f"Could not notify user {uid}: {e}")


@app.on_callback_query(filters.regex(r"^reject:(\d+)$"))
async def cb_reject(client: Client, cq: CallbackQuery):
    if not is_owner(cq.from_user.id):
        return await cq.answer("Not authorized.", show_alert=True)
    uid  = int(cq.data.split(":")[1])
    data = load_verified()
    data["pending"].pop(str(uid), None)
    save_verified(data)
    await cq.answer("Rejected.")
    try:
        await cq.message.edit_text(
            cq.message.text + f"\n\n❌ Rejected by {cq.from_user.first_name}", reply_markup=None
        )
    except Exception:
        pass
    try:
        await client.send_message(
            uid,
            "❌ **Your verification request was not approved.**\n\n"
            f"Contact @{SUPPORT_USERNAME} for more info.",
        )
    except Exception as e:
        LOG.warning(f"Could not notify user {uid}: {e}")


# ---------------------------------------------------------------------------
# /UpdateWatermark — owner-only: change default watermark image URL (or fallback text)
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["UpdateWatermark", "updatewatermark", "setwatermark", "wmark"]) & AUTH)
async def update_watermark_cmd(_, message: Message):
    if not _is_control_actor(message):
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        current_url  = get_default_watermark_img_url()
        current_text = get_default_watermark()
        return await message.reply_text(
            f"**Watermark Settings**\n\n"
            f"🖼 Image URL: `{current_url}`\n"
            f"📝 Fallback text: `{current_text}`\n\n"
            f"**To set image watermark (recommended):**\n"
            f"`/UpdateWatermark https://example.com/logo.png`\n\n"
            f"**To set text watermark (fallback):**\n"
            f"`/UpdateWatermark @YourChannelName`\n\n"
            f"Image watermark takes priority. Text is used only when image can't be downloaded."
        )

    new_val = parts[1].strip()

    if new_val.lower().startswith("http"):
        # Image URL
        set_default_watermark_img_url(new_val)
        await message.reply_text("⬇️ Downloading new watermark image…")
        ok = await _async_ensure_watermark_img()
        if ok:
            await message.reply_text(
                f"✅ **Watermark image updated!**\n\n"
                f"URL: `{new_val}`\n\n"
                f"All `/rec`, `/drec`, and `/Watermark` will now use this image (bottom-right, last 2 min)."
            )
        else:
            await message.reply_text(
                f"⚠️ URL saved, but image download failed. Check the URL.\n`{new_val}`"
            )
    else:
        # Text fallback
        set_default_watermark(new_val)
        await message.reply_text(
            f"✅ **Fallback watermark text updated!**\n\n"
            f"Text: `{new_val}`\n\n"
            f"This is used only when the watermark image can't be fetched."
        )


# ---------------------------------------------------------------------------
# /setwatermarksize — owner-only: change watermark logo size (px width)
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["setwatermarksize", "SetWatermarkSize", "wmarksize"]) & AUTH)
async def setwatermarksize_cmd(_, message: Message):
    if not _is_control_actor(message):
        return
    current = get_watermark_size()
    parts   = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text(
            f"**Watermark Size Settings**\n\n"
            f"📐 Current size: `{current}px` width\n\n"
            f"**Usage:**\n"
            f"`/setwatermarksize <pixels>`\n\n"
            f"**Examples:**\n"
            f"• `/setwatermarksize 60` — chota (small)\n"
            f"• `/setwatermarksize 80` — medium (default)\n"
            f"• `/setwatermarksize 120` — thoda bada\n"
            f"• `/setwatermarksize 150` — bada (large)\n\n"
            f"_Range: 20px – 500px. Yeh size photo aur M3U8 recording dono par apply hogi._"
        )
    val = parts[1].strip()
    if not val.isdigit():
        return await message.reply_text("❌ Sirf number dena hai. Example: `/setwatermarksize 100`")
    px = int(val)
    if px < 20 or px > 500:
        return await message.reply_text("❌ Size 20 se 500 ke beech honi chahiye.")
    set_watermark_size(px)
    await message.reply_text(
        f"✅ **Watermark size updated!**\n\n"
        f"📐 New size: `{px}px` width\n\n"
        f"Yeh size ab se `/rec`, `/drec`, aur `/Watermark` sab mein apply hogi.\n"
        f"Photo watermark aur M3U8 live recording dono mein."
    )


# ---------------------------------------------------------------------------
# /audionameupdate — owner-only: change the audio track brand name live
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["audionameupdate", "audionamechange", "audiobrand"]) & AUTH)
async def audionameupdate_cmd(_, message: Message):
    if not _is_control_actor(message):
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        current = get_audio_brand_name()
        return await message.reply_text(
            f"**Audio Brand Name**\n\n"
            f"Current: `{current}`\n\n"
            f"Usage: `/audionameupdate @YourChannelName`\n"
            f"Takes effect on the **next** recording."
        )
    new_name = parts[1].strip()
    set_audio_brand_name(new_name)
    await message.reply_text(
        f"✅ **Audio brand name updated!**\n\n"
        f"New name: `{new_name}`\n\n"
        f"This will be embedded in the **title** and **handler_name** of all audio tracks "
        f"in every recording from now on. Visible in VLC, MX Player, and Telegram's audio track selector."
    )


# ---------------------------------------------------------------------------
# /anonadmin — owner/admin Anonymous Admin mode
# ---------------------------------------------------------------------------

def _anonadmin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Enable", callback_data="anonadmin:enable"),
            InlineKeyboardButton("🔴 Disable", callback_data="anonadmin:disable"),
        ]
    ])


def _anonadmin_text() -> str:
    status = "✅ ON" if is_anonymous_admin_enabled() else "❌ OFF"
    return (
        "🕵️ **Anonymous Admin**\n\n"
        f"Current Status: **{status}**\n\n"
        "When enabled:\n"
        "• Admin actions are shown without the admin's personal name\n"
        "• Bot responses can use `🛡 Anonymous Admin`\n"
        "• **User** = normal user\n"
        "• **Admin** = configured admin user\n"
        "• **Anonymous Admin** = group anonymous-admin mode\n"
        "• Normal users remain unchanged"
    )


@app.on_message(filters.command(["anonadmin", "AnonymousAdmin"]) & AUTH)
async def anonadmin_cmd(_, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /anonadmin.")
    await message.reply_text(_anonadmin_text(), reply_markup=_anonadmin_kb())


@app.on_callback_query(filters.regex(r"^anonadmin:(enable|disable)$"))
async def anonadmin_cb(_, cq: CallbackQuery):
    uid = cq.from_user.id if cq.from_user else 0
    # Callback queries originate from a message; use that message for the
    # same owner/admin/Anonymous Admin authorization path.
    if not cq.message or not _is_control_actor(cq.message):
        return await cq.answer("Only owner/admin can change this.", show_alert=True)

    enabled = cq.data.split(":", 1)[1] == "enable"
    set_anonymous_admin(enabled)
    await cq.answer("Anonymous Admin enabled." if enabled else "Anonymous Admin disabled.")
    try:
        await cq.message.edit_text(_anonadmin_text(), reply_markup=_anonadmin_kb())
    except Exception:
        pass


# /Admin_add, /Admin_delete, /Admin_list — owner-only, hidden
# ---------------------------------------------------------------------------

@app.on_message(filters.command("admin_add"))
async def admin_add_cmd(_, message: Message):
    uid = _message_actor_id(message) or None
    if not uid or not is_owner(uid):
        return await message.reply_text("❌ This command is for the bot owner only.")
    parts = message.command
    if len(parts) < 2:
        return await message.reply_text(
            "ℹ️ **Usage:** `/Admin_add <user_id>`\n\n"
            "Example: `/Admin_add 123456789`\n"
            "To find a user ID, forward their message to @userinfobot"
        )
    try:
        uid = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")
    if is_owner(uid):
        return await message.reply_text("That user is already an owner.")
    ok = add_admin(uid)
    invalidate_member_cache(uid)
    if ok:
        await message.reply_text(f"✅ {uid} added as admin.")
    else:
        await message.reply_text(f"⚠️ {uid} is already an admin.")


@app.on_message(filters.command("admin_delete"))
async def admin_delete_cmd(_, message: Message):
    if not _is_control_actor(message):
        return await message.reply_text("❌ This command is for the bot owner only.")
    parts = message.command
    if len(parts) < 2:
        return await message.reply_text(
            "ℹ️ **Usage:** `/Admin_delete <user_id>`\n\n"
            "Example: `/Admin_delete 123456789`"
        )
    try:
        uid = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")
    ok = del_admin(uid)
    invalidate_member_cache(uid)
    if ok:
        await message.reply_text(f"✅ `{uid}` removed from admins.")
    else:
        await message.reply_text(f"⚠️ `{uid}` was not an admin.")


@app.on_message(filters.command("Admin_list"))
async def admin_list_cmd(_, message: Message):
    if not _is_control_actor(message):
        return await message.reply_text("❌ This command is for the bot owner only.")
    admins = load_admins()
    if not admins:
        return await message.reply_text("No admins configured.")
    lines = "\n".join(f"• `{uid}`" for uid in admins)
    await message.reply_text(f"**Admin list ({len(admins)}):**\n{lines}")


# ---------------------------------------------------------------------------
# /ffmpeg  /ffprobe — owner/admin shell commands
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FFmpeg Task-mode helpers
# ---------------------------------------------------------------------------

_FF_DUR_RE  = re.compile(r"(?:^|\s)-t\s+([^\s-]\S*)", re.IGNORECASE)
_FF_TIME_RE = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
_FF_SIZE_RE = re.compile(r"size=\s*(\d+)kB")
_FF_OUT_EXTS = {".mkv", ".mp4", ".ts", ".avi", ".mov", ".flv", ".webm", ".m4v",
                ".aac", ".mp3", ".m4a", ".opus", ".ogg"}


def _ff_hms_to_sec(s: str) -> float:
    try:
        parts = s.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except Exception:
        return 0.0


def _ff_sec_to_hms(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ff_bar(pct: float, length: int = 10) -> str:
    filled = int(length * min(pct, 100.0) / 100)
    return "█" * filled + "░" * (length - filled)


def _parse_ff_duration(args_text: str) -> float:
    m = _FF_DUR_RE.search(args_text)
    return _ff_hms_to_sec(m.group(1)) if m else 0.0


def _detect_ff_output(args_text: str) -> str | None:
    """Return last non-URL argument that looks like an output file, or None."""
    try:
        parts = shlex.split(args_text)
    except Exception:
        return None
    _skip_flags = {
        "-i", "-t", "-ss", "-to", "-vf", "-af", "-c", "-c:v", "-c:a",
        "-b:v", "-b:a", "-r", "-s", "-f", "-vcodec", "-acodec", "-preset",
        "-crf", "-maxrate", "-bufsize", "-threads", "-movflags", "-metadata",
        "-user_agent", "-headers", "-referer", "-origin", "-timeout",
        "-map", "-filter_complex", "-g", "-keyint_min", "-sc_threshold",
        "-loglevel", "-hide_banner", "-stats", "-nostats",
    }
    skip_next = False
    last_file = None
    for p in parts:
        if skip_next:
            skip_next = False
            continue
        if p in _skip_flags:
            skip_next = True
            continue
        if p.startswith("-"):
            continue
        if p.startswith(("http://", "https://", "rtmp://", "rtsp://", "rtp://", "udp://")):
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext in _FF_OUT_EXTS:
            last_file = p
    return last_file


def _ffmpeg_running_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Gen Preview",      callback_data=f"ffpg:{task_id}:preview"),
            InlineKeyboardButton("🔄 Refresh Progress", callback_data=f"ffpg:{task_id}:refresh"),
        ],
        [InlineKeyboardButton("❌ Cancel",              callback_data=f"ffpg:{task_id}:cancel")],
    ])


def _ffmpeg_upload_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Telegram",          callback_data=f"ffup:{task_id}:tg"),
            InlineKeyboardButton("☁️ Google Drive",      callback_data=f"ffup:{task_id}:gd"),
        ],
        [InlineKeyboardButton("📤➕☁️ Upload to Both",   callback_data=f"ffup:{task_id}:both")],
        [InlineKeyboardButton("🔲 16:9 Crop (1280×720)", callback_data=f"ffup:{task_id}:169")],
        [InlineKeyboardButton("🗑️ Delete",               callback_data=f"ffup:{task_id}:del")],
    ])


async def _run_ffmpeg_task(client: Client, args_text: str, task_id: str) -> None:
    """Background coroutine: run ffmpeg, stream progress, handle result."""
    job        = ffmpeg_jobs[task_id]
    status_msg = job["status_msg"]
    duration   = job["duration"]
    out_file   = job.get("output_file")

    cur_time_sec = 0.0
    cur_size_kb  = 0

    try:
        proc = await asyncio.create_subprocess_shell(
            f"ffmpeg -y {args_text}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        job["proc"] = proc
        job["pid"]  = proc.pid

        loop_time   = asyncio.get_event_loop().time
        last_edit   = loop_time() - 12      # trigger first edit quickly
        UPDATE_SECS = 8

        stderr_tail: list[str] = []

        while True:
            try:
                raw = await asyncio.wait_for(proc.stderr.readline(), timeout=60)
            except asyncio.TimeoutError:
                break
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            stderr_tail.append(line)
            if len(stderr_tail) > 100:
                stderr_tail.pop(0)

            tm = _FF_TIME_RE.search(line)
            sz = _FF_SIZE_RE.search(line)
            if tm:
                cur_time_sec = _ff_hms_to_sec(tm.group(1))
                job["cur_time_sec"] = cur_time_sec
            if sz:
                cur_size_kb = int(sz.group(1))
                job["cur_size_kb"] = cur_size_kb

            if job.get("cancelled"):
                try:
                    proc.kill()
                except Exception:
                    pass
                break

            now = loop_time()
            if now - last_edit >= UPDATE_SECS:
                pct      = min(100.0, cur_time_sec / duration * 100) if duration > 0 else 0.0
                bar      = _ff_bar(pct)
                size_str = f"{cur_size_kb / 1024:.1f} MB" if cur_size_kb else "—"
                tot_str  = _ff_sec_to_hms(duration) if duration else "—"
                text = (
                    f"🎬 **FFmpeg Task Running...**\n\n"
                    f"📡 **Stream Capture**\n"
                    f"[{bar}] {pct:.0f}%\n\n"
                    f"⏱️ Time : `{_ff_sec_to_hms(cur_time_sec)}` / `{tot_str}`\n"
                    f"📦 Size : `{size_str}`\n"
                    f"🆔 Task : `{task_id}`"
                )
                try:
                    await status_msg.edit_text(text, reply_markup=_ffmpeg_running_kb(task_id))
                except Exception:
                    pass
                last_edit = now

        await proc.wait()
        rc = proc.returncode or 0
        job["done"]        = True
        job["rc"]          = rc
        job["stderr_tail"] = "".join(stderr_tail)

        if job.get("cancelled"):
            try:
                await status_msg.edit_text(f"❌ Task `{task_id}` was cancelled.", reply_markup=None)
            except Exception:
                pass
            return

        # ── Output file produced ───────────────────────────────────────────
        if rc == 0 and out_file and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
            size_mb  = os.path.getsize(out_file) / (1024 * 1024)
            dur_str  = _ff_sec_to_hms(cur_time_sec or duration)
            fname    = os.path.basename(out_file)
            text = (
                f"🎉 **FFmpeg Task Completed!**\n\n"
                f"📁 **File Name:** `{fname}`\n"
                f"📦 **Size:** `{size_mb:.1f} MB`\n"
                f"⏱️ **Duration:** `{dur_str}`\n\n"
                f"Choose upload option:"
            )
            try:
                await status_msg.edit_text(text, reply_markup=_ffmpeg_upload_kb(task_id))
            except Exception:
                pass

        # ── No file / error → show text output ────────────────────────────
        else:
            output = job["stderr_tail"].strip() or "(no output)"
            if len(output) > 3500:
                output = "…(trimmed)\n" + output[-3500:]
            icon = "✅ Done" if rc == 0 else f"❌ Exit code: {rc}"
            try:
                await status_msg.edit_text(
                    f"{icon}\n\n```\n{output}\n```",
                    parse_mode=None,
                    reply_markup=None,
                )
            except Exception:
                pass

    except Exception as exc:
        job["done"] = True
        try:
            await status_msg.edit_text(f"❌ FFmpeg error: `{exc}`", reply_markup=None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helper: auto-convert /rec-style shorthand to proper ffmpeg args
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


def _rec_to_ffmpeg_args(args_text: str):
    """Detect recording-style shorthand and convert to proper ffmpeg argument string.

    Shorthand format:
      /ffmpeg <url> [HH:MM:SS] [filename] [-cookie val] [-ua val] [-referer val]

    Bot-specific flags converted:
      -cookie   → -headers "Cookie: <val>\\r\\n"
      -ua       → -user_agent "<val>"
      -referer  → -headers "Referer: <val>\\r\\n"
      -aes      → -decryption_key <val>

    Returns (converted_args_str, notice_str) or (None, None) if not rec-style.
    """
    try:
        tokens = shlex.split(args_text)
    except ValueError:
        tokens = args_text.split()

    if not tokens or not tokens[0].startswith(("http://", "https://", "rtmp://", "rtmps://")):
        return None, None

    url  = tokens[0]
    rest = tokens[1:]

    # Optional: duration token (MM:SS or HH:MM:SS)
    duration = None
    if rest and _TS_RE.match(rest[0]):
        duration = rest[0]
        rest = rest[1:]

    # Optional: output filename (no - prefix, not a URL)
    filename = None
    if rest and not rest[0].startswith("-") and "://" not in rest[0]:
        filename = rest[0]
        rest = rest[1:]

    # Parse bot-specific flags from remaining tokens
    flags = _parse_rec_flags(rest)

    # Build input-side options (must appear before -i)
    input_opts: list[str] = []
    if flags.get("user_agent"):
        input_opts.append(f"-user_agent {shlex.quote(flags['user_agent'])}")

    header_parts: list[str] = []
    if flags.get("cookie"):
        header_parts.append(f"Cookie: {flags['cookie']}")
    if flags.get("referer"):
        header_parts.append(f"Referer: {flags['referer']}")
    if flags.get("origin"):
        header_parts.append(f"Origin: {flags['origin']}")
    if header_parts:
        hval = "\r\n".join(header_parts) + "\r\n"
        input_opts.append(f"-headers {shlex.quote(hval)}")

    if flags.get("aes_key"):
        input_opts.append(f"-decryption_key {shlex.quote(flags['aes_key'])}")

    # Output filename
    if not filename:
        filename = f"rec_{int(time.time())}"
    if "." not in os.path.basename(filename):
        filename += ".mp4"

    # Assemble final ffmpeg args
    parts = input_opts + [f"-i {shlex.quote(url)}"]
    if duration:
        parts.append(f"-t {duration}")
    parts += ["-c copy", shlex.quote(filename)]

    converted = " ".join(parts)
    notice    = (
        "ℹ️ **Recording shorthand detected — auto-converted to ffmpeg args:**\n"
        f"`{converted}`\n\n"
        "Running now…"
    )
    return converted, notice


# ---------------------------------------------------------------------------
# /ffmpeg command
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["ffmpeg", "Ffmpeg"]))
async def ffmpeg_cmd(client: Client, message: Message):
    """Owner/Admin only: run ffmpeg — task mode if output file detected, else shell mode."""
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Sirf Owner/Admin use kar sakte hain.")

    # Use raw message text so quoted strings and multi-line pastes are preserved
    raw = (message.text or message.caption or "").strip()
    # Strip the command prefix (/ffmpeg or /Ffmpeg + optional @botname)
    first_space = raw.find(" ")
    args_text = raw[first_space:].strip() if first_space != -1 else ""
    # Collapse backslash line-continuation markers (common in copy-pasted shell cmds)
    args_text = re.sub(r"\s*\\\s*", " ", args_text).strip()
    if not args_text:
        return await message.reply_text(
            "**Usage:** `/ffmpeg <arguments>`\n\n"
            "Examples (raw ffmpeg):\n"
            "`/ffmpeg -version`\n"
            "`/ffmpeg -i https://stream.m3u8 -t 00:02:10 -c copy output.mkv`\n\n"
            "Examples (recording shorthand — same as /rec flags):\n"
            "`/ffmpeg https://stream.m3u8 00:01:00 FileName -ua \"MyApp\" -cookie \"hdntl=...\" -referer https://...`"
        )

    # Auto-detect /rec-style shorthand (URL first, bot flags)
    converted, notice = _rec_to_ffmpeg_args(args_text)
    if converted:
        args_text = converted
        await message.reply_text(notice, parse_mode=enums.ParseMode.MARKDOWN)

    out_file = _detect_ff_output(args_text)
    duration = _parse_ff_duration(args_text)

    # ── Task mode (has output file) ─────────────────────────────────────────
    if out_file:
        task_id  = f"{uid % 10000:04d}{int(time.time()) % 100000}"
        save_dir = join(DOWNLOAD_DIRECTORY, f"ff_{task_id}")
        os.makedirs(save_dir, exist_ok=True)

        # Redirect output to save_dir if relative path
        if not os.path.isabs(out_file):
            out_path  = join(save_dir, os.path.basename(out_file))
            args_text = args_text.replace(out_file, out_path, 1)
        else:
            out_path = out_file

        tot_str    = _ff_sec_to_hms(duration) if duration else "—"
        status_msg = await message.reply_text(
            f"🎬 **FFmpeg Task Running...**\n\n"
            f"📡 **Stream Capture**\n"
            f"[{'░' * 10}] 0%\n\n"
            f"⏱️ Time : `00:00:00` / `{tot_str}`\n"
            f"📦 Size : `—`\n"
            f"🆔 Task : `{task_id}`",
            reply_markup=_ffmpeg_running_kb(task_id),
        )
        ffmpeg_jobs[task_id] = {
            "uid":          uid,
            "chat_id":      message.chat.id,
            "status_msg":   status_msg,
            "output_file":  out_path,
            "save_dir":     save_dir,
            "duration":     duration,
            "done":         False,
            "cancelled":    False,
            "proc":         None,
            "cur_time_sec": 0.0,
            "cur_size_kb":  0,
        }
        asyncio.create_task(_run_ffmpeg_task(client, args_text, task_id))

    # ── Simple shell mode (-version, -formats, -h, etc.) ───────────────────
    else:
        wait_msg = await message.reply_text("⚙️ Running ffmpeg...")
        try:
            rc, stdout, stderr = await asyncio.wait_for(
                runcmd(f"ffmpeg {args_text}"), timeout=300
            )
            # Prefer stdout (help/info output); fall back to stderr; combine both
            # stdout first so -h / -formats / -protocols content is not lost
            out_part = stdout.strip()
            err_part = stderr.strip()
            if out_part and err_part:
                output = out_part + "\n\n--- stderr ---\n" + err_part
            elif out_part:
                output = out_part
            else:
                output = err_part
            output = output or "(no output)"

            icon = "✅ Done" if rc == 0 else f"❌ Exit code: {rc}"

            if len(output) <= 3800:
                await wait_msg.edit_text(
                    f"{icon}\n\n```\n{output}\n```", parse_mode=None
                )
            else:
                # Output too large for a message — send as downloadable file
                tmp_out = join(DOWNLOAD_DIRECTORY, f"ffmpeg_out_{int(time.time())}.txt")
                try:
                    os.makedirs(DOWNLOAD_DIRECTORY, exist_ok=True)
                    with open(tmp_out, "w", encoding="utf-8") as _f:
                        _f.write(f"# ffmpeg {args_text}\n# {icon}\n\n")
                        _f.write(output)
                    preview = output[:800].strip()
                    await wait_msg.edit_text(
                        f"{icon}\n\n_(Output too large — sending as file)_\n\n"
                        f"**Preview (first 800 chars):**\n```\n{preview}\n…\n```",
                        parse_mode=None,
                    )
                    await message.reply_document(
                        tmp_out,
                        caption=f"{icon} — full ffmpeg output ({len(output):,} chars)",
                        file_name="ffmpeg_output.txt",
                    )
                finally:
                    try:
                        os.remove(tmp_out)
                    except Exception:
                        pass
        except asyncio.TimeoutError:
            await wait_msg.edit_text("⏰ Timeout (5 min) — command took too long.")
        except Exception as e:
            await wait_msg.edit_text(f"❌ Error: `{e}`")


@app.on_message(filters.command(["ffprobe", "Ffprobe"]))
async def ffprobe_cmd(client: Client, message: Message):
    """Owner/Admin only: run ffprobe with given arguments and return output."""
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Sirf Owner/Admin use kar sakte hain.")

    raw = (message.text or message.caption or "").strip()
    first_space = raw.find(" ")
    args_text = raw[first_space:].strip() if first_space != -1 else ""
    args_text = re.sub(r"\s*\\\s*", " ", args_text).strip()
    if not args_text:
        return await message.reply_text(
            "**Usage:** `/ffprobe <arguments>`\n\n"
            "Example:\n"
            "`/ffprobe -version`\n"
            "`/ffprobe -v error -show_entries format=duration -i video.mp4`\n"
            "`/ffprobe -user_agent \"Mozilla/5.0\" -i https://example.com/live.m3u8`"
        )

    wait_msg = await message.reply_text("🔍 Running ffprobe...")
    try:
        rc, stdout, stderr = await asyncio.wait_for(
            runcmd(f"ffprobe {args_text}"), timeout=120
        )
        output = (stdout + stderr).strip() or "(no output)"
        if len(output) > 3800:
            output = "…(trimmed)\n" + output[-3800:]
        icon = "✅ Done" if rc == 0 else f"❌ Exit code: {rc}"
        await wait_msg.edit_text(f"{icon}\n\n```\n{output}\n```", parse_mode=None)
    except asyncio.TimeoutError:
        await wait_msg.edit_text("⏰ Timeout (2 min) — command took too long.")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Error: `{e}`")


# ---------------------------------------------------------------------------
# FFmpeg progress & upload callbacks
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^ffpg:\w+:(refresh|cancel|preview)$"))
async def ffmpeg_progress_cb(client: Client, cq: CallbackQuery):
    parts   = cq.data.split(":")
    task_id = parts[1]
    action  = parts[2]
    job     = ffmpeg_jobs.get(task_id)

    if not job:
        return await cq.answer("❌ Task not found or already expired.", show_alert=True)
    if cq.from_user.id != job["uid"]:
        return await cq.answer("Not your task.", show_alert=True)

    if action == "cancel":
        job["cancelled"] = True
        proc = job.get("proc")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return await cq.answer("❌ Cancelling…", show_alert=False)

    if action == "refresh":
        if job.get("done"):
            return await cq.answer("Task already finished.", show_alert=False)
        dur          = job.get("duration", 0)
        cur_time_sec = job.get("cur_time_sec", 0.0)
        cur_size_kb  = job.get("cur_size_kb", 0)
        pct          = min(100.0, cur_time_sec / dur * 100) if dur > 0 else 0.0
        bar          = _ff_bar(pct)
        size_str     = f"{cur_size_kb / 1024:.1f} MB" if cur_size_kb else "—"
        tot_str      = _ff_sec_to_hms(dur) if dur else "—"
        text = (
            f"🎬 **FFmpeg Task Running...**\n\n"
            f"📡 **Stream Capture**\n"
            f"[{bar}] {pct:.0f}%\n\n"
            f"⏱️ Time : `{_ff_sec_to_hms(cur_time_sec)}` / `{tot_str}`\n"
            f"📦 Size : `{size_str}`\n"
            f"🆔 Task : `{task_id}`"
        )
        try:
            await cq.message.edit_text(text, reply_markup=_ffmpeg_running_kb(task_id))
        except Exception:
            pass
        return await cq.answer("🔄 Refreshed!", show_alert=False)

    if action == "preview":
        out_file = job.get("output_file")
        if not out_file or not os.path.exists(out_file):
            return await cq.answer("⚠️ No output file yet for preview.", show_alert=True)
        await cq.answer("🎬 Generating preview…")
        save_dir     = job.get("save_dir", DOWNLOAD_DIRECTORY)
        preview_path = join(save_dir, "preview.jpg")
        rc, _, _ = await runcmd(
            f"ffmpeg -y -hide_banner -loglevel error "
            f"-sseof -30 -i {shlex.quote(out_file)} "
            f"-vframes 1 -q:v 2 {shlex.quote(preview_path)}"
        )
        if rc == 0 and os.path.exists(preview_path):
            try:
                await client.send_photo(
                    job["chat_id"],
                    photo=preview_path,
                    caption=f"🎬 Preview — Task `{task_id}`",
                )
            except Exception as e:
                await cq.answer(f"Send failed: {e}", show_alert=True)
        else:
            await cq.answer("⚠️ Could not generate preview.", show_alert=True)


@app.on_callback_query(filters.regex(r"^ffup:\w+:(tg|gd|both|del|169)$"))
async def ffmpeg_upload_cb(client: Client, cq: CallbackQuery):
    parts   = cq.data.split(":")
    task_id = parts[1]
    dest    = parts[2]
    job     = ffmpeg_jobs.get(task_id)

    if not job:
        return await cq.answer("❌ Task not found or already expired.", show_alert=True)
    if cq.from_user.id != job["uid"]:
        return await cq.answer("Not your task.", show_alert=True)

    out_file = job.get("output_file")
    save_dir = job.get("save_dir", DOWNLOAD_DIRECTORY)
    uid      = job["uid"]
    chat_id  = job["chat_id"]

    if dest == "del":
        ffmpeg_jobs.pop(task_id, None)
        if save_dir and os.path.exists(save_dir):
            import shutil as _shutil
            _shutil.rmtree(save_dir, ignore_errors=True)
        await cq.answer("🗑️ Deleted.", show_alert=False)
        try:
            await cq.message.edit_text("🗑️ File deleted.", reply_markup=None)
        except Exception:
            pass
        return

    if not out_file or not os.path.exists(out_file):
        return await cq.answer("⚠️ Output file not found.", show_alert=True)

    # ── 16:9 crop/scale ──────────────────────────────────────────────────────
    if dest == "169":
        await cq.answer("🔲 Starting 16:9 crop…")
        status_msg = cq.message
        fname_169  = f"16x9_{int(time.time())}.mp4"
        out_169    = join(save_dir, fname_169)
        new_tid    = f"{uid % 10000:04d}{int(time.time()) % 100000}"
        try:
            await status_msg.edit_text(
                f"🔲 **16:9 Crop Running...**\n\n"
                f"[{'░' * 10}] 0%\n\n"
                f"⏱️ Time : `00:00:00` / `—`\n"
                f"📦 Size : `—`\n"
                f"🆔 Task : `{new_tid}`",
                reply_markup=_ffmpeg_running_kb(new_tid),
            )
        except Exception:
            pass
        dur_sec = int(job.get("cur_time_sec") or job.get("duration", 0))
        ffmpeg_jobs[new_tid] = {
            "uid":          uid,
            "chat_id":      chat_id,
            "status_msg":   status_msg,
            "output_file":  out_169,
            "save_dir":     save_dir,
            "duration":     dur_sec,
            "done":         False,
            "cancelled":    False,
            "proc":         None,
            "cur_time_sec": 0.0,
            "cur_size_kb":  0,
        }
        crop_args = (
            f'-i {shlex.quote(out_file)} '
            f'-vf "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720" '
            f'-c:v libx264 -preset medium -crf 20 '
            f'-c:a copy -movflags +faststart '
            f'{shlex.quote(out_169)}'
        )
        asyncio.create_task(_run_ffmpeg_task(client, crop_args, new_tid))
        return

    if dest in ("gd", "both"):
        if not (_gdrive_is_enabled() or is_user_connected(uid)):
            return await cq.answer(
                "☁️ Google Drive linked nahi hai!\n/DriveAuth se connect karein.",
                show_alert=True,
            )

    await cq.answer("⬆️ Uploading…")
    status_msg = cq.message
    try:
        await status_msg.edit_text("⬆️ Uploading… please wait.", reply_markup=None)
    except Exception:
        pass

    fname   = os.path.basename(out_file)
    dur_sec = int(job.get("cur_time_sec") or job.get("duration", 0))

    if dest in ("tg", "both"):
        upload_start = time.time()
        try:
            await split_and_send_video(
                status_msg, out_file,
                caption=f"🎬 **{BRAND_TITLE}**\n📁 `{fname}`",
                duration=dur_sec,
                thumb_path=None,
                status_msg=status_msg,
                progress=progress_for_pyrogram,
                progress_args=(status_msg, upload_start, status_msg, save_dir, False),
                _uid=uid, _chat_id=status_msg.chat.id,
            )
        except Exception as ue:
            try:
                await status_msg.edit_text(f"❌ Telegram upload failed: `{ue}`")
            except Exception:
                pass

    if dest in ("gd", "both"):
        await upload_and_notify(client, uid, out_file, fname)

    schedule_retention_cleanup(save_dir)
    ffmpeg_jobs.pop(task_id, None)


# ---------------------------------------------------------------------------
# /approve, /revoke, /pending — owner commands
# ---------------------------------------------------------------------------

@app.on_message(filters.command("approve") & AUTH)
async def approve_cmd(client: Client, message: Message):
    if not _is_control_actor(message):
        return await message.reply_text("Owner-only command.")
    parts = message.command
    if len(parts) < 2:
        return await message.reply_text("Usage: `/approve <user_id> [days]`")
    try:
        uid  = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return await message.reply_text("Invalid user_id or days.")
    data  = load_verified()
    entry = {"approved_by": _message_actor_id(message), "approved_at": datetime.now(tz).isoformat()}
    if days > 0:
        entry["expires_at"] = (datetime.now(tz) + timedelta(days=days)).isoformat()
        label = f"{days} days"
    else:
        label = "permanent"
    data.setdefault("verified", {})[str(uid)] = entry
    data.get("pending", {}).pop(str(uid), None)
    save_verified(data)
    await message.reply_text(f"✅ User `{uid}` approved ({label}).")
    try:
        await client.send_message(uid, f"✅ You are now verified ({label}). Use /rec to start.")
    except Exception:
        pass


@app.on_message(filters.command("revoke") & AUTH)
async def revoke_cmd(client: Client, message: Message):
    if not _is_control_actor(message):
        return await message.reply_text("Owner-only command.")
    parts = message.command
    if len(parts) < 2:
        return await message.reply_text("Usage: `/revoke <user_id>`")
    try:
        uid = int(parts[1])
    except ValueError:
        return await message.reply_text("Invalid user_id.")
    data = load_verified()
    removed = data.get("verified", {}).pop(str(uid), None)
    save_verified(data)
    if removed:
        await message.reply_text(f"✅ Revoked access for `{uid}`.")
    else:
        await message.reply_text(f"User `{uid}` was not verified.")


@app.on_message(filters.command("pending") & AUTH)
async def pending_cmd(client: Client, message: Message):
    if not _is_control_actor(message):
        return await message.reply_text("Owner-only command.")
    data    = load_verified()
    pending = data.get("pending", {})
    if not pending:
        return await message.reply_text("No pending verification requests.")
    lines = ["**Pending Verification Requests**\n"]
    for uid, info in pending.items():
        lines.append(f"• `{uid}` — {info.get('name', 'N/A')} @{info.get('username', 'N/A')}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /broadcast — owner/admin: send a message to all verified users
# ---------------------------------------------------------------------------

@app.on_message(filters.command("broadcast") & AUTH)
async def broadcast_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return

    # Resolve broadcast text: either inline argument or a replied-to message
    replied = message.reply_to_message
    bcast_text: str = ""
    bcast_media_msg = None

    if replied:
        bcast_media_msg = replied          # forward the original message
        bcast_text      = replied.text or replied.caption or ""
    else:
        parts = message.command
        if len(parts) < 2:
            return await message.reply_text(
                "**Usage:**\n"
                "`/broadcast <your message>` — send text\n"
                "Or **reply** to any message with `/broadcast` to forward it."
            )
        bcast_text = " ".join(parts[1:])

    # Collect all known user IDs from verified.json + user_limits.json
    vdata   = load_verified()
    targets = set(int(k) for k in vdata.get("verified", {}).keys())

    # The quota helpers are merged into this module; keep broadcast usable
    # when the original limit_system.py companion file is not present.
    ls_data = _load()
    targets |= set(int(k) for k in ls_data.keys())

    targets.discard(uid)          # don't send to sender
    total = len(targets)

    if total == 0:
        return await message.reply_text("No users to broadcast to.")

    status_msg = await message.reply_text(
        f"📢 **Broadcasting to {total} users…**\n\n"
        "⏳ Please wait…"
    )

    sent = failed = blocked = 0
    UPDATE_EVERY = max(1, total // 10)   # update progress every ~10%

    for i, target_uid in enumerate(targets, 1):
        try:
            if bcast_media_msg:
                await bcast_media_msg.forward(target_uid)
            else:
                await client.send_message(target_uid, bcast_text)
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err or "forbidden" in err:
                blocked += 1
            else:
                failed += 1

        # Rate-limit: ~20 msg/s to stay under Telegram limits
        await asyncio.sleep(0.05)

        if i % UPDATE_EVERY == 0 or i == total:
            try:
                await status_msg.edit_text(
                    f"📢 **Broadcasting…** {i}/{total}\n\n"
                    f"✅ Sent: {sent}  |  🚫 Blocked: {blocked}  |  ❌ Failed: {failed}"
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"📢 **Broadcast Complete!**\n\n"
        f"👥 Total targeted : {total}\n"
        f"✅ Sent           : {sent}\n"
        f"🚫 Blocked/left   : {blocked}\n"
        f"❌ Other errors   : {failed}"
    )


# ---------------------------------------------------------------------------
# /stats — owner/admin: bot statistics + new users last 3 days
# ---------------------------------------------------------------------------

@app.on_message(filters.command("stats") & AUTH)
async def stats_cmd(_, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return

    now_ts  = time.time()
    now_dt  = datetime.fromtimestamp(now_ts, tz)
    THREE_DAYS_AGO = now_ts - 3 * 86400

    # ── Limit-system data ────────────────────────────────────────────────────
    ls_data      = _load()
    total_users  = len(ls_data)
    lucky_count  = sum(1 for u in ls_data.values() if u.get("is_lucky"))
    has_credits  = sum(1 for u in ls_data.values() if u.get("rec_limit", 0) > 0)

    # Group new users (joined_at present) by calendar day — last 3 days only
    day_counts: dict = {}   # "DD Mon YYYY" -> count
    new_total = 0
    for user_rec in ls_data.values():
        jt = user_rec.get("joined_at")
        if jt and jt >= THREE_DAYS_AGO:
            day_label = datetime.fromtimestamp(jt, tz).strftime("%d %b %Y")
            day_counts[day_label] = day_counts.get(day_label, 0) + 1
            new_total += 1

    # ── Verified.json data ────────────────────────────────────────────────────
    vdata          = load_verified()
    verified_count = len(vdata.get("verified", {}))
    pending_count  = len(vdata.get("pending", {}))

    # ── Active recordings ────────────────────────────────────────────────────
    active_recs = len(user_tasks)

    # ── Admins ────────────────────────────────────────────────────────────────
    admin_count = len(load_admins())

    # ── Build message ─────────────────────────────────────────────────────────
    lines = [
        "📊 **BOT STATISTICS**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👥 **Total Users**     : {total_users}",
        f"✅ **Verified**        : {verified_count}",
        f"⏳ **Pending Verify**  : {pending_count}",
        f"🎰 **Lucky Users**     : {lucky_count}",
        f"🎬 **Active Recs**     : {active_recs}",
        f"💳 **Users with Rec>0**: {has_credits}",
        f"🛡️ **Admins**          : {admin_count}",
        "",
        f"📅 **New Users — Last 3 Days** ({new_total} total):",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if day_counts:
        # Sort newest first
        for day_label in sorted(day_counts, reverse=True):
            count = day_counts[day_label]
            bar   = "█" * min(count, 20)
            lines.append(f"  📆 {day_label}  —  **{count}** user{'s' if count != 1 else ''}  {bar}")
    else:
        lines.append("  No new users in the last 3 days.")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⏰ {now_dt.strftime('%d-%m-%Y %I:%M %p IST')}",
    ]

    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /plan
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["plan", "plans"]) & AUTH)
async def plan_cmd(client: Client, message: Message):
    await message.reply_text(render_plans_text(), disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /contact
# ---------------------------------------------------------------------------

@app.on_message(filters.command("contact") & AUTH)
async def contact_cmd(client: Client, message: Message):
    await message.reply_text(
        f"📬 **Contact & Support**\n\n"
        f"💬 Support: @{SUPPORT_USERNAME}\n\n"
        f"For subscriptions, custom requests, or issues — reach us on the channel or DM support."
    )


# ---------------------------------------------------------------------------
# /channel — browse hidden-URL channel list (names only)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("channel") & AUTH)
async def channel_cmd(client: Client, message: Message):
    ok = await _refresh_channel_playlist()
    if not ok:
        return await message.reply_text(f"❌ Playlist load failed: `{CHANNEL_PLAYLIST_LAST_ERROR}`")
    await message.reply_text("**Browse channels**\n\nPick a channel group:", reply_markup=_channel_root_kb())


@app.on_callback_query(filters.regex(r"^chgrp:(\d+)$"))
async def cb_channel_group(client: Client, cq: CallbackQuery):
    groups = list(CHANNEL_GROUPS)
    pos = int(cq.data.split(":", 1)[1])
    if pos >= len(groups):
        return await cq.answer("Group not found.", show_alert=True)
    group = groups[pos]
    await cq.answer()
    await cq.message.edit_text(f"📁 **{group}**\n\nPick a channel:", reply_markup=_channel_group_kb(group))


@app.on_callback_query(filters.regex(r"^chback$"))
async def cb_channel_back(client: Client, cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text("**Browse channels**\n\nPick a channel group:", reply_markup=_channel_root_kb())


@app.on_callback_query(filters.regex(r"^ch:(\d+)$"))
async def cb_channel_select(client: Client, cq: CallbackQuery):
    item = CHANNEL_ENTRIES.get(cq.data.split(":", 1)[1])
    if not item:
        return await cq.answer("Channel not found. Open /channel again to refresh.", show_alert=True)
    name = item["name"]
    alias = _channel_command_alias(name)
    await cq.answer(f"✅ {name} selected!")
    sent = await cq.message.reply_text(
        f"📺 **{name}** selected!\n\n"
        f"Record command:\n`/Rec {alias} 00:00:30`\n`/REC {alias.upper()} 00:00:30`\n\n"
        f"Example: `/Rec {alias} 00:00:30`"
    )
    # Channel-selection instructions are temporary bot messages.
    _schedule_message_delete(sent, 5 * 60)


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------

@app.on_message(filters.command("search") & AUTH)
async def search_cmd(client: Client, message: Message):
    # Always load the current GitHub playlist. Never use stale/static channels.
    ok = await _refresh_channel_playlist()
    if not ok:
        return await message.reply_text(
            f"❌ Playlist load failed: `{CHANNEL_PLAYLIST_LAST_ERROR}`"
        )

    if len(message.command) < 2:
        return await message.reply_text("Usage: `/search Pogo`")

    query = " ".join(message.command[1:]).strip()
    if not query:
        return await message.reply_text("Usage: `/search Pogo`")

    q = query.casefold()
    matches = []
    for entry_id, item in CHANNEL_ENTRIES.items():
        if q in item["name"].casefold():
            matches.append((entry_id, item["name"]))

    if not matches:
        return await message.reply_text(f"❌ No channels found for `{query}`.")

    # Search results are selectable. The stream URL/User-Agent are never shown.
    rows = []
    for entry_id, name in matches[:50]:
        alias = _channel_command_alias(name)
        rows.append([InlineKeyboardButton(
            f"📺 {name}", callback_data=f"sch:{entry_id}"
        )])

    text = f"🔍 **Search results for: `{query}`**\n\n"
    text += "Select a channel below:"
    if len(matches) > 50:
        text += f"\n\n…and {len(matches) - 50} more."
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


@app.on_callback_query(filters.regex(r"^sch:(\d+)$"))
async def cb_search_channel_select(client: Client, cq: CallbackQuery):
    entry_id = cq.data.split(":", 1)[1]
    item = CHANNEL_ENTRIES.get(entry_id)
    if not item:
        return await cq.answer(
            "Channel not found. Run /search again to refresh.",
            show_alert=True,
        )

    name = item["name"]
    alias = _channel_command_alias(name)
    await cq.answer(f"✅ {name} selected!")
    sent = await cq.message.reply_text(
        f"📺 **{name}** selected!\n\n"
        f"Record command:\n"
        f"`/Rec {alias} 00:00:30`\n"
        f"`/REC {alias.upper()} 00:00:30`\n\n"
        f"Example: `/Rec {alias} 00:00:30`"
    )
    # Search-selection instructions are temporary bot messages too.
    _schedule_message_delete(sent, 5 * 60)


# ---------------------------------------------------------------------------
# /statusme / /cancelme
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["statusme", "status"]) & AUTH)
async def statusme_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    lines = ["📊 **Active Jobs**"]
    found = False

    # ---------------------------------------------------------------
    # /Rec / /dRec
    # ---------------------------------------------------------------
    recs = active_recs.get(user_id, {})
    if recs:
        found = True
        for i, (rid, entry) in enumerate(recs.items(), 1):
            st = entry.get("status", {}) or {}
            filename = st.get("filename", "?")
            progress = st.get("progress", "00:00:00")
            target = st.get("target", "?")

            lines.append(
                f"\n🔴 **/Rec /dRec**\n"
                f"📺 `{filename}`\n"
                f"⏱️ `{progress}` / `{target}`"
            )

    # ---------------------------------------------------------------
    # Normal user_status jobs:
    # /download /compress /compressadvance /trim /merge
    # /watermark and other jobs using the common status registry
    # ---------------------------------------------------------------
    other = user_status.get(user_id)

    if other:
        found = True

        channel = str(other.get("channel_name") or "").strip()
        filename = other.get("filename") or "?"
        progress = other.get("progress") or "0%"
        duration = other.get("duration_str") or ""

        job_map = {
            "OTT": ("⬇️", "/download", "Downloading"),
            "Compress": ("🗜️", "/compress", "Compressing"),
            "CompressAdvance": ("🗜️", "/compressadvance", "Compressing"),
            "Trim": ("✂️", "/trim", "Processing"),
            "Merge": ("🧩", "/merge", "Processing"),
            "Watermark": ("🎨", "/watermark", "Processing"),
            "Screenshots": ("📸", "/screenshot", "Processing"),
            "Title": ("🔤", "/title", "Processing"),
        }

        emoji, command, state_text = job_map.get(
            channel,
            ("⚙️", f"/{channel.lower()}" if channel else "/job", "Processing")
        )

        extra = f"\n⏱️ `{duration}`" if duration and duration != "—" else ""

        lines.append(
            f"\n{emoji} **{command}**\n"
            f"📁 `{filename}`\n"
            f"🔄 {state_text}\n"
            f"📊 `{progress}`"
            f"{extra}"
        )

    # ---------------------------------------------------------------
    # Dedicated multi-job registry
    # Keep /statusme consistent with the quota used by _six_acquire().
    # Avoid hiding jobs that are tracked for quota but not in user_status.
    # ---------------------------------------------------------------
    try:
        _six_prune_finished()
        _six_entries = [
            (_jid, _info)
            for _jid, _info in list(_six_active_jobs.items())
            if int(_info.get("user_id", 0)) == int(user_id)
        ]
    except Exception:
        _six_entries = []

    for _jid, _info in _six_entries:
        _kind = str(_info.get("type") or "job")
        _task = _info.get("task")
        _state = "Running" if _task is not None and not _task.done() else "Cleaning up"
        found = True
        lines.append(
            f"\n⚙️ **/{_kind}**\n"
            f"🔄 {_state}\n"
            f"🆔 `{_jid.split(':')[-1][:8]}`"
        )

    # ---------------------------------------------------------------
    # /audiotrack
    #
    # Older code only sets user_tasks=True, so detect its active
    # download/process folder indirectly through user_tasks.
    # Do NOT duplicate a job already represented by user_status.
    # ---------------------------------------------------------------
    if user_id in user_tasks and not other and not recs:
        found = True
        lines.append(
            "\n🎵 **/audiotrack**\n"
            "📁 Audio track job\n"
            "🔄 Processing"
        )

    # ---------------------------------------------------------------
    # /ffmpeg
    # ffmpeg_jobs uses task IDs instead of user_status.
    # ---------------------------------------------------------------
    ff_jobs = []
    try:
        for task_id, job in list(ffmpeg_jobs.items()):
            if job.get("uid") != user_id:
                continue
            if job.get("done"):
                continue
            ff_jobs.append((task_id, job))
    except Exception:
        ff_jobs = []

    for task_id, job in ff_jobs:
        found = True

        duration = float(job.get("duration") or 0)
        cur = float(job.get("cur_time_sec") or 0)
        pct = (cur / duration * 100) if duration > 0 else 0
        pct = max(0, min(100, pct))

        filename = os.path.basename(
            job.get("output_file") or "ffmpeg_output"
        )

        lines.append(
            f"\n⚙️ **/ffmpeg**\n"
            f"📁 `{filename}`\n"
            f"🔄 Processing\n"
            f"📊 `{pct:.1f}%`\n"
            f"🆔 `{task_id}`"
        )

    if not found:
        return await message.reply_text("No active recording or job.")

    lines.append("\n\nUse `/cancelme` to stop active jobs.")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command(["cancelme", "cancel"]) & AUTH)
async def cancelme_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    recs    = active_recs.get(user_id, {})
    has_other = user_id in user_tasks or user_id in reclink_jobs

    # Also include the dedicated multi-job registry.  /audiotrack, /compress,
    # /merge, etc. acquire a _six_* slot, but the old /cancelme guard only
    # checked user_tasks/reclink_jobs.  That could leave a stale dedicated
    # job visible in /statusme while /cancelme said there was nothing to do.
    six_job_ids = set(_six_user_active.get(int(user_id), set()))
    has_six = bool(six_job_ids)

    if not recs and not has_other and not has_six:
        return await message.reply_text("No active recording or job to cancel.")

    import psutil as _psutil

    # Cancel all active /rec recordings
    for rid, entry in list(recs.items()):
        cancelled_recs.add((user_id, rid))
        pid = entry.get("ffmpeg_pid")
        if pid:
            try:
                _psutil.Process(pid).terminate()
                LOG.info(f"Sent SIGTERM to FFmpeg pid={pid} rec={rid}")
            except Exception as e:
                LOG.warning(f"Could not terminate FFmpeg pid={pid}: {e}")
        pt = entry.get("progress_task")
        if pt:
            pt.cancel()

    # Cancel other job types (OTT download, compress, trim, etc.)
    if user_id in user_tasks:
        cancelled_users.add(user_id)
        pid = user_ffmpeg_pids.get(user_id)
        if pid:
            try:
                _psutil.Process(pid).terminate()
            except Exception as e:
                LOG.warning(f"Could not terminate FFmpeg pid={pid}: {e}")
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
        # If the worker already died, its finally block may never have had a
        # chance to clear this guard.  Remove the stale in-memory marker now.
        user_tasks.pop(user_id, None)

    rl = reclink_jobs.get(user_id)
    if rl and rl.get("task"):
        rl["task"].cancel()

    # Cancel every Hotstar wizard/recording/upload belonging to this user.
    # Hotstar jobs are keyed by (user_id, status_message_id).
    for _hs_key, _hs_task in list(HOTSTAR_ACTIVE_TASKS.items()):
        if isinstance(_hs_key, tuple) and _hs_key[0] == user_id:
            HOTSTAR_ACTIVE_TASKS.pop(_hs_key, None)
            if _hs_task and not _hs_task.done():
                _hs_task.cancel()
    _hotstar_pop_user_sessions(user_id)

    # Release every dedicated _six_* slot owned by this user.  This is
    # especially important for a job such as /audiotrack whose handler may
    # have exited abnormally but whose registry entry is still present.
    for _jid in list(six_job_ids):
        try:
            _six_release(_jid)
        except Exception as _exc:
            LOG.warning("Could not release dedicated job %s: %s", _jid, _exc)

    count = len(recs)
    note  = f"{count} recording(s)" if count else "job"
    await message.reply_text(
        f"⏹ **Cancel signal sent** ({note}).\n\nIf files were partially recorded they will be uploaded now."
    )


# ---------------------------------------------------------------------------
# /rec — pre-recording setup wizard entry
# ---------------------------------------------------------------------------

def _channel_name_key(name: str) -> tuple:
    """Normalize channel names for command matching.

    This makes names such as `Pogo 01`, `POGO.01`, `Pogo-1`, and `pogo_1`
    resolve to the same playlist entry while keeping the original playlist
    name unchanged in the UI. Numeric parts are normalized so leading zeroes
    do not matter.
    """
    parts = re.findall(r"[A-Za-z0-9]+", (name or "").casefold())
    out = []
    for part in parts:
        if part.isdigit():
            out.append(str(int(part)))
        else:
            out.append(part)
    return tuple(out)


def _resolve_playlist_channel_name(source_name: str):
    """Return the real playlist channel name for a flexible user alias."""
    key = _channel_name_key(source_name)
    if not key:
        return None
    # Prefer exact case-insensitive playlist names first.
    for name in BOT_CHANNELS:
        if (name or "").casefold() == (source_name or "").casefold():
            return name
    for name in BOT_CHANNELS:
        if _channel_name_key(name) == key:
            return name
    return None


def _resolve_channel_prefix(message_text: str) -> str:
    """Resolve a playlist channel name/alias to its hidden stream source.

    Matching is case-insensitive and punctuation/spacing tolerant, so
    `/Rec Pogo.1 00:00:30` and `/REC POGO.01 00:00:30` can resolve a playlist
    entry named `Pogo 01`. The URL/User-Agent properties remain internal.
    """
    space = message_text.find(" ")
    if space == -1:
        return message_text
    cmd_part = message_text[:space]
    rest = message_text[space + 1:].strip()
    if not rest:
        return message_text

    # Only the first argument is the channel name; duration/filename remain.
    parts = rest.split(None, 1)
    source_name = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""
    real_name = _resolve_playlist_channel_name(source_name)
    if not real_name:
        return message_text

    return f"{cmd_part} {BOT_CHANNELS[real_name]} {remainder}".rstrip()


def _channel_command_alias(name: str) -> str:
    """Human-friendly command alias, e.g. `Pogo 01` -> `Pogo.1`."""
    parts = re.findall(r"[A-Za-z0-9]+", (name or "").strip())
    if not parts:
        return name
    cleaned = []
    for part in parts:
        if part.isdigit():
            part = str(int(part))
        cleaned.append(part)
    return ".".join(cleaned)


def _shlex_parse_rec(message_text: str):
    """Return (url, timestamp, filename, flags_dict) from a /rec or /drec message text.
    Uses shlex so quoted values with spaces work correctly.

    Supports two formats:
      Standard:  /rec <url> HH:MM:SS [filename] [flags...]
      ffprobe:   /rec -user_agent "..." -headers "..." -i <url> -t HH:MM:SS [filename]
    """
    # Strip leading /command word (or 'ffprobe' if user pastes raw terminal command)
    space = message_text.find(" ")
    rest  = message_text[space:].strip() if space != -1 else ""

    # Handle $'...' ANSI-C quoting from shell: $'Cookie: val\r\nOrigin: val\r\n'
    # Convert to a regular single-quoted string with literal \r\n for our parser
    rest = re.sub(
        r"\$'((?:[^'\\]|\\.)*)'",
        lambda m: "'" + m.group(1) + "'",
        rest,
    )

    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()

    if not tokens:
        return None, None, None, {}

    # ── ffprobe/ffmpeg style: first token starts with '-' ────────────────────
    if tokens[0].startswith("-"):
        flags        = _parse_rec_flags(tokens)
        url          = flags.pop("input_url", None)
        if url:
            url, stream_props = _split_stream_properties(url)
            if stream_props.get("user-agent") and not flags.get("user_agent"):
                flags["user_agent"] = stream_props["user-agent"]
        timestamp    = flags.pop("timestamp", None)
        raw_filename = flags.pop("filename", DEFAULT_FILENAME)
        if not url or not timestamp:
            return None, None, None, {}
        return url, timestamp, raw_filename, flags

    # ── Standard format: url timestamp [filename] [flags...] ─────────────────
    if len(tokens) < 2:
        return None, None, None, {}
    url       = tokens[0]
    url, stream_props = _split_stream_properties(url)
    timestamp = tokens[1]
    idx       = 2
    raw_filename = DEFAULT_FILENAME
    if idx < len(tokens) and not tokens[idx].startswith("-"):
        raw_filename = tokens[idx]
        idx = 3
    flags = _parse_rec_flags(tokens[idx:])
    if stream_props.get("user-agent") and not flags.get("user_agent"):
        flags["user_agent"] = stream_props["user-agent"]
    # -i in trailing flags overrides positional URL
    if flags.get("input_url"):
        url = flags.pop("input_url")
    # -t in trailing flags overrides positional timestamp
    if flags.get("timestamp"):
        timestamp = flags.pop("timestamp")
    return url, timestamp, raw_filename, flags


def _build_setup(user_id: int, message, url: str, timestamp: str,
                  raw_filename: str, flags: dict) -> dict:
    """Build a do_record setup dict from parsed URL, timestamp, filename, and flags."""
    for bad in '/\\:*?"<>|':
        raw_filename = raw_filename.replace(bad, "_")
    return {
        "user_id":               user_id,
        "chat_id":               message.chat.id,
        "orig_msg":              message,
        "url":                   url,
        "timestamp":             timestamp,
        "duration_sec":          time_to_seconds(timestamp),
        "filename":              raw_filename,
        "watermark_on":          False,
        "watermark_pos":         "bottom_center",
        "watermark_size":        150,
        "watermark_preset":      "Watermark 1",
        "watermark_text":        get_default_watermark(),
        "watermark_times":       [],
        "audio_track":           [],
        "auto_mode":             False,
        "quality":               "original",
        "aspect":                "none",
        "step":                  -1,
        "detected_audio_tracks": [],
        "detected_video_dimensions": [],
        "detected_video_streams": [],
        "video_stream_index":     None,
        # Inline flags
        "aes_key":      flags.get("aes_key", ""),
        "flag_cookie":  flags.get("cookie", ""),
        "flag_ua":      flags.get("user_agent", ""),
        "flag_referer": flags.get("referer", ""),
        "flag_origin":  flags.get("origin", ""),
        "license_url":  flags.get("license_url", ""),
        "drm_scheme":   flags.get("drm_scheme", ""),
    }


@app.on_message(filters.command("rec"))
async def rec_cmd(client: Client, message: Message):
    print(
        "REC DEBUG:",
        "chat_id=", getattr(getattr(message, "chat", None), "id", None),
        "chat_type=", getattr(getattr(message, "chat", None), "type", None),
        "sender_chat_id=", getattr(getattr(message, "sender_chat", None), "id", None),
        "sender_chat_type=", getattr(getattr(message, "sender_chat", None), "type", None),
        "from_user_id=", getattr(getattr(message, "from_user", None), "id", None),
        "anonymous=", _is_official_anonymous_admin(message),
    )
    anonymous_admin = _is_official_anonymous_admin(message)
    if anonymous_admin:
        # Internal sentinel for Anonymous Admin jobs. This is authorization
        # state, not a human Telegram user identity.
        user_id = OFFICIAL_ANONYMOUS_GROUP_ID
    else:
        if message.from_user is None:
            return
        user_id = _message_actor_id(message)

    if not anonymous_admin and not is_verified(user_id):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "You must be **verified** to use /rec. Run /verify.")
    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously.\n"
            "Use /statusme to check them or /cancelme to cancel all."
        )

    # Refresh the playlist before resolving a channel name. This allows
    # /rec <channel> to work with newly-added playlist entries without restart.
    msg_text = message.text or ""
    first_arg = (msg_text.split(None, 2)[1] if len(msg_text.split(None, 2)) > 1 else "")
    # Direct URLs do not need the remote channel catalogue.  Avoid making
    # every recording wait on GitHub before FFmpeg can start.
    if first_arg.lower().startswith(("http://", "https://")):
        playlist_ok = True
    else:
        playlist_ok = await _refresh_channel_playlist()
    if playlist_ok:
        msg_text = _resolve_channel_prefix(msg_text)
    else:
        # Direct URLs can still be recorded; channel-name commands cannot.
        if not first_arg.lower().startswith(("http://", "https://")):
            return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "❌ Channel playlist could not be loaded. Please try again.")

    # ── CloudPlay / JSON-ish multi-line format ────────────────────────────────
    #   Format A:  "user_agent": "..." -- "mpd_url": "...", -t HH:MM:SS
    #   Format B:  "m3u8_url": "...", "headers": { "Cookie": "...", ... }  -t HH:MM:SS
    _CP_KEYS = ('"mpd_url"', '"m3u8_url"', '"hls_url"', '"stream_url"')
    if "--" in msg_text or any(k in msg_text for k in _CP_KEYS):
        parsed = _parse_cloudplay_format(msg_text)
        if parsed:
            url, timestamp, raw_filename, flags = parsed
            if not url or not timestamp:
                return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
                    "❌ Could not parse the CloudPlay format.\n"
                    "Make sure `mpd_url` / `m3u8_url` and `-t HH:MM:SS` are present."
                )
            setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
            asyncio.create_task(do_record(client, None, setup))
            return

    # ── Standard format: /rec url duration [filename] [flags...] ─────────────
    if len(message.command) < 3:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "**Usage:** `/rec <link> HH:MM:SS <filename>`\n\n"
            "Example: `/rec https://cdn.example.com/live.m3u8 01:30:00 MyShow`\n\n"
            "**Inline flags** (can be on separate lines):\n"
            "`-aes <hex>`     — AES-128 key (HLS)\n"
            "`-cookie <v>`    — Cookie header\n"
            "`-ua <v>`        — User-Agent\n"
            "`-referer <v>`   — Referer header\n"
            "`-origin <v>`    — Origin header (e.g. Hotstar)\n"
            "`-license <url>` — ClearKey DRM license URL (MPD/DASH)\n\n"
            "**CloudPlay multi-line format also supported** — paste directly."
        )

    # Parse CLI-style flags but keep the full recording wizard.
    # Example: /rec -user_agent "VLC/3.0.20" -i URL -t 00:10:00 Test
    url, timestamp, raw_filename, flags = _shlex_parse_rec(msg_text)
    if flags:
        setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
        rec_setup_sessions[user_id] = setup
        probe_msg = await message.reply_text("🔍 **Probing stream for audio tracks…**")
        setup["status_msg"] = probe_msg
        setup["setup_msg_id"] = probe_msg.id
        setup["current_status_text"] = "🔍 **Probing stream for audio tracks…**"
        setup["current_reply_markup"] = None
        try:
            probe = await probe_stream(url)
            effective_url = probe["final_url"]
            tracks = await _probe_audio_tracks(effective_url)
            setup["detected_audio_tracks"] = tracks
            setup["effective_url"] = effective_url
            setup["is_hls"] = probe["is_hls"]
            audio_names = ", ".join(_audio_track_label(track) for track in tracks) or "No tagged tracks; all audio will be kept"
            await _edit_setup_message(
                probe_msg, setup,
                f"✅ **Audio tracks probed**\n{audio_names}\n\n"
                "🔍 **Probing stream for Video Quality…**",
            )
            video_streams = await _probe_video_streams(effective_url)
            setup["detected_video_streams"] = video_streams
            setup["detected_video_dimensions"] = [
                (stream["width"], stream["height"]) for stream in video_streams
            ]
            await _edit_setup_message(
                probe_msg, setup, _audio_step_text(setup), _kb_audio_step(setup)
            )
        except Exception as e:
            rec_setup_sessions.pop(user_id, None)
            await _stop_live_preview(setup)
            return await _edit_setup_message(
                probe_msg, setup, f"❌ **Stream probe failed:** `{e}`"
            )
        return

    # Channel-name support:
    # /rec POGO 00:30:00  -> internally convert POGO to its configured stream URL
    # /rec <URL> 00:30:00 -> keep normal direct-URL behaviour
    if msg_text != (message.text or ""):
        try:
            old_command = message.command
            # Rebuild command tokens from the internally-resolved text.
            try:
                resolved_tokens = shlex.split(
                    msg_text[msg_text.find(" "):].strip()
                )
            except Exception:
                resolved_tokens = msg_text.split()[1:]

            message.command = ["rec"] + resolved_tokens
            await handle_record(client, message)
            return
        except Exception:
            message.command = old_command
            raise

    await handle_record(client, message)


# ---------------------------------------------------------------------------
# /DirectRec — instant recording, no wizard
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["DirectRec", "directrec", "drec", "dr"]))
async def directrec_cmd(client: Client, message: Message):
    anonymous_admin = _is_official_anonymous_admin(message)
    if anonymous_admin:
        # Internal sentinel for Anonymous Admin jobs; never a human user ID.
        user_id = OFFICIAL_ANONYMOUS_GROUP_ID
    else:
        if message.from_user is None:
            return
        user_id = _message_actor_id(message)
    if not anonymous_admin and not is_verified(user_id):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "You must be **verified** to use /DirectRec. Run /verify.")
    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously.\n"
            "Use /statusme to check them or /cancelme to cancel all."
        )

    msg_text = message.text or ""
    first_arg = (msg_text.split(None, 2)[1] if len(msg_text.split(None, 2)) > 1 else "")
    if first_arg.lower().startswith(("http://", "https://")):
        playlist_ok = True
    else:
        playlist_ok = await _refresh_channel_playlist()
    if playlist_ok:
        msg_text = _resolve_channel_prefix(msg_text)
    else:
        if not first_arg.lower().startswith(("http://", "https://")):
            return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "❌ Channel playlist could not be loaded. Please try again.")
    url, timestamp, raw_filename, flags = _shlex_parse_rec(msg_text)
    if not url or not timestamp:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "**Usage:** `/drec <url|channel name> HH:MM:SS [filename] [flags]`\n\n"
            "Example:\n"
            "```\n/drec POGO 00:00:10\n"
            "/drec https://cdn.example.com/live.m3u8 00:30:00 MyShow\n"
            "-cookie \"token=abc123\"\n"
            "-ua \"Mozilla/5.0\"\n"
            "-referer \"https://example.com/\"\n"
            "-aes 7a6ba0b06fd254538156f3c5d2366bcb\n```\n\n"
            "**Flags** (optional, can be on separate lines):\n"
            "`-aes <hex>`   — AES-128 decryption key (32-char hex)\n"
            "`-cookie <v>`  — Cookie header value\n"
            "`-ua <v>` / `-user_agent <v>` — User-Agent string\n"
            "`-referer <v>` — Referer header\n"
            "`-aio`         — Allow all input extensions"
        )
    if time_to_seconds(timestamp) <= 0:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "❌ Invalid duration. Use a positive `HH:MM:SS` value, for example `00:10:00`.",
        )

    setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
    # User command is temporary too: remove /drec after 5 minutes.
    _schedule_message_delete(message, AUTO_DELETE_5MIN)
    # Start capture directly; skip the expensive pre-record live-preview loop.
    asyncio.create_task(do_record(client, None, setup))


# ---------------------------------------------------------------------------
# /threerec — start three playlist channels in parallel with per-channel output
# ---------------------------------------------------------------------------

@app.on_message(filters.command("threerec"))
async def threerec_cmd(client: Client, message: Message):
    anonymous_admin = _is_official_anonymous_admin(message)
    if anonymous_admin:
        user_id = OFFICIAL_ANONYMOUS_GROUP_ID
    else:
        if message.from_user is None:
            return
        user_id = _message_actor_id(message)

    if not anonymous_admin and not is_verified(user_id):
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "You must be **verified** to use /threerec. Run /verify."
        )

    try:
        tokens = shlex.split(message.text or "")
    except Exception:
        tokens = (message.text or "").split()

    # Syntax: /threerec CHANNEL_1 CHANNEL_2 CHANNEL_3 -t HH:MM:SS
    duration = None
    channel_names = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.lower() in ("-t", "--time"):
            if i + 1 >= len(tokens):
                return await _reply_auto_delete(
                    message, AUTO_DELETE_5MIN,
                    "❌ Duration missing. Example: `/threerec POGO.TPLAY Pogo.1080p POGO.FW -t 00:00:30`"
                )
            duration = tokens[i + 1]
            i += 2
            continue
        channel_names.append(token)
        i += 1

    if len(channel_names) != 3 or not duration:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "**Usage:** `/threerec POGO.TPLAY Pogo.1080p POGO.FW -t 00:00:30`\n\n"
            "• `POGO.TPLAY` → 720×576\n"
            "• `Pogo.1080p` → original source resolution/quality\n"
            "• `POGO.FW` → 720×576\n"
            "All three recordings start together."
        )

    duration_seconds = time_to_seconds(duration)
    if duration_seconds <= 0:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "❌ Invalid duration. Use `HH:MM:SS`, for example `00:00:30`."
        )

    current_count = len(active_recs.get(user_id, {}))
    if current_count + 3 > MAX_CONCURRENT_REC:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            f"⚠️ Three more recordings need 3 free slots. "
            f"Currently active: {current_count}/{MAX_CONCURRENT_REC}."
        )

    if not await _refresh_channel_playlist():
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "❌ Channel playlist could not be loaded. Please try again."
        )

    resolved_jobs = []
    for channel_name in channel_names:
        resolved = _resolve_channel_prefix(f"/rec {channel_name} {duration}")
        try:
            resolved_tokens = shlex.split(resolved)
        except Exception:
            resolved_tokens = resolved.split()

        if len(resolved_tokens) < 3 or not resolved_tokens[1].lower().startswith(("http://", "https://")):
            return await _reply_auto_delete(
                message, AUTO_DELETE_5MIN,
                f"❌ Playlist channel not found: `{channel_name}`"
            )
        stream_url = resolved_tokens[1]
        resolved_jobs.append((channel_name, stream_url))

    _schedule_message_delete(message, AUTO_DELETE_5MIN)
    started = await message.reply_text(
        "🚀 **Starting all 3 recordings…**\n"
        + "\n".join(f"• `{name}`" for name, _ in resolved_jobs)
    )

    for channel_name, stream_url in resolved_jobs:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", channel_name).strip("._") or "Recording"
        setup = _build_setup(
            user_id, message, stream_url, duration,
            f"{safe_name}_{duration.replace(':', '-')}", {}
        )
        setup["watermark_on"] = True
        setup["watermark_pos"] = "bottom_center"
        setup["watermark_preset"] = "Watermark 1"
        setup["watermark_size"] = 180
        setup["watermark_times"] = []
        setup["quality"] = "original"
        setup["custom_crf"] = 18

        normalized = re.sub(r"[^a-z0-9]+", "", channel_name.lower())
        if normalized == "pogotplay":
            setup["custom_scale"] = "720:576"
        elif normalized == "pogofw":
            setup["custom_scale"] = "720:576"
        elif normalized == "pogo1080p":
            # Do not downscale the 1080p feed. Keep its native dimensions.
            setup["preserve_source_resolution"] = True
            try:
                dimensions = await _probe_video_streams(stream_url)
                if any(
                    item.get("width") == 1920 and item.get("height") == 1080
                    for item in dimensions
                ):
                    setup["custom_video_label"] = ".1080p"
                else:
                    LOG.warning(
                        "/threerec %s: source is not confirmed as 1920x1080; "
                        "preserving its actual source resolution without a 1080p label",
                        channel_name,
                    )
            except Exception as probe_error:
                LOG.warning("1080p source check failed for %s: %s", channel_name, probe_error)
                setup["preserve_source_resolution"] = True

        asyncio.create_task(do_record(client, None, setup))

    try:
        await started.edit_text(
            "✅ **Three recording jobs submitted.**\n"
            "Each channel has its own recording/status message."
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NEW: direct text recording flow (keeps the existing /rec wizard unchanged)
# ---------------------------------------------------------------------------
_AUTO_TEXT_REC_RE = re.compile(
    r"^(?P<source>.+?)\s+(?P<duration>\d{1,2}:\d{2}:\d{2})(?:\s+(?P<filename>.+))?$"
)


async def _probe_video_qualities(url: str, timeout_sec: int = 20) -> list:
    """Return only video heights that FFprobe actually finds in the source."""
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    args = [
        ffprobe_bin, "-v", "error", "-hide_banner",
        "-print_format", "json",
        "-show_entries", "stream=codec_type,width,height",
        "-select_streams", "v",
        "-user_agent", DEFAULT_USER_AGENT,
        "-probesize", "20000000",
        "-analyzeduration", "10000000",
        "-i", url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0:
            LOG.warning("Quality probe failed: %s", stderr.decode(errors="ignore")[-500:])
            return []
        data = json.loads(stdout.decode(errors="ignore") or "{}")
        heights = set()
        for stream in data.get("streams", []):
            try:
                h = int(stream.get("height") or 0)
            except (TypeError, ValueError):
                h = 0
            if h > 0:
                heights.add(h)
        return sorted(heights, reverse=True)
    except asyncio.TimeoutError:
        LOG.warning("Quality probe timed out for %s", url)
        return []
    except Exception as e:
        LOG.warning("Quality probe error for %s: %s", url, e)
        return []


async def _probe_video_streams(url: str, timeout_sec: int = 20) -> list[dict]:
    """Return selectable source video streams with their input-relative indexes."""
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    args = [
        ffprobe_bin, "-v", "error", "-hide_banner",
        "-print_format", "json",
        "-show_entries", "stream=width,height",
        "-select_streams", "v",
        "-user_agent", DEFAULT_USER_AGENT,
        "-probesize", "20000000",
        "-analyzeduration", "10000000",
        "-i", url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0:
            LOG.warning("Video dimension probe failed: %s", stderr.decode(errors="ignore")[-500:])
            return []
        data = json.loads(stdout.decode(errors="ignore") or "{}")
        video_streams = []
        seen_dimensions = set()
        for video_index, stream in enumerate(data.get("streams", [])):
            try:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0 and (width, height) not in seen_dimensions:
                seen_dimensions.add((width, height))
                video_streams.append({
                    "video_index": video_index,
                    "width": width,
                    "height": height,
                })
        return sorted(
            video_streams,
            key=lambda item: (item["height"], item["width"]),
            reverse=True,
        )
    except asyncio.TimeoutError:
        LOG.warning("Video dimension probe timed out for %s", url)
        return []
    except Exception as e:
        LOG.warning("Video dimension probe error for %s: %s", url, e)
        return []


def _quality_labels_from_heights(heights: list) -> list[str]:
    """Convert actual FFprobe heights to familiar quality labels; never invent one."""
    labels = ["Real"]
    standard = [2160, 1440, 1080, 720, 576, 480, 360, 240, 144]
    for h in standard:
        if h in heights:
            labels.append(f"{h}p")
    return labels


@app.on_message(filters.private & filters.text & AUTH, group=0)
async def direct_text_record(client: Client, message: Message):
    """Record `Link/ChannelName HH:MM:SS [filename]` without touching /rec wizard."""
    text = (message.text or "").strip()
    if not text or text.startswith("/") or message.from_user is None:
        message.continue_propagation()
        return

    match = _AUTO_TEXT_REC_RE.match(text)
    if not match:
        message.continue_propagation()
        return

    user_id = _message_actor_id(message)
    source = match.group("source").strip()
    timestamp = match.group("duration")
    raw_filename = (match.group("filename") or "").strip()
    duration_sec = time_to_seconds(timestamp)
    if duration_sec <= 0:
        return

    if not is_owner(user_id) and not is_verified(user_id):
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            "You must be **verified** to use recording. Run /verify."
        )
    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await _reply_auto_delete(
            message, AUTO_DELETE_5MIN,
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously."
        )

    # Channel name or direct URL. Keep /rec's playlist resolver separate.
    url = source
    if not source.lower().startswith(("http://", "https://")):
        try:
            await _refresh_channel_playlist()
        except Exception:
            pass
        real_name = _resolve_playlist_channel_name(source)
        if real_name:
            url = BOT_CHANNELS[real_name]
        else:
            return await _reply_auto_delete(
                message, AUTO_DELETE_5MIN,
                f"❌ Channel not found: `{source}`"
            )

    if not raw_filename:
        raw_filename = source if not source.lower().startswith(("http://", "https://")) else DEFAULT_FILENAME
    for bad in '/\\:*?"<>|':
        raw_filename = raw_filename.replace(bad, "_")

    setup = _build_setup(user_id, message, url, timestamp, raw_filename, {})
    # Explicitly preserve source quality and disable all video processing.
    setup.update({
        "watermark_on": False,
        "quality": "original",
        "aspect": "none",
        "audio_track": [],
        "auto_mode": False,
    })

    rec_setup_sessions[user_id] = setup

    # Anonymous Admin /rec messages must remain visible.
    # Keep the existing 5-minute auto-delete for normal users.
    if user_id != OFFICIAL_ANONYMOUS_GROUP_ID:
        _schedule_message_delete(message, AUTO_DELETE_5MIN)

    status = await message.reply_text(
        "🛠 **FFmpeg • FFprobe**\n↓\n"
        "🔍 **Probing stream for audio tracks…**"
    )
    setup["setup_msg_id"] = status.id

    try:
        probe = await probe_stream(url)
        effective_url = probe["final_url"]
        tracks = await _probe_audio_tracks(effective_url)
        heights = await _probe_video_qualities(effective_url)

        setup["detected_audio_tracks"] = tracks
        setup["effective_url"] = effective_url
        setup["is_hls"] = probe["is_hls"]
        setup["detected_video_heights"] = heights

        audio_note = " • ".join(
            _audio_track_label(t).split(" • ")[0] for t in tracks
        ) or "No separate audio tracks detected"
        quality_labels = _quality_labels_from_heights(heights)

        await status.edit_text(
            "🛠 **FFmpeg • FFprobe** ✅\n↓\n"
            f"🔍 **Probing stream for audio tracks…** ✅\n"
            f"   {audio_note}\n↓\n"
            "🔍 **Probing stream for Quality…** ✅\n"
            f"   {' • '.join(quality_labels)}\n↓\n"
            "▶️ **Start Recording**"
        )

        # No watermark, resize, crop, pad, or forced 480p: do_record receives
        # quality=original + watermark_off and therefore uses the source stream.
        await asyncio.sleep(0.5)
        await status.edit_text(
            "▶️ **Start Recording**\n↓\n"
            "🎬 **Recording #1 in Progress**"
        )
        asyncio.create_task(do_record(client, None, setup))

        # Anonymous Admin recording status must remain visible.
        # Normal /rec users keep the existing 5-minute cleanup.
        if user_id != OFFICIAL_ANONYMOUS_GROUP_ID:
            _schedule_message_delete(status, AUTO_DELETE_5MIN)
    except Exception as e:
        LOG.exception("Direct text recording probe failed")
        rec_setup_sessions.pop(user_id, None)
        await _stop_live_preview(setup)
        try:
            await status.edit_text(f"❌ **Stream probe failed:** `{e}`")
        except Exception:
            pass

# Quick-paste: user sends an HLS URL directly as a text message
# ---------------------------------------------------------------------------

def _is_hls_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith(("http://", "https://", "//")) and ".m3u8" in t


@app.on_message(filters.private & filters.text & AUTH)
async def quick_rec_text(client: Client, message: Message):
    user_id = _message_actor_id(message)
    text    = (message.text or "").strip()

    # Pass commands to their own handlers (do NOT block them)
    if text.startswith("/"):
        message.continue_propagation()
    if not _is_hls_url(text):
        return
    # Skip if user is typing watermark text
    if user_id in _wm_text_pending:
        return

    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await message.reply_text(
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously.\n"
            "Use /statusme to check them or /cancelme to cancel all."
        )
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to start recordings. Run /verify.")

    message.command = ["rec", text, DEFAULT_REC_DURATION, DEFAULT_FILENAME]
    await handle_record(client, message)


# ---------------------------------------------------------------------------
# Pre-recording setup wizard callback handler
# ---------------------------------------------------------------------------


@app.on_callback_query(filters.regex(r"^wm3q:(\d+):(\d+):(720|576)$"))
async def wm3_quality_callback(client: Client, cq: CallbackQuery):
    try:
        _, uid_s, rec_s, quality = cq.data.split(":")
        uid = int(uid_s)
        rec_id = int(rec_s)
    except (ValueError, AttributeError):
        return await cq.answer("Invalid quality selection.", show_alert=True)

    if not cq.from_user or cq.from_user.id != uid:
        return await cq.answer("This is not your recording.", show_alert=True)

    state = wm3_quality_waiters.get((uid, rec_id))
    if not state or state.done():
        return await cq.answer("Quality selection expired.", show_alert=True)

    state.set_result(quality)
    await cq.answer(f"{quality}p selected.")

@app.on_callback_query(filters.regex(r"^rs:"))
async def rec_setup_cb(client: Client, cq: CallbackQuery):
    parts   = cq.data.split(":")
    uid_str = parts[1] if len(parts) > 1 else ""
    action  = parts[2] if len(parts) > 2 else ""
    val     = parts[3] if len(parts) > 3 else ""

    try:
        uid = int(uid_str)
    except ValueError:
        return await cq.answer("Invalid session.", show_alert=True)

    # CallbackQuery.from_user is the actual user who clicked the button.
    # Do NOT use cq.message here: that is the bot's setup message.
    clicker_id = cq.from_user.id if cq.from_user else 0

    in_allowed_group = bool(
        GROUP_CHAT_ID
        and cq.message
        and getattr(cq.message.chat, "id", None) == GROUP_CHAT_ID
    )

    anonymous_setup = (uid == OFFICIAL_ANONYMOUS_GROUP_ID)

    if clicker_id != uid:
        if anonymous_setup:
            callback_chat_id = getattr(
                getattr(cq.message, "chat", None), "id", None
            )
            if callback_chat_id != OFFICIAL_ANONYMOUS_GROUP_ID:
                return await cq.answer(
                    "This is not your setup menu.", show_alert=True
                )
        elif not (
            in_allowed_group
            and (is_owner(clicker_id) or is_admin(clicker_id))
        ):
            return await cq.answer(
                "This is not your setup menu.", show_alert=True
            )

    setup = rec_setup_sessions.get(uid)
    if not setup:
        return await cq.answer("Setup session expired.", show_alert=True)

    if action == "cancel":
        rec_setup_sessions.pop(uid, None)
        await _stop_live_preview(setup)
        try:
            await _edit_setup_message(cq.message, setup, "Recording setup cancelled.")
        except Exception:
            pass
        return await cq.answer("Cancelled.")

    if action == "gen_preview":
        ok = await _start_live_preview(client, setup)
        if ok:
            return await cq.answer("✅ Preview generated. Tap Gen Preview again to update it.")
        return await cq.answer("Could not capture the stream preview. Try again.", show_alert=True)

    # ---- Step 0: Audio track selection ----

    if action == "video_stream":
        if val == "auto":
            setup["video_stream_index"] = None
            await cq.answer("Automatic video stream selected.")
        else:
            try:
                index = int(val)
            except ValueError:
                return await cq.answer("Invalid video stream.", show_alert=True)
            stream = next(
                (item for item in setup.get("detected_video_streams", [])
                 if item.get("video_index") == index),
                None,
            )
            if not stream:
                return await cq.answer("That video stream is no longer available.", show_alert=True)
            setup["video_stream_index"] = index
            await cq.answer(f"Video: {stream['width']}×{stream['height']}")
        try:
            await _edit_setup_message(
                cq.message, setup, _audio_step_text(setup), _kb_audio_step(setup)
            )
        except Exception:
            pass
        return

    if action == "audio_toggle":
        try:
            idx = int(val)
        except ValueError:
            return await cq.answer("Bad value.", show_alert=True)
        tracks = setup.get("detected_audio_tracks", [])
        sel    = list(setup["audio_track"])
        if idx in sel:
            sel.remove(idx)
        else:
            sel.append(idx)
        if len(sel) >= len(tracks) and tracks:
            sel = []
        setup["audio_track"] = sel
        lang = tracks[idx - 1].get("lang", "?").upper() if tracks and idx <= len(tracks) else f"T{idx}"
        state = "✅" if (not setup["audio_track"] or idx in setup["audio_track"]) else "⬜"
        await cq.answer(f"{state} {lang}")
        try:
            await _edit_setup_message(cq.message, setup, _audio_step_text(setup), _kb_audio_step(setup))
        except Exception:
            pass
        return

    if action == "audio_select_all":
        setup["audio_track"] = []
        await cq.answer("✅ All Tracks selected")
        try:
            await _edit_setup_message(cq.message, setup, _audio_step_text(setup), _kb_audio_step(setup))
        except Exception:
            pass
        return

    if action == "audio_select":
        try:
            idx = int(val)
        except ValueError:
            return await cq.answer("Bad value.", show_alert=True)
        setup["audio_track"] = [] if idx == 0 else [idx]
        tracks = setup.get("detected_audio_tracks", [])
        label  = "All Tracks" if not setup["audio_track"] else (
            _audio_track_label(tracks[idx - 1]) if tracks and idx <= len(tracks) else f"Track {idx}"
        )
        await cq.answer(f"🎙 {label}")
        try:
            await _edit_setup_message(cq.message, setup, _audio_step_text(setup), _kb_audio_step(setup))
        except Exception:
            pass
        return

    if action == "next_wm":
        setup["step"] = 1
        try:
            await cq.answer("💧 Watermark settings")
        except Exception:
            pass
        try:
            await _edit_setup_message(
                cq.message, setup, _setup_summary(setup), _kb_step1(setup)
            )
        except Exception as e:
            LOG.warning("Next Watermark edit failed: %s", e)
            try:
                await cq.answer("Could not open Watermark step.", show_alert=True)
            except Exception:
                pass
        return

    if action == "back_audio":
        setup["step"] = 0
        await cq.answer()
        try:
            await _edit_setup_message(cq.message, setup, _audio_step_text(setup), _kb_audio_step(setup))
        except Exception:
            pass
        return

    # ---- Step 1: Watermark (OWNER ONLY) ----

    if action in ("wm_preset", "wm_time") and not is_owner(cq.from_user.id if cq.from_user else 0):
        return await cq.answer("Only Owner can use Watermark controls.", show_alert=True)

    if action == "wm_preset":
        if val == "1":
            setup["watermark_preset"] = "Watermark 1"
            setup["watermark_size"] = 150
            setup["watermark_pos"] = "bottom_center"
        elif val == "2":
            setup["watermark_preset"] = "Watermark 2"
            setup["watermark_size"] = 150
            # Watermark 2: slightly higher than the normal bottom position.
            setup["watermark_pos"] = "wm2_bottom_center"
        elif val == "3":
            setup["watermark_preset"] = "Normal Watermark 3"
            setup["watermark_size"] = 300
            setup["watermark_pos"] = "wm3_bottom_left"
        else:
            return await cq.answer("Unknown watermark preset.", show_alert=True)
        setup["watermark_on"] = True
        await cq.answer(f"{setup['watermark_preset']} selected")
        try:
            await _edit_setup_message(cq.message, setup, _setup_summary(setup), _kb_step1(setup))
        except Exception:
            pass
        return

    if action == "wm_time":
        if val not in ("5_6", "10_11"):
            return await cq.answer("Unknown watermark time.", show_alert=True)
        times = setup.setdefault("watermark_times", [])
        if val in times:
            times.remove(val)
            state = "OFF"
        else:
            times.append(val)
            state = "ON"
        setup["watermark_on"] = True
        label = "00:05:00 → 00:06:00" if val == "5_6" else "00:10:00 → 00:11:00"
        await cq.answer(f"{label}: {state}")
        try:
            await _edit_setup_message(cq.message, setup, _setup_summary(setup), _kb_step1(setup))
        except Exception:
            pass
        return

    if action == "next_quality":
        setup["step"] = 2
        await cq.answer()
        try:
            await _edit_setup_message(cq.message, setup, _setup_summary(setup), _kb_step2(setup))
        except Exception:
            pass
        return

    if action == "quality":
        setup["quality"] = val
        await cq.answer(f"Quality: {val}p" if val != "original" else "Quality: Original")
        try:
            await _edit_setup_message(cq.message, setup, _setup_summary(setup), _kb_step2(setup))
        except Exception:
            pass
        return

    if action == "back_step2":
        setup["step"] = 1
        await cq.answer()
        try:
            await _edit_setup_message(cq.message, setup, _setup_summary(setup), _kb_step1(setup))
        except Exception:
            pass
        return

    if action == "start":
        rec_setup_sessions.pop(uid, None)
        await cq.answer("Starting recording...")
        setup["status_msg"] = setup.get("status_msg") or cq.message
        setup["setup_msg_id"] = setup["status_msg"].id

        asyncio.create_task(do_record(client, cq, setup))
        return

    await cq.answer()


# ---------------------------------------------------------------------------
# Watermark text input handler
# ---------------------------------------------------------------------------

def _wm_filter(_, __, m: Message) -> bool:
    return bool(m.from_user and m.from_user.id in _wm_text_pending)


_wm_filter_obj = filters.create(_wm_filter)


@app.on_message(filters.private & filters.text & _wm_filter_obj)
async def wm_text_input(client: Client, message: Message):
    # Pass commands to their own handlers
    if (message.text or "").strip().startswith("/"):
        message.continue_propagation()
    user_id = _message_actor_id(message)
    _wm_text_pending.discard(user_id)
    setup = rec_setup_sessions.get(user_id)
    if not setup:
        return
    setup["watermark_text"] = (message.text or "").strip() or get_default_watermark()
    setup["watermark_on"]   = True
    try:
        setup_msg = await client.get_messages(setup["chat_id"], setup.get("setup_msg_id"))
        await _edit_setup_message(
            setup_msg, setup, _setup_summary(setup), _kb_step1(setup)
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cookies management
# ---------------------------------------------------------------------------

def _drop_pending_cookie_archive(user_id: int) -> None:
    state = pending_cookie_archives.pop(int(user_id), None)
    if state and state.get("path"):
        try:
            os.remove(state["path"])
        except OSError:
            pass


def _cookie_zip_keyboard(user_id: int, token: str, entries: list) -> InlineKeyboardMarkup:
    rows = []
    for index, (member_name, _size) in enumerate(entries):
        label = os.path.basename(member_name)
        if len(label) > 38:
            label = label[:35] + "..."
        rows.append([
            InlineKeyboardButton(
                f"{index + 1}. {label}",
                callback_data=f"ckzip:{user_id}:{token}:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"ckzip:{user_id}:{token}:cancel",
        )
    ])
    return InlineKeyboardMarkup(rows)


@app.on_message(filters.command("set_cookies"))
async def set_cookies_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    _drop_pending_cookie_archive(user_id)
    pending_cookies_users[user_id] = time.time()
    await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
        "📎 **Send your cookies file** (`.txt`, `.cookies`, or `.zip`).\n\n"
        "A ZIP may contain multiple cookie files; you can choose one after upload.\n"
        "Export it from your browser using a cookies extension.\n"
        "You have **5 minutes** to send the file."
    )


@app.on_message(filters.command("cookies_status"))
async def cookies_status_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    await _reply_auto_delete(message, AUTO_DELETE_5MIN, _cookies_summary(user_id))


@app.on_message(filters.command("del_cookies"))
async def del_cookies_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    path    = _user_cookies_path(user_id)
    if os.path.exists(path):
        try:
            os.remove(path)
            await _reply_auto_delete(message, AUTO_DELETE_5MIN, "✅ Cookies deleted.")
        except Exception as e:
            await _reply_auto_delete(message, AUTO_DELETE_5MIN, f"Failed to delete cookies: `{e}`")
    else:
        await _reply_auto_delete(message, AUTO_DELETE_5MIN, "No cookies stored.")


@app.on_message(filters.private & filters.document & AUTH, group=0)
async def cookies_document_handler(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if user_id not in pending_cookies_users:
        return

    prompt_time = pending_cookies_users.get(user_id, 0)
    if time.time() - prompt_time > _COOKIE_PROMPT_TTL_SEC:
        pending_cookies_users.pop(user_id, None)
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "Cookie upload window expired. Run /set_cookies again.")

    pending_cookies_users.pop(user_id, None)
    doc = message.document
    if not doc:
        return

    filename = (doc.file_name or "").lower()
    is_archive = filename.endswith(".zip")
    if not (is_archive or filename.endswith(".txt") or filename.endswith(".cookies")):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "Please send a `.txt`, `.cookies`, or `.zip` file containing cookies."
        )
    max_upload_size = _MAX_COOKIE_ARCHIVE_BYTES if is_archive else _MAX_COOKIE_FILE_BYTES
    if doc.file_size and doc.file_size > max_upload_size:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            f"File too large ({doc.file_size // 1024} KB). "
            f"Max is {max_upload_size // (1024 * 1024)} MB."
        )

    status = await _reply_auto_delete(
        message, AUTO_DELETE_5MIN,
        "⬇️ Downloading cookie ZIP..." if is_archive else "⬇️ Downloading cookie file..."
    )
    archive_path = None
    try:
        tmp_path = _user_cookies_path(user_id) + ".tmp"
        download_path = tmp_path
        if is_archive:
            archive_path = tmp_path + ".zip"
            download_path = archive_path
        await message.download(file_name=download_path)
        if is_archive:
            await status.edit_text("📦 Extracting cookie file from ZIP...")
            entries = _cookie_zip_entries(archive_path)
            if len(entries) > 1:
                token = pysecrets.token_hex(4)
                pending_cookie_archives[user_id] = {
                    "token": token,
                    "path": archive_path,
                    "entries": entries,
                    "status": status,
                    "created_at": time.time(),
                }
                archive_path = None
                await status.edit_text(
                    f"📦 ZIP contains `{len(entries)}` cookie files.\n\n"
                    "Choose which cookie profile to save:",
                    reply_markup=_cookie_zip_keyboard(user_id, token, entries),
                )
                return
            _extract_cookie_file_from_zip(archive_path, tmp_path, entries[0][0])
        try:
            cookie_count = _normalize_netscape_cookie_file(tmp_path)
        except ValueError as exc:
            os.remove(tmp_path)
            return await status.edit_text(
                f"**Invalid cookie file.**\n\n`{exc}`\n\n"
                "Send browser-exported cookies in Netscape format."
            )
        os.replace(tmp_path, _user_cookies_path(user_id))
        await status.edit_text(
            f"✅ Cookies saved and normalized!\n"
            f"• Valid cookie rows: `{cookie_count}`\n\n"
            f"{_cookies_summary(user_id)}"
        )
    except Exception as e:
        LOG.error(f"Cookie upload failed for {user_id}: {e}")
        try:
            await status.edit_text(f"Failed to save cookies: `{e}`")
        except Exception:
            pass
    finally:
        if archive_path:
            try:
                os.remove(archive_path)
            except OSError:
                pass


@app.on_callback_query(filters.regex(r"^ckzip:(\d+):([A-Za-z0-9]+):(cancel|\d+)$"))
async def cookie_zip_callback(client: Client, cq: CallbackQuery):
    user_id = int(cq.matches[0].group(1))
    token = cq.matches[0].group(2)
    selection = cq.matches[0].group(3)
    clicker_id = cq.from_user.id if cq.from_user else 0
    if clicker_id != user_id:
        return await cq.answer("Yeh aapka cookie selection nahi hai.", show_alert=True)

    state = pending_cookie_archives.get(user_id)
    if not state or state.get("token") != token:
        return await cq.answer("Cookie ZIP selection expired.", show_alert=True)
    if time.time() - state.get("created_at", 0) > _COOKIE_PROMPT_TTL_SEC:
        _drop_pending_cookie_archive(user_id)
        return await cq.answer("Cookie ZIP selection expired.", show_alert=True)

    if selection == "cancel":
        _drop_pending_cookie_archive(user_id)
        await cq.answer("Cancelled.")
        try:
            await cq.message.edit_text("❌ Cookie ZIP selection cancelled.", reply_markup=None)
        except Exception:
            pass
        return

    try:
        index = int(selection)
        entries = state["entries"]
        member_name = entries[index][0]
    except (ValueError, IndexError, KeyError):
        return await cq.answer("Invalid cookie selection.", show_alert=True)

    pending_cookie_archives.pop(user_id, None)
    archive_path = state["path"]
    tmp_path = _user_cookies_path(user_id) + ".tmp"
    try:
        await cq.answer("Saving selected cookies...")
        await cq.message.edit_text("📦 Extracting selected cookie profile...")
        _extract_cookie_file_from_zip(archive_path, tmp_path, member_name)
        cookie_count = _normalize_netscape_cookie_file(tmp_path)
        os.replace(tmp_path, _user_cookies_path(user_id))
        await cq.message.edit_text(
            f"✅ Cookies saved and normalized!\n"
            f"• Valid cookie rows: `{cookie_count}`\n\n"
            f"{_cookies_summary(user_id)}"
        )
    except Exception as e:
        LOG.error(f"Cookie ZIP selection failed for {user_id}: {e}")
        try:
            await cq.message.edit_text(f"Failed to save selected cookies: `{e}`")
        except Exception:
            pass
    finally:
        for path in (archive_path, tmp_path):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AOV — Audio Only Video
# ---------------------------------------------------------------------------

_AOV_JOBS: dict = {}

_AOV_LANG_NAMES = {
    "hin": "Hindi",
    "eng": "English",
    "tam": "Tamil",
    "tel": "Telugu",
    "kan": "Kannada",
    "mal": "Malayalam",
    "ben": "Bengali",
    "mar": "Marathi",
    "pan": "Punjabi",
    "guj": "Gujarati",
    "ori": "Odia",
    "asm": "Assamese",
    "urd": "Urdu",
    "ara": "Arabic",
    "spa": "Spanish",
    "fra": "French",
    "deu": "German",
    "jpn": "Japanese",
    "kor": "Korean",
    "chi": "Chinese",
    "rus": "Russian",
}

_AOV_EMOJIS = [
    "🥵", "🥵", "🔥", "😵‍💫", "❤️",
    "😱", "😇", "😡", "🤯", "😈",
    "🔥", "👑", "😰", "🙄", "😙",
    "👊", "😆", "😵", "🤐", "💯",
]


def _aov_lang_name(code: str) -> str:
    code = (code or "und").lower()[:3]
    return _AOV_LANG_NAMES.get(code, code.upper() if code != "und" else "Unknown")


def _aov_safe_name(name: str) -> str:
    name = os.path.splitext(os.path.basename(name))[0]

    # Old branding -> new branding.
    name = re.sub(
        r"Atozcartoonist",
        "LittleSinghamChannel",
        name,
        flags=re.IGNORECASE,
    )

    # Remove an existing LittleSinghamChannel|Language suffix.
    name = re.sub(
        r"\s*LittleSinghamChannel\|[^|]+$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(" ._-")

    return name or "video"


def _aov_progress_text(title, current, total, speed, eta):
    if not total:
        return f"⬇️ **{title}**"

    percent = max(0, min(100, int(current * 100 / total)))
    filled = min(20, percent // 5)
    emoji_index = min(19, filled)
    emoji = _AOV_EMOJIS[emoji_index]

    if percent >= 100:
        emoji = "💯"
        filled = 20

    bar = "▓" * filled + "░" * (20 - filled)

    speed_mb = speed / (1024 * 1024)
    eta_text = TimeFormatter(int(eta * 1000)) if eta > 0 else "00:00"

    return (
        f"{emoji} **{title}**\n\n"
        f"`{bar}` **{percent:02d}%**\n"
        f"📦 `{current / (1024 * 1024):.1f} / "
        f"{total / (1024 * 1024):.1f} MB`\n"
        f"⚡ Speed: `{speed_mb:.1f} MB/s`\n"
        f"⏳ ETA: `{eta_text}`"
    )


_AOV_LAST_EDIT: dict = {}


async def _aov_progress(current, total, message, start, title):
    if not total:
        return

    now = time.time()
    key = id(message)

    # Prevent Telegram flood limits.
    last = _AOV_LAST_EDIT.get(key, 0)
    if current != total and now - last < 3:
        return

    _AOV_LAST_EDIT[key] = now

    elapsed = max(now - start, 1.0)
    speed = current / elapsed
    eta = (total - current) / speed if speed > 0 else 0

    text = _aov_progress_text(title, current, total, speed, eta)

    try:
        await message.edit_text(text)
    except Exception:
        pass

    if current == total:
        _AOV_LAST_EDIT.pop(key, None)


def _aov_keyboard(uid: int, job: dict) -> InlineKeyboardMarkup:
    rows = []

    for track in job["tracks"]:
        idx = track["index"]
        selected = idx in job["selected"]
        mark = "✅ " if selected else ""

        rows.append([
            InlineKeyboardButton(
                f"{mark}🔊 {track['label']}",
                callback_data=f"aov:{uid}:a:{idx}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "✅ Select All",
            callback_data=f"aov:{uid}:all",
        ),
        InlineKeyboardButton(
            "❌ Clear",
            callback_data=f"aov:{uid}:clear",
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            "🎬 Generate",
            callback_data=f"aov:{uid}:go",
        ),
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"aov:{uid}:cancel",
        ),
    ])

    return InlineKeyboardMarkup(rows)


@app.on_message(filters.command(["Audio_Olny_video", "audio_olny_video"]))
async def audio_only_video_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if uid in _AOV_JOBS:
        return await message.reply_text("⚠️ You already have an Audio Only Video job running.")
    try:
        args = shlex.split(message.text or "")[1:]
    except Exception:
        args = (message.text or "").split()[1:]
    reply = message.reply_to_message
    media = None
    if reply:
        if reply.video:
            media = reply.video
        elif reply.document and (reply.document.mime_type or "").startswith("video/"):
            media = reply.document
    if media:
        output_base = args[0] if args else ""
        source_name = getattr(media, "file_name", None) or f"video_{reply.id}.mkv"
    else:
        if len(args) < 2:
            return await message.reply_text(
                "📥 **Telegram video:** reply to a video with `/Audio_Olny_video Output_Name`\n\n"
                "🗂 **Server video:** `/Audio_Olny_video ServerFileName Output_Name`"
            )
        server_name, output_base = args[0], args[1]
        existing_path = _audiostream_find_server_file(server_name)
        if not existing_path:
            return await message.reply_text(f"❌ Server file not found: `{os.path.basename(server_name)}`")
        source_name = os.path.basename(existing_path)
    job_dir = os.path.join(DOWNLOAD_DIRECTORY, f"aov_{uid}_{pysecrets.token_hex(5)}")
    os.makedirs(job_dir, exist_ok=True)
    source_path = os.path.join(job_dir, os.path.basename(source_name))
    status = await message.reply_text("⬇️ **Downloading/copying source video…**")
    try:
        if media:
            downloaded = await client.download_media(
                reply, file_name=source_path, progress=_aov_progress,
                progress_args=(status, time.time(), "Downloading source video…"),
            )
            if not downloaded or not os.path.isfile(downloaded) or os.path.getsize(downloaded) <= 0:
                raise RuntimeError("Telegram source video download failed or returned an empty file.")
            source_path = downloaded
        else:
            shutil.copy2(existing_path, source_path)

        await status.edit_text(
            "🔍 **Detecting audio tracks…**"
        )

        probe = await _ffprobe_video(source_path)
        audio_streams = probe.get("audio_streams") or []

        if not audio_streams:
            raise Exception("No audio tracks were found in this video.")

        tracks = []

        for number, stream in enumerate(audio_streams, 1):
            lang = (stream.get("lang") or "und").lower()[:3]
            label = _aov_lang_name(lang)

            if label == "Unknown":
                label = f"Audio Track {number}"

            tracks.append({
                "index": int(stream["index"]),
                "lang": lang,
                "label": label,
                "codec": stream.get("codec", "?"),
                "number": number,
            })

        source_duration = float(probe.get("duration") or 0.0)

        _AOV_JOBS[uid] = {
            "uid": uid,
            "chat_id": message.chat.id,
            "source": source_path,
            "duration": source_duration,
            "source_media_duration": float(getattr(media, "duration", 0) or 0),
            "source_name": os.path.basename(
                getattr(media, "file_name", None) or "video.mkv"
            ),
            "base_name": _aov_safe_name(output_base or os.path.splitext(source_name)[0]),
            "tracks": tracks,
            "selected": set(),
            "dir": job_dir,
            "status": status,
            "command": message,
        }

        text = (
            "🎧 **Audio Tracks Found**\n\n"
            "Select the audio track(s) you want.\n\n"
            "📹 Original video stream will remain unchanged."
        )

        await status.edit_text(
            text,
            reply_markup=_aov_keyboard(uid, _AOV_JOBS[uid]),
        )

    except Exception as ex:
        _AOV_JOBS.pop(uid, None)

        try:
            await status.edit_text(
                f"❌ **Audio track detection failed.**\n\n"
                f"`{ex}`"
            )
        except Exception:
            pass

        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^aov:(\d+):([^:]+):?(.*)$"))
async def cb_audio_only_video(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":", 3)

    uid = int(parts[1])
    action = parts[2]
    value = parts[3] if len(parts) > 3 else ""

    if not _callback_actor_matches(cq, uid):
        return await cq.answer(
            "This selection is not yours.",
            show_alert=True,
        )

    job = _AOV_JOBS.get(uid)

    if not job:
        return await cq.answer(
            "Session expired. Run /Audio_Olny_video again.",
            show_alert=True,
        )

    if action == "cancel":
        _AOV_JOBS.pop(uid, None)

        await cq.answer("Cancelled.")

        try:
            await cq.message.edit_text(
                "❌ **Audio Only Video cancelled.**",
                reply_markup=None,
            )
        except Exception:
            pass

        try:
            shutil.rmtree(job["dir"], ignore_errors=True)
        except Exception:
            pass

        return

    if action == "a":
        try:
            stream_index = int(value)
        except ValueError:
            return await cq.answer("Invalid audio track.", show_alert=True)

        selected = job["selected"]

        if stream_index in selected:
            selected.remove(stream_index)
            await cq.answer("Audio track removed.")
        else:
            selected.add(stream_index)
            await cq.answer("Audio track selected.")

        try:
            await cq.message.edit_reply_markup(
                reply_markup=_aov_keyboard(uid, job)
            )
        except Exception:
            pass

        return

    if action == "all":
        job["selected"] = {
            track["index"] for track in job["tracks"]
        }

        await cq.answer(
            f"✅ All {len(job['tracks'])} tracks selected."
        )

        try:
            await cq.message.edit_reply_markup(
                reply_markup=_aov_keyboard(uid, job)
            )
        except Exception:
            pass

        return

    if action == "clear":
        job["selected"] = set()

        await cq.answer("Selection cleared.")

        try:
            await cq.message.edit_reply_markup(
                reply_markup=_aov_keyboard(uid, job)
            )
        except Exception:
            pass

        return

    if action != "go":
        return

    selected_indices = set(job["selected"])

    if not selected_indices:
        return await cq.answer(
            "Select at least one audio track.",
            show_alert=True,
        )

    await cq.answer("🎬 Generating video(s)…")

    # Remove job from selectable state so another click cannot start it twice.
    _AOV_JOBS.pop(uid, None)

    source = job["source"]
    base_name = job["base_name"]
    job_dir = job["dir"]
    status = job["status"]

    selected_tracks = [
        track
        for track in job["tracks"]
        if track["index"] in selected_indices
    ]

    async def _aov_worker():
        generated = []

        try:
            await status.edit_text(
                "🎬 **Preparing selected audio tracks…**"
            )

            for video_number, track in enumerate(selected_tracks, 1):
                language = track["label"]

                output_name = (
                    f"{base_name}|{language}.mp4"
                )

                output_path = os.path.join(
                    job_dir,
                    output_name,
                )

                await status.edit_text(
                    f"🎬 **Video {video_number}/{len(selected_tracks)}**\n\n"
                    f"└── 🔊 `{language}`\n\n"
                    f"⚙️ Keeping original video stream…"
                )

                # Video is copied bit-for-bit.
                # Only the selected audio stream is encoded for MP4.
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-fflags",
                    "+genpts+discardcorrupt",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-i",
                    source,
                    "-map",
                    "0:v:0",
                    "-map",
                    f"0:{track['index']}",
                    "-map_metadata",
                    "0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-metadata:s:a:0",
                    f"language={track['lang']}",
                    "-metadata:s:a:0",
                    f"title={get_audio_brand_name()}|{language}|{base_name}",
                    "-metadata:s:a:0",
                    f"handler_name={get_audio_brand_name()}|{language}|{base_name}",
                    "-movflags",
                    "+faststart",
                    "-max_interleave_delta",
                    "0",
                    output_path,
                ]

                proc = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )

                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    err = (
                        stderr.decode(
                            "utf-8",
                            errors="replace",
                        ).strip()
                    )

                    raise Exception(
                        f"FFmpeg failed for {language}: {err[-1500:]}"
                    )

                if not os.path.exists(output_path):
                    raise Exception(
                        f"Output was not created for {language}."
                    )

                if os.path.getsize(output_path) <= 0:
                    raise Exception(
                        f"Output file is empty for {language}."
                    )

                # Verify the generated MP4 has a real duration before upload.
                out_duration = 0.0
                try:
                    out_probe = await _ffprobe_video(output_path)
                    out_duration = float(out_probe.get("duration") or 0.0)
                except Exception as probe_ex:
                    LOG.warning("AOV output duration probe failed for %s: %s", language, probe_ex)

                if out_duration <= 0:
                    # Fallback to Telegram's original media duration / source probe.
                    out_duration = max(
                        float(job.get("source_media_duration") or 0.0),
                        float(job.get("duration") or 0.0),
                    )

                if out_duration <= 0:
                    raise Exception(
                        f"Could not determine output duration for {language}."
                    )

                generated.append(
                    (output_path, language, video_number, int(round(out_duration)))
                )

                await status.edit_text(
                    f"✅ **Video {video_number}/{len(selected_tracks)} ready**\n\n"
                    f"📹 Video {video_number}\n"
                    f"└── 🔊 `{language}`\n\n"
                    f"📦 `{os.path.basename(output_path)}`"
                )

            # Upload generated videos one-by-one.
            for output_path, language, video_number, duration_sec in generated:
                await status.edit_text(
                    f"📤 **Uploading Video {video_number}/{len(generated)}…**\n\n"
                    f"📹 Video {video_number}\n"
                    f"└── 🔊 `{language}`"
                )

                upload_start = time.time()

                caption = (
                    f"📹 Video {video_number}\n"
                    f"└── 🔊 {language}\n\n"
                    f"LittleSinghamChannel|{language}"
                )

                await client.send_video(
                    chat_id=job["chat_id"],
                    video=output_path,
                    caption=caption,
                    duration=duration_sec,
                    supports_streaming=True,
                    progress=_aov_progress,
                    progress_args=(
                        status,
                        upload_start,
                        f"📤 Uploading Video {video_number}…",
                    ),
                )

            await status.edit_text(
                "✅ **Audio Only Video completed successfully.**\n\n"
                + "\n".join(
                    f"📹 Video {n}\n└── 🔊 `{lang}`\n⏱ `{duration_sec}s`"
                    for _, lang, n, duration_sec in generated
                )
            )

        except Exception as ex:
            LOG.error(
                f"AOV worker failed: {ex}",
                exc_info=True,
            )

            try:
                await status.edit_text(
                    f"❌ **Audio Only Video failed.**\n\n"
                    f"`{ex}`"
                )
            except Exception:
                pass

        finally:
            schedule_two_hour_cleanup(job_dir)

    asyncio.create_task(_aov_worker())





# ---------------------------------------------------------------------------
# /AudioStream — operate only on an existing file in DOWNLOAD_DIRECTORY.
# No Telegram source-video download is performed by this command.
# ---------------------------------------------------------------------------

_AUDIOSTREAM_JOBS: dict = {}
_AUDIOSTREAM_PENDING: dict = {}
_AUDIOSTREAM_LANGS = {
    "hin": "Hindi", "tam": "Tamil", "tel": "Telugu",
    "kan": "Kannada", "mal": "Malayalam", "mar": "Marathi",
    "eng": "English", "ben": "Bengali", "pan": "Punjabi",
    "guj": "Gujarati", "urd": "Urdu", "ori": "Odia",
    "asm": "Assamese",
}


def _audiostream_find_server_file(name: str) -> Optional[str]:
    """Resolve a filename strictly inside DOWNLOAD_DIRECTORY."""
    wanted = os.path.basename((name or "").strip().strip('"').strip("'"))
    if not wanted:
        return None
    exact = []
    stem_matches = []
    wanted_stem = os.path.splitext(wanted)[0].casefold()
    for root, _dirs, files in os.walk(DOWNLOAD_DIRECTORY):
        for filename in files:
            path = os.path.join(root, filename)
            if filename.casefold() == wanted.casefold():
                exact.append(path)
            elif os.path.splitext(filename)[0].casefold() == wanted_stem:
                stem_matches.append(path)
    matches = exact or stem_matches
    if not matches:
        return None
    return max(matches, key=lambda p: os.path.getmtime(p))


async def _audiostream_probe_tracks(path: str) -> list:
    info = await _ffprobe_video(path)
    streams = info.get("audio_streams") or []
    tracks = []
    for n, stream in enumerate(streams, 1):
        lang = (stream.get("lang") or "und").lower()[:3]
        label = _AUDIOSTREAM_LANGS.get(lang) or _aov_lang_name(lang)
        if label == "Unknown":
            label = f"Audio Track {n}"
        tracks.append({
            "index": int(stream["index"]),
            "lang": lang,
            "label": label,
            "codec": stream.get("codec", "?"),
            "number": n,
        })
    return tracks


def _audiostream_keyboard(uid: int, job: dict) -> InlineKeyboardMarkup:
    rows = []
    for track in job.get("tracks", []):
        idx = track["index"]
        label = track["label"]
        rows.append([
            InlineKeyboardButton(
                f"⬆️ Upload {label}",
                callback_data=f"astream:{uid}:up:{idx}",
            ),
            InlineKeyboardButton(
                f"🗑 Remove {label}",
                callback_data=f"astream:{uid}:rm:{idx}",
            ),
        ])
    rows.extend([
        [InlineKeyboardButton("🔄 Refresh audio tracks", callback_data=f"astream:{uid}:refresh")],
        [InlineKeyboardButton("🎬 Upload video", callback_data=f"astream:{uid}:video")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"astream:{uid}:cancel")],
    ])
    return InlineKeyboardMarkup(rows)


async def _audiostream_extract_audio(client, cq, job, track):
    source = job["source"]
    label = track["label"]
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "Audio"
    audio_path = os.path.join(job["dir"], f"{safe_label}.m4a")
    status = cq.message
    await status.edit_text(f"⬆️ **Audio Uploading...**\n\n🔊 `{label}.m4a`")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source, "-map", f"0:{track['index']}",
        "-vn", "-c:a", "aac", "-b:a", "192k",
        "-metadata", f"language={track['lang']}", audio_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.isfile(audio_path) or os.path.getsize(audio_path) <= 0:
        raise RuntimeError((err or b"Audio extraction failed").decode("utf-8", "replace")[-1200:])
    await client.send_document(
        chat_id=job["chat_id"],
        document=audio_path,
        caption=f"🔊 **{label}.m4a**",
        progress=_aov_progress,
        progress_args=(status, time.time(), "📤 Audio Uploading..."),
    )
    await status.edit_text(
        f"✅ **Audio uploaded successfully**\n\n"
        f"🔊 `{label}.m4a`\n\n"
        f"Choose another audio track or remove streams below.",
        reply_markup=_audiostream_keyboard(job["uid"], job),
    )


async def _audiostream_remove_track(cq, job, track):
    source = job["source"]
    temp_path = os.path.join(job["dir"], "updated_video.mkv")
    await cq.message.edit_text(
        f"🗑 **Removing Audio...**\n\n🔊 `{track['label']}`"
    )
    # Preserve all streams except the selected audio stream.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source, "-map", "0", "-map", f"-0:{track['index']}",
        "-c", "copy", temp_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
        raise RuntimeError((err or b"Could not remove audio stream").decode("utf-8", "replace")[-1200:])
    os.replace(temp_path, source)
    job["tracks"] = await _audiostream_probe_tracks(source)
    if not job["tracks"]:
        await cq.message.edit_text(
            "✅ **Audio removed.** No audio tracks remain in this video.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎬 Upload video", callback_data=f"astream:{job['uid']}:video")
            ]]),
        )
    else:
        await cq.message.edit_text(
            "✅ **Audio removed successfully.**\n\n"
            "You can remove more streams or upload an audio track.",
            reply_markup=_audiostream_keyboard(job["uid"], job),
        )


@app.on_message(filters.command(["AudioStream", "audiostream"]))
async def audiostream_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if uid in _AUDIOSTREAM_JOBS:
        return await message.reply_text("⚠️ Your /AudioStream session is already active.")

    parts = (message.text or "").split(maxsplit=1)
    server_name = parts[1].strip() if len(parts) > 1 else ""
    replied = message.reply_to_message
    media = None
    if replied:
        if replied.video:
            media = replied.video
        elif replied.document and (replied.document.mime_type or "").startswith("video/"):
            media = replied.document

    job_dir = os.path.join(DOWNLOAD_DIRECTORY, f"astream_{uid}_{int(time.time())}")
    os.makedirs(job_dir, exist_ok=True)
    source_path = None
    status = None
    try:
        if media:
            # Reply-to-video mode: download the actual Telegram media into this job folder.
            original_name = getattr(media, "file_name", None) or f"telegram_video_{replied.id}.mkv"
            safe_name = os.path.basename(original_name).replace("\x00", "")
            target_path = os.path.join(job_dir, safe_name)
            status = await message.reply_text("⬇️ **Downloading source video from Telegram…**")
            downloaded = await client.download_media(
                replied,
                file_name=target_path,
                progress=_aov_progress,
                progress_args=(status, time.time(), "⬇️ Downloading source video…"),
            )
            if not downloaded or not os.path.isfile(downloaded) or os.path.getsize(downloaded) <= 0:
                raise RuntimeError("Telegram video download failed or returned an empty file.")
            source_path = downloaded
            server_name = os.path.basename(source_path)
        else:
            # Existing-server-file mode remains available when no Telegram reply is attached.
            if not server_name:
                shutil.rmtree(job_dir, ignore_errors=True)
                return await message.reply_text(
                    "📥 **Reply to a Telegram video with `/AudioStream`**\n\n"
                    "Or use `/AudioStream ServerFileName` for a file already on the server."
                )
            existing_path = _audiostream_find_server_file(server_name)
            if not existing_path:
                shutil.rmtree(job_dir, ignore_errors=True)
                return await message.reply_text(
                    "❌ **Server file not found.**\n\n"
                    f"📁 Searched: `{DOWNLOAD_DIRECTORY}`\n"
                    f"File: `{os.path.basename(server_name)}`\n\n"
                    "Reply to the Telegram video with `/AudioStream` to download it automatically."
                )
            source_path = os.path.join(job_dir, os.path.basename(existing_path))
            shutil.copy2(existing_path, source_path)
            status = await message.reply_text("🔍 **Detecting audio tracks from server file…**")

        schedule_retention_cleanup(job_dir)
        await status.edit_text(
            "🔍 **Detecting audio tracks…**\n\n"
            f"📁 `{os.path.basename(source_path)}`\n"
            "✅ Source video is saved on the server."
        )
        tracks = await _audiostream_probe_tracks(source_path)
        if not tracks:
            raise RuntimeError("No audio tracks were found.")
        job = {
            "uid": uid, "chat_id": message.chat.id,
            "source": source_path, "source_name": os.path.basename(source_path),
            "base_name": os.path.splitext(os.path.basename(source_path))[0],
            "tracks": tracks, "dir": job_dir, "status": status,
        }
        _AUDIOSTREAM_JOBS[uid] = job
        rows = [f"🔊 **{t['label']}** — `{t['codec']}`" for t in tracks]
        await status.edit_text(
            "✅ **Audio Tracks Found**\n\n"
            f"📁 `{job['source_name']}`\n\n" + "\n".join(rows) +
            "\n\nSelect a separate Upload or Remove action for each track.\n"
            "🕒 Server files are retained for 3 hours.",
            reply_markup=_audiostream_keyboard(uid, job),
        )
    except Exception as ex:
        shutil.rmtree(job_dir, ignore_errors=True)
        if status:
            try:
                await status.edit_text(f"❌ **AudioStream failed**\n\n`{ex}`")
            except Exception:
                pass
        else:
            await message.reply_text(f"❌ **AudioStream failed**\n\n`{ex}`")


@app.on_callback_query(filters.regex(r"^astream:(\d+):(up|rm|refresh|video|cancel|rename|norename):?(.*)$"))
async def audiostream_callback(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    uid, action = int(parts[1]), parts[2]
    value = parts[3] if len(parts) > 3 else ""
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("This selection is not yours.", show_alert=True)
    job = _AUDIOSTREAM_JOBS.get(uid)
    if not job:
        return await cq.answer("Session expired. Run /AudioStream again.", show_alert=True)

    if action == "cancel":
        _AUDIOSTREAM_JOBS.pop(uid, None)
        _AUDIOSTREAM_PENDING.pop(uid, None)
        shutil.rmtree(job["dir"], ignore_errors=True)
        return await cq.message.edit_text("❌ **AudioStream cancelled.**", reply_markup=None)

    if action in ("up", "rm"):
        try:
            track = next(t for t in job["tracks"] if t["index"] == int(value))
        except (StopIteration, ValueError):
            return await cq.answer("Audio track not found.", show_alert=True)
        await cq.answer()
        try:
            if action == "up":
                await _audiostream_extract_audio(client, cq, job, track)
            else:
                await _audiostream_remove_track(cq, job, track)
        except Exception as ex:
            await cq.message.edit_text(
                f"❌ **{('Audio upload' if action == 'up' else 'Audio removal')} failed**\n\n`{ex}`",
                reply_markup=_audiostream_keyboard(uid, job),
            )
        return

    if action == "refresh":
        await cq.answer("Refreshing audio tracks...")
        try:
            job["tracks"] = await _audiostream_probe_tracks(job["source"])
            await cq.message.edit_text(
                "🔄 **Audio tracks refreshed.**\n\nSelect another action.",
                reply_markup=_audiostream_keyboard(uid, job),
            )
        except Exception as ex:
            await cq.message.edit_text(f"❌ Refresh failed: `{ex}`")
        return

    if action == "video":
        await cq.answer()
        _AUDIOSTREAM_PENDING[uid] = {"mode": "rename", "job": job}
        return await cq.message.reply_text(
            "✏️ **Send the new video filename** (extension optional).\n"
            "Default extension: `.mkv`\n"
            "To keep the original name, press **Keep original name**.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Keep original name", callback_data=f"astream:{uid}:norename")
            ]]),
        )

    if action == "norename":
        await cq.answer()
        _AUDIOSTREAM_PENDING.pop(uid, None)
        return await _audiostream_upload_video(client, cq.message, job, job["source_name"])


async def _audiostream_upload_video(client, status, job, requested_name):
    name = os.path.basename((requested_name or "").strip())
    if not name:
        name = job["source_name"]
    if not os.path.splitext(name)[1]:
        name += ".mp4"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".mkv", ".mp4"):
        name += ".mp4"
    upload_path = job["source"]
    # Preserve the server source; use a named copy only when the user renames it.
    if name != os.path.basename(upload_path):
        renamed_path = os.path.join(job["dir"], name)
        shutil.copy2(upload_path, renamed_path)
        upload_path = renamed_path
    await status.edit_text(f"📤 **Uploading video...**\n\n📁 `{name}`")
    try:
        duration = 0
        try:
            probe = await _ffprobe_video(upload_path)
            duration = int(float(probe.get("duration") or 0))
        except Exception:
            pass
        upload_progress_args = (
            status, time.time(), "📤 Uploading video..."
        )
        if os.path.splitext(name)[1].lower() == ".mp4":
            await client.send_video(
                chat_id=job["chat_id"], video=upload_path,
                caption=f"🎬 `{name}`",
                duration=duration or None,
                supports_streaming=True,
                progress=_aov_progress,
                progress_args=upload_progress_args,
            )
        else:
            await client.send_document(
                chat_id=job["chat_id"], document=upload_path,
                caption=f"🎬 `{name}`",
                progress=_aov_progress,
                progress_args=upload_progress_args,
            )
        await status.edit_text(
            "✅ **Video uploaded successfully.**\n\n"
            f"📁 `{name}`\n"
            f"🕒 Server temporary files follow the configured retention period ({RETENTION_HOURS} hours)."
        )
    except Exception as ex:
        await status.edit_text(f"❌ **Video upload failed**\n\n`{ex}`")
    finally:
        _AUDIOSTREAM_JOBS.pop(job["uid"], None)
        _AUDIOSTREAM_PENDING.pop(job["uid"], None)
        # Keep the working copy and extracted audio on the server until the
        # configured retention timer (3 hours by default) removes the folder.


_AUDIOSTREAM_PENDING_FILTER = filters.create(
    lambda _flt, _client, message:
        _message_actor_id(message) in _AUDIOSTREAM_PENDING
)

@app.on_message(filters.text & _AUDIOSTREAM_PENDING_FILTER)
async def audiostream_text_input(client: Client, message: Message):
    uid = _message_actor_id(message)
    pending = _AUDIOSTREAM_PENDING.get(uid)
    if not pending or pending.get("mode") != "rename":
        return
    job = _AUDIOSTREAM_JOBS.get(uid)
    if not job:
        _AUDIOSTREAM_PENDING.pop(uid, None)
        return
    name = (message.text or "").strip()
    if not name:
        return await message.reply_text("Please send a valid filename.")
    _AUDIOSTREAM_PENDING.pop(uid, None)
    status = await message.reply_text("📤 **Preparing video upload...**")
    await _audiostream_upload_video(client, status, job, name)


# ---------------------------------------------------------------------------
# /Hotstarhotstar — Hotstar/Hotstar M3U recorder
# ---------------------------------------------------------------------------
# The playlist URL/path is deliberately configurable.  No credential is
# generated or refreshed here: only values already present in the M3U entry
# are applied to the authorized stream request.
JIOOTT_M3U_URL = "https://github.com/Live-Anime-Cartoon-bot/CompressBot/raw/refs/heads/main/JioOTT.m3u"
JIOOTT_M3U_FILE = _environ.get("JIOOTT_M3U_FILE", "").strip()
HOTSTAR_REFRESH_SECONDS = 3 * 60 * 60
HOTSTAR_PARAM_GROUPS = {
    # JioOTT.m3u may use either spelling for the main parameterized group.
    "JioHotstar",
    "JioHotstar 1",
    "Jio TV+ | Kids",
    "Jio TV+|Kids",
}
HOTSTAR_DIRECT_GROUPS = {
    "JioHotstar Entertainment",
    "JioHotstar Kids",
}


def _hotstar_display_group(group: str) -> str:
    # The public /hotstar wizard exposes the main parameterized group simply
    # as JioHotstar, while accepting the legacy playlist spellings above.
    if group in {"JioHotstar", "JioHotstar 1", "Jio TV+ | Kids", "Jio TV+|Kids"}:
        return "JioHotstar"
    return group
hotstar_cache = {"channels": {}, "groups": {}, "entries": {}, "refreshed_at": 0.0, "error": ""}
hotstar_sessions = {}  # {(uid, status_message_id): session}
HOTSTAR_ACTIVE_TASKS = {}  # {(uid, status_message_id): asyncio.Task}
HOTSTAR_MAX_JOBS_PER_USER = 10
HOTSTAR_PENDING_RESERVATIONS = {}  # uid -> set of command reservation tokens
HOTSTAR_LIMIT_LOCK = asyncio.Lock()

def _hotstar_unlimited(uid: int) -> bool:
    # Owner/Anonymous Admin and active Premium users are unlimited.
    return bool(is_owner(uid) or is_premium(uid))

def _hotstar_active_count(uid: int) -> int:
    """Count every live Hotstar job/session exactly once.

    A Hotstar job remains in ``hotstar_sessions`` from wizard creation until
    recording/upload cleanup, so counting both sessions and tasks would count
    the same job twice.
    """
    uid = int(uid)
    return sum(1 for key in hotstar_sessions
               if isinstance(key, tuple) and key[0] == uid)

def _hotstar_pending_session_count(uid: int) -> int:
    uid = int(uid)
    return sum(1 for key in hotstar_sessions if isinstance(key, tuple) and key[0] == uid)

def _hotstar_get_session(cq, uid: int):
    # Each wizard is tied to its own Telegram status-message ID, so multiple
    # Hotstar wizards/jobs for the same user can coexist safely.
    msg_id = getattr(getattr(cq, "message", None), "id", None)
    key = (int(uid), int(msg_id)) if msg_id is not None else None
    if key is not None and key in hotstar_sessions:
        return key, hotstar_sessions[key]
    return None, None

def _hotstar_pop_user_sessions(uid: int) -> None:
    uid = int(uid)
    for key in list(hotstar_sessions):
        if isinstance(key, tuple) and key[0] == uid:
            hotstar_sessions.pop(key, None)

def _hotstar_pop_session(session: dict) -> None:
    key = session.get("job_key") if isinstance(session, dict) else None
    if key:
        hotstar_sessions.pop(key, None)

_hotstar_refresh_task = None


def _hotstar_safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    return (name or "Hotstar_Record")[:180]


def _hotstar_parse_duration(value: str) -> int:
    value = (value or "").strip()
    if not re.fullmatch(r"\d{1,3}:\d{2}:\d{2}", value):
        raise ValueError("Duration must be HH:MM:SS.")
    h, m, sec = map(int, value.split(":"))
    if m > 59 or sec > 59 or h < 0:
        raise ValueError("Invalid duration.")
    total = h * 3600 + m * 60 + sec
    if total <= 0:
        raise ValueError("Duration must be greater than 00:00:00.")
    return total


async def _hotstar_fetch_playlist() -> str:
    import urllib.request
    source = JIOOTT_M3U_URL or JIOOTT_M3U_FILE
    if not source:
        candidates = [
            os.path.join(_SCRIPT_DIR, "JioOTT.m3u"),
            os.path.join(DATA_DIRECTORY, "JioOTT.m3u"),
        ]
        source = next((p for p in candidates if os.path.isfile(p)), "")
    if not source:
        raise RuntimeError("JIOOTT_M3U_URL or JIOOTT_M3U_FILE is not configured, and JioOTT.m3u was not found.")
    if re.match(r"^https?://", source, re.I):
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        def _read():
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8", errors="replace")
        return await asyncio.to_thread(_read)
    return await asyncio.to_thread(lambda: open(source, encoding="utf-8", errors="replace").read())


def _hotstar_parse_m3u(text: str) -> tuple[dict, dict, dict]:
    # Reuse the bot's robust EXTINF parser.  Each entry retains the original
    # URL|properties string, so Cookie/Referer/Origin/User-Agent survive.
    return _parse_m3u_playlist(text)


async def _hotstar_refresh(force: bool = False) -> bool:
    """Refresh the complete JioOTT playlist without group-name filtering."""
    now = time.time()
    if (
        not force
        and hotstar_cache["entries"]
        and now - hotstar_cache["refreshed_at"] < HOTSTAR_REFRESH_SECONDS
    ):
        return True
    try:
        text = await _hotstar_fetch_playlist()

        # Reuse the existing complete M3U parser:
        # (channels, groups, entries) = parsed playlist data.
        channels, playlist_groups, entries = _hotstar_parse_m3u(text)
        if not entries:
            raise RuntimeError("JioOTT.m3u loaded but no valid channels were detected")

        # Keep every entry/group. Only normalize the legacy Hotstar group
        # aliases for display; no channel is filtered out.
        normalized_entries = {}
        groups = set()
        for key, item in entries.items():
            item = dict(item)
            group = _hotstar_display_group(
                str(item.get("group") or "Other")
            ).strip() or "Other"
            item["group"] = group
            normalized_entries[str(key)] = item
            groups.add(group)

        hotstar_cache["channels"] = channels
        hotstar_cache["entries"] = normalized_entries
        hotstar_cache["groups"] = sorted(groups, key=str.casefold)
        hotstar_cache["refreshed_at"] = now
        hotstar_cache["error"] = ""
        LOG.info(
            "JioOTT playlist refreshed: %d channels in %d groups",
            len(entries), len(groups)
        )
        return True
    except Exception as exc:
        hotstar_cache["error"] = str(exc)
        LOG.exception("JioOTT playlist refresh failed")
        return False


def _hotstar_m3u_headers(props: dict | None) -> dict:
    """Convert M3U URL properties into real HTTP headers.

    Supports the common IPTV property spellings used in JioOTT.m3u:
    User-Agent/UA, Referer/HTTP-Referrer, Origin and Cookie.
    No credentials are generated; only values supplied by the playlist are used.
    """
    props = props or {}
    headers = {}

    def get(*names):
        for name in names:
            value = props.get(name.lower())
            if value:
                return str(value).strip()
        return ""

    ua = get("user-agent", "useragent", "ua")
    referer = get("referer", "http-referrer", "http_referer", "referrer")
    origin = get("origin")
    cookie = get("cookie")

    if ua:
        headers["User-Agent"] = ua
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if cookie:
        headers["Cookie"] = cookie

    return headers


def _hotstar_header_text(headers: dict | None) -> str:
    """Format HTTP headers for FFmpeg/ffprobe -headers."""
    if not headers:
        return ""
    return "".join(
        f"{str(key).strip()}: {str(value).strip()}\r\n"
        for key, value in headers.items()
        if str(key).strip() and str(value).strip()
    )


async def _hotstar_probe_manifest(url: str, props: dict) -> dict:
    import urllib.request
    from urllib.parse import urljoin
    headers = _hotstar_m3u_headers(props)
    def _get(u):
        req = urllib.request.Request(u, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace"), resp.geturl()
    try:
        text, final_url = await asyncio.to_thread(_get, url)
    except Exception as first_exc:
        # Some authorized HLS origins reject Python/urllib manifest requests
        # with 403 even when the exact M3U-supplied headers are valid for
        # FFmpeg.  Retry probing through ffprobe, without adding or generating
        # any credential beyond the M3U entry parameters.
        try:
            header_text = _hotstar_header_text(headers)
            header_arg = shlex.quote(header_text) if header_text else "''"
            ua_arg = (f"-user_agent {shlex.quote(headers['User-Agent'])} "
                      if headers.get('User-Agent') else "")
            cmd = (f"ffprobe -v error -hide_banner -print_format json "
                   f"-headers {header_arg} {ua_arg}"
                   f"-show_streams -show_format {shlex.quote(url)}")
            rc, out, err = await runcmd(cmd)
            if rc != 0:
                raise RuntimeError((err or str(first_exc))[-1800:])
            data = json.loads(out or "{}")
            v = next((x for x in data.get("streams", [])
                      if x.get("codec_type") == "video"), None)
            astreams = [x for x in data.get("streams", [])
                        if x.get("codec_type") == "audio"]
            height = int((v or {}).get("height") or 0)
            detected_audio = []
            for idx, a in enumerate(astreams, 1):
                lang = a.get("tags", {}).get("language", "")
                name = a.get("tags", {}).get("title", "") or lang or f"Audio {idx}"
                detected_audio.append({
                    "label": name,
                    "lang": lang,
                    "url": "",
                    "group": "",
                    "stream_index": int(a.get("index", idx - 1)),
                })
            return {
                "qualities": [{
                    "label": f"{height}p" if height else "Source",
                    "height": height, "bandwidth": 0,
                    "url": url, "audio_group": "",
                    "source_type": "ffprobe",
                    "video_stream_index": int((v or {}).get("index", 0)),
                }],
                "audios": detected_audio,
                "final_url": url,
                "probe_fallback": True,
            }
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Manifest access failed ({first_exc}); ffprobe fallback failed ({fallback_exc})."
            )
    qualities, audios = [], []
    lines = [x.strip() for x in text.splitlines()]
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = {}
        for m in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]):
            attrs[m.group(1)] = m.group(2).strip('"')
        uri = lines[i + 1] if i + 1 < len(lines) and not lines[i + 1].startswith("#") else ""
        if not uri:
            continue
        res = attrs.get("RESOLUTION", "")
        try: height = int(res.split("x", 1)[1]) if "x" in res else 0
        except Exception: height = 0
        bw = int(attrs.get("BANDWIDTH", "0") or 0)
        qualities.append({"label": f"{height}p" if height else (f"{round(bw/1000)}k" if bw else "Source"),
                          "height": height, "bandwidth": bw, "url": urljoin(final_url, uri),
                          "audio_group": attrs.get("AUDIO", "")})
    audio_by_group = {}
    for line in lines:
        if not line.startswith("#EXT-X-MEDIA:") or "TYPE=AUDIO" not in line:
            continue
        attrs = {}
        for m in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]):
            attrs[m.group(1)] = m.group(2).strip('"')
        gid = attrs.get("GROUP-ID", "")
        uri = attrs.get("URI", "")
        label = attrs.get("NAME") or attrs.get("LANGUAGE") or "Audio"
        item = {"label": label, "lang": attrs.get("LANGUAGE", ""),
                "url": urljoin(final_url, uri) if uri else "", "group": gid}
        audio_by_group.setdefault(gid, []).append(item)
    # Keep all source-declared audio renditions, preserving their GROUP-ID so
    # the selected quality can be matched to its own audio group dynamically.
    audios = [a for group_items in audio_by_group.values() for a in group_items]
    if not qualities:
        # Media playlist / direct M3U8: still provide one dynamically detected
        # source option.  FFprobe is used only to label the available source.
        height = 0
        try:
            cmd = (f"ffprobe -v error -select_streams v:0 -show_entries stream=height "
                   f"-of default=noprint_wrappers=1:nokey=1 {shlex.quote(url)}")
            rc, out, _ = await runcmd(cmd)
            if rc == 0: height = int((out or "0").strip() or 0)
        except Exception: pass
        qualities = [{"label": f"{height}p" if height else "Source", "height": height,
                      "bandwidth": 0, "url": final_url, "audio_group": ""}]
    qualities.sort(key=lambda x: (x.get("height", 0), x.get("bandwidth", 0)))
    return {"qualities": qualities, "audios": audios, "final_url": final_url}


def _hotstar_ffmpeg_input(url: str, props: dict) -> str:
    headers = _hotstar_m3u_headers(props)
    parts = []
    if "User-Agent" in headers:
        parts += ["-user_agent", shlex.quote(headers["User-Agent"])]
    non_ua = {k: v for k, v in headers.items() if k != "User-Agent"}
    if non_ua:
        parts += ["-headers", shlex.quote(_hotstar_header_text(non_ua))]
    parts += ["-i", shlex.quote(url)]
    return " ".join(parts)


async def _hotstar_record(client: Client, message: Message, session: dict, uid_override=None):
    # The wizard status message is authored by the bot, so _message_actor_id(status_msg)
    # cannot identify the real job owner (especially for Anonymous Admin). Always
    # prefer the UID captured by the wizard callback.
    uid = int(uid_override) if uid_override is not None else _message_actor_id(message)
    save_dir = join(DOWNLOAD_DIRECTORY, f"hotstar_{int(time.time())}_{uid}")
    os.makedirs(save_dir, exist_ok=True)
    status = session["status_msg"]
    out_name = _hotstar_safe_name(session.get("filename") or session.get("channel_name") or "Hotstar_Record") + ".mkv"
    out_path = join(save_dir, out_name)
    duration = int(session["duration"])
    # Keep all authorization properties captured from the playlist.
    # They may be required by signed Hotstar manifests.
    props = session.get("props") or {}

    # Normally the channel callback stores qualities after manifest probing.
    # If a stale/partially-created session reaches Record, recover by probing
    # the authorized stream URL once instead of crashing with KeyError('qualities').
    qualities = session.get("qualities") or []
    if not qualities:
        fallback_url = session.get("url") or ""
        if fallback_url:
            probe = await _hotstar_probe_manifest(fallback_url, props)
            qualities = probe.get("qualities") or []
            if qualities:
                session["qualities"] = qualities
                session.setdefault("audios", probe.get("audios") or [])
                session.setdefault("quality_index", 0)
                session.setdefault("audio_indices", [])
        if not qualities:
            raise RuntimeError("Hotstar quality data is unavailable. Please select the channel again.")

    q_idx = int(session.get("quality_index", 0) or 0)
    q_idx = max(0, min(q_idx, len(qualities) - 1))
    session["quality_index"] = q_idx
    q = qualities[q_idx]
    url = q.get("url") or session.get("url")
    if not url:
        raise RuntimeError("Hotstar stream URL is unavailable. Please select the channel again.")
    input_part = _hotstar_ffmpeg_input(url, props)
    # Map every audio rendition the user selected. HLS master playlists often
    # expose each language as a separate audio playlist; mapping only selected[0]
    # silently dropped the remaining selected languages from the final MKV.
    audios = session.get("audios") or []
    selected_audio = []
    for raw_idx in (session.get("audio_indices") or []):
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(audios) and idx not in selected_audio:
            selected_audio.append(idx)

    video_map = (
        f"-map 0:{int(q.get('video_stream_index', 0))}"
        if q.get("source_type") == "ffprobe"
        else "-map 0:v:0"
    )
    input_parts = [input_part]
    map_args = [video_map]
    external_audio_count = 0
    mapped_audio_items = []

    if selected_audio:
        for audio_idx in selected_audio:
            audio_item = audios[audio_idx]
            audio_url = str(audio_item.get("url") or "").strip()
            stream_index = audio_item.get("stream_index")
            if audio_url:
                input_parts.append(_hotstar_ffmpeg_input(audio_url, props))
                external_audio_count += 1
                map_args.append(f"-map {external_audio_count}:a:0?")
                mapped_audio_items.append(audio_item)
            elif stream_index is not None:
                map_args.append(f"-map 0:{int(stream_index)}?")
                mapped_audio_items.append(audio_item)
            else:
                map_args.append(f"-map 0:a:{audio_idx}?")
                mapped_audio_items.append(audio_item)
    else:
        audio_group = str(q.get("audio_group") or "")
        group_audio = [
            a for a in audios
            if audio_group and str(a.get("group") or "") == audio_group and a.get("url")
        ]
        if group_audio:
            for audio_item in group_audio:
                input_parts.append(_hotstar_ffmpeg_input(str(audio_item["url"]), props))
                external_audio_count += 1
                map_args.append(f"-map {external_audio_count}:a:0?")
                mapped_audio_items.append(audio_item)
        else:
            map_args.append("-map 0:a?")

    # Audio output indexes are the order in which audio maps were added.
    # Owner and normal users intentionally receive different metadata formats.
    hotstar_meta_args = []
    is_actual_owner = int(uid) in OWNER_IDS
    for out_audio_idx, audio_item in enumerate(mapped_audio_items):
        lang_raw = str(audio_item.get("lang") or audio_item.get("label") or "Untagged").strip()
        lang_display = LANG_LABEL.get(lang_raw.lower()[:3], lang_raw)
        if is_actual_owner:
            combined = f"{OWNER_AUDIO_BRAND}|{lang_display}|{OWNER_AUDIO_TITLE}"
        else:
            combined = f"{USER_AUDIO_BRAND}|{lang_display}"
        hotstar_meta_args += [f"-metadata:s:a:{out_audio_idx}", f"title={combined}",
                              f"-metadata:s:a:{out_audio_idx}", f"handler_name={combined}"]
        if audio_item.get("lang"):
            hotstar_meta_args += [f"-metadata:s:a:{out_audio_idx}", f"language={str(audio_item['lang'])[:3].lower()}"]

    cmd = (
        "ffmpeg -hide_banner -loglevel error -y -nostdin "
        + " ".join(input_parts)
        + f" -t {duration} "
        + " ".join(map_args)
        + " " + " ".join(shlex.quote(x) for x in hotstar_meta_args)
        + " -c copy -progress pipe:1 -nostats "
        + shlex.quote(out_path)
    )
    try:
        await status.edit_text("📥 **Recording**\n`[░░░░░░░░░░░░░░░░░░░░] 0% / 100%`")
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Drain stderr concurrently.  Waiting to read stderr until stdout EOF
        # can fill the OS pipe and make FFmpeg block mid-recording.
        stderr_task = asyncio.create_task(proc.stderr.read())
        last_edit = 0.0
        rc = None
        try:
            while True:
                line = await proc.stdout.readline()

                if not line:
                    rc = await proc.wait()
                    break

                text = line.decode(errors="ignore").strip()

                if text.startswith("out_time_ms="):
                    try:
                        cur = int(text.split("=", 1)[1]) / 1_000_000
                        pct = min(100.0, cur / max(duration, 1) * 100)

                        if time.time() - last_edit >= 3:
                            filled = min(20, int(pct // 5))
                            bar = "▓" * filled + "░" * (20 - filled)

                            await status.edit_text(
                                f"📥 **Recording**\\n"
                                f"`[{bar}] {pct:.0f}% / 100%`"
                            )
                            last_edit = time.time()
                    except Exception:
                        pass

            stderr_bytes = await stderr_task
        except asyncio.CancelledError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            if not stderr_task.done():
                stderr_task.cancel()
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()

        stderr = stderr_bytes.decode(errors="ignore") if stderr_bytes else ""
        if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(stderr[-1800:] or f"FFmpeg exited with code {rc}")
        await status.edit_text("⚙️ **Processing**")
        thumb = join(save_dir, "thumb.jpg")
        await runcmd(f"ffmpeg -hide_banner -loglevel error -nostats -y -ss {max(1, duration//2)} "
                     f"-i {shlex.quote(out_path)} -frames:v 1 -q:v 2 {shlex.quote(thumb)}")
        thumb_path = thumb if os.path.exists(thumb) else None
        await status.edit_text("🖼 **Generating thumbnail**")
        caption = (f"🎬 **JioHotstar Recording**\n\n"
                   f"📺 `{session.get('channel_name','')}`\n"
                   f"🎞 `{q.get('label','Source')}`\n"
                   f"⏱ `{session.get('duration_str')}`\n"
                   f"📁 `{out_name}`\n\n"
                   f"_Auto-deleted from server after {_retention_label()}._")
        await status.edit_text("📤 **Uploading**\n`[░░░░░░░░░░░░░░░░░░░░] 0% / 100%`")

        # IMPORTANT: Do not await the long Telegram upload inside the recording
        # handler. Pyrogram can spend a long time transferring a large media file;
        # keeping this handler attached to that await made the bot appear frozen
        # to the user while the upload was active. Run only the Hotstar upload in
        # a background task so the main dispatcher remains responsive to /start,
        # /status, /cancel and other commands.
        upload_detached = True

        async def _hotstar_upload_worker():
            try:
                upload_start = time.time()
                await split_and_send_video(
                    status, out_path, caption, duration,
                    thumb_path=thumb_path, status_msg=status,
                    progress=progress_for_pyrogram,
                    progress_args=(status, upload_start, status, save_dir, False),
                    _uid=uid, _chat_id=status.chat.id,
                )
                try:
                    await status.edit_text("✅ **Processing Complete**")
                except Exception:
                    pass
                LOG.info("Hotstar upload worker completed uid=%s file=%s", uid, out_path)
            except asyncio.CancelledError:
                LOG.warning("Hotstar upload worker cancelled uid=%s file=%s", uid, out_path)
                raise
            except Exception as exc:
                LOG.error("Hotstar upload worker failed uid=%s file=%s: %s", uid, out_path, exc, exc_info=True)
                try:
                    await status.edit_text(
                        f"❌ **Upload failed**\n\n`{str(exc)[-1800:]}`\n\n"
                        "The recording is kept on the server for retry."
                    )
                except Exception:
                    pass
            finally:
                _hotstar_pop_session(session)
                # Hotstar jobs are tracked independently from the legacy
                # single-job user_tasks map. Never clear user_tasks here because
                # it may belong to another command running for the same user.
                if save_dir and os.path.exists(save_dir):
                    # Keep the completed/failed recording for retention; upload
                    # cancellation must not delete a potentially useful file.
                    schedule_retention_cleanup(save_dir)

        upload_task = asyncio.create_task(_hotstar_upload_worker(), name=f"hotstar-upload-{uid}")
        HOTSTAR_ACTIVE_TASKS[session.get("job_key")] = upload_task
        def _hotstar_upload_done(done_task, _job_key=session.get("job_key")):
            current = HOTSTAR_ACTIVE_TASKS.get(_job_key)
            if current is done_task:
                HOTSTAR_ACTIVE_TASKS.pop(_job_key, None)
            if done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except Exception:
                exc = None
            if exc:
                LOG.error("Hotstar upload task failed job=%s: %s", _job_key, exc, exc_info=True)
        upload_task.add_done_callback(_hotstar_upload_done)
        LOG.info("Hotstar upload detached from command handler uid=%s file=%s", uid, out_path)
        return
    finally:
        if not locals().get("upload_detached", False):
            _hotstar_pop_session(session)
            user_tasks.pop(uid, None)
            if save_dir and os.path.exists(save_dir):
                schedule_retention_cleanup(save_dir)


@app.on_message(filters.command("hotstar"))
async def hotstar_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if not is_verified(uid):
        return await message.reply_text("You must be **verified** to use /hotstar. Run /verify.")
    try:
        args = list(message.command[1:])
        if not args:
            raise ValueError("Usage: /hotstar HH:MM:SS -f File_Name")
        duration_str = args[0]
        duration = _hotstar_parse_duration(duration_str)
        filename = ""
        i = 1
        while i < len(args):
            if args[i] == "-f" and i + 1 < len(args):
                filename = args[i + 1]; i += 2
            else:
                i += 1
        filename = _hotstar_safe_name(filename)
    except Exception as exc:
        return await message.reply_text(f"❌ `{exc}`")
    _hotstar_reservation = None
    if not _hotstar_unlimited(uid):
        async with HOTSTAR_LIMIT_LOCK:
            active = _hotstar_active_count(uid)
            pending = len(HOTSTAR_PENDING_RESERVATIONS.get(uid, set()))
            total = active + pending
            if total >= HOTSTAR_MAX_JOBS_PER_USER:
                return await message.reply_text(
                    "❌ **Maximum 10 active Hotstar jobs allowed.**\n"
                    f"Your active Hotstar jobs: `{total}/10`"
                )
            _hotstar_reservation = pysecrets.token_hex(6)
            HOTSTAR_PENDING_RESERVATIONS.setdefault(uid, set()).add(_hotstar_reservation)
    try:
        if not await _hotstar_refresh():
            return await message.reply_text(f"❌ JioOTT.m3u refresh failed.\n`{hotstar_cache.get('error','')}`")
        status = await message.reply_text(f"🔄 **Fresh JioOTT.m3u loaded**\n\nSelect a group:")
        # Keep invocation data in the session only after the group menu exists.
        job_key = (uid, int(status.id))
        hotstar_sessions[job_key] = {"duration": duration, "duration_str": duration_str,
                                      "filename": filename, "status_msg": status,
                                      "job_key": job_key}
        # Build the group menu from every group present in the fresh playlist.
        sess = hotstar_sessions[job_key]
        sess["group_tokens"] = {}
        rows, row = [], []
        for group in hotstar_cache["groups"]:
            token = pysecrets.token_hex(4)
            sess["group_tokens"][token] = group
            row.append(InlineKeyboardButton(
                f"📁 {group}", callback_data=f"hsg:{uid}:{token}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if not rows:
            rows = [[InlineKeyboardButton(
                "❌ No JioOTT groups", callback_data=f"hsgroups:{uid}"
            )]]
        await status.edit_text(
            "🔄 **Fresh JioOTT.m3u loaded**\n\n📁 **All detected groups:**",
            reply_markup=InlineKeyboardMarkup(rows)
        )
    finally:
        if _hotstar_reservation:
            pending = HOTSTAR_PENDING_RESERVATIONS.get(uid)
            if pending:
                pending.discard(_hotstar_reservation)
                if not pending:
                    HOTSTAR_PENDING_RESERVATIONS.pop(uid, None)


HOTSTAR_CHANNELS_PER_PAGE = 12


def _hotstar_channel_page_kb(cq, uid: int, group: str, page: int = 0):
    items = [
        x for x in hotstar_cache["entries"].values()
        if _hotstar_display_group(str(x.get("group") or "Other")) == group
    ]
    items.sort(key=lambda x: str(x.get("name") or "").casefold())

    total_pages = max(1, (len(items) + HOTSTAR_CHANNELS_PER_PAGE - 1) // HOTSTAR_CHANNELS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = items[
        page * HOTSTAR_CHANNELS_PER_PAGE:
        (page + 1) * HOTSTAR_CHANNELS_PER_PAGE
    ]

    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess:
        return None, 0, 0

    sess["channel_tokens"] = {}
    rows, row = [], []

    for item in page_items:
        token = pysecrets.token_hex(4)
        sess["channel_tokens"][token] = item["name"]
        row.append(
            InlineKeyboardButton(
                f"📺 {str(item['name'])[:38]}",
                callback_data=f"hsc:{uid}:{token}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"hsp:{uid}:{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"hsp:{uid}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("⬅️ Groups", callback_data=f"hsgroups:{uid}")])
    return InlineKeyboardMarkup(rows), page + 1, total_pages


@app.on_callback_query(filters.regex(r"^hsp:(-?\d+):(\d+)$"))
async def hotstar_channel_page_cb(client: Client, cq: CallbackQuery):
    uid, page = int(cq.data.split(":")[1]), int(cq.data.split(":")[2])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your job.", show_alert=True)

    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess:
        return await cq.answer("Session expired.", show_alert=True)

    group = str(sess.get("group") or "")
    if not group:
        return await cq.answer("Group session expired.", show_alert=True)

    kb, shown_page, total_pages = _hotstar_channel_page_kb(cq, uid, group, page)
    if kb is None:
        return await cq.answer("Session expired.", show_alert=True)

    await cq.answer()
    await _hotstar_safe_edit(
        cq.message,
        f"📁 **{group}**\\n\\nSelect a channel:\\nPage `{shown_page}/{total_pages}`",
        reply_markup=kb,
    )


@app.on_callback_query(filters.regex(r"^hsg:(-?\d+):([0-9a-f]+)$"))
async def hotstar_group_cb(client: Client, cq: CallbackQuery):
    uid, token = int(cq.data.split(":")[1]), cq.data.split(":")[2]
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("This is not your job.", show_alert=True)

    job_key, sess = _hotstar_get_session(cq, uid)
    group = (sess or {}).get("group_tokens", {}).get(token)
    if not sess or not group:
        return await cq.answer("Session expired.", show_alert=True)

    sess["group"] = group
    kb, shown_page, total_pages = _hotstar_channel_page_kb(cq, uid, group, 0)
    if kb is None:
        return await cq.answer("Session expired.", show_alert=True)

    await cq.answer()
    await _hotstar_safe_edit(
        cq.message,
        f"📁 **{group}**\\n\\nSelect a channel:\\nPage `{shown_page}/{total_pages}`",
        reply_markup=kb,
    )


@app.on_callback_query(filters.regex(r"^hsgroups:(-?\d+)$"))
async def hotstar_groups_cb(client: Client, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your job.", show_alert=True)

    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess:
        return await cq.answer("Session expired.", show_alert=True)

    sess["group_tokens"] = {}
    sess["channel_tokens"] = {}
    rows, row = [], []

    for group in hotstar_cache["groups"]:
        token = pysecrets.token_hex(4)
        sess["group_tokens"][token] = group
        row.append(InlineKeyboardButton(
            f"📁 {group}", callback_data=f"hsg:{uid}:{token}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if not rows:
        rows = [[InlineKeyboardButton(
            "❌ No JioOTT groups", callback_data=f"hsgroups:{uid}"
        )]]

    await cq.answer()
    await _hotstar_safe_edit(
        cq.message,
        "📁 **Jio Hotstar / JioOTT Groups**\n\nSelect a group:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@app.on_callback_query(filters.regex(r"^hsc:(-?\d+):([0-9a-f]+)$"))
async def hotstar_channel_cb(client: Client, cq: CallbackQuery):
    uid, token = int(cq.data.split(":")[1]), cq.data.split(":")[2]
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    job_key, sess = _hotstar_get_session(cq, uid)
    name = (sess or {}).get("channel_tokens", {}).get(token)
    if not sess or not name: return await cq.answer("Session expired.", show_alert=True)
    item = next((x for x in hotstar_cache["entries"].values() if x["name"] == name), None)
    if not item: return await cq.answer("Channel no longer exists after refresh.", show_alert=True)
    raw_url = item["url"]
    url, props = _split_stream_properties(raw_url)
    # Use authorized properties whenever the playlist entry supplies them.
    # This keeps newly added groups working without another hard-coded group
    # name update.
    # Always preserve authorization properties supplied by the playlist.
    # Do not discard them merely because the channel group is not hard-coded.
    apply_params = bool(props)
    sess.update({"channel_name": name, "group": _hotstar_display_group(item["group"]), "url": url,
                 "props": props, "apply_params": apply_params})
    await cq.answer("🔍 Detecting quality/audio…")
    try:
        probe = await _hotstar_probe_manifest(url, props)
        sess.update({"qualities": probe["qualities"], "audios": probe["audios"], "quality_index": 0, "audio_indices": []})
        await cq.message.edit_text(_hotstar_status(sess), reply_markup=_hotstar_quality_kb(uid, sess))
    except Exception as exc:
        # A second click can legitimately request the exact same error text.
        # Telegram then returns MESSAGE_NOT_MODIFIED; never let that mask the
        # Keep the original Hotstar probe error visible.
        await _hotstar_safe_edit(cq.message, f"❌ Quality detection failed.\n`{str(exc)[:1800]}`")


@app.on_callback_query(filters.regex(r"^hsq:(-?\d+):(\d+)$"))
async def hotstar_quality_cb(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":"); uid, idx = int(parts[1]), int(parts[2])
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess or idx >= len(sess.get("qualities") or []): return await cq.answer("Session expired.", show_alert=True)
    sess["quality_index"] = idx; await cq.answer(f"Quality: {sess['qualities'][idx]['label']}")
    await cq.message.edit_text(_hotstar_status(sess), reply_markup=_hotstar_quality_kb(uid, sess))




def _hotstar_quality_kb(uid: int, sess: dict):
    qualities = sess.get("qualities") or []
    selected = int(sess.get("quality_index") or 0)
    rows = []

    for i, q in enumerate(qualities):
        label = str(q.get("label") or f"Quality {i + 1}")
        prefix = "✅ " if i == selected else ""
        rows.append([
            InlineKeyboardButton(
                f"{prefix}{label}",
                callback_data=f"hsq:{uid}:{i}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🎵 Audio Tracks",
            callback_data=f"hsa:{uid}:menu"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "▶️ Start Recording",
            callback_data=f"hsgo:{uid}"
        ),
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"hscancel:{uid}"
        )
    ])

    return InlineKeyboardMarkup(rows)


def _hotstar_audio_kb(uid: int, sess: dict):
    audios = sess.get("audios") or []
    selected = set(int(x) for x in (sess.get("audio_indices") or []))
    rows = []

    for i, audio in enumerate(audios):
        label = str(
            audio.get("label")
            or audio.get("language")
            or f"Audio {i + 1}"
        )
        prefix = "✅ " if i in selected else ""

        rows.append([
            InlineKeyboardButton(
                f"{prefix}{label}",
                callback_data=f"hsa:{uid}:{i}"
            )
        ])

    if audios:
        rows.append([
            InlineKeyboardButton(
                "🧹 Clear Selection",
                callback_data=f"hsa:{uid}:clear"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data=f"hsback:{uid}"
        )
    ])

    return InlineKeyboardMarkup(rows)


def _hotstar_status(sess: dict) -> str:
    """Render the current Hotstar quality/audio selection state."""
    channel = str(sess.get("channel") or sess.get("name") or "Unknown Channel")
    duration = str(sess.get("duration_text") or "00:00:00")

    qualities = sess.get("qualities") or []
    qidx = int(sess.get("quality_index") or 0)
    if qualities and 0 <= qidx < len(qualities):
        quality = str(qualities[qidx].get("label") or "Auto")
    else:
        quality = "Auto"

    audios = sess.get("audios") or []
    selected = sess.get("audio_indices") or []
    if selected and audios:
        names = []
        for i in selected:
            if 0 <= int(i) < len(audios):
                names.append(str(audios[int(i)].get("label") or audios[int(i)].get("language") or "Unknown"))
        audio_text = ", ".join(names) if names else "Auto"
    elif audios:
        audio_text = "Auto"
    else:
        audio_text = "Auto"

    return (
        "🎬 **Jio Hotstar Recording**\\n\\n"
        f"📺 Channel: **{channel}**\\n"
        f"⏱ Duration: `{duration}`\\n"
        f"🎞 Quality: **{quality}**\\n"
        f"🔊 Audio: **{audio_text}**"
    )


async def _hotstar_safe_edit(message, text, reply_markup=None):
    """Edit a wizard message without crashing on duplicate/same-state clicks."""
    try:
        current = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if current == text and reply_markup is None:
            return message
        return await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        # Telegram returns MESSAGE_NOT_MODIFIED when the requested state is already shown.
        if "MESSAGE_NOT_MODIFIED" in str(exc):
            return message
        raise


@app.on_callback_query(filters.regex(r"^hsa:(-?\d+):(menu|clear|\d+)$"))
async def hotstar_audio_cb(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":"); uid, val = int(parts[1]), parts[2]
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess: return await cq.answer("Session expired.", show_alert=True)
    if val == "menu":
        await cq.answer(); return await _hotstar_safe_edit(cq.message, "🎵 **Select Audio**", _hotstar_audio_kb(uid, sess))
    if val == "clear": sess["audio_indices"] = []
    else:
        idx = int(val)
        selected = sess.setdefault("audio_indices", [])
        if idx in selected: selected.remove(idx)
        else: selected.append(idx)
    await cq.answer(); await _hotstar_safe_edit(cq.message, "🎵 **Select Audio**", _hotstar_audio_kb(uid, sess))


@app.on_callback_query(filters.regex(r"^hsback:(-?\d+)$"))
async def hotstar_back_cb(client: Client, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1]); job_key, sess = _hotstar_get_session(cq, uid)
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    if not sess: return await cq.answer("Session expired.", show_alert=True)
    await cq.answer(); await cq.message.edit_text(_hotstar_status(sess), reply_markup=_hotstar_quality_kb(uid, sess))


@app.on_callback_query(filters.regex(r"^hscancel:(-?\d+)$"))
async def hotstar_cancel_cb(client: Client, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    job_key, sess = _hotstar_get_session(cq, uid)
    if not job_key or not sess:
        return await cq.answer("Session expired.", show_alert=True)
    hotstar_sessions.pop(job_key, None)
    task = HOTSTAR_ACTIVE_TASKS.pop(job_key, None)
    if task and not task.done():
        task.cancel()
    await cq.answer("Cancelled."); await cq.message.edit_text("❌ JioHotstar recording cancelled.", reply_markup=None)


@app.on_callback_query(filters.regex(r"^hsgo:(-?\d+)$"))
async def hotstar_go_cb(client: Client, cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    if not _callback_actor_matches(cq, uid): return await cq.answer("Not your job.", show_alert=True)
    job_key, sess = _hotstar_get_session(cq, uid)
    if not sess: return await cq.answer("Session expired.", show_alert=True)
    if not job_key: return await cq.answer("Session expired.", show_alert=True)
    await cq.answer("▶️ Recording started")
    task = asyncio.create_task(
        _hotstar_record(client, sess["status_msg"], sess, uid_override=uid),
        name=f"hotstar-record-{uid}-{sess['status_msg'].id}",
    )
    HOTSTAR_ACTIVE_TASKS[job_key] = task

    def _hotstar_task_done(done_task, _job_key=job_key):
        current = HOTSTAR_ACTIVE_TASKS.get(_job_key)
        if current is done_task:
            HOTSTAR_ACTIVE_TASKS.pop(_job_key, None)
        if done_task.cancelled():
            return
        try:
            exc = done_task.exception()
        except Exception:
            exc = None
        if exc:
            LOG.error("Hotstar recording task failed job=%s: %s", _job_key, exc, exc_info=True)

    task.add_done_callback(_hotstar_task_done)


async def _hotstar_refresh_loop():
    while True:
        try: await _hotstar_refresh(force=True)
        except Exception as exc: LOG.warning("Hotstar refresh loop: %s", exc)
        await asyncio.sleep(HOTSTAR_REFRESH_SECONDS)

# /download — OTT downloader with manifest-probed quality + multi-audio wizard
# ---------------------------------------------------------------------------

@app.on_message(filters.command("download"))
async def download_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'download')
    if _six_job_error:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, _six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not is_verified(user_id):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "You must be **verified** to use /download. Run /verify.")
    if len(message.command) < 2:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "**Usage:** `/download <url> [filename]`\n\n"
            "Example:\n`/download https://www.hotstar.com/1260093240 MyShow`\n\n"
            "Supported: Hotstar, JioCinema, ZEE5, SonyLIV, Voot, MX Player, YouTube, and more.\n\n"
            "For login-gated content, upload cookies first with /set_cookies."
        )

    parts        = message.text.split(maxsplit=2)
    url          = parts[1].strip()
    raw_filename = parts[2].strip() if len(parts) > 2 else ""
    for bad in '/\\:*?"<>|':
        raw_filename = raw_filename.replace(bad, "_")

    cookies_path = _user_cookies_path(user_id) if _user_has_cookies(user_id) else ""

    probe_msg = await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
        f"🔍 **Probing manifest…**\n\n"
        f"🔗 `{url[:60]}{'…' if len(url)>60 else ''}`\n\n"
        f"_Fetching available video qualities and audio tracks…_"
    )

    try:
        probe = await asyncio.to_thread(_probe_url_formats, url, cookies_path)
    except Exception as _ex:
        LOG.warning(f"Manifest probe failed uid={user_id}: {_ex}")
        probe = {"title": "", "video_formats": [], "audio_formats": []}

    vfmts = probe["video_formats"]
    afmts = probe["audio_formats"]
    title = probe.get("title") or ""

    # Pre-select audio tracks matching user's default language preference
    _pref_lang = (user_dl_prefs.get(user_id) or {}).get("default_audio_lang", "")
    _pre_sel_audio: list = []
    if _pref_lang and afmts:
        for _f in afmts:
            if _pref_lang.lower() in (_f.get("lang_name") or "").lower():
                _pre_sel_audio.append(_f["id"])

    ott_sessions[user_id] = {
        "url":           url,
        "filename":      raw_filename,
        "message":       message,
        "msg":           probe_msg,
        "cookies_path":  cookies_path,
        "phase":         "video",
        "probe_title":   title,
        "video_formats": vfmts,
        "audio_formats": afmts,
        "sel_video_id":  "best",
        "sel_audio_ids": _pre_sel_audio,
        "default_lang":  _pref_lang,
    }

    if vfmts:
        text = _dl_status_text(ott_sessions[user_id])
        kb   = _dl_video_keyboard(user_id, vfmts, "best")
    else:
        text = (
            f"🎬 **Select Quality**\n\n"
            f"🔗 `{url[:60]}{'…' if len(url)>60 else ''}`\n"
            + (f"📌 **{title[:60]}**\n\n" if title else "\n")
            + f"_(Manifest not parseable — choose generic quality)_"
        )
        kb = _dl_fallback_keyboard(user_id, "best")

    try:
        await probe_msg.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^dl:(\d+):([^:]+):?(.*)$"))
async def cb_dl_wizard(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":", 3)
    uid    = int(parts[1])
    action = parts[2]
    value  = parts[3] if len(parts) > 3 else ""

    if not _callback_actor_matches(cq, uid):
        return await cq.answer("This is not your download.", show_alert=True)

    sess = ott_sessions.get(uid)
    if not sess:
        await cq.answer("Session expired. Run /download again.", show_alert=True)
        try:
            await cq.message.delete()
        except Exception:
            pass
        return

    vfmts = sess.get("video_formats") or []
    afmts = sess.get("audio_formats") or []

    if action == "cancel":
        ott_sessions.pop(uid, None)
        await cq.answer("Cancelled.")
        try:
            await cq.message.edit_text("❌ Download cancelled.", reply_markup=None)
        except Exception:
            pass
        return

    if action == "phase":
        sess["phase"] = value
        await cq.answer()
        if value == "audio":
            if not afmts:
                return await cq.answer("No separate audio tracks found — best audio will be used.", show_alert=True)
            try:
                await cq.message.edit_text(
                    _dl_status_text(sess),
                    reply_markup=_dl_audio_keyboard(
                        uid, afmts, sess.get("sel_audio_ids") or [],
                        default_lang=sess.get("default_lang") or ""
                    )
                )
            except Exception:
                pass
        else:
            try:
                await cq.message.edit_text(
                    _dl_status_text(sess) if vfmts else f"🎬 **Select Quality**\n\n🔗 `{sess.get('url','')[:55]}`",
                    reply_markup=(_dl_video_keyboard(uid, vfmts, sess.get("sel_video_id") or "best")
                                  if vfmts else _dl_fallback_keyboard(uid, sess.get("sel_video_id") or "best"))
                )
            except Exception:
                pass
        return

    if action == "v":
        sess["sel_video_id"] = value
        qlabel = ("Best Available" if value == "best"
                  else next((f"{f['height']}p" for f in vfmts if f["id"] == value), value))
        await cq.answer(f"🎥 {qlabel}")
        try:
            await cq.message.edit_text(
                _dl_status_text(sess),
                reply_markup=_dl_video_keyboard(uid, vfmts, value) if vfmts else _dl_fallback_keyboard(uid, value)
            )
        except Exception:
            pass
        return

    _def_lang = sess.get("default_lang") or ""

    if action == "a":
        sel = sess.setdefault("sel_audio_ids", [])
        if value in sel:
            sel.remove(value)
            toggled = False
        else:
            sel.append(value)
            toggled = True
        alabel = next((f"{f['lang_name']} {f['codec_label']}" for f in afmts if f["id"] == value), value)
        await cq.answer(f"{'✅' if toggled else '○'} {alabel}")
        try:
            await cq.message.edit_text(_dl_status_text(sess),
                                        reply_markup=_dl_audio_keyboard(uid, afmts, sel,
                                                                         default_lang=_def_lang))
        except Exception:
            pass
        return

    if action == "aall":
        sess["sel_audio_ids"] = [f["id"] for f in afmts]
        await cq.answer(f"✅ All {len(afmts)} audio tracks selected")
        try:
            await cq.message.edit_text(_dl_status_text(sess),
                                        reply_markup=_dl_audio_keyboard(uid, afmts, sess["sel_audio_ids"],
                                                                         default_lang=_def_lang))
        except Exception:
            pass
        return

    if action == "abest":
        sess["sel_audio_ids"] = []
        await cq.answer("🏆 Best audio only")
        try:
            await cq.message.edit_text(_dl_status_text(sess),
                                        reply_markup=_dl_audio_keyboard(uid, afmts, [],
                                                                         default_lang=_def_lang))
        except Exception:
            pass
        return

    if action == "q":   # fallback generic quality
        sess["sel_video_id"] = value
        await cq.answer(f"Quality: {_DL_QUALITY_OPTS.get(value, ('?',))[0]}")
        try:
            await cq.message.edit_reply_markup(reply_markup=_dl_fallback_keyboard(uid, value))
        except Exception:
            pass
        return

    if action == "go":
        await cq.answer("⬇️ Starting download…")
        orig_message  = sess["message"]
        status_msg    = sess.get("msg")
        sel_video_id  = sess.get("sel_video_id") or "best"
        sel_audio_ids = list(sess.get("sel_audio_ids") or [])
        _url          = sess.get("url") or ""
        _filename     = sess.get("filename") or ""
        # Fallback: if sel_video_id is a generic key (e.g. "1080"), use format string
        _legacy_fmt   = ""
        if sel_video_id in _DL_QUALITY_OPTS:
            _legacy_fmt  = _DL_QUALITY_OPTS[sel_video_id][1]
            sel_video_id = "best"
        vfmts_snap    = list(vfmts)
        afmts_snap    = list(afmts)
        ott_sessions.pop(uid, None)

        async def _ott_task():
            try:
                await handle_ott_download(
                    client, orig_message,
                    url=_url, filename=_filename,
                    video_id=sel_video_id,
                    audio_ids=sel_audio_ids,
                    video_formats=vfmts_snap,
                    audio_formats=afmts_snap,
                    fmt=_legacy_fmt,
                    status_msg=status_msg,
                )
            except Exception as _ex:
                LOG.error(f"OTT download task crashed: {_ex}", exc_info=True)
                try:
                    await _reply_auto_delete(orig_message, AUTO_DELETE_5MIN, f"❌ Download crashed: `{_ex}`")
                except Exception:
                    pass
        asyncio.create_task(_ott_task())


# ---------------------------------------------------------------------------
# Upload destination choice callback (Telegram / Google Drive / Both)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^upl_ch:(\d+):(tg|gd|both|cancel)$"))
async def cb_upl_choice(client: Client, cq: CallbackQuery):
    uid  = int(cq.data.split(":")[1])
    dest = cq.data.split(":")[2]
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your upload.", show_alert=True)
    state = pending_upload_state.get(uid)
    if not state:
        return await cq.answer("Already processed or expired.", show_alert=True)
    state["dest"][0] = dest
    state["ev"].set()
    labels = {"tg": "📤 Uploading to Telegram…",
              "gd": "☁️ Uploading to Google Drive…",
              "both": "📤+☁️ Uploading to both…",
              "cancel": "❌ Cancelled"}
    await cq.answer(labels.get(dest, dest))
    try:
        await cq.message.edit_text(labels.get(dest, "Processing…"), reply_markup=None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /audioDefault — set preferred default audio language for /download wizard
# ---------------------------------------------------------------------------

_KNOWN_LANGS = {
    "hindi": "Hindi", "hin": "Hindi", "hi": "Hindi",
    "tamil": "Tamil", "tam": "Tamil", "ta": "Tamil",
    "telugu": "Telugu", "tel": "Telugu", "te": "Telugu",
    "kannada": "Kannada", "kan": "Kannada", "kn": "Kannada",
    "malayalam": "Malayalam", "mal": "Malayalam", "ml": "Malayalam",
    "english": "English", "eng": "English", "en": "English",
    "bengali": "Bengali", "ben": "Bengali", "bn": "Bengali",
    "marathi": "Marathi", "mar": "Marathi", "mr": "Marathi",
    "punjabi": "Punjabi", "pan": "Punjabi", "pa": "Punjabi",
    "none": "", "off": "", "clear": "",
}

@app.on_message(filters.command("audioDefault") & AUTH)
async def audio_default_cmd(_client: Client, message: Message):
    user_id = _message_actor_id(message)
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /audioDefault. Run /verify.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        cur = (user_dl_prefs.get(user_id) or {}).get("default_audio_lang") or "_(not set)_"
        return await message.reply_text(
            "🔊 **Default Audio Language**\n\n"
            f"Current setting: `{cur}`\n\n"
            "**Usage:** `/audioDefault <language>`\n\n"
            "**Examples:**\n"
            "`/audioDefault Tamil` — Tamil track auto-selected & set as default\n"
            "`/audioDefault Hindi` — Hindi track auto-selected & set as default\n"
            "`/audioDefault none` — Clear preference\n\n"
            "**Supported:** Hindi, Tamil, Telugu, Kannada, Malayalam, English, Bengali, Marathi, Punjabi\n\n"
            "When `/download` opens, the matching audio track will be **pre-ticked ✅** and "
            "on multi-track downloads it will be set as the **default playback track** in the video file."
        )

    raw = parts[1].strip().lower()
    resolved = _KNOWN_LANGS.get(raw)
    if resolved is None:
        # Try substring match on display names
        resolved_key = next(
            (v for k, v in _KNOWN_LANGS.items() if raw in k or k in raw), None
        )
        if resolved_key is None:
            return await message.reply_text(
                f"❌ Unknown language: `{parts[1].strip()}`\n\n"
                "Try: `Tamil`, `Hindi`, `Telugu`, `Kannada`, `Malayalam`, `English`, `Bengali`, `none`"
            )
        resolved = resolved_key

    if not resolved:
        user_dl_prefs.pop(user_id, None)
        return await message.reply_text("✅ Default audio preference cleared.")

    user_dl_prefs.setdefault(user_id, {})["default_audio_lang"] = resolved
    await message.reply_text(
        f"✅ **Default audio set to `{resolved}`**\n\n"
        f"Next time you use `/download`:\n"
        f"• The **{resolved}** track will be **pre-ticked ✅** in the audio menu\n"
        f"• If multi-track is downloaded, **{resolved}** will be the **default playback track** "
        f"(video players auto-play it on open)\n\n"
        f"Use `/audioDefault none` to clear."
    )


# ---------------------------------------------------------------------------
# /compress — compress a replied video
# ---------------------------------------------------------------------------

@app.on_message(filters.command("compress"))
async def compress_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'compress')
    if _six_job_error:
        return await message.reply_text(_six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /compress. Run /verify.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Send a video with** `/compress` **as the caption**, or reply to a video with `/compress`."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"cmp_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("⬇️ Downloading source video...")

    try:
        dl_path = await src_msg.download(
            file_name=join(save_dir, f"src_{src_msg.id}")
        )
        await status.edit_text("🔍 Probing video...")
        info = await _ffprobe_video(dl_path)
        if info["duration"] <= 0:
            raise Exception("Could not determine video duration.")

        avail_langs = sorted({s["lang"] for s in info["audio_streams"]})
        default_lang = (["multi"] if len(avail_langs) > 1
                        else (["hin"] if "hin" in avail_langs
                        else ([avail_langs[0]] if avail_langs else ["multi"])))
        state = {
            "user_id":       user_id,
            "save_dir":      save_dir,
            "src_path":      dl_path,
            "duration":      info["duration"],
            "video_height":  info["video_height"],
            "audio_streams": info["audio_streams"],
            "available_langs": avail_langs,
            "size_mb":       300,
            "res_key":       "h720",
            "langs":         default_lang,
        }
        compress_jobs[user_id] = state
        await status.edit_text(_compress_status_text(state), reply_markup=_compress_menu(state))
    except Exception as e:
        LOG.error(f"Compress setup failed: {e}")
        try:
            await status.edit_text(f"Setup failed: `{e}`")
        finally:
            compress_jobs.pop(user_id, None)
            if save_dir and os.path.exists(save_dir):
                import shutil
                shutil.rmtree(save_dir, ignore_errors=True)


@app.on_message(filters.command(["Compressadvance", "compressadvance", "cmpadvance", "cmpAdv"]) & AUTH)
async def compress_advance_cmd(client: Client, message: Message):
    """
    /Compressadvance — same as /compress but with smarter defaults:
      • 576p resolution
      • 350 MB target size
      • Multi audio (all tracks kept)
    Available to all verified users — no owner / premium restriction.
    No duration limit.
    """
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'Compressadvance')
    if _six_job_error:
        return await message.reply_text(_six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /Compressadvance. Run /verify.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with `/Compressadvance`.\n\n"
            "**Defaults:** 576p · 350 MB · Multi audio (all tracks kept)\n"
            "Change any option from the menu before pressing **Start**."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"cmpAdv_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("⬇️ Downloading source video…")

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        await status.edit_text("🔍 Probing video…")
        info = await _ffprobe_video(dl_path)
        if info["duration"] <= 0:
            raise Exception("Could not determine video duration.")

        avail_langs = sorted({s["lang"] for s in info["audio_streams"]})
        state = {
            "user_id":        user_id,
            "save_dir":       save_dir,
            "src_path":       dl_path,
            "duration":       info["duration"],
            "video_height":   info["video_height"],
            "audio_streams":  info["audio_streams"],
            "available_langs": avail_langs,
            # ── Advanced defaults ──────────────────────────
            "size_mb":  350,       # target ~280-390 MB for typical 50-min 576p content
            "res_key":  "h576",    # 576p
            "langs":    ["multi"], # keep all audio tracks
        }
        compress_jobs[user_id] = state
        await status.edit_text(_compress_status_text(state), reply_markup=_compress_menu(state))
    except Exception as e:
        LOG.error(f"Compressadvance setup failed uid={user_id}: {e}")
        try:
            await status.edit_text(f"Setup failed: `{e}`")
        finally:
            compress_jobs.pop(user_id, None)
            if save_dir and os.path.exists(save_dir):
                import shutil
                shutil.rmtree(save_dir, ignore_errors=True)


@app.on_callback_query(filters.regex(r"^cmp:"))
async def cmp_callback(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    state   = compress_jobs.get(user_id)
    if not state:
        return await cq.answer("This compress session is no longer active.", show_alert=True)

    parts  = cq.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    val    = parts[2] if len(parts) > 2 else ""

    if action == "cancel":
        compress_jobs.pop(user_id, None)
        import shutil
        shutil.rmtree(state["save_dir"], ignore_errors=True)
        try:
            await cq.message.edit_text("Compress cancelled.", reply_markup=None)
        except Exception:
            pass
        return await cq.answer("Cancelled.")

    if action == "size":
        state["size_mb"] = int(val)
        await cq.answer(f"Target: {val} MB")

    elif action == "res":
        state["res_key"] = val
        await cq.answer(f"Resolution: {COMPRESS_RES_CONFIG.get(val, {}).get('label', val)}")

    elif action == "lang":
        sel = set(state.get("langs", []))
        if val == "multi":
            sel = {"multi"} if "multi" not in sel else set()
        else:
            sel.discard("multi")
            if val in sel:
                sel.discard(val)
            else:
                sel.add(val)
        if not sel:
            sel = {val}
        state["langs"] = sorted(sel)
        await cq.answer(f"Audio: {', '.join(LANG_LABEL.get(l, l) for l in state['langs'])}")

    elif action == "start":
        if not state.get("size_mb") or not state.get("res_key") or not state.get("langs"):
            return await cq.answer("Please select size, resolution, and audio first.", show_alert=True)
        compress_jobs.pop(user_id, None)
        await cq.answer("Starting compression...")
        asyncio.create_task(run_compress(client, cq.message, state))
        return

    try:
        await cq.message.edit_text(_compress_status_text(state), reply_markup=_compress_menu(state))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /reclink — headless browser stream extractor
# ---------------------------------------------------------------------------

@app.on_message(filters.command("reclink"))
async def reclink_command(client: Client, message: Message):
    # Anonymous Admin is an authorized actor in the official group.  Use the
    # same internal sentinel as /rec and /drec so verification is not required
    # and recording/job state remains isolated from real user IDs.
    anonymous_admin = _is_official_anonymous_admin(message)
    if anonymous_admin:
        user_id = OFFICIAL_ANONYMOUS_GROUP_ID
    else:
        if message.from_user is None:
            return
        user_id = _message_actor_id(message)

    if not anonymous_admin and not is_verified(user_id):
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "You must be **verified** to use /reclink. Run /verify.")
    if user_id in user_tasks or user_id in reclink_jobs:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "You already have an active job. Use /statusme or /cancelme.")
    if len(message.command) < 2:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
            "**Invalid format.**\n\n"
            "Usage: `/reclink <player_or_webpage_url> HH:MM:SS <filename>`\n"
            "Example: `/reclink https://embed.example.com/live/abc 00:30:00 MyShow`\n\n"
            "Use this when the page **runs JavaScript** to load the stream."
        )

    params    = " ".join(message.command[1:])
    parts     = params.split(" ", 2)
    if len(parts) < 2:
        return await _reply_auto_delete(message, AUTO_DELETE_5MIN, "Bad arguments. Use `/reclink <url> HH:MM:SS <filename>`.")
    page_url     = parts[0]
    timestamp    = parts[1]
    raw_filename = parts[2].strip() if len(parts) > 2 else DEFAULT_FILENAME

    msg = await _reply_auto_delete(message, AUTO_DELETE_5MIN, 
        "🌐 **Launching headless browser...**\n\n"
        "Opening the page in Chromium and watching network traffic for "
        "`.m3u8` / `.mpd` requests. This usually takes 10–30s."
    )

    log_lines: list  = []
    last_render      = {"t": 0.0}

    def push_log(line: str):
        log_lines.append(line)
        now = time.time()
        if now - last_render["t"] < 2.5:
            return
        last_render["t"] = now
        tail = "\n".join(log_lines[-8:])
        try:
            asyncio.create_task(msg.edit_text(
                "🌐 **Extracting stream...**\n\n"
                f"Page: `{page_url[:90]}{'…' if len(page_url) > 90 else ''}`\n\n"
                f"```\n{tail}\n```"
            ))
        except Exception:
            pass

    async def runner():
        try:
            result = await _extract_streams_with_chromium(page_url, timeout_sec=30, log_cb=push_log)
        except Exception as e:
            # A plain HTML probe is useful when Playwright/Chromium is
            # unavailable and handles pages that publish an m3u8 in their
            # initial markup.
            LOG.warning(f"reclink Chromium extraction failed: {e}")
            try:
                fallback = await probe_stream(page_url)
                if not fallback.get("is_hls"):
                    raise RuntimeError(str(e))
                fallback_url = fallback.get("final_url") or page_url
                result = {
                    "streams": [{
                        "url": fallback_url,
                        "headers": {
                            "Referer": fallback.get("referer", ""),
                            "User-Agent": fallback.get("user_agent", DEFAULT_USER_AGENT),
                        },
                        "is_master": _looks_like_master_playlist(fallback_url),
                    }],
                    "page_title": "",
                    "final_url": fallback_url,
                    "log": [f"Chromium unavailable; plain probe fallback: {e}"],
                }
            except Exception as fallback_error:
                LOG.error(f"reclink extraction failed: {fallback_error}")
                try:
                    await msg.edit_text(
                        f"**Extraction failed.**\n\n`{fallback_error}`\n\n"
                        "If the page needs login, capture cookies on a real browser and try `/download` instead."
                    )
                finally:
                    reclink_jobs.pop(user_id, None)
                return

        streams = result["streams"]
        if not streams:
            tail = "\n".join(log_lines[-12:]) or "(no log)"
            try:
                await msg.edit_text(
                    "**No `.m3u8` / `.mpd` streams seen.**\n\n"
                    f"Page title: `{result.get('page_title', '?')[:80]}`\n"
                    f"Final URL: `{result.get('final_url', page_url)[:120]}`\n\n"
                    "Possible reasons:\n"
                    "• Page needs login → use `/set_cookies` then `/download`.\n"
                    "• Stream is DRM-protected.\n"
                    "• Player only starts after a captcha or user gesture.\n\n"
                    f"```\n{tail}\n```"
                )
            finally:
                reclink_jobs.pop(user_id, None)
            return

        chosen = streams[0]
        tail   = "\n".join(log_lines[-6:])
        try:
            await msg.edit_text(
                f"✅ **Captured stream — handing off to recorder.**\n\n"
                f"Picked: `{'master' if chosen['is_master'] else 'media'} playlist`\n"
                f"`{chosen['url'][:120]}{'…' if len(chosen['url']) > 120 else ''}`"
                f"\n\n```\n{tail}\n```"
            )
        except Exception:
            pass

        try:
            if time_to_seconds(timestamp) <= 0:
                raise ValueError("Invalid duration. Use a positive HH:MM:SS value.")
            message.command = ["rec", chosen["url"], timestamp, raw_filename]
            await handle_record(
                client,
                message,
                extra_flags=_stream_request_flags(chosen.get("headers")),
            )
        except Exception as e:
            LOG.error(f"reclink → handle_record failed: {e}")
            try:
                await msg.edit_text(f"Recording start failed: `{e}`")
            except Exception:
                pass
        finally:
            reclink_jobs.pop(user_id, None)

    task = asyncio.create_task(runner())
    reclink_jobs[user_id] = {"task": task}


# ---------------------------------------------------------------------------
# /screenshot — evenly-spaced screenshots from a replied video
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["screenshot", "Screenshot", "ss"]))
async def screenshot_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'screenshot')
    if _six_job_error:
        return await message.reply_text(_six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /screenshot. Run /verify.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Send a video with** `/screenshot` **as the caption**, or reply to a video with `/screenshot`."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"ss_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("Downloading source video...")

    try:
        dl_path = join(save_dir, f"src_{src_msg.id}")

        async def _ss_download_progress(current, total, *_args):
            if not total:
                return

            percent = current * 100 / total
            current_mb = current / (1024 * 1024)
            total_mb = total / (1024 * 1024)

            try:
                await status.edit_text(
                    "📥 **Downloading source video...**\n\n"
                    f"Progress: `{percent:.1f}%`\n"
                    f"📦 `{current_mb:.1f} MB / {total_mb:.1f} MB`"
                )
            except Exception:
                pass

        # Screenshot source: use Pyrogram's native downloader.
        # The custom parallel range downloader can produce a same-size
        # but corrupted MP4, which causes "moov atom not found".
        await client.download_media(
            src_media,
            file_name=dl_path,
            progress=_ss_download_progress,
        )

        # Screenshot setup: use Telegram's own video metadata first.
        # This avoids rejecting a usable Telegram video because strict ffprobe
        # validation fails on the downloaded temporary file.
        await status.edit_text("⚙️ Preparing screenshot generator...")

        duration = float(getattr(src_media, "duration", 0) or 0)
        video_height = int(getattr(src_media, "height", 0) or 0)

        # Fallback to FFmpeg only when Telegram metadata has no duration.
        if duration <= 0:
            try:
                rc, out, err = await runcmd(
                    f'ffmpeg -hide_banner -i {shlex.quote(dl_path)} -f null - 2>&1'
                )
                probe_text = (out or "") + "\n" + (err or "")

                m = re.search(
                    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                    probe_text
                )
                if m:
                    hh, mm, ss = m.groups()
                    duration = (
                        int(hh) * 3600
                        + int(mm) * 60
                        + float(ss)
                    )
            except Exception as e:
                LOG.warning(f"Screenshot duration fallback failed: {e}")

        if duration <= 0:
            raise Exception("Could not determine video duration.")

        state = {
            "src_path":    dl_path,
            "save_dir":    save_dir,
            "duration":    duration,
            "video_height": video_height,
            "user_id":     user_id,
            "chat_id":     status.chat.id,
            "status_msg_id": status.id,
            "username":    message.from_user.username or "anonymous",
        }
        ss_jobs[user_id] = state
        await status.edit_text(_ss_menu_text(state), reply_markup=_ss_menu())
    except Exception as e:
        LOG.error(f"screenshot setup failed: {e}")
        try:
            await status.edit_text(f"Setup failed: `{e}`")
        finally:
            ss_jobs.pop(user_id, None)
            _safe_rmtree(save_dir)


@app.on_callback_query(filters.regex(r"^ss:"))
async def ss_callback(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    state   = ss_jobs.get(user_id)
    if not state:
        return await cq.answer("This menu is no longer active.", show_alert=True)

    parts  = cq.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        ss_jobs.pop(user_id, None)
        _safe_rmtree(state["save_dir"])
        try:
            await cq.message.edit_text("Screenshot cancelled.", reply_markup=None)
        except Exception:
            pass
        return await cq.answer("Cancelled.")

    if action == "n" and len(parts) > 2:
        try:
            n = int(parts[2])
        except ValueError:
            return await cq.answer("Bad number.", show_alert=True)
        # SS_MIN and SS_MAX are defined in Bot.py
        if not (SS_MIN <= n <= SS_MAX):
            return await cq.answer("Out of range.", show_alert=True)
        await cq.answer(f"Generating {n} screenshots...")
        asyncio.create_task(run_screenshots(client, cq.message, state, n))
        return

    await cq.answer()


# ---------------------------------------------------------------------------
# /trim — cut a portion of a replied video
# ---------------------------------------------------------------------------

@app.on_message(filters.command("trim"))
async def trim_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /trim. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Send a video with** `/trim <start> <end>` **as the caption**, or reply to a video.\n\n"
            "Example: `/trim 00:00:30 00:02:00`\nShorthand: `/trim 30s 2m`"
        )

    if len(message.command) < 3:
        return await message.reply_text(
            "**Need a start and end timestamp.**\n\nUsage: `/trim <start> <end>`\n"
            "Examples:\n• `/trim 00:00:30 00:02:00`\n• `/trim 30s 2m`\n• `/trim 1:30 5:00`"
        )

    start_tok = message.command[1]
    end_tok   = message.command[2]
    start_sec = _parse_duration_token(start_tok)
    end_sec   = _parse_duration_token(end_tok)

    if end_sec <= 0 or start_sec < 0:
        return await message.reply_text(
            f"Bad timestamp(s): `{start_tok}` / `{end_tok}`. "
            "Use `HH:MM:SS`, `MM:SS`, or shorthand like `30s`, `2m`, `1h`."
        )
    if end_sec <= start_sec:
        return await message.reply_text(
            f"End (`{_seconds_to_hms(end_sec)}`) must be **after** start (`{_seconds_to_hms(start_sec)}`)."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"trim_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("Downloading source video...")

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        await status.edit_text("Probing video...")
        info = await _ffprobe_video(dl_path)
        if info["duration"] <= 0:
            raise Exception("Could not determine source video duration.")
        if start_sec >= info["duration"]:
            raise Exception(f"Start `{_seconds_to_hms(start_sec)}` is past video end "
                            f"`{_seconds_to_hms(int(info['duration']))}`.")
        clip_end = min(end_sec, int(info["duration"]))
        clip_len = clip_end - start_sec
        out_path = join(save_dir, f"trim_{int(time.time())}.mkv")

        user_tasks[user_id]  = time.time()
        user_status[user_id] = {
            "id": int(user_tasks[user_id]), "user_id": user_id,
            "filename": os.path.basename(out_path),
            "duration_str": _seconds_to_hms(clip_len),
            "channel_name": "Trim", "url": "(local)", "progress": "0%",
        }

        await status.edit_text(
            f"✂️ Trimming `{_seconds_to_hms(start_sec)}` → "
            f"`{_seconds_to_hms(clip_end)}` (`{_seconds_to_hms(clip_len)}` total)..."
        )
        cmd = (f'ffmpeg -hide_banner -loglevel error -nostats -y '
               f'-ss {start_sec} -to {clip_end} '
               f'-i {shlex.quote(dl_path)} '
               f'-map 0 -c copy -avoid_negative_ts make_zero {shlex.quote(out_path)}')
        rc, _o, err = await runcmd(cmd)

        if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await status.edit_text("Stream-copy trim failed; falling back to re-encode...")
            cmd2 = (f'ffmpeg -hide_banner -loglevel error -nostats -y '
                    f'-ss {start_sec} -to {clip_end} '
                    f'-i {shlex.quote(dl_path)} '
                    f'-map 0:v:0 -map 0:a? '
                    f'-c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 128k {shlex.quote(out_path)}')
            rc2, _o2, err2 = await runcmd(cmd2)
            if rc2 != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                raise Exception(f"FFmpeg trim failed.\n{(err2 or err)[-1500:]}")

        thumb = join(save_dir, "thumb.jpg")
        await runcmd(f'ffmpeg -hide_banner -loglevel error -nostats -y '
                     f'-ss {min(2, max(0, clip_len // 2))} -i {shlex.quote(out_path)} '
                     f'-vframes 1 -q:v 2 {shlex.quote(thumb)}')
        thumb_path   = thumb if os.path.exists(thumb) else None
        out_size_mb  = os.path.getsize(out_path) / (1024 * 1024)
        retention_note = (f"_The video is automatically deleted from the server after "
                          f"{_retention_label()}._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"✂️ Trimmed: `{_seconds_to_hms(start_sec)}` → `{_seconds_to_hms(clip_end)}`\n"
                   f"Duration: `{_seconds_to_hms(clip_len)}`\nSize: `{out_size_mb:.1f} MB`\n"
                   f"{retention_note}")

        _dest = await _await_upload_choice(user_id, status, f"Trimmed: `{out_size_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, user_id, _dest, status, out_path, caption, clip_len,
                thumb_path=thumb_path, save_dir=save_dir,
            )
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Trim failed uid={user_id}: {e}")
        err_text = str(e)
        if len(err_text) > 2500: err_text = "...[truncated]...\n" + err_text[-2500:]
        try: await status.edit_text(f"❌ **Trim failed.**\n\n`{err_text}`")
        except Exception: pass
        if save_dir and os.path.exists(save_dir): _safe_rmtree(save_dir)
    finally:
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)


# ---------------------------------------------------------------------------
# /Watermark — burn text watermark into a replied video (last 2 min)
# ---------------------------------------------------------------------------

def _get_replied_video(message):
    """Return (src_msg, src_media) for a replied or caption-attached video/document."""
    # 1) Video attached directly to the command message (sent with command as caption)
    if message.video:
        return message, message.video
    if message.document and (message.document.mime_type or "").startswith("video/"):
        return message, message.document
    # 2) Classic reply-to-video flow
    src = message.reply_to_message
    if not src:
        return None, None
    if src.video:
        return src, src.video
    if src.document and (src.document.mime_type or "").startswith("video/"):
        return src, src.document
    return None, None


# ---------------------------------------------------------------------------
# /Watermark — position picker helpers
# ---------------------------------------------------------------------------

_WM_POS_LABELS = {
    "normal1": "💧 Normal Watermark 1",
    "normal2": "💧 Normal Watermark 2",
    "watermark3": "💧 Watermark 3 • 1536x1228",
    "center":  "🎯 Center",
    "watermark4": "🆕 New Watermark 4 • 1080p",
}

_WM_POS_IMG = {
    "normal1": "(W-w)/2:H-h-55",
    "normal2": "(W-w)/2:H-h-50",
    "watermark3": "20:main_h-overlay_h-170",
    "center":  "(W-w)/2:(H-h)/2",
    "watermark4": "(main_w-overlay_w)/2:main_h-overlay_h-50",
}

_WM_POS_TXT = {
    "normal1": "x=(w-tw)/2:y=h-th-55",
    "normal2": "x=(w-tw)/2:y=h-th-50",
    "watermark3": "x=20:y=h-th-170",
    "center":  "x=(w-tw)/2:y=(h-th)/2",
    "watermark4": "x=(w-tw)/2:y=h-th-50",
}

def _wm_pos_keyboard(uid: int, selected: str, session_id: str) -> InlineKeyboardMarkup:
    def btn(key: str) -> InlineKeyboardButton:
        label = ("✅ " if key == selected else "") + _WM_POS_LABELS[key]
        return InlineKeyboardButton(
            label,
            callback_data=f"wmp:{uid}:{session_id}:{key}"
        )

    return InlineKeyboardMarkup([
        [btn("normal1"), btn("normal2")],
        [btn("watermark3")],
        [btn("center")],
        [btn("watermark4")],
        [
            InlineKeyboardButton(
                "▶️ Start",
                callback_data=f"wmp:{uid}:{session_id}:start"
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"wmp:{uid}:{session_id}:cancel"
            ),
        ],
    ])


@app.on_message(filters.command(["Watermark", "watermark", "wm"]))
async def watermark_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if not _is_control_actor(message) and not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /Watermark. Run /verify.")

    src_msg, src_media = _get_replied_video(message)
    raw_text = message.text or ""
    try:
        tokens = shlex.split(raw_text)
    except Exception:
        tokens = raw_text.split()
    args = tokens[1:]
    photo_link = ""
    server_source = None

    # Optional custom watermark image URL, followed by output filename.
    if args and re.match(r"^https?://", args[0], re.I):
        photo_link = args.pop(0).strip()

    if src_media:
        # Reply mode: /watermark Output_File.mkv (or URL Output_File.mkv)
        if not args:
            return await message.reply_text("❌ Filename is required")
        filename = " ".join(args).strip()
    else:
        # Server mode: /watermark ServerFileName Output_File.mkv
        if len(args) < 2:
            return await message.reply_text(
                "📥 Reply to a Telegram video with `/watermark Filename`\n\n"
                "Or use `/watermark ServerFileName Filename` for a file already on the server."
            )
        server_name = args.pop(0).strip()
        server_source = _audiostream_find_server_file(server_name)
        if not server_source:
            return await message.reply_text(
                f"❌ Server file not found: `{server_name}`\n"
                f"📁 Searched: `{DOWNLOAD_DIRECTORY}`"
            )
        filename = " ".join(args).strip()

    if not filename:
        return await message.reply_text("❌ Filename is required")
    for bad in '/\\:*?"<>|':
        filename = filename.replace(bad, "_")
    if not re.search(r"\.[A-Za-z0-9]{1,8}$", filename):
        filename += ".mp4"

    wm_text = get_default_watermark()
    session_id = pysecrets.token_hex(4)
    session_key = (user_id, session_id)
    _wm_pos_sessions[session_key] = {
        "src_msg": src_msg,
        "src_media": src_media,
        "src_path": server_source,
        "wm_text": wm_text,
        "pos": "normal1",
        "filename": filename,
        "wm_img_url": photo_link or None,
        "session_id": session_id,
    }
    await message.reply_text(
        f"**💧 Watermark Setup**\n\n📁 Filename: `{filename}`\n"
        + (f"🖼️ Photo link: `{photo_link}`\n\n" if photo_link else "")
        + "Choose watermark style:",
        reply_markup=_wm_pos_keyboard(user_id, "normal1", session_id),
    )

@app.on_callback_query(filters.regex(r"^wms:(-?\d+):([A-Za-z0-9]+):(wm1|wm2|normal1|normal2|center|delete)$"))
async def wms_callback(client: Client, cq: CallbackQuery):
    uid = int(cq.matches[0].group(1))
    session_id = cq.matches[0].group(2)
    action = cq.matches[0].group(3)
    clicker_id = cq.from_user.id if cq.from_user else 0
    if clicker_id != uid and not (
        uid == OFFICIAL_ANONYMOUS_GROUP_ID and _is_official_group_message(cq.message)
    ):
        return await cq.answer("Yeh aapka session nahi hai.", show_alert=True)
    session_key = (uid, session_id)
    session = _wm_pos_sessions.get(session_key)
    if not session:
        return await cq.answer("Session expire ho gaya. Dobara /watermark karein.", show_alert=True)
    if action == "delete":
        _wm_pos_sessions.pop(session_key, None)
        await cq.answer("Deleted.")
        try: await cq.message.edit_text("🗑️ Watermark deleted.", reply_markup=None)
        except Exception: pass
        return
    session["style"] = action
    if action == "center":
        session["pos"] = "center"
    await cq.answer(f"Selected: {action}")
    try:
        await cq.message.edit_text(
            f"**💧 Watermark Setup**\n\n"
            f"📁 Filename: `{session.get('filename', '-')}`\n"
            f"Style: `{action}`\n\n"
            f"Choose position:",
            reply_markup=_wm_pos_keyboard(uid, session.get("pos", "bottom_right"), session_id),
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^wmp:(-?\d+):([A-Za-z0-9]+):(normal1|normal2|watermark3|center|watermark4|start|cancel)$"))
async def wmp_callback(client: Client, cq: CallbackQuery):
    uid    = int(cq.matches[0].group(1))
    session_id = cq.matches[0].group(2)
    action = cq.matches[0].group(3)

    clicker_id = cq.from_user.id if cq.from_user else 0
    if clicker_id != uid and not (
        uid == OFFICIAL_ANONYMOUS_GROUP_ID and _is_official_group_message(cq.message)
    ):
        return await cq.answer("Yeh aapka session nahi hai.", show_alert=True)

    session_key = (uid, session_id)
    session = _wm_pos_sessions.get(session_key)
    if not session:
        await cq.answer("Session expire ho gaya. Dobara /Watermark karein.", show_alert=True)
        try:
            await cq.message.delete()
        except Exception:
            pass
        return

    if action == "delete":
        _wm_pos_sessions.pop(session_key, None)
        await cq.answer("Deleted.")
        try:
            await cq.message.edit_text("🗑️ Watermark deleted.")
        except Exception:
            pass
        return

    if action != "start":
        # User tapped a position button — update selection
        session["pos"] = action
        await cq.answer(_WM_POS_LABELS[action])
        try:
            await cq.message.edit_reply_markup(_wm_pos_keyboard(uid, action, session_id))
        except Exception:
            pass
        return

    # ── Start pressed ──────────────────────────────────────────────────────
    _six_job_id, _six_job_error = _six_acquire(uid, "watermark")
    if _six_job_error:
        return await cq.answer(_six_job_error, show_alert=True)
    _six_handler_task = asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )

    _wm_pos_sessions.pop(session_key, None)
    src_msg = session["src_msg"]
    src_media = session.get("src_media")

    # Backward compatibility for sessions created before src_media
    # was stored in the watermark session.
    if not src_media:
        if getattr(src_msg, "video", None):
            src_media = src_msg.video
        elif getattr(src_msg, "document", None) and (
            getattr(src_msg.document, "mime_type", "") or ""
        ).startswith("video/"):
            src_media = src_msg.document

    src_path = session.get("src_path")
    if not src_media and not src_path:
        return await cq.answer(
            "❌ Source video is no longer available. Please run /watermark again.",
            show_alert=True,
        )

    wm_text = session["wm_text"]
    pos      = session["pos"]
    pos_label= _WM_POS_LABELS.get(pos, pos)
    ff_pos = {
    "normal1": "normal1",
    "normal2": "normal2",
    "center": "center",
}.get(pos, "normal2")

    await cq.answer("Starting…")
    try:
        await cq.message.edit_text(
            f"⬇️ Downloading video…\n\n"
            f"Watermark: `{wm_text}`\n"
            f"Position: {pos_label}"
        )
    except Exception:
        pass
    status   = cq.message
    save_dir = join(DOWNLOAD_DIRECTORY, f"wm_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    _success = False

    try:
        # Watermark source: use Pyrogram's native downloader.
        # The custom parallel range downloader can create a corrupted/incomplete
        # media file that ffprobe/FFmpeg cannot read.
        if src_path:
            dl_path = join(save_dir, os.path.basename(src_path))
            shutil.copy2(src_path, dl_path)
        else:
            dl_path = await client.download_media(
                src_media,
                file_name=join(save_dir, f"src_{src_msg.id}"),
                progress=progress_for_telegram_download,
                progress_args=(status, time.time(), f"src_{src_msg.id}"),
            )

        if not dl_path or not os.path.exists(dl_path):
            raise Exception("Telegram video download failed.")

        if os.path.getsize(dl_path) <= 0:
            raise Exception("Downloaded video file is empty.")
        await status.edit_text(
            f"🔍 Probing video…\n\nWatermark: `{wm_text}`\nPosition: {pos_label}"
        )
        info = await _ffprobe_video(dl_path)
        dur  = info["duration"]
        if dur <= 0:
            raise Exception("Could not determine video duration — ffprobe failed.")

        _requested_name = (session.get("filename") or "").strip()

        if not _requested_name:
            raise Exception("❌ Filename is required")

        for bad in '/\\:*?"<>|':
            _requested_name = _requested_name.replace(bad, "_")

        if not re.search(r"\.[A-Za-z0-9]{1,8}$", _requested_name):
            _requested_name += ".mp4"

        out_path = join(save_dir, _requested_name)
        # ── Watermark image + exact periodic timing ─────────────────
        await status.edit_text(
            f"⬇️ Fetching watermark image…\\n\\n"
            f"📁 Output: `{os.path.basename(out_path)}`\\n"
            f"Position: {pos_label}"
        )

        custom_url = session.get("wm_img_url")
        wm_img = None

        if custom_url:
            custom_path = join(save_dir, "watermark_source.png")
            rc_dl, _, err_dl = await runcmd(
                f'curl -L -s --max-time 30 -o {shlex.quote(custom_path)} '
                f'{shlex.quote(custom_url)}'
            )
            if rc_dl == 0 and os.path.exists(custom_path) and os.path.getsize(custom_path) > 0:
                wm_img = custom_path
            else:
                raise Exception("Watermark image download failed.")
        else:
            has_img = await _async_ensure_watermark_img()
            wm_img = _watermark_img_path() if has_img else None

        if wm_img:
            _wm_sz = 180

            _enable = (
                "between(t,20,80)+"
                "between(t,360,420)+"
                "between(t,900,960)+"
                "between(t,1200,1260)+"
                "between(t,1800,1890)"
            )

            if pos == "watermark3":
                _wm_sz = 300
                _enable = ("between(t,0,30)+between(t,540,600)+"
                           "between(t,1200,1260)+between(t,2400,2460)")
                fc = (
                    f"[0:v]scale=1536:1228,setsar=1[base];"
                    f"[1:v]format=rgba,scale={_wm_sz}:-1[wm];"
                    f"[base][wm]overlay=20:main_h-overlay_h-170:"
                    f"enable='{_enable}'[out]"
                )

            elif pos == "normal1":
                fc = (
                    f"[0:v]"
                    f"scale=854:480:force_original_aspect_ratio=decrease,"
                    f"pad=854:480:(ow-iw)/2:(oh-ih)/2:black,"
                    f"setsar=1[base];"
                    f"[1:v]format=rgba,scale={_wm_sz}:-1[wm];"
                    f"[base][wm]"
                    f"overlay=(W-w)/2:H-h-55:"
                    f"enable='{_enable}'[out]"
                )

            elif pos == "normal2":
                fc = (
                    f"[0:v]scale=854:480,setsar=1[base];"
                    f"[1:v]format=rgba,scale={_wm_sz}:-1[wm];"
                    f"[base][wm]"
                    f"overlay=(main_w-overlay_w)/2:"
                    f"main_h-overlay_h-50:"
                    f"enable='{_enable}'[out]"
                )

            elif pos == "center":
                fc = (
                    f"[0:v]setsar=1[base];"
                    f"[1:v]format=rgba,scale={_wm_sz}:-1[wm];"
                    f"[base][wm]"
                    f"overlay=(W-w)/2:(H-h)/2:"
                    f"enable='{_enable}'[out]"
                )

            elif pos == "watermark4":
                _wm_sz = 180
                # New Watermark 4 • 1080p: show watermark only in requested intervals.
                _enable = (
                    "between(t,30,90)+"
                    "between(t,600,660)+"
                    "between(t,1200,1260)+"
                    "between(t,1800,1860)+"
                    "between(t,2400,2460)+"
                    "between(t,3000,3060)+"
                    "between(t,4800,5100)"
                )
                fc = (
                    f"[0:v]scale=1920:1080,setsar=1[base];"
                    f"[1:v]format=rgba,scale={_wm_sz}:-1[wm];"
                    f"[base][wm]"
                    f"overlay=(main_w-overlay_w)/2:main_h-overlay_h-50:"
                    f"enable='{_enable}'[out]"
                )

            else:
                raise Exception(f"Unknown watermark position: {pos}")

            await status.edit_text(
                f"🎨 Burning watermark image…\n\n"
                f"📁 Output: `{os.path.basename(out_path)}`\n"
                f"Position: {pos_label}"
            )

            _wm_encoder = (
                "-c:v libx264 -pix_fmt yuv420p -crf 23 -preset ultrafast -threads 2 -c:a aac"
                if pos == "watermark3" else
                "-c:v libx264 -preset veryfast -crf 20 -c:a copy"
            )
            # Preserve every audio and subtitle stream, then stamp each track
            # with the requested brand|detected-language|title metadata.
            _source_tracks = await _ffprobe_video(dl_path)
            _audio_tracks = _source_tracks.get("audio_streams") or []
            _subtitle_tracks = _source_tracks.get("subtitle_streams") or []
            _track_meta_args = []
            for _audio_idx, _audio_track in enumerate(_audio_tracks):
                _audio_lang = _audio_display_language(_audio_track)
                _audio_title = f"{OWNER_AUDIO_BRAND}|{_audio_lang}|{OWNER_AUDIO_TITLE}"
                _track_meta_args += [
                    f"-metadata:s:a:{_audio_idx}", f"title={_audio_title}",
                    f"-metadata:s:a:{_audio_idx}", f"handler_name={_audio_title}",
                ]
                _audio_lang_code = str(_audio_track.get("lang") or "").strip().lower()
                if _audio_lang_code and _audio_lang_code != "und":
                    _track_meta_args += [
                        f"-metadata:s:a:{_audio_idx}", f"language={_audio_lang_code[:3]}"
                    ]
            for _sub_idx, _sub_track in enumerate(_subtitle_tracks):
                _sub_lang = _audio_display_language(_sub_track)
                _sub_title = f"{OWNER_AUDIO_BRAND}|{_sub_lang}|{OWNER_AUDIO_TITLE}"
                _track_meta_args += [
                    f"-metadata:s:s:{_sub_idx}", f"title={_sub_title}",
                    f"-metadata:s:s:{_sub_idx}", f"handler_name={_sub_title}",
                ]
                _sub_lang_code = str(_sub_track.get("lang") or "").strip().lower()
                if _sub_lang_code and _sub_lang_code != "und":
                    _track_meta_args += [
                        f"-metadata:s:s:{_sub_idx}", f"language={_sub_lang_code[:3]}"
                    ]
            _track_meta_str = " ".join(shlex.quote(x) for x in _track_meta_args)
            _wm_cmd = (
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-i {shlex.quote(dl_path)} '
                f'-i {shlex.quote(wm_img)} '
                f'-filter_complex {shlex.quote(fc)} '
                f'-map "[out]" -map 0:a? -map 0:s? '
                f'-map_chapters -1 '
                f'{_wm_encoder} -c:s copy '
                f'-map_metadata -1 {_track_meta_str} '
                f'{shlex.quote(out_path)}'
            )

            LOG.info("WATERMARK FFMPEG CMD: %s", _wm_cmd)

            rc, _out, err = await runcmd(_wm_cmd)

            LOG.info(
                "WATERMARK FFMPEG RESULT rc=%s stderr=%s stdout=%s",
                rc,
                (err or "")[-4000:],
                (_out or "")[-2000:],
            )

            wm_label = f"Image · {pos_label} · periodic"

        else:
            raise Exception("Watermark image could not be loaded.")

        if rc != 0:
            _wm_error = (err or "").strip()

            if not _wm_error:
                _wm_error = (_out or "").strip()

            if not _wm_error:
                _wm_error = f"FFmpeg exited with return code {rc} and produced no error text."

            raise Exception(
                f"FFmpeg watermark failed:\n"
                f"Return code: {rc}\n"
                f"{_wm_error[-3000:]}"
            )

        out_mb  = os.path.getsize(out_path) / (1024 * 1024)
        thumb   = join(save_dir, "thumb.jpg")
        await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-ss {min(int(dur)-5, max(0, int(dur)-10))} '
            f'-i {shlex.quote(out_path)} '
            f'-vframes 1 -q:v 2 {shlex.quote(thumb)}'
        )
        thumb_ok = os.path.exists(thumb)

        caption = (
            f"🎨 **Watermark Applied**\n\n"
            f"Watermark: {wm_label}\n"
            f"Size: `{out_mb:.1f} MB` | Duration: `{_seconds_to_hms(int(dur))}`\n"
            f"_Auto-deleted from server after 2 hours._"
        )
        _dest = await _await_watermark_upload_choice(uid, status, f"Watermarked: `{out_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, uid, _dest, status, out_path, caption, dur,
                thumb_path=(thumb if thumb_ok else None), save_dir=save_dir,
            )
            try:
                await status.edit_text(
                    f"✅ **Watermark applied and uploaded.**\n\n"
                    f"📁 `{os.path.basename(out_path)}`\n"
                    f"Position: {pos_label}\n"
                    f"_Auto-deleted from server after 2 hours._"
                )
            except Exception:
                pass
        else:
            try:
                await status.edit_text(
                    f"✅ **Watermark applied. Upload skipped.**\\n\\n"
                    f"📁 `{os.path.basename(out_path)}`\\n"
                    f"_Auto-deleted from server after 2 hours._"
                )
            except Exception:
                pass
        _success = True

    except Exception as e:
        LOG.error(f"Watermark failed uid={uid}: {e}")
        err_text = str(e)
        if len(err_text) > 2500:
            err_text = "...[truncated]...\n" + err_text[-2500:]
        try:
            await status.edit_text(f"❌ **Watermark failed.**\n\n`{err_text}`")
        except Exception:
            pass
    finally:
        if save_dir and os.path.exists(save_dir):
            if _success:
                schedule_two_hour_cleanup(save_dir)
            else:
                _safe_rmtree(save_dir)


# ---------------------------------------------------------------------------
# /audiotrack — inject audio metadata lock without re-encoding (instant)
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["audiotrack", "AudioTrack", "at"]))
async def audiotrack_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'audiotrack')
    if _six_job_error:
        return await message.reply_text(_six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not _is_control_actor(message) and not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /audiotrack. Run /verify.")

    src_msg, src_media = _get_replied_video(message)
    parts = (message.text or "").split()
    args = parts[1:]
    server_source = None
    if src_media:
        at_name = " ".join(args).strip() if args else get_audio_brand_name()
    else:
        if len(args) < 2:
            return await message.reply_text(
                "📥 Reply to a Telegram video with `/audiotrack Filename`\n\n"
                "Or use `/audiotrack ServerFileName Filename` for a file already on the server."
            )
        server_name = args.pop(0).strip()
        server_source = _audiostream_find_server_file(server_name)
        if not server_source:
            return await message.reply_text(
                f"❌ Server file not found: `{server_name}`\n📁 Searched: `{DOWNLOAD_DIRECTORY}`"
            )
        at_name = " ".join(args).strip()
    if not at_name:
        at_name = get_audio_brand_name()
    # Accept the requested template: your_title|audio_language|your_title.
    # The middle field "audio_language" is replaced with each stream's detected language.
    at_template = at_name

    save_dir = join(DOWNLOAD_DIRECTORY, f"at_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status   = await message.reply_text("⬇️ Downloading video…")
    user_tasks[user_id] = True

    try:
        if server_source:
            dl_path = join(save_dir, os.path.basename(server_source))
            shutil.copy2(server_source, dl_path)
        else:
            dl_path = await client.download_media(
                src_media,
                file_name=join(save_dir, f"src_{src_msg.id}"),
                progress=progress_for_telegram_download,
                progress_args=(status, time.time(), f"src_{src_msg.id}"),
            )
        if not dl_path or not os.path.isfile(dl_path) or os.path.getsize(dl_path) <= 0:
            raise Exception("Source video download/copy failed.")
        out_path = join(save_dir, f"locked_{int(time.time())}.mkv")

        await status.edit_text(
            f"🔒 Locking audio track metadata…\n"
            f"Name: `{at_name}`\n"
            f"_No re-encode — this will finish in seconds._"
        )

        # Probe language tags first, then stamp each audio stream with
        # brand|language|title while keeping the original media streams copied.
        source_info = await _ffprobe_video(dl_path)
        source_tracks = source_info.get("audio_streams") or []
        meta_args = _audio_metadata_args(
            len(source_tracks), source_tracks, at_template,
            os.path.splitext(os.path.basename(dl_path))[0],
            user_id=user_id,
        )
        meta_str = " ".join(shlex.quote(x) for x in meta_args)

        rc, _out, err = await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-i {shlex.quote(dl_path)} '
            f'-map 0 -c copy '
            f'-map_metadata -1 '
            f'{meta_str} '
            f'{shlex.quote(out_path)}'
        )
        if rc != 0:
            raise Exception(f"FFmpeg audiotrack failed:\n{err.strip()[-1500:]}")

        out_mb   = os.path.getsize(out_path) / (1024 * 1024)
        info     = await _ffprobe_video(out_path)
        dur      = info["duration"]
        # Generate thumb
        thumb    = join(save_dir, "thumb.jpg")
        await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-ss {max(0, int(dur) // 2)} '
            f'-i {shlex.quote(out_path)} '
            f'-vframes 1 -q:v 2 {shlex.quote(thumb)}'
        )
        thumb_ok = os.path.exists(thumb)

        caption = (
            f"🔒 **Audio Track Locked**\n\n"
            f"Brand: `{at_name}`\n"
            f"Tracks locked: Audio 0, 1, 2\n"
            f"Global metadata: ❌ Wiped\n"
            f"Re-encoded: ❌ (stream copy)\n"
            f"Size: `{out_mb:.1f} MB`\n"
            f"_Visible in VLC → Track Info, MX Player, Telegram audio selector._\n"
            f"_Auto-deleted from server after 2 hours._"
        )
        _dest = await _await_upload_choice(user_id, status, f"Audio locked: `{out_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, user_id, _dest, status, out_path, caption, dur,
                thumb_path=(thumb if thumb_ok else None), save_dir=save_dir,
            )
            try:
                await status.edit_text(
                    f"✅ **Audio track locked and uploaded.**\n\n"
                    f"Brand: `{at_name}`\n"
                    f"📁 `{os.path.basename(out_path)}`\n"
                    f"_Auto-deleted from server after 2 hours._"
                )
            except Exception:
                pass
        if save_dir and os.path.exists(save_dir):
            schedule_two_hour_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Audiotrack cmd failed uid={user_id}: {e}")
        err_text = str(e)
        if len(err_text) > 2500:
            err_text = "...[truncated]...\n" + err_text[-2500:]
        try:
            await status.edit_text(f"**Audio track lock failed.**\n\n`{err_text}`")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        user_tasks.pop(user_id, None)


# ---------------------------------------------------------------------------
# /16x9 — scale+crop any replied video to 1280×720 (16:9)
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["16x9", "169", "crop169", "16by9"]) & AUTH)
async def cmd_16x9(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /16x9. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with `/16x9` to convert it to **1280×720 16:9** format.\n\n"
            "What it does:\n"
            "• Scale to fill 1280×720 (no black bars)\n"
            "• Crop to exact 16:9 (center crop)\n"
            "• Re-encode: H.264 CRF 20, AAC audio copy"
        )

    save_dir   = join(DOWNLOAD_DIRECTORY, f"169_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status     = await message.reply_text("⬇️ Downloading video…")
    user_tasks[user_id] = time.time()

    try:
        dl_path  = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        out_path = join(save_dir, f"16x9_{int(time.time())}.mp4")

        await status.edit_text("🔲 **Converting to 16:9 (1280×720)…**\n\n⚙️ Starting…")

        task_id = f"{user_id % 10000:04d}{int(time.time()) % 100000}"
        ffmpeg_jobs[task_id] = {
            "uid":          user_id,
            "chat_id":      message.chat.id,
            "status_msg":   status,
            "output_file":  out_path,
            "save_dir":     save_dir,
            "duration":     0.0,
            "done":         False,
            "cancelled":    False,
            "proc":         None,
            "cur_time_sec": 0.0,
            "cur_size_kb":  0,
        }

        # Get source duration for progress bar
        info = await _ffprobe_video(dl_path)
        ffmpeg_jobs[task_id]["duration"] = info.get("duration", 0)

        await status.edit_text(
            f"🔲 **16:9 Crop Running...**\n\n"
            f"[{'░' * 10}] 0%\n\n"
            f"⏱️ Time : `00:00:00` / `{_ff_sec_to_hms(info.get('duration', 0))}`\n"
            f"📦 Size : `—`\n"
            f"🆔 Task : `{task_id}`",
            reply_markup=_ffmpeg_running_kb(task_id),
        )

        crop_args = (
            f'-i {shlex.quote(dl_path)} '
            f'-vf "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720" '
            f'-c:v libx264 -preset medium -crf 20 '
            f'-c:a copy -movflags +faststart '
            f'{shlex.quote(out_path)}'
        )
        await _run_ffmpeg_task(client, crop_args, task_id)

    except Exception as e:
        LOG.error(f"/16x9 failed uid={user_id}: {e}")
        try:
            await status.edit_text(f"❌ **16:9 crop failed.**\n\n`{e}`")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        user_tasks.pop(user_id, None)


# ---------------------------------------------------------------------------
# /merge — collect videos then concatenate them
# ---------------------------------------------------------------------------

@app.on_message(filters.command("merge"))
async def merge_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    # Dedicated multi-job slot
    _six_job_id, _six_job_error = _six_acquire(user_id, 'merge')
    if _six_job_error:
        return await message.reply_text(_six_job_error)

    # Release the dedicated slot when this command handler ends.
    import asyncio as _six_asyncio
    _six_handler_task = _six_asyncio.current_task()
    if _six_handler_task is not None:
        _six_handler_task.add_done_callback(
            lambda _t, _jid=_six_job_id: _six_release(_jid)
        )
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /merge. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")
    if user_id in merge_sessions:
        return await message.reply_text(_merge_session_status(merge_sessions[user_id]))

    save_dir = join(DOWNLOAD_DIRECTORY, f"merge_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    sess = {"save_dir": save_dir, "videos": [], "started_at": time.time(), "chat_id": message.chat.id}
    merge_sessions[user_id] = sess
    msg = await message.reply_text(
        f"🧩 **Merge session started.**\n\n"
        f"Send me **2 to {MERGE_MAX_VIDEOS} videos** one by one in the order you want them joined. "
        "After the last one, send `/merge_done`.\n\nCancel any time with `/merge_cancel`.\n"
        "Session expires in 30 min if you stop sending."
    )
    sess["status_msg_id"] = msg.id


@app.on_message(filters.command("merge_cancel") & AUTH)
async def merge_cancel_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    sess    = merge_sessions.pop(user_id, None)
    if not sess:
        return await message.reply_text("No active merge session.")
    _safe_rmtree(sess["save_dir"])
    await message.reply_text("🧩 Merge session cancelled — collected videos discarded.")


@app.on_message(filters.command("merge_done"))
async def merge_done_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)
    sess    = merge_sessions.get(user_id)
    if not sess:
        return await message.reply_text("No active merge session. Start one with /merge.")
    if len(sess["videos"]) < 2:
        return await message.reply_text(
            f"Need at least **2 videos**. You have `{len(sess['videos'])}`. "
            "Send more, or /merge_cancel."
        )
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job — finish or /cancelme first.")
    asyncio.create_task(run_merge(client, message, sess))


@app.on_message((filters.private | filters.group) & (filters.video | filters.document) & AUTH, group=1)
async def merge_video_collector(client: Client, message: Message):
    user_id = _message_actor_id(message)
    if user_id in pending_cookies_users:
        return
    sess = merge_sessions.get(user_id)
    if not sess:
        return

    if time.time() - sess["started_at"] > MERGE_SESSION_TTL:
        merge_sessions.pop(user_id, None)
        _safe_rmtree(sess["save_dir"])
        return await message.reply_text("🧩 Merge session expired (30 min idle). Start again with /merge.")

    src = None
    if message.video:
        src = message.video
    elif message.document and (message.document.mime_type or "").startswith("video/"):
        src = message.document
    if not src:
        return

    if len(sess["videos"]) >= MERGE_MAX_VIDEOS:
        return await message.reply_text(
            f"🧩 Already at the max of `{MERGE_MAX_VIDEOS}` videos. Send /merge_done to merge."
        )

    idx = len(sess["videos"]) + 1
    ack = await message.reply_text(f"⬇️ Downloading video #{idx}...")
    try:
        path   = await message.download(file_name=join(sess["save_dir"], f"part_{idx:02d}"))
        info   = await _ffprobe_video(path)
        codec_v, codec_a, width = "?", "?", 0
        try:
            probe2 = await runcmd(f'ffprobe -v error -hide_banner -print_format json '
                                  f'-show_streams {shlex.quote(path)}')
            data = json.loads(probe2[1] or "{}")
            for s in data.get("streams", []):
                if s.get("codec_type") == "video" and codec_v == "?":
                    codec_v = s.get("codec_name", "?")
                    width   = int(s.get("width") or 0)
                elif s.get("codec_type") == "audio" and codec_a == "?":
                    codec_a = s.get("codec_name", "?")
        except Exception:
            pass

        sess["videos"].append({"path": path, "duration": info["duration"],
                               "height": info["video_height"], "width": width,
                               "codec_v": codec_v, "codec_a": codec_a,
                               "audio_streams": info["audio_streams"]})
        sess["started_at"] = time.time()
        await ack.edit_text(_merge_session_status(sess))
    except Exception as e:
        LOG.error(f"merge collector failed: {e}")
        try: await ack.edit_text(f"Failed to add video: `{e}`")
        except Exception: pass


# ---------------------------------------------------------------------------
# /title — burn text overlay onto a replied video
# ---------------------------------------------------------------------------

@app.on_message(filters.command("title") & AUTH)
async def title_cmd(client: Client, message: Message):
    user_id = _message_actor_id(message)

    if not is_verified(user_id):
        return await message.reply_text(
            "You must be **verified** to use /title. Run /verify first."
        )
    if user_id in user_tasks:
        return await message.reply_text(
            "You already have an active job. Wait for it to finish or /cancelme."
        )
    if user_id in title_jobs:
        return await message.reply_text(
            "You already have a pending title job. Use the buttons or /cancel_title."
        )

    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:** `/title <your text>` _(reply to a video)_\n\n"
            "Example: `/title Dragon Ball Super — Episode 1`\n\n"
            "• Title is burned into the video with FFmpeg.\n"
            "• Videos **> 46 minutes**: title disappears in the last 3 minutes."
        )

    title_text = " ".join(message.command[1:]).strip()
    if len(title_text) > 100:
        return await message.reply_text("Title too long (max 100 characters).")

    # Must reply to a video
    src_msg = message.reply_to_message
    src_media = None
    if src_msg:
        if src_msg.video:
            src_media = src_msg.video
        elif src_msg.document and (src_msg.document.mime_type or "").startswith("video/"):
            src_media = src_msg.document

    if not src_media:
        return await message.reply_text(
            "Please **reply to a video** with `/title <your text>`."
        )

    status = await message.reply_text("⬇️ Downloading video for title overlay…")

    save_dir = join(DOWNLOAD_DIRECTORY, f"title_{user_id}_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, "source"))
        info    = await _ffprobe_video(dl_path)
        dur     = info["duration"]
        height  = info["video_height"]
    except Exception as e:
        _safe_rmtree(save_dir)
        return await status.edit_text(f"❌ Download / probe failed: `{e}`")

    state = {
        "user_id":      user_id,
        "src_path":     dl_path,
        "save_dir":     save_dir,
        "duration":     dur,
        "video_height": height,
        "title_text":   title_text,
        "status_msg":   status,
    }
    title_jobs[user_id] = state

    try:
        await status.edit_text(_title_menu_text(state), reply_markup=_title_kb(user_id))
    except Exception:
        pass


@app.on_message(filters.command("cancel_title") & AUTH)
async def cancel_title_cmd(_, message: Message):
    user_id = _message_actor_id(message)
    state   = title_jobs.pop(user_id, None)
    if state:
        _safe_rmtree(state.get("save_dir", ""))
    await message.reply_text("Title job cancelled." if state else "No pending title job.")


@app.on_callback_query(filters.regex(r"^ti:(\d+):(pos|cancel):?(\w*)$"))
async def title_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    action = parts[2]
    val    = parts[3] if len(parts) > 3 else ""

    if not _callback_actor_matches(cq, uid):
        return await cq.answer("Not your job.", show_alert=True)

    state = title_jobs.get(uid)
    if not state:
        await cq.answer("Session expired.", show_alert=True)
        try: await cq.message.edit_reply_markup(None)
        except Exception: pass
        return

    if action == "cancel":
        title_jobs.pop(uid, None)
        _safe_rmtree(state.get("save_dir", ""))
        await cq.answer("Cancelled.")
        try: await cq.message.edit_text("Title overlay cancelled.", reply_markup=None)
        except Exception: pass
        return

    if action == "pos":
        if val not in TITLE_POS_MAP:
            return await cq.answer("Unknown position.", show_alert=True)
        await cq.answer(f"Position: {TITLE_POS_MAP[val][0]}")
        title_jobs.pop(uid, None)
        asyncio.create_task(run_title(client, cq.message, state, val))


# ---------------------------------------------------------------------------
# /start inline button helpers
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^show_help$"))
async def cb_show_help(_, cq: CallbackQuery):
    await cq.message.reply_text(HELP_TEXT, disable_web_page_preview=True)
    await cq.answer()


@app.on_callback_query(filters.regex(r"^show_plans$"))
async def cb_show_plans(_, cq: CallbackQuery):
    await cq.message.reply_text(render_plans_text(), disable_web_page_preview=True)
    await cq.answer()


@app.on_callback_query(filters.regex(r"^show_channels$"))
async def cb_show_channels(_, cq: CallbackQuery):
    ok = await _refresh_channel_playlist()
    if not ok:
        await cq.answer("Playlist load failed.", show_alert=True)
        return
    await cq.message.reply_text("**Browse channels**\n\nPick a channel group:", reply_markup=_channel_root_kb())
    await cq.answer()


@app.on_callback_query(filters.regex(r"^show_download$"))
async def cb_show_download(_, cq: CallbackQuery):
    await cq.message.reply_text(
        "📥 **OTT Download**\n\n"
        "Send the video page URL with:\n"
        "`/download <url> [filename]`\n\n"
        "Example:\n"
        "`/download https://www.example.com/video MyShow`\n\n"
        "For login-protected content, send `/set_cookies` first and upload "
        "your browser-exported cookies file.",
        disable_web_page_preview=True,
    )
    await cq.answer()


@app.on_callback_query(filters.regex(r"^show_verify$"))
async def cb_show_verify(client: Client, cq: CallbackQuery):
    msg = cq.message
    msg.from_user = cq.from_user
    msg.command   = ["verify"]
    await verify_cmd(client, msg)
    await cq.answer()


@app.on_callback_query(filters.regex(r"^noop$"))
async def cb_noop(_, cq: CallbackQuery):
    await cq.answer()


# ---------------------------------------------------------------------------
# /stats — owner/admin bot statistics dashboard
# ---------------------------------------------------------------------------

def _dir_size_mb(path: str) -> float:
    """Return total size of a directory tree in MB."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except Exception:
        pass
    return total / (1024 * 1024)


@app.on_message(filters.command(["stats", "Stats"]) & AUTH)
async def stats_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /stats.")

    # ── Users ──────────────────────────────────────────────────────────────
    verified_data   = load_verified()
    total_verified  = len(verified_data)
    total_admins    = len(load_admins())

    # ── Active jobs ────────────────────────────────────────────────────────
    active_rec_count   = sum(len(v) for v in active_recs.values())
    active_compress    = len(compress_jobs)
    active_ss          = len(ss_jobs)
    active_merge       = len(merge_sessions)
    active_misc        = len([u for u in user_tasks
                               if u not in compress_jobs
                               and u not in ss_jobs
                               and u not in merge_sessions
                               and u not in active_recs])

    # ── Disk ───────────────────────────────────────────────────────────────
    dl_mb   = _dir_size_mb(DOWNLOAD_DIRECTORY)
    data_mb = _dir_size_mb(DATA_DIRECTORY)

    # ── Uptime ─────────────────────────────────────────────────────────────
    up_sec   = int(time.time() - _BOT_START_TIME)
    up_h, r  = divmod(up_sec, 3600)
    up_m, up_s = divmod(r, 60)
    uptime   = f"{up_h}h {up_m}m {up_s}s"

    # ── Relay ──────────────────────────────────────────────────────────────
    relay_state = "✅ ON" if relay_enabled else "❌ OFF"

    # ── Group ──────────────────────────────────────────────────────────────
    group_info = f"`{GROUP_CHAT_ID}`" if GROUP_CHAT_ID else "not set (open)"

    text = (
        f"📊 **Bot Statistics**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 **Users**\n"
        f"  Verified : `{total_verified}`\n"
        f"  Admins   : `{total_admins}`\n\n"
        f"⚙️ **Active Jobs**\n"
        f"  Recordings  : `{active_rec_count}`\n"
        f"  Compress    : `{active_compress}`\n"
        f"  Screenshot  : `{active_ss}`\n"
        f"  Merge       : `{active_merge}`\n"
        f"  Other jobs  : `{active_misc}`\n\n"
        f"💾 **Disk Usage**\n"
        f"  Downloads : `{dl_mb:.1f} MB`\n"
        f"  Data      : `{data_mb:.1f} MB`\n\n"
        f"📡 **DM Relay** : {relay_state}  "
        f"(blocked: `{len(relay_blocked)}`)\n"
        f"📌 **Group ID** : {group_info}\n\n"
        f"⏱ **Uptime** : `{uptime}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply_text(text)


# ---------------------------------------------------------------------------
# /broadcast — send a message to all verified users (owner/admin only)
# ---------------------------------------------------------------------------

_broadcast_running: bool = False


@app.on_message(filters.command(["broadcast", "Broadcast", "bc"]) & AUTH)
async def broadcast_cmd(client: Client, message: Message):
    global _broadcast_running
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /broadcast.")

    if _broadcast_running:
        return await message.reply_text("⏳ A broadcast is already in progress. Wait for it to finish.")

    text_parts = message.command
    if len(text_parts) < 2:
        return await message.reply_text(
            "**Usage:** `/broadcast <your message>`\n\n"
            "Sends the message to all **verified users**.\n"
            "Example: `/broadcast Bot updated! New features added.`"
        )

    bc_text = " ".join(text_parts[1:])
    verified_data = load_verified()
    user_ids = list(verified_data.keys())

    if not user_ids:
        return await message.reply_text("No verified users to broadcast to.")

    _broadcast_running = True
    status = await message.reply_text(
        f"📢 **Broadcasting** to `{len(user_ids)}` verified users…\n_This may take a moment._"
    )

    sent = 0
    failed = 0
    blocked = 0

    for i, user_id_str in enumerate(user_ids, 1):
        try:
            target_id = int(user_id_str)
            if is_owner(target_id) or is_admin(target_id):
                continue  # skip staff
            await client.send_message(target_id, bc_text)
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "forbidden" in err or "user is deactivated" in err:
                blocked += 1
            else:
                failed += 1

        # Update progress every 20 users
        if i % 20 == 0 or i == len(user_ids):
            try:
                await status.edit_text(
                    f"📢 **Broadcasting…** `{i}` / `{len(user_ids)}`\n"
                    f"✅ Sent: `{sent}`  ❌ Failed: `{failed}`  🚫 Blocked: `{blocked}`"
                )
            except Exception:
                pass

        await asyncio.sleep(0.05)   # ~20 msg/s — stay under flood limit

    _broadcast_running = False
    await status.edit_text(
        f"📢 **Broadcast complete!**\n\n"
        f"✅ Sent    : `{sent}`\n"
        f"🚫 Blocked : `{blocked}` _(user blocked the bot)_\n"
        f"❌ Failed  : `{failed}`"
    )


# ---------------------------------------------------------------------------
# /userinfo — inspect any verified user  |  /unverify — remove verification
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["userinfo", "Userinfo", "user_info"]) & AUTH)
async def userinfo_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /userinfo.")

    args = message.command
    if len(args) < 2:
        return await message.reply_text("**Usage:** `/userinfo <user_id>`")

    try:
        target_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID — must be a number.")

    vdata   = load_verified()
    v_entry = vdata.get("verified", {}).get(str(target_id))
    p_entry = vdata.get("pending",  {}).get(str(target_id))

    # ── Resolve display name from Telegram ─────────────────────────────────
    try:
        tg_user  = await client.get_users(target_id)
        name     = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
        username = f"@{tg_user.username}" if tg_user.username else "_none_"
    except Exception:
        name     = "_unknown_"
        username = "_unknown_"

    # ── Roles ───────────────────────────────────────────────────────────────
    roles = []
    if is_owner(target_id):  roles.append("👑 Owner")
    if is_admin(target_id):  roles.append("🛡 Admin")
    role_str = "  ".join(roles) if roles else "👤 User"

    # ── Verification status ─────────────────────────────────────────────────
    if is_owner(target_id) or is_admin(target_id):
        status_str = "✅ Permanent (staff)"
        plan_str   = "_N/A_"
        expires_str = "_N/A_"
        added_str   = "_N/A_"
    elif v_entry:
        expires    = v_entry.get("expires_at")
        added      = v_entry.get("added_at", "_unknown_")
        plan_str   = v_entry.get("plan", "_unknown_")
        added_str  = added[:10] if isinstance(added, str) and len(added) >= 10 else str(added)
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if datetime.now(tz) > exp_dt:
                    status_str  = "⏰ Expired"
                    expires_str = exp_dt.strftime("%Y-%m-%d %H:%M")
                else:
                    status_str  = "✅ Active"
                    expires_str = exp_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                status_str  = "✅ Active (no expiry)"
                expires_str = "_parse error_"
        else:
            status_str  = "✅ Active (no expiry)"
            expires_str = "Never"
    elif p_entry:
        status_str  = "⏳ Pending verification"
        plan_str    = p_entry.get("plan", "_unknown_")
        expires_str = "_N/A_"
        added_str   = p_entry.get("added_at", "_unknown_")
    else:
        status_str  = "❌ Not verified"
        plan_str    = "_N/A_"
        expires_str = "_N/A_"
        added_str   = "_N/A_"

    # ── Active jobs ─────────────────────────────────────────────────────────
    job_parts = []
    if target_id in active_recs and active_recs[target_id]:
        job_parts.append(f"🔴 {len(active_recs[target_id])} recording(s)")
    if target_id in compress_jobs:  job_parts.append("🗜 compress")
    if target_id in ss_jobs:        job_parts.append("📸 screenshot")
    if target_id in merge_sessions: job_parts.append("🔗 merge")
    jobs_str = "  ".join(job_parts) if job_parts else "_none_"

    text = (
        f"👤 **User Info**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 **ID**       : `{target_id}`\n"
        f"📛 **Name**     : {name}\n"
        f"🔗 **Username** : {username}\n"
        f"🎭 **Role**     : {role_str}\n\n"
        f"🔐 **Status**   : {status_str}\n"
        f"📦 **Plan**     : {plan_str}\n"
        f"📅 **Added**    : {added_str}\n"
        f"⏳ **Expires**  : {expires_str}\n\n"
        f"⚙️ **Active jobs** : {jobs_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply_text(text)


@app.on_message(filters.command(["unverify", "Unverify"]) & AUTH)
async def unverify_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /unverify.")

    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:** `/unverify <user_id>`\n\n"
            "Removes the user from verified list immediately."
        )

    try:
        target_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID — must be a number.")

    if is_owner(target_id):
        return await message.reply_text("❌ Cannot unverify the owner.")

    vdata = load_verified()
    removed_from = []

    if str(target_id) in vdata.get("verified", {}):
        del vdata["verified"][str(target_id)]
        removed_from.append("verified")
    if str(target_id) in vdata.get("pending", {}):
        del vdata["pending"][str(target_id)]
        removed_from.append("pending")

    if not removed_from:
        return await message.reply_text(f"ℹ️ User `{target_id}` was not in the verified or pending list.")

    save_verified(vdata)
    await message.reply_text(
        f"✅ User `{target_id}` removed from **{' & '.join(removed_from)}** list.\n"
        f"They will need to re-verify to use the bot."
    )


# ---------------------------------------------------------------------------
# /cancelall — master kill-switch: stop every active job across all users
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["cancelall", "Cancelall", "cancel_all"]) & AUTH)
async def cancelall_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /cancelall.")

    import psutil as _psutil

    recs_killed   = 0
    jobs_killed   = 0
    merge_killed  = 0
    other_killed  = 0

    # ── Kill all active recordings (skip owner & admins) ───────────────────
    for user_id, recs in list(active_recs.items()):
        if is_owner(user_id) or is_admin(user_id):
            continue
        for rec_id, entry in list(recs.items()):
            cancelled_recs.add((user_id, rec_id))
            pid = entry.get("ffmpeg_pid")
            if pid:
                try:
                    _psutil.Process(pid).terminate()
                    LOG.info(f"/cancelall: sent SIGTERM to FFmpeg pid={pid} user={user_id} rec={rec_id}")
                except Exception as e:
                    LOG.warning(f"/cancelall: could not terminate FFmpeg pid={pid}: {e}")
            pt = entry.get("progress_task")
            if pt:
                pt.cancel()
            recs_killed += 1

    # ── Kill compress / download / screenshot jobs (skip owner & admins) ───
    for user_id in list(user_tasks.keys()):
        if is_owner(user_id) or is_admin(user_id):
            continue
        cancelled_users.add(user_id)
        pid = user_ffmpeg_pids.get(user_id)
        if pid:
            try:
                _psutil.Process(pid).terminate()
                LOG.info(f"/cancelall: sent SIGTERM to FFmpeg pid={pid} user={user_id} (job)")
            except Exception as e:
                LOG.warning(f"/cancelall: could not terminate FFmpeg pid={pid}: {e}")
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
        if user_id in compress_jobs:
            jobs_killed += 1
        elif user_id in ss_jobs:
            jobs_killed += 1
        else:
            jobs_killed += 1

    # ── Clear merge sessions (skip owner & admins) ─────────────────────────
    for user_id in list(merge_sessions.keys()):
        if is_owner(user_id) or is_admin(user_id):
            continue
        merge_sessions.pop(user_id, None)
        merge_killed += 1

    # ── Clear reclink / title wizard sessions (skip owner & admins) ────────
    for user_id in list(reclink_jobs.keys()):
        if is_owner(user_id) or is_admin(user_id):
            continue
        reclink_jobs.pop(user_id, None)
        other_killed += 1
    for user_id in list(title_jobs.keys()):
        if is_owner(user_id) or is_admin(user_id):
            continue
        title_jobs.pop(user_id, None)
        other_killed += 1

    total = recs_killed + jobs_killed + merge_killed + other_killed
    if total == 0:
        return await message.reply_text("ℹ️ No active jobs to cancel.")

    await message.reply_text(
        f"🛑 **All jobs cancelled!**\n\n"
        f"🔴 Recordings stopped : `{recs_killed}`\n"
        f"⚙️ Jobs stopped       : `{jobs_killed}`\n"
        f"🔗 Merge sessions     : `{merge_killed}`\n"
        f"🔎 Other sessions     : `{other_killed}`"
    )


# ---------------------------------------------------------------------------
# /listusers — paginated list of all verified users (owner/admin)
# /extendplan <id> <days> — extend a user's expiry by N days
# ---------------------------------------------------------------------------

_PAGE_SIZE = 15


@app.on_message(filters.command(["listusers", "Listusers", "list_users", "lu"]) & AUTH)
async def listusers_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /listusers.")

    args    = message.command
    page    = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    vdata   = load_verified()
    entries = vdata.get("verified", {})

    if not entries:
        return await message.reply_text("ℹ️ No verified users yet.")

    # Sort by added_at descending (newest first)
    def _sort_key(item):
        return item[1].get("added_at", "") if isinstance(item[1], dict) else ""

    sorted_entries = sorted(entries.items(), key=_sort_key, reverse=True)
    total          = len(sorted_entries)
    total_pages    = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page           = max(1, min(page, total_pages))
    start          = (page - 1) * _PAGE_SIZE
    page_entries   = sorted_entries[start : start + _PAGE_SIZE]

    now = datetime.now(tz)
    lines = []
    for i, (uid_str, entry) in enumerate(page_entries, start + 1):
        plan    = entry.get("plan", "?") if isinstance(entry, dict) else "?"
        expires = entry.get("expires_at") if isinstance(entry, dict) else None
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if now > exp_dt:
                    status_icon = "⏰"
                    exp_str     = exp_dt.strftime("%y-%m-%d")
                else:
                    status_icon = "✅"
                    exp_str     = exp_dt.strftime("%y-%m-%d")
            except Exception:
                status_icon, exp_str = "✅", "?"
        else:
            status_icon, exp_str = "✅", "∞"

        lines.append(f"{i}. {status_icon} `{uid_str}` — {plan} — exp: {exp_str}")

    text = (
        f"👥 **Verified Users** — Page `{page}` / `{total_pages}`  (total: `{total}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines)
        + f"\n\n_Use_ `/listusers {page + 1}` _for next page_"
          if page < total_pages else ""
    )
    await message.reply_text(text)


@app.on_message(filters.command(["extendplan", "Extendplan", "extend"]) & AUTH)
async def extendplan_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /extendplan.")

    args = message.command
    if len(args) < 3:
        return await message.reply_text(
            "**Usage:** `/extendplan <user_id> <days>`\n\n"
            "Extends the user's current expiry by the given number of days.\n"
            "Example: `/extendplan 123456789 30`"
        )

    try:
        target_id = int(args[1])
        days      = int(args[2])
        if days <= 0:
            raise ValueError
    except ValueError:
        return await message.reply_text("❌ Invalid arguments. User ID and days must be positive numbers.")

    vdata   = load_verified()
    v_entry = vdata.get("verified", {}).get(str(target_id))

    if not v_entry:
        return await message.reply_text(
            f"❌ User `{target_id}` is not in the verified list.\n"
            f"Use `/userinfo {target_id}` to check their status."
        )

    # Calculate new expiry
    expires = v_entry.get("expires_at")
    now     = datetime.now(tz)
    if expires:
        try:
            base = datetime.fromisoformat(expires)
            base = max(base, now)   # if already expired, extend from today
        except Exception:
            base = now
    else:
        base = now   # no expiry set — start counting from today

    new_expiry = base + timedelta(days=days)
    v_entry["expires_at"] = new_expiry.isoformat()
    vdata["verified"][str(target_id)] = v_entry
    save_verified(vdata)

    await message.reply_text(
        f"✅ Extended `{target_id}`'s access by **{days} days**.\n"
        f"📅 New expiry: `{new_expiry.strftime('%Y-%m-%d %H:%M')}`"
    )


# ---------------------------------------------------------------------------
# /premium_add, /premium_expire, /premium_list — owner/admin only
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["premium_add", "addpremium", "padd"]))
async def premium_add_cmd(_client: Client, message: Message):
    anonymous_admin = _is_official_anonymous_admin(message)
    uid = _message_actor_id(message)

    # Authorization is checked inside the handler so Anonymous Admin is not
    # blocked by the generic AUTH filter before reaching this code.
    if not (anonymous_admin or is_owner(uid) or is_admin(uid)):
        return await message.reply_text("❌ Only owner/admin can use /premium_add.")

    parts = message.command   # [cmd, user_id, days?, plan?]
    if len(parts) < 2:
        return await message.reply_text(
            "**Usage:** `/premium_add <user_id> [days] [plan_name]`\n\n"
            "Examples:\n"
            "`/premium_add 123456789` — 30 days Standard\n"
            "`/premium_add 123456789 7` — 7 days\n"
            "`/premium_add 123456789 90 Pro` — 90 days Pro\n"
            "`/premium_add 123456789 forever` — Lifetime (no expiry)\n\n"
            f"Plans: {', '.join(_PREMIUM_PLANS)}"
        )

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID — must be a number.")

    # Parse optional days argument
    days: int | None = 30
    plan = "Standard"

    if len(parts) >= 3:
        raw_days = parts[2].lower()
        if raw_days in ("forever", "lifetime", "0", "none", "∞"):
            days = None
            plan = "Lifetime"
        else:
            raw_days = raw_days.rstrip("d").rstrip("day").rstrip("days")
            try:
                days = int(raw_days)
                if days <= 0:
                    return await message.reply_text("❌ Days must be a positive number.")
            except ValueError:
                # Maybe 3rd arg is the plan name, not days
                plan = parts[2]
                days = 30

    if len(parts) >= 4:
        plan = " ".join(parts[3:])

    entry = add_premium(target_id, days, plan, uid)

    exp = entry.get("expires_at")
    if exp is None:
        exp_str = "♾️ **Lifetime** (no expiry)"
    else:
        try:
            exp_dt  = datetime.fromisoformat(exp)
            exp_str = f"📅 `{exp_dt.strftime('%Y-%m-%d %H:%M')}` ({days} days)"
        except Exception:
            exp_str = str(exp)

    await message.reply_text(
        f"✅ **Premium Granted!**\n\n"
        f"👤 User ID : `{target_id}`\n"
        f"🎖️ Plan    : **{plan}**\n"
        f"⏳ Expires : {exp_str}\n"
        f"👑 Added by: `{uid}`"
    )


@app.on_message(filters.command(["premium_expire", "expremium", "premiumexpire", "premiumremove", "premiumdel"]) & AUTH)
async def premium_expire_cmd(_client: Client, message: Message):
    anonymous_admin = _is_official_anonymous_admin(message)
    uid = _message_actor_id(message)
    if not (anonymous_admin or is_owner(uid) or is_admin(uid)):
        return await message.reply_text("❌ Only owner/admin can use /premium_expire.")

    parts = message.command
    if len(parts) < 2:
        return await message.reply_text(
            "**Usage:** `/premium_expire <user_id>`\n\n"
            "Example: `/premium_expire 123456789`\n"
            "This immediately expires the user's premium plan."
        )

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    if is_owner(target_id):
        return await message.reply_text("❌ Cannot expire the owner's premium.")

    ok = remove_premium(target_id)
    if ok:
        await message.reply_text(
            f"✅ **Premium Expired**\n\n"
            f"👤 User `{target_id}`'s premium has been revoked immediately."
        )
    else:
        await message.reply_text(
            f"⚠️ User `{target_id}` has no premium entry on record.\n"
            f"Use `/userinfo {target_id}` to check their status."
        )


@app.on_message(filters.command(["premium_list", "listpremium", "premiumlist", "plist"]) & AUTH)
async def premium_list_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    if not _is_control_actor(message):
        return await message.reply_text("❌ Only owner/admin can use /premium_list.")

    data  = load_premium()
    users = data.get("users", {})

    if not users:
        return await message.reply_text(
            "📋 **Premium Users List**\n\n"
            "No premium users found.\n"
            "Use `/premium_add <user_id>` to grant premium."
        )

    now    = datetime.now(tz)
    active = []
    expired = []

    for uid_str, entry in users.items():
        exp = entry.get("expires_at")
        if exp is None:
            active.append((uid_str, entry))   # lifetime
        else:
            try:
                exp_dt = datetime.fromisoformat(exp)
                if now < exp_dt:
                    active.append((uid_str, entry))
                else:
                    expired.append((uid_str, entry))
            except Exception:
                active.append((uid_str, entry))

    # Sort active by expiry (lifetime last)
    def sort_key(item):
        exp = item[1].get("expires_at")
        return exp if exp else "9999-12-31"
    active.sort(key=sort_key)

    lines = [f"👑 **Premium Users** — {len(active)} active / {len(expired)} expired\n"]

    if active:
        lines.append("**✅ Active:**")
        for uid_str, entry in active:
            lines.append(_premium_status_line(uid_str, entry))

    if expired:
        lines.append("\n**❌ Expired:**")
        for uid_str, entry in expired[:10]:   # cap at 10 to avoid message overflow
            lines.append(_premium_status_line(uid_str, entry))
        if len(expired) > 10:
            lines.append(f"  _…and {len(expired) - 10} more expired entries_")

    lines.append(f"\n_Updated: {now.strftime('%Y-%m-%d %H:%M')} IST_")

    text = "\n".join(lines)
    # Telegram message limit is 4096 chars
    if len(text) > 4000:
        text = text[:3990] + "\n…_(truncated)_"

    await message.reply_text(text)


# /mypremium — any authorized user can check their own status
@app.on_message(filters.command(["mypremium", "myplan", "mystatus", "plan"]) & AUTH)
async def my_premium_cmd(_client: Client, message: Message):
    uid = _message_actor_id(message)
    now  = datetime.now(tz)

    # Owner / admin → always unlimited
    if uid in OWNER_IDS:
        return await message.reply_text(
            "👑 **Tumhara Status: Owner**\n\n"
            "✅ Unlimited access — sab kuch khula hai.\n\n"
        )
    if is_admin(uid):
        return await message.reply_text(
            "🛡️ **Tumhara Status: Admin**\n\n"
            "✅ Unlimited access — sab kuch khula hai.\n\n"
        )

    data  = load_premium()
    entry = data.get("users", {}).get(str(uid))

    if not entry:
        return await message.reply_text(
            "📦 **Tumhara Plan: Free**\n\n"
            "❌ Premium nahi liya abhi tak.\n\n"
            "**Free limits:**\n"
            "Premium ke liye admin se baat karo! 🚀"
        )

    exp   = entry.get("expires_at")
    plan  = entry.get("plan", "Standard")
    added = entry.get("added_at", "?")

    if exp is None:
        exp_str     = "♾️ **Lifetime** — kabhi expire nahi hoga"
        active      = True
        remaining_d = None
    else:
        try:
            exp_dt      = datetime.fromisoformat(exp)
            active      = now < exp_dt
            remaining_d = max((exp_dt - now).days, 0) if active else 0
            exp_str     = (
                f"📅 Expire: `{exp_dt.strftime('%d %b %Y, %I:%M %p')}`\n"
                f"⏳ Bacha: **{remaining_d} din**"
                if active else
                f"❌ Expired on `{exp_dt.strftime('%d %b %Y')}`"
            )
        except Exception:
            active, exp_str, remaining_d = True, str(exp), None

    status_icon = "✅" if active else "❌"
    status_word = "Active" if active else "Expired"

    lines = [
        f"{status_icon} **Tumhara Plan: {plan}** ({status_word})\n",
        exp_str,
        f"\n🗓️ Liya tha: `{added[:10]}`",
        "\n**Tumhare limits:**",
    ]
    if active:
        lines += [
        ]
    else:
        lines += [
            "\nPremium renew ke liye admin se baat karo! 🚀",
        ]

    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# DM Relay — forward user messages to owner; relay owner replies back
# ---------------------------------------------------------------------------

_RELAY_MAP_MAX = 2000   # cap memory usage
def _owner_or_anonymous_filter(_filter, _client, message):
    return _is_control_actor(message)


_OWNER_FILTER = filters.create(_owner_or_anonymous_filter, name="OwnerOrAnonymousAdmin")


# ---------------------------------------------------------------------------
# /relay on|off — toggle relay
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["relay"]) & _OWNER_FILTER)
async def relay_toggle_cmd(_client: Client, message: Message):
    """Owner-only: /relay on|off"""
    global relay_enabled
    args = message.command
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        state = "✅ ON" if relay_enabled else "❌ OFF"
        return await message.reply_text(
            f"📡 **DM Relay** is currently **{state}**\n\n"
            "Toggle: `/relay on` | `/relay off`\n"
            "Send DM to user: `/DM <user_id> <text>`\n"
            "Block user: `/DM <user_id> delete`"
        )
    relay_enabled = args[1].lower() == "on"
    state = "✅ ON" if relay_enabled else "❌ OFF"
    await message.reply_text(f"📡 **DM Relay** turned **{state}**.")


# ---------------------------------------------------------------------------
# /DM <user_id> <text|delete> — owner → user direct message or block
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["DM", "dm"]) & _OWNER_FILTER)
async def dm_cmd(client: Client, message: Message):
    """/DM <user_id> <text>  — send message to user by ID
       /DM <user_id> delete  — block user from relay"""
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "`/DM <user_id> <your message>` — send DM to user\n"
            "`/DM <user_id> delete` — block user from relay\n\n"
            "**Blocked users:** " + (
                ", ".join(f"`{u}`" for u in relay_blocked) or "none"
            )
        )

    try:
        target_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID. Must be a number.")

    if len(args) < 3:
        return await message.reply_text(
            "❌ Provide a message or `delete`.\n"
            "Example: `/DM 123456789 Hello!`"
        )

    action = " ".join(args[2:])

    # /DM <id> delete — block from relay
    if action.lower() == "delete":
        relay_blocked.add(target_id)
        # Remove any queued relay entries for this user
        blocked_keys = [k for k, v in relay_map.items() if v == target_id]
        for k in blocked_keys:
            del relay_map[k]
        return await message.reply_text(
            f"🚫 User `{target_id}` blocked from DM relay.\n"
            f"Their messages will no longer be forwarded.\n\n"
            f"To unblock: `/DM {target_id} unblock`"
        )

    # /DM <id> unblock
    if action.lower() == "unblock":
        relay_blocked.discard(target_id)
        return await message.reply_text(f"✅ User `{target_id}` unblocked from relay.")

    # /DM <id> <text> — send message
    try:
        sent = await client.send_message(target_id, action)
        relay_map[sent.id] = target_id
        await message.reply_text(f"✅ Message sent to `{target_id}`.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to send to `{target_id}`: {e}")


# ---------------------------------------------------------------------------
# Relay user DM → owner  (verified users only; normal/unverified = skipped)
# ---------------------------------------------------------------------------

@app.on_message(filters.private & AUTH, group=5)
async def relay_to_owner(client: Client, message: Message):
    """Forward verified user DMs to the owner."""
    if not relay_enabled:
        return
    user_id = _message_actor_id(message)
    # Only relay messages from regular users (not owner/admin)
    if is_owner(user_id) or is_admin(user_id):
        return
    if not OWNER_IDS:
        return
    # Skip blocked users
    if user_id in relay_blocked:
        return
    # Skip command messages
    if message.text and message.text.startswith("/"):
        return
    # Skip during active merge session
    if user_id in merge_sessions:
        return
    # ✅ Only relay verified users — group normal users are skipped
    if not is_verified(user_id):
        return

    owner_id = list(OWNER_IDS)[0]
    u = message.from_user
    name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "Unknown"
    uname = f"@{u.username}" if u.username else "no username"
    info = (
        f"📨 **User DM**\n"
        f"👤 {name}  |  {uname}\n"
        f"🆔 `{user_id}`\n"
        f"💬 Reply to this to respond  |  `/DM {user_id} delete` to block"
    )
    try:
        fwd = await client.forward_messages(owner_id, message.chat.id, message.id)
        await client.send_message(owner_id, info)
        if len(relay_map) >= _RELAY_MAP_MAX:
            oldest = next(iter(relay_map))
            del relay_map[oldest]
        relay_map[fwd.id] = user_id
    except Exception as e:
        LOG.warning("relay_to_owner failed uid=%s: %s", user_id, e)


# ---------------------------------------------------------------------------
# Relay owner reply → user
# ---------------------------------------------------------------------------

@app.on_message(
    filters.private & filters.reply & _OWNER_FILTER,
    group=5
)
async def relay_to_user(client: Client, message: Message):
    """Relay owner's reply back to the original user."""
    replied = message.reply_to_message
    if not replied:
        return
    user_id = relay_map.get(replied.id)
    if not user_id:
        return
    try:
        await client.forward_messages(user_id, message.chat.id, message.id)
        await message.reply_text(f"✅ Reply sent to `{user_id}`.", quote=True)
    except Exception as e:
        await message.reply_text(f"❌ Could not deliver to `{user_id}`: {e}", quote=True)


# =============================================================================
# Added main commands: /qualitymax, /compressadvance, /public, /private
# =============================================================================

# Persistent Public / Private mode.
BOT_MODE_FILE = join(DATA_DIRECTORY, "bot_mode.json")
BOT_MODE = "public"


def load_bot_mode() -> str:
    """Load persistent bot mode; invalid/missing values safely become public."""
    global BOT_MODE
    data = _load_json(BOT_MODE_FILE, {"bot_mode": "public"})
    mode = str(data.get("bot_mode", "public")).strip().lower() if isinstance(data, dict) else "public"
    if mode not in {"public", "private"}:
        mode = "public"
    BOT_MODE = mode
    _save_json(BOT_MODE_FILE, {"bot_mode": BOT_MODE})
    return BOT_MODE


def save_bot_mode(mode: str) -> str:
    """Persist owner-selected Public / Private mode."""
    global BOT_MODE
    BOT_MODE = "private" if str(mode).lower() == "private" else "public"
    _save_json(BOT_MODE_FILE, {"bot_mode": BOT_MODE})
    return BOT_MODE


load_bot_mode()


def _mode_command(message: Message) -> str:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text.startswith("/"):
        return ""
    return text.split(maxsplit=1)[0].split("@", 1)[0].lower()


@app.on_message(filters.all, group=-100)
async def bot_mode_guard(client: Client, message: Message):
    """When Private Mode is enabled, only the owner can use the bot."""
    if BOT_MODE != "private":
        return
    uid = _message_actor_id(message)
    # Owner and official Anonymous Admin remain allowed.
    if is_owner(uid) or is_admin(uid):
        return
    # /public and /private are owner-only, so non-owners are denied here too.
    await message.reply_text(
        "🚫 **Access Denied**\n\n"
        "🔒 The bot is currently in **Private Mode**.\n\n"
        "Only the Bot Owner/Admin can use this bot."
    )
    raise StopPropagation


@app.on_callback_query(group=-100)
async def bot_mode_callback_guard(client: Client, cq: CallbackQuery):
    """Block old inline-button sessions too when Private Mode is enabled."""
    if BOT_MODE != "private":
        return
    uid = int(getattr(getattr(cq, "from_user", None), "id", 0) or 0)
    if is_owner(uid) or is_admin(uid):
        return
    try:
        await cq.answer("🔒 Private Mode: Owner/Admin only.", show_alert=True)
    finally:
        raise StopPropagation


@app.on_message(filters.command(["public", "private"]) & _OWNER_FILTER, group=-90)
async def bot_mode_command(client: Client, message: Message):
    command = _mode_command(message)
    if command == "/private":
        save_bot_mode("private")
        await message.reply_text(
            "🔒 **Private Mode Enabled**\n\n"
            "Only the Bot Owner/Admin can use the bot now.\n"
            "Use `/public` to return to Public Mode."
        )
    else:
        save_bot_mode("public")
        await message.reply_text(
            "🌐 **Public Mode Enabled**\n\n"
            "The bot is now available according to the normal verification/access rules."
        )


# ------------------------------ /qualitymax ------------------------------
QUALITYMAX_TTL = 10 * 60
QUALITYMAX_PENDING: dict = {}
QUALITYMAX_HEIGHTS = (140, 240, 480, 720, 1080)
QUALITYMAX_ACTIVE: set = set()


def _qualitymax_source(message: Message):
    """Return replied Telegram video/document media, or None."""
    if not message or not message.reply_to_message:
        return None, None
    src = message.reply_to_message
    if src.video:
        return src, src.video
    if src.document and (src.document.mime_type or "").lower().startswith("video/"):
        return src, src.document
    return None, None


def _qualitymax_keyboard(uid: int, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("140p", callback_data=f"qmax:{uid}:{token}:140"),
            InlineKeyboardButton("240p", callback_data=f"qmax:{uid}:{token}:240"),
            InlineKeyboardButton("480p", callback_data=f"qmax:{uid}:{token}:480"),
        ],
        [
            InlineKeyboardButton("720p", callback_data=f"qmax:{uid}:{token}:720"),
            InlineKeyboardButton("1080p", callback_data=f"qmax:{uid}:{token}:1080"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"qmax:{uid}:{token}:cancel")],
    ])


@app.on_message(filters.command(["qualitymax", "Qualitymax"]) & AUTH)
async def qualitymax_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if not is_verified(uid):
        return await message.reply_text("❌ You must be **verified** to use /qualitymax. Run /verify.")

    # Remove expired selections.
    now = time.time()
    for token, item in list(QUALITYMAX_PENDING.items()):
        if now - item.get("created_at", 0) > QUALITYMAX_TTL:
            QUALITYMAX_PENDING.pop(token, None)

    src_msg, media = _qualitymax_source(message)
    if not media:
        return await message.reply_text(
            "❌ **Reply to a video** with `/qualitymax`.\n\n"
            "Choose 140p, 240p, 480p, 720p or 1080p from the menu."
        )

    file_size = int(getattr(media, "file_size", 0) or 0)
    if file_size and file_size > 2 * 1024 * 1024 * 1024:
        return await message.reply_text("❌ Source video is larger than 2 GB and cannot be downloaded by this bot.")

    token = pysecrets.token_hex(6)
    QUALITYMAX_PENDING[token] = {
        "user_id": uid,
        "chat_id": message.chat.id,
        "message_id": src_msg.id,
        "file_name": getattr(media, "file_name", None) or f"video_{src_msg.id}.mp4",
        "created_at": now,
    }
    await message.reply_text(
        "🎞️ **QualityMax**\n\n"
        f"📹 `{QUALITYMAX_PENDING[token]['file_name']}`\n\n"
        "Select the output quality:",
        reply_markup=_qualitymax_keyboard(uid, token),
    )


async def _qualitymax_convert_and_upload(client: Client, cq: CallbackQuery, pending: dict, height: int):
    uid = int(pending["user_id"])
    slot_id, slot_error = _six_acquire(uid, "qualitymax")
    if slot_error:
        await cq.answer("Server busy", show_alert=True)
        await cq.message.reply_text(slot_error)
        return

    work_dir = join(DOWNLOAD_DIRECTORY, f"qualitymax_{uid}_{pysecrets.token_hex(6)}")
    os.makedirs(work_dir, exist_ok=True)
    input_path = join(work_dir, "source")
    output_path = join(work_dir, f"Anime Cartoon {height}p.mp4")
    status = cq.message

    try:
        await cq.answer(f"{height}p conversion started")
        await status.edit_text("⬇️ **Downloading source video…**")
        source_message = await client.get_messages(
            pending["chat_id"], pending["message_id"]
        )
        if not source_message:
            raise RuntimeError("Source Telegram message could not be found.")
        downloaded = await client.download_media(
            source_message,
            file_name=input_path,
            progress=_aov_progress,
            progress_args=(status, time.time(), "Downloading source video…"),
        )
        if not downloaded or not os.path.exists(downloaded) or os.path.getsize(downloaded) <= 0:
            raise RuntimeError("Source video download failed.")

        info = await _ffprobe_video(downloaded)
        duration = float(info.get("duration") or 0)
        source_height = int(info.get("video_height") or 0)
        if duration <= 0 or source_height <= 0:
            raise RuntimeError("Could not read the source video's duration/resolution.")

        await status.edit_text(
            "⚙️ **FFmpeg conversion…**\n\n"
            f"Source: `{source_height}p`\n"
            f"Output: `{height}p`\n"
            f"Duration: `{TimeFormatter(int(duration * 1000))}`"
        )

        # Keep every audio track. Video is re-encoded to the requested height.
        vf = f"scale=-2:{height}:flags=lanczos"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", downloaded,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map_metadata", "0",
            "-movflags", "+faststart",
            output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise RuntimeError("FFmpeg conversion timed out.")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg failed: {err[-1800:]}")
        if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            raise RuntimeError("FFmpeg did not create a valid output file.")

        out_info = await _ffprobe_video(output_path)
        out_duration = int(round(float(out_info.get("duration") or duration)))
        await status.edit_text(
            "📤 **Uploading converted video…**\n\n"
            f"Quality: `{height}p`\n"
            f"Size: `{os.path.getsize(output_path) / (1024 * 1024):.1f} MB`"
        )
        await client.send_video(
            chat_id=pending["chat_id"],
            video=output_path,
            caption=(
                f"🎞️ **QualityMax**\n"
                f"📺 Quality: `{height}p`\n"
                f"📁 `{os.path.basename(output_path)}`"
            ),
            duration=out_duration,
            supports_streaming=True,
            progress=_aov_progress,
            progress_args=(status, time.time(), f"Uploading {height}p video…"),
        )
        await status.edit_text(
            "✅ **QualityMax completed**\n\n"
            f"🎞️ Output: `{height}p`\n"
            f"📦 Size: `{os.path.getsize(output_path) / (1024 * 1024):.1f} MB`"
        )
    except Exception as exc:
        LOG.exception("QualityMax failed for uid=%s", uid)
        try:
            await status.edit_text(f"❌ **QualityMax failed**\n\n`{str(exc)[:1800]}`")
        except Exception:
            pass
    finally:
        _six_release(slot_id)
        shutil.rmtree(work_dir, ignore_errors=True)


@app.on_callback_query(filters.regex(r"^qmax:(\d+):([^:]+):(140|240|480|720|1080|cancel)$"))
async def qualitymax_callback(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    uid = int(parts[1])
    token = parts[2]
    value = parts[3]

    if cq.from_user.id != uid:
        return await cq.answer("This selection is not yours.", show_alert=True)

    pending = QUALITYMAX_PENDING.get(token)
    if not pending or time.time() - pending.get("created_at", 0) > QUALITYMAX_TTL:
        QUALITYMAX_PENDING.pop(token, None)
        return await cq.answer("This QualityMax session has expired.", show_alert=True)

    if value == "cancel":
        QUALITYMAX_PENDING.pop(token, None)
        await cq.answer("Cancelled")
        try:
            await cq.message.edit_text("❌ **QualityMax cancelled.**", reply_markup=None)
        except Exception:
            pass
        return

    height = int(value)
    QUALITYMAX_PENDING.pop(token, None)
    asyncio.create_task(_qualitymax_convert_and_upload(client, cq, pending, height))


# =============================================================================
# End added main commands
# =============================================================================

# =============================================================================
# Entry point (merged from main.py)
# =============================================================================

from pyrogram.types import BotCommand

_BOT_COMMANDS = [
    BotCommand("start",          "Welcome message"),
    BotCommand("help",           "All commands and usage guide"),
    BotCommand("rec",            "Record HLS/M3U8/DASH stream (wizard)"),
    BotCommand("drec",           "Direct record — no wizard, instant start"),
    BotCommand("threerec",       "Record three playlist channels simultaneously"),
    BotCommand("reclink",        "Auto-extract stream from a web page"),
    BotCommand("download",       "Download from OTT platforms"),
    BotCommand("hotstar", "Record Hotstar from JioOTT.m3u"),
    BotCommand("compress",        "Compress a video (reply to video)"),
    BotCommand("compressadvance", "Compress: 576p · 350 MB · Multi audio (all users)"),
    BotCommand("qualitymax",       "Convert a replied video to 140p/240p/480p/720p/1080p"),
    BotCommand("public",            "Enable Public Mode (owner/admin)"),
    BotCommand("private",           "Enable Private Mode (owner/admin)"),
    BotCommand("screenshot",     "Extract screenshots from a video"),
    BotCommand("trim",           "Trim a video clip"),
    BotCommand("merge",          "Merge multiple videos"),
    BotCommand("watermark",      "Burn watermark into a video (reply to video)"),
    BotCommand("audiotrack",     "Lock audio track metadata without re-encoding"),
    BotCommand("gdrive",         "Connect or manage Google Drive"),
    BotCommand("drivelogout",    "Disconnect Google Drive account"),
    BotCommand("set_cookies",    "Upload cookies.txt for OTT login"),
    BotCommand("cookies_status", "Show stored cookies"),
    BotCommand("del_cookies",    "Delete stored cookies"),
    BotCommand("statusme",       "Show active recording or job status"),
    BotCommand("cancelme",       "Cancel active recording or job"),
    BotCommand("limit",          "Check your recording quota"),
    BotCommand("plan",           "Subscription plans"),
    BotCommand("contact",        "Support contact"),
    BotCommand("channel",        "Browse available channels"),
    BotCommand("search",         "Search channels"),
    BotCommand("verify",         "Verify your account"),
    BotCommand("admin_add",      "Add an admin by user ID (owner only)"),
    BotCommand("admin_delete",   "Remove an admin by user ID (owner only)"),
    BotCommand("anonadmin",      "Enable/disable Anonymous Admin mode"),
    BotCommand("stats",          "Bot statistics dashboard (owner/admin only)"),
    BotCommand("broadcast",      "Send message to all verified users (owner/admin)"),
    BotCommand("userinfo",       "Inspect a verified user's details (owner/admin)"),
    BotCommand("unverify",       "Remove a user's verification (owner/admin)"),
    BotCommand("cancelall",      "Cancel every active job across all users (owner/admin)"),
    BotCommand("listusers",      "Paginated list of all verified users (owner/admin)"),
    BotCommand("extendplan",     "Extend a user's plan by N days (owner/admin)"),
    BotCommand("premium_add",    "Grant premium plan to a user (owner/admin)"),
    BotCommand("premium_expire", "Revoke a user's premium plan (owner/admin)"),
    BotCommand("premium_list",   "List all premium users with status (owner/admin)"),
    BotCommand("autocompress",    "Toggle auto-compression for 800MB-1GB files (owner only)"),
    BotCommand("compresssettings","View/change auto-compress thresholds (owner only)"),
]


async def _register_commands():
    try:
        await app.set_bot_commands(_BOT_COMMANDS)
        LOG.info("Bot commands registered (%d).", len(_BOT_COMMANDS))
    except Exception as e:
        LOG.warning("Could not register bot commands: %s", e)


# Register commands on every successful connection (handles reconnects too)
@app.on_disconnect()
async def _on_reconnect(_client):
    pass  # placeholder — keeps decorator happy



# Use a raw update handler that fires once to register commands
import asyncio as _asyncio

_commands_registered = False


@app.on_raw_update()
async def _register_once(_client, _update, _users, _chats):
    global _commands_registered
    if not _commands_registered:
        _commands_registered = True
        _asyncio.create_task(_register_commands())
        global _hotstar_refresh_task
        if _hotstar_refresh_task is None or _hotstar_refresh_task.done():
            _hotstar_refresh_task = _asyncio.create_task(_hotstar_refresh_loop())
# ---------------------------------------------------------------------------
# /autocompress Yes|No  — owner-only toggle for auto-compression
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["autocompress", "AutoCompress"]) & _OWNER_FILTER)
async def autocompress_toggle_cmd(client: Client, message: Message):
    args = message.text.split(None, 1)
    current = _auto_compress_enabled()

    # No argument — show current status
    if len(args) < 2:
        status_icon = "✅ Enabled" if current else "❌ Disabled"
        return await message.reply_text(
            f"🗜 **Auto-Compression Status:** {status_icon}\n\n"
            f"• Triggers when recording is **800 MB – 1 GB**\n"
            f"• Compresses to **~355 MB** at **640p / 576p**\n"
            f"• All audio/language tracks are always preserved\n\n"
            f"**Usage:**\n"
            f"`/autocompress Yes` — enable\n"
            f"`/autocompress No`  — disable"
        )

    arg = args[1].strip().lower()
    if arg in ("yes", "on", "true", "1", "enable"):
        _set_auto_compress(True)
        await message.reply_text(
            "✅ **Auto-Compression Enabled**\n\n"
            "Recordings between **800 MB – 1 GB** will now be automatically\n"
            "compressed to **~355 MB** at **640p / 576p** before upload.\n"
            "_All audio/language tracks are preserved._"
        )
    elif arg in ("no", "off", "false", "0", "disable"):
        _set_auto_compress(False)
        await message.reply_text(
            "❌ **Auto-Compression Disabled**\n\n"
            "Large recordings will be uploaded as-is.\n"
            "Use `/autocompress Yes` to re-enable."
        )
    else:
        await message.reply_text(
            "⚠️ Unknown option.\n\n"
            "**Usage:**\n"
            "`/autocompress Yes` — enable\n"
            "`/autocompress No`  — disable\n"
            "`/autocompress`     — show current status"
        )


# ---------------------------------------------------------------------------
# /compresssettings  — owner-only: view or change auto-compress thresholds
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["compresssettings", "CompressSettings"]) & _OWNER_FILTER)
async def compress_settings_cmd(client: Client, message: Message):
    """
    Usage:
      /compresssettings                    — show current values
      /compresssettings min <MB>           — set lower trigger (default 800)
      /compresssettings max <MB>           — set upper trigger (default 1024)
      /compresssettings target <MB>        — set output target (default 355)
      /compresssettings reset              — restore all defaults
    """
    args = message.text.split()
    cs   = _get_compress_settings()

    def _fmt() -> str:
        enabled = "✅ Enabled" if _auto_compress_enabled() else "❌ Disabled"
        return (
            f"🗜 **Auto-Compress Settings**\n\n"
            f"Status : {enabled}\n"
            f"Trigger: `{cs['min_mb']} MB` – `{cs['max_mb']} MB`\n"
            f"Target : `{cs['target_mb']} MB` (≤360 MB recommended)\n"
            f"Resolution: 640p → 576p fallback\n\n"
            f"**Commands:**\n"
            f"`/compresssettings min <MB>`    — lower trigger\n"
            f"`/compresssettings max <MB>`    — upper trigger\n"
            f"`/compresssettings target <MB>` — output size\n"
            f"`/compresssettings reset`       — restore defaults\n"
            f"`/autocompress Yes/No`          — enable/disable"
        )

    # No subcommand — just show status
    if len(args) < 2:
        return await message.reply_text(_fmt())

    sub = args[1].lower()

    if sub == "reset":
        _update_compress_settings(min_mb=800, max_mb=1024, target_mb=355)
        cs = _get_compress_settings()
        return await message.reply_text(f"✅ **Defaults restored.**\n\n{_fmt()}")

    if sub in ("min", "max", "target") and len(args) >= 3:
        try:
            val = int(args[2])
        except ValueError:
            return await message.reply_text("❌ Value must be a whole number in MB.")

        if sub == "min":
            if val < 100 or val >= cs["max_mb"]:
                return await message.reply_text(
                    f"❌ `min` must be ≥ 100 MB and less than current `max` ({cs['max_mb']} MB)."
                )
            _update_compress_settings(min_mb=val)
            cs["min_mb"] = val
        elif sub == "max":
            if val <= cs["min_mb"] or val > 4096:
                return await message.reply_text(
                    f"❌ `max` must be > current `min` ({cs['min_mb']} MB) and ≤ 4096 MB."
                )
            _update_compress_settings(max_mb=val)
            cs["max_mb"] = val
        elif sub == "target":
            if val < 50 or val > 2000:
                return await message.reply_text("❌ `target` must be between 50 MB and 2000 MB.")
            _update_compress_settings(target_mb=val)
            cs["target_mb"] = val

        return await message.reply_text(f"✅ **Updated.**\n\n{_fmt()}")

    # Unrecognised subcommand
    await message.reply_text(_fmt())



# ---------------------------------------------------------------------------
# JioTV v3.1 + Login/OTP
# ---------------------------------------------------------------------------
JIOTV_CHANNEL_LIST_URL = (
    "https://jiotv.data.cdn.jio.com/apis/v3.1/"
    "getMobileChannelList/get/?os=android&devicetype=phone"
    "&usertype=tvYR7NSNn7rymo3F"
)
JIOTV_OTP_SEND_URL = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/send"
JIOTV_OTP_VERIFY_URL = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/verify"
JTV_LOGIN_PENDING = {}
JTV_CHANNEL_CACHE = []
JTV_CHANNEL_CACHE_TS = 0.0
JTV_CHANNEL_CACHE_TTL = 300
JTV_CREDS_FILE = os.path.join(DATA_DIRECTORY, "jiotv_credentials.json")


def _jtv_save_creds(data, mobile):
    payload = dict(data or {})
    payload["mobile"] = mobile
    tmp = JTV_CREDS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, JTV_CREDS_FILE)


def _jtv_has_login():
    try:
        with open(JTV_CREDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("ssoToken") or data.get("authToken"))
    except Exception:
        return False


def _jtv_send_otp(mobile):
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9",
    }
    payload = {"number": base64.b64encode(f"+91{mobile}".encode()).decode()}
    try:
        r = requests.post(JIOTV_OTP_SEND_URL, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        LOG.warning("JioTV OTP send request failed: %s", exc)
        return False, "JioTV login service did not respond. Please try again later."
    if r.status_code in (200, 201, 202, 204):
        return True, "OTP request accepted"
    try:
        d = r.json()
        detail = d.get("message") or d.get("error") or f"HTTP {r.status_code}"
        return False, str(detail)
    except Exception:
        return False, f"HTTP {r.status_code}"


def _jtv_verify_otp(mobile, otp):
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9",
    }
    payload = {
        "number": base64.b64encode(f"+91{mobile}".encode()).decode(),
        "otp": otp,
        "deviceInfo": {
            "consumptionDeviceName": "RMX1945",
            "info": {
                "type": "android",
                "platform": {"name": "RMX1945"},
                "androidId": "tsjiotvbot123456",
            },
        },
    }
    r = requests.post(JIOTV_OTP_VERIFY_URL, json=payload, headers=headers, timeout=15)
    try:
        d = r.json()
    except Exception:
        return False, f"HTTP {r.status_code}"
    if d.get("ssoToken"):
        _jtv_save_creds(d, mobile)
        return True, "Login successful"
    msg = d.get("message", "")
    if not msg and isinstance(d.get("errors"), list) and d["errors"]:
        msg = d["errors"][-1].get("message", "")
    return False, msg or f"Verify failed: HTTP {r.status_code}"


def _jtv_get_channels(force=False):
    global JTV_CHANNEL_CACHE, JTV_CHANNEL_CACHE_TS
    now = time.time()
    if not force and JTV_CHANNEL_CACHE and now - JTV_CHANNEL_CACHE_TS < JTV_CHANNEL_CACHE_TTL:
        return JTV_CHANNEL_CACHE
    last = ""
    for attempt, delay in enumerate((0, 2, 5), 1):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(
                JIOTV_CHANNEL_LIST_URL,
                headers={
                    "User-Agent": "JioTV.Plus/2.3.1_2041 (Linux;Android 14) AndroidXMedia3/1.4.0",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            last = f"HTTP {r.status_code}"
            if r.status_code == 503:
                continue
            r.raise_for_status()
            d = r.json()
            result = d.get("result", []) if isinstance(d, dict) else []
            if isinstance(result, dict):
                result = result.get("channels") or result.get("channelList") or result.get("data") or []
            channels = []
            for item in result if isinstance(result, list) else []:
                if not isinstance(item, dict):
                    continue
                cid = item.get("channel_id") or item.get("channelId") or item.get("id")
                name = item.get("channel_name") or item.get("channelName") or item.get("name")
                if cid and name:
                    channels.append({
                        "channel_id": str(cid),
                        "channel_name": str(name),
                        "category": str(item.get("channelCategoryId") or item.get("category") or item.get("categoryName") or "Other"),
                        "catchup": str(item.get("isCatchupAvailable", "False")),
                    })
            if not channels:
                raise RuntimeError("JioTV returned an empty channel list")
            JTV_CHANNEL_CACHE, JTV_CHANNEL_CACHE_TS = channels, time.time()
            return channels
        except Exception as e:
            last = str(e)
            if attempt == 3:
                raise RuntimeError(f"JioTV channel API unavailable ({last})")
    raise RuntimeError(f"JioTV channel API unavailable ({last})")


@app.on_message(filters.command("login") & AUTH)
async def jtv_login_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text("📱 Usage: /login Jio_number")
    mobile = re.sub(r"\D", "", args[0])
    if mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    if len(mobile) != 10:
        return await message.reply_text("❌ Enter a valid 10-digit Jio mobile number.")
    status = await message.reply_text("⏳ Contacting JioTV to request OTP…")
    try:
        ok, result = await asyncio.to_thread(_jtv_send_otp, mobile)
    except Exception:
        LOG.exception("Unexpected error in /login handler")
        return await status.edit_text("❌ /login failed unexpectedly. Check the Termux log and try again.")
    if not ok:
        return await status.edit_text(f"❌ OTP request failed: {result}")
    JTV_LOGIN_PENDING[message.from_user.id] = mobile
    await status.edit_text("📲 OTP request accepted.\n\nNow use: /otp YOUR_OTP")


@app.on_message(filters.command("otp") & AUTH)
async def jtv_otp_cmd(client: Client, message: Message):
    args = message.command[1:]
    mobile = JTV_LOGIN_PENDING.get(message.from_user.id)
    if not mobile:
        return await message.reply_text("❌ First use /login Jio_number")
    if not args or not re.fullmatch(r"\d{4,8}", args[0]):
        return await message.reply_text("❌ Usage: /otp OTP")
    status = await message.reply_text("⏳ Verifying JioTV OTP…")
    try:
        ok, result = await asyncio.to_thread(_jtv_verify_otp, mobile, args[0])
    except Exception:
        LOG.exception("Unexpected error in /otp handler")
        return await status.edit_text("❌ /otp failed unexpectedly. Check the Termux log and try again.")
    if not ok:
        return await status.edit_text(f"❌ OTP verification failed: {result}")
    JTV_LOGIN_PENDING.pop(message.from_user.id, None)
    global JTV_CHANNEL_CACHE
    JTV_CHANNEL_CACHE = []
    try:
        os.chmod(JTV_CREDS_FILE, 0o600)
    except OSError:
        pass
    await status.edit_text("✅ JioTV Login Successful.\n\nNow use /jiotvchannels")


@app.on_message(filters.command("jiotvchannels") & AUTH)
async def jtv_channels_cmd(client: Client, message: Message):
    if not _jtv_has_login():
        return await message.reply_text("❌ JioTV login required.\n\nUse /login Jio_number, then /otp OTP")
    status = await message.reply_text("🔄 Loading JioTV channels (v3.1)…")
    try:
        channels = await asyncio.to_thread(_jtv_get_channels, True)
    except Exception as e:
        return await status.edit_text(f"❌ {e}")
    cats = sorted({c["category"] for c in channels})
    rows = [[InlineKeyboardButton(cat[:60], callback_data=f"jtvcat:{i}")] for i, cat in enumerate(cats)]
    await status.edit_text(f"📺 JioTV Channels ({len(channels)} total)\n\nChoose a category:", reply_markup=InlineKeyboardMarkup(rows))


@app.on_callback_query(filters.regex(r"^jtvcat:\d+$"))
async def jtv_category_callback(client: Client, query: CallbackQuery):
    try:
        channels = await asyncio.to_thread(_jtv_get_channels, False)
        cats = sorted({c["category"] for c in channels})
        idx = int(query.data.split(":", 1)[1])
        cat = cats[idx]
        selected = [c for c in channels if c["category"] == cat]
        text = f"📺 {cat} ({len(selected)} channels)\n\n" + "\n".join(
            f"• {c['channel_name']}" + (" 📼" if c["catchup"].lower() == "true" else "") for c in selected
        )
        if len(text) > 3900:
            text = text[:3900] + "\n…"
        await query.answer()
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="jtvcatback")]]))
    except Exception as e:
        await query.answer(str(e)[:180], show_alert=True)


@app.on_callback_query(filters.regex(r"^jtvcatback$"))
async def jtv_category_back(client: Client, query: CallbackQuery):
    try:
        channels = await asyncio.to_thread(_jtv_get_channels, False)
        cats = sorted({c["category"] for c in channels})
        rows = [[InlineKeyboardButton(cat[:60], callback_data=f"jtvcat:{i}")] for i, cat in enumerate(cats)]
        await query.answer()
        await query.message.edit_text(f"📺 JioTV Channels ({len(channels)} total)\n\nChoose a category:", reply_markup=InlineKeyboardMarkup(rows))
    except Exception as e:
        await query.answer(str(e)[:180], show_alert=True)

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /VideoauraudioMerger — replace audio or append audio while retaining subtitles
# ---------------------------------------------------------------------------
_VIDEO_AUDIO_MERGE = {}
_VIDEO_AUDIO_PENDING = {}

def _vam_safe_name(value):
    name = os.path.basename((value or "").strip())
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    return name or "Merged_Video"

def _vam_keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Merger 1 • Replace existing audio", callback_data=f"vam:{uid}:mode:replace")],
        [InlineKeyboardButton("Merger 2 • Keep audio + subtitles", callback_data=f"vam:{uid}:mode:keep")],
        [InlineKeyboardButton("Cancel", callback_data=f"vam:{uid}:cancel")],
    ])

async def _vam_process(client, uid, job, status):
    try:
        if not job["audios"]:
            raise RuntimeError("No audio tracks were added.")
        output = os.path.join(job["dir"], job["output_name"])
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", job["source"]]
        for item in job["audios"]:
            cmd += ["-i", item["path"]]
        cmd += ["-map", "0:v?"]
        if job["mode"] == "keep":
            cmd += ["-map", "0:a?", "-map", "0:s?"]
        for n in range(len(job["audios"])):
            cmd += ["-map", f"{n+1}:a:0"]
        # MP4 output keeps the result playable in Telegram: video is stream-copied,
        # audio is normalized to AAC, and text subtitles are converted to mov_text.
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-c:s", "mov_text", "-movflags", "+faststart"]
        offset = job["original_audio_count"] if job["mode"] == "keep" else 0
        for n, item in enumerate(job["audios"]):
            idx = offset + n
            cmd += [f"-metadata:s:a:{idx}", f"language={item['lang']}"]
            if item["title"]:
                cmd += [f"-metadata:s:a:{idx}", f"title={item['title']}"]
        cmd += ["-max_interleave_delta", "0", output]
        await status.edit_text("🎞 **Video-Audio Merging Now…**")
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode or not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise RuntimeError((err or b"FFmpeg merge failed").decode("utf-8", "replace")[-1600:])
        probe = await _ffprobe_video(output)
        duration = int(float(probe.get("duration") or job["duration"] or 0))
        await status.edit_text("📤 **Uploading merged video to Telegram…**")
        await client.send_video(chat_id=job["chat_id"], video=output, duration=duration or None,
            supports_streaming=True, caption=f"🎬 `{job['output_name']}`",
            progress=_aov_progress, progress_args=(status, time.time(), "Uploading merged video…"))
        await status.edit_text("✅ **Merger completed.**\n\nSource and processed files will be deleted after 2 hours.")
        schedule_two_hour_cleanup(job["dir"])
    except Exception as exc:
        LOG.exception("Video-Audio Merger failed")
        _safe_rmtree(job["dir"])
        try:
            await status.edit_text(f"❌ **Merger failed:** `{str(exc)[:1500]}`")
        except Exception:
            pass
    finally:
        _VIDEO_AUDIO_MERGE.pop(uid, None)
        _VIDEO_AUDIO_PENDING.pop(uid, None)

async def _vam_prepare(client, uid, chat_id, source_message, output_name, status, server_source=None):
    job_dir = os.path.join(DOWNLOAD_DIRECTORY, f"vam_{uid}_{pysecrets.token_hex(5)}")
    os.makedirs(job_dir, exist_ok=True)
    try:
        source_target = os.path.join(job_dir, "source" + (os.path.splitext(server_source or "source.mkv")[1] or ".mkv"))
        if server_source:
            shutil.copy2(server_source, source_target)
            source = source_target
        else:
            source = await client.download_media(
                source_message, file_name=source_target,
                progress=_aov_progress,
                progress_args=(status, time.time(), "Downloading source video…"))
        if not source or not os.path.isfile(source) or os.path.getsize(source) == 0:
            raise RuntimeError("Source video download failed or server file is empty.")
        info = await _ffprobe_video(source)
        _VIDEO_AUDIO_MERGE[uid] = {
            "uid": uid, "chat_id": chat_id, "dir": job_dir, "source": source,
            "output_name": output_name, "duration": float(info.get("duration") or 0),
            "original_audio_count": len(info.get("audio_streams") or []),
            "mode": None, "audios": [],
        }
        await status.edit_text(
            "🎬 **Choose your merger mode**\\n\\n"
            "Merger 1 replaces all existing audio tracks.\\n"
            "Merger 2 keeps existing audio tracks and subtitles.",
            reply_markup=_vam_keyboard(uid))
    except Exception as exc:
        _safe_rmtree(job_dir)
        await status.edit_text(f"❌ Could not prepare video: `{str(exc)[:1200]}`")

def _vam_find_server_file(filename):
    safe = _vam_safe_name(filename)
    for candidate in (os.path.join(DOWNLOAD_DIRECTORY, safe), os.path.join(_SCRIPT_DIR, safe)):
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return os.path.abspath(candidate)
    return None

@app.on_message(filters.command(["VideoauraudioMerger", "videoaudiomerger"]))
async def video_audio_merger_cmd(client: Client, message: Message):
    uid = _message_actor_id(message)
    if uid in _VIDEO_AUDIO_MERGE or uid in _VIDEO_AUDIO_PENDING:
        return await message.reply_text("⚠️ Your merger session is already active.")
    src_msg, media = _get_replied_video(message)
    try:
        args = shlex.split(message.text or "")[1:]
    except Exception:
        args = (message.text or "").split()[1:]

    if not media:
        if len(args) < 2:
            return await message.reply_text(
                "📹 **Telegram video:** reply to a video with `/VideoauraudioMerger Filename` "
                "(or send the command alone and then send the output filename).\\n\\n"
                "🗂 **Server file:** `/VideoauraudioMerger ServerFileName Output_Name`\\n"
                f"Server source files are searched in `{DOWNLOAD_DIRECTORY}` and the bot folder."
            )
        server_source = _vam_find_server_file(args[0])
        if not server_source:
            return await message.reply_text(
                f"❌ Server file not found: `{_vam_safe_name(args[0])}`\\n"
                f"Place it in `{DOWNLOAD_DIRECTORY}` and try again."
            )
        name = _vam_safe_name(args[1])
        if not os.path.splitext(name)[1]:
            name += ".mp4"
        status = await message.reply_text("📂 **Preparing server video…**")
        return await _vam_prepare(client, uid, message.chat.id, None, name, status, server_source=server_source)

    if args:
        name = _vam_safe_name(args[0])
        if not os.path.splitext(name)[1]:
            name += ".mp4"
        status = await message.reply_text("⬇️ **Preparing source video…**")
        return await _vam_prepare(client, uid, message.chat.id, src_msg, name, status)

    _VIDEO_AUDIO_PENDING[uid] = {
        "step": "output", "source_message": src_msg, "chat_id": message.chat.id}
    await message.reply_text("✏️ **Now Send Name of Output Video**")

_VAM_OUTPUT_FILTER = filters.create(lambda _f, _c, m: _message_actor_id(m) in _VIDEO_AUDIO_PENDING and _VIDEO_AUDIO_PENDING[_message_actor_id(m)].get("step") == "output")
@app.on_message(filters.text & _VAM_OUTPUT_FILTER)
async def _vam_output_name(client: Client, message: Message):
    uid = _message_actor_id(message)
    pending = _VIDEO_AUDIO_PENDING.get(uid)
    if not pending:
        return
    name = _vam_safe_name(message.text)
    if not os.path.splitext(name)[1]:
        name += ".mp4"
    _VIDEO_AUDIO_PENDING.pop(uid, None)
    status = await message.reply_text("⬇️ **Preparing source video…**")
    await _vam_prepare(client, uid, pending["chat_id"], pending["source_message"], name, status)

@app.on_callback_query(filters.regex(r"^vam:(\d+):(mode|more|finish|cancel)(?::([^:]+))?$"))
async def _vam_callback(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":")
    uid, action = int(parts[1]), parts[2]
    value = parts[3] if len(parts) > 3 else ""
    if not _callback_actor_matches(cq, uid):
        return await cq.answer("This selection is not yours.", show_alert=True)
    job = _VIDEO_AUDIO_MERGE.get(uid)
    if action == "cancel":
        _VIDEO_AUDIO_PENDING.pop(uid, None)
        if job:
            _VIDEO_AUDIO_MERGE.pop(uid, None)
            _safe_rmtree(job["dir"])
        await cq.answer("Cancelled.")
        return await cq.message.edit_text("❌ Merger cancelled.", reply_markup=None)
    if not job:
        return await cq.answer("Session expired.", show_alert=True)
    if action == "mode":
        job["mode"] = value
        await cq.answer("Mode selected")
        return await cq.message.edit_text(
            "🎵 **Now Send Audio File To Merge**\n\nSupported: Opus, Vorbis, MP2, MP3, LC-AAC, HE-AAC, WMA, AC3, E-AC3\n\nSend language code such as `eng`, `hin`, `tam`, `tel`.")
    if action == "more":
        await cq.answer("Send another audio file.")
        return await cq.message.edit_text("➕ **Send another audio file**")
    if action == "finish":
        await cq.answer("Merging…")
        return await _vam_process(client, uid, job, cq.message)

_VAM_AUDIO_FILTER = filters.create(lambda _f, _c, m: _message_actor_id(m) in _VIDEO_AUDIO_MERGE and bool(_VIDEO_AUDIO_MERGE[_message_actor_id(m)].get("mode")) and bool(m.audio or m.document))
@app.on_message(_VAM_AUDIO_FILTER)
async def _vam_audio_file(client: Client, message: Message):
    uid = _message_actor_id(message)
    job = _VIDEO_AUDIO_MERGE.get(uid)
    media = message.audio or message.document
    filename = getattr(media, "file_name", "") or "audio"
    ext = os.path.splitext(filename)[1].lower()
    allowed = {".opus", ".ogg", ".oga", ".mp2", ".mp3", ".aac", ".m4a", ".wma", ".ac3", ".eac3"}
    if ext not in allowed and not (getattr(media, "mime_type", "") or "").startswith("audio/"):
        return await message.reply_text("❌ Send a supported audio file.")
    path = os.path.join(job["dir"], f"audio_{len(job['audios'])+1}{ext or '.audio'}")
    status = await message.reply_text("⬇️ **Downloading audio…**")
    try:
        downloaded = await client.download_media(message, file_name=path,
            progress=_aov_progress, progress_args=(status, time.time(), "Downloading audio…"))
        if not downloaded or not os.path.isfile(downloaded) or os.path.getsize(downloaded) == 0:
            raise RuntimeError("Audio download failed.")
        _VIDEO_AUDIO_PENDING[uid] = {"step": "audio_meta", "path": downloaded, "status": status}
        await status.edit_text("🏷 **Send audio title and language**\n\nExamples: `eng`, `hin`, `tam` or `MyTitle|eng`")
    except Exception as exc:
        await status.edit_text(f"❌ Audio download failed: `{str(exc)[:1000]}`")

_VAM_META_FILTER = filters.create(lambda _f, _c, m: _message_actor_id(m) in _VIDEO_AUDIO_PENDING and _VIDEO_AUDIO_PENDING[_message_actor_id(m)].get("step") == "audio_meta")
@app.on_message(filters.text & _VAM_META_FILTER)
async def _vam_audio_metadata(client: Client, message: Message):
    uid = _message_actor_id(message)
    pending = _VIDEO_AUDIO_PENDING.get(uid)
    job = _VIDEO_AUDIO_MERGE.get(uid)
    if not pending or not job:
        return
    raw = (message.text or "").strip()
    title, lang = ([x.strip() for x in raw.split("|", 1)] if "|" in raw else ("", raw))
    if not re.fullmatch(r"[a-zA-Z]{2,3}", lang):
        return await message.reply_text("❌ Use a language code such as `eng`, `hin`, `tam`, `tel`.")
    job["audios"].append({"path": pending["path"], "title": title, "lang": lang.lower()})
    _VIDEO_AUDIO_PENDING.pop(uid, None)
    await pending["status"].edit_text(f"✅ **Audio added:** `{title or lang}`\n\nAdd more audio?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Add Audio", callback_data=f"vam:{uid}:more"),
            InlineKeyboardButton("✅ Finish & Merge", callback_data=f"vam:{uid}:finish"),
        ]]))


if __name__ == "__main__":
    print("Starting Video Recorder Bot...")
    missing_config = []
    API_ID = int(API_ID) if str(API_ID).strip().isdigit() else 0

    if API_ID <= 0:
        missing_config.append("API_ID")
    if not API_HASH:
        missing_config.append("API_HASH")
    if not BOT_TOKEN:
        missing_config.append("BOT_TOKEN")
    if missing_config:
        raise SystemExit(
            "Missing required environment configuration: "
            + ", ".join(missing_config)
        )
    LOG.info("OWNER_IDS loaded: %d ID(s) — %s", len(OWNER_IDS), OWNER_IDS if OWNER_IDS else "NONE (no owner set!)")
    LOG.info("AUTH_USERS loaded: %d ID(s)", len(AUTH_USERS))
    sweep_old_downloads()
    LOG.info(
        "Recordings will be auto-deleted from the server after %s.",
        _retention_label(),
    )
    try:
        app.run()
    except KeyboardInterrupt:
        LOG.info("KeyboardInterrupt received. Shutting down cleanly…")
    finally:
        # processes so Ctrl+C does not leave the bot or recorder children alive.
        for _task_name in ("_hotstar_refresh_task",):
            try:
                _task = globals().get(_task_name)
                if _task and not _task.done():
                    _task.cancel()
            except Exception:
                pass
        try:
            _pids = set()
            for _entry in active_recs.values():
                for _rec in (_entry or {}).values():
                    _pid = (_rec or {}).get("ffmpeg_pid")
                    if _pid:
                        _pids.add(int(_pid))
            _pids.update(int(_pid) for _pid in user_ffmpeg_pids.values() if _pid)
            for _pid in _pids:
                try:
                    import signal as _signal
                    os.kill(_pid, _signal.SIGTERM)
                except Exception:
                    pass
        except Exception as _shutdown_exc:
            LOG.warning("Shutdown cleanup failed: %s", _shutdown_exc)
