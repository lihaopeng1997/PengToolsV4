# -*- coding: utf-8 -*-
"""统一弹窗体系：确认 / 通知 / 退出选择。

- 视觉走企业级卡片；按钮角色对齐 design_system
- 退出弹窗支持「不再提示」，由 MainWindow 写回 settings
- API 保持 confirm_action / show_* / offer_next_steps；ask_close_action 返回 (action, dont_ask)
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ui.design_system import apply_button


class ConfirmActionDialog(QDialog):
    def __init__(self, title, message, confirm_text='确认删除', parent=None, danger=True):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = QLabel('!' if danger else '?')
        badge.setObjectName('notice-badge-warning' if danger else 'notice-badge-info')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(40, 40)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
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
        apply_button(self.confirm_button, 'danger' if danger else 'primary', compact=False)
        if danger:
            self.confirm_button.setObjectName('btn-danger')
        else:
            self.confirm_button.setObjectName('primary-btn')
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setMinimumWidth(108)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        root.addLayout(buttons)
        self.cancel_button.setFocus()


class _CloseOptionCard(QFrame):
    clicked = pyqtSignal()

    def __init__(self, title_text, tip_text, object_name, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)
        title = QLabel(title_text)
        title.setObjectName('close-option-title')
        tip = QLabel(tip_text)
        tip.setObjectName('close-option-tip')
        tip.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(tip)

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
    """关闭主窗口：托盘继续 / 退出；可勾选「不再提示」。"""

    def __init__(self, language='zh', default_action='minimize', parent=None):
        super().__init__(parent)
        self._result = None
        zh = language == 'zh'
        self.setObjectName('confirm-dialog')
        self.setWindowTitle('关闭 PengTools' if zh else 'Close PengTools')
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMaximumWidth(540)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        badge = QLabel('⏻')
        badge.setObjectName('notice-badge-info')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(40, 40)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        title = QLabel('关闭主窗口' if zh else 'Close main window')
        title.setObjectName('confirm-title')
        title_wrap.addWidget(title)
        subtitle = QLabel(
            '想继续在后台待命，还是彻底结束？点选一项即可，无需再点确认。'
            if zh else
            'Keep running in the background, or quit. One click chooses — no extra confirm.'
        )
        subtitle.setObjectName('confirm-message')
        subtitle.setWordWrap(True)
        title_wrap.addWidget(subtitle)
        header.addLayout(title_wrap, 1)
        root.addLayout(header)

        self.minimize_button = _CloseOptionCard(
            '隐藏到系统托盘' if zh else 'Hide to tray',
            '任务栏不再显示窗口，托盘图标可随时重新打开。悬浮栏与快捷键继续可用。'
            if zh else
            'Leaves the taskbar; reopen anytime from the tray. Floating bar and hotkeys stay active.',
            'close-option-primary',
        )
        self.minimize_button.clicked.connect(lambda: self._choose('minimize'))
        root.addWidget(self.minimize_button)

        self.exit_button = _CloseOptionCard(
            '退出软件' if zh else 'Exit',
            '结束主窗口、悬浮栏、托盘与后台服务，进程完全退出。'
            if zh else
            'Closes the main window, floating bar, tray and background services.',
            'close-option-danger',
        )
        self.exit_button.clicked.connect(lambda: self._choose('exit'))
        root.addWidget(self.exit_button)

        remember_row = QHBoxLayout()
        remember_row.setContentsMargins(2, 2, 2, 0)
        self.dont_ask_check = QCheckBox(
            '不再提示，以后直接使用本次选择' if zh else "Don't ask again — use this choice next time"
        )
        self.dont_ask_check.setObjectName('close-dont-ask')
        self.dont_ask_check.setToolTip(
            '勾选后会写入设置：关闭时不再询问，并采用你本次选择的默认操作。可在「设置」中改回。'
            if zh else
            'Saves to Settings: skip this prompt and use the action you pick. Change anytime in Settings.'
        )
        remember_row.addWidget(self.dont_ask_check)
        remember_row.addStretch()
        root.addLayout(remember_row)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.cancel_button = QPushButton('取消' if zh else 'Cancel')
        apply_button(self.cancel_button, 'ghost', compact=True)
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        hint = QLabel('也可在 设置 → 交互反馈 中管理' if zh else 'Also available in Settings → Behavior')
        hint.setObjectName('field-hint')
        footer.addWidget(hint)
        root.addLayout(footer)

        default = self.exit_button if default_action == 'exit' else self.minimize_button
        default.setFocus()

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
        badge = QLabel(self._badge_text(kind))
        badge.setObjectName(f'notice-badge-{kind}')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(40, 40)
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

    @staticmethod
    def _badge_text(kind):
        return {
            'success': '✓',
            'warning': '!',
            'error': '×',
            'info': 'i',
        }.get(kind, 'i')


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
