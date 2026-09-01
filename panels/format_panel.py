# -*- coding: utf-8 -*-
"""格式工具：JSON / XML / SQL 离线整理（不联网、不执行 SQL）。"""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from tools.sql_tool import (
    deduplicate_sql_statements, split_statements, strip_comments,
    validate_oracle_sql_detailed,
)
from tools.text_dev_helpers import (
    TextHelperError, decode_base64, decode_unicode_escapes, decode_url,
    encode_base64, encode_unicode_escapes, encode_url, extract_java_stack,
    format_timestamp_bundle,
)
from ui.confirm_dialog import show_warning
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import size_enum_combo
from ui.json_viewer import JsonViewer
from ui.page_chrome import make_page_header
from ui.splitter_prefs import install_splitter_prefs
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
    """轻量缩进与关键字大写规整。"""
    from tools.sql_tool import format_sql
    return format_sql(sql)


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
        self.paste_btn.setToolTip('粘贴' if zh else 'Paste')
        self.open_btn.setText('打开文件' if zh else 'Open file')
        self.open_btn.setToolTip('打开文件' if zh else 'Open file')
        self.format_btn.setText('格式化缩进' if zh else 'Pretty indent')
        self.format_btn.setToolTip('格式化缩进' if zh else 'Pretty indent')
        self.compact_btn.setText('压缩单行' if zh else 'Minify')
        self.compact_btn.setToolTip('压缩单行' if zh else 'Minify')
        self.dedupe_btn.setText('去重语句' if zh else 'Dedupe')
        self.dedupe_btn.setToolTip('去重语句' if zh else 'Dedupe')
        self.validate_btn.setText('风险检查' if zh else 'Lint risks')
        self.validate_btn.setToolTip('风险检查' if zh else 'Lint risks')
        self.copy_btn.setText('复制' if zh else 'Copy')
        self.copy_btn.setToolTip('复制' if zh else 'Copy')
        self.export_btn.setText('导出' if zh else 'Export')
        self.export_btn.setToolTip('导出' if zh else 'Export')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.clear_btn.setToolTip('清空' if zh else 'Clear')
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
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('format_sql_open')
        path, _ = QFileDialog.getOpenFileName(
            self,
            '打开 SQL' if self.language == 'zh' else 'Open SQL',
            start,
            'SQL (*.sql *.txt);;All (*.*)',
        )
        if not path:
            return
        remember_dialog_path('format_sql_open', path)
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
        from tools.dialog_paths import get_dialog_save_path, remember_dialog_path
        start = get_dialog_save_path('format_sql_export', 'formatted.sql')
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出 SQL' if self.language == 'zh' else 'Export SQL',
            start,
            'SQL (*.sql);;Text (*.txt)',
        )
        if not path:
            return
        remember_dialog_path('format_sql_export', path)
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


class _TextDevHelpersTab(QWidget):
    """Base64 / URL / Unicode / 时间戳 / Java 堆栈。"""

    MODES = ('Base64', 'URL', 'Unicode', '时间戳', 'Java 堆栈')

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
        self.mode_label = QLabel()
        tools.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(self.MODES))
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        size_enum_combo(self.mode_combo)
        tools.addWidget(self.mode_combo)
        tools.addStretch(1)
        self.encode_btn = QPushButton()
        apply_button(self.encode_btn, 'secondary', compact=True, icon='lock', icon_size=16)
        self.encode_btn.clicked.connect(self._encode)
        tools.addWidget(self.encode_btn)
        self.decode_btn = QPushButton()
        apply_button(self.decode_btn, 'secondary', compact=True, icon='unlock', icon_size=16)
        self.decode_btn.clicked.connect(self._decode)
        tools.addWidget(self.decode_btn)
        self.convert_btn = QPushButton()
        apply_button(self.convert_btn, 'secondary', compact=True, icon='refresh', icon_size=16)
        self.convert_btn.clicked.connect(self._convert)
        self.convert_btn.hide()
        tools.addWidget(self.convert_btn)
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True, icon='copy', icon_size=16)
        self.copy_btn.clicked.connect(self._copy_out)
        tools.addWidget(self.copy_btn)
        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True, icon='delete', icon_size=16)
        self.clear_btn.clicked.connect(self._clear)
        tools.addWidget(self.clear_btn)
        root.addLayout(tools)

        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(6)
        self.input = QPlainTextEdit()
        self.input.setObjectName('text-helper-input')
        self.input.setFont(mono)
        self.input.setMinimumHeight(140)
        self.vsplit.addWidget(self.input)
        self.output = QPlainTextEdit()
        self.output.setObjectName('text-helper-output')
        self.output.setFont(mono)
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(140)
        self.vsplit.addWidget(self.output)
        install_splitter_prefs(
            self.vsplit,
            defaults=[240, 240],
            page_id='format-tools',
            tab_id='text-dev',
            min_sizes=[140, 140],
            accessible_name='文本辅助输入与输出分隔',
        )
        root.addWidget(self.vsplit, 1)
        self.status = QLabel()
        self.status.setObjectName('field-hint')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self._on_mode(0)

    def _mode_key(self) -> str:
        idx = self.mode_combo.currentIndex()
        return ('base64', 'url', 'unicode', 'timestamp', 'java')[max(0, min(4, idx))]

    def _on_mode(self, _index=0):
        key = self._mode_key()
        stack = key == 'java' or key == 'timestamp'
        self.encode_btn.setVisible(not stack)
        self.decode_btn.setVisible(not stack)
        self.convert_btn.setVisible(stack)
        zh = self.language == 'zh'
        if key == 'timestamp':
            self.convert_btn.setText('转换' if zh else 'Convert')
        elif key == 'java':
            self.convert_btn.setText('提取异常链' if zh else 'Extract stack')
        self.status.setText('')

    def _encode(self):
        text = self.input.toPlainText()
        key = self._mode_key()
        try:
            if key == 'base64':
                self.output.setPlainText(encode_base64(text))
            elif key == 'url':
                self.output.setPlainText(encode_url(text))
            elif key == 'unicode':
                self.output.setPlainText(encode_unicode_escapes(text))
            self.status.setText('完成' if self.language == 'zh' else 'Done')
        except TextHelperError as exc:
            self.output.clear()
            self.status.setText(str(exc))
            show_warning(self, '文本辅助', str(exc))

    def _decode(self):
        text = self.input.toPlainText()
        key = self._mode_key()
        try:
            if key == 'base64':
                self.output.setPlainText(decode_base64(text))
            elif key == 'url':
                self.output.setPlainText(decode_url(text))
            elif key == 'unicode':
                self.output.setPlainText(decode_unicode_escapes(text))
            self.status.setText('完成' if self.language == 'zh' else 'Done')
        except TextHelperError as exc:
            self.output.clear()
            self.status.setText(str(exc))
            show_warning(self, '文本辅助', str(exc))

    def _convert(self):
        text = self.input.toPlainText()
        key = self._mode_key()
        try:
            if key == 'timestamp':
                self.output.setPlainText(format_timestamp_bundle(text))
            elif key == 'java':
                result = extract_java_stack(text)
                self.output.setPlainText(result.get('compact_text') or result.get('summary') or '')
            self.status.setText('完成' if self.language == 'zh' else 'Done')
        except TextHelperError as exc:
            self.output.clear()
            self.status.setText(str(exc))
            show_warning(self, '文本辅助', str(exc))

    def _copy_out(self):
        text = self.output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _clear(self):
        self.input.clear()
        self.output.clear()
        self.status.setText('')

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.mode_label.setText('模式' if zh else 'Mode')
        labels = (
            ['Base64', 'URL', 'Unicode', '时间戳', 'Java 堆栈'] if zh else
            ['Base64', 'URL', 'Unicode', 'Timestamp', 'Java stack']
        )
        for i, name in enumerate(labels):
            if i < self.mode_combo.count():
                self.mode_combo.setItemText(i, name)
        self.encode_btn.setText('编码' if zh else 'Encode')
        self.decode_btn.setText('解码' if zh else 'Decode')
        self.copy_btn.setText('复制结果' if zh else 'Copy result')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.input.setPlaceholderText(
            '粘贴待转换文本…' if zh else 'Paste text…'
        )
        self._on_mode(self.mode_combo.currentIndex())


class FormatToolsPanel(QWidget):
    """JSON / XML / SQL / 文本与开发辅助 四 Tab 格式工具。"""

    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._layout_mode = 'standard'
        self._setup_ui()
        self.set_language(language)

    def apply_layout_mode(self, mode, low_height=False):
        self._layout_mode = mode
        from ui.responsive import set_subtitle_visible, apply_splitter_orientation, editor_min_height
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        min_h = editor_min_height()
        # JSON 内部若有 splitter
        for attr in ('json_viewer', 'xml_workspace', 'sql_tab', 'text_tab'):
            w = getattr(self, attr, None)
            if w is None:
                continue
            if hasattr(w, 'apply_layout_mode'):
                try:
                    w.apply_layout_mode(mode, low_height)
                except Exception:
                    pass
            if hasattr(w, 'editor') and w.editor is not None:
                w.editor.setMinimumHeight(min_h)
            if hasattr(w, 'input') and w.input is not None:
                w.input.setMinimumHeight(min_h // 2 if mode in ('compact', 'narrow') else min_h)
            if hasattr(w, 'output') and w.output is not None:
                w.output.setMinimumHeight(min_h // 2 if mode in ('compact', 'narrow') else min_h)
        # XML 工作区 splitter
        xml = getattr(self, 'xml_workspace', None)
        if xml is not None and hasattr(xml, 'splitter'):
            apply_splitter_orientation(xml.splitter, mode, min_editor=min_h)
        jv = getattr(self, 'json_viewer', None)
        if jv is not None and hasattr(jv, 'text_edit') and jv.text_edit is not None:
            jv.text_edit.setMinimumHeight(min_h)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        header, self.page_title, self.page_subtitle = make_page_header(
            '格式工具',
            '离线整理，不落盘',
            'json',
        )
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName('module-tabs')
        self.tabs.setDocumentMode(False)

        # JSON：复用 JsonViewer，不改其 API
        json_page = QWidget()
        json_layout = QVBoxLayout(json_page)
        json_layout.setContentsMargins(0, 4, 0, 0)
        self.json_viewer = JsonViewer(self.language)
        json_layout.addWidget(self.json_viewer)
        self.tabs.addTab(json_page, 'JSON')

        # XML：复用 XmlWorkspace
        self.xml_workspace = XmlWorkspace(self.language)
        if hasattr(self.xml_workspace, 'splitter') and self.xml_workspace.splitter is not None:
            install_splitter_prefs(
                self.xml_workspace.splitter,
                defaults=[480, 520],
                page_id='format-tools',
                tab_id='xml',
                min_sizes=[220, 220],
                accessible_name='格式工具 XML 分隔',
            )
        # 精简 XML 页顶区说明（工具内已有 zone）
        self.tabs.addTab(self.xml_workspace, 'XML')

        self.sql_tab = _SqlFormatTab(self.language)
        self.tabs.addTab(self.sql_tab, 'SQL')

        self.text_tab = _TextDevHelpersTab(self.language)
        self.tabs.addTab(self.text_tab, '文本与开发辅助')

        root.addWidget(self.tabs, 1)
        self.refresh_theme()

    def refresh_theme(self):
        """刷新常驻 Tab 图标，避免主题切换后保留旧主题色。"""
        try:
            from ui.icons import qicon
            for index, role in enumerate(('json', 'xml', 'database', 'terminal')):
                self.tabs.setTabIcon(index, qicon(role))
        except Exception:
            pass

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('格式工具' if zh else 'Format tools')
        self.page_subtitle.setText(
            '离线整理，不落盘' if zh else 'Offline. Nothing is saved.'
        )
        self.tabs.setTabText(0, 'JSON')
        self.tabs.setTabText(1, 'XML')
        self.tabs.setTabText(2, 'SQL')
        self.tabs.setTabText(3, '文本与开发辅助' if zh else 'Text & Dev helpers')
        self.json_viewer.set_language(language)
        self.xml_workspace.set_language(language)
        self.sql_tab.set_language(language)
        self.text_tab.set_language(language)

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

    def open_text_helper(self, text: str = ''):
        self.tabs.setCurrentIndex(3)
        if text:
            self.text_tab.input.setPlainText(text)
