# -*- coding: utf-8 -*-
import csv
import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHeaderView, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ui.confirm_dialog import show_error
from tools.vin_generator import CHINA_WMIS, generate_vin_batch, validate_vin
from ui.design_system import apply_button
from ui.field_metrics import CompactStepper, fit_combo, size_combo


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
        row = QHBoxLayout(self.settings)
        self.year_label = QLabel()
        row.addWidget(self.year_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.year_combo = QComboBox()
        size_combo(self.year_combo, 'sm')
        self.year_combo.addItems([str(y) for y in range(2001, 2031)])
        self.year_combo.setCurrentText(str(datetime.date.today().year))
        row.addWidget(self.year_combo)
        self.wmi_label = QLabel()
        row.addWidget(self.wmi_label)
        self.wmi_combo = QComboBox()
        size_combo(self.wmi_combo, 'sm')
        self.wmi_combo.addItems(['AUTO'] + list(CHINA_WMIS))
        row.addWidget(self.wmi_combo)
        self.qty_label = QLabel()
        row.addWidget(self.qty_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.qty = CompactStepper(1, 200, 10)
        row.addWidget(self.qty)
        self.generate_btn = QPushButton()
        apply_button(self.generate_btn, 'primary', compact=True)
        self.generate_btn.clicked.connect(self._generate)
        row.addWidget(self.generate_btn)
        row.addStretch(1)
        layout.addWidget(self.settings)

        self.table = QTableWidget(0, 5)
        try:
            from ui.design_system import apply_list_header
            apply_list_header(self.table.horizontalHeader())
        except Exception:
            pass
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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
        self.year_label.setText('车型年份' if zh else 'Model year')
        self.wmi_label.setText('制造商 WMI' if zh else 'Manufacturer WMI')
        self.qty_label.setText('数量' if zh else 'Qty')
        self.generate_btn.setText('生成' if zh else 'Generate')
        self.copy_btn.setText('复制全部' if zh else 'Copy all')
        self.export_btn.setText('导出 CSV' if zh else 'Export CSV')
        self.table.setHorizontalHeaderLabels(
            ['序号', 'VIN', 'WMI', '年份码', '校验'] if zh
            else ['#', 'VIN', 'WMI', 'Year code', 'Valid']
        )
        fit_combo(self.year_combo)
        fit_combo(self.wmi_combo)

    def _visible_fill_count(self) -> int:
        try:
            return max(1, min(200, int(self.qty.value())))
        except Exception:
            return 10

    def showEvent(self, event):
        super().showEvent(event)
        fit_combo(self.year_combo)
        fit_combo(self.wmi_combo)
        if self._pending_fill:
            self._pending_fill = False
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._generate)

    def _generate(self):
        year = int(self.year_combo.currentText())
        wmi = self.wmi_combo.currentText()
        count = self._visible_fill_count()
        self._results = generate_vin_batch(count, year, '' if wmi == 'AUTO' else wmi)
        self.table.setRowCount(len(self._results))
        self.table.verticalHeader().setDefaultSectionSize(32)
        for row, vin in enumerate(self._results):
            values = (str(row + 1), vin, vin[:3], vin[9], '✓' if validate_vin(vin) else '×')
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 32)
        self.table.scrollToTop()
        self.status.setText(
            f'{len(self._results)} 条' if self.language == 'zh' else f'{len(self._results)} rows'
        )
        self.status.setToolTip(
            'GB 16735 校验位' if self.language == 'zh' else 'GB 16735 check digit'
        )

    def _copy(self):
        if self._results:
            QApplication.clipboard().setText('\n'.join(self._results))
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
                writer.writerow(['VIN', 'WMI', 'YEAR_CODE', 'VALID'])
                writer.writerows((vin, vin[:3], vin[9], validate_vin(vin)) for vin in self._results)
            self.status.setText(path)
        except OSError as exc:
            show_error(self, 'Error', str(exc))
