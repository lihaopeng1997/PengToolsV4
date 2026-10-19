"""Modeless native document editor; injected dispatch owns every worker."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout

from ui.design_system import apply_button


class MongoDocumentEditDialog(QDialog):
    def __init__(self, loaded, dispatch, *, language="zh", parent=None):
        super().__init__(parent)
        self.setObjectName("mongo-document-edit-dialog")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.loaded = loaded
        self.dispatch = dispatch
        self.language = language
        self.plan = None
        self._worker = None
        self._terminal = None
        self._pending_close = False
        self._write_attempted = False
        self.setWindowTitle(self._text("按 _id 修改文档", "Edit document by _id"))
        self.resize(860, 620)
        if self.screen() is not None:
            area = self.screen().availableGeometry()
            self.resize(min(860, int(area.width() * .85)), min(620, int(area.height() * .85)))
        root = QVBoxLayout(self)
        target = loaded.context.target
        label = QLabel(f"{target.connection_id} / {target.database}.{loaded.context.collection}", self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setToolTip(target.profile_revision)
        root.addWidget(label)
        identifier_row = QHBoxLayout()
        identifier_row.addWidget(QLabel("_id", self))
        self.identifier = QLineEdit(self)
        self.identifier.setReadOnly(True)
        self.identifier.setMaxLength(64 * 1024 * 1024)
        self.identifier.setText(loaded.context.identifier_json.replace("\n", " "))
        identifier_row.addWidget(self.identifier, 1)
        root.addLayout(identifier_row)
        note = QLabel(self._text(
            "已按 _id 重新读取完整文档。确认后替换整份文档，省略的字段会删除；_id 不可修改。其他客户端期间的修改可能被覆盖，操作不提供事务回滚。",
            "The complete document was reloaded by _id. Confirmation replaces the whole document, removing omitted fields; _id cannot change. Concurrent changes may be overwritten. There is no transaction rollback."), self)
        note.setWordWrap(True)
        root.addWidget(note)
        self.tabs = QTabWidget(self)
        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("mongo-document-editor")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlainText(loaded.document_json)
        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("mongo-document-preview")
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.editor, self._text("完整文档 · Extended JSON", "Complete document · Extended JSON"))
        self.tabs.addTab(self.preview, self._text("变更预览", "Change preview"))
        root.addWidget(self.tabs, 1)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.preview_btn = QPushButton(self._text("预览修改", "Preview changes"), self)
        self.apply_btn = QPushButton(self._text("确认替换文档", "Confirm replacement"), self)
        self.close_btn = QPushButton(self._text("关闭", "Close"), self)
        for button in (self.preview_btn, self.apply_btn, self.close_btn):
            button.setAutoDefault(False)
            apply_button(button, "primary" if button is self.apply_btn else "ghost", compact=True)
            buttons.addWidget(button)
        self.apply_btn.setEnabled(False)
        root.addLayout(buttons)
        self.editor.textChanged.connect(self._edited)
        self.preview_btn.clicked.connect(self.prepare)
        self.apply_btn.clicked.connect(self.apply)
        self.close_btn.clicked.connect(self.reject)

    def _text(self, zh, en):
        return zh if self.language == "zh" else en

    def _edited(self):
        self.plan = None
        self.apply_btn.setEnabled(False)
        self.preview.clear()
        self.status.setText(self._text("修改后请重新预览。", "Preview the changes before confirming."))

    def _attach(self, worker):
        self._worker = worker
        self._terminal = None
        self.editor.setReadOnly(True)
        self.preview_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        worker.completed.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)

    def prepare(self):
        if self._worker is not None or self._write_attempted:
            return
        self._dispatch("prepare_edit", {"loaded": self.loaded, "edited_text": self.editor.toPlainText()})

    def apply(self):
        if self._worker is not None or self._write_attempted or self.plan is None or not self.plan.changed:
            return
        self._dispatch("document_update", {"plan": self.plan, "authorized": True})

    def _dispatch(self, kind, values):
        try:
            accepted = self.dispatch(kind, values, self._attach)
        except Exception as error:
            self.status.setText(self._error(getattr(error, "code", str(error))))
            return
        if not accepted:
            worker = self._worker
            if worker is not None and not worker.isRunning() and not worker.isFinished():
                self._worker = None
                self.editor.setReadOnly(False)
                self.preview_btn.setEnabled(True)
                self.apply_btn.setEnabled(bool(self.plan and self.plan.changed))
            self.status.setText(self._text("工作台仍在处理任务，请稍后重试。", "The workbench is busy; try again when it finishes."))
            return
        if kind == "document_update":
            self._write_attempted = True
        self.status.setText(self._text("正在准备预览…", "Preparing preview…") if kind == "prepare_edit" else self._text("正在执行，停止请求不会撤销已生效修改…", "Updating; a stop request cannot undo an applied change…"))

    def _done(self, kind, value):
        if self.sender() is not self._worker:
            return
        self._terminal = kind, value
        if kind == "prepare_edit":
            self.plan = value
            self.preview.setPlainText(value.preview)
            self.tabs.setCurrentWidget(self.preview)
            self.status.setText(self._text("预览已生成，正在结束操作…", "Preview ready; finishing operation…"))
        else:
            self.status.setText(self._text("服务端已返回，正在结束操作…", "Server replied; finishing operation…"))

    def _failed(self, kind, message):
        if self.sender() is not self._worker:
            return
        self._terminal = "error", message
        self.status.setText(self._error(message))

    def _finished(self):
        worker = self.sender()
        if worker is not self._worker:
            return
        self._worker = None
        if self._pending_close:
            super().reject()
            return
        terminal = self._terminal
        if worker.kind == "prepare_edit":
            self.editor.setReadOnly(False)
            self.preview_btn.setEnabled(True)
            self.apply_btn.setEnabled(bool(terminal and terminal[0] != "error" and self.plan and self.plan.changed))
            if terminal and terminal[0] != "error":
                self.status.setText(self._text("请核对完整变更与目标，再确认执行。", "Review the changes and target before confirming.") if self.plan.changed else self._text("内容未变化，不会发送写操作。", "No changes; no write will be sent."))
            elif terminal is None:
                self.status.setText(self._text("预览已停止。", "Preview stopped."))
        elif terminal and terminal[0] != "error":
            value = terminal[1]
            self.plan = None
            self.status.setText(self._text(f"已匹配 {value['matched']} 条，修改 {value['modified']} 条。请刷新结果查看；当前窗口不再重复执行。", f"Matched {value['matched']}, modified {value['modified']}. Refresh results to view; this window will not execute again."))
        elif terminal is None:
            self.status.setText(self._text("操作已停止；若已开始发送写入，结果需重新查询核实。", "Operation stopped; if the write started, query again to verify its outcome."))

    def _error(self, code):
        messages = {
            "MONGO_EDIT_ID_IMMUTABLE": ("_id 不可修改。", "_id cannot be changed."),
            "MONGO_EDIT_ID_REQUIRED": ("文档必须保留 _id。", "The document must retain _id."),
            "MONGO_EDIT_INVALID_JSON": ("Extended JSON 无效，请修改后重新预览。", "Invalid Extended JSON; correct it and preview again."),
            "MONGO_EDIT_DUPLICATE_FIELD": ("存在重复字段，请修改后重新预览。", "Duplicate field names; correct them before previewing."),
            "MONGO_EDIT_NOT_FOUND": ("未匹配到文档，未新增记录；请关闭后重新读取。", "Document not found; no record was inserted. Close and reload."),
            "MONGO_EDIT_TOO_LARGE": ("文档超过 BSON 或编辑文本上限。", "Document exceeds the BSON or editor text limit."),
            "PROFILE_MISMATCH": ("连接配置已变化，未执行修改；请关闭后重新读取。", "Connection profile changed; no update sent. Close and reload."),
            "PROFILE_UNAVAILABLE": ("原连接不可用，未执行修改；编辑文字仍保留。", "Original connection unavailable; no update sent. Edited text is kept."),
        }
        pair = messages.get(code)
        return self._text(*pair) if pair else self._text("操作失败或结果未知；请核实目标与文档，写入失败后需关闭并重新读取。", "Operation failed or outcome unknown; verify the target and document. After a failed write, close and reload.")

    def reject(self):
        if self._worker is not None:
            self._pending_close = True
            self._worker.cancelled = True
            self.status.setText(self._text("正在等待操作结束；停止不代表撤销已生效修改。", "Waiting for the operation to finish; stopping does not undo applied changes."))
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
