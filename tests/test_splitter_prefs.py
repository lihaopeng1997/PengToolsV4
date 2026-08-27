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
    from ui.splitter_prefs import install_splitter_prefs
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
        self.assertGreaterEqual(splitter.handleWidth(), 6)

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


if __name__ == '__main__':
    unittest.main()
