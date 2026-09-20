# UI BUGFIX V2

Changes in this build:
- `/help` works in private chats and groups; Telegram command mentions such as `/help@CHANMusic_bot` are handled by the existing Pyrogram command filter.
- Removed incompatible direct Pyrogram `quote=` usage from the user-facing start/help handlers. The remaining `quote=True` in `plugins/info/start.py` belongs to the project's custom `utils.safe_text()` helper and was intentionally preserved.
- `/vplay` keeps using the existing `play_usage` language key; no `vplay_usage` key was introduced.
- Help buttons now use functional icons without colored-circle emoji pretending to be button colors.
- Help/start buttons request Telegram Bot API `style` values (`primary`, `success`, `danger`) through a compatibility wrapper. If the installed Pyrogram build does not support `style`, the wrapper automatically falls back to normal inline buttons instead of crashing.
- The current cover image and media assets were not changed.

Validation:
- Python `compileall` passed for the project.
