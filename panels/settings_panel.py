# -*- coding: utf-8 -*-
from PyQt6.QtCore import QEvent, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLayout,
    QInputDialog, QLabel, QLineEdit, QPushButton, QSlider,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from config import DEFAULT_SETTINGS, normalize_settings, save_settings
from ui.field_metrics import size_combo
from ui.theme_manager import THEME_META, preview_swatches, resolve_theme_id


class ThemePreviewWidget(QWidget):
    """在自身 paintEvent 中绘制微型界面预览，避免父卡片绘制被子控件覆盖。"""

    def __init__(self, theme_id: str, parent=None):
        super().__init__(parent)
        self.theme_id = theme_id
        self._swatches = preview_swatches(theme_id)
        self.setObjectName('theme-card-preview')
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_theme_id(self, theme_id: str):
        self.theme_id = theme_id
        self._swatches = preview_swatches(theme_id)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._swatches
        rect = self.rect()
        # 主背景
        painter.fillRect(rect, QColor(s['bg']))
        # 左侧导航
        sidebar_w = max(14, int(rect.width() * 0.18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(s['sidebar']))
        painter.drawRoundedRect(2, 2, sidebar_w, rect.height() - 4, 3, 3)
        # 导航项条
        painter.setBrush(QColor(s['primary']))
        painter.setOpacity(0.85)
        painter.drawRoundedRect(5, 10, sidebar_w - 6, 6, 2, 2)
        painter.setOpacity(0.35)
        painter.drawRoundedRect(5, 20, sidebar_w - 8, 5, 2, 2)
        painter.setOpacity(1.0)
        # 白色内容卡
        card_x = sidebar_w + 8
        card_w = max(24, rect.width() - card_x - 6)
        card_h = max(20, rect.height() - 14)
        painter.setPen(QPen(QColor(s['border']), 1))
        painter.setBrush(QColor(s['surface']))
        painter.drawRoundedRect(card_x, 6, card_w, card_h, 4, 4)
        # 内容区弱分隔线
        painter.setPen(QPen(QColor(s['border']), 1))
        line_y = 6 + int(card_h * 0.38)
        painter.drawLine(card_x + 6, line_y, card_x + card_w - 6, line_y)
        # 弱文本条
        painter.setPen(Qt.PenStyle.NoPen)
        muted = QColor(s.get('text_muted') or s['border'])
        painter.setBrush(muted)
        painter.setOpacity(0.55)
        painter.drawRoundedRect(card_x + 6, line_y + 5, max(12, card_w // 2), 4, 2, 2)
        painter.setOpacity(0.35)
        painter.drawRoundedRect(card_x + 6, line_y + 12, max(10, card_w // 3), 3, 2, 2)
        painter.setOpacity(1.0)
        # 主按钮
        btn_w = max(16, min(36, card_w // 3))
        btn_h = 8
        painter.setBrush(QColor(s['primary']))
        painter.drawRoundedRect(card_x + card_w - btn_w - 6, 10, btn_w, btn_h, 3, 3)
        painter.end()


class ThemeCard(QFrame):
    """自适应主题预览卡：完整微型界面 + 当前使用标识。"""

    clicked = pyqtSignal(str)

    def __init__(self, theme_id: str, parent=None):
        super().__init__(parent)
        self.theme_id = theme_id
        self.setObjectName('theme-card')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(148, 96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setProperty('selected', False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.preview = ThemePreviewWidget(theme_id)
        layout.addWidget(self.preview, 1)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(4)
        self.name_label = QLabel()
        self.name_label.setObjectName('theme-card-name')
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        name_row.addWidget(self.name_label, 1)
        self.current_badge = QLabel()
        self.current_badge.setObjectName('theme-current-badge')
        self.current_badge.hide()
        name_row.addWidget(self.current_badge, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(name_row)
        self._swatches = preview_swatches(theme_id)

    def set_selected(self, selected: bool):
        self.setProperty('selected', selected)
        self.current_badge.setVisible(bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_title(self, title: str, *, current_label: str = ''):
        # 标题不带 ✓ 前缀；当前状态用角标
        clean = title[2:].strip() if title.startswith('✓ ') else title
        self.name_label.setText(clean)
        if current_label:
            self.current_badge.setText(current_label)
        elif not self.current_badge.text():
            self.current_badge.setText('当前使用')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.theme_id)
        super().mouseReleaseEvent(event)


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal(object)
    reset_floating_position = pyqtSignal()
    floating_opacity_preview = pyqtSignal(int)
    theme_preview = pyqtSignal(str)
    edit_floating_shortcuts = pyqtSignal()

    def __init__(self, settings, language='zh'):
        super().__init__()
        self.language = language
        self._secret_clicks = 0
        self._secret_unlocked = False
        self._ui_theme = 'calm'
        self._setup_ui()
        self.load_values(settings)
        self.set_language(language)

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

        root = QVBoxLayout(content)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.setContentsMargins(16, 10, 16, 16)
        root.setSpacing(14)
        self.title = QLabel()
        self.title.setObjectName('page-title')
        self.title.installEventFilter(self)
        root.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName('page-subtitle')
        root.addWidget(self.subtitle)

        self.appearance_group = QGroupBox()
        appearance_outer = QVBoxLayout(self.appearance_group)
        appearance_outer.setSpacing(12)

        # 一层提示即可；详细说明进标题 tooltip
        self.theme_hint = QLabel()
        self.theme_hint.setObjectName('field-hint')
        self.theme_hint.setWordWrap(True)
        appearance_outer.addWidget(self.theme_hint)
        self.theme_title = self.theme_hint  # 兼容旧引用
        self.theme_note = QLabel()
        self.theme_note.hide()

        self.theme_grid = QGridLayout()
        self.theme_grid.setSpacing(10)
        self._theme_cards = {}
        for index, theme_id in enumerate(('calm', 'clear', 'warm', 'night')):
            card = ThemeCard(theme_id)
            card.clicked.connect(self._on_theme_clicked)
            self._theme_cards[theme_id] = card
            self.theme_grid.addWidget(card, index // 2, index % 2)
        appearance_outer.addLayout(self.theme_grid)

        appearance = QFormLayout()
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 18)
        self.font_size.setSuffix(' px')
        self.font_label = QLabel()
        appearance.addRow(self.font_label, self.font_size)
        self.language_combo = QComboBox()
        size_combo(self.language_combo, 'sm')
        self.language_combo.addItem('中文', 'zh')
        self.language_combo.addItem('English', 'en')
        self.default_language_label = QLabel()
        appearance.addRow(self.default_language_label, self.language_combo)
        appearance_outer.addLayout(appearance)
        root.addWidget(self.appearance_group)

        self.float_group = QGroupBox()
        floating = QFormLayout(self.float_group)
        opacity_row = QWidget()
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(45, 100)
        self.opacity.valueChanged.connect(self._preview_opacity)
        opacity_layout.addWidget(self.opacity, 1)
        self.opacity_value = QLabel()
        self.opacity_value.setObjectName('settings-value')
        opacity_layout.addWidget(self.opacity_value)
        self.opacity_label = QLabel()
        floating.addRow(self.opacity_label, opacity_row)
        self.always_on_top = QCheckBox()
        self.always_on_top_label = QLabel()
        floating.addRow(self.always_on_top_label, self.always_on_top)
        self.show_on_startup = QCheckBox()
        self.show_on_startup_label = QLabel()
        floating.addRow(self.show_on_startup_label, self.show_on_startup)
        self.reset_position_btn = QPushButton()
        self.reset_position_btn.clicked.connect(self.reset_floating_position.emit)
        self.reset_position_label = QLabel()
        floating.addRow(self.reset_position_label, self.reset_position_btn)
        root.addWidget(self.float_group)

        # 快捷入口独立分组，避免与透明度/置顶混在一起
        self.shortcuts_group = QGroupBox()
        shortcuts_form = QFormLayout(self.shortcuts_group)
        self.edit_shortcuts_btn = QPushButton()
        self.edit_shortcuts_btn.clicked.connect(self.edit_floating_shortcuts.emit)
        self.edit_shortcuts_label = QLabel()
        shortcuts_form.addRow(self.edit_shortcuts_label, self.edit_shortcuts_btn)
        self.shortcuts_summary = QLabel()
        self.shortcuts_summary.setObjectName('field-hint')
        self.shortcuts_summary.setWordWrap(True)
        shortcuts_form.addRow(self.shortcuts_summary)
        root.addWidget(self.shortcuts_group)

        # 日报提醒（从日报页迁入）
        self.reminder_group = QGroupBox()
        reminder_form = QFormLayout(self.reminder_group)
        self.reminder_enabled = QCheckBox()
        self.reminder_enabled_label = QLabel()
        reminder_form.addRow(self.reminder_enabled_label, self.reminder_enabled)
        self.reminder_time = QSpinBox()  # placeholder; replaced below with time if available
        try:
            from PyQt6.QtWidgets import QTimeEdit
            from PyQt6.QtCore import QTime
            self.reminder_time = QTimeEdit()
            self.reminder_time.setDisplayFormat('HH:mm')
            self._reminder_uses_timeedit = True
        except Exception:
            self.reminder_time = QSpinBox()
            self.reminder_time.setRange(0, 23)
            self._reminder_uses_timeedit = False
        self.reminder_time_label = QLabel()
        reminder_form.addRow(self.reminder_time_label, self.reminder_time)
        self.reminder_save_btn = QPushButton()
        self.reminder_save_btn.clicked.connect(self._save_reminder_settings)
        reminder_form.addRow(self.reminder_save_btn)
        root.addWidget(self.reminder_group)

        self.behavior_group = QGroupBox()
        behavior = QFormLayout(self.behavior_group)
        self.close_ask = QCheckBox()
        self.close_ask_label = QLabel()
        behavior.addRow(self.close_ask_label, self.close_ask)
        self.close_default_action = QComboBox()
        size_combo(self.close_default_action, 'md')
        self.close_default_action.addItem('', 'minimize')
        self.close_default_action.addItem('', 'exit')
        self.close_default_label = QLabel()
        behavior.addRow(self.close_default_label, self.close_default_action)
        self.close_behavior_hint = QLabel()
        self.close_behavior_hint.setObjectName('field-hint')
        self.close_behavior_hint.setWordWrap(True)
        behavior.addRow(self.close_behavior_hint)
        self.copy_duration = QComboBox()
        size_combo(self.copy_duration, 'sm')
        for milliseconds in (1000, 1500, 2000, 3000):
            self.copy_duration.addItem(f'{milliseconds / 1000:g} s', milliseconds)
        self.copy_duration_label = QLabel()
        behavior.addRow(self.copy_duration_label, self.copy_duration)
        self.safety_note = QLabel()
        self.safety_note.setObjectName('ops-safety-note')
        self.safety_note.setWordWrap(True)
        self.safety_note.hide()  # 仅「直接退出」时显示
        behavior.addRow(self.safety_note)
        root.addWidget(self.behavior_group)
        self.close_ask.toggled.connect(self._refresh_close_behavior_hint)
        self.close_default_action.currentIndexChanged.connect(self._refresh_close_behavior_hint)

        self.keep_awake_group = QGroupBox()
        keep_awake = QFormLayout(self.keep_awake_group)
        self.keep_awake_enabled = QCheckBox()
        self.keep_awake_enabled_label = QLabel()
        keep_awake.addRow(self.keep_awake_enabled_label, self.keep_awake_enabled)
        self.keep_awake_interval = QSpinBox()
        self.keep_awake_interval.setRange(1, 60)
        self.keep_awake_interval_label = QLabel()
        keep_awake.addRow(self.keep_awake_interval_label, self.keep_awake_interval)
        self.keep_awake_note = QLabel()
        self.keep_awake_note.setWordWrap(True)
        self.keep_awake_note.setMinimumHeight(42)
        self.keep_awake_note.setObjectName('ops-safety-note')
        keep_awake.addRow(self.keep_awake_note)
        self.keep_awake_group.hide()
        root.addWidget(self.keep_awake_group)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.restore_btn = QPushButton()
        self.restore_btn.clicked.connect(self._restore_defaults)
        buttons.addWidget(self.restore_btn)
        self.save_btn = QPushButton()
        self.save_btn.setObjectName('primary-btn')
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn)
        root.addLayout(buttons)
        root.addStretch()

    def values(self):
        return normalize_settings({
            'font_size': self.font_size.value(),
            'ui_theme': resolve_theme_id(self._ui_theme),
            'floating_opacity': self.opacity.value(),
            'floating_always_on_top': self.always_on_top.isChecked(),
            'floating_show_on_startup': self.show_on_startup.isChecked(),
            'floating_shortcuts': list(getattr(self, '_floating_shortcuts', DEFAULT_SETTINGS.get('floating_shortcuts', [10, 2, 9, 5]))),
            'copy_feedback_ms': self.copy_duration.currentData(),
            'default_language': self.language_combo.currentData(),
            'close_ask_each_time': self.close_ask.isChecked(),
            'close_default_action': self.close_default_action.currentData(),
            'keep_awake_enabled': self.keep_awake_enabled.isChecked(),
            'keep_awake_interval_minutes': self.keep_awake_interval.value(),
        })

    def _preview_opacity(self, value):
        self.opacity_value.setText(f'{value}%')
        self.floating_opacity_preview.emit(value)

    def _on_theme_clicked(self, theme_id: str):
        theme_id = resolve_theme_id(theme_id)
        self._ui_theme = theme_id
        self._refresh_theme_cards()
        # 即时预览 + 自动保存
        try:
            from ui.theme_manager import ThemeManager
            ThemeManager.instance().apply(
                None, theme_id, font_size=self.font_size.value()
            )
        except Exception:
            pass
        self.theme_preview.emit(theme_id)
        self._save()

    def _refresh_theme_cards(self):
        current = resolve_theme_id(self._ui_theme)
        zh = self.language == 'zh'
        current_label = '当前使用' if zh else 'Current'
        for theme_id, card in self._theme_cards.items():
            card.set_selected(theme_id == current)
            name = THEME_META[theme_id][0 if zh else 1]
            card.set_title(name, current_label=current_label)

    def load_values(self, settings):
        settings = normalize_settings(settings)
        self.font_size.setValue(settings['font_size'])
        self._ui_theme = resolve_theme_id(settings.get('ui_theme', 'calm'))
        self._refresh_theme_cards()
        self.opacity.setValue(settings['floating_opacity'])
        self.opacity_value.setText(f"{settings['floating_opacity']}%")
        self.always_on_top.setChecked(settings['floating_always_on_top'])
        self.show_on_startup.setChecked(settings['floating_show_on_startup'])
        self._floating_shortcuts = list(settings.get('floating_shortcuts') or DEFAULT_SETTINGS['floating_shortcuts'])
        self._refresh_shortcuts_summary()
        self.language_combo.setCurrentIndex(self.language_combo.findData(settings['default_language']))
        self.close_ask.blockSignals(True)
        self.close_default_action.blockSignals(True)
        self.close_ask.setChecked(settings['close_ask_each_time'])
        self.close_default_action.setCurrentIndex(
            self.close_default_action.findData(settings['close_default_action'])
        )
        self.close_ask.blockSignals(False)
        self.close_default_action.blockSignals(False)
        self.keep_awake_enabled.setChecked(settings['keep_awake_enabled'])
        self.keep_awake_interval.setValue(settings['keep_awake_interval_minutes'])
        index = self.copy_duration.findData(settings['copy_feedback_ms'])
        self.copy_duration.setCurrentIndex(max(index, 0))
        self._load_reminder_values()
        self._refresh_close_behavior_hint()

    def _load_reminder_values(self):
        try:
            from tools.daily_reports import load_reminder_settings
            from PyQt6.QtCore import QTime
            reminder = load_reminder_settings()
            self.reminder_enabled.setChecked(bool(reminder.get('enabled')))
            if getattr(self, '_reminder_uses_timeedit', False):
                self.reminder_time.setTime(QTime.fromString(str(reminder.get('time') or '18:00'), 'HH:mm'))
        except Exception:
            pass

    def _save_reminder_settings(self):
        try:
            from tools.daily_reports import load_reminder_settings, save_reminder_settings
            current = load_reminder_settings()
            time_text = (
                self.reminder_time.time().toString('HH:mm')
                if getattr(self, '_reminder_uses_timeedit', False)
                else '18:00'
            )
            previous = current.get('time')
            current['enabled'] = self.reminder_enabled.isChecked()
            current['time'] = time_text
            if previous != time_text:
                current['last_reminder_date'] = ''
            save_reminder_settings(current)
            from ui.confirm_dialog import show_success
            show_success(
                self,
                '日报提醒' if self.language == 'zh' else 'Daily reminder',
                '提醒设置已保存。' if self.language == 'zh' else 'Reminder saved.',
            )
        except Exception as exc:
            from ui.confirm_dialog import show_warning
            show_warning(self, '日报提醒', str(exc))

    def _refresh_shortcuts_summary(self):
        from ui.navigation_model import display_name
        zh = self.language == 'zh'
        names = [display_name(i, self.language) for i in getattr(self, '_floating_shortcuts', [])]
        if not names:
            self.shortcuts_summary.setText('' if zh else '')
            return
        joined = ' · '.join(names)
        self.shortcuts_summary.setText(
            f'当前快捷：{joined}' if zh else f'Shortcuts: {joined}'
        )

    def _refresh_close_behavior_hint(self, *_args):
        zh = self.language == 'zh'
        ask = self.close_ask.isChecked()
        action = self.close_default_action.currentData()
        action_text = (
            ('隐藏到系统托盘' if zh else 'hide to tray')
            if action == 'minimize' else
            ('退出软件' if zh else 'exit the app')
        )
        if ask:
            self.close_behavior_hint.setText(
                '关闭时会询问：隐藏到托盘或退出。' if zh else
                'Closing will ask: tray or exit.'
            )
            self.safety_note.hide()
        else:
            self.close_behavior_hint.setText(
                f'关闭时直接「{action_text}」。' if zh else
                f'Close will immediately {action_text}.'
            )
            # 安全提醒仅对「直接退出」显示
            if action == 'exit':
                self.safety_note.setText(
                    '直接退出会关闭主窗口、悬浮栏与托盘。' if zh else
                    'Exit closes the main window, floating bar and tray.'
                )
                self.safety_note.show()
            else:
                self.safety_note.hide()

    def _save(self):
        settings = save_settings(self.values())
        self.settings_changed.emit(settings)

    def _restore_defaults(self):
        self.load_values(DEFAULT_SETTINGS)
        self._save()

    def eventFilter(self, watched, event):
        if watched is self.title and event.type() == QEvent.Type.MouseButtonRelease:
            self._secret_clicks += 1
            if self._secret_clicks >= 5:
                self._secret_clicks = 0
                self._unlock_keep_awake()
        return super().eventFilter(watched, event)

    def _unlock_keep_awake(self):
        if self._secret_unlocked:
            return
        key, accepted = QInputDialog.getText(
            self,
            '验证' if self.language == 'zh' else 'Verification',
            '输入密钥' if self.language == 'zh' else 'Enter key',
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if key != 'Lihp':
            from ui.confirm_dialog import show_warning
            show_warning(
                self,
                '验证失败' if self.language == 'zh' else 'Verification failed',
                '密钥不正确。' if self.language == 'zh' else 'Incorrect key.',
            )
            return
        self._secret_unlocked = True
        self.keep_awake_group.show()

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.title.setText('设置' if zh else 'Settings')
        self.subtitle.setText('个人偏好' if zh else 'Preferences')
        self.appearance_group.setTitle('外观' if zh else 'Appearance')
        self.theme_hint.setText(
            '选择界面外观；布局与数据不变。' if zh else
            'Choose appearance; layout and data stay the same.'
        )
        self.appearance_group.setToolTip(
            '主题仅改变外观，不影响文件、数据、SVN 与功能位置。' if zh else
            'Themes only change appearance — never files, data, SVN or feature placement.'
        )
        self._refresh_theme_cards()
        self.font_label.setText('全局字体大小' if zh else 'Global font size')
        self.default_language_label.setText('默认界面语言' if zh else 'Default language')
        self.float_group.setTitle('悬浮工具栏' if zh else 'Floating toolbar')
        self.opacity_label.setText('透明度' if zh else 'Opacity')
        self.always_on_top_label.setText('保持在其他窗口上方' if zh else 'Always on top')
        self.always_on_top.setText('启用' if zh else 'Enabled')
        self.show_on_startup_label.setText('启动软件时显示' if zh else 'Show on startup')
        self.show_on_startup.setText('启用' if zh else 'Enabled')
        self.shortcuts_group.setTitle('快捷入口' if zh else 'Shortcuts')
        self.edit_shortcuts_label.setText('编辑' if zh else 'Edit')
        self.edit_shortcuts_btn.setText('编辑快捷入口' if zh else 'Edit shortcuts')
        self._refresh_shortcuts_summary()
        self.reset_position_label.setText('位置异常时' if zh else 'If position is lost')
        self.reset_position_btn.setText('重置到屏幕右侧' if zh else 'Reset to screen right')
        self.reminder_group.setTitle('日报提醒' if zh else 'Daily report reminder')
        self.reminder_enabled_label.setText('每日提醒' if zh else 'Daily reminder')
        self.reminder_enabled.setText('启用' if zh else 'Enabled')
        self.reminder_time_label.setText('提醒时间' if zh else 'Time')
        self.reminder_save_btn.setText('保存提醒' if zh else 'Save reminder')
        self.behavior_group.setTitle('关闭与交互' if zh else 'Close & interaction')
        self.close_ask_label.setText('关闭提示' if zh else 'Close prompt')
        self.close_ask.setText('恢复关闭提示' if zh else 'Restore close prompt')
        self.close_default_label.setText('关闭时不再提示 · 默认操作' if zh else 'Default when not asking')
        self.close_default_action.setItemText(0, '隐藏到系统托盘' if zh else 'Hide to system tray')
        self.close_default_action.setItemText(1, '退出软件' if zh else 'Exit application')
        self.copy_duration_label.setText('“已复制”提示时长' if zh else '“Copied” toast duration')
        self._refresh_close_behavior_hint()
        self.keep_awake_group.setTitle('远程会话守护' if zh else 'Remote session guard')
        self.keep_awake_enabled_label.setText('防止自动锁屏' if zh else 'Prevent automatic lock')
        self.keep_awake_enabled.setText('启用' if zh else 'Enabled')
        self.keep_awake_interval_label.setText('活动间隔' if zh else 'Activity interval')
        self.keep_awake_interval.setSuffix(' 分钟' if zh else ' min')
        self.keep_awake_note.setText(
            '启用后按设定间隔发送极小鼠标活动并立即复位，仅在 PengTools 运行期间生效。'
            if zh else
            'Sends tiny mouse activity at the selected interval and immediately restores it; active only while PengTools runs.'
        )
        self.restore_btn.setText('恢复默认设置' if zh else 'Restore defaults')
        self.save_btn.setText('应用并保存' if zh else 'Apply and save')
