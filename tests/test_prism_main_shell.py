# -*- coding: utf-8 -*-
"""Prism Main Client Shell & ContextHeader 定向测试 (PRISM-UI-P03)。

验证点：
1. 客户区层级与布局几何：ContextHeader 52px, StatusBar 28px, content_area 边距 0, PageBody 承载 Stack；
2. 响应式边距与快速面板紧凑状态：Wide(24)/Standard(20)/Compact(16)/Narrow(16)；
3. ContextHeader 初始状态（首页 / Home）；
4. 导航叶子项同步（普通叶子项与数据库 18..23 叶子项）；
5. 父级导航（14, 15）不切换页面、不改变 ContextHeader；
6. 锁定态私密入口（8）不切换页面、不改变 ContextHeader；
7. 语言切换双语同步（ZH / EN）；
8. 快速面板按钮点击信号触发 MainWindow._open_quick_panel 恰好一次；
9. 主题切换刷新 Header 图标且不重建 ContextHeader 控件实例；
10. Windows 原生系统边框保留（无 FramelessWindowHint）；
11. ContextHeader 独立控件单元测试与 API 契约。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QMargins, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QPushButton, QStackedWidget, QStatusBar

from ui.layout_metrics import (
    CONTEXT_ACTION_H, CONTEXT_HEADER_H, STATUS_H,
    MARGIN_COMPACT, MARGIN_STANDARD, MARGIN_WIDE,
)
from ui.page_chrome import ContextHeader


_app = QApplication.instance() or QApplication([])
_SHARED_WINDOW = None


def _get_window():
    global _SHARED_WINDOW
    if _SHARED_WINDOW is None:
        os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
        from main_window import MainWindow
        from config import DEFAULT_SETTINGS
        settings = dict(DEFAULT_SETTINGS)
        settings['ui_web_shell'] = False
        with patch('main_window.load_settings', return_value=settings):
            _SHARED_WINDOW = MainWindow()
    return _SHARED_WINDOW




class ContextHeaderUnitTests(unittest.TestCase):
    """ContextHeader 独立控件层测试。"""

    def setUp(self):
        self.header = ContextHeader()

    def tearDown(self):
        self.header.deleteLater()

    def test_dimensions_and_object_names(self):
        self.assertEqual(self.header.objectName(), 'context-header')
        self.assertEqual(self.header.height(), CONTEXT_HEADER_H)
        self.assertEqual(self.header.minimumHeight(), CONTEXT_HEADER_H)
        self.assertEqual(self.header.maximumHeight(), CONTEXT_HEADER_H)
        self.assertEqual(self.header.icon_label.objectName(), 'context-header-icon')
        self.assertEqual(self.header.name_label.objectName(), 'context-header-name')
        self.assertEqual(self.header.quick_btn.objectName(), 'context-quick-btn')
        self.assertEqual(self.header.quick_btn.height(), CONTEXT_ACTION_H)

    def test_quick_panel_requested_signal(self):
        called = []
        self.header.quick_panel_requested.connect(lambda: called.append(True))
        self.header.quick_btn.click()
        self.assertEqual(len(called), 1)

    def test_set_context_updates_name_and_icon(self):
        self.header.set_context('需求管理', 'requirements')
        self.assertEqual(self.header.name_label.text(), '需求管理')
        pix = self.header.icon_label.pixmap()
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())

    def test_set_language_and_tooltips(self):
        self.header.set_language('zh')
        self.assertIn('快速面板', self.header.quick_btn.toolTip())
        self.assertIn('Ctrl+K', self.header.quick_btn.toolTip())
        self.assertEqual(self.header.quick_btn.text(), '快速面板')

        self.header.set_language('en')
        self.assertIn('Quick Panel', self.header.quick_btn.toolTip())
        self.assertIn('Ctrl+K', self.header.quick_btn.toolTip())
        self.assertEqual(self.header.quick_btn.text(), 'Quick Panel')

    def test_compact_mode_toggle(self):
        self.header.set_compact(True)
        self.assertEqual(self.header.quick_btn.text(), '')
        self.assertEqual(self.header.quick_btn.width(), CONTEXT_ACTION_H)
        self.assertEqual(self.header.quick_btn.property('compact'), True)

        self.header.set_compact(False)
        self.assertNotEqual(self.header.quick_btn.text(), '')
        self.assertEqual(self.header.quick_btn.maximumWidth(), 16777215)
        self.assertEqual(self.header.quick_btn.property('compact'), False)

    def test_horizontal_padding(self):
        self.header.set_horizontal_padding(24)
        margins = self.header.layout().contentsMargins()
        self.assertEqual(margins.left(), 24)
        self.assertEqual(margins.right(), 24)
        self.assertEqual(margins.top(), 0)
        self.assertEqual(margins.bottom(), 0)


class MainWindowClientShellTests(unittest.TestCase):
    """主窗口客户区外壳与 ContextHeader 集成测试。"""

    @classmethod
    def setUpClass(cls):
        cls.win = _get_window()

    def test_client_shell_geometry_and_hierarchy(self):
        # 1. content_area: margins 0, spacing 0
        content_margins = self.win._content_layout.contentsMargins()
        self.assertEqual(content_margins, QMargins(0, 0, 0, 0))
        self.assertEqual(self.win._content_layout.spacing(), 0)

        # 2. ContextHeader: 52px height, child of content_frame
        header = getattr(self.win, '_context_header', None)
        self.assertIsNotNone(header)
        self.assertIsInstance(header, ContextHeader)
        self.assertEqual(header.objectName(), 'context-header')
        self.assertEqual(header.height(), CONTEXT_HEADER_H)

        # 3. page-body: QFrame, contains stack, spacing 0
        body = getattr(self.win, '_page_body', None)
        self.assertIsNotNone(body)
        self.assertIsInstance(body, QFrame)
        self.assertEqual(body.objectName(), 'page-body')
        self.assertEqual(self.win._page_body_layout.spacing(), 0)
        self.assertEqual(self.win.stack.parent(), body)

        # 4. QStackedWidget count and slots intact
        from main_window import _STACK_PANEL_ATTRS
        self.assertEqual(self.win.stack.count(), len(_STACK_PANEL_ATTRS))

        # 5. QStatusBar: 28px height, objectName status_bar
        self.assertEqual(self.win.status_bar.objectName(), 'status_bar')
        self.assertEqual(self.win.status_bar.height(), STATUS_H)
        self.assertEqual(self.win.status_bar.minimumHeight(), STATUS_H)
        self.assertEqual(self.win.status_bar.maximumHeight(), STATUS_H)

    def test_responsive_margins_and_compact_header(self):
        # wide: 24px margins, expanded quick btn
        self.win._on_layout_mode('wide', False)
        self.assertEqual(self.win._page_body_layout.contentsMargins(), QMargins(24, 24, 24, 24))
        self.assertEqual(self.win._context_header._horizontal_padding, 24)
        self.assertFalse(self.win._context_header._compact)
        self.assertNotEqual(self.win._context_header.quick_btn.text(), '')

        # standard: 20px margins, expanded quick btn
        self.win._on_layout_mode('standard', False)
        self.assertEqual(self.win._page_body_layout.contentsMargins(), QMargins(20, 20, 20, 20))
        self.assertEqual(self.win._context_header._horizontal_padding, 20)
        self.assertFalse(self.win._context_header._compact)
        self.assertNotEqual(self.win._context_header.quick_btn.text(), '')

        # compact: 16px margins, icon-only quick btn
        self.win._on_layout_mode('compact', False)
        self.assertEqual(self.win._page_body_layout.contentsMargins(), QMargins(16, 16, 16, 16))
        self.assertEqual(self.win._context_header._horizontal_padding, 16)
        self.assertTrue(self.win._context_header._compact)
        self.assertEqual(self.win._context_header.quick_btn.text(), '')

        # narrow: 16px margins, icon-only quick btn
        self.win._on_layout_mode('narrow', False)
        self.assertEqual(self.win._page_body_layout.contentsMargins(), QMargins(16, 16, 16, 16))
        self.assertEqual(self.win._context_header._horizontal_padding, 16)
        self.assertTrue(self.win._context_header._compact)
        self.assertEqual(self.win._context_header.quick_btn.text(), '')

        # restore standard
        self.win._on_layout_mode('standard', False)

    def test_initial_context_header_state(self):
        self.win._show_panel(0)
        self.assertEqual(self.win._current_nav_index, 0)
        self.assertEqual(self.win._context_header.name_label.text(), '首页')
        pix = self.win._context_header.icon_label.pixmap()
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())

    def test_leaf_navigation_updates_context_header(self):
        self.win._show_panel(1)
        self.assertEqual(self.win._context_header.name_label.text(), '证件类型')

        self.win._show_panel(2)
        self.assertEqual(self.win._context_header.name_label.text(), '发版联动')

        self.win._show_panel(10)
        self.assertEqual(self.win._context_header.name_label.text(), '需求管理')

    def test_database_leaves_update_context_header(self):
        # 18: Oracle
        self.win._show_panel(18)
        self.assertEqual(self.win._context_header.name_label.text(), 'Oracle')

        # 20: OceanBase
        self.win._show_panel(20)
        self.assertEqual(self.win._context_header.name_label.text(), 'OceanBase')

        # 23: MongoDB
        self.win._show_panel(23)
        self.assertEqual(self.win._context_header.name_label.text(), 'MongoDB')

    def test_parent_nav_does_not_update_context_header(self):
        self.win._show_panel(1)
        self.assertEqual(self.win._context_header.name_label.text(), '证件类型')

        # 14 = SQL Console parent
        self.win._show_panel(14)
        self.assertEqual(self.win._current_nav_index, 1)
        self.assertEqual(self.win._context_header.name_label.text(), '证件类型')

        # 15 = AI parent
        self.win._show_panel(15)
        self.assertEqual(self.win._current_nav_index, 1)
        self.assertEqual(self.win._context_header.name_label.text(), '证件类型')

    def test_locked_private_item_does_not_update_context_header(self):
        was_unlocked = self.win._private_unlocked
        try:
            self.win._private_unlocked = False
            self.win._show_panel(1)
            self.assertEqual(self.win._context_header.name_label.text(), '证件类型')

            # 8 = 自我学习 (locked)
            self.win._show_panel(8)
            self.assertEqual(self.win._current_nav_index, 1)
            self.assertEqual(self.win._context_header.name_label.text(), '证件类型')
        finally:
            self.win._private_unlocked = was_unlocked

    def test_language_switch_updates_context_header(self):
        self.win._show_panel(0)
        self.win._set_language(0)  # zh
        self.assertEqual(self.win._context_header.name_label.text(), '首页')
        self.assertIn('快速面板', self.win._context_header.quick_btn.toolTip())

        self.win._set_language(1)  # en
        self.assertEqual(self.win._context_header.name_label.text(), 'Home')
        self.assertIn('Quick Panel', self.win._context_header.quick_btn.toolTip())

        # restore zh
        self.win._set_language(0)
        self.assertEqual(self.win._context_header.name_label.text(), '首页')

    def test_quick_button_click_triggers_open_quick_panel(self):
        called = []
        original = self.win._open_quick_panel
        try:
            self.win._open_quick_panel = lambda: called.append(True)
            self.win._context_header.quick_btn.click()
            self.assertEqual(len(called), 1)
        finally:
            self.win._open_quick_panel = original

    def test_theme_toggle_refreshes_header_icon_without_widget_recreation(self):
        header_id = id(self.win._context_header)
        dark_settings = dict(self.win._settings)
        dark_settings['ui_theme'] = 'black'
        self.win._apply_settings(dark_settings, persist=False)

        # Header widget identity unchanged
        self.assertEqual(id(self.win._context_header), header_id)
        pix = self.win._context_header.icon_label.pixmap()
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())

        # Restore calm
        light_settings = dict(self.win._settings)
        light_settings['ui_theme'] = 'calm'
        self.win._apply_settings(light_settings, persist=False)
        self.assertEqual(id(self.win._context_header), header_id)

    def test_system_frame_preserved(self):
        # Windows 原生非客户区外框保留，禁止设置 FramelessWindowHint
        flags = self.win.windowFlags()
        self.assertFalse(
            bool(flags & Qt.WindowType.FramelessWindowHint),
            'MainWindow 不得设置 FramelessWindowHint，必须保留操作系统原生窗口外边框',
        )


if __name__ == '__main__':
    unittest.main()
