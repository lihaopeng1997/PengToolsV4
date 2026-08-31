# -*- coding: utf-8 -*-
"""V2 Web 壳定向测试：桥接/配置默认/导航白名单/首页数据（offscreen 安全，不实例化视图）。"""
import json
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # 直接 `python tests/test_web_shell.py` 时保证能导入根目录模块

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
    """Case A-F：loaded 与 bridge_ready 双四条件健康判定。"""

    def _tracker(self):
        return WebHealthTracker(expected=('chrome', 'dashboard'))

    def test_case_a_load_then_bridge_all_ready(self):
        t = self._tracker()
        t.mark_loaded('chrome', True); t.mark_loaded('dashboard', True)
        t.mark_bridge_ready('chrome'); t.mark_bridge_ready('dashboard')
        self.assertTrue(t.is_ready())

    def test_case_b_bridge_ready_without_load_not_ready(self):
        t = self._tracker()
        t.mark_bridge_ready('chrome'); t.mark_bridge_ready('dashboard')
        self.assertFalse(t.is_ready())

    def test_case_c_bridge_first_then_load_ready_last(self):
        t = self._tracker()
        t.mark_bridge_ready('chrome'); t.mark_bridge_ready('dashboard')
        self.assertFalse(t.is_ready())
        t.mark_loaded('chrome', True)
        self.assertFalse(t.is_ready())
        t.mark_loaded('dashboard', True)
        self.assertTrue(t.is_ready())

    def test_case_d_load_all_but_bridge_missing_not_ready(self):
        t = self._tracker()
        t.mark_loaded('chrome', True); t.mark_loaded('dashboard', True)
        t.mark_bridge_ready('chrome')
        self.assertFalse(t.is_ready())
        self.assertEqual(t.missing_bridge_pages(), {'dashboard'})

    def test_case_e_load_false_marks_failed_not_ready(self):
        t = self._tracker()
        t.mark_bridge_ready('dashboard')
        t.mark_loaded('dashboard', False)   # load 失败
        self.assertTrue(t.failed_pages)
        self.assertFalse(t.is_ready())

    def test_case_f_repeated_marks_idempotent(self):
        t = self._tracker()
        for _ in range(3):
            t.mark_loaded('chrome', True)
            t.mark_bridge_ready('chrome')
        t.mark_loaded('dashboard', True)
        for _ in range(3):
            t.mark_bridge_ready('dashboard')
        self.assertTrue(t.is_ready())
        self.assertEqual(t.loaded_pages, {'chrome', 'dashboard'})

    def test_timeout_missing_split(self):
        t = self._tracker()
        t.mark_loaded('chrome', True)
        t.mark_bridge_ready('dashboard')
        self.assertEqual(t.missing_loaded_pages(), {'dashboard'})
        self.assertEqual(t.missing_bridge_pages(), {'chrome'})

    def test_load_false_then_recovered(self):
        t = self._tracker()
        t.mark_loaded('dashboard', False)
        t.mark_loaded('dashboard', True)
        t.mark_bridge_ready('dashboard')
        t.mark_loaded('chrome', True)
        t.mark_bridge_ready('chrome')
        self.assertTrue(t.is_ready())


class RuntimeAvailabilityTest(unittest.TestCase):
    def tearDown(self):
        import ui.web_shell as ws
        ws.WEBENGINE_RUNTIME_FAILED = False

    def test_runtime_default_available(self):
        import ui.web_shell as ws
        self.assertTrue(ws.runtime_web_shell_available())

    def test_runtime_failed_blocks_availability(self):
        import ui.web_shell as ws
        ws.mark_webengine_runtime_failed()
        self.assertTrue(ws.WEB_SHELL_AVAILABLE)          # 模块仍可导入
        self.assertFalse(ws.runtime_web_shell_available())  # 运行态不可用

    def test_main_window_uses_runtime_not_import_flag(self):
        self.assertIn('_web_shell.runtime_web_shell_available()', _MAIN_WINDOW_SRC)
        self.assertNotIn(
            "self._web_shell_enabled = WEB_SHELL_AVAILABLE and bool(self._settings.get('ui_web_shell', True))",
            _MAIN_WINDOW_SRC)


class JsHandshakeOrderTest(unittest.TestCase):
    def _read(self, name):
        return io.open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'resources', 'webui', name), encoding='utf-8').read()

    def test_chrome_page_ready_after_init(self):
        src = self._read('chrome.html')
        init = src.find('render(JSON.parse(bridge.navModel()))')
        ready = src.find("bridge.pageReady('chrome')")
        self.assertGreater(ready, init > 0 and init or -1, "pageReady 必须在 render 初始化之后")
        self.assertGreater(src.find('} catch (e) {'), init, '初始化应有 try/catch')

    def test_dashboard_page_ready_after_render(self):
        # 验证 Vue 生产 Dashboard main.ts 启动时序
        dash_main = io.open(os.path.join(
            ROOT, 'frontend', 'src', 'dashboard', 'main.ts'), encoding='utf-8').read()
        mount_idx = dash_main.find("app.mount('#app')")
        connect_idx = dash_main.find('connectBridge()')
        summary_idx = dash_main.find('bridge.dashboardSummary()')
        parse_idx = dash_main.find('JSON.parse(')
        next_tick_idx = dash_main.find('nextTick()')
        ready_idx = dash_main.find("bridge.pageReady('dashboard')")
        catch_idx = dash_main.find('.catch(')

        self.assertGreater(mount_idx, 0, 'Vue app 必须先 mount')
        self.assertGreater(connect_idx, mount_idx, 'connectBridge 必须在 mount 后调用')
        self.assertGreater(summary_idx, connect_idx, 'dashboardSummary 必须在 bridge 连接后调用')
        self.assertGreater(parse_idx, summary_idx, 'JSON.parse 必须在 summary 返回后执行')
        self.assertGreater(next_tick_idx, parse_idx, 'nextTick 必须在 parse/state 应用之后')
        self.assertGreater(ready_idx, next_tick_idx, 'pageReady 必须在 nextTick 渲染就绪之后')
        self.assertGreater(catch_idx, ready_idx, '异常捕获 catch 必须存在')
        # 异常分支不得调用 pageReady
        catch_body = dash_main[catch_idx:]
        self.assertNotIn("pageReady('dashboard')", catch_body, 'catch 分支不得发送 pageReady')


class ReadyAnnounceGuardTest(unittest.TestCase):
    """Step 2A.1：web_shell_ready 只 announce 一次（重复事件不重复记录）。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'main_window.py')
        with open(path, encoding='utf-8') as fh:
            cls.main_src = fh.read()

    def _function_source(self, name):
        marker = '    def ' + name + '(self'
        start = self.main_src.find(marker)
        self.assertGreater(start, 0, f'{name} 未找到')
        end = self.main_src.find('\n    def ', start + 1)
        end = len(self.main_src) if end < 0 else end
        return self.main_src[start:end]

    def test_check_guard_exists(self):
        body = self._function_source('_check_web_shell_ready')
        self.assertIn('_web_shell_ready_announced', body)
        self.assertIn('self._web_shell_ready_announced = True', body)
        self.assertIn('return', body)   # 已 fallback / 未满足 / 已 announce 三重早退

    def test_both_entries_route_to_check(self):
        for handler in ('_on_web_page_ready', '_on_web_load_finished'):
            body = self._function_source(handler)
            self.assertIsNotNone(body, handler)
            self.assertIn('self._check_web_shell_ready()', body,
                          f'{handler} 必须经统一 _check_web_shell_ready')


def read_main_window():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'main_window.py')
    with open(path, encoding='utf-8') as fh:
        return fh.read()




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




class SandboxPolicyTest(unittest.TestCase):
    """Step 2B：应用不再主动关闭 Chromium sandbox（onedir 迁移后 workaround 移除）。"""

    def setUp(self):
        self.run_src = io.open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'run.py'), encoding='utf-8').read()

    def test_run_py_no_forced_sandbox_disable(self):
        # 允许只读记录（诊断字段）；禁止任何写入/设置行为
        self.assertNotIn("os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX'", self.run_src)
        self.assertNotIn("os.environ['QTWEBENGINE_DISABLE_SANDBOX']", self.run_src)

    def test_run_py_no_sandbox_flag_injection(self):
        # 允许只读检查；禁止注入 --no-sandbox 或设置 CHROMIUM_FLAGS
        self.assertNotIn("os.environ['QTWEBENGINE_CHROMIUM_FLAGS']", self.run_src)
        self.assertNotIn("os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS'", self.run_src)

    def test_diag_still_records_sandbox_state(self):
        """启动诊断保留 sandbox 状态字段（A/B 证据能力不丢）。"""
        diag = io.open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'ui', 'web_diagnostics.py'), encoding='utf-8').read()
        self.assertIn('log_web_event', diag)  # 诊断模块仍在（字段在 run.py 传入）
        run_src = self.run_src
        self.assertIn('sandbox_env_present', run_src)
        self.assertIn('app_forces_sandbox_disabled=False', run_src)


class VueShellMigrationGuardTest(unittest.TestCase):
    """STEP-4/5 守护：Sidebar 与 Dashboard 迁 Vue 后的接线与产物存在性。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, 'ui', 'web_shell.py')
        with open(path, encoding='utf-8') as fh:
            cls.web_shell_src = fh.read()

    def _function_source(self, name):
        marker = 'def ' + name + '('
        start = self.web_shell_src.find(marker)
        self.assertGreater(start, 0, f'{name} 未找到')
        end = self.web_shell_src.find('\ndef ', start + 1)
        end = len(self.web_shell_src) if end < 0 else end
        return self.web_shell_src[start:end]

    def test_chrome_widget_uses_vue_chrome(self):
        self.assertIn("'vue/chrome.html'", self._function_source('create_chrome_widget'))

    def test_dashboard_widget_uses_vue_dashboard(self):
        src = self._function_source('create_dashboard_widget')
        self.assertIn("'vue/dashboard.html'", src)

    def test_legacy_chrome_html_retained(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'resources', 'webui', 'chrome.html')),
                        'legacy chrome.html 必须保留（应急对照）')

    def test_legacy_dashboard_html_retained(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'resources', 'webui', 'dashboard.html')),
                        'legacy dashboard.html 必须保留（应急对照）')

    def test_embedded_vue_chrome_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'resources', 'webui', 'vue', 'chrome.html')),
                        'embedded Vue chrome.html 缺失（frontend: npm run build:embedded）')

    def test_embedded_vue_dashboard_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'resources', 'webui', 'vue', 'dashboard.html')),
                        'embedded Vue dashboard.html 缺失（frontend: npm run build:embedded）')

    def test_home_bridge_contract_unchanged(self):
        for slot in ('navigate', 'openPalette', 'navModel', 'homeUsername',
                     'dashboardSummary', 'pageReady'):
            self.assertTrue(hasattr(web_shell.HomeBridge, slot), f'HomeBridge 缺少槽 {slot}')
        for signal in ('navigateRequested', 'paletteRequested', 'activeChanged',
                       'pageReadyReceived'):
            self.assertTrue(hasattr(web_shell.HomeBridge, signal), f'HomeBridge 缺少信号 {signal}')


class VueSidebarInfoGuardTest(unittest.TestCase):
    """Step 4B 守护：Sidebar 菜单行信息减法与图标显式映射（源码级，共 1 项）。"""

    def test_menu_rows_single_label_and_icon_mapping(self):
        with open(os.path.join(ROOT, 'frontend', 'src', 'chrome', 'ChromeApp.vue'),
                  encoding='utf-8') as fh:
            app_src = fh.read()
        # 菜单行不再渲染英文副标签与 SQL 缩写（数据字段保留在 navModel，仅视觉减法）
        self.assertNotIn('class="en"', app_src, '菜单行不得再渲染英文副标签')
        self.assertNotIn('class="dia"', app_src, '菜单行不得再渲染 SQL 缩写')
        # 分组双语标题保留（分区标签不属于菜单行）
        self.assertIn('{{ g.zh }} · {{ g.en }}', app_src, '分组双语标题必须保留')
        with open(os.path.join(ROOT, 'frontend', 'src', 'chrome', 'icons.ts'),
                  encoding='utf-8') as fh:
            icons_src = fh.read()
        for role in ('home', 'database', 'requirements', 'release', 'doc-update',
                     'daily-report', 'operations', 'shield-key', 'api-debug',
                     'json', 'document-id', 'vin', 'learning'):
            # icons.ts 对简单标识符用裸 key，带连字符的用引号 key
            needle = f"'{role}':" if '-' in role else f"{role}:"
            self.assertIn(needle, icons_src, f'icons.ts 缺少 role 显式映射：{role}')
        self.assertIn('FALLBACK_SYMBOL', icons_src, '未知 role 必须有中性回退图标')


class VueDashboardReducedMotionGuardTest(unittest.TestCase):
    """STEP-5 Review Fix A 守护：reduced-motion 下 Dashboard 不得因动画禁用而永久透明。"""

    def test_reduced_motion_restores_enter_opacity_and_transform(self):
        with open(os.path.join(ROOT, 'frontend', 'src', 'dashboard', 'DashboardApp.vue'),
                  encoding='utf-8') as fh:
            app_src = fh.read()
        self.assertIn('@media (prefers-reduced-motion: reduce)', app_src)
        # 必须显式在 reduced-motion 块中将 .enter 恢复为 opacity:1 与 transform:none
        reduced_block_start = app_src.find('@media (prefers-reduced-motion: reduce)')
        self.assertGreater(reduced_block_start, 0)
        reduced_block = app_src[reduced_block_start:]
        self.assertIn('.enter', reduced_block)
        self.assertIn('opacity:1', reduced_block)
        self.assertIn('transform:none', reduced_block)


if __name__ == '__main__':
    unittest.main()
