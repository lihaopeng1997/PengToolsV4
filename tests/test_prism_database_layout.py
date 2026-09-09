"""Regression for stacked table minima expanding the native database window."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class DatabaseLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm')
        for target in ('config.load_layout_splitter', 'config.save_layout_splitter'):
            mock = patch(target, return_value=None)
            mock.start()
            self.addCleanup(mock.stop)

    def test_tabs_fit_and_keep_command_input(self):
        from panels.db_redis_panel import RedisWorkbenchPanel
        from panels.db_mongodb_panel import MongoDBWorkbenchPanel
        for cls in (RedisWorkbenchPanel, MongoDBWorkbenchPanel):
            with self.subTest(panel=cls.__name__), patch.object(cls, '_reload_connections'):
                panel = cls('zh')
                try:
                    panel.resize(1144, 740)
                    panel.apply_layout_mode('standard', False)
                    panel.show()
                    panel.cmd_input.setText('preview input; never executed')
                    for index in range(panel.side_tabs.count()):
                        panel.side_tabs.setCurrentIndex(index)
                        self.app.processEvents()
                        self.assertEqual((panel.width(), panel.height()), (1144, 740))
                        page = panel.side_tabs.currentWidget()
                        self.assertTrue(page.isVisible())
                        self.assertGreaterEqual(page.height(), 280)
                        self.assertEqual(panel.cmd_input.text(), 'preview input; never executed')
                    self.assertTrue(panel.cmd_input.isVisible())
                    splitter = getattr(panel, 'main_split', None) or panel.body_splitter
                    self.assertAlmostEqual(splitter.sizes()[0], 240, delta=2)
                    if isinstance(panel, RedisWorkbenchPanel):
                        self.assertGreaterEqual(panel.value_tabs.minimumHeight(), 280)
                finally:
                    panel.close()
                    panel.deleteLater()
                    self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
