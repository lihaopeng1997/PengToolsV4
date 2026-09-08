# -*- coding: utf-8 -*-
"""Web Dashboard 真实 QWebEngine 生产渲染与 MainWindow 实时同步集成测试。

覆盖两大生产测试矩阵：
1. test_dashboard_real_qwebengine_production_chain_and_interactions:
   - 生产 HTML + QWebChannel + HomeBridge + Vue 完整就绪
   - 单主题与旧 Black 输入兼容归一探针（tokens / 几何阴影 / 背景色）
   - 真实 DOM 交互：点击数据中心派发 nav=18、点击新建需求派发信号、点击真实需求查看派发 ID
   - 示例模式隔离：呈现“示例”徽标，点击示例行严防虚假 ID 派发

2. test_dashboard_real_qwebengine_mainwindow_live_sync:
   - 严格数据隔离：主动导入并显式 patch tools.dashboard_summary.load_requirements 等数据源
   - 真实 MainWindow + RequirementPanel + 信号连接
   - 自然事件流：RequirementPanel._move_requirements -> requirements_changed -> MainWindow._push_dashboard_summary -> _dash_bridge.push_summary -> Vue
   - 同一个已加载 Vue DOM 响应式清零月度任务（1 -> 0）并确认旧最近需求卡片未重新出现，全程无手工 push
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
    """真实 QWebEngine 运行态与 MainWindow 生产集成测试（带 <= 10s 硬超时守护）。"""

    @classmethod
    def setUpClass(cls):
        cls.prod_html = os.path.join(ROOT, 'resources', 'webui', 'vue', 'dashboard.html')
        if not os.path.isfile(cls.prod_html):
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - 缺少生产产物 {cls.prod_html}')

        if not web_shell.WEB_SHELL_AVAILABLE:
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}')

    def test_dashboard_real_qwebengine_production_chain_and_interactions(self):
        """测试 1：验证真实 QWebEngine 运行态主题切换与完整 DOM 交互链。"""
        import tools.daily_reports
        import tools.dashboard_summary
        import tools.requirements
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from ui.theme_manager import ThemeManager

        app = QApplication.instance() or QApplication(sys.argv)
        tm = ThemeManager.instance()
        old_theme = tm.theme_id

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

        nav_events = []
        create_events = []
        open_events = []

        widget = None
        loop = QEventLoop()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timed_out = [False]
        def on_timeout():
            timed_out[0] = True
            loop.quit()

        timeout_timer.timeout.connect(on_timeout)
        timeout_timer.start(8000)

        try:
            tm.apply(app, 'calm')

            bridge = web_shell.HomeBridge()
            from ui.navigation_model import build_web_nav_model_data
            bridge.set_nav_model(build_web_nav_model_data())
            bridge.navigateRequested.connect(nav_events.append)
            bridge.createRequirementRequested.connect(lambda: create_events.append(True))
            bridge.openRequirementRequested.connect(open_events.append)

            with patch('tools.dashboard_summary.load_requirements', return_value=current_reqs), \
                 patch('tools.dashboard_summary.load_release_board', return_value={}), \
                 patch('tools.daily_reports.load_reports', return_value={}):

                bridge.set_summary_provider(
                    lambda: tools.dashboard_summary.build_dashboard_summary(requirements=current_reqs, today=today)
                )

                calm_payload = {
                    'id': 'calm',
                    'is_dark': False,
                    'tokens': tm.palette(),
                }
                bridge.set_theme_payload(calm_payload)

                widget = web_shell.create_dashboard_widget(bridge)
                widget.show()

                ready_pages = []
                bridge.pageReadyReceived.connect(lambda p: (ready_pages.append(p), loop.quit()))
                loop.exec()

                if timed_out[0]:
                    self.fail('QWebEngine 未在 8 秒内触发 pageReady，加载超时')

                timeout_timer.stop()
                self.assertIn('dashboard', ready_pages)

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

                # A. 探针验证 Calm 模式
                js_probe = '''(() => {
                    const root = document.documentElement;
                    const style = getComputedStyle(root);
                    const hero = document.querySelector('.hero');
                    const stat = document.querySelector('.stat');
                    const card = document.querySelector('.card');
                    const btnPrimary = document.querySelector('.btn-primary');
                    const heroStyle = hero ? getComputedStyle(hero) : null;
                    const statStyle = stat ? getComputedStyle(stat) : null;
                    const cardStyle = card ? getComputedStyle(card) : null;
                    const btnStyle = btnPrimary ? getComputedStyle(btnPrimary) : null;

                    return {
                        theme: root.getAttribute('data-theme'),
                        isDark: root.classList.contains('dark'),
                        primary: style.getPropertyValue('--primary').trim(),
                        appBg: style.getPropertyValue('--app-bg').trim(),
                        surface: style.getPropertyValue('--surface').trim(),
                        glassBg: style.getPropertyValue('--glass-bg').trim(),
                        hero: !!hero,
                        statCount: document.querySelectorAll('.stat').length,
                        cardCount: document.querySelectorAll('.card').length,
                        btnPrimary: !!btnPrimary,
                        glassShadow: heroStyle ? heroStyle.boxShadow : '',
                        cardShadow: cardStyle ? cardStyle.boxShadow : '',
                        btnShadow: btnStyle ? btnStyle.boxShadow : '',
                        cardBg: cardStyle ? cardStyle.backgroundColor : '',
                    };
                })()'''
                calm_res = run_js(js_probe) or {}
                self.assertEqual(calm_res.get('theme'), 'calm')
                self.assertFalse(calm_res.get('isDark'))
                self.assertEqual(calm_res.get('primary'), '#6C58D9')
                self.assertEqual(calm_res.get('appBg'), '#F4F3FA')
                self.assertEqual(calm_res.get('surface'), '#FDFDFF')
                self.assertTrue(calm_res.get('glassShadow') and calm_res.get('glassShadow') != 'none')
                self.assertTrue(calm_res.get('cardShadow') and calm_res.get('cardShadow') != 'none')

                # B. 验证单一晴空棱镜：尝试切换至 black 时，生产管理器安全收口为 calm，杜绝进入 black
                applied = tm.apply(app, 'black')
                self.assertEqual(applied, 'calm')
                self.assertEqual(tm.theme_id, 'calm')
                calm_tokens = tm.palette()
                self.assertEqual(calm_tokens['PRIMARY'], '#6C58D9')
                payload = {
                    'id': tm.theme_id,
                    'is_dark': False,
                    'tokens': calm_tokens,
                    'motion_enabled': True,
                }
                bridge.set_theme_payload(payload)

                timer_wait = QTimer()
                timer_wait.setSingleShot(True)
                timer_wait.timeout.connect(loop.quit)
                timer_wait.start(200)
                loop.exec()

                black_res = run_js(js_probe) or {}
                self.assertEqual(black_res.get('theme'), 'calm')
                self.assertFalse(black_res.get('isDark'))
                self.assertEqual(black_res.get('primary'), '#6C58D9')
                self.assertEqual(black_res.get('appBg'), '#F4F3FA')
                self.assertEqual(black_res.get('surface'), '#FDFDFF')

                # C. DOM 点击“数据中心”工具 => Python 收到 nav 18
                js_click_dc = '''(() => {
                    const tools = Array.from(document.querySelectorAll('.tool'));
                    const dc = tools.find(el => el.textContent.includes('数据中心'));
                    if (dc) { dc.click(); return true; }
                    return false;
                })()'''
                self.assertTrue(run_js(js_click_dc), '页面中必须找到“数据中心”工具')
                self.assertIn(18, nav_events, '点击数据中心必须向 Python 派发 nav 18')

                # D. DOM 点击“+ 新建需求”按钮
                js_click_create = '''(() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const btn = btns.find(el => el.textContent.includes('新建需求'));
                    if (btn) { btn.click(); return true; }
                    return false;
                })()'''
                self.assertTrue(run_js(js_click_create), '页面中必须找到“新建需求”按钮')
                self.assertGreaterEqual(len(create_events), 1, '点击新建需求必须触发 createRequirementRequested')

                # E. 真实 requirement 行点击“查看”
                js_click_view = '''(() => {
                    const btn = document.querySelector('.req-list .ck:not(.is-demo) .row-act-btn');
                    if (btn) { btn.click(); return true; }
                    return false;
                })()'''
                self.assertTrue(run_js(js_click_view), '页面中必须找到真实需求行的“查看”按钮')
                self.assertIn('REQ-PROD-99', open_events, '点击真实需求“查看”必须向 Python 派发对应 ID')

                # E2. 验证 Requirement ID Badge 存在、内容、title 与字号契约
                js_check_id_badge = '''(() => {
                    const badge = document.querySelector('.req-list .ck:not(.is-demo) .req-id-badge');
                    if (!badge) return null;
                    const style = getComputedStyle(badge);
                    return {
                        text: badge.textContent.trim(),
                        title: badge.getAttribute('title'),
                        fontSize: parseFloat(style.fontSize) || 0,
                        fontWeight: parseInt(style.fontWeight, 10) || 0,
                    };
                })()'''
                badge_res = run_js(js_check_id_badge)
                self.assertIsNotNone(badge_res, '真实需求行必须渲染 .req-id-badge')
                self.assertEqual(badge_res.get('text'), 'REQ-0099')
                self.assertEqual(badge_res.get('title'), 'REQ-0099')
                self.assertGreaterEqual(badge_res.get('fontSize'), 12.0, '需求编号字号不得过小（>= 12px）')
                self.assertGreaterEqual(badge_res.get('fontWeight'), 600, '需求编号 font-weight 至少 600')

                # E3. 键盘事件与嵌套按钮冒泡防重：真实需求行 Enter / Space 激活契约
                # 1) focus row, dispatch Enter => exactly 1 increment
                open_cnt = len(open_events)
                js_row_enter = '''(() => {
                    const row = document.querySelector('.req-list .ck:not(.is-demo)');
                    if (!row) return false;
                    row.focus();
                    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                    return true;
                })()'''
                self.assertTrue(run_js(js_row_enter))
                timer_wait.start(100)
                loop.exec()
                self.assertEqual(len(open_events), open_cnt + 1, '聚焦真实需求行按 Enter 必须派发且仅派发 1 次 openRequirement')

                # 2) focus row, dispatch Space => exactly 1 increment
                open_cnt = len(open_events)
                js_row_space = '''(() => {
                    const row = document.querySelector('.req-list .ck:not(.is-demo)');
                    if (!row) return false;
                    row.focus();
                    row.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', code: 'Space', keyCode: 32, which: 32, bubbles: true }));
                    return true;
                })()'''
                self.assertTrue(run_js(js_row_space))
                timer_wait.start(100)
                loop.exec()
                self.assertEqual(len(open_events), open_cnt + 1, '聚焦真实需求行按 Space 必须派发且仅派发 1 次 openRequirement')

                # 3) focus .row-act-btn, dispatch Enter/native activation => exactly 1 increment
                open_cnt = len(open_events)
                js_btn_enter = '''(() => {
                    const btn = document.querySelector('.req-list .ck:not(.is-demo) .row-act-btn');
                    if (!btn) return false;
                    btn.focus();
                    btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                    btn.click();
                    return true;
                })()'''
                self.assertTrue(run_js(js_btn_enter))
                timer_wait.start(100)
                loop.exec()
                self.assertEqual(len(open_events), open_cnt + 1, '聚焦“查看”按钮按 Enter/激活必须且仅派发 1 次 openRequirement（不得重复触发）')

                # 4) focus .row-act-btn, dispatch Space/native activation => exactly 1 increment
                open_cnt = len(open_events)
                js_btn_space = '''(() => {
                    const btn = document.querySelector('.req-list .ck:not(.is-demo) .row-act-btn');
                    if (!btn) return false;
                    btn.focus();
                    btn.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', code: 'Space', keyCode: 32, which: 32, bubbles: true }));
                    btn.click();
                    return true;
                })()'''
                self.assertTrue(run_js(js_btn_space))
                timer_wait.start(100)
                loop.exec()
                self.assertEqual(len(open_events), open_cnt + 1, '聚焦“查看”按钮按 Space/激活必须且仅派发 1 次 openRequirement（不得重复触发）')

                # F. 示例模式与虚假 ID 防御
                open_count_before = len(open_events)
                bridge.set_summary_provider(
                    lambda: tools.dashboard_summary.build_dashboard_summary(requirements=None, today=today)
                )
                with patch('tools.dashboard_summary.load_requirements', return_value=[]):
                    bridge.push_summary()

                timer_wait.start(250)
                loop.exec()

                js_demo_check = '''(() => {
                    const demoBadges = document.querySelectorAll('.demo-badge');
                    const demoRow = document.querySelector('.req-list .ck.is-demo');
                    let role = null;
                    let tabindex = null;
                    if (demoRow) {
                        role = demoRow.getAttribute('role');
                        tabindex = demoRow.getAttribute('tabindex');
                        demoRow.click();
                        demoRow.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                        demoRow.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', code: 'Space', keyCode: 32, which: 32, bubbles: true }));
                    }
                    return {
                        badgeCount: demoBadges.length,
                        hasDemoRow: !!demoRow,
                        role: role,
                        tabindex: tabindex,
                    };
                })()'''
                demo_res = run_js(js_demo_check)
                self.assertGreaterEqual(demo_res['badgeCount'], 1, '示例模式下必须渲染示例徽章')
                self.assertTrue(demo_res['hasDemoRow'], '示例模式下必须渲染带有 is-demo 的需求行')
                self.assertIsNone(demo_res.get('role'), 'Demo 行不得暴露 role="button"')
                self.assertTrue(demo_res.get('tabindex') is None or int(demo_res.get('tabindex')) < 0, 'Demo 行不得具备键盘可交互 tabindex')
                self.assertEqual(len(open_events), open_count_before, '点击或键盘激活示例需求行绝对不得向 Python 派发虚假 ID')

        finally:
            if widget is not None:
                widget.close()
                widget.setParent(None)
                widget.deleteLater()
            tm.apply(app, old_theme)
            app.processEvents()

    def test_dashboard_real_qwebengine_mainwindow_live_sync(self):
        """测试 2：验证真实 MainWindow + RequirementPanel + 信号自然流转的 Live Sync。"""
        import tools.daily_reports
        import tools.dashboard_summary
        import tools.requirements
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)

        now = datetime.date.today()
        cur_month = now.strftime('%Y-%m')
        next_month = f"{now.year + (1 if now.month == 12 else 0):04d}-{(1 if now.month == 12 else now.month + 1):02d}"

        real_req = {
            'id': 'REQ-PROD-LIVE-1',
            'code': 'REQ-1001',
            'title': '核心交易流水查询优化',
            'system': '核心业务',
            'status': '开发中',
            'actual_release_date': f"{cur_month}-15",
            'actual_online_date': f"{cur_month}-15",
            'online_month': cur_month,
            'test_points': [{'id': '1', 'text': 'tp1', 'done': True}],
        }

        db_records = [dict(real_req)]

        def fake_load():
            return [dict(r) for r in db_records]

        def fake_save(reqs):
            nonlocal db_records
            db_records = [dict(r) for r in reqs]

        win = None
        loop = QEventLoop()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timed_out = [False]
        def on_timeout():
            timed_out[0] = True
            loop.quit()

        timeout_timer.timeout.connect(on_timeout)
        timeout_timer.start(8000)

        import config
        test_settings = dict(config.load_settings())
        test_settings['ui_web_shell'] = True

        try:
            # 显式 patch 所有相关数据源，保证 import 顺序无关性
            with patch('main_window.load_settings', return_value=test_settings), \
                 patch('tools.dashboard_summary.load_requirements', side_effect=fake_load), \
                 patch('tools.dashboard_summary.load_release_board', return_value={}), \
                 patch('tools.daily_reports.load_reports', return_value={}), \
                 patch('tools.requirements.load_requirements', side_effect=fake_load), \
                 patch('tools.requirements.save_requirements', side_effect=fake_save), \
                 patch('panels.requirement_panel.load_requirements', side_effect=fake_load), \
                 patch('panels.requirement_panel.save_requirements', side_effect=fake_save), \
                 patch('panels.requirement_panel.load_systems', return_value=[]):

                # Step B: 真实 MainWindow + RequirementPanel
                win = MainWindow()
                win.show()
                req_panel = win._ensure_requirement_panel()

                self.assertIsNotNone(win._dash_bridge, 'MainWindow 必须实例化 _dash_bridge')
                self.assertIsNotNone(win._dash_web, 'MainWindow 必须实例化 _dash_web')

                ready_pages = []
                def on_ready(p):
                    ready_pages.append(p)
                    if p == 'dashboard':
                        loop.quit()

                win._dash_bridge.pageReadyReceived.connect(on_ready)
                if 'dashboard' in win._web_health.bridge_ready_pages:
                    ready_pages.append('dashboard')
                else:
                    loop.exec()

                if timed_out[0]:
                    self.fail('QWebEngine 未在 8 秒内触发 pageReady，生产链路加载超时')

                timeout_timer.stop()

                def run_js(code):
                    res_box = {}
                    def on_res(r):
                        res_box['val'] = r
                        loop.quit()
                    t_js = QTimer()
                    t_js.setSingleShot(True)
                    t_js.timeout.connect(loop.quit)
                    t_js.start(3000)
                    win._dash_web.web_page.runJavaScript(code, on_res)
                    loop.exec()
                    t_js.stop()
                    return res_box.get('val')

                # Step C: DOM 确认初始当月上线任务数量 = 1
                js_init_count = '''(() => {
                    const tasks = document.querySelectorAll('[data-testid=monthly-release-tasks] .req-list .ck');
                    return tasks.length;
                })()'''
                init_tasks = run_js(js_init_count)
                self.assertEqual(init_tasks, 1, '初始状态本月任务应有 1 项')

                # Step D: 仅调用真实 requirement_panel._move_requirements(..., next_month)
                # 严禁手工调用 bridge.push_summary() 或 MainWindow._push_dashboard_summary()！
                req_panel._move_requirements(['REQ-PROD-LIVE-1'], next_month)

                # Step E & F: 自然 emit requirements_changed，由 MainWindow 真实信号链路自动推送到 Web
                timer_wait = QTimer()
                timer_wait.setSingleShot(True)
                timer_wait.timeout.connect(loop.quit)
                timer_wait.start(350)
                loop.exec()

                # Step G & H: 在同一个已加载的 Vue 页面 DOM 中验证（无 reload，无手工 push）
                js_after_state = f"""(() => {{
                    const tasks = document.querySelectorAll('[data-testid=monthly-release-tasks] .req-list .ck');
                    const dchip = document.querySelector('[data-testid=monthly-release-tasks] .dchip')?.textContent?.trim() || '';
                    const oldCardAbsent = !document.body.textContent.includes('最近需求');
                    return {{
                        count: tasks.length,
                        dchip: dchip,
                        oldCardAbsent: oldCardAbsent
                    }};
                }})()"""
                after_state = run_js(js_after_state)

                self.assertEqual(after_state['count'], 0, '自然信号触发下，DOM 中当月任务列表已自动清零（1 -> 0）')
                self.assertEqual(after_state['dchip'], '0 项', '自然信号触发下，DOM 徽标已自动更新为 0 项')
                self.assertTrue(after_state['oldCardAbsent'], '首页只显示月度任务，不重新引入最近需求卡片')

        finally:
            if win is not None:
                win._force_exit = True
                win.close()
                win.deleteLater()
                app.processEvents()

    def test_dashboard_real_qwebengine_nav_model_authority_over_summary(self):
        """测试 3：验证 navModel 是 Dashboard 常用工具的绝对生产权威（覆盖 summary 冲突与 push_summary 二次覆盖）。"""
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from ui.theme_manager import ThemeManager
        from ui.navigation_model import build_web_nav_model_data

        app = QApplication.instance() or QApplication(sys.argv)
        tm = ThemeManager.instance()

        nav_events = []
        widget = None
        loop = QEventLoop()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timed_out = [False]

        def on_timeout():
            timed_out[0] = True
            loop.quit()

        timeout_timer.timeout.connect(on_timeout)
        timeout_timer.start(8000)

        try:
            bridge = web_shell.HomeBridge()
            bridge.navigateRequested.connect(nav_events.append)

            # 1. summary 故意提供与 navModel 冲突的伪造工具数据
            conflicting_summary = {
                'username': 'Tester',
                'greeting': '你好',
                'date_line': '2026-09-08',
                'stats': {'req_open': 0, 'req_trend': '', 'daily_done': 0, 'daily_total': 5, 'daily_note': ''},
                'release': {
                    'version': 'R1', 'total': 0, 'done': 0, 'percent': 0,
                    'days_left': 1, 'date_text': '2026-09-30', 'countdown_state': 'ok',
                },
                'recent': [],
                'checklist': [],
                'monthly_release_tasks': [],
                'tools': [
                    {'i': 999, 'zh': '冲突假工具999', 'ds': '假描述999', 'icon': 'fake-icon-999'},
                    {'i': 888, 'zh': '冲突假工具888', 'ds': '假描述888', 'icon': 'fake-icon-888'},
                ],
            }
            current_summary = [conflicting_summary]
            bridge.set_summary_provider(lambda: current_summary[0])

            # 2. 注入唯一权威 navModel
            nav_model_data = build_web_nav_model_data(private_unlocked=False, current=0)
            bridge.set_nav_model(nav_model_data)

            # 3. 注入主题
            bridge.set_theme_payload({
                'id': 'calm',
                'is_dark': False,
                'tokens': tm.palette(),
            })

            widget = web_shell.create_dashboard_widget(bridge)
            widget.show()

            ready_pages = []
            bridge.pageReadyReceived.connect(lambda p: (ready_pages.append(p), loop.quit()))
            loop.exec()

            if timed_out[0]:
                self.fail('QWebEngine 未在 8 秒内触发 pageReady，加载超时')

            timeout_timer.stop()
            self.assertIn('dashboard', ready_pages)

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

            # 4. 验证初次渲染：必须无视 summary 中的冲突工具，权威渲染 navModel 派生的 6 个工具
            js_inspect_tools = '''(() => {
                const btns = Array.from(document.querySelectorAll('.quick-btn.tool'));
                return btns.map(b => ({
                    name: b.querySelector('.quick-name')?.textContent?.trim() || '',
                    sub: b.querySelector('.quick-sub')?.textContent?.trim() || '',
                }));
            })()'''

            tools_rendered = run_js(js_inspect_tools) or []
            self.assertEqual(len(tools_rendered), 6, 'DOM 中必须严格渲染 6 个常用工具')

            tool_names = [t['name'] for t in tools_rendered]
            self.assertNotIn('冲突假工具999', tool_names, 'DOM 中绝不能出现 summary 中的冲突假工具 999')
            self.assertNotIn('冲突假工具888', tool_names, 'DOM 中绝不能出现 summary 中的冲突假工具 888')

            self.assertEqual(tool_names[0], '数据中心', '第 1 个工具必须权威展示为数据中心')
            self.assertEqual(tool_names[1], '模型对话', '第 2 个工具必须权威展示为模型对话')
            self.assertIn('接口排查', tool_names)
            self.assertIn('日志排查', tool_names)
            self.assertIn('加解密', tool_names)
            self.assertIn('格式工具', tool_names)

            # 5. 测试交互：点击“数据中心”工具 => Python 必须收到 nav 18
            js_click_dc = '''(() => {
                const tools = Array.from(document.querySelectorAll('.tool'));
                const dc = tools.find(el => el.textContent.includes('数据中心'));
                if (dc) { dc.click(); return true; }
                return false;
            })()'''
            self.assertTrue(run_js(js_click_dc), '页面中必须找到“数据中心”工具')
            self.assertIn(18, nav_events, '点击权威数据中心工具必须向 Python 派发 nav 18')

            # 6. 验证二次推送：调用 bridge.push_summary 注入新一批冲突假数据，DOM 仍必须被 navModel 守卫
            second_conflicting = dict(conflicting_summary)
            second_conflicting['tools'] = [
                {'i': 777, 'zh': '二次冲突假工具777', 'ds': '假描述777', 'icon': 'fake-icon-777'},
            ]
            current_summary[0] = second_conflicting
            bridge.push_summary()

            timer_wait = QTimer()
            timer_wait.setSingleShot(True)
            timer_wait.timeout.connect(loop.quit)
            timer_wait.start(250)
            loop.exec()

            tools_after_push = run_js(js_inspect_tools) or []
            self.assertEqual(len(tools_after_push), 6, 'push_summary 后 DOM 仍必须保持 6 个权威工具')
            names_after_push = [t['name'] for t in tools_after_push]
            self.assertNotIn('二次冲突假工具777', names_after_push, 'push_summary 绝不能以新 tools 覆盖 navModel 权威')
            self.assertEqual(names_after_push[0], '数据中心')
            self.assertEqual(names_after_push[1], '模型对话')

        finally:
            if widget is not None:
                widget.close()
                widget.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
