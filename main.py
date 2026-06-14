from pyrogram.types import BotCommand
from logic import app, LOG, sweep_old_downloads, _retention_label, BOT_TOKEN, API_ID, API_HASH
import command  # registers all handlers as a side effect of import

_BOT_COMMANDS = [
    BotCommand("start",          "Welcome message"),
    BotCommand("help",           "All commands and usage guide"),
    BotCommand("rec",            "Record HLS/M3U8/DASH stream (wizard)"),
    BotCommand("drec",           "Direct record — no wizard, instant start"),
    BotCommand("reclink",        "Auto-extract stream from a web page"),
    BotCommand("download",       "Download from OTT platforms"),
    BotCommand("compress",       "Compress a video (reply to video)"),
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
    BotCommand("channel",        "Browse channels by category"),
    BotCommand("search",         "Search channels"),
    BotCommand("verify",         "Verify your account"),
    BotCommand("Admin_add",      "Add an admin by user ID (owner only)"),
    BotCommand("Admin_delete",   "Remove an admin by user ID (owner only)"),
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


if __name__ == "__main__":
    print("Starting Video Recorder Bot...")
    if not BOT_TOKEN or not API_ID or not API_HASH:
        raise SystemExit(
            "Missing BOT_TOKEN / API_ID / API_HASH.\n"
            "Set them as environment variables (Railway Variables tab, Replit Secrets, or bot/.env file)."
        )
    sweep_old_downloads()
    LOG.info(
        "Recordings will be auto-deleted from the server after %s.",
        _retention_label(),
    )
    app.run()
