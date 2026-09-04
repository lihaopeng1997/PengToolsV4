# -*- coding: utf-8 -*-
"""install_splitter_prefs：双击 handle 复位默认比例。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QLabel, QSplitter
    from ui.splitter_prefs import SPLITTER_HANDLE_WIDTH, install_splitter_prefs
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class SplitterPrefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_double_click_handle_restores_defaults(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('left'))
        splitter.addWidget(QLabel('right'))
        defaults = [200, 300]
        install_splitter_prefs(splitter, defaults=defaults, on_changed=None)
        splitter.resize(500, 120)
        splitter.show()
        QApplication.processEvents()

        self.assertFalse(splitter.childrenCollapsible())
        self.assertGreaterEqual(splitter.handleWidth(), SPLITTER_HANDLE_WIDTH)

        splitter.setSizes([80, 420])
        QApplication.processEvents()
        distorted = list(splitter.sizes())
        self.assertNotEqual(distorted, defaults)

        handle = splitter.handle(1)
        self.assertIsNotNone(handle)
        QTest.mouseDClick(handle, Qt.MouseButton.LeftButton)
        QApplication.processEvents()

        restored = list(splitter.sizes())
        self.assertEqual(len(restored), 2)
        # Qt 可能按 handle/最小宽微调，允许小误差
        self.assertAlmostEqual(restored[0], defaults[0], delta=8)
        self.assertAlmostEqual(restored[1], defaults[1], delta=8)
        self.assertLess(abs(restored[0] - defaults[0]), abs(distorted[0] - defaults[0]))

    def test_children_not_collapsible_after_install(self):
        splitter = QSplitter()
        splitter.addWidget(QLabel('a'))
        splitter.addWidget(QLabel('b'))
        splitter.setChildrenCollapsible(True)
        install_splitter_prefs(splitter, defaults=[100, 100])
        self.assertFalse(splitter.childrenCollapsible())

    def test_accessible_name_and_clamp(self):
        from ui.splitter_prefs import clamp_splitter_sizes, splitter_storage_key
        self.assertEqual(
            splitter_storage_key('ops-log', 'main', 'narrow'),
            'ops-log|main|narrow',
        )
        clamped = clamp_splitter_sizes([10, 990], [120, 200], total=500)
        self.assertGreaterEqual(clamped[0], 120)
        self.assertGreaterEqual(clamped[1], 200)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        install_splitter_prefs(
            splitter,
            defaults=[200, 300],
            page_id='unit-test',
            tab_id='t',
            bucket='standard',
            accessible_name='单元测试分隔',
            persist=False,
        )
        self.assertEqual(splitter.accessibleName(), '单元测试分隔')
        handle = splitter.handle(1)
        self.assertTrue(bool(handle.accessibleName()))

    def test_arrow_key_nudges_sizes(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        install_splitter_prefs(splitter, defaults=[250, 250], min_sizes=[80, 80], persist=False)
        splitter.resize(500, 120)
        splitter.show()
        QApplication.processEvents()
        before = list(splitter.sizes())
        splitter.setFocus()
        QTest.keyClick(splitter, Qt.Key.Key_Right)
        QApplication.processEvents()
        after = list(splitter.sizes())
        self.assertNotEqual(before, after)

    def test_extreme_saved_sizes_restore_safe_defaults(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        splitter.resize(500, 120)
        install_splitter_prefs(
            splitter,
            defaults=[200, 300],
            saved=[900, 20],
            min_sizes=[120, 160],
            persist=False,
        )
        splitter.show()
        QApplication.processEvents()
        sizes = list(splitter.sizes())
        self.assertAlmostEqual(sizes[0], 200, delta=8)
        self.assertAlmostEqual(sizes[1], 300, delta=8)

    def test_s1_install_20x_callback_once_per_move(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        splitter.resize(600, 200)
        splitter.show()
        QApplication.processEvents()

        calls = []
        for _ in range(20):
            install_splitter_prefs(
                splitter,
                defaults=[250, 350],
                min_sizes=[100, 100],
                debounce_ms=10,
                on_changed=lambda sz: calls.append(sz),
                persist=False,
            )

        splitter.splitterMoved.emit(300, 1)
        QTest.qWait(50)
        QApplication.processEvents()
        self.assertEqual(len(calls), 1)

    def test_s2_handle_hit_width_ge_8(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        install_splitter_prefs(splitter, defaults=[200, 300], persist=False)
        self.assertGreaterEqual(splitter.handleWidth(), 8)
        handle = splitter.handle(1)
        self.assertEqual(handle.cursor().shape(), Qt.CursorShape.SplitHCursor)

    def test_s3_proportional_normalization(self):
        from ui.splitter_prefs import normalize_splitter_sizes
        normalized = normalize_splitter_sizes(
            [960, 960],
            defaults=[600, 600],
            min_sizes=[200, 200],
            current_total=1280,
            old_total=1920,
        )
        self.assertEqual(normalized, [640, 640])

    def test_s4_extreme_old_size_resets_to_defaults(self):
        from ui.splitter_prefs import normalize_splitter_sizes
        normalized = normalize_splitter_sizes(
            [10, 1270],
            defaults=[400, 880],
            min_sizes=[200, 300],
            current_total=1280,
        )
        self.assertEqual(normalized, [400, 880])

    def test_s5_same_layout_bucket_does_not_reset_drag(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(QLabel('L'))
        splitter.addWidget(QLabel('R'))
        splitter.resize(1000, 300)
        splitter.show()
        QApplication.processEvents()

        install_splitter_prefs(
            splitter,
            defaults=[400, 600],
            min_sizes=[200, 200],
            page_id='test-page',
            tab_id='test-tab',
            bucket='standard',
            persist=False,
        )
        QApplication.processEvents()
        splitter.setSizes([550, 450])
        dragged = list(splitter.sizes())

        install_splitter_prefs(
            splitter,
            defaults=[400, 600],
            min_sizes=[200, 200],
            page_id='test-page',
            tab_id='test-tab',
            bucket='standard',
            persist=False,
        )
        QApplication.processEvents()
        self.assertEqual(list(splitter.sizes()), dragged)

    def test_s6_s7_s8_s9_sql_workbench_splitters(self):
        from panels.ai_workbench_panel import sql_splitter_tab_id
        # S8: Dialect key consistency
        self.assertEqual(sql_splitter_tab_id('columns', 'Oracle'), 'columns-oracle')
        self.assertEqual(sql_splitter_tab_id('body', 'MySQL'), 'body-mysql')
        self.assertEqual(sql_splitter_tab_id('columns', ''), 'columns')

        # S6: Horizontal columns splitter all panes >= mins [260, 480, 300]
        col_splitter = QSplitter(Qt.Orientation.Horizontal)
        col_splitter.addWidget(QLabel('Tree'))
        col_splitter.addWidget(QLabel('Editor'))
        col_splitter.addWidget(QLabel('AI'))
        col_splitter.resize(1600, 600)
        col_splitter.show()
        QApplication.processEvents()

        install_splitter_prefs(
            col_splitter,
            defaults=[300, 700, 340],
            min_sizes=[260, 480, 300],
            page_id='sql-console',
            tab_id=sql_splitter_tab_id('columns', 'oracle'),
            bucket='wide',
            persist=False,
        )
        QApplication.processEvents()
        col_sizes = col_splitter.sizes()
        self.assertGreaterEqual(col_sizes[0], 260)
        self.assertGreaterEqual(col_sizes[1], 480)
        self.assertGreaterEqual(col_sizes[2], 300)

        # S7: Vertical body splitter results >= min 200
        body_splitter = QSplitter(Qt.Orientation.Vertical)
        body_splitter.addWidget(QLabel('Top'))
        body_splitter.addWidget(QLabel('Results'))
        body_splitter.resize(1000, 800)
        body_splitter.show()
        QApplication.processEvents()

        install_splitter_prefs(
            body_splitter,
            defaults=[620, 300],
            min_sizes=[320, 200],
            page_id='sql-console',
            tab_id=sql_splitter_tab_id('body', 'oracle'),
            bucket='wide',
            persist=False,
        )
        QApplication.processEvents()
        body_sizes = body_splitter.sizes()
        self.assertGreaterEqual(body_sizes[0], 320)
        self.assertGreaterEqual(body_sizes[1], 200)

        # S9: Drag -> save -> reconstruct -> restore approximately same ratio
        col_splitter.setSizes([350, 750, 400])
        saved_sizes = list(col_splitter.sizes())

        new_splitter = QSplitter(Qt.Orientation.Horizontal)
        new_splitter.addWidget(QLabel('Tree'))
        new_splitter.addWidget(QLabel('Editor'))
        new_splitter.addWidget(QLabel('AI'))
        new_splitter.resize(1600, 600)
        new_splitter.show()
        QApplication.processEvents()

        install_splitter_prefs(
            new_splitter,
            defaults=[300, 700, 340],
            saved=saved_sizes,
            min_sizes=[260, 480, 300],
            page_id='sql-console',
            tab_id=sql_splitter_tab_id('columns', 'oracle'),
            bucket='wide',
            persist=False,
        )
        QApplication.processEvents()
        restored = list(new_splitter.sizes())
        self.assertAlmostEqual(restored[0], saved_sizes[0], delta=10)
        self.assertAlmostEqual(restored[1], saved_sizes[1], delta=10)
        self.assertAlmostEqual(restored[2], saved_sizes[2], delta=10)

    def test_s10_sql_real_panel_apply_layout_mode_preserves_drag(self):
        """S10: 真实 AiWorkbenchPanel 在同 bucket apply_layout_mode 时绝不重置用户拖拽尺寸。"""
        from panels.ai_workbench_panel import AiWorkbenchPanel

        panel = AiWorkbenchPanel(dialect='oracle')
        panel.resize(1600, 900)
        panel.show()
        panel.apply_layout_mode('wide')
        QApplication.processEvents()

        # 模拟用户拖拽 custom 尺寸
        custom_wide = [350, 780, 420]
        panel.columns_splitter.setSizes(custom_wide)
        QApplication.processEvents()

        # 再次 apply_layout_mode('wide') (同 bucket)
        panel.apply_layout_mode('wide')
        QApplication.processEvents()

        wide_sizes = panel.columns_splitter.sizes()
        self.assertAlmostEqual(wide_sizes[0], custom_wide[0], delta=20)
        self.assertAlmostEqual(wide_sizes[1], custom_wide[1], delta=20)
        self.assertAlmostEqual(wide_sizes[2], custom_wide[2], delta=20)

        # 切到 compact 模式（视口调整到典型 compact 宽度 1100）
        panel.resize(1100, 700)
        panel.apply_layout_mode('compact')
        QApplication.processEvents()

        # 拖拽 compact 尺寸
        custom_compact = [240, 560, 260]
        panel.columns_splitter.setSizes(custom_compact)
        QApplication.processEvents()

        # 第二次 apply_layout_mode('compact')，不得 reset
        panel.apply_layout_mode('compact')
        QApplication.processEvents()

        compact_sizes = panel.columns_splitter.sizes()
        self.assertAlmostEqual(compact_sizes[0], custom_compact[0], delta=20)
        self.assertAlmostEqual(compact_sizes[1], custom_compact[1], delta=20)
        self.assertAlmostEqual(compact_sizes[2], custom_compact[2], delta=20)

        panel.close()


if __name__ == '__main__':
    unittest.main()
