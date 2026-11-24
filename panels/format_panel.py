# -*- coding: utf-8 -*-
"""格式工具：JSON / XML / SQL 离线整理（不联网、不执行 SQL）。"""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import QBuffer, QIODevice, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from tools.media_base64 import (
    MAX_MEDIA_FILE_BYTES, MediaBase64Result, decode_base64_to_media,
    encode_bytes_to_base64, encode_file_to_base64, format_byte_size,
    format_media_base64_output,
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
from ui.confirm_dialog import show_error, show_info, show_warning
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

        from ui.wrap_layout import WrapLayout
        tools = WrapLayout()
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
        from ui.design_system import mono_font
        mono = mono_font()
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

        from ui.design_system import mono_font
        mono = mono_font()
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(16)
        self.vsplit.setProperty('prismGutter', True)
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


class _MediaEncodeWorker(QThread):
    """后台读取并编码图片/PDF，避免大 PDF 阻塞界面主线程。"""

    finished_ok = pyqtSignal(object)  # MediaBase64Result
    failed = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            self.finished_ok.emit(encode_file_to_base64(self._path))
        except Exception as exc:
            self.failed.emit(str(exc))


class _MediaDropCard(QFrame):
    """支持拖拽图片 / PDF 或点击选择文件的可视化预览卡。"""

    file_dropped = pyqtSignal(str)
    clicked_empty = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('media-drop-card')
        self.setAcceptDrops(True)
        self._has_media = False
        self._apply_border(dragging=False)

    def _apply_border(self, *, dragging: bool):
        if dragging:
            self.setStyleSheet(
                'QFrame#media-drop-card { background: #F2EEFB; border: 2px dashed #6C58D9; border-radius: 12px; }'
            )
        else:
            self.setStyleSheet(
                'QFrame#media-drop-card { background: #F8F6FE; border: 1.5px dashed #CFC6EE; border-radius: 12px; }'
            )

    def set_has_media(self, value: bool):
        self._has_media = bool(value)
        if self._has_media:
            self.setStyleSheet(
                'QFrame#media-drop-card { background: #FDFDFF; border: 1px solid #DFD9F0; border-radius: 12px; }'
            )
        else:
            self._apply_border(dragging=False)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and os.path.isfile(url.toLocalFile()):
                    self._apply_border(dragging=True)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.set_has_media(self._has_media)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.set_has_media(self._has_media)
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if os.path.isfile(path):
                        event.acceptProposedAction()
                        self.file_dropped.emit(path)
                        return
        event.ignore()

    def mouseReleaseEvent(self, event):
        if not self._has_media and event.button() == Qt.MouseButton.LeftButton:
            self.clicked_empty.emit()
        super().mouseReleaseEvent(event)


class _MediaBase64Tab(QWidget):
    """图片 / PDF ↔ Base64 双向离线转换页签。"""

    _PREVIEW_CHAR_CAP = 120_000

    def __init__(self, language='zh', parent=None):
        super().__init__(parent)
        self.language = language
        self._current_result: MediaBase64Result | None = None
        self._decoded_bytes: bytes = b''
        self._decoded_ext: str = '.bin'
        self._decoded_mime: str = ''
        self._source_path: str = ''
        self._preview_pixmap: QPixmap | None = None
        self._full_output_text: str = ''
        self._output_truncated: bool = False
        self._worker: _MediaEncodeWorker | None = None
        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.timeout.connect(self._restore_copy_label)
        self._setup_ui()
        self.set_language(language)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        # 顶栏：文件选择 / 剪贴板粘贴 / 输出格式 / 76 列换行 / 反向还原 / 清空
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.pick_btn = QPushButton()
        apply_button(self.pick_btn, 'primary', compact=True, icon='folder-open', icon_size=15)
        self.pick_btn.clicked.connect(self._pick_file)
        bar.addWidget(self.pick_btn)

        self.paste_clip_btn = QPushButton()
        apply_button(self.paste_clip_btn, 'secondary', compact=True)
        self.paste_clip_btn.clicked.connect(self._paste_from_clipboard)
        bar.addWidget(self.paste_clip_btn)

        self.mode_label = QLabel()
        self.mode_label.setObjectName('field-caption')
        bar.addWidget(self.mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem('纯 Base64（无前缀）', 'raw')
        self.mode_combo.addItem('Data URI（含 data:...;base64,）', 'data_uri')
        self.mode_combo.addItem('HTML 标签（<img> / <embed>）', 'html')
        self.mode_combo.addItem('CSS background-image', 'css')
        size_enum_combo(self.mode_combo)
        self.mode_combo.currentIndexChanged.connect(self._rerender_output)
        bar.addWidget(self.mode_combo)

        self.wrap_cb = QCheckBox()
        self.wrap_cb.toggled.connect(self._rerender_output)
        bar.addWidget(self.wrap_cb)

        bar.addStretch(1)

        self.decode_btn = QPushButton()
        apply_button(self.decode_btn, 'secondary', compact=True)
        self.decode_btn.clicked.connect(self._decode_from_editor)
        bar.addWidget(self.decode_btn)

        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True)
        self.clear_btn.clicked.connect(self._clear_all)
        bar.addWidget(self.clear_btn)
        root.addLayout(bar)

        # 主体左右分栏：左侧预览/拖放区 | 右侧 Base64 文本与操作区
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.setHandleWidth(14)
        self.hsplit.setProperty('prismGutter', True)
        self.hsplit.setChildrenCollapsible(False)

        left_pane = QWidget()
        left_l = QVBoxLayout(left_pane)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        left_head_host = QWidget(left_pane)
        left_head_host.setFixedHeight(32)
        left_head = QHBoxLayout(left_head_host)
        left_head.setContentsMargins(0, 0, 0, 0)
        left_head.setSpacing(8)
        self.preview_title = QLabel()
        self.preview_title.setObjectName('zone-title')
        left_head.addWidget(self.preview_title)
        self.meta_pill = QLabel()
        self.meta_pill.setObjectName('field-hint')
        self.meta_pill.setWordWrap(False)
        left_head.addWidget(self.meta_pill, 1)
        self.save_media_btn = QPushButton()
        apply_button(self.save_media_btn, 'secondary', compact=True, icon='export', icon_size=15)
        self.save_media_btn.setEnabled(False)
        self.save_media_btn.clicked.connect(self._save_restored_file)
        left_head.addWidget(self.save_media_btn)
        left_l.addWidget(left_head_host)

        self.drop_card = _MediaDropCard(left_pane)
        self.drop_card.setMinimumHeight(260)
        self.drop_card.file_dropped.connect(self.load_file)
        self.drop_card.clicked_empty.connect(self._pick_file)
        drop_l = QVBoxLayout(self.drop_card)
        drop_l.setContentsMargins(16, 16, 16, 16)
        drop_l.setSpacing(8)
        drop_l.addStretch(1)

        self.preview_badge = QLabel()
        self.preview_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_badge.setFixedSize(64, 42)
        self.preview_badge.setStyleSheet(
            'background-color: #EEE9FF; color: #503DBE; border: 1px solid #D5CCF3;'
            ' border-radius: 10px; font-size: 15px; font-weight: 700;'
        )
        self.preview_badge.hide()
        drop_l.addWidget(self.preview_badge, 0, Qt.AlignmentFlag.AlignHCenter)

        self.preview_image_lbl = QLabel()
        self.preview_image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image_lbl.setMinimumHeight(140)
        self.preview_image_lbl.hide()
        drop_l.addWidget(self.preview_image_lbl, 1)

        self.drop_title = QLabel()
        self.drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_title.setWordWrap(True)
        self.drop_title.setStyleSheet('color: #262438; font-size: 14px; font-weight: 700; background: transparent;')
        drop_l.addWidget(self.drop_title)

        self.drop_hint = QLabel()
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setWordWrap(True)
        self.drop_hint.setStyleSheet('color: #615D73; font-size: 12px; background: transparent;')
        drop_l.addWidget(self.drop_hint)
        drop_l.addStretch(1)
        left_l.addWidget(self.drop_card, 1)
        self.hsplit.addWidget(left_pane)

        right_pane = QWidget()
        right_l = QVBoxLayout(right_pane)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        out_head_host = QWidget(right_pane)
        out_head_host.setFixedHeight(32)
        out_head = QHBoxLayout(out_head_host)
        out_head.setContentsMargins(0, 0, 0, 0)
        out_head.setSpacing(8)
        self.out_title = QLabel()
        self.out_title.setObjectName('zone-title')
        out_head.addWidget(self.out_title)
        self.stats_lbl = QLabel()
        self.stats_lbl.setObjectName('field-hint')
        out_head.addWidget(self.stats_lbl, 1)
        self.export_txt_btn = QPushButton()
        apply_button(self.export_txt_btn, 'secondary', compact=True)
        self.export_txt_btn.setEnabled(False)
        self.export_txt_btn.clicked.connect(self._export_base64_txt)
        out_head.addWidget(self.export_txt_btn)
        self.copy_b64_btn = QPushButton()
        apply_button(self.copy_b64_btn, 'primary', compact=True, icon='copy', icon_size=15)
        self.copy_b64_btn.setEnabled(False)
        self.copy_b64_btn.clicked.connect(self._copy_base64)
        out_head.addWidget(self.copy_b64_btn)
        right_l.addWidget(out_head_host)

        self.trunc_banner = QLabel()
        self.trunc_banner.setObjectName('ops-safety-note')
        self.trunc_banner.setWordWrap(True)
        self.trunc_banner.hide()
        right_l.addWidget(self.trunc_banner)

        self.b64_edit = QPlainTextEdit()
        self.b64_edit.setObjectName('format-text-output')
        self.b64_edit.setFont(QFont('Consolas', 10))
        self.b64_edit.textChanged.connect(self._on_editor_text_changed)
        right_l.addWidget(self.b64_edit, 1)
        self.hsplit.addWidget(right_pane)

        self.hsplit.setStretchFactor(0, 4)
        self.hsplit.setStretchFactor(1, 6)
        install_splitter_prefs(
            self.hsplit,
            defaults=[380, 620],
            defaults_for_extent=lambda extent: [
                max(320, min(460, int(extent * 0.38))),
                max(420, extent - max(320, min(460, int(extent * 0.38)))),
            ],
            page_id='format-tools',
            tab_id='media-base64',
            min_sizes=[280, 380],
            accessible_name='图片PDF与Base64分隔',
        )
        root.addWidget(self.hsplit, 1)

        self.status_lbl = QLabel()
        self.status_lbl.setObjectName('field-hint')
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.pick_btn.setText('选择图片 / PDF' if zh else 'Select Image / PDF')
        self.paste_clip_btn.setText('粘贴剪贴板图片' if zh else 'Paste clipboard image')
        self.mode_label.setText('输出格式' if zh else 'Format')
        self.mode_combo.setItemText(0, '纯 Base64（无前缀）' if zh else 'Raw Base64')
        self.mode_combo.setItemText(1, 'Data URI（data:...;base64,）' if zh else 'Data URI')
        self.mode_combo.setItemText(2, 'HTML 标签（<img> / <embed>）' if zh else 'HTML tag')
        self.mode_combo.setItemText(3, 'CSS background-image' if zh else 'CSS background-image')
        self.wrap_cb.setText('76 列换行' if zh else 'Wrap 76 cols')
        self.decode_btn.setText('Base64 还原预览' if zh else 'Decode Base64')
        self.clear_btn.setText('清空' if zh else 'Clear')
        if hasattr(self, 'preview_title'):
            self.preview_title.setText('文件预览' if zh else 'File Preview')
        self.out_title.setText('Base64 文本' if zh else 'Base64 Text')
        self.export_txt_btn.setText('导出 .txt' if zh else 'Export .txt')
        self._restore_copy_label()
        self.save_media_btn.setText('保存还原文件…' if zh else 'Save file…')
        self.b64_edit.setPlaceholderText(
            '选择或拖入图片 / PDF 后自动生成 Base64；\n也可以直接把已有 Base64（含或不含 data:...;base64, 前缀）粘贴到这里，点上方「Base64 还原预览」查看图片或另存为文件。'
            if zh else
            'Select or drop an image/PDF to encode, or paste Base64 here and click Decode Base64.'
        )
        if not self._current_result and not self._decoded_bytes:
            self._show_empty_drop_state()

    def _restore_copy_label(self):
        self.copy_b64_btn.setText('复制 Base64' if self.language == 'zh' else 'Copy Base64')

    def _show_empty_drop_state(self):
        zh = self.language == 'zh'
        self.drop_card.set_has_media(False)
        self.preview_badge.hide()
        self.preview_image_lbl.hide()
        self.preview_image_lbl.clear()
        self.drop_title.setText('拖拽图片或 PDF 到此处，或点击选择文件' if zh else 'Drop an image or PDF here, or click to browse')
        self.drop_title.show()
        self.drop_hint.setText(
            f'支持 PNG / JPG / WebP / GIF / BMP / SVG / ICO / PDF（单文件 ≤ {format_byte_size(MAX_MEDIA_FILE_BYTES)}，纯离线不上传）'
            if zh else
            f'Supports PNG/JPG/WebP/GIF/BMP/SVG/ICO/PDF up to {format_byte_size(MAX_MEDIA_FILE_BYTES)} offline.'
        )
        self.drop_hint.show()
        self.meta_pill.setText('')

    def _pick_file(self):
        zh = self.language == 'zh'
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        start = get_dialog_start_dir('format_media_base64', os.path.dirname(self._source_path) if self._source_path else '')
        flt = (
            '图片与 PDF (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.svg *.ico *.tif *.tiff *.pdf);;'
            '图片文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.svg *.ico *.tif *.tiff);;'
            'PDF 文档 (*.pdf);;所有文件 (*)'
            if zh else
            'Images & PDF (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.svg *.ico *.tif *.tiff *.pdf);;All Files (*)'
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            '选择要转 Base64 的图片或 PDF' if zh else 'Select image or PDF',
            start,
            flt,
        )
        if not path:
            return
        remember_dialog_path('format_media_base64', path)
        self.load_file(path)

    def load_file(self, path: str):
        zh = self.language == 'zh'
        if not path or not os.path.isfile(path):
            show_warning(self, 'PengTools', '未找到该文件。' if zh else 'File not found.')
            return
        self._source_path = path
        try:
            fsize = os.path.getsize(path)
        except OSError as exc:
            show_error(self, 'PengTools', str(exc))
            return
        # 小文件（<= 1 MB）即时编码零延迟；大文件走后台线程防界面卡顿
        if fsize <= 1024 * 1024:
            try:
                res = encode_file_to_base64(path)
                self._apply_encode_result(res)
            except Exception as exc:
                show_warning(self, 'PengTools', str(exc))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.pick_btn.setEnabled(False)
        self.status_lbl.setText(f'正在编码 {os.path.basename(path)}（{format_byte_size(fsize)}）…' if zh else f'Encoding {os.path.basename(path)}…')
        worker = _MediaEncodeWorker(path, self)
        worker.finished_ok.connect(self._on_worker_done)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_worker_done(self, result: MediaBase64Result):
        self._worker = None
        self.pick_btn.setEnabled(True)
        self._apply_encode_result(result)

    def _on_worker_failed(self, err: str):
        self._worker = None
        self.pick_btn.setEnabled(True)
        self.status_lbl.setText('')
        show_warning(self, 'PengTools', err)

    def _paste_from_clipboard(self):
        zh = self.language == 'zh'
        cb = QApplication.clipboard()
        if cb is None:
            return
        mime = cb.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and os.path.isfile(url.toLocalFile()):
                    self.load_file(url.toLocalFile())
                    return
        image: QImage = cb.image()
        if image.isNull():
            show_warning(
                self,
                'PengTools',
                '剪贴板里没有图片或本地文件；可先截图或复制图片/PDF 文件后再点此按钮。'
                if zh else
                'No image or local file found in clipboard.',
            )
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buf, 'PNG')
        buf.close()
        raw = bytes(buf.data())
        if not ok or not raw:
            show_warning(self, 'PengTools', '无法读取剪贴板图片。' if zh else 'Failed to encode clipboard image.')
            return
        self._source_path = ''
        try:
            res = encode_bytes_to_base64(raw, filename='clipboard.png', mime='image/png')
            self._apply_encode_result(res)
        except Exception as exc:
            show_warning(self, 'PengTools', str(exc))

    def _apply_encode_result(self, res: MediaBase64Result):
        self._current_result = res
        self._decoded_bytes = res.raw_bytes
        self._decoded_ext = res.ext
        self._decoded_mime = res.mime
        self._render_media_preview(res.raw_bytes, res.filename, res.mime, res.kind, res.pdf_pages)
        self._rerender_output()
        self.save_media_btn.setEnabled(True)
        zh = self.language == 'zh'
        self.status_lbl.setText(
            f'已转换：{res.filename}（{res.mime} · 原始 {format_byte_size(res.raw_size)}）'
            if zh else
            f'Encoded: {res.filename} ({res.mime} · {format_byte_size(res.raw_size)})'
        )

    def _render_media_preview(self, raw: bytes, name: str, mime: str, kind: str, pdf_pages: int = 0):
        zh = self.language == 'zh'
        self.drop_card.set_has_media(True)
        self._preview_pixmap = None
        if kind == 'image':
            pix = QPixmap()
            if pix.loadFromData(raw):
                self._preview_pixmap = pix
                self.preview_badge.hide()
                self.drop_title.hide()
                self.drop_hint.hide()
                self.preview_image_lbl.show()
                self._rescale_preview_pixmap()
                self.meta_pill.setText(
                    f'{name}  ·  {pix.width()} × {pix.height()} px  ·  {mime}  ·  {format_byte_size(len(raw))}'
                )
                return
        # PDF 或无法直接光栅化的格式：展示清晰文档卡片
        self.preview_image_lbl.hide()
        self.preview_badge.setText('PDF' if kind == 'pdf' else (mime.split('/')[-1].upper()[:5] or 'FILE'))
        self.preview_badge.show()
        self.drop_title.setText(name or ('PDF 文档' if kind == 'pdf' else '已解码文件'))
        self.drop_title.show()
        page_note = (f'约 {pdf_pages} 页 · ' if pdf_pages > 0 else '') if zh else (f'~{pdf_pages} page(s) · ' if pdf_pages > 0 else '')
        self.drop_hint.setText(f'{page_note}{mime} · {format_byte_size(len(raw))}')
        self.drop_hint.show()
        self.meta_pill.setText(f'{name}  ·  {page_note}{mime}  ·  {format_byte_size(len(raw))}')

    def _rescale_preview_pixmap(self):
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            return
        avail = self.drop_card.size()
        max_w = max(180, avail.width() - 36)
        max_h = max(160, avail.height() - 40)
        scaled = self._preview_pixmap.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_image_lbl.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_preview_pixmap()

    def _rerender_output(self):
        if self._current_result is None:
            return
        mode = str(self.mode_combo.currentData() or 'raw')
        wrap = 76 if self.wrap_cb.isChecked() else 0
        full_text = format_media_base64_output(self._current_result, mode=mode, line_wrap=wrap)
        self._set_output_text(full_text)

    def _set_output_text(self, full_text: str):
        zh = self.language == 'zh'
        self._full_output_text = full_text
        total_chars = len(full_text)
        self._output_truncated = total_chars > self._PREVIEW_CHAR_CAP
        self.b64_edit.blockSignals(True)
        try:
            if self._output_truncated:
                self.b64_edit.setPlainText(full_text[:self._PREVIEW_CHAR_CAP] + '\n…')
                self.trunc_banner.setText(
                    f'内容较大（共 {total_chars:,} 字符），编辑框仅预览前 {self._PREVIEW_CHAR_CAP:,} 字符以保持流畅；'
                    f'点击「复制 Base64」或「导出 .txt」均会输出完整内容。'
                    if zh else
                    f'Large output ({total_chars:,} chars): showing first {self._PREVIEW_CHAR_CAP:,} chars; Copy/Export uses full content.'
                )
                self.trunc_banner.show()
            else:
                self.b64_edit.setPlainText(full_text)
                self.trunc_banner.hide()
        finally:
            self.b64_edit.blockSignals(False)
        has_text = bool(full_text)
        self.copy_b64_btn.setEnabled(has_text)
        self.export_txt_btn.setEnabled(has_text)
        if has_text and self._current_result is not None:
            self.stats_lbl.setText(
                f'原始 {format_byte_size(self._current_result.raw_size)} → {total_chars:,} 字符'
                if zh else
                f'{format_byte_size(self._current_result.raw_size)} → {total_chars:,} chars'
            )
        elif has_text:
            self.stats_lbl.setText(f'{total_chars:,} 字符' if zh else f'{total_chars:,} chars')
        else:
            self.stats_lbl.setText('')

    def _on_editor_text_changed(self):
        # 用户手动粘贴或修改文本后，以编辑器内容为准
        self._output_truncated = False
        self.trunc_banner.hide()
        text = self.b64_edit.toPlainText()
        self._full_output_text = text
        has_text = bool(text.strip())
        self.copy_b64_btn.setEnabled(has_text)
        self.export_txt_btn.setEnabled(has_text)
        self.stats_lbl.setText(f'{len(text):,} 字符' if has_text else '')

    def _decode_from_editor(self):
        zh = self.language == 'zh'
        source_text = self._full_output_text if self._output_truncated else self.b64_edit.toPlainText()
        try:
            decoded = decode_base64_to_media(source_text)
        except Exception as exc:
            show_warning(self, 'PengTools', str(exc))
            return
        self._current_result = None
        self._decoded_bytes = decoded.raw_bytes
        self._decoded_ext = decoded.ext
        self._decoded_mime = decoded.mime
        name = f'decoded{decoded.ext}'
        self._render_media_preview(decoded.raw_bytes, name, decoded.mime, decoded.kind, decoded.pdf_pages)
        self.save_media_btn.setEnabled(True)
        self.status_lbl.setText(
            f'已还原：{decoded.mime} · {format_byte_size(decoded.raw_size)}（可点左侧「保存还原文件…」落盘）'
            if zh else
            f'Decoded: {decoded.mime} · {format_byte_size(decoded.raw_size)}'
        )

    def _copy_base64(self):
        text = self._full_output_text if self._full_output_text else self.b64_edit.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        zh = self.language == 'zh'
        self.copy_b64_btn.setText('已复制完整内容' if zh else 'Copied')
        self._copy_reset_timer.start(1600)
        self.status_lbl.setText(
            f'已复制完整 Base64 到剪贴板（{len(text):,} 字符）'
            if zh else
            f'Copied {len(text):,} chars to clipboard'
        )

    def _export_base64_txt(self):
        text = self._full_output_text if self._full_output_text else self.b64_edit.toPlainText()
        if not text:
            return
        zh = self.language == 'zh'
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        base = os.path.splitext(self._current_result.filename)[0] if self._current_result else 'media'
        default_dir = get_dialog_start_dir('format_media_base64_export')
        default_path = os.path.join(default_dir, f'{base}_base64.txt') if default_dir else f'{base}_base64.txt'
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出 Base64 文本' if zh else 'Export Base64 text',
            default_path,
            'Text Files (*.txt);;All Files (*)',
        )
        if not path:
            return
        remember_dialog_path('format_media_base64_export', path)
        try:
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(text)
            self.status_lbl.setText(('已导出 Base64 文本：' if zh else 'Exported: ') + path)
        except OSError as exc:
            show_error(self, 'PengTools', str(exc))

    def _save_restored_file(self):
        if not self._decoded_bytes:
            return
        zh = self.language == 'zh'
        from tools.dialog_paths import get_dialog_start_dir, remember_dialog_path
        ext = self._decoded_ext or '.bin'
        default_dir = get_dialog_start_dir('format_media_base64_save')
        default_name = (
            self._current_result.filename if self._current_result else f'restored{ext}'
        )
        default_path = os.path.join(default_dir, default_name) if default_dir else default_name
        path, _ = QFileDialog.getSaveFileName(
            self,
            '保存还原后的图片 / PDF' if zh else 'Save decoded file',
            default_path,
            f'Media (*{ext});;All Files (*)',
        )
        if not path:
            return
        remember_dialog_path('format_media_base64_save', path)
        try:
            with open(path, 'wb') as stream:
                stream.write(self._decoded_bytes)
            show_info(
                self,
                '保存成功' if zh else 'Saved',
                f'已保存文件（{format_byte_size(len(self._decoded_bytes))}）：\n{path}' if zh else f'Saved to:\n{path}',
                kind='success',
            )
            self.status_lbl.setText(('已保存还原文件：' if zh else 'Saved: ') + path)
        except OSError as exc:
            show_error(self, 'PengTools', str(exc))

    def _clear_all(self):
        self._current_result = None
        self._decoded_bytes = b''
        self._decoded_ext = '.bin'
        self._decoded_mime = ''
        self._source_path = ''
        self._preview_pixmap = None
        self._full_output_text = ''
        self._output_truncated = False
        self.trunc_banner.hide()
        self.b64_edit.blockSignals(True)
        self.b64_edit.clear()
        self.b64_edit.blockSignals(False)
        self.copy_b64_btn.setEnabled(False)
        self.export_txt_btn.setEnabled(False)
        self.save_media_btn.setEnabled(False)
        self.stats_lbl.setText('')
        self.status_lbl.setText('')
        self._show_empty_drop_state()


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
            apply_splitter_orientation(xml.splitter, mode,
                                       min_editor=120 if mode in ('compact', 'narrow') or low_height else min_h)
        jv = getattr(self, 'json_viewer', None)
        if jv is not None and hasattr(jv, 'text_edit') and jv.text_edit is not None:
            jv.text_edit.setMinimumHeight(min_h)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        from ui.guided_tour import make_guide_button
        self.guide_btn = make_guide_button(self._start_guided_tour, self.language, self)
        header, self.page_title, self.page_subtitle = make_page_header(
            '格式工具',
            '离线整理，不落盘',
            'json',
            trailing=self.guide_btn,
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
        # 精简 XML 页顶区说明（工具内已有 zone）
        self.tabs.addTab(self.xml_workspace, 'XML')

        self.sql_tab = _SqlFormatTab(self.language)
        self.tabs.addTab(self.sql_tab, 'SQL')

        self.text_tab = _TextDevHelpersTab(self.language)
        self.tabs.addTab(self.text_tab, '文本与开发辅助')

        self.media_base64_tab = _MediaBase64Tab(self.language)
        self.tabs.addTab(self.media_base64_tab, '图片 / PDF 转 Base64')

        from PyQt6.QtWidgets import QScrollArea
        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setWidget(self.tabs)
        root.addWidget(self.workspace_scroll, 1)
        self.refresh_theme()

    def _start_guided_tour(self):
        from ui.guided_tour import start_guided_tour
        zh = self.language == 'zh'
        steps = [
            (
                self.tabs.tabBar(),
                '五大离线格式化与编码工具' if zh else 'Five offline format & encoding tools',
                '顶部一键切换 JSON（2 万节点快搜）、XML、SQL、文本辅助与新增的「图片 / PDF 转 Base64」，全部离线不上传。'
                if zh else
                'Switch between JSON, XML, SQL, Text helpers, and Image/PDF Base64 completely offline.',
            ),
            (
                self.tabs,
                '支持双向互转与接口排查联动' if zh else 'Bidirectional convert & API Debug handoff',
                '图片/PDF 支持拖拽、选文件或粘贴截图转 Base64，也支持粘贴 Base64 还原预览并另存文件；JSON/XML 支持从接口排查一键送入。'
                if zh else
                'Drop/paste images or PDFs for Base64, decode Base64 back to files, or inspect payloads from API Debug.',
            ),
        ]
        start_guided_tour(self, steps, module_title='格式工具' if zh else 'Format Tools', language=self.language)

    def refresh_theme(self):
        """刷新常驻 Tab 图标，避免主题切换后保留旧主题色。"""
        try:
            from ui.icons import qicon
            for index, role in enumerate(('json', 'xml', 'database', 'terminal', 'document-id')):
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
        if self.tabs.count() >= 5:
            self.tabs.setTabText(4, '图片 / PDF 转 Base64' if zh else 'Image & PDF Base64')
        self.json_viewer.set_language(language)
        self.xml_workspace.set_language(language)
        self.sql_tab.set_language(language)
        self.text_tab.set_language(language)
        if hasattr(self, 'media_base64_tab'):
            self.media_base64_tab.set_language(language)

    def open_media_base64(self, file_path: str = ''):
        self.tabs.setCurrentIndex(4)
        if file_path and hasattr(self, 'media_base64_tab'):
            self.media_base64_tab.load_file(file_path)

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
