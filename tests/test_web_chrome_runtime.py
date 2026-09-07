# -*- coding: utf-8 -*-
"""Web Chrome 真实 QWebEngine 侧栏渲染冒烟测试。

验证生产链：
resources/webui/vue/chrome.html
  -> create_chrome_widget()
  -> QWebEngineView
  -> QWebChannel
  -> HomeBridge
  -> Vue Chrome
  -> pageReady('chrome')
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui import web_shell


class WebChromeProductionRuntimeTest(unittest.TestCase):
    """真实 Chrome 运行态冒烟测试（带 <= 10s 硬超时守护）。"""

    @classmethod
    def setUpClass(cls):
        cls.prod_html = os.path.join(ROOT, 'resources', 'webui', 'vue', 'chrome.html')
        if not os.path.isfile(cls.prod_html):
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - 缺少生产产物 {cls.prod_html}')

        if not web_shell.WEB_SHELL_AVAILABLE:
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}')

    def test_chrome_real_qwebengine_production_chain(self):
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from ui.theme_manager import ThemeManager

        app = QApplication.instance() or QApplication(sys.argv)
        tm = ThemeManager.instance()
        old_theme = tm.theme_id

        widget = None
        try:
            tm.apply(app, 'calm')

            bridge = web_shell.HomeBridge()
            nav_model = {
                'groups': [
                    {
                        'key': 'workspace',
                        'zh': '工作台',
                        'en': 'WORKSPACE',
                        'items': [
                            {'i': 0, 'zh': '首页', 'en': 'HOME', 'icon': 'home'},
                            {'i': 1, 'zh': '数据库', 'en': 'DB', 'icon': 'database'},
                        ],
                    }
                ],
                'settings': {'i': 7, 'zh': '设置', 'en': 'SET', 'icon': 'gear'},
                'current': 0,
            }
            bridge.set_nav_model(nav_model)

            calm_payload = {
                'id': 'calm',
                'is_dark': False,
                'tokens': tm.palette(),
            }
            bridge.set_theme_payload(calm_payload)

            widget = web_shell.create_chrome_widget(bridge)
            widget.show()

            ready_pages = []
            bridge.pageReadyReceived.connect(ready_pages.append)

            loop = QEventLoop()
            timed_out = [False]
            def on_timeout():
                timed_out[0] = True
                loop.quit()

            timeout_timer = QTimer()
            timeout_timer.setSingleShot(True)
            timeout_timer.timeout.connect(on_timeout)
            timeout_timer.start(8000)

            bridge.pageReadyReceived.connect(lambda _p: loop.quit())
            loop.exec()

            if timed_out[0]:
                self.fail('WEB_REAL_RENDERER_TIMEOUT: Chrome 未在 8s 内触发 pageReady')

            self.assertIn('chrome', ready_pages, '生产链必须触发 pageReady("chrome")')

            # 验证 Calm 下 Chrome 侧栏计算样式与 DOM
            js_probe = '''(() => {
                const root = document.documentElement;
                const body = document.body;
                const activeItem = document.querySelector('.nav-item.active');
                const logo = document.querySelector('.logo');
                const brand = document.querySelector('.brand-name');
                const sRoot = getComputedStyle(root);
                const sBody = getComputedStyle(body);
                const sActive = activeItem ? getComputedStyle(activeItem) : null;
                const sLogo = logo ? getComputedStyle(logo) : null;
                return {
                    theme: root.getAttribute('data-theme'),
                    isDark: root.classList.contains('dark'),
                    sidebarBgVar: sRoot.getPropertyValue('--sidebar-bg').trim(),
                    sidebarTextVar: sRoot.getPropertyValue('--sidebar-text').trim(),
                    primaryGradStartVar: sRoot.getPropertyValue('--primary-grad-start').trim(),
                    primaryGradEnd: sRoot.getPropertyValue('--primary-grad-end').trim(),
                    bodyBg: sBody.backgroundImage || sBody.background,
                    brandNameColor: brand ? getComputedStyle(brand).color : '',
                    hasActiveItem: !!activeItem,
                    activeBg: sActive ? (sActive.backgroundImage || sActive.background) : '',
                    logoBg: sLogo ? (sLogo.backgroundImage || sLogo.background) : ''
                };
            })()'''

            calm_res = {}
            def on_calm_result(res):
                calm_res.update(res or {})
                loop.quit()

            timeout_timer.start(4000)
            widget.web_page.runJavaScript(js_probe, on_calm_result)
            loop.exec()

            self.assertEqual(calm_res.get('theme'), 'calm', '初始主题必须为 calm')
            self.assertFalse(calm_res.get('isDark'), 'calm 模式下 isDark 必须为 False')
            self.assertEqual(calm_res.get('sidebarBgVar'), '#161D30', 'Calm 下 --sidebar-bg 应为深靛蓝 #161D30')
            self.assertEqual(calm_res.get('sidebarTextVar'), '#F7F9FF', 'Calm 下 --sidebar-text 应为亮白 #F7F9FF')
            self.assertEqual(calm_res.get('primaryGradStartVar'), '#5B73FF', 'Calm 下品牌渐变起色应为 #5B73FF')
            self.assertEqual(calm_res.get('primaryGradEnd'), '#4A61F0', 'Calm 下品牌渐变终色应为 #4A61F0')
            self.assertTrue(calm_res.get('hasActiveItem'), '侧栏必须渲染激活导航项')

            # 动态切换至 black 墨黑
            tm.apply(app, 'black')
            black_payload = {
                'id': 'black',
                'is_dark': True,
                'tokens': tm.palette(),
            }
            bridge.set_theme_payload(black_payload)

            black_res = {}
            def on_black_result(res):
                black_res.update(res or {})
                loop.quit()

            timeout_timer.start(4000)
            t_switch = QTimer()
            t_switch.setSingleShot(True)
            t_switch.timeout.connect(lambda: widget.web_page.runJavaScript(js_probe, on_black_result))
            t_switch.start(350)
            loop.exec()

            self.assertEqual(black_res.get('theme'), 'black', '动态切换后主题必须为 black')
            self.assertTrue(black_res.get('isDark'), 'black 模式下 isDark 必须为 True')
            self.assertEqual(black_res.get('sidebarBgVar'), '#111114', 'Black 侧栏背景应为近黑 #111114')

        finally:
            if widget is not None:
                widget.close()
            tm.apply(app, old_theme)


if __name__ == '__main__':
    unittest.main()
