"""Learning rail geometry and unchanged search/selection across resizing."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class LearningLayoutTests(unittest.TestCase):
    def test_search_selection_and_dragged_rail_survive_resize(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtTest import QTest
        from panels.personal_panel import KnowledgeTab
        from ui.theme_manager import ThemeManager
        app = QApplication.instance() or QApplication(['learning-layout-test'])
        ThemeManager.instance().apply(app, 'calm')
        entries = [dict(id=f'demo-{i}', title=title, content=title + '正文', category='other', tags='')
                   for i, title in enumerate(('示例测试资料', '示例发布说明'))]
        with patch('panels.personal_panel.load_seed_entries', return_value=entries), \
                patch('panels.personal_panel.load_custom_entries', return_value=[]), \
                patch('config.load_layout_splitter', return_value=None), \
                patch('config.save_layout_splitter'):
            panel = KnowledgeTab()
            try:
                panel.resize(1144, 660)
                panel.show()
                QTest.qWait(30)
                self.assertEqual(panel.learn_splitter.sizes()[0], 260)
                self.assertEqual(panel.learn_splitter.handle(1).width(), 16)
                self.assertTrue(panel.learn_splitter.widget(0).isAncestorOf(panel.search_edit))
                panel.search_edit.setText('测试')
                QTest.qWait(250)
                self.assertEqual(panel.entry_list.count(), 1)
                panel.entry_list.setCurrentRow(0)
                self.assertIn('示例测试资料', panel.content_view.toPlainText())
                panel.learn_splitter.setSizes([300, 828])
                dragged = panel.learn_splitter.sizes()
                panel.apply_layout_mode('standard')
                self.assertEqual(panel.learn_splitter.sizes(), dragged)
                panel.apply_layout_mode('narrow', True)
                panel.resize(864, 440)
                QTest.qWait(30)
                self.assertEqual((panel.width(), panel.height()), (864, 440))
                self.assertEqual(panel.search_edit.text(), '测试')
                self.assertIn('示例测试资料', panel.content_view.toPlainText())
                self.assertTrue(panel.copy_btn.isVisible())
            finally:
                panel.close()
                panel.deleteLater()
