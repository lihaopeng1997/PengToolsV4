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

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self._language = 'zh'
        self._compact = bool(compact)
        self._stopping = False
        self._composing = False
        self.setObjectName('chat-composer-card')
        self.setProperty('compactComposer', self._compact)
        self.setProperty('focused', False)
        layout = QVBoxLayout(self)
        if self._compact:
            layout.setContentsMargins(10, 8, 8, 8)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)
        self.attachment_bar = QLabel()
        self.attachment_bar.setObjectName('field-hint')
        self.attachment_bar.setWordWrap(True)
        self.attachment_bar.hide()
        layout.addWidget(self.attachment_bar)
        self.input = QPlainTextEdit()
        self.input.setObjectName('chat-composer-input')
        self.input.setMinimumHeight(40 if self._compact else 120)
        if self._compact:
            self.input.setMaximumHeight(64)
            self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.installEventFilter(self)
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
        self.send_hint.setWordWrap(not self._compact)
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
        self._footer_row = None
        if self._compact:
            self.add_attachment_btn.hide()
            self.retry_btn.hide()
            layout.addWidget(self.input, 1)
            compact_foot = QHBoxLayout()
            compact_foot.setContentsMargins(2, 0, 0, 0)
            compact_foot.setSpacing(8)
            compact_foot.addWidget(self.send_hint, 1)
            compact_foot.addWidget(
                self.send_btn,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            layout.addLayout(compact_foot)
        else:
            self._footer_row = row
            layout.addWidget(self.input, 1)
            row.addWidget(self.add_attachment_btn, 0)
            row.addWidget(self.send_hint, 1)
            row.addWidget(self.retry_btn, 0)
            row.addWidget(self.send_btn, 0)
            layout.addLayout(row)
        self.set_language('zh')

    def insert_footer_widget(self, index: int, widget, stretch: int = 0) -> None:
        if self._footer_row is not None and widget is not None:
            self._footer_row.insertWidget(index, widget, stretch)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.input:
            ev_type = event.type()
            if ev_type == QEvent.Type.FocusIn:
                self._set_focused(True)
            elif ev_type == QEvent.Type.FocusOut:
                self._set_focused(False)
            elif ev_type == QEvent.Type.InputMethod:
                self._composing = bool(event.preeditString())
            elif ev_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._composing or event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
                        return False
                    if event.isAutoRepeat():
                        return True
                    if not self.send_btn.property('running'):
                        self.send_requested.emit()
                    return True
        return super().eventFilter(watched, event)

    def _set_focused(self, focused: bool) -> None:
        if bool(self.property('focused')) == bool(focused):
            return
        self.setProperty('focused', bool(focused))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_running(self, running: bool, *, stopping: bool = False) -> None:
        was_stopping = self._stopping
        self._stopping = stopping
        active = running or stopping
        self._set_danger(active)
        if active or was_stopping:
            self.send_btn.setEnabled(not stopping)
        self.retry_btn.setEnabled(not active)
        self._update_send_text()

    def set_language(self, language: str) -> None:
        self._language = language
        zh = language == 'zh'
        self.add_attachment_btn.setToolTip('添加附件（文件/图片）' if zh else 'Add attachment (file/image)')
        if self._compact:
            self.input.setPlaceholderText('输入消息，直接向 AI 提问…' if zh else 'Ask anything…')
            self.send_hint.setText(
                'Enter 发送 · Alt+Enter 换行' if zh else 'Enter send · Alt+Enter newline'
            )
        else:
            self.input.setPlaceholderText(
                '输入消息…（Enter 发送，Alt+Enter 换行）' if zh else 'Type message… (Enter to send, Alt+Enter for newline)'
            )
            self.send_hint.setText(
                'Enter 发送 · Alt+Enter 换行 · 内网模型响应仅供参考' if zh
                else 'Enter: send · Alt+Enter: newline · Model output is for reference only'
            )
        self.retry_btn.setText('重新生成' if zh else 'Regenerate')
        self.add_file_action.setText('添加文本文件' if zh else 'Add text file')
        self.add_img_action.setText('添加图片' if zh else 'Add image')
        self.clear_attach_action.setText('清空附件' if zh else 'Clear attachments')
        self._update_send_text()

    def _update_send_text(self) -> None:
        zh = self._language == 'zh'
        if self._stopping:
            self.send_btn.setText('停止中…' if zh else 'Stopping…')
        elif self.send_btn.property('running'):
            self.send_btn.setText('停止' if zh else 'Stop')
        else:
            self.send_btn.setText('发送' if zh else 'Send')

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
        if self._compact:
            self.send_btn.setFixedWidth(68)
            return
        current = self.send_btn.text()
        widths = []
        for label in ('发送', '停止', '停止中…', 'Send', 'Stop', 'Stopping…'):
            self.send_btn.setText(label)
            widths.append(self.send_btn.sizeHint().width())
        self.send_btn.setText(current)
        self.send_btn.setFixedWidth(max(widths) if widths else 72)
