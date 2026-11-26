# -*- coding: utf-8 -*-
"""Composer: attachments, input, and the send/stop button."""

from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
)


class _AutoGrowFilter(QObject):
    """根据输入行数自动平滑调整 QPlainTextEdit 高度（从紧凑单行随内容增高至上限）。"""

    def __init__(self, edit: QPlainTextEdit, *, min_height: int = 44, max_height: int = 168):
        super().__init__(edit)
        self._edit = edit
        self._min_h = max(32, int(min_height))
        self._max_h = max(self._min_h, int(max_height))
        self._last_h = self._min_h
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        edit.setFixedHeight(self._min_h)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        edit.installEventFilter(self)
        edit.textChanged.connect(self.sync)
        doc_layout = edit.document().documentLayout()
        if doc_layout is not None:
            doc_layout.documentSizeChanged.connect(lambda _sz: self.sync())
        QTimer.singleShot(0, self.sync)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._edit and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.sync()
        return super().eventFilter(watched, event)

    def sync(self) -> None:
        edit = self._edit
        if not edit.toPlainText():
            clamped = self._min_h
            if edit.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
                edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            if clamped != self._last_h:
                self._last_h = clamped
                edit.setFixedHeight(clamped)
                edit.updateGeometry()
            return
        doc = edit.document()
        doc_layout = doc.documentLayout()
        # QPlainTextEdit 的 documentSize().height() 是视觉行数（含自动换行）
        raw_lines = float(doc_layout.documentSize().height()) if doc_layout is not None else float(doc.blockCount())
        lines = max(1.0, raw_lines)
        line_h = max(18, edit.fontMetrics().lineSpacing())
        cm = edit.contentsMargins()
        frame = edit.frameWidth()
        doc_margin = math.ceil(doc.documentMargin() * 2)
        # cm 已包含 QSS 上下 padding，仅加 4px 视觉余量，避免空输入框虚高
        wanted = math.ceil(lines * line_h) + doc_margin + cm.top() + cm.bottom() + frame * 2 + 4
        clamped = max(self._min_h, min(self._max_h, wanted))
        need_scroll = wanted > self._max_h
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if need_scroll
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if edit.verticalScrollBarPolicy() != policy:
            edit.setVerticalScrollBarPolicy(policy)
        if clamped != self._last_h:
            self._last_h = clamped
            edit.setFixedHeight(clamped)
            edit.updateGeometry()


def install_autogrow_plain_edit(
    edit: QPlainTextEdit,
    *,
    min_height: int = 44,
    max_height: int = 168,
) -> _AutoGrowFilter:
    flt = _AutoGrowFilter(edit, min_height=min_height, max_height=max_height)
    edit._autogrow_filter = flt
    return flt


class ModelPillButton(QPushButton):
    """胶囊式模型选择器（点击向上弹出模型菜单，选定后在输入框左下方实时展示当前模型）。"""

    def __init__(self, model_combo: QComboBox, parent=None, *, extra_menu_builder=None):
        super().__init__(parent)
        self._combo = model_combo
        self._extra_builder = extra_menu_builder
        self._suffix_badge = ''
        self.setObjectName('chat-model-pill')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        model_combo.hide()
        model_combo.installEventFilter(self)
        model_combo.currentIndexChanged.connect(lambda *_: self.sync())
        m = model_combo.model()
        if m is not None:
            m.rowsInserted.connect(lambda *_: self.sync())
            m.rowsRemoved.connect(lambda *_: self.sync())
            m.modelReset.connect(lambda *_: self.sync())
            m.dataChanged.connect(lambda *_: self.sync())
        self.clicked.connect(self._open_popup)
        self.sync()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._combo and event.type() == QEvent.Type.EnabledChange:
            self.setEnabled(self._combo.isEnabled())
        return super().eventFilter(watched, event)

    def set_suffix_badge(self, badge: str) -> None:
        self._suffix_badge = str(badge or '').strip()
        self.sync()

    def sync(self) -> None:
        raw = (self._combo.currentText() or '').strip()
        if not raw:
            label = (self._combo.placeholderText() or '选择模型').strip()
        else:
            # 优先展示配置名或精简模型名，避免过长撑宽
            parts = [p.strip() for p in raw.split('·') if p.strip()]
            label = parts[0] if parts else raw
            if len(label) > 24:
                label = label[:23] + '…'
        if self._suffix_badge:
            label = f'{label} · {self._suffix_badge}'
        self.setText(f'{label}  ▾')
        self.setToolTip(raw or label)
        self.setEnabled(self._combo.isEnabled())

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(bool(enabled))

    def _open_popup(self) -> None:
        menu = QMenu(self)
        menu.setObjectName('chat-model-popup')
        header = menu.addAction('Model')
        header.setEnabled(False)
        count = self._combo.count()
        cur = self._combo.currentIndex()
        if count == 0:
            empty_act = menu.addAction('暂无已启用模型（请前往设置添加）')
            empty_act.setEnabled(False)
        else:
            for idx in range(count):
                text = self._combo.itemText(idx)
                prefix = '✓  ' if idx == cur else '    '
                act = menu.addAction(f'{prefix}{text}')
                act.triggered.connect(lambda _checked=False, i=idx: self._combo.setCurrentIndex(i))
        if callable(self._extra_builder):
            self._extra_builder(menu)
        menu.ensurePolished()
        hint = menu.sizeHint()
        global_top_left = self.mapToGlobal(QPoint(0, 0))
        target_y = global_top_left.y() - hint.height() - 6
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if target_y < avail.top() + 8:
                target_y = global_top_left.y() + self.height() + 4
        menu.exec(QPoint(global_top_left.x(), target_y))


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        if self._compact:
            layout.setContentsMargins(10, 8, 8, 8)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(6)
        self.attachment_bar = QLabel()
        self.attachment_bar.setObjectName('field-hint')
        self.attachment_bar.setWordWrap(True)
        self.attachment_bar.hide()
        layout.addWidget(self.attachment_bar)
        self.input = QPlainTextEdit()
        self.input.setObjectName('chat-composer-input')
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        install_autogrow_plain_edit(
            self.input,
            min_height=40 if self._compact else 46,
            max_height=76 if self._compact else 168,
        )
        self.input.installEventFilter(self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.add_attachment_btn = QToolButton()
        self.add_attachment_btn.setObjectName('chat-attach-btn')
        self.add_attachment_btn.setText('+')
        self.add_attachment_btn.setFixedSize(28, 28)
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
        self.retry_btn.setFixedHeight(28)
        self.retry_btn.clicked.connect(self.regenerate_requested.emit)
        self.send_btn = QPushButton('发送')
        self.send_btn.setObjectName('chat-send-btn')
        self.send_btn.setFixedHeight(28)
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
            row.addWidget(self.add_attachment_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.send_hint, 1, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addLayout(row)
        self.set_language('zh')

    def insert_footer_widget(self, index: int, widget, stretch: int = 0) -> None:
        if self._footer_row is not None and widget is not None:
            self._footer_row.insertWidget(index, widget, stretch, Qt.AlignmentFlag.AlignVCenter)

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
