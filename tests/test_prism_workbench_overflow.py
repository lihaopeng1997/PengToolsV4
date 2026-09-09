"""Visible geometry regressions for the minimum supported client workspace."""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPoint, QRect
from PyQt6.QtWidgets import QApplication, QPushButton


class WorkbenchOverflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['prism-overflow-tests'])

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def dispose(self, panel):
        panel.close()
        panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_sql_toolbar_wraps_without_losing_actions_or_draft(self):
        from panels.ai_workbench_panel import AiWorkbenchPanel
        from ui.theme_manager import ThemeManager
        for dialect in ('oracle', 'mysql', 'oceanbase', 'dameng'):
            with self.subTest(dialect=dialect), patch.object(AiWorkbenchPanel, '_reload_connections'):
                panel = AiWorkbenchPanel('zh', dialect=dialect)
                try:
                    panel._current_tab().editor.setPlainText('select 42; -- preview only')
                    for language, font in (('zh', 13), ('en', 16)):
                        ThemeManager.instance().apply(self.app, 'calm', font_size=font)
                        panel.set_language(language)
                        for width, height, mode in ((1308, 772, 'wide'), (856, 528, 'narrow')):
                            panel.apply_layout_mode(mode, height < 640, content_width=width)
                            panel.resize(width, height)
                            panel.show()
                            self.settle()
                            panel.resize(width, height)
                            self.settle()
                            self.assertEqual((panel.width(), panel.height()), (width, height), (language, font))
                            toolbar = panel.connection_toolbar
                            rects = []
                            for child in toolbar.findChildren(QPushButton) + [panel.conn_combo, panel.conn_target_hint]:
                                if not child.isVisible() or not child.width():
                                    continue
                                rect = QRect(child.mapTo(toolbar, QPoint()), child.size())
                                self.assertTrue(toolbar.rect().contains(rect), (dialect, language, type(child).__name__, rect))
                                for other in rects:
                                    self.assertFalse(rect.intersects(other), (dialect, language, rect, other))
                                rects.append(rect)
                            self.assertEqual(panel._current_tab().editor.toPlainText(), 'select 42; -- preview only')
                    with patch.object(panel, '_start_db') as start:
                        panel.test_btn.setEnabled(True)
                        panel.test_btn.click()
                        start.assert_called_once_with('test')
                finally:
                    self.dispose(panel)

    def test_interface_stacked_workspace_scrolls_and_preserves_request(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        panel = InterfaceDebugPanel('zh')
        try:
            panel.rt_body.setPlainText('{"preview": true}')
            for width, height, mode in ((1144, 740, 'standard'), (856, 528, 'narrow'), (1144, 740, 'standard')):
                panel.apply_layout_mode(mode, height < 640)
                panel.resize(width, height)
                panel.show()
                self.settle()
                panel.resize(width, height)
                self.settle()
                self.assertEqual((panel.width(), panel.height()), (width, height))
                hint = panel.empty_hint
                self.assertTrue(hint.parentWidget().rect().contains(hint.geometry()))
                self.assertGreaterEqual(hint.height(), hint.heightForWidth(hint.width()))
                self.assertEqual(panel.rt_body.toPlainText(), '{"preview": true}')
                if mode == 'narrow':
                    scroll = panel.workspace_scroll
                    self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                    scroll.ensureWidgetVisible(panel.detail_tabs)
                    self.settle()
                    self.assertGreater(scroll.verticalScrollBar().value(), 0)
        finally:
            # No listener is started by this geometry test.
            self.dispose(panel)


if __name__ == '__main__':
    unittest.main()
