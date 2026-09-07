# -*- coding: utf-8 -*-
"""Web Dashboard 真实 QWebEngine 生产渲染与 MainWindow 实时同步集成测试。

严格验证完整生产链与 Live Sync：
RequirementPanel._move_requirements()
  -> requirements_changed (Qt Signal)
  -> MainWindow._push_dashboard_summary() (Qt Slot)
  -> _dash_bridge.push_summary()
  -> summaryChanged (QWebChannel Signal)
  -> 同一个已加载 Vue DOM 响应式重绘（无 reload，无手工 push）
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
    """真实 QWebEngine 与 MainWindow 运行态集成测试（带 <= 10s 硬超时守护）。"""

    @classmethod
    def setUpClass(cls):
        cls.prod_html = os.path.join(ROOT, 'resources', 'webui', 'vue', 'dashboard.html')
        if not os.path.isfile(cls.prod_html):
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - 缺少生产产物 {cls.prod_html}')

        if not web_shell.WEB_SHELL_AVAILABLE:
            raise unittest.SkipTest(f'WEB_REAL_RENDERER: PENDING_ENVIRONMENT - PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}')

    def test_dashboard_real_qwebengine_mainwindow_live_sync(self):
        """验证真实 MainWindow + RequirementPanel + Web Dashboard 生产链路与 Live Sync。"""
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
        from main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)

        # Step A: 构造一条 actual_release_date 在当前月的需求
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
        # 硬超时 8 秒（<= 10s 规范）
        timeout_timer.start(8000)

        try:
            with patch('tools.requirements.load_requirements', side_effect=fake_load), \
                 patch('tools.requirements.save_requirements', side_effect=fake_save), \
                 patch('panels.requirement_panel.load_requirements', side_effect=fake_load), \
                 patch('panels.requirement_panel.save_requirements', side_effect=fake_save), \
                 patch('panels.requirement_panel.load_systems', return_value=[]):

                # Step B: 创建真实 MainWindow，ensure RequirementPanel，等待真实 Web Dashboard pageReady
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
                    const tasks = document.querySelectorAll('.card:nth-child(2) .req-list .ck');
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
                    const tasks = document.querySelectorAll('.card:nth-child(2) .req-list .ck');
                    const dchip = document.querySelector('.card:nth-child(2) .dchip')?.textContent?.trim() || '';
                    const recentText = document.querySelector('.card:nth-child(1) .req-list .ck')?.textContent || '';
                    return {{
                        count: tasks.length,
                        dchip: dchip,
                        hasNewDate: recentText.includes('{next_month}')
                    }};
                }})()"""
                after_state = run_js(js_after_state)

                # 本月数量 1 -> 0
                self.assertEqual(after_state['count'], 0, '自然信号触发下，DOM 中当月任务列表已自动清零（1 -> 0）')
                self.assertEqual(after_state['dchip'], '0 项', '自然信号触发下，DOM 徽标已自动更新为 0 项')
                # 最近需求 actual_release_date 变为下月日期
                self.assertTrue(after_state['hasNewDate'], f'自然信号触发下，最近需求已自动同步为下月日期: {next_month}')

        finally:
            if win is not None:
                win._force_exit = True
                win.close()
                win.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
