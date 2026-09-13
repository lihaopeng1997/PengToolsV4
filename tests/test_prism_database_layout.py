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

    def test_redis_detail_actions_fit_large_font_without_horizontal_scroll(self):
        from PyQt6.QtCore import QPoint, QRect
        from panels.db_redis_panel import RedisWorkbenchPanel
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=16)
        with patch.object(RedisWorkbenchPanel, '_reload_connections'):
            panel = RedisWorkbenchPanel('en')
        try:
            panel.resize(856, 528)
            panel.apply_layout_mode('narrow', True)
            panel.show()
            panel.side_tabs.setCurrentIndex(1)
            for _ in range(5):
                self.app.processEvents()
            scroll = panel.side_tabs.widget(1)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            samples = (
                ('hash', {'DEMO field': 'DEMO value'}, panel.hash_table, 1),
                ('list', ['DEMO value'], panel.list_table, 2),
                ('set', {'DEMO value'}, panel.list_table, 2),
                ('zset', [{'member': 'DEMO value', 'score': 42}], panel.zset_table, 3),
            )
            for kind, value, table, index in samples:
                panel._render_value(kind, value)
                self.app.processEvents()
                self.assertEqual(panel.value_tabs.currentIndex(), index)
                self.assertEqual(table.rowCount(), 1)
                self.assertGreaterEqual(panel.value_tabs.height(), 280)
                self.assertTrue(any('DEMO' in table.item(0, col).text()
                                    for col in range(table.columnCount())))
            for button in (panel.refresh_val_btn, panel.copy_val_btn, panel.copy_key_btn,
                           panel.del_btn, panel.rename_btn, panel.expire_btn):
                self.assertGreaterEqual(button.width(), button.sizeHint().width())
                scroll.ensureWidgetVisible(button)
                self.app.processEvents()
                rect = QRect(button.mapTo(scroll.viewport(), QPoint()), button.size())
                self.assertTrue(scroll.viewport().rect().contains(rect), button.text())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_mongo_compact_documents_keep_pagination_below_results(self):
        from PyQt6.QtCore import QPoint, QRect
        from panels.db_mongodb_panel import MongoDBWorkbenchPanel
        with patch.object(MongoDBWorkbenchPanel, '_reload_connections'):
            panel = MongoDBWorkbenchPanel('zh')
        try:
            panel.resize(856, 528)
            panel.apply_layout_mode('narrow', True)
            panel.show()
            panel.query_input.setPlainText('{"preview":true}')
            panel._render_docs([{'preview': True, 'text': 'DEMO ' * 100}], 1, 50)
            for _ in range(5):
                self.app.processEvents()
            self.assertGreater(panel.prev_btn.y(), panel.doc_table.geometry().bottom())
            for _ in range(2):
                panel.view_mode_btn.click()
                self.app.processEvents()
                result = panel.doc_json if panel.doc_json.isVisible() else panel.doc_table
                self.assertGreaterEqual(result.height(), 120)
                self.assertGreater(panel.prev_btn.y(), result.geometry().bottom())
                self.assertEqual(panel.query_input.toPlainText(), '{"preview":true}')
            scroll = panel.side_tabs.widget(0)
            scroll.ensureWidgetVisible(panel.copy_btn)
            self.app.processEvents()
            rect = QRect(panel.copy_btn.mapTo(scroll.viewport(), QPoint()), panel.copy_btn.size())
            self.assertTrue(scroll.viewport().rect().contains(rect))
            self.assertIn('DEMO', panel.doc_json.toPlainText())
            self.assertEqual((panel.width(), panel.height()), (856, 528))
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_redis_low_detail_scroll_keeps_value_actions_and_command(self):
        from PyQt6.QtCore import QPoint, QRect
        from panels.db_redis_panel import RedisWorkbenchPanel
        with patch.object(RedisWorkbenchPanel, '_reload_connections'):
            panel = RedisWorkbenchPanel('zh')
        try:
            panel.resize(856, 528)
            panel.apply_layout_mode('narrow', True)
            panel.show()
            panel.side_tabs.setCurrentIndex(1)
            sample = '\n'.join(f'Preview value {i}' for i in range(50))
            panel._render_value('string', sample)
            panel.cmd_input.setText('GET preview-only')
            for _ in range(5):
                self.app.processEvents()
            panel.resize(856, 528)
            scroll = panel.side_tabs.widget(1)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertGreaterEqual(panel.value_tabs.height(), 280)
            self.assertGreater(panel.copy_val_btn.y(), panel.value_tabs.geometry().bottom())
            scroll.ensureWidgetVisible(panel.copy_val_btn)
            self.app.processEvents()
            rect = QRect(panel.copy_val_btn.mapTo(scroll.viewport(), QPoint()), panel.copy_val_btn.size())
            self.assertTrue(scroll.viewport().rect().contains(rect))
            self.assertEqual((panel.width(), panel.height()), (856, 528))
            panel.side_tabs.setCurrentIndex(2)
            panel.side_tabs.setCurrentIndex(1)
            self.assertEqual(panel.cmd_input.text(), 'GET preview-only')
            self.assertIn('Preview value 49', panel.string_value.toPlainText())
            self.assertEqual(panel._bottom_split.handle(1).height(), 16)
            self.assertEqual(panel.left_split.handle(1).height(), 16)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
