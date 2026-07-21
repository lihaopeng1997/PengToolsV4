# -*- coding: utf-8 -*-
"""夜间主题 token + 响应式布局定向测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QAction, QColor, QImage
    from PyQt6.QtWidgets import QApplication
    from config import DEFAULT_SETTINGS
    from panels.dashboard_panel import DashboardPanel
    from panels.format_panel import FormatToolsPanel
    from panels.gateway_panel import GatewayDecodePanel
    from panels.settings_panel import SettingsPanel, ThemePreviewWidget
    from ui.aurora_progress import AuroraProgress
    from ui.responsive import (
        ActionDensity, ResponsiveActionBar, classify_layout, density_for_mode,
        editor_orientation_for_mode, is_low_height,
    )
    from ui.theme_manager import (
        THEMES, ThemeManager, theme_display_name, theme_subtitle,
    )
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class NightThemeTokenTests(unittest.TestCase):
    def test_night_display_name(self):
        self.assertEqual(theme_display_name('night', 'zh'), '夜间安读')
        self.assertIn('低眩光', theme_subtitle('night', 'zh'))

    def test_all_themes_have_extended_tokens(self):
        required = (
            'ELEVATED_SURFACE', 'CODE_BG', 'OVERLAY_BG', 'ON_PRIMARY',
            'INFO_BG', 'SUCCESS_BG', 'WARNING_BG', 'DANGER_BG',
            'SEARCH_MATCH', 'SEARCH_CURRENT', 'LOADING_TRACK',
            'MONTH_HEADER_BG', 'MONTH_HEADER_FG',
        )
        for tid, pal in THEMES.items():
            for key in required:
                self.assertIn(key, pal, msg=f'{tid}.{key}')

    def test_night_surfaces_not_white(self):
        pal = THEMES['night']
        self.assertEqual(pal['APP_BG'], '#1B211E')
        for key in (
            'APP_BG', 'SIDEBAR_BG', 'SURFACE', 'SURFACE_SOFT', 'ELEVATED_SURFACE',
            'CODE_BG', 'INPUT_BG', 'TABLE_ALT', 'DANGER_BG', 'SUCCESS_BG',
        ):
            val = pal[key].upper()
            self.assertNotIn(val, ('#FFFFFF', '#FFF', 'WHITE'), msg=key)
            # 不应接近纯白
            hexv = val.lstrip('#')
            if len(hexv) >= 6:
                r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
                self.assertLess((r + g + b) / 3, 220, msg=f'{key} too bright')

    def test_render_night_qss_has_tokens_applied(self):
        tm = ThemeManager.instance()
        tm.load_template()
        qss = tm.render('night')
        self.assertIn('#1B211E', qss)
        self.assertIn('#29332E', qss)
        self.assertNotIn('color: white;', qss.lower().replace(' ', ''))

    def test_night_preview_not_blank(self):
        app = QApplication.instance() or QApplication([])
        w = ThemePreviewWidget('night')
        w.resize(180, 64)
        w.show()
        app.processEvents()
        image = w.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        opaque = 0
        near_white = 0
        colors = set()
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                c = QColor(image.pixel(x, y))
                if c.alpha() > 200:
                    opaque += 1
                    colors.add((c.red() // 20, c.green() // 20, c.blue() // 20))
                    if c.red() > 245 and c.green() > 245 and c.blue() > 245:
                        near_white += 1
        self.assertGreater(opaque, 40)
        self.assertGreaterEqual(len(colors), 2)
        self.assertLess(near_white, max(1, opaque * 0.2))
        w.close()


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class ResponsiveLayoutTests(unittest.TestCase):
    def test_breakpoints(self):
        self.assertEqual(classify_layout(1440), 'wide')
        self.assertEqual(classify_layout(1439), 'standard')
        self.assertEqual(classify_layout(1280), 'standard')
        self.assertEqual(classify_layout(1279), 'compact')
        self.assertEqual(classify_layout(1080), 'compact')
        self.assertEqual(classify_layout(1079), 'narrow')
        self.assertEqual(classify_layout(960), 'narrow')
        self.assertTrue(is_low_height(700))
        self.assertFalse(is_low_height(720))

    def test_density_mapping(self):
        self.assertEqual(density_for_mode('wide'), ActionDensity.FULL)
        self.assertEqual(density_for_mode('standard'), ActionDensity.FULL)
        self.assertEqual(density_for_mode('compact'), ActionDensity.COMPACT)
        self.assertEqual(density_for_mode('narrow'), ActionDensity.OVERFLOW)

    def test_editor_orientation(self):
        self.assertEqual(editor_orientation_for_mode('wide'), Qt.Orientation.Horizontal)
        self.assertEqual(editor_orientation_for_mode('compact'), Qt.Orientation.Vertical)

    def test_action_bar_shares_qaction(self):
        app = QApplication.instance() or QApplication([])
        bar = ResponsiveActionBar()
        hits = []
        act = QAction('导出', bar)
        act.triggered.connect(lambda: hits.append(1))
        bar.add_action(act, role='primary')
        overflow = QAction('帮助', bar)
        overflow.triggered.connect(lambda: hits.append(2))
        bar.add_action(overflow, role='secondary')
        bar.apply_density(ActionDensity.OVERFLOW)
        # primary not hidden; secondary in menu — same QAction
        # 父级未 show 时 isVisible 不可靠，用 isHidden
        self.assertFalse(bar._items[0].button.isHidden())
        self.assertTrue(bar._items[1].button.isHidden())
        self.assertIn(overflow, bar._more_menu.actions())
        overflow.trigger()
        self.assertEqual(hits, [2])


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class PanelLayoutModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dashboard_narrow_stacks_and_limits_tools(self):
        panel = DashboardPanel('zh')
        panel.apply_layout_mode('wide', False)
        self.assertEqual(panel._list_limit(), 5)
        panel.apply_layout_mode('narrow', True)
        self.assertEqual(panel._list_limit(), 3)
        self.assertTrue(panel.tools_more.isVisible() or panel.ops.isHidden())
        self.assertTrue(panel.subtitle.isHidden())

    def test_settings_theme_grid_columns(self):
        panel = SettingsPanel(DEFAULT_SETTINGS, 'zh')
        panel.apply_layout_mode('narrow')
        # 单列：night card at row 3
        item = panel.theme_grid.itemAtPosition(3, 0)
        self.assertIsNotNone(item)
        panel.apply_layout_mode('wide')
        item = panel.theme_grid.itemAtPosition(0, 1)
        self.assertIsNotNone(item)

    def test_gateway_and_format_layout_mode(self):
        g = GatewayDecodePanel('zh')
        g.apply_layout_mode('narrow')
        self.assertFalse(g.response_btn.isHidden())
        self.assertTrue(g.clear_btn.isHidden() or not g.more_btn.isHidden())
        f = FormatToolsPanel('zh')
        f.apply_layout_mode('compact', True)
        self.assertEqual(f._layout_mode, 'compact')

    def test_aurora_uses_theme_without_crash(self):
        host = DashboardPanel('zh')
        ap = AuroraProgress(host)
        ap.start_busy('测试')
        ap.set_progress(50, '半程')
        ap.finish('完成')
        ap.fail('失败')


if __name__ == '__main__':
    unittest.main()
