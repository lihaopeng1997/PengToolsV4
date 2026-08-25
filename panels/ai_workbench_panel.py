# -*- coding: utf-8 -*-
"""模型工作台：精简 Navicat 式查库 + 自然语言查询。界面不出现 harness。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tools.db_connect import (
    DIALECTS, DEFAULT_PORTS, PAGE_SIZE, DbError, delete_connection, list_tables,
    load_connections, open_connection, close_connection, run_read_query,
    schema_summary, upsert_connection,
)
from tools.intranet_llm import is_enabled, load_ai_local
from tools.sql_guard import is_read_query, reject_reason
from ui.confirm_dialog import confirm_action, show_error, show_info, show_warning
from ui.design_system import apply_button, apply_table
from ui.field_metrics import size_enum_combo, size_line, size_pick_combo
from ui.page_chrome import make_page_header


class _DbWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, kind: str, item: dict, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = item
        self.kwargs = kwargs

    def run(self):
        conn = None
        try:
            conn = open_connection(self.item)
            dialect = str(self.item.get('dialect') or 'oracle')
            if self.kind == 'test':
                tables = list_tables(conn, dialect)
                self.completed.emit({'ok': True, 'tables': tables})
            elif self.kind == 'tables':
                self.completed.emit({'tables': list_tables(conn, dialect)})
            elif self.kind == 'schema':
                self.completed.emit({'summary': schema_summary(conn, dialect)})
            elif self.kind == 'query':
                result = run_read_query(
                    conn, dialect, self.kwargs.get('sql') or '',
                    offset=int(self.kwargs.get('offset') or 0),
                    limit=int(self.kwargs.get('limit') or PAGE_SIZE),
                )
                self.completed.emit(result)
            else:
                raise DbError(f'未知任务：{self.kind}')
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            close_connection(conn)


class _NlWorker(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, prompt: str, context: str, cfg):
        super().__init__()
        self.prompt = prompt
        self.context = context
        self.cfg = cfg

    def run(self):
        try:
            from tools.ptools_harness import run_task
            sql = run_task('sql.draft', self.prompt, context=self.context, cfg=self.cfg)
            self.completed.emit(str(sql or ''))
        except Exception as exc:
            self.failed.emit(str(exc))


class _ConnectionDialog(QDialog):
    def __init__(self, language='zh', item=None, parent=None):
        super().__init__(parent)
        self.language = language
        zh = language == 'zh'
        self.setWindowTitle('编辑连接' if zh else 'Edit connection')
        self.setMinimumWidth(460)
        self._item = dict(item or {})
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(str(self._item.get('name') or ''))
        size_line(self.name, 'path')
        self.dialect = QComboBox()
        for key, label in DIALECTS:
            self.dialect.addItem(label, key)
        index = self.dialect.findData(str(self._item.get('dialect') or 'oracle'))
        self.dialect.setCurrentIndex(index if index >= 0 else 0)
        size_enum_combo(self.dialect)
        self.dialect.currentIndexChanged.connect(self._on_dialect_changed)
        self.host = QLineEdit(str(self._item.get('host') or ''))
        size_line(self.host, 'path')
        self.port = QLineEdit(str(self._item.get('port') or DEFAULT_PORTS['oracle']))
        size_line(self.port, 'std')
        self.database = QLineEdit(str(self._item.get('database') or ''))
        self.database.setPlaceholderText('SID / Service / 库名')
        size_line(self.database, 'path')
        self.username = QLineEdit(str(self._item.get('username') or ''))
        size_line(self.username, 'path')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        size_line(self.password, 'path')
        form.addRow('名称' if zh else 'Name', self.name)
        form.addRow('类型' if zh else 'Type', self.dialect)
        form.addRow('主机' if zh else 'Host', self.host)
        form.addRow('端口' if zh else 'Port', self.port)
        self.database_label = QLabel('库名' if zh else 'Database')
        form.addRow(self.database_label, self.database)
        form.addRow('用户' if zh else 'User', self.username)
        form.addRow('密码' if zh else 'Password', self.password)
        root.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('取消' if zh else 'Cancel')
        apply_button(cancel, 'secondary', compact=True)
        cancel.clicked.connect(self.reject)
        ok = QPushButton('保存' if zh else 'Save')
        apply_button(ok, 'primary', compact=True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)
        self._on_dialect_changed()

    def _on_dialect_changed(self):
        dialect = self.dialect.currentData() or 'oracle'
        zh = self.language == 'zh'
        if dialect == 'oracle':
            self.database_label.setText('SID/服务名' if zh else 'SID')
            self.database.setPlaceholderText('ORCL / 服务名')
        elif dialect == 'dameng':
            self.database_label.setText('模式/库名' if zh else 'Schema')
            self.database.setPlaceholderText('')
        else:
            self.database_label.setText('库名' if zh else 'Database')
            self.database.setPlaceholderText('mysql 库名，例如 test')
        if not self.port.text().strip() or self.port.text().strip() in {'1521', '2881', '3306', '5236'}:
            self.port.setText(str(DEFAULT_PORTS.get(dialect, 3306 if dialect == 'mysql' else 1521)))

    def payload(self) -> tuple[dict, str]:
        item = dict(self._item)
        item['name'] = self.name.text().strip()
        item['dialect'] = self.dialect.currentData() or 'oracle'
        item['host'] = self.host.text().strip()
        try:
            item['port'] = int(self.port.text().strip() or DEFAULT_PORTS.get(item['dialect'], 1521))
        except ValueError:
            item['port'] = DEFAULT_PORTS.get(item['dialect'], 1521)
        item['database'] = self.database.text().strip()
        item['username'] = self.username.text().strip()
        return item, self.password.text()


class AiWorkbenchPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._worker = None
        self._nl_worker = None
        self._schema_text = ''
        self._last_sql = ''
        self._offset = 0
        self._has_more = False
        self._setup_ui()
        self.set_language(language)
        self._reload_connections()
        self._refresh_model_status()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        header, self.page_title, self.page_subtitle = make_page_header(
            '模型工作台',
            '自然语言查库，结果在下方表格展示',
            'database',
        )
        root.addWidget(header)

        top = QFrame()
        top.setObjectName('sql-delivery-zone')
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(12, 8, 12, 8)
        top_l.setSpacing(8)
        self.conn_combo = QComboBox()
        size_pick_combo(self.conn_combo)
        self.conn_new_btn = QPushButton()
        apply_button(self.conn_new_btn, 'secondary', compact=True)
        self.conn_new_btn.clicked.connect(self._edit_connection)
        self.conn_del_btn = QPushButton()
        apply_button(self.conn_del_btn, 'ghost', compact=True)
        self.conn_del_btn.clicked.connect(self._delete_connection)
        self.test_btn = QPushButton()
        apply_button(self.test_btn, 'secondary', compact=True)
        self.test_btn.clicked.connect(lambda: self._start_db('test'))
        self.sync_btn = QPushButton()
        apply_button(self.sync_btn, 'secondary', compact=True)
        self.sync_btn.clicked.connect(lambda: self._start_db('tables'))
        self.model_status = QLabel()
        self.model_status.setObjectName('field-hint')
        top_l.addWidget(self.conn_combo, 1)
        top_l.addWidget(self.conn_new_btn)
        top_l.addWidget(self.conn_del_btn)
        top_l.addWidget(self.test_btn)
        top_l.addWidget(self.sync_btn)
        top_l.addWidget(self.model_status, 1)
        root.addWidget(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName('dashboard-task-card')
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        self.table_title = QLabel()
        self.table_title.setObjectName('section-title')
        left_l.addWidget(self.table_title)
        self.table_list = QListWidget()
        self.table_list.itemDoubleClicked.connect(self._on_table_activated)
        left_l.addWidget(self.table_list, 1)
        split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)
        nl_row = QHBoxLayout()
        self.nl_input = QLineEdit()
        size_line(self.nl_input, 'path')
        self.nl_input.returnPressed.connect(self._run_natural_query)
        self.nl_run_btn = QPushButton()
        apply_button(self.nl_run_btn, 'primary', compact=True)
        self.nl_run_btn.clicked.connect(self._run_natural_query)
        self.nl_sql_btn = QPushButton()
        apply_button(self.nl_sql_btn, 'secondary', compact=True)
        self.nl_sql_btn.clicked.connect(lambda: self._run_natural_query(sql_only=True))
        nl_row.addWidget(self.nl_input, 1)
        nl_row.addWidget(self.nl_run_btn)
        nl_row.addWidget(self.nl_sql_btn)
        right_l.addLayout(nl_row)

        self.sql_edit = QPlainTextEdit()
        self.sql_edit.setFont(QFont('Consolas', 10))
        self.sql_edit.setPlaceholderText('SELECT ...')
        self.sql_edit.setMinimumHeight(120)
        right_l.addWidget(self.sql_edit, 1)

        sql_row = QHBoxLayout()
        self.run_sql_btn = QPushButton()
        apply_button(self.run_sql_btn, 'primary', compact=True)
        self.run_sql_btn.clicked.connect(lambda: self._run_sql(reset=True))
        self.next_btn = QPushButton()
        apply_button(self.next_btn, 'secondary', compact=True)
        self.next_btn.clicked.connect(self._fetch_next)
        self.next_btn.setEnabled(False)
        self.all_btn = QPushButton()
        apply_button(self.all_btn, 'ghost', compact=True)
        self.all_btn.clicked.connect(self._fetch_all)
        self.all_btn.setEnabled(False)
        self.result_status = QLabel()
        self.result_status.setObjectName('field-hint')
        sql_row.addWidget(self.run_sql_btn)
        sql_row.addWidget(self.next_btn)
        sql_row.addWidget(self.all_btn)
        sql_row.addWidget(self.result_status, 1)
        right_l.addLayout(sql_row)

        self.result = QTableWidget()
        apply_table(self.result, alternating=True)
        self.result.setMinimumHeight(180)
        right_l.addWidget(self.result, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.page_title.setText('模型工作台' if zh else 'Model Workbench')
        self.page_subtitle.setText(
            '自然语言查库，一页 20 行，可下一页或获取全部' if zh else
            'Natural-language query · 20 rows per page'
        )
        self.conn_new_btn.setText('新建连接' if zh else 'New')
        self.conn_del_btn.setText('删除' if zh else 'Delete')
        self.test_btn.setText('测试连接' if zh else 'Test')
        self.sync_btn.setText('同步表' if zh else 'Sync tables')
        self.table_title.setText('表' if zh else 'Tables')
        self.nl_input.setPlaceholderText(
            '例如：帮我查询 prpCmain 表中的数据' if zh else
            'e.g. show rows from prpCmain'
        )
        self.nl_run_btn.setText('查询' if zh else 'Query')
        self.nl_sql_btn.setText('只生成 SQL' if zh else 'SQL only')
        self.run_sql_btn.setText('执行 SQL' if zh else 'Run SQL')
        self.next_btn.setText('下一页' if zh else 'Next page')
        self.all_btn.setText('获取全部' if zh else 'Fetch all')
        self._refresh_model_status()

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(self.page_subtitle, low_height)

    def _refresh_model_status(self):
        zh = self.language == 'zh'
        ready = False
        try:
            ready = is_enabled()
        except Exception:
            ready = False
        self.nl_run_btn.setEnabled(ready)
        self.nl_sql_btn.setEnabled(ready)
        self.nl_input.setEnabled(ready)
        self.model_status.setText(
            '内网模型已启用' if ready and zh else
            'Model ready' if ready else
            '未配置内网模型，可手写 SQL；自然语言查询需先到设置里探测。' if zh else
            'Configure the intranet model in Settings for natural language.'
        )

    def _current_conn(self) -> dict | None:
        data = self.conn_combo.currentData()
        return dict(data) if isinstance(data, dict) else None

    def _reload_connections(self, select_id: str = ''):
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        current = select_id
        for item in load_connections():
            self.conn_combo.addItem(str(item.get('name') or item.get('id')), item)
            if item.get('id') == current:
                self.conn_combo.setCurrentIndex(self.conn_combo.count() - 1)
        self.conn_combo.blockSignals(False)

    def _edit_connection(self):
        current = self._current_conn()
        dialog = _ConnectionDialog(self.language, current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        saved = upsert_connection(item, password if password else None)
        self._reload_connections(saved.get('id'))

    def _delete_connection(self):
        item = self._current_conn()
        if not item:
            return
        zh = self.language == 'zh'
        if not confirm_action(self, '模型工作台', '删除该连接？' if zh else 'Delete connection?', confirm_text='删除' if zh else 'Delete', danger=True):
            return
        delete_connection(item.get('id'))
        self._reload_connections()
        self.table_list.clear()

    def _busy(self, on: bool):
        for btn in (self.test_btn, self.sync_btn, self.run_sql_btn, self.nl_run_btn, self.next_btn, self.all_btn):
            btn.setEnabled(not on)
        if not on:
            self._refresh_model_status()
            self.next_btn.setEnabled(self._has_more)
            self.all_btn.setEnabled(self._has_more)

    def _start_db(self, kind: str, **kwargs):
        item = self._current_conn()
        zh = self.language == 'zh'
        if not item:
            show_warning(self, '模型工作台', '请先新建并选择连接' if zh else 'Create a connection first')
            return
        if kind == 'query':
            sql = str(kwargs.get('sql') or self.sql_edit.toPlainText())
            reason = reject_reason(sql)
            if reason:
                show_warning(self, '模型工作台', reason)
                return
        self._busy(True)
        self.result_status.setText('正在连接/查询…' if zh else 'Working…')
        self._worker = _DbWorker(kind, item, **kwargs)
        self._worker.completed.connect(lambda payload: self._on_db_ok(kind, payload, kwargs))
        self._worker.failed.connect(self._on_db_fail)
        self._worker.finished.connect(lambda: self._busy(False))
        self._worker.start()

    def _on_db_ok(self, kind: str, payload: dict, kwargs: dict):
        zh = self.language == 'zh'
        if kind in ('test', 'tables'):
            tables = payload.get('tables') or []
            self.table_list.clear()
            for name in tables:
                self.table_list.addItem(QListWidgetItem(str(name)))
            show_info(self, '模型工作台', f'已同步 {len(tables)} 张表' if zh else f'{len(tables)} table(s)')
            return
        if kind == 'schema':
            self._schema_text = str(payload.get('summary') or '')
            return
        if kind == 'query':
            append = bool(kwargs.get('append'))
            self._last_sql = str(payload.get('sql') or self._last_sql)
            self._offset = int(payload.get('offset') or 0) + len(payload.get('rows') or [])
            self._has_more = bool(payload.get('has_more'))
            self._fill_result(payload, append=append)
            shown = self.result.rowCount()
            extra = '，还有更多' if self._has_more and zh else (', more available' if self._has_more else '')
            self.result_status.setText(
                f'已显示 {shown} 行{extra}' if zh else f'Showing {shown} row(s){extra}'
            )
            self.next_btn.setEnabled(self._has_more)
            self.all_btn.setEnabled(self._has_more)

    def _on_db_fail(self, message: str):
        show_error(self, '模型工作台', str(message or ''))
        self.result_status.setText('查询失败' if self.language == 'zh' else 'Failed')

    def _fill_result(self, payload: dict, *, append: bool = False):
        columns = list(payload.get('columns') or [])
        rows = list(payload.get('rows') or [])
        if not append:
            self.result.clear()
            self.result.setColumnCount(len(columns))
            self.result.setHorizontalHeaderLabels(columns)
            self.result.setRowCount(0)
        start = self.result.rowCount()
        self.result.setRowCount(start + len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if c >= self.result.columnCount():
                    self.result.setColumnCount(c + 1)
                self.result.setItem(start + r, c, QTableWidgetItem(str(value)))
        header = self.result.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _run_sql(self, *, reset: bool = True):
        sql = self.sql_edit.toPlainText().strip()
        if reset:
            self._offset = 0
            self._last_sql = sql
            self.result.setRowCount(0)
        self._start_db('query', sql=sql, offset=self._offset if not reset else 0, limit=PAGE_SIZE, append=not reset)

    def _fetch_next(self):
        if not self._last_sql or not self._has_more:
            return
        self._start_db('query', sql=self._last_sql, offset=self._offset, limit=PAGE_SIZE, append=True)

    def _fetch_all(self):
        if not self._last_sql:
            return
        from tools.db_connect import MAX_ROWS
        remaining = max(PAGE_SIZE, MAX_ROWS - self.result.rowCount())
        self._start_db('query', sql=self._last_sql, offset=self._offset, limit=remaining, append=True)

    def _on_table_activated(self, item: QListWidgetItem):
        name = item.text().strip()
        if not name:
            return
        dialect = str((self._current_conn() or {}).get('dialect') or 'oracle')
        if dialect in ('oceanbase', 'mysql'):
            sql = f'SELECT * FROM `{name}`'
        else:
            sql = f'SELECT * FROM {name}'
        self.sql_edit.setPlainText(sql)
        self._run_sql(reset=True)

    def _run_natural_query(self, sql_only: bool = False):
        zh = self.language == 'zh'
        if not is_enabled():
            show_warning(self, '模型工作台', '请先在设置中启用并探测内网模型' if zh else 'Enable the intranet model first')
            return
        prompt = self.nl_input.text().strip()
        if not prompt:
            show_warning(self, '模型工作台', '请输入要查询的内容' if zh else 'Enter a question')
            return
        item = self._current_conn() or {}
        dialect = str(item.get('dialect') or 'oracle')
        context_parts = [
            f'当前已选择数据库：{dialect}',
            f'连接名：{item.get("name") or ""}',
            '只生成一条可执行的 SELECT 查询，不要解释。',
        ]
        if self._schema_text:
            context_parts.append(self._schema_text)
        elif self.table_list.count():
            names = [self.table_list.item(i).text() for i in range(min(40, self.table_list.count()))]
            context_parts.append('已知表：' + '、'.join(names))
        self._busy(True)
        self.result_status.setText('正在让模型生成 SQL…' if zh else 'Generating SQL…')
        self._nl_worker = _NlWorker(prompt, '\n'.join(context_parts), load_ai_local())
        self._nl_worker.completed.connect(lambda sql: self._on_nl_sql(sql, sql_only=bool(sql_only)))
        self._nl_worker.failed.connect(self._on_nl_fail)
        self._nl_worker.start()

    def _on_nl_fail(self, message: str):
        self._on_db_fail(message)
        self._busy(False)

    def _on_nl_sql(self, sql: str, *, sql_only: bool):
        text = str(sql or '').strip()
        self.sql_edit.setPlainText(text)
        if sql_only:
            self.result_status.setText('已生成 SQL，未执行' if self.language == 'zh' else 'SQL generated')
            self._busy(False)
            return
        reason = reject_reason(text)
        if reason or not is_read_query(text):
            show_warning(
                self, '模型工作台',
                (reason or '不是查询语句') + '\nSQL 已放入编辑器，不会自动执行。',
            )
            self._busy(False)
            return
        self._run_sql(reset=True)
