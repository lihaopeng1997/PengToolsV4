# -*- coding: utf-8 -*-
"""XML 完整工作区：粘贴 → 清洗 → 格式化 → 复制，输入/输出可拖分栏。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from tools.xml_formatter import format_xml_text, normalize_xml_input
from ui.confirm_dialog import show_warning
from ui.design_system import apply_button, apply_surface
from ui.foldable_text_edit import FoldablePlainTextEdit
from ui.search_highlight import (
    apply_text_match_index,
    clear_text_highlights,
    collect_text_spans,
)


class XmlWorkspace(QWidget):
    """加解密模块内的独立 XML 工具台。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        # 搜索：[(edit, start, end), ...]
        self._search_hits: list[tuple[object, int, int]] = []
        self._search_index = -1
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QFrame()
        apply_surface(header, 'muted')
        header.setObjectName('xml-workspace-header')
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(4)
        self.zone_title = QLabel()
        self.zone_title.setObjectName('zone-title')
        header_layout.addWidget(self.zone_title)
        self.zone_hint = QLabel()
        self.zone_hint.setObjectName('field-hint')
        self.zone_hint.setWordWrap(True)
        header_layout.addWidget(self.zone_hint)
        root.addWidget(header)

        toolbar = QFrame()
        apply_surface(toolbar, 'zone')
        toolbar.setObjectName('xml-toolbar')
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(10, 8, 10, 8)
        tools.setSpacing(8)

        self.paste_btn = QPushButton()
        apply_button(self.paste_btn, 'secondary', compact=True, icon='edit', icon_size=16)
        self.paste_btn.clicked.connect(self._paste_input)
        tools.addWidget(self.paste_btn)

        self.clear_input_btn = QPushButton()
        apply_button(self.clear_input_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_input_btn.clicked.connect(self._clear_input)
        tools.addWidget(self.clear_input_btn)

        self.normalize_btn = QPushButton()
        apply_button(self.normalize_btn, 'secondary', compact=True, icon='xml', icon_size=16)
        self.normalize_btn.clicked.connect(self._normalize_only)
        tools.addWidget(self.normalize_btn)

        self.format_btn = QPushButton()
        apply_button(self.format_btn, 'primary', compact=True, icon='xml', icon_size=16)
        self.format_btn.clicked.connect(self._format)
        tools.addWidget(self.format_btn)

        tools.addStretch(1)

        self.wrap_check = QCheckBox()
        self.wrap_check.setObjectName('xml-wrap-check')
        self.wrap_check.toggled.connect(self._apply_wrap)
        tools.addWidget(self.wrap_check)

        self.expand_btn = QPushButton()
        apply_button(self.expand_btn, 'ghost', compact=True, icon='expand', icon_size=16)
        self.expand_btn.clicked.connect(self._expand_output)
        tools.addWidget(self.expand_btn)

        self.collapse_btn = QPushButton()
        apply_button(self.collapse_btn, 'ghost', compact=True, icon='collapse', icon_size=16)
        self.collapse_btn.clicked.connect(self._collapse_output)
        tools.addWidget(self.collapse_btn)

        self.glance_btn = QPushButton()
        apply_button(self.glance_btn, 'ghost', compact=True, icon='more', icon_size=16)
        self.glance_btn.setCheckable(True)
        self.glance_btn.setChecked(True)
        self.glance_btn.toggled.connect(self._toggle_glance)
        tools.addWidget(self.glance_btn)

        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_btn.clicked.connect(self._copy_output)
        tools.addWidget(self.copy_btn)

        self.clear_all_btn = QPushButton()
        apply_button(self.clear_all_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_all_btn.clicked.connect(self.clear)
        tools.addWidget(self.clear_all_btn)

        root.addWidget(toolbar)

        # 搜索条：优先输出区，无输出则搜输入；高亮并跳转
        search_bar = QHBoxLayout()
        search_bar.setContentsMargins(2, 0, 2, 0)
        search_bar.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('xml-search')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._search)
        self.search_edit.returnPressed.connect(self._next_match)
        search_bar.addWidget(self.search_edit, 1)
        self.search_prev_btn = QPushButton('↑')
        apply_button(self.search_prev_btn, 'ghost', compact=True)
        self.search_prev_btn.clicked.connect(self._prev_match)
        search_bar.addWidget(self.search_prev_btn)
        self.search_next_btn = QPushButton('↓')
        apply_button(self.search_next_btn, 'ghost', compact=True)
        self.search_next_btn.clicked.connect(self._next_match)
        search_bar.addWidget(self.search_next_btn)
        self.search_status = QLabel('0 / 0')
        self.search_status.setObjectName('field-hint')
        search_bar.addWidget(self.search_status)
        root.addLayout(search_bar)

        work = QFrame()
        apply_surface(work, 'card')
        work.setObjectName('xml-work-zone')
        work_layout = QVBoxLayout(work)
        work_layout.setContentsMargins(10, 10, 10, 10)
        work_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName('xml-splitter')
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.input_label = QLabel()
        self.input_label.setObjectName('zone-title')
        left_layout.addWidget(self.input_label)
        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName('xml-input')
        self.input_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.input_edit.textChanged.connect(self._refresh_status)
        left_layout.addWidget(self.input_edit, 1)
        self.splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self.output_label = QLabel()
        self.output_label.setObjectName('zone-title')
        right_layout.addWidget(self.output_label)
        # 输出区支持 HiJson 风格折叠
        self.output_edit = FoldablePlainTextEdit(fold_mode='indent')
        self.output_edit.setObjectName('xml-output')
        self.output_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output_edit.setReadOnly(False)
        self.output_edit.textChanged.connect(self._refresh_status)
        right_layout.addWidget(self.output_edit, 1)
        self.splitter.addWidget(right)

        self.splitter.setSizes([480, 520])
        work_layout.addWidget(self.splitter, 1)
        root.addWidget(work, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.status_label = QLabel()
        self.status_label.setObjectName('field-hint')
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        root.addLayout(footer)

        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.input_edit.setFont(mono)
        self.output_edit.setFont(mono)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.zone_title.setText('XML 工具' if zh else 'XML tools')
        self.zone_hint.setText(
            '粘贴网关/日志中的 XML（可含外层引号、\\n / \\" 转义），一键清洗并美化。数据仅在本机处理。'
            if zh else
            'Paste gateway/log XML (outer quotes, \\n / \\" escapes OK). Format locally only.'
        )
        self.paste_btn.setText('粘贴' if zh else 'Paste')
        self.clear_input_btn.setText('清空输入' if zh else 'Clear input')
        self.normalize_btn.setText('去引号/反转义' if zh else 'Strip / unescape')
        self.normalize_btn.setToolTip(
            '仅清洗外层双引号与常见转义，不做缩进格式化'
            if zh else
            'Strip outer quotes and common escapes only — no indent'
        )
        self.format_btn.setText('一键格式化' if zh else 'Format')
        self.format_btn.setToolTip(
            '清洗 + 校验 + 缩进美化（保留 XML 声明）'
            if zh else
            'Normalize, validate and pretty-print (keeps XML declaration)'
        )
        self.wrap_check.setText('自动换行' if zh else 'Word wrap')
        self.expand_btn.setText('全部展开' if zh else 'Expand all')
        self.expand_btn.setToolTip(
            '展开输出区折叠块' if zh else 'Expand all folds in output'
        )
        self.collapse_btn.setText('全部折叠' if zh else 'Collapse all')
        self.collapse_btn.setToolTip(
            '折叠输出区（参考 HiJson 格式化文本折叠）' if zh else
            'Collapse output folds (HiJson-style)'
        )
        self.glance_btn.setText('缩略图' if zh else 'Minimap')
        self.glance_btn.setToolTip(
            '右侧 CodeGlance 缩略导航：点击跳转 / 拖视口滚动 / 拖左边调宽'
            if zh else
            'CodeGlance minimap: click jump / drag viewport / resize from left edge'
        )
        self.copy_btn.setText('复制结果' if zh else 'Copy result')
        self.clear_all_btn.setText('全部清空' if zh else 'Clear all')
        self.search_edit.setPlaceholderText(
            '搜索输入/输出中的标签、属性或文本（有输出时优先搜输出）· 回车下一个'
            if zh else
            'Search input/output (prefer output) · Enter next'
        )
        self.search_prev_btn.setToolTip('上一个' if zh else 'Previous')
        self.search_next_btn.setToolTip('下一个' if zh else 'Next')
        self.input_label.setText('输入' if zh else 'Input')
        self.output_label.setText('输出' if zh else 'Output')
        self.input_edit.setPlaceholderText(
            '在此粘贴 XML 原文… 支持外层引号与转义字符'
            if zh else
            'Paste raw XML here… outer quotes and escapes supported'
        )
        self.output_edit.setPlaceholderText(
            '格式化结果将显示在这里，可编辑后复制；左侧 +/- 可折叠'
            if zh else
            'Formatted result appears here; use +/- margin to fold'
        )
        self._refresh_status()

    def set_input_text(self, text: str, *, auto_format: bool = False):
        """外部灌入文本（例如解密结果像 XML 时）。"""
        self.input_edit.setPlainText(text or '')
        if auto_format and (text or '').strip():
            self._format()
        else:
            self._refresh_status()

    def input_text(self) -> str:
        return self.input_edit.toPlainText()

    def output_text(self) -> str:
        return self.output_edit.toPlainText()

    def clear(self):
        self.input_edit.clear()
        self.output_edit.clear()
        self._clear_search()
        if hasattr(self, 'search_edit'):
            self.search_edit.clear()
        self._refresh_status()

    def _apply_wrap(self, enabled: bool):
        mode = (
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if enabled else
            QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.input_edit.setLineWrapMode(mode)
        self.output_edit.setLineWrapMode(mode)

    def _paste_input(self):
        text = QApplication.clipboard().text()
        if text:
            self.input_edit.setPlainText(text)
            self.input_edit.moveCursor(QTextCursor.MoveOperation.Start)
            self._refresh_status()

    def _clear_input(self):
        self.input_edit.clear()
        self._refresh_status()

    def _normalize_only(self):
        text = self.input_edit.toPlainText()
        try:
            cleaned = normalize_xml_input(text)
        except ValueError as exc:
            self._show_error(str(exc))
            return False
        self.output_edit.setPlainText(cleaned)
        zh = self.language == 'zh'
        self.status_label.setText(
            f'已清洗 · {self._stats(cleaned)} · 尚未做缩进格式化'
            if zh else
            f'Normalized · {self._stats(cleaned)} · not pretty-printed yet'
        )
        self.status_label.setProperty('xmlStatus', 'ok')
        self._repolish_status()
        return True

    def _format(self):
        text = self.input_edit.toPlainText()
        try:
            formatted = format_xml_text(text)
        except ValueError as exc:
            self._show_error(str(exc))
            return False
        self.output_edit.setPlainText(formatted)
        # 失败路径已在 _show_error；成功时滚动到输出顶部
        self.output_edit.moveCursor(QTextCursor.MoveOperation.Start)
        zh = self.language == 'zh'
        fold_n = 0
        if hasattr(self.output_edit, 'fold_regions'):
            fold_n = len(self.output_edit.fold_regions())
        self.status_label.setText(
            f'格式化成功 · {self._stats(formatted)} · 可折叠 {fold_n} 处 · 可复制右侧结果'
            if zh else
            f'Formatted · {self._stats(formatted)} · {fold_n} folds · copy from output'
        )
        self.status_label.setProperty('xmlStatus', 'ok')
        self._repolish_status()
        # 格式化后若搜索框有词，刷新命中
        if hasattr(self, 'search_edit') and self.search_edit.text().strip():
            self._search(self.search_edit.text())
        return True

    def _expand_output(self):
        if hasattr(self.output_edit, 'expand_all_folds'):
            self.output_edit.expand_all_folds()

    def _collapse_output(self):
        if hasattr(self.output_edit, 'collapse_all_folds'):
            self.output_edit.collapse_all_folds()

    def _toggle_glance(self, checked: bool):
        if hasattr(self.output_edit, 'set_glance_visible'):
            self.output_edit.set_glance_visible(bool(checked))

    def _clear_search(self):
        self._search_hits = []
        self._search_index = -1
        if hasattr(self, 'search_status'):
            self.search_status.setText('0 / 0')
        for edit in (getattr(self, 'input_edit', None), getattr(self, 'output_edit', None)):
            if edit is None:
                continue
            clear_text_highlights(edit)

    def _search_targets(self) -> list:
        """输出优先，再输入（都有内容时两边都搜）。"""
        targets = []
        if self.output_edit.toPlainText().strip():
            targets.append(self.output_edit)
        if self.input_edit.toPlainText().strip():
            targets.append(self.input_edit)
        return targets

    def _search(self, query: str = ''):
        self._clear_search()
        needle = (query if query is not None else self.search_edit.text()).strip()
        if not needle:
            return
        hits: list[tuple[object, int, int]] = []
        for edit in self._search_targets():
            for start, end in collect_text_spans(edit, needle):
                hits.append((edit, start, end))
        self._search_hits = hits
        if not hits:
            self.search_status.setText('0 / 0')
            return
        self._search_index = 0
        self._apply_search_index()

    def _apply_search_index(self):
        if not self._search_hits:
            self.search_status.setText('0 / 0')
            return
        from ui.search_highlight import build_text_extra_selections, _set_edit_extra_selections

        clear_text_highlights(self.input_edit)
        clear_text_highlights(self.output_edit)

        by_edit: dict[int, dict] = {}
        for edit, start, end in self._search_hits:
            pack = by_edit.setdefault(id(edit), {'edit': edit, 'spans': []})
            pack['spans'].append((start, end))

        cur_edit, cur_start, cur_end = self._search_hits[self._search_index]
        for pack in by_edit.values():
            edit = pack['edit']
            spans = pack['spans']
            if edit is cur_edit:
                local = 0
                for i, (s, e) in enumerate(spans):
                    if s == cur_start and e == cur_end:
                        local = i
                        break
                apply_text_match_index(edit, spans, local)
            else:
                sels = build_text_extra_selections(edit, spans, current_index=-1)
                _set_edit_extra_selections(edit, sels)

        where = '输出' if cur_edit is self.output_edit else '输入'
        if self.language != 'zh':
            where = 'out' if cur_edit is self.output_edit else 'in'
        total = len(self._search_hits)
        self.search_status.setText(f'{self._search_index + 1} / {total} · {where}')

    def _next_match(self):
        if not self._search_hits:
            self._search(self.search_edit.text())
            return
        self._search_index = (self._search_index + 1) % len(self._search_hits)
        self._apply_search_index()

    def _prev_match(self):
        if not self._search_hits:
            self._search(self.search_edit.text())
            return
        self._search_index = (self._search_index - 1) % len(self._search_hits)
        self._apply_search_index()

    def _copy_output(self):
        text = self.output_edit.toPlainText()
        if not text.strip():
            text = self.input_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            zh = self.language == 'zh'
            self.status_label.setText('已复制到剪贴板' if zh else 'Copied to clipboard')
            self.status_label.setProperty('xmlStatus', 'ok')
            self._repolish_status()

    def _show_error(self, message: str):
        zh = self.language == 'zh'
        show_warning(self, 'XML 工具' if zh else 'XML tools', message)
        self.status_label.setText(message)
        self.status_label.setProperty('xmlStatus', 'error')
        self._repolish_status()
        # 尝试按「第 N 行」跳到输入区，便于改错
        line = self._parse_error_line(message)
        if line is not None:
            block = self.input_edit.document().findBlockByNumber(max(0, line - 1))
            if block.isValid():
                cursor = QTextCursor(block)
                self.input_edit.setTextCursor(cursor)
                self.input_edit.setFocus()

    @staticmethod
    def _parse_error_line(message: str) -> int | None:
        import re
        match = re.search(r'第\s*(\d+)\s*行', message) or re.search(r'line\s*(\d+)', message, re.I)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _refresh_status(self):
        zh = self.language == 'zh'
        inn = self.input_edit.toPlainText()
        out = self.output_edit.toPlainText()
        if not inn.strip() and not out.strip():
            self.status_label.setText(
                '等待粘贴 XML · 支持外层引号与转义'
                if zh else
                'Waiting for XML · outer quotes and escapes OK'
            )
            self.status_label.setProperty('xmlStatus', 'idle')
            self._repolish_status()
            return
        parts = []
        if inn.strip():
            parts.append(('输入' if zh else 'In') + ' ' + self._stats(inn))
        if out.strip():
            parts.append(('输出' if zh else 'Out') + ' ' + self._stats(out))
        self.status_label.setText(' · '.join(parts))
        self.status_label.setProperty('xmlStatus', 'idle')
        self._repolish_status()

    @staticmethod
    def _stats(text: str) -> str:
        if not text:
            return '0 行 / 0 字'
        lines = text.count('\n') + (0 if text.endswith('\n') else 1)
        chars = len(text)
        if chars >= 10000:
            size = f'{chars / 1000:.1f}k'
        else:
            size = str(chars)
        return f'{lines} 行 / {size} 字'

    def _repolish_status(self):
        style = self.status_label.style()
        if style is not None:
            style.unpolish(self.status_label)
            style.polish(self.status_label)
        self.status_label.update()
