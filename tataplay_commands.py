"""
Tata Play bot command handlers for Pyrogram.
Implements: /tplogin, /tpotp, /tpstatus, /tpchannels
"""

import asyncio
import logging
from pyrogram import filters
import tataplay

LOG = logging.getLogger(__name__)

# Store user sessions {user_id: {"service": "tataplay", "status": str}}
_user_sessions = {}


def setup_tataplay_commands(app):
    """Register all Tata Play commands with the bot."""
    
    @app.on_message(filters.command("tplogin"))
    async def tplogin(client, message):
        """
        Start Tata Play login with SID (Subscriber ID).
        Usage: /tplogin <10-digit-SID>
        """
        user_id = message.from_user.id
        args = message.text.split(maxsplit=1)
        
        if len(args) < 2:
            return await message.reply_text(
                "❌ **Usage:** `/tplogin <Subscriber_ID>`\n\n"
                "**Example:** `/tplogin 1234567890`\n\n"
                "Your Subscriber ID is a 10-digit number found on your Tata Play bill."
            )
        
        sid = args[1].strip()
        
        try:
            # Call send_otp from tataplay module
            result = await asyncio.to_thread(tataplay.send_otp, sid)
            
            if result.get("success"):
                _user_sessions[user_id] = {
                    "service": "tataplay",
                    "status": "otp_sent",
                    "sid": sid,
                }
                return await message.reply_text(result["message"])
            else:
                return await message.reply_text(result.get("message", "❌ Login failed."))
        except Exception as e:
            LOG.error("tplogin error: %s", e)
            return await message.reply_text(f"❌ Error: {str(e)}")
    
    
    @app.on_message(filters.command("tpotp"))
    async def tpotp(client, message):
        """
        Submit OTP for Tata Play login.
        Usage: /tpotp <6-digit-OTP>
        """
        user_id = message.from_user.id
        args = message.text.split(maxsplit=1)
        
        if len(args) < 2:
            return await message.reply_text(
                "❌ **Usage:** `/tpotp <6-digit-OTP>`\n\n"
                "Enter the OTP sent to your registered phone/email."
            )
        
        # Check if user has pending login
        if user_id not in _user_sessions or _user_sessions[user_id].get("service") != "tataplay":
            return await message.reply_text(
                "❌ No pending Tata Play login.\n\n"
                "Use `/tplogin <SID>` first."
            )
        
        otp = args[1].strip()
        
        try:
            result = await asyncio.to_thread(tataplay.verify_otp, otp)
            
            if result.get("success"):
                _user_sessions[user_id]["status"] = "logged_in"
                return await message.reply_text(
                    result.get("message", "✅ Login successful!") + 
                    "\n\nUse `/tpchannels` to browse channels."
                )
            else:
                return await message.reply_text(result.get("message", "❌ OTP verification failed."))
        except Exception as e:
            LOG.error("tpotp error: %s", e)
            return await message.reply_text(f"❌ Error: {str(e)}")
    
    
    @app.on_message(filters.command("tpstatus"))
    async def tpstatus(client, message):
        """Check Tata Play login status."""
        user_id = message.from_user.id
        
        # Check module-level login status
        try:
            is_logged = await asyncio.to_thread(tataplay.is_logged_in)
        except Exception as is_logged:
            is_logged = False
        
        if not is_logged:
            return await message.reply_text(
                "❌ Not logged in to Tata Play.\n\n"
                "Use `/tplogin <SID>` to login."
            )
        
        try:
            session_info = await asyncio.to_thread(tataplay.get_session_info)
            status_msg = (
                "✅ **Tata Play Status:**\n\n"
                f"📱 **SID:** `{session_info.get('sid', 'N/A')}`\n"
                f"👤 **Name:** {session_info.get('sname', 'N/A')}\n"
                f"🟢 Status: Logged In\n\n"
                "Use `/tpchannels` to browse channels."
            )
            return await message.reply_text(status_msg)
        except Exception as e:
            LOG.error("tpstatus error: %s", e)
            return await message.reply_text(f"❌ Error fetching status: {str(e)}")
    
    
    @app.on_message(filters.command("tpchannels"))
    async def tpchannels(client, message):
        """
        Browse Tata Play channels.
        Usage: /tpchannels [search_query]
        """
        user_id = message.from_user.id
        args = message.text.split(maxsplit=1)
        search_query = args[1] if len(args) > 1 else ""
        
        try:
            is_logged = await asyncio.to_thread(tataplay.is_logged_in)
        except Exception:
            is_logged = False
        
        if not is_logged:
            return await message.reply_text(
                "❌ Not logged in to Tata Play.\n\n"
                "Use `/tplogin <SID>` and `/tpotp <OTP>` first."
            )
        
        try:
            if search_query:
                channels = await asyncio.to_thread(tataplay.search_channel, search_query)
            else:
                channels = await asyncio.to_thread(tataplay.get_channels)
            
            if not channels:
                return await message.reply_text(
                    f"❌ No channels found{f' for \"{search_query}\"' if search_query else ''}."
                )
            
            # Format channel list
            channel_list = f"📺 **Tata Play Channels**"
            if search_query:
                channel_list += f" (matching \"{search_query}\")"
            channel_list += ":\n\n"
            
            for i, ch in enumerate(channels[:20], 1):  # Limit to 20 channels
                ch_id = ch.get("id") or ch.get("channel_id") or "?"
                ch_name = ch.get("name") or ch.get("channelName") or "Unknown"
                channel_list += f"{i}. **{ch_name}** (`{ch_id}`)\n"
            
            if len(channels) > 20:
                channel_list += f"\n... and {len(channels) - 20} more channels.\n"
            
            channel_list += "\nUse `/tpstream <channel_id>` to play a channel."
            
            await message.reply_text(channel_list)
        except Exception as e:
            LOG.error("tpchannels error: %s", e)
            await message.reply_text(f"❌ Error fetching channels: {str(e)}")
    
    
    @app.on_message(filters.command("tplogout"))
    async def tplogout(client, message):
        """Logout from Tata Play."""
        user_id = message.from_user.id
        
        try:
            await asyncio.to_thread(tataplay.logout)
            if user_id in _user_sessions:
                del _user_sessions[user_id]
            return await message.reply_text("✅ Logged out from Tata Play.")
        except Exception as e:
            LOG.error("tplogout error: %s", e)
            return await message.reply_text(f"❌ Logout error: {str(e)}")
