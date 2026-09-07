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
        from ui.theme_manager import ThemeManager

        app = QApplication.instance() or QApplication(sys.argv)
        tm = ThemeManager.instance()
        old_theme = tm.theme_id

        widget = None
        try:
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
                self.fail('WEB_REAL_RENDERER_TIMEOUT: QWebEngine 未在 8s 内触发 pageReady')

            self.assertIn('dashboard', ready_pages, '生产链必须触发 pageReady("dashboard")')

            # 3. 验证 Calm 主题状态与 DOM / computed styles / shadows
            js_probe = '''(() => {
                const root = document.documentElement;
                const s = getComputedStyle(root);
                const glass = document.querySelector('.glass');
                const card = document.querySelector('.card');
                const btn = document.querySelector('.btn-primary');
                return {
                    theme: root.getAttribute('data-theme'),
                    isDark: root.classList.contains('dark'),
                    hero: !!document.querySelector('.hero'),
                    statCount: document.querySelectorAll('.stat').length,
                    cardCount: document.querySelectorAll('.card').length,
                    btnPrimary: !!btn,
                    primary: s.getPropertyValue('--primary').trim(),
                    primaryGradStart: s.getPropertyValue('--primary-grad-start').trim(),
                    primaryGradEnd: s.getPropertyValue('--primary-grad-end').trim(),
                    glassBg: s.getPropertyValue('--glass-bg').trim(),
                    glassShadow: glass ? getComputedStyle(glass).boxShadow : '',
                    cardShadow: card ? getComputedStyle(card).boxShadow : '',
                    btnShadow: btn ? getComputedStyle(btn).boxShadow : '',
                    cardBg: card ? getComputedStyle(card).backgroundColor : ''
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
            self.assertEqual(calm_res.get('primary'), '#4A61F0')
            self.assertEqual(calm_res.get('primaryGradStart'), '#5B73FF')
            self.assertEqual(calm_res.get('primaryGradEnd'), '#4A61F0')
            self.assertEqual(calm_res.get('glassBg'), 'rgba(255, 254, 251, 0.9255)')

            # 验证 Calm 高程阴影不是 none，包含正确 px 几何与归一化后的 SHADOW 颜色
            glass_shadow = calm_res.get('glassShadow', '')
            card_shadow = calm_res.get('cardShadow', '')
            btn_shadow = calm_res.get('btnShadow', '')
            card_bg_calm = calm_res.get('cardBg', '')

            self.assertTrue(glass_shadow and glass_shadow != 'none', 'glass computed boxShadow 不得为 none')
            self.assertIn('8px 28px', glass_shadow, f'glass boxShadow 必须包含几何 8px 28px: {glass_shadow}')
            self.assertIn('rgba(43, 48, 74, 0.176', glass_shadow, f'glass boxShadow 必须包含归一化 L2 阴影色: {glass_shadow}')

            self.assertTrue(card_shadow and card_shadow != 'none', 'card computed boxShadow 不得为 none')
            self.assertIn('2px 8px', card_shadow, f'card boxShadow 必须包含几何 2px 8px: {card_shadow}')
            self.assertIn('rgba(43, 48, 74, 0.098', card_shadow, f'card boxShadow 必须包含归一化 L1 阴影色: {card_shadow}')

            self.assertTrue(btn_shadow and btn_shadow != 'none', 'btn-primary computed boxShadow 不得为 none')
            self.assertIn('8px 22px', btn_shadow, f'btn-primary boxShadow 必须包含几何 8px 22px: {btn_shadow}')
            self.assertIn('rgba(43, 48, 74, 0.176', btn_shadow, f'btn-primary boxShadow 必须包含归一化 L2 阴影色: {btn_shadow}')

            self.assertEqual(card_bg_calm, 'rgb(255, 255, 255)', f'Calm card 背景色应为白色 SURFACE: {card_bg_calm}')

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

            # 验证 Black 下阴影与卡片背景
            black_glass_shadow = black_res.get('glassShadow', '')
            black_card_shadow = black_res.get('cardShadow', '')
            black_btn_shadow = black_res.get('btnShadow', '')
            black_card_bg = black_res.get('cardBg', '')

            self.assertTrue(black_glass_shadow and black_glass_shadow != 'none', 'Black 下 glass boxShadow 不得为 none')
            self.assertTrue(black_card_shadow and black_card_shadow != 'none', 'Black 下 card boxShadow 不得为 none')
            self.assertTrue(black_btn_shadow and black_btn_shadow != 'none', 'Black 下 btn-primary boxShadow 不得为 none')

            self.assertNotEqual(black_card_bg, card_bg_calm, 'Black card 背景必须与 Calm 不同')
            self.assertEqual(black_card_bg, 'rgb(22, 22, 24)', f'Black card 背景必须来源于墨黑 SURFACE (#161618): {black_card_bg}')

        finally:
            # 清理资源与还原主题，防止测试污染
            if widget is not None:
                widget.close()
            tm.apply(app, old_theme)


if __name__ == '__main__':
    unittest.main()
