# -*- coding: utf-8 -*-
"""Targeted acceptance verification tests for Round 2 corrections."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from config import DEFAULT_SETTINGS
from panels.agent_workbench_panel import AgentWorkbenchPanel
from panels.interface_debug_panel import InterfaceDebugPanel
from panels.settings_panel import SettingsPanel
from tools.intranet_llm import DEFAULT_AI_LOCAL, normalize_ai_local
from ui.field_metrics import CompactStepper
from ui.quick_panel import QuickPanel
from ui.ssh_terminal import SshTerminalWidget


class _MainWindowStub:
    def __init__(self):
        self.navigated_to = []

    def showNormal(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def navigate_to(self, index: int):
        self.navigated_to.append(index)


class AcceptanceCorrectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # QtWebEngineWidgets 必须在 QApplication 之前 import，否则 ui.web_shell
        # 会永久落到 WEB_SHELL_AVAILABLE=False 的 stub HomeBridge。
        try:
            import ui.web_shell  # noqa: F401
        except Exception:
            pass
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_quick_panel_chat_mode_and_navigation(self):
        win = _MainWindowStub()
        panel = QuickPanel(win)
        try:
            panel.toggle_expanded()
            self.assertEqual(panel._mode, 'tools')
            self.assertTrue(panel.grid_host.isVisible())
            self.assertTrue(panel.chat_container.isHidden())

            # Switch to chat mode
            panel._set_mode('chat')
            self.assertEqual(panel._mode, 'chat')
            self.assertTrue(panel.chat_container.isVisible())
            self.assertTrue(panel.grid_host.isHidden())

            # Test Open Full Model Chat button triggers navigation index 16
            panel.open_full_chat_btn.click()
            self.assertIn(16, win.navigated_to)

            # Test chat send/stop cycle
            panel.chat_input.setText('你好')
            dummy_cfg = {'name': 'TestModel', 'model': 'test', 'enabled': True, 'base_url': 'http://127.0.0.1:8000/v1'}
            panel.chat_model_combo.addItem('TestModel', dummy_cfg)
            panel.chat_model_combo.setCurrentIndex(panel.chat_model_combo.count() - 1)

            with patch('ui.quick_panel._QuickChatWorker.start'):
                panel._on_chat_send_or_stop()
                self.assertIsNotNone(panel._chat_worker)
                self.assertEqual(panel.chat_send_btn.text(), '停止')
                panel._on_chat_completed('测试回复')
                self.assertEqual(panel.chat_send_btn.text(), '发送')
                self.assertIn('测试回复', panel.chat_history.toPlainText())

            # Clear chat
            panel._clear_chat()
            self.assertEqual(panel.chat_history.toPlainText(), '')
            self.assertEqual(len(panel._chat_messages), 0)
        finally:
            panel.close()

    def test_supports_vision_persistence_and_normalization(self):
        # Default has supports_vision = False
        self.assertIn('supports_vision', DEFAULT_AI_LOCAL)
        self.assertFalse(DEFAULT_AI_LOCAL['supports_vision'])

        normalized = normalize_ai_local({'name': 'VLM', 'supports_vision': True})
        self.assertTrue(normalized['supports_vision'])

        normalized_default = normalize_ai_local({'name': 'Standard'})
        self.assertFalse(normalized_default['supports_vision'])

        # Settings panel UI binding
        page = SettingsPanel(DEFAULT_SETTINGS)
        try:
            self.assertTrue(hasattr(page, 'ai_supports_vision'))
            page.ai_supports_vision.setChecked(True)
            cfg = page._ai_cfg_from_ui()
            self.assertTrue(cfg.get('supports_vision'))

            page.ai_supports_vision.setChecked(False)
            cfg2 = page._ai_cfg_from_ui()
            self.assertFalse(cfg2.get('supports_vision'))
        finally:
            page.close()

        # Real disk save / load roundtrip with temp path
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            temp_path = f.name
        try:
            with patch('tools.intranet_llm.AI_LOCAL_FILE', temp_path):
                from tools.intranet_llm import save_model_catalog, load_model_catalog, upsert_model_item
                initial_catalog = {
                    'version': 2,
                    'active_model_id': 'm_vision',
                    'items': [{
                        'id': 'm_vision',
                        'name': 'VisionModel',
                        'model': 'qwen-vl',
                        'base_url': 'http://127.0.0.1:8000/v1',
                        'enabled': True,
                        'supports_vision': True,
                    }],
                }
                save_model_catalog(initial_catalog)
                loaded = load_model_catalog()
                self.assertEqual(len(loaded.get('items', [])), 1)
                self.assertTrue(loaded['items'][0]['supports_vision'])

                # Update with supports_vision = False
                updated_item = dict(loaded['items'][0], supports_vision=False)
                upsert_model_item(updated_item)
                reloaded = load_model_catalog()
                self.assertFalse(reloaded['items'][0]['supports_vision'])
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_homepage_bridge_navigate_requested_changes_panel(self):
        from main_window import MainWindow
        settings = dict(DEFAULT_SETTINGS, ui_web_shell=True)
        dummy_widget = QWidget()
        dummy_widget.web_view = MagicMock()
        dummy_widget.web_page = MagicMock()
        dummy_widget.web_name = 'dashboard'
        # 该用例只验证 HomeBridge.navigate；真实 QWebEngineView 在 offscreen 下会 native crash。
        with patch('ui.web_shell.create_dashboard_widget', return_value=dummy_widget), \
             patch('ui.web_shell.create_chrome_widget', return_value=dummy_widget), \
             patch('ui.web_shell.runtime_web_shell_available', return_value=True), \
             patch('main_window.load_settings', return_value=settings):
            win = MainWindow()
        try:
            self.assertIsNotNone(win._dash_bridge)
            # Direct slot call test: bridge.navigate(index)
            win._dash_bridge.navigate(9)
            self.assertEqual(win._current_nav_index, 9)

            win._dash_bridge.navigate(10)
            self.assertEqual(win._current_nav_index, 10)

            win._dash_bridge.navigate(1)
            self.assertEqual(win._current_nav_index, 1)

            win._dash_bridge.navigate(0)
            self.assertEqual(win._current_nav_index, 0)
        finally:
            if win.hotkey_service: win.hotkey_service.unregister()
            if win.quick_panel: win.quick_panel.close_toolbar()
            if win.tray_service: win.tray_service.hide()
            if win.keep_awake_service: win.keep_awake_service.stop()
            win.hide()
            win.deleteLater()

    def test_all_main_modules_have_return_home_action(self):
        from ui.page_chrome import make_page_header
        from main_window import MainWindow
        # 1. make_page_header default creates home_btn
        header, title_lbl, sub_lbl = make_page_header('测试页面', '测试副标题')
        self.assertTrue(hasattr(header, 'home_btn'))
        self.assertEqual(header.home_btn.text(), '返回首页')
        self.assertEqual(header.home_btn.toolTip(), '返回首页')

        # 2. show_home=False omits home_btn
        header_dash, _, _ = make_page_header('工作台', '副标题', show_home=False)
        self.assertFalse(hasattr(header_dash, 'home_btn'))

        # 3. Test Home button exists and works across all primary panels
        settings = dict(DEFAULT_SETTINGS, ui_web_shell=False)
        with patch('main_window.load_settings', return_value=settings):
            win = MainWindow()
        try:
            # Modules to test: Requirement(10), ModelChat(16), Workbench(17), InterfaceDebug(12),
            # OpsLog(13), Gateway(5), Format(11), Credit(1), Oracle(18), Redis(22)
            for nav_idx in (10, 16, 17, 12, 13, 5, 11, 1, 18, 22):
                win.navigate_to(nav_idx)
                self.assertEqual(win._current_nav_index, nav_idx)
                panel = win.stack.currentWidget()
                home_buttons = panel.findChildren(QPushButton, 'header-home-btn')
                self.assertGreater(len(home_buttons), 0, f"Module nav={nav_idx} missing header-home-btn")
                # Click home button
                home_buttons[0].click()
                self.assertEqual(win._current_nav_index, 0, f"Module nav={nav_idx} home button did not return to 0")

            # 4. Verify language toggle updates header-home-btn
            win._set_language(1)  # Switch to English
            self.assertEqual(win.language, 'en')
            win.navigate_to(10)
            panel = win.stack.currentWidget()
            home_buttons = panel.findChildren(QPushButton, 'header-home-btn')
            self.assertEqual(home_buttons[0].text(), 'Home')

            win._set_language(0)  # Switch to Chinese
            self.assertEqual(win.language, 'zh')
            win.navigate_to(10)
            panel = win.stack.currentWidget()
            home_buttons = panel.findChildren(QPushButton, 'header-home-btn')
            self.assertEqual(home_buttons[0].text(), '返回首页')

            # 5. Dashboard itself has no home button
            win.navigate_to(0)
            dash_panel = win.dashboard_panel
            dash_home_btns = dash_panel.findChildren(QPushButton, 'header-home-btn')
            self.assertEqual(len(dash_home_btns), 0, "Dashboard panel must not have a return home button")
        finally:
            if win.hotkey_service: win.hotkey_service.unregister()
            if win.quick_panel: win.quick_panel.close_toolbar()
            if win.tray_service: win.tray_service.hide()
            if win.keep_awake_service: win.keep_awake_service.stop()
            win.hide()
            win.deleteLater()

    def test_requirement_editable_and_readonly_field_properties(self):
        from panels.requirement_panel import RequirementPanel, DateInput
        panel = RequirementPanel()
        try:
            self.assertTrue(panel.search_edit.property('editableField'))
            self.assertTrue(panel.file_search_edit.property('editableField'))
            self.assertTrue(panel.sql_preview.property('readOnlyField'))

            date_input = DateInput()
            self.assertTrue(date_input.edit.property('editableField'))
        finally:
            panel.deleteLater()

    def test_quick_panel_icon_audit_and_workbench_role(self):
        from ui.navigation_model import floating_candidates, icon_role_for
        from ui.icons import icon_file, qicon

        candidates = floating_candidates()
        self.assertGreater(len(candidates), 0)
        for item in candidates:
            role = icon_role_for(item.index)
            path = icon_file(role)
            self.assertTrue(os.path.exists(path), f"Missing icon SVG file for role '{role}' (nav index {item.index})")
            icon = qicon(role)
            self.assertFalse(icon.isNull(), f"qicon for role '{role}' returned null")

        # Specific check for Agent Workbench (index 17) -> workbench
        self.assertEqual(icon_role_for(17), 'workbench')
        self.assertTrue(os.path.exists(icon_file('workbench')))
        self.assertFalse(qicon('workbench').isNull())

    def test_agent_workbench_file_attachment_refs(self):
        panel = AgentWorkbenchPanel()
        try:
            panel.show()
            self.app.processEvents()
            with tempfile.TemporaryDirectory() as tmpdir:
                # Mock workspace session bound to tmpdir
                file_a = os.path.join(tmpdir, 'code.py')
                with open(file_a, 'w', encoding='utf-8') as f:
                    f.write('print("hello")')

                panel._workspace_session = {'id': 'test-ws', 'workspace_dir': tmpdir, 'conversations': [{'id': 'c1', 'messages': []}]}
                panel.dir_label.setText(tmpdir)

                with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileNames', return_value=([file_a], 'All Files (*)')):
                    panel._pick_workspace_file_ref()

                self.assertIn('code.py', panel._file_attachments)
                self.assertTrue(panel.attachment_bar.isVisible())
                self.assertIn('code.py', panel.attachment_bar.text())

                # Typing user prompt
                panel.input.setPlainText('请分析该文件')

                # Clearing attachment should not clear prompt text
                panel._clear_attachments()
                self.assertEqual(len(panel._file_attachments), 0)
                self.assertTrue(panel.attachment_bar.isHidden())
                self.assertEqual(panel.input.toPlainText(), '请分析该文件')

                # Re-add and send: attachment references should be appended into message
                with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileNames', return_value=([file_a], 'All Files (*)')):
                    panel._pick_workspace_file_ref()

                with patch.object(panel, '_current_model', return_value={'enabled': True, 'base_url': 'http://test'}):
                    with patch('panels.agent_workbench_panel._WorkbenchWorker') as mock_worker_cls:
                        mock_worker = MagicMock()
                        mock_worker_cls.return_value = mock_worker
                        panel._send()
                        # Verify the text sent to worker contains both prompt and file ref
                        sent_prompt = mock_worker_cls.call_args[1]['user_message']
                        self.assertIn('请分析该文件', sent_prompt)
                        self.assertIn('code.py', sent_prompt)
        finally:
            panel.close()

    def test_interface_debug_button_visibility(self):
        panel = InterfaceDebugPanel()
        try:
            panel.show()
            panel.resize(1000, 700)
            panel.detail_tabs.setCurrentIndex(3)
            panel._update_responsive_workspace(left_width=500, right_width=600)
            self.app.processEvents()

            # Direct actions must be visible
            self.assertTrue(panel.rt_send_btn.isVisible())
            self.assertTrue(panel.rt_fill_btn.isVisible())
            self.assertTrue(panel.rt_save_api_btn.isVisible())

            # Secondary actions must be tucked in more menu
            self.assertTrue(panel.rt_form_more_btn.isVisible())
            self.assertTrue(panel.rt_io_more_btn.isVisible())
        finally:
            panel.close()

    def test_compact_stepper_and_terminal_metrics(self):
        stepper = CompactStepper(minimum=0, maximum=100, value=10, suffix='ms')
        try:
            self.assertEqual(stepper.value(), 10)
            self.assertEqual(stepper.suffix_label.text(), 'ms')
            stepper.plus_btn.click()
            self.assertEqual(stepper.value(), 11)
            stepper.minus_btn.click()
            self.assertEqual(stepper.value(), 10)
        finally:
            stepper.close()

        term = SshTerminalWidget()
        try:
            cw, lh = term.view._cell_dimensions()
            self.assertGreater(cw, 0)
            self.assertGreater(lh, 0)
        finally:
            term.close()

    def test_model_chat_and_theme_contracts(self):
        from panels.model_chat_panel import ModelChatPanel
        from ui.theme_manager import ThemeManager

        panel = ModelChatPanel()
        try:
            # Model Chat has no DB selector dropdown
            self.assertFalse(hasattr(panel, 'db_combo'))
            self.assertFalse(hasattr(panel, 'dialect_combo'))
            # Model Chat idle/running button text contract
            self.assertEqual(panel.send_btn.text(), '发送')
            panel._is_running = True
            panel._sync_running_state()
            self.assertEqual(panel.send_btn.text(), '停止')
            panel._is_running = False
            panel._sync_running_state()
            self.assertEqual(panel.send_btn.text(), '发送')
        finally:
            panel.deleteLater()

        # Theme Manager black theme tokens contract
        tm = ThemeManager.instance()
        black_bg = tm.token('APP_BG', theme_id='black')
        self.assertEqual(black_bg, '#09090B')
        black_surface = tm.token('SURFACE', theme_id='black')
        self.assertEqual(black_surface, '#161618')


if __name__ == '__main__':
    unittest.main()
