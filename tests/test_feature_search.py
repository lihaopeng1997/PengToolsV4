# -*- coding: utf-8 -*-
"""标题栏功能搜索：选择模块，不打开悬浮栏。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from ui.feature_search import FeatureSearchPopup, feature_catalog, filter_features


class FeatureSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_catalog_lists_pages_and_hides_parent_and_legacy_slots(self):
        indexes = [entry.index for entry in feature_catalog()]
        self.assertIn(14, indexes)
        self.assertIn(7, indexes)
        self.assertNotIn(15, indexes)
        self.assertFalse(any(18 <= index <= 23 for index in indexes))

    def test_oracle_query_finds_data_center_only(self):
        matched = filter_features(feature_catalog(), 'oracle')
        self.assertEqual([entry.index for entry in matched], [14])

    def test_popup_choice_emits_nav_index_and_closes(self):
        anchor = QWidget()
        anchor.resize(180, 30)
        popup = FeatureSearchPopup()
        chosen = []
        popup.chosen.connect(chosen.append)
        popup.set_catalog(feature_catalog(), placeholder='搜索功能')
        popup.open_under(anchor)
        self.app.processEvents()
        popup.query.setText('日报')
        self.app.processEvents()
        self.assertEqual(popup.results.count(), 1)
        popup.query.returnPressed.emit()
        self.app.processEvents()
        self.assertEqual(chosen, [9])
        self.assertFalse(popup.isVisible())
        popup.close()
        anchor.deleteLater()

    def test_escape_hides_without_choosing(self):
        popup = FeatureSearchPopup()
        chosen = []
        popup.chosen.connect(chosen.append)
        popup.set_catalog(feature_catalog())
        popup.show()
        self.app.processEvents()
        popup.query.setText('不存在的模块xyz')
        self.assertEqual(popup.results.count(), 0)
        popup.eventFilter(popup.query, _key(Qt.Key.Key_Escape))
        self.assertFalse(popup.isVisible())
        self.assertEqual(chosen, [])
        popup.close()


def _key(key):
    from PyQt6.QtGui import QKeyEvent
    return QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
