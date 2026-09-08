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
            nav_requests = []
            palette_requests = []
            bridge.navigateRequested.connect(nav_requests.append)
            bridge.paletteRequested.connect(lambda: palette_requests.append(True))

            nav_model = {
                'groups': [
                    {
                        'key': 'workspace',
                        'zh': '工作台',
                        'en': 'WORKSPACE',
                        'items': [
                            {'i': 0, 'zh': '首页', 'en': 'HOME', 'icon': 'home'},
                            {
                                'i': 14,
                                'zh': '数据中心',
                                'en': 'DATA CENTER',
                                'icon': 'database',
                                'children': [
                                    {'i': 18, 'zh': 'Oracle', 'en': 'ORACLE', 'icon': 'database'},
                                ],
                            },
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

            # 验证 Chrome 侧栏计算样式、覆盖层与 DOM
            js_probe = '''(() => {
                const root = document.documentElement;
                const body = document.body;
                const activeItem = document.querySelector('.nav-item.active');
                const brand = document.querySelector('.brand');
                const brandName = document.querySelector('.brand-name');
                const groupLabel = document.querySelector('.g-label');
                const parentItem = document.querySelector('.nav-item.parent');
                const settingsItem = document.querySelector('#foot-settings .nav-item');
                const paletteKbd = document.querySelector('.kbd');
                const logo = document.querySelector('.logo');
                const sRoot = getComputedStyle(root);
                const sBody = getComputedStyle(body);
                const sBefore = getComputedStyle(body, '::before');
                const sActive = activeItem ? getComputedStyle(activeItem) : null;
                const sLogo = logo ? getComputedStyle(logo) : null;
                return {
                    theme: root.getAttribute('data-theme'),
                    isDark: root.classList.contains('dark'),
                    sidebarBgVar: sRoot.getPropertyValue('--sidebar-bg').trim(),
                    sidebarTextVar: sRoot.getPropertyValue('--sidebar-text').trim(),
                    navActiveTextVar: sRoot.getPropertyValue('--nav-active-text').trim(),
                    primaryGradStartVar: sRoot.getPropertyValue('--primary-grad-start').trim(),
                    primaryGradEnd: sRoot.getPropertyValue('--primary-grad-end').trim(),
                    bodyBg: sBody.backgroundColor || sBody.background,
                    beforeOpacity: parseFloat(sBefore.opacity || '0'),
                    beforeBg: sBefore.backgroundImage || sBefore.background,
                    hasActiveItem: !!activeItem,
                    hasBrand: !!brand && !!brandName,
                    hasGroupLabel: !!groupLabel,
                    hasParent: !!parentItem,
                    hasSettings: !!settingsItem,
                    hasPalette: !!paletteKbd,
                    activeColor: sActive ? sActive.color : '',
                    activeBg: sActive ? (sActive.backgroundColor || sActive.background) : '',
                    logoBg: sLogo ? (sLogo.backgroundImage || sLogo.background) : '',
                    logoShadow: sLogo ? sLogo.boxShadow : ''
                };
            })()'''

            def probe_theme(theme_name: str, is_dark: bool) -> dict:
                tm.apply(app, theme_name)
                payload = {
                    'id': theme_name,
                    'is_dark': is_dark,
                    'tokens': tm.palette(theme_name),
                }
                bridge.set_theme_payload(payload)

                res = {}
                def on_res(r):
                    res.update(r or {})
                    loop.quit()

                timeout_timer.start(4000)
                t_switch = QTimer()
                t_switch.setSingleShot(True)
                t_switch.timeout.connect(lambda: widget.web_page.runJavaScript(js_probe, on_res))
                t_switch.start(350)
                loop.exec()
                return res

            # 1. 初始 Calm 模式验证
            calm_res = {}
            def on_calm_result(res):
                calm_res.update(res or {})
                loop.quit()

            timeout_timer.start(4000)
            widget.web_page.runJavaScript(js_probe, on_calm_result)
            loop.exec()

            self.assertEqual(calm_res.get('theme'), 'calm', '初始主题必须为 calm')
            self.assertFalse(calm_res.get('isDark'), 'calm 模式下 isDark 必须为 False')
            self.assertEqual(calm_res.get('sidebarBgVar'), '#F7F6FC', 'Calm 下 --sidebar-bg 应为 #F7F6FC')
            self.assertEqual(calm_res.get('sidebarTextVar'), '#262438', 'Calm 下 --sidebar-text 应为 #262438')
            self.assertEqual(calm_res.get('navActiveTextVar'), '#FFFFFF', 'Calm 下 --nav-active-text 应为 #FFFFFF')
            self.assertEqual(calm_res.get('primaryGradStartVar'), '#7C6AE6', 'Calm 下品牌渐变起色应为 #7C6AE6')
            self.assertEqual(calm_res.get('primaryGradEnd'), '#6C58D9', 'Calm 下品牌渐变终色应为 #6C58D9')
            self.assertTrue(calm_res.get('hasBrand'), '侧栏必须渲染品牌区')
            self.assertTrue(calm_res.get('hasActiveItem'), '侧栏必须渲染激活导航项')
            self.assertTrue(calm_res.get('hasGroupLabel'), '侧栏必须渲染分组标签')
            self.assertTrue(calm_res.get('hasParent'), '侧栏必须渲染可折叠父导航')
            self.assertTrue(calm_res.get('hasSettings'), '侧栏底部必须渲染设置项')
            self.assertTrue(calm_res.get('hasPalette'), '侧栏底部必须渲染快捷面板入口')
            self.assertLessEqual(calm_res.get('beforeOpacity', 0), 0.08, 'Calm 下 Aurora 透明度应 <= 0.08')

            # 2. 点击父节点：navigateRequested 必须为 0
            widget.web_page.runJavaScript("document.querySelector('.nav-item.parent').click()")
            t_parent = QTimer()
            t_parent.setSingleShot(True)
            t_parent.timeout.connect(loop.quit)
            t_parent.start(150)
            loop.exec()
            self.assertEqual(len(nav_requests), 0, '点击父节点绝不得触发 navigateRequested')

            # 3. 点击叶子节点：navigateRequested 必须为 1
            widget.web_page.runJavaScript("document.querySelector('.sub .nav-item').click()")
            t_leaf = QTimer()
            t_leaf.setSingleShot(True)
            t_leaf.timeout.connect(loop.quit)
            t_leaf.start(300)
            loop.exec()
            self.assertEqual(len(nav_requests), 1, '点击叶子项必须触发一次 navigateRequested')
            self.assertEqual(nav_requests[0], 18, '点击子叶子必须导航到 index 18')

            # 4. 点击快速面板：paletteRequested 必须为 1
            widget.web_page.runJavaScript("document.querySelector('.kbd').click()")
            t_kbd = QTimer()
            t_kbd.setSingleShot(True)
            t_kbd.timeout.connect(loop.quit)
            t_kbd.start(300)
            loop.exec()
            self.assertEqual(len(palette_requests), 1, '点击快捷面板按钮必须触发一次 paletteRequested')

            # 记录切换前实例与状态
            widget_ref = widget
            web_view_ref = widget.web_view
            ready_count_before = ready_pages.count('chrome')

            # 5. 动态切换至 Black 墨黑
            black_res = probe_theme('black', True)
            self.assertEqual(black_res.get('theme'), 'black')
            self.assertTrue(black_res.get('isDark'))
            self.assertEqual(black_res.get('sidebarBgVar'), '#191924', 'Black 侧栏背景应为 #191924')
            self.assertEqual(black_res.get('sidebarTextVar'), '#ECEAF7', 'Black 侧栏文本应为 #ECEAF7')
            self.assertEqual(black_res.get('navActiveTextVar'), '#FFFFFF')
            self.assertLessEqual(black_res.get('beforeOpacity', 0), 0.10, 'Black 下 Aurora 透明度应 <= 0.10')

            # 验证 DOM widget 不重建，pageReady 不重复触发
            self.assertIs(widget, widget_ref, '主题切换后 widget 实例不得重建')
            self.assertIs(widget.web_view, web_view_ref, '主题切换后 QWebEngineView 不得重建')
            self.assertEqual(ready_pages.count('chrome'), ready_count_before, '主题切换不得重复触发 pageReady')

        finally:
            if widget is not None:
                widget.close()
            tm.apply(app, old_theme)


if __name__ == '__main__':
    unittest.main()
