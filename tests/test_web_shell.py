# -*- coding: utf-8 -*-
"""V2 Web 壳定向测试：桥接/配置默认/导航白名单/首页数据（offscreen 安全，不实例化视图）。"""
import json
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS, load_settings, normalize_settings
from ui import web_shell

_app = QApplication.instance() or QApplication([])


class WebShellAvailabilityTest(unittest.TestCase):
    def test_availability_flag_is_bool(self):
        self.assertIsInstance(web_shell.WEB_SHELL_AVAILABLE, bool)

    def test_dependency_present_in_dev_env(self):
        # 本机已安装 PyQt6-WebEngine；缺失时提示安装而不是静默降级
        self.assertTrue(web_shell.WEB_SHELL_AVAILABLE,
                        'PyQt6-WebEngine 未安装：pip install PyQt6-WebEngine==6.11.0')


class LocalNavigationWhitelistTest(unittest.TestCase):
    def test_local_schemes_allowed(self):
        for url in ('file:///C:/app/resources/webui/chrome.html', 'qrc:/webui/x.html',
                    'about:blank', 'data:text/html,hi'):
            self.assertTrue(web_shell.is_allowed_navigation(QUrl(url)), url)

    def test_remote_blocked(self):
        for url in ('https://example.com/x.html', 'http://192.168.1.5/',
                    'ftp://host/file', 'ws://host/'):
            self.assertFalse(web_shell.is_allowed_navigation(QUrl(url)), url)


class HomeBridgeTest(unittest.TestCase):
    def setUp(self):
        self.bridge = web_shell.HomeBridge()

    def test_nav_model_roundtrip(self):
        model = {'groups': [{'key': 'workspace', 'zh': '工作台', 'en': 'WORKSPACE',
                             'items': [{'i': 0, 'zh': '首页', 'en': 'HOME', 'icon': 'home'}]}],
                 'settings': {'i': 7, 'zh': '设置', 'en': 'SET', 'icon': 'gear'},
                 'current': 0}
        self.bridge.set_nav_model(model)
        loaded = json.loads(self.bridge.navModel())
        self.assertEqual(loaded['groups'][0]['items'][0]['i'], 0)
        self.assertEqual(loaded['settings']['i'], 7)

    def test_summary_provider_called(self):
        self.bridge.set_summary_provider(lambda: {'username': 'tester', 'recent': [1, 2]})
        data = json.loads(self.bridge.dashboardSummary())
        self.assertEqual(data['username'], 'tester')

    def test_summary_provider_error_is_safe(self):
        def boom():
            raise RuntimeError('x')
        self.bridge.set_summary_provider(boom)
        data = json.loads(self.bridge.dashboardSummary())
        self.assertIsInstance(data, dict)

    def test_username_default(self):
        self.bridge.set_username('')
        self.assertEqual(self.bridge.homeUsername(), 'Lihp')

    def test_navigate_signal(self):
        seen = []
        self.bridge.navigateRequested.connect(seen.append)
        self.bridge.navigate(14)
        self.assertEqual(seen, [14])

    def test_push_active_signal(self):
        seen = []
        self.bridge.activeChanged.connect(seen.append)
        self.bridge.push_active(0)
        self.assertEqual(seen, [0])


class SettingsDefaultsTest(unittest.TestCase):
    def test_home_username_default(self):
        self.assertEqual(DEFAULT_SETTINGS['home_username'], 'Lihp')
        self.assertTrue(DEFAULT_SETTINGS['ui_web_shell'])

    def test_normalize_username(self):
        normalized = normalize_settings({'home_username': '  小彭  '})
        self.assertEqual(normalized['home_username'], '小彭')
        normalized_empty = normalize_settings({'home_username': '   '})
        self.assertEqual(normalized_empty['home_username'], 'Lihp')

    def test_normalize_web_shell_flag(self):
        self.assertTrue(normalize_settings({'ui_web_shell': 'true'})['ui_web_shell'])
        self.assertFalse(normalize_settings({'ui_web_shell': 'off'})['ui_web_shell'])
        self.assertFalse(normalize_settings({'ui_web_shell': 0})['ui_web_shell'])

    def test_load_settings_roundtrip(self):
        settings = load_settings()
        self.assertIn('home_username', settings)
        self.assertIn('ui_web_shell', settings)


if __name__ == '__main__':
    unittest.main()
