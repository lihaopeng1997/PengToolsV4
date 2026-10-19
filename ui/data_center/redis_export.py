"""One explicitly selected raw Redis key; worker dispatch is injected."""

import base64
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ui.confirm_dialog import confirm_action
from ui.design_system import apply_button


class RedisExportDialog(QDialog):
    def __init__(self, target, keys, dispatch, *, language='zh', parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName('redis-export-dialog')
        self.target, self.dispatch, self.language = target, dispatch, language
        self._worker = None
        self._terminal = None
        self._pending_close = False
        self._completed = False
        self.setWindowTitle(self._text('导出选中 Key', 'Export selected key'))
        self.resize(720, 400)
        if self.screen() is not None:
            area = self.screen().availableGeometry()
            self.resize(min(720, int(area.width() * .85)), min(400, int(area.height() * .85)))
        root = QVBoxLayout(self)
        target_label = QLabel(f'{target.connection_id} / DB {target.database}', self)
        target_label.setTextFormat(Qt.TextFormat.PlainText)
        target_label.setWordWrap(True)
        root.addWidget(target_label)
        note = QLabel(self._text(
            '仅导出明确选择的一个 Key，JSON 中以 Base64 保存原始名称和值。分块读取，文件上限 512 MiB，集合单项上限 4 MiB。这是实时查看数据，不是备份或一致性快照；扫描类结果可能重复，不提供导入恢复。',
            'Exports one explicitly selected key, preserving raw names and values as Base64 in JSON. Reads in chunks; up to 512 MiB per file and 4 MiB per collection item. Live viewing data, not a backup or consistent snapshot. Scan results may repeat; no import/restore.'), self)
        note.setWordWrap(True)
        root.addWidget(note)
        self.keys = QComboBox(self)
        if len(keys) > 1:
            self.keys.addItem(self._text('显示名称相同，请选择原始 Key（Base64）', 'Identical display names; select the raw key (Base64)'), None)
        for key in keys:
            encoded = base64.b64encode(key).decode('ascii')
            self.keys.addItem(f'Base64: {encoded[:120]} ({len(key)} bytes)', key)
            self.keys.setItemData(self.keys.count() - 1, encoded, Qt.ItemDataRole.ToolTipRole)
        root.addWidget(self.keys)
        row = QHBoxLayout()
        self.path = QLineEdit(self)
        self.browse_btn = QPushButton(self._text('选择文件', 'Choose file'), self)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_btn)
        root.addLayout(row)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status, 1)
        buttons = QHBoxLayout()
        self.export_btn = QPushButton(self._text('导出这个 Key', 'Export this key'), self)
        self.close_btn = QPushButton(self._text('关闭', 'Close'), self)
        for button in (self.export_btn, self.close_btn):
            button.setAutoDefault(False)
            apply_button(button, 'primary' if button is self.export_btn else 'ghost', compact=True)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.export_btn.setEnabled(False)
        self.path.textChanged.connect(self._ready)
        self.keys.currentIndexChanged.connect(self._ready)
        self.browse_btn.clicked.connect(self._browse)
        self.export_btn.clicked.connect(self.apply)
        self.close_btn.clicked.connect(self.reject)

    def _text(self, zh, en):
        return zh if self.language == 'zh' else en

    def _ready(self):
        self.export_btn.setEnabled(not self._completed and self._worker is None and bool(self.path.text().strip()) and type(self.keys.currentData()) is bytes)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, self.windowTitle(), '', 'JSON (*.json)', options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.path.setText(path)

    def apply(self):
        key = self.keys.currentData()
        destination = self.path.text().strip()
        if self._completed or self._worker is not None or type(key) is not bytes or not destination:
            return
        overwrite = Path(destination).exists()
        if overwrite and not confirm_action(self, self.windowTitle(), self._text('替换所选文件？', 'Replace the selected file?'),
                                            confirm_text=self._text('替换文件', 'Replace file')):
            return
        try:
            accepted = self.dispatch({'key': key, 'destination': destination, 'target': self.target, 'overwrite': overwrite}, self._attach)
        except Exception:
            self.status.setText(self._text('原连接已变化或不可用，未开始导出。', 'The original connection changed or is unavailable; export not started.'))
            return
        if not accepted:
            worker = self._worker
            if worker is not None and not worker.isRunning() and not worker.isFinished():
                self._worker = None
                self._controls(True)
            self.status.setText(self._text('工作台忙或任务未能启动，请稍后重试。', 'Workbench busy or worker did not start; try later.'))
            return
        self.status.setText(self._text('正在导出…', 'Exporting…'))

    def _controls(self, enabled):
        for widget in (self.keys, self.path, self.browse_btn):
            widget.setEnabled(enabled)
        self._ready()

    def _attach(self, worker):
        self._worker = worker
        self._terminal = None
        self._controls(False)
        worker.completed.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.progress.connect(self._progress)

    def _done(self, kind, receipt):
        if self.sender() is self._worker:
            self._terminal = 'done', receipt

    def _failed(self, kind, code):
        if self.sender() is self._worker:
            self._terminal = 'error', code

    def _progress(self, count, total):
        if self.sender() is self._worker:
            self.status.setText(self._text(f'已处理 {count} 条，正在等待完成…', f'Processed {count}; waiting for completion…'))

    def _finished(self):
        if self.sender() is not self._worker:
            return
        self._worker = None
        if self._pending_close:
            super().reject()
            return
        if self._terminal is not None and self._terminal[0] == 'done':
            self._completed = True
            result = self._terminal[1]
            self.status.setText(self._text(f"导出完成：{result['records']} 条记录，{result['bytes']} 字节。", f"Export complete: {result['records']} records, {result['bytes']} bytes."))
            self.export_btn.setEnabled(False)
            return
        messages = {
            'REDIS_EXPORT_MISSING': ('Key 已过期或不存在。', 'Key expired or does not exist.'),
            'REDIS_EXPORT_VALUE_LIMIT': ('集合单项超过 4 MiB，未发布文件。', 'A collection item exceeds 4 MiB; file not published.'),
            'REDIS_EXPORT_OUTPUT_LIMIT': ('文件超过 512 MiB，未发布文件。', 'Output exceeds 512 MiB; file not published.'),
            'REDIS_EXPORT_UNSUPPORTED_TYPE': ('暂不支持此 Key 类型的查看性导出。', 'Viewing export is unavailable for this key type.'),
            'REDIS_EXPORT_KEY_CHANGED': ('读取期间 Key 已变化，请刷新后重试。', 'Key changed during reading; refresh and retry.'),
        }
        code = self._terminal[1] if self._terminal is not None else ''
        self.status.setText(self._text(*messages.get(code, ('导出失败或已停止；已有目标文件未改变。', 'Export failed or stopped; the prior destination was retained.'))))
        self._controls(True)

    def reject(self):
        if self._worker is not None:
            self._pending_close = True
            self._worker.cancelled = True
            self.status.setText(self._text('正在等待读取和文件清理结束…', 'Waiting for reading and file cleanup…'))
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
