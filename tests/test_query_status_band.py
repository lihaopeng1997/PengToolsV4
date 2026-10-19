# -*- coding: utf-8 -*-
"""LD-04 status band on the SQL workbench result area."""

import os
import sys
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication

from panels.ai_workbench_panel import AiWorkbenchPanel
from ui.query_status_band import QueryStatusBand


_app = QApplication.instance() or QApplication([])


class QueryStatusBandTests(unittest.TestCase):
    def test_workbench_query_text_shows_the_status_band_without_clearing_rows(self):
        panel = AiWorkbenchPanel('zh')
        try:
            band = panel.result_status
            self.assertIsInstance(band, QueryStatusBand)
            self.assertEqual(band.height(), 32)
            self.assertEqual(band.layout().contentsMargins().left(), 12)
            self.assertEqual(band._icon.width(), 16)
            self.assertEqual(band._icon.height(), 16)

            panel.result.setRowCount(2)
            panel.result.setColumnCount(1)
            rows = panel.result.rowCount()
            panel.result_status.setText('正在执行查询…')
            self.assertEqual(panel.result_status.text(), '正在执行查询…')
            self.assertTrue(panel.result_status.is_querying())
            self.assertEqual(panel.result.rowCount(), rows)
            self.assertEqual(panel.result_status.height(), 32)

            panel.result_status.setText('查询完成')
            self.assertFalse(panel.result_status.is_querying())
            self.assertEqual(panel.result.rowCount(), rows)
        finally:
            deadline = time.monotonic() + 3.0
            while not panel.agent_shutdown_cleanup() and time.monotonic() < deadline:
                _app.processEvents()
                time.sleep(0.002)
            self.assertTrue(
                panel.agent_shutdown_cleanup(),
                'Agent catalog/controller owners must finish before deleting the panel',
            )
            _app.processEvents()
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
