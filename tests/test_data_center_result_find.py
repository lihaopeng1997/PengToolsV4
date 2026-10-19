"""Local typed-result find keeps page/row identity and yields to Qt."""

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from tools.data_center.manual_session import ManualColumn, ManualQueryPage
from ui.data_center.result_find import ResultFindDialog, scan_matches
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.result_view import ResultTableView

APP = QApplication.instance() or QApplication([])


def make_page(rows, number=1):
    return ManualQueryPage('q', 'tab', 1, (ManualColumn('text'), ManualColumn('data')), tuple(rows), number, 200, False, 4096)


class ResultFindTests(unittest.TestCase):
    def setUp(self):
        self.model = ManualResultTableModel()
        self.model.replace_page(make_page((('first', None), ('needle', b'\x00\xff'), ('last', 'NEEDLE'))))
        self.view = ResultTableView()
        self.view.setModel(self.model)
        self.view.resize(420, 240)
        self.view.show()
        self.finder = ResultFindDialog(self.view, self.model)
        self.finder.show()

    def tearDown(self):
        self.finder.close()
        self.finder.deleteLater()
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not predicate():
            QTest.qWait(2)
        self.assertTrue(predicate(), 'cooperative search did not finish within one second')

    def test_raw_text_and_hex_search_cross_chunk_boundaries_and_keep_page_offsets(self):
        text = 'x' * 32766 + 'Straße'
        binary = b'a' * 8191 + b'\x00\xff'
        pages = (make_page(((text, binary),)), make_page((('after', None),), number=2))
        self.assertEqual([m for m in scan_matches(pages, 'strasse') if m is not None], [(0, 0)])
        self.assertEqual([m for m in scan_matches(pages, '00ff') if m is not None], [(0, 1)])
        self.assertEqual([m for m in scan_matches(pages, 'after') if m is not None], [(1, 0)])
        self.assertEqual([m for m in scan_matches(pages, 'null') if m is not None], [(1, 1)])

    def test_navigation_uses_original_rows_and_preserves_the_loaded_model(self):
        original_pages = self.model.pages
        self.view.set_first_column_frozen(True)
        self.finder.search.setText('needle')
        self.finder.restart()
        self.wait_until(lambda: not self.finder._timer.isActive())
        self.assertEqual(self.finder._matches, [(1, 0), (2, 1)])
        self.assertEqual(self.view.currentIndex().row(), 1)
        self.view.hideColumn(1)
        self.finder.next.click()
        self.assertEqual((self.view.currentIndex().row(), self.view.currentIndex().column()), (2, 1))
        self.assertFalse(self.view.isColumnHidden(1))
        self.finder.previous.click()
        self.assertEqual(self.view.currentIndex().row(), 1)
        QTest.keyClick(self.finder.search, Qt.Key.Key_Return)
        self.assertEqual(self.view.currentIndex().row(), 2)
        self.assertIs(self.model.pages, original_pages)
        self.assertEqual(self.model.rowCount(), 3)

    def test_result_replacement_restarts_and_hiding_stops_all_search_timers(self):
        self.finder.search.setText('needle')
        self.finder.restart()
        self.wait_until(lambda: not self.finder._timer.isActive())
        self.model.replace_page(make_page((('changed', None),)))
        self.wait_until(lambda: not self.finder._timer.isActive())
        self.assertEqual(self.finder._matches, [])
        self.assertFalse(self.finder.next.isEnabled())
        self.finder.search.setText('changed')
        self.assertTrue(self.finder._debounce.isActive())
        self.view.hide()
        self.assertFalse(self.finder.isVisible())
        self.assertFalse(self.finder._debounce.isActive())
        self.assertFalse(self.finder._timer.isActive())
        self.assertIsNone(self.finder._iterator)

    def test_match_limit_is_explicit_and_does_not_truncate_raw_results(self):
        with patch('ui.data_center.result_find.MAX_MATCHES', 2):
            self.model.replace_page(make_page((('needle', 'needle'), ('needle', 'needle'))))
            self.finder.search.setText('needle')
            self.finder.restart()
            self.wait_until(lambda: not self.finder._timer.isActive())
        self.assertEqual(len(self.finder._matches), 2)
        self.assertTrue(self.finder._limited)
        self.assertIn('请缩小范围', self.finder.status.text())
        self.assertEqual(len(self.model.pages[0].rows), 2)


if __name__ == '__main__':
    unittest.main()
