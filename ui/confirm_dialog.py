# -*- coding: utf-8 -*-
"""统一弹窗体系：确认 / 通知 / 退出选择。

设计原则（Fluent 2 / Material 3 思路）：
- 标题直接表达核心决定，不空泛
- 动作数量少、按钮语义直接
- 危险操作与普通通知层级分明
- 默认焦点落在安全动作上

API：confirm_action / show_* / offer_next_steps；
ask_close_action → (action, dont_ask) 或 None。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ui.design_system import apply_button
from ui.icons import make_badge_label, apply_icon


class ConfirmActionDialog(QDialog):
    """危险/确认操作：取消在左、确认在右，默认焦点永远在取消。"""

    def __init__(self, title, message, confirm_text='确认删除', parent=None, danger=True):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = make_badge_label('danger' if danger else 'info', size=40, icon_size=22)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_label.setWordWrap(True)
        title_wrap.addWidget(title_label)
        if danger:
            role_hint = QLabel('此操作需二次确认 · 默认焦点在「取消」')
            role_hint.setObjectName('field-hint')
            title_wrap.addWidget(role_hint)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        card = QFrame()
        card.setObjectName('confirm-card')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        message_label = QLabel(message)
        message_label.setObjectName('confirm-message')
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(message_label)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        self.cancel_button = QPushButton('取消')
        apply_button(self.cancel_button, 'secondary', compact=True)
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        apply_button(
            self.confirm_button,
            'danger' if danger else 'primary',
            compact=False,
            icon='delete' if danger else None,
            icon_size=16,
        )
        if danger:
            self.confirm_button.setObjectName('btn-danger')
        else:
            self.confirm_button.setObjectName('primary-btn')
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setDefault(False)
        self.confirm_button.setMinimumWidth(108)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        root.addLayout(buttons)
        self.cancel_button.setFocus()


class _CloseOptionCard(QFrame):
    """可键盘聚焦的选择卡片：一点即选，不再二次确认。"""

    clicked = pyqtSignal()

    def __init__(self, icon_role, title_text, tip_text, object_name, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 16, 14)
        layout.setSpacing(12)

        badge = QLabel()
        badge.setObjectName('close-option-badge')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(36, 36)
        from ui.icons import icon_pixmap
        pix = icon_pixmap(icon_role, 20)
        if not pix.isNull():
            badge.setPixmap(pix)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel(title_text)
        title.setObjectName('close-option-title')
        tip = QLabel(tip_text)
        tip.setObjectName('close-option-tip')
        tip.setWordWrap(True)
        text_col.addWidget(title)
        text_col.addWidget(tip)
        layout.addLayout(text_col, 1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class CloseActionDialog(QDialog):
    """关闭主窗口决策：隐藏托盘 / 退出；可勾选「不再提示」。

    标题直接问决策；危险动作不设默认焦点。
    """

    def __init__(self, language='zh', default_action='minimize', parent=None):
        super().__init__(parent)
        self._result = None
        zh = language == 'zh'
        self.setObjectName('confirm-dialog')
        self.setWindowTitle('关闭 PengTools？' if zh else 'Close PengTools?')
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)

        # —— 决策标题 ——
        header = QHBoxLayout()
        header.setSpacing(14)
        badge = make_badge_label('info', size=44, icon_size=24)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(6)
        title = QLabel('关闭 PengTools？' if zh else 'Close PengTools?')
        title.setObjectName('confirm-title')
        title_wrap.addWidget(title)
        subtitle = QLabel(
            '选择如何结束当前窗口。隐藏到托盘后进程仍在运行；退出将彻底结束所有服务。'
            if zh else
            'Choose how to leave this window. Tray keeps the process alive; Exit stops everything.'
        )
        subtitle.setObjectName('confirm-message')
        subtitle.setWordWrap(True)
        title_wrap.addWidget(subtitle)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        # —— 两种明确后果（本地 SVG，无 Emoji）——
        self.minimize_button = _CloseOptionCard(
            'settings',
            '隐藏到系统托盘' if zh else 'Hide to system tray',
            '主窗口离开任务栏，托盘图标可随时恢复。悬浮栏、快捷键与后台服务继续可用。'
            if zh else
            'Leaves the taskbar; reopen from tray anytime. Floating bar, hotkeys and services stay on.',
            'close-option-primary',
        )
        self.minimize_button.clicked.connect(lambda: self._choose('minimize'))
        root.addWidget(self.minimize_button)

        self.exit_button = _CloseOptionCard(
            'error',
            '退出软件' if zh else 'Exit application',
            '结束主窗口、悬浮栏、托盘与全部后台服务，进程完全退出。未保存的面板状态将丢失。'
            if zh else
            'Closes the main window, floating bar, tray and all background services. Unsaved panel state is lost.',
            'close-option-danger',
        )
        self.exit_button.clicked.connect(lambda: self._choose('exit'))
        root.addWidget(self.exit_button)

        # —— 记住选择（漂亮复选容器）——
        remember_card = QFrame()
        remember_card.setObjectName('close-remember-card')
        remember_layout = QHBoxLayout(remember_card)
        remember_layout.setContentsMargins(12, 10, 12, 10)
        remember_layout.setSpacing(10)
        self.dont_ask_check = QCheckBox(
            '关闭时不再提示' if zh else "Don't ask again when closing"
        )
        self.dont_ask_check.setObjectName('close-dont-ask')
        self.dont_ask_check.setToolTip(
            '勾选后写入设置：关闭时直接使用本次选择，不再弹出。可在「设置 → 关闭与交互」中恢复关闭提示。'
            if zh else
            'Saves Settings: use this choice next time without a prompt. Re-enable the close prompt in Settings anytime.'
        )
        remember_layout.addWidget(self.dont_ask_check, 1)
        root.addWidget(remember_card)

        # —— 底部：取消 + 设置入口提示 ——
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.cancel_button = QPushButton('取消' if zh else 'Cancel')
        apply_button(self.cancel_button, 'ghost', compact=True)
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        hint = QLabel(
            '也可在 设置 → 关闭与交互 中管理' if zh else 'Also in Settings → Close & interaction'
        )
        hint.setObjectName('field-hint')
        footer.addWidget(hint)
        root.addLayout(footer)

        # 默认焦点：安全动作（托盘）；若配置默认是 exit，仍不把焦点放危险卡上，改放取消
        if default_action == 'exit':
            self.cancel_button.setFocus()
        else:
            self.minimize_button.setFocus()

    def _choose(self, action):
        self._result = action
        self.accept()

    def selected_action(self):
        return self._result

    def dont_ask_again(self):
        return bool(self.dont_ask_check.isChecked())


class AppNoticeDialog(QDialog):
    """统一成功 / 提示 / 警告弹窗，替代原生 QMessageBox 的生硬外观。"""

    def __init__(self, title, message, kind='info', parent=None, button_text='知道了'):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = make_badge_label(kind if kind in ('info', 'success', 'warning', 'error') else 'info', size=40, icon_size=22)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_wrap.addWidget(title_label)
        if message:
            message_label = QLabel(message)
            message_label.setObjectName('confirm-message')
            message_label.setWordWrap(True)
            message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            title_wrap.addWidget(message_label)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.ok_button = QPushButton(button_text)
        apply_button(self.ok_button, 'primary', compact=False)
        self.ok_button.setObjectName('primary-btn')
        self.ok_button.setMinimumWidth(108)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)
        self.ok_button.setFocus()


def confirm_action(parent, title, message, confirm_text='确认删除', danger=True):
    return ConfirmActionDialog(
        title, message, confirm_text, parent, danger=danger
    ).exec() == QDialog.DialogCode.Accepted


def ask_close_action(parent, language='zh', default_action='minimize'):
    """返回 (action, dont_ask_again)；取消返回 None。

    action: 'minimize' | 'exit'
    dont_ask_again: bool — 为 True 时调用方应写入 settings 并关闭询问。
    """
    dialog = CloseActionDialog(language=language, default_action=default_action, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    action = dialog.selected_action()
    if action not in ('minimize', 'exit'):
        return None
    return action, dialog.dont_ask_again()


def show_info(parent, title, message, kind='info', button_text='知道了'):
    return AppNoticeDialog(title, message, kind=kind, parent=parent, button_text=button_text).exec()


def show_success(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='success', button_text=button_text)


def show_warning(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='warning', button_text=button_text)


def show_error(parent, title, message, button_text='知道了'):
    return show_info(parent, title, message, kind='error', button_text=button_text)


class NextStepDialog(QDialog):
    """懒人下一步：单次弹窗提供推荐操作，避免连环确认。"""

    def __init__(self, title, message, actions, parent=None, recommended=None):
        super().__init__(parent)
        self._result = None
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        root.addWidget(title_label)

        if message:
            message_label = QLabel(message)
            message_label.setObjectName('confirm-message')
            message_label.setWordWrap(True)
            message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(message_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()
        later = QPushButton('稍后')
        apply_button(later, 'ghost', compact=True)
        later.setObjectName('confirm-cancel')
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        button_row.addWidget(later)

        self._action_buttons = []
        for action_id, label, is_primary in actions:
            button = QPushButton(label)
            if is_primary or action_id == recommended:
                apply_button(button, 'primary', compact=True)
                button.setObjectName('primary-btn')
            else:
                apply_button(button, 'secondary', compact=True)
                button.setObjectName('btn-secondary')
            button.setAutoDefault(False)
            button.setMinimumWidth(96)
            button.clicked.connect(lambda _checked=False, value=action_id: self._choose(value))
            button_row.addWidget(button)
            self._action_buttons.append(button)
            if action_id == recommended or is_primary:
                button.setDefault(True)
                button.setFocus()
        root.addLayout(button_row)
        if not any(action_id == recommended or is_primary for action_id, _label, is_primary in actions):
            later.setDefault(True)
            later.setFocus()

    def _choose(self, action_id):
        self._result = action_id
        self.accept()

    def selected_action(self):
        return self._result


def offer_next_steps(parent, title, message, actions, recommended=None):
    """显示下一步建议，返回 action_id 或 None（稍后/取消）。

    actions: [(action_id, label, is_primary), ...]
    """
    dialog = NextStepDialog(title, message, actions, parent=parent, recommended=recommended)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_action()
