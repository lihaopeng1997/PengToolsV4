# -*- coding: utf-8 -*-
"""PRISM-UI-P09B 契约测试：数据中心与数据库工作台区域模板与契约规范对齐。

规范覆盖：
1. SQL 工作台 (AiWorkbenchPanel):
   - C>=1200: 树 220, 主 C-522, 辅助 270 (col_defs=[220, max(420, C-522), 270]);
   - C<1200: 树 200 + 主 C-216, AI/对象使用窄面板切换 (col_defs=[200, max(360, C-216), 240]);
   - 主编辑初始 260, 结果余 V-276 (body_splitter defaults=[260, 380]);
   - DATA_CENTER_SQL_EXECUTION_SCOPE: 有选中执行选中，无选中执行全文，空编辑器提示，快捷键对齐；
   - 顶部标题栏无冗余执行按钮，仅保留在编辑器工作区内部。
2. Redis 工作台 (RedisWorkbenchPanel):
   - 树 240 + 右 C-256 (main_split defaults=[240, 960], min_sizes=[240, 480]);
   - 类型值区 min 280 (value_tabs.minimumHeight() >= 280);
   - CLI 高 200 或余高 (_bottom_split defaults=[520, 200]);
   - 响应式 apply_layout_mode.
3. MongoDB 工作台 (MongoDBWorkbenchPanel):
   - 集合 240 + 右 C-256 (body_splitter defaults=[240, 720]);
   - 过滤输入框 min 96 (query_input.minimumHeight() >= 96);
   - 响应式 apply_layout_mode.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTextEdit

from panels.ai_workbench_panel import AiWorkbenchPanel
from panels.db_redis_panel import RedisWorkbenchPanel
from panels.db_mongodb_panel import MongoDBWorkbenchPanel


def _coord(splitter):
    return splitter.property('_pengtools_splitter_coordinator')


class TestP09BDatabasePanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_01_sql_workbench_splitters_geometry(self):
        """1. SQL 工作台：树 220 / 辅助 270 / 主编辑 260 几何分栏契约。"""
        panel = AiWorkbenchPanel(language='zh')
        try:
            panel.resize(1440, 900)
            panel.show()
            self.app.processEvents()

            # 验证垂直 body_splitter 默认高度配置为 260 / 380
            coord_body = _coord(panel.body_splitter)
            self.assertIsNotNone(coord_body)
            self.assertEqual(coord_body.defaults, [260, 380])
            self.assertEqual(coord_body.min_sizes, [220, 180])

            # 验证水平列 columns_splitter 默认宽度配置为 树 220 / 主 (C-522=918) / 辅助 270
            coord_cols = _coord(panel.columns_splitter)
            self.assertIsNotNone(coord_cols)
            self.assertEqual(coord_cols.defaults, [220, 1440 - 522, 270])
            self.assertEqual(coord_cols.min_sizes, [200, 420, 240])

            # 顶部标题栏不应有执行按钮 (已移除到编辑器工具条)
            self.assertFalse(hasattr(panel, 'top_run_btn'))
            # 确认主 run_btn 存在且属于编辑器工作区
            self.assertTrue(hasattr(panel, 'run_btn'))
            self.assertIn(panel.run_btn, panel.findChildren(type(panel.run_btn)))
        finally:
            panel.deleteLater()

    def test_02_sql_execution_scope_approved_override(self):
        """2. SQL 执行范围契约 (DATA_CENTER_SQL_EXECUTION_SCOPE)：有选中执行选中，无选中执行全文。"""
        panel = AiWorkbenchPanel(language='zh')
        try:
            panel.resize(1280, 800)
            panel.show()
            self.app.processEvents()

            editor = panel._current_editor()
            self.assertIsNotNone(editor)

            full_sql = "SELECT id, name FROM users;\nSELECT * FROM orders WHERE status = 'PAID';"
            editor.setPlainText(full_sql)

            # Case 1: 无选中状态 -> 必须返回全文，而非基于光标解析单句
            cursor = editor.textCursor()
            cursor.clearSelection()
            cursor.setPosition(5)  # 光标在第一句内部
            editor.setTextCursor(cursor)

            selected_sql = panel._editor_sql()
            self.assertEqual(
                selected_sql,
                full_sql.strip(),
                "无选中时必须返回编辑器全文，符合 DATA_CENTER_SQL_EXECUTION_SCOPE 契约",
            )

            # Case 2: 有选中状态 -> 仅返回选中片段
            cursor.setPosition(0)
            cursor.setPosition(28, cursor.MoveMode.KeepAnchor)
            editor.setTextCursor(cursor)

            selected_sql = panel._editor_sql()
            self.assertEqual(
                selected_sql,
                "SELECT id, name FROM users;",
                "有选中时必须仅返回选中 SQL",
            )

            # Case 3: 编辑器为空 -> 返回空串，并验证 _run_sql 给出提示而不执行
            editor.setPlainText("   \n  \t  ")
            self.assertEqual(panel._editor_sql(), "")

            with patch('panels.ai_workbench_panel.show_warning') as mock_warn, \
                 patch.object(panel, '_start_db') as mock_start, \
                 patch.object(panel, '_current_conn', return_value={'name': 'test_db', 'dialect': 'mysql'}):
                panel._run_sql(reset=True)
                mock_warn.assert_called_once()
                self.assertIn("没有可执行的语句", mock_warn.call_args[0][2])
                mock_start.assert_not_called()
        finally:
            panel.deleteLater()

    def test_03_sql_workbench_dialects_and_responsive(self):
        """3. SQL 工作台：四大方言、C=1200 阈值响应式切换与 Splitter 持久化。"""
        dialects = ['oracle', 'mysql', 'oceanbase', 'dameng']
        for d in dialects:
            panel = AiWorkbenchPanel(dialect=d, language='zh')
            try:
                self.assertEqual(panel._dialect, d)
            finally:
                panel.deleteLater()

        panel = AiWorkbenchPanel(dialect='oracle', language='zh')
        try:
            panel.show()
            self.app.processEvents()

            # Case A: 同一 mode='wide' 下，C=1144 < 1200 (1440 展开导航基准) -> 窄面板开关栏可见，三栏非常驻
            panel.resize(1144, 900)
            panel.apply_layout_mode('wide', content_width=1144)
            self.app.processEvents()
            self.assertTrue(
                panel.narrow_chrome.isVisible(),
                "C=1144 < 1200 时必须显示窄面板切换条 (narrow_chrome)",
            )
            # 默认对象树与 AI 侧栏隐藏
            self.assertFalse(panel.left_pane.isVisible())
            self.assertFalse(panel.side_tabs.isVisible())

            # 验证窄面板开关交互：点击展开对象栏与 AI 助手
            panel.show_objects_btn.setChecked(True)
            self.app.processEvents()
            self.assertTrue(panel.left_pane.isVisible())

            panel.show_ai_side_btn.setChecked(True)
            self.app.processEvents()
            self.assertTrue(panel.side_tabs.isVisible())

            # 验证 C < 1200 默认分栏配置契约：树 200 + 主 C-216 (1144-216=928) + 辅助 240
            coord = _coord(panel.columns_splitter)
            self.assertEqual(coord.defaults, [200, 928, 240])

            # 收起窄面板
            panel.show_objects_btn.setChecked(False)
            panel.show_ai_side_btn.setChecked(False)
            self.app.processEvents()
            self.assertFalse(panel.left_pane.isVisible())
            self.assertFalse(panel.side_tabs.isVisible())

            # Case B: 同一 mode='wide' 下，C=1308 >= 1200 (1440 图标导航基准) -> 三栏常驻，窄面板开关栏隐藏
            panel.resize(1308, 900)
            panel.apply_layout_mode('wide', content_width=1308)
            self.app.processEvents()
            self.assertFalse(
                panel.narrow_chrome.isVisible(),
                "C=1308 >= 1200 时必须隐藏窄面板切换条 (narrow_chrome)",
            )
            self.assertTrue(
                panel.left_pane.isVisible(),
                "C=1308 >= 1200 时左侧对象树必须常驻可见",
            )
            self.assertTrue(
                panel.side_tabs.isVisible(),
                "C=1308 >= 1200 时右侧辅助侧栏必须常驻可见",
            )
            # 验证 C >= 1200 默认分栏配置契约：树 220 + 主 C-522 (1308-522=786) + 辅助 270
            coord = _coord(panel.columns_splitter)
            self.assertEqual(coord.defaults, [220, 786, 270])

            # Case C: 动态 resize 跨越 1200 阈值自适应 (1144 <-> 1308)
            panel.resize(1144, 900)
            self.app.processEvents()
            self.assertTrue(panel.narrow_chrome.isVisible())

            panel.resize(1308, 900)
            self.app.processEvents()
            self.assertFalse(panel.narrow_chrome.isVisible())
            self.assertTrue(panel.left_pane.isVisible())
            self.assertTrue(panel.side_tabs.isVisible())

            # Case D: 其他基准宽度验证 (§5.1 明确基准)
            # 1280 展开导航: C=1020 < 1200
            panel.resize(1020, 800)
            panel.apply_layout_mode('standard', content_width=1020)
            self.app.processEvents()
            self.assertTrue(panel.narrow_chrome.isVisible())

            # 1100 图标导航: C=984 < 1200
            panel.resize(984, 720)
            panel.apply_layout_mode('compact', content_width=984)
            self.app.processEvents()
            self.assertTrue(panel.narrow_chrome.isVisible())

            # Case E: 用户 splitter 持久化保留
            panel.resize(1308, 900)
            panel.apply_layout_mode('wide', content_width=1308)
            self.app.processEvents()
            custom_sizes = [250, 750, 308]
            panel.columns_splitter.setSizes(custom_sizes)
            self.app.processEvents()

            # 再次 apply_layout_mode 同 bucket 不得重置用户拖拽尺寸
            panel.apply_layout_mode('wide', content_width=1308)
            self.app.processEvents()
            live_sizes = panel.columns_splitter.sizes()
            self.assertAlmostEqual(live_sizes[0], custom_sizes[0], delta=20)
            self.assertAlmostEqual(live_sizes[1], custom_sizes[1], delta=20)
            self.assertAlmostEqual(live_sizes[2], custom_sizes[2], delta=20)
        finally:
            panel.deleteLater()

    def test_04_redis_workbench_geometry_and_contracts(self):
        """4. Redis 工作台：树 240 / 类型值区 min 280 / CLI 高度 200。"""
        panel = RedisWorkbenchPanel('zh')
        try:
            panel.resize(1440, 900)
            panel.show()
            self.app.processEvents()

            # 验证全局主分栏默认配置为 [240, 960]，允许收缩至 240
            coord_main = _coord(panel.main_split)
            self.assertIsNotNone(coord_main)
            self.assertEqual(coord_main.defaults, [240, 960])
            self.assertEqual(coord_main.min_sizes, [240, 480])

            # 验证类型值区最小高度 >= 280px (类型值区min280)
            self.assertGreaterEqual(
                panel.value_tabs.minimumHeight(),
                280,
                "Redis 类型值区必须满足最小高度 280px 规范契约",
            )

            # 验证右侧上下分栏 defaults 包含 CLI 高度 200
            coord_right = _coord(panel._bottom_split)
            self.assertIsNotNone(coord_right)
            self.assertEqual(coord_right.defaults, [520, 200])

            # 验证响应式适配
            panel.apply_layout_mode('wide')
            self.assertEqual(panel._layout_mode, 'wide')
            panel.apply_layout_mode('narrow')
            self.assertEqual(panel._layout_mode, 'narrow')
        finally:
            panel.deleteLater()

    def test_05_mongodb_workbench_geometry_and_contracts(self):
        """5. MongoDB 工作台：集合树 240 / 过滤输入框 min 96 / 响应式模式。"""
        panel = MongoDBWorkbenchPanel('zh')
        try:
            panel.resize(1440, 900)
            panel.show()
            self.app.processEvents()

            # 验证主分栏默认配置为 [240, 720]
            coord_main = _coord(panel.body_splitter)
            self.assertIsNotNone(coord_main)
            self.assertEqual(coord_main.defaults, [240, 720])

            # 验证查询过滤输入框最小高度 >= 96px (过滤96)
            self.assertGreaterEqual(
                panel.query_input.minimumHeight(),
                96,
                "MongoDB 过滤输入框必须满足最小高度 96px 规范契约",
            )

            # 验证 Shell 控制台与输出区域完好保留
            self.assertTrue(hasattr(panel, 'cmd_input'))
            self.assertTrue(hasattr(panel, 'cmd_output'))
            self.assertTrue(hasattr(panel, 'cmd_btn'))

            # 验证响应式适配
            panel.apply_layout_mode('wide')
            self.assertEqual(panel._layout_mode, 'wide')
            panel.apply_layout_mode('narrow')
            self.assertEqual(panel._layout_mode, 'narrow')
        finally:
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
