# -*- coding: utf-8 -*-
"""主题预览、弹框图标对比度、格式工具导航定向测试。"""

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication
    from config import DEFAULT_SETTINGS
    from panels.settings_panel import SettingsPanel, ThemeCard, ThemePreviewWidget
    from ui.icons import badge_background_token, make_badge_label, status_icon_tint
    from ui.navigation_model import NAV_ITEMS, NAV_MODEL, display_name, normalize_floating_shortcuts
    from ui.theme_manager import preview_swatches
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class ThemePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_swatches_differ_across_themes(self):
        themes = ('calm', 'clear', 'warm', 'night')
        primaries = {preview_swatches(t)['primary'] for t in themes}
        bgs = {preview_swatches(t)['bg'] for t in themes}
        self.assertEqual(len(primaries), 4)
        self.assertEqual(len(bgs), 4)

    def test_theme_preview_widget_has_non_transparent_pixels(self):
        for theme_id in ('calm', 'clear', 'warm', 'night'):
            widget = ThemePreviewWidget(theme_id)
            widget.resize(160, 56)
            widget.show()
            self.app.processEvents()
            pix = widget.grab()
            image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            opaque = 0
            colors = set()
            for y in range(0, image.height(), 2):
                for x in range(0, image.width(), 2):
                    c = QColor(image.pixel(x, y))
                    if c.alpha() > 200:
                        opaque += 1
                        colors.add((c.red() // 16, c.green() // 16, c.blue() // 16))
            self.assertGreater(opaque, 40, theme_id)
            self.assertGreaterEqual(len(colors), 2, theme_id)
            widget.close()

    def test_settings_panel_creates_four_theme_cards(self):
        page = SettingsPanel(DEFAULT_SETTINGS)
        self.assertEqual(len(page._theme_cards), 4)
        for theme_id, card in page._theme_cards.items():
            self.assertIsInstance(card, ThemeCard)
            self.assertIsInstance(card.preview, ThemePreviewWidget)
            self.assertEqual(card.preview.theme_id, theme_id)


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class BadgeContrastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_status_icon_tint_differs_from_badge_background(self):
        for kind in ('info', 'success', 'warning', 'error', 'danger'):
            tint = status_icon_tint(kind)
            bg = badge_background_token(kind)
            self.assertTrue(tint)
            self.assertTrue(bg)
            self.assertNotEqual(tint.lower(), bg.lower(), kind)
            badge = make_badge_label(kind, size=40, icon_size=22)
            self.assertFalse(badge.pixmap() is None or badge.pixmap().isNull(), kind)
            prop_tint = badge.property('iconTint')
            self.assertEqual(str(prop_tint).lower(), tint.lower())


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class FormatToolsNavTests(unittest.TestCase):
    def test_format_tools_nav_index_is_stable_eleven(self):
        item = NAV_ITEMS.get(11)
        self.assertIsNotNone(item)
        self.assertEqual(item.name_zh, '格式工具')
        self.assertEqual(item.icon_role, 'json')
        self.assertTrue(item.floating_eligible)
        # 0–10 历史含义保持
        self.assertEqual(display_name(5, 'zh'), '加解密')
        self.assertEqual(display_name(10, 'zh'), '需求管理')
        # 出现在 devtools 分组
        dev = dict(NAV_MODEL)['devtools']
        indexes = [row[0] for row in dev]
        self.assertIn(11, indexes)

    def test_floating_shortcuts_accept_format_tools(self):
        result = normalize_floating_shortcuts([10, 11, 5])
        self.assertEqual(result, [10, 11, 5])

    def test_format_panel_and_gateway_import(self):
        from panels.format_panel import FormatToolsPanel
        from panels.gateway_panel import GatewayDecodePanel
        from main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        fmt = FormatToolsPanel()
        self.assertEqual(fmt.tabs.count(), 3)
        gw = GatewayDecodePanel()
        self.assertTrue(hasattr(gw, 'open_format_xml'))
        self.assertIsNone(gw.xml_workspace)
        self.assertEqual(MainWindow._stack_index_for_nav(11), 10)
        self.assertEqual(MainWindow._stack_index_for_nav(10), 9)
        self.assertEqual(MainWindow._stack_index_for_nav(5), 5)


if __name__ == '__main__':
    unittest.main()
