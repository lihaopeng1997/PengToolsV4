# -*- coding: utf-8 -*-
"""Targeted unit tests for Agent Workbench & Model Chat layout improvements."""

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSplitter


class AgentWorkbenchLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='agent_wb_test_')

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

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
                    panel._on_agent_done(final_answer, updated_msgs, [])
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
