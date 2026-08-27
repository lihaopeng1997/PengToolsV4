# -*- coding: utf-8 -*-
import csv

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.confirm_dialog import show_error, show_warning
from tools.credit_code import (
    BUSINESS_SCOPES, ORG_TYPES, PROVINCES, generate_unit_records, validate_code,
)
from tools.id_documents import (
    DOCUMENT_TYPES, ETHNIC_GROUPS, generate_personal_records,
    validate_personal_document,
)
from tools.china_regions import REGIONS
from ui.design_system import apply_button
from ui.field_metrics import CompactStepper, apply_caption, size_enum_combo, size_pick_combo
from ui.page_chrome import make_page_header


class CreditCodePanel(QWidget):
    """个人与单位证件模拟数据生成器；保留旧类名以兼容现有导航。"""

    PERSONAL_HEADERS_ZH = (
        '序号', '证件类型', '姓名', '证件号码', '民族', '有效起期', '有效止期',
        '签发机关', '手机号', '电子邮箱', '邮政编码', '身份证地址',
    )
    PERSONAL_HEADERS_EN = (
        '#', 'Type', 'Name', 'Number', 'Ethnicity', 'Valid from', 'Valid to',
        'Issuer', 'Mobile', 'Email', 'Postcode', 'Address',
    )
    PERSONAL_KEYS = (
        'index', 'type', 'name', 'document', 'ethnicity', 'valid_from', 'valid_to',
        'issuer', 'mobile', 'email', 'postal_code', 'address',
    )
    UNIT_HEADERS_ZH = (
        '序号', '证件类型', '企业名称', '统一社会信用代码', '有效起期', '有效止期',
        '经营范围', '企业电话', '企业邮箱', '企业邮编', '企业地址',
    )
    UNIT_HEADERS_EN = (
        '#', 'Type', 'Company', 'USCC', 'Valid from', 'Valid to',
        'Scope', 'Phone', 'Email', 'Postcode', 'Address',
    )
    UNIT_KEYS = (
        'index', 'type', 'name', 'document', 'valid_from', 'valid_to',
        'business_scope', 'phone', 'email', 'postal_code', 'address',
    )

    def __init__(self):
        super().__init__()
        self.language = 'zh'
        self._personal_results = []
        self._unit_results = []
        self._setup_ui()
        self.set_language('zh')

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header, self.title, self.subtitle = make_page_header(
            '证件类型',
            '离线测试数据，不落盘',
            'document-id',
        )
        root.addWidget(header)
        self.page_title = self.title
        self.page_subtitle = self.subtitle

        self.category_tabs = QTabWidget()
        self.category_tabs.setObjectName('module-tabs')
        self.category_tabs.setDocumentMode(False)
        self.category_tabs.addTab(self._create_personal_tab(), '')
        self.category_tabs.addTab(self._create_unit_tab(), '')
        self.category_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.category_tabs.currentChanged.connect(self._on_category_tab_changed)
        root.addWidget(self.category_tabs, 0)
        self._sync_tab_height()

        self.format_note = QLabel()
        self.format_note.setObjectName('path-note')
        self.format_note.setWordWrap(True)
        self.format_note.hide()  # 仅切换类型或非法输入时显示
        root.addWidget(self.format_note)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.PERSONAL_HEADERS_ZH))
        try:
            from ui.design_system import apply_list_header, apply_table
            apply_table(self.table)
            apply_list_header(self.table.horizontalHeader())
        except Exception:
            pass
        self._apply_column_widths(len(self.PERSONAL_HEADERS_ZH))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setMinimumHeight(280)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.verticalHeader().setMinimumSectionSize(32)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.itemDoubleClicked.connect(self._copy_cell)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.result_label = QLabel()
        self.result_label.setObjectName('small-label')
        bottom.addWidget(self.result_label)
        bottom.addStretch()
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True)
        self.copy_btn.clicked.connect(self._copy_all)
        bottom.addWidget(self.copy_btn)
        self.export_btn = QPushButton()
        apply_button(self.export_btn, 'secondary', compact=True)
        self.export_btn.clicked.connect(self._export_csv)
        bottom.addWidget(self.export_btn)
        self.clear_btn = QPushButton()
        apply_button(self.clear_btn, 'ghost', compact=True)
        self.clear_btn.clicked.connect(self._clear)
        bottom.addWidget(self.clear_btn)
        root.addLayout(bottom)

    @staticmethod
    def _filter_card():
        card = QFrame()
        card.setObjectName('credit-filter-card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        return card, layout

    def _create_personal_tab(self):
        tab = QWidget()
        tab.setObjectName('credit-tab')
        tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        card, layout = self._filter_card()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer.addWidget(card)

        first = QHBoxLayout()
        first.setSpacing(8)
        self.personal_type_label = QLabel()
        apply_caption(self.personal_type_label)
        first.addWidget(self.personal_type_label)
        self.personal_type = QComboBox()
        for key, labels in DOCUMENT_TYPES.items():
            self.personal_type.addItem(labels[0], key)
        size_enum_combo(self.personal_type)
        self.personal_type.currentIndexChanged.connect(self._on_personal_type_changed)
        first.addWidget(self.personal_type)
        self.personal_mode_label = QLabel()
        apply_caption(self.personal_mode_label)
        first.addWidget(self.personal_mode_label)
        self.personal_mode = QComboBox()
        self.personal_mode.addItems(['全随机', '指定条件'])
        size_enum_combo(self.personal_mode)
        self.personal_mode.currentIndexChanged.connect(self._on_personal_mode_changed)
        first.addWidget(self.personal_mode)
        self.personal_qty_label = QLabel()
        apply_caption(self.personal_qty_label)
        first.addWidget(self.personal_qty_label)
        self.personal_qty = self._quantity_box()
        first.addWidget(self.personal_qty)
        self.personal_generate = QPushButton()
        apply_button(self.personal_generate, 'primary', compact=True)
        self.personal_generate.clicked.connect(self._generate_personal)
        first.addWidget(self.personal_generate)
        first.addStretch(1)
        layout.addLayout(first)

        self.id_custom = QWidget()
        self.id_custom.setObjectName('credit-tab')
        custom = QVBoxLayout(self.id_custom)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.setSpacing(8)

        region = QHBoxLayout()
        region.setSpacing(8)
        self.id_province_label = QLabel()
        apply_caption(self.id_province_label)
        region.addWidget(self.id_province_label)
        self.id_province = QComboBox()
        size_enum_combo(self.id_province)
        self.id_province.currentIndexChanged.connect(self._load_id_cities)
        region.addWidget(self.id_province)
        self.id_city_label = QLabel()
        apply_caption(self.id_city_label)
        region.addWidget(self.id_city_label)
        self.id_city = QComboBox()
        size_pick_combo(self.id_city)
        self.id_city.currentIndexChanged.connect(self._load_id_districts)
        region.addWidget(self.id_city)
        self.id_district_label = QLabel()
        apply_caption(self.id_district_label)
        region.addWidget(self.id_district_label)
        self.id_district = QComboBox()
        size_pick_combo(self.id_district)
        region.addWidget(self.id_district)
        region.addStretch(1)
        custom.addLayout(region)

        demo = QHBoxLayout()
        demo.setSpacing(8)
        self.id_age_label = QLabel()
        apply_caption(self.id_age_label)
        demo.addWidget(self.id_age_label)
        self.id_min_age = CompactStepper(0, 120, 18)
        self.id_min_age.valueChanged.connect(self._sync_age_range)
        demo.addWidget(self.id_min_age)
        self.id_age_separator = QLabel('—')
        demo.addWidget(self.id_age_separator)
        self.id_max_age = CompactStepper(18, 120, 60)
        demo.addWidget(self.id_max_age)
        self.id_gender_label = QLabel()
        apply_caption(self.id_gender_label)
        demo.addWidget(self.id_gender_label)
        self.id_gender = QComboBox()
        self.id_gender.addItem('随机', 'random')
        self.id_gender.addItem('男', 'male')
        self.id_gender.addItem('女', 'female')
        size_enum_combo(self.id_gender)
        demo.addWidget(self.id_gender)
        demo.addStretch(1)
        custom.addLayout(demo)

        extra = QHBoxLayout()
        extra.setSpacing(8)
        self.id_nation_label = QLabel()
        apply_caption(self.id_nation_label)
        extra.addWidget(self.id_nation_label)
        self.id_nation = QComboBox()
        self.id_nation.addItem('随机', '')
        for name in ETHNIC_GROUPS:
            self.id_nation.addItem(name, name)
        size_enum_combo(self.id_nation)
        extra.addWidget(self.id_nation)
        self.id_term_label = QLabel()
        apply_caption(self.id_term_label)
        extra.addWidget(self.id_term_label)
        self.id_term = QComboBox()
        self.id_term.addItem('按年龄规则', '')
        self.id_term.addItem('5年', 5)
        self.id_term.addItem('10年', 10)
        self.id_term.addItem('20年', 20)
        self.id_term.addItem('长期', 'long')
        size_enum_combo(self.id_term)
        extra.addWidget(self.id_term)
        extra.addStretch(1)
        custom.addLayout(extra)
        self._id_region_row = region
        self._id_demo_row = demo
        layout.addWidget(self.id_custom)
        self._load_id_provinces()
        self._on_personal_type_changed()
        return tab

    def _create_unit_tab(self):
        tab = QWidget()
        tab.setObjectName('credit-tab')
        tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        card, layout = self._filter_card()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer.addWidget(card)

        first = QHBoxLayout()
        first.setSpacing(8)
        self.unit_mode_label = QLabel()
        apply_caption(self.unit_mode_label)
        first.addWidget(self.unit_mode_label)
        self.unit_mode = QComboBox()
        self.unit_mode.addItems(['随机', '指定条件'])
        size_enum_combo(self.unit_mode)
        self.unit_mode.currentIndexChanged.connect(self._on_unit_mode_changed)
        first.addWidget(self.unit_mode)
        self.unit_qty_label = QLabel()
        apply_caption(self.unit_qty_label)
        first.addWidget(self.unit_qty_label)
        self.unit_qty = self._quantity_box()
        first.addWidget(self.unit_qty)
        self.unit_generate = QPushButton()
        apply_button(self.unit_generate, 'primary', compact=True)
        self.unit_generate.clicked.connect(self._generate_unit)
        first.addWidget(self.unit_generate)
        first.addStretch(1)
        layout.addLayout(first)

        self.unit_custom = QWidget()
        self.unit_custom.setObjectName('credit-tab')
        custom = QHBoxLayout(self.unit_custom)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.setSpacing(8)
        self.province_label = QLabel()
        apply_caption(self.province_label)
        custom.addWidget(self.province_label)
        self.province_combo = QComboBox()
        self.province_combo.addItems([f'{key} - {value}' for key, value in sorted(PROVINCES.items())])
        size_enum_combo(self.province_combo)
        custom.addWidget(self.province_combo)
        self.org_type_label = QLabel()
        apply_caption(self.org_type_label)
        custom.addWidget(self.org_type_label)
        self.org_type_combo = QComboBox()
        self.org_type_combo.addItems([f'{key} - {value}' for key, value in sorted(ORG_TYPES.items())])
        size_enum_combo(self.org_type_combo)
        custom.addWidget(self.org_type_combo)
        self.unit_scope_label = QLabel()
        apply_caption(self.unit_scope_label)
        custom.addWidget(self.unit_scope_label)
        self.unit_scope = QComboBox()
        self.unit_scope.addItem('随机', '')
        for scope in BUSINESS_SCOPES:
            self.unit_scope.addItem(scope.split('；', 1)[0], scope)
        size_enum_combo(self.unit_scope)
        custom.addWidget(self.unit_scope)
        self.unit_term_label = QLabel()
        apply_caption(self.unit_term_label)
        custom.addWidget(self.unit_term_label)
        self.unit_term = QComboBox()
        self.unit_term.addItem('随机', '')
        self.unit_term.addItem('3年', 3)
        self.unit_term.addItem('5年', 5)
        self.unit_term.addItem('10年', 10)
        self.unit_term.addItem('长期', 'long')
        size_enum_combo(self.unit_term)
        custom.addWidget(self.unit_term)
        custom.addStretch(1)
        self.unit_custom.hide()
        layout.addWidget(self.unit_custom)
        return tab

    @staticmethod
    def _quantity_box():
        return CompactStepper(1, 200, 10)

    def _quantity(self, box):
        try:
            return max(1, min(200, int(box.value() if hasattr(box, 'value') else box.text())))
        except ValueError:
            show_warning(
                self, '数量无效' if self.language == 'zh' else 'Invalid Quantity',
                '请输入 1 到 200 之间的数字。' if self.language == 'zh' else 'Enter a number from 1 to 200.',
            )
            return None

    def _generate_personal(self):
        quantity = self._quantity(self.personal_qty)
        if quantity is None:
            return
        kind = self.personal_type.currentData()
        options = {}
        if self.personal_mode.currentIndex() == 1:
            options['ethnicity'] = self.id_nation.currentData() or ''
            options['valid_term'] = self.id_term.currentData()
            if kind == 'resident_id':
                options.update({
                    'area_code': self.id_district.currentData(),
                    'min_age': self.id_min_age.value(),
                    'max_age': self.id_max_age.value(),
                    'gender': self.id_gender.currentData(),
                })
        records = generate_personal_records(kind, quantity, **options)
        self._personal_results = records
        valid = sum(validate_personal_document(kind, item['document']) for item in records)
        self._update_table()
        self._show_result(quantity, valid)

    def _generate_unit(self):
        quantity = self._quantity(self.unit_qty)
        if quantity is None:
            return
        province = org_type = business = ''
        valid_term = None
        if self.unit_mode.currentIndex() == 1:
            province = self.province_combo.currentText().split(' - ', 1)[0]
            org_type = self.org_type_combo.currentText().split(' - ', 1)[0]
            business = self.unit_scope.currentData() or ''
            valid_term = self.unit_term.currentData()
        records = generate_unit_records(
            quantity, province=province, org_type=org_type,
            business=business, valid_term=valid_term,
        )
        self._unit_results = records
        valid = sum(validate_code(item['document']) for item in records)
        self._update_table()
        self._show_result(quantity, valid)

    def _show_result(self, quantity, valid):
        self.result_label.setText(
            f'已生成 {quantity} 条（{valid} 条格式有效）'
            if self.language == 'zh' else
            f'Generated {quantity} records ({valid} format-valid)'
        )

    def _type_label(self, kind):
        if kind == 'credit_code':
            return '统一社会信用代码' if self.language == 'zh' else 'Unified Social Credit Code'
        return DOCUMENT_TYPES[kind][0 if self.language == 'zh' else 1]

    def _fit_fixed_combos(self):
        for combo in (
            self.personal_type, self.personal_mode, self.id_gender,
            self.id_nation, self.id_term, self.unit_mode, self.province_combo,
            self.org_type_combo, self.unit_scope, self.unit_term, self.id_province,
        ):
            if combo is not None and combo.count():
                size_enum_combo(combo)
        size_pick_combo(self.id_city)
        size_pick_combo(self.id_district)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_fixed_combos()
        self._sync_tab_height()

    def _sync_tab_height(self):
        """QTabWidget 默认按最高一页定高，个人指定条件不能把单位页一起撑高。"""
        current = self.category_tabs.currentWidget()
        if current is None:
            return
        current.adjustSize()
        bar = self.category_tabs.tabBar()
        bar_h = bar.sizeHint().height() if bar is not None else 28
        height = max(72, min(360, int(current.sizeHint().height()) + int(bar_h) + 16))
        self.category_tabs.setMinimumHeight(height)
        self.category_tabs.setMaximumHeight(height)

    @property
    def _results(self):
        if getattr(self, 'category_tabs', None) is not None and self.category_tabs.currentIndex() == 1:
            return self._unit_results
        return self._personal_results

    def _result_kind(self):
        if getattr(self, 'category_tabs', None) is not None and self.category_tabs.currentIndex() == 1:
            return 'unit'
        return 'personal'

    def _on_category_tab_changed(self, *_):
        self._sync_tab_height()
        self._update_table()
        if self._results:
            self._show_result(len(self._results), len(self._results))
        else:
            zh = self.language == 'zh'
            self.result_label.setText('选择个人或单位证件后生成' if zh else 'Choose personal or unit documents to generate')

    def _headers_and_keys(self):
        zh = self.language == 'zh'
        if self._result_kind() == 'unit':
            headers = self.UNIT_HEADERS_ZH if zh else self.UNIT_HEADERS_EN
            return headers, self.UNIT_KEYS
        headers = self.PERSONAL_HEADERS_ZH if zh else self.PERSONAL_HEADERS_EN
        return headers, self.PERSONAL_KEYS

    def _apply_column_widths(self, count):
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 48)
        for column in range(1, count):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)

    def _fit_table_to_row(self):
        """内容不够宽则按比例铺满；超出则可横向拖动查看。"""
        table = self.table
        count = table.columnCount()
        if count <= 0:
            return
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        for column in range(1, count):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.resizeColumnsToContents()
        table.setColumnWidth(0, 48)
        used = sum(table.columnWidth(column) for column in range(count))
        viewport = table.viewport().width() if table.viewport() else table.width()
        leftover = viewport - used
        if leftover > 8 and count > 1:
            weights = [max(table.columnWidth(column), 1) for column in range(1, count)]
            total = sum(weights) or 1
            for offset, weight in enumerate(weights):
                column = offset + 1
                extra = int(leftover * weight / total)
                table.setColumnWidth(column, table.columnWidth(column) + extra)
        for column in range(1, count):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 48)

    def _record_value(self, record, key, index):
        if key == 'index':
            return str(index)
        if key == 'type':
            return self._type_label(record.get('kind'))
        return str(record.get(key) or '')

    def _update_table(self):
        headers, keys = self._headers_and_keys()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self._apply_column_widths(len(headers))
        self.table.setRowCount(len(self._results))
        self.table.verticalHeader().setDefaultSectionSize(32)
        for row, record in enumerate(self._results):
            for column, key in enumerate(keys):
                item = QTableWidgetItem(self._record_value(record, key, row + 1))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 32)
        try:
            from ui.design_system import finish_result_rows
            finish_result_rows(self.table)
        except Exception:
            if self._results:
                self.table.scrollToTop()
        self._fit_table_to_row()

    def _on_unit_mode_changed(self, index):
        self.unit_custom.setVisible(index == 1)
        self._sync_tab_height()

    def _on_personal_type_changed(self, *_):
        is_resident_id = self.personal_type.currentData() == 'resident_id'
        custom = self.personal_mode.currentIndex() == 1
        self.id_custom.setVisible(custom)
        for widget in (
            self.id_province_label, self.id_province, self.id_city_label, self.id_city,
            self.id_district_label, self.id_district, self.id_age_label, self.id_min_age,
            self.id_age_separator, self.id_max_age, self.id_gender_label, self.id_gender,
        ):
            widget.setVisible(custom and is_resident_id)
        if hasattr(self, 'format_note') and self.format_note.text():
            self.format_note.show()
        self._sync_tab_height()

    def _on_personal_mode_changed(self, index):
        self._on_personal_type_changed()

    def _load_id_provinces(self):
        self.id_province.blockSignals(True)
        self.id_province.clear()
        for code, (name, _) in REGIONS.items():
            self.id_province.addItem(name, code)
        self.id_province.blockSignals(False)
        size_enum_combo(self.id_province)
        self._load_id_cities()

    def _load_id_cities(self, *_):
        province = REGIONS.get(self.id_province.currentData())
        self.id_city.blockSignals(True)
        self.id_city.clear()
        if province:
            for code, (name, _) in province[1].items():
                self.id_city.addItem(name, code)
        self.id_city.blockSignals(False)
        size_pick_combo(self.id_city)
        self._load_id_districts()

    def _load_id_districts(self, *_):
        province = REGIONS.get(self.id_province.currentData())
        city = province[1].get(self.id_city.currentData()) if province else None
        self.id_district.clear()
        if city:
            for code, name in city[1].items():
                self.id_district.addItem(name, code)
        size_pick_combo(self.id_district)

    def _sync_age_range(self, minimum):
        self.id_max_age.setMinimum(minimum)

    def _copy_all(self):
        if not self._results:
            return
        headers, keys = self._headers_and_keys()
        lines = ['\t'.join(headers)]
        for index, record in enumerate(self._results, 1):
            lines.append('\t'.join(self._record_value(record, key, index) for key in keys))
        QApplication.clipboard().setText('\n'.join(lines))
        self.result_label.setText('已复制到剪贴板' if self.language == 'zh' else 'Copied to clipboard')

    def _copy_cell(self, item):
        QApplication.clipboard().setText(item.text())
        self.result_label.setText(f'已复制：{item.text()}' if self.language == 'zh' else f'Copied: {item.text()}')

    def _export_csv(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export CSV', '', 'CSV Files (*.csv)')
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                headers, keys = self._headers_and_keys()
                writer.writerow(headers)
                for index, record in enumerate(self._results, 1):
                    writer.writerow([self._record_value(record, key, index) for key in keys])
            self.result_label.setText(('已导出：' if self.language == 'zh' else 'Exported to ') + path)
        except OSError as exc:
            show_error(self, 'Export Failed', str(exc))

    def _clear(self):
        if self.category_tabs.currentIndex() == 1:
            self._unit_results = []
        else:
            self._personal_results = []
        self._update_table()
        self.result_label.setText('已清空' if self.language == 'zh' else 'Cleared')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_table_to_row()

    def refresh_config(self):
        pass

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        set_subtitle_visible(getattr(self, 'subtitle', None), low_height)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.title.setText('证件类型' if zh else 'Documents')
        self.subtitle.setText('离线测试数据，不落盘' if zh else 'Offline test data. Nothing is saved.')
        self.category_tabs.setTabText(0, '个人证件' if zh else 'Personal Documents')
        self.category_tabs.setTabText(1, '单位证件' if zh else 'Unit Documents')
        self.personal_type_label.setText('证件类型' if zh else 'Type')
        for index, (_, labels) in enumerate(DOCUMENT_TYPES.items()):
            self.personal_type.setItemText(index, labels[0 if zh else 1])
        self.personal_qty_label.setText('数量' if zh else 'Qty')
        self.personal_mode_label.setText('模式' if zh else 'Mode')
        self.personal_mode.setItemText(0, '全随机' if zh else 'Fully random')
        self.personal_mode.setItemText(1, '指定条件' if zh else 'Custom')
        self.id_province_label.setText('省份' if zh else 'Province')
        self.id_city_label.setText('城市' if zh else 'City')
        self.id_district_label.setText('区县' if zh else 'District')
        self.id_age_label.setText('年龄' if zh else 'Age')
        self.id_gender_label.setText('性别' if zh else 'Gender')
        self.id_gender.setItemText(0, '随机' if zh else 'Random')
        self.id_gender.setItemText(1, '男' if zh else 'Male')
        self.id_gender.setItemText(2, '女' if zh else 'Female')
        self.id_nation_label.setText('民族' if zh else 'Ethnicity')
        self.id_nation.setItemText(0, '随机' if zh else 'Any')
        self.id_term_label.setText('有效期限' if zh else 'Validity')
        self.id_term.setItemText(0, '按年龄规则' if zh else 'By age')
        self.id_term.setItemText(1, '5年' if zh else '5 years')
        self.id_term.setItemText(2, '10年' if zh else '10 years')
        self.id_term.setItemText(3, '20年' if zh else '20 years')
        self.id_term.setItemText(4, '长期' if zh else 'Long-term')
        self.personal_generate.setText('生成' if zh else 'Generate')
        self.unit_mode_label.setText('模式' if zh else 'Mode')
        self.unit_mode.setItemText(0, '随机' if zh else 'Random')
        self.unit_mode.setItemText(1, '指定条件' if zh else 'Custom')
        self.unit_qty_label.setText('数量' if zh else 'Qty')
        self.unit_generate.setText('生成' if zh else 'Generate')
        self.province_label.setText('省份' if zh else 'Province')
        self.org_type_label.setText('机构类型' if zh else 'Org type')
        self.unit_scope_label.setText('经营范围' if zh else 'Scope')
        self.unit_scope.setItemText(0, '随机' if zh else 'Any')
        self.unit_term_label.setText('有效期限' if zh else 'Validity')
        self.unit_term.setItemText(0, '随机' if zh else 'Any')
        self.unit_term.setItemText(1, '3年' if zh else '3 years')
        self.unit_term.setItemText(2, '5年' if zh else '5 years')
        self.unit_term.setItemText(3, '10年' if zh else '10 years')
        self.unit_term.setItemText(4, '长期' if zh else 'Long-term')
        self.format_note.setText(
            '格式说明：身份证支持全随机或按真实省市区代码、年龄范围和性别定向生成，并计算 MOD 11-2 校验码；护照为 E+8 位数字；军官证、武警身份证件使用常见模拟格式。'
            if zh else
            'Formats: 18-character resident ID with MOD 11-2 check digit; passport E + 8 digits; military and armed-police documents use common Chinese business-system display formats.'
        )
        self.format_note.hide()
        self.format_note.setToolTip(self.format_note.text())
        headers, _keys = self._headers_and_keys()
        self.table.setHorizontalHeaderLabels(headers)
        self.copy_btn.setText('复制全部' if zh else 'Copy All')
        self.export_btn.setText('导出 CSV' if zh else 'Export CSV')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self._fit_fixed_combos()
        self._update_table()
        if not self._results:
            self.result_label.setText('选择个人或单位证件后生成' if zh else 'Choose personal or unit documents to generate')
