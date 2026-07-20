# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


class ConfirmActionDialog(QDialog):
    def __init__(self, title, message, confirm_text='确认删除', parent=None, danger=True):
        super().__init__(parent)
        self.setObjectName('confirm-dialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName('confirm-title')
        root.addWidget(title_label)

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
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setObjectName('ops-delete-custom' if danger else 'primary-btn')
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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
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
    """关闭主窗口时的选择：托盘继续 / 退出软件。"""

    def __init__(self, language='zh', default_action='minimize', parent=None):
        super().__init__(parent)
        self._result = None
        zh = language == 'zh'
        self.setObjectName('confirm-dialog')
        self.setWindowTitle('关闭 PengTools' if zh else 'Close PengTools')
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(520)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel('关闭主窗口' if zh else 'Close main window')
        title.setObjectName('confirm-title')
        root.addWidget(title)

        subtitle = QLabel(
            '想继续在后台待命，还是彻底结束？'
            if zh else
            'Keep running in the background, or quit completely?'
        )
        subtitle.setObjectName('confirm-message')
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

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

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.cancel_button = QPushButton('取消' if zh else 'Cancel')
        self.cancel_button.setObjectName('confirm-cancel')
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        root.addLayout(footer)

        default = self.exit_button if default_action == 'exit' else self.minimize_button
        default.setFocus()

    def _choose(self, action):
        self._result = action
        self.accept()

    def selected_action(self):
        return self._result


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
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

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
    dialog = CloseActionDialog(language=language, default_action=default_action, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_action()


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
        root.setContentsMargins(18, 18, 18, 16)
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
        later.setObjectName('confirm-cancel')
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        button_row.addWidget(later)

        self._action_buttons = []
        for action_id, label, is_primary in actions:
            button = QPushButton(label)
            button.setObjectName('primary-btn' if is_primary or action_id == recommended else 'confirm-cancel')
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
