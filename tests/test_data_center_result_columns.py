"""Column interactions on the actual typed Qt model, without DB/config I/O."""

import os
import unittest
from decimal import Decimal

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QMenu, QTableView, QWidget
from tools.data_center.manual_session import ManualColumn, ManualQueryPage
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.result_view import ResultTableView

APP = QApplication.instance() or QApplication([])


def page(names=tuple(f'column_{i}' for i in range(12))):
    rows = tuple((Decimal(i), None, b'\x00\xff', *range(len(names) - 3)) for i in range(80))
    return ManualQueryPage('q', 'tab', 1, tuple(ManualColumn(n) for n in names), rows, 1, 200, False, 4096)


class ResultColumnTests(unittest.TestCase):
    def setUp(self):
        self.model = ManualResultTableModel()
        self.model.replace_page(page())
        self.view = ResultTableView(language='zh')
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.view.resize(620, 280)
        self.view.show()
        APP.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        APP.processEvents()

    def test_frozen_column_stays_in_place_with_shared_selection_and_vertical_scroll(self):
        view = self.view
        view.set_first_column_frozen(True)
        view.horizontalScrollBar().setValue(350)
        view.verticalScrollBar().setValue(150)
        APP.processEvents()
        frozen = view.frozen_view
        self.assertTrue(frozen.isVisible())
        self.assertIs(frozen.model(), self.model)
        self.assertIs(frozen.selectionModel(), view.selectionModel())
        self.assertEqual(frozen.x(), view.viewport().x())
        self.assertEqual(frozen.visualRect(self.model.index(5, 0)).left(), 0)
        self.assertLess(view.visualRect(self.model.index(5, 0)).left(), 0)
        self.assertEqual(frozen.verticalScrollBar().value(), view.verticalScrollBar().value())
        self.assertEqual(frozen.visualRect(self.model.index(5, 0)).top(), view.visualRect(self.model.index(5, 0)).top())
        frozen.verticalScrollBar().setValue(240)
        APP.processEvents()
        self.assertEqual(view.verticalScrollBar().value(), frozen.verticalScrollBar().value())

    def test_menu_hides_columns_without_mutating_raw_results_and_never_hides_every_column(self):
        view = self.view
        before = self.model.pages
        menu = view.create_column_menu()
        freeze = next(a for a in menu.actions() if a.objectName() == 'result-freeze-first-column')
        freeze.trigger()
        self.assertTrue(view.first_column_frozen)
        column = next(a for a in menu.actions() if a.objectName() == 'result-column-1')
        column.trigger()
        self.assertTrue(view.isColumnHidden(1))
        self.assertEqual(self.model.columnCount(), 12)
        self.assertIs(self.model.index(0, 1).data(Qt.ItemDataRole.UserRole), None)
        self.assertEqual(self.model.index(0, 2).data(Qt.ItemDataRole.UserRole), b'\x00\xff')
        self.assertEqual(self.model.pages, before)
        for i in range(12):
            view.hideColumn(i)
        self.assertEqual(sum(not view.isColumnHidden(i) for i in range(12)), 1)
        next_menu = view.create_column_menu()
        next(a for a in next_menu.actions() if a.objectName() == 'result-show-all-columns').trigger()
        self.assertTrue(all(not view.isColumnHidden(i) for i in range(12)))
        self.model.replace_page(page(('new_id', 'new_name', 'new_bytes')))
        APP.processEvents()
        self.assertTrue(all(not view.isColumnHidden(i) for i in range(3)))
        self.assertFalse(view.frozen_view.isColumnHidden(0))
        self.assertTrue(view.frozen_view.isColumnHidden(1))
        menu.deleteLater()
        next_menu.deleteLater()

    def test_frozen_mouse_detail_and_context_coordinates_keep_the_original_first_column(self):
        view = self.view
        view.set_first_column_frozen(True)
        view.horizontalScrollBar().setValue(400)
        APP.processEvents()
        frozen = view.frozen_view
        index = self.model.index(1, 0)
        point = frozen.visualRect(index).center()
        spy = QSignalSpy(view.doubleClicked)
        QTest.mouseClick(frozen.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(view.selectionModel().currentIndex(), index)
        self.assertEqual(view.selectionModel().selectedRows()[0].row(), 1)
        QTest.mouseDClick(frozen.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(len(spy), 1)
        self.assertEqual(spy[0][0], index)
        mapped = view.viewport().mapFromGlobal(frozen.viewport().mapToGlobal(point))
        self.assertEqual(view.indexAt(mapped), index)

    def test_resize_and_keyboard_keep_unfrozen_cells_reachable(self):
        view = self.view
        view.set_first_column_frozen(True)
        view.setColumnWidth(0, 190)
        view.setRowHeight(2, 42)
        view.resize(470, 230)
        APP.processEvents()
        self.assertEqual(view.frozen_view.columnWidth(0), 190)
        self.assertEqual(view.frozen_view.rowHeight(2), 42)
        view.frozen_view.setColumnWidth(0, 140)
        self.assertEqual(view.columnWidth(0), 140)
        index = self.model.index(2, 5)
        view.scrollTo(index)
        view.setCurrentIndex(index)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Left)
        APP.processEvents()
        self.assertGreaterEqual(view.visualRect(view.currentIndex()).left(), view.frozen_view.width())
        view.set_first_column_frozen(False)
        self.assertFalse(view.frozen_view.isVisible())

    def test_more_menu_refresh_releases_previous_column_submenu(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.data_center.manual_query import ManualQueryHost

        panel = QWidget()
        panel.language = 'zh'
        panel._current_tab = lambda: None
        host = ManualQueryHost(panel, profile_host=Mock())
        host.result_view = self.view
        host.table_editor = SimpleNamespace(isHidden=lambda: True)
        host.workbench_more_menu = QMenu(panel)
        for _ in range(5):
            host._refresh_workbench_actions()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertEqual(len(host.workbench_more_menu.findChildren(QMenu)), 1)
        panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
