# -*- coding: utf-8 -*-
"""需求管理右侧：摘要紧凑 + 文件库占满剩余。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NormalizeContentSizesTests(unittest.TestCase):
    def test_content_sized_top(self):
        from panels.requirement_panel import normalize_content_splitter_sizes
        self.assertEqual(normalize_content_splitter_sizes(None, total_h=1000, top_h=160), [160, 840])
        # 旧 3:7 存储不再强制比例，优先 top_h
        self.assertEqual(normalize_content_splitter_sizes([400, 200], total_h=1000, top_h=150), [150, 850])
        # 无 top_h 时用 stored 并夹紧
        sizes = normalize_content_splitter_sizes([500, 200], total_h=1000)
        self.assertLessEqual(sizes[0], 280)
        self.assertGreater(sizes[1], sizes[0])


class RequirementCompactStackUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_panel(self, ui=None):
        from panels.requirement_panel import RequirementPanel
        ui = ui if ui is not None else {
            'splitter_sizes': [320, 780],
            'content_splitter_sizes': [240, 560],
        }
        with patch('panels.requirement_panel.load_requirements', return_value=[]), \
                patch('panels.requirement_panel.load_requirement_ui', return_value=ui):
            return RequirementPanel('zh')

    def test_detail_card_is_content_sized(self):
        from PyQt6.QtWidgets import QSizePolicy
        panel = self._make_panel()
        sp = panel.detail_card.sizePolicy()
        self.assertEqual(sp.verticalPolicy(), QSizePolicy.Policy.Maximum)
        self.assertIsNone(panel.file_sql_splitter)
        panel.close()

    def test_file_tabs_take_remaining_space(self):
        panel = self._make_panel()
        panel.resize(1200, 900)
        panel.show()
        self.app.processEvents()
        # 文件库区应明显大于摘要卡
        self.assertGreater(panel.detail_tabs.height(), panel.detail_card.height())
        self.assertLess(panel.detail_card.height(), panel.height() * 0.45)
        panel.close()

    def test_flags_single_row(self):
        from PyQt6.QtWidgets import QHBoxLayout
        panel = self._make_panel()
        self.assertIsInstance(panel.flag_chips_layout, QHBoxLayout)
        panel.close()

    def test_file_library_responsive_action_bar(self):
        """文件库只常驻 4 个高频操作，低频操作在「更多」菜单且与底层状态同步。"""
        panel = self._make_panel()
        try:
            self.assertEqual(
                panel.file_library_action_buttons,
                (panel.open_folder_btn, panel.refresh_svn_btn, panel.update_current_btn, panel.commit_btn),
            )
            for mode in ('wide', 'standard', 'compact', 'narrow'):
                panel.apply_layout_mode(mode)
                for b in panel.file_library_action_buttons:
                    self.assertFalse(b.isHidden(), mode)
                self.assertFalse(panel.file_more_btn.isHidden(), mode)
                for b in (panel.add_file_btn, panel.new_text_btn, panel.lock_file_btn,
                          panel.unlock_file_btn, panel.revert_btn):
                    self.assertTrue(b.isHidden(), mode)

            action_texts = [a.text() for a in panel.file_more_menu.actions()]
            for text in ('添加文件…', '新建文本…', '锁定选中文件', '解锁选中文件', '回滚选中改动'):
                self.assertIn(text, action_texts)

            panel.lock_file_btn.setEnabled(False)
            panel._sync_file_more_menu()
            self.assertFalse(panel._action_lock.isEnabled())
        finally:
            panel.close()

    def test_file_library_search_row_responsive(self):
        """文件库搜索行只保留搜索框和计数；展开/折叠走右键菜单。"""
        panel = self._make_panel()
        try:
            self.assertTrue(hasattr(panel, 'file_search_edit'))
            for name in ('file_expand_btn', 'file_collapse_btn', 'file_tree_more_btn'):
                self.assertFalse(hasattr(panel, name), name)
        finally:
            panel.close()

    def test_requirement_top_toolbar_responsive(self):
        """顶部唯一工具条：拉取最新 / 更多 / 新建需求在所有断点都可见，次级动作在更多菜单。"""
        panel = self._make_panel()
        try:
            for mode in ('wide', 'standard', 'compact', 'narrow'):
                panel.apply_layout_mode(mode)
                for b in (panel.update_all_btn, panel.toolbar_more_btn, panel.add_btn, panel.search_edit, panel.month_filter):
                    self.assertFalse(b.isHidden(), mode)
            top_action_texts = [a.text() for a in panel.toolbar_more_menu.actions()]
            for text in ('登记缺陷（粘贴 BUG）', '扫描需求目录…', '检出代码…', '导入资料…', '系统配置'):
                self.assertIn(text, top_action_texts)
        finally:
            panel.close()

    def test_splitter_handle_hit_target_and_qss_styling(self):
        """测试 Splitter 保持 8px 交互把手宽度，且 QSS 采用细线边距设计。"""
        panel = self._make_panel()
        try:
            self.assertGreaterEqual(panel.detail_splitter.handleWidth(), 8)
            from ui.theme_manager import ThemeManager
            manager = ThemeManager.instance()
            manager.load_template()
            qss = manager.render('calm')
            self.assertIn('QSplitter::handle:horizontal', qss)
            self.assertIn('margin: 0 3px', qss)
            self.assertIn('QSplitter::handle:vertical', qss)
            self.assertIn('margin: 3px 0', qss)
        finally:
            panel.close()

    def test_r1_1600_width_first_open_left_ge_400(self):
        """R1: 1600 宽首次打开左栏 >= 400。"""
        panel = self._make_panel(ui={})
        try:
            panel.resize(1600, 900)
            panel.show()
            self.app.processEvents()
            panel.apply_layout_mode('wide')
            self.app.processEvents()
            sizes = panel.detail_splitter.sizes()
            self.assertGreaterEqual(sizes[0], 400)
        finally:
            panel.close()

    def test_r2_1280_width_controls_remain_usable(self):
        """R2: 1280 宽下左栏与主控件可用。"""
        panel = self._make_panel(ui={})
        try:
            panel.resize(1280, 800)
            panel.show()
            self.app.processEvents()
            panel.apply_layout_mode('standard')
            self.app.processEvents()
            sizes = panel.detail_splitter.sizes()
            self.assertGreaterEqual(sizes[0], 320)
            self.assertGreaterEqual(sizes[1], 520)
        finally:
            panel.close()

    def test_r3_saved_valid_custom_width_wins(self):
        """R3: 用户拖拽有效自定义宽度在相同 bucket 下优先保留。"""
        panel = self._make_panel(ui={'splitter_sizes': [480, 800]})
        try:
            panel.resize(1280, 800)
            panel.show()
            self.app.processEvents()
            panel.apply_layout_mode('standard')
            self.app.processEvents()
            sizes = panel.detail_splitter.sizes()
            self.assertAlmostEqual(sizes[0], 480, delta=10)
        finally:
            panel.close()


if __name__ == '__main__':
    unittest.main()
