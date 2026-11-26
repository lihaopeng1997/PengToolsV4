# -*- coding: utf-8 -*-
"""Markdown body and fenced code blocks. No remote resources."""

from __future__ import annotations

import math
import re

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QTextDocument, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

_CODE_LIMIT = 420
_HEADER = 30

_MARKDOWN_CSS = (
    'p { margin-top: 0px; margin-bottom: 4px; }'
    'p:last-child { margin-bottom: 0px; }'
    'ul, ol { margin-top: 2px; margin-bottom: 4px; }'
    'li { margin-bottom: 1px; }'
    'h1, h2, h3, h4 { margin-top: 6px; margin-bottom: 3px; }'
    'pre, blockquote { margin-top: 3px; margin-bottom: 4px; }'
)

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
        self.document().setDocumentMargin(1)
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)
        self.setMinimumWidth(0)
        font = QFont()
        font.setFamilies(['Microsoft YaHei UI', 'Segoe UI', 'sans-serif'])
        font.setPixelSize(13)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self._fitted_height = 22
        self._last_fit_width = 0
        self._natural_width = 80

    def natural_width(self) -> int:
        return self._natural_width

    def set_markdown(self, text: str, width: int) -> None:
        features = (
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML
        )
        safe = _protect_sql_stars(text)
        self.document().setMarkdown(safe, features)
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)
        # Strip trailing paragraph bottom margin on the very last block so there is no phantom whitespace
        last_blk = self.document().lastBlock()
        if last_blk.isValid():
            bf = last_blk.blockFormat()
            if bf.bottomMargin() > 0:
                cur = self.textCursor()
                cur.setPosition(last_blk.position())
                bf.setBottomMargin(0)
                cur.setBlockFormat(bf)
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self._fit(width)

    def set_plain(self, text: str, width: int) -> None:
        self.document().setPlainText(str(text or ''))
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)
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
        pad_x = cm.left() + cm.right() + frame * 2
        doc = self.document()
        doc.setTextWidth(-1)
        ideal = math.ceil(doc.idealWidth())
        self._natural_width = max(48, ideal + pad_x + 8)
        avail = max(80, int(width) - pad_x)
        self._last_fit_width = avail
        doc.setTextWidth(avail)
        doc_h = math.ceil(doc.size().height())
        line_h = max(20, self.fontMetrics().lineSpacing())
        height = max(line_h + 4, doc_h + cm.top() + cm.bottom() + frame * 2 + 4)
        self._fitted_height = height
        self.setFixedHeight(height)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(48, self._fitted_height)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(min(max(48, self._natural_width), max(48, self._last_fit_width + 4)), self._fitted_height)


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


_SQL_BLOCK_LANGS = frozenset({'sql', 'oracle', 'mysql', 'plsql', 'postgresql', 'postgres', 'oceanbase', 'tsql'})
_SQL_SNIPPET_RE = re.compile(r'^\s*(?:--[^\n]*\n\s*)*(?:SELECT|WITH|SHOW|DESC|DESCRIBE|EXPLAIN)\b', re.IGNORECASE)


def _is_sql_code_block(language: str, code: str) -> bool:
    lang = str(language or '').strip().lower()
    if lang in _SQL_BLOCK_LANGS:
        return True
    if not lang or lang in {'text', 'txt'}:
        return bool(_SQL_SNIPPET_RE.match(str(code or '')))
    return False


class CodeBlock(QFrame):
    apply_sql_requested = pyqtSignal(str)
    run_sql_requested = pyqtSignal(str)

    def __init__(self, language: str, code: str, parent=None, *, sql_actions: bool = False):
        super().__init__(parent)
        self.setObjectName('chat-code-block')
        self.setMaximumHeight(_CODE_LIMIT)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._code = str(code or '').replace('\r\n', '\n')
        self._raw_language = (language or '').strip()
        self._sql_actions_enabled = bool(sql_actions)
        self._fitted_total_h = 84
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._header_bar = QWidget(self)
        bar = QHBoxLayout(self._header_bar)
        bar.setContentsMargins(10, 4, 8, 4)
        bar.setSpacing(6)
        self.language = QLabel(self._raw_language or 'text')
        self.language.setObjectName('chat-code-lang')
        self.apply_sql_btn = QPushButton('应用')
        self.apply_sql_btn.setObjectName('chat-sql-action-btn')
        self.apply_sql_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_sql_btn.setToolTip('将此 SQL 放入编辑器')
        self.apply_sql_btn.clicked.connect(self._emit_apply_sql)
        self.run_sql_btn = QPushButton('▷ 执行')
        self.run_sql_btn.setObjectName('chat-sql-run-btn')
        self.run_sql_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_sql_btn.setToolTip('将此 SQL 放入编辑器并立即执行')
        self.run_sql_btn.clicked.connect(self._emit_run_sql)
        copy = QPushButton('复制')
        copy.setObjectName('chat-copy-btn')
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(self._copy)
        bar.addWidget(self.language, 1)
        bar.addWidget(self.apply_sql_btn, 0)
        bar.addWidget(self.run_sql_btn, 0)
        bar.addWidget(copy, 0)
        layout.addWidget(self._header_bar)
        self.editor = _CodeEditor()
        self.editor.setPlainText(self._code)
        layout.addWidget(self.editor)
        self._refresh_sql_actions()
        self._fit_height()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._code)

    def _emit_apply_sql(self) -> None:
        text = self._code.strip()
        if text:
            self.apply_sql_requested.emit(text)

    def _emit_run_sql(self) -> None:
        text = self._code.strip()
        if text:
            self.run_sql_requested.emit(text)

    def _refresh_sql_actions(self) -> None:
        visible = self._sql_actions_enabled and bool(self._code.strip()) and _is_sql_code_block(self._raw_language, self._code)
        self.apply_sql_btn.setVisible(visible)
        self.run_sql_btn.setVisible(visible)

    def set_code(self, language: str, code: str) -> None:
        self._raw_language = (language or '').strip()
        new_lang = self._raw_language or 'text'
        if self.language.text() != new_lang:
            self.language.setText(new_lang)
        normalized = str(code or '').replace('\r\n', '\n')
        if normalized == self._code:
            self._refresh_sql_actions()
            return
        if normalized.startswith(self._code):
            delta = normalized[len(self._code):]
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(delta)
        else:
            self.editor.setPlainText(normalized)
        self._code = normalized
        self._refresh_sql_actions()
        self._fit_height()

    def _fit_height(self) -> None:
        metrics = self.editor.fontMetrics()
        trimmed = self._code.rstrip('\n')
        lines = max(1, trimmed.count('\n') + 1) if trimmed else 1
        line_h = max(18, metrics.lineSpacing())
        header_h = max(_HEADER, self._header_bar.sizeHint().height())
        wanted = lines * line_h + 24
        cap = _CODE_LIMIT - header_h - 4
        height = min(cap, max(42, wanted))
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

    content_changed = pyqtSignal()
    apply_sql_requested = pyqtSignal(str)
    run_sql_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, sql_actions: bool = False):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._source = ''
        self._plain = False
        self._width = 320
        self._sql_actions = bool(sql_actions)
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
        self.content_changed.emit()

    def set_plain_text(self, text: str) -> None:
        self._source = str(text or '')
        if self._plain and self._blocks and self._source.strip() and isinstance(self._blocks[0], _FittingBrowser):
            block = self._blocks[0]
            block.set_plain(self._source, self._width)
            self._block_specs = [('plain', '', self._source)]
            self.content_changed.emit()
            return
        self._plain = True
        self._rebuild()
        self.content_changed.emit()

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
                        tail_widget.set_plain(new_body, self._width)
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
        self.content_changed.emit()

    def _create_block(self, kind: str, lang: str, body: str) -> QWidget:
        if kind == 'code':
            block = CodeBlock(lang, body, sql_actions=self._sql_actions)
            if self._sql_actions:
                block.apply_sql_requested.connect(self.apply_sql_requested.emit)
                block.run_sql_requested.connect(self.run_sql_requested.emit)
        else:
            block = _FittingBrowser()
            if kind == 'plain':
                block.set_plain(body, self._width)
            else:
                block.set_markdown(body, self._width)
        block.setMinimumWidth(0)
        block.setMaximumWidth(self._width)
        return block

    def has_code_blocks(self) -> bool:
        return any(isinstance(b, CodeBlock) for b in self._blocks)

    def natural_width(self) -> int:
        if not self._blocks:
            return 80
        widest = 80
        for block in self._blocks:
            if isinstance(block, CodeBlock):
                return self._width
            if isinstance(block, _FittingBrowser):
                widest = max(widest, block.natural_width())
        return min(self._width, widest)

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
