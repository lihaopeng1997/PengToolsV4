# -*- coding: utf-8 -*-
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSlider,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from config import DEFAULT_SETTINGS, normalize_settings, save_settings
from ui.field_metrics import size_combo


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal(object)
    reset_floating_position = pyqtSignal()
    floating_opacity_preview = pyqtSignal(int)

    def __init__(self, settings, language='zh'):
        super().__init__()
        self.language = language
        self._secret_clicks = 0
        self._secret_unlocked = False
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
        appearance = QFormLayout(self.appearance_group)
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
            'floating_opacity': self.opacity.value(),
            'floating_always_on_top': self.always_on_top.isChecked(),
            'floating_show_on_startup': self.show_on_startup.isChecked(),
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

    def load_values(self, settings):
        settings = normalize_settings(settings)
        self.font_size.setValue(settings['font_size'])
        self.opacity.setValue(settings['floating_opacity'])
        self.opacity_value.setText(f"{settings['floating_opacity']}%")
        self.always_on_top.setChecked(settings['floating_always_on_top'])
        self.show_on_startup.setChecked(settings['floating_show_on_startup'])
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
        self._refresh_close_behavior_hint()

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
                '关闭主窗口时会弹出选择：托盘继续 / 彻底退出。可在弹窗勾选「不再提示」。'
                if zh else
                'Closing the main window shows tray / exit choices. You can check “Don’t ask again”.'
            )
        else:
            self.close_behavior_hint.setText(
                f'当前不再弹窗：关闭主窗口将直接「{action_text}」。勾选上方「弹出确认」即可恢复再次提示。'
                if zh else
                f'Prompt is off: closing will immediately {action_text}. Re-enable the checkbox above to ask again.'
            )

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
            QMessageBox.warning(
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
        self.subtitle.setText('调整界面、悬浮工具栏和交互反馈 · 设置仅保存在本机' if zh else 'Customize interface, floating toolbar and feedback · local only')
        self.appearance_group.setTitle('界面外观' if zh else 'Appearance')
        self.font_label.setText('全局字体大小' if zh else 'Global font size')
        self.default_language_label.setText('默认界面语言' if zh else 'Default language')
        self.float_group.setTitle('悬浮工具栏' if zh else 'Floating toolbar')
        self.opacity_label.setText('透明度' if zh else 'Opacity')
        self.always_on_top_label.setText('保持在其他窗口上方' if zh else 'Always on top')
        self.always_on_top.setText('启用' if zh else 'Enabled')
        self.show_on_startup_label.setText('启动软件时显示' if zh else 'Show on startup')
        self.show_on_startup.setText('启用' if zh else 'Enabled')
        self.reset_position_label.setText('位置异常时' if zh else 'If position is lost')
        self.reset_position_btn.setText('重置到屏幕右侧' if zh else 'Reset to screen right')
        self.behavior_group.setTitle('关闭与交互' if zh else 'Close & interaction')
        self.close_ask_label.setText('关闭主窗口时' if zh else 'When closing the main window')
        self.close_ask.setText('弹出确认（可恢复「再次提示」）' if zh else 'Show close prompt (can re-enable anytime)')
        self.close_default_label.setText('关闭默认操作' if zh else 'Default close action')
        self.close_default_action.setItemText(0, '隐藏到系统托盘' if zh else 'Hide to system tray')
        self.close_default_action.setItemText(1, '退出软件' if zh else 'Exit application')
        self.copy_duration_label.setText('“已复制”显示时间' if zh else 'Copied feedback duration')
        self.safety_note.setText(
            '退出弹窗勾选「不再提示」后：此处「弹出确认」会自动关闭，并写入本次选择为默认操作。随时可重新勾选以再次提示。高风险确认与禁止删除命令始终启用，不能关闭。'
            if zh else
            'If you choose “Don’t ask again” on exit, this checkbox turns off and the chosen action becomes default. Re-enable anytime. Risk confirmations stay always on.'
        )
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
