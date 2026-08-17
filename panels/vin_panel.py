# -*- coding: utf-8 -*-
import csv
import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHeaderView, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ui.confirm_dialog import show_error, show_warning
from tools.vin_generator import (
    CHINA_WMIS, PROVINCE_PREFIXES, VEHICLE_HEADERS_EN, VEHICLE_HEADERS_ZH,
    VehicleFilterError, generate_vehicle_batch, list_category_options,
    list_energy_options, list_kind_options, vehicle_row_values,
)
from ui.design_system import apply_button
from ui.field_metrics import CompactStepper, apply_caption, size_enum_combo


class VinPanel(QWidget):
    def __init__(self, language='zh'):
        super().__init__()
        self.language = language
        self._results = []
        self._setup_ui()
        self.set_language(language)
        self._pending_fill = True

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        try:
            from ui.page_chrome import make_page_header
            header, self.page_title, self.page_subtitle = make_page_header(
                '车辆 VIN',
                '离线测试数据，不落盘',
                'vin',
            )
            layout.addWidget(header)
        except Exception:
            self.page_title = None
            self.page_subtitle = None

        self.settings = QGroupBox()
        self.settings.setObjectName('vin-settings')
        self.settings.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        settings_l = QVBoxLayout(self.settings)
        settings_l.setContentsMargins(12, 12, 12, 12)
        settings_l.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.mode_label = QLabel()
        apply_caption(self.mode_label)
        row.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(['全随机', '指定条件'])
        size_enum_combo(self.mode_combo)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self.mode_combo)
        self.year_label = QLabel()
        apply_caption(self.year_label)
        row.addWidget(self.year_label)
        self.year_combo = QComboBox()
        self.year_combo.addItems([str(y) for y in range(2001, 2031)])
        self.year_combo.setCurrentText(str(datetime.date.today().year))
        size_enum_combo(self.year_combo)
        row.addWidget(self.year_combo)
        self.wmi_label = QLabel()
        apply_caption(self.wmi_label)
        row.addWidget(self.wmi_label)
        self.wmi_combo = QComboBox()
        self.wmi_combo.addItems(['AUTO'] + list(CHINA_WMIS))
        size_enum_combo(self.wmi_combo)
        row.addWidget(self.wmi_combo)
        self.qty_label = QLabel()
        apply_caption(self.qty_label)
        row.addWidget(self.qty_label)
        self.qty = CompactStepper(1, 200, 10)
        row.addWidget(self.qty)
        self.generate_btn = QPushButton()
        apply_button(self.generate_btn, 'primary', compact=True)
        self.generate_btn.clicked.connect(self._generate)
        row.addWidget(self.generate_btn)
        row.addStretch(1)
        settings_l.addLayout(row)

        self.custom = QWidget()
        custom = QHBoxLayout(self.custom)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.setSpacing(8)
        self.energy_label = QLabel()
        apply_caption(self.energy_label)
        custom.addWidget(self.energy_label)
        self.energy_combo = QComboBox()
        self.energy_combo.addItem('随机', '')
        for item in list_energy_options():
            self.energy_combo.addItem(item, item)
        size_enum_combo(self.energy_combo)
        custom.addWidget(self.energy_combo)
        self.category_label = QLabel()
        apply_caption(self.category_label)
        custom.addWidget(self.category_label)
        self.category_combo = QComboBox()
        self.category_combo.addItem('随机', '')
        for item in list_category_options():
            self.category_combo.addItem(item, item)
        size_enum_combo(self.category_combo)
        self.category_combo.currentIndexChanged.connect(self._refresh_kind_options)
        custom.addWidget(self.category_combo)
        self.kind_label = QLabel()
        apply_caption(self.kind_label)
        custom.addWidget(self.kind_label)
        self.kind_combo = QComboBox()
        self._refresh_kind_options()
        custom.addWidget(self.kind_combo)
        self.plate_label = QLabel()
        apply_caption(self.plate_label)
        custom.addWidget(self.plate_label)
        self.plate_combo = QComboBox()
        self.plate_combo.addItem('随机', '')
        for item in PROVINCE_PREFIXES:
            self.plate_combo.addItem(item, item)
        size_enum_combo(self.plate_combo)
        custom.addWidget(self.plate_combo)
        custom.addStretch(1)
        self.custom.hide()
        settings_l.addWidget(self.custom)
        layout.addWidget(self.settings)

        self.table = QTableWidget(0, len(VEHICLE_HEADERS_ZH))
        try:
            from ui.design_system import apply_list_header
            apply_list_header(self.table.horizontalHeader())
        except Exception:
            pass
        header = self.table.horizontalHeader()
        for column in range(len(VEHICLE_HEADERS_ZH)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if column == 3 else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(column, mode)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.horizontalHeader().setStretchLastSection(False)
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
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName('status-pill')
        bottom.addWidget(self.status)
        bottom.addStretch()
        self.copy_btn = QPushButton()
        apply_button(self.copy_btn, 'secondary', compact=True)
        self.copy_btn.clicked.connect(self._copy)
        bottom.addWidget(self.copy_btn)
        self.export_btn = QPushButton()
        apply_button(self.export_btn, 'ghost', compact=True)
        self.export_btn.clicked.connect(self._export)
        bottom.addWidget(self.export_btn)
        layout.addLayout(bottom)

    def apply_layout_mode(self, mode, low_height=False):
        from ui.responsive import set_subtitle_visible
        set_subtitle_visible(getattr(self, 'page_subtitle', None), low_height)
        set_subtitle_visible(getattr(self, 'subtitle', None), low_height)

    def set_language(self, language):
        self.language = language
        zh = language == 'zh'
        self.settings.setTitle('')
        if getattr(self, 'page_title', None) is not None:
            self.page_title.setText('车辆 VIN' if zh else 'Vehicle VIN')
        if getattr(self, 'page_subtitle', None) is not None:
            self.page_subtitle.setText(
                '离线测试数据，不落盘' if zh else 'Offline test data. Nothing is saved.'
            )
        self.mode_label.setText('模式' if zh else 'Mode')
        self.mode_combo.setItemText(0, '全随机' if zh else 'Random')
        self.mode_combo.setItemText(1, '指定条件' if zh else 'Custom')
        self.year_label.setText('车型年份' if zh else 'Model year')
        self.wmi_label.setText('制造商' if zh else 'Maker')
        self.qty_label.setText('数量' if zh else 'Qty')
        self.energy_label.setText('能源' if zh else 'Energy')
        self.category_label.setText('车辆大类' if zh else 'Category')
        self.kind_label.setText('车辆种类' if zh else 'Kind')
        self.plate_label.setText('号牌省份' if zh else 'Plate')
        self.energy_combo.setItemText(0, '随机' if zh else 'Any')
        self.category_combo.setItemText(0, '随机' if zh else 'Any')
        self.plate_combo.setItemText(0, '随机' if zh else 'Any')
        self.generate_btn.setText('生成' if zh else 'Generate')
        self.copy_btn.setText('复制全部' if zh else 'Copy all')
        self.export_btn.setText('导出 CSV' if zh else 'Export CSV')
        self.table.setHorizontalHeaderLabels(VEHICLE_HEADERS_ZH if zh else VEHICLE_HEADERS_EN)
        size_enum_combo(self.mode_combo)
        size_enum_combo(self.year_combo)
        size_enum_combo(self.wmi_combo)
        size_enum_combo(self.energy_combo)
        size_enum_combo(self.category_combo)
        size_enum_combo(self.kind_combo)
        size_enum_combo(self.plate_combo)

    def _visible_fill_count(self) -> int:
        try:
            return max(1, min(200, int(self.qty.value())))
        except Exception:
            return 10

    def showEvent(self, event):
        super().showEvent(event)
        size_enum_combo(self.year_combo)
        size_enum_combo(self.wmi_combo)
        if self._pending_fill:
            self._pending_fill = False
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._generate)

    def _on_mode_changed(self, index):
        self.custom.setVisible(index == 1)

    def _refresh_kind_options(self, *_):
        current = self.kind_combo.currentData() if hasattr(self, 'kind_combo') and self.kind_combo.count() else ''
        self.kind_combo.blockSignals(True)
        self.kind_combo.clear()
        self.kind_combo.addItem('随机' if self.language == 'zh' else 'Any', '')
        for item in list_kind_options(self.category_combo.currentData() or ''):
            self.kind_combo.addItem(item, item)
        idx = self.kind_combo.findData(current)
        self.kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.kind_combo.blockSignals(False)
        size_enum_combo(self.kind_combo)

    def _combo_filter(self, combo):
        value = combo.currentData()
        return '' if value is None else str(value)

    def _generate(self):
        year = int(self.year_combo.currentText())
        wmi = self.wmi_combo.currentText()
        count = self._visible_fill_count()
        energy = category = kind = plate = ''
        if self.mode_combo.currentIndex() == 1:
            energy = self._combo_filter(self.energy_combo)
            category = self._combo_filter(self.category_combo)
            kind = self._combo_filter(self.kind_combo)
            plate = self._combo_filter(self.plate_combo)
        try:
            self._results = generate_vehicle_batch(
                count, year, '' if wmi == 'AUTO' else wmi,
                energy=energy, category=category, kind=kind, plate_province=plate,
            )
        except VehicleFilterError as exc:
            show_warning(self, '车辆 VIN' if self.language == 'zh' else 'VIN', str(exc))
            return
        self.table.setRowCount(len(self._results))
        self.table.verticalHeader().setDefaultSectionSize(32)
        for row, record in enumerate(self._results):
            values = vehicle_row_values(record, row + 1)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 4, 8, 9):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 32)
        self.table.scrollToTop()
        self.status.setText(
            f'{len(self._results)} 条' if self.language == 'zh' else f'{len(self._results)} rows'
        )
        self.status.setToolTip(
            'VIN 按 GB 16735；号牌按 GA 36（新能源 D/F）' if self.language == 'zh'
            else 'VIN per GB 16735; plates per GA 36'
        )

    def _copy(self):
        if not self._results:
            return
        lines = ['\t'.join(VEHICLE_HEADERS_ZH if self.language == 'zh' else VEHICLE_HEADERS_EN)]
        lines.extend('\t'.join(vehicle_row_values(record, index)) for index, record in enumerate(self._results, 1))
        QApplication.clipboard().setText('\n'.join(lines))
        self.status.setText('已复制到剪贴板' if self.language == 'zh' else 'Copied to clipboard')

    def _copy_cell(self, item):
        QApplication.clipboard().setText(item.text())
        self.status.setText(
            f'已复制：{item.text()}' if self.language == 'zh' else f'Copied: {item.text()}'
        )

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'CSV', 'vin_test_data.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                writer.writerow(VEHICLE_HEADERS_ZH if self.language == 'zh' else VEHICLE_HEADERS_EN)
                writer.writerows(vehicle_row_values(record, index) for index, record in enumerate(self._results, 1))
            self.status.setText(path)
        except OSError as exc:
            show_error(self, 'Error', str(exc))
