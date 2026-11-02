"""Native AI/monitor workspace; credentials stay in the host service."""
from pathlib import Path
import time
import json
from collections import deque

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QComboBox, QLineEdit, QLabel, QCheckBox, QSpinBox, QSplitter, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QTabBar, QMessageBox,
    QFileDialog, QApplication, QMenu, QScrollArea, QGroupBox, QHeaderView)

from tools.log_analysis.service import LogService
from tools.log_analysis.report import Store, report, stamp
from ui.text_actions import install_text_actions
from ui.thinking_indicator import ThinkingIndicator
from .chat_view import ChatView


class Composer(QPlainTextEdit):
    submitted = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not self.composing:
                self.submitted.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class LogAnalysisWorkspace(QWidget):
    def __init__(self, server_loader, parent=None, *, store=None):
        super().__init__(parent)
        self.server_loader = server_loader
        if store is None:
            from config import local_data_dir
            store = Store(Path(local_data_dir()) / 'log_analysis' / 'v1')
        self.store = store
        self.service = None
        self.snapshots = {}
        self.chats = {}
        self.reports = {}
        self.report_baselines = {}
        self.selected_id = ''
        self.restored = {s['id']: s for s in store.load()}
        self._setup()
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def _setup(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        modes = QHBoxLayout()
        modes.addWidget(QLabel('排查方式'))
        self.mode = QTabBar()
        self.mode.setExpanding(False)
        for text, key, hint in [('历史日志分析', 'history', '选择本地或服务器文件，按启动时边界读取'),
                                ('Linux 实时监控', 'system', '持续观察系统资源、服务、进程和端口，日志可选'),
                                ('仅实时日志', 'live', '持续读取所选文件的新增内容')]:
            index = self.mode.addTab(text)
            self.mode.setTabData(index, key)
            self.mode.setTabToolTip(index, hint)
        modes.addWidget(self.mode, 1)
        root.addLayout(modes)
        target_toggle = QPushButton('收起设置')
        target_toggle.setCheckable(True)
        target_toggle.setChecked(True)
        controls = QHBoxLayout()
        controls.addWidget(target_toggle)
        controls.addStretch()
        root.addLayout(controls)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.split, 1)
        left = QWidget()
        target_layout = QVBoxLayout(left)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        target_layout.addLayout(form)
        target_layout.addStretch(1)
        self.form = form
        self.source = QComboBox()
        self.source.addItem('本地日志文件', 'local')
        self.source.addItem('服务器日志文件', 'remote')
        form.addRow('历史文件来源', self.source)
        self.files = QPlainTextEdit()
        self.files.setPlaceholderText('选择文件，或粘贴完整路径（每行一个，最多20个）')
        self.files.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.files.setMaximumHeight(76)
        form.addRow('文件清单', self.files)
        self.browse_btn = QPushButton('选择 / 浏览日志文件…')
        self.browse_btn.clicked.connect(self.browse_files)
        form.addRow(self.browse_btn)
        self.tail_lines = QSpinBox()
        self.tail_lines.setRange(0, 100000)
        self.tail_lines.setSpecialValueText('整个文件（启动时边界）')
        self.tail_lines.setSuffix(' 行（尾部）')
        form.addRow('历史读取范围', self.tail_lines)
        self.use_time = QCheckBox('按日志时间筛选（支持带年月日的标准时间）')
        form.addRow(self.use_time)
        self.time_options = QWidget()
        time_form = QFormLayout(self.time_options)
        time_form.setContentsMargins(0, 0, 0, 0)
        self.time_start = QLineEdit(); self.time_start.setPlaceholderText('2026-10-29 08:00:00')
        self.time_end = QLineEdit(); self.time_end.setPlaceholderText('2026-10-29 09:00:00')
        self.timezone = QSpinBox(); self.timezone.setRange(-720, 840); self.timezone.setSingleStep(30); self.timezone.setValue(480)
        self.timezone.setSuffix(' 分钟（相对UTC）')
        time_form.addRow('起始时间', self.time_start); time_form.addRow('结束时间', self.time_end); time_form.addRow('日志时区', self.timezone)
        form.addRow(self.time_options)
        self.servers = QComboBox()
        self.services = QComboBox()
        self.path = QLineEdit()
        self.path.setPlaceholderText('/明确的日志文件路径/app.log')
        self.encoding = QComboBox()
        self.encoding.addItems(['utf-8', 'gbk'])
        self.interval = QSpinBox()
        self.interval.setRange(1, 30)
        self.interval.setValue(2)
        self.interval.setSuffix(' 秒')
        self.backfill = QCheckBox('补读尾部最多256 KiB')
        self.warn = QCheckBox('包含 WARN')
        self.tasks = QComboBox()
        self.tasks.addItem('请选择或启动任务', '')
        for ident, snapshot in self.restored.items():
            self.tasks.addItem('待恢复 · ' + snapshot['service'], ident)
            self.snapshots[ident] = snapshot
        self.models = QComboBox()
        self.models.addItem('请选择内网模型', '')
        self.model_consent = QCheckBox('确认使用所选内网模型')
        self.model_consent.setToolTip('只使用明确选择的配置，不自动切换模型。请核对配置地址。')
        self.auto = QCheckBox('自动分析新异常')
        self.auto.setToolTip('同类异常至少间隔60秒；全模块仅一个模型请求并发。')
        self.auto.toggled.connect(self._auto_changed)
        self.model_consent.toggled.connect(lambda enabled: self.auto.setChecked(False) if not enabled else None)
        for label, widget in [('服务器', self.servers), ('服务', self.services), ('日志文件', self.path),
                              ('编码', self.encoding), ('采集周期', self.interval)]:
            form.addRow(label, widget)
        modes.addWidget(QLabel('任务'))
        self.tasks.setMinimumWidth(150)
        self.tasks.setMaximumWidth(250)
        modes.addWidget(self.tasks)
        self.with_logs = QCheckBox('同时跟踪所选日志文件')
        form.addRow(self.with_logs)
        self.with_logs.toggled.connect(self.mode_changed)
        self.system_options = QGroupBox('系统监控对象与阈值')
        self.system_advanced = QPushButton('高级设置')
        self.system_advanced.setCheckable(True)
        self.system_advanced.toggled.connect(self.mode_changed)
        form.addRow(self.system_advanced)
        system_form = QFormLayout(self.system_options)
        self.units = QLineEdit()
        self.units.setPlaceholderText('例如 nginx.service, app.service（可空）')
        self.processes = QLineEdit()
        self.processes.setPlaceholderText('进程名，例如 java, nginx（可空）')
        self.ports = QLineEdit()
        self.ports.setPlaceholderText('例如 8080, 8443（可空）')
        self.mounts = QLineEdit('/')
        self.cpu_threshold = QSpinBox(); self.cpu_threshold.setRange(1, 100); self.cpu_threshold.setValue(85)
        self.memory_threshold = QSpinBox(); self.memory_threshold.setRange(1, 100); self.memory_threshold.setValue(10)
        self.disk_threshold = QSpinBox(); self.disk_threshold.setRange(1, 100); self.disk_threshold.setValue(85)
        self.duration = QSpinBox(); self.duration.setRange(0, 3600); self.duration.setValue(60)
        for label, widget in [('systemd服务', self.units), ('关键进程', self.processes), ('监听端口', self.ports),
                              ('磁盘路径', self.mounts), ('CPU告警 %', self.cpu_threshold), ('可用内存低于 %', self.memory_threshold),
                              ('磁盘/inode告警 %', self.disk_threshold), ('持续秒数', self.duration)]:
            system_form.addRow(label, widget)
        form.addRow(self.system_options)
        for widget in (self.backfill, self.warn):
            form.addRow(widget)
        self.more_btn = QPushButton('更多')
        more_menu = QMenu(self.more_btn)
        self.more_btn.setMenu(more_menu)
        self.control_buttons = {}
        for text, callback in [('刷新目标与模型', self.reload), ('开始监控 / AI取证', self.start),
                               ('暂停 / 恢复采集', self.pause), ('暂停 AI 分析', lambda: self.cancel_analysis(disable_auto=True)),
                               ('停止任务', self.stop), ('恢复所选历史任务', self.resume),
                               ('清理事件缓存…', self.clear_cache)]:
            button = QPushButton(text)
            self.control_buttons[text] = button
            button.clicked.connect(callback)
            if text in ('开始监控 / AI取证', '暂停 / 恢复采集', '停止任务'):
                controls.addWidget(button)
            else:
                button.hide()
                more_menu.addAction({'暂停 AI 分析': '停止分析及自动派发', '恢复所选历史任务': '打开缓存任务'}.get(text, text), button.click)
        controls.addWidget(self.more_btn)
        self.notice = notice = QLabel('实时监控仅在软件运行时进行。\n历史扫描：最多20文件 / 200 MiB，支持文本和gz。\n指标无法获取时显示未知，不代表正常。\n只读查询；不执行修改或重启。')
        notice.setWordWrap(True)
        form.addRow(notice)
        self.target_scroll = QScrollArea()
        self.target_scroll.setWidgetResizable(True)
        self.target_scroll.setWidget(left)
        self.split.addWidget(self.target_scroll)
        target_toggle.toggled.connect(self.target_scroll.setVisible)
        target_toggle.toggled.connect(lambda checked: target_toggle.setText('收起设置' if checked else '展开设置'))
        center = QWidget()
        middle = QVBoxLayout(center)
        self.scope = QLabel('未选任务；先选择服务器、服务及具体文件。')
        self.scope.setWordWrap(True)
        middle.addWidget(self.scope)
        self.capture_waiting = ThinkingIndicator(self)
        self.capture_waiting.hide()
        middle.addWidget(self.capture_waiting)
        self.metric_summary = QLabel()
        self.metric_summary.setWordWrap(True)
        middle.addWidget(self.metric_summary)
        self.events = QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(['异常', '次数', '最近观察', '状态'])
        self.events.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.events.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.events.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.events.setColumnWidth(2, 170)
        self.events.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.events.customContextMenuRequested.connect(self.event_menu)
        self.events.itemSelectionChanged.connect(self.show_evidence)
        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(self.events, '异常事件')
        self.metric_view = QPlainTextEdit(); self.metric_view.setReadOnly(True)
        self.result_tabs.addTab(self.metric_view, '系统 / 服务 / 端口')
        self.recent_view = QPlainTextEdit(); self.recent_view.setReadOnly(True)
        self.result_tabs.addTab(self.recent_view, '已采集日志')
        self.coverage_view = QPlainTextEdit(); self.coverage_view.setReadOnly(True)
        self.result_tabs.addTab(self.coverage_view, '实际覆盖范围')
        middle.addWidget(self.result_tabs, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('选择异常查看脱敏堆栈、来源和前文')
        install_text_actions(self.details)
        middle.addWidget(self.details, 1)
        self.details.hide()
        self.result_tabs.currentChanged.connect(self.show_evidence)
        row = QHBoxLayout()
        for text, action in [('分析所选', self.analyze_selected)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            row.addWidget(button)
        report_btn = QPushButton('报告')
        report_menu = QMenu(report_btn)
        report_menu.addAction('生成证据汇总', self.make_report)
        report_menu.addAction('让 AI 整理报告', self.make_ai_report)
        report_menu.addAction('保存报告…', self.save_report)
        report_btn.setMenu(report_menu)
        row.addWidget(report_btn)
        middle.addLayout(row)
        self.split.addWidget(center)
        right = QWidget()
        box = QVBoxLayout(right)
        box.addWidget(QLabel('AI 助手'))
        self.ai_waiting = ThinkingIndicator(self)
        self.ai_waiting.hide()
        box.addWidget(self.ai_waiting)
        self.ai_activity = QLabel('尚未开始分析')
        self.ai_activity.setWordWrap(True)
        box.addWidget(self.ai_activity)
        box.addWidget(self.models)
        box.addWidget(self.model_consent)
        box.addWidget(self.auto)
        self.right_tabs = QTabWidget()
        self.chat = ChatView()
        self.draft = QPlainTextEdit()
        install_text_actions(self.chat)
        install_text_actions(self.draft)
        self.right_tabs.addTab(self.chat, 'AI 对话')
        self.right_tabs.addTab(self.draft, '报告草稿（可编辑）')
        box.addWidget(self.right_tabs, 1)
        self.jump = QPushButton('查看最新消息')
        self.jump.clicked.connect(lambda: self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum()))
        self.jump.hide()
        box.addWidget(self.jump)
        self.input = Composer()
        self.input.setPlaceholderText('描述问题；Enter发送，Shift+Enter换行。仅查询当前采集快照。')
        self.input.setMaximumHeight(100)
        install_text_actions(self.input)
        self.input.submitted.connect(self.send)
        box.addWidget(self.input)
        actions = QHBoxLayout()
        for text, callback in [('发送', self.send), ('停止分析', self.cancel_analysis)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        box.addLayout(actions)
        self.split.addWidget(right)
        self.split.setSizes([240, 450, 400])
        for i in range(3):
            self.split.widget(i).setMinimumWidth(0)
            self.split.setCollapsible(i, True)
        self.target_scroll.setMinimumWidth(320)
        self.status = QLabel('未启动 · 未连接服务器')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.servers.currentIndexChanged.connect(self.server_changed)
        self.services.currentIndexChanged.connect(lambda: self.path.setText(str(self.services.currentData() or '')))
        self.tasks.currentIndexChanged.connect(self.select_task)
        self.models.currentIndexChanged.connect(self._model_changed)
        self.mode.currentChanged.connect(self.mode_changed)
        self.source.currentIndexChanged.connect(self.mode_changed)
        self.use_time.toggled.connect(self.mode_changed)
        self.reload()
        self.mode_changed()

    def mode_changed(self, *_):
        mode = self.selected_mode()
        historical = mode == 'history'
        local = historical and self.source.currentData() == 'local'
        advanced = self.system_advanced.isChecked()
        self.system_advanced.setText('收起高级设置' if advanced else '高级设置')
        for widget, visible in ((self.source, historical), (self.files, historical), (self.tail_lines, historical and advanced),
                                (self.use_time, historical and advanced), (self.time_options, historical and advanced and self.use_time.isChecked()),
                                (self.servers, not local), (self.services, not local and (mode != 'system' or self.with_logs.isChecked())),
                                (self.browse_btn, mode != 'system' or self.with_logs.isChecked()),
                                (self.path, not historical and (mode == 'live' or self.with_logs.isChecked())),
                                (self.interval, not historical and advanced), (self.system_advanced, True),
                                (self.encoding, advanced), (self.warn, advanced), (self.notice, advanced),
                                (self.system_options, mode == 'system' and self.system_advanced.isChecked()),
                                (self.with_logs, mode == 'system'), (self.backfill, not historical and advanced)):
            self.form.setRowVisible(widget, visible)
        self.result_tabs.setTabVisible(1, mode == 'system')
        self.control_buttons['开始监控 / AI取证'].setText('读取文件' if historical else '开始监控')
        self.control_buttons['停止任务'].setText('停止采集')
        self.control_buttons['暂停 / 恢复采集'].setVisible(not historical)
        self.control_buttons['暂停 AI 分析'].setText('停止本次分析与自动派发')
        self.control_buttons['恢复所选历史任务'].setText('打开所选任务的缓存证据')
        self.interval.setMinimum(5 if mode == 'system' else 1)
        self.tail_lines.setEnabled(not self.use_time.isChecked())

    def selected_mode(self):
        return self.mode.tabData(self.mode.currentIndex())

    def browse_files(self):
        if self.selected_mode() == 'history' and self.source.currentData() == 'local':
            paths, _ = QFileDialog.getOpenFileNames(self, '选择历史日志文件', '', '日志 (*.log *.txt *.gz);;所有文件 (*)')
        else:
            if not self.servers.currentData():
                self.status.setText('请先选择服务器')
                return
            from .file_picker import RemoteFilePicker
            picker = RemoteFilePicker(self.servers.currentData(), self.path.text().strip() or '/', self)
            if picker.exec() != picker.DialogCode.Accepted:
                return
            paths = picker.selected_paths
        if paths:
            if self.selected_mode() == 'history':
                self.files.setPlainText('\n'.join(paths))
            else:
                self.path.setText(paths[0])
                self.with_logs.setChecked(True)

    @staticmethod
    def _values(widget):
        return [x.strip() for x in widget.text().replace('，', ',').split(',') if x.strip()]

    def reload(self):
        old = (self.servers.currentData() or {}).get('id')
        self.servers.clear()
        for server in self.server_loader():
            self.servers.addItem(str(server.get('name') or server.get('id')), server)
            if server.get('id') == old:
                self.servers.setCurrentIndex(self.servers.count() - 1)
        from tools.intranet_llm import list_enabled_items
        selected = self.models.currentData()
        self.models.clear()
        self.models.addItem('请选择内网模型', '')
        for item in list_enabled_items():
            self.models.addItem(str(item.get('name') or item.get('model')), item['id'])
            if item['id'] == selected:
                self.models.setCurrentIndex(self.models.count() - 1)

    def server_changed(self):
        from tools.ops_ssh import server_services
        self.services.clear()
        server = self.servers.currentData()
        if server:
            for svc in server_services(server, only_enabled=False):
                self.services.addItem(str(svc.get('name') or '默认'), str(svc.get('log_path') or ''))

    def start(self, _checked=False, restored=None):
        server = self.servers.currentData()
        path = self.path.text().strip()
        mode = self.selected_mode()
        local = mode == 'history' and self.source.currentData() == 'local'
        if not server and not local:
            self.status.setText('请先选择服务器。')
            return
        try:
            if self.service is None:
                self.service = LogService(self.store)
            options = {'encoding': self.encoding.currentText(), 'warn': self.warn.isChecked()}
            if mode == 'history':
                path = [line.strip() for line in self.files.toPlainText().splitlines() if line.strip()]
                options.update(local=local, tail_lines=0 if self.use_time.isChecked() else self.tail_lines.value())
                if self.use_time.isChecked():
                    if not self.time_start.text().strip() or not self.time_end.text().strip():
                        raise ValueError('请输入日志筛选的起止时间')
                    options.update(time_start=self.time_start.text(), time_end=self.time_end.text(), timezone_minutes=self.timezone.value())
            else:
                options.update(interval=self.interval.value(), backfill=256 * 1024 if self.backfill.isChecked() else 0)
                if mode == 'system':
                    path = path if self.with_logs.isChecked() else ''
                    options.update(units=self._values(self.units), processes=self._values(self.processes),
                                   ports=self._values(self.ports), mounts=self._values(self.mounts),
                                   cpu=self.cpu_threshold.value(), memory=self.memory_threshold.value(),
                                   disk=self.disk_threshold.value(), duration=self.duration.value())
            task = self.service.add(server or {'id': 'local', 'name': '本地文件'},
                                    self.services.currentText() if not local else '历史日志', path, mode=mode, **options)
            index = self.tasks.findData(task.id)
            if index < 0:
                self.tasks.addItem(self.mode.tabText(self.mode.currentIndex()) + ' · ' + task.label + ' / ' + task.service, task.id)
                index = self.tasks.count() - 1
            self.tasks.setCurrentIndex(index)
            self.select_task()
            self.result_tabs.setCurrentIndex(1 if mode == 'system' else 0)
        except Exception as error:
            self.status.setText(str(error) if isinstance(error, ValueError) else '启动失败：' + type(error).__name__)

    def select_task(self):
        if self.selected_id:
            self.reports[self.selected_id] = self.draft.toPlainText()
        self.selected_id = self.tasks.currentData() or ''
        self.events.clearSelection()
        self.details.clear()
        self.auto.blockSignals(True)
        self.auto.setChecked(bool(self.service and self.selected_id in self.service.auto))
        self.auto.blockSignals(False)
        self.chat.set_messages(self.chats.get(self.selected_id, []))
        self.draft.setPlainText(self.reports.get(self.selected_id, ''))
        self.input.clear()
        self.refresh()

    def current_task(self):
        return self.service.tasks.get(self.selected_id) if self.service else None

    def pause(self):
        task = self.current_task()
        if task:
            if hasattr(task, 'set_paused'):
                task.set_paused(not task.paused.is_set())
                return
            if task.paused.is_set():
                task.paused.clear()
            else:
                task.paused.set()

    def cancel_analysis(self, _checked=False, disable_auto=False):
        if self.service:
            self.service.cancel_analysis(self.selected_id, disable_auto=disable_auto)
        if disable_auto:
            self.auto.setChecked(False)
        self._refresh_analysis_activity()

    def stop(self):
        if self.current_task():
            self.service.stop(self.selected_id)
            self.refresh()

    def resume(self):
        snapshot = self.snapshots.get(self.selected_id)
        if not snapshot:
            self.scope.setText('未选任务；先选择服务器、服务及具体文件。')
            self.events.setRowCount(0)
            self.details.clear()
            return
        if self.service is None:
            self.service = LogService(self.store)
        try:
            self.service.open_snapshot(snapshot)
            self.status.setText('已打开缓存证据，可提问；未重新连接服务器或启动采集。')
        except ValueError as error:
            self.status.setText(str(error))

    def _model_changed(self):
        self.model_consent.setChecked(False)
        self.auto.setChecked(False)

    def _auto_changed(self, enabled):
        if self.service:
            if enabled and self.models.currentData() and self.model_consent.isChecked():
                self.service.auto[self.selected_id] = self.models.currentData()
            else:
                self.service.auto.pop(self.selected_id, None)
                with self.service.lock:
                    self.service.queue = deque((q for q in self.service.queue if q[0] != self.selected_id or q[4] != 'auto'), maxlen=50)
        if enabled and (not self.service or not self.models.currentData() or not self.model_consent.isChecked()):
            self.auto.setChecked(False)
            self.status.setText('先启动任务并选择、确认内网模型。')

    def append_chat(self, task_id, text, force=False, *, role='system'):
        message = {'role': role, 'text': str(text)}
        if len(message['text']) > 150000:
            message['text'] = message['text'][:150000] + '\n[本条显示已截断]'
        messages = self.chats.setdefault(task_id, [])
        messages.append(message)
        total = sum(len(row['text']) for row in messages)
        trimmed = False
        while len(messages) > 1 and (len(messages) > 200 or total > 150000):
            total -= len(messages.pop(0)['text'])
            trimmed = True
        if task_id != self.selected_id:
            return
        bar = self.chat.verticalScrollBar()
        near_end = bar.value() >= bar.maximum() - 20
        position = bar.value()
        if trimmed:
            self.chat.set_messages(messages)
        else:
            self.chat.append_message(message)
        if force or near_end:
            bar.setValue(bar.maximum())
            self.jump.hide()
        else:
            bar.setValue(position)
            self.jump.show()

    def send(self, _checked=False, event_id=None, question=None, kind='chat'):
        text = question or self.input.toPlainText().strip() or ('分析所选异常：' + event_id if event_id else '')
        if not text:
            return
        if not self.models.currentData() or not self.model_consent.isChecked():
            self.status.setText('请选择并确认内网模型。')
            return
        if not self.service or not self.service.enqueue(self.selected_id, text, self.models.currentData(), event_id, kind=kind):
            self.status.setText('请先读取文件、启动监控或打开缓存任务；分析队列可能已满。')
            return
        self.append_chat(self.selected_id, text, force=True, role='user')
        if question is None:
            self.input.clear()
        self.right_tabs.setCurrentIndex(0)
        self._refresh_analysis_activity()

    def selected_events(self):
        return [self.events.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)
                for row in self.events.selectionModel().selectedRows()]

    def analyze_selected(self):
        selected = self.selected_events()
        if selected:
            self.send(event_id=selected[0])

    def show_evidence(self, *_):
        selected = set(self.selected_events())
        snapshot = self.snapshots.get(self.selected_id, {})
        event = next((e for e in snapshot.get('events', []) if e['id'] in selected), None)
        self.details.setVisible(bool(event) and self.result_tabs.currentIndex() == 0)
        if event:
            self._set_preview(self.details, f"证据 {event['id']} · 来源 {event.get('source') or snapshot.get('path', '')}\n"
                              f"代次 {event['generation']} · 字节 {event['offset']}\n" + event['evidence'])
        else:
            self.details.clear()

    def event_menu(self, point):
        menu = QMenu(self)
        menu.addAction('复制所选堆栈', lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        selected = self.selected_events()
        event = next((e for e in self.snapshots.get(self.selected_id, {}).get('events', []) if e['id'] in selected), None)
        if event:
            menu.addAction('复制 traceId', lambda: QApplication.clipboard().setText(event.get('trace_id', '')))
        menu.addAction('查看前后文', self.show_evidence)
        menu.addAction('交给 AI 分析', self.analyze_selected)
        menu.addAction('所选事件生成报告', self.make_report)
        menu.exec(self.events.viewport().mapToGlobal(point))

    def make_report(self):
        snapshot = self.snapshots.get(self.selected_id)
        if not snapshot:
            return
        if self.draft.toPlainText() and QMessageBox.question(self, '重新生成报告', '当前草稿可能包含人工编辑，是否替换？') != QMessageBox.StandardButton.Yes:
            return
        self.draft.setPlainText(report(snapshot, self.selected_events() or None))
        self.right_tabs.setCurrentIndex(1)

    def make_ai_report(self):
        if self.draft.toPlainText() and QMessageBox.question(self, '生成AI报告', '完成后替换未再编辑的当前报告草稿？') != QMessageBox.StandardButton.Yes:
            return
        self.report_baselines[self.selected_id] = self.draft.toPlainText()
        self.send(question='请先查看覆盖范围、异常和可用系统指标，生成Markdown排查报告：范围、事实、证据、原因假设、处理建议和缺口。不能声称未读取的范围已检查。', kind='report')

    def save_report(self):
        if not self.draft.toPlainText().strip():
            return
        path, _ = QFileDialog.getSaveFileName(self, '保存排查报告', '日志排查报告.md', 'Markdown (*.md)')
        if not path:
            return
        # Native save dialog owns overwrite confirmation.
        try:
            Path(path).write_text(self.draft.toPlainText(), encoding='utf-8')
            self.status.setText('报告已保存')
        except OSError:
            self.status.setText('保存失败，请检查文件权限或磁盘空间。')

    def clear_cache(self):
        if self.service and any(t.thread and t.thread.is_alive() for t in self.service.tasks.values()):
            self.status.setText('请先停止监控任务再清理缓存。')
            return
        if QMessageBox.question(self, '清理日志缓存', f'删除 {self.store.root} 中的任务缓存？不删除远端日志或导出报告。') == QMessageBox.StandardButton.Yes:
            try:
                if self.service:
                    self.service.close()
                    if not self.service.join(12):
                        self.status.setText('后台任务仍在退出，暂未清理缓存，请稍后重试。')
                        return
                    self.service = None
                self.store.clear()
                self.status.setText('缓存已清理')
            except OSError:
                self.status.setText('缓存清理失败，请检查目录权限。')

    def refresh(self):
        self._refresh_analysis_activity()
        task = self.current_task()
        capturing = bool(task and task.thread and task.thread.is_alive() and not task.stop_event.is_set()
                         and task.state.startswith(('连接中', '重连中', '校验文件', '读取文件')))
        if capturing:
            self.capture_waiting.set_text(task.state + '…')
            self.capture_waiting.start()
        else:
            self.capture_waiting.stop()
            self.capture_waiting.hide()
        if self.service:
            for task_id, task in list(self.service.tasks.items()):
                snapshot = task.snapshot(blocking=False)
                if snapshot is not None:
                    self.snapshots[task_id] = snapshot
            for task_id, text, kind in self.service.drain():
                self.append_chat(task_id, text, role='assistant')
                if kind == 'report':
                    current = self.draft.toPlainText() if task_id == self.selected_id else self.reports.get(task_id, '')
                    if current == self.report_baselines.get(task_id):
                        self.reports[task_id] = text
                        if task_id == self.selected_id:
                            self.draft.setPlainText(text)
                            self.right_tabs.setCurrentIndex(1)
                    else:
                        self.append_chat(task_id, '报告生成期间草稿已被编辑，新报告保留在对话中，未覆盖草稿。')
        snapshot = self.snapshots.get(self.selected_id)
        if not snapshot:
            self.scope.setText('任务已启动，等待首次采集结果。' if self.current_task() else
                               '历史分析：选择文件后开始读取。Linux监控：选择服务器即可开始，日志文件可选。')
            self.metric_summary.clear()
            self.events.setRowCount(0)
            self.details.clear()
            return
        mode = snapshot.get('mode', 'live')
        mode_label = {'history': '历史日志分析', 'system': 'Linux实时监控', 'live': '实时日志'}.get(mode, mode)
        self.scope.setText(f"{mode_label} · {snapshot['label']} / {snapshot['service']}\n"
                           + snapshot.get('coverage', '仅覆盖已采集证据') + ' · ' + snapshot['state'])
        self.scope.setToolTip(snapshot['path'])
        metrics = snapshot.get('metrics', [])
        latest = metrics[-1] if metrics else {}
        def show(key):
            value = latest.get(key)
            return '未知/预热' if value is None else str(value) + '%'
        self.metric_summary.setText(f"CPU {show('cpu_percent')}  ·  可用内存 {show('memory_available_percent')}  ·  上次采样 {stamp(latest.get('time'))}" if mode == 'system' else '')
        self._set_preview(self.metric_view, self._format_metrics(metrics[-1] if metrics else None, snapshot))
        self._set_preview(self.recent_view, '\n'.join(f'[{g}:{offset}] {line}' for g, offset, line in snapshot.get('recent', [])), follow_new=True)
        self._set_preview(self.coverage_view, json.dumps({k: snapshot.get(k) for k in
                          ('mode', 'path', 'sources', 'coverage', 'bytes', 'lines_read', 'complete', 'time_filter', 'last_poll', 'targets', 'notices')}, ensure_ascii=False, indent=2))
        selected = set(self.selected_events())
        scroll_position = self.events.verticalScrollBar().value()
        self.events.blockSignals(True)
        self.events.setRowCount(len(snapshot['events']))
        for row, event in enumerate(snapshot['events']):
            for col, value in enumerate((event['title'], event['count'], stamp(event['last_seen']),
                                          '已分析' if event.get('analyses') else '待分析')):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                item.setData(Qt.ItemDataRole.UserRole, event['id'])
                self.events.setItem(row, col, item)
                if event['id'] in selected:
                    item.setSelected(True)
        self.events.blockSignals(False)
        self.events.verticalScrollBar().setValue(scroll_position)
        self.show_evidence()
        extra = '' if not self.service else f" · {self.service.analysis_activity(self.selected_id)['state']} · {self.service.cache_state}"
        self.status.setText(f"{snapshot['state']} · 最近接收 {stamp(snapshot.get('last_received'))} · 积压{snapshot['backlog']}字节"
                            f" · 淘汰{snapshot['dropped_events']}组 · 超长行{snapshot['truncated_lines']}" + extra
                            + ('\n' + snapshot['notices'][-1] if snapshot.get('notices') else ''))

    def _refresh_analysis_activity(self):
        activity = self.service.analysis_activity(self.selected_id) if self.service else None
        if activity and activity['busy']:
            text = activity['state'] + f" · {activity['elapsed']}秒"
            if activity['queued']:
                text += f" · 本任务排队{activity['queued']}"
            self.ai_waiting.set_text('正在取消…' if '取消' in activity['state'] else '正在处理，请稍候…')
            self.ai_waiting.start()
            self.ai_activity.setText(text)
        else:
            self.ai_waiting.stop()
            self.ai_waiting.hide()
            text = activity['state'] if activity else '尚未开始分析'
            if text == '尚未开始分析':
                text = ('AI未启用；采集和证据汇总可独立使用' if not self.models.currentData() or not self.model_consent.isChecked()
                        else 'AI就绪；发送问题或开启自动分析新异常')
            self.ai_activity.setText(text)

    @staticmethod
    def _set_preview(widget, text, *, follow_new=False):
        if widget.toPlainText() == text:
            return
        bar = widget.verticalScrollBar()
        follow, position = follow_new and bar.value() >= bar.maximum() - 20, bar.value()
        widget.setPlainText(text)
        bar.setValue(bar.maximum() if follow else position)

    @staticmethod
    def _format_metrics(sample, snapshot):
        if not sample:
            return '暂无系统采样。Linux实时监控启动后显示；CPU需要至少两次有效采样。'
        def value(key, suffix=''):
            val = sample.get(key)
            return '未知 / 预热中' if val is None else str(val) + suffix
        lines = [f"采样时间：{stamp(sample['time'])}", f"CPU：{value('cpu_percent', '%')}",
                 f"可用内存：{value('memory_available_percent', '%')}", f"系统负载：{value('load')}",
                 f"逻辑CPU：{value('cpu_count')}", f"交换区已用：{value('swap_used_kib', ' KiB')}", '', '磁盘 / inode']
        for disk in sample['disks']:
            lines.append(f"{disk['path']}  空间：{disk['used_percent'] if disk['used_percent'] is not None else '未知'}%  inode：{disk['inode_percent'] if disk['inode_percent'] is not None else '未知'}%")
        lines += ['', '服务状态'] + [f"{k}：{v if v is not None else '未知'}" for k, v in sample['units'].items()]
        lines += ['', '关键进程'] + [f"{k}：{str(len(v)) + '个' if v is not None else '未知'}" for k, v in sample['processes'].items()]
        lines += ['', '监听端口（监听不代表业务健康）'] + [f"{k}：{'未知' if v is None else '监听中' if v else '未监听'}" for k, v in sample['ports'].items()]
        lines += ['', '资源进程（ps累计CPU占比 / RSS KiB）'] + [f"{p['pid']} {p['name']}  {p['cpu_percent']}% / {p['rss_kib']}" for p in sample['top_processes']]
        lines += ['', '无法采集的项目'] + [f'{k}：{v}' for k, v in sample['errors'].items()]
        lines += ['', f"保留最近{len(snapshot.get('metrics', []))}次采样；AI可查询最近60次。"]
        return '\n'.join(lines)

    def shutdown(self):
        self.timer.stop()
        self.ai_waiting.stop()
        self.ai_waiting.hide()
        self.capture_waiting.stop()
        self.capture_waiting.hide()
        if self.service:
            self.service.close()
            self.service.join(12)

    def resizeEvent(self, event):
        from ui.layout_metrics import BP_NARROW
        # Use the existing breakpoint; user can reopen the collapsible target rail.
        narrow = event.size().width() < BP_NARROW
        if narrow != getattr(self, '_narrow', None):
            self._narrow = narrow
            self.target_scroll.setVisible(not narrow)
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
