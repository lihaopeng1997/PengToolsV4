# -*- coding: utf-8 -*-
"""整体软化 P1：密度、对比度与主题保存事务回归。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget
    import config
    from config import DEFAULT_SETTINGS
    from panels.settings_panel import SettingsPanel
    from ui.field_metrics import size_compact_button
    from ui.theme_manager import THEMES, ThemeManager
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


def _channel(value: str) -> float:
    number = int(value, 16) / 255
    return number / 12.92 if number <= 0.04045 else ((number + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    value = color.lstrip('#')
    return (
        0.2126 * _channel(value[0:2])
        + 0.7152 * _channel(value[2:4])
        + 0.0722 * _channel(value[4:6])
    )


def contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class SoftThemeP1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        manager = ThemeManager.instance()
        manager.load_template(PROJECT_DIR)
        manager.apply(self.app, 'calm', font_size=12)

    def test_compact_action_keeps_28px_in_both_density_modes(self):
        window = QMainWindow()
        host = QWidget()
        layout = QVBoxLayout(host)
        button = QPushButton('刷新')
        size_compact_button(button)
        layout.addWidget(button)
        window.setCentralWidget(host)

        for density in ('compact', 'comfortable'):
            window.setProperty('uiDensity', density)
            window.style().unpolish(window)
            window.style().polish(window)
            button.style().unpolish(button)
            button.style().polish(button)
            size_compact_button(button)
            self.assertEqual(button.minimumHeight(), 28, density)
            self.assertEqual(button.maximumHeight(), 28, density)

    def test_all_soft_themes_meet_aa_for_key_text_pairs(self):
        for theme_id, palette in THEMES.items():
            pairs = (
                ('TEXT', 'SURFACE'),
                ('TEXT_MUTED', 'SURFACE'),
                ('PRIMARY', 'SURFACE'),
                ('ON_PRIMARY', 'PRIMARY'),
            )
            for foreground, background in pairs:
                ratio = contrast_ratio(palette[foreground], palette[background])
                self.assertGreaterEqual(
                    ratio,
                    4.5,
                    f'{theme_id}: {foreground}/{background} = {ratio:.2f}:1',
                )

    def test_settings_panel_defers_persistence_to_main_window(self):
        panel = SettingsPanel(DEFAULT_SETTINGS, 'zh')
        received = []
        panel.settings_changed.connect(received.append)
        with patch('panels.settings_panel.save_settings', side_effect=AssertionError('settings panel must not write directly')):
            panel._save()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['ui_theme'], 'calm')

    def test_settings_file_is_replaced_atomically(self):
        original = config.SETTINGS_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                config.SETTINGS_FILE = os.path.join(directory, 'settings.json')
                with patch.object(config.os, 'replace', wraps=os.replace) as replace:
                    saved = config.save_settings({'ui_theme': 'night'})
                self.assertEqual(saved['ui_theme'], 'black')
                self.assertTrue(replace.called)
                self.assertTrue(os.path.isfile(config.SETTINGS_FILE))
        finally:
            config.SETTINGS_FILE = original


if __name__ == '__main__':
    unittest.main()
