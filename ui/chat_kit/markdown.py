# -*- coding: utf-8 -*-
"""Markdown body and fenced code blocks. No remote resources."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QTextDocument, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

_CODE_LIMIT = 320
_HEADER = 32


class OfflineDocument(QTextDocument):
    def loadResource(self, resource_type, url):  # noqa: N802
        return None


class _FittingBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(OfflineDocument(self))
        self.setObjectName('chat-markdown')
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self.document().setDocumentMargin(0)
        self.setMinimumWidth(0)
        font = QFont()
        font.setPixelSize(14)
        self.setFont(font)
        self.document().setDefaultFont(font)

    def set_markdown(self, text: str, width: int) -> None:
        features = (
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML
        )
        self.document().setMarkdown(str(text or ''), features)
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self._fit(width)

    def _fit(self, width: int) -> None:
        inner = max(40, int(width) - 8)
        self.document().setTextWidth(inner)
        height = int(self.document().size().height()) + 4
        self.setFixedHeight(max(20, height))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, self.height() or 20)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self.height() or 20)


class _CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-code-edit')
        self.setReadOnly(True)
        self.setMinimumWidth(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont()
        font.setFamilies(['Cascadia Mono', 'Consolas', 'Courier New'])
        font.setPixelSize(13)
        self.setFont(font)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 24)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, max(24, self.height()))


class CodeBlock(QFrame):
    def __init__(self, language: str, code: str, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-code-block')
        self.setMaximumHeight(_CODE_LIMIT)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._code = str(code or '').replace('\r\n', '\n')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        self.language = QLabel((language or 'text').strip() or 'text')
        self.language.setObjectName('chat-code-lang')
        copy = QPushButton('复制')
        copy.setObjectName('chat-copy-btn')
        copy.clicked.connect(self._copy)
        bar.addWidget(self.language, 1)
        bar.addWidget(copy, 0)
        layout.addLayout(bar)
        self.editor = _CodeEditor()
        self.editor.setPlainText(self._code)
        layout.addWidget(self.editor)
        self._fit_height()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._code)

    def _fit_height(self) -> None:
        metrics = self.editor.fontMetrics()
        lines = max(1, self._code.count('\n') + (0 if self._code.endswith('\n') or not self._code else 1))
        if self._code.endswith('\n'):
            lines = max(1, self._code.count('\n'))
        wanted = lines * max(16, metrics.lineSpacing()) + 8
        cap = _CODE_LIMIT - _HEADER
        height = min(cap, max(28, wanted))
        self.editor.setFixedHeight(height)
        self.setFixedHeight(min(_CODE_LIMIT, height + _HEADER))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.editor.viewport().update()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 28)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self.height() or 48)


class MarkdownBody(QWidget):
    """Prose in a text browser, fenced regions in code blocks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._source = ''
        self._plain = False
        self._width = 320
        self._blocks: list[QWidget] = []

    def set_width(self, width: int) -> None:
        self._width = max(80, int(width))
        self._rebuild()

    def set_markdown(self, text: str) -> None:
        self._source = str(text or '')
        self._plain = False
        self._rebuild()

    def set_plain_text(self, text: str) -> None:
        self._source = str(text or '')
        if self._plain and self._blocks and self._source.strip():
            block = self._blocks[0]
            block.setPlainText(self._source)
            block._fit(self._width)
            return
        self._plain = True
        self._rebuild()

    def append_text(self, piece: str) -> None:
        self._source += str(piece or '')
        self._rebuild()

    def source(self) -> str:
        return self._source

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._blocks.clear()
        blocks = [('plain', '', self._source)] if self._plain else _split_fences(self._source)
        for kind, lang, body in blocks:
            if kind == 'code':
                block = CodeBlock(lang, body)
            else:
                if not str(body or '').strip():
                    continue
                block = _FittingBrowser()
                if kind == 'plain':
                    block.setPlainText(body)
                    block._fit(self._width)
                else:
                    block.set_markdown(body, self._width)
            block.setMinimumWidth(0)
            block.setMaximumWidth(self._width)
            self._layout.addWidget(block)
            self._blocks.append(block)
        if not self._blocks:
            spacer = QWidget()
            spacer.setFixedHeight(0)
            self._layout.addWidget(spacer)


def _split_fences(text: str) -> list[tuple[str, str, str]]:
    """Split on ``` fences. An unclosed fence stays a code block."""

    parts = str(text or '').split('```')
    blocks: list[tuple[str, str, str]] = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            blocks.append(('md', '', part))
            continue
        language, code = _code_language(part)
        blocks.append(('code', language, code))
    return blocks


def _code_language(part: str) -> tuple[str, str]:
    if '\n' not in part:
        token = part.strip()
        if token and ' ' not in token and len(token) <= 20:
            return token, ''
        return '', part
    first, rest = part.split('\n', 1)
    token = first.strip()
    if token and ' ' not in token and len(token) <= 20:
        return token, rest
    return '', part
