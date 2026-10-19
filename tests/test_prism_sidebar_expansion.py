import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
from config import DEFAULT_SETTINGS
from main_window import MainWindow
from ui.navigation_model import build_web_nav_model_data
from ui.prism_sidebar import PrismSidebar
from ui.responsive import NAV_ICON, nav_width_for_mode

app=QApplication.instance() or QApplication([])


class _Host(QWidget):
    layout_mode_changed=pyqtSignal(str,bool)
    _on_layout_mode=MainWindow._on_layout_mode
    _set_nav_collapsed=MainWindow._set_nav_collapsed
    _toggle_nav_collapse=MainWindow._toggle_nav_collapse

    def __init__(self,language='zh'):
        super().__init__()
        self.language=language
        self._layout_mode='compact'
        self._layout_controller=SimpleNamespace(low_height=False)
        self._nav_collapsed=False
        self._nav_force_expanded=False
        self._settings=dict(DEFAULT_SETTINGS)
        self._sidebar=QWidget(self)
        QVBoxLayout(self._sidebar)
        self._sidebar_stack=None
        self._prism_title_bar=QWidget(self)
        self._collapse_btn=QPushButton(self._prism_title_bar)
        self._collapse_btn.clicked.connect(self._toggle_nav_collapse)
        self.version_label=QLabel(self)
        self._prism_sidebar=PrismSidebar(build_web_nav_model_data(language=language), language=language, parent=self._sidebar)

    def _sync_prism_sidebar_index(self):
        self._prism_sidebar.set_current(0)


class SidebarExpansionTests(unittest.TestCase):
    def test_expand_button_flattens_all_pages_even_in_compact_mode(self):
        host=_Host()
        try:
            host._on_layout_mode('compact',False)
            self.assertEqual(host._sidebar.width(),NAV_ICON)
            self.assertEqual(host._collapse_btn.text(),'展开全部菜单')
            with patch('config.save_settings',side_effect=lambda data:dict(data)) as save:
                host._collapse_btn.click()
                self.assertFalse(host._nav_icon_only)
                self.assertFalse(host._prism_sidebar._icon_only)
                self.assertEqual(host._sidebar.width(),nav_width_for_mode('standard'))
                self.assertGreater(len(host._prism_sidebar._buttons),10)
                self.assertTrue(all(not button.isHidden() for button in host._prism_sidebar._buttons.values()))
                self.assertFalse(save.call_args.args[0]['sidebar_collapsed'])
                host._on_layout_mode('narrow',True)
                self.assertFalse(host._nav_icon_only)
                host._collapse_btn.click()
                self.assertTrue(host._nav_icon_only)
                self.assertTrue(save.call_args.args[0]['sidebar_collapsed'])
        finally:
            host.deleteLater()
            app.processEvents()

    def test_new_profiles_expand_and_menu_text_fits_both_languages(self):
        self.assertFalse(DEFAULT_SETTINGS['sidebar_collapsed'])
        for language in ('zh','en'):
            host=_Host(language)
            try:
                host._on_layout_mode('wide',False)
                self.assertFalse(host._nav_icon_only)
                self.assertEqual(host._collapse_btn.text(),'收起菜单' if language=='zh' else 'Collapse menus')
                host._on_layout_mode('compact',False)
                font=host._collapse_btn.font()
                font.setPixelSize(17)
                host._collapse_btn.setFont(font)
                host._collapse_btn.resize(host._collapse_btn.sizeHint().expandedTo(host._collapse_btn.minimumSizeHint()))
                self.assertGreaterEqual(host._collapse_btn.width(),host._collapse_btn.fontMetrics().horizontalAdvance(host._collapse_btn.text()))
            finally:
                host.deleteLater()
                app.processEvents()


if __name__=='__main__':
    unittest.main()
