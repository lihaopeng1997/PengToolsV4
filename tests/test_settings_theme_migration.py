# -*- coding: utf-8 -*-
"""Dual-theme settings migration and fail-safe settings_version regression tests."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config


class SettingsThemeMigrationTests(unittest.TestCase):
    """验证 config.load_settings() 双主题 v1->v2 迁移、版本容错及其他字段保留。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_settings_file = os.path.join(self.temp_dir.name, 'settings.json')
        self.orig_settings_file = config.SETTINGS_FILE
        config.SETTINGS_FILE = self.fake_settings_file

    def tearDown(self):
        config.SETTINGS_FILE = self.orig_settings_file
        self.temp_dir.cleanup()

    def _write_settings(self, data: dict):
        with open(self.fake_settings_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_disk_settings(self) -> dict:
        with open(self.fake_settings_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def test_coerce_settings_version_helper(self):
        """测试 _coerce_settings_version 对异常类型的防护。"""
        self.assertEqual(config._coerce_settings_version(None), 0)
        self.assertEqual(config._coerce_settings_version(''), 0)
        self.assertEqual(config._coerce_settings_version('broken'), 0)
        self.assertEqual(config._coerce_settings_version({}), 0)
        self.assertEqual(config._coerce_settings_version([]), 0)
        self.assertEqual(config._coerce_settings_version(-1), 0)
        self.assertEqual(config._coerce_settings_version(1), 1)
        self.assertEqual(config._coerce_settings_version('2'), 2)
        self.assertEqual(config._coerce_settings_version(float('inf')), 0)
        self.assertEqual(config._coerce_settings_version(float('-inf')), 0)
        self.assertEqual(config._coerce_settings_version(float('nan')), 0)
        self.assertEqual(config._coerce_settings_version(1e309), 0)
        self.assertEqual(config._coerce_settings_version(-1e309), 0)

    def test_migration_clear_to_calm(self):
        """A. settings_version=1, ui_theme=clear -> calm, version=2, disk rewritten."""
        self._write_settings({'settings_version': 1, 'ui_theme': 'clear'})
        loaded = config.load_settings()
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_warm_to_calm(self):
        """B. version=1, warm -> calm + disk v2."""
        self._write_settings({'settings_version': 1, 'ui_theme': 'warm'})
        loaded = config.load_settings()
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_night_to_calm(self):
        """C. version=1, night -> calm + disk v2."""
        self._write_settings({'settings_version': 1, 'ui_theme': 'night'})
        loaded = config.load_settings()
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_light_alias_to_calm(self):
        """D. version=1, light -> calm."""
        self._write_settings({'settings_version': 1, 'ui_theme': 'light'})
        loaded = config.load_settings()
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_dark_alias_to_calm(self):
        """E. version=1, dark -> calm."""
        self._write_settings({'settings_version': 1, 'ui_theme': 'dark'})
        loaded = config.load_settings()
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_malformed_settings_version(self):
        """F. malformed version 'broken' + clear -> no exception, canonical calm, version 2."""
        for malformed in ('broken', {}, [], None):
            with self.subTest(malformed=malformed):
                self._write_settings({'settings_version': malformed, 'ui_theme': 'clear'})
                loaded = config.load_settings()
                self.assertEqual(loaded['ui_theme'], 'calm')
                self.assertEqual(loaded['settings_version'], 2)

                disk = self._read_disk_settings()
                self.assertEqual(disk['ui_theme'], 'calm')
                self.assertEqual(disk['settings_version'], 2)

    def test_migration_overflow_settings_version(self):
        """测试 JSON 中极大数值（如 1e309, -1e309）解析为 inf 时的 OverflowError 容错与迁移。"""
        raw_cases = [
            '{"settings_version": 1e309, "ui_theme": "clear"}',
            '{"settings_version": -1e309, "ui_theme": "clear"}',
        ]
        for raw_json in raw_cases:
            with self.subTest(raw_json=raw_json):
                with open(self.fake_settings_file, 'w', encoding='utf-8') as f:
                    f.write(raw_json)
                loaded = config.load_settings()
                self.assertEqual(loaded['ui_theme'], 'calm')
                self.assertEqual(loaded['settings_version'], 2)

                disk = self._read_disk_settings()
                self.assertEqual(disk['ui_theme'], 'calm')
                self.assertEqual(disk['settings_version'], 2)

    def test_migration_preserves_version0_web_shell_migration(self):
        """G. version 0 + ui_web_shell false: 验证原 v1 migration 没有被 v2 迁移破坏。"""
        self._write_settings({'ui_web_shell': False, 'ui_theme': 'clear'})
        loaded = config.load_settings()
        self.assertTrue(loaded['ui_web_shell'])
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)

        disk = self._read_disk_settings()
        self.assertTrue(disk['ui_web_shell'])
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)

    def test_migration_preserves_other_settings(self):
        """3. migration preservation: 迁移不能丢失其它配置字段。"""
        initial = {
            'font_size': 15,
            'default_language': 'en',
            'ui_theme': 'warm',
            'settings_version': 1,
            'keep_awake_interval_minutes': 25,
            'close_default_action': 'exit',
        }
        self._write_settings(initial)
        loaded = config.load_settings()
        self.assertEqual(loaded['font_size'], 15)
        self.assertEqual(loaded['default_language'], 'en')
        self.assertEqual(loaded['ui_theme'], 'calm')
        self.assertEqual(loaded['settings_version'], 2)
        self.assertEqual(loaded['keep_awake_interval_minutes'], 25)
        self.assertEqual(loaded['close_default_action'], 'exit')

        disk = self._read_disk_settings()
        self.assertEqual(disk['font_size'], 15)
        self.assertEqual(disk['default_language'], 'en')
        self.assertEqual(disk['ui_theme'], 'calm')
        self.assertEqual(disk['settings_version'], 2)
        self.assertEqual(disk['keep_awake_interval_minutes'], 25)
        self.assertEqual(disk['close_default_action'], 'exit')

    def test_normalize_settings_always_canonical_theme_and_v2(self):
        """normalize_settings 永远返回 canonical theme ('calm') 且 settings_version >= 2。"""
        legacy_cases = [
            ('clear', 'calm'),
            ('warm', 'calm'),
            ('light', 'calm'),
            ('unknown_foo', 'calm'),
            ('calm', 'calm'),
            ('night', 'calm'),
            ('dark', 'calm'),
            ('black', 'calm'),
        ]
        for input_theme, expected in legacy_cases:
            with self.subTest(input_theme=input_theme):
                norm = config.normalize_settings({
                    'ui_theme': input_theme,
                    'settings_version': 'broken',
                })
                self.assertEqual(norm['ui_theme'], expected)
                self.assertGreaterEqual(norm['settings_version'], 2)


if __name__ == '__main__':
    unittest.main()
