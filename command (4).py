import os
import json
import time
import asyncio
import shlex
import secrets as pysecrets
import urllib.request
import urllib.parse
from os.path import join
from datetime import datetime, timedelta
from typing import Optional

import pytz
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)

from logic import (
    app, LOG, tz, AUTH, is_owner, is_admin, add_admin, del_admin, load_admins,
    split_and_send_video,
    is_group_member, invalidate_member_cache,
    is_verified, load_verified, save_verified,
    render_plans_text, load_channels, _channel_root_kb,
    user_status, user_tasks, rec_setup_sessions, _wm_text_pending,
    user_ffmpeg_pids, progress_tasks, cancelled_users, pending_cookies_users,
    active_recs, cancelled_recs, MAX_CONCURRENT_REC, pending_uploads,
    ott_progress, compress_jobs, reclink_jobs, ss_jobs, merge_sessions,
    title_jobs, run_title, _title_kb, _title_menu_text, TITLE_POS_MAP,
    handle_record, do_record, handle_ott_download,
    take_stream_snapshot, _rec_progress_kb,
    _parse_rec_flags, _prepare_aes_input,
    _parse_cloudplay_format, _fetch_clearkey_keys_sync,
    _setup_summary, _kb_step1, _kb_step2, _kb_step3,
    _kb_audio_step, _audio_step_text, _audio_track_label,
    _ss_menu, _ss_menu_text, _ffprobe_video, run_screenshots,
    _compress_menu, _compress_status_text, COMPRESS_RES_CONFIG, COMPRESS_LANG_PRESET,
    run_compress, MERGE_MAX_VIDEOS, MERGE_SESSION_TTL, _merge_session_status, run_merge,
    _all_streams_compatible,
    _user_cookies_path, _user_has_cookies, _cookies_summary,
    _NETSCAPE_HEADER, _MAX_COOKIE_FILE_BYTES, _COOKIE_PROMPT_TTL_SEC,
    _resolve_chromium_path, _extract_streams_with_chromium,
    _load,
    _parse_duration_token, _seconds_to_hms, _safe_rmtree, schedule_retention_cleanup,
    _retention_label, TimeFormatter, runcmd, time_to_seconds,
    probe_stream, progress_for_pyrogram, LANG_LABEL,
    VERIFIED_FILE, PLANS_FILE, CHANNELS_FILE, ADMIN_FILE, AUDIO_NAME_FILE, WATERMARK_NAME_FILE,
    get_audio_brand_name, set_audio_brand_name,
    get_default_watermark, set_default_watermark,
    # Config constants
    API_ID, API_HASH, BOT_TOKEN, AUTH_USERS, OWNER_IDS,
    DOWNLOAD_DIRECTORY, DATA_DIRECTORY, COOKIES_DIRECTORY,
    RETENTION_HOURS, DEFAULT_METADATA, DEFAULT_FILENAME, DEFAULT_REC_DURATION,
    BRAND_TITLE, TIMEZONE, SUPPORT_USERNAME, SUPPORT_CHANNEL,
    GROUP_CHAT_ID, GROUP_INVITE_LINK, SHRINKME_API_KEY, BOT_USERNAME,
    GDRIVE_SA_JSON, GDRIVE_FOLDER_ID, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    # Google Drive helpers
    is_user_connected, disconnect_user, get_sa_email,
    start_device_flow_sync, poll_and_save_token,
    upload_and_notify, _is_enabled as _gdrive_is_enabled,
    _sa_enabled as _gdrive_sa_enabled, _oauth_enabled as _gdrive_oauth_enabled,
    # Limit system
    get_user, use_rec, apply_verify_bonus, format_limit_message,
    DEFAULT_VERIFY_LEFT, VERIFY_STEPS, REFRESH_SECONDS,
)

# ---------------------------------------------------------------------------
# Module-level state for ad-click verify flow
# ---------------------------------------------------------------------------

# {user_id: {"short_url": str, "expires": float}}
pending_verify: dict = {}

_VERIFY_LINK_TTL = 300  # seconds before link expires and a new one is generated


# ---------------------------------------------------------------------------
# Shrinkme.io URL shortener (sync wrapped in asyncio.to_thread)
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
    """Shorten via shrinkme.io. Returns the short URL, or the original on failure."""
    key     = SHRINKME_API_KEY
    encoded = urllib.parse.quote(long_url, safe=":/?&=%")
    api_url = f"https://shrinkme.io/api?api={key}&url={encoded}"
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

━━━ 🎬 **Recording** ━━━
• `/rec <url> HH:MM:SS <filename>` — Record HLS/M3U8/DASH stream (opens wizard).
• `/drec <url> HH:MM:SS [filename] [flags]` — Direct record, no wizard.
• `/reclink <page_url> HH:MM:SS <filename>` — Auto-extract stream from any page.
• Send any `.m3u8` URL directly for quick recording.

**Optional flags for `/rec` & `/drec`:**
  `-aes <hex>`       AES-128 decryption key (HLS)
  `-cookie <val>`    Cookie header
  `-ua <val>`        User-Agent
  `-referer <val>`   Referer header
  `-license <url>`   ClearKey DRM license URL (MPD/DASH)

**CloudPlay multi-line format** also supported — paste directly into `/rec`.

━━━ 📥 **OTT Download** ━━━
• `/download <url> [filename]` — Download from OTT (Hotstar, JioCinema, ZEE5, SonyLIV…)

━━━ 🎞 **Video Tools** ━━━
• `/compress` — Reply to a video to compress it.
• `/screenshot` or `/ss` — Reply to a video to extract screenshots.
• `/trim <start> <end>` — Trim a clip (e.g. `/trim 00:01:00 00:03:30`).
• `/merge` — Multi-video merge session (send videos, then `/merge_done`).
• `/Watermark <text>` — Reply to a video → burn watermark (last 2 min, bottom-right).
• `/audiotrack <name>` — Reply to a video → lock audio track metadata instantly (no re-encode).

━━━ ☁️ **Google Drive** ━━━
• `/gdrive` or `/googledrive` — Connect your Google Drive (OAuth).
• `/Drivelogout` — Disconnect your Drive account.

━━━ 🍪 **Cookies (OTT Login)** ━━━
• `/set_cookies` — Upload `cookies.txt` (Netscape format).
• `/cookies_status` — Show stored cookies.
• `/del_cookies` — Delete stored cookies.

━━━ 📊 **Status & Control** ━━━
• `/statusme` or `/status` — Your current recording/job status.
• `/cancelme` or `/cancel` — Cancel active recording/job.
• `/limit` — Check your recording quota.

━━━ ℹ️ **Info** ━━━
• `/start` — Welcome message.
• `/help` — This message.
• `/plan` — Subscription plans.
• `/contact` — Support contact.
• `/channel` — Browse channels by category.
• `/search <query>` — Search channels.

📡 Support: @{SUPPORT_CHANNEL}
"""

_OWNER_HELP_TEXT = """
━━━ 👑 **Owner / Admin Commands** ━━━

**Branding**
`/UpdateWatermark <text>` — Change default watermark text for all recordings.
`/audionameupdate <name>` — Change audio track brand name (embedded in MP4 metadata).

**User Management**
`/stats` — Bot statistics + new users last 3 days.
`/broadcast <msg>` — Send message to all users.
`/approve <user_id> [days]` — Approve a user manually.
`/revoke <user_id>` — Revoke a user's access.
`/pending` — List pending verification requests.

**Admin**
`/Admin_add <user_id>` — Add admin (bypasses group gate).
`/Admin_delete <user_id>` — Remove admin.
`/Admin_list` — List all admins.
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

    # ── Deep-link: /start vfy_{uid}  (user came via shrinkme.io ad click) ──
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
# Group membership gate — runs BEFORE all other handlers (group=-1)
# ---------------------------------------------------------------------------

@app.on_message(filters.private, group=-1)
async def _group_gate(client: Client, message: Message):
    uid = message.from_user.id
    if is_owner(uid) or is_admin(uid):
        return  # bypass — let through to normal handlers

    # Allow /start vfy_<uid> deep-link to pass so verification always lands
    text = (message.text or message.caption or "").strip()
    if text.startswith("/start vfy_"):
        return

    if not await is_group_member(client, uid):
        name = message.from_user.first_name or "User"
        kb   = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Join Group & Use Bot", url=GROUP_INVITE_LINK)
        ]])
        await message.reply_text(
            f"👋 Hi **{name}**, I only work in our official group.\n\n"
            "👉 Please join the group below to use this bot.\n\n"
            "**User Normal Plan** — Record live streams after joining.",
            reply_markup=kb,
        )
        message.stop_propagation()


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
# /verify — ad-click self-service verification (shrinkme.io)
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
        )
        if setup.get("auto_mode") and not was_cancelled and dur > 120:
            try:
                await status_msg.edit_text("✂️ Auto mode: generating first & last 1-min clips…")
            except Exception:
                pass
            clip_dir   = join(save_dir, "auto_clips")
            os.makedirs(clip_dir, exist_ok=True)
            first_clip = join(clip_dir, "first_1min.mkv")
            last_clip  = join(clip_dir, "last_1min.mkv")
            last_start = max(0, dur - 60)
            for cp, ss, to in [(first_clip, 0, 60), (last_clip, last_start, dur)]:
                await runcmd(
                    f'ffmpeg -hide_banner -loglevel error -nostats -y '
                    f'-ss {ss} -to {to} -i {shlex.quote(video_path)} '
                    f'-c copy {shlex.quote(cp)}'
                )
            for cp, label in [(first_clip, "⏮ First 1 min"), (last_clip, "⏭ Last 1 min")]:
                if os.path.exists(cp) and os.path.getsize(cp) > 0:
                    try:
                        await send_target.reply_video(
                            video=cp,
                            caption=(f"🎬 **{BRAND_TITLE}** — {label}\n"
                                     f"Channel: @{SUPPORT_CHANNEL}"),
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
# /UpdateWatermark — owner-only: change the default watermark text live
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["UpdateWatermark", "updatewatermark", "setwatermark", "wmark"]) & AUTH)
async def update_watermark_cmd(_, message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        current = get_default_watermark()
        return await message.reply_text(
            f"**Default Watermark Text**\n\n"
            f"Current: `{current}`\n\n"
            f"Usage: `/UpdateWatermark Your Channel Name`\n"
            f"Example: `/UpdateWatermark @LittleSinghamChannel`\n\n"
            f"Takes effect on the **next** recording. Users can still override it manually in the wizard."
        )
    new_name = parts[1].strip()
    set_default_watermark(new_name)
    await message.reply_text(
        f"✅ **Default watermark updated!**\n\n"
        f"New watermark: `{new_name}`\n\n"
        f"This will be the default watermark text for all new recordings.\n"
        f"Users can still change it per-recording via the wizard."
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
# /channel — browse channels by category → language
# ---------------------------------------------------------------------------

@app.on_message(filters.command("channel") & AUTH)
async def channel_cmd(client: Client, message: Message):
    await message.reply_text("**Browse channels**\n\nPick a category:", reply_markup=_channel_root_kb())


@app.on_callback_query(filters.regex(r"^chcat:(.+)$"))
async def cb_channel_cat(client: Client, cq: CallbackQuery):
    cat   = cq.data[6:]
    chans = load_channels()
    langs = list(chans.get("categories", {}).get(cat, {}).keys())
    if not langs:
        return await cq.answer("No languages found.", show_alert=True)
    rows = []
    row  = []
    for l in langs:
        row.append(InlineKeyboardButton(l, callback_data=f"chlang:{cat}:{l}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ Back", callback_data="chback")])
    await cq.message.edit_text(f"**{cat}** — pick a language:", reply_markup=InlineKeyboardMarkup(rows))
    await cq.answer()


@app.on_callback_query(filters.regex(r"^chlang:(.+):(.+)$"))
async def cb_channel_lang(client: Client, cq: CallbackQuery):
    _, cat, lang = cq.data.split(":", 2)
    chans        = load_channels()
    items        = chans.get("categories", {}).get(cat, {}).get(lang, [])
    if not items:
        return await cq.answer("No channels found.", show_alert=True)
    lines = [f"**{cat} — {lang}**\n"]
    for ch in items:
        if isinstance(ch, dict):
            name = ch.get("name", "?")
            url  = ch.get("url", "")
            lines.append(f"• {name}\n  `{url}`")
        else:
            lines.append(f"• `{ch}`")
    rows = [[InlineKeyboardButton("◀ Back", callback_data=f"chcat:{cat}")]]
    await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows),
                               disable_web_page_preview=True)
    await cq.answer()


@app.on_callback_query(filters.regex(r"^chback$"))
async def cb_channel_back(client: Client, cq: CallbackQuery):
    await cq.message.edit_text("**Browse channels**\n\nPick a category:",
                                reply_markup=_channel_root_kb())
    await cq.answer()


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------

@app.on_message(filters.command("search") & AUTH)
async def search_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/search <query>`")
    query = " ".join(message.command[1:]).lower()
    chans = load_channels()
    found = []
    for cat, langs in chans.get("categories", {}).items():
        for lang, items in langs.items():
            for ch in items:
                if isinstance(ch, dict):
                    name = ch.get("name", "")
                    url  = ch.get("url", "")
                    if query in name.lower() or query in url.lower():
                        found.append(f"**{name}** ({cat} / {lang})\n`{url}`")
                elif query in str(ch).lower():
                    found.append(f"`{ch}`")
    if not found:
        return await message.reply_text(f"No channels found for `{query}`.")
    lines = [f"🔍 **Results for `{query}`**\n"] + found[:20]
    if len(found) > 20:
        lines.append(f"\n…and {len(found) - 20} more.")
    await message.reply_text("\n\n".join(lines), disable_web_page_preview=True)


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

def _shlex_parse_rec(message_text: str):
    """Return (url, timestamp, filename, flags_dict) from a /rec or /drec message text.
    Uses shlex so quoted values with spaces work correctly."""
    # Strip leading /command word
    space = message_text.find(" ")
    rest  = message_text[space:].strip() if space != -1 else ""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    if len(tokens) < 2:
        return None, None, None, {}
    url       = tokens[0]
    timestamp = tokens[1]
    # filename: next token that doesn't start with '-'
    idx = 2
    raw_filename = DEFAULT_FILENAME
    if idx < len(tokens) and not tokens[idx].startswith("-"):
        raw_filename = tokens[idx]
        idx = 3
    flags = _parse_rec_flags(tokens[idx:])
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
        "audio_track":           0,
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
    msg_text = message.text or ""

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

    url, timestamp, raw_filename, flags = _shlex_parse_rec(message.text or "")
    if not url or not timestamp:
        return await message.reply_text(
            "**Usage:** `/drec <url> HH:MM:SS [filename] [flags]`\n\n"
            "Example:\n"
            "```\n/drec https://cdn.example.com/live.m3u8 00:30:00 MyShow\n"
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

    # Skip if it's a known command or not an HLS URL
    if text.startswith("/"):
        return
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

    if action == "audio_select":
        try:
            setup["audio_track"] = int(val)
        except ValueError:
            return await cq.answer("Bad value.", show_alert=True)
        tracks  = setup.get("detected_audio_tracks", [])
        sel     = setup["audio_track"]
        if sel == 0:
            label = "All Tracks"
        elif tracks and sel <= len(tracks):
            label = _audio_track_label(tracks[sel - 1])
        else:
            label = f"Track {sel}"
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
        setup["audio_track"] = (setup["audio_track"] + 1) % 5
        label = "All" if setup["audio_track"] == 0 else f"Track {setup['audio_track']}"
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
# /download — OTT downloader
# ---------------------------------------------------------------------------

@app.on_message(filters.command("download") & AUTH)
async def download_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_verified(user_id):
        return await message.reply_text("You must be **verified** to use /download. Run /verify.")
    if user_id in user_tasks:
        return await message.reply_text("You already have an active job. Use /statusme or /cancelme.")
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:** `/download <url> [filename]`\n\n"
            "Example:\n`/download https://www.hotstar.com/1260093240 MyShow`\n\n"
            "Supported: Hotstar, JioCinema, ZEE5, SonyLIV, Voot, MX Player, YouTube, and more.\n\n"
            "For login-gated content, upload cookies first with /set_cookies."
        )
    asyncio.create_task(handle_ott_download(client, message))


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

    src_msg   = message.reply_to_message
    src_media = None
    if src_msg:
        if src_msg.video:
            src_media = src_msg.video
        elif src_msg.document and (src_msg.document.mime_type or "").startswith("video/"):
            src_media = src_msg.document
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with /compress to start.\n\n"
            "Send the video first, then reply to it with `/compress`."
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
        default_lang = (["hin"] if "hin" in avail_langs
                        else ([avail_langs[0]] if avail_langs else ["multi"]))
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

    src_msg   = message.reply_to_message
    src_media = None
    if src_msg:
        if src_msg.video:
            src_media = src_msg.video
        elif src_msg.document and (src_msg.document.mime_type or "").startswith("video/"):
            src_media = src_msg.document
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with /screenshot to start.\n\n"
            "Send the video first, then reply to it with `/screenshot`."
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

    src_msg   = message.reply_to_message
    src_media = None
    if src_msg:
        if src_msg.video:
            src_media = src_msg.video
        elif src_msg.document and (src_msg.document.mime_type or "").startswith("video/"):
            src_media = src_msg.document
    if not src_media:
        return await message.reply_text(
            "**Reply to a video** with `/trim <start> <end>`.\n\n"
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
               f'-c copy -avoid_negative_ts make_zero {shlex.quote(out_path)}')
        rc, _o, err = await runcmd(cmd)

        if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await status.edit_text("Stream-copy trim failed; falling back to re-encode...")
            cmd2 = (f'ffmpeg -hide_banner -loglevel error -nostats -y '
                    f'-ss {start_sec} -to {clip_end} '
                    f'-i {shlex.quote(dl_path)} '
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

        upload_start = time.time()
        await split_and_send_video(
            status, out_path, caption, clip_len,
            thumb_path=thumb_path,
            status_msg=status,
            progress=progress_for_pyrogram,
            progress_args=(status, upload_start, status, save_dir, False),
        )
        asyncio.create_task(upload_and_notify(
            client, message.chat.id, out_path, os.path.basename(out_path)
        ))
        try:
            await status.edit_text(f"✂️ Trim done — uploaded `{out_size_mb:.1f} MB`.\n"
                                   f"Server copy auto-deletes in {_retention_label()}.")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Trim failed: {e}")
        err_text = str(e)
        if len(err_text) > 2500: err_text = "...[truncated]...\n" + err_text[-2500:]
        try: await status.edit_text(f"**Trim failed.**\n\n`{err_text}`")
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
    """Return (src_msg, src_media) for a replied video/document, else (None, None)."""
    src = message.reply_to_message
    if not src:
        return None, None
    if src.video:
        return src, src.video
    if src.document and (src.document.mime_type or "").startswith("video/"):
        return src, src.document
    return src, None


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

    parts = message.text.split(None, 1)
    wm_text = parts[1].strip() if len(parts) > 1 else get_default_watermark()
    if not wm_text:
        wm_text = get_default_watermark()

    save_dir = join(DOWNLOAD_DIRECTORY, f"wm_{int(time.time())}")
    os.makedirs(save_dir, exist_ok=True)
    status = await message.reply_text("⬇️ Downloading video...")
    user_tasks[user_id] = True

    try:
        dl_path  = await src_msg.download(file_name=join(save_dir, f"src_{src_msg.id}"))
        await status.edit_text("🔍 Probing video...")
        info     = await _ffprobe_video(dl_path)
        dur      = info["duration"]
        if dur <= 0:
            raise Exception("Could not determine video duration — ffprobe failed.")

        out_path = join(save_dir, f"wm_output_{int(time.time())}.mkv")

        # Watermark appears only in the last 2 minutes
        wm_start = max(0, dur - 120)
        safe_txt = wm_text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        vf_filter = (
            f"drawtext=text='{safe_txt}':fontsize=28:fontcolor=white"
            f":box=1:boxcolor=black@0.4:boxborderw=4"
            f":x=w-tw-10:y=h-th-10"
            f":enable='gte(t,{wm_start})'"
        )

        await status.edit_text(f"🎨 Burning watermark `{wm_text}` into video…\n_This may take a moment._")

        rc, _out, err = await runcmd(
            f'ffmpeg -hide_banner -loglevel error -nostats -y '
            f'-i {shlex.quote(dl_path)} '
            f'-vf {shlex.quote(vf_filter)} '
            f'-c:v libx264 -preset veryfast -crf 20 '
            f'-c:a copy '
            f'{shlex.quote(out_path)}'
        )
        if rc != 0:
            raise Exception(f"FFmpeg watermark failed:\n{err.strip()[-1500:]}")

        out_mb  = os.path.getsize(out_path) / (1024 * 1024)
        # Generate thumb
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
            f"Text: `{wm_text}`\n"
            f"Position: Bottom-Right (last 2 min)\n"
            f"Size: `{out_mb:.1f} MB` | Duration: `{_seconds_to_hms(int(dur))}`\n"
            f"Channel: @{SUPPORT_CHANNEL}\n\n"
            f"_Auto-deleted from server after {_retention_label()}._"
        )
        upload_start = time.time()
        await split_and_send_video(
            status, out_path, caption, int(dur),
            thumb_path=thumb if thumb_ok else None,
            status_msg=status,
            progress=progress_for_pyrogram,
            progress_args=(status, upload_start, status, save_dir, False),
        )
        try:
            await status.edit_text(f"✅ Watermark done — `{out_mb:.1f} MB` uploaded.")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            schedule_retention_cleanup(save_dir)

    except Exception as e:
        LOG.error(f"Watermark cmd failed uid={user_id}: {e}")
        err_text = str(e)
        if len(err_text) > 2500:
            err_text = "...[truncated]...\n" + err_text[-2500:]
        try:
            await status.edit_text(f"**Watermark failed.**\n\n`{err_text}`")
        except Exception:
            pass
        if save_dir and os.path.exists(save_dir):
            _safe_rmtree(save_dir)
    finally:
        user_tasks.pop(user_id, None)


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
        upload_start = time.time()
        await split_and_send_video(
            status, out_path, caption, int(dur),
            thumb_path=thumb if thumb_ok else None,
            status_msg=status,
            progress=progress_for_pyrogram,
            progress_args=(status, upload_start, status, save_dir, False),
        )
        try:
            await status.edit_text(f"✅ Audio lock done — `{out_mb:.1f} MB` uploaded.")
        except Exception:
            pass
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


@app.on_message(filters.private & (filters.video | filters.document) & AUTH, group=1)
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
                               "codec_v": codec_v, "codec_a": codec_a})
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
    await cq.message.reply_text("**Browse channels**\n\nPick a category:", reply_markup=_channel_root_kb())
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
