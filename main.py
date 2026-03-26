import csv
from logging import DEBUG, Formatter, Logger, StreamHandler
import os
from random import randint
import re
import sys
from collections import defaultdict, deque
from queue import Empty, Full, Queue
from datetime import datetime
import gc
import json
import html
import threading
from time import sleep
import hashlib
from typing import TypedDict

import sounddevice as sd
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMessageBox,
    QHBoxLayout,
    QSlider,
    QComboBox,
    QDialog,
    QMenuBar,
    QGridLayout,
    QLineEdit,
    QCheckBox,
    QListView,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt,
    QTimer,
    QFile,
    QIODevice,
)
from PyQt6.QtGui import QFont, QAction, QPalette, QIcon, QShortcut, QKeySequence
import numpy as np
from googletrans import Translator
from torch import no_grad, set_num_threads

from app.chat_message import ChatMessage, ChatMessageDelegate, ChatMessageListModel
from app.chat_overlay import ChatOverlayWindow
from app.menu_combo_check_box import MenuComboCheckBox
from app.schema import MessageStatsTD, TwitchCredentialsTD
from app.translations import (
    DEFAULT_LANGUAGE,
    TRANSLATIONS,
    _,
    translate_text,
    transliteration,
)
from app.twitch.auth_worker import AuthWorker
from app.twitch.chat_listener import TwitchChatListener
from app.constants import (
    APP_VERSION,
    APP_NAME,
    DEFAULTS,
    PADDING,
    SAMPLE_RATE,
    SPEECH_RATE_INDEX,
    VOICES,
    MODELS,
)
from app.utils import (
    clean_emoji,
    clean_links,
    clean_message,
    clean_stop_words,
    clean_symbol_spam,
    clear_cache_detoxify,
    clear_cache_silero,
    configure_torch_hub_cache,
    contain_words_or_nums,
    convert_numbers_to_words,
    detoxify_get_model_and_tokenizer_local_only,
    find_cached_detoxify_checkpoint,
    find_cached_silero_repo,
    get_banned_list_path,
    get_detoxify,
    get_detoxify_impl,
    get_settings_path,
    get_torch_hub,
    icon_path,
    load_stop_words,
    resource_path,
)
from app.youtube.chat_parser import YouTubeChatParser

size_policy_fixed = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
window_flag_fixed = (
    Qt.WindowType.Window
    | Qt.WindowType.CustomizeWindowHint
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowCloseButtonHint
)
twitch_default_credentials = TwitchCredentialsTD(
    client_id=None,
    access=None,
    refresh=None,
    nickname=None,
)


logger = Logger("main")
# logger.setLevel(DEBUG)

# handler = StreamHandler()
# handler.setLevel(DEBUG)

# formatter = Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# handler.setFormatter(formatter)

# logger.addHandler(handler)

set_num_threads(max(1, os.cpu_count() or 1))


class PlatformMessage(TypedDict):
    msg_id: str
    platform: str
    author: str
    message: str
    connection_token: int
    is_sponsor: bool
    is_staff: bool
    is_owner: bool
    is_donate: bool


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon = QIcon(resource_path(icon_path()))
        self.setWindowIcon(icon)
        self.setMinimumSize(1200, 600)

        self.root_widget = QWidget()
        self.setCentralWidget(self.root_widget)
        self.root_layout = QVBoxLayout(self.root_widget)

        self.language = DEFAULT_LANGUAGE
        self.voice_language = DEFAULT_LANGUAGE
        self.voice = DEFAULTS["voice"]

        self.auto_scroll = DEFAULTS["auto_scroll"]
        self.add_accents = DEFAULTS["add_accents"]
        self.read_author_names = DEFAULTS["read_author_names"]
        self.read_platform_names = DEFAULTS["read_platform_names"]
        self.read_filter = DEFAULTS["read_filter"]
        self.auto_translate = DEFAULTS["auto_translate"]

        self.font_size = DEFAULTS["font_size"]
        self.volume = DEFAULTS["volume"]
        self.speech_rate = DEFAULTS["speech_rate"]
        self.speech_delay = DEFAULTS["speech_delay"]
        self.min_text_length = DEFAULTS["min_text_length"]
        self.max_text_length = DEFAULTS["max_text_length"]
        self.toxic_sense = DEFAULTS["toxic_sense"]
        self.ban_limit = DEFAULTS["ban_limit"]

        self.stop_words = tuple()
        self.chat_only_mode = False
        self.is_paused = False

        # Chat overlay
        self.chat_overlay_show = False
        self.chat_overlay = None
        self.chat_overlay_geometry = None
        self.chat_overlay_always_on_top = False

        # Connections
        self.youtube = None
        self.yt_credentials = None
        self.yt_is_connected = False

        self.twitch = None
        self.twitch_credentials: TwitchCredentialsTD = twitch_default_credentials
        self.twitch_is_connected = False

        self.messages_stats: MessageStatsTD = defaultdict(int)

        # Message queue
        self.buffer_maxsize = DEFAULTS["buffer_maxsize"]
        self._pending_messages = deque()
        self._pending_ui_updates = deque()
        self._pending_status_messages = deque()
        self._pending_ui_calls = deque()
        self._pending_stats_update = False
        self.toxic_dict = defaultdict(float)
        self.banned_set = set()
        self.processed_messages = set()
        self.message_state_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.message_workers = min(4, max(2, os.cpu_count() or 2))
        self._cache_clear_in_progress = False
        self._connection_token_seq = 0
        self._active_connection_tokens = {"youtube": None, "twitch": None}

        self.load_settings()

        self.audio_queue = Queue(maxsize=self.buffer_maxsize)
        self.process_message_queue = Queue()

        self.setup_ui()

        self.detox_model = None
        self.model = None
        self.model_lock = threading.Lock()
        self.translator = Translator()
        self.translator_lock = threading.Lock()

        QTimer.singleShot(0, self.start_background_services)

    def start_background_services(self):
        threading.Thread(target=self.process_audio_loop, daemon=True).start()
        for worker_idx in range(self.message_workers):
            threading.Thread(
                target=self.process_messages_loop,
                daemon=False,
                name=f"process_messages_loop_{worker_idx}",
            ).start()
        threading.Thread(
            target=lambda: self.init_silero(self.voice_language), daemon=True
        ).start()

        if self.toxic_sense >= 1.0:
            self.add_sys_message(
                author="Detoxify",
                text=f"{_(self.language, 'Toxicity threshold')} >= 1.0, {_(self.language, 'skip load')}",
                status="warning",
            )
            return
        else:
            threading.Thread(target=self.init_detoxify, daemon=True).start()

    # === UI setup ===

    def setup_menu_bar(self):
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        self.setup_file_menu(menu_bar)
        self.setup_language_menu(menu_bar)
        self.voice_menu = menu_bar.addMenu(_(self.language, "Speech configuration"))
        self.setup_voice_menu()
        self.setup_menu_message_settings(menu_bar)

        chat_overlay_menu = menu_bar.addMenu(_(self.language, "Chat overlay"))

        show_chat_overlay_action = QAction(
            _(self.language, "Show chat"), chat_overlay_menu
        )
        show_chat_overlay_action.setCheckable(True)
        show_chat_overlay_action.setChecked(self.chat_overlay_show)
        show_chat_overlay_action.triggered.connect(self.toggle_show_chat_overlay)
        show_chat_overlay_action.setShortcut(QKeySequence("F12"))
        chat_overlay_menu.addAction(show_chat_overlay_action)
        self.toggle_show_chat_overlay(self.chat_overlay_show)

        reset_chat_overlay_action = QAction(
            _(self.language, "Reset position"), chat_overlay_menu
        )
        reset_chat_overlay_action.triggered.connect(self.on_chat_overlay_reset)
        chat_overlay_menu.addAction(reset_chat_overlay_action)

    def setup_file_menu(self, menu_bar):
        file_menu = menu_bar.addMenu(_(self.language, "File"))

        export_log_action = QMenu(_(self.language, "Export log"), file_menu)
        file_menu.addMenu(export_log_action)
        msg_log_html_action = QAction("Html", export_log_action)
        msg_log_html_action.triggered.connect(lambda: self.export_log("html"))
        export_log_action.addAction(msg_log_html_action)
        msg_log_md_action = QAction("Markdown", export_log_action)
        msg_log_md_action.triggered.connect(lambda: self.export_log("md"))
        export_log_action.addAction(msg_log_md_action)
        msg_log_text_action = QAction("Text", export_log_action)
        msg_log_text_action.triggered.connect(lambda: self.export_log("text"))
        export_log_action.addAction(msg_log_text_action)
        msg_log_csv_action = QAction("CSV", export_log_action)
        msg_log_csv_action.triggered.connect(self.export_chat_csv)
        export_log_action.addAction(msg_log_csv_action)
        msg_log_merge_action = QAction("Merge CSV", export_log_action)
        msg_log_merge_action.triggered.connect(self.merge_chat_csv)
        export_log_action.addAction(msg_log_merge_action)
        msg_log_merge_recalc_action = QAction(
            "Merge CSV with recalculation", export_log_action
        )
        msg_log_merge_recalc_action.triggered.connect(
            lambda: self.merge_chat_csv(with_recalculate_toxicity=True)
        )
        export_log_action.addAction(msg_log_merge_recalc_action)

        load_models_action = QAction(_(self.language, "Load models"), file_menu)
        load_models_action.triggered.connect(self.on_load_models_action)
        file_menu.addAction(load_models_action)

        reset_settings_action = QAction(_(self.language, "Reset settings"), file_menu)
        reset_settings_action.triggered.connect(self.on_reset_settings_action)
        file_menu.addAction(reset_settings_action)

    def setup_language_menu(self, menu_bar):
        language_menu = menu_bar.addMenu(_(self.language, "Language"))
        language_menu.clear()
        for lang in TRANSLATIONS.keys():
            lang_action = QAction(lang, self)
            lang_action.setCheckable(True)
            lang_action.setChecked(lang == self.language)
            lang_action.triggered.connect(
                lambda checked, l=lang: self.language_changed(l)
            )
            language_menu.addAction(lang_action)

    def setup_voice_menu(self):
        self.voice_menu.clear()
        for voice_lang in VOICES.keys():
            voice_lang_menu = self.voice_menu.addMenu(voice_lang)

            voices = ["random"] + list(VOICES[voice_lang])
            for voice in voices:
                voice_action = QAction(voice, self)
                voice_action.setCheckable(True)
                voice_action.setChecked(
                    voice == self.voice and voice_lang == self.voice_language
                )
                voice_action.triggered.connect(
                    lambda checked, l=voice_lang, v=voice: self.voice_changed(l, v)
                )
                voice_lang_menu.addAction(voice_action)

        add_accents_action = QAction(_(self.language, "Add accents"), self)
        add_accents_action.setCheckable(True)
        add_accents_action.setChecked(self.add_accents)
        add_accents_action.triggered.connect(self.toggle_add_accents)
        self.voice_menu.addAction(add_accents_action)

    def setup_menu_message_settings(self, menu_bar: QMenu):
        msg_settings_menu = menu_bar.addMenu(_(self.language, "Message Settings"))

        banned_action = QAction(_(self.language, "List of banned"), msg_settings_menu)
        banned_action.triggered.connect(self.on_list_of_banned_action)
        msg_settings_menu.addAction(banned_action)

        stop_words_action = QAction(_(self.language, "Stop words"), msg_settings_menu)
        stop_words_action.triggered.connect(self.on_stop_words_action)
        msg_settings_menu.addAction(stop_words_action)

        delays_action = QAction(
            _(self.language, "Delays and processing"), msg_settings_menu
        )
        delays_action.triggered.connect(self.on_delays_settings_action)
        msg_settings_menu.addAction(delays_action)

        msg_settings_menu.addSeparator()

        read_authors_action = QAction(
            _(self.language, "Read author names"), msg_settings_menu
        )
        read_authors_action.setCheckable(True)
        read_authors_action.setChecked(self.read_author_names)
        read_authors_action.triggered.connect(self.toggle_read_author_names)
        msg_settings_menu.addAction(read_authors_action)

        read_platform_action = QAction(
            _(self.language, "Read platform name"), msg_settings_menu
        )
        read_platform_action.setCheckable(True)
        read_platform_action.setChecked(self.read_platform_names)
        read_platform_action.triggered.connect(self.toggle_read_platform_names)
        msg_settings_menu.addAction(read_platform_action)

        auto_translate_action = QAction(
            _(self.language, "Translate messages"), msg_settings_menu
        )
        auto_translate_action.setCheckable(True)
        auto_translate_action.setChecked(self.auto_translate)
        auto_translate_action.triggered.connect(self.toggle_auto_translate)
        msg_settings_menu.addAction(auto_translate_action)

    def setup_connections_grid(self):
        connections_grid = QGridLayout()
        self.connections_widget = QWidget()
        self.connections_widget.setLayout(connections_grid)
        self.root_layout.addWidget(self.connections_widget)

        yt_layout = QHBoxLayout()
        yt_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        yt_label = QLabel("YouTube")
        yt_layout.addWidget(yt_label)
        self.yt_video_input = QLineEdit()
        self.yt_video_input.returnPressed.connect(self.on_click_yt_connect)
        self.yt_video_input.setPlaceholderText(
            "https://www.youtube.com/watch?v=VIDEO_ID or VIDEO_ID"
        )
        yt_layout.addWidget(self.yt_video_input)
        self.connect_yt_button = QPushButton(_(self.language, "Connect"))
        self.connect_yt_button.clicked.connect(self.on_click_yt_connect)
        self.connect_yt_button.setFixedWidth(150)
        self.connect_yt_button.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_layout.addWidget(self.connect_yt_button)
        # self.configure_yt_button = QPushButton(_(self.language, "Configure"))
        # self.configure_yt_button.clicked.connect(self.on_configure_yt)
        # yt_layout.addWidget(self.configure_yt_button)
        connections_grid.addLayout(yt_layout, 0, 0)

        twitch_layout = QHBoxLayout()
        twitch_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        twitch_label = QLabel("Twitch")
        twitch_layout.addWidget(twitch_label)
        self.twitch_input = QLineEdit()
        self.twitch_input.returnPressed.connect(self.on_click_connect_twitch)
        self.twitch_input.setPlaceholderText(
            "https://www.twitch.tv/CHANNEL_NAME or CHANNEL_NAME"
        )
        twitch_layout.addWidget(self.twitch_input)
        self.connect_twitch_button = QPushButton(_(self.language, "Connect"))
        self.connect_twitch_button.clicked.connect(self.on_click_connect_twitch)
        self.connect_twitch_button.setFixedWidth(150)
        self.connect_twitch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        twitch_layout.addWidget(self.connect_twitch_button)
        self.configure_twitch_button = QPushButton(_(self.language, "Configure"))
        self.configure_twitch_button.clicked.connect(self.on_configure_twitch)
        self.configure_twitch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        twitch_layout.addWidget(self.configure_twitch_button)
        connections_grid.addLayout(twitch_layout, 0, 1)

    def setup_pause_button_color(self):
        palette = self.pause_button.palette()
        if self.is_paused:
            palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.darkRed)
            self.pause_button.setPalette(palette)
        else:
            self.pause_button.setPalette(self.style().standardPalette())

    def setup_read_filter(self):
        items = [_(self.language, i) for i in DEFAULTS["read_filter"]]
        self.read_filter_combo = MenuComboCheckBox(
            _(self.language, "Read messages"), items
        )
        self.read_filter_combo.setSelected(self.read_filter)
        self.read_filter_combo.changed.connect(self.on_change_read_filter)
        self.read_filter_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.control_layout.addWidget(self.read_filter_combo)

    def setup_central_widget(self):
        chat_header_layout = QHBoxLayout()
        chat_header_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)

        self.control_layout = QHBoxLayout()
        chat_header_layout.addLayout(self.control_layout)
        self.control_layout.setContentsMargins(0, 0, PADDING, 0)
        self.audio_indicator = QLabel("🟢")
        self.control_layout.addWidget(self.audio_indicator)

        self.setup_read_filter()

        self.pause_button = QPushButton()
        self.setup_pause_button_color()
        self.pause_button.clicked.connect(self.on_pause_clicked)
        self.pause_button.setFixedWidth(250)
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_pause_button_text()
        self.control_layout.addWidget(self.pause_button)

        self.clr_queue_button = QPushButton(_(self.language, "Clear queue"))
        self.clr_queue_button.clicked.connect(self.on_clear_queue)
        self.clr_queue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.control_layout.addWidget(self.clr_queue_button)

        self.auto_scroll_checkbox = QCheckBox(_(self.language, "Auto-scroll"))
        self.auto_scroll_checkbox.setChecked(self.auto_scroll)
        self.auto_scroll_checkbox.clicked.connect(self.toggle_auto_scroll)
        self.auto_scroll_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        chat_header_layout.addWidget(
            self.auto_scroll_checkbox, 1, Qt.AlignmentFlag.AlignRight
        )

        self.clear_log_button = QPushButton(_(self.language, "Clear log"))
        self.clear_log_button.clicked.connect(self.clear_log)
        self.clear_log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        chat_header_layout.addWidget(self.clear_log_button)

        self.font_size_combo = QComboBox()
        self.font_size_combo.addItems([str(s) for s in range(8, 24, 2)])
        self.font_size_combo.setCurrentText(str(self.font_size))
        self.font_size_combo.currentIndexChanged.connect(self.font_size_changed)
        self.font_size_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        chat_header_layout.addWidget(self.font_size_combo)

        self.chat_header_widget = QWidget()
        self.chat_header_widget.setLayout(chat_header_layout)
        self.root_layout.addWidget(self.chat_header_widget)

        self.chat_text = QListView()
        self.chat_text.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.chat_text.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.chat_text.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chat_text.setWordWrap(True)
        self.chat_text.setUniformItemSizes(False)
        self.chat_text.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.chat_text.setSpacing(4)
        self.chat_model = ChatMessageListModel(self.chat_text)
        self.chat_text.setModel(self.chat_model)
        # self.chat_text.setItemDelegate(ChatMessageDelegate(self.chat_text, only_system_msg=True, with_avatar=False))
        self.root_layout.addWidget(self.chat_text)

        # Timer to flush messages added from background threads
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush_pending_messages)
        self._flush_timer.start(100)

    def setup_status_bar(self):
        self.statusBar().setStyleSheet("QStatusBar::item { border: none; }")

        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setContentsMargins(PADDING, 0, PADDING, 10)
        self.statusBar().addWidget(self.version_label)

        # == Stats labels ==
        self.stats_label = QLabel(self.stats_text())
        self.stats_label.setContentsMargins(PADDING, 0, PADDING, 10)
        self.statusBar().addWidget(self.stats_label, 1)

        self.voice_label = QLabel(self.status_voice_text())
        self.voice_label.setContentsMargins(PADDING, 0, 0, 5)
        self.statusBar().addWidget(self.voice_label)

        # == Volume slider ==
        self.vol_label = QLabel(_(self.language, "Volume"))
        self.vol_label.setContentsMargins(PADDING, 0, 0, 5)
        self.statusBar().addWidget(self.vol_label)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setMinimum(0)
        self.vol_slider.setMaximum(200)
        self.vol_slider.setValue(self.volume)
        self.vol_slider.valueChanged.connect(self.on_change_volume)
        self.statusBar().addWidget(self.vol_slider)

        self.vol_label_value = QLabel(f"{self.volume}%")
        self.vol_label_value.setContentsMargins(0, 0, PADDING, 5)
        self.statusBar().addWidget(self.vol_label_value)

        # == Speech rate ==
        self.speech_rate_label = QLabel(_(self.language, "Speech rate"))
        self.speech_rate_label.setContentsMargins(0, 0, 0, 5)
        self.statusBar().addWidget(self.speech_rate_label)

        self.speech_rate_combo = QComboBox()
        self.speech_rate_combo.addItems(SPEECH_RATE_INDEX.values())
        self.speech_rate_combo.setCurrentIndex(2)
        self.speech_rate_combo.currentIndexChanged.connect(self.speech_rate_changed)
        self.speech_rate_combo.setContentsMargins(0, 0, 0, 5)
        self.statusBar().addWidget(self.speech_rate_combo)

    def setup_ui(self):
        self.setup_menu_bar()
        self.setup_status_bar()
        self.setup_connections_grid()
        self.setup_central_widget()
        self.exit_chat_only_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.exit_chat_only_shortcut.activated.connect(self.exit_chat_only_mode)
        self.toggle_chat_only_shortcut = QShortcut(QKeySequence("F11"), self)
        self.toggle_chat_only_shortcut.activated.connect(
            lambda: self.toggle_chat_only_mode(not self.chat_only_mode)
        )
        self.pause_play_shortcut = QShortcut(QKeySequence("Space"), self)
        self.pause_play_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.pause_play_shortcut.activated.connect(self.on_pause_clicked)
        self.volume_up_shortcut = QShortcut(QKeySequence("Up"), self)
        self.volume_up_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.volume_up_shortcut.activated.connect(lambda: self.change_volume_by_step(5))
        self.volume_down_shortcut = QShortcut(QKeySequence("Down"), self)
        self.volume_down_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.volume_down_shortcut.activated.connect(
            lambda: self.change_volume_by_step(-5)
        )

        self.speech_rate_up_shortcut = QShortcut(QKeySequence("Right"), self)
        self.speech_rate_up_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.speech_rate_up_shortcut.activated.connect(
            lambda: self.change_speech_rate_by_step(1)
        )
        self.speech_rate_down_shortcut = QShortcut(QKeySequence("Left"), self)
        self.speech_rate_down_shortcut.setContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.speech_rate_down_shortcut.activated.connect(
            lambda: self.change_speech_rate_by_step(-1)
        )
        self.speech_rate_combo.setCurrentText(str(self.speech_rate))
        self.font_size_changed(2)
        self.apply_chat_only_mode()

    # === UI event handlers ===

    def change_volume_by_step(self, delta):
        self.vol_slider.setValue(
            max(
                self.vol_slider.minimum(),
                min(self.vol_slider.maximum(), self.vol_slider.value() + delta),
            )
        )

    def change_speech_rate_by_step(self, delta):
        _max = self.speech_rate_combo.count() - 1
        _min = 0
        current_index = self.speech_rate_combo.currentIndex()
        self.speech_rate_combo.setCurrentIndex(
            max(_min, min(_max, current_index + delta))
        )

    def on_pause_clicked(self):
        self.is_paused = not self.is_paused
        self.update_pause_button_text()
        self.setup_pause_button_color()
        if self.is_paused:
            self.statusBar().showMessage(
                _(self.language, "Playback has been stopped"), 3000
            )
        else:
            self.statusBar().showMessage(
                _(self.language, "Speech playback continued..."), 3000
            )

    def language_changed(self, lang):
        self.language = lang
        self.save_settings()
        self.setup_menu_bar()
        self.apply_chat_only_mode()
        self.on_change_stats()

        if hasattr(self, "chat_overlay") and self.chat_overlay:
            self.chat_overlay.language_changed(lang)

        read_filter_selected = self.read_filter_combo.getSelectedIndex()
        read_filter_items = [_(self.language, i) for i in DEFAULTS["read_filter"]]
        self.read_filter_combo.setItems(read_filter_items)
        self.read_filter_combo.setSelectedIndices(read_filter_selected)
        self.read_filter_combo.setTitle(_(self.language, "Read messages"))
        self.read_filter = self.read_filter_combo.getSelected()

        self.speech_rate_label.setText(_(self.language, "Speech rate"))
        self.vol_label.setText(_(self.language, "Volume"))
        self.voice_label.setText(self.status_voice_text())

        self.connect_yt_button.setText(
            _(self.language, "Connected" if self.yt_is_connected else "Connect")
        )
        # self.configure_yt_button.setText(_(self.language, "Configure"))
        self.connect_twitch_button.setText(
            _(self.language, "Connected" if self.twitch_is_connected else "Connect")
        )
        self.configure_twitch_button.setText(_(self.language, "Configure"))

        self.auto_scroll_checkbox.setText(_(self.language, "Auto-scroll"))
        self.clear_log_button.setText(_(self.language, "Clear log"))
        self.update_pause_button_text()
        self.clr_queue_button.setText((_(self.language, "Clear queue")))

    def voice_changed(self, lang, voice):
        if self.model_lock.locked():
            return
        self.voice = voice
        if self.voice_language != lang:
            self.voice_language = lang
            with self.model_lock:
                self.model = None
            threading.Thread(
                target=lambda: self.init_silero(self.voice_language), daemon=True
            ).start()
            self.stop_words = load_stop_words(self.voice_language)

        self.save_settings()
        self.setup_voice_menu()
        self.voice_label.setText(self.status_voice_text())

    def speech_rate_changed(self, index):
        self.speech_rate = SPEECH_RATE_INDEX[index]

    def on_change_volume(self, value):
        self.volume = value
        self.vol_label_value.setText(f"{self.volume}%")

    def on_change_read_filter(self):
        self.read_filter = self.read_filter_combo.getSelected()

    def font_size_changed(self, index):
        self.font_size = int(self.font_size_combo.currentText())
        font = self.chat_text.font()
        font.setPointSize(self.font_size)
        self.chat_text.setFont(font)
        self.chat_text.doItemsLayout()
        if self.chat_overlay is not None:
            self.chat_overlay.chat_view.setFont(font)
            self.chat_overlay.chat_view.doItemsLayout()

    def on_configure_twitch(self):
        if getattr(self, "dlg", False) and self.dlg:
            self.dlg.close()

        self.dlg = QDialog(self)
        self.dlg.setFixedSize(600, 100)
        self.dlg.setSizePolicy(size_policy_fixed)
        self.dlg.setWindowFlags(window_flag_fixed)
        self.dlg.setWindowTitle(_(self.language, "Twitch account"))
        root_widget = QWidget(self.dlg)
        root_widget.setMinimumSize(600, 100)
        root_layout = QVBoxLayout(root_widget)

        entry_layout = QHBoxLayout()
        entry_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        root_layout.addLayout(entry_layout)
        self.twitch_client_id_input = QLineEdit()
        entry_layout.addWidget(self.twitch_client_id_input, 1)

        self.twitch_client_id_input.setPlaceholderText(
            _(self.language, "Enter your client id")
        )
        if self.twitch_credentials["client_id"] is None:
            self.twitch_client_id_input.returnPressed.connect(
                self.on_click_twitch_save_settings
            )
            self.twitch_client_id_button = QPushButton(_(self.language, "Save"))
            self.twitch_client_id_button.clicked.connect(
                self.on_click_twitch_save_settings
            )
            entry_layout.addWidget(self.twitch_client_id_button)
        else:
            self.twitch_client_id_input.setText(
                "*" * len(self.twitch_credentials["client_id"])
            )
            self.twitch_client_id_input.setReadOnly(True)
            self.twitch_client_id_button = QPushButton(_(self.language, "Edit"))
            self.twitch_client_id_button.clicked.connect(
                self.on_click_twitch_edit_credential
            )
            entry_layout.addWidget(self.twitch_client_id_button)

        client_id_help_text = QLabel(
            f'{_(self.language, "client_id_help_text")}: <a href="https://dev.twitch.tv/console/apps/">https://dev.twitch.tv/console/apps/</a>'
        )
        client_id_help_text.setOpenExternalLinks(True)
        root_layout.addWidget(client_id_help_text)

        self.dlg.exec()

    def _start_twitch_device_auth(self, client_id):
        def on_finish():
            if getattr(self, "dlg", False) and self.dlg:
                self.dlg.close()

        def on_error(err):
            self.add_sys_message(author="Twitch", text=err, status="error")

        def on_user_code(verification_uri, user_code, expires_in):
            on_finish()

            self.dlg = QDialog(self)
            self.dlg.setWindowTitle(_(self.language, "Twitch account"))
            self.dlg.setFixedSize(600, 250)
            self.dlg.setSizePolicy(size_policy_fixed)
            self.dlg.setWindowFlags(window_flag_fixed)
            root_widget = QWidget(self.dlg)
            root_widget.setMinimumSize(600, 250)
            root_layout = QVBoxLayout(root_widget)
            root_widget.setContentsMargins(PADDING, PADDING, PADDING, PADDING)

            continue_authorize_browser_text = QLabel(
                _(self.language, "continue_authorize_browser")
            )
            root_layout.addWidget(continue_authorize_browser_text)

            user_code_text = QLabel(str(user_code))
            user_code_text.setFont(QFont("Arial", 25, 900))
            user_code_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root_layout.addWidget(user_code_text, 1)

            expires_in_text = QLabel(f"{_(self.language, 'expires_in')}: {expires_in}")
            root_layout.addWidget(expires_in_text)
            root_layout.setContentsMargins(0, 0, 0, PADDING)

            verification_uri_text = QLabel(
                f'{_(self.language, "verification_uri")}: <a href="{verification_uri}">{verification_uri}</a>'
            )
            verification_uri_text.setWordWrap(True)
            verification_uri_text.setOpenExternalLinks(True)
            root_layout.addWidget(verification_uri_text)
            self.dlg.exec()

        def on_token_received(token, refresh_token, nickname):
            self.twitch_credentials["access"] = token
            self.twitch_credentials["refresh"] = refresh_token
            self.twitch_credentials["client_id"] = client_id
            self.twitch_credentials["nickname"] = nickname
            self.save_settings()
            self.add_sys_message(
                author="Twitch",
                text=_(self.language, "Success authorized"),
                status="success",
            )
            on_finish()

        self.worker = AuthWorker(client_id=client_id, lang=self.language)
        self.worker.user_code_signal.connect(on_user_code)
        self.worker.token_signal.connect(on_token_received)
        self.worker.error_signal.connect(on_error)
        self.worker.finished.connect(on_finish)
        self.worker.start()

    def on_click_twitch_save_settings(self):
        # if self.twitch_credentials["client_id"] is not None:
        #     res = QMessageBox.question(
        #         self,
        #         _(self.language, "Twitch account"),
        #         _(self.language, "twitch_save_settings_warning")
        #     )
        #     if res != QMessageBox.StandardButton.Yes:
        #         return

        client_id = self.twitch_client_id_input.text()

        self.twitch_client_id_input.setText("*" * len(client_id))
        self.twitch_client_id_input.setReadOnly(True)
        self.twitch_client_id_input.returnPressed.disconnect()
        self.twitch_client_id_button.clicked.disconnect()
        self.twitch_client_id_button.clicked.connect(
            self.on_click_twitch_edit_credential
        )
        self.twitch_client_id_button.setText(_(self.language, "Edit"))
        self._start_twitch_device_auth(client_id)

    def on_click_twitch_edit_credential(self):
        self.twitch_client_id_input.returnPressed.connect(
            self.on_click_twitch_save_settings
        )
        self.twitch_client_id_input.setText(self.twitch_credentials["client_id"])
        self.twitch_client_id_input.setReadOnly(False)
        self.twitch_client_id_button.clicked.disconnect()
        self.twitch_client_id_button.clicked.connect(self.on_click_twitch_save_settings)
        self.twitch_client_id_button.setText(_(self.language, "Save"))

    def on_click_connect_twitch(self):
        if self.twitch:
            self.twitch.disconnect()
            self.on_disconnect_twitch(self.twitch)
            self.twitch = None
        else:
            video_id = self.twitch_input.text()
            if not video_id:
                QMessageBox.warning(
                    self,
                    "URL / ID",
                    _(self.language, "Please enter video ID or URL"),
                )
                return

            client_id = str(self.twitch_credentials.get("client_id") or "").strip()
            if not client_id or client_id.lower() == "none":
                self.on_configure_twitch()
                return

            connection_token = self._open_connection_token("twitch")

            def on_expiries_access():
                access, refresh = AuthWorker.ensure_valid_access_token(
                    self.twitch_credentials["client_id"],
                    self.twitch_credentials["access"],
                    self.twitch_credentials["refresh"],
                    self.language,
                )
                self.twitch_credentials["access"] = access
                self.twitch_credentials["refresh"] = refresh
                self.save_settings()
                return access

            def on_msg(
                msg_id,
                author,
                msg,
                is_sponsor=False,
                is_staff=False,
                is_owner=False,
                is_donate=False,
            ):
                if not self._is_active_connection_token("twitch", connection_token):
                    return
                self.process_message_queue.put_nowait(
                    PlatformMessage(
                        msg_id=msg_id,
                        platform="twitch",
                        author=author,
                        message=msg,
                        connection_token=connection_token,
                        is_sponsor=is_sponsor,
                        is_staff=is_staff,
                        is_owner=is_owner,
                        is_donate=is_donate,
                    )
                )

            def on_error(err):
                self.add_sys_message(author="Twitch", text=err, status="error")

            try:
                (
                    self.twitch_credentials["access"],
                    self.twitch_credentials["refresh"],
                ) = AuthWorker.ensure_valid_access_token(
                    self.twitch_credentials["client_id"],
                    self.twitch_credentials["access"],
                    self.twitch_credentials["refresh"],
                    self.language,
                )
                self.save_settings()
            except Exception as e:
                on_error(translate_text(str(e), self.language))
                self._start_twitch_device_auth(self.twitch_credentials["client_id"])
                return

            self.on_reconnect_twitch()

            listener = TwitchChatListener(
                client_id=self.twitch_credentials["client_id"],
                token=self.twitch_credentials["access"],
                nickname=self.twitch_credentials["nickname"],
                channel=video_id,
                on_connect=lambda: self.on_connect_twitch(listener),
                on_disconnect=lambda: self.on_disconnect_twitch(listener),
                on_error=on_error,
                on_message=on_msg,
                on_reconnect=lambda: self.on_reconnect_twitch(listener),
                on_expiries_access=on_expiries_access,
                lang=self.language,
            )
            self.twitch = listener
            self.twitch.run()

    def on_reconnect_twitch(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_reconnect_twitch, listener)
            return
        if listener is not None and listener is not self.twitch:
            return
        self.connect_twitch_button.setPalette(self.style().standardPalette())
        self.connect_twitch_button.setText(_(self.language, "Connecting"))

        self.twitch_input.setReadOnly(True)
        self.connect_twitch_button.setEnabled(False)

    def on_disconnect_twitch(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_disconnect_twitch, listener)
            return
        if listener is not None and listener is not self.twitch:
            return
        self._close_connection_token("twitch")
        self.twitch = None
        self.twitch_is_connected = False
        self.twitch_input.setReadOnly(False)
        self.connect_twitch_button.setEnabled(True)

        self.connect_twitch_button.setText(_(self.language, "Connect"))
        self.connect_twitch_button.setPalette(self.style().standardPalette())
        self.add_sys_message(
            author="Twitch",
            text=_(self.language, "chat_disconnected"),
            status="success",
        )

    def on_connect_twitch(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_connect_twitch, listener)
            return
        if listener is not None and listener is not self.twitch:
            return
        self.twitch_is_connected = True
        self.connect_twitch_button.setEnabled(True)

        self.connect_twitch_button.setText(_(self.language, "Connected"))
        palette = self.connect_twitch_button.palette()
        palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.darkGreen)
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        self.connect_twitch_button.setPalette(palette)
        self.add_sys_message(
            author="Twitch", text=_(self.language, "chat_connected"), status="success"
        )

    def on_configure_yt(self):
        dlg = QDialog(self)
        dlg.setFixedSize(600, 100)
        dlg.setSizePolicy(size_policy_fixed)
        dlg.setWindowFlags(window_flag_fixed)
        dlg.setWindowTitle(_(self.language, "Google API Key"))

        root_widget = QWidget(dlg)
        root_widget.setMinimumSize(600, 100)
        root_layout = QVBoxLayout(root_widget)

        entry_layout = QHBoxLayout()
        entry_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)
        root_layout.addLayout(entry_layout)
        self.google_api_key_input = QLineEdit()
        entry_layout.addWidget(self.google_api_key_input, 1)

        self.google_api_key_input.setPlaceholderText(
            _(self.language, "Enter your API key")
        )
        if self.yt_credentials is None:
            self.google_api_key_input.returnPressed.connect(
                self.on_click_yt_save_settings
            )
            self.google_api_key_button = QPushButton(_(self.language, "Save"))
            self.google_api_key_button.clicked.connect(self.on_click_yt_save_settings)
            entry_layout.addWidget(self.google_api_key_button)
        else:
            self.google_api_key_input.setText("*" * len(self.yt_credentials))
            self.google_api_key_input.setReadOnly(True)
            self.google_api_key_button = QPushButton(_(self.language, "Edit"))
            self.google_api_key_button.clicked.connect(self.on_click_yt_edit_credential)
            entry_layout.addWidget(self.google_api_key_button)

        api_keys_help_text = QLabel(
            f'{_(self.language, "api_keys_help_text")}: <a href="https://console.cloud.google.com/">https://console.cloud.google.com/</a>'
        )
        api_keys_help_text.setOpenExternalLinks(True)
        root_layout.addWidget(api_keys_help_text)

        dlg.exec()

    def on_click_yt_connect(self):
        if self.youtube:
            self.youtube.disconnect()
            self.on_disconnect_yt(self.youtube)
            self.youtube = None
        else:
            video_id = self.yt_video_input.text()
            if not video_id:
                QMessageBox.warning(
                    self,
                    "URL / ID",
                    _(self.language, "Please enter video ID or URL"),
                )
                return

            if not self.model:
                res = QMessageBox.question(
                    self,
                    _(self.language, "Silero not loaded"),
                    _(self.language, "note_tts_not_loaded"),
                )
                if res != QMessageBox.StandardButton.Yes:
                    return

            connection_token = self._open_connection_token("youtube")

            def on_msg(
                msg_id,
                author,
                msg,
                is_sponsor=False,
                is_staff=False,
                is_owner=False,
                is_donate=False,
            ):
                if not self._is_active_connection_token("youtube", connection_token):
                    return
                self.process_message_queue.put_nowait(
                    PlatformMessage(
                        msg_id=msg_id,
                        platform="youtube",
                        author=author,
                        message=msg,
                        connection_token=connection_token,
                        is_sponsor=is_sponsor,
                        is_staff=is_staff,
                        is_owner=is_owner,
                        is_donate=is_donate,
                    )
                )

            def on_error(err):
                self.add_sys_message(author="YouTube", text=err, status="error")

            self.on_reconnect_yt()

            listener = YouTubeChatParser(
                url=video_id,
                on_connect=lambda: self.on_connect_yt(listener),
                on_disconnect=lambda: self.on_disconnect_yt(listener),
                on_message=on_msg,
                on_error=on_error,
                on_reconnect=lambda: self.on_reconnect_yt(listener),
                lang=self.language,
            )
            self.youtube = listener

            self.youtube.run()

    def on_connect_yt(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_connect_yt, listener)
            return
        if listener is not None and listener is not self.youtube:
            return
        self.yt_is_connected = True
        self.connect_yt_button.setEnabled(True)

        self.add_sys_message(
            author="YouTube", text=_(self.language, "chat_connected"), status="success"
        )
        self.connect_yt_button.setText(_(self.language, "Connected"))
        palette = self.connect_yt_button.palette()
        palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.darkGreen)
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        self.connect_yt_button.setPalette(palette)

    def on_disconnect_yt(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_disconnect_yt, listener)
            return
        if listener is not None and listener is not self.youtube:
            return
        self._close_connection_token("youtube")
        self.youtube = None
        self.yt_is_connected = False
        self.yt_video_input.setReadOnly(False)
        self.connect_yt_button.setEnabled(True)

        self.add_sys_message(
            author="YouTube",
            text=_(self.language, "chat_disconnected"),
            status="success",
        )
        self.connect_yt_button.setText(_(self.language, "Connect"))
        self.connect_yt_button.setPalette(self.style().standardPalette())

    def on_reconnect_yt(self, listener=None):
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self.on_reconnect_yt, listener)
            return
        if listener is not None and listener is not self.youtube:
            return
        self.connect_yt_button.setPalette(self.style().standardPalette())
        self.connect_yt_button.setText(_(self.language, "Connecting"))

        self.yt_video_input.setReadOnly(True)
        self.connect_yt_button.setEnabled(False)

    def on_list_of_banned_action(self):
        dlg = QDialog(self)
        dlg.setMinimumSize(600, 300)
        dlg.setWindowTitle(_(self.language, "List of banned"))

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)

        self.banned_text = QPlainTextEdit()
        self.banned_text.setPlainText("\n".join(self.banned_set))

        self.banned_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        layout.addWidget(self.banned_text, 1)

        save_banned_btn = QPushButton(_(self.language, "Save"))
        save_banned_btn.clicked.connect(self.on_save_banned)

        layout.addWidget(save_banned_btn)

        dlg.exec()

    def on_save_banned(self):
        content = self.banned_text.toPlainText().strip()
        content = set([w.strip() for w in content.splitlines() if w.strip()])
        self.banned_set = content  # TODO: Need a concat with new items if updated (from self.process_toxic_message)
        self.save_banned_list()
        self.statusBar().showMessage(_(self.language, "Saved"), 3000)

    def on_stop_words_action(self):
        dlg = QDialog(self)
        dlg.setMinimumSize(600, 300)
        dlg.setWindowTitle(_(self.language, "Stop words"))

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)

        self.stop_words_text = QPlainTextEdit()
        self.stop_words_text.setPlainText("\n".join(self.stop_words))

        self.stop_words_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        layout.addWidget(self.stop_words_text, 1)

        save_stop_words_btn = QPushButton(_(self.language, "Save"))
        save_stop_words_btn.clicked.connect(self.on_save_stop_words)

        layout.addWidget(save_stop_words_btn)

        dlg.exec()

    def on_save_stop_words(self):
        content = self.stop_words_text.toPlainText().strip()
        self.stop_words = sorted(
            tuple(set([w.lower().strip() for w in content.splitlines() if w.strip()]))
        )

        with open(
            resource_path(f"spam_filter/{self.voice_language}.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("\n".join(self.stop_words) + "\n")

        self.statusBar().showMessage(
            f"{_(self.language, 'Saved')} {len(self.stop_words)} {_(self.language, 'stop words')}",
            3000,
        )

    def on_click_yt_edit_credential(self):
        self.google_api_key_input.returnPressed.connect(self.on_click_yt_save_settings)
        self.google_api_key_input.setText(self.yt_credentials)
        self.google_api_key_input.setReadOnly(False)
        self.google_api_key_button.clicked.disconnect()
        self.google_api_key_button.clicked.connect(self.on_click_yt_save_settings)
        self.google_api_key_button.setText(_(self.language, "Save"))

    def on_click_yt_save_settings(self):
        self.yt_credentials = self.google_api_key_input.text()
        self.save_settings()
        self.google_api_key_input.setText("*" * len(self.yt_credentials))
        self.google_api_key_input.setReadOnly(True)
        self.google_api_key_input.returnPressed.disconnect()
        self.google_api_key_button.clicked.disconnect()
        self.google_api_key_button.clicked.connect(self.on_click_yt_edit_credential)
        self.google_api_key_button.setText(_(self.language, "Edit"))

    def on_change_toxic_sense(self, value):
        self.toxic_sense = value / 100.0
        self.toxic_sense_label_value.setText(f"{self.toxic_sense:.2f}")

    def on_change_ban_limit(self, value):
        self.ban_limit = value
        self.ban_limit_label_value.setText(str(self.ban_limit))

    def on_delays_settings_action(self):
        dlg = QDialog(self)
        dlg.setSizePolicy(size_policy_fixed)
        dlg.setWindowFlags(window_flag_fixed)
        dlg.setWindowTitle(_(self.language, "Delays and processing"))

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(PADDING, PADDING, PADDING, PADDING)

        # Toxicity threshold

        toxic_sense_v_layout = QVBoxLayout()
        toxic_sense_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(toxic_sense_v_layout)

        self.toxic_sense_label_desc = QLabel(_(self.language, "Toxicity threshold"))
        toxic_sense_v_layout.addWidget(self.toxic_sense_label_desc)

        toxic_sense_layout = QHBoxLayout()
        toxic_sense_v_layout.addLayout(toxic_sense_layout)

        toxic_sense_slider = QSlider(Qt.Orientation.Horizontal)
        toxic_sense_layout.addWidget(toxic_sense_slider)
        toxic_sense_slider.setMinimum(10)
        toxic_sense_slider.setMaximum(100)
        toxic_sense_slider.setValue(int(self.toxic_sense * 100))
        toxic_sense_slider.valueChanged.connect(self.on_change_toxic_sense)

        self.toxic_sense_label_value = QLabel(str(self.toxic_sense))
        toxic_sense_layout.addWidget(self.toxic_sense_label_value)

        # Toxicity level for user ban

        ban_limit_v_layout = QVBoxLayout()
        ban_limit_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(ban_limit_v_layout)

        self.ban_limit_label_desc = QLabel(
            _(self.language, "Toxicity level for user ban")
        )
        ban_limit_v_layout.addWidget(self.ban_limit_label_desc)

        ban_limit_layout = QHBoxLayout()
        ban_limit_v_layout.addLayout(ban_limit_layout)

        ban_limit_slider = QSlider(Qt.Orientation.Horizontal)
        ban_limit_layout.addWidget(ban_limit_slider)
        ban_limit_slider.setMinimum(1)
        ban_limit_slider.setMaximum(100)
        ban_limit_slider.setValue(self.ban_limit)
        ban_limit_slider.valueChanged.connect(self.on_change_ban_limit)

        self.ban_limit_label_value = QLabel(str(self.ban_limit))
        ban_limit_layout.addWidget(self.ban_limit_label_value)

        # Queue depth

        queue_depth_v_layout = QVBoxLayout()
        queue_depth_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(queue_depth_v_layout)

        self.queue_depth_label_desc = QLabel(_(self.language, "queue_depth_desc"))
        queue_depth_v_layout.addWidget(self.queue_depth_label_desc)

        queue_depth_layout = QHBoxLayout()
        queue_depth_v_layout.addLayout(queue_depth_layout)

        queue_depth_slider = QSlider(Qt.Orientation.Horizontal)
        queue_depth_layout.addWidget(queue_depth_slider)
        queue_depth_slider.setMinimum(1)
        queue_depth_slider.setMaximum(100)
        queue_depth_slider.setValue(self.buffer_maxsize)
        queue_depth_slider.valueChanged.connect(self.on_change_queue_depth)

        self.queue_depth_label_value = QLabel(str(self.buffer_maxsize))
        queue_depth_layout.addWidget(self.queue_depth_label_value)

        # Min message length

        min_msg_len_v_layout = QVBoxLayout()
        min_msg_len_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(min_msg_len_v_layout)

        self.min_msg_len_label_desc = QLabel(_(self.language, "Min message length"))
        min_msg_len_v_layout.addWidget(self.min_msg_len_label_desc)

        min_msg_len_layout = QHBoxLayout()
        min_msg_len_v_layout.addLayout(min_msg_len_layout)

        min_msg_len_slider = QSlider(Qt.Orientation.Horizontal)
        min_msg_len_layout.addWidget(min_msg_len_slider)
        min_msg_len_slider.setMinimum(2)
        min_msg_len_slider.setMaximum(50)
        min_msg_len_slider.setValue(self.min_text_length)
        min_msg_len_slider.valueChanged.connect(self.on_change_min_msg_len)

        self.min_msg_len_label_value = QLabel(str(self.min_text_length))
        min_msg_len_layout.addWidget(self.min_msg_len_label_value)

        # Max message length

        msg_len_v_layout = QVBoxLayout()
        msg_len_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(msg_len_v_layout)

        self.msg_len_label_desc = QLabel(_(self.language, "Max message length"))
        msg_len_v_layout.addWidget(self.msg_len_label_desc)

        msg_len_layout = QHBoxLayout()
        msg_len_v_layout.addLayout(msg_len_layout)

        msg_len_slider = QSlider(Qt.Orientation.Horizontal)
        msg_len_layout.addWidget(msg_len_slider)
        msg_len_slider.setMinimum(50)
        msg_len_slider.setMaximum(300)
        msg_len_slider.setValue(self.max_text_length)
        msg_len_slider.valueChanged.connect(self.on_change_max_msg_len)

        self.msg_len_label_value = QLabel(str(self.max_text_length))
        msg_len_layout.addWidget(self.msg_len_label_value)

        # Speech delay

        speech_delay_v_layout = QVBoxLayout()
        # speech_delay_v_layout.setContentsMargins(0, 0, 0, PADDING)
        root_layout.addLayout(speech_delay_v_layout)

        self.speech_delay_label_desc = QLabel(
            _(self.language, "Delay between messages")
        )
        speech_delay_v_layout.addWidget(self.speech_delay_label_desc)

        speech_delay_layout = QHBoxLayout()
        speech_delay_v_layout.addLayout(speech_delay_layout)

        speech_delay_slider = QSlider(Qt.Orientation.Horizontal)
        speech_delay_layout.addWidget(speech_delay_slider)
        speech_delay_slider.setMinimum(5)
        speech_delay_slider.setMaximum(50)
        speech_delay_slider.setValue(int(self.speech_delay * 10))
        speech_delay_slider.valueChanged.connect(self.on_change_queue_speech_delay)

        self.speech_delay_label_value = QLabel(str(self.speech_delay))
        speech_delay_layout.addWidget(self.speech_delay_label_value)

        dlg.adjustSize()
        dlg.setFixedSize(dlg.sizeHint())
        dlg.finished.connect(self.save_settings)
        dlg.exec()

    def on_change_queue_speech_delay(self, value):
        self.speech_delay = value / 10
        self.speech_delay_label_value.setText(f"{float(self.speech_delay):.2f}")

    def on_change_min_msg_len(self, value):
        self.min_text_length = value
        self.min_msg_len_label_value.setText(str(self.min_text_length))

    def on_change_max_msg_len(self, value):
        self.max_text_length = value
        self.msg_len_label_value.setText(str(self.max_text_length))

    def on_change_queue_depth(self, value):
        self.buffer_maxsize = value
        self.queue_depth_label_value.setText(str(self.buffer_maxsize))
        old_queue = self.audio_queue
        self.audio_queue = Queue(maxsize=self.buffer_maxsize)
        old_items = []
        while True:
            try:
                old_items.append(old_queue.get_nowait())
            except Empty:
                break
        for item in old_items[-self.buffer_maxsize :]:
            self.audio_queue.put_nowait(item)

    def on_change_stats(self):
        if threading.current_thread() is threading.main_thread():
            self.stats_label.setText(self.stats_text())
            return
        self._pending_stats_update = True

    def on_clear_queue(self):
        while True:
            try:
                self.audio_queue.get_nowait()
            except Empty:
                break
        self.on_change_stats()
        self.statusBar().showMessage(_(self.language, "Queue cleared"), 3000)

    def toggle_add_accents(self, checked):
        self.add_accents = checked

    def toggle_read_author_names(self, checked):
        self.read_author_names = checked

    def toggle_read_platform_names(self, checked):
        self.read_platform_names = checked

    def toggle_auto_translate(self, checked):
        self.auto_translate = checked

    def toggle_chat_only_mode(self, checked):
        self.chat_only_mode = checked
        self.apply_chat_only_mode()

    def exit_chat_only_mode(self):
        if self.chat_only_mode:
            self.toggle_chat_only_mode(False)

    def apply_chat_only_mode(self):
        chat_only = self.chat_only_mode
        menu_bar = self.menuBar()
        if menu_bar is not None:
            menu_bar.setVisible(not chat_only)
        self.statusBar().setVisible(not chat_only)

        self.chat_text.setItemDelegate(
            ChatMessageDelegate(
                self.chat_text,
                only_system_msg=not chat_only,
                hide_system_msg=chat_only,
                with_avatar=False,
            )
        )

        if hasattr(self, "connections_widget"):
            self.connections_widget.setVisible(not chat_only)
        if hasattr(self, "chat_header_widget"):
            self.chat_header_widget.setVisible(not chat_only)

    def update_pause_button_text(self):
        self.pause_button.setText(
            _(self.language, "Stopped")
            if self.is_paused
            else _(self.language, "Playback...")
        )

    def toggle_auto_scroll(self, checked):
        self.auto_scroll = checked

    def toggle_show_chat_overlay(self, checked):
        self.chat_overlay_show = checked
        self.on_show_chat_overlay()

    def on_show_chat_overlay(self):
        if self.chat_overlay_show is False:
            if self.chat_overlay is not None:
                self.chat_overlay.close()
                self.chat_overlay = None
            return
        else:
            self.chat_overlay = ChatOverlayWindow(
                self,
                self.chat_model,
                self.chat_text.font(),
                lang=self.language,
                always_on_top=self.chat_overlay_always_on_top,
            )
            if self.chat_overlay_geometry is not None:
                self.chat_overlay.setGeometry(*self.chat_overlay_geometry)
            else:
                self.on_chat_overlay_reset()

            self.chat_overlay.show()

    def on_chat_overlay_reset(self):
        if hasattr(self, "chat_overlay") and self.chat_overlay:
            self.chat_overlay.setGeometry(self.x(), self.y(), 400, 600)

    def on_chat_overlay_closed(self):
        if self.chat_overlay is None:
            return

        geometry = self.chat_overlay.geometry()
        self.chat_overlay_geometry = (
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )
        self.chat_overlay_always_on_top = self.chat_overlay.always_on_top

        self.chat_overlay = None

    def clear_log(self):
        self.chat_model.clear()
        self.statusBar().showMessage(_(self.language, "Log cleared"), 3000)

    def on_load_models_action(self):
        threading.Thread(
            target=lambda: self.init_silero(self.voice_language), daemon=True
        ).start()
        if self.toxic_sense >= 1.0:
            self.add_sys_message(
                author="Detoxify",
                text=f"{_(self.language, 'Toxicity threshold')} >= 1.0, {_(self.language, 'skip load')}",
                status="warning",
            )
            return
        else:
            threading.Thread(target=self.init_detoxify, daemon=True).start()

    def on_reset_settings_action(self):
        answer = QMessageBox.question(
            self,
            _(self.language, "Reset settings"),
            _(self.language, "Reset settings to defaults?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.language = DEFAULT_LANGUAGE
        self.voice_language = DEFAULT_LANGUAGE
        self.voice = DEFAULTS["voice"]
        self.auto_scroll = DEFAULTS["auto_scroll"]
        self.add_accents = DEFAULTS["add_accents"]
        self.read_author_names = DEFAULTS["read_author_names"]
        self.read_platform_names = DEFAULTS["read_platform_names"]
        self.read_filter = DEFAULTS["read_filter"]
        self.auto_translate = DEFAULTS["auto_translate"]
        self.volume = DEFAULTS["volume"]
        self.speech_rate = DEFAULTS["speech_rate"]
        self.speech_delay = DEFAULTS["speech_delay"]
        self.min_text_length = DEFAULTS["min_text_length"]
        self.max_text_length = DEFAULTS["max_text_length"]
        self.toxic_sense = DEFAULTS["toxic_sense"]
        self.ban_limit = DEFAULTS["ban_limit"]
        self.buffer_maxsize = DEFAULTS["buffer_maxsize"]

        self.yt_credentials = None
        self.twitch_credentials = twitch_default_credentials
        self.stop_words = load_stop_words(self.voice_language)

        old_queue = self.audio_queue
        self.audio_queue = Queue(maxsize=self.buffer_maxsize)
        while True:
            try:
                old_queue.get_nowait()
            except Empty:
                break

        self.setup_menu_bar()
        self.read_filter_combo.setItems(
            [_(self.language, i) for i in DEFAULTS["read_filter"]]
        )
        self.read_filter_combo.setSelected(self.read_filter)
        self.read_filter_combo.setTitle(_(self.language, "Read messages"))
        self.auto_scroll_checkbox.setChecked(self.auto_scroll)

        self.vol_label.setText(_(self.language, "Volume"))
        self.vol_slider.setValue(self.volume)
        self.vol_label_value.setText(f"{self.volume}%")

        self.speech_rate_label.setText(_(self.language, "Speech rate"))

        self.voice_label.setText(self.status_voice_text())
        self.connect_yt_button.setText(
            _(self.language, "Connected" if self.yt_is_connected else "Connect")
        )
        self.connect_twitch_button.setText(
            _(self.language, "Connected" if self.twitch_is_connected else "Connect")
        )
        self.configure_twitch_button.setText(_(self.language, "Configure"))
        self.auto_scroll_checkbox.setText(_(self.language, "Auto-scroll"))
        self.clear_log_button.setText(_(self.language, "Clear log"))
        self.update_pause_button_text()
        self.clr_queue_button.setText(_(self.language, "Clear queue"))
        self.on_change_stats()

        self.save_settings()
        self.statusBar().showMessage(_(self.language, "Settings reset"), 3000)
        threading.Thread(
            target=lambda: self.init_silero(self.voice_language), daemon=True
        ).start()

    def export_log(self, choice):
        if choice == "html":
            log = self.chat_model_to_html()
            res = "Html Files (*.html);;All Files (*)"
        elif choice == "md":
            log = self.chat_model_to_markdown()
            res = "Markdown Files (*.md);;All Files (*)"
        else:
            log = self.chat_model_to_text()
            res = "Text Files (*.txt);;All Files (*)"

        file_path, _ = QFileDialog.getSaveFileName(self, "Save File", "", res)

        if file_path:
            file = QFile(file_path)
            if file.open(
                QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text
            ):
                file.write(log.encode("utf-8"))
                file.close()
                self.statusBar().showMessage(
                    f"{_(self.language, 'File saved')}: {file_path}", 3000
                )

    def chat_model_to_text(self):
        lines = []
        for message in self.chat_model.messages():
            text = str(message.get("text", "")).replace("\r\n", "\n")
            lines.append(
                f"[{message.get('time', '')}] [{message.get('platform', '')}] "
                f"{message.get('author', '')}: {text}"
            )
        return "\n".join(lines)

    def chat_model_to_markdown(self):
        lines = []
        for message in self.chat_model.messages():
            time_str = message.get("time", "")
            platform = message.get("platform", "")
            author = message.get("author", "")
            text = str(message.get("text", "")).replace("\r\n", "\n")
            lines.append(f"- **{author}** [{time_str}] [{platform}]: {text}")
        return "\n".join(lines)

    def chat_model_to_html(self):
        rows = [
            "<html><body style='background:#222;color:#fff;font-family:Arial,sans-serif;'>"
        ]
        for message in self.chat_model.messages():
            author = html.escape(str(message.get("author", "")))
            platform = html.escape(str(message.get("platform", "")))
            time_str = html.escape(str(message.get("time", "")))
            text = html.escape(str(message.get("text", ""))).replace("\n", "<br>")
            color = html.escape(str(message.get("color", "#fff")))
            background = html.escape(str(message.get("background", "#444")))
            rows.append(
                "<div style='margin:8px 0;padding:10px;border-radius:8px;"
                f"background:{background};color:{color};'>"
                f"<b>{author}</b> <span style='font-size:12px;'>[{time_str}] [{platform}]</span><br>"
                f"{text}</div>"
            )
        rows.append("</body></html>")
        return "".join(rows)

    def export_chat_csv(self):
        path, __ = QFileDialog.getSaveFileName(
            self,
            _(self.language, "Save CSV"),
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return

        messages = self.chat_model.messages()
        seen = set()
        rows = []
        for msg in messages:
            text = str(msg.get("text", "")).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            rows.append(text)

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "id",
                        "comment_text",
                        "toxic",
                        "severe_toxic",
                        "obscene",
                        "threat",
                        "insult",
                        "identity_hate",
                    ]
                )
                for i, text in enumerate(rows, start=1):
                    writer.writerow([i, text, 0, 0, 0, 0, 0, 0])
            self.statusBar().showMessage(
                f"{_(self.language, 'File saved')}: {path}", 3000
            )
        except Exception as e:
            self.statusBar().showMessage(
                f"{_(self.language, 'Failed to save file')}: {e}", 3000
            )

    def merge_chat_csv(self, with_recalculate_toxicity=False):

        paths, __ = QFileDialog.getOpenFileNames(
            self,
            _(self.language, "Select CSV files to merge"),
            "",
            "CSV Files (*.csv)",
        )

        if not paths:
            return

        seen_texts = set()
        rows = []

        header = [
            "id",
            "comment_text",
            "toxicity",
            "severe_toxicity",
            "obscene",
            "identity_attack",
            "insult",
            "threat",
            "sexual_explicit",
        ]

        for p in paths:
            try:
                with open(p, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)

                    for r in reader:

                        text = str(r.get("comment_text", "")).strip()

                        if not text or text in seen_texts or len(text) < 3:
                            continue

                        seen_texts.add(text)
                        rows.append(r)

            except Exception as e:
                self.statusBar().showMessage(f"Failed to read {p}: {e}", 3000)

        messages = self.chat_model.messages()

        for msg in messages:

            cleaned_text = clean_links(msg.get("text", ""), lang=self.voice_language)
            cleaned_text = clean_emoji(cleaned_text)
            text = clean_message(cleaned_text, lang=self.voice_language)

            if not text or text in seen_texts:
                continue

            seen_texts.add(text)

            rows.append(
                {
                    "comment_text": text,
                    "toxicity": "0",
                    "severe_toxicity": "0",
                    "obscene": "0",
                    "identity_attack": "0",
                    "insult": "0",
                    "threat": "0",
                    "sexual_explicit": "0",
                }
            )

        save_path, __ = QFileDialog.getSaveFileName(
            self,
            _(self.language, "Save merged CSV"),
            "",
            "CSV Files (*.csv)",
        )

        if not save_path:
            return

        try:

            with open(save_path, "w", newline="", encoding="utf-8") as f:

                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()

                for i, r in enumerate(rows, start=1):

                    cleaned_text = clean_links(
                        r.get("comment_text", ""), lang=self.voice_language
                    )
                    cleaned_text = clean_emoji(cleaned_text)
                    text = clean_message(cleaned_text, lang=self.voice_language)
                    if not text:
                        continue

                    r["id"] = str(i)

                    if with_recalculate_toxicity:
                        toxic_val = self.calc_toxicity(text)

                        for t in toxic_val:
                            r[t] = f"{toxic_val[t]:.2f}"

                    else:
                        for t in header:
                            if t == "id":
                                continue
                            if t == "comment_text":
                                r[t] = text
                            if t not in r or not r.get(t):
                                r[t] = "0"

                    writer.writerow({k: r.get(k, "") for k in header})

            self.statusBar().showMessage(
                f"{_(self.language, 'File saved')}: {save_path}",
                3000,
            )

        except Exception as e:

            self.statusBar().showMessage(
                f"{_(self.language, 'Failed to save file')}: {e}",
                3000,
            )

    # === Helper methods ===

    def stats_text(self):
        return (
            f"{_(self.language, 'Messages')}: {self.messages_stats['messages_count']} | "
            f"{_(self.language, 'Spoken')}: {self.messages_stats['spoken_count']} | "
            f"{_(self.language, 'Filtered')}: {self.messages_stats['filtered_count']} | "
            f"{_(self.language, 'In queue')}: {self.audio_queue.qsize()}"
        )

    def status_voice_text(self):
        return f"{_(self.language, 'Voice')}: {_(self.language, self.voice_language)} - {self.voice}"

    def add_sys_message(self, author, text, status="default"):
        status_colors = {
            "default": "#444444",
            "warning": "orange",
            "error": "darkRed",
            "success": "darkGreen",
        }

        return self.add_message(
            platform="system",
            author=author,
            text=text,
            background=status_colors[status],
        )

    def add_message(self, platform, author, text, color=None, background=None):
        with self.stats_lock:
            self.messages_stats["messages_count"] += 1
        self.on_change_stats()

        # If called from a non-main thread, enqueue for the GUI flush timer
        if threading.current_thread() is not threading.main_thread():
            self._pending_messages.append((platform, author, text, color, background))
            return

        # On main thread, insert immediately
        self._insert_message(platform, author, text, color, background)

    def _flush_pending_messages(self):
        while len(self._pending_ui_calls) > 0:
            callback, args, kwargs = self._pending_ui_calls.popleft()
            callback(*args, **kwargs)
        while len(self._pending_messages) > 0:
            platform, author, text, color, background = self._pending_messages.popleft()
            self._insert_message(platform, author, text, color, background)
        while len(self._pending_ui_updates) > 0:
            indicator_text = self._pending_ui_updates.popleft()
            self.audio_indicator.setText(indicator_text)
        while len(self._pending_status_messages) > 0:
            status_text, timeout_ms = self._pending_status_messages.popleft()
            self.statusBar().showMessage(status_text, timeout_ms)
        if self._pending_stats_update:
            self._pending_stats_update = False
            stats_label = getattr(self, "stats_label", None)
            if stats_label is not None:
                stats_label.setText(self.stats_text())

    def _set_audio_indicator(self, indicator_text):
        if threading.current_thread() is threading.main_thread():
            self.audio_indicator.setText(indicator_text)
            return
        self._pending_ui_updates.append(indicator_text)

    def _show_status_message(self, text, timeout_ms=3000):
        if threading.current_thread() is threading.main_thread():
            self.statusBar().showMessage(text, timeout_ms)
            return
        self._pending_status_messages.append((text, timeout_ms))

    def _insert_message(
        self,
        platform: str,
        author: str,
        text: str,
        color: str | None = None,
        background: str | None = None,
    ):
        scrollbar = self.chat_text.verticalScrollBar()
        prev_scroll_value = scrollbar.value()

        msg = ChatMessage(
            time=datetime.now().strftime("%H:%M:%S"),
            platform=platform,
            author=author,
            text=text,
            color=color,
            background=background,
        )

        try:
            self.chat_model.add_message(msg)

            if self.auto_scroll:
                self.chat_text.scrollToBottom()
                if self.chat_overlay is not None:
                    self.chat_overlay.chat_view.scrollToBottom()
            else:
                scrollbar.setValue(prev_scroll_value)
        except Exception:
            logger.error("Failed insert message to self.chat_text")
            pass

    def save_settings(self):
        settings = {
            "language": self.language,
            "voice_language": self.voice_language,
            "voice": self.voice,
            "volume": self.volume,
            "speech_rate_ssml": self.speech_rate,
            "speech_delay": self.speech_delay,
            "auto_scroll": self.auto_scroll,
            "add_accents": self.add_accents,
            "read_author_names": self.read_author_names,
            "read_platform_names": self.read_platform_names,
            "read_filter": self.read_filter,
            "font_size": self.font_size,
            "toxic_sense": self.toxic_sense,
            "ban_limit": self.ban_limit,
            "auto_translate": self.auto_translate,
            "min_text_length": self.min_text_length,
            "max_text_length": self.max_text_length,
            "buffer_maxsize": self.buffer_maxsize,
            "yt_credentials": self.yt_credentials,
            "twitch_credentials": self.twitch_credentials,
            "chat_window_geometry": self.chat_overlay_geometry,
            "chat_window_always_on_top": self.chat_overlay_always_on_top,
        }
        with open(get_settings_path(), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

        self.statusBar().showMessage(_(self.language, "Settings saved"), 3000)

    def load_settings(self):
        settings_path = get_settings_path()
        legacy_path = resource_path("settings.json")
        path_candidates = [settings_path]
        if legacy_path != settings_path:
            path_candidates.append(legacy_path)

        try:
            settings = None
            for path in path_candidates:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    break

            if settings is None:
                return

            self.language = settings.get("language", self.language)
            self.voice_language = settings.get("voice_language", self.voice_language)
            self.voice = settings.get("voice", self.voice)
            self.font_size = settings.get("font_size", self.font_size)
            self.volume = settings.get("volume", self.volume)
            self.speech_rate = settings.get("speech_rate_ssml", self.speech_rate)
            self.speech_delay = settings.get("speech_delay", self.speech_delay)
            self.auto_scroll = settings.get("auto_scroll", self.auto_scroll)
            self.add_accents = settings.get("add_accents", self.add_accents)
            self.read_author_names = settings.get(
                "read_author_names", self.read_author_names
            )
            self.read_platform_names = settings.get(
                "read_platform_names", self.read_platform_names
            )
            self.read_filter = settings.get("read_filter", self.read_filter)
            self.toxic_sense = settings.get("toxic_sense", self.toxic_sense)
            self.ban_limit = settings.get("ban_limit", self.ban_limit)
            self.auto_translate = settings.get("auto_translate", self.auto_translate)
            self.buffer_maxsize = settings.get("buffer_maxsize", self.buffer_maxsize)
            self.min_text_length = settings.get("min_text_length", self.min_text_length)
            self.max_text_length = settings.get("max_text_length", self.max_text_length)
            self.yt_credentials = settings.get("yt_credentials", self.yt_credentials)
            self.twitch_credentials = settings.get(
                "twitch_credentials", twitch_default_credentials
            )
            self.chat_overlay_always_on_top = settings.get(
                "chat_window_always_on_top", self.chat_overlay_always_on_top
            )

            self.chat_overlay_geometry = settings.get(
                "chat_window_geometry", self.chat_overlay_geometry
            )

            if not isinstance(self.twitch_credentials, dict):
                self.twitch_credentials = twitch_default_credentials

            self.stop_words = load_stop_words(self.voice_language)
            self.banned_set = self.load_banned_list()

        except FileNotFoundError:
            pass  # No settings file, use defaults

    def save_banned_list(self):
        with self.message_state_lock:
            banned_items = sorted(self.banned_set)
        with open(
            get_banned_list_path(),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("\n".join(banned_items) + "\n")

    def load_banned_list(self):
        try:
            with open(get_banned_list_path(), "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
        except FileNotFoundError:
            pass
        except Exception as e:
            pass
        return set()

    def closeEvent(self, event):
        if self.chat_overlay is not None:
            self.chat_overlay.close()
        self.save_settings()
        sd.stop()
        super().closeEvent(event)

    def init_detoxify(self):
        attempt = 0
        error_text = None

        self.add_sys_message(
            author="Detoxify", text=_(self.language, "detoxify_loading")
        )
        configure_torch_hub_cache()
        cached_checkpoint, huggingface_config_path = find_cached_detoxify_checkpoint(
            "multilingual"
        )
        detoxify_impl = get_detoxify_impl()
        Detoxify = get_detoxify()

        while attempt < 5 and not self.detox_model:
            try:
                if cached_checkpoint and attempt == 0:
                    if not getattr(detoxify_impl, "_fj_local_patch_applied", False):
                        detoxify_impl.get_model_and_tokenizer = (
                            detoxify_get_model_and_tokenizer_local_only
                        )
                        detoxify_impl._fj_local_patch_applied = True

                    self.detox_model = Detoxify(
                        model_type="multilingual",
                        checkpoint=cached_checkpoint[0],
                        huggingface_config_path=huggingface_config_path,
                    )
                else:
                    os.environ["TRANSFORMERS_OFFLINE"] = "0"
                    os.environ["HF_HUB_OFFLINE"] = "0"
                    if not getattr(detoxify_impl, "_fj_patch_applied", False):
                        detoxify_impl.get_model_and_tokenizer = lambda model_type, model_name, tokenizer_name, num_classes, state_dict, huggingface_config_path=None: detoxify_get_model_and_tokenizer_local_only(
                            model_type,
                            model_name,
                            tokenizer_name,
                            num_classes,
                            state_dict,
                            huggingface_config_path=None,
                            local_files_only=False,
                        )
                        detoxify_impl._fj_patch_applied = True
                    self.detox_model = Detoxify("multilingual")

            except Exception as e:
                attempt += 1
                error_str = str(e)
                error_text = _(self.language, error_str)

                if (
                    "PytorchStreamReader" in error_str
                    or "Ran out of input" in error_str
                ):
                    clear_cache_detoxify()

                self.add_sys_message(
                    author="Detoxify",
                    text=f"{_(self.language, 'detoxify_loading_failed')}. {error_text}. {attempt}/5",
                    status="error",
                )

        else:
            if self.detox_model and getattr(self.detox_model, "predict"):
                self.add_sys_message(
                    author="Detoxify",
                    text=_(self.language, "detoxify_loaded"),
                    status="success",
                )
            else:
                self.add_sys_message(
                    author="Detoxify",
                    text=f"{_(self.language, 'detoxify_loading_failed')}. {error_text if error_text else ""}",
                    status="error",
                )

    def init_silero(self, voice_language):
        self._run_on_ui_thread(self.voice_menu.setDisabled, True)
        attempt = 0
        error_text = None
        hub = get_torch_hub()

        while attempt < 5 and not self.model:
            if attempt > 0:
                self.add_sys_message(
                    author="Silero",
                    text=_(self.language, "silero_loading") + f" {attempt}/5",
                )
            else:
                self.add_sys_message(
                    author="Silero", text=_(self.language, "silero_loading")
                )

            try:
                with self.model_lock:
                    # if voice_language == self.voice_language:
                    if self.model and getattr(self.model, "apply_tts"):
                        pass
                    else:
                        configure_torch_hub_cache()

                        if getattr(sys, "frozen", False):
                            cached_repo = find_cached_silero_repo()
                            if cached_repo:
                                self.model, txt = hub.load(
                                    repo_or_dir=cached_repo,
                                    source="local",
                                    model="silero_tts",
                                    language=voice_language,
                                    speaker=MODELS[voice_language],
                                    trust_repo=True,
                                    force_reload=False,
                                    verbose=False,
                                )
                            else:
                                self.model, txt = hub.load(
                                    repo_or_dir="snakers4/silero-models",
                                    source="github",
                                    model="silero_tts",
                                    language=voice_language,
                                    speaker=MODELS[voice_language],
                                    trust_repo=True,
                                    force_reload=attempt > 0,
                                    verbose=False,
                                )
                        else:
                            self.model, txt = hub.load(
                                repo_or_dir="snakers4/silero-models",
                                source="github",
                                model="silero_tts",
                                language=voice_language,
                                speaker=MODELS[voice_language],
                                trust_repo=True,
                                force_reload=attempt > 0,
                                verbose=False,
                            )

            except Exception as e:
                attempt += 1
                error_text = str(e)

                if (
                    "Speaker not in the supported list" in error_text
                    or "failed reading zip archive" in error_text
                ):
                    clear_cache_silero()

        else:
            if self.model and getattr(self.model, "apply_tts"):
                self.add_sys_message(
                    author="Silero",
                    text=_(self.language, "silero_loaded"),
                    status="success",
                )
            else:
                self.add_sys_message(
                    author="Silero",
                    text=f"{_(self.language, 'silero_failed')}. {translate_text(error_text, self.language) if error_text else ""}",
                    status="error",
                )
        self._run_on_ui_thread(self.voice_menu.setDisabled, False)

    def get_msg_hash(self, platform, author, message):
        return hashlib.md5(f"{platform}:{author}:{message}".encode()).hexdigest()

    def process_toxic_message(
        self,
        platform: str,
        author: str,
        message: str,
        reason: str,
        is_staff=False,
        is_owner=False,
        severity=1.0,
    ):
        platform_author = f"{platform}:{author}"
        self.add_message(
            platform=platform,
            author=author,
            text=f"[{_(self.language, reason)} {severity:.2f}] {message}",
            color="gray",
        )
        should_save_banned_list = False
        with self.stats_lock:
            self.messages_stats["filtered_count"] += 1
        self.on_change_stats()

        if is_staff is False and is_owner is False:
            with self.message_state_lock:
                self.toxic_dict[platform_author] += severity
                if self.toxic_dict[platform_author] > self.ban_limit:
                    self.banned_set.add(platform_author)
                    should_save_banned_list = True
            if should_save_banned_list:
                self.add_sys_message(
                    author=platform,
                    text=f"[{_(self.language, 'Banned')}] {author}",
                    status="warning",
                )
                self.save_banned_list()

    def calc_toxicity(self, text):
        if self.detox_model and getattr(self.detox_model, "predict"):
            return self.detox_model.predict(text.lower())

    def process_chat_message(
        self,
        msg_id,
        platform,
        author,
        message,
        is_sponsor=False,
        is_staff=False,
        is_owner=False,
        is_donate=False,
    ):
        logger.debug(
            "process_chat_message(): msg_id=%s platform=%s author=%s",
            msg_id,
            platform,
            author,
        )

        cleaned_author = author.removeprefix("@")
        cleaned_text = clean_links(message, lang=self.voice_language)
        cleaned_text = clean_emoji(cleaned_text)

        platform_author = f"{platform}:{cleaned_author}"
        msg_id = str(msg_id)
        with self.message_state_lock:
            is_banned = platform_author in self.banned_set
            is_processed = msg_id in self.processed_messages
            if not is_processed:
                self.processed_messages.add(msg_id)

        if is_banned:
            self.add_message(
                platform=platform,
                author=cleaned_author,
                text=f"[{_(self.language, 'Banned')}] {cleaned_text}",
                color="gray",
            )
            return
        if is_processed:
            return

        if self.auto_translate:
            cleaned_text = translate_text(cleaned_text, self.voice_language)

        cleaned_text = clean_message(
            cleaned_text,
            lang=self.voice_language,
            convert_numbers=False,
            clean_spam=False,
        )

        if not cleaned_text:
            return

        cleaned_text = clean_stop_words(cleaned_text, stop_words=self.stop_words)

        toxic_val = self.calc_toxicity(cleaned_text)
        if toxic_val:
            detox_key = max(toxic_val, key=toxic_val.get)
            detox_value = toxic_val[detox_key]
            if detox_value > self.toxic_sense:
                self.process_toxic_message(
                    platform=platform,
                    author=cleaned_author,
                    message=cleaned_text,
                    reason=str(detox_key).replace("_", " ").capitalize(),
                    is_staff=is_staff,
                    is_owner=is_owner,
                    severity=detox_value,
                )
                return

        self.add_message(
            platform=platform,
            author=cleaned_author,
            text=cleaned_text,
            background="darkGreen" if is_donate else None,
        )

        if not contain_words_or_nums(cleaned_text, lang=self.voice_language):
            return

        read_filter = self.read_filter
        if is_staff and _(self.language, "Moderator") not in read_filter:
            return
        elif is_owner and _(self.language, "Author") not in read_filter:
            return
        elif is_sponsor and _(self.language, "Sponsor") not in read_filter:
            return
        elif is_donate and _(self.language, "Donation") not in read_filter:
            return
        elif (
            not is_staff
            and not is_owner
            and not is_sponsor
            and not is_donate
            and _(self.language, "Regular") not in read_filter
        ):
            return

        cleaned_text = convert_numbers_to_words(cleaned_text, self.voice_language)
        cleaned_text = clean_symbol_spam(cleaned_text)
        cleaned_text = transliteration(cleaned_text, self.voice_language)

        if len(cleaned_text) < self.min_text_length:
            return
        if len(cleaned_text) > self.max_text_length:
            cleaned_text = cleaned_text[: self.max_text_length] + "..."

        cleaned_author = clean_message(
            author,
            lang=self.voice_language,
            convert_numbers=False,
            clean_spam=False,
        )
        cleaned_text = self.cleaned_text_to_ssml(platform, cleaned_author, cleaned_text)

        self.speak(cleaned_text)

    def cleaned_text_to_text(self, platform, author, text):
        if self.read_author_names and self.read_platform_names:
            cleaned_author = re.sub(r"\d+", "", author)
            cleaned_author = clean_symbol_spam(cleaned_author)
            cleaned_author = transliteration(cleaned_author, self.voice_language)
            cleaned_text = f"{_(self.voice_language, 'Message on')} {_(self.voice_language, str(platform).lower())} {_(self.voice_language, 'from')} {cleaned_author}: {text}"

        elif self.read_author_names:
            cleaned_author = re.sub(r"\d+", "", author)
            cleaned_author = clean_symbol_spam(cleaned_author)
            cleaned_author = transliteration(cleaned_author, self.voice_language)
            cleaned_text = (
                f"{_(self.voice_language, 'Message from')} {cleaned_author} - {text}"
            )

        elif self.read_platform_names:
            cleaned_text = f"{_(self.voice_language, 'Message on')} {_(self.voice_language, str(platform).lower())}: {text}"
        else:
            cleaned_text = text

        return cleaned_text

    def cleaned_text_to_ssml(self, platform, author, text):
        if self.read_author_names and self.read_platform_names:
            cleaned_author = re.sub(r"\d+", "", author)
            cleaned_author = clean_symbol_spam(cleaned_author)
            cleaned_author = transliteration(cleaned_author, self.voice_language)
            cleaned_text = f"""
<speak>
    <s>{_(self.voice_language, 'Message on')} <prosody pitch="x-high">{_(self.voice_language, str(platform).lower())}</prosody> {_(self.voice_language, 'from')} <prosody pitch="x-high">{cleaned_author}</prosody></s>:
    <prosody rate="{self.speech_rate}" pitch="medium">{text}</prosody>
</speak>
            """

        elif self.read_author_names:
            cleaned_author = re.sub(r"\d+", "", author)
            cleaned_author = clean_symbol_spam(cleaned_author)
            cleaned_author = transliteration(cleaned_author, self.voice_language)
            cleaned_text = f"""
<speak>
    <s>{_(self.voice_language, "Message from")} <prosody pitch="x-high">{cleaned_author}</prosody></s> - <prosody rate="{self.speech_rate}" pitch="medium">{text}</prosody>
</speak>
            """

        elif self.read_platform_names:
            cleaned_text = f"""
<speak>
    <s>{_(self.voice_language, "Message on")} <prosody pitch="x-high">{_(self.voice_language, str(platform).lower())}</prosody></s>: <prosody rate="{self.speech_rate}" pitch="medium">{text}</prosody>
</speak>
            """
        else:
            cleaned_text = f'<speak><prosody rate="{self.speech_rate}" pitch="medium">{text}</prosody></speak>'

        return cleaned_text

    # == Audio processing ==

    def text_to_speech(self, text, is_ssml=True):
        """Convert text to speech using Silero"""
        logger.debug("text_to_speech(): %s", text)
        try:
            if self.model is not None and getattr(self.model, "apply_tts"):
                with self.model_lock:
                    with no_grad():
                        available_voices = VOICES.get(self.voice_language) or []
                        if not available_voices:
                            raise RuntimeError(
                                f"No voices configured for language '{self.voice_language}'"
                            )

                        selected_voice = self.voice
                        if (
                            selected_voice != "random"
                            and selected_voice not in available_voices
                        ):
                            selected_voice = "random"

                        if selected_voice == "random":
                            num = randint(0, len(available_voices) - 1)
                            selected_voice = available_voices[num]

                        return self.model.apply_tts(
                            ssml_text=text,
                            speaker=selected_voice,
                            sample_rate=SAMPLE_RATE,
                            put_accent=self.add_accents,
                            put_yo=True,
                        )
            else:
                self.add_sys_message(
                    author="Silero",
                    text=_(self.language, "silero_not_loaded"),
                    status="error",
                )
        except Exception as e:
            logger.exception(e)
            self.add_sys_message(
                author="text_to_speech()",
                text=f"{_(self.language, 'Error convert text to speech')}. {translate_text(str(e), self.language)}",
                status="error",
            )
            return None

    def postprocess_audio(self, audio):
        """Postprocess audio: convert to numpy, normalize, apply volume and speed"""
        logger.debug("postprocess_audio()")
        try:
            if hasattr(audio, "cpu"):
                audio = audio.cpu().numpy()
            else:
                audio = np.asarray(audio)
        except Exception:
            audio = np.asarray(audio)

        if audio.size == 0:
            return audio

        audio = np.asarray(audio, dtype=np.float32)

        if audio.ndim == 1:
            pass

        elif audio.ndim == 2:
            # (channels, frames) → (frames, channels)
            if audio.shape[0] < audio.shape[1]:
                audio = audio.T

        else:
            audio = np.squeeze(audio)

            if audio.ndim > 2:
                raise ValueError(f"Unsupported audio shape: {audio.shape}")

        if self.volume != 100:
            max_abs = float(np.max(np.abs(audio)))
            if not np.isfinite(max_abs) or max_abs <= 0.0:
                max_abs = 1.0
            audio = audio / max_abs

            # Apply volume (single scaling)
            audio = audio * (self.volume / 100.0)

        return audio

    def speak(self, text):
        """Main TTS method"""
        logger.debug("speak(): %s", text)
        try:
            audio = self.text_to_speech(text)
            if audio is None:
                return False
            audio_numpy = self.postprocess_audio(audio)
            if len(audio_numpy) > 0:
                self._put_audio_latest(audio_numpy)
                return True
            return False

        except Exception as e:
            self.add_sys_message(
                author="speak()",
                text=f"{_(self.language, 'Audio playback error')}. {translate_text(str(e), self.language)}",
                status="error",
            )
            return False

    def _put_audio_latest(self, audio_numpy):
        logger.debug("_put_audio_latest()")
        while True:
            try:
                self.audio_queue.put_nowait(audio_numpy)
                return
            except Full:
                try:
                    self.audio_queue.get_nowait()
                except Empty:
                    continue

    def play_audio(self, audio_to_play):
        logger.debug("play_audio()")
        try:
            self._set_audio_indicator("🔴")

            with sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1 if audio_to_play.ndim == 1 else audio_to_play.shape[1],
                dtype="float32",
                blocksize=0,
                latency="high",
            ) as stream:
                stream.write(audio_to_play)

            # sd.play(audio_to_play, device=sd.default.device, blocking=True, samplerate=SAMPLE_RATE, latency="high")
            # sd.wait()
        except Exception as e:
            self.add_sys_message(
                author="play_audio()",
                text=f"{_(self.language, 'Audio playback error')}. {translate_text(str(e), self.language)}",
                status="error",
            )
        finally:
            self._set_audio_indicator("🟢")

    def process_audio_loop(self):
        logger.debug("process_audio_loop()")
        """Main loop to process audio queue"""
        while True:
            played_message = False
            try:
                if self.is_paused:
                    sleep(0.1)
                    continue

                try:
                    audio_data = self.audio_queue.get(timeout=0.2)
                except Empty:
                    continue

                self.play_audio(audio_data)
                played_message = True

                del audio_data
                gc.collect()

                with self.stats_lock:
                    self.messages_stats["spoken_count"] += 1
                self.on_change_stats()

            except Exception as e:
                self.add_sys_message(
                    author="process_audio_loop()",
                    text=f"{_(self.language, 'Audio queue error')}. {translate_text(str(e), self.language)}",
                    status="error",
                )
            if played_message:
                sleep(self.speech_delay)

    def process_messages_loop(self):
        logger.debug("process_messages_loop()")

        while True:
            try:
                try:
                    msg_data: PlatformMessage = self.process_message_queue.get(
                        timeout=0.2
                    )
                except Empty:
                    continue

                if not self._is_active_connection_token(
                    msg_data["platform"], msg_data["connection_token"]
                ):
                    continue

                self.process_chat_message(
                    msg_id=msg_data["msg_id"],
                    platform=msg_data["platform"],
                    author=msg_data["author"],
                    message=msg_data["message"],
                    is_sponsor=msg_data["is_sponsor"],
                    is_staff=msg_data["is_staff"],
                    is_owner=msg_data["is_owner"],
                    is_donate=msg_data["is_donate"],
                )

            except Exception as e:
                self.add_sys_message(
                    author="process_messages_loop()",
                    text=f"{_(self.language, 'Process message queue error')}. {translate_text(str(e), self.language)}",
                    status="error",
                )

            sleep(0.1)

    def _open_connection_token(self, platform: str) -> int:
        self._connection_token_seq += 1
        token = self._connection_token_seq
        self._active_connection_tokens[platform] = token
        return token

    def _close_connection_token(self, platform: str):
        self._active_connection_tokens[platform] = None

    def _is_active_connection_token(self, platform: str, token: int) -> bool:
        return self._active_connection_tokens.get(platform) == token

    def _run_on_ui_thread(self, callback, *args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            callback(*args, **kwargs)
            return
        self._pending_ui_calls.append((callback, args, kwargs))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Darwin")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
