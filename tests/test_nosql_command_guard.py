# -*- coding: utf-8 -*-
"""Redis/MongoDB 命令行必须在发起连接前拦截写操作。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class NoSqlCommandGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_redis_shell_blocks_write_before_worker_starts(self):
        from panels.db_redis_panel import RedisWorkbenchPanel

        panel = RedisWorkbenchPanel("zh")
        panel._current_conn = lambda: {"dialect": "redis", "name": "test"}
        panel.cmd_input.setText("DEL user:1")
        with patch.object(panel, "_run_worker") as worker, patch(
            "panels.db_redis_panel.show_warning"
        ) as warning:
            panel._run_command()
        self.assertFalse(worker.called)
        self.assertTrue(warning.called)
        panel.close()

    def test_mongodb_shell_blocks_write_before_worker_starts(self):
        from panels.db_mongodb_panel import MongoDBWorkbenchPanel

        panel = MongoDBWorkbenchPanel("zh")
        panel._current_conn = lambda: {"dialect": "mongodb", "name": "test"}
        panel.cmd_input.setText("db.users.deleteMany({})")
        with patch.object(panel, "_run_worker") as worker, patch(
            "panels.db_mongodb_panel.show_warning"
        ) as warning:
            panel._run_command()
        self.assertFalse(worker.called)
        self.assertTrue(warning.called)
        panel.close()


if __name__ == "__main__":
    unittest.main()
