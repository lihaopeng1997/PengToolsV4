# -*- coding: utf-8 -*-
"""Composer: attachments, input, and the send/stop button."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout,
)


class Composer(QFrame):
    send_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    regenerate_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-composer-card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.attachment_bar = QLabel()
        self.attachment_bar.setObjectName('field-hint')
        self.attachment_bar.setWordWrap(True)
        self.attachment_bar.hide()
        layout.addWidget(self.attachment_bar)
        self.input = QPlainTextEdit()
        self.input.setObjectName('chat-composer-input')
        self.input.setMinimumHeight(120)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
        layout.addWidget(self.input, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.add_attachment_btn = QToolButton()
        self.add_attachment_btn.setObjectName('chat-attach-btn')
        self.add_attachment_btn.setText('+')
        self.add_attachment_btn.setToolTip('添加附件（文件/图片）')
        self.add_attachment_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.add_attachment_btn)
        self.add_file_action = self.menu.addAction('添加文本文件')
        self.add_img_action = self.menu.addAction('添加图片')
        self.menu.addSeparator()
        self.clear_attach_action = self.menu.addAction('清空附件')
        self.add_attachment_btn.setMenu(self.menu)
        self.send_hint = QLabel()
        self.send_hint.setObjectName('field-hint')
        self.send_hint.setWordWrap(True)
        self.send_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.retry_btn = QPushButton('重新生成')
        self.retry_btn.setObjectName('chat-retry-btn')
        self.retry_btn.clicked.connect(self.regenerate_requested.emit)
        self.send_btn = QPushButton('发送')
        self.send_btn.setObjectName('chat-send-btn')
        self.send_btn.setProperty('danger', False)
        self.send_btn.setProperty('running', False)
        self.send_btn.setProperty('actionRole', 'primary')
        self._lock_send_width()
        self.send_btn.clicked.connect(self._on_send_clicked)
        row.addWidget(self.add_attachment_btn, 0)
        row.addWidget(self.send_hint, 1)
        row.addWidget(self.retry_btn, 0)
        row.addWidget(self.send_btn, 0)
        layout.addLayout(row)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
                    return False
                if event.isAutoRepeat():
                    return True
                self.send_requested.emit()
                return True
        return super().eventFilter(watched, event)

    def set_running(self, running: bool, *, stopping: bool = False) -> None:
        if stopping:
            self.send_btn.setText('停止中…')
            self.send_btn.setEnabled(False)
            self._set_danger(True)
            self.retry_btn.setEnabled(False)
            return
        if running:
            self.send_btn.setText('停止')
            self.send_btn.setEnabled(True)
            self._set_danger(True)
            self.retry_btn.setEnabled(False)
            return
        self.send_btn.setText('发送')
        self._set_danger(False)
        self.retry_btn.setEnabled(True)

    def set_language(self, language: str) -> None:
        zh = language == 'zh'
        self.input.setPlaceholderText(
            '输入消息…（Enter 发送，Alt+Enter 换行）' if zh else 'Type message… (Enter to send, Alt+Enter for newline)'
        )
        self.retry_btn.setText('重新生成' if zh else 'Regenerate')
        self.send_hint.setText(
            'Enter 发送 · Alt+Enter 换行 · 内网模型响应仅供参考' if zh
            else 'Enter: send · Alt+Enter: newline · Model output is for reference only'
        )
        self.add_file_action.setText('添加文本文件' if zh else 'Add text file')
        self.add_img_action.setText('添加图片' if zh else 'Add image')
        self.clear_attach_action.setText('清空附件' if zh else 'Clear attachments')
        if not self.send_btn.property('running'):
            self.send_btn.setText('发送' if zh else 'Send')
        self._lock_send_width()

    def _on_send_clicked(self) -> None:
        if self.send_btn.property('running'):
            self.stop_requested.emit()
        else:
            self.send_requested.emit()

    def _set_danger(self, danger: bool) -> None:
        self.send_btn.setProperty('danger', bool(danger))
        self.send_btn.setProperty('running', bool(danger))
        self.send_btn.setProperty('actionRole', 'danger' if danger else 'primary')
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)
        self.send_btn.update()

    def _lock_send_width(self) -> None:
        current = self.send_btn.text()
        widths = []
        for label in ('发送', '停止', '停止中…', 'Send', 'Stop', 'Stopping…'):
            self.send_btn.setText(label)
            widths.append(self.send_btn.sizeHint().width())
        self.send_btn.setText(current)
        self.send_btn.setFixedWidth(max(widths) if widths else 72)
