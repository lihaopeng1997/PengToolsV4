"""Real native sidebar geometry with isolated settings and no desktop services."""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication, QScrollArea


class NativeSidebarGeometryTests(unittest.TestCase):
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
                original_buttons = list(window.nav_buttons)
                original_menu = window.user_chip.menu()
                for width, height, expected_width, collapsed in ((960, 640, 72, False), (1100, 720, 84, False),
                                                                 (1280, 800, 84, True), (1440, 900, 248, False)):
                    window._set_nav_collapsed(collapsed, persist=False)
                    window.resize(width, height)
                    window.show()
                    window._layout_controller.force(width, height)
                    app.processEvents()
                    self.assertEqual(window.main_shell_renderer, 'native')
                    self.assertEqual(window._sidebar.width(), expected_width)
                    scroll = window._sidebar.findChild(QScrollArea, 'sidebar-scroll')
                    viewport = scroll.viewport()
                    for index, button in enumerate(window.nav_buttons):
                        if button is None or button.isHidden() or index == 7:
                            continue
                        scroll.ensureWidgetVisible(button, 0, 0)
                        app.processEvents()
                        box = QRect(button.mapTo(viewport, QPoint()), button.size())
                        self.assertTrue(viewport.rect().contains(box), (width, index, box, viewport.rect()))
                        if window._nav_icon_only:
                            self.assertEqual((button.width(), button.height()), (44, 44))
                    settings_box = QRect(window.settings_button.mapTo(window._sidebar, QPoint()),
                                         window.settings_button.size())
                    self.assertTrue(window._sidebar.rect().contains(settings_box), (width, settings_box))
                    # The author unlock and real user menu are common title-bar
                    # controls, so they remain reachable while Web/native
                    # sidebar pages swap in the stack.
                    self.assertTrue(window.user_chip.isVisible())
                    self.assertTrue(window.version_label.isVisible() or window._nav_icon_only)
                    self.assertEqual(window.nav_buttons, original_buttons)
                    self.assertIs(window.user_chip.menu(), original_menu)
                # Prism native fallback uses the same escaped group popup as
                # the Web rail; it no longer maintains a hidden inline child
                # list with a second layout contract.
                self.assertIsNotNone(window._prism_sidebar._group_buttons.get('workspace:14'))
                self.assertIsNotNone(window._prism_sidebar._group_buttons.get('ai:15'))
            finally:
                window._force_exit = True
                window.close()
                window.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
