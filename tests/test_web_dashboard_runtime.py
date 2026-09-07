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
import datetime
import os
import sys
import unittest
from unittest.mock import patch

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

    def test_dashboard_real_qwebengine_production_chain_and_interactions(self):
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from ui.theme_manager import ThemeManager
        from tools.dashboard_summary import build_dashboard_summary
        from panels.requirement_panel import RequirementPanel

        app = QApplication.instance() or QApplication(sys.argv)
        tm = ThemeManager.instance()
        old_theme = tm.theme_id

        widget = None
        try:
            tm.apply(app, 'calm')

            bridge = web_shell.HomeBridge()

            today = datetime.date(2026, 9, 7)
            real_req = {
                'id': 'REQ-PROD-99',
                'code': 'REQ-0099',
                'title': '生产订单接口改造',
                'system': '核心业务',
                'status': '开发中',
                'actual_release_date': '2026-09-20',
                'actual_online_date': '2026-09-20',
                'online_month': '2026-09',
                'test_points': [{'id': '1', 'text': 'tp1', 'done': True}],
            }
            current_reqs = [real_req]

            bridge.set_summary_provider(lambda: build_dashboard_summary(requirements=current_reqs, today=today))

            nav_events = []
            create_events = []
            open_events = []

            bridge.navigateRequested.connect(nav_events.append)
            bridge.createRequirementRequested.connect(lambda: create_events.append(True))
            bridge.openRequirementRequested.connect(open_events.append)

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
                self.fail('QWebEngine 未在 8 秒内触发 pageReady，生产链路加载超时')

            # 验证 bridge 收到了来自 Vue 的 pageReady('dashboard')
            self.assertIn('dashboard', ready_pages, f'必须收到 dashboard 就绪通知，实际: {ready_pages}')

            def run_js(code):
                res_box = {}
                def on_res(r):
                    res_box['val'] = r
                    loop.quit()
                t_js = QTimer()
                t_js.setSingleShot(True)
                t_js.timeout.connect(loop.quit)
                t_js.start(3000)
                widget.web_page.runJavaScript(code, on_res)
                loop.exec()
                t_js.stop()
                return res_box.get('val')

            # 3. 探针验证 Calm 主题与几何尺寸
            js_probe = '''(() => {
                const root = document.documentElement;
                const style = getComputedStyle(root);
                const hero = document.querySelector('.hero');
                const stat = document.querySelector('.stat');
                const card = document.querySelector('.card');
                const btnPrimary = document.querySelector('.btn-primary');
                const statStyle = stat ? getComputedStyle(stat) : null;
                const cardStyle = card ? getComputedStyle(card) : null;
                const btnStyle = btnPrimary ? getComputedStyle(btnPrimary) : null;

                return {
                    theme: root.getAttribute('data-theme'),
                    isDark: root.classList.contains('dark'),
                    primary: style.getPropertyValue('--primary').trim(),
                    primaryGradStart: style.getPropertyValue('--primary-grad-start').trim(),
                    primaryGradEnd: style.getPropertyValue('--primary-grad-end').trim(),
                    glassBg: style.getPropertyValue('--glass-bg').trim(),
                    hero: !!hero,
                    statCount: document.querySelectorAll('.stat').length,
                    cardCount: document.querySelectorAll('.card').length,
                    btnPrimary: !!btnPrimary,
                    glassShadow: statStyle ? statStyle.boxShadow : '',
                    cardShadow: cardStyle ? cardStyle.boxShadow : '',
                    btnShadow: btnStyle ? btnStyle.boxShadow : '',
                    cardBg: cardStyle ? cardStyle.backgroundColor : '',
                };
            })()'''

            calm_res = run_js(js_probe) or {}

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

            # 等待 200ms 让 DOM 与样式更新完成
            timer_wait = QTimer()
            timer_wait.setSingleShot(True)
            timer_wait.timeout.connect(loop.quit)
            timer_wait.start(200)
            loop.exec()

            black_res = run_js(js_probe) or {}

            self.assertEqual(black_res.get('theme'), 'black', '动态切换后主题必须为 black')
            self.assertTrue(black_res.get('isDark'), 'black 模式下 isDark 必须为 True')
            self.assertEqual(black_res.get('primary'), '#8FBB9E')
            self.assertEqual(black_res.get('primaryGradStart'), '#8FBB9E')
            self.assertEqual(black_res.get('primaryGradEnd'), '#6F9E7E')
            self.assertEqual(black_res.get('glassBg'), 'rgba(22, 22, 24, 0.902)')

            black_glass_shadow = black_res.get('glassShadow', '')
            black_card_shadow = black_res.get('cardShadow', '')
            black_btn_shadow = black_res.get('btnShadow', '')
            black_card_bg = black_res.get('cardBg', '')

            self.assertTrue(black_glass_shadow and black_glass_shadow != 'none', 'Black 下 glass boxShadow 不得为 none')
            self.assertTrue(black_card_shadow and black_card_shadow != 'none', 'Black 下 card boxShadow 不得为 none')
            self.assertTrue(black_btn_shadow and black_btn_shadow != 'none', 'Black 下 btn-primary boxShadow 不得为 none')

            self.assertNotEqual(black_card_bg, card_bg_calm, 'Black card 背景必须与 Calm 不同')
            self.assertEqual(black_card_bg, 'rgb(22, 22, 24)', f'Black card 背景必须来源于墨黑 SURFACE (#161618): {black_card_bg}')

            # 5. 真实 DOM 交互验证
            # A. 点击真实“数据中心”工具 DOM => Python 收到 nav 18
            js_click_dc = '''(() => {
                const tools = Array.from(document.querySelectorAll('.tool'));
                const dc = tools.find(el => el.textContent.includes('数据中心'));
                if (dc) { dc.click(); return true; }
                return false;
            })()'''
            clicked_dc = run_js(js_click_dc)
            self.assertTrue(clicked_dc, '页面中必须找到“数据中心”工具')
            self.assertIn(18, nav_events, '点击数据中心必须向 Python 派发 nav 18')

            # B. 点击真实“+ 新建需求”按钮 DOM
            js_click_create = '''(() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const btn = btns.find(el => el.textContent.includes('新建需求'));
                if (btn) { btn.click(); return true; }
                return false;
            })()'''
            clicked_create = run_js(js_click_create)
            self.assertTrue(clicked_create, '页面中必须找到“新建需求”按钮')
            self.assertGreaterEqual(len(create_events), 1, '点击新建需求必须触发 createRequirementRequested')

            # C. 真实 requirement 行点击“查看”
            js_click_view = '''(() => {
                const btn = document.querySelector('.req-list .ck:not(.is-demo) .row-act-btn');
                if (btn) { btn.click(); return true; }
                return false;
            })()'''
            clicked_view = run_js(js_click_view)
            self.assertTrue(clicked_view, '页面中必须找到真实需求行的“查看”按钮')
            self.assertIn('REQ-PROD-99', open_events, '点击真实需求“查看”必须向 Python 派发对应 requirement ID')

            # 6. Live sync:
            js_count_tasks = '''(() => {
                const tasks = document.querySelectorAll('.card:nth-child(2) .req-list .ck');
                return tasks.length;
            })()'''
            initial_count = run_js(js_count_tasks)
            self.assertEqual(initial_count, 1, '初始状态本月任务应有 1 项')

            # 模拟 RequirementPanel._move_requirements 移动到下月
            with patch('panels.requirement_panel.save_requirements'), \
                 patch('panels.requirement_panel.load_requirements', return_value=current_reqs), \
                 patch('panels.requirement_panel.load_systems', return_value=[]):
                panel = RequirementPanel()
                panel._requirements = current_reqs
                panel._move_requirements([real_req], '2026-10')

            # 通过 bridge 同步 push_summary（不 reload 页面）
            bridge.push_summary()

            timer_wait.start(200)
            loop.exec()

            # 验证同一个页面 DOM 中的当月上线任务已响应式清零且标签更新为 0 项
            js_after_count = '''(() => {
                const tasks = document.querySelectorAll('.card:nth-child(2) .req-list .ck');
                const dchip = document.querySelector('.card:nth-child(2) .dchip')?.textContent?.trim() || '';
                const recentText = document.querySelector('.card:nth-child(1) .req-list .ck')?.textContent || '';
                return {
                    count: tasks.length,
                    dchip: dchip,
                    hasNewDate: recentText.includes('2026-10-20')
                };
            })()'''
            after_state = run_js(js_after_count)
            self.assertEqual(after_state['count'], 0, '真实需求移至下月后，DOM 中当月任务列表应自动变为空')
            self.assertEqual(after_state['dchip'], '0 项', 'DOM 徽标应自动变为 0 项')
            self.assertTrue(after_state['hasNewDate'], 'DOM 最近需求中的上线日期应自动同步为 2026-10-20')

            # 7. Demo 模式与虚假 ID 防御
            open_count_before = len(open_events)
            with patch('tools.dashboard_summary.load_requirements', return_value=[]):
                bridge.set_summary_provider(lambda: build_dashboard_summary(requirements=None, today=today))
                bridge.push_summary()

            timer_wait.start(200)
            loop.exec()

            # 验证页面已呈现示例标识
            js_demo_check = '''(() => {
                const demoBadges = document.querySelectorAll('.demo-badge');
                const demoRow = document.querySelector('.req-list .ck.is-demo');
                if (demoRow) {
                    demoRow.click();
                }
                return {
                    badgeCount: demoBadges.length,
                    hasDemoRow: !!demoRow
                };
            })()'''
            demo_res = run_js(js_demo_check)
            self.assertGreaterEqual(demo_res['badgeCount'], 1, '示例模式下必须渲染示例徽章')
            self.assertTrue(demo_res['hasDemoRow'], '示例模式下必须渲染带有 is-demo 的需求行')
            self.assertEqual(len(open_events), open_count_before, '点击示例需求行绝对不得向 Python 派发虚假 ID 或 openRequirementRequested')

        finally:
            # 清理资源与还原主题，防止测试污染
            if widget is not None:
                widget.close()
            tm.apply(app, old_theme)


if __name__ == '__main__':
    unittest.main()
