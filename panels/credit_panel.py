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
    ORG_TYPES, PROVINCES, generate_batch, generate_company_name, validate_code,
)
from tools.id_documents import (
    DOCUMENT_TYPES, generate_person_name, generate_personal_batch,
    validate_personal_document,
)
from tools.china_regions import REGIONS
from ui.design_system import apply_button
from ui.field_metrics import CompactStepper, apply_caption, size_enum_combo, size_pick_combo


class CreditCodePanel(QWidget):
    """个人与单位证件模拟数据生成器；保留旧类名以兼容现有导航。"""

    def __init__(self):
        super().__init__()
        self.language = 'zh'
        self._results = []
        self._setup_ui()
        self.set_language('zh')

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        try:
            from ui.page_chrome import make_page_header
            header, self.title, self.subtitle = make_page_header(
                '证件类型',
                '离线测试数据，不落盘',
                'document-id',
            )
            root.addWidget(header)
        except Exception:
            self.title = QLabel()
            self.title.setObjectName('page-title')
            root.addWidget(self.title)
            self.subtitle = QLabel()
            self.subtitle.setObjectName('page-subtitle')
            root.addWidget(self.subtitle)
        self.page_title = self.title
        self.page_subtitle = self.subtitle

        self.category_tabs = QTabWidget()
        self.category_tabs.setObjectName('module-tabs')
        self.category_tabs.setDocumentMode(False)
        self.category_tabs.addTab(self._create_personal_tab(), '')
        self.category_tabs.addTab(self._create_unit_tab(), '')
        self.category_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.category_tabs.currentChanged.connect(lambda *_: self._sync_tab_height())
        root.addWidget(self.category_tabs, 0)
        self._sync_tab_height()

        self.format_note = QLabel()
        self.format_note.setObjectName('path-note')
        self.format_note.setWordWrap(True)
        self.format_note.hide()  # 仅切换类型或非法输入时显示
        root.addWidget(self.format_note)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        try:
            from ui.design_system import apply_list_header, apply_table
            apply_table(self.table)
            apply_list_header(self.table.horizontalHeader())
        except Exception:
            pass
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 160)
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
        if kind == 'resident_id' and self.personal_mode.currentIndex() == 1:
            options = {
                'area_code': self.id_district.currentData(),
                'min_age': self.id_min_age.value(),
                'max_age': self.id_max_age.value(),
                'gender': self.id_gender.currentData(),
            }
        numbers = generate_personal_batch(kind, quantity, **options)
        self._results = [
            (index, kind, number, generate_person_name())
            for index, number in enumerate(numbers, 1)
        ]
        valid = sum(validate_personal_document(kind, number) for number in numbers)
        self._update_table()
        self._show_result(quantity, valid)

    def _generate_unit(self):
        quantity = self._quantity(self.unit_qty)
        if quantity is None:
            return
        province = ''
        org_type = ''
        if self.unit_mode.currentIndex() == 1:
            province = self.province_combo.currentText().split(' - ', 1)[0]
            org_type = self.org_type_combo.currentText().split(' - ', 1)[0]
        numbers = generate_batch(quantity, province=province, org_type=org_type)
        self._results = [
            (index, 'credit_code', number, generate_company_name())
            for index, number in enumerate(numbers, 1)
        ]
        valid = sum(validate_code(number) for number in numbers)
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
            self.unit_mode, self.province_combo, self.org_type_combo,
            self.id_province,
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
        height = max(72, int(current.sizeHint().height()) + int(bar_h) + 16)
        self.category_tabs.setMinimumHeight(height)
        self.category_tabs.setMaximumHeight(height)

    def _update_table(self):
        self.table.setRowCount(len(self._results))
        self.table.verticalHeader().setDefaultSectionSize(32)
        for row, (number, kind, document, name) in enumerate(self._results):
            self.table.setItem(row, 0, QTableWidgetItem(str(number)))
            self.table.setItem(row, 1, QTableWidgetItem(self._type_label(kind)))
            self.table.setItem(row, 2, QTableWidgetItem(document))
            self.table.setItem(row, 3, QTableWidgetItem(name))
            self.table.setRowHeight(row, 32)
        if self._results:
            self.table.scrollToTop()

    def _on_unit_mode_changed(self, index):
        self.unit_custom.setVisible(index == 1)
        self._sync_tab_height()

    def _on_personal_type_changed(self, *_):
        is_resident_id = self.personal_type.currentData() == 'resident_id'
        self.personal_mode_label.setVisible(is_resident_id)
        self.personal_mode.setVisible(is_resident_id)
        self.id_custom.setVisible(is_resident_id and self.personal_mode.currentIndex() == 1)
        # 切换类型时短暂展示格式说明
        if hasattr(self, 'format_note') and self.format_note.text():
            self.format_note.show()
        self._sync_tab_height()

    def _on_personal_mode_changed(self, index):
        self.id_custom.setVisible(self.personal_type.currentData() == 'resident_id' and index == 1)
        self._sync_tab_height()

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
        headers = ('证件类型', '证件号码', '模拟名称') if self.language == 'zh' else ('Document Type', 'Document Number', 'Mock Name')
        lines = ['\t'.join(headers)]
        lines.extend(f'{self._type_label(kind)}\t{document}\t{name}' for _, kind, document, name in self._results)
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
                writer.writerow(['Document Type', 'Document Number', 'Mock Name'])
                for _, kind, document, name in self._results:
                    writer.writerow([self._type_label(kind), document, name])
            self.result_label.setText(('已导出：' if self.language == 'zh' else 'Exported to ') + path)
        except OSError as exc:
            show_error(self, 'Export Failed', str(exc))

    def _clear(self):
        self._results = []
        self.table.setRowCount(0)
        self.result_label.setText('已清空' if self.language == 'zh' else 'Cleared')

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
        self.personal_generate.setText('生成' if zh else 'Generate')
        self.unit_mode_label.setText('模式' if zh else 'Mode')
        self.unit_mode.setItemText(0, '随机' if zh else 'Random')
        self.unit_mode.setItemText(1, '指定条件' if zh else 'Custom')
        self.unit_qty_label.setText('数量' if zh else 'Qty')
        self.unit_generate.setText('生成' if zh else 'Generate')
        self.province_label.setText('省份' if zh else 'Province')
        self.org_type_label.setText('机构类型' if zh else 'Org type')
        self.format_note.setText(
            '格式说明：身份证支持全随机或按真实省市区代码、年龄范围和性别定向生成，并计算 MOD 11-2 校验码；护照为 E+8 位数字；军官证、武警身份证件使用常见模拟格式。'
            if zh else
            'Formats: 18-character resident ID with MOD 11-2 check digit; passport E + 8 digits; military and armed-police documents use common Chinese business-system display formats.'
        )
        self.format_note.hide()
        self.format_note.setToolTip(self.format_note.text())
        self.table.setHorizontalHeaderLabels(
            ['序号', '证件类型', '证件号码', '模拟名称']
            if zh else ['#', 'Document Type', 'Document Number', 'Mock Name']
        )
        self.copy_btn.setText('复制全部' if zh else 'Copy All')
        self.export_btn.setText('导出 CSV' if zh else 'Export CSV')
        self.clear_btn.setText('清空' if zh else 'Clear')
        self._fit_fixed_combos()
        if self._results:
            self._update_table()
        else:
            self.result_label.setText('选择个人或单位证件后生成' if zh else 'Choose personal or unit documents to generate')
