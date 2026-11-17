"""内网排障：场景配置、材料预览、外发确认与只读运行界面。

The dialog owns no policy of its own: it prepares a scenario, asks for an
outbound grant when the endpoint is not provably intranet, and calls
``tools.diagnostics_run.run_diagnostics``. The forced check lives inside that
call, so closing or bypassing this dialog cannot send anything.
"""
from __future__ import annotations

import os
import uuid

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from tools import outbound_authorization as auth
from tools.intranet_scenario import IntranetScenario, load_scenario, save_scenario
from tools.log_materials import MaterialReader
from tools.model_boundary import classify_endpoint
from tools.readonly_diagnostics import READ_ONLY_TOOLS


_ORPHANS = set()  # workers that outlived their dialog; finished -> deleteLater


class _RunWorker(QThread):
    """One read-only run; never touches the UI thread directly."""

    done = pyqtSignal(object, str)
    failed = pyqtSignal(str, str)

    def __init__(self, scenario, question, model_id, run_id, token, parent=None):
        super().__init__(parent)
        self.scenario = scenario
        self.question = question
        self.model_id = model_id
        self.run_id = run_id
        self.token = token

    def run(self):  # noqa: D102
        from tools.data_center.host_model_adapter import AgentModelHostAdapter
        from tools.diagnostics_run import run_diagnostics

        # The token is created before the thread starts; honour it first so an
        # immediate stop never opens a connection.
        if self.token.is_cancelled():
            self.failed.emit('已取消', self.run_id)
            return
        try:
            adapter = AgentModelHostAdapter(self.model_id, retry_count=1,
                                            strict_tool_envelope=True, expose_reasoning=True)
            if self.token.is_cancelled():
                self.failed.emit('已取消', self.run_id)
                return
            outcome = run_diagnostics(self.scenario, self.question, adapter=adapter,
                                      run_id=self.run_id, cancellation=self.token)
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}', self.run_id)
            return
        self.done.emit(outcome, self.run_id)

    def cancel(self):
        self.token.cancel()


class IntranetDiagnosticsDialog(QDialog):
    def __init__(self, parent=None, language='zh'):
        super().__init__(parent)
        self.zh = language == 'zh'
        self.scenario = load_scenario()
        self.worker = None
        self.run_id = ''
        self._token = None
        self._void_runs = set()
        self.granted_scope = None
        self.granted_scope_summary = None
        self._closing = False
        self._tearing_down = False
        self.setWindowTitle('内网排障' if self.zh else 'Intranet diagnostics')
        self.resize(880, 720)
        self._build()

    # -- helpers ----------------------------------------------------------
    def _t(self, zh, en):
        return zh if self.zh else en

    def _build(self):
        root = QVBoxLayout(self)

        target = QFormLayout()
        self.system_edit = QLineEdit(self.scenario.system_name)
        target.addRow(self._t('系统名称', 'System'), self.system_edit)

        root_row = QHBoxLayout()
        self.code_root_edit = QLineEdit(self.scenario.code_root)
        self.code_root_edit.textChanged.connect(self._on_scope_changed)
        browse = QPushButton(self._t('选择…', 'Browse…'))
        browse.clicked.connect(self._pick_root)
        root_row.addWidget(self.code_root_edit, 1)
        root_row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(root_row)
        target.addRow(self._t('代码根目录', 'Code root'), holder)

        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._refresh_boundary)
        target.addRow(self._t('目标模型', 'Model'), self.model_combo)
        self.boundary_label = QLabel()
        self.boundary_label.setWordWrap(True)
        target.addRow(self._t('外发目标', 'Destination'), self.boundary_label)
        root.addLayout(target)

        root.addWidget(QLabel(self._t('日志材料（可来自代码根之外，仅这些文件可读）',
                                      'Log materials (may live outside the code root; only these are readable)')))
        self.materials = QListWidget()
        self.materials.model().rowsInserted.connect(self._on_scope_changed)
        self.materials.model().rowsRemoved.connect(self._on_scope_changed)
        root.addWidget(self.materials, 1)
        material_row = QHBoxLayout()
        add_log = QPushButton(self._t('添加日志…', 'Add log…'))
        add_log.clicked.connect(self._add_material)
        remove = QPushButton(self._t('移除所选', 'Remove'))
        remove.clicked.connect(self._remove_material)
        preview = QPushButton(self._t('预览将发送内容', 'Preview payload'))
        preview.clicked.connect(self._preview_materials)
        material_row.addWidget(add_log)
        material_row.addWidget(remove)
        material_row.addWidget(preview)
        material_row.addStretch()
        root.addLayout(material_row)

        self.tools_label = QLabel()
        self.tools_label.setWordWrap(True)
        root.addWidget(self.tools_label)

        self.question_edit = QPlainTextEdit()
        self.question_edit.setPlaceholderText(self._t('描述现象，例如：订单 42 在 10:00 报错',
                                                      'Describe the symptom'))
        self.question_edit.setMaximumHeight(90)
        root.addWidget(self.question_edit)

        confirm_row = QHBoxLayout()
        self.confirm_check = QCheckBox(self._t('我确认将上述材料发送到该模型目标', 'I approve sending these materials'))
        self.confirm_check.toggled.connect(self._refresh_buttons)
        confirm_row.addWidget(self.confirm_check)
        confirm_row.addStretch()
        self.confirm_btn = QPushButton(self._t('记录本次授权', 'Record authorization'))
        self.confirm_btn.clicked.connect(self._grant)
        confirm_row.addWidget(self.confirm_btn)
        root.addLayout(confirm_row)

        action_row = QHBoxLayout()
        self.run_btn = QPushButton(self._t('开始只读排障', 'Run read-only diagnostics'))
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton(self._t('停止', 'Stop'))
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        action_row.addWidget(self.run_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        root.addWidget(self.output, 2)

        self._load_models()
        self._load_materials()
        self.tools_label.setText(self._t(
            f'可用工具（只读）：{"、".join(self.scenario.allowed_tools or READ_ONLY_TOOLS)}',
            f'Read-only tools: {", ".join(self.scenario.allowed_tools or READ_ONLY_TOOLS)}'))
        self._refresh_boundary()
        self._refresh_buttons()

    def _load_models(self):
        from tools.intranet_llm import list_enabled_items
        self.model_combo.clear()
        self.model_combo.addItem(self._t('请选择模型', 'Select a model'), '')
        try:
            for item in list_enabled_items():
                self.model_combo.addItem(str(item.get('name') or item.get('model')), item['id'])
        except Exception:
            pass
        if self.scenario.model_config_id:
            index = self.model_combo.findData(self.scenario.model_config_id)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)

    def _load_materials(self):
        self.materials.clear()
        self.materials.addItems(list(self.scenario.log_materials))

    def _pick_root(self):
        folder = QFileDialog.getExistingDirectory(self, self._t('选择代码根目录', 'Select code root'),
                                                  self.code_root_edit.text() or os.getcwd())
        if folder:
            self.code_root_edit.setText(folder)

    def _add_material(self):
        paths, _ = QFileDialog.getOpenFileNames(self, self._t('选择日志文件', 'Select log files'))
        for path in paths:
            if path and not self.materials.findItems(path, Qt.MatchFlag.MatchExactly):
                self.materials.addItem(path)

    def _remove_material(self):
        for item in self.materials.selectedItems():
            self.materials.takeItem(self.materials.row(item))

    def _current_model(self):
        return str(self.model_combo.currentData() or '')

    def _boundary(self):
        model_id = self._current_model()
        if not model_id:
            return classify_endpoint('')
        from tools.intranet_scenario import resolve_model_boundary
        return resolve_model_boundary(model_id)

    def _scope_key(self):
        """Everything the approval covers; any change requires a new approval."""
        scenario = self._scenario()
        boundary = self._boundary()
        return (
            scenario.model_config_id,
            boundary.host,
            scenario.code_root_resolved,
            tuple(sorted(str(item) for item in scenario.log_materials)),
            tuple(sorted(scenario.allowed_tools)),
        )

    def _on_scope_changed(self, *_args):
        self._invalidate_grant(clear_check=True)
        self._refresh_buttons()

    def _invalidate_grant(self, *, clear_check=True):
        """Drop the approval and revoke the grant bound to the old scope."""
        if clear_check:
            try:
                previous = self.confirm_check.blockSignals(True)
                self.confirm_check.setChecked(False)
                self.confirm_check.blockSignals(previous)
            except RuntimeError:
                pass
        if self.run_id:
            auth.revoke(self.run_id)
        self.run_id = ''
        self._token = None
        self.granted_scope = None

    def _refresh_boundary(self, *_args):
        self._invalidate_grant(clear_check=True)
        boundary = self._boundary()
        color = '#1a7f37' if boundary.is_intranet else '#b42318'
        self.boundary_label.setStyleSheet(f'color:{color}; font-weight:600')
        self.boundary_label.setText(boundary.label(self.zh))
        self._refresh_buttons()

    def _scenario(self):
        materials = [self.materials.item(i).text() for i in range(self.materials.count())]
        return IntranetScenario(
            system_name=self.system_edit.text().strip(),
            code_root=self.code_root_edit.text().strip(),
            log_materials=tuple(materials),
            allowed_tools=tuple(self.scenario.allowed_tools or READ_ONLY_TOOLS),
            model_config_id=self._current_model(),
            max_rounds=self.scenario.max_rounds,
            max_tool_calls=self.scenario.max_tool_calls,
            max_seconds=self.scenario.max_seconds,
            max_input_chars=self.scenario.max_input_chars,
        )

    def _preview_materials(self):
        scenario = self._scenario()
        try:
            reader = MaterialReader(scenario.log_materials, scenario.code_root_resolved)
            rows = reader.describe()
        except Exception as exc:
            QMessageBox.warning(self, self._t('预览失败', 'Preview failed'), str(exc))
            return
        if not rows:
            self.output.setPlainText(self._t('尚未选择日志材料。', 'No material selected.'))
            return
        lines = [self._t('将发送的材料（含截断情况）：', 'Payload to send (with truncation):')]
        for row in rows:
            if not row.get('ok', True):
                lines.append(f"- {row['path']}  ⚠ {row.get('reason')}")
                continue
            mark = self._t('将截断', 'truncated') if row['will_truncate'] else self._t('完整', 'full')
            outside = self._t('（代码根外，已授权）', ' (outside root, approved)') if row['outside_root'] else ''
            lines.append(f"- {row['label']}: {row['bytes']} bytes / 预算 {row['budget_chars']} 字符 / {mark}{outside}")
        lines.append('')
        lines.append(self._t(
            f'注意：代码工具只能读取 {scenario.code_root_resolved}，'
            '但工具返回的源码片段会随工具结果一并发送给模型。',
            f'Note: code tools can only read {scenario.code_root_resolved}, '
            'but the source snippets they return are sent to the model with the tool result.'))
        self.output.setPlainText('\n'.join(lines))

    def _refresh_buttons(self, *_args):
        model_ready = bool(self._current_model())
        has_material = self.materials.count() > 0
        boundary = self._boundary()
        needs_confirm = bool(model_ready) and boundary.requires_confirmation
        self.confirm_check.setEnabled(needs_confirm)
        self.confirm_btn.setEnabled(needs_confirm and model_ready)
        # A checked box is not enough: the recorded grant must match this scope.
        scope_approved = bool(self.run_id) and self.granted_scope == self._scope_key()
        approved = True if not needs_confirm else (
            self.confirm_check.isChecked() and scope_approved)
        self.run_btn.setEnabled(bool(model_ready and has_material and approved))

    def _grant(self):
        scenario = self._scenario()
        boundary = self._boundary()
        if not self.confirm_check.isChecked():
            QMessageBox.warning(self, self._t('需要勾选确认', 'Approval required'),
                                self._t('请先勾选确认，再记录授权。',
                                        'Tick the approval box before recording it.'))
            return
        # One run id per approved scope: the run reuses this grant instead of
        # issuing a second one.
        run_id = self.run_id or uuid.uuid4().hex
        grant = auth.issue(run_id, model_config_id=scenario.model_config_id,
                           host=boundary.host, materials=scenario.log_materials,
                           code_root=scenario.code_root_resolved,
                           allowed_tools=scenario.allowed_tools,
                           boundary_kind=boundary.kind)
        self.run_id = run_id
        self.granted_scope = self._scope_key()
        self.granted_scope_summary = grant.summary()
        self.output.setPlainText(self._t(
            f'已记录授权\n目标: {grant.host}\n代码根: {grant.code_root}\n'
            f'工具: {", ".join(grant.allowed_tools)}\n材料: {len(grant.materials)} 份\n'
            '模型、地址、代码根或材料一旦变化，授权作废并需重新确认。',
            f'Authorized\nHost: {grant.host}\nCode root: {grant.code_root}\n'
            f'Tools: {", ".join(grant.allowed_tools)}\nMaterials: {len(grant.materials)}\n'
            'Changing the model, host, code root or materials revokes it.'))
        self._refresh_buttons()

    def _run(self):
        if self._closing:
            return  # closing: never start another model request
        if self.worker is not None:
            return
        scenario = self._scenario()
        errors = scenario.validate()
        if errors:
            QMessageBox.warning(self, self._t('场景不完整', 'Incomplete scenario'), '；'.join(errors))
            return
        boundary = self._boundary()
        if boundary.requires_confirmation:
            if not self.confirm_check.isChecked():
                QMessageBox.warning(self, self._t('需要确认外发', 'Approval required'),
                                    self._t('该目标不是可证内网，请先勾选确认并记录授权。',
                                            'This target is not provably intranet; approve it first.'))
                return
            if not self.run_id or self.granted_scope != self._scope_key():
                QMessageBox.warning(self, self._t('需要重新确认', 'Re-approval required'),
                                    self._t('模型、地址、代码根或材料已变化，请重新点「记录本次授权」。',
                                            'Model, host, code root or materials changed; record approval again.'))
                return
            try:
                auth.check(self.run_id, model_config_id=scenario.model_config_id,
                           host=boundary.host, materials=scenario.log_materials,
                           code_root=scenario.code_root_resolved,
                           allowed_tools=scenario.allowed_tools)
            except auth.OutboundNotAuthorized as exc:
                QMessageBox.warning(self, self._t('需要重新确认', 'Re-approval required'), exc.reason)
                self._invalidate_grant()
                return
        else:
            # Provably intranet: no approval needed, but every run is its own id.
            self.run_id = uuid.uuid4().hex
            self.granted_scope = None
        try:
            save_scenario(scenario)
        except ValueError:
            pass
        self.scenario = scenario
        self.output.setPlainText(self._t('开始只读排障…', 'Running read-only diagnostics…'))
        from tools.agent_core.contracts import CancellationToken
        token = CancellationToken()  # created before the thread starts
        self._token = token
        self.worker = _RunWorker(scenario, self.question_edit.toPlainText().strip() or '请分析所选日志',
                                 scenario.model_config_id, self.run_id, token, self)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def _stop(self):
        if self._closing:
            return
        worker = self.worker
        if worker is not None:
            # Void this run id: anything that arrives later is dropped, and the
            # pending placeholder is cleared instead of being appended to.
            self._void_runs.add(self.run_id)
            worker.cancel()
            self.stop_btn.setEnabled(False)
            self.output.setPlainText(self._t('已停止，本轮结果不再追加。',
                                             'Stopped; this run will not append further output.'))
        self._invalidate_grant()

    def _on_done(self, outcome, run_id):
        if self._closing:
            return
        if run_id != self.run_id or run_id in self._void_runs:
            return  # a late result from an earlier or stopped run never lands here
        lines = [
            f"状态: {getattr(getattr(outcome.result, 'status', None), 'value', '')}",
            f"目标: {outcome.boundary_label(self.zh)}",
            f"材料: {'; '.join(outcome.materials) or '（无）'}",
            f"工具: {', '.join(outcome.tool_names) or '（未调用）'}",
            '',
        ]
        if outcome.missing_sections:
            lines.append('缺少段落: ' + '、'.join(outcome.missing_sections))
        lines.append(outcome.text)
        self.output.setPlainText('\n'.join(lines))

    def _on_failed(self, message, run_id):
        if self._closing:
            return
        if run_id != self.run_id or run_id in self._void_runs:
            return
        self.output.setPlainText(self._t(f'运行失败：{message}', f'Run failed: {message}'))

    def _on_finished(self):
        if self._closing:
            return
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        self.stop_btn.setEnabled(False)
        self._refresh_buttons()

    def _shutdown(self):
        """Cancel and retire the worker without ever destroying a live QThread."""
        worker, self.worker = self.worker, None
        if worker is None:
            return
        self._void_runs.add(self.run_id)
        worker.cancel()
        if worker.wait(2500):
            worker.deleteLater()
            return
        # Still running: detach it from this dialog so closing the window cannot
        # destroy a live thread, and let it clean itself up when it finishes.
        worker.setParent(None)
        _ORPHANS.add(worker)
        worker.finished.connect(lambda w=worker: (_ORPHANS.discard(w), w.deleteLater()))

    def _teardown(self):
        """Single idempotent exit path for Esc, the X button and window close."""
        if self._tearing_down:
            return
        self._tearing_down = True
        self._closing = True
        self._void_runs.add(self.run_id)
        try:
            self._invalidate_grant()
        except RuntimeError:
            pass
        self._shutdown()

    def done(self, result):
        # Esc and the X button both end here (QDialog.reject -> done), so this
        # is the one place that must clean up.
        self._teardown()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802
        self._teardown()
        super().closeEvent(event)
