# ==============================================================================
# __main__.py - Entry Point
# ==============================================================================
# Bootstraps the bot:
# - connects to DB
# - starts main bot
# - starts assistants
# - starts voice handler
# - loads plugins
# - waits until stopped
# - guarantees cleanup
#
# Added:
# - Pyrogram SQLite session lock retry
# - safer startup cleanup
# - safer shutdown cleanup
# ==============================================================================

import asyncio
import importlib
import sqlite3
import sys

from pyrogram import idle

# Raise the file descriptor limit on Linux to avoid "[Errno 24] Too many open files"
if sys.platform != "win32":
    try:
        import resource

        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target = min(65536, _hard)

        if _soft < _target:
            resource.setrlimit(
                resource.RLIMIT_NOFILE,
                (_target, _hard)
            )
    except Exception:
        pass

from HasiiMusic import (
    tune,
    app,
    config,
    db,
    logger,
    stop,
    userbot,
    yt,
)

from HasiiMusic.plugins import all_modules


async def boot_main_bot_with_retry(
    retries: int = 4,
    delays=(2, 4, 6, 10),
):
    """
    Start the main Pyrogram bot.

    Pyrogram stores the bot session in SQLite.
    If a previous process has not released the session file yet,
    SQLite may temporarily return:

        sqlite3.OperationalError: database is locked

    Retry startup instead of immediately terminating the bot.
    """

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            await app.boot()

            logger.info(
                f"✅ Main bot session opened successfully "
                f"(attempt {attempt}/{retries})"
            )

            return

        except sqlite3.OperationalError as e:
            last_error = e

            if "database is locked" not in str(e).lower():
                raise

            logger.warning(
                f"⚠️ Pyrogram session database is locked "
                f"(attempt {attempt}/{retries})."
            )

            if attempt >= retries:
                logger.error(
                    "❌ Pyrogram session database remained locked "
                    "after all startup retries."
                )
                raise

            delay = delays[min(attempt - 1, len(delays) - 1)]

            logger.warning(
                f"⏳ Waiting {delay}s before retrying Bot startup..."
            )

            await asyncio.sleep(delay)

        except Exception:
            raise

    if last_error:
        raise last_error


async def main():
    startup_completed = False

    try:
        # ==========================================================
        # 1. Connect to DB
        # ==========================================================
        await db.connect()

        # ==========================================================
        # 2. Start main Telegram bot
        # ==========================================================
        await boot_main_bot_with_retry()

        # ==========================================================
        # 3. Start assistant/userbot clients
        # ==========================================================
        await userbot.boot()

        # ==========================================================
        # 4. Initialize voice call handler
        # ==========================================================
        await tune.boot()

        # ==========================================================
        # 5. Load all plugins
        # ==========================================================
        for module in all_modules:
            try:
                importlib.import_module(
                    f"HasiiMusic.plugins.{module}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to load plugin {module}: {e}",
                    exc_info=True,
                )

        logger.info(
            f"🔌 Loaded {len(all_modules)} plugin modules."
        )

        # ==========================================================
        # 6. Download YouTube cookies if configured
        # ==========================================================
        if config.COOKIES_URL:
            try:
                await yt.save_cookies(config.COOKIES_URL)
            except Exception as e:
                logger.error(
                    f"Failed to download cookies: {e}"
                )

        # ==========================================================
        # 7. Load sudoers / blacklist
        # ==========================================================
        sudoers = await db.get_sudoers()

        app.sudoers.update(sudoers)
        app.sudo_filter.update(sudoers)

        app.bl_users.update(
            await db.get_blacklisted()
        )

        logger.info(
            f"👑 Loaded {len(app.sudoers)} sudo users."
        )

        logger.info(
            "\n🎉 Bot started successfully! "
            "Ready to play music! 🎵\n"
        )

        startup_completed = True

        # ==========================================================
        # 8. Keep running
        # ==========================================================
        try:
            await idle()

        except KeyboardInterrupt:
            logger.info(
                "Received stop signal..."
            )

        except Exception as e:
            logger.error(
                f"Error during idle: {e}",
                exc_info=True,
            )

    except Exception as e:
        logger.error(
            f"Critical error in main: {e}",
            exc_info=True,
        )

        raise

    finally:
        # ==========================================================
        # ALWAYS cleanup
        #
        # This is important:
        # even if startup fails halfway through,
        # already-open clients must be closed.
        # ==========================================================
        try:
            logger.info(
                "🧹 Running final cleanup..."
            )

            await stop()

        except Exception as cleanup_error:
            logger.error(
                f"⚠️ Cleanup error: {cleanup_error}",
                exc_info=True,
            )

        if startup_completed:
            logger.info(
                "✅ Bot shutdown sequence completed."
            )


if __name__ == "__main__":
    loop = None

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(main())

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped by user (Ctrl+C)"
        )

    except SystemExit as e:
        logger.error(
            f"Bot exited with system error: {e}"
        )

    except Exception as e:
        logger.error(
            f"Unexpected error caused bot to stop: {e}",
            exc_info=True,
        )

    finally:
        # ==========================================================
        # Final event-loop cleanup
        # ==========================================================
        if loop is not None:
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass