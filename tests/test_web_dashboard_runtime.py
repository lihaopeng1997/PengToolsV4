# -*- coding: utf-8 -*-
"""Web Dashboard 真实 QWebEngine 生产渲染冒烟测试。

验证生产链：
resources/webui/vue/dashboard.html
  -> create_dashboard_widget()
  -> QWebEngineView
  -> QWebChannel
  -> HomeBridge
  -> Vue Dashboard
  -> pageReady('dashboard')
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui import web_shell


class WebDashboardProductionRuntimeTest(unittest.TestCase):
    """真实 QWebEngine 运行态冒烟测试（带 <= 10s 硬超时守护）。"""

    @classmethod
    def setUpClass(cls):
        # 确保生产 embedded 产物存在
        cls.prod_html = os.path.join(ROOT, 'resources', 'webui', 'vue', 'dashboard.html')
        if not os.path.isfile(cls.prod_html):
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - 缺少生产产物 {cls.prod_html}')

        # 检查 WebEngine 是否可用
        if not web_shell.WEB_SHELL_AVAILABLE:
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}')

    def test_dashboard_real_qwebengine_production_chain(self):
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from ui.theme_manager import ThemeManager, theme_mode

        app = QApplication.instance() or QApplication(sys.argv)

        tm = ThemeManager.instance()
        tm.apply(app, 'calm')

        bridge = web_shell.HomeBridge()
        bridge.set_summary_provider(lambda: {
            'greeting': '下午好',
            'username': 'Tester',
            'stats': {
                'req_open': 3,
                'req_trend': '+1',
                'daily_done': 4,
                'daily_total': 5,
            },
            'release': {
                'countdown_state': 'normal',
                'days_left': 2,
                'date_text': '2026-09-09',
                'total': 8,
                'done': 6,
                'percent': 75,
            },
            'recent': [
                {'code': 'REQ-101', 'title': '主题对齐', 'status': 'run'},
            ],
            'checklist': [
                {'t': '自测完成', 'mini': 'OK'},
            ],
            'tools': [
                {'i': 1, 'zh': '数据库', 'icon': 'db', 'grad': 'c1'},
            ],
        })

        # 1. 初始向 bridge 注入 calm 语义 payload
        calm_payload = {
            'id': 'calm',
            'is_dark': False,
            'tokens': tm.palette(),
        }
        bridge.set_theme_payload(calm_payload)

        # 2. 通过正式工厂创建生产 widget（加载 vue/dashboard.html）
        widget = web_shell.create_dashboard_widget(bridge)
        widget.show()

        ready_pages = []
        bridge.pageReadyReceived.connect(ready_pages.append)

        loop = QEventLoop()

        # 硬超时 8 秒（符合规范要求的 <= 10s，防止任何死循环）
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
            widget.close()
            self.fail('WEB_REAL_RENDERER_TIMEOUT: QWebEngine 未在 8s 内触发 pageReady')

        self.assertIn('dashboard', ready_pages, '生产链必须触发 pageReady("dashboard")')

        # 3. 验证 Calm 主题状态与 DOM / computed styles
        js_probe = '''(() => {
            const root = document.documentElement;
            const s = getComputedStyle(root);
            return {
                theme: root.getAttribute('data-theme'),
                isDark: root.classList.contains('dark'),
                hero: !!document.querySelector('.hero'),
                statCount: document.querySelectorAll('.stat').length,
                cardCount: document.querySelectorAll('.card').length,
                btnPrimary: !!document.querySelector('.btn-primary'),
                primary: s.getPropertyValue('--primary').trim(),
                primaryGradStart: s.getPropertyValue('--primary-grad-start').trim(),
                primaryGradEnd: s.getPropertyValue('--primary-grad-end').trim(),
                glassBg: s.getPropertyValue('--glass-bg').trim()
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
        self.assertTrue(calm_res.get('hero'), 'Production DOM 中 .hero 必须存在')
        self.assertEqual(calm_res.get('statCount'), 4, 'Production DOM 中必须有 4 个 .stat')
        self.assertGreaterEqual(calm_res.get('cardCount', 0), 2, 'Production DOM 中必须有 .card')
        self.assertTrue(calm_res.get('btnPrimary'), 'Production DOM 中必须有 .btn-primary')
        self.assertEqual(calm_res.get('primary'), '#5B5FC7')
        self.assertEqual(calm_res.get('primaryGradStart'), '#5B5FC7')
        self.assertEqual(calm_res.get('primaryGradEnd'), '#4C50B0')
        self.assertEqual(calm_res.get('glassBg'), 'rgba(255, 254, 251, 0.9255)')

        # 4. 动态切换至 black 墨黑主题
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

        # 等待 350ms 让 DOM 与样式更新完成
        timeout_timer.start(4000)
        t_switch = QTimer()
        t_switch.setSingleShot(True)
        t_switch.timeout.connect(lambda: widget.web_page.runJavaScript(js_probe, on_black_result))
        t_switch.start(350)
        loop.exec()

        self.assertEqual(black_res.get('theme'), 'black', '动态切换后主题必须为 black')
        self.assertTrue(black_res.get('isDark'), 'black 模式下 isDark 必须为 True')
        self.assertEqual(black_res.get('primary'), '#8FBB9E')
        self.assertEqual(black_res.get('primaryGradStart'), '#8FBB9E')
        self.assertEqual(black_res.get('primaryGradEnd'), '#6F9E7E')
        self.assertEqual(black_res.get('glassBg'), 'rgba(22, 22, 24, 0.902)')

        # 资源清理
        widget.close()


if __name__ == '__main__':
    unittest.main()
