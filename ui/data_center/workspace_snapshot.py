"""Explicit workspace save/restore using the application-owned store worker.

Only text and non-secret immutable targets cross this boundary. Restoration
creates dormant drafts, never a database job, transaction, or table ChangeSet.
"""

from dataclasses import replace
import uuid

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDialog, QLabel, QPlainTextEdit, QVBoxLayout

from tools.data_center.workspace_store import (
    DraftRecord, HistoryRecord, WorkspaceLayout, WorkspaceSnapshot, WorkspaceStoreError,
    WorkspaceTabKind, WorkspaceTextKind, validate_workspace_snapshot, workspace_draft_slot,
)
from ui.data_center.nosql_query_history import NoSqlDraft, restore_nosql_history_record, validate_nosql_target


def _saved_id(tab):
    value = getattr(tab, "_workspace_saved_id", "")
    if not value:
        value = uuid.uuid4().hex
        setattr(tab, "_workspace_saved_id", value)
    return value


def capture_workspace(panel):
    targets, drafts = [], []
    selected = None
    for index in range(panel.workspace_tabs.count()):
        workspace = panel.workspace_tabs.widget(index)
        host = getattr(workspace, "host", None)
        if host is None:
            continue
        visible = workspace is panel.workspace_tabs.currentWidget()
        sql_tabs = getattr(host, "sql_tabs", None)
        if sql_tabs is not None:
            manual = getattr(host, "manual_query_host", None)
            if manual is None:
                raise WorkspaceStoreError("INVALID_TARGET")
            for sql_index in range(sql_tabs.count()):
                tab = sql_tabs.widget(sql_index)
                target, state = manual.workspace_tab_context(tab)
                target = replace(target, tab_id=_saved_id(tab))
                targets.append(target)
                drafts.append(DraftRecord(target.tab_id + ":sql", target, tab.editor.toPlainText(), WorkspaceTextKind.SQL, state_code=state))
                if visible and tab is sql_tabs.currentWidget():
                    selected = target.tab_id
            continue
        nosql_tabs = getattr(host, "nosql_draft_tabs", None)
        if nosql_tabs is None:
            raise WorkspaceStoreError("INVALID_TARGET")
        host._save_nosql_draft_text(nosql_tabs.current_draft)
        for draft in nosql_tabs.drafts():
            if draft.target is None:
                raise WorkspaceStoreError("INVALID_TARGET")
            target = replace(draft.target, tab_id=_saved_id(draft))
            targets.append(target)
            worker = getattr(host, "_worker", None)
            state = "INTERRUPTED" if worker is not None and worker.isRunning() else "DRAFT"
            slots = ("command",) if target.tab_kind is WorkspaceTabKind.REDIS else ("documents", "command")
            for slot in slots:
                drafts.append(DraftRecord(target.tab_id + ":" + slot, target, draft.editor_texts.get(slot, ""), WorkspaceTextKind.NOSQL, state_code=state))
            if draft.snapshot is not None:
                slot = "command" if draft.snapshot.action == "command" else "documents"
                if draft.editor_texts.get(slot) == draft.snapshot.editor_text:
                    drafts.append(DraftRecord(target.tab_id + ":snapshot", target, draft.snapshot.body, WorkspaceTextKind.NOSQL, state_code=state))
            if visible and draft is nosql_tabs.current_draft:
                selected = target.tab_id
    snapshot = WorkspaceSnapshot(WorkspaceLayout(tuple(targets), selected), tuple(drafts))
    validate_workspace_snapshot(snapshot)
    return snapshot


def restore_host_draft(host, target, records):
    values = {workspace_draft_slot(record): record for record in records}
    if target.tab_kind is WorkspaceTabKind.SQL:
        tabs = getattr(host, "sql_tabs", None)
        manual = getattr(host, "manual_query_host", None)
        if tabs is None or manual is None:
            raise WorkspaceStoreError("INVALID_TARGET")
        for index in range(tabs.count()):
            tab = tabs.widget(index)
            if getattr(tab, "_workspace_saved_id", None) == target.tab_id:
                return tab  # Preserve current edits and any active transaction.
        record = values["sql"]
        history = HistoryRecord("workspace-" + target.tab_id, target, record.text, WorkspaceTextKind.SQL, state_code=record.state_code)
        code = manual.validate_history_record(history)
        title = "恢复草稿" if getattr(host, "language", "zh") == "zh" else "Restored draft"
        tab = host._new_sql_tab(record.text, title, history_record=history, history_validation_code=code)
        if tab is None:
            raise WorkspaceStoreError("STORE_CLOSED")
        tab._workspace_saved_id = target.tab_id
        return tab
    tabs = getattr(host, "nosql_draft_tabs", None)
    expected = "redis" if target.tab_kind is WorkspaceTabKind.REDIS else "mongo"
    if tabs is None or tabs.provider != expected:
        raise WorkspaceStoreError("INVALID_TARGET")
    for draft in tabs.drafts():
        if getattr(draft, "_workspace_saved_id", None) == target.tab_id:
            return draft
    host._save_nosql_draft_text(tabs.current_draft)
    profile_host = host._nosql_profile_host
    if "snapshot" in values:
        history = HistoryRecord("workspace-" + target.tab_id, target, values["snapshot"].text, WorkspaceTextKind.NOSQL, state_code="INTERRUPTED")
        draft = restore_nosql_history_record(history, expected, profile_host)
    else:
        draft = NoSqlDraft(uuid.uuid4().hex, expected, target, locked=True, runnable=False)
        try:
            profile = profile_host.load_connection_profile(target.connection_id, target.profile_revision)
            choice = profile_host.capture_connection(profile, database=target.database)
            draft.mode = choice.mode
            draft.db_index = int(target.database) if expected == "redis" else None
            draft.collection = target.schema if expected == "mongo" else ""
            validate_nosql_target(draft, profile_host)
            draft.runnable = True
        except Exception as error:
            code = getattr(error, "code", "PROFILE_UNAVAILABLE")
            draft.validation_code = code if code in {"PROFILE_MISMATCH", "PROFILE_UNAVAILABLE", "INVALID_TARGET"} else "PROFILE_UNAVAILABLE"
    for slot in ("documents", "command"):
        if slot in values:
            draft.editor_texts[slot] = values[slot].text
    draft._workspace_saved_id = target.tab_id
    tabs.add_draft(draft, select=False)
    return draft


class WorkspacePersistence(QObject):
    changed = pyqtSignal()

    def __init__(self, panel, *, service=None):
        super().__init__(panel)
        self.panel = panel
        self._service = service
        self._controller = None
        self._request_id = ""
        self._restoring = False
        self._pending = ()
        self._unavailable = []
        self._restored = {}
        self._positions = {}
        self._connection_order = []
        self._selected = None
        self._preview = None
        self._destroy_hook = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)

    @property
    def busy(self):
        return self._controller is not None or self._restoring

    def _text(self, zh, en):
        return zh if self.panel.language == "zh" else en

    def _status(self, text):
        self.panel.workspace_status.setText(text)
        self.panel.workspace_status.setVisible(bool(text))

    def save(self):
        if self.busy or self.panel._manual_shutdown_gated:
            return
        try:
            snapshot = capture_workspace(self.panel)
        except Exception as error:
            self._failure(getattr(error, "code", "INVALID_TARGET"))
            return
        self._begin("save", snapshot)

    def restore(self):
        if not self.busy and not self.panel._manual_shutdown_gated:
            self._begin("restore")

    def _begin(self, operation, snapshot=None):
        try:
            if self._service is None:
                from ui.data_center.query_history_service import get_application_history_service
                self._service = get_application_history_service()
            controller = self._service.new_controller()
            self._controller = controller
            service = self._service
            self._destroy_hook = lambda _object=None, c=controller, s=service: s.retire_controller(c)
            self.destroyed.connect(self._destroy_hook)
            controller.operation_completed.connect(self._saved)
            controller.workspace_ready.connect(self._loaded)
            controller.request_failed.connect(self._store_failed)
            self.changed.emit()
            self._status(self._text("正在保存工作区…", "Saving workspace…") if operation == "save" else self._text("正在读取上次工作区…", "Loading saved workspace…"))
            self._request_id = controller.save_workspace(snapshot) if operation == "save" else controller.load_workspace()
        except Exception:
            self._failure("STORE_FAILED")

    def _retire(self):
        controller, self._controller = self._controller, None
        if controller is not None:
            if self._destroy_hook is not None:
                self.destroyed.disconnect(self._destroy_hook)
                self._destroy_hook = None
            self._service.retire_controller(controller)
        self.changed.emit()

    def _saved(self, operation):
        if operation.request_id != self._request_id:
            return
        self._status(self._text("工作区已保存：草稿、目标和标签顺序；会话、事务与表格待应用变更不参与恢复。", "Workspace saved: drafts, targets and tab order. Sessions, transactions and staged table changes are not restored."))
        self._retire()

    def _loaded(self, result):
        if result.request_id != self._request_id:
            return
        snapshot = result.snapshot
        self._retire()
        if snapshot is None or not snapshot.layout.tabs:
            self._status(self._text("还没有保存的工作区。", "No saved workspace yet."))
            return
        self._snapshot = snapshot
        self._prior_workspace = self.panel.workspace_tabs.currentWidget()
        self._pending = iter(snapshot.layout.tabs)
        self._selected = snapshot.layout.selected_tab_id
        self._restored, self._positions, self._connection_order, self._unavailable = {}, {}, [], []
        self._restoring = True
        self.changed.emit()
        self._next()

    def _next(self):
        if self.panel._manual_shutdown_gated:
            self._restoring = False
            self._status(self._text("恢复已停止，已打开的草稿保留。", "Restore stopped; already opened drafts were kept."))
            self.changed.emit()
            return
        target = next(self._pending, None)
        if target is None:
            self._finish_restore()
            return
        records = tuple(record for record in self._snapshot.drafts if record.target == target)
        try:
            if self.panel.catalog.get_profile(target.connection_id) is None:
                raise WorkspaceStoreError("INVALID_TARGET")
            workspace = self.panel._open_connection(target.connection_id)
            if workspace is None:
                raise WorkspaceStoreError("INVALID_TARGET")
            item = restore_host_draft(workspace.host, target, records)
            self._restored[target.tab_id] = workspace, item
            position = self._positions.get(target.connection_id, 0)
            self._positions[target.connection_id] = position + 1
            if target.connection_id not in self._connection_order:
                self._connection_order.append(target.connection_id)
            host = workspace.host
            if target.tab_kind is WorkspaceTabKind.SQL:
                host.sql_tabs.tabBar().moveTab(host.sql_tabs.indexOf(item), position)
            else:
                bar = host.nosql_draft_tabs.tab_bar
                index = next(i for i in range(bar.count()) if bar.tabData(i) == item.draft_id)
                bar.moveTab(index, position)
        except Exception:
            self._unavailable.extend(record for record in records if workspace_draft_slot(record) != "snapshot")
        self._timer.start(0)

    def _finish_restore(self):
        self._restoring = False
        for position, connection_id in enumerate(self._connection_order, 1):
            workspace = self.panel._workspaces.get(connection_id)
            self.panel.workspace_tabs.tabBar().moveTab(self.panel.workspace_tabs.indexOf(workspace), position)
        selected = self._restored.get(self._selected)
        if selected:
            workspace, item = selected
            self.panel.workspace_tabs.setCurrentWidget(workspace)
            if hasattr(item, "editor"):
                workspace.host.sql_tabs.setCurrentWidget(item)
            else:
                workspace.host.nosql_draft_tabs.select_draft(item.draft_id)
        elif self.panel.workspace_tabs.indexOf(self._prior_workspace) >= 0:
            self.panel.workspace_tabs.setCurrentWidget(self._prior_workspace)
        self._status(self._text(f"已恢复 {len(self._restored)} 个草稿，未执行查询。", f"Restored {len(self._restored)} drafts; no query was executed."))
        if any(record.state_code == "INTERRUPTED" for record in self._snapshot.drafts):
            self._status(self.panel.workspace_status.text() + self._text(" 上次保存包含未完成操作，原会话及事务状态不会恢复。", " The save includes interrupted work; original sessions and transaction state are not restored."))
        self.changed.emit()
        if self._unavailable:
            self._show_unavailable()

    def _show_unavailable(self):
        if self._preview is not None:
            try:
                self._preview.close()
            except RuntimeError:
                pass
        dialog = QDialog(self.panel)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowTitle(self._text("未能绑定目标的草稿", "Drafts with unavailable targets"))
        dialog.resize(720, 480)
        if dialog.screen() is not None:
            area = dialog.screen().availableGeometry()
            dialog.resize(min(720, int(area.width() * .85)), min(480, int(area.height() * .85)))
        layout = QVBoxLayout(dialog)
        label = QLabel(self._text("连接已删除或类型不匹配；草稿仍保存在工作区中，可在这里查看和复制。", "The connection was removed or its type changed. Saved drafts remain available here for viewing and copying."))
        label.setWordWrap(True)
        layout.addWidget(label)
        choice = QComboBox(dialog)
        preview = QPlainTextEdit(dialog)
        preview.setReadOnly(True)
        records = tuple(self._unavailable)
        for record in records:
            choice.addItem(f"{record.target.connection_id} / {record.target.database} / {record.target.schema} · {workspace_draft_slot(record)}")
            choice.setItemData(choice.count() - 1, f"{record.target.connection_id} / {record.target.profile_revision} / {record.target.database} / {record.target.schema}", Qt.ItemDataRole.ToolTipRole)
        choice.currentIndexChanged.connect(lambda index: preview.setPlainText(records[index].text))
        preview.setPlainText(records[0].text)
        layout.addWidget(choice)
        layout.addWidget(preview, 1)
        self._preview = dialog
        dialog.show()

    def _failure(self, code):
        known = {"CREDENTIAL_TEXT_REJECTED": self._text("草稿包含凭据信息，未保存；请移除后重试。", "Draft contains credentials; remove them before saving."),
                 "INVALID_TARGET": self._text("部分草稿没有固定目标，未保存。", "Some drafts have no fixed target; workspace was not saved."),
                 "STORE_LIMIT_REACHED": self._text("工作区存储已满，原保存内容保持不变。", "Workspace storage is full; the previous save was kept.")}
        self._status(known.get(code, self._text("工作区操作失败，当前草稿与上次保存内容已保留。", "Workspace operation failed; current drafts and the previous save were kept.")))
        self._retire()

    def _store_failed(self, failure):
        self._failure(failure.code)
