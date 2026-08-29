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


# ==================== Step 2A：诊断/握手/回退 ====================

import io
import re

from ui.web_shell import WebHealthTracker

_MAIN_WINDOW_SRC = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'main_window.py'), encoding='utf-8').read()


def _function_source(name):
    m = re.search(r'    def ' + name + r'\(.*?(?=\n    def |\Z)', _MAIN_WINDOW_SRC, re.S)
    return m.group(0) if m else ''


class PageReadyHandshakeTest(unittest.TestCase):
    def setUp(self):
        self.bridge = web_shell.HomeBridge()
        self.received = []
        self.bridge.pageReadyReceived.connect(self.received.append)

    def test_page_ready_accepts_known_pages(self):
        self.bridge.pageReady('chrome')
        self.bridge.pageReady('dashboard')
        self.assertEqual(self.received, ['chrome', 'dashboard'])

    def test_page_ready_rejects_unknown_pages(self):
        for bad in ('evil', '', 'admin', 'chrome;drop'):
            self.bridge.pageReady(bad)
        self.assertEqual(self.received, [], '非法 page_name 必须被忽略')


class WebHealthTrackerTest(unittest.TestCase):
    def test_all_ready_only_when_both_pages_ready(self):
        t = WebHealthTracker(expected=('chrome', 'dashboard'))
        self.assertFalse(t.is_ready())
        t.mark_ready('chrome')
        self.assertFalse(t.is_ready())
        t.mark_ready('dashboard')
        self.assertTrue(t.is_ready())

    def test_missing_pages_reports_pending(self):
        t = WebHealthTracker(expected=('chrome', 'dashboard'))
        t.mark_ready('dashboard')
        self.assertEqual(t.missing_pages(), {'chrome'})

    def test_mark_failed_removes_ready_and_records_reason(self):
        t = WebHealthTracker(expected=('chrome', 'dashboard'))
        t.mark_ready('chrome')
        t.mark_failed('chrome', 'load_failed')
        self.assertNotIn('chrome', t.ready_pages)
        self.assertEqual(t.failed_pages.get('chrome'), 'load_failed')

    def test_unknown_page_ignored(self):
        t = WebHealthTracker(expected=('chrome', 'dashboard'))
        t.mark_ready('evil')
        t.mark_failed('evil', 'x')
        self.assertEqual(t.ready_pages, set())
        self.assertEqual(t.failed_pages, {})


class FallbackBehaviourSourceTest(unittest.TestCase):
    """源码级守护：回退幂等、不动持久配置、显式 sidebar stack、holder 保留。"""

    def test_runtime_failure_handlers_exist(self):
        self.assertIn('def _disable_web_shell_live(self, reason=', _MAIN_WINDOW_SRC)
        self.assertIn('def _on_web_render_terminated(self, page_name, status, exit_code)',
                      _MAIN_WINDOW_SRC)
        self.assertIn('def _on_web_load_finished(self, page_name, ok)', _MAIN_WINDOW_SRC)

    def test_disable_is_idempotent(self):
        src = _function_source('_disable_web_shell_live')
        self.assertIn("if not getattr(self, '_web_shell_enabled', False):", src)
        self.assertIn('return', src)

    def test_fallback_does_not_persist_settings(self):
        src = _function_source('_disable_web_shell_live')
        self.assertNotIn('save_settings', src)
        self.assertNotIn('ui_web_shell', src)

    def test_sidebar_stack_explicit(self):
        self.assertIn('self._sidebar_stack = side_stack', _MAIN_WINDOW_SRC)
        fb = _function_source('_disable_web_shell_live')
        self.assertIn('self._sidebar_stack.setCurrentIndex(0)', fb)
        self.assertNotIn('self._sidebar.parent()', _MAIN_WINDOW_SRC)

    def test_dashboard_holder_fallback_kept(self):
        fb = _function_source('_disable_web_shell_live')
        self.assertIn("getattr(self, '_dash_holder', None)", fb)
        self.assertIn('setCurrentIndex(1)', fb)

    def test_timeout_wiring(self):
        self.assertIn('self._web_timeout_timer.start(10000)', _MAIN_WINDOW_SRC)
        self.assertIn('def _on_web_shell_timeout(self):', _MAIN_WINDOW_SRC)
        fb_src = _function_source('_disable_web_shell_live')
        self.assertIn('self._web_timeout_timer.stop()', fb_src)


if __name__ == '__main__':
    unittest.main()
