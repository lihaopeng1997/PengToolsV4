# -*- coding: utf-8 -*-
"""Focused behavior checks for the native Prism group menu."""

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from ui.navigation_model import build_web_nav_model_data
from ui.prism_sidebar import PrismSidebar, derive_prism_groups
from ui.web_shell import HomeBridge


APP = QApplication.instance() or QApplication([])


class PrismSidebarMenuTests(unittest.TestCase):
    def setUp(self):
        self.model = build_web_nav_model_data(private_unlocked=True, current=0)
        self.sidebar = PrismSidebar(self.model)
        self.sidebar.resize(248, 700)
        self.sidebar.show()
        APP.processEvents()

    def tearDown(self):
        self.sidebar.close()
        self.sidebar.deleteLater()
        APP.processEvents()

    def test_display_groups_are_derived_from_nav_model(self):
        data = derive_prism_groups(self.model)
        self.assertEqual(
            [group['key'] for group in data['groups']],
            ['workspace:14', 'ai:15', 'delivery', 'ops', 'devtools', 'personal'],
        )
        self.assertEqual([item['i'] for item in data['groups'][0]['items']], [18, 19, 20, 21, 22, 23])
        self.assertEqual([item['i'] for item in data['groups'][1]['items']], [16, 17])
        self.assertEqual([item['i'] for item in data['groups'][-1]['items']], [8])
        self.assertEqual(data['settings']['i'], 7)

    def test_group_menu_emits_leaf_index_and_marks_current_item(self):
        requested = []
        self.sidebar.navigate_requested.connect(requested.append)
        self.sidebar.set_current(19)
        self.assertTrue(self.sidebar._group_buttons['workspace:14'].isChecked())

        self.sidebar.show_group_menu('workspace:14', self.sidebar._group_buttons['workspace:14'])
        APP.processEvents()
        menu = self.sidebar._group_menu
        self.assertIsNotNone(menu)
        actions = [action for action in menu.actions() if action.data() is not None]
        self.assertEqual([action.data() for action in actions], [18, 19, 20, 21, 22, 23])
        self.assertTrue(actions[1].isChecked())
        actions[0].trigger()
        APP.processEvents()
        self.assertEqual(requested, [18])

    def test_icon_only_mode_keeps_group_menu_and_bottom_actions_reachable(self):
        self.sidebar.set_icon_only(True)
        self.assertTrue(all(not button.text() for button in self.sidebar._group_buttons.values()))
        self.assertEqual(self.sidebar._group_buttons['workspace:14'].property('iconOnly'), True)

        self.sidebar.show_group_menu_at(
            'workspace:14',
            {'left': 2, 'top': 2, 'right': 72, 'bottom': 46, 'width': 70, 'height': 44},
        )
        APP.processEvents()
        self.assertIsNotNone(self.sidebar._group_menu)
        self.assertEqual(
            [a.data() for a in self.sidebar._group_menu.actions() if a.data() is not None],
            [18, 19, 20, 21, 22, 23],
        )

        palette = []
        self.sidebar.palette_requested.connect(lambda: palette.append(True))
        QTest.mouseClick(self.sidebar._palette_button, Qt.MouseButton.LeftButton)
        self.assertEqual(palette, [True])

    def test_settings_still_navigates_as_a_real_leaf(self):
        requested = []
        self.sidebar.navigate_requested.connect(requested.append)
        QTest.mouseClick(self.sidebar._settings_button, Qt.MouseButton.LeftButton)
        self.assertEqual(requested, [7])

    def test_invalid_web_anchor_is_ignored_safely(self):
        self.sidebar.show_group_menu_at(
            'workspace:14',
            {'left': 'NaN', 'top': 0, 'right': 72, 'bottom': 46, 'width': 70, 'height': 44},
        )
        self.assertIsNone(self.sidebar._group_menu)

    def test_bridge_group_request_is_small_and_data_free(self):
        bridge = HomeBridge()
        received = []
        bridge.navGroupRequested.connect(lambda key, rect: received.append((key, rect)))
        bridge.openNavGroup('workspace:14', '{"left":2,"right":72}')
        self.assertEqual(received, [('workspace:14', '{"left":2,"right":72}')])


if __name__ == '__main__':
    unittest.main()
