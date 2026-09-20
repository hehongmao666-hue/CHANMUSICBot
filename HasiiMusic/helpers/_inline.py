# ==============================================================================
# _inline.py - Keyboard Buttons
# ==============================================================================
# Helper methods to generate all the inline keyboards
# (play controls, help menus, etc).
# ==============================================================================

from pyrogram import enums, types

from HasiiMusic import app, config, lang


class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton

    def _button(
        self,
        *,
        text,
        callback_data=None,
        url=None,
        copy_text=None,
        style=None,
    ):
        """Create an inline button with optional Bot API style support.

        Pyrogram expects ButtonStyle enum values for the style field.

        Supported styles:
            primary = blue
            success = green
            danger  = red

        If the installed Pyrogram build does not support style,
        automatically fall back to a normal button.
        """

        kwargs = {"text": text}

        if callback_data is not None:
            kwargs["callback_data"] = callback_data

        if url is not None:
            kwargs["url"] = url

        if copy_text is not None:
            kwargs["copy_text"] = copy_text

        if style is not None:
            # Convert string style names to Pyrogram ButtonStyle enums.
            if isinstance(style, str):
                style_map = {
                    "primary": enums.ButtonStyle.PRIMARY,
                    "success": enums.ButtonStyle.SUCCESS,
                    "danger": enums.ButtonStyle.DANGER,
                }

                style = style_map.get(style.lower())

            if style is not None:
                try:
                    return self.ikb(
                        **kwargs,
                        style=style,
                    )
                except TypeError:
                    # Compatibility fallback for older Pyrogram builds.
                    pass

        return self.ikb(**kwargs)

    # ==========================================================================
    # DOWNLOAD CANCEL
    # ==========================================================================

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text=f"❌ {text}",
                        callback_data="cancel_dl",
                    )
                ]
            ]
        )

    # ==========================================================================
    # PLAYER CONTROLS
    # ==========================================================================

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
    ) -> types.InlineKeyboardMarkup:

        keyboard = []

        if status:
            keyboard.append(
                [
                    self.ikb(
                        text=f"🎵 {status}",
                        callback_data=f"controls status {chat_id}",
                    )
                ]
            )

        elif timer:
            keyboard.append(
                [
                    self.ikb(
                        text=f"⏱️ {timer}",
                        callback_data=f"controls status {chat_id}",
                    )
                ]
            )

        if not remove:
            keyboard.append(
                [
                    self.ikb(
                        text="⏪ 30s",
                        callback_data=f"controls seek_back_30 {chat_id}",
                    ),
                    self.ikb(
                        text="⏪ 10s",
                        callback_data=f"controls seek_back_10 {chat_id}",
                    ),
                    self.ikb(
                        text="10s ⏩",
                        callback_data=f"controls seek_forward_10 {chat_id}",
                    ),
                    self.ikb(
                        text="30s ⏩",
                        callback_data=f"controls seek_forward_30 {chat_id}",
                    ),
                ]
            )

            keyboard.append(
                [
                    self.ikb(
                        text="▶️",
                        callback_data=f"controls resume {chat_id}",
                    ),
                    self.ikb(
                        text="⏸️",
                        callback_data=f"controls pause {chat_id}",
                    ),
                    self.ikb(
                        text="🔂",
                        callback_data=f"controls replay {chat_id}",
                    ),
                    self.ikb(
                        text="⏭️",
                        callback_data=f"controls skip {chat_id}",
                    ),
                    self.ikb(
                        text="⏹️",
                        callback_data=f"controls stop {chat_id}",
                    ),
                ]
            )

            keyboard.append(
                [
                    self.ikb(
                        text="🗑️ DELETE",
                        callback_data=f"controls close {chat_id}",
                    )
                ]
            )

        return self.ikm(keyboard)

    # ==========================================================================
    # HELP MENU
    # ==========================================================================

    def help_markup(
        self,
        _lang: dict,
        back: bool = False,
    ) -> types.InlineKeyboardMarkup:
        """Build the Help menu with Telegram native button colors."""

        if back:
            rows = [
                [
                    self._button(
                        text="↩️ BACK",
                        callback_data="help_main",
                        style="primary",
                    )
                ]
            ]

        else:
            rows = [
                [
                    self._button(
                        text="👑 ADMINS",
                        callback_data="help_admins",
                        style="primary",
                    ),
                    self._button(
                        text="🔑 AUTH",
                        callback_data="help_auth",
                        style="success",
                    ),
                    self._button(
                        text="📢 BROADCAST",
                        callback_data="help_broadcast",
                        style="danger",
                    ),
                ],
                [
                    self._button(
                        text="🔁 LOOP",
                        callback_data="help_loop",
                        style="success",
                    ),
                    self._button(
                        text="▶️ PLAY",
                        callback_data="help_play",
                        style="primary",
                    ),
                    self._button(
                        text="📋 QUEUE",
                        callback_data="help_queue",
                        style="primary",
                    ),
                ],
                [
                    self._button(
                        text="💬 BL-CHAT",
                        callback_data="help_blchat",
                        style="danger",
                    ),
                    self._button(
                        text="👤 BL-USER",
                        callback_data="help_bluser",
                        style="danger",
                    ),
                    self._button(
                        text="🔎 SEEK",
                        callback_data="help_seek",
                        style="primary",
                    ),
                ],
                [
                    self._button(
                        text="📶 PING",
                        callback_data="help_ping",
                        style="success",
                    ),
                    self._button(
                        text="📊 STATS",
                        callback_data="help_stats",
                        style="primary",
                    ),
                    self._button(
                        text="⭐ SUDO",
                        callback_data="help_sudo",
                        style="danger",
                    ),
                ],
                [
                    self._button(
                        text="↩️ BACK",
                        callback_data="start",
                        style="primary",
                    )
                ],
            ]

        return self.ikm(rows)

    # ==========================================================================
    # PING MENU
    # ==========================================================================

    def ping_markup(
        self,
        text: str,
    ) -> types.InlineKeyboardMarkup:
        """Ping / stats bottom buttons with native Telegram colors."""

        return self.ikm(
            [
                [
                    self._button(
                        text="📢 Channel",
                        url=config.SUPPORT_CHANNEL,
                        style="primary",
                    ),
                    self._button(
                        text="🆘 Support",
                        url=config.SUPPORT_CHAT,
                        style="primary",
                    ),
                ],
                [
                    self._button(
                        text="➕ Add Me to Your Group",
                        url=f"https://t.me/{app.username}?startgroup=true",
                        style="success",
                    ),
                ],
            ]
        )

    # ==========================================================================
    # PLAY QUEUED
    # ==========================================================================

    def play_queued(
        self,
        chat_id: int,
        item_id: str,
        _text: str,
    ) -> types.InlineKeyboardMarkup:

        return self.ikm(
            [
                [
                    self.ikb(
                        text="▶️",
                        callback_data=f"controls resume {chat_id}",
                    ),
                    self.ikb(
                        text="⏸️",
                        callback_data=f"controls pause {chat_id}",
                    ),
                    self.ikb(
                        text="⏭️",
                        callback_data=f"controls skip {chat_id}",
                    ),
                    self.ikb(
                        text="⏹️",
                        callback_data=f"controls stop {chat_id}",
                    ),
                ],
                [
                    self.ikb(
                        text="🗑️ DELETE",
                        callback_data=f"controls close {chat_id}",
                    )
                ],
            ]
        )

    # ==========================================================================
    # QUEUE MENU
    # ==========================================================================

    def queue_markup(
        self,
        chat_id: int,
        _text: str,
        playing: bool,
    ) -> types.InlineKeyboardMarkup:

        _action = "pause" if playing else "resume"
        _icon = "⏸️" if playing else "▶️"

        return self.ikm(
            [
                [
                    self.ikb(
                        text=f"{_icon} {_text}",
                        callback_data=f"controls {_action} {chat_id} q",
                    )
                ]
            ]
        )

    # ==========================================================================
    # SETTINGS
    # ==========================================================================

    def settings_markup(
        self,
        lang: dict,
        admin_only: bool,
        language: str,
        chat_id: int,
    ) -> types.InlineKeyboardMarkup:

        return self.ikm(
            [
                [
                    self.ikb(
                        text=f"⚙️ {lang['play_mode']} ➜",
                        callback_data=f"controls status {chat_id}",
                    ),
                    self.ikb(
                        text=f"🎛️ {admin_only}",
                        callback_data="playmode",
                    ),
                ],
            ]
        )

    # ==========================================================================
    # START MENU
    # ==========================================================================

    def start_key(
        self,
        lang: dict,
        private: bool = False,
    ) -> types.InlineKeyboardMarkup:

        rows = [
            # ------------------------------------------------------------------
            # ADD BOT
            # Green / SUCCESS
            # ------------------------------------------------------------------
            [
                self._button(
                    text=f"➕ {lang['add_me']}",
                    url=f"https://t.me/{app.username}?startgroup=true",
                    style="success",
                )
            ],

            # ------------------------------------------------------------------
            # HELP
            # Blue / PRIMARY
            # ------------------------------------------------------------------
            [
                self._button(
                    text=f"⚙️ {lang.get('help', 'Help')}",
                    callback_data="help",
                    style="primary",
                )
            ],

            # ------------------------------------------------------------------
            # SUPPORT + CHANNEL
            # Blue / PRIMARY
            # ------------------------------------------------------------------
            [
                self._button(
                    text=f"💬 {lang['support']}",
                    url=config.SUPPORT_CHAT,
                    style="primary",
                ),
                self._button(
                    text=f"📢 {lang['channel']}",
                    url=config.SUPPORT_CHANNEL,
                    style="primary",
                ),
            ],
        ]

        # ----------------------------------------------------------------------
        # SOURCE
        # Private chat only
        # Blue / PRIMARY
        # ----------------------------------------------------------------------

        if private:
            rows.append(
                [
                    self._button(
                        text=f"💻 {lang['source']}",
                        url="https://hasiimusic.hasindunagolla.live/",
                        style="primary",
                    )
                ]
            )

        return self.ikm(rows)

    # ==========================================================================
    # YOUTUBE
    # ==========================================================================

    def yt_key(
        self,
        link: str,
    ) -> types.InlineKeyboardMarkup:

        return self.ikm(
            [
                [
                    self.ikb(
                        text="📋 COPY LINK",
                        copy_text=link,
                    ),
                    self.ikb(
                        text="▶️ OPEN YOUTUBE",
                        url=link,
                    ),
                ],
            ]
        )