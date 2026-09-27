# ==============================================================================
# bot.py - Main Bot Client
# ==============================================================================
# Main Telegram Bot client.
#
# Important:
# The main bot uses BOT_TOKEN, so we use an in-memory Pyrogram session.
# This prevents Pyrogram from creating/locking HasiiMusic.session SQLite file.
#
# Userbot sessions are separate and continue using session strings.
# ==============================================================================

import pyrogram
from typing import Optional

from HasiiMusic import config, logger


class Bot(pyrogram.Client):

    def __init__(self):
        """
        Initialize the main Telegram Bot.

        The main bot uses BOT_TOKEN, therefore an in-memory session is
        sufficient and avoids the local SQLite session lock problem.
        """

        super().__init__(
            name="HasiiMusic",

            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,

            # IMPORTANT:
            # Do not use HasiiMusic.session SQLite storage.
            in_memory=True,

            parse_mode=pyrogram.enums.ParseMode.HTML,

            max_concurrent_transmissions=3,

            link_preview_options=pyrogram.types.LinkPreviewOptions(
                is_disabled=True
            ),
        )

        # ----------------------------------------------------------------------
        # Bot configuration
        # ----------------------------------------------------------------------

        self.owner: int = config.OWNER_ID
        self.logger: int = config.LOGGER_ID

        # Blacklist filter
        self.bl_users: pyrogram.filters.Filter = pyrogram.filters.user()

        # Sudo users
        self.sudoers: set = {self.owner}

        self.sudo_filter: pyrogram.filters.Filter = (
            pyrogram.filters.user(self.owner)
        )

        # ----------------------------------------------------------------------
        # Runtime information
        # ----------------------------------------------------------------------

        self.id: Optional[int] = None
        self.name: Optional[str] = None
        self.username: Optional[str] = None
        self.mention: Optional[str] = None

    # ==========================================================================
    # START / BOOT
    # ==========================================================================

    async def boot(self) -> None:
        """
        Start the main Telegram Bot and verify logger access.
        """

        try:
            # ------------------------------------------------------------------
            # Start Pyrogram
            # ------------------------------------------------------------------

            await super().start()

            # ------------------------------------------------------------------
            # Get bot information
            # ------------------------------------------------------------------

            self.id = self.me.id
            self.name = self.me.first_name
            self.username = self.me.username
            self.mention = self.me.mention

            # ------------------------------------------------------------------
            # Logger group/channel check
            # ------------------------------------------------------------------

            try:
                await self.send_message(
                    self.logger,
                    "🤖 ʙᴏᴛ ꜱᴛᴀʀᴛᴇᴅ"
                )

                member = await self.get_chat_member(
                    self.logger,
                    self.id
                )

            except Exception as ex:

                logger.warning(
                    f"⚠️ Failed to access logger group/channel "
                    f"{self.logger}: {ex}"
                )

                # Close the bot if logger initialization fails.
                try:
                    if self.is_connected:
                        await super().stop()
                except Exception as stop_error:
                    logger.warning(
                        f"⚠️ Failed to stop bot after logger error: "
                        f"{stop_error}"
                    )

                raise SystemExit(
                    f"❌ ʙᴏᴛ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴄᴄᴇꜱꜱ "
                    f"ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                    f"ʀᴇᴀꜱᴏɴ: {ex}\n"
                    f"ᴘʟᴇᴀꜱᴇ ᴇɴꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ "
                    f"ᴀᴅᴅᴇᴅ ᴛᴏ ᴛʜᴇ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ."
                )

            # ------------------------------------------------------------------
            # Administrator check
            # ------------------------------------------------------------------

            if member.status != pyrogram.enums.ChatMemberStatus.ADMINISTRATOR:

                logger.error(
                    f"❌ Bot is not administrator in logger group: "
                    f"{self.logger}"
                )

                try:
                    if self.is_connected:
                        await super().stop()
                except Exception as stop_error:
                    logger.warning(
                        f"⚠️ Failed to stop bot after admin check failure: "
                        f"{stop_error}"
                    )

                raise SystemExit(
                    f"❌ ʙᴏᴛ ɪꜱ ɴᴏᴛ ᴀɴ "
                    f"ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ ɪɴ "
                    f"ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                    f"ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴍᴏᴛᴇ ᴛʜᴇ ʙᴏᴛ "
                    f"ᴛᴏ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ."
                )

            # ------------------------------------------------------------------
            # Startup successful
            # ------------------------------------------------------------------

            logger.info(
                f"🤖 Bot started successfully as @{self.username}"
            )

            logger.info(
                "✅ Main bot is using in-memory Pyrogram session "
                "(SQLite session lock protection enabled)."
            )

        except SystemExit:
            raise

        except Exception:
            # If boot itself fails, make sure the client is closed.
            try:
                if self.is_connected:
                    await super().stop()
            except Exception as stop_error:
                logger.warning(
                    f"⚠️ Failed to stop bot after boot exception: "
                    f"{stop_error}"
                )

            raise

    # ==========================================================================
    # EXIT / SHUTDOWN
    # ==========================================================================

    async def exit(self) -> None:
        """
        Safely stop the main Telegram Bot.

        Since the main Bot uses an in-memory session, there is no
        HasiiMusic.session SQLite file that needs to be unlocked.
        """

        try:

            if not self.is_connected:
                logger.info(
                    "ℹ️ Bot client already disconnected."
                )
                return

            logger.info(
                "🛑 Stopping main Bot client..."
            )

            await super().stop()

            logger.info(
                "🤖 Bot client stopped."
            )

        except Exception as e:

            logger.warning(
                f"⚠️ Error stopping main bot client: {e}"
            )

            # Never interrupt the global shutdown sequence.
            return