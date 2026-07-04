"""
TataPlay API module — real implementation.
Handles OTP login, channel list, JWT + HMAC tokens, catchup stream URLs.
Based on tataplay-m3u PHP reference (functions.php / login.php / manifest.php).
"""

import os
import json
import time
import base64
import logging
import requests

try:
    from Crypto.Cipher import AES
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session storage
# ---------------------------------------------------------------------------
_DATA_DIR     = os.environ.get(
    "DATA_DIRECTORY",
    os.path.join(os.path.dirname(__file__), "data"),
)
_SESSION_FILE = os.path.join(_DATA_DIR, "tataplay_session.json")
_CACHE_DIR    = os.path.join(_DATA_DIR, "tataplay_cache")

_session: dict = {}
# {channel_id: {"jwt": str, "exp": int}}
_jwt_cache: dict  = {}
# {channel_id: {"hmac": str, "exp": int}}
_hmac_cache: dict = {}

def _load_session() -> None:
    global _session
    try:
        if os.path.exists(_SESSION_FILE):
            with open(_SESSION_FILE, "r", encoding="utf-8") as f:
                _session = json.load(f)
    except Exception as e:
        LOG.warning("tataplay: could not load session: %s", e)
        _session = {}

def _save_session() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(_session, f)
    except Exception as e:
        LOG.warning("tataplay: could not save session: %s", e)

_load_session()

# ---------------------------------------------------------------------------
# Device / request headers constants (matching PHP reference)
# ---------------------------------------------------------------------------
_DEVICE_DETAILS = (
    '{"pl":"web","os":"WINDOWS","lo":"en-us","app":"1.48.8","dn":"PC",'
    '"bv":116,"bn":"OPERA","device_id":"7683d93848b0f472c508e38b1827038a",'
    '"device_type":"WEB","device_platform":"PC","device_category":"open",'
    '"manufacturer":"WINDOWS_OPERA_116","model":"PC","sname":""}'
)
_USER_AGENT  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
_BASE_HEADERS = {
    "Content-Type":     "application/json",
    "Accept":           "*/*",
    "Accept-Encoding":  "gzip, deflate, br",
    "Accept-Language":  "en-US,en;q=0.9,en-IN;q=0.8",
    "User-Agent":       _USER_AGENT,
    "device_details":   _DEVICE_DETAILS,
    "Referer":          "https://watch.tataplay.com/",
    "Origin":           "https://watch.tataplay.com",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_creds() -> dict:
    return {
        "accessToken": _session.get("accessToken", ""),
        "sid":         _session.get("sid", ""),
        "sname":       _session.get("sname", ""),
        "profileId":   _session.get("profileId", ""),
    }

def _auth_headers(sname: str = "") -> dict:
    creds = _get_creds()
    dd = _DEVICE_DETAILS.replace('"sname":""', f'"sname":"{sname or creds["sname"]}"')
    return {
        **_BASE_HEADERS,
        "authorization":  f"bearer {creds['accessToken']}",
        "device_details": dd,
        "profileid":      creds["profileId"],
        "platform":       "web",
    }

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def is_logged_in() -> bool:
    return bool(_session.get("accessToken"))

def get_session_info() -> dict:
    if not is_logged_in():
        return {}
    return {
        "sid":    _session.get("sid", ""),
        "sname":  _session.get("sname", ""),
    }

# ---------------------------------------------------------------------------
# OTP flow — Step 1: request OTP
# ---------------------------------------------------------------------------

def send_otp(subscriber_id: str) -> dict:
    """
    Initiate TataPlay OTP login.
    subscriber_id: 10-digit SID.
    Returns {"success": bool, "message": str, "encrypted_rmn": str|None}.
    """
    sid = subscriber_id.strip()
    if not sid.isdigit() or len(sid) != 10:
        return {"success": False, "message": "❌ SID must be exactly 10 digits.", "encrypted_rmn": None}

    url  = "https://tm.tapi.videoready.tv/login-service/pub/api/v2/generate/otp"
    body = json.dumps({"sid": sid, "rmn": ""})
    try:
        resp = requests.post(url, data=body.encode(), headers=_BASE_HEADERS, timeout=15)
        data = resp.json()
    except Exception as e:
        LOG.error("tataplay send_otp error: %s", e)
        return {"success": False, "message": f"❌ Network error: {e}", "encrypted_rmn": None}

    if data.get("code") == 0:
        decrypted_rmn  = data["data"].get("decryptedRMN", "your registered number")
        encrypted_rmn  = data["data"].get("rmn", "")
        # Store pending state in session
        _session["_pending_sid"] = sid
        _session["_pending_rmn"] = encrypted_rmn
        _save_session()
        return {
            "success": True,
            "message": f"✅ OTP sent to `{decrypted_rmn}`.\nEnter OTP with `/tpotp <code>`",
            "encrypted_rmn": encrypted_rmn,
        }
    else:
        msg = data.get("message", "Unknown error during OTP generation.")
        return {"success": False, "message": f"❌ {msg}", "encrypted_rmn": None}


# ---------------------------------------------------------------------------
# OTP flow — Step 2: verify OTP
# ---------------------------------------------------------------------------

def verify_otp(otp: str) -> dict:
    """
    Verify OTP using stored pending SID + encrypted RMN.
    Returns {"success": bool, "message": str}.
    """
    global _session
    otp = otp.strip()
    if not otp.isdigit() or len(otp) != 6:
        return {"success": False, "message": "❌ Enter the 6-digit OTP sent to your phone."}

    sid           = _session.get("_pending_sid", "")
    encrypted_rmn = _session.get("_pending_rmn", "")

    if not sid:
        return {"success": False, "message": "❌ No pending login. Use `/tplogin <SID>` first."}

    url  = "https://tm.tapi.videoready.tv/login-service/pub/api/v3/login/ott"
    body = json.dumps({
        "rmn":          encrypted_rmn,
        "sid":          sid,
        "authorization": otp,
        "loginOption":  "OTP",
    })
    try:
        resp = requests.post(url, data=body.encode(), headers=_BASE_HEADERS, timeout=15)
        data = resp.json()
    except Exception as e:
        LOG.error("tataplay verify_otp error: %s", e)
        return {"success": False, "message": f"❌ Network error: {e}"}

    if data.get("code") == 0:
        d = data.get("data", {})
        _session = {
            "accessToken": d.get("accessToken", ""),
            "sid":         d.get("userDetails", {}).get("sid", sid),
            "sname":       d.get("userDetails", {}).get("sName", ""),
            "profileId":   d.get("userProfile", {}).get("id", ""),
            "_raw":        d,
        }
        _save_session()
        sname = _session["sname"] or sid
        return {"success": True, "message": f"✅ TataPlay logged in as **{sname}** (SID: {sid})"}
    else:
        msg = data.get("message", "OTP verification failed.")
        return {"success": False, "message": f"❌ {msg}"}


# ---------------------------------------------------------------------------
# Channel list (from ygxworld fetcher)
# ---------------------------------------------------------------------------

_fetcher_cache: dict = {"data": None, "ts": 0}
_FETCHER_TTL = 86400  # 24 hours

def _get_fetcher_data() -> list:
    """Return list of TataPlay channels from ygxworld fetcher API."""
    global _fetcher_cache
    now = time.time()
    if _fetcher_cache["data"] and (now - _fetcher_cache["ts"]) < _FETCHER_TTL:
        return _fetcher_cache["data"]
    try:
        resp = requests.get(
            "https://api.ygxworld.workers.dev/fetcher.json",
            timeout=20,
            headers={"User-Agent": _USER_AGENT},
        )
        data = resp.json()
        channels = data.get("data", {}).get("channels", [])
        _fetcher_cache = {"data": channels, "ts": now}
        return channels
    except Exception as e:
        LOG.error("tataplay fetcher error: %s", e)
        return _fetcher_cache.get("data") or []


def get_channels() -> list:
    if not is_logged_in():
        return []
    return _get_fetcher_data()


def search_channel(name: str) -> list:
    """Fuzzy-search TataPlay channels by name."""
    channels = _get_fetcher_data()
    name_lower = name.lower().strip()
    exact, partial = [], []
    for ch in channels:
        ch_name = (ch.get("name") or ch.get("channelName") or "").lower()
        if ch_name == name_lower:
            exact.append(ch)
        elif name_lower in ch_name or ch_name in name_lower:
            partial.append(ch)
    return (exact + partial)[:10]


# ---------------------------------------------------------------------------
# JWT token (for stream auth)
# ---------------------------------------------------------------------------

def _decode_jwt_exp(jwt: str) -> int:
    """Return expiry timestamp from JWT payload, or 0 on error."""
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return 0
        payload = base64.b64decode(parts[1] + "==")
        return json.loads(payload).get("exp", 0)
    except Exception:
        return 0


def _get_channel_entitlements(channel_id: str) -> list:
    """Fetch channel entitlements (bid list) for JWT generation."""
    creds = _get_creds()
    url   = f"https://tm.tapi.videoready.tv/content-detail/pub/api/v6/channels/{channel_id}?platform=WEB"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=15)
        data = resp.json()
        detail       = data.get("data", {}).get("detail", {})
        entitlements = detail.get("entitlements", [])
        special_id   = "1000001274"
        epids = []
        if special_id in entitlements:
            epids.append({"epid": "Subscription", "bid": special_id})
        elif entitlements:
            epids.append({"epid": "Subscription", "bid": entitlements[0]})
        return epids
    except Exception as e:
        LOG.warning("tataplay entitlements error ch=%s: %s", channel_id, e)
        return []


def _generate_jwt(channel_id: str) -> str | None:
    """Generate JWT for streaming, with in-memory cache."""
    now = int(time.time())
    cached = _jwt_cache.get(channel_id)
    if cached and cached["exp"] > now + 60:
        return cached["jwt"]

    epids = _get_channel_entitlements(channel_id)
    creds = _get_creds()
    url   = "https://tm.tapi.videoready.tv/auth-service/v3/sampling/token-service/token"
    body  = json.dumps({
        "action":          "stream",
        "epids":           epids,
        "samplingExpiry":  "ucPtCl63EsD1qBrlIhY9nw==#v2",
    })
    headers = {
        **_auth_headers(),
        "content-type": "application/json",
        "locale":       "ENG",
        "x-device-platform":   "PC",
        "x-device-type":       "WEB",
        "x-subscriber-id":     creds["sid"],
        "x-subscriber-name":   creds["sname"],
    }
    try:
        resp = requests.post(url, data=body.encode(), headers=headers, timeout=15)
        data = resp.json()
        if data.get("code") == 0 and data.get("data", {}).get("token"):
            jwt = data["data"]["token"]
            exp = _decode_jwt_exp(jwt)
            _jwt_cache[channel_id] = {"jwt": jwt, "exp": exp}
            return jwt
        LOG.warning("tataplay JWT error: %s", data.get("message"))
        return None
    except Exception as e:
        LOG.error("tataplay _generate_jwt error: %s", e)
        return None


# ---------------------------------------------------------------------------
# HMAC token (Akamai CDN cookie)
# ---------------------------------------------------------------------------

def _aes_ecb_decrypt(b64_data: str) -> str | None:
    """Decrypt AES-128-ECB base64 string with key 'aesEncryptionKey'."""
    if not _CRYPTO_OK:
        return None
    try:
        key       = b"aesEncryptionKey"
        encrypted = base64.b64decode(b64_data.split("#")[0])
        cipher    = AES.new(key, AES.MODE_ECB)
        decrypted = cipher.decrypt(encrypted)
        pad_len   = decrypted[-1]
        return decrypted[:-pad_len].decode("utf-8")
    except Exception as e:
        LOG.warning("tataplay AES decrypt error: %s", e)
        return None


def _get_hmac(channel_id: str) -> str | None:
    """Get Akamai HMAC token for channel, with in-memory cache."""
    now = int(time.time())
    cached = _hmac_cache.get(channel_id)
    if cached and cached["exp"] > now + 60:
        return cached["hmac"]

    creds = _get_creds()
    url   = f"https://tm.tapi.videoready.tv/digital-feed-services/api/partner/cdn/player/details/LIVE/{channel_id}"
    dd    = _DEVICE_DETAILS.replace('"sname":""', f'"sname":"{creds["sname"]}"')
    headers = {
        "accept":           "*/*",
        "accept-language":  "en-US,en;q=0.9,en-IN;q=0.8",
        "authorization":    creds["accessToken"],
        "content-type":     "application/json",
        "device_details":   dd,
        "kp":               "false",
        "locale":           "ENG",
        "origin":           "https://watch.tataplay.com",
        "platform":         "web",
        "profileid":        creds["profileId"],
        "referer":          "https://watch.tataplay.com/",
        "user-agent":       _USER_AGENT,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            LOG.warning("tataplay HMAC API returned %s", resp.status_code)
            return None
        data = resp.json()
        encrypted_token = data.get("data", {}).get("dashWidevinePlayUrl", "")
        if not encrypted_token:
            return None

        redirect_url = _aes_ecb_decrypt(encrypted_token)
        if not redirect_url:
            LOG.warning("tataplay: could not decrypt dashWidevinePlayUrl")
            return None

        # Follow the URL (HEAD request) to grab Set-Cookie: hdntl=...
        head_resp = requests.head(
            redirect_url,
            headers={
                "User-Agent": _USER_AGENT,
                "Origin":     "https://watch.tataplay.com",
                "Referer":    "https://watch.tataplay.com/",
            },
            allow_redirects=True,
            timeout=10,
        )
        # Search all Set-Cookie headers from redirect history + final response
        hdntl = None
        for r in list(head_resp.history) + [head_resp]:
            cookie_hdr = r.headers.get("Set-Cookie", "")
            if "hdntl=" in cookie_hdr:
                for part in cookie_hdr.split(";"):
                    part = part.strip()
                    if part.startswith("hdntl="):
                        hdntl = part
                        break
            if hdntl:
                break

        if not hdntl:
            LOG.warning("tataplay: hdntl cookie not found for ch %s", channel_id)
            return None

        import re as _re
        exp_match = _re.search(r"exp=(\d+)", hdntl)
        exp_time  = int(exp_match.group(1)) if exp_match else now + 600
        _hmac_cache[channel_id] = {"hmac": hdntl, "exp": exp_time}
        return hdntl
    except Exception as e:
        LOG.error("tataplay _get_hmac error ch=%s: %s", channel_id, e)
        return None


# ---------------------------------------------------------------------------
# Live stream URL (DASH MPD with HMAC)
# ---------------------------------------------------------------------------

def get_stream_url(channel_id: str) -> dict:
    """Get live DASH MPD URL for a TataPlay channel."""
    if not is_logged_in():
        return {"success": False, "url": None, "message": "Not logged in."}

    channels  = _get_fetcher_data()
    ch_data   = next((c for c in channels if str(c.get("id")) == str(channel_id)), None)
    if not ch_data:
        return {"success": False, "url": None, "message": f"Channel {channel_id} not found."}

    manifest_url = ch_data.get("manifest_url", "")
    if not manifest_url:
        return {"success": False, "url": None, "message": "No manifest URL for this channel."}

    if "bpaita" not in manifest_url:
        return {"success": True, "url": manifest_url, "message": ""}

    hmac = _get_hmac(channel_id)
    if not hmac:
        return {"success": False, "url": None, "message": "Could not obtain HMAC token."}

    stream_url = f"{manifest_url}?{hmac}"
    return {"success": True, "url": stream_url, "message": ""}


# ---------------------------------------------------------------------------
# Catchup stream URL
# ---------------------------------------------------------------------------

def get_catchup_url(channel_id: str, begin_ts: int, end_ts: int) -> dict:
    """
    Get DASH catchup MPD URL for a past time range.
    begin_ts / end_ts: Unix timestamps (UTC seconds).
    Returns {"success": bool, "url": str|None, "message": str, "drm": bool}.
    """
    if not is_logged_in():
        return {"success": False, "url": None, "message": "Not logged in.", "drm": False}

    channels = _get_fetcher_data()
    ch_data  = next((c for c in channels if str(c.get("id")) == str(channel_id)), None)
    if not ch_data:
        return {"success": False, "url": None,
                "message": f"Channel {channel_id} not found.", "drm": False}

    if not ch_data.get("is_catchup_available", False):
        return {"success": False, "url": None,
                "message": "Catchup is not available for this channel.", "drm": False}

    manifest_url = ch_data.get("manifest_url", "")
    if not manifest_url:
        return {"success": False, "url": None, "message": "No manifest URL.", "drm": False}

    from datetime import datetime, timezone
    begin_fmt = datetime.fromtimestamp(begin_ts, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    end_fmt   = datetime.fromtimestamp(end_ts,   tz=timezone.utc).strftime("%Y%m%dT%H%M%S")

    if "bpaita" not in manifest_url:
        # Possibly HLS direct — append time params
        sep = "&" if "?" in manifest_url else "?"
        catchup_url = f"{manifest_url}{sep}begin={begin_fmt}&end={end_fmt}"
        return {"success": True, "url": catchup_url, "message": "", "drm": False}

    # DASH + Akamai HMAC
    hmac = _get_hmac(channel_id)
    if not hmac:
        return {"success": False, "url": None,
                "message": "Could not obtain HMAC token for catchup.", "drm": True}

    catchup_manifest = manifest_url.replace("bpaita", "bpaicatchupta")
    catchup_url = f"{catchup_manifest}?{hmac}&begin={begin_fmt}&end={end_fmt}"
    return {"success": True, "url": catchup_url, "message": "", "drm": True}


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def logout() -> None:
    global _session, _jwt_cache, _hmac_cache, _fetcher_cache
    _session = {}
    _jwt_cache = {}
    _hmac_cache = {}
    _fetcher_cache = {"data": None, "ts": 0}
    try:
        if os.path.exists(_SESSION_FILE):
            os.remove(_SESSION_FILE)
    except Exception:
        pass
    LOG.info("tataplay: session cleared")
