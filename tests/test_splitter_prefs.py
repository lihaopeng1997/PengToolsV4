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


if __name__ == '__main__':
    unittest.main()
