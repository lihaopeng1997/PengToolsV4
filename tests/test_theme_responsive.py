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
    from config import DEFAULT_SETTINGS, normalize_settings
    from panels.dashboard_panel import DashboardPanel
    from panels.format_panel import FormatToolsPanel
    from panels.gateway_panel import GatewayDecodePanel
    from panels.settings_panel import SettingsPanel, ThemePreviewWidget
    from ui.aurora_progress import AuroraProgress
    from ui import design_system
    from ui.responsive import (
        ActionDensity, ResponsiveActionBar, classify_layout, density_for_mode,
        editor_orientation_for_mode, is_low_height,
    )
    from ui import theme_manager
    from ui.theme_manager import THEMES, ThemeManager, theme_display_name, theme_subtitle
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class NightThemeTokenTests(unittest.TestCase):
    def test_night_display_name(self):
        self.assertEqual(theme_display_name('night', 'zh'), '夜间安读')
        self.assertTrue(
            '低眩光' in theme_subtitle('night', 'zh')
            or '石板' in theme_subtitle('night', 'zh')
        )

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

    def test_all_themes_have_design_system_tokens(self):
        required = (
            'CONTROL_HEIGHT_COMPACT', 'CONTROL_HEIGHT_COMFORTABLE',
            'ROW_HEIGHT_COMPACT', 'ROW_HEIGHT_COMFORTABLE', 'FOCUS_RING',
            'STATUS_INFO_BG', 'STATUS_SUCCESS_BG', 'STATUS_WARNING_BG',
            'STATUS_DANGER_BG',
        )
        for theme_id, palette in THEMES.items():
            self.assertEqual(
                theme_manager.missing_theme_tokens(palette, required), (), theme_id
            )

    def test_rendered_qss_has_no_unresolved_design_tokens(self):
        manager = ThemeManager.instance()
        manager.load_template()
        qss = manager.render('calm')
        self.assertEqual(theme_manager.unresolved_qss_tokens(qss), ())

    def test_qss_contains_global_component_contract(self):
        manager = ThemeManager.instance()
        manager.load_template()
        qss = manager.render('night')
        for selector in ('#page-title', '#page-context', '#status-banner', '#primary-btn', '#danger-btn'):
            self.assertIn(selector, qss)
        self.assertIn('QPushButton:focus', qss)
        self.assertNotIn('__CONTROL_HEIGHT_COMPACT__', qss)

    def test_headers_and_tabs_share_compact_style(self):
        from ui.layout_metrics import TAB_H, TABLE_HEADER_H

        self.assertLessEqual(TABLE_HEADER_H, 28)
        self.assertLessEqual(TAB_H, 28)
        manager = ThemeManager.instance()
        manager.load_template()
        qss = manager.render('calm')
        self.assertIn('QHeaderView::section', qss)
        self.assertIn('QTabBar::tab', qss)
        self.assertIn('padding: 3px 8px', qss)
        self.assertIn('padding: 3px 10px', qss)
        self.assertNotIn('padding: 11px 10px', qss)
        self.assertNotIn('padding: 10px 18px', qss)

    def _avg_luma(self, hex_color: str) -> float:
        hexv = hex_color.upper().lstrip('#')
        r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
        return (r + g + b) / 3

    def test_night_surfaces_not_white(self):
        pal = THEMES['night']
        # 最深底
        self.assertLess(self._avg_luma(pal['APP_BG']), 40)
        for key in (
            'APP_BG', 'SIDEBAR_BG', 'SURFACE', 'SURFACE_SOFT', 'ELEVATED_SURFACE',
            'CODE_BG', 'INPUT_BG', 'TABLE_ALT', 'DANGER_BG', 'SUCCESS_BG',
        ):
            val = pal[key].upper()
            self.assertNotIn(val, ('#FFFFFF', '#FFF', 'WHITE'), msg=key)
            self.assertLess(self._avg_luma(val), 80, msg=f'{key} too bright for night')

    def test_night_hierarchy_and_selection_contrast(self):
        """夜间主题：层次递进 + 选中底不能比正文更亮导致看不清。"""
        pal = THEMES['night']
        app_l = self._avg_luma(pal['APP_BG'])
        side_l = self._avg_luma(pal['SIDEBAR_BG'])
        surf_l = self._avg_luma(pal['SURFACE'])
        elev_l = self._avg_luma(pal.get('ELEVATED_SURFACE', pal['SURFACE']))
        code_l = self._avg_luma(pal['CODE_BG'])
        self.assertLessEqual(app_l, side_l + 2)
        self.assertLessEqual(side_l, surf_l + 2)
        self.assertLessEqual(surf_l, elev_l + 8)
        self.assertLessEqual(code_l, surf_l + 2)
        # 选中底应偏暗，亮字才可读
        select_l = self._avg_luma(pal['TABLE_SELECT'])
        text_l = self._avg_luma(pal['TEXT_STRONG'])
        self.assertLess(select_l, 90, msg='TABLE_SELECT too bright for dark theme')
        self.assertGreater(text_l, 180, msg='TEXT_STRONG too dim')
        # 正文层级：STRONG > TEXT > MUTED
        self.assertGreater(self._avg_luma(pal['TEXT_STRONG']), self._avg_luma(pal['TEXT']))
        self.assertGreater(self._avg_luma(pal['TEXT']), self._avg_luma(pal['TEXT_MUTED']))

    def test_render_night_qss_has_tokens_applied(self):
        tm = ThemeManager.instance()
        tm.load_template()
        qss = tm.render('night')
        self.assertIn(THEMES['night']['APP_BG'], qss)
        self.assertIn(THEMES['night']['SURFACE'], qss)
        self.assertNotIn('color: white;', qss.lower().replace(' ', ''))

    def test_list_selection_uses_theme_soft_fill_not_system_blue(self):
        tm = ThemeManager.instance()
        tm.load_template()
        for theme_id in ('calm', 'clear', 'warm', 'night'):
            qss = tm.render(theme_id)
            pal = THEMES[theme_id]
            self.assertIn(pal['TABLE_SELECT'], qss)
            self.assertNotIn('__BRANCH_CLOSED__', qss)
            self.assertNotIn('__BRANCH_OPEN__', qss)
            self.assertIn('QTreeWidget#ops-command-list::item:selected', qss)

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
class DesignSystemSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_normalize_settings_defaults_global_density_preferences(self):
        settings = normalize_settings({'ui_density': 'unknown', 'sidebar_collapsed': 'yes'})
        self.assertEqual(settings['ui_density'], 'compact')
        self.assertTrue(settings['sidebar_collapsed'])
        self.assertFalse(normalize_settings({'sidebar_collapsed': 'false'})['sidebar_collapsed'])

    def test_density_metrics_are_stable(self):
        self.assertEqual(design_system.density_metrics('compact').control_height, 32)
        self.assertEqual(design_system.density_metrics('comfortable').control_height, 36)
        self.assertEqual(design_system.density_metrics('compact').row_height, 32)
        self.assertEqual(design_system.density_metrics('comfortable').row_height, 40)

    def test_compact_button_uses_28_pixel_height(self):
        from PyQt6.QtWidgets import QPushButton
        from ui.field_metrics import size_compact_button

        button = QPushButton('刷新')
        size_compact_button(button)

        self.assertEqual(button.height(), 28)
        self.assertEqual(button.minimumHeight(), 28)
        self.assertEqual(button.property('compactAction'), True)


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
class MainWindowDesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_stack_mapping_is_unchanged(self):
        from main_window import MainWindow

        self.assertEqual(MainWindow._stack_index_for_nav(8), 8)
        self.assertEqual(MainWindow._stack_index_for_nav(9), 8)
        self.assertEqual(MainWindow._stack_index_for_nav(10), 9)
        self.assertEqual(MainWindow._stack_index_for_nav(11), 10)
        self.assertEqual(MainWindow._stack_index_for_nav(12), 11)
        self.assertEqual(MainWindow._stack_index_for_nav(13), 12)

    def test_apply_density_sets_window_property(self):
        from main_window import MainWindow

        window = MainWindow()
        window._apply_settings({**DEFAULT_SETTINGS, 'ui_density': 'comfortable'})
        self.assertEqual(window.property('uiDensity'), 'comfortable')

    def test_runtime_settings_do_not_reset_manual_sidebar_state(self):
        from main_window import MainWindow

        window = MainWindow()
        window._set_nav_collapsed(True)
        window._set_nav_collapsed(False)
        window._apply_settings({**DEFAULT_SETTINGS, 'sidebar_collapsed': True})
        self.assertFalse(window._nav_collapsed)


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class PageChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_chrome_exposes_context_and_primary_action_slots(self):
        from ui import page_chrome

        chrome = page_chrome.PageChrome('接口排查', '当前环境：模拟环境')
        self.assertEqual(chrome.title_label.text(), '接口排查')
        self.assertEqual(chrome.context_label.text(), '当前环境：模拟环境')
        self.assertIsNotNone(chrome.primary_actions)
        self.assertIsNotNone(chrome.secondary_actions)


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

    def test_settings_exposes_density_preference(self):
        panel = SettingsPanel({**DEFAULT_SETTINGS, 'ui_density': 'comfortable'}, 'zh')
        self.assertEqual(panel.density_combo.currentData(), 'comfortable')
        self.assertFalse(panel.sidebar_collapsed_check.isChecked())

    def test_settings_values_preserve_layout_preferences(self):
        panel = SettingsPanel(DEFAULT_SETTINGS, 'zh')
        panel.density_combo.setCurrentIndex(panel.density_combo.findData('comfortable'))
        panel.sidebar_collapsed_check.setChecked(True)
        values = panel.values()
        self.assertEqual(values['ui_density'], 'comfortable')
        self.assertTrue(values['sidebar_collapsed'])

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
