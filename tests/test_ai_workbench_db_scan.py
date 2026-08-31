# -*- coding: utf-8 -*-
"""Deterministic tests for AI Workbench DB structure scan, qualified naming, and vertical resizing."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSplitter
from panels.ai_workbench_panel import AiWorkbenchPanel


class AiWorkbenchDbScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.mock_connections = [
            {
                'id': 'conn_mysql',
                'name': 'MySQL Test',
                'dialect': 'mysql',
                'host': '127.0.0.1',
                'port': 3306,
                'database': '',
                'username': 'root',
            },
            {
                'id': 'conn_oracle',
                'name': 'Oracle Test',
                'dialect': 'oracle',
                'host': '127.0.0.1',
                'port': 1521,
                'database': 'orcl',
                'username': 'scott',
            },
        ]

    def test_mysql_qualified_identifier_uses_owner_when_present(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=self.mock_connections):
            panel = AiWorkbenchPanel(language='zh')
            panel._browse_conn = MagicMock(return_value=self.mock_connections[0])

            # 1. 跨库扫描（owner 为 app_db）
            obj_with_owner = {'name': 'users', 'owner': 'app_db'}
            self.assertEqual(panel._qualified(obj_with_owner), '`app_db`.`users`')

            # 2. 单库无 owner
            obj_no_owner = {'name': 'orders', 'owner': ''}
            self.assertEqual(panel._qualified(obj_no_owner), '`orders`')

            panel.deleteLater()

    def test_oracle_qualified_identifier_uses_owner(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=self.mock_connections):
            panel = AiWorkbenchPanel(language='zh')
            panel._browse_conn = MagicMock(return_value=self.mock_connections[1])
            if panel._current_tab() is not None:
                panel._current_tab().conn_item = self.mock_connections[1]

            obj = {'name': 'EMP', 'owner': 'SCOTT'}
            self.assertEqual(panel._qualified(obj), 'SCOTT.EMP')

            panel.deleteLater()

    def test_scan_failure_shows_error_and_does_not_show_success_info(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=self.mock_connections), \
             patch('panels.ai_workbench_panel.show_error') as mock_err, \
             patch('panels.ai_workbench_panel.show_info') as mock_info:
            panel = AiWorkbenchPanel(language='zh')
            fail_payload = {
                'status': 'failed',
                'warning': 'Access denied for user root@localhost',
                'objects': [],
            }
            panel._on_db_ok('scan', fail_payload, {})

            mock_err.assert_called_once()
            mock_info.assert_not_called()
            self.assertEqual(panel.loading._state, 'fail')

            panel.deleteLater()

    def test_scan_success_shows_info(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=self.mock_connections), \
             patch('panels.ai_workbench_panel.show_error') as mock_err, \
             patch('panels.ai_workbench_panel.show_info') as mock_info:
            panel = AiWorkbenchPanel(language='zh')
            ok_payload = {
                'status': 'ok',
                'warning': '',
                'objects': [{'name': 'users', 'owner': 'app_db'}],
            }
            panel._on_db_ok('scan', ok_payload, {})

            mock_info.assert_called_once()
            mock_err.assert_not_called()

            panel.deleteLater()

    def test_vertical_body_splitter_allows_resizing_and_respects_mins(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=self.mock_connections):
            panel = AiWorkbenchPanel(language='zh')
            panel.resize(1200, 800)
            panel.show()
            self.app.processEvents()

            body = panel.body_splitter
            self.assertIsInstance(body, QSplitter)
            self.assertEqual(body.orientation(), Qt.Orientation.Vertical)
            self.assertFalse(body.childrenCollapsible())

            # 调整垂直高度（例如将结果区调大）
            body.setSizes([300, 500])
            self.app.processEvents()
            sizes = body.sizes()
            self.assertGreaterEqual(sizes[0], 100)
            self.assertGreaterEqual(sizes[1], 100)

            panel.hide()
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
