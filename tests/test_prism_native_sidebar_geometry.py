"""Real native sidebar geometry with isolated settings and no desktop services."""
import unittest
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication, QScrollArea


class NativeSidebarGeometryTests(unittest.TestCase):
    @staticmethod
    def _current_sidebar_controls(sidebar):
        controls = list(sidebar._buttons.values()) + list(sidebar._group_buttons.values())
        controls.extend(button for button in (sidebar._settings_button, sidebar._palette_button)
                        if button is not None)
        return controls

    def _assert_current_index(self, window, width):
        sidebar = window._prism_sidebar
        current_ids = {id(button) for button in self._current_sidebar_controls(sidebar)}
        for index, button in enumerate(window.nav_buttons):
            if button is None:
                continue
            self.assertIn(id(button), current_ids, (width, index, button.objectName()))
            self.assertTrue(sidebar.isAncestorOf(button), (width, index))
        self.assertIs(window.settings_button, sidebar._settings_button)
        self.assertIs(window.nav_buttons[7], sidebar._settings_button)

    def _assert_sidebar_geometry(self, window, app, width, expected_width, collapsed):
        sidebar = window._prism_sidebar
        self.assertEqual(window.main_shell_renderer, 'native')
        self.assertEqual(window._sidebar.width(), expected_width)
        self.assertEqual(sidebar.width(), expected_width)
        self.assertEqual(window._nav_icon_only, collapsed or width < 1280)
        self._assert_current_index(window, width)

        group_leaf_ids = {
            int(item['i'])
            for group in sidebar.nav_model.get('groups', [])
            for item in group.get('items', [])
        }
        if collapsed or width < 1280:
            self.assertTrue(sidebar._group_buttons, (width, 'expected type categories'))
            self.assertFalse(group_leaf_ids & sidebar._buttons.keys(),
                             (width, 'collapsed sidebar retained group leaves'))
            scroll = sidebar.findChild(QScrollArea, 'sidebar-scroll')
            viewport = scroll.viewport()
            navigable = [button for button in sidebar._buttons.values()
                         if button is not sidebar._settings_button]
            navigable.extend(sidebar._group_buttons.values())
        else:
            self.assertFalse(sidebar._group_buttons, (width, 'expanded sidebar retained categories'))
            self.assertTrue(group_leaf_ids <= sidebar._buttons.keys(),
                            (width, 'expanded sidebar is missing direct leaves'))
            scroll = sidebar.findChild(QScrollArea, 'sidebar-scroll')
            viewport = scroll.viewport()
            navigable = [button for button in sidebar._buttons.values()
                         if button is not sidebar._settings_button]

        self.assertIsNotNone(scroll)
        for button in navigable:
            scroll.ensureWidgetVisible(button, 0, 0)
            app.processEvents()
            self.assertTrue(button.isVisible(), (width, button.objectName(), button.text()))
            box = QRect(button.mapTo(viewport, QPoint()), button.size())
            self.assertTrue(viewport.rect().contains(box), (width, box, viewport.rect()))
            self.assertEqual(button.iconSize(), QSize(20, 20),
                             (width, button.objectName(), button.property('navIndex'), button.iconSize()))
            self.assertFalse(button.icon().isNull(), (width, button.objectName()))
            if window._nav_icon_only:
                self.assertEqual((button.width(), button.height()), (44, 44), (width, box))

        footer = sidebar._footer_layout.geometry()
        for button in (sidebar._settings_button, sidebar._palette_button):
            self.assertIsNotNone(button)
            self.assertTrue(button.isVisible(), (width, button.objectName()))
            box = QRect(button.mapTo(sidebar, QPoint()), button.size())
            self.assertTrue(sidebar.rect().contains(box), (width, box, sidebar.rect()))
            self.assertTrue(footer.contains(box), (width, box, footer))
            if button is sidebar._palette_button:
                self.assertEqual(button.iconSize(), QSize(20, 20), (width, button.iconSize()))
                self.assertFalse(button.icon().isNull(), (width, button.objectName()))
            if window._nav_icon_only:
                self.assertEqual((button.width(), button.height()), (44, 44), (width, box))

    def test_compact_navigation_and_footer_remain_fully_reachable(self):
        import config
        from main_window import MainWindow
        from ui.theme_manager import ThemeManager
        app = QApplication.instance() or QApplication(['native-sidebar-test'])
        ThemeManager.instance().apply(app, 'calm', font_size=16)
        settings = dict(config.DEFAULT_SETTINGS, ui_web_shell=True, floating_enabled=False,
                        private_unlocked=True, sidebar_collapsed=False, font_size=16)
        with patch('main_window.load_settings', return_value=settings), \
                patch.object(MainWindow, '_ensure_services'), \
                patch('ui.web_shell.runtime_web_shell_available', return_value=False):
            window = MainWindow()
            try:
                original_menu = window.user_chip.menu()
                cases = (
                    (960, 640, 72, False),
                    (1100, 720, 84, False),
                    (1280, 800, 84, True),
                    (1440, 900, 248, False),
                    (1280, 800, 84, True),
                    (1440, 900, 248, False),
                )
                for width, height, expected_width, collapsed in cases:
                    old_controls = self._current_sidebar_controls(window._prism_sidebar)
                    window._set_nav_collapsed(collapsed, persist=False)
                    window.resize(width, height)
                    window.show()
                    window._layout_controller.force(width, height)
                    app.processEvents()
                    self._assert_sidebar_geometry(window, app, width, expected_width, collapsed)
                    # 标题栏右侧只留搜索；头像菜单内容已并入 文件/视图/帮助 菜单
                    self.assertFalse(window.user_chip.isVisible())
                    self.assertTrue(window.user_chip.property('titlebarHidden'))
                    # 版本号已移入用户菜单，标题栏不再常驻显示
                    self.assertFalse(window.version_label.isVisible())
                    self.assertTrue(window.version_label.property('titlebarHidden'))
                    self.assertIs(window.user_chip.menu(), original_menu)
                    current_ids = {id(button) for button in self._current_sidebar_controls(window._prism_sidebar)}
                    stale_controls = [button for button in old_controls if id(button) not in current_ids]
                    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                    self._assert_current_index(window, width)
                    self.assertTrue(all(sip.isdeleted(button) for button in stale_controls),
                                    (width, 'retired sidebar controls remain alive'))
            finally:
                window._force_exit = True
                window.close()
                window.deleteLater()
                QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
