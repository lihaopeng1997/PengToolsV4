# -*- coding: utf-8 -*-
"""Focused behavior checks for Prism sidebar leaf and type-menu modes."""

import os
import sys
import unittest
from copy import deepcopy

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea

from ui.navigation_model import build_web_nav_model_data
from ui.prism_sidebar import PrismSidebar, derive_prism_groups


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

    def test_all_business_pages_are_direct_leaves(self):
        data = derive_prism_groups(self.model)
        self.assertEqual(
            [group['key'] for group in data['groups']],
            ['datacenter', 'ai', 'delivery', 'ops', 'devtools', 'personal'],
        )
        # 数据中心是统一入口 14；旧 18–23 仅为内部兼容路由，不进侧栏
        self.assertEqual([item['i'] for item in data['groups'][0]['items']], [14])
        self.assertEqual([item['i'] for item in data['groups'][1]['items']], [16, 17])
        self.assertEqual([item['i'] for item in data['groups'][-1]['items']], [8])
        self.assertEqual(len(self.sidebar._buttons), 17)
        self.assertIn(14, self.sidebar._buttons)
        self.assertFalse(self.sidebar._buttons.keys() & {15, 18, 19, 20, 21, 22, 23})
        self.assertFalse(self.sidebar._group_buttons)
        self.assertEqual(data['settings']['i'], 7)

    def test_direct_leaf_click_emits_business_index_and_marks_current(self):
        requested = []
        self.sidebar.navigate_requested.connect(requested.append)
        self.sidebar.set_current(16)
        self.assertTrue(self.sidebar._buttons[16].isChecked())
        self.assertFalse(self.sidebar._buttons[14].isChecked())

        QTest.mouseClick(self.sidebar._buttons[14], Qt.MouseButton.LeftButton)
        self.assertEqual(requested, [14])

    def test_icon_only_scroll_and_bottom_actions_remain_reachable(self):
        self.sidebar.set_icon_only(True)
        self.sidebar.set_current(14)
        self.assertTrue(self.sidebar._group_buttons['datacenter'].isChecked())
        self.assertNotIn(14, self.sidebar._buttons)
        self.assertTrue(all(not button.text() for button in self.sidebar._buttons.values()))
        self.assertEqual(self.sidebar._group_buttons['datacenter'].toolTip(), '数据中心')
        self.assertTrue(all(button.property('iconOnly') for button in self.sidebar._buttons.values()))
        scroll = self.sidebar.findChild(QScrollArea, 'sidebar-scroll')
        self.assertIsNotNone(scroll)
        scroll.ensureWidgetVisible(self.sidebar._group_buttons['personal'])
        APP.processEvents()
        self.assertTrue(self.sidebar._group_buttons['personal'].isVisible())
        self.assertTrue(self.sidebar._settings_button.isVisible())

        palette = []
        self.sidebar.palette_requested.connect(lambda: palette.append(True))
        QTest.mouseClick(self.sidebar._palette_button, Qt.MouseButton.LeftButton)
        self.assertEqual(palette, [True])

    def test_collapsed_type_menu_selects_leaf_and_expanding_restores_it(self):
        requested = []
        self.sidebar.navigate_requested.connect(requested.append)
        self.sidebar.set_current(10)
        self.sidebar.set_icon_only(True)

        self.assertEqual(
            list(self.sidebar._group_buttons),
            ['datacenter', 'ai', 'delivery', 'ops', 'devtools', 'personal'],
        )
        self.assertFalse(self.sidebar._buttons.keys() & {10, 2, 3, 9})
        self.assertTrue(self.sidebar._group_buttons['delivery'].isChecked())

        QTest.mouseClick(self.sidebar._group_buttons['delivery'], Qt.MouseButton.LeftButton)
        menu = self.sidebar._group_menu
        self.assertIsNotNone(menu)
        leaf_actions = [action for action in menu.actions() if action.data() is not None]
        self.assertEqual([action.data() for action in leaf_actions], [10, 2, 3, 9])
        self.assertTrue(leaf_actions[0].isChecked())
        leaf_actions[2].trigger()
        self.assertEqual(requested, [3])
        self.assertIsNone(self.sidebar._group_menu)

        self.sidebar.set_current(3)
        self.sidebar.set_icon_only(False)
        self.assertFalse(self.sidebar._group_buttons)
        self.assertTrue(self.sidebar._buttons[3].isChecked())

    def test_settings_still_navigates_as_a_real_leaf(self):
        requested = []
        original_model = deepcopy(self.model)
        self.assertEqual(self.model['settings']['icon'], 'gear')
        self.sidebar.navigate_requested.connect(requested.append)

        for language, icon_only, tooltip in (
            ('zh', False, '设置'),
            ('en', True, 'SET'),
            ('en', False, 'SET'),
        ):
            self.sidebar.set_language(language)
            self.sidebar.set_icon_only(icon_only)
            settings_button = self.sidebar._settings_button
            self.assertIsNotNone(settings_button)
            self.assertIs(settings_button, self.sidebar._buttons[7])
            self.assertFalse(settings_button.icon().isNull())
            self.assertEqual(settings_button.iconSize(), QSize(20, 20))
            self.assertEqual(settings_button.toolTip(), tooltip)
            self.assertEqual(self.sidebar.nav_model['settings']['icon'], 'gear')
            requested.clear()
            QTest.mouseClick(settings_button, Qt.MouseButton.LeftButton)
            self.assertEqual(requested, [7], (language, icon_only))

        self.assertEqual(self.model, original_model)

    def test_learning_page_is_rendered_without_easter_egg(self):
        self.sidebar.set_nav_model(build_web_nav_model_data(private_unlocked=False))
        self.assertIn(8, self.sidebar._buttons)
        self.assertIn('personal', [group['key'] for group in self.sidebar.nav_model['groups']])
        self.sidebar.set_icon_only(True)
        self.assertIn('personal', self.sidebar._group_buttons)
        self.sidebar.set_nav_model(self.model)
        self.assertIn('personal', self.sidebar._group_buttons)
        self.assertIn(8, [item['i'] for item in self.sidebar.nav_model['groups'][-1]['items']])


if __name__ == '__main__':
    unittest.main()
