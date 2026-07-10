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
from typing import Tuple, Optional
from os.path import join
from datetime import datetime, timedelta
import psutil
import pytz
import yt_dlp
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
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

API_ID        = _parse_int("API_ID",    _environ.get("API_ID", "0"))
API_HASH      = _environ.get("API_HASH",    "")
BOT_TOKEN     = _environ.get("BOT_TOKEN",   "")

AUTH_USERS    = _parse_id_list("AUTH_USERS", _environ.get("AUTH_USERS", ""))
OWNER_IDS     = _parse_id_list("OWNER_IDS",  _environ.get("OWNER_IDS",  ""))

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

DEFAULT_METADATA    = _environ.get("DEFAULT_METADATA",    "")
DEFAULT_FILENAME    = _environ.get("DEFAULT_FILENAME",    "Anime Cartoon")
DEFAULT_REC_DURATION = _environ.get("DEFAULT_REC_DURATION", "01:00:00")
BRAND_TITLE         = _environ.get("BRAND_TITLE",         "Anime Cartoon")

TIMEZONE            = _environ.get("TIMEZONE",            "Asia/Kolkata")

SUPPORT_USERNAME    = _environ.get("SUPPORT_USERNAME",    "LS_Owner_bot")
SUPPORT_CHANNEL     = _environ.get("SUPPORT_CHANNEL",     "LS_Owner_bot")

GROUP_CHAT_ID       = _parse_int("GROUP_CHAT_ID",  _environ.get("GROUP_CHAT_ID",  "0"))
GROUP_INVITE_LINK   = _environ.get("GROUP_INVITE_LINK", "https://t.me/+ww77CDQwoigzYjk1")

SHRINKME_API_KEY    = _environ.get("SHORTXLINKS_API_KEY", _environ.get("SHRINKME_API_KEY", ""))
BOT_USERNAME        = _environ.get("BOT_USERNAME",        "LittlesinghamMovie_Bot")

GDRIVE_SA_JSON      = _environ.get("GDRIVE_SA_JSON",      "")
GDRIVE_FOLDER_ID    = _environ.get("GDRIVE_FOLDER_ID",    "")
GOOGLE_CLIENT_ID    = _environ.get("GOOGLE_CLIENT_ID",    "")
GOOGLE_CLIENT_SECRET = _environ.get("GOOGLE_CLIENT_SECRET", "")

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
BOT_CHANNELS = {
    "POGO": "https://ksr.indevs.in/m3u/?stream=475137",
    "CARTOON NETWORK": "https://ksr.indevs.in/m3u/?stream=475130",
    "DISCOVERY KIDS": "https://ksr.indevs.in/m3u/?stream=475132",
    "DISNEY TV": "https://ksr.indevs.in/m3u/?stream=475134",
    "NICK": "https://ksr.indevs.in/m3u/?stream=475136",
    "HUNGAMA": "https://ksr.indevs.in/m3u/?stream=475135",
    "SONIC": "https://ksr.indevs.in/m3u/?stream=475138",
    "NICK JR": "https://ksr.indevs.in/m3u/?stream=476786",
    "MINIX": "https://vodzong.mjunoon.tv:8087/streamtest/157-1M/chunks.m3u8",
    "POGO 2": "http://line.sweetv.xyz/play/live.php?mac=00:1A:79:00:03:B2&stream=156128&extension=ts&play_token=9sBGXoBcMI",
    "DISCOVERY KIDS 2": "http://line.sweetv.xyz/play/live.php?mac=00:1A:79:00:03:B2&stream=1540017&extension=ts&play_token=eFCOqzrsPI",
    "DISNEY CHANNEL": "http://103.155.18.191:8000/play/a01q/index.m3u8",
    "NICK 2": "http://103.155.18.191:8000/play/a04c/index.m3u8",
    "CARTOON NETWORK 2": "http://202.70.146.135:8000/play/a0a8/index.m3u8",
    "CARTOON NETWORK HD+": "http://202.70.146.135:8000/play/a0a3/index.m3u8",
    "DISCOVERY KIDS 3": "http://202.70.146.135:8000/play/a0a6/index.m3u8",
}
ADMIN_FILE       = join(DATA_DIRECTORY, "admins.json")
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
    if user_id in OWNER_IDS:
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
    max_concurrent_transmissions=8,   # parallel upload chunks
)

# ---------------------------------------------------------------------------
# Shared runtime state
# ---------------------------------------------------------------------------

user_status:        dict = {}
user_tasks:         dict = {}
rec_setup_sessions: dict = {}   # user_id -> setup dict
_wm_text_pending:   set  = set()
user_ffmpeg_pids:   dict = {}
progress_tasks:     dict = {}
cancelled_users:    set  = set()

MAX_CONCURRENT_REC  = 5
active_recs:        dict = {}   # {user_id: {rec_id: {"status", "ffmpeg_pid", "progress_task", "start"}}}
cancelled_recs:     set  = set()  # set of (user_id, rec_id)
pending_uploads:    dict = {}   # {(user_id, rec_id): upload state dict}
pending_cookies_users: dict = {}
ott_progress:       dict = {}
ott_sessions:       dict = {}   # {user_id: {url, filename, fmt, audio_lang, msg}}
user_dl_prefs:      dict = {}   # {user_id: {"default_audio_lang": "tamil"/"hindi"/"eng"/…}}
pending_upload_state: dict = {}  # {user_id: upload-choice state for /trim /merge /watermark /download}
_wm_pos_sessions:   dict = {}   # {user_id: {src_msg, wm_text, pos, status_msg}}
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

def _auth_filter():
    if AUTH_USERS:
        return filters.user(AUTH_USERS) | filters.user(OWNER_IDS or [])
    return filters.all


AUTH = _auth_filter()


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


# ---------------------------------------------------------------------------
# Admin system
# ---------------------------------------------------------------------------

def load_admins() -> list:
    return _load_json(ADMIN_FILE, [])


def save_admins(data: list) -> None:
    _save_json(ADMIN_FILE, data)


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


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


async def take_stream_snapshot(url: str, out_path: str, is_hls: bool = True) -> bool:
    """Capture a single frame from a live/HLS stream for a live preview thumbnail."""
    try:
        hls_part = "-f hls -allowed_extensions ALL " if is_hls else ""
        rc, _, _ = await asyncio.wait_for(
            runcmd(
                f'ffmpeg -y -user_agent "{DEFAULT_USER_AGENT}" '
                f'{hls_part}'
                f'-probesize 5000000 -analyzeduration 5000000 '
                f'-i {shlex.quote(url)} '
                f'-vframes 1 -q:v 2 {shlex.quote(out_path)}'
            ),
            timeout=25,
        )
        return rc == 0 and os.path.exists(out_path)
    except Exception:
        return False


def _rec_progress_kb(user_id: int, rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Gen Preview",      callback_data=f"rec_prev:{user_id}:{rec_id}")],
        [InlineKeyboardButton("🔄 Refresh Progress", callback_data=f"rec_ref:{user_id}:{rec_id}"),
         InlineKeyboardButton("❌ Cancel",           callback_data=f"rec_cxl:{user_id}:{rec_id}")],
    ])


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
    return {"duration": duration, "video_height": video_height, "audio_streams": audio_streams}

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
    """Flat list of hidden-URL channel buttons — names only, never the URL."""
    rows, row = [], []
    for name in BOT_CHANNELS:
        row.append(InlineKeyboardButton(f"📺 {name}", callback_data=f"ch:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if not rows:
        rows = [[InlineKeyboardButton("No channels configured", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)

# ---------------------------------------------------------------------------
# Pre-recording Setup Wizard helpers
# ---------------------------------------------------------------------------

_QUALITY_BITRATE_KBPS = {"480": 600, "576": 820, "640": 1000, "720": 1500, "1080": 2500}


def _est_size_mb(duration_sec: int, quality: str) -> str:
    br = _QUALITY_BITRATE_KBPS.get(quality, 0)
    if not br or duration_sec <= 0:
        return "?"
    return f"~{duration_sec * br / 8 / 1024:.0f} MB"


def _setup_summary(s: dict) -> str:
    q      = s["quality"]
    q_str  = f"{q}p" if q != "original" else "Original"
    q_icon = "🔵" if q == "1080" else ("🔒" if q == "original" else "📺")
    asp    = s["aspect"]
    asp_label = {
        "none": "None (Keep as-is)", "21:9": "21:9 Aspect", "16:9": "16:9 Aspect",
        "4:5": "4:5 Aspect", "bars": "16:9 Black Bars", "zoom": "16:9 Zoom",
        "1280x720": "scale=1280:720",
    }.get(asp, asp)
    wm     = s["watermark_pos"].replace("_", " ").title() if s["watermark_on"] else "OFF"
    at     = s["audio_track"]
    tracks = s.get("detected_audio_tracks", [])
    if not at:
        audio_s = "All Tracks"
    elif len(at) == 1:
        idx = at[0]
        audio_s = _audio_track_label(tracks[idx - 1]) if tracks and idx <= len(tracks) else f"Track {idx}"
    else:
        parts = []
        for idx in sorted(at):
            lang = tracks[idx - 1].get("lang", "?").upper() if tracks and idx <= len(tracks) else f"T{idx}"
            parts.append(lang)
        audio_s = ", ".join(parts)
    auto_s = "✅ On" if s["auto_mode"] else "❌ Off"
    return (
        f"📋 **Recording Setup**\n\n"
        f"⏱ Duration: `{s['timestamp']}`\n"
        f"🔄 Auto Mode: {auto_s}\n"
        f"📁 Filename: `{s['filename']}`\n"
        f"🎙 Audio: `{audio_s}`\n"
        f"💧 Watermark: `{wm}`\n"
        f"{q_icon} Size: `{q_str}`\n"
        f"📐 Aspect: `🔒 {asp_label}`\n\n"
        f"👇 Choose an option:"
    )


def _kb_step1(s: dict) -> InlineKeyboardMarkup:
    uid       = s["user_id"]
    wm_on     = s["watermark_on"]
    wm_toggle_label = "❌ Disable Watermark" if wm_on else "✅ Enable Watermark"
    auto_icon = "✅" if s["auto_mode"] else "⏩"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Top Left",      callback_data=f"rs:{uid}:wm_pos:top_left"),
         InlineKeyboardButton("⬆️ Top Center",    callback_data=f"rs:{uid}:wm_pos:top_center")],
        [InlineKeyboardButton("↗️ Top Right",     callback_data=f"rs:{uid}:wm_pos:top_right"),
         InlineKeyboardButton("🎯 Center",        callback_data=f"rs:{uid}:wm_pos:center")],
        [InlineKeyboardButton("↙️ Bottom Left",   callback_data=f"rs:{uid}:wm_pos:bottom_left"),
         InlineKeyboardButton("↘️ Bottom Right",  callback_data=f"rs:{uid}:wm_pos:bottom_right")],
        [InlineKeyboardButton(wm_toggle_label,                       callback_data=f"rs:{uid}:wm_toggle")],
        [InlineKeyboardButton("✏️ Change Watermark Text",            callback_data=f"rs:{uid}:wm_text")],
        [InlineKeyboardButton(f"{auto_icon} Last 2 minute",          callback_data=f"rs:{uid}:auto_toggle")],
        [InlineKeyboardButton("🔙 Back",                              callback_data=f"rs:{uid}:back_audio"),
         InlineKeyboardButton("📐 Next: Video Size →",               callback_data=f"rs:{uid}:next_quality")],
        [InlineKeyboardButton("❌ Cancel",                            callback_data=f"rs:{uid}:cancel")],
    ])


def _kb_step2(s: dict) -> InlineKeyboardMarkup:
    uid  = s["user_id"]
    dur  = s["duration_sec"]
    rows = []
    for q, label, icon in [
        ("480", "480p", "🖥️"), ("576", "576p", "🖥️"), ("640", "640p", "🖥️"),
        ("720", "720p", "🖥️"), ("1080", "1080p", "🔵"), ("original", "Original", "🔒"),
    ]:
        sel = "✅ " if s["quality"] == q else ""
        rows.append([InlineKeyboardButton(f"{sel}{icon} {label} ({_est_size_mb(dur, q)})",
                                          callback_data=f"rs:{uid}:quality:{q}")])
    rows.append([InlineKeyboardButton("◀️ Back to Watermark",    callback_data=f"rs:{uid}:back_step1")])
    rows.append([InlineKeyboardButton("📐 Next: Aspect Ratio →", callback_data=f"rs:{uid}:next_aspect"),
                 InlineKeyboardButton("❌ Cancel",               callback_data=f"rs:{uid}:cancel")])
    return InlineKeyboardMarkup(rows)


def _kb_step3(s: dict) -> InlineKeyboardMarkup:
    uid  = s["user_id"]
    rows = []
    for asp, label in [
        ("none",    "🔒 None (Keep as-is)"), ("21:9", "📽 21:9 Aspect"),
        ("16:9",    "🖥️ 16:9 Aspect"),       ("4:5",  "📱 4:5 Aspect"),
        ("bars",    "⬛ 16:9 Black Bars"),    ("zoom", "🔍 16:9 Zoom"),
        ("1280x720","📐 scale=1280:720"),
    ]:
        sel = "✅ " if s["aspect"] == asp else ""
        rows.append([InlineKeyboardButton(f"{sel}{label}", callback_data=f"rs:{uid}:aspect:{asp}")])
    rows.append([InlineKeyboardButton("◀️ Quality/Size",   callback_data=f"rs:{uid}:back_step2")])
    rows.append([InlineKeyboardButton("▶️ Start Recording", callback_data=f"rs:{uid}:start"),
                 InlineKeyboardButton("❌ Cancel",          callback_data=f"rs:{uid}:cancel")])
    return InlineKeyboardMarkup(rows)


def _build_vf_and_codec(setup: dict) -> tuple[list[str], list[str], bool]:
    """
    Returns (extra_inputs, post_args, needs_encode).
    extra_inputs: additional -i args inserted after the main stream input (e.g. ["-i", logo_path]).
    post_args: everything after all inputs — filters, maps, codec args.
    """
    quality      = setup["quality"]
    aspect       = setup["aspect"]
    wm_on        = setup["watermark_on"]
    needs_encode = quality != "original" or aspect != "none" or wm_on
    vf: list[str] = []

    if aspect == "21:9":
        vf.append("crop=ih*21/9:ih")
    elif aspect == "16:9":
        vf.append("crop=min(iw\\,ih*16/9):min(ih\\,iw*9/16)")
    elif aspect == "4:5":
        vf.append("crop=ih*4/5:ih")
    elif aspect == "bars":
        vf += ["scale=-2:720", "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black"]
    elif aspect == "zoom":
        vf += ["scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080"]
    elif aspect == "1280x720":
        vf.append("scale=1280:720")

    res_map = {"480": "-2:480", "576": "-2:576", "640": "-2:640", "720": "-2:720", "1080": "-2:1080"}
    if quality in res_map and aspect not in ("bars", "zoom", "1280x720"):
        vf.append(f"scale={res_map[quality]}")

    extra_inputs: list[str] = []

    if wm_on:
        dur_sec  = setup.get("duration_sec", 0) or time_to_seconds(setup.get("timestamp", "0"))
        wm_start = max(0, dur_sec - 120)
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
                "bottom_center": "(W-w)/2:H-h-10",
                "bottom_right":  "W-w-20:H-h-60",
            }
            img_pos = pos_map_img.get(setup["watermark_pos"], "W-w-10:H-h-10")
            _wm_sz = get_watermark_size()
            if vf:
                fc = (f"[0:v]{','.join(vf)}[_vs];"
                      f"[1:v]scale={_wm_sz}:-1[_wm];"
                      f"[_vs][_wm]overlay={img_pos}:enable='gte(t,{wm_start})'[_out]")
            else:
                fc = (f"[1:v]scale={_wm_sz}:-1[_wm];"
                      f"[0:v][_wm]overlay={img_pos}:enable='gte(t,{wm_start})'[_out]")

            sel_tracks = setup["audio_track"]
            post: list[str] = ["-filter_complex", fc, "-map", "[_out]"]
            if not sel_tracks:
                post += ["-map", "0:a?"]
            else:
                for t in sorted(sel_tracks):
                    post += ["-map", f"0:a:{t-1}?"]
            crf = "23" if quality in ("480", "576", "640") else "21"
            abr = "192k" if quality == "1080" else "128k"
            post += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                     "-c:a", "aac", "-b:a", abr]
            return extra_inputs, post, needs_encode

        else:
            # ── Fallback: text watermark (drawtext) ───────────────────────────
            pos_map_txt = {
                "top_left":      "x=19:y=80",
                "top_center":    "x=(w-tw)/2:y=46",
                "top_right":     "x=w-tw-20:y=80",
                "center":        "x=(w-tw)/2:y=(h-th)/2",
                "bottom_left":   "x=20:y=h-th-60",
                "bottom_center": "x=(w-tw)/2:y=h-th-10",
                "bottom_right":  "x=w-tw-20:y=h-th-60",
            }
            xy   = pos_map_txt.get(setup["watermark_pos"], "x=10:y=10")
            safe = ((setup.get("watermark_text") or get_default_watermark())
                    .replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:"))
            vf.append(f"drawtext=text='{safe}':fontsize=28:fontcolor=white"
                      f":box=1:boxcolor=black@0.4:boxborderw=4:{xy}"
                      f":enable='gte(t,{wm_start})'")

    post: list[str] = []
    sel_tracks = setup["audio_track"]
    if not sel_tracks:
        post += ["-map", "0:v?", "-map", "0:a?"]
    else:
        post += ["-map", "0:v?"]
        for t in sorted(sel_tracks):
            post += ["-map", f"0:a:{t - 1}?"]

    if needs_encode:
        if vf:
            post += ["-vf", ",".join(vf)]
        crf = "23" if quality in ("480", "576", "640") else "21"
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
        code  = lang.upper()[:3]
        icon  = "✅ " if (all_selected or i in sel) else ""
        buttons.append(InlineKeyboardButton(
            f"{icon}{code} ({codec})",
            callback_data=f"rs:{uid}:audio_toggle:{i}"
        ))
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    all_icon = "✅" if all_selected else "🔄"
    rows.append([InlineKeyboardButton(
        f"{all_icon} Select All Tracks",
        callback_data=f"rs:{uid}:audio_select_all"
    )])

    rows.append([
        InlineKeyboardButton("◀️ Back",              callback_data=f"rs:{uid}:cancel"),
        InlineKeyboardButton("✅ Next: Watermark →",  callback_data=f"rs:{uid}:next_wm"),
    ])
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
    lines.append("\n👇 Choose an audio track, then tap **Next**:")
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

async def handle_record(client: Client, message: Message):
    user_id = message.from_user.id
    params  = " ".join(message.command[1:])
    parts   = params.split(" ", 2)
    if len(parts) < 2:
        return await message.reply_text("Bad arguments. Use `/rec <link> HH:MM:SS <filename>`.")
    url          = parts[0]
    timestamp    = parts[1]
    raw_filename = parts[2].strip() if len(parts) > 2 else DEFAULT_FILENAME
    for bad in '/\\:*?"<>|':
        raw_filename = raw_filename.replace(bad, "_")

    dur_sec = time_to_seconds(timestamp)
    setup: dict = {
        "user_id":        user_id,
        "chat_id":        message.chat.id,
        "orig_msg":       message,
        "url":            url,
        "timestamp":      timestamp,
        "duration_sec":   dur_sec,
        "filename":       raw_filename,
        "watermark_on":   False,
        "watermark_pos":  "bottom_right",
        "watermark_text": get_default_watermark(),
        "audio_track":    [],
        "auto_mode":      False,
        "quality":        "original",
        "aspect":         "none",
        "step":           0,
        "detected_audio_tracks": [],
    }
    rec_setup_sessions[user_id] = setup

    # Probe audio tracks from the stream before showing the wizard
    probe_msg = await message.reply_text(
        "🔍 **Probing stream for audio tracks…**"
    )
    setup["setup_msg_id"] = probe_msg.id

    # Effective URL after redirect/page extraction
    probe = await probe_stream(url)
    effective_url = probe["final_url"]
    tracks = await _probe_audio_tracks(effective_url)
    setup["detected_audio_tracks"] = tracks
    setup["effective_url"]         = effective_url
    setup["is_hls"]                = probe["is_hls"]

    await probe_msg.edit_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))

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

    msg = await client.send_message(chat_id, "⚙️ Initializing recording...")

    try:
        raw_filename = filename
        for bad in '/\\:*?"<>|':
            raw_filename = raw_filename.replace(bad, "_")
        mkv_filename = f"{raw_filename}.mkv"
        save_dir     = join(DOWNLOAD_DIRECTORY, str(int(time.time())))
        os.makedirs(save_dir, exist_ok=True)
        video_path   = join(save_dir, mkv_filename)

        recording_start = time.time()
        duration        = time_to_seconds(timestamp)

        rec_entry = {
            "start":         recording_start,
            "status":        {
                "filename": raw_filename, "target": timestamp,
                "progress": "00:00:00", "save_dir": save_dir,
            },
            "ffmpeg_pid":    None,
            "progress_task": None,
            "effective_url": None,
            "is_hls":        False,
            "is_photo_msg":  False,
            "snap_path":     None,
        }
        active_recs.setdefault(user_id, {})[rec_id] = rec_entry

        def _build_progress_text() -> str:
            elapsed = time.time() - recording_start
            pct     = min((elapsed / duration) * 100, 100) if duration > 0 else 0
            bar     = "●" * int(10 * pct // 100) + "⬜" * (10 - int(10 * pct // 100))
            task_id = hex(rec_id)[2:10]
            active_recs[user_id][rec_id]["status"]["progress"] = TimeFormatter(int(elapsed * 1000))
            q_str  = f"{setup['quality']}p" if setup["quality"] != "original" else "Original"
            wm_str = setup["watermark_pos"].replace("_", " ").title() if setup["watermark_on"] else "Off"
            slot_n = list(active_recs.get(user_id, {}).keys()).index(rec_id) + 1
            return (
                f"🎬 **Recording #{slot_n} in Progress...**\n\n"
                f"📡 Stream Capture\n"
                f"[{bar}]  {pct:.1f}%\n"
                f"⏱ Time  : {TimeFormatter(int(elapsed*1000))} / {TimeFormatter(duration*1000)}\n"
                f"🆔 Task  : {task_id}\n\n"
                f"📺 Quality: `{q_str}` | 💧 WM: `{wm_str}`\n"
                f"_Press **Gen Preview** for a live thumbnail_"
            )

        async def update_recording_progress():
            while rec_id in active_recs.get(user_id, {}):
                if (user_id, rec_id) in cancelled_recs:
                    break
                kb = _rec_progress_kb(user_id, rec_id)
                text = _build_progress_text()
                try:
                    entry = active_recs.get(user_id, {}).get(rec_id, {})
                    if entry.get("is_photo_msg"):
                        await msg.edit_caption(text, reply_markup=kb)
                    else:
                        await msg.edit_text(text, reply_markup=kb)
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
            await msg.edit_text("▶️ Starting recording...", reply_markup=_pkb)
        elif is_mpd:
            # DASH/MPD — skip probe, use URL directly
            effective_url  = url
            is_hls         = False
            extracted_from = None
            await msg.edit_text("📡 DASH stream detected — starting recording...", reply_markup=_pkb)
        else:
            await msg.edit_text("🔍 Probing stream...", reply_markup=_pkb)
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
                await msg.edit_text("Found embedded HLS stream — starting recording...", reply_markup=_pkb)
            else:
                await msg.edit_text("▶️ Starting recording...", reply_markup=_pkb)

        # Store stream info in rec_entry for Gen Preview callback
        rec_entry["effective_url"] = effective_url
        rec_entry["is_hls"]        = is_hls

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
                await msg.edit_text("🔑 Patching AES key into manifest…", reply_markup=_pkb)
                ffmpeg_input = await _prepare_aes_input(
                    url, setup["aes_key"], extra_headers, save_dir
                )
                is_hls = True
                rec_entry["effective_url"] = ffmpeg_input
                LOG.info(f"AES patch OK uid={user_id} patched={ffmpeg_input}")
            except Exception as e:
                LOG.warning(f"AES patch failed: {e} — falling back to original URL")
                ffmpeg_input = effective_url
            await msg.edit_text("▶️ Starting recording…", reply_markup=_pkb)

        # ── ClearKey DRM (DASH/MPD): fetch keys from license URL ───────────
        if setup.get("license_url"):
            try:
                await msg.edit_text("🔑 Fetching ClearKey DRM license…", reply_markup=_pkb)
                ck = await asyncio.to_thread(
                    _fetch_clearkey_keys_sync, setup["license_url"], extra_headers
                )
                if ck:
                    clearkey_arg = ck
                    LOG.info(f"ClearKey key fetched uid={user_id}: {ck[:8]}…")
                await msg.edit_text("▶️ Starting recording…", reply_markup=_pkb)
            except Exception as e:
                LOG.warning(f"ClearKey license fetch failed: {e} — recording without decryption key")
                await msg.edit_text(f"⚠️ ClearKey fetch failed: `{e}`\n▶️ Continuing without key…", reply_markup=_pkb)

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
        for _i in range(3):
            args += [
                f"-metadata:s:a:{_i}", f"title={_brand}",
                f"-metadata:s:a:{_i}", f"handler_name={_brand}",
            ]

        args += [
            # H264 resilience: ignore decode errors in live streams
            "-fflags", "+discardcorrupt+genpts",
            "-err_detect", "ignore_err",
            "-t", str(duration), video_path,
        ]

        ffmpeg_process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        rec_entry["ffmpeg_pid"] = ffmpeg_process.pid
        LOG.info(f"FFmpeg pid={ffmpeg_process.pid} user={user_id} rec={rec_id} re_encode={re_encodes}")

        # Take a background snapshot right after FFmpeg starts — switch progress msg to photo
        async def _try_initial_snapshot():
            await asyncio.sleep(10)   # give FFmpeg a moment to buffer first segment
            if rec_id not in active_recs.get(user_id, {}):
                return
            snap_path = join(save_dir, "live_preview.jpg")
            ok = await take_stream_snapshot(effective_url, snap_path, is_hls)
            if not ok or rec_id not in active_recs.get(user_id, {}):
                return
            try:
                kb   = _rec_progress_kb(user_id, rec_id)
                text = _build_progress_text()
                await client.edit_message_media(
                    chat_id, msg.id,
                    InputMediaPhoto(snap_path, caption=text),
                    reply_markup=kb,
                )
                active_recs[user_id][rec_id]["is_photo_msg"] = True
                active_recs[user_id][rec_id]["snap_path"]    = snap_path
            except Exception as e:
                LOG.debug(f"Initial snapshot switch failed: {e}")

        asyncio.create_task(_try_initial_snapshot())

        _stdout, stderr = await ffmpeg_process.communicate()
        retcode = ffmpeg_process.returncode
        rec_entry.pop("ffmpeg_pid", None)
        pt = rec_entry.pop("progress_task", None)
        if pt:
            pt.cancel()

        was_cancelled = (user_id, rec_id) in cancelled_recs
        if retcode != 0 and not was_cancelled:
            err_tail = stderr.decode(errors="ignore").strip()
            if len(err_tail) > 1500:
                err_tail = "..." + err_tail[-1500:]
            if not err_tail:
                err_tail = f"FFmpeg exited with code {retcode} (no stderr)."
            raise Exception(f"FFmpeg error:\n{err_tail}")

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            if was_cancelled:
                await msg.edit_text("Recording cancelled — no video recorded.")
                return
            raise Exception("No video file created or file is empty.")

        await msg.edit_text("🖼 Generating thumbnail...")
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

        retention_note = f"_Auto-deleted from server after {_retention_label()}._"
        q_str = f"{setup['quality']}p" if setup["quality"] != "original" else "Original"
        asp_label = {
            "none": "None", "21:9": "21:9", "16:9": "16:9", "4:5": "4:5",
            "bars": "16:9 Bars", "zoom": "16:9 Zoom", "1280x720": "1280×720",
        }.get(setup["aspect"], setup["aspect"])
        audio_note = "All tracks" if setup["audio_track"] == 0 else f"Track {setup['audio_track']}"
        wm_note    = (f"💧 Watermark: `{setup['watermark_pos'].replace('_',' ').title()}`"
                      if setup["watermark_on"] else "")

        if was_cancelled:
            caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                       f"Duration: `{TimeFormatter(dur * 1000)}`\nFormat: `MKV (partial)`\n"
                       f"Channel: @{SUPPORT_CHANNEL}\n\n"
                       f"_Recording was cancelled — partial file attached._\n{retention_note}")
        else:
            caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                       f"Duration: `{TimeFormatter(dur * 1000)}`\n"
                       f"Quality: `{q_str}` | Aspect: `{asp_label}`\n"
                       f"Audio: `{audio_note}`\n"
                       + (f"{wm_note}\n" if wm_note else "")
                       + f"Channel: @{SUPPORT_CHANNEL}\n\n{retention_note}")

        send_target  = orig_msg or (query.message if query else msg)
        size_bytes   = os.path.getsize(video_path)
        size_str     = (f"{size_bytes / (1024**3):.2f} GB" if size_bytes >= 1024**3
                        else f"{size_bytes / (1024**2):.1f} MB")

        # ── Auto-compress if recording is 800 MB – 1 GB ─────────────────────
        # Runs silently before showing upload buttons. All audio tracks (multi-
        # language) are preserved. Skipped for cancelled/partial recordings.
        if not was_cancelled:
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

        pending_uploads[(user_id, rec_id)] = {
            "video_path":    video_path,
            "thumb_path":    thumb_path if thumb_ok else None,
            "caption":       caption,
            "dur":           dur,
            "chat_id":       chat_id,
            "save_dir":      save_dir,
            "was_cancelled": was_cancelled,
            "filename":      mkv_filename,
            "send_target":   send_target,
            "status_msg":    msg,
            "setup":         setup,
        }

        # Always show all 3 upload buttons; Drive guard is handled in the callback
        buttons = [
            [
                InlineKeyboardButton("📤 Telegram",       callback_data=f"upl:{user_id}:{rec_id}:tg"),
                InlineKeyboardButton("☁️ Google Drive",   callback_data=f"upl:{user_id}:{rec_id}:gd"),
            ],
            [
                InlineKeyboardButton("📤+☁️ Upload to Both", callback_data=f"upl:{user_id}:{rec_id}:both"),
            ],
        ]
        kb = InlineKeyboardMarkup(buttons)

        await msg.edit_text(
            f"🎉 **Recording Successfully Completed!**\n\n"
            f"🎬 File Name: `{mkv_filename}`\n"
            f"📦 Size: `{size_str}`\n"
            f"⏱ Duration: `{TimeFormatter(dur * 1000)}`"
            f"{partial_note}\n\n"
            "Kripya choose karein aap is file ko kahan upload karna chahte hain:",
            reply_markup=kb,
        )

    except Exception as e:
        LOG.error(f"do_record error uid={user_id}: {e}")
        try:
            if (user_id, rec_id) not in cancelled_recs:
                if is_owner(user_id) or is_admin(user_id):
                    # Admins/owners see full technical error
                    err_text = str(e)
                    if len(err_text) > 3500:
                        err_text = "...[truncated]...\n" + err_text[-3500:]
                    await msg.edit_text(f"**Recording failed.**\n\n`{err_text}`")
                else:
                    # Normal users see a clean message — no FFmpeg internals
                    await msg.edit_text(
                        "❌ **Recording failed.**\n\n"
                        "Stream could not be recorded. Please check the link and try again.\n"
                        "Use /contact if the problem persists."
                    )
            if (user_id, rec_id) not in cancelled_recs and save_dir and os.path.exists(save_dir):
                _safe_rmtree(save_dir)
        except Exception as exc:
            LOG.error(f"Failed to edit error message: {exc}")
    finally:
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
    """Choice buttons shown before upload: Drive / Telegram / Drive + Telegram."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ Drive",              callback_data=f"upl_ch:{uid}:gd")],
        [InlineKeyboardButton("📤 Telegram",            callback_data=f"upl_ch:{uid}:tg")],
        [InlineKeyboardButton("☁️📤 Drive + Telegram",  callback_data=f"upl_ch:{uid}:both")],
        [InlineKeyboardButton("❌ Cancel",               callback_data=f"upl_ch:{uid}:cancel")],
    ])


async def _await_upload_choice(uid: int, status_msg, info_text: str = "") -> str:
    """Show upload destination buttons, wait up to 4 min, return 'tg'/'gd'/'both'/'cancel'."""
    ev   = asyncio.Event()
    dest = ["tg"]
    pending_upload_state[uid] = {"ev": ev, "dest": dest}
    try:
        await status_msg.edit_text(
            f"✅ **Processing complete!**\n\n"
            + (f"{info_text}\n\n" if info_text else "")
            + "📤 **Select Upload Destination**\n"
            + "Please choose an upload option within 4 minutes.\n"
            + "_(Upload option chuniye — 4 minute ke andar)_\n\n"
            + "⏳ If no button is clicked within 4 minutes, the file will be automatically uploaded to Telegram.\n"
            + "_(4 minute mein button na dabaya to file automatically Telegram par upload ho jayegi.)_",
            reply_markup=_upload_dest_keyboard(uid),
        )
    except Exception:
        pass
    try:
        await asyncio.wait_for(ev.wait(), timeout=240)
    except asyncio.TimeoutError:
        dest[0] = "tg"   # default: Telegram if no response in 4 min
    finally:
        pending_upload_state.pop(uid, None)
    return dest[0]


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
    user_id  = message.from_user.id
    msg      = status_msg
    save_dir: Optional[str] = None
    if audio_ids is None:
        audio_ids = []
    try:
        if msg is None:
            msg = await message.reply_text("⬇️ Initializing download...")
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
            "concurrent_fragment_downloads": 3,   # low for weak server
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
                   f"Channel: @{SUPPORT_CHANNEL}\n\n{retention_note}")

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
                await message.reply_text(fail_text)
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
# Auto-compression constants (800 MB – 1 GB → ≤360 MB, all audio tracks kept)
# ---------------------------------------------------------------------------
AUTO_COMPRESS_MIN_MB    = 800    # trigger threshold (inclusive)
AUTO_COMPRESS_MAX_MB    = 1024   # upper limit (inclusive, = 1 GB)
AUTO_COMPRESS_TARGET_MB = 355    # desired output size (headroom below 360 MB)
AUTO_COMPRESS_HEIGHTS   = [640, 576]   # try 640p first, fall back to 576p

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
    Automatically compress a recording that falls between AUTO_COMPRESS_MIN_MB and
    AUTO_COMPRESS_MAX_MB (800 MB – 1 GB) down to AUTO_COMPRESS_TARGET_MB (~355 MB).

    Resolution order: 640p → 576p (first one that succeeds is used).
    ALL audio tracks are preserved with -map 0:a? so multi-language recordings
    stay intact.  Subtitles are copied as-is.
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
    cpu_threads = str(min(os.cpu_count() or 1, 2))

    for height in AUTO_COMPRESS_HEIGHTS:
        out_path = join(save_dir, f"autocomp_{height}p_{int(time.time())}.mkv")

        # Reserve ~512 kbps for all audio tracks combined (4 tracks × 128 kbps).
        # Use 'fast' preset so compression finishes well within 4 minutes.
        target_total_kbps = (target_mb * 8 * 1024) / max(1, duration)
        video_kbps = max(200, int(target_total_kbps - 512 - 32))

        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-threads", cpu_threads,
            "-progress", "pipe:1", "-y",
            "-i", video_path,
            "-map", "0:v:0",     # primary video track
            "-map", "0:a?",      # ALL audio tracks — multi-language safe
            "-map", "0:s?",      # subtitle tracks copied verbatim
            "-vf", f"scale=-2:{height}:flags=lanczos",
            "-c:v", "libx264",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.4)}k",
            "-bufsize", f"{int(video_kbps * 2.5)}k",
            "-preset", "fast",   # balances speed vs quality; keeps ≤4-min window
            "-tune", "film",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-c:s", "copy",
            out_path,
        ]

        try:
            await status_msg.edit_text(
                f"🗜 **Auto-Compressing…**\n\n"
                f"File size: `{size_mb:.0f} MB` (exceeds 800 MB threshold)\n"
                f"Target: `~{target_mb} MB` @ `{height}p`\n"
                f"_All audio/language tracks will be preserved._"
            )
        except Exception:
            pass

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        user_ffmpeg_pids[user_id] = proc.pid
        progress_state = {"out_time_us": 0, "total_size": 0, "speed": 1.0}

        async def _read_prog(_proc=proc):
            while True:
                line = await _proc.stdout.readline()
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

        async def _render_prog(_proc=proc):
            while _proc.returncode is None:
                done_sec = progress_state["out_time_us"] / 1_000_000
                pct      = min(100.0, done_sec / max(1, duration) * 100)
                cur_mb   = progress_state["total_size"] / (1024 * 1024)
                spd      = progress_state["speed"]
                bar_filled = int(pct / 5)
                bar = "█" * bar_filled + "░" * (20 - bar_filled)
                try:
                    await status_msg.edit_text(
                        f"🗜 **Auto-Compressing…** {pct:.1f}%\n"
                        f"`{bar}`\n\n"
                        f"Resolution: `{height}p` | Target: `{target_mb} MB`\n"
                        f"Current size: `{cur_mb:.1f} MB` | Speed: `{spd:.1f}x`\n"
                        f"_All audio tracks preserved ✓_"
                    )
                except Exception:
                    pass
                await asyncio.sleep(8)

        reader_task   = asyncio.create_task(_read_prog())
        renderer_task = asyncio.create_task(_render_prog())

        rc = await proc.wait()
        reader_task.cancel()
        renderer_task.cancel()
        user_ffmpeg_pids.pop(user_id, None)

        if rc != 0:
            err = (await proc.stderr.read()).decode(errors="ignore")
            LOG.warning(f"auto_compress {height}p failed rc={rc}: {err[-500:]}")
            try:
                os.remove(out_path)
            except Exception:
                pass
            continue  # try next resolution

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            LOG.warning(f"auto_compress {height}p produced empty file")
            continue

        out_mb = os.path.getsize(out_path) / (1024 * 1024)
        LOG.info(
            f"auto_compress: {size_mb:.0f} MB → {out_mb:.1f} MB @ {height}p "
            f"uid={user_id}"
        )
        try:
            await status_msg.edit_text(
                f"✅ **Auto-Compression Done!**\n\n"
                f"Original: `{size_mb:.0f} MB`  →  Compressed: `{out_mb:.1f} MB`\n"
                f"Resolution: `{height}p` | All audio tracks kept ✓\n\n"
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

    cpu_threads = str(min(os.cpu_count() or 1, 2))   # cap at 2 for weak server
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
        args += ["-c:v", res_cfg["codec"], "-crf", "18", "-preset", "medium",
                 "-tune", "film"]
    elif res_cfg["codec"] == "libx265":
        args += ["-c:v", "libx265", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2.5)}k",
                 "-preset", "medium", "-x265-params", "log-level=error:aq-mode=3",
                 "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "libx264", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2.5)}k",
                 "-preset", "medium", "-tune", "film"]
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
                   f"Channel: @{SUPPORT_CHANNEL}\n\n{retention_note}")

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

        first = True
        for chunk_start in range(0, len(produced), 10):
            chunk = produced[chunk_start:chunk_start + 10]
            media = []
            for i, (path, ts) in enumerate(chunk):
                global_idx = chunk_start + i + 1
                cap = (f"🎬 **{BRAND_TITLE}**\n\n"
                       f"📸 `{len(produced)}` screenshot{'s' if len(produced) != 1 else ''} • "
                       f"video `{TimeFormatter(int(duration * 1000))}`\n"
                       f"Channel: @{SUPPORT_CHANNEL}"
                       if first and i == 0
                       else f"`{global_idx:02d}` • `{TimeFormatter(int(ts * 1000))}`")
                media.append(InputMediaPhoto(media=path, caption=cap))
            await status_msg.reply_media_group(media=media)
            first = False

        try: await status_msg.edit_text(f"✅ Done — sent `{len(produced)}` screenshot"
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
    user_id   = message.from_user.id
    save_dir  = sess["save_dir"]
    videos    = sess["videos"]
    out_path  = join(save_dir, f"merged_{int(time.time())}.mkv")
    total_dur = sum(v["duration"] for v in videos)

    user_tasks[user_id]  = time.time()
    user_status[user_id] = {"id": int(user_tasks[user_id]), "user_id": user_id,
                            "filename": os.path.basename(out_path),
                            "duration_str": TimeFormatter(int(total_dur * 1000)),
                            "channel_name": "Merge", "url": "(local)", "progress": "0%"}
    status = await message.reply_text(
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
                   f"Channel: @{SUPPORT_CHANNEL}\n\n{retention_note}")

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
        retention_note = (f"_Auto-deleted from server after {_retention_label()}._")
        caption = (f"🎬 **{BRAND_TITLE}**\n\n"
                   f"🔤 Title: `{raw_text[:80]}`\n"
                   f"📌 Position: `{pos_label}`\n"
                   f"⏱ Duration: `{TimeFormatter(int(duration * 1000))}`\n"
                   f"💾 Size: `{out_size_mb:.1f} MB`\n"
                   f"Channel: @{SUPPORT_CHANNEL}{long_note}\n\n"
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


def _get_edit_queue(msg) -> "asyncio.Queue[str | None]":
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
                    await msg.edit_text(latest)
                except Exception:
                    pass

        asyncio.create_task(_worker())
    return _edit_queues[key]


def _fire_edit(msg, text: str) -> None:
    """Enqueue a message edit; the per-message worker serializes & deduplicates them."""
    _get_edit_queue(msg).put_nowait(text)


def _close_edit_queue(msg) -> None:
    """Send sentinel to stop the worker after all queued edits finish."""
    q = _edit_queues.get(id(msg))
    if q:
        q.put_nowait(None)


async def progress_for_pyrogram(current, total, message, start, msg,
                                 save_dir=None, was_cancelled=False):
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
        final    = (
            f"✅ Sent Successfully\n\n"
            f"📁 File Name:\n{filename}\n\n"
            f"📦 Size:\n{size_str}\n\n"
            f"📤 Destination:\nTelegram\n\n"
            f"🆔 Task:\n{task_id}\n\n"
        )
        if was_cancelled:
            final += "⚠️ Partial recording sent.\n"
        final += f"Server copy auto-deletes in {label}."
        try:
            await msg.edit_text(final)
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
    _fire_edit(msg, text)   # non-blocking; worker serialises & deduplicates


# ---------------------------------------------------------------------------
# 2 GB auto-split helper
# ---------------------------------------------------------------------------

TG_MAX_BYTES = 1_950_000_000  # 1.95 GB — safe margin under Telegram's 2 GB hard limit


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
                            await status_msg.edit_text(
                                f"⚠️ Upload interrupted (attempt {attempt + 1}/{_MAX_ATTEMPTS})."
                                f" Retrying in {wait}s…"
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
            await status_msg.edit_text(
                f"📦 File is {size_gb:.2f} GB — exceeds Telegram's 2 GB limit.\n"
                f"Splitting into {num_parts} parts… please wait."
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
                f'-c copy {shlex.quote(part_path)}'
            )
        else:
            part_sec = None
            cmd = (
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-i {shlex.quote(video_path)} '
                f'-c copy -f segment -segment_size {TG_MAX_BYTES} '
                f'-reset_timestamps 1 '
                f'{shlex.quote(part_path)}'
            )

        rc, _out, err = await runcmd(cmd)
        if rc != 0 or not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
            LOG.error("Split part %d/%d failed: %s", i + 1, num_parts, err[-500:])
            continue

        part_caption = caption + f"\n\n📂 **Part {i + 1} / {num_parts}**"
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
    f = service.files().create(
        body=meta, media_body=media, fields="id,webViewLink"
    ).execute()
    link = f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view"
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
        final = (
            f"✅ Uploaded to Google Drive\n"
            f"🔗 [Drive Link Ready]({link})"
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

❌ Free Users: Not Allowed
✅ Premium / Admin / Owner: Allowed

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

❌ Free Users: Not Allowed
✅ Verify / Premium / Admin / Owner: Allowed

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

❌ Free Users: Not Allowed
✅ Verify / Premium / Admin / Owner: Allowed

• `/download [filename]`

Supported OTT:
• Hotstar
• JioCinema
• ZEE5
• SonyLIV

━━━━━━━━━━━━━━━━━━━━━━━
☁️ **Google Drive**
━━━━━━━━━━━━━━━━━━━━━━━

❌ Free Users: Not Allowed
✅ Verify / Premium / Admin / Owner: Allowed

• `/gdrive` or `/googledrive`
Connect your Google Drive.

• `/drivelogout`
Disconnect your Drive account.

━━━━━━━━━━━━━━━━━━━━━━━
🍪 **Cookies (OTT Login)**
━━━━━━━━━━━━━━━━━━━━━━━

❌ Free Users: Not Allowed
✅ Verify / Premium / Admin / Owner: Allowed

• `/set_cookies`
Upload cookies.txt (Netscape format).

• `/cookies_status`
Show stored cookies.

• `/del_cookies`
Delete stored cookies.

━━━━━━━━━━━━━━━━━━━━━━━
📡 **JioTV Catchup**
━━━━━━━━━━━━━━━━━━━━━━━

✅ Verify / Premium / Admin / Owner: Allowed

• `/jiostatus` — Login status check
• `/channels [search]` — Channel list browse karo
• `/jiorec` — JioTV live record wizard
• `/dl -Jiotv -c ChannelName -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM -n File`
Download catchup recording

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

📡 Support: @{SUPPORT_CHANNEL}
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

**JioTV Login (Owner Only)**

• `/login <10-digit mobile>`
Start JioTV login — OTP aayega phone pe.

• `/otp <6-digit code>`
Submit OTP to complete JioTV login.

• `/jiostatus`
Check JioTV login status.

• `/channels [search]`
Browse JioTV channel list.
"""


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

@app.on_message(filters.command("start") & AUTH)
async def start_cmd(client: Client, message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "User"

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
        f"📡 Channel: @{SUPPORT_CHANNEL}\n"
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
    uid  = message.from_user.id if message.from_user else 0
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

@app.on_message(filters.group, group=-1)
async def _group_chat_gate(client: Client, message: Message):
    """
    In group chats: only respond inside the configured GROUP_CHAT_ID.
    If GROUP_CHAT_ID is 0 (not set), allow all groups.
    Owner/admin messages in any group always pass.
    """
    uid = message.from_user.id if message.from_user else 0
    if is_owner(uid) or is_admin(uid):
        return
    if GROUP_CHAT_ID and message.chat.id != GROUP_CHAT_ID:
        message.stop_propagation()


@app.on_message(filters.private, group=-1)
async def _group_gate(client: Client, message: Message):
    uid = message.from_user.id

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
        # JioTV commands (must pass group gate)
        "/login", "/otp", "/channels", "/jiorec",
        "/jiostatus", "/jiotvlogin", "/jiotvotp",
        "/jiotvchannels", "/jiotvlogout",
        "/dl", "/dlhelp",
        # Quick /di command
        "/di",
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    text = HELP_TEXT
    if is_owner(uid) or is_admin(uid):
        text = text + _OWNER_HELP_TEXT
    await message.reply_text(text, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /googledrive — per-user Google Drive OAuth2 connect / disconnect
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["googledrive", "gdrive"]) & AUTH)
async def googledrive_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args    = message.command[1:]
    sub     = args[0].lower() if args else ""

    # ── disconnect ────────────────────────────────────────────────────────
    if sub == "disconnect":
        if disconnect_user(user_id):
            return await message.reply_text(
                "✅ **Google Drive disconnected.**\n\n"
                "Aapki future recordings Telegram par upload hongi.\n"
                "Dobara connect karne ke liye: /googledrive"
            )
        return await message.reply_text(
            "⚠️ Aapka Google Drive abhi connected nahi tha."
        )

    # ── status ────────────────────────────────────────────────────────────
    if sub in ("status", "info"):
        connected = is_user_connected(user_id)
        sa_ready  = _gdrive_sa_enabled()
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
        return await message.reply_text("\n".join(lines))

    # ── already connected ─────────────────────────────────────────────────
    if is_user_connected(user_id):
        return await message.reply_text(
            "✅ **Google Drive Already Connected!**\n\n"
            "Aapki recordings automatically **aapki Drive** par upload hongi.\n\n"
            "🔹 Status: /googledrive status\n"
            "🔹 Disconnect: /googledrive disconnect"
        )

    # ── OAuth2 not configured — show service-account info or error ────────
    if not _gdrive_oauth_enabled():
        if _gdrive_sa_enabled():
            sa_email = get_sa_email()
            return await message.reply_text(
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
        return await message.reply_text(
            "⚠️ **Google Drive abhi configure nahi hai.**\n\n"
            "Owner se yeh secrets set karne ko bolein:\n"
            "• `GDRIVE_SA_JSON` + `GDRIVE_FOLDER_ID` (shared account)\n"
            "• `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (per-user login)"
        )

    # ── start OAuth2 device flow ──────────────────────────────────────────
    wait_msg = await message.reply_text("🔗 Google Drive se connect kar raha hoon...")
    try:
        flow = await asyncio.to_thread(start_device_flow_sync)
    except Exception as e:
        LOG.error(f"GDrive device flow start failed uid={user_id}: {e}")
        return await wait_msg.edit_text(f"❌ Google Drive connect nahi ho saka: `{e}`")

    user_code        = flow["user_code"]
    verification_url = flow.get("verification_url", "https://www.google.com/device")
    device_code      = flow["device_code"]
    interval         = int(flow.get("interval", 5))
    expires_in       = int(flow.get("expires_in", 1800))

    await wait_msg.edit_text(
        "🤖 **Google Drive Connect Karein**\n\n"
        f"**Step 1:** Yeh link kholo:\n{verification_url}\n\n"
        f"**Step 2:** Yeh code enter karo:\n`{user_code}`\n\n"
        "**Step 3:** Apna Google account select karein aur **Allow** karein.\n\n"
        f"⏰ Code **{expires_in // 60} minutes** mein expire hoga.\n"
        "_Jaise hi aap allow karein, bot automatically detect kar lega._"
    )

    asyncio.create_task(
        poll_and_save_token(client, user_id, device_code, interval, expires_in)
    )


# ---------------------------------------------------------------------------
# /Drivelogout — quick alias to disconnect Google Drive
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["Drivelogout", "drivelogout", "gdrive_logout", "drivelog"]) & AUTH)
async def drivelogout_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if disconnect_user(user_id):
        await message.reply_text(
            "✅ **Google Drive Disconnected!**\n\n"
            "Aapki agli recordings Telegram par upload hongi.\n\n"
            "Dobara connect karne ke liye: /googledrive"
        )
    else:
        await message.reply_text(
            "⚠️ Aapka Google Drive pehle se connected nahi tha.\n\n"
            "Connect karne ke liye: /googledrive"
        )


# ---------------------------------------------------------------------------
# /verify — ad-click self-service verification (shortxlinks.in)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("verify") & AUTH)
async def verify_cmd(client: Client, message: Message):
    uid = message.from_user.id

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
    if cq.from_user.id != uid:
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
    if cq.from_user.id != uid:
        return await cq.answer("Not your recording.", show_alert=True)
    entry = active_recs.get(uid, {}).get(rec_id)
    if not entry:
        return await cq.answer("Recording finished or not found.", show_alert=True)
    eff_url = entry.get("effective_url")
    is_hls  = entry.get("is_hls", True)
    save_dir = entry["status"]["save_dir"]
    if not eff_url:
        return await cq.answer("Stream URL not ready yet — try again in a moment.", show_alert=True)
    await cq.answer("📸 Capturing live frame…")
    snap_path = join(save_dir, f"snap_{int(time.time())}.jpg")
    ok = await take_stream_snapshot(eff_url, snap_path, is_hls)
    if not ok:
        return await cq.message.reply_text("❌ Could not capture a frame from the stream right now.")
    recording_start = entry["start"]
    duration        = time_to_seconds(entry["status"]["target"])
    elapsed         = time.time() - recording_start
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
        f"_Live preview — tap **Gen Preview** to refresh_"
    )
    kb = _rec_progress_kb(uid, rec_id)
    try:
        from pyrogram.types import InputMediaPhoto
        await client.edit_message_media(
            cq.message.chat.id, cq.message.id,
            InputMediaPhoto(snap_path, caption=text),
            reply_markup=kb,
        )
        active_recs[uid][rec_id]["is_photo_msg"] = True
        active_recs[uid][rec_id]["snap_path"]    = snap_path
    except Exception:
        await cq.message.reply_photo(snap_path, caption=text, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^rec_ref:(\d+):(\d+)$"))
async def rec_refresh_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":")
    uid    = int(parts[1])
    rec_id = int(parts[2])
    if cq.from_user.id != uid:
        return await cq.answer("Not your recording.", show_alert=True)
    entry = active_recs.get(uid, {}).get(rec_id)
    if not entry:
        return await cq.answer("Recording finished or not found.", show_alert=True)
    recording_start = entry["start"]
    duration        = time_to_seconds(entry["status"]["target"])
    elapsed         = time.time() - recording_start
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
    if cq.from_user.id != uid:
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

    if cq.from_user.id != uid:
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
                        caption=(f"🎬 **{BRAND_TITLE}** — ⏭ Last 2 minute\n"
                                 f"Channel: @{SUPPORT_CHANNEL}"),
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
    uid = message.from_user.id
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
    if not is_owner(message.from_user.id):
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
    if not is_owner(message.from_user.id):
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
    if not is_owner(message.from_user.id):
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
# /Admin_add, /Admin_delete, /Admin_list — owner-only, hidden
# ---------------------------------------------------------------------------

@app.on_message(filters.command("Admin_add"))
async def admin_add_cmd(_, message: Message):
    uid = message.from_user.id if message.from_user else None
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


@app.on_message(filters.command("Admin_delete"))
async def admin_delete_cmd(_, message: Message):
    if not is_owner(message.from_user.id):
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
    if not is_owner(message.from_user.id):
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
        filename += ".mkv"

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
    uid = message.from_user.id if message.from_user else 0
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id if message.from_user else 0
    if not (is_owner(uid) or is_admin(uid)):
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
    if not is_owner(message.from_user.id):
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
    entry = {"approved_by": message.from_user.id, "approved_at": datetime.now(tz).isoformat()}
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
    if not is_owner(message.from_user.id):
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
    if not is_owner(message.from_user.id):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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

    import limit_system as _ls
    ls_data = _ls._load()
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
        f"📡 Channel: @{SUPPORT_CHANNEL}\n"
        f"💬 Support: @{SUPPORT_USERNAME}\n\n"
        f"For subscriptions, custom requests, or issues — reach us on the channel or DM support."
    )


# ---------------------------------------------------------------------------
# /channel — browse hidden-URL channel list (names only)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("channel") & AUTH)
async def channel_cmd(client: Client, message: Message):
    await message.reply_text("**Browse channels**\n\nPick a channel:", reply_markup=_channel_root_kb())


@app.on_callback_query(filters.regex(r"^ch:(.+)$"))
async def cb_channel_select(client: Client, cq: CallbackQuery):
    name = cq.data.split(":", 1)[1]
    if name not in BOT_CHANNELS:
        return await cq.answer("Channel not found.", show_alert=True)
    await cq.answer(f"✅ {name} selected!")
    await cq.message.reply_text(
        f"📺 **{name}** select hua!\n\n"
        f"Record karne ke liye:\n`/drec {name} HH:MM:SS`\n`/rec {name} HH:MM:SS`\n\n"
        f"Example: `/drec {name} 00:30:00`"
    )


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------

@app.on_message(filters.command("search") & AUTH)
async def search_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/search POGO`")
    query = " ".join(message.command[1:]).lower()
    found = [name for name in BOT_CHANNELS if query in name.lower()]
    if not found:
        return await message.reply_text(f"No channels found for `{query}`.")
    lines = [f"🔍 **Results for `{query}`**\n"] + [f"📺 {n}" for n in found[:20]]
    if len(found) > 20:
        lines.append(f"\n…and {len(found) - 20} more.")
    lines.append("\nRecord karne ke liye: `/drec <channel name> HH:MM:SS`")
    await message.reply_text("\n\n".join(lines) if len(lines) <= 3 else "\n".join(lines))


# ---------------------------------------------------------------------------
# /statusme / /cancelme
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["statusme", "status"]) & AUTH)
async def statusme_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    recs    = active_recs.get(user_id, {})
    other   = user_status.get(user_id)

    if not recs and not other:
        return await message.reply_text("No active recording or job.")

    lines = []
    if recs:
        lines.append(f"🎬 **Active Recordings ({len(recs)}/{MAX_CONCURRENT_REC}):**")
        for i, (rid, entry) in enumerate(recs.items(), 1):
            st = entry.get("status", {})
            lines.append(
                f"\n**#{i}** `{st.get('filename', '?')}`\n"
                f"  ⏱ Progress: `{st.get('progress', '00:00:00')}` / `{st.get('target', '?')}`"
            )
        lines.append("\nUse /cancelme to stop all recordings.")
    if other:
        lines.append(
            f"\n📡 **Active Job**\n"
            f"File: `{other.get('filename', '?')}`\n"
            f"Progress: `{other.get('progress', '?')}`"
        )
        if other.get("url"):
            lines.append(f"URL: `{other['url'][:80]}{'…' if len(other.get('url',''))>80 else ''}`")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command(["cancelme", "cancel"]) & AUTH)
async def cancelme_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    recs    = active_recs.get(user_id, {})
    has_other = user_id in user_tasks or user_id in reclink_jobs

    if not recs and not has_other:
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

    rl = reclink_jobs.get(user_id)
    if rl and rl.get("task"):
        rl["task"].cancel()

    count = len(recs)
    note  = f"{count} recording(s)" if count else "job"
    await message.reply_text(
        f"⏹ **Cancel signal sent** ({note}).\n\nIf files were partially recorded they will be uploaded now."
    )


# ---------------------------------------------------------------------------
# /rec — pre-recording setup wizard entry
# ---------------------------------------------------------------------------

def _resolve_channel_prefix(message_text: str) -> str:
    """If the text right after the /rec|/drec command starts with a known
    BOT_CHANNELS name (case-insensitive, may contain spaces), swap that name
    for its hidden stream URL and return the rewritten message text. The URL
    itself is never shown back to the user — only used internally for FFmpeg.
    """
    space = message_text.find(" ")
    if space == -1:
        return message_text
    cmd_part = message_text[:space]
    rest     = message_text[space + 1:]
    for name in sorted(BOT_CHANNELS, key=len, reverse=True):
        name_len = len(name)
        if rest[:name_len].lower() == name.lower() and (
            len(rest) == name_len or rest[name_len] in " \t"
        ):
            remainder = rest[name_len:].strip()
            return f"{cmd_part} {BOT_CHANNELS[name]} {remainder}".rstrip()
    return message_text


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
        timestamp    = flags.pop("timestamp", None)
        raw_filename = flags.pop("filename", DEFAULT_FILENAME)
        if not url or not timestamp:
            return None, None, None, {}
        return url, timestamp, raw_filename, flags

    # ── Standard format: url timestamp [filename] [flags...] ─────────────────
    if len(tokens) < 2:
        return None, None, None, {}
    url       = tokens[0]
    timestamp = tokens[1]
    idx       = 2
    raw_filename = DEFAULT_FILENAME
    if idx < len(tokens) and not tokens[idx].startswith("-"):
        raw_filename = tokens[idx]
        idx = 3
    flags = _parse_rec_flags(tokens[idx:])
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
        "watermark_pos":         "bottom_right",
        "watermark_text":        get_default_watermark(),
        "audio_track":           [],
        "auto_mode":             False,
        "quality":               "original",
        "aspect":                "none",
        "step":                  -1,
        "detected_audio_tracks": [],
        # Inline flags
        "aes_key":      flags.get("aes_key", ""),
        "flag_cookie":  flags.get("cookie", ""),
        "flag_ua":      flags.get("user_agent", ""),
        "flag_referer": flags.get("referer", ""),
        "flag_origin":  flags.get("origin", ""),
        "license_url":  flags.get("license_url", ""),
        "drm_scheme":   flags.get("drm_scheme", ""),
    }


@app.on_message(filters.command("rec") & AUTH)
async def rec_cmd(client: Client, message: Message):
    user_id  = message.from_user.id
    msg_text = _resolve_channel_prefix(message.text or "")

    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /rec. Run /verify.")
    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await message.reply_text(
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously.\n"
            "Use /statusme to check them or /cancelme to cancel all."
        )

    # ── CloudPlay / JSON-ish multi-line format ────────────────────────────────
    #   Format A:  "user_agent": "..." -- "mpd_url": "...", -t HH:MM:SS
    #   Format B:  "m3u8_url": "...", "headers": { "Cookie": "...", ... }  -t HH:MM:SS
    _CP_KEYS = ('"mpd_url"', '"m3u8_url"', '"hls_url"', '"stream_url"')
    if "--" in msg_text or any(k in msg_text for k in _CP_KEYS):
        parsed = _parse_cloudplay_format(msg_text)
        if parsed:
            url, timestamp, raw_filename, flags = parsed
            if not url or not timestamp:
                return await message.reply_text(
                    "❌ Could not parse the CloudPlay format.\n"
                    "Make sure `mpd_url` / `m3u8_url` and `-t HH:MM:SS` are present."
                )
            setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
            asyncio.create_task(do_record(client, None, setup))
            return

    # ── Standard format: /rec url duration [filename] [flags...] ─────────────
    if len(message.command) < 3:
        return await message.reply_text(
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

    # If any flags present → skip wizard, record directly
    url, timestamp, raw_filename, flags = _shlex_parse_rec(msg_text)
    if flags:
        setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
        asyncio.create_task(do_record(client, None, setup))
        return

    await handle_record(client, message)


# ---------------------------------------------------------------------------
# /DirectRec — instant recording, no wizard
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["DirectRec", "directrec", "drec", "dr"]) & AUTH)
async def directrec_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /DirectRec. Run /verify.")
    if len(active_recs.get(user_id, {})) >= MAX_CONCURRENT_REC:
        return await message.reply_text(
            f"⚠️ You already have **{MAX_CONCURRENT_REC} recordings** running simultaneously.\n"
            "Use /statusme to check them or /cancelme to cancel all."
        )

    url, timestamp, raw_filename, flags = _shlex_parse_rec(_resolve_channel_prefix(message.text or ""))
    if not url or not timestamp:
        return await message.reply_text(
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
            "`-ua <v>`      — User-Agent string\n"
            "`-referer <v>` — Referer header\n"
            "`-aio`         — Allow all input extensions"
        )

    setup = _build_setup(user_id, message, url, timestamp, raw_filename, flags)
    asyncio.create_task(do_record(client, None, setup))


# ---------------------------------------------------------------------------
# Quick-paste: user sends an HLS URL directly as a text message
# ---------------------------------------------------------------------------

def _is_hls_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith(("http://", "https://", "//")) and ".m3u8" in t


@app.on_message(filters.private & filters.text & AUTH)
async def quick_rec_text(client: Client, message: Message):
    user_id = message.from_user.id
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

    if cq.from_user.id != uid:
        return await cq.answer("This is not your setup menu.", show_alert=True)

    setup = rec_setup_sessions.get(uid)
    if not setup:
        return await cq.answer("Setup session expired.", show_alert=True)

    if action == "cancel":
        rec_setup_sessions.pop(uid, None)
        try:
            await cq.message.edit_text("Recording setup cancelled.", reply_markup=None)
        except Exception:
            pass
        return await cq.answer("Cancelled.")

    # ---- Step 0: Audio track selection ----

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
            await cq.message.edit_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))
        except Exception:
            pass
        return

    if action == "audio_select_all":
        setup["audio_track"] = []
        await cq.answer("✅ All Tracks selected")
        try:
            await cq.message.edit_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))
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
            await cq.message.edit_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))
        except Exception:
            pass
        return

    if action == "next_wm":
        setup["step"] = 1
        await cq.answer()
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "back_audio":
        setup["step"] = 0
        await cq.answer()
        try:
            await cq.message.edit_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))
        except Exception:
            pass
        return

    # ---- Step 1: Watermark ----

    if action == "wm_toggle":
        setup["watermark_on"] = not setup["watermark_on"]
        await cq.answer(f"Watermark {'ON' if setup['watermark_on'] else 'OFF'}")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "wm_pos":
        setup["watermark_pos"] = val
        setup["watermark_on"]  = True
        await cq.answer(f"Position: {val.replace('_', ' ').title()}")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "wm_text":
        _wm_text_pending.add(uid)
        await cq.answer("Send your watermark text now.")
        try:
            await cq.message.edit_text(
                "✏️ **Send your watermark text** as the next message.\n\n"
                "Example: `Anime Cartoon`",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    if action == "audio_cycle":
        tracks = setup.get("detected_audio_tracks", [])
        at     = setup["audio_track"]
        if not at:
            setup["audio_track"] = [1] if tracks else []
        elif len(at) == 1 and at[0] < len(tracks):
            setup["audio_track"] = [at[0] + 1]
        else:
            setup["audio_track"] = []
        at_new = setup["audio_track"]
        label  = "All" if not at_new else f"Track {at_new[0]}"
        await cq.answer(f"Audio: {label}")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "auto_toggle":
        setup["auto_mode"] = not setup["auto_mode"]
        await cq.answer(f"Auto mode: {'ON' if setup['auto_mode'] else 'OFF'}")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "next_quality":
        setup["step"] = 2
        await cq.answer()
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step2(setup))
        except Exception:
            pass
        return

    if action == "quality":
        setup["quality"] = val
        await cq.answer(f"Quality: {val}p" if val != "original" else "Quality: Original")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step2(setup))
        except Exception:
            pass
        return

    if action == "back_step1":
        setup["step"] = 1
        await cq.answer()
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
        except Exception:
            pass
        return

    if action == "next_aspect":
        setup["step"] = 3
        await cq.answer()
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step3(setup))
        except Exception:
            pass
        return

    if action == "aspect":
        setup["aspect"] = val
        await cq.answer(f"Aspect: {val}")
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step3(setup))
        except Exception:
            pass
        return

    if action == "back_step2":
        setup["step"] = 2
        await cq.answer()
        try:
            await cq.message.edit_text(_setup_summary(setup), reply_markup=_kb_step2(setup))
        except Exception:
            pass
        return

    if action == "start":
        rec_setup_sessions.pop(uid, None)
        await cq.answer("Starting recording...")
        try:
            await cq.message.edit_text("⚙️ Starting recording with your settings...", reply_markup=None)
        except Exception:
            pass
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
    user_id = message.from_user.id
    _wm_text_pending.discard(user_id)
    setup = rec_setup_sessions.get(user_id)
    if not setup:
        return
    setup["watermark_text"] = (message.text or "").strip() or get_default_watermark()
    setup["watermark_on"]   = True
    try:
        setup_msg = await client.get_messages(setup["chat_id"], setup.get("setup_msg_id"))
        await setup_msg.edit_text(_setup_summary(setup), reply_markup=_kb_step1(setup))
    except Exception:
        pass
    await message.reply_text(f"✅ Watermark text set to: `{setup['watermark_text']}`")


# ---------------------------------------------------------------------------
# Cookies management
# ---------------------------------------------------------------------------

@app.on_message(filters.command("set_cookies") & AUTH)
async def set_cookies_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    pending_cookies_users[user_id] = time.time()
    await message.reply_text(
        "📎 **Send your `cookies.txt` file** (Netscape HTTP Cookie File format).\n\n"
        "Export it from your browser using a cookies extension.\n"
        "You have **5 minutes** to send the file."
    )


@app.on_message(filters.command("cookies_status") & AUTH)
async def cookies_status_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    await message.reply_text(_cookies_summary(user_id))


@app.on_message(filters.command("del_cookies") & AUTH)
async def del_cookies_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    path    = _user_cookies_path(user_id)
    if os.path.exists(path):
        try:
            os.remove(path)
            await message.reply_text("✅ Cookies deleted.")
        except Exception as e:
            await message.reply_text(f"Failed to delete cookies: `{e}`")
    else:
        await message.reply_text("No cookies stored.")


@app.on_message(filters.private & filters.document & AUTH, group=0)
async def cookies_document_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in pending_cookies_users:
        return

    prompt_time = pending_cookies_users.get(user_id, 0)
    if time.time() - prompt_time > _COOKIE_PROMPT_TTL_SEC:
        pending_cookies_users.pop(user_id, None)
        return await message.reply_text("Cookie upload window expired. Run /set_cookies again.")

    pending_cookies_users.pop(user_id, None)
    doc = message.document
    if not doc:
        return

    filename = (doc.file_name or "").lower()
    if not (filename.endswith(".txt") or filename.endswith(".cookies")):
        return await message.reply_text(
            "Please send a `.txt` file in Netscape cookie format."
        )
    if doc.file_size and doc.file_size > _MAX_COOKIE_FILE_BYTES:
        return await message.reply_text(
            f"File too large ({doc.file_size // 1024} KB). Max is 2 MB."
        )

    status = await message.reply_text("⬇️ Downloading cookie file...")
    try:
        tmp_path = _user_cookies_path(user_id) + ".tmp"
        await message.download(file_name=tmp_path)
        with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
            header = f.read(64)
        if _NETSCAPE_HEADER not in header:
            os.remove(tmp_path)
            return await status.edit_text(
                "**Invalid format.**\n\nThe file must start with:\n"
                "`# Netscape HTTP Cookie File`\n\n"
                "Export cookies from your browser using a cookies extension."
            )
        os.replace(tmp_path, _user_cookies_path(user_id))
        await status.edit_text(f"✅ Cookies saved!\n\n{_cookies_summary(user_id)}")
    except Exception as e:
        LOG.error(f"Cookie upload failed for {user_id}: {e}")
        try:
            await status.edit_text(f"Failed to save cookies: `{e}`")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /download — OTT downloader with manifest-probed quality + multi-audio wizard
# ---------------------------------------------------------------------------

@app.on_message(filters.command("download") & AUTH)
async def download_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /download. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("⏳ You already have an active job. Use /statusme or /cancelme.")
    if len(message.command) < 2:
        return await message.reply_text(
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

    probe_msg = await message.reply_text(
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

    if cq.from_user.id != uid:
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
                    await orig_message.reply_text(f"❌ Download crashed: `{_ex}`")
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
    if cq.from_user.id != uid:
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
    user_id = message.from_user.id
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

@app.on_message(filters.command("compress") & AUTH)
async def compress_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /compress. Run /verify.")
    if user_id in user_tasks or user_id in compress_jobs:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Send a video with** `/compress` **as the caption**, or reply to a video with `/compress`."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"cmp_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("⬇️ Downloading source video...")

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
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
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /Compressadvance. Run /verify.")
    if user_id in user_tasks or user_id in compress_jobs:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

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

@app.on_message(filters.command("reclink") & AUTH)
async def reclink_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /reclink. Run /verify.")
    if user_id in user_tasks or user_id in reclink_jobs:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")
    if len(message.command) < 2:
        return await message.reply_text(
            "**Invalid format.**\n\n"
            "Usage: `/reclink <player_or_webpage_url> HH:MM:SS <filename>`\n"
            "Example: `/reclink https://embed.example.com/live/abc 00:30:00 MyShow`\n\n"
            "Use this when the page **runs JavaScript** to load the stream."
        )

    params    = " ".join(message.command[1:])
    parts     = params.split(" ", 2)
    if len(parts) < 2:
        return await message.reply_text("Bad arguments. Use `/reclink <url> HH:MM:SS <filename>`.")
    page_url     = parts[0]
    timestamp    = parts[1]
    raw_filename = parts[2].strip() if len(parts) > 2 else DEFAULT_FILENAME

    msg = await message.reply_text(
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
            LOG.error(f"reclink extraction failed: {e}")
            try:
                await msg.edit_text(
                    f"**Extraction failed.**\n\n`{e}`\n\n"
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
            message.command = ["rec", chosen["url"], timestamp, raw_filename]
            await handle_record(client, message)
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

@app.on_message(filters.command(["screenshot", "Screenshot", "ss"]) & AUTH)
async def screenshot_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /screenshot. Run /verify.")
    if user_id in user_tasks or user_id in ss_jobs:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Send a video with** `/screenshot` **as the caption**, or reply to a video with `/screenshot`."
        )

    save_dir = join(DOWNLOAD_DIRECTORY, f"ss_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("Downloading source video...")

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        await status.edit_text("Probing video...")
        info = await _ffprobe_video(dl_path)
        if info["duration"] <= 0:
            raise Exception("Could not determine video duration.")

        state = {
            "src_path":    dl_path,
            "save_dir":    save_dir,
            "duration":    info["duration"],
            "video_height": info["video_height"],
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
        from logic import SS_MIN, SS_MAX
        if not (SS_MIN <= n <= SS_MAX):
            return await cq.answer("Out of range.", show_alert=True)
        await cq.answer(f"Generating {n} screenshots...")
        asyncio.create_task(run_screenshots(client, cq.message, state, n))
        return

    await cq.answer()


# ---------------------------------------------------------------------------
# /trim — cut a portion of a replied video
# ---------------------------------------------------------------------------

@app.on_message(filters.command("trim") & AUTH)
async def trim_cmd(client: Client, message: Message):
    user_id = message.from_user.id
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
                   f"Channel: @{SUPPORT_CHANNEL}\n\n{retention_note}")

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
    "top_left":    "↖️ Top Left",
    "top_center":  "⬆️ Top Center",
    "top_right":   "↗️ Top Right",
    "center":      "🎯 Center",
    "bottom_left": "↙️ Bottom Left",
    "bottom_right":"↘️ Bottom Right",
}

# FFmpeg overlay coords for image watermarks (per position)
_WM_POS_IMG = {
    "top_left":    "20:20",
    "top_center":  "(W-w)/2:20",
    "top_right":   "W-w-20:20",
    "center":      "(W-w)/2:(H-h)/2",
    "bottom_left": "20:H-h-20",
    "bottom_right":"W-w-20:H-h-20",
}

# FFmpeg drawtext coords for text watermarks (per position)
_WM_POS_TXT = {
    "top_left":    "x=20:y=20",
    "top_center":  "x=(w-tw)/2:y=20",
    "top_right":   "x=w-tw-20:y=20",
    "center":      "x=(w-tw)/2:y=(h-th)/2",
    "bottom_left": "x=20:y=h-th-20",
    "bottom_right":"x=w-tw-20:y=h-th-20",
}


def _wm_pos_keyboard(uid: int, selected: str) -> InlineKeyboardMarkup:
    """6-position picker + Start / Cancel for /Watermark."""
    def btn(key: str) -> InlineKeyboardButton:
        label = ("✅ " if key == selected else "") + _WM_POS_LABELS[key]
        return InlineKeyboardButton(label, callback_data=f"wmp:{uid}:{key}")
    return InlineKeyboardMarkup([
        [btn("top_left"),    btn("top_center"),   btn("top_right")],
        [btn("center")],
        [btn("bottom_left"), btn("bottom_right")],
        [
            InlineKeyboardButton("▶️ Start",  callback_data=f"wmp:{uid}:start"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"wmp:{uid}:cancel"),
        ],
    ])


@app.on_message(filters.command(["Watermark", "watermark", "wm"]) & AUTH)
async def watermark_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /Watermark. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with `/Watermark <text>`.\n\n"
            "Example: `/Watermark @LittleSinghamChannel`\n\n"
            "⚠️ Watermark will appear in the **last 2 minutes** of the video."
        )

    parts   = message.text.split(None, 1)
    wm_text = (parts[1].strip() if len(parts) > 1 else "") or get_default_watermark()

    # Clear any stale picker session for this user before creating a new one
    _wm_pos_sessions.pop(user_id, None)

    default_pos = "bottom_right"
    _wm_pos_sessions[user_id] = {
        "src_msg": src_msg,
        "wm_text": wm_text,
        "pos":     default_pos,
    }

    await message.reply_text(
        f"**🎨 Watermark Position**\n\n"
        f"Text: `{wm_text}`\n\n"
        f"Watermark kahan lagana chahte hain? Ek position choose karein, phir **Start** dabayein.",
        reply_markup=_wm_pos_keyboard(user_id, default_pos),
    )


@app.on_callback_query(filters.regex(r"^wmp:(\d+):(top_left|top_center|top_right|center|bottom_left|bottom_right|start|cancel)$"))
async def wmp_callback(client: Client, cq: CallbackQuery):
    uid    = int(cq.matches[0].group(1))
    action = cq.matches[0].group(2)

    if cq.from_user.id != uid:
        return await cq.answer("Yeh aapka session nahi hai.", show_alert=True)

    session = _wm_pos_sessions.get(uid)
    if not session:
        await cq.answer("Session expire ho gaya. Dobara /Watermark karein.", show_alert=True)
        try:
            await cq.message.delete()
        except Exception:
            pass
        return

    if action == "cancel":
        _wm_pos_sessions.pop(uid, None)
        await cq.answer("Cancelled.")
        try:
            await cq.message.edit_text("❌ Watermark cancelled.")
        except Exception:
            pass
        return

    if action != "start":
        # User tapped a position button — update selection
        session["pos"] = action
        await cq.answer(_WM_POS_LABELS[action])
        try:
            await cq.message.edit_reply_markup(_wm_pos_keyboard(uid, action))
        except Exception:
            pass
        return

    # ── Start pressed ──────────────────────────────────────────────────────
    if uid in user_tasks:
        return await cq.answer("Aapka ek job already chal raha hai. /cancelme use karein.", show_alert=True)

    _wm_pos_sessions.pop(uid, None)
    src_msg  = session["src_msg"]
    wm_text  = session["wm_text"]
    pos      = session["pos"]
    pos_label= _WM_POS_LABELS.get(pos, pos)

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
    user_tasks[uid] = True
    _success = False

    try:
        dl_path = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        await status.edit_text(
            f"🔍 Probing video…\n\nWatermark: `{wm_text}`\nPosition: {pos_label}"
        )
        info = await _ffprobe_video(dl_path)
        dur  = info["duration"]
        if dur <= 0:
            raise Exception("Could not determine video duration — ffprobe failed.")

        out_path = join(save_dir, f"wm_output_{int(time.time())}.mkv")
        wm_start = max(0, dur - 120)

        # Try image watermark first, fallback to text
        await status.edit_text(
            f"⬇️ Fetching watermark image…\n\nPosition: {pos_label}"
        )
        has_img = await _async_ensure_watermark_img()
        wm_img  = _watermark_img_path() if has_img else None

        if wm_img:
            img_pos = _WM_POS_IMG.get(pos, "W-w-20:H-h-20")
            _wm_sz  = get_watermark_size()
            fc = (f"[1:v]scale={_wm_sz}:-1[_wm];"
                  f"[0:v][_wm]overlay={img_pos}"
                  f":enable='gte(t,{wm_start})'[_out]")
            await status.edit_text(
                f"🎨 Burning watermark image…\n\nPosition: {pos_label}\n_This may take a moment._"
            )
            rc, _out, err = await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-i {shlex.quote(dl_path)} '
                f'-i {shlex.quote(wm_img)} '
                f'-filter_complex {shlex.quote(fc)} '
                f'-map [_out] -map 0:a? '
                f'-c:v libx264 -preset veryfast -crf 20 '
                f'-c:a copy '
                f'{shlex.quote(out_path)}'
            )
            wm_label = f"Image · {pos_label} · last 2 min"
        else:
            safe_txt  = wm_text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
            xy        = _WM_POS_TXT.get(pos, "x=w-tw-20:y=h-th-20")
            vf_filter = (
                f"drawtext=text='{safe_txt}':fontsize=28:fontcolor=white"
                f":box=1:boxcolor=black@0.4:boxborderw=4"
                f":{xy}:enable='gte(t,{wm_start})'"
            )
            await status.edit_text(
                f"🎨 Burning watermark `{wm_text}`…\n\nPosition: {pos_label}\n_This may take a moment._"
            )
            rc, _out, err = await runcmd(
                f'ffmpeg -hide_banner -loglevel error -nostats -y '
                f'-i {shlex.quote(dl_path)} '
                f'-map 0:v:0 -map 0:a? '
                f'-vf {shlex.quote(vf_filter)} '
                f'-c:v libx264 -preset veryfast -crf 20 '
                f'-c:a copy '
                f'{shlex.quote(out_path)}'
            )
            wm_label = f"`{wm_text}` · {pos_label} · last 2 min"

        if rc != 0:
            raise Exception(f"FFmpeg watermark failed:\n{err.strip()[-1500:]}")

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
            f"Channel: @{SUPPORT_CHANNEL}\n\n"
            f"_Auto-deleted from server after {_retention_label()}._"
        )
        _dest = await _await_upload_choice(uid, status, f"Watermarked: `{out_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, uid, _dest, status, out_path, caption, dur,
                thumb_path=(thumb if thumb_ok else None), save_dir=save_dir,
            )
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
        user_tasks.pop(uid, None)
        if save_dir and os.path.exists(save_dir):
            if _success:
                schedule_retention_cleanup(save_dir)
            else:
                _safe_rmtree(save_dir)


# ---------------------------------------------------------------------------
# /audiotrack — inject audio metadata lock without re-encoding (instant)
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["audiotrack", "AudioTrack", "at"]) & AUTH)
async def audiotrack_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /audiotrack. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")

    src_msg, src_media = _get_replied_video(message)
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with `/audiotrack <name>`.\n\n"
            "Example: `/audiotrack @LittleSinghamChannel`\n\n"
            "✅ No re-encoding — runs in 2-3 seconds even on large files.\n"
            "🔒 Wipes global metadata, injects your brand into all audio tracks."
        )

    parts    = message.text.split(None, 1)
    at_name  = parts[1].strip() if len(parts) > 1 else get_audio_brand_name()
    if not at_name:
        at_name = get_audio_brand_name()

    save_dir = join(DOWNLOAD_DIRECTORY, f"at_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status   = await message.reply_text("⬇️ Downloading video…")
    user_tasks[user_id] = True

    try:
        dl_path  = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        out_path = join(save_dir, f"locked_{int(time.time())}.mkv")

        await status.edit_text(
            f"🔒 Locking audio track metadata…\n"
            f"Name: `{at_name}`\n"
            f"_No re-encode — this will finish in seconds._"
        )

        # Build metadata args for 3 audio tracks
        meta_args = []
        for i in range(3):
            meta_args += [
                f"-metadata:s:a:{i}", f"title={at_name}",
                f"-metadata:s:a:{i}", f"handler_name={at_name}",
            ]
        meta_str = " ".join(shlex.quote(a) for a in meta_args)

        rc, _out, err = await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-i {shlex.quote(dl_path)} '
            f'-map 0 -c copy '
            f'-map_metadata -1 '          # wipe global metadata block
            f'{meta_str} '               # inject stream-level audio brand
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
            f"Channel: @{SUPPORT_CHANNEL}\n\n"
            f"_Visible in VLC → Track Info, MX Player, Telegram audio selector._\n"
            f"_Auto-deleted from server after {_retention_label()}._"
        )
        _dest = await _await_upload_choice(user_id, status, f"Audio locked: `{out_mb:.1f} MB`")
        if _dest != "cancel":
            await _run_upload_destination(
                client, user_id, _dest, status, out_path, caption, dur,
                thumb_path=(thumb if thumb_ok else None), save_dir=save_dir,
            )
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

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
    user_id = message.from_user.id
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

@app.on_message(filters.command("merge") & AUTH)
async def merge_cmd(client: Client, message: Message):
    user_id = message.from_user.id
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
    user_id = message.from_user.id
    sess    = merge_sessions.pop(user_id, None)
    if not sess:
        return await message.reply_text("No active merge session.")
    _safe_rmtree(sess["save_dir"])
    await message.reply_text("🧩 Merge session cancelled — collected videos discarded.")


@app.on_message(filters.command("merge_done") & AUTH)
async def merge_done_cmd(client: Client, message: Message):
    user_id = message.from_user.id
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
    user_id = message.from_user.id
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
    user_id = message.from_user.id

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
    user_id = message.from_user.id
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

    if cq.from_user.id != uid:
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
    await cq.message.reply_text("**Browse channels**\n\nPick a channel:", reply_markup=_channel_root_kb())
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id
    if not (is_owner(uid) or is_admin(uid)):
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

@app.on_message(filters.command(["premium_add", "addpremium", "padd"]) & AUTH)
async def premium_add_cmd(_client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id if message.from_user else 0
    if not (is_owner(uid) or is_admin(uid)):
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
    uid = message.from_user.id if message.from_user else 0
    if not (is_owner(uid) or is_admin(uid)):
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
    uid  = message.from_user.id
    now  = datetime.now(tz)

    # Owner / admin → always unlimited
    if uid in OWNER_IDS:
        return await message.reply_text(
            "👑 **Tumhara Status: Owner**\n\n"
            "✅ Unlimited access — sab kuch khula hai.\n\n"
            "JioTV: Unlimited recording"
        )
    if is_admin(uid):
        return await message.reply_text(
            "🛡️ **Tumhara Status: Admin**\n\n"
            "✅ Unlimited access — sab kuch khula hai.\n\n"
            "JioTV: Unlimited recording"
        )

    data  = load_premium()
    entry = data.get("users", {}).get(str(uid))

    if not entry:
        return await message.reply_text(
            "📦 **Tumhara Plan: Free**\n\n"
            "❌ Premium nahi liya abhi tak.\n\n"
            "**Free limits:**\n"
            "• JioTV recording: max 10 seconds\n"
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
            "• JioTV recording: ✅ Unlimited",
        ]
    else:
        lines += [
            "• JioTV recording: ⏱ max 10 seconds (free limit)",
            "\nPremium renew ke liye admin se baat karo! 🚀",
        ]

    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# DM Relay — forward user messages to owner; relay owner replies back
# ---------------------------------------------------------------------------

_RELAY_MAP_MAX = 2000   # cap memory usage
_OWNER_FILTER  = filters.user(list(OWNER_IDS) if OWNER_IDS else [0])


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
    user_id = message.from_user.id
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
# Entry point (merged from main.py)
# =============================================================================

from pyrogram.types import BotCommand

_BOT_COMMANDS = [
    BotCommand("start",          "Welcome message"),
    BotCommand("help",           "All commands and usage guide"),
    BotCommand("rec",            "Record HLS/M3U8/DASH stream (wizard)"),
    BotCommand("drec",           "Direct record — no wizard, instant start"),
    BotCommand("reclink",        "Auto-extract stream from a web page"),
    BotCommand("download",       "Download from OTT platforms"),
    BotCommand("compress",        "Compress a video (reply to video)"),
    BotCommand("compressadvance", "Compress: 576p · 350 MB · Multi audio (all users)"),
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
    BotCommand("Admin_add",      "Add an admin by user ID (owner only)"),
    BotCommand("Admin_delete",   "Remove an admin by user ID (owner only)"),
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
    BotCommand("di",              "Quick JioTV catchup record for today (channel -t time - time file.mkv)"),
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


# ---------------------------------------------------------------------------
# JioTV — Login + Channel List + Live Recording (via bot/jiotv.py module)
# ---------------------------------------------------------------------------

_jiotv_cat_cache: dict = {}
_jiotv_ch_cache:  dict = {}


# /jiotvlogin <phone>  — ADMIN ONLY
@app.on_message(filters.command(["jiotvlogin", "JioTVLogin", "login"]) & _OWNER_FILTER)
async def jiotv_login_cmd(client: Client, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:** `/jiotvlogin <10-digit mobile number>`\n\n"
            "Example: `/jiotvlogin 9876543210`\n\n"
            "_(Sirf admin use kare — ek baar login karo, baaki sab use kar sakte hain)_"
        )
    phone = args[1].strip()
    if not phone.isdigit() or len(phone) != 10:
        return await message.reply_text("❌ 10-digit number dena hai, country code nahi.")

    status = await message.reply_text(f"📲 OTP bhej raha hoon **{phone}** pe…")
    result = await asyncio.get_event_loop().run_in_executor(None, jiotv.send_otp, phone)
    if result["success"]:
        await status.edit_text(
            f"✅ {result['message']}\n\nOTP mila? Send karo:\n`/jiotvotp <6-digit OTP>`"
        )
    else:
        await status.edit_text(f"❌ {result['message']}")


# /jiotvotp <otp>  — ADMIN ONLY
@app.on_message(filters.command(["jiotvotp", "JioTVOTP", "otp"]) & _OWNER_FILTER)
async def jiotv_otp_cmd(client: Client, message: Message):
    args = message.command
    if len(args) < 2:
        return await message.reply_text("**Usage:** `/jiotvotp <6-digit OTP>`")
    otp    = args[1].strip()
    status = await message.reply_text("🔐 OTP verify kar raha hoon…")
    result = await asyncio.get_event_loop().run_in_executor(None, jiotv.verify_otp, otp)
    if result["success"]:
        await status.edit_text(
            f"✅ **JioTV Login Successful!**\n\n{result['message']}\n\n"
            "Commands:\n"
            "• `/jiochannels` — channel list\n"
            "• `/jiorec <channel> <duration>` — live record\n\n"
            "Example: `/jiorec Pogo 01:00:00`"
        )
    else:
        await status.edit_text(f"❌ {result['message']}")


# /jiochannels [category]
@app.on_message(filters.command(["jiochannels", "JioChannels", "channels"]) & AUTH)
async def jiotv_channels_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if not jiotv.is_logged_in():
        return await message.reply_text(
            "❌ JioTV mein login nahi hai.\nAdmin se kaho `/jiotvlogin` kare."
        )
    status   = await message.reply_text("📡 JioTV channels fetch kar raha hoon…")
    channels = await asyncio.get_event_loop().run_in_executor(None, jiotv.get_channels)
    if not channels:
        return await status.edit_text(
            "❌ Channels load nahi hue.\nDobara login karo: `/jiotvlogin <phone>`"
        )

    cats: dict = {}
    for ch in channels:
        cat = ch.get("channelCategoryName") or ch.get("category") or "Other"
        cats.setdefault(cat, []).append(ch)

    args = message.command
    if len(args) >= 2:
        query_cat = " ".join(args[1:]).strip().lower()
        matched   = {k: v for k, v in cats.items() if query_cat in k.lower()}
        if not matched:
            return await status.edit_text(
                f"❌ Category `{query_cat}` nahi mili.\n\nAvailable: {', '.join(list(cats.keys())[:20])}"
            )
        cats = matched

    _jiotv_cat_cache[uid] = cats
    cat_names = sorted(cats.keys())
    rows, row = [], []
    for cat in cat_names[:24]:
        count = len(cats[cat])
        row.append(InlineKeyboardButton(f"{cat} ({count})", callback_data=f"jtv_cat:{uid}:{cat[:20]}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)

    await status.edit_text(
        f"📺 **JioTV — {len(channels)} Channels**\n\nCategory select karo:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@app.on_callback_query(filters.regex(r"^jtv_cat:"))
async def jiotv_cat_cb(client: Client, cq: CallbackQuery):
    parts    = cq.data.split(":", 2)
    uid, cat = int(parts[1]), parts[2]
    if cq.from_user.id != uid:
        return await cq.answer("Yeh tumhara session nahi hai.", show_alert=True)
    cats     = _jiotv_cat_cache.get(uid, {})
    full_cat = next((k for k in cats if k[:20] == cat), cat)
    channels = cats.get(full_cat, [])
    if not channels:
        return await cq.answer("No channels in this category.", show_alert=True)
    ch_map = _jiotv_ch_cache.setdefault(uid, {})
    for ch in channels:
        name = ch.get("channelName") or ch.get("channel_name") or ""
        ch_map[name.lower()] = ch
    rows, row = [], []
    for ch in channels[:48]:
        name  = ch.get("channelName") or ch.get("channel_name") or "?"
        ch_id = str(ch.get("channel_id") or ch.get("id") or "")
        row.append(InlineKeyboardButton(name, callback_data=f"jtv_ch:{uid}:{ch_id}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Back", callback_data=f"jtv_back:{uid}")])
    await cq.message.edit_text(
        f"📺 **{full_cat}** — {len(channels)} channels\n\nRecord karne ke liye:\n`/jiorec <channel name> <duration>`",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    await cq.answer()


@app.on_callback_query(filters.regex(r"^jtv_back:"))
async def jiotv_back_cb(client: Client, cq: CallbackQuery):
    uid  = int(cq.data.split(":")[1])
    if cq.from_user.id != uid:
        return await cq.answer("Yeh tumhara session nahi hai.", show_alert=True)
    cats      = _jiotv_cat_cache.get(uid, {})
    cat_names = sorted(cats.keys())
    rows, row = [], []
    for cat in cat_names[:24]:
        count = len(cats[cat])
        row.append(InlineKeyboardButton(f"{cat} ({count})", callback_data=f"jtv_cat:{uid}:{cat[:20]}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await cq.message.edit_text(
        "📺 **JioTV — Category select karo:**",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    await cq.answer()


@app.on_callback_query(filters.regex(r"^jtv_ch:"))
async def jiotv_ch_cb(client: Client, cq: CallbackQuery):
    parts  = cq.data.split(":", 2)
    uid, ch_id = int(parts[1]), parts[2]
    if cq.from_user.id != uid:
        return await cq.answer("Yeh tumhara session nahi hai.", show_alert=True)
    ch_map   = _jiotv_ch_cache.get(uid, {})
    ch_entry = next((v for v in ch_map.values()
                     if str(v.get("channel_id") or v.get("id") or "") == ch_id), None)
    ch_name  = (ch_entry.get("channelName") or ch_entry.get("channel_name") or ch_id) if ch_entry else ch_id
    await cq.answer(f"✅ {ch_name} selected!")
    await cq.message.reply_text(
        f"📺 **{ch_name}** select hua!\n\n"
        f"Record karne ke liye:\n`/jiorec {ch_name} 01:00:00`\n\n"
        f"_(Duration: HH:MM:SS)_"
    )


# /jiorec <channel name> <duration>
@app.on_message(filters.command(["jiorec", "JioRec"]) & AUTH)
async def jiotv_rec_cmd(client: Client, message: Message):
    uid = message.from_user.id
    try:
        if not jiotv.is_logged_in():
            return await message.reply_text(
                "❌ **JioTV mein login nahi hai.**\n\n"
                "Admin se kaho pehle yeh kare:\n"
                "`/jiotvlogin <10-digit phone number>`\n\n"
                "Example: `/jiotvlogin 9876543210`"
            )
        args = message.command
        if len(args) < 3:
            return await message.reply_text(
                "**Usage:** `/jiorec <channel name> <duration>`\n\n"
                "Example: `/jiorec Pogo 01:00:00`\n"
                "Channel list: `/jiochannels`"
            )

        duration_str = args[-1].strip()
        ch_name_raw  = " ".join(args[1:-1]).strip()
        duration_sec = _parse_duration_token(duration_str)
        if duration_sec <= 0:
            return await message.reply_text("❌ Duration galat. Format: `HH:MM:SS` ya `30m`")

        # Tier-based recording limits
        # Owner: unlimited | Premium: max 2 hours | Free/Verified: max 1 hour
        _is_owner   = uid in OWNER_IDS
        _is_premium = is_premium(uid)
        if not _is_owner:
            if _is_premium:
                _max_rec = 2 * 3600   # 2 hours
                _limit_label = "2 hours (Premium)"
            else:
                _max_rec = 3600       # 1 hour
                _limit_label = "1 hour (Free/Verified)"
            if duration_sec > _max_rec:
                return await message.reply_text(
                    f"⏱ **Recording limit crossed!**\n\n"
                    f"Your plan allows max **{_limit_label}** per recording.\n"
                    f"You requested: `{_seconds_to_hms(duration_sec)}`\n\n"
                    f"{'Upgrade to Premium for 2-hour recordings.' if not _is_premium else ''}"
                    f"\nUse `/contact` to upgrade. 🚀"
                )

        status   = await message.reply_text(f"🔍 JioTV mein **{ch_name_raw}** dhundh raha hoon…")
        channels = await asyncio.get_event_loop().run_in_executor(None, jiotv.get_channels)

        ch_name_lower = ch_name_raw.lower()
        matched = None
        for ch in channels:
            name = (ch.get("channelName") or ch.get("channel_name") or "").lower()
            if name == ch_name_lower:
                matched = ch; break
        if not matched:
            for ch in channels:
                name = (ch.get("channelName") or ch.get("channel_name") or "").lower()
                if ch_name_lower in name:
                    matched = ch; break

        if not matched:
            similar = [(ch.get("channelName") or ch.get("channel_name") or "")
                       for ch in channels if ch_name_lower[:4] in (ch.get("channelName") or "").lower()][:6]
            hint = "\n".join(f"• {s}" for s in similar) if similar else "Channel list: `/jiochannels`"
            return await status.edit_text(f"❌ Channel **{ch_name_raw}** nahi mila.\n\nShayad:\n{hint}")

        ch_id    = str(matched.get("channel_id") or matched.get("id") or "")
        ch_label = matched.get("channelName") or matched.get("channel_name") or ch_name_raw

        await status.edit_text(f"📡 **{ch_label}** ka stream URL fetch kar raha hoon…")
        result = await asyncio.get_event_loop().run_in_executor(None, jiotv.get_stream_url, ch_id)
        if not result["success"]:
            return await status.edit_text(
                f"❌ Stream URL nahi mila: {result['message']}\n\nDobara login karo: `/jiotvlogin <phone>`"
            )

        stream_url = result["url"]
        LOG.info("JioTV stream for %s (id=%s): %s", ch_label, ch_id, stream_url[:80])
        dur_hms  = _seconds_to_hms(duration_sec)
        filename = re.sub(r"[^\w\s-]", "", ch_label).strip().replace(" ", "_")

        setup = {
            "user_id": uid, "chat_id": message.chat.id,
            "url": stream_url, "timestamp": duration_sec,
            "filename": filename, "watermark_on": True,
            "watermark_pos": "bottom_right",
            "watermark_text": get_default_watermark(),
            "audio_track": [], "auto_mode": False,
            "quality": "original", "aspect": "none",
            "step": 0, "detected_audio_tracks": [],
            "effective_url": stream_url, "orig_msg": message,
            "is_hls": True,
            "jiotv": True, "jiotv_channel": ch_label,
        }
        rec_setup_sessions[uid] = setup
        await status.edit_text(
            f"✅ **{ch_label}** — Stream ready!\n"
            f"⏱ Duration: `{dur_hms}`\n📡 Source: JioTV Live\n\n⚙️ Setup wizard khul raha hai…"
        )
        tracks = await _probe_audio_tracks(stream_url)
        setup["detected_audio_tracks"] = tracks
        wizard_msg = await message.reply_text(_audio_step_text(setup), reply_markup=_kb_audio_step(setup))
        setup["setup_msg_id"] = wizard_msg.id
    except Exception as e:
        LOG.exception("jiotv_rec_cmd error uid=%s: %s", uid, e)
        try:
            await message.reply_text(
                f"❌ **Kuch galat ho gaya:**\n`{e}`\n\n"
                f"Agar yeh baar baar aaye toh `/jiotvlogin` se dobara login karo."
            )
        except Exception:
            pass


# /jiotvlogout  — ADMIN ONLY
@app.on_message(filters.command(["jiotvlogout", "JioTVLogout"]) & _OWNER_FILTER)
async def jiotv_logout_cmd(client: Client, message: Message):
    if not jiotv.is_logged_in():
        return await message.reply_text("❌ JioTV mein login nahi hai.")
    jiotv.logout()
    await message.reply_text("✅ JioTV logout ho gaya. Ab koi bhi JioTV use nahi kar sakta.")


# /jiotvstatus
@app.on_message(filters.command(["jiotvstatus", "JioTVStatus", "jiostatus"]) & AUTH)
async def jiotv_status_cmd(client: Client, message: Message):
    if not jiotv.is_logged_in():
        return await message.reply_text(
            "❌ JioTV login nahi hai.\nAdmin se kaho `/jiotvlogin <phone>` kare."
        )
    await message.reply_text(
        "✅ **JioTV Login Active**\n\n"
        "Commands:\n"
        "• `/jiochannels` — channel list\n"
        "• `/jiorec <channel> <duration>` — live record\n"
        "• `/jiotvlogout` — logout"
    )


# ---------------------------------------------------------------------------
# /dl  — JioTV catchup download
# Syntax:
#   /dl -Jiotv -c ChannelName -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM -n File
# ---------------------------------------------------------------------------

def _parse_dl_command(text: str) -> dict | None:
    """
    Parse /dl command arguments.
    Returns dict with keys: source, channel, begin_ts, end_ts, filename
    or None on parse failure.
    """
    import re as _re
    # Remove the command itself
    text = _re.sub(r"^/dl\s*", "", text, flags=_re.IGNORECASE).strip()

    # Extract source: -Jiotv or -Tplay (case-insensitive)
    src_match = _re.search(r"-([Jj]iotv)\b", text)
    if not src_match:
        return None
    source = "jiotv"

    # Extract -c <channel name>
    ch_match = _re.search(r"-c\s+(.+?)(?=\s+-[a-zA-Z])", text + " -z")
    if not ch_match:
        return None
    channel = ch_match.group(1).strip()

    # Extract -n <filename>
    fn_match = _re.search(r"-n\s+(.+?)(?=\s+-[a-zA-Z]|$)", text + " -z")
    if not fn_match:
        return None
    filename = fn_match.group(1).strip()

    # Extract -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM
    # Format: -t 02-07-2026 09:00 AM - 11:00 AM
    t_match = _re.search(
        r"-t\s+(\d{2}-\d{2}-\d{4})\s+(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*-\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])",
        text,
    )
    if not t_match:
        return None

    date_str   = t_match.group(1)      # DD-MM-YYYY
    start_time = t_match.group(2)      # HH:MM
    start_ampm = t_match.group(3).upper()  # AM/PM
    end_time   = t_match.group(4)      # HH:MM
    end_ampm   = t_match.group(5).upper()  # AM/PM

    try:
        import pytz as _pytz
        from datetime import datetime as _dt
        ist = _pytz.timezone("Asia/Kolkata")

        def _parse_time(date_s, time_s, ampm_s):
            dt = _dt.strptime(f"{date_s} {time_s} {ampm_s}", "%d-%m-%Y %I:%M %p")
            return ist.localize(dt)

        begin_dt = _parse_time(date_str, start_time, start_ampm)
        end_dt   = _parse_time(date_str, end_time,   end_ampm)

        # If end < begin, end is next day
        if end_dt <= begin_dt:
            from datetime import timedelta as _td
            end_dt += _td(days=1)

        begin_ts = int(begin_dt.timestamp())
        end_ts   = int(end_dt.timestamp())
    except Exception:
        return None

    duration_sec = end_ts - begin_ts
    if duration_sec <= 0 or duration_sec > 24 * 3600:
        return None

    return {
        "source":       source,
        "channel":      channel,
        "begin_ts":     begin_ts,
        "end_ts":       end_ts,
        "duration_sec": duration_sec,
        "filename":     filename,
        "date_str":     date_str,
        "start_time":   f"{start_time} {start_ampm}",
        "end_time":     f"{end_time} {end_ampm}",
    }


# ---------------------------------------------------------------------------
# /di  — Quick JioTV catchup for TODAY (simpler syntax)
# Format: /di <channel> -t <HH:MM AM/PM> - <HH:MM AM/PM> <filename.mkv>
# Example: /di Pogo -t 12:00PM - 01:00PM ls.mkv
# ---------------------------------------------------------------------------

def _parse_di_command(text: str) -> dict | None:
    """
    Parse /di command.
    Format: /di <channel> -t HH:MM AM/PM - HH:MM AM/PM <filename.mkv>
    Returns dict with channel, begin_ts, end_ts, duration_sec, filename,
    plus h24_start, h24_end (24-hour strings for proxy) or None.
    """
    import re as _re
    import pytz as _pytz
    from datetime import datetime as _dt, timedelta as _td

    ist = _pytz.timezone("Asia/Kolkata")
    today = _dt.now(ist).strftime("%d-%m-%Y")

    # Strip command prefix
    body = _re.sub(r"^/di\s*", "", text, flags=_re.IGNORECASE).strip()
    if not body:
        return None

    # Extract time range: -t HH:MM AM/PM - HH:MM AM/PM
    # Flexible: space or no-space between time and AM/PM
    t_match = _re.search(
        r"-t\s+(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*-\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])",
        body,
    )
    if not t_match:
        return None

    start_time   = t_match.group(1)
    start_ampm   = t_match.group(2).upper()
    end_time     = t_match.group(3)
    end_ampm     = t_match.group(4).upper()

    # Convert to 24-hour format (HH:MM)
    def _to_24h(t, ap):
        h, m = map(int, t.split(":"))
        if ap == "PM" and h != 12:
            h += 12
        elif ap == "AM" and h == 12:
            h = 0
        return f"{h:02d}:{m:02d}"

    h24_start = _to_24h(start_time, start_ampm)
    h24_end   = _to_24h(end_time,   end_ampm)

    # Build IST datetime objects for today
    try:
        begin_dt = _dt.strptime(f"{today} {start_time} {start_ampm}", "%d-%m-%Y %I:%M %p")
        begin_dt = ist.localize(begin_dt)
        end_dt   = _dt.strptime(f"{today} {end_time} {end_ampm}", "%d-%m-%Y %I:%M %p")
        end_dt   = ist.localize(end_dt)
    except Exception:
        return None

    # If end <= begin, end is next day
    if end_dt <= begin_dt:
        end_dt += _td(days=1)

    begin_ts   = int(begin_dt.timestamp())
    end_ts     = int(end_dt.timestamp())
    duration_sec = end_ts - begin_ts
    if duration_sec <= 0 or duration_sec > 24 * 3600:
        return None

    # Remove the time portion from body to isolate channel + filename
    body_no_time = body[:t_match.start()].strip() + " " + body[t_match.end():].strip()
    body_no_time = body_no_time.strip()

    # Last token = filename (must contain .mkv or .mp4, else default)
    parts = body_no_time.split()
    if not parts:
        return None

    # Heuristic: last token with an extension is filename
    filename = None
    channel_tokens = parts
    for i in range(len(parts) - 1, -1, -1):
        p = parts[i]
        if "." in p and len(p.split(".")[-1]) <= 4:
            filename = p
            channel_tokens = parts[:i]
            break

    if not filename:
        # No filename found — default to channel name + .mkv
        channel_tokens = parts
        filename = "catchup.mkv"

    channel = " ".join(channel_tokens).strip()
    if not channel:
        return None

    return {
        "channel":       channel,
        "begin_ts":      begin_ts,
        "end_ts":        end_ts,
        "duration_sec":  duration_sec,
        "filename":      filename,
        "h24_start":     h24_start,
        "h24_end":       h24_end,
        "today":         today,
        "start_str":     f"{start_time} {start_ampm}",
        "end_str":       f"{end_time} {end_ampm}",
    }


async def _di_proxy_fetch(channel_id: str, h24_start: str, h24_end: str,
                          ch_label: str, tmp_dir: str) -> str | None:
    """
    Fetch HLS stream via jitendraunatti's public JioTV proxy and record with FFmpeg.
    Returns output file path on success, None on failure.
    """
    proxy_url = (
        "https://tvjio.iptvbd.xyz"
        f"/{channel_id}"
        f"_{h24_start.replace(':', '')}"
        f"_{h24_end.replace(':', '')}"
        ".mp4"                        # proxy always returns MP4/HLS container
    )

    out_file = os.path.join(tmp_dir, "catchup_proxy.mkv")
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-headers", "User-Agent: Mozilla/5.0 (Linux; Android 10; K)\r\n",
        "-i", proxy_url,
        "-c", "copy",
        "-f", "matroska",
        "-avoid_negative_ts", "make_zero",
        out_file,
    ]

    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0 or not os.path.exists(out_file) or os.path.getsize(out_file) < 1024:
        err = (stderr or b"").decode(errors="ignore")[-300:]
        LOG.warning("di proxy ffmpeg failed for %s: %s", ch_label, err)
        return None
    return out_file


@app.on_message(filters.command(["di", "DI"]) & AUTH)
async def di_catchup_cmd(client: Client, message: Message):
    """
    /di <channel> -t HH:MM AM/PM - HH:MM AM/PM <filename.mkv>
    Quick JioTV catchup for today using public proxy or native API.
    """
    uid = message.from_user.id

    if uid in user_tasks:
        return await message.reply_text(
            "⏳ Tumhara ek kaam chal raha hai. Pehle /cancelme karo."
        )

    parsed = _parse_di_command(message.text or "")
    if not parsed:
        return await message.reply_text(
            "❌ **Syntax galat hai.**\n\n"
            "**Format:**\n"
            "`/di <channel> -t HH:MM AM/PM - HH:MM AM/PM <filename.mkv>`\n\n"
            "**Example:**\n"
            "`/di Pogo -t 12:00PM - 01:00PM ls.mkv`\n"
            "`/di Star Plus -t 09:00 AM - 11:00 AM morning_show.mkv`\n\n"
            "• Time 12-hour AM/PM format mein\n"
            "• Date aaj ki auto-pick hoti hai\n"
            "• Output MKV format mein"
        )

    ch_name      = parsed["channel"]
    begin_ts     = parsed["begin_ts"]
    end_ts       = parsed["end_ts"]
    duration_sec = parsed["duration_sec"]
    filename     = re.sub(r"[^\w\s.-]", "", parsed["filename"]).strip() or "catchup.mkv"
    if not filename.lower().endswith(".mkv"):
        filename += ".mkv"

    dur_label = _seconds_to_hms(duration_sec)

    status = await message.reply_text(
        f"🔍 **Quick Catchup — {ch_name}**\n"
        f"📅 Date: `{parsed['today']}`\n"
        f"🕐 Time: `{parsed['start_str']} → {parsed['end_str']}`\n"
        f"⏱ Duration: `{dur_label}`\n"
        f"⏳ Channel search kar raha hoon…"
    )

    # ---- login check ----
    if not jiotv.is_logged_in():
        return await status.edit_text(
            "❌ JioTV login nahi hai.\n"
            "Admin se kaho: `/login <phone>`"
        )

    # ---- channel search ----
    matches = await asyncio.get_event_loop().run_in_executor(
        None, jiotv.search_channel, ch_name
    )
    if not matches:
        return await status.edit_text(
            f"❌ Channel **{ch_name}** nahi mila.\n\n"
            f"`/channels {ch_name}` se list dekho."
        )

    matched = matches[0]
    ch_id    = str(matched.get("id") or matched.get("channelId") or matched.get("channel_id") or "")
    ch_label = matched.get("name") or matched.get("channelName") or ch_name

    await status.edit_text(
        f"📡 **{ch_label}** mila!\n"
        f"⏳ Catchup URL fetch kar raha hoon…"
    )

    # ---- tier limits (same as /dl) ----
    is_owner   = uid in OWNER_IDS
    _dl_premium = is_premium(uid)
    _trimmed    = False
    if not is_owner:
        if _dl_premium:
            if duration_sec > 3600:
                return await status.edit_text(
                    "⏱ **Premium plan:** Max **1 hour** per catchup.\n"
                    f"You requested: `{dur_label}`\n\n"
                    "Time range chhota karo."
                )
        else:
            if duration_sec > 120:
                end_ts       = begin_ts + 120
                duration_sec = 120
                _trimmed     = True
                dur_label    = _seconds_to_hms(duration_sec)

    # ---- get catchup URL via JioTV API ----
    result = await asyncio.get_event_loop().run_in_executor(
        None, jiotv.get_catchup_url, ch_id, begin_ts, end_ts
    )

    import tempfile
    tmp_dir  = tempfile.mkdtemp(prefix="di_")
    out_file = os.path.join(tmp_dir, filename)

    stream_url = None
    if result.get("success") and result.get("url"):
        stream_url = result["url"]
        LOG.info("di: native JioTV catchup URL OK for %s", ch_label)
    else:
        LOG.info("di: native JioTV failed (%s), trying proxy fallback", result.get("message"))

    user_tasks[uid] = {"status": "di_catchup", "cancel": False}

    try:
        # ---- Method 1: Native JioTV API stream ----
        if stream_url:
            await status.edit_text(
                f"✅ Native stream mila!\n"
                f"📅 `{parsed['today']}` | 🕐 `{parsed['start_str']} → {parsed['end_str']}`\n"
                f"📺 **{ch_label}**\n"
                f"⏱ `{dur_label}`\n"
                f"📥 Recording shuru…"
                + (
                    "\n⚠️ Free limit: 2 min trim"
                    if _trimmed else ""
                )
            )

            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-headers", "User-Agent: Mozilla/5.0\r\n",
                "-i", stream_url,
                "-t", str(duration_sec),
                "-c", "copy",
                "-f", "matroska",
                "-avoid_negative_ts", "make_zero",
                out_file,
            ]

            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Progress updater
            async def _di_progress():
                last_edit = time.time()
                bar_chars = 10
                elapsed   = 0
                while proc.returncode is None:
                    await asyncio.sleep(5)
                    elapsed += 5
                    if user_tasks.get(uid, {}).get("cancel"):
                        proc.kill()
                        break
                    if time.time() - last_edit >= 10:
                        pct    = min(int((elapsed / duration_sec) * 100), 99) if duration_sec > 0 else 0
                        filled = int(pct / 100 * bar_chars)
                        bar    = "█" * filled + "░" * (bar_chars - filled)
                        try:
                            await status.edit_text(
                                f"📥 **Recording…** `{ch_label}`\n"
                                f"[{bar}] {pct}%\n"
                                f"⏱ Elapsed: `{_seconds_to_hms(elapsed)}` / `{dur_label}`"
                            )
                        except Exception:
                            pass
                        last_edit = time.time()

            asyncio.get_event_loop().create_task(_di_progress())
            _, stderr = await proc.communicate()

            if user_tasks.get(uid, {}).get("cancel"):
                await status.edit_text("❌ Cancelled.")
                user_tasks.pop(uid, None)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            if proc.returncode != 0 or not os.path.exists(out_file) or os.path.getsize(out_file) < 1024:
                err_text = (stderr or b"").decode(errors="ignore")[-300:]
                LOG.warning("di native ffmpeg failed: %s", err_text)
                stream_url = None   # fallback to proxy
            else:
                # Success with native
                file_size_mb = os.path.getsize(out_file) / (1024 * 1024)
                await status.edit_text(
                    f"✅ Recording complete!\n"
                    f"📁 Size: `{file_size_mb:.1f} MB`\n"
                    f"📤 Upload ho raha hai…"
                )

        # ---- Method 2: Proxy fallback ----
        if not stream_url or not os.path.exists(out_file) or os.path.getsize(out_file) < 1024:
            await status.edit_text(
                f"⏳ Native stream fail ho gaya.\n"
                f"🔗 Proxy se try kar raha hoon…\n"
                f"📺 **{ch_label}** | ⏱ `{dur_label}`"
            )
            proxy_file = await _di_proxy_fetch(
                ch_id, parsed["h24_start"], parsed["h24_end"],
                ch_label, tmp_dir
            )
            if proxy_file:
                out_file = proxy_file
            else:
                return await status.edit_text(
                    "❌ **Dono methods fail ho gaye.**\n\n"
                    "Shayad:\n"
                    "• Catchup is time range mein available nahi\n"
                    "• Token expire ho gaya — `/login` se dobara login karo\n"
                    "• Channel DRM protected hai"
                )

        # ---- Upload ----
        if os.path.exists(out_file) and os.path.getsize(out_file) >= 1024:
            file_size_mb = os.path.getsize(out_file) / (1024 * 1024)
            caption = (
                f"📺 **{ch_label}** — Catchup\n"
                f"📅 `{parsed['today']}` | 🕐 `{parsed['start_str']} → {parsed['end_str']}`\n"
                f"⏱ `{dur_label}` | 📡 JioTV\n"
                f"🎥 {get_default_watermark()}"
            )
            try:
                await message.reply_document(
                    document=out_file,
                    caption=caption,
                    file_name=filename,
                )
                await status.delete()
            except Exception as up_err:
                LOG.warning("di upload failed: %s", up_err)
                await status.edit_text(f"❌ Upload failed: `{up_err}`")
        else:
            await status.edit_text("❌ File empty ya missing hai.")

    except Exception as e:
        LOG.exception("di_catchup_cmd error uid=%s: %s", uid, e)
        try:
            await status.edit_text(f"❌ Error: `{e}`")
        except Exception:
            pass
    finally:
        user_tasks.pop(uid, None)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@app.on_message(filters.command(["dl", "DL"]) & AUTH)
async def dl_catchup_cmd(client: Client, message: Message):
    """
    /dl -Jiotv -c Channel -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM -n Filename
    Downloads a JioTV catchup recording and sends it.
    """
    uid = message.from_user.id
    status = None

    # --- check for active job ---
    if uid in user_tasks:
        return await message.reply_text(
            "⏳ Tumhara ek kaam chal raha hai. Pehle /cancelme karo."
        )

    # --- parse ---
    parsed = _parse_dl_command(message.text or "")
    if not parsed:
        return await message.reply_text(
            "❌ **Syntax galat hai.**\n\n"
            "**JioTV:**\n"
            "`/dl -Jiotv -c ChannelName -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM -n File`\n\n"
            "**Example:**\n"
            "`/dl -Jiotv -c Star Plus -t 02-07-2026 09:00 AM - 11:00 AM -n StarPlus_Morning`\n"
        )

    source       = parsed["source"]
    ch_name      = parsed["channel"]
    begin_ts     = parsed["begin_ts"]
    end_ts       = parsed["end_ts"]
    duration_sec = parsed["duration_sec"]
    out_filename = re.sub(r"[^\w\s-]", "", parsed["filename"]).strip().replace(" ", "_") or "catchup"

    # --- Tier-based /dl limits ---
    # Owner: unlimited | Premium: max 1 hour | Free/Verified: max 2 min (auto-trim)
    is_owner   = uid in OWNER_IDS
    _dl_premium = is_premium(uid)
    _trimmed    = False
    if not is_owner:
        if _dl_premium:
            if duration_sec > 3600:
                return await message.reply_text(
                    "⏱ **Premium plan:** Maximum **1 hour** per `/dl` download.\n"
                    f"You requested: `{_seconds_to_hms(duration_sec)}`\n\n"
                    "Shorten your time range and try again."
                )
        else:
            if duration_sec > 120:
                # Auto-trim to 2 minutes instead of rejecting
                end_ts       = begin_ts + 120
                duration_sec = 120
                _trimmed     = True

    dur_label = _seconds_to_hms(duration_sec)
    src_label = "JioTV"

    status = await message.reply_text(
        f"🔍 **{src_label} Catchup**\n"
        f"📺 Channel: `{ch_name}`\n"
        f"📅 Date: `{parsed['date_str']}`\n"
        f"🕐 Time: `{parsed['start_time']} → {parsed['end_time']}`\n"
        f"⏱ Duration: `{dur_label}`" + (
            "\n⚠️ **Free/Verified limit:** trimmed to 2 minutes.\n"
            "Use `/contact` to upgrade for longer downloads."
            if _trimmed else ""
        ) + "\n\n"
        f"⏳ Channel search kar raha hoon…"
    )

    # --- channel lookup ---
    if source == "jiotv":
        if not jiotv.is_logged_in():
            return await status.edit_text(
                "❌ JioTV login nahi hai.\n"
                "Admin se kaho: `/jiotvlogin <phone>`"
            )
        matches = await asyncio.get_event_loop().run_in_executor(
            None, jiotv.search_channel, ch_name
        )


    if not matches:
        return await status.edit_text(
            f"❌ Channel **{ch_name}** nahi mila.\n\n"
            f"Check: `/channels` se list dekho."
        )

    matched = matches[0]
    ch_id    = str(matched.get("id") or matched.get("channelId") or matched.get("channel_id") or "")
    ch_label = matched.get("name") or matched.get("channelName") or ch_name

    await status.edit_text(
        f"📡 **{ch_label}** mila!\n"
        f"⏳ Catchup stream URL fetch kar raha hoon…"
    )

    # --- get catchup URL ---
    if source == "jiotv":
        result = await asyncio.get_event_loop().run_in_executor(
            None, jiotv.get_catchup_url, ch_id, begin_ts, end_ts
        )


    if not result.get("success"):
        return await status.edit_text(
            f"❌ **Catchup URL nahi mila:**\n{result.get('message', 'Unknown error')}\n\n"
            f"Possible reasons:\n"
            f"• Channel mein catchup available nahi\n"
            f"• Token expire ho gaya (dobara login karo)\n"
            f"• Time range bohot purana hai (7 days max)"
        )

    stream_url = result["url"]
    is_drm     = result.get("drm", False)

    if is_drm:
        return await status.edit_text(
            f"⚠️ **{ch_label}** — Widevine DRM protected hai.\n\n"
            f"Is channel ka catchup direct download nahi ho sakta.\n"
            f"DRM-free channels ka use karo ya JioTV try karo."
        )

    # --- ffmpeg download ---
    await status.edit_text(
        f"✅ Stream URL mila!\n"
        f"📥 **{ch_label}** ka catchup download ho raha hai…\n"
        f"⏱ Duration: `{dur_label}`\n"
        f"📅 `{parsed['date_str']} {parsed['start_time']} → {parsed['end_time']}`"
    )

    import tempfile
    tmp_dir  = tempfile.mkdtemp(prefix="catchup_")
    out_file = os.path.join(tmp_dir, f"{out_filename}.mp4")

    user_tasks[uid] = {"status": "catchup_dl", "cancel": False}

    try:
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-headers", "User-Agent: Mozilla/5.0\r\n",
            "-i", stream_url,
            "-t", str(duration_sec),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            out_file,
        ]

        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Progress updater
        async def _dl_progress():
            last_edit = time.time()
            bar_chars = 10
            elapsed   = 0
            while proc.returncode is None:
                await asyncio.sleep(5)
                elapsed += 5
                if user_tasks.get(uid, {}).get("cancel"):
                    proc.kill()
                    break
                if time.time() - last_edit >= 10:
                    pct    = min(int((elapsed / duration_sec) * 100), 99) if duration_sec > 0 else 0
                    filled = int(pct / 100 * bar_chars)
                    bar    = "█" * filled + "░" * (bar_chars - filled)
                    try:
                        await status.edit_text(
                            f"📥 **Downloading Catchup…**\n"
                            f"📺 `{ch_label}`\n"
                            f"[{bar}] {pct}%\n"
                            f"⏱ Elapsed: {_seconds_to_hms(elapsed)} / {dur_label}"
                        )
                    except Exception:
                        pass
                    last_edit = time.time()

        asyncio.get_event_loop().create_task(_dl_progress())

        _, stderr = await proc.communicate()

        if user_tasks.get(uid, {}).get("cancel"):
            await status.edit_text("❌ Download cancelled.")
            user_tasks.pop(uid, None)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        if proc.returncode != 0:
            err_text = (stderr or b"").decode(errors="ignore")[-500:]
            LOG.error("ffmpeg catchup failed: %s", err_text)
            user_tasks.pop(uid, None)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return await status.edit_text(
                f"❌ **Download failed.**\n\n```{err_text}```"
            )

        if not os.path.exists(out_file) or os.path.getsize(out_file) < 1024:
            user_tasks.pop(uid, None)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return await status.edit_text(
                "❌ Download complete hua lekin file empty hai.\n"
                "Shayad stream mein content nahi tha is time range mein."
            )

        file_size_mb = os.path.getsize(out_file) / (1024 * 1024)
        await status.edit_text(
            f"✅ Download complete!\n"
            f"📁 Size: `{file_size_mb:.1f} MB`\n"
            f"📤 Upload ho raha hai…"
        )

        # Auto-compress if large (uses existing auto_compress_large_video)
        final_file = out_file
        final_file = await auto_compress_large_video(
            out_file, tmp_dir, float(duration_sec), status, uid
        )

        caption = (
            f"📺 **{ch_label}** — Catchup\n"
            f"📅 {parsed['date_str']} | {parsed['start_time']} → {parsed['end_time']}\n"
            f"⏱ {dur_label} | 📡 {src_label}\n"
            f"🎬 {get_default_watermark()}"
        )

        try:
            await message.reply_video(
                video=final_file,
                caption=caption,
                supports_streaming=True,
            )
            await status.delete()
        except Exception as upload_err:
            LOG.warning("catchup video upload failed, trying document: %s", upload_err)
            await message.reply_document(
                document=final_file,
                caption=caption,
            )
            await status.delete()

    except Exception as e:
        LOG.exception("dl_catchup_cmd error uid=%s: %s", uid, e)
        try:
            if status:
                await status.edit_text(f"❌ **Kuch galat ho gaya:**\n`{e}`")
            else:
                await message.reply_text(f"❌ **Kuch galat ho gaya:**\n`{e}`")
        except Exception:
            pass
    finally:
        user_tasks.pop(uid, None)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /dl help
# ---------------------------------------------------------------------------
@app.on_message(filters.command(["dlhelp", "DLHelp"]) & AUTH)
async def dl_help_cmd(client: Client, message: Message):
    await message.reply_text(
        "📡 **JioTV Catchup Download**\n\n"
        "**Syntax:**\n"
        "`/dl -Jiotv -c ChannelName -t DD-MM-YYYY HH:MM AM/PM - HH:MM AM/PM -n File`\n\n"
        "**Examples:**\n"
        "`/dl -Jiotv -c Star Plus -t 02-07-2026 09:00 AM - 11:30 AM -n StarPlus_Morning`\n"
        "`/dl -Jiotv -c Colors -t 01-07-2026 08:00 PM - 10:00 PM -n Colors_Primetime`\n"
        "**Notes:**\n"
        "• Date/time should be IST (Indian Standard Time)\n"
        "• Max duration: 6 hours\n"
        "• Free users: 3 min max | Verified: unlimited\n"
        "• Use `/channels` to search JioTV channels\n"
    )


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

if __name__ == "__main__":
    print("Starting Video Recorder Bot...")
    LOG.info("OWNER_IDS loaded: %d ID(s) — %s", len(OWNER_IDS), OWNER_IDS if OWNER_IDS else "NONE (no owner set!)")
    LOG.info("AUTH_USERS loaded: %d ID(s)", len(AUTH_USERS))
    sweep_old_downloads()
    LOG.info(
        "Recordings will be auto-deleted from the server after %s.",
        _retention_label(),
    )
    app.run()
