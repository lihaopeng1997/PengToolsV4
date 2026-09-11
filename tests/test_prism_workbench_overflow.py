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

    def test_agent_composer_and_context_toggle_keep_draft(self):
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        panel = AgentWorkbenchPanel()
        try:
            panel.input.setPlainText('Preview draft; do not execute')
            for width, height, mode in ((1144, 740, 'standard'), (856, 528, 'narrow')):
                panel.apply_layout_mode(mode, height < 640)
                panel.resize(width, height)
                panel.show()
                self.settle()
                panel.resize(width, height)
                self.settle()
                self.assertEqual((panel.width(), panel.height()), (width, height))
                self.assertGreaterEqual(panel.input.height(), 120)
                for splitter in (panel.center_split, panel.context_split):
                    self.assertEqual(splitter.handle(1).height(), 16)
                for _ in range(2):
                    was_hidden = panel.context_panel.isHidden()
                    panel.context_toggle_btn.click()
                    self.settle()
                    self.assertNotEqual(panel.context_panel.isHidden(), was_hidden)
                    self.assertEqual(panel.input.toPlainText(), 'Preview draft; do not execute')
                self.assertTrue(panel.composer_card.rect().contains(QRect(panel.send_btn.mapTo(panel.composer_card, QPoint()), panel.send_btn.size())))
        finally:
            self.dispose(panel)

    def test_requirement_tree_actions_fit_and_still_expand(self):
        from panels.requirement_panel import RequirementPanel
        from PyQt6.QtWidgets import QTreeWidgetItem
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        with patch('panels.requirement_panel.load_requirements', return_value=[]):
            panel = RequirementPanel()
        try:
            panel.apply_layout_mode('narrow', True)
            panel.resize(856, 528)
            panel.show()
            self.settle()
            panel.resize(856, 528)
            self.settle()
            self.assertEqual((panel.width(), panel.height()), (856, 528))
            rail = panel.detail_splitter.widget(0)
            rects = []
            for widget in (panel.select_all_check, panel.batch_delete_btn, panel.ticket_btn, panel.expand_tree_btn, panel.collapse_tree_btn):
                rect = QRect(widget.mapTo(rail, QPoint()), widget.size())
                self.assertTrue(rail.rect().contains(rect))
                for other in rects:
                    self.assertFalse(rect.intersects(other))
                rects.append(rect)
            group = QTreeWidgetItem(panel.requirement_list, ['Preview group'])
            QTreeWidgetItem(group, ['Preview task'])
            panel.expand_tree_btn.click()
            self.assertTrue(group.isExpanded())
            panel.collapse_tree_btn.click()
            self.assertFalse(group.isExpanded())
        finally:
            self.dispose(panel)

    def test_commands_preview_keeps_copy_action_reachable(self):
        from panels.ops_panel import OpsPanel
        panel = OpsPanel()
        try:
            panel.apply_layout_mode('narrow', True)
            panel.resize(856, 528)
            panel.show()
            self.settle()
            panel.generate_btn.click()
            self.assertGreaterEqual(panel.preview.height(), 240)
            self.assertTrue(panel.rect().contains(QRect(panel.copy_btn.mapTo(panel, QPoint()), panel.copy_btn.size())))
            self.assertTrue(panel.preview.isReadOnly())
            self.assertTrue(panel.preview.toPlainText())
        finally:
            self.dispose(panel)

    def test_format_tabs_keep_text_and_run_original_conversion(self):
        from panels.format_panel import FormatToolsPanel
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(self.app, 'calm', font_size=13)
        panel = FormatToolsPanel()
        try:
            sample = '晴空 Prism / ' * 100
            panel.text_tab.input.setPlainText(sample)
            panel.sql_tab.editor.setPlainText('select 42 from dual;')
            for width, height, mode in ((1144, 740, 'standard'), (856, 528, 'narrow')):
                panel.apply_layout_mode(mode, height < 640)
                panel.resize(width, height)
                panel.show()
                for index in range(panel.tabs.count()):
                    panel.tabs.setCurrentIndex(index)
                    self.settle()
                    self.assertEqual((panel.width(), panel.height()), (width, height))
                    self.assertEqual(panel.text_tab.input.toPlainText(), sample)
                    self.assertEqual(panel.sql_tab.editor.toPlainText(), 'select 42 from dual;')
                    if index in (1, 2):
                        button = (panel.xml_workspace if index == 1 else panel.sql_tab).format_btn
                        toolbar = button.parentWidget()
                        rects = []
                        for action in toolbar.findChildren(QPushButton):
                            if not action.isVisible():
                                continue
                            rect = QRect(action.mapTo(toolbar, QPoint()), action.size())
                            self.assertTrue(toolbar.rect().contains(rect))
                            self.assertGreaterEqual(action.width(), action.sizeHint().width())
                            for other in rects:
                                self.assertFalse(rect.intersects(other))
                            rects.append(rect)
            self.assertEqual(panel.text_tab.vsplit.handle(1).height(), 16)
            self.assertEqual(panel.xml_workspace.splitter.handle(1).height(), 16)
            panel.xml_workspace.input_edit.setPlainText('<root><name>晴空</name></root>')
            panel.xml_workspace.format_btn.click()
            self.assertIn('<name>晴空</name>', panel.xml_workspace.output_edit.toPlainText())
            panel.text_tab.encode_btn.click()
            encoded = panel.text_tab.output.toPlainText()
            self.assertTrue(encoded)
            panel.text_tab.input.setPlainText(encoded)
            panel.text_tab.decode_btn.click()
            self.assertEqual(panel.text_tab.output.toPlainText(), sample)
        finally:
            self.dispose(panel)

    def test_document_editor_keeps_sql_and_import_callback(self):
        from panels.docx_panel import DocxUpdatePanel
        panel = DocxUpdatePanel()
        try:
            panel.sql_editor.setPlainText('alter table preview add sample varchar(20);')
            panel.author.setText('Preview author')
            for width, height, mode in ((1144, 740, 'standard'), (856, 528, 'narrow')):
                panel.apply_layout_mode(mode, height < 640)
                panel.resize(width, height)
                panel.show()
                self.settle()
                panel.resize(width, height)
                self.settle()
                self.assertEqual((panel.width(), panel.height()), (width, height))
                self.assertEqual(panel.editor_splitter.handle(1).height(), 16)
                self.assertEqual(panel.author.text(), 'Preview author')
                self.assertEqual(panel.sql_editor.toPlainText(), 'alter table preview add sample varchar(20);')
            with patch('panels.docx_panel.QFileDialog.getOpenFileNames', return_value=([], '')) as picker:
                panel.load_sql_btn.click()
                picker.assert_called_once()
        finally:
            self.dispose(panel)


if __name__ == '__main__':
    unittest.main()
