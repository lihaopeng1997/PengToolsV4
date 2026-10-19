# -*- coding: utf-8 -*-
"""Targeted unit tests for Agent Workbench & Model Chat layout improvements."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget


class AgentWorkbenchLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='agent_wb_test_')
        agent_data = os.path.join(self.tmp_dir, 'agent-data')
        os.makedirs(agent_data, exist_ok=True)
        self._network_guard_hits = 0
        self._catalog_guard_hits = 0

        def deny_network(*_args, **_kwargs):
            self._network_guard_hits += 1
            raise AssertionError('Network access is disabled in this targeted test.')

        def deny_catalog(*_args, **_kwargs):
            self._catalog_guard_hits += 1
            raise AssertionError('The real model catalog reader is disabled in this targeted test.')

        self._patchers = [
            patch('tools.agent_store.AGENT_DIR', agent_data),
            patch('tools.agent_store.AGENT_WORKSPACES_DIR', os.path.join(agent_data, 'workspaces')),
            patch('tools.agent_store.AGENT_INDEX_FILE', os.path.join(agent_data, 'index.json')),
            patch('tools.agent_store.ensure_config_dir', lambda: None),
            patch('config.load_settings', return_value={}),
            patch('config.save_settings', return_value={}),
            patch('config.load_layout_splitter', return_value=None),
            patch('config.save_layout_splitter', return_value=None),
            patch('panels.agent_workbench_panel.load_settings', return_value={}),
            patch('panels.agent_workbench_panel.list_enabled_items', return_value=[]),
            patch('tools.intranet_llm.list_enabled_items', side_effect=deny_catalog),
            patch('panels.model_chat_panel.list_enabled_items', return_value=[]),
            patch('socket.create_connection', side_effect=deny_network),
            patch('http.client.HTTPConnection.connect', side_effect=deny_network),
            patch('http.client.HTTPSConnection.connect', side_effect=deny_network),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for patcher in reversed(self._patchers):
            patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.assertEqual(self._catalog_guard_hits, 0, 'A real catalog reader was reached.')
        self.assertEqual(self._network_guard_hits, 0, 'A network connection was attempted.')

    def test_agent_layout_architecture(self):
        """测试 Agent 工作台信息架构：主区对话居中、左栏仅空间列表、右侧为可折叠 Context 面板。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        panel = AgentWorkbenchPanel()
        try:
            # 1. 左栏不包含 project_tree
            left_frame = panel.space_tree.parentWidget()
            self.assertIsNotNone(left_frame)
            self.assertFalse(panel.project_tree.isAncestorOf(left_frame))
            self.assertNotEqual(panel.project_tree.parentWidget(), left_frame)

            # 2. 右侧 Context 面板包含 project_tree 和 preview
            self.assertTrue(hasattr(panel, 'context_panel'))
            self.assertTrue(hasattr(panel, 'context_toggle_btn'))
            self.assertTrue(panel.project_tree.isAncestorOf(panel.context_panel) or panel.context_panel.isAncestorOf(panel.project_tree))
            self.assertTrue(panel.preview.isAncestorOf(panel.context_panel) or panel.context_panel.isAncestorOf(panel.preview))

            # 3. Context 面板可折叠切换
            self.assertFalse(panel.context_panel.isHidden())
            panel.context_toggle_btn.click()
            self.assertTrue(panel.context_panel.isHidden())
            panel.context_toggle_btn.click()
            self.assertFalse(panel.context_panel.isHidden())

            # 4. 中央对话流和输入区为垂直 Splitter，且不可拖拽至 0
            self.assertTrue(hasattr(panel, 'center_split'))
            self.assertEqual(panel.center_split.orientation(), Qt.Orientation.Vertical)
            self.assertFalse(panel.center_split.childrenCollapsible())

            # 5. 输入框无 300px 硬性最大高度限制
            self.assertGreaterEqual(panel.input.minimumHeight(), 100)
            self.assertGreater(panel.input.maximumHeight(), 500)
        finally:
            panel.deleteLater()

    def test_project_tree_recursive_lazy_loading_and_preview(self):
        """测试项目文件树多层级递归展开、懒加载与文件预览。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        # 构造测试目录：
        # ws/
        #   src/
        #     api/
        #       client.py
        #     app.py
        #   readme.md
        ws_path = os.path.join(self.tmp_dir, 'my_project')
        api_path = os.path.join(ws_path, 'src', 'api')
        os.makedirs(api_path, exist_ok=True)

        client_py = os.path.join(api_path, 'client.py')
        with open(client_py, 'w', encoding='utf-8') as f:
            f.write("def get_client(): return 'ok'")

        app_py = os.path.join(ws_path, 'src', 'app.py')
        with open(app_py, 'w', encoding='utf-8') as f:
            f.write("# app")

        readme_md = os.path.join(ws_path, 'readme.md')
        with open(readme_md, 'w', encoding='utf-8') as f:
            f.write("# Readme")

        panel = AgentWorkbenchPanel()
        try:
            panel._workspace_session = {'id': 'test_ws', 'workspace_dir': ws_path}
            panel._refresh_tree(ws_path)
            self.assertEqual(panel.project_tree.topLevelItemCount(), 1)
            root_item = panel.project_tree.topLevelItem(0)
            self.assertTrue(root_item.text(0).startswith(os.path.basename(ws_path)))

            # 首层子节点：src/ 和 readme.md
            child_texts = [root_item.child(i).text(0) for i in range(root_item.childCount())]
            self.assertIn('src/', child_texts)
            self.assertIn('readme.md', child_texts)

            src_item = next(root_item.child(i) for i in range(root_item.childCount()) if root_item.child(i).text(0) == 'src/')
            # 初始状态下 src/ 有一个未加载占位符
            self.assertEqual(src_item.childCount(), 1)
            self.assertEqual(src_item.child(0).data(0, Qt.ItemDataRole.UserRole), '')

            # 触发展开 src/ -> 懒加载子目录
            src_item.setExpanded(True)
            panel._on_tree_item_expanded(src_item)
            src_child_texts = [src_item.child(i).text(0) for i in range(src_item.childCount())]
            self.assertIn('api/', src_child_texts)
            self.assertIn('app.py', src_child_texts)

            # 展开 api/ -> 懒加载 client.py
            api_item = next(src_item.child(i) for i in range(src_item.childCount()) if src_item.child(i).text(0) == 'api/')
            api_item.setExpanded(True)
            panel._on_tree_item_expanded(api_item)
            api_child_texts = [api_item.child(i).text(0) for i in range(api_item.childCount())]
            self.assertIn('client.py', api_child_texts)

            # 双击 client.py -> 在 preview 中显示内容
            client_item = next(api_item.child(i) for i in range(api_item.childCount()) if api_item.child(i).text(0) == 'client.py')
            panel._on_tree_double_click(client_item, 0)
            self.assertIn("def get_client(): return 'ok'", panel.preview.toPlainText())

            # 双击目录 -> 切换展开状态，不影响 preview 内容
            self.assertTrue(src_item.isExpanded())
            panel._on_tree_double_click(src_item, 0)
            self.assertFalse(src_item.isExpanded())
            panel._on_tree_double_click(src_item, 0)
            self.assertTrue(src_item.isExpanded())
        finally:
            panel.deleteLater()

    def test_plan_mode_and_execution_mode_mapping(self):
        """测试执行方式下拉框正确映射 plan_confirm 字段与持久化。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from tools.agent_store import empty_workspace, load_workspace, save_workspace

        ws = empty_workspace(title='测试工作区')
        ws['plan_confirm'] = True
        save_workspace(ws)

        panel = AgentWorkbenchPanel()
        try:
            panel._select_workspace(ws['id'])
            # 验证选中的是“执行前确认计划” (True, index 1)
            self.assertEqual(panel.exec_mode_combo.currentIndex(), 1)
            self.assertEqual(panel.exec_mode_combo.currentData(), True)
            self.assertTrue(panel._plan_confirm)

            # 切换为“直接执行” (False, index 0)
            panel.exec_mode_combo.setCurrentIndex(0)
            self.assertFalse(panel._plan_confirm)
            loaded = load_workspace(ws['id'])
            self.assertFalse(loaded['plan_confirm'])
        finally:
            panel.deleteLater()

    def test_model_chat_composer_and_bubble_sizing(self):
        """测试模型对话面板使用垂直 Splitter，无固定 300px 输入框限制。"""
        from panels.model_chat_panel import ModelChatPanel

        panel = ModelChatPanel()
        try:
            self.assertTrue(hasattr(panel, 'chat_vsplit'))
            self.assertEqual(panel.chat_vsplit.orientation(), Qt.Orientation.Vertical)
            self.assertFalse(panel.chat_vsplit.childrenCollapsible())
            self.assertGreaterEqual(panel.input.minimumHeight(), 100)
            self.assertGreater(panel.input.maximumHeight(), 500)
        finally:
            panel.deleteLater()

    def test_workspace_boundary_containment_helper(self):
        """测试工作区路径包含判定：严格限制真实物理路径在工作区内，防前缀碰撞与跨盘符逃逸。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        panel = AgentWorkbenchPanel()
        try:
            ws = os.path.join(self.tmp_dir, 'workspace')
            os.makedirs(ws, exist_ok=True)
            safe_file = os.path.join(ws, 'src', 'safe.py')

            # 正常内部路径
            self.assertTrue(panel._is_path_within_workspace(safe_file, ws))
            self.assertTrue(panel._is_path_within_workspace(ws, ws))

            # 字符串前缀碰撞（如 workspace2/a.py 不得误判为在 workspace 内）
            ws2_file = os.path.join(self.tmp_dir, 'workspace2', 'a.py')
            self.assertFalse(panel._is_path_within_workspace(ws2_file, ws))

            # 相对路径越界逃逸
            escape_file = os.path.join(ws, '..', 'outside.py')
            self.assertFalse(panel._is_path_within_workspace(escape_file, ws))

            # 跨盘符路径
            cross_drive = 'C:\\forbidden\\secret.txt' if ws[0].upper() != 'C' else 'Z:\\forbidden\\secret.txt'
            self.assertFalse(panel._is_path_within_workspace(cross_drive, ws))

            # 空路径或未绑定
            self.assertFalse(panel._is_path_within_workspace('', ws))
            self.assertFalse(panel._is_path_within_workspace(safe_file, ''))
        finally:
            panel.deleteLater()

    def test_workspace_boundary_refuses_outside_file_preview_and_symlink(self):
        """测试双击外部路径或 symlink/junction 目标在外部时，文件读取被拦截且不泄露机密。"""
        from PyQt6.QtWidgets import QTreeWidgetItem
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        ws = os.path.join(self.tmp_dir, 'ws_bound')
        outside = os.path.join(self.tmp_dir, 'outside_bound')
        os.makedirs(os.path.join(ws, 'src'), exist_ok=True)
        os.makedirs(outside, exist_ok=True)

        safe_path = os.path.join(ws, 'src', 'safe.py')
        with open(safe_path, 'w', encoding='utf-8') as f:
            f.write("SAFE_TOKEN = 'ALLOW'")

        secret_path = os.path.join(outside, 'secret.txt')
        with open(secret_path, 'w', encoding='utf-8') as f:
            f.write("TOP_SECRET_PASSWORD_12345")

        panel = AgentWorkbenchPanel()
        try:
            panel._workspace_session = {'id': 'test_ws', 'workspace_dir': ws}

            # 1. 安全内部文件：正常预览
            safe_item = QTreeWidgetItem()
            safe_item.setData(0, Qt.ItemDataRole.UserRole, safe_path)
            panel._on_tree_double_click(safe_item, 0)
            self.assertIn("SAFE_TOKEN = 'ALLOW'", panel.preview.toPlainText())

            # 2. 外部机密文件：拒绝读取，不泄露内容
            secret_item = QTreeWidgetItem()
            secret_item.setData(0, Qt.ItemDataRole.UserRole, secret_path)
            panel._on_tree_double_click(secret_item, 0)
            self.assertNotIn("TOP_SECRET_PASSWORD_12345", panel.preview.toPlainText())
            self.assertIn("该路径超出当前工作区，已拒绝读取。", panel.preview.toPlainText())

            # 3. 尝试在工作区内建立指向外部的 symlink/junction（若系统权限支持）
            symlink_dir = os.path.join(ws, 'linked_outside')
            try:
                os.symlink(outside, symlink_dir, target_is_directory=True)
                has_symlink = True
            except (OSError, NotImplementedError):
                has_symlink = False

            if has_symlink:
                # 验证目录树加载时自动忽略指向工作区外的 symlink
                root_item = QTreeWidgetItem()
                root_item.setData(0, Qt.ItemDataRole.UserRole, ws)
                panel._populate_dir_item(root_item, ws)
                child_texts = [root_item.child(i).text(0) for i in range(root_item.childCount())]
                self.assertNotIn('linked_outside/', child_texts)
                self.assertNotIn('linked_outside', child_texts)
        finally:
            panel.deleteLater()

    def test_workspace_action_labels(self):
        """测试工作区顶部操作按钮和侧边栏标题文本规范统一。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        panel = AgentWorkbenchPanel(language='zh')
        try:
            self.assertEqual(panel.new_btn.text(), '新建工作区')
            self.assertEqual(panel.space_title.text(), '工作区')
            self.assertEqual(panel.space_new_btn.toolTip(), '新建对话')

            panel.set_language('en')
            self.assertEqual(panel.new_btn.text(), 'New workspace')
            self.assertEqual(panel.space_title.text(), 'Workspaces')
            self.assertEqual(panel.space_new_btn.toolTip(), 'New conversation')
        finally:
            panel.deleteLater()

    def test_bound_workspace_path_does_not_force_wide_panel(self):
        """A long bound path and large bilingual controls fit the narrow panel bounds."""
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from tools.agent_store import empty_workspace, save_workspace

        project_name = 'long-project-name-' * 5
        workspace_dir = os.path.join(self.tmp_dir, project_name)
        os.makedirs(workspace_dir, exist_ok=True)
        prior_font = self.app.font()
        large_font = QFont(prior_font)
        large_font.setPointSize(13)
        self.app.setFont(large_font)
        panel = AgentWorkbenchPanel('en')
        workspace = empty_workspace(title='Narrow layout', workspace_dir=workspace_dir)
        save_workspace(workspace)
        try:
            panel._select_workspace(workspace['id'])
            panel.resize(960, 720)
            panel.show()
            self.app.processEvents()

            self.assertTrue(panel.dir_label.wordWrap())
            self.assertLessEqual(panel.dir_label.maximumWidth(), 240)
            self.assertEqual(panel.dir_label.toolTip(), f'Bound: {os.path.normpath(workspace_dir)}')
            self.assertIn('…', panel.dir_label.text())
            self.assertNotIn(workspace_dir, panel.dir_label.text())
            self.assertTrue(panel.dir_label.text().endswith(project_name[-14:]))
            if self.app.platformName() == 'windows':
                self.assertLessEqual(panel.width(), 960)
            for language in ('zh', 'en'):
                panel.set_language(language)
                self.app.processEvents()
                mode_text_width = max(
                    QFontMetrics(panel.exec_mode_combo.font()).horizontalAdvance(
                        panel.exec_mode_combo.itemText(index)
                    )
                    for index in range(panel.exec_mode_combo.count())
                )
                self.assertGreaterEqual(
                    panel.exec_mode_combo.width(), mode_text_width + 44,
                    f'{language} execution mode option is clipped',
                )
                if language == 'zh':
                    self.assertEqual(
                        panel.dir_label.toolTip(), f'已绑定：{os.path.normpath(workspace_dir)}',
                    )
                else:
                    self.assertEqual(
                        panel.dir_label.toolTip(), f'Bound: {os.path.normpath(workspace_dir)}',
                    )
                self.assertLessEqual(
                    panel.input.geometry().bottom(), panel.send_hint.geometry().top(),
                    f'{language} composer hint overlaps the input area',
                )
                for button in (panel.bind_dir_btn, panel.new_btn, panel.context_toggle_btn,
                               panel.add_attachment_btn, panel.send_btn):
                    self.assertGreaterEqual(
                        button.width(), button.sizeHint().width(),
                        f'{language} button text is clipped: {button.text()}',
                    )
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()
            self.app.setFont(prior_font)

    def test_context_and_conflict_viewers_fit_embedded_sizes_and_remain_read_only(self):
        from PyQt6.QtCore import QRect
        from tools.agent_store import empty_workspace, save_workspace
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        evidence_dir = os.environ.get('AGENT_CONTEXT_UI_EVIDENCE_DIR')
        screenshot_saved = False
        prior_font = QFont(self.app.font())
        project_dir = os.path.join(self.tmp_dir, 'context-ui-project')
        os.makedirs(project_dir, exist_ok=True)
        long_history = '\n'.join(f'Archive line {index} ' + ('read only history ' * 8) for index in range(500))
        budget_state = {
            'schema': 1,
            'status': 'ready',
            'estimate_kind': 'heuristic_utf8_bytes',
            'input_estimate': 42_000,
            'context_window_known': False,
            'output_reserve': 4096,
            'messages_kept': 12,
            'messages_dropped': 4,
            'reason': 'trimmed_to_application_budget_window_unknown',
        }

        try:
            for font_px in (17, 21):
                font = QFont(prior_font)
                font.setPixelSize(font_px)
                self.app.setFont(font)
                for width, height in ((960, 640), (1320, 860)):
                    for language in ('zh', 'en'):
                        container = QWidget()
                        container.resize(width, height)
                        outer = QVBoxLayout(container)
                        outer.setContentsMargins(0, 0, 0, 0)
                        panel = AgentWorkbenchPanel(language)
                        outer.addWidget(panel)
                        workspace = empty_workspace(
                            title=f'UI evidence {font_px}-{width}-{language}',
                            workspace_dir=project_dir,
                        )
                        workspace['conflicted_runs'] = [{
                            'messages': [
                                {'role': 'assistant', 'content': long_history},
                                {'role': 'assistant', 'reasoning_content': 'private field must not render',
                                 'content': 'not shown'},
                            ],
                            'tool_calls': [{'name': 'never replay', 'arguments': {'secret': 'hidden'}}],
                        }]
                        save_workspace(workspace)
                        try:
                            panel._select_workspace(workspace['id'])
                            panel._show_context_budget_state(budget_state)
                            container.show()
                            panel.show()
                            self.app.processEvents()
                            self.assertEqual(panel.conflicts_btn.isEnabled(), True)
                            self.assertTrue(
                                panel.conflicts_btn.text().startswith('冲突 ' if language == 'zh' else 'Conflicts ')
                            )
                            self.assertIn('启发式' if language == 'zh' else 'local approximate plan',
                                          panel.context_budget_label.toolTip())
                            self.assertLessEqual(panel.context_budget_label.height(),
                                                 panel.context_budget_label.sizeHint().height() + 2)

                            viewer = panel._open_conflict_viewer()
                            self.app.processEvents()
                            self.assertFalse(viewer.isModal())
                            text_widget = viewer.findChild(QPlainTextEdit, 'agent-conflict-viewer-text')
                            self.assertTrue(text_widget.isReadOnly())
                            self.assertGreater(text_widget.verticalScrollBar().maximum(), 0)
                            self.assertTrue('Archive line 499' in text_widget.toPlainText())
                            self.assertNotIn('private field must not render', text_widget.toPlainText())
                            self.assertNotIn('never replay', text_widget.toPlainText())

                            screen = panel.screen() or self.app.primaryScreen()
                            parent_rect = QRect(
                                panel.mapToGlobal(panel.contentsRect().topLeft()),
                                panel.contentsRect().size(),
                            )
                            self.assertTrue(parent_rect.contains(viewer.frameGeometry()),
                                            f'{width}x{height}, {font_px}px, {language}: outside panel')
                            if screen is not None:
                                self.assertTrue(screen.availableGeometry().contains(viewer.frameGeometry()),
                                                f'{width}x{height}, {font_px}px, {language}: outside screen')

                            if (not screenshot_saved and evidence_dir
                                    and font_px == 17 and width == 960 and language == 'en'):
                                panel_image = container.grab().toImage()
                                dialog_image = viewer.grab().toImage()
                                composite = QImage(
                                    panel_image.width() + dialog_image.width() + 16,
                                    max(panel_image.height(), dialog_image.height()),
                                    QImage.Format.Format_ARGB32,
                                )
                                composite.fill(self.app.palette().window().color())
                                painter = QPainter(composite)
                                painter.drawImage(0, 0, panel_image)
                                painter.drawImage(panel_image.width() + 16, 0, dialog_image)
                                painter.end()
                                screenshot_path = os.path.join(evidence_dir, 'synthetic-context-conflict-ui.png')
                                self.assertTrue(composite.save(screenshot_path))
                                screenshot_saved = True
                        finally:
                            if panel._conflict_viewer is not None:
                                panel._conflict_viewer.close()
                            container.close()
                            container.deleteLater()
                            self.app.processEvents()
                            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                            self.app.processEvents()
            if evidence_dir:
                self.assertTrue(screenshot_saved, 'Requested evidence screenshot was not produced.')
        finally:
            self.app.setFont(prior_font)

    def test_execution_mode_text_fits_actual_calm_edit_field_at_large_fonts(self):
        from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from ui.theme_manager import ThemeManager

        old_font = QFont(self.app.font())
        old_stylesheet = self.app.styleSheet()
        try:
            for font_px in (17, 21):
                font = QFont(old_font)
                font.setPixelSize(font_px)
                self.app.setFont(font)
                self.app.setStyleSheet(ThemeManager.instance().render('calm', font_size=font_px))
                for language in ('zh', 'en'):
                    container = QWidget()
                    container.resize(960, 640)
                    outer = QVBoxLayout(container)
                    outer.setContentsMargins(0, 0, 0, 0)
                    panel = AgentWorkbenchPanel(language)
                    outer.addWidget(panel)
                    panel._set_conflict_button_count(999)
                    try:
                        container.show()
                        self.app.processEvents()
                        for target_language in (language, 'en' if language == 'zh' else 'zh'):
                            if target_language != language:
                                panel.set_language(target_language)
                                self.app.processEvents()
                            option = QStyleOptionComboBox()
                            panel.exec_mode_combo.initStyleOption(option)
                            text_rect = panel.exec_mode_combo.style().subControlRect(
                                QStyle.ComplexControl.CC_ComboBox, option,
                                QStyle.SubControl.SC_ComboBoxEditField, panel.exec_mode_combo,
                            )
                            for index in range(panel.exec_mode_combo.count()):
                                text = panel.exec_mode_combo.itemText(index)
                                self.assertLessEqual(
                                    panel.exec_mode_combo.fontMetrics().horizontalAdvance(text),
                                    text_rect.width(), f'{font_px}px {target_language}: {text}',
                                )
                            self.assertLessEqual(container.width(), 960)
                            for button in (panel.conflicts_btn, panel.new_btn, panel.context_toggle_btn):
                                self.assertGreaterEqual(button.width(), button.sizeHint().width())
                    finally:
                        container.close()
                        container.deleteLater()
                        for _ in range(2):
                            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                            self.app.processEvents()
        finally:
            self.app.setStyleSheet(old_stylesheet)
            self.app.setFont(old_font)

    def test_conflict_history_text_reserves_truncation_marker_without_store_writes(self):
        from panels.agent_workbench_panel import (
            AgentWorkbenchPanel, _CONFLICT_VIEWER_MAX_CHARS,
        )
        from tools.agent_store import empty_workspace, list_workspaces, load_workspace, save_workspace

        workspace_dir = os.path.join(self.tmp_dir, 'strict-text-cap-project')
        os.makedirs(workspace_dir, exist_ok=True)
        workspace = empty_workspace(title='Strict text cap', workspace_dir=workspace_dir)
        workspace['conflicted_runs'] = [{
            'messages': [
                {
                    'role': 'assistant',
                    'reasoning_content': 'PRIVATE_REASONING_SENTINEL',
                    'content': 'PRIVATE_MESSAGE_SENTINEL',
                },
                {'role': 'assistant', 'content': 'public-history ' * (_CONFLICT_VIEWER_MAX_CHARS // 15 + 100)},
            ],
        }]
        save_workspace(workspace)
        before = load_workspace(workspace['id'])
        panel = AgentWorkbenchPanel(language='en')
        try:
            for language, marker in (
                ('en', '\n\n[Content truncated]'),
                ('zh', '\n\n[内容已截断]'),
            ):
                panel.language = language
                with patch(
                    'panels.agent_workbench_panel.save_workspace',
                    side_effect=AssertionError('Conflict history formatting must not write the Store.'),
                ) as save_spy:
                    rendered = panel._conflict_history_text(list_workspaces())
                save_spy.assert_not_called()
                self.assertLessEqual(len(rendered), _CONFLICT_VIEWER_MAX_CHARS)
                self.assertTrue(rendered.endswith(marker), 'The complete localized truncation marker must remain visible.')
                self.assertNotIn('PRIVATE_REASONING_SENTINEL', rendered)
                self.assertNotIn('PRIVATE_MESSAGE_SENTINEL', rendered)
                self.assertEqual(load_workspace(workspace['id']), before)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_composer_button_layout(self):
        """测试输入区发送按钮采用紧凑右对齐，不全行拉伸。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel

        panel = AgentWorkbenchPanel()
        try:
            self.assertIsNotNone(panel.send_btn)
            self.assertIsNotNone(panel.stop_btn)
            self.assertEqual(panel.send_btn.objectName(), 'primary-btn')
            self.assertEqual(panel.stop_btn.objectName(), 'btn-secondary')
        finally:
            panel.deleteLater()

    def test_agent_user_and_assistant_single_render_and_reopen(self):
        """测试 Agent 发送用户消息与接收最终回答时，UI 与持久化气泡数均严格为 1，重开后仍各为 1。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from tools.agent_store import empty_workspace, save_workspace, load_workspace
        from unittest.mock import patch
        from PyQt6.QtWidgets import QLabel

        ws = empty_workspace(title='单次渲染测试空间')
        ws_dir = os.path.join(self.tmp_dir, 'wb_test_ws')
        os.makedirs(ws_dir, exist_ok=True)
        ws['workspace_dir'] = ws_dir
        save_workspace(ws)

        panel = AgentWorkbenchPanel()
        try:
            panel._select_workspace(ws['id'])
            active_conv = panel._active_conversation_of(panel._workspace_session)
            self.assertIsNotNone(active_conv)

            # 模拟模型配置已就绪，避免 _send 被早期拦截
            with patch.object(panel, '_current_model', return_value={'enabled': True, 'base_url': 'http://dummy'}):
                with patch('panels.agent_workbench_panel._WorkbenchWorker') as mock_worker_cls:
                    mock_worker = mock_worker_cls.return_value
                    mock_worker.isRunning.return_value = False

                    # 输入并发送
                    panel.input.setPlainText('请帮我查找所有的订单接口')
                    panel._send()
                    self.app.processEvents()

                    # 1. 验证持久化 messages 中 user 消息恰好只有 1 条
                    conv = panel._active_conversation_of(panel._workspace_session)
                    user_msgs = [m for m in (conv.get('messages') or []) if m.get('role') == 'user']
                    self.assertEqual(len(user_msgs), 1, "持久化 messages 中 user 消息必须恰好 1 条")

                    # 2. 统计当前 UI 气泡数量
                    def count_ui_bubbles(p):
                        users, assts = 0, 0
                        for i in range(p.thread_layout.count()):
                            w = p.thread_layout.itemAt(i).widget()
                            if not w:
                                continue
                            lbls = w.findChildren(QLabel)
                            for lbl in lbls:
                                txt = lbl.text() or ''
                                if txt.startswith('👤 '):
                                    users += 1
                                elif txt.startswith('🤖 '):
                                    assts += 1
                        return users, assts

                    u_count, a_count = count_ui_bubbles(panel)
                    self.assertEqual(u_count, 1, "发送后当前 UI user bubble 必须恰好 1 个")
                    self.assertEqual(a_count, 0)

                    # 3. 模拟 Agent 完成回调（_on_agent_done）
                    final_answer = '已为您找到 3 个订单接口。'
                    updated_msgs = list(conv.get('messages') or []) + [
                        {'id': 'asst-1', 'role': 'assistant', 'content': final_answer}
                    ]
                    task_id = panel._active_task['id']
                    panel._on_agent_completed(task_id, final_answer, updated_msgs, [])
                    panel._on_worker_thread_finished(task_id, mock_worker)
                    self.app.processEvents()

                    u_count, a_count = count_ui_bubbles(panel)
                    self.assertEqual(u_count, 1, "完成回答后 UI user bubble 必须恰好 1 个")
                    self.assertEqual(a_count, 1, "完成回答后 UI assistant bubble 必须恰好 1 个")

                    # 4. 重新加载/重新渲染对话（模拟 reopen workspace）
                    saved_ws = load_workspace(ws['id'])
                    panel._workspace_session = saved_ws
                    reopen_conv = panel._active_conversation_of(saved_ws)
                    panel._render_conversation(saved_ws, reopen_conv)
                    self.app.processEvents()

                    u_reopen, a_reopen = count_ui_bubbles(panel)
                    self.assertEqual(u_reopen, 1, "重新渲染后 user bubble 仍必须恰好 1 个")
                    self.assertEqual(a_reopen, 1, "重新渲染后 assistant bubble 仍必须恰好 1 个")
        finally:
            panel.deleteLater()

    def test_project_tree_shared_ignore_and_whitelist(self):
        """测试项目树严格遵循共享过滤契约：显示 Java/Gradle 源码，隐藏 target/build/out/node_modules 等。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from PyQt6.QtWidgets import QTreeWidgetItem

        ws_dir = os.path.join(self.tmp_dir, 'java_project')
        os.makedirs(ws_dir, exist_ok=True)

        # 创建白名单允许的源码/配置文件
        for fname in ('pom.xml', 'build.gradle', 'settings.gradle', 'application.properties'):
            with open(os.path.join(ws_dir, fname), 'w', encoding='utf-8') as f:
                f.write(f"# {fname}")
        os.makedirs(os.path.join(ws_dir, 'src'), exist_ok=True)

        # 创建应被忽略的目录
        for ign in ('target', 'build', 'out', 'node_modules', '.git', '.idea'):
            ign_path = os.path.join(ws_dir, ign)
            os.makedirs(ign_path, exist_ok=True)
            with open(os.path.join(ign_path, 'dummy.txt'), 'w', encoding='utf-8') as f:
                f.write("ignore me")

        panel = AgentWorkbenchPanel()
        try:
            panel._workspace_session = {'id': 'test_java_ws', 'workspace_dir': ws_dir}
            root_item = QTreeWidgetItem()
            root_item.setData(0, Qt.ItemDataRole.UserRole, ws_dir)
            panel._populate_dir_item(root_item, ws_dir)

            child_texts = [root_item.child(i).text(0) for i in range(root_item.childCount())]

            # 必须看见：pom.xml, build.gradle, settings.gradle, application.properties, src/
            self.assertIn('pom.xml', child_texts)
            self.assertIn('build.gradle', child_texts)
            self.assertIn('settings.gradle', child_texts)
            self.assertIn('application.properties', child_texts)
            self.assertIn('src/', child_texts)

            # 必须看不见：target/, build/, out/, node_modules/, .git/, .idea/
            for ign in ('target/', 'build/', 'out/', 'node_modules/', '.git/', '.idea/'):
                self.assertNotIn(ign, child_texts)
            for ign in ('target', 'build', 'out', 'node_modules', '.git', '.idea'):
                self.assertNotIn(ign, child_texts)
        finally:
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
