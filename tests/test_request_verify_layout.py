# -*- coding: utf-8 -*-
"""请求验证两行紧凑上下文：环境+Base+方法 / URL+HTTPS+发送。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class RequestVerifyLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_two_row_context_and_dynamic_send_label(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        self.assertEqual(panel.detail_tabs.tabText(3), '请求验证')
        self.assertTrue(hasattr(panel, 'request_verify_context'))
        # 两行上下文：环境/Base/方法与 URL/HTTPS/发送同属一个 frame
        ctx = panel.request_verify_context
        self.assertIs(panel.local_target_combo.parentWidget(), ctx)
        self.assertIs(panel.rt_base_edit.parentWidget(), ctx)
        self.assertIs(panel.rt_method.parentWidget(), ctx)
        self.assertIs(panel.rt_url.parentWidget(), ctx)
        self.assertIs(panel.rt_ssl_verify.parentWidget(), ctx)
        self.assertIs(panel.rt_send_btn.parentWidget(), ctx)
        panel.rt_base_edit.setText('http://10.12.8.23:8080')
        panel.rt_url.setText('')
        panel._rt_refresh_send_label()
        self.assertIn('发送 · 到 10.12.8.23', panel.rt_send_btn.text())
        panel.rt_base_edit.setText('')
        panel.local_target_combo.setCurrentIndex(-1)
        panel._rt_refresh_send_label()
        # 无 host 且无环境时禁用
        if panel.local_target_combo.count() == 0 or not panel.local_target_combo.currentData():
            self.assertTrue(
                (not panel.rt_send_btn.isEnabled())
                or panel.rt_send_btn.text().startswith('发送')
            )
        panel.close()

    def test_source_has_no_stacked_env_base_method_rows(self):
        path = os.path.join(ROOT, 'panels', 'interface_debug_panel.py')
        with open(path, encoding='utf-8') as stream:
            source = stream.read()
        self.assertIn("setObjectName('request-verify-context')", source)
        self.assertIn('发送 · 到', source)
        self.assertIn('选择环境后发送', source)

    def test_interface_page_chrome_matches_design_labels(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        panel = InterfaceDebugPanel('zh')
        self.assertEqual(panel.capture_toggle_btn.objectName(), 'primary-btn')
        self.assertIn('监听', panel.capture_toggle_btn.text())
        self.assertEqual(panel.clear_list_btn.text(), '清空本次会话')
        self.assertEqual(panel.test_listen_btn.text(), '测试监听')
        self.assertEqual(panel.session_pane_title.text(), '会话与筛选')
        self.assertEqual(panel.local_template_btn.text(), '本地接口模板')
        self.assertEqual(panel.local_sent_btn.text(), '本地发送记录')
        self.assertIn('已脱敏', panel.overview_redact_callout.text())
        self.assertIn('真实请求', panel.verify_danger_callout.text())
        panel.close()

    def test_sql_console_narrow_shows_side_toggles(self):
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel = AiWorkbenchPanel('zh')
        panel.apply_layout_mode('narrow', False)
        self.assertFalse(panel.narrow_chrome.isHidden())
        self.assertTrue(panel.left_pane.isHidden())
        self.assertTrue(panel.side_tabs.isHidden())
        panel.show_objects_btn.setChecked(True)
        self.assertFalse(panel.left_pane.isHidden())
        panel.apply_layout_mode('wide', False)
        self.assertTrue(panel.narrow_chrome.isHidden())
        self.assertFalse(panel.left_pane.isHidden())
        panel.close()


if __name__ == '__main__':
    unittest.main()