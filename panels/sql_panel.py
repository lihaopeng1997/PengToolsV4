# -*- coding: utf-8 -*-
import os
import shutil
import sys

from PyQt6.QtCore import QDate, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from config import DELIVERY_TEMPLATE, VALIDATION_TEMPLATE, load_systems, save_systems
from tools.sql_tool import (
    build_sql_package, classify_sql_type, export_sql_package,
    deduplicate_sql_statements, read_file_auto_encoding, validate_oracle_sql,
    validate_oracle_sql_detailed,
)
from tools.release_prep import (
    RELEASE_SVN_URL, RELEASE_WORKBOOK_NAME, branch_name_from_svn,
    rank_requirements, release_row_from_requirement, update_release_workbook,
)
from tools.requirements import load_requirements, merged_sql, save_requirements
from ui.aurora_progress import AuroraProgress
from ui.confirm_dialog import confirm_action, show_error, show_info, show_success, show_warning
from ui.field_metrics import (
    size_caption, size_combo, size_compact_button, size_date, size_line,
    size_status_pill, size_system_chip,
)


class SqlExportWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, sql, system, environment, date_text):
        super().__init__()
        self.root = root
        self.sql = sql
        self.system = system
        self.environment = environment
        self.date_text = date_text

    def run(self):
        try:
            self.completed.emit(export_sql_package(
                self.root, self.sql, self.system, self.environment, self.date_text
            ))
        except Exception as exc:
            self.failed.emit(str(exc))


class SqlToolPanel(QWidget):
    task_completed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.language = 'zh'
        self._systems = load_systems()
        self._current_system_idx = 0
        self._form_labels = {}
        self._hints = {}
        self._export_worker = None
        self._has_file_input = False
        self._has_paste_input = False
        self._mixed_source_confirmed = False
        self._paste_count = 0
        self._release_requirements = []
        self._release_all_requirements = []
        self._release_date_confirmed = ''
        self._release_reload_timer = QTimer(self)
        self._release_reload_timer.setSingleShot(True)
        self._release_reload_timer.setInterval(280)
        self._release_reload_timer.timeout.connect(self._load_release_candidates)
        self._setup_ui()
        self._load_systems()
        self.set_language('zh')
        # 懒人流程：打开后按当前升级日自动载入候选，无需再点确认日期
        self._load_release_candidates()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏不再放 SQL 按钮，避免切换 Sheet 时“突然冒出一排按钮”
        self.tabs = QTabWidget()
        self.tabs.setObjectName('module-tabs')
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.addTab(self._create_release_tab(), '')
        self.tabs.addTab(self._create_processing_tab(), '')
        self.tabs.addTab(self._create_config_tab(), '')
        root.addWidget(self.tabs, 1)

    def _create_release_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(2, 4, 2, 2)

        intro = QLabel('懒人流程：选择升级日期后自动加载候选需求 / BUG；可点「刷新候选」重载；开发分支可留空；勾选后一键生成 SQL 包与生产发版清单。')
        intro.setObjectName('path-note')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # 顶区：路径 + 筛选，形成「配置模块」
        filter_zone = QFrame()
        filter_zone.setObjectName('release-filter-zone')
        filter_zone_layout = QVBoxLayout(filter_zone)
        filter_zone_layout.setContentsMargins(14, 12, 14, 12)
        filter_zone_layout.setSpacing(10)

        filter_title = QLabel('发版路径与筛选')
        filter_title.setObjectName('zone-title')
        filter_zone_layout.addWidget(filter_title)

        svn_row = QHBoxLayout()
        svn_label = QLabel('生产发版 SVN')
        svn_label.setObjectName('field-caption')
        size_caption(svn_label)
        svn_row.addWidget(svn_label)
        self.release_svn = QLineEdit(RELEASE_SVN_URL)
        self.release_svn.setReadOnly(True)
        self.release_svn.setObjectName('release-readonly')
        size_line(self.release_svn, 'path')
        svn_row.addWidget(self.release_svn, 1)
        copy_svn = QPushButton('复制链接')
        size_compact_button(copy_svn)
        copy_svn.clicked.connect(lambda: QApplication.clipboard().setText(RELEASE_SVN_URL))
        svn_row.addWidget(copy_svn)
        filter_zone_layout.addLayout(svn_row)

        path_row = QHBoxLayout()
        root_label = QLabel('发版清单目录')
        root_label.setObjectName('field-caption')
        size_caption(root_label)
        path_row.addWidget(root_label)
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '02-生产环境发版任务清单'))
        default_root = workspace_root if os.path.isdir(workspace_root) and not getattr(sys, 'frozen', False) else os.path.join(os.path.expanduser('~'), 'Desktop', '02-生产环境发版任务清单')
        self.release_root = QLineEdit(default_root)
        size_line(self.release_root, 'path')
        path_row.addWidget(self.release_root, 1)
        choose_root = QPushButton('选择目录')
        size_compact_button(choose_root)
        choose_root.clicked.connect(self._choose_release_root)
        path_row.addWidget(choose_root)
        filter_zone_layout.addLayout(path_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        date_label = QLabel('升级日期')
        date_label.setObjectName('field-caption')
        size_caption(date_label)
        date_row.addWidget(date_label)
        self.release_date = QDateEdit(QDate.currentDate())
        self.release_date.setObjectName('release-date')
        self.release_date.setDisplayFormat('yyyy-MM-dd')
        size_date(self.release_date)
        self.release_date.dateChanged.connect(self._release_date_changed)
        date_row.addWidget(self.release_date)
        self.refresh_release_btn = QPushButton('刷新候选')
        self.refresh_release_btn.setObjectName('primary-btn')
        size_compact_button(self.refresh_release_btn)
        self.refresh_release_btn.clicked.connect(self._load_release_candidates)
        date_row.addWidget(self.refresh_release_btn)
        select_all = QPushButton('全选')
        size_compact_button(select_all)
        select_all.clicked.connect(lambda: self._set_release_selection(True))
        date_row.addWidget(select_all)
        select_none = QPushButton('取消全选')
        size_compact_button(select_none)
        select_none.clicked.connect(lambda: self._set_release_selection(False))
        date_row.addWidget(select_none)
        self.release_count = QLabel('加载中…')
        self.release_count.setObjectName('status-pill')
        size_status_pill(self.release_count)
        self.release_count.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        date_row.addWidget(self.release_count)
        date_row.addStretch(1)
        filter_zone_layout.addLayout(date_row)
        layout.addWidget(filter_zone)

        # 中区：候选表格
        table_zone = QFrame()
        table_zone.setObjectName('release-table-zone')
        table_zone_layout = QVBoxLayout(table_zone)
        table_zone_layout.setContentsMargins(12, 10, 12, 12)
        table_zone_layout.setSpacing(8)
        table_title = QLabel('候选需求 / BUG')
        table_title.setObjectName('zone-title')
        table_zone_layout.addWidget(table_title)

        self.release_table = QTableWidget(0, 10)
        self.release_table.setObjectName('release-table')
        self.release_table.setHorizontalHeaderLabels((
            '选择', '类型', '任务编号', '标题', '上线时间', '所属系统', '开发分支 SVN',
            '前/后端', '任务内容', '升级公告',
        ))
        self.release_table.verticalHeader().setVisible(False)
        self.release_table.verticalHeader().setDefaultSectionSize(42)
        self.release_table.setAlternatingRowColors(True)
        self.release_table.setShowGrid(False)
        self.release_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.release_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.release_table.setWordWrap(False)
        self.release_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.release_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.release_table.setSizePolicy(
            self.release_table.sizePolicy().horizontalPolicy(),
            self.release_table.sizePolicy().verticalPolicy(),
        )
        header = self.release_table.horizontalHeader()
        header.setObjectName('release-table-header')
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(52)
        header.setStretchLastSection(False)
        fixed_widths = {0: 52, 1: 58, 2: 128, 4: 108, 5: 178, 7: 108}
        for column, width in fixed_widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.release_table.setColumnWidth(column, width)
        for column in (3, 6, 8, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.release_table.setColumnWidth(3, 180)
        self.release_table.setColumnWidth(6, 220)
        self.release_table.setColumnWidth(8, 180)
        self.release_table.setColumnWidth(9, 150)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_zone_layout.addWidget(self.release_table, 1)
        layout.addWidget(table_zone, 1)

        # 底区：生成动作
        bottom_card = QFrame()
        bottom_card.setObjectName('release-generate-zone')
        bottom = QHBoxLayout(bottom_card)
        bottom.setContentsMargins(14, 12, 14, 12)
        bottom.setSpacing(10)
        gen_title = QLabel('生成')
        gen_title.setObjectName('zone-title-inline')
        bottom.addWidget(gen_title)
        self.release_no_sql = QCheckBox('本次确认无 SQL')
        bottom.addWidget(self.release_no_sql)
        extra_label = QLabel('额外 SQL 归属')
        extra_label.setObjectName('field-caption')
        bottom.addWidget(extra_label)
        self.release_extra_sql_system = QComboBox()
        self.release_extra_sql_system.setObjectName('release-extra-system')
        size_combo(self.release_extra_sql_system, 'md')
        self.release_extra_sql_system.addItem('自动（单系统）', '')
        for system in self._systems:
            self.release_extra_sql_system.addItem(system['name'], system['name'])
        bottom.addWidget(self.release_extra_sql_system)
        hint = QLabel('有 SQL 时汇总所选需求/BUG 已关联 SQL，并合并 SQL 整理页编辑区内容。')
        hint.setObjectName('small-label')
        hint.setWordWrap(True)
        bottom.addWidget(hint, 1)
        self.release_generate = QPushButton('生成升级材料')
        self.release_generate.setObjectName('primary-btn')
        self.release_generate.clicked.connect(self._generate_release_materials)
        bottom.addWidget(self.release_generate)
        layout.addWidget(bottom_card)
        return tab

    def _choose_release_root(self):
        path = QFileDialog.getExistingDirectory(self, '选择生产发版任务清单目录', self.release_root.text())
        if path:
            self.release_root.setText(path)

    def _release_date_changed(self):
        self._set_status_label(self.release_count, '正在加载…', '升级日期已改变，正在重新加载候选需求', max_chars=12)
        self._release_reload_timer.start()

    def _make_table_combo(self, items, current='', placeholder='请选择'):
        combo = QComboBox()
        combo.setObjectName('table-combo')
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        combo.setMaxVisibleItems(12)
        combo.addItem(placeholder, '')
        for value in items:
            combo.addItem(value, value)
        if current:
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _load_release_candidates(self):
        """按当前升级日期加载/刷新候选需求（替代「确认日期」）。"""
        if self._release_reload_timer.isActive():
            self._release_reload_timer.stop()
        target = self.release_date.date().toString('yyyy-MM-dd')
        self._release_date_confirmed = target
        self._release_all_requirements = load_requirements()
        self._release_requirements = rank_requirements(self._release_all_requirements, target)
        self.release_table.setRowCount(len(self._release_requirements))
        exact = 0
        system_names = [system['name'] for system in self._systems]
        for row, requirement in enumerate(self._release_requirements):
            date_value = str(requirement.get('actual_online_date') or requirement.get('planned_online_date') or '')[:10]
            checked = date_value == target
            exact += int(checked)
            choose = QTableWidgetItem()
            choose.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            choose.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            choose.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.release_table.setItem(row, 0, choose)

            kind_item = QTableWidgetItem(str(requirement.get('record_kind', '需求') or ''))
            kind_item.setFlags(kind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            kind_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.release_table.setItem(row, 1, kind_item)

            code_item = QTableWidgetItem(str(requirement.get('code', '') or ''))
            code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            code_item.setToolTip(code_item.text())
            self.release_table.setItem(row, 2, code_item)

            title = str(requirement.get('title', '') or '')
            title_item = QTableWidgetItem(title)
            title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            title_item.setToolTip(title)
            self.release_table.setItem(row, 3, title_item)

            date_item = QTableWidgetItem(date_value)
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.release_table.setItem(row, 4, date_item)

            current_system = self._matched_system_name(requirement.get('system', ''))
            system_combo = self._make_table_combo(system_names, current_system, '请选择系统')
            if not current_system:
                system_combo.setToolTip('请选择该需求或 BUG 实际开发的系统')
            else:
                system_combo.setToolTip(current_system)
            self.release_table.setCellWidget(row, 5, system_combo)

            editable_values = (
                requirement.get('svn_url', ''),
                requirement.get('release_scope', '后端：全部'),
                requirement.get('description') or requirement.get('title', ''),
                requirement.get('title') or requirement.get('description', ''),
            )
            for column, value in enumerate(editable_values, 6):
                item = QTableWidgetItem(str(value or ''))
                item.setToolTip(str(value or ''))
                if column == 7:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.release_table.setItem(row, column, item)
            self.release_table.setRowHeight(row, 42)
        self._set_status_label(
            self.release_count,
            f'已载 {len(self._release_requirements)} · 勾选 {exact}',
            f'已加载 {len(self._release_requirements)} 条，自动勾选当天 {exact} 条，升级日 {target}',
            max_chars=20,
        )

    # 兼容旧测试/调用名
    def _confirm_release_date(self):
        self._load_release_candidates()

    def _set_release_selection(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.release_table.rowCount()):
            self.release_table.item(row, 0).setCheckState(state)

    def _matched_system_name(self, value):
        wanted = str(value or '').strip()
        names = [system['name'] for system in self._systems]
        if wanted in names:
            return wanted
        return next((name for name in names if wanted and (wanted in name or name in wanted)), '')

    def _system_config(self, name):
        return next((system for system in self._systems if system.get('name') == name), None)

    def _selected_release_rows(self):
        selected = []
        for row, requirement in enumerate(self._release_requirements):
            if self.release_table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            system_name = self.release_table.cellWidget(row, 5).currentData()
            if not system_name:
                raise ValueError(f'请为“{requirement.get("title", "") or requirement.get("code", "")}”选择所属系统')
            svn_url = self.release_table.item(row, 6).text().strip()
            requirement['svn_url'] = svn_url
            requirement['system'] = system_name
            requirement['release_scope'] = self.release_table.item(row, 7).text().strip() or '后端：全部'
            overrides = {
                '系统名': system_name,
                '前端：全部': requirement['release_scope'],
                '任务内容': self.release_table.item(row, 8).text().strip(),
                '升级公告': self.release_table.item(row, 9).text().strip(),
            }
            selected.append((requirement, release_row_from_requirement(requirement, self._release_date_confirmed, overrides)))
        return selected

    def _generate_release_materials(self):
        target = self.release_date.date().toString('yyyy-MM-dd')
        # 日期若已变，自动按当前日期重新加载，不打断用户再点确认
        if self._release_date_confirmed != target:
            self._load_release_candidates()
        try:
            selected = self._selected_release_rows()
            if not selected:
                raise ValueError('请至少勾选一个本次升级的需求或 BUG')
            root = self.release_root.text().strip()
            template = os.path.join(root, RELEASE_WORKBOOK_NAME)
            packaged_template = os.path.join(getattr(sys, '_MEIPASS', ''), 'resources', 'release_workbook_template.xlsx')
            if not os.path.isfile(template) and os.path.isfile(packaged_template):
                os.makedirs(root, exist_ok=True)
                shutil.copy2(packaged_template, template)
            if not os.path.isfile(template):
                raise ValueError(f'未找到生产发版清单模板：\n{template}')
            sql_by_system = {}
            for requirement, _row in selected:
                sql = merged_sql(requirement).strip()
                if sql:
                    sql_by_system.setdefault(requirement['system'], []).append(sql)
            editor_sql = self.input_sql.toPlainText().strip()
            if editor_sql:
                selected_systems = {requirement['system'] for requirement, _row in selected}
                extra_system = self.release_extra_sql_system.currentData()
                if not extra_system and len(selected_systems) == 1:
                    extra_system = next(iter(selected_systems))
                if not extra_system:
                    raise ValueError('已选择多个系统，请指定“额外 SQL 归属”。')
                sql_by_system.setdefault(extra_system, []).append(editor_sql)
            if not sql_by_system and not self.release_no_sql.isChecked():
                raise ValueError('没有发现 SQL。若本次确实无 SQL，请勾选“本次确认无 SQL”后再生成。')
            date_key = target.replace('-', '')
            output_workbook = os.path.join(root, f'{os.path.splitext(RELEASE_WORKBOOK_NAME)[0]}_{date_key}.xlsx')
            workbook_source = output_workbook if os.path.isfile(output_workbook) else template
            result = update_release_workbook(workbook_source, output_workbook, date_key, [row for _requirement, row in selected])
            sql_paths = []
            for system_name, blocks in sql_by_system.items():
                system = self._system_config(system_name)
                if not system:
                    raise ValueError(f'系统“{system_name}”没有可用的 SQL 配置')
                sql_paths.extend(export_sql_package(
                    os.path.join(root, '升级SQL'), '\n\n'.join(blocks), dict(system),
                    system.get('prod_env_name', '生产环境'), date_key,
                ))
            save_requirements(self._release_all_requirements)
        except (OSError, ValueError) as exc:
            show_warning(self, '升级准备', str(exc))
            return
        self.task_completed.emit()
        show_success(
            self, '升级材料已生成',
            f'发版清单：{result["path"]}\n'
            f'写入 Sheet：{result["sheet_name"]} 第 {result["start_row"]}-{result["end_row"]} 行\n'
            f'系统：{len(sql_by_system)} 个 · SQL 文件：{len(sql_paths)} 个',
        )

    def _create_processing_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(2, 4, 2, 2)

        # 1) 工具区：导入 / 粘贴 / 清空（仅本 Sheet，切换页时不会“突然出现”）
        sql_bar = QFrame()
        sql_bar.setObjectName('sql-tool-zone')
        tool_outer = QVBoxLayout(sql_bar)
        tool_outer.setContentsMargins(14, 10, 14, 10)
        tool_outer.setSpacing(8)
        self.sql_tool_title = QLabel('SQL 输入来源')
        self.sql_tool_title.setObjectName('zone-title')
        tool_outer.addWidget(self.sql_tool_title)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self.load_btn = QPushButton()
        self.load_btn.setProperty('compactAction', True)
        self.load_btn.clicked.connect(self._load_file)
        toolbar.addWidget(self.load_btn)
        self.paste_btn = QPushButton()
        self.paste_btn.setProperty('compactAction', True)
        self.paste_btn.clicked.connect(self._paste_sql)
        toolbar.addWidget(self.paste_btn)
        self.clear_btn = QPushButton()
        self.clear_btn.setProperty('compactAction', True)
        self.clear_btn.clicked.connect(self._clear_sql)
        toolbar.addWidget(self.clear_btn)
        toolbar.addStretch(1)
        self.status = QLabel('就绪')
        self.status.setObjectName('status-pill')
        size_status_pill(self.status)
        self.status.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        toolbar.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        tool_outer.addLayout(toolbar)
        layout.addWidget(sql_bar)

        # 2) 交付配置区（替代默认 QGroupBox，与升级准备分区语言统一）
        self.delivery_group = QFrame()
        self.delivery_group.setObjectName('sql-delivery-zone')
        delivery = QVBoxLayout(self.delivery_group)
        delivery.setContentsMargins(14, 12, 14, 12)
        delivery.setSpacing(8)
        self.delivery_zone_title = QLabel('交付信息')
        self.delivery_zone_title.setObjectName('zone-title')
        delivery.addWidget(self.delivery_zone_title)
        first = QHBoxLayout()
        first.setSpacing(8)
        self.current_system_label = QLabel()
        self.current_system_label.setObjectName('system-chip')
        size_system_chip(self.current_system_label)
        first.addWidget(self.current_system_label, 0, Qt.AlignmentFlag.AlignVCenter)
        first.addStretch(1)
        self.env_label = QLabel()
        self.env_label.setObjectName('field-caption')
        size_caption(self.env_label)
        first.addWidget(self.env_label)
        self.env_combo = QComboBox()
        size_combo(self.env_combo, 'sm')
        self.env_combo.addItems(['模拟环境', '生产环境'])
        first.addWidget(self.env_combo)
        self.date_label = QLabel()
        self.date_label.setObjectName('field-caption')
        size_caption(self.date_label)
        first.addWidget(self.date_label)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setObjectName('release-date')
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        size_date(self.date_edit)
        first.addWidget(self.date_edit)
        delivery.addLayout(first)
        second = QHBoxLayout()
        second.setSpacing(8)
        self.root_label = QLabel()
        self.root_label.setObjectName('field-caption')
        size_caption(self.root_label)
        second.addWidget(self.root_label)
        self.output_root = QLineEdit(os.path.join(os.path.expanduser('~'), 'Desktop'))
        size_line(self.output_root, 'path')
        second.addWidget(self.output_root, 1)
        self.root_btn = QPushButton()
        size_compact_button(self.root_btn)
        self.root_btn.clicked.connect(self._choose_root)
        second.addWidget(self.root_btn)
        delivery.addLayout(second)
        self.path_note = QLabel()
        self.path_note.setObjectName('path-note')
        self.path_note.setWordWrap(True)
        self.path_note.setMaximumHeight(40)
        delivery.addWidget(self.path_note)
        layout.addWidget(self.delivery_group)

        # 3) 编辑 / 预览区
        editor_zone = QFrame()
        editor_zone.setObjectName('sql-editor-zone')
        editor_outer = QVBoxLayout(editor_zone)
        editor_outer.setContentsMargins(12, 10, 12, 12)
        editor_outer.setSpacing(8)
        self.sql_editor_title = QLabel('编辑与预览')
        self.sql_editor_title.setObjectName('zone-title')
        editor_outer.addWidget(self.sql_editor_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName('sql-editor-splitter')
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        input_widget = QWidget()
        input_widget.setMinimumWidth(220)
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(6)
        self.input_label = QLabel()
        self.input_label.setObjectName('zone-title')
        input_layout.addWidget(self.input_label)
        self.input_sql = QPlainTextEdit()
        self.input_sql.setObjectName('sql-input-editor')
        self.input_sql.setFont(QFont('Consolas', 10))
        self.input_sql.setSizePolicy(self.input_sql.sizePolicy().horizontalPolicy(), self.input_sql.sizePolicy().verticalPolicy())
        self.input_sql.textChanged.connect(self._reset_sources_if_empty)
        input_layout.addWidget(self.input_sql, 1)
        splitter.addWidget(input_widget)

        preview_widget = QWidget()
        preview_widget.setMinimumWidth(240)
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        self.preview_label = QLabel()
        self.preview_label.setObjectName('zone-title')
        preview_layout.addWidget(self.preview_label)
        self.preview_tabs = QTabWidget()
        self.preview_tabs.setObjectName('module-tabs')
        self.preview_tabs.setDocumentMode(True)
        self.preview_tabs.setUsesScrollButtons(True)
        self.preview_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.upgrade_preview = self._preview_editor()
        self.rollback_preview = self._preview_editor()
        self.validation_preview = self._preview_editor()
        self.preview_tabs.addTab(self.upgrade_preview, '')
        self.preview_tabs.addTab(self.rollback_preview, '')
        self.preview_tabs.addTab(self.validation_preview, '')
        preview_layout.addWidget(self.preview_tabs, 1)
        splitter.addWidget(preview_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 520])
        editor_outer.addWidget(splitter, 1)
        layout.addWidget(editor_zone, 1)

        self.progress = AuroraProgress()
        layout.addWidget(self.progress)

        # 4) 底部动作区
        action_zone = QFrame()
        action_zone.setObjectName('sql-action-zone')
        actions = QHBoxLayout(action_zone)
        actions.setContentsMargins(14, 10, 14, 10)
        actions.setSpacing(8)
        self.sql_action_title = QLabel('检查 / 导出')
        self.sql_action_title.setObjectName('zone-title-inline')
        actions.addWidget(self.sql_action_title)
        self.analyze_btn = QPushButton()
        self.analyze_btn.setProperty('compactAction', True)
        self.analyze_btn.clicked.connect(self._analyze)
        actions.addWidget(self.analyze_btn)
        self.preview_btn = QPushButton()
        self.preview_btn.setProperty('compactAction', True)
        self.preview_btn.clicked.connect(self._preview_package)
        actions.addWidget(self.preview_btn)
        actions.addStretch()
        self.export_btn = QPushButton()
        self.export_btn.setObjectName('primary-btn')
        self.export_btn.clicked.connect(self._export_package)
        actions.addWidget(self.export_btn)
        layout.addWidget(action_zone)
        return tab

    @staticmethod
    def _preview_editor():
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(QFont('Consolas', 9))
        return editor

    def _create_config_tab(self):
        tab = QWidget()
        outer = QVBoxLayout(tab)
        selector = QHBoxLayout()
        self.config_system_label = QLabel()
        self.system_label = self.config_system_label
        selector.addWidget(self.config_system_label)
        self.system_combo = QComboBox()
        size_combo(self.system_combo, 'lg')
        self.system_combo.currentIndexChanged.connect(self._on_system_changed)
        selector.addWidget(self.system_combo)
        selector.addStretch()
        outer.addLayout(selector)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        self.identity_group = QGroupBox()
        identity_form = QFormLayout(self.identity_group)
        self.name_box = self._hinted_field(identity_form, 'name', '客户信息平台（ECIF）', '下拉框显示名称', 'Name shown in the selector')
        self.title_box = self._hinted_field(identity_form, 'title', 'ECIF', '用于文件名：【ECIF】', 'Used in filename: 【ECIF】')
        self.folder_box = self._hinted_field(identity_form, 'folder', '客户信息平台-张小龙', 'SVN 系统目录名称', 'SVN system folder name')
        self.author_box = self._hinted_field(identity_form, 'author', '李浩鹏', '用于文件名：李浩鹏-【ECIF】升级SQL.sql', 'Used as the filename prefix')
        layout.addWidget(self.identity_group)

        self.sim_group = QGroupBox()
        sim_form = QFormLayout(self.sim_group)
        self.sim_addr_box = self._hinted_field(sim_form, 'sim_addr', '10.128.23.211', '表头第一行地址', 'Header address')
        self.sim_sid_box = self._hinted_field(sim_form, 'sim_sid', 'simutfdb', '表头第二行 SID', 'Header SID')
        self.sim_user_box = self._hinted_field(sim_form, 'sim_user', 'sitecif', '表头第三行用户名', 'Header user')
        layout.addWidget(self.sim_group)

        self.prod_group = QGroupBox()
        prod_form = QFormLayout(self.prod_group)
        self.prod_addr_box = self._hinted_field(prod_form, 'prod_addr', '10.0.129.207', '表头第一行地址', 'Header address')
        self.prod_sid_box = self._hinted_field(prod_form, 'prod_sid', 'hxutf', '表头第二行 SID', 'Header SID')
        self.prod_user_box = self._hinted_field(prod_form, 'prod_user', 'ecif', '表头第三行用户名', 'Header user')
        layout.addWidget(self.prod_group)

        self.path_group = QGroupBox()
        path_form = QFormLayout(self.path_group)
        self.delivery_template_box = self._hinted_field(
            path_form, 'delivery', DELIVERY_TEMPLATE,
            '示例：20260629/生产环境/DML/客户信息平台-张小龙/升级SQL',
            'Example: 20260629/生产环境/DML/客户信息平台-张小龙/升级SQL',
        )
        self.validation_template_box = self._hinted_field(
            path_form, 'validation', VALIDATION_TEMPLATE,
            '独立于 SVN 提交目录；生产执行前留存及执行后验证',
            'Outside SVN; pre-change backup and post-production verification',
        )
        layout.addWidget(self.path_group)

        buttons = QHBoxLayout()
        self.add_sys_btn = QPushButton()
        self.add_sys_btn.clicked.connect(self._add_system)
        buttons.addWidget(self.add_sys_btn)
        self.save_sys_btn = QPushButton()
        self.save_sys_btn.setObjectName('primary-btn')
        self.save_sys_btn.clicked.connect(self._save_current)
        buttons.addWidget(self.save_sys_btn)
        self.delete_sys_btn = QPushButton()
        self.delete_sys_btn.clicked.connect(self._delete_system)
        buttons.addWidget(self.delete_sys_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return tab

    def _hinted_field(self, form, key, example, zh_hint, en_hint):
        label = QLabel()
        self._form_labels[key] = label
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)
        line_edit = QLineEdit()
        size_line(line_edit, 'std')
        line_edit.setPlaceholderText(example)
        hint = QLabel()
        hint.setObjectName('field-hint')
        hint.setProperty('zh_hint', zh_hint)
        hint.setProperty('en_hint', en_hint)
        hint.setWordWrap(True)
        self._hints[key] = hint
        box.addWidget(line_edit)
        box.addWidget(hint)
        form.addRow(label, widget)
        return line_edit

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.load_btn.setText('导入 SQL' if zh else 'Import SQL')
        self.paste_btn.setText('粘贴 SQL' if zh else 'Paste SQL')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self.tabs.setTabText(0, '升级准备' if zh else 'Release Prep')
        self.tabs.setTabText(1, 'SQL 整理' if zh else 'SQL Scripts')
        self.tabs.setTabText(2, '系统配置' if zh else 'Systems')
        self.sql_tool_title.setText('SQL 输入来源' if zh else 'SQL sources')
        self.delivery_zone_title.setText('交付信息' if zh else 'Delivery')
        self.sql_editor_title.setText('编辑与预览' if zh else 'Edit & preview')
        self.sql_action_title.setText('检查 / 导出' if zh else 'Check / Export')
        self.env_label.setText('环境' if zh else 'Env')
        self.env_combo.setItemText(0, '模拟环境' if zh else 'Simulation')
        self.env_combo.setItemText(1, '生产环境' if zh else 'Production')
        self.date_label.setText('日期' if zh else 'Date')
        self.root_label.setText('输出目录' if zh else 'Output')
        self.root_btn.setText('选择' if zh else 'Browse')
        self.path_note.setText(
            '目录：日期/环境/DDL|DML/系统/升级|回滚；验证 SQL 不进 SVN。'
            if zh else
            'Path: date/env/DDL|DML/system/upgrade|rollback; validation stays outside SVN.'
        )
        self.input_label.setText('SQL 输入' if zh else 'SQL input')
        self.preview_label.setText('预览' if zh else 'Preview')
        self.preview_tabs.setTabText(0, '升级 SQL' if zh else 'Upgrade SQL')
        self.preview_tabs.setTabText(1, '回滚 SQL' if zh else 'Rollback SQL')
        self.preview_tabs.setTabText(2, '验证 SQL' if zh else 'Validation SQL')
        self.analyze_btn.setText('检查 SQL' if zh else 'Check SQL')
        self.preview_btn.setText('生成预览' if zh else 'Preview')
        self.export_btn.setText('导出全部' if zh else 'Export all')
        self.identity_group.setTitle('系统与文件名' if zh else 'System & filename')
        self.sim_group.setTitle('模拟环境' if zh else 'Simulation')
        self.prod_group.setTitle('生产环境' if zh else 'Production')
        self.path_group.setTitle('目录模板' if zh else 'Path templates')
        names = {
            'name': ('系统名称', 'System name'), 'title': ('SQL 标题', 'SQL title'),
            'folder': ('SVN 系统目录', 'SVN system folder'), 'author': ('脚本作者', 'Script author'),
            'sim_addr': ('地址', 'Address'), 'sim_sid': ('SID', 'SID'), 'sim_user': ('用户名', 'User'),
            'prod_addr': ('地址', 'Address'), 'prod_sid': ('SID', 'SID'), 'prod_user': ('用户名', 'User'),
            'delivery': ('提交目录模板', 'Delivery template'), 'validation': ('验证目录模板', 'Validation template'),
        }
        for key, label in self._form_labels.items():
            label.setText(names[key][0 if zh else 1])
        for hint in self._hints.values():
            hint.setText(hint.property('zh_hint' if zh else 'en_hint'))
        self.add_sys_btn.setText('新增系统' if zh else 'Add system')
        self.save_sys_btn.setText('保存当前配置' if zh else 'Save current')
        self.delete_sys_btn.setText('删除系统' if zh else 'Delete system')
        ready = '就绪' if zh else 'Ready'
        if not self.status.text() or self.status.text() in ('就绪', 'Ready') or self.status.toolTip() in ('就绪', 'Ready', ''):
            self._set_status_label(self.status, ready, ready, max_chars=6)
        self.config_system_label.setText('配置系统' if zh else 'System')
        self._update_current_system_label()

    def _load_systems(self):
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        self.system_combo.addItems([system['name'] for system in self._systems])
        self.system_combo.blockSignals(False)
        if self._systems:
            self._current_system_idx = min(self._current_system_idx, len(self._systems) - 1)
            self.system_combo.setCurrentIndex(self._current_system_idx)
            self._populate_form()
        self._update_current_system_label()
        if hasattr(self, 'release_extra_sql_system'):
            current = self.release_extra_sql_system.currentData()
            self.release_extra_sql_system.blockSignals(True)
            self.release_extra_sql_system.clear()
            self.release_extra_sql_system.addItem('自动（单系统）', '')
            for system in self._systems:
                self.release_extra_sql_system.addItem(system['name'], system['name'])
            self.release_extra_sql_system.setCurrentIndex(max(0, self.release_extra_sql_system.findData(current)))
            self.release_extra_sql_system.blockSignals(False)

    def _populate_form(self):
        system = self._get_system()
        if not system:
            return
        mapping = {
            self.name_box: 'name', self.title_box: 'sql_title', self.folder_box: 'system_folder', self.author_box: 'script_author',
            self.sim_addr_box: 'sim_addr', self.sim_sid_box: 'sim_sid', self.sim_user_box: 'sim_user',
            self.prod_addr_box: 'prod_addr', self.prod_sid_box: 'prod_sid', self.prod_user_box: 'prod_user',
            self.delivery_template_box: 'delivery_template', self.validation_template_box: 'validation_template',
        }
        for widget, key in mapping.items():
            widget.setText(system.get(key, ''))

    def _sync_form(self):
        system = self._get_system()
        if not system:
            return
        values = {
            'name': self.name_box.text().strip(), 'sql_title': self.title_box.text().strip(),
            'system_folder': self.folder_box.text().strip(), 'script_author': self.author_box.text().strip(),
            'sim_addr': self.sim_addr_box.text().strip(), 'sim_sid': self.sim_sid_box.text().strip(), 'sim_user': self.sim_user_box.text().strip(),
            'prod_addr': self.prod_addr_box.text().strip(), 'prod_sid': self.prod_sid_box.text().strip(), 'prod_user': self.prod_user_box.text().strip(),
            'delivery_template': self.delivery_template_box.text().strip() or DELIVERY_TEMPLATE,
            'validation_template': self.validation_template_box.text().strip() or VALIDATION_TEMPLATE,
            'sim_env_name': '模拟环境', 'prod_env_name': '生产环境',
        }
        system.update(values)

    def _get_system(self):
        if 0 <= self._current_system_idx < len(self._systems):
            return self._systems[self._current_system_idx]
        return None

    def _on_system_changed(self, index):
        if 0 <= index < len(self._systems):
            if index != self._current_system_idx:
                self._sync_form()
            self._current_system_idx = index
            self._populate_form()
            self._update_current_system_label()

    def _set_status_label(self, label, text, tip=None, max_chars=18):
        """状态胶囊：按内容收缩；过长省略，完整文案放 tooltip。"""
        full = str(text or '').strip()
        tip_text = (tip if tip is not None else full) or full
        label.setToolTip(tip_text)
        if len(full) > max_chars:
            label.setText(full[: max_chars - 1] + '…')
        else:
            label.setText(full)
        label.adjustSize()

    def _update_current_system_label(self):
        system = self._get_system()
        name = system.get('name', '') if system else '-'
        if self.language == 'zh':
            short = f'系统 · {name}'
            tip = f'当前系统：{name}\n请到「系统配置」页切换'
        else:
            short = f'Sys · {name}'
            tip = f'Configured system: {name}\nSwitch on System Config tab'
        self._set_status_label(self.current_system_label, short, tip, max_chars=16)

    def _add_system(self):
        self._systems.append({
            'name': '新系统', 'sql_title': '新系统', 'system_folder': '新系统-提交人', 'script_author': '李浩鹏',
            'sim_addr': '', 'sim_sid': '', 'sim_user': '', 'prod_addr': '', 'prod_sid': '', 'prod_user': '',
            'sim_env_name': '模拟环境', 'prod_env_name': '生产环境',
            'delivery_template': DELIVERY_TEMPLATE, 'validation_template': VALIDATION_TEMPLATE,
        })
        self._current_system_idx = len(self._systems) - 1
        self._load_systems()

    def _save_current(self):
        self._sync_form()
        save_systems(self._systems)
        self._load_systems()
        self._set_status_label(
            self.status,
            '已保存' if self.language == 'zh' else 'Saved',
            '配置已保存' if self.language == 'zh' else 'Configuration saved',
            max_chars=8,
        )

    def _delete_system(self):
        if len(self._systems) <= 1:
            return
        name = self._systems[self._current_system_idx].get('name', '当前系统')
        if not confirm_action(
            self, '删除系统配置',
            f'即将删除系统“{name}”的 SQL 配置。\n\n已有需求数据不会被删除，但升级前必须重新选择有效系统，是否继续？',
        ):
            return
        self._systems.pop(self._current_system_idx)
        self._current_system_idx = max(0, self._current_system_idx - 1)
        save_systems(self._systems)
        self._load_systems()

    def _load_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'SQL', '', 'SQL (*.sql *.txt);;All files (*.*)')
        if not paths:
            return
        if not self._confirm_mixed_sources('file'):
            return
        try:
            parts = [(os.path.basename(path), read_file_auto_encoding(path)) for path in paths]
            self._append_sql_parts(parts, 'file')
            self._set_status_label(
                self.status,
                f'已载 {len(paths)} 个文件' if self.language == 'zh' else f'Loaded {len(paths)} file(s)',
                f'已加载 {len(paths)} 个 SQL 文件' if self.language == 'zh' else f'Loaded {len(paths)} SQL files',
                max_chars=14,
            )
            self._validate_input(show_popup=True)
        except OSError as exc:
            show_error(self, 'PengTools', str(exc))

    def _paste_sql(self):
        text = QApplication.clipboard().text()
        if not text or not self._confirm_mixed_sources('paste'):
            return
        self._paste_count += 1
        label = f'粘贴内容 {self._paste_count}' if self.language == 'zh' else f'Pasted content {self._paste_count}'
        self._append_sql_parts([(label, text)], 'paste')
        self._validate_input(show_popup=True)

    def _confirm_mixed_sources(self, incoming):
        opposite_exists = self._has_paste_input if incoming == 'file' else self._has_file_input
        if not opposite_exists or self._mixed_source_confirmed:
            return True
        zh = self.language == 'zh'
        accepted = confirm_action(
            self, 'PengTools · SQL',
            ('检测到上传文件和粘贴 SQL 将同时参与整理。继续后会合并全部语句、自动去重，再分别生成一个 DDL 和一个 DML 升级文件，是否继续？'
             if zh else
             'Uploaded files and pasted SQL will be processed together. All statements will be merged, deduplicated, then written to one DDL and one DML upgrade file. Continue?'),
            confirm_text='继续合并' if zh else 'Continue',
            danger=False,
        )
        self._mixed_source_confirmed = accepted
        return self._mixed_source_confirmed

    def _append_sql_parts(self, parts, source_kind):
        blocks = []
        for label, content in parts:
            content = content.strip()
            if content:
                blocks.append(f'-- 来源：{label}\n{content}')
        if not blocks:
            return
        current = self.input_sql.toPlainText().strip()
        combined = '\n\n'.join(([current] if current else []) + blocks)
        self.input_sql.setPlainText(combined)
        self._has_file_input = self._has_file_input or source_kind == 'file'
        self._has_paste_input = self._has_paste_input or source_kind == 'paste'

    def _reset_sources_if_empty(self):
        if self.input_sql.toPlainText().strip():
            return
        self._has_file_input = False
        self._has_paste_input = False
        self._mixed_source_confirmed = False
        self._paste_count = 0

    def _clear_sql(self):
        self.input_sql.clear()
        for editor in (self.upgrade_preview, self.rollback_preview, self.validation_preview):
            editor.clear()

    def _prepared_sql(self):
        return deduplicate_sql_statements(self.input_sql.toPlainText())

    def _choose_root(self):
        path = QFileDialog.getExistingDirectory(self, 'Output', self.output_root.text())
        if path:
            self.output_root.setText(path)

    def _env_name(self):
        system = self._get_system()
        return system.get('sim_env_name', '模拟环境') if self.env_combo.currentIndex() == 0 else system.get('prod_env_name', '生产环境')

    def _date_str(self):
        return self.date_edit.date().toString('yyyyMMdd')

    def _analyze(self):
        sql, duplicates = self._prepared_sql()
        if not sql.strip():
            return
        kind = classify_sql_type(sql)
        warnings = validate_oracle_sql(sql)
        message = (
            f'{kind} · 提示 {len(warnings)} · 去重 {len(duplicates)}'
            if self.language == 'zh' else
            f'{kind} · {len(warnings)} issue(s) · {len(duplicates)} dup(s)'
        )
        tip = (
            f'{kind} · {len(warnings)} 个提示 · 已过滤 {len(duplicates)} 条重复 SQL'
            if self.language == 'zh' else
            f'{kind} · {len(warnings)} issue(s) · {len(duplicates)} duplicate(s) removed'
        )
        self._set_status_label(self.status, message, tip, max_chars=18)
        if warnings:
            self.validation_preview.setPlainText('\n'.join(warnings))
            self.preview_tabs.setCurrentIndex(2)

    def _validate_input(self, show_popup=False):
        sql = self.input_sql.toPlainText()
        if not sql.strip():
            return []
        issues = validate_oracle_sql_detailed(sql)
        zh = self.language == 'zh'
        errors = [item for item in issues if item['severity'] == 'error']
        lines = []
        for issue in issues:
            level = ('错误' if issue['severity'] == 'error' else '提醒') if zh else issue['severity'].upper()
            message = issue['message_zh' if zh else 'message_en']
            lines.append(f"[{level}] SQL {issue['statement']}: {message}")
        if lines:
            lines.append(
                '\n说明：这是离线轻量检查，不能替代 Oracle 数据库实际编译。'
                if zh else '\nNote: offline lightweight validation cannot replace Oracle compilation.'
            )
            self.validation_preview.setPlainText('\n'.join(lines))
            self.preview_tabs.setCurrentIndex(2)
            self._set_status_label(
                self.status,
                f'预检 错{len(errors)} 提{len(issues) - len(errors)}' if zh else f'Precheck {len(errors)}E/{len(issues) - len(errors)}W',
                f'语法预检：{len(errors)} 个错误，{len(issues) - len(errors)} 个提醒'
                if zh else f'Precheck: {len(errors)} error(s), {len(issues) - len(errors)} warning(s)',
                max_chars=16,
            )
        else:
            self._set_status_label(
                self.status,
                '预检通过' if zh else 'Precheck OK',
                'SQL 轻量语法预检通过' if zh else 'SQL lightweight precheck passed',
                max_chars=10,
            )
        if show_popup and errors:
            preview = '\n'.join(lines[:6])
            if len(errors) > 6:
                preview += f"\n... {'另有' if zh else 'plus'} {len(errors) - 6}"
            show_warning(
                self, 'PengTools · SQL',
                (f'发现可能不正确的 SQL：\n\n{preview}\n\n请修改后再生成脚本。'
                 if zh else f'Potential SQL errors found:\n\n{preview}\n\nFix them before generating scripts.'),
            )
        return issues

    def _preview_package(self):
        sql, duplicates = self._prepared_sql()
        system = self._get_system()
        if not sql.strip() or not system:
            return []
        self.progress.set_progress(18, '正在拆分 DDL / DML 数据流…' if self.language == 'zh' else 'Splitting DDL / DML streams…')
        QApplication.processEvents()
        artifacts = build_sql_package(sql, system, self._env_name(), self._date_str())
        self.progress.set_progress(72, '正在编排升级、回滚与验证脚本…' if self.language == 'zh' else 'Composing upgrade, rollback, and verification scripts…')
        QApplication.processEvents()
        editors = {'upgrade': self.upgrade_preview, 'rollback': self.rollback_preview, 'validation': self.validation_preview}
        for kind, editor in editors.items():
            parts = []
            for artifact in artifacts:
                if artifact['kind'] == kind:
                    parts.append(f'===== {artifact["relative_path"]} =====\n{artifact["content"]}')
            editor.setPlainText('\n\n'.join(parts))
        self._set_status_label(
            self.status,
            f'预览 {len(artifacts)} · 去重 {len(duplicates)}' if self.language == 'zh' else f'Preview {len(artifacts)}; -{len(duplicates)} dup',
            f'已生成 {len(artifacts)} 个文件预览，过滤 {len(duplicates)} 条重复 SQL'
            if self.language == 'zh' else
            f'Previewed {len(artifacts)} files; removed {len(duplicates)} duplicate(s)',
            max_chars=16,
        )
        self.progress.finish('脚本拓扑已生成' if self.language == 'zh' else 'Script topology generated')
        return artifacts

    def _export_package(self):
        sql, _ = self._prepared_sql()
        system = self._get_system()
        root = self.output_root.text().strip()
        if not sql.strip() or not system or not root:
            show_warning(self, 'PengTools', '请填写 SQL、系统和输出根目录。' if self.language == 'zh' else 'Enter SQL, system and output root.')
            return
        issues = self._validate_input(show_popup=False)
        errors = [item for item in issues if item['severity'] == 'error']
        if errors:
            zh = self.language == 'zh'
            if not confirm_action(
                self, 'PengTools · SQL',
                (f'轻量语法检查发现 {len(errors)} 个明确问题。复杂 Oracle 语法可能出现误报，是否仍然继续导出？'
                 if zh else
                 f'Lightweight validation found {len(errors)} error(s). Complex Oracle syntax may be a false positive. Export anyway?'),
                confirm_text='仍要导出' if zh else 'Export anyway',
                danger=False,
            ):
                return
        self._sync_form()
        self.export_btn.setEnabled(False)
        self.progress.start_busy('正在构建 SVN 交付矩阵…' if self.language == 'zh' else 'Building the SVN delivery matrix…')
        self._export_worker = SqlExportWorker(root, sql, dict(system), self._env_name(), self._date_str())
        self._export_worker.completed.connect(self._on_export_completed)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.start()

    def _on_export_completed(self, paths):
        self._preview_package()
        self._set_status_label(
            self.status,
            f'已导出 {len(paths)} 个' if self.language == 'zh' else f'Exported {len(paths)}',
            f'已导出 {len(paths)} 个文件' if self.language == 'zh' else f'Exported {len(paths)} files',
            max_chars=12,
        )
        self.task_completed.emit()
        show_success(
            self, 'PengTools',
            ('文件已整理到：\n' if self.language == 'zh' else 'Files exported to:\n')
            + os.path.join(self.output_root.text().strip(), self._date_str())
        )

    def _on_export_failed(self, message):
        self.progress.fail('导出失败，请检查路径' if self.language == 'zh' else 'Export failed; check the path')
        show_error(self, 'PengTools', message)

    def _on_export_finished(self):
        self.export_btn.setEnabled(True)
        if self._export_worker:
            self._export_worker.deleteLater()
            self._export_worker = None

    def refresh_config(self):
        self._systems = load_systems()
        self._load_systems()
