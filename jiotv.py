"""
JioTV API module — real implementation.
Handles OTP login, token refresh, channel list, live + catchup stream URLs.
Based on JioTv PHP reference (jitendraunatti.php / live.php).
"""

import os
import json
import time
import base64
import hashlib
import logging
import random
import string
import requests

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session storage
# ---------------------------------------------------------------------------
_DATA_DIR = os.environ.get(
    "DATA_DIRECTORY",
    os.path.join(os.path.dirname(__file__), "data"),
)
_SESSION_FILE = os.path.join(_DATA_DIR, "jiotv_session.json")
_PHONE_FILE   = os.path.join(_DATA_DIR, "jiotv_pending_phone.txt")

_session: dict = {}

def _load_session() -> None:
    global _session
    try:
        if os.path.exists(_SESSION_FILE):
            with open(_SESSION_FILE, "r", encoding="utf-8") as f:
                _session = json.load(f)
    except Exception as e:
        LOG.warning("jiotv: could not load session: %s", e)
        _session = {}

def _save_session() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(_session, f)
    except Exception as e:
        LOG.warning("jiotv: could not save session: %s", e)

_load_session()

# ---------------------------------------------------------------------------
# Device / app constants (matching PHP reference)
# ---------------------------------------------------------------------------
_APP_NAME    = "RJIL_JioTV"
_OS          = "android"
_DEVICE_TYPE = "phone"
_VERSION     = "353"
_OS_VERSION  = "13"
_DEVICE_NAME = "V2302A"
_P_NAME      = "PD2302"
_MANUFACTURER = "vivo"
_UA_OKHTTP   = "okhttp/4.9.3"
_UA_PLAYTV   = "plaYtv/7.1.5 (Linux;Android 13) ExoPlayerLib/2.11.7"

def _rand_android_id() -> str:
    return hashlib.sha1(
        (str(time.time()) + str(random.randint(0, 99))).encode()
    ).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def is_logged_in() -> bool:
    return bool(_session.get("authToken") or _session.get("ssoToken"))

def get_session_info() -> dict:
    """Return safe summary of current session for display."""
    if not is_logged_in():
        return {}
    sa = _session.get("sessionAttributes", {}).get("user", {})
    return {
        "subscriberId": sa.get("subscriberId", ""),
        "uid":          sa.get("uid", ""),
        "name":         sa.get("name", "JioTV User"),
        "unique":       sa.get("unique", ""),
    }

# ---------------------------------------------------------------------------
# OTP flow
# ---------------------------------------------------------------------------

def send_otp(phone: str) -> dict:
    """Send OTP to the given 10-digit Indian mobile number."""
    phone = phone.strip()
    if not phone.isdigit() or len(phone) != 10:
        return {"success": False, "message": "❌ Enter a valid 10-digit mobile number."}

    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_PHONE_FILE, "w") as f:
            f.write(phone)
    except Exception:
        pass

    url  = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/send?langId=6"
    body = json.dumps({"number": base64.b64encode(f"+91{phone}".encode()).decode()})
    headers = {
        "appName":      _APP_NAME,
        "os":           _OS,
        "devicetype":   _DEVICE_TYPE,
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "Host":         "jiotvapi.media.jio.com",
        "Accept-Encoding": "gzip",
        "User-Agent":   _UA_OKHTTP,
    }
    try:
        resp = requests.post(url, data=body.encode(), headers=headers, timeout=15)
        if resp.status_code == 204:
            return {"success": True, "message": "✅ OTP sent successfully to your Jio number.\nEnter OTP with `/otp <code>`"}
        elif resp.status_code == 400:
            return {"success": False, "message": "❌ OTP sending failed. Check your number and try again."}
        else:
            return {"success": False, "message": f"❌ Unexpected response (HTTP {resp.status_code}). Try again."}
    except Exception as e:
        LOG.error("jiotv send_otp error: %s", e)
        return {"success": False, "message": f"❌ Network error: {e}"}


def verify_otp(otp: str) -> dict:
    """Verify the OTP and store session credentials."""
    otp = otp.strip()
    if not otp.isdigit() or len(otp) < 4:
        return {"success": False, "message": "❌ Enter the OTP you received."}

    phone = ""
    try:
        if os.path.exists(_PHONE_FILE):
            with open(_PHONE_FILE) as f:
                phone = f.read().strip()
    except Exception:
        pass

    if not phone:
        return {"success": False, "message": "❌ No pending login. Use `/login <phone>` first."}

    url  = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/verify?langId=6"
    body = json.dumps({
        "number": base64.b64encode(f"+91{phone}".encode()).decode(),
        "otp": otp,
        "deviceInfo": {
            "consumptionDeviceName": _DEVICE_NAME,
            "info": {
                "type": _OS,
                "platform": {"name": _P_NAME},
                "androidId": _rand_android_id(),
            },
        },
    })
    headers = {
        "appName":      _APP_NAME,
        "os":           _OS,
        "devicetype":   _DEVICE_TYPE,
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "Host":         "jiotvapi.media.jio.com",
        "Accept-Encoding": "gzip",
        "User-Agent":   _UA_OKHTTP,
    }
    try:
        resp = requests.post(url, data=body.encode(), headers=headers, timeout=15)
        data = resp.json()
    except Exception as e:
        LOG.error("jiotv verify_otp error: %s", e)
        return {"success": False, "message": f"❌ Network error: {e}"}

    if data.get("code") == "1043":
        return {"success": False, "message": f"❌ {data.get('message', 'Invalid OTP')}"}
    if not data.get("authToken"):
        return {"success": False, "message": f"❌ Login failed: {data.get('message', 'Unknown error')}"}

    global _session
    _session = data
    _save_session()

    try:
        os.remove(_PHONE_FILE)
    except Exception:
        pass

    _refresh_token()

    sa   = data.get("sessionAttributes", {}).get("user", {})
    name = sa.get("name") or sa.get("subscriberId") or "JioTV User"
    return {"success": True, "message": f"✅ Logged in as **{name}**"}


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def _refresh_token() -> bool:
    """Refresh the access token using the stored refreshToken."""
    if not _session.get("refreshToken"):
        return False
    url  = "https://auth.media.jio.com/tokenservice/apis/v1/refreshtoken?langId=6"
    body = json.dumps({
        "appName":     _APP_NAME,
        "deviceId":    _session.get("deviceId", ""),
        "refreshToken": _session["refreshToken"],
    })
    sa  = _session.get("sessionAttributes", {}).get("user", {})
    headers = {
        "accesstoken":  _session.get("authToken", ""),
        "uniqueId":     sa.get("unique", ""),
        "devicetype":   _DEVICE_TYPE,
        "versionCode":  _VERSION,
        "os":           _OS,
        "Connection":   "Keep-Alive",
        "Content-Type": "application/json",
        "User-Agent":   _UA_OKHTTP,
    }
    try:
        resp = requests.post(url, data=body.encode(), headers=headers, timeout=15)
        data = resp.json()
        new_token = data.get("authToken") or data.get("accessToken")
        if new_token:
            _session["_refresh_authToken"] = new_token
            _save_session()
            LOG.info("jiotv: token refreshed")
            return True
    except Exception as e:
        LOG.warning("jiotv: token refresh failed: %s", e)
    return False


def _get_access_token() -> str:
    """Return current access token (refreshed if available)."""
    return _session.get("_refresh_authToken") or _session.get("authToken", "")


# ---------------------------------------------------------------------------
# Auth headers for authenticated API calls
# ---------------------------------------------------------------------------

def _auth_headers(channel_id: str = "") -> dict:
    sa = _session.get("sessionAttributes", {}).get("user", {})
    return {
        "os":           _OS,
        "appName":      _APP_NAME,
        "subscriberid": sa.get("subscriberId", ""),
        "accesstoken":  _get_access_token(),
        "deviceid":     _session.get("deviceId", ""),
        "userid":       sa.get("uid", ""),
        "versionCode":  _VERSION,
        "devicetype":   _DEVICE_TYPE,
        "crmid":        sa.get("subscriberId", ""),
        "osversion":    _OS_VERSION,
        "srno":         "240727144017",
        "usergroup":    "tvYR7NSNn7rymo3F",
        "x-platform":   "android",
        "uniqueid":     sa.get("unique", ""),
        "ssotoken":     _session.get("ssoToken", ""),
        "channel_id":   str(channel_id),
        "user-agent":   _UA_PLAYTV,
        "accept-encoding": "gzip, deflate",
    }

# ---------------------------------------------------------------------------
# Channel list
# ---------------------------------------------------------------------------

def get_channels() -> list:
    """Return list of JioTV channels with catchup info."""
    if not is_logged_in():
        return []
    url = (
        "https://jiotvapi.media.jio.com/apis/v1.3/render/channel/get"
        "?langId=6&offset=0&filterByTag=&filterBy=&isDth=0&city=&onHisnet=0"
    )
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=20)
        data = resp.json()
        channels = data.get("result", [])
        if not channels:
            channels = data.get("channelList", [])
        return channels
    except Exception as e:
        LOG.error("jiotv get_channels error: %s", e)
        return []


def search_channel(name: str) -> list:
    """Fuzzy-search channels by name, return top matches."""
    channels = get_channels()
    name_lower = name.lower().strip()
    exact, partial = [], []
    for ch in channels:
        ch_name = (ch.get("channelName") or ch.get("name") or "").lower()
        if ch_name == name_lower:
            exact.append(ch)
        elif name_lower in ch_name or ch_name in name_lower:
            partial.append(ch)
    return (exact + partial)[:10]


# ---------------------------------------------------------------------------
# Live stream URL
# ---------------------------------------------------------------------------

def _stream_headers(channel_id: str) -> dict:
    """
    Headers for the JioTV geturl endpoint — matches live.php exactly.
    Note: userid must be subscriberId (not uid) per PHP reference.
    """
    sa = _session.get("sessionAttributes", {}).get("user", {})
    body_str = f"stream_type=Seek&channel_id={channel_id}"
    return {
        "appkey":        "NzNiMDhlYzQyNjJm",
        "devicetype":    _DEVICE_TYPE,
        "os":            _OS,
        "deviceid":      _session.get("deviceId", ""),
        "osversion":     _OS_VERSION,
        "dm":            _P_NAME,
        "uniqueid":      sa.get("unique", ""),
        "usergroup":     "tvYR7NSNn7rymo3F",
        "languageid":    "6",
        "userid":        sa.get("subscriberId", ""),   # subscriberId, NOT uid
        "sid":           _session.get("analyticsId", ""),
        "crmid":         sa.get("subscriberId", ""),
        "isott":         "false",
        "channel_id":    str(channel_id),
        "langid":        "",
        "camid":         "",
        "accesstoken":   _get_access_token(),
        "subscriberid":  sa.get("subscriberId", ""),
        "lbcookie":      "1",
        "versioncode":   _VERSION,
        "content-type":  "application/x-www-form-urlencoded",
        "content-length": str(len(body_str)),
        "accept-encoding": "gzip",
        "user-agent":    _UA_OKHTTP,
    }


def get_stream_url(channel_id: str) -> dict:
    """Get live HLS stream URL for a channel."""
    if not is_logged_in():
        return {"success": False, "url": None, "message": "Not logged in."}

    url  = "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6"
    body = f"stream_type=Seek&channel_id={channel_id}"
    try:
        resp = requests.post(url, data=body, headers=_stream_headers(channel_id), timeout=15)
        data = resp.json()
        if data.get("code") == 419:
            _refresh_token()
            return get_stream_url(channel_id)
        if data.get("code") == 200 and data.get("result"):
            return {"success": True, "url": data["result"], "message": ""}
        return {"success": False, "url": None,
                "message": data.get("message", "Stream URL fetch failed.")}
    except Exception as e:
        LOG.error("jiotv get_stream_url error: %s", e)
        return {"success": False, "url": None, "message": str(e)}


# ---------------------------------------------------------------------------
# Catchup stream URL
# ---------------------------------------------------------------------------

def _catchup_headers(channel_id: str, begin_ts: int, end_ts: int) -> dict:
    """Headers for catchup geturl — same structure as live, different body."""
    sa = _session.get("sessionAttributes", {}).get("user", {})
    body_str = f"stream_type=Catchup&channel_id={channel_id}&begin={begin_ts}&end={end_ts}"
    return {
        "appkey":        "NzNiMDhlYzQyNjJm",
        "devicetype":    _DEVICE_TYPE,
        "os":            _OS,
        "deviceid":      _session.get("deviceId", ""),
        "osversion":     _OS_VERSION,
        "dm":            _P_NAME,
        "uniqueid":      sa.get("unique", ""),
        "usergroup":     "tvYR7NSNn7rymo3F",
        "languageid":    "6",
        "userid":        sa.get("subscriberId", ""),   # subscriberId per PHP ref
        "sid":           _session.get("analyticsId", ""),
        "crmid":         sa.get("subscriberId", ""),
        "isott":         "false",
        "channel_id":    str(channel_id),
        "langid":        "",
        "camid":         "",
        "accesstoken":   _get_access_token(),
        "subscriberid":  sa.get("subscriberId", ""),
        "lbcookie":      "1",
        "versioncode":   _VERSION,
        "content-type":  "application/x-www-form-urlencoded",
        "content-length": str(len(body_str)),
        "accept-encoding": "gzip",
        "user-agent":    _UA_OKHTTP,
    }


def get_catchup_url(channel_id: str, begin_ts: int, end_ts: int) -> dict:
    """
    Get HLS catchup stream URL for a past time range.
    begin_ts / end_ts: Unix timestamps (seconds, IST).
    Returns {"success": bool, "url": str|None, "message": str}.
    """
    if not is_logged_in():
        return {"success": False, "url": None, "message": "Not logged in."}

    url  = "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6"
    body = f"stream_type=Catchup&channel_id={channel_id}&begin={begin_ts}&end={end_ts}"
    try:
        resp = requests.post(url, data=body,
                             headers=_catchup_headers(channel_id, begin_ts, end_ts),
                             timeout=15)
        data = resp.json()
        if data.get("code") == 419:
            _refresh_token()
            return get_catchup_url(channel_id, begin_ts, end_ts)
        if data.get("code") == 200 and data.get("result"):
            return {"success": True, "url": data["result"], "message": ""}
        msg = data.get("message", "Catchup URL fetch failed.")
        LOG.warning("jiotv catchup failed: %s | body=%s", data, body)
        return {"success": False, "url": None, "message": msg}
    except Exception as e:
        LOG.error("jiotv get_catchup_url error: %s", e)
        return {"success": False, "url": None, "message": str(e)}


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def logout() -> None:
    global _session
    _session = {}
    for path in (_SESSION_FILE, _PHONE_FILE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    LOG.info("jiotv: session cleared")
