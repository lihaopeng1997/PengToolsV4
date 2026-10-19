"""Modeless Mongo import/export controls with captured targets and owned jobs."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from ui.confirm_dialog import confirm_action
from ui.design_system import apply_button


class MongoTransferDialog(QDialog):
    def __init__(self, target, dispatch, *, exporting=False, selected_count=0, page_count=0, language='zh', parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.target, self.dispatch = target, dispatch
        self.exporting, self.language = exporting, language
        self.plan = None
        self._worker = None
        self._terminal = None
        self._pending_close = False
        self._attempted = False
        self.setObjectName('mongo-transfer-dialog')
        self.setWindowTitle(self._text('导出 Extended JSON' if exporting else '导入 Extended JSON',
                                       'Export Extended JSON' if exporting else 'Import Extended JSON'))
        self.resize(760, 560)
        if self.screen() is not None:
            area = self.screen().availableGeometry()
            self.resize(min(760, int(area.width() * .85)), min(560, int(area.height() * .85)))
        root = QVBoxLayout(self)
        label = QLabel(f'{target.connection_id} / {target.database}.{target.schema}', self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        root.addWidget(label)
        self.scope = QComboBox(self)
        if exporting:
            if selected_count:
                self.scope.addItem(self._text(f'选中文档（{selected_count} 条）', f'Selected documents ({selected_count})'), 'selected')
            self.scope.addItem(self._text(f'当前页快照（{page_count} 条）', f'Current page snapshot ({page_count})'), 'page')
            self.scope.addItem(self._text('整个筛选查询（从 0 开始，忽略分页数量）', 'Entire filtered query (from 0, without page limit)'), 'query')
            root.addWidget(self.scope)
        note = self._text(
            '导出为每行一个文档的 Canonical Extended JSON（.jsonl），保留查询投影；不是数据库一致性快照。整个查询流式读取，文件上限 512 MiB。停止或失败不发布不完整文件。' if exporting else
            '支持 UTF-8 Extended JSON 数组、单个文档或 JSON Lines；最多 64 MiB / 10000 条。仅追加插入，保留 _id 和 BSON 类型；无 _id 时自动生成。确认后遇到首个失败即停止，已插入文档不会回滚；不会重试或覆盖同 _id 文档。',
            'Canonical Extended JSON Lines (.jsonl), retaining the query projection. This is not a consistent database snapshot. Entire queries stream up to 512 MiB. Cancelled or failed exports do not publish incomplete files.' if exporting else
            'UTF-8 Extended JSON array, single document or JSON Lines; up to 64 MiB / 10000 documents. Insert only, preserving _id and BSON types; missing ids are generated. Stops on first failure without rolling back prior inserts. No retry or replacement of existing ids.')
        description = QLabel(note, self)
        description.setWordWrap(True)
        root.addWidget(description)
        file_row = QHBoxLayout()
        self.path = QLineEdit(self)
        self.browse_btn = QPushButton(self._text('选择文件', 'Choose file'), self)
        file_row.addWidget(self.path, 1)
        file_row.addWidget(self.browse_btn)
        root.addLayout(file_row)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        root.addWidget(self.preview, 1)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        row = QHBoxLayout()
        self.preview_btn = QPushButton(self._text('读取并预览', 'Read and preview'), self)
        self.apply_btn = QPushButton(self._text('开始导出' if exporting else '确认追加导入', 'Export' if exporting else 'Confirm insert'), self)
        self.close_btn = QPushButton(self._text('关闭', 'Close'), self)
        for button in (self.preview_btn, self.apply_btn, self.close_btn):
            apply_button(button, 'primary' if button is self.apply_btn else 'ghost', compact=True)
            button.setAutoDefault(False)
            row.addWidget(button)
        self.preview_btn.setVisible(not exporting)
        root.addLayout(row)
        self.apply_btn.setEnabled(False)
        self.path.textChanged.connect(self._path_changed)
        self.browse_btn.clicked.connect(self._browse)
        self.preview_btn.clicked.connect(self.prepare)
        self.apply_btn.clicked.connect(self.apply)
        self.close_btn.clicked.connect(self.reject)

    def _text(self, zh, en):
        return zh if self.language == 'zh' else en

    def _path_changed(self):
        self.plan = None
        self.preview.clear()
        self.apply_btn.setEnabled(self.exporting and bool(self.path.text().strip()) and not self._attempted)

    def _browse(self):
        if self.exporting:
            path, _ = QFileDialog.getSaveFileName(self, self.windowTitle(), '', 'Extended JSON Lines (*.jsonl)',
                                                 options=QFileDialog.Option.DontConfirmOverwrite)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.windowTitle(), '', 'Extended JSON (*.json *.jsonl *.ndjson)')
        if path:
            self.path.setText(path)

    def prepare(self):
        if self._worker is None and not self._attempted and self.path.text().strip():
            self.plan = None
            self.preview.clear()
            self._dispatch('prepare_import', {'path': self.path.text().strip(), 'target': self.target})

    def apply(self):
        if self._worker is not None or self._attempted:
            return
        if self.exporting:
            destination = self.path.text().strip()
            if not destination:
                return
            overwrite = Path(destination).exists()
            if overwrite and not confirm_action(self, self.windowTitle(), self._text('替换所选目标文件？', 'Replace the selected destination file?'),
                                                confirm_text=self._text('替换文件', 'Replace file')):
                return
            self._dispatch('export_query' if self.scope.currentData() == 'query' else 'export_documents',
                           {'destination': destination, 'overwrite': overwrite, 'scope': self.scope.currentData()})
        elif self.plan is not None:
            self._dispatch('document_import', {'plan': self.plan, 'authorized': True})

    def _attach(self, worker):
        self._worker = worker
        self._terminal = None
        self._controls(False)
        worker.completed.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.progress.connect(self._progress)

    def _dispatch(self, kind, values):
        try:
            accepted = self.dispatch(kind, values, self._attach)
        except Exception:
            self.status.setText(self._text('目标连接已变化或不可用；未开始操作。', 'The original connection changed or is unavailable; operation not started.'))
            return
        if not accepted:
            worker = self._worker
            if worker is not None and not worker.isRunning() and not worker.isFinished():
                self._worker = None
                self._controls(True)
            self.status.setText(self._text('工作台忙或任务未能启动，请稍后重试。', 'Workbench busy or worker did not start; try again later.'))
            return
        self._attempted = kind != 'prepare_import'
        self.status.setText(self._text('正在处理…', 'Working…'))

    def _controls(self, enabled):
        for widget in (self.path, self.browse_btn, self.scope, self.preview_btn):
            widget.setEnabled(enabled and not self._attempted)
        self.apply_btn.setEnabled(enabled and not self._attempted and (bool(self.path.text().strip()) if self.exporting else self.plan is not None))

    def _progress(self, completed, total):
        if self.sender() is self._worker:
            self.status.setText(self._text(f'已处理 {completed}' + (f' / {total}' if total else '') + ' 条，正在等待完成…',
                                           f'Processed {completed}' + (f' / {total}' if total else '') + '; waiting for completion…'))

    def _done(self, kind, payload):
        if self.sender() is self._worker:
            self._terminal = kind, payload

    def _failed(self, kind, code):
        if self.sender() is self._worker:
            self._terminal = 'error', code

    def _finished(self):
        worker = self.sender()
        if worker is not self._worker:
            return
        self._worker = None
        if self._pending_close:
            super().reject()
            return
        terminal = self._terminal
        if terminal is None or terminal[0] == 'error':
            messages = {
                'MONGO_IMPORT_READ_FAILED': ('无法读取 UTF-8 文件，请检查文件和路径。', 'Cannot read the UTF-8 file; check the file and path.'),
                'MONGO_IMPORT_INVALID_JSON': ('文件不是有效的 Extended JSON。', 'The file is not valid Extended JSON.'),
                'MONGO_IMPORT_DUPLICATE_FIELD': ('文件含重复字段，请修正后重新预览。', 'Duplicate fields; correct the file and preview again.'),
                'MONGO_IMPORT_LIMIT': ('导入为空或超过 64 MiB / 10000 条上限。', 'Import is empty or exceeds 64 MiB / 10000 documents.'),
                'MONGO_EDIT_INVALID_JSON': ('Extended JSON 类型格式无效，请修正后重新预览。', 'Invalid Extended JSON type; correct it and preview again.'),
                'MONGO_EDIT_DOCUMENT_REQUIRED': ('每一项必须是文档对象。', 'Every item must be a document object.'),
                'MONGO_EDIT_TOO_LARGE': ('存在超过 BSON / 文本上限的文档。', 'A document exceeds the BSON or text limit.'),
                'MONGO_EXPORT_EXISTS': ('目标文件已存在，未覆盖。', 'The destination exists and was not overwritten.'),
                'MONGO_EXPORT_LIMIT': ('导出超过 512 MiB，上次目标文件未改变。', 'Export exceeds 512 MiB; the previous destination was retained.'),
            }
            pair = messages.get(terminal[1]) if terminal is not None else None
            fallback = ('操作已停止或失败；导入可能已有部分生效，请重新查询核实。', 'Stopped or failed; imports may have partially applied. Query to verify.') if not self.exporting and self._attempted else ('操作已停止或失败，请检查文件和目标后重试。', 'Stopped or failed; check the file and target before retrying.')
            self.status.setText(self._text(*(pair or fallback)))
        elif terminal[0] == 'prepare_import':
            self.plan = terminal[1]
            self.preview.setPlainText(self.plan.preview)
            self.status.setText(self._text(f'共 {len(self.plan.documents)} 条，{self.plan.generated_ids} 条将生成 _id。仅预览前 3 条；请确认目标后导入。',
                                           f'{len(self.plan.documents)} documents; {self.plan.generated_ids} generated ids. Preview shows up to 3 documents. Confirm the target before importing.'))
        elif terminal[0] == 'document_import':
            result = terminal[1]
            labels = {'inserted': self._text('成功', 'inserted'), 'duplicate': self._text('重复 _id，失败', 'duplicate id'),
                      'unknown': self._text('失败或结果未知，需核实', 'failed or unknown; verify'), 'skipped': self._text('未执行', 'skipped')}
            self.preview.setPlainText('\n'.join(f'{index}: {labels[state]}' for index, state in result['items']))
            self.status.setText(self._text(f"已确认插入 {result['inserted']} / {result['total']} 条。请刷新查询；本窗口不再重试。",
                                           f"Confirmed {result['inserted']} / {result['total']} inserts. Refresh the query; this window will not retry."))
        else:
            self.status.setText(self._text(f"导出完成：{terminal[1]['documents']} 条，{terminal[1]['bytes']} 字节。",
                                           f"Export complete: {terminal[1]['documents']} documents, {terminal[1]['bytes']} bytes."))
        self._controls(True)

    def reject(self):
        if self._worker is not None:
            self._pending_close = True
            self._worker.cancelled = True
            self.status.setText(self._text('正在等待结束；停止不会撤销已插入文档。', 'Waiting for completion; stopping will not undo inserted documents.'))
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
