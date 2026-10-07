# ==============================================================================
# autoplay.py - Automatic related-track playback
# ==============================================================================

from pyrogram import filters, types

from HasiiMusic import app, db, lang
from HasiiMusic.helpers import can_manage_vc


@app.on_message(filters.command("autoplay") & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _autoplay(_, m: types.Message):
    try:
        await m.delete()
    except Exception:
        pass

    if len(m.command) == 1:
        enabled = await db.get_autoplay(m.chat.id)
        return await m.reply_text(m.lang["autoplay_status"].format("ON" if enabled else "OFF"))

    value = m.command[1].lower()
    if value not in ("on", "off"):
        return await m.reply_text(m.lang["autoplay_usage"])

    enabled = value == "on"
    await db.set_autoplay(m.chat.id, enabled)
    key = "autoplay_enabled" if enabled else "autoplay_disabled"
    return await m.reply_text(m.lang[key])
