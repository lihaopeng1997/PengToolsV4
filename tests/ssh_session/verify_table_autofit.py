"""Offline P10 checks. Run from tests/ssh_session on the P7/P8/P10 stack."""

import ast
import inspect
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
script = Path(__file__).resolve()
repo = script.parents[2]
sys.path[:0] = [str(script.parent), str(repo)]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import _isolation

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QHeaderView, QTableWidget, QTableWidgetItem

from ui.data_center.result_view import ResultTableView
from ui.table_utils import autofit_columns


def settle():
    QTest.qWait(35)


def drag(header, column, distance):
    surface = header.viewport()
    right = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
    start = QPoint(right, surface.height() // 2)
    end = start + QPoint(distance, 0)
    button = Qt.MouseButton.LeftButton
    QTest.mousePress(surface, button, pos=start)
    QTest.mouseMove(surface, end, 10)
    QTest.mouseRelease(surface, button, pos=end)
    settle()


def table_checks():
    table = QTableWidget(200, 4)
    table.resize(1000, 190)
    table.setHorizontalHeaderLabels(["i", "i", "i", "tail"])
    table.setItem(0, 0, QTableWidgetItem("visible value"))
    table.setItem(1, 1, QTableWidgetItem("filtered out " * 100))
    table.setItem(180, 1, QTableWidgetItem("outside viewport " * 100))
    table.setItem(0, 2, QTableWidgetItem("visible long text " * 100))
    table.hideRow(1)
    fit = autofit_columns(table)
    assert autofit_columns(table) is fit
    dragged = []
    fit.column_dragged.connect(lambda *args: dragged.append(args))
    table.show()
    settle()
    assert 48 < table.columnWidth(0) < 420
    assert table.columnWidth(1) == 48
    assert table.columnWidth(2) == 420
    assert sum(table.columnWidth(column) for column in range(4)) == table.viewport().width()
    initial = table.columnWidth(3)
    table.resize(1120, 190)
    settle()
    assert table.columnWidth(3) > initial and not dragged

    table.setHorizontalHeaderItem(1, QTableWidgetItem("Header wider than forty-eight"))
    settle()
    assert table.columnWidth(1) > 48
    table.showRow(1)
    autofit_columns(table)
    settle()
    assert table.columnWidth(1) == 420
    table.hideRow(1)
    table.setHorizontalHeaderItem(1, QTableWidgetItem("i"))
    settle()
    assert table.columnWidth(1) == 48

    header = table.horizontalHeader()
    previous = table.columnWidth(0)
    drag(header, 0, 41)
    manual = table.columnWidth(0)
    assert manual == previous + 41, (manual, previous)
    assert dragged[-1] == (0, manual)
    table.setItem(0, 0, QTableWidgetItem("replacement " * 100))
    table.hideColumn(3)
    table.resize(1200, 200)
    settle()
    assert table.columnWidth(0) == manual
    assert table.isColumnHidden(3)
    assert table.columnWidth(2) == table.viewport().width() - manual - table.columnWidth(1)
    header.moveSection(header.visualIndex(2), 0)
    settle()
    assert table.columnWidth(1) == table.viewport().width() - manual - table.columnWidth(2)

    table.resize(1050, 200)
    settle()
    assert table.columnWidth(1) > 100
    drag(header, 1, -65)
    tail = table.columnWidth(1)
    assert dragged[-1] == (1, tail)
    table.resize(1110, 200)
    settle()
    assert table.columnWidth(1) == tail
    table.close()


def config_checks():
    table = QTableWidget(1, 4)
    table.resize(800, 200)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(0, 60)
    fit = autofit_columns(table)
    fit.configure({2: 95})
    fit.configure({1: 135}, scope="sheet-a", minimum=True)
    table.show()
    settle()
    assert [table.columnWidth(column) for column in range(3)] == [60, 135, 95]
    drag(header, 1, 40)
    assert table.columnWidth(1) == 175
    fit.configure({1: 100}, scope="sheet-b", minimum=True)
    settle()
    assert table.columnWidth(1) == 100
    fit.configure({1: 135}, scope="sheet-a", minimum=True)
    settle()
    assert table.columnWidth(1) == 175
    table.close()


def widget_checks():
    table = QTableWidget(1, 2)
    table.resize(800, 150)
    combo = QComboBox()
    combo.addItem("editor display text " * 2)
    table.setCellWidget(0, 0, combo)
    autofit_columns(table)
    table.show()
    settle()
    assert table.columnWidth(0) >= min(combo.sizeHint().width(), 420)
    table.close()


def result_checks():
    table = ResultTableView()
    table.resize(740, 220)
    model = QStandardItemModel(4, 3, table)
    model.setHorizontalHeaderLabels(["Frozen", "Display", "Tail"])
    item = QStandardItem("short")
    item.setToolTip("this tooltip must not affect sizing " * 100)
    item.setData("non-display payload " * 100, Qt.ItemDataRole.UserRole)
    model.setItem(0, 1, item)
    table.setModel(model)
    table.set_first_column_frozen(True)
    table.show()
    settle()
    assert table.columnWidth(1) < 100
    assert table.frozen_view.isVisible()
    assert table.frozen_view.columnWidth(0) == table.columnWidth(0)
    previous = table.columnWidth(0)
    drag(table.frozen_view.horizontalHeader(), 0, -8)
    manual = table.columnWidth(0)
    assert manual == previous - 8, (manual, previous)
    replacement = QStandardItemModel(3, 3, table)
    replacement.setHorizontalHeaderLabels(["Frozen", "Display", "Tail"])
    replacement.setItem(0, 0, QStandardItem("new cell longer than the manual width " * 30))
    table.setModel(replacement)
    settle()
    assert table.frozen_view.columnWidth(0) == table.columnWidth(0) == manual
    table.close()


def source_checks():
    entries = {
        "panels/ai_workbench_panel.py": "apply_table(self.result",
        "panels/credit_panel.py": "apply_table(self.table",
        "panels/db_mongodb_panel.py": "apply_table(self.doc_table",
        "panels/db_redis_panel.py": "apply_table(self.hash_table",
        "panels/interface_debug_panel.py": "autofit_columns(self.table)",
        "panels/ops_log_panel.py": "autofit_columns(self.services_table)",
        "panels/sql_panel.py": "autofit_columns(self.release_table)",
        "panels/vin_panel.py": "apply_table(self.table",
        "ui/data_center/import_dialog.py": "autofit_columns(self.preview_table)",
        "ui/data_center/result_view.py": "autofit_columns(self)",
        "ui/data_center/table_query_dialog.py": "autofit_columns(table)",
        "ui/data_center/table_structure_dialog.py": "autofit_columns(table)",
        "ui/design_system.py": "autofit_columns(table)",
        "ui/json_viewer.py": "autofit_columns(self.child_table)",
        "ui/log_analysis/file_picker.py": "autofit_columns(self.table)",
        "ui/log_analysis/workspace.py": "autofit_columns(self.events)",
        "ui/quick_panel.py": "autofit_columns(self.preview_table)",
        "ui/table_utils.py": "def autofit_columns(table)",
    }
    for relative, hookup in entries.items():
        path = repo / relative
        source = path.read_text(encoding="utf-8-sig")
        ast.parse(source, filename=str(path))
        assert hookup in source, relative
    interface = (repo / "panels/interface_debug_panel.py").read_text(encoding="utf-8-sig")
    assert "self.rt_response_tabs.addTab" in interface
    assert "fit.column_dragged.connect(self._on_column_resized)" in interface
    viewer = (repo / "ui/json_viewer.py").read_text(encoding="utf-8-sig")
    assert viewer.index("self.child_table.setModel(") < viewer.index("autofit_columns(self.child_table)")


if __name__ == "__main__":
    try:
        app = QApplication([])
        assert tuple(inspect.signature(autofit_columns).parameters) == ("table",)
        for check in (table_checks, config_checks, widget_checks, result_checks, source_checks):
            check()
            print(f"PASS {check.__name__}")
    finally:
        _isolation.assert_untouched()
    print("RESULT: 5 passed, 0 failed")
