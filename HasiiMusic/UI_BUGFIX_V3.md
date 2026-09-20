# UI Bugfix V3

- Fixed Pyrogram InlineKeyboardButton color styles by converting `primary`/`success`/`danger` strings to `pyrogram.enums.ButtonStyle` values before constructing buttons.
- Start menu Help text now comes from the active locale (`lang["help"]`), so Chinese uses `帮助` while other languages retain their localized text.
- Callback data and non-UI bot logic were not changed.
