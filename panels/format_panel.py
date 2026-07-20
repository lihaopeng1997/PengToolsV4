# -*- coding: utf-8 -*-
"""格式工具：JSON / XML / SQL 离线整理（不联网、不执行 SQL）。"""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from tools.sql_tool import (
    deduplicate_sql_statements, split_statements, strip_comments,
    validate_oracle_sql_detailed,
)
from ui.confirm_dialog import show_warning
from ui.design_system import apply_button, apply_surface
from ui.json_viewer import JsonViewer
from ui.page_chrome import make_page_header
from ui.xml_workspace import XmlWorkspace


def _compact_sql(sql: str) -> str:
    """压缩为单行：去注释后折叠空白，语句间保留分号。"""
    clean = strip_comments(sql or '')
    stmts = split_statements(clean)
    parts = []
    for stmt in stmts:
        one = re.sub(r'\s+', ' ', stmt).strip()
        if one:
            parts.append(one if one.endswith(';') else one + ';')
    return ' '.join(parts)


def _pretty_sql(sql: str) -> str:
    """轻量缩进：按关键字换行，不追求完整方言。"""
    clean = strip_comments(sql or '')
    stmts = split_statements(clean)
    keywords = (
        'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'JOIN', 'LEFT', 'RIGHT', 'INNER',
        'OUTER', 'ON', 'GROUP BY', 'ORDER BY', 'HAVING', 'UNION', 'INSERT', 'INTO',
        'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'TABLE',
        'INDEX', 'COMMENT', 'BEGIN', 'END', 'COMMIT', 'ROLLBACK',
    )
    pattern = re.compile(
        r'\b(' + '|'.join(re.escape(k) for k in sorted(keywords, key=len, reverse=True)) + r')\b',
        re.IGNORECASE,
    )
    blocks = []
    for stmt in stmts:
        text = re.sub(r'\s+', ' ', stmt).strip()
        if not text:
            continue
        text = pattern.sub(lambda m: '\n' + m.group(0).upper(), text)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        indent = 0
        out = []
        for line in lines:
            upper = line.upper()
            if upper.startswith(('AND ', 'OR ', 'SET ', 'VALUES', 'ON ')):
                out.append('  ' * max(1, indent) + line)
            elif upper.startswith(('FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'GROUP', 'ORDER', 'HAVING', 'UNION')):
                out.append('  ' * max(0, indent) + line)
            else:
                out.append('  ' * indent + line)
            if upper.startswith(('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER')):
                indent = 1
        body = '\n'.join(out).strip()
        if body and not body.endswith(';'):
            body += ';'
        blocks.append(body)
    return '\n\n'.join(blocks)


class _SqlFormatTab(QWidget):
    """SQL 离线文本整理：不执行、不落盘敏感路径。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.paste_btn = QPushButton()
        apply_button(self.paste_btn, 'secondary', compact=True, icon='edit', icon_size=16)
        self.paste_btn.clicked.connect(self._paste)
        tools.addWidget(self.paste_btn)
        self.open_btn = QPushButton()
        apply_button(self.open_btn, 'secondary', compact=True, icon='folder-open', icon_size=16)
        self.open_btn.clicked.connect(self._open_file)
        tools.addWidget(self.open_btn)
        self.format_btn = QPushButton()
        apply_button(self.format_btn, 'primary', compact=True, icon='database', icon_size=16)
        self.format_btn.clicked.connect(self._format)
        tools.addWidget(self.format_btn)
        self.compact_btn = QPushButton()
        apply_button(self.compact_btn, 'secondary', compact=True, icon='collapse', icon_size=16)
        self.compact_btn.clicked.connect(self._compact)
        tools.addWidget(self.compact_btn)
        self.dedupe_btn = QPushButton()
        apply_button(self.dedupe_btn, 'secondary', compact=True, icon='filter', icon_size=16)
        self.dedupe_btn.clicked.connect(self._dedupe)
        tools.addWidget(self.dedupe_btn)
        self.validate_btn = QPushButton()
        apply_button(self.validate_btn, 'ghost', compact=True, icon='info', icon_size=16)
        self.validate_btn.clicked.connect(self._validate)
        tools.addWidget(self.validate_btn)
        tools.addStretch(1)
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_btn.clicked.connect(self._copy)
        tools.addWidget(self.copy_btn)
        self.export_btn = QPushButton()
        apply_button(self.export_btn, 'ghost', compact=True, icon='export', icon_size=16)
        self.export_btn.clicked.connect(self._export)
        tools.addWidget(self.export_btn)
        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_btn.clicked.connect(self._clear)
        tools.addWidget(self.clear_btn)
        root.addLayout(tools)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName('sql-format-editor')
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(mono)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.editor, 1)

        self.status = QLabel()
        self.status.setObjectName('field-hint')
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.paste_btn.setText('粘贴' if zh else 'Paste')
        self.open_btn.setText('打开文件' if zh else 'Open file')
        self.format_btn.setText('格式化缩进' if zh else 'Pretty indent')
        self.compact_btn.setText('压缩单行' if zh else 'Minify')
        self.dedupe_btn.setText('去重语句' if zh else 'Dedupe')
        self.validate_btn.setText('风险检查' if zh else 'Lint risks')
        self.copy_btn.setText('复制' if zh else 'Copy')
        self.export_btn.setText('导出' if zh else 'Export')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.editor.setPlaceholderText(
            '粘贴 SQL（仅离线文本整理，不连接数据库）…' if zh else
            'Paste SQL (offline text only — never executes)…'
        )
        self._refresh_status()

    def _text(self) -> str:
        return self.editor.toPlainText()

    def _paste(self):
        text = QApplication.clipboard().text()
        if text:
            self.editor.setPlainText(text)
            self._refresh_status()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            '打开 SQL' if self.language == 'zh' else 'Open SQL',
            '',
            'SQL (*.sql *.txt);;All (*.*)',
        )
        if not path:
            return
        try:
            from tools.sql_tool import read_file_auto_encoding
            self.editor.setPlainText(read_file_auto_encoding(path))
            self._refresh_status()
        except Exception as exc:
            show_warning(self, 'SQL', str(exc))

    def _format(self):
        self.editor.setPlainText(_pretty_sql(self._text()))
        self._refresh_status('formatted')

    def _compact(self):
        self.editor.setPlainText(_compact_sql(self._text()))
        self._refresh_status('compact')

    def _dedupe(self):
        text, duplicates = deduplicate_sql_statements(self._text())
        removed = len(duplicates or [])
        self.editor.setPlainText(text)
        zh = self.language == 'zh'
        self.status.setText(
            (f'已去重，移除 {removed} 条重复。' if zh else f'Deduped, removed {removed}.')
            if removed else
            ('未发现重复语句。' if zh else 'No duplicates found.')
        )

    def _validate(self):
        issues = validate_oracle_sql_detailed(self._text())
        zh = self.language == 'zh'
        if not issues:
            self.status.setText(
                '未发现明显结构风险（不能替代 Oracle 实际编译）。' if zh else
                'No structural risks found (not a substitute for Oracle compile).'
            )
            return
        lines = []
        for issue in issues[:12]:
            msg = issue['message_zh'] if zh else issue['message_en']
            lines.append(f"#{issue['statement']} [{issue['severity']}] {msg}")
        more = len(issues) - 12
        if more > 0:
            lines.append(('… 另有 %d 条' % more) if zh else f'… +{more} more')
        lines.append(
            '以上仅为离线风险提示，不能替代 Oracle 实际编译。' if zh else
            'Offline lint only — not a substitute for Oracle compile.'
        )
        self.status.setText('\n'.join(lines))

    def _copy(self):
        text = self._text()
        if text:
            QApplication.clipboard().setText(text)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出 SQL' if self.language == 'zh' else 'Export SQL',
            'formatted.sql',
            'SQL (*.sql);;Text (*.txt)',
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(self._text())
            self.status.setText(
                f'已导出：{path}' if self.language == 'zh' else f'Exported: {path}'
            )
        except OSError as exc:
            show_warning(self, 'SQL', str(exc))

    def _clear(self):
        self.editor.clear()
        self._refresh_status()

    def _refresh_status(self, mode=''):
        text = self._text()
        zh = self.language == 'zh'
        if not text.strip():
            self.status.setText('就绪 · 不执行 SQL、不联网' if zh else 'Ready · no execute, offline only')
            return
        stmts = split_statements(strip_comments(text))
        empty = sum(1 for s in stmts if not s.strip())
        self.status.setText(
            f'{len(stmts)} 条语句 · 空语句 {empty} · 仅离线整理'
            if zh else
            f'{len(stmts)} statements · empty {empty} · offline only'
        )


class FormatToolsPanel(QWidget):
    """JSON / XML / SQL 三 Tab 格式工具。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        header, self.page_title, self.page_subtitle = make_page_header(
            '格式工具',
            'JSON · XML · SQL 离线整理 · 不联网、不落盘敏感内容',
            'json',
        )
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName('module-tabs')
        self.tabs.setDocumentMode(True)

        # JSON：复用 JsonViewer，不改其 API
        json_page = QWidget()
        json_layout = QVBoxLayout(json_page)
        json_layout.setContentsMargins(0, 8, 0, 0)
        self.json_viewer = JsonViewer(self.language)
        json_layout.addWidget(self.json_viewer)
        self.tabs.addTab(json_page, 'JSON')

        # XML：复用 XmlWorkspace
        self.xml_workspace = XmlWorkspace(self.language)
        # 精简 XML 页顶区说明（工具内已有 zone）
        self.tabs.addTab(self.xml_workspace, 'XML')

        self.sql_tab = _SqlFormatTab(self.language)
        self.tabs.addTab(self.sql_tab, 'SQL')

        root.addWidget(self.tabs, 1)
        try:
            from ui.icons import qicon
            self.tabs.setTabIcon(0, qicon('json'))
            self.tabs.setTabIcon(1, qicon('xml'))
            self.tabs.setTabIcon(2, qicon('database'))
        except Exception:
            pass

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('格式工具' if zh else 'Format tools')
        self.page_subtitle.setText(
            'JSON · XML · SQL 离线整理 · 不联网、不落盘敏感内容' if zh else
            'JSON · XML · SQL offline formatting · no network'
        )
        self.tabs.setTabText(0, 'JSON')
        self.tabs.setTabText(1, 'XML')
        self.tabs.setTabText(2, 'SQL')
        self.json_viewer.set_language(language)
        self.xml_workspace.set_language(language)
        self.sql_tab.set_language(language)

    def open_json(self, text: str = ''):
        self.tabs.setCurrentIndex(0)
        if text:
            self.json_viewer.set_text(text, auto_format=True)

    def open_xml(self, text: str = ''):
        self.tabs.setCurrentIndex(1)
        if text:
            self.xml_workspace.set_input_text(text, auto_format=True)

    def open_sql(self, text: str = ''):
        self.tabs.setCurrentIndex(2)
        if text:
            self.sql_tab.editor.setPlainText(text)
            self.sql_tab._refresh_status()
