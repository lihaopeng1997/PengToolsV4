# -*- coding: utf-8 -*-
"""Markdown body and fenced code blocks. No remote resources."""

from __future__ import annotations

import math
import re

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QTextDocument, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

_CODE_LIMIT = 420
_HEADER = 36

# 保护裸露 SQL 中的 COUNT(*) / SUM(*) / SELECT * FROM 等星号，避免被 CommonMark 误解析为斜体
_INLINE_CODE_RE = re.compile(r'(`+[^`]*`+)')
_SQL_PAREN_STAR_RE = re.compile(r'\(\*\)')
_SQL_SPACE_STAR_RE = re.compile(r'(?<=[\s,])\*(?=[\s,;])')


def _protect_sql_stars(text: str) -> str:
    raw = str(text or '')
    if '*' not in raw:
        return raw
    parts = _INLINE_CODE_RE.split(raw)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        if '*' in seg:
            seg = _SQL_PAREN_STAR_RE.sub(r'(\\*)', seg)
            seg = _SQL_SPACE_STAR_RE.sub(r'\\*', seg)
            parts[i] = seg
    return ''.join(parts)


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
        self.document().setDocumentMargin(2)
        self.setMinimumWidth(0)
        font = QFont()
        font.setFamilies(['Microsoft YaHei UI', 'Segoe UI', 'sans-serif'])
        font.setPixelSize(14)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self._fitted_height = 26
        self._last_fit_width = 0

    def set_markdown(self, text: str, width: int) -> None:
        features = (
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML
        )
        safe = _protect_sql_stars(text)
        self.document().setMarkdown(safe, features)
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self._fit(width)

    def append_plain(self, delta: str, width: int) -> None:
        if not delta:
            return
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(delta)
        self._fit(width)

    def _fit(self, width: int) -> None:
        cm = self.contentsMargins()
        frame = self.frameWidth()
        vp_w = self.viewport().width() if self.isVisible() and self.viewport().width() > 40 else 0
        avail = vp_w if vp_w > 40 else max(40, int(width) - cm.left() - cm.right() - frame * 2 - 4)
        inner = max(40, avail)
        self._last_fit_width = inner
        self.document().setTextWidth(inner)
        doc_h = math.ceil(self.document().size().height())
        line_h = max(22, self.fontMetrics().lineSpacing())
        height = max(line_h + 8, doc_h + cm.top() + cm.bottom() + frame * 2 + 12)
        self._fitted_height = height
        self.setFixedHeight(height)
        self.updateGeometry()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        vp_w = self.viewport().width()
        if vp_w > 40 and abs(vp_w - self._last_fit_width) >= 4:
            self._fit(vp_w)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, self._fitted_height)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self._fitted_height)


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
        self._fitted_height = 44

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, self._fitted_height)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self._fitted_height)


class CodeBlock(QFrame):
    def __init__(self, language: str, code: str, parent=None):
        super().__init__(parent)
        self.setObjectName('chat-code-block')
        self.setMaximumHeight(_CODE_LIMIT)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._code = str(code or '').replace('\r\n', '\n')
        self._fitted_total_h = 84
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._header_bar = QWidget(self)
        bar = QHBoxLayout(self._header_bar)
        bar.setContentsMargins(10, 5, 8, 5)
        self.language = QLabel((language or 'text').strip() or 'text')
        self.language.setObjectName('chat-code-lang')
        copy = QPushButton('复制')
        copy.setObjectName('chat-copy-btn')
        copy.clicked.connect(self._copy)
        bar.addWidget(self.language, 1)
        bar.addWidget(copy, 0)
        layout.addWidget(self._header_bar)
        self.editor = _CodeEditor()
        self.editor.setPlainText(self._code)
        layout.addWidget(self.editor)
        self._fit_height()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._code)

    def set_code(self, language: str, code: str) -> None:
        new_lang = (language or 'text').strip() or 'text'
        if self.language.text() != new_lang:
            self.language.setText(new_lang)
        normalized = str(code or '').replace('\r\n', '\n')
        if normalized == self._code:
            return
        if normalized.startswith(self._code):
            delta = normalized[len(self._code):]
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(delta)
        else:
            self.editor.setPlainText(normalized)
        self._code = normalized
        self._fit_height()

    def _fit_height(self) -> None:
        metrics = self.editor.fontMetrics()
        trimmed = self._code.rstrip('\n')
        lines = max(1, trimmed.count('\n') + 1) if trimmed else 1
        line_h = max(18, metrics.lineSpacing())
        header_h = max(_HEADER, self._header_bar.sizeHint().height())
        # 12px 上下内边距 + 16px 横向滚动条预留 + 10px 缓冲，杜绝单行代码块被压成一条滚动条或出现多余竖向滚动条
        wanted = lines * line_h + 38
        cap = _CODE_LIMIT - header_h - 4
        height = min(cap, max(56, wanted))
        self.editor.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if wanted > cap else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.editor._fitted_height = height
        self.editor.setFixedHeight(height)
        self.editor.updateGeometry()
        total = min(_CODE_LIMIT, height + header_h + 4)
        self._fitted_total_h = total
        self.setFixedHeight(total)
        self.updateGeometry()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.editor.viewport().update()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, self._fitted_total_h)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self._fitted_total_h)


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
        self._block_specs: list[tuple[str, str, str]] = []
        self._spacer: QWidget | None = None

    def set_width(self, width: int) -> None:
        new_width = max(80, int(width))
        if new_width == self._width and self._blocks:
            return
        self._width = new_width
        # BUG-011: 窗口缩放/切页不再销毁重建所有 Markdown 控件，仅就地重排高度
        if self._blocks:
            for block in self._blocks:
                block.setMaximumWidth(self._width)
                if isinstance(block, _FittingBrowser):
                    block._fit(self._width)
            return
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
            self._block_specs = [('plain', '', self._source)]
            return
        self._plain = True
        self._rebuild()

    def append_text(self, piece: str) -> None:
        piece = str(piece or '')
        if not piece:
            return
        self._source += piece
        # BUG-011: 增量更新尾部块，前缀已定型的段落与代码块零重建
        raw_blocks = [('plain', '', self._source)] if self._plain else _split_fences(self._source)
        specs = [
            (kind, lang, body)
            for kind, lang, body in raw_blocks
            if kind == 'code' or str(body or '').strip()
        ]
        if not specs:
            return
        if self._spacer is not None:
            self._layout.removeWidget(self._spacer)
            self._spacer.deleteLater()
            self._spacer = None

        # 找到未变化的公共前缀长度
        common = 0
        limit = min(len(self._block_specs), len(specs))
        while common < limit and self._block_specs[common] == specs[common]:
            common += 1

        # 如果仅尾部块增长且类型一致，直接就地更新该尾部控件
        if common == len(self._block_specs) - 1 and len(specs) >= len(self._block_specs) and self._blocks:
            old_kind, old_lang, old_body = self._block_specs[-1]
            new_kind, new_lang, new_body = specs[common]
            if old_kind == new_kind:
                tail_widget = self._blocks[-1]
                if new_kind == 'code' and isinstance(tail_widget, CodeBlock):
                    tail_widget.set_code(new_lang, new_body)
                    self._block_specs[-1] = (new_kind, new_lang, new_body)
                    common += 1
                elif new_kind == 'plain' and isinstance(tail_widget, _FittingBrowser):
                    if new_body.startswith(old_body):
                        tail_widget.append_plain(new_body[len(old_body):], self._width)
                    else:
                        tail_widget.setPlainText(new_body)
                        tail_widget._fit(self._width)
                    self._block_specs[-1] = (new_kind, new_lang, new_body)
                    common += 1
                elif new_kind == 'md' and isinstance(tail_widget, _FittingBrowser):
                    tail_widget.set_markdown(new_body, self._width)
                    self._block_specs[-1] = (new_kind, new_lang, new_body)
                    common += 1

        # 移除尾部多余旧块（例如刚闭合/拆分出的块）
        while len(self._blocks) > common:
            w = self._blocks.pop()
            self._block_specs.pop()
            self._layout.removeWidget(w)
            w.hide()
            w.deleteLater()

        # 追加新增块
        for kind, lang, body in specs[common:]:
            block = self._create_block(kind, lang, body)
            self._layout.addWidget(block)
            self._blocks.append(block)
            self._block_specs.append((kind, lang, body))

    def _create_block(self, kind: str, lang: str, body: str) -> QWidget:
        if kind == 'code':
            block = CodeBlock(lang, body)
        else:
            block = _FittingBrowser()
            if kind == 'plain':
                block.setPlainText(body)
                block._fit(self._width)
            else:
                block.set_markdown(body, self._width)
        block.setMinimumWidth(0)
        block.setMaximumWidth(self._width)
        return block

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
        self._block_specs.clear()
        self._spacer = None
        blocks = [('plain', '', self._source)] if self._plain else _split_fences(self._source)
        for kind, lang, body in blocks:
            if kind != 'code' and not str(body or '').strip():
                continue
            block = self._create_block(kind, lang, body)
            self._layout.addWidget(block)
            self._blocks.append(block)
            self._block_specs.append((kind, lang, body))
        if not self._blocks:
            spacer = QWidget()
            spacer.setFixedHeight(0)
            self._layout.addWidget(spacer)
            self._spacer = spacer


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
