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
from typing import Tuple, Optional
from os.path import join
from datetime import datetime, timedelta

import psutil
import pytz
import yt_dlp
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters
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
SUPPORT_CHANNEL     = _environ.get("SUPPORT_CHANNEL",     "LittleSinghamChannel")

GROUP_CHAT_ID       = _parse_int("GROUP_CHAT_ID",  _environ.get("GROUP_CHAT_ID",  "0"))
GROUP_INVITE_LINK   = _environ.get("GROUP_INVITE_LINK", "https://t.me/+MuzbPV3m55llNmFl")

SHRINKME_API_KEY    = _environ.get("SHRINKME_API_KEY",    "9503d9bf87c90aa9e0aab35d4dec7d1ce24c0a23")
BOT_USERNAME        = _environ.get("BOT_USERNAME",        "M3u8LiveRecordingBot")

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
ADMIN_FILE       = join(DATA_DIRECTORY, "admins.json")
AUDIO_NAME_FILE     = join(DATA_DIRECTORY, "audio_brand_name.txt")
WATERMARK_NAME_FILE = join(DATA_DIRECTORY, "watermark_name.txt")


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
compress_jobs:      dict = {}
reclink_jobs:       dict = {}
ss_jobs:            dict = {}
merge_sessions:     dict = {}
title_jobs:         dict = {}

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
        result = False

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
        h, m, s = time_str.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
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
    chans = load_channels()
    cats  = list(chans.get("categories", {}).keys())
    rows, row = [], []
    for i, c in enumerate(cats):
        row.append(InlineKeyboardButton(c, callback_data=f"chcat:{c}"))
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
    if at == 0:
        audio_s = "All Tracks"
    elif tracks and at <= len(tracks):
        audio_s = _audio_track_label(tracks[at - 1])
    else:
        audio_s = f"Track {at}"
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
    wm_icon   = "✅" if s["watermark_on"] else "🚫"
    wm_label  = "ON" if s["watermark_on"] else "OFF"
    auto_icon = "✅" if s["auto_mode"] else "⏩"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Top-Left",    callback_data=f"rs:{uid}:wm_pos:top_left"),
         InlineKeyboardButton("↗️ Top-Right",   callback_data=f"rs:{uid}:wm_pos:top_right")],
        [InlineKeyboardButton("⊙ Center",       callback_data=f"rs:{uid}:wm_pos:center")],
        [InlineKeyboardButton("↙️ Bottom-Left", callback_data=f"rs:{uid}:wm_pos:bottom_left"),
         InlineKeyboardButton("↘️ Bottom-Right",callback_data=f"rs:{uid}:wm_pos:bottom_right")],
        [InlineKeyboardButton(f"{wm_icon} 🚫 Watermark {wm_label}", callback_data=f"rs:{uid}:wm_toggle")],
        [InlineKeyboardButton("✏️ Change Watermark Text",            callback_data=f"rs:{uid}:wm_text")],
        [InlineKeyboardButton(f"{auto_icon} Auto: First+Last 1min", callback_data=f"rs:{uid}:auto_toggle")],
        [InlineKeyboardButton("◀️ Back: Audio Track",               callback_data=f"rs:{uid}:back_audio"),
         InlineKeyboardButton("📐 Next: Video Size →",              callback_data=f"rs:{uid}:next_quality")],
        [InlineKeyboardButton("❌ Cancel",                           callback_data=f"rs:{uid}:cancel")],
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


def _build_vf_and_codec(setup: dict) -> tuple[list[str], bool]:
    quality  = setup["quality"]
    aspect   = setup["aspect"]
    wm_on    = setup["watermark_on"]
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

    if wm_on:
        pos_map = {
            "top_left":    "x=10:y=10",          "top_right":    "x=w-tw-10:y=10",
            "center":      "x=(w-tw)/2:y=(h-th)/2",
            "bottom_left": "x=10:y=h-th-10",     "bottom_right": "x=w-tw-10:y=h-th-10",
        }
        xy   = pos_map.get(setup["watermark_pos"], "x=10:y=10")
        safe = ((setup.get("watermark_text") or get_default_watermark())
                .replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:"))
        # Show watermark only in the last 2 minutes of the recording
        dur_sec   = setup.get("duration_sec", 0) or time_to_seconds(setup.get("timestamp", "0"))
        wm_start  = max(0, dur_sec - 120)
        vf.append(f"drawtext=text='{safe}':fontsize=28:fontcolor=white"
                  f":box=1:boxcolor=black@0.4:boxborderw=4:{xy}"
                  f":enable='gte(t,{wm_start})'")  # last-2-min only

    post: list[str] = []
    at = setup["audio_track"]
    if at == 0:
        post += ["-map", "0:v?", "-map", "0:a?"]
    else:
        post += ["-map", "0:v?", "-map", f"0:a:{at - 1}?"]

    if needs_encode:
        if vf:
            post += ["-vf", ",".join(vf)]
        crf = "23" if quality in ("480", "576", "640") else "21"
        abr = "192k" if quality == "1080" else "128k"
        post += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                 "-c:a", "aac", "-b:a", abr]
    else:
        post += ["-c:v", "copy", "-c:a", "copy"]
    return post, needs_encode

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
    sel    = setup["audio_track"]   # 0 = all, 1 = first track, 2 = second, …
    rows   = []

    all_icon = "✅ " if sel == 0 else ""
    rows.append([InlineKeyboardButton(
        f"{all_icon}🎵 All Tracks",
        callback_data=f"rs:{uid}:audio_select:0"
    )])

    for i, t in enumerate(tracks, 1):
        icon = "✅ " if sel == i else ""
        rows.append([InlineKeyboardButton(
            f"{icon}🎙 {_audio_track_label(t)}",
            callback_data=f"rs:{uid}:audio_select:{i}"
        )])

    rows.append([
        InlineKeyboardButton("📐 Next: Watermark →", callback_data=f"rs:{uid}:next_wm"),
        InlineKeyboardButton("❌ Cancel",             callback_data=f"rs:{uid}:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def _audio_step_text(setup: dict) -> str:
    tracks = setup.get("detected_audio_tracks", [])
    url    = setup.get("url", "")
    lines  = [
        "**🎙 Step 1 — Audio Track**\n",
        f"📡 URL: `{url[:80]}{'…' if len(url) > 80 else ''}`",
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
      -ua   <value>         User-Agent string
      -referer <value>      Referer header
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
        elif t in ("ua", "user-agent", "useragent") and i + 1 < len(tokens):
            flags["user_agent"] = tokens[i + 1]
            i += 2
        elif t in ("referer", "ref", "r") and i + 1 < len(tokens):
            flags["referer"] = tokens[i + 1]
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
        "audio_track":    0,
        "auto_mode":      False,
        "quality":        "original",
        "aspect":         "none",
        "step":           0,
        "detected_audio_tracks": [],
    }
    rec_setup_sessions[user_id] = setup

    # Probe audio tracks from the stream before showing the wizard
    probe_msg = await message.reply_text(
        "🔍 **Probing stream for audio tracks…**\n\n"
        f"`{url[:90]}{'…' if len(url) > 90 else ''}`"
    )
    setup["setup_msg_id"] = probe_msg.id

    # Effective URL after redirect/page extraction
    probe = await probe_stream(url)
    effective_url = probe["final_url"]
    tracks = await _probe_audio_tracks(effective_url)
    setup["detected_audio_tracks"] = tracks
    setup["effective_url"]         = effective_url

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
            is_hls         = effective_url != url or ".m3u8" in effective_url.lower()
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
        if setup.get("flag_origin"):
            extra_headers["Origin"] = setup["flag_origin"]

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
        args += ["-probesize", "10000000", "-analyzeduration", "15000000", "-i", ffmpeg_input]
        extra_post, re_encodes = _build_vf_and_codec(setup)
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

        args += ["-t", str(timestamp), video_path]

        ffmpeg_process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        rec_entry["ffmpeg_pid"] = ffmpeg_process.pid
        LOG.info(f"FFmpeg pid={ffmpeg_process.pid} user={user_id} rec={rec_id} re_encode={re_encodes}")

        # Take a background snapshot right after FFmpeg starts — switch progress msg to photo
        async def _try_initial_snapshot():
            await asyncio.sleep(4)   # give FFmpeg a moment to buffer first segment
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


def _ott_progress_text(state: dict) -> str:
    pct     = state.get("percent", 0.0)
    bar_len = 20
    filled  = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
    bar     = "●" * filled + "○" * (bar_len - filled)

    def _fmt_bytes(n):
        if n is None: return "?"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024: return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _fmt_eta(s):
        if s is None or s < 0: return "?"
        s = int(s)
        h, rem = divmod(s, 3600); m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    speed  = state.get("speed")
    title  = state.get("title") or "Downloading"
    return (f"📡 **{title[:80]}**\n\nStatus: `{state.get('status', '?')}`\n"
            f"`{bar}` `{pct:5.1f}%`\n"
            f"💾 Size: `{_fmt_bytes(state.get('downloaded'))}` / `{_fmt_bytes(state.get('total'))}`\n"
            f"⚡ Speed: `{f'{_fmt_bytes(speed)}/s' if speed else '?'}`\n"
            f"⏳ ETA: `{_fmt_eta(state.get('eta'))}`")


async def handle_ott_download(client: Client, message: Message):
    user_id  = message.from_user.id
    msg      = await message.reply_text("Initializing download...")
    save_dir: Optional[str] = None
    url      = ""
    try:
        parts        = message.text.split(maxsplit=2)
        url          = parts[1].strip()
        raw_filename = parts[2].strip() if len(parts) > 2 else ""
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
                await asyncio.sleep(4)

        watcher_task           = asyncio.create_task(watcher())
        progress_tasks[user_id] = watcher_task

        outtmpl  = join(save_dir, (raw_filename or "%(title).200B") + ".%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl, "format": "bv*+ba/b", "merge_output_format": "mkv",
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "concurrent_fragment_downloads": 4, "retries": 5, "fragment_retries": 5,
            "progress_hooks": [_hook], "user_agent": DEFAULT_USER_AGENT, "trim_file_name": 200,
            "geo_bypass": True, "geo_bypass_country": "IN", "verbose": False,
            "extractor_args": {
                "hotstar":  {"video_resolution": ["max"]},
                "sonyliv":  {"prefer_subs_lang": ["hi"]},
                "youtube":  {"player_client": ["android", "web"]},
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

        start_time = time.time()
        await split_and_send_video(
            message, video_path, caption, duration or 0,
            thumb_path=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            status_msg=msg,
            progress=progress_for_pyrogram,
            progress_args=(message, start_time, msg, save_dir, False),
        )
        asyncio.create_task(upload_and_notify(
            client, message.chat.id, video_path, os.path.basename(video_path)
        ))
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Error in handle_ott_download: {e}")
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
            hint_block = ("\n\n" + "\n\n".join(hints)) if hints else ""
            if is_owner(user_id) or is_admin(user_id):
                if len(err_text) > 2500:
                    err_text = "...[truncated]...\n" + err_text[-2500:]
                await msg.edit_text(f"**Download failed.**\n\n`{err_text}`{hint_block}")
            else:
                await msg.edit_text(
                    "❌ **Download failed.**\n\n"
                    "Could not download this video. Please check the link and try again."
                    f"{hint_block}\n\nUse /contact if the problem persists."
                )
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
    bar_len  = 20
    filled   = max(0, min(bar_len, int(round(pct / 100 * bar_len))))
    bar      = "●" * filled + "○" * (bar_len - filled)
    size_mb  = size_bytes / (1024 * 1024)
    remaining_sec = max(0.0, (dur_sec - done_sec) / max(0.05, speed_mult))
    return (f"📡 **Compressing**\n\nStatus: `encoding`\n`{bar}` `{pct:5.1f}%`\n"
            f"💾 Size: `{size_mb:.1f} MB` / target `{target_mb} MB`\n"
            f"⚡ Speed: `{speed_mult:.2f}x`\n"
            f"⏳ ETA: `{TimeFormatter(int(remaining_sec * 1000))}`")


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

    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1", "-y", "-i", src, "-map", "0:v:0"]
    if "multi" in langs or not kept_audio:
        args += ["-map", "0:a?"]
    else:
        for s in kept_audio:
            args += ["-map", f"0:{s['index']}"]
    if res_cfg["height"] > 0:
        args += ["-vf", f"scale=-2:{res_cfg['height']}"]
    if state["res_key"] == "hq":
        args += ["-c:v", res_cfg["codec"], "-crf", "20", "-preset", "veryfast"]
    elif res_cfg["codec"] == "libx265":
        args += ["-c:v", "libx265", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2)}k",
                 "-preset", "fast", "-x265-params", "log-level=error", "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "libx264", "-b:v", f"{video_kbps}k",
                 "-maxrate", f"{int(video_kbps * 1.4)}k", "-bufsize", f"{int(video_kbps * 2)}k",
                 "-preset", "veryfast"]
    args += ["-c:a", "aac", "-b:a", f"{audio_kbps_per}k", out_path]

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
            await asyncio.sleep(4)

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

        upload_start = time.time()
        await split_and_send_video(
            status_msg, out_path, caption, int(duration),
            thumb_path=thumb_path,
            status_msg=status_msg,
            progress=progress_for_pyrogram,
            progress_args=(status_msg, upload_start, status_msg, save_dir, False),
        )
        asyncio.create_task(upload_and_notify(
            client, status_msg.chat.id, out_path, os.path.basename(out_path)
        ))
        try:
            await status_msg.edit_text(f"Compress done — uploaded `{out_size_mb:.1f} MB`.\n"
                                       f"Server copy auto-deletes in {_retention_label()}.")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)
    except Exception as e:
        LOG.error(f"Compress failed: {e}")
        try:
            if is_owner(user_id) or is_admin(user_id):
                err_text = str(e)
                if len(err_text) > 3500: err_text = "...[truncated]...\n" + err_text[-3500:]
                await status_msg.edit_text(f"**Compress failed.**\n\n`{err_text}`")
            else:
                await status_msg.edit_text("❌ **Compress failed.**\n\nCould not process the video. Please try again.")
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
        LOG.error(f"Screenshot job failed: {e}")
        try:
            if is_owner(user_id) or is_admin(user_id):
                err_text = str(e)
                if len(err_text) > 2500: err_text = "...[truncated]...\n" + err_text[-2500:]
                await status_msg.edit_text(f"**Screenshot job failed.**\n\n`{err_text}`")
            else:
                await status_msg.edit_text("❌ **Screenshot failed.**\n\nCould not extract screenshots. Please try again.")
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
            filter_complex = ("".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
                              + f"concat=n={n}:v=1:a=1[outv][outa]")
            args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
                    *inputs, "-filter_complex", filter_complex,
                    "-map", "[outv]", "-map", "[outa]",
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

        upload_start = time.time()
        await split_and_send_video(
            status, out_path, caption, int(total_dur),
            thumb_path=thumb_path,
            status_msg=status,
            progress=progress_for_pyrogram,
            progress_args=(status, upload_start, status, save_dir, False),
        )
        asyncio.create_task(upload_and_notify(
            client, status.chat.id, out_path, os.path.basename(out_path)
        ))
        try: await status.edit_text(f"🧩 Merge done — uploaded `{out_size_mb:.1f} MB`.\n"
                                    f"Server copy auto-deletes in {_retention_label()}.")
        except Exception: pass
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
                await asyncio.sleep(4)

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


async def progress_for_pyrogram(current, total, message, start, msg,
                                 save_dir=None, was_cancelled=False):
    now        = time.time()
    diff       = now - start or 1
    percentage = current * 100 / total
    speed      = current / diff
    bar_length = 15
    filled     = int(bar_length * percentage // 100)
    bar        = "█" * filled + "░" * (bar_length - filled)

    if int(percentage) in (0, 10, 25, 50, 75, 90, 95, 99, 100) or current == total:
        eta    = TimeFormatter(int((total - current) / speed * 1000)) if speed > 0 else "00:00:00"
        prefix = "**Uploading partial recording**" if was_cancelled else "**Uploading video**"
        text   = (f"{prefix}\n`[{bar}]` {percentage:.1f}%\n"
                  f"Progress: `{current/(1024*1024):.1f} / {total/(1024*1024):.1f} MB`\n"
                  f"Speed: `{speed/(1024*1024):.1f} MB/s`\nETA: `{eta}`")
        try: await msg.edit_text(text)
        except Exception: pass

        if current == total:
            label = _retention_label()
            final = "Partial recording sent." if was_cancelled else "Upload completed successfully."
            try: await msg.edit_text(f"{final}\nThe server copy will be auto-deleted in {label}.")
            except Exception: pass


# ---------------------------------------------------------------------------
# 2 GB auto-split helper
# ---------------------------------------------------------------------------

TG_MAX_BYTES = 1_950_000_000  # 1.95 GB — safe margin under Telegram's 2 GB hard limit


async def split_and_send_video(
    send_target,
    video_path: str,
    caption: str,
    duration: int,
    thumb_path=None,
    status_msg=None,
    progress=None,
    progress_args=None,
):
    """
    Send a video to Telegram.
    If the file exceeds TG_MAX_BYTES (1.95 GB) it is automatically split into
    equal-duration parts using FFmpeg (-c copy, no re-encoding) and each part
    is uploaded separately with '📂 Part X / Y' appended to the caption.
    """
    size = os.path.getsize(video_path)

    if size <= TG_MAX_BYTES:
        await send_target.reply_video(
            video=video_path,
            caption=caption,
            duration=duration or None,
            thumb=thumb_path,
            progress=progress,
            progress_args=progress_args,
        )
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

import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional


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


async def upload_and_notify(client, chat_id: int, file_path: str, filename: str):
    """Upload to Drive (user tokens preferred, SA as fallback) and send the link."""
    user_connected = is_user_connected(chat_id)
    if not user_connected and not _sa_enabled():
        return
    try:
        folder_id = None if user_connected else GDRIVE_FOLDER_ID
        link = await asyncio.to_thread(
            _upload_sync, file_path, filename, folder_id, chat_id
        )
        await client.send_message(
            chat_id,
            f"🤖 **Google Drive Upload Complete!**\n\n"
            f"📄 File: `{filename}`\n"
            f"🔗 [Open in Drive]({link})",
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOG.error(f"GDrive upload failed for {filename}: {e}")
        try:
            await client.send_message(chat_id, f"⚠️ Google Drive upload failed: `{e}`")
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
