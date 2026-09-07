# -*- coding: utf-8 -*-
"""Round 4-V2A 原生界面视觉层级与契约回归测试。

覆盖：
1. SQL 工作台表面层级与 SchemaSearchPopup QSS 契约
2. Requirement 目录卡片与纯展示契约（无业务统计侵入）
3. Ops Terminal Island 结构与无网络纯状态契约
4. Settings ThemeCard objectName/selected 状态与字体步进器外部单位
5. Redis 工作台表面层级、TTL 语义化格式、各类型 Badge 提取、清除/删除隐藏与 Raw value 原始对象不变性
"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtWidgets import QApplication, QFrame, QWidget
    QT_AVAILABLE = True
except Exception:  # pragma: no cover
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class TestVisualNativeSurfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tracked_widgets = []

    def track(self, widget):
        if widget is not None:
            self._tracked_widgets.append(widget)
        return widget

    def tearDown(self):
        while self._tracked_widgets:
            w = self._tracked_widgets.pop()
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
        if QT_AVAILABLE and self.app:
            self.app.processEvents()

    # ── 1. SQL Workbench ──────────────────────────────────────────────────

    def test_sql_surface_object_names(self):
        """SQL Workbench 核心表面 objectName 存在且符合语义分层。"""
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel = self.track(AiWorkbenchPanel('zh'))

        object_names = {
            'sql-object-pane',
            'sql-editor-tabs',
            'sql-side-tabs',
            'sql-ai-pane',
            'sql-detail-pane',
            'sql-result-tabs',
        }
        found = set()
        for w in panel.findChildren(QWidget):
            name = w.objectName()
            if name in object_names:
                found.add(name)

        missing = object_names - found
        self.assertEqual(missing, set(), f'SQL 表面缺少语义 objectName: {missing}')

    def test_schema_search_popup_qss_migration(self):
        """_SchemaSearchPopup 移除行内 styleSheet，依赖全局 QSS。"""
        from panels.ai_workbench_panel import _SchemaSearchPopup
        from ui.theme_manager import ThemeManager

        popup = self.track(_SchemaSearchPopup())
        self.assertEqual(popup.objectName(), 'schema-search-popup')
        self.assertEqual(popup.list_widget.objectName(), 'schema-search-list')
        self.assertEqual(popup.list_widget.styleSheet(), '', 'list_widget 不得保留行内 styleSheet')

        rendered_qss = ThemeManager.instance().render()
        self.assertIn('schema-search-popup', rendered_qss)
        self.assertIn('schema-search-list', rendered_qss)

    # ── 2. Requirement Panel ──────────────────────────────────────────────

    def test_requirement_tree_card_and_clean_contract(self):
        """RequirementPanel 左侧保持 req-tree-card，tree_count_label 存在，且无发明统计 badge。"""
        from panels.requirement_panel import RequirementPanel
        panel = self.track(RequirementPanel('zh'))

        tree_card = None
        for w in panel.findChildren(QWidget):
            if w.objectName() == 'req-tree-card':
                tree_card = w
                break
        self.assertIsNotNone(tree_card, '必须存在 req-tree-card 容器')

        self.assertTrue(hasattr(panel, 'tree_count_label'), 'tree_count_label 必须保留')
        self.assertFalse(hasattr(panel, 'req_total_badge'), 'req_total_badge 必须完全移除')
        self.assertFalse(hasattr(panel, 'req_pending_badge'), 'req_pending_badge 必须完全移除')
        self.assertFalse(hasattr(panel, 'req_done_badge'), 'req_done_badge 必须完全移除')

    def test_requirement_refresh_preserves_status_model(self):
        """RequirementPanel 刷新逻辑不得修改 requirement.status。"""
        from panels.requirement_panel import RequirementPanel
        panel = self.track(RequirementPanel('zh'))
        sample = {'id': 'REQ-TEST-1', 'title': 'Test Req', 'status': '待分析', 'record_kind': '需求'}
        panel._requirements = [sample]
        panel._refresh()

        self.assertEqual(sample.get('status'), '待分析', '刷新操作不得篡改业务 status 属性')

    # ── 3. Ops Terminal Island ────────────────────────────────────────────

    def test_ops_terminal_island_structure_and_pure_display_state(self):
        """Ops Terminal Island 结构完整，且状态刷新为纯 display state（无网络/SSH 线程）。"""
        from panels.ops_log_panel import OpsLogPanel
        panel = self.track(OpsLogPanel('zh'))

        # Terminal Island 核心表面
        self.assertEqual(panel.term_shell.objectName(), 'ops-term-shell')
        self.assertEqual(panel.term_status_dot.objectName(), 'ops-term-status-dot')
        self.assertEqual(panel.term_session_info.objectName(), 'ops-term-session-info')

        # 左侧上下文三卡片 Surface 与内层 Soft 树
        self.assertEqual(panel.server_card.objectName(), 'ops-server-card')
        self.assertEqual(panel.session_ops.objectName(), 'ops-capture-card')
        self.assertEqual(panel.remote_ops.objectName(), 'ops-remote-card')
        self.assertEqual(panel.remote_tree.objectName(), 'ops-remote-tree')

        # 工具栏按钮角色
        self.assertEqual(panel.connect_btn.objectName(), 'btn-secondary')
        self.assertEqual(panel.toolbar_export_btn.objectName(), 'btn-secondary')
        self.assertEqual(panel.disconnect_btn.objectName(), 'btn-secondary')
        self.assertEqual(panel.cmd_send_btn.objectName(), 'primary-btn')

        # 终端每 Tab 会话独立隔离契约
        self.assertEqual(panel.term_tabs.count(), 1)
        self.assertEqual(len(panel._term_sessions), 1)
        panel._add_term_tab()
        self.assertEqual(panel.term_tabs.count(), 2)
        self.assertEqual(len(panel._term_sessions), 2)
        self.assertIsNot(panel._term_sessions[0], panel._term_sessions[1])
        self.assertIsNot(panel._term_sessions[0]['widget'], panel._term_sessions[1]['widget'])
        panel._close_term_tab(1)
        self.assertEqual(panel.term_tabs.count(), 1)

        # 初始未连接状态
        self.assertFalse(panel.term_status_dot.property('termConnected'))
        self.assertIn('未连接', panel.term_session_info.text())

        # 模拟已连接 session（纯数据字典，无真实 SSH client/连接）
        panel._term_sessions[0] = {
            'client': True,
            'connected': True,
            'server_id': 'prod-bastion-01',
        }
        panel._refresh_header_session_status()

        self.assertTrue(panel.term_status_dot.property('termConnected'))
        self.assertIn('已连接', panel.term_session_info.text())
        self.assertIn('prod-bastion-01', panel.term_session_info.text())

    # ── 4. Settings ThemeCard & Surface Hierarchy ─────────────────────────

    def test_settings_theme_card_and_font_stepper(self):
        """ThemeCard 具有 theme-card objectName 与 selected 属性切换契约；字体 px 单位在外。"""
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel, ThemeCard

        card = self.track(ThemeCard('light'))
        self.assertEqual(card.objectName(), 'theme-card')
        self.assertFalse(card.property('selected'))
        self.assertTrue(card.current_badge.isHidden())

        card.set_selected(True)
        self.assertTrue(card.property('selected'))
        self.assertFalse(card.current_badge.isHidden())

        card.set_selected(False)
        self.assertFalse(card.property('selected'))
        self.assertTrue(card.current_badge.isHidden())

        panel = self.track(SettingsPanel(DEFAULT_SETTINGS, 'zh'))
        self.assertTrue(hasattr(panel, 'font_size'))
        self.assertTrue(hasattr(panel, 'font_unit_label'))
        self.assertEqual(panel.font_unit_label.text(), 'px')
        self.assertNotEqual(panel.font_size, panel.font_unit_label)

    def test_settings_surface_hierarchy_and_responsive_grid(self):
        """Settings 卡片语义 objectName、双主题卡契约、wide 双列与 compact 单列自适应布局。"""
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel

        panel = self.track(SettingsPanel(DEFAULT_SETTINGS, 'zh'))

        # 1. 严格仅有两张 ThemeCard (light -> calm, dark -> black)，无 clear/warm/night
        self.assertEqual(len(panel._theme_cards), 2)
        self.assertEqual(set(panel._theme_cards.keys()), {'light', 'dark'})
        self.assertEqual(panel._theme_cards['light'].theme_id, 'calm')
        self.assertEqual(panel._theme_cards['dark'].theme_id, 'black')
        self.assertNotIn('clear', panel._theme_cards)
        self.assertNotIn('warm', panel._theme_cards)
        self.assertNotIn('night', panel._theme_cards)

        # 2. Section card objectNames and property
        cards = {
            'settings-appearance-card': panel.appearance_group,
            'settings-floating-card': panel.float_group,
            'settings-shortcuts-card': panel.shortcuts_group,
            'settings-reminder-card': panel.reminder_group,
            'settings-behavior-card': panel.behavior_group,
            'settings-security-card': panel.security_group,
            'settings-oracle-card': panel.oracle_group,
            'settings-ai-card': panel.ai_group,
        }
        for expected_name, grp in cards.items():
            self.assertEqual(grp.objectName(), expected_name)
            self.assertTrue(grp.property('settingsSectionCard'))
        self.assertEqual(panel.action_bar.objectName(), 'settings-action-bar')

        # 3. 操作按钮层级：Save 为 Primary，Restore 为 Secondary
        self.assertEqual(panel.save_btn.objectName(), 'primary-btn')
        self.assertEqual(panel.restore_btn.objectName(), 'btn-secondary')

        # 4. Wide 响应式：两列布局，外观和 AI 跨两列
        panel.apply_layout_mode('wide')
        pos = lambda w: panel.sections_grid.getItemPosition(panel.sections_grid.indexOf(w))
        self.assertEqual(pos(panel.appearance_group), (0, 0, 1, 2))
        self.assertEqual(pos(panel.float_group), (1, 0, 1, 1))
        self.assertEqual(pos(panel.shortcuts_group), (1, 1, 1, 1))
        self.assertEqual(pos(panel.reminder_group), (2, 0, 1, 1))
        self.assertEqual(pos(panel.behavior_group), (2, 1, 1, 1))
        self.assertEqual(pos(panel.security_group), (3, 0, 1, 1))
        self.assertEqual(pos(panel.oracle_group), (3, 1, 1, 1))
        self.assertEqual(pos(panel.ai_group), (4, 0, 1, 2))

        # 5. Compact 响应式：单列布局
        panel.apply_layout_mode('compact')
        for grp in cards.values():
            _r, col, _rs, cs = pos(grp)
            self.assertEqual(col, 0)
            self.assertEqual(cs, 1)

    # ── 5. Redis Workbench Surfaces & Badges ──────────────────────────────

    def test_redis_surfaces(self):
        """Redis Workbench 具有提升后的表面语义 objectName。"""
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = self.track(RedisWorkbenchPanel('zh'))

        found = {w.objectName() for w in panel.findChildren(QWidget)}
        self.assertIn('redis-left-pane', found)
        self.assertIn('redis-detail-pane', found)
        self.assertIn('redis-console-pane', found)

    def test_format_key_ttl_badge(self):
        """format_key_ttl_badge 语言与 TTL 语义正确性。"""
        from panels.db_redis_panel import format_key_ttl_badge

        # ttl >= 0
        self.assertEqual(format_key_ttl_badge(120, 'zh'), 'TTL: 120s')
        self.assertEqual(format_key_ttl_badge(120, 'en'), 'TTL: 120s')
        self.assertEqual(format_key_ttl_badge(0, 'zh'), 'TTL: 0s')

        # ttl == -1
        self.assertEqual(format_key_ttl_badge(-1, 'zh'), 'TTL: 永不过期')
        self.assertEqual(format_key_ttl_badge(-1, 'en'), 'TTL: No expiry')

        # ttl == -2
        self.assertEqual(format_key_ttl_badge(-2, 'zh'), 'TTL: Key 不存在')
        self.assertEqual(format_key_ttl_badge(-2, 'en'), 'TTL: Key missing')

        # other negatives
        self.assertEqual(format_key_ttl_badge(-3, 'zh'), 'TTL: —')
        self.assertEqual(format_key_ttl_badge(-3, 'en'), 'TTL: —')

        # 绝不出现“已过期”
        self.assertNotIn('已过期', format_key_ttl_badge(-2, 'zh'))
        self.assertNotIn('已过期', format_key_ttl_badge(-1, 'zh'))

    def test_redis_ttl_production_display_consistency(self):
        """真实 key_meta 回调保留零值，且中英文 badge/detail 使用相同 TTL 语义。"""
        from panels.db_redis_panel import RedisWorkbenchPanel

        for language, type_label, no_expiry, missing in (
            ('zh', '类型', '永不过期', 'Key 不存在'),
            ('en', 'Type', 'No expiry', 'Key missing'),
        ):
            with patch('panels.db_redis_panel.load_connections', return_value=[]):
                panel = self.track(RedisWorkbenchPanel(language))
            for ttl, expected in (
                (0, '0s'), (120, '120s'), (-1, no_expiry),
                (-2, missing), (-3, '—'), (None, missing), ('invalid', missing),
            ):
                with self.subTest(language=language, ttl=ttl):
                    panel._on_worker_done('key_meta', {'type': 'string', 'ttl': ttl})
                    self.assertEqual(panel.key_ttl_badge.text(), f'TTL: {expected}')
                    self.assertEqual(
                        panel.key_meta.text(), f'{type_label}: string · TTL: {expected}',
                    )

    def test_redis_badges_content_and_visibility(self):
        """Redis Key 详情 badges 覆盖 string inspect model、list、hash、ttl 及隐藏逻辑。"""
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = self.track(RedisWorkbenchPanel('zh'))

        # 1. key_meta -> TYPE 与 TTL badge
        panel._on_worker_done('key_meta', {'type': 'string', 'ttl': 120})
        self.assertEqual(panel.key_type_badge.text(), 'TYPE: STRING')
        self.assertEqual(panel.key_ttl_badge.text(), 'TTL: 120s')
        self.assertFalse(panel.key_type_badge.isHidden())
        self.assertFalse(panel.key_ttl_badge.isHidden())
        self.assertTrue(panel.key_size_badge.isHidden())

        # 2. String inspect model: size=123
        panel._on_worker_done('key_value', {
            'type': 'string',
            'value': {'raw': b'x' * 123, 'size': 123, 'kind': 'binary'},
        })
        self.assertEqual(panel.key_size_badge.text(), 'SIZE: 123 B')
        self.assertFalse(panel.key_size_badge.isHidden())

        # 3. List: 3 items -> LENGTH: 3
        panel._on_worker_done('key_value', {
            'type': 'list',
            'value': ['item1', 'item2', 'item3'],
        })
        self.assertEqual(panel.key_size_badge.text(), 'LENGTH: 3')
        self.assertFalse(panel.key_size_badge.isHidden())

        # 4. Hash: 2 entries (dict) -> LENGTH: 2
        panel._on_worker_done('key_value', {
            'type': 'hash',
            'value': {'f1': 'v1', 'f2': 'v2'},
        })
        self.assertEqual(panel.key_size_badge.text(), 'LENGTH: 2')
        self.assertFalse(panel.key_size_badge.isHidden())

        # 5. Hash: 2 entries (list of dicts) -> LENGTH: 2
        panel._on_worker_done('key_value', {
            'type': 'hash',
            'value': [{'field': 'f1', 'value': 'v1'}, {'field': 'f2', 'value': 'v2'}],
        })
        self.assertEqual(panel.key_size_badge.text(), 'LENGTH: 2')
        self.assertFalse(panel.key_size_badge.isHidden())

        # 6. clear workspace -> 三个 badge 全部隐藏
        panel._clear_workspace()
        self.assertTrue(panel.key_type_badge.isHidden())
        self.assertTrue(panel.key_ttl_badge.isHidden())
        self.assertTrue(panel.key_size_badge.isHidden())

        # 7. delete -> 三个 badge 全部隐藏
        panel.key_type_badge.show()
        panel.key_ttl_badge.show()
        panel.key_size_badge.show()
        panel._on_worker_done('delete', {'key': 'test_key', 'deleted': 1})
        self.assertTrue(panel.key_type_badge.isHidden())
        self.assertTrue(panel.key_ttl_badge.isHidden())
        self.assertTrue(panel.key_size_badge.isHidden())

    def test_redis_raw_value_preserved_into_renderer(self):
        """Redis value 对象必须原样进入 renderer，严格保持原始对象与 raw bytes identity。"""
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = self.track(RedisWorkbenchPanel('zh'))

        raw_inspect_model = {
            'raw': b'\x00\x01\x02\xff\xfe\xca\xfe\xba\xbe',
            'size': 9,
            'kind': 'binary',
        }
        captured = []
        original_render = panel._render_value

        def spy_render(kind, value):
            captured.append((kind, value))
            return original_render(kind, value)

        panel._render_value = spy_render

        panel._on_worker_done('key_value', {
            'type': 'string',
            'value': raw_inspect_model,
        })

        self.assertEqual(len(captured), 1)
        kind, value = captured[0]
        self.assertEqual(kind, 'string')
        self.assertIs(value, raw_inspect_model, 'value 对象必须是传入的原始对象')
        self.assertEqual(value['raw'], b'\x00\x01\x02\xff\xfe\xca\xfe\xba\xbe')

    # ── 6. Database AI Assistant Geometry & Button Contracts ───────────────

    def test_database_ai_assistant_geometry_and_buttons(self):
        """四数据库工作台（Oracle/MySQL/OceanBase/达梦）AI 助手稳定 1:1 输入/输出比例与非空按钮契约。"""
        from PyQt6.QtWidgets import QSizePolicy
        from panels.ai_workbench_panel import AiWorkbenchPanel

        for dialect in ('oracle', 'mysql', 'oceanbase', 'dameng'):
            panel = AiWorkbenchPanel('zh', dialect=dialect)
            try:
                # 1. input and output widgets exist
                self.assertIsNotNone(panel.nl_input, f'{dialect} nl_input 必须存在')
                self.assertIsNotNone(panel.ai_explain, f'{dialect} ai_explain 必须存在')

                # 2. both Expanding
                self.assertEqual(
                    panel.nl_input.sizePolicy().verticalPolicy(),
                    QSizePolicy.Policy.Expanding,
                    f'{dialect} nl_input 必须使用 Expanding vertical policy'
                )
                self.assertEqual(
                    panel.ai_explain.sizePolicy().verticalPolicy(),
                    QSizePolicy.Policy.Expanding,
                    f'{dialect} ai_explain 必须使用 Expanding vertical policy'
                )

                # 3. input no tiny fixed maximum height
                self.assertGreater(
                    panel.nl_input.maximumHeight(),
                    1000,
                    f'{dialect} nl_input 不得有 <= 170 的矮限制'
                )

                # 4. default geometry / stretch approximately balanced (1:1)
                panel.resize(1200, 800)
                panel.show()
                self.app.processEvents()
                h_in = panel.nl_input.height()
                h_out = panel.ai_explain.height()
                self.assertGreater(h_in, 100, f'{dialect} nl_input 高度过小')
                self.assertGreater(h_out, 100, f'{dialect} ai_explain 高度过小')
                diff = abs(h_in - h_out)
                self.assertLessEqual(
                    diff,
                    25,
                    f'{dialect} 输入输出高度不平衡: in={h_in}, out={h_out}, diff={diff}'
                )

                # 5. button row contains no visible empty-text QPushButton
                for btn in (panel.ai_gen_btn, panel.ai_pick_btn, panel.ai_snap_btn, panel.agent_more):
                    self.assertTrue(btn.isVisible(), f'{dialect} 核心操作按钮必须可见')
                    text = btn.text().strip()
                    self.assertTrue(
                        bool(text),
                        f'{dialect} 按钮 {btn} 存在空白文字占位'
                    )
                self.assertEqual(panel.ai_gen_btn.text(), '生成 SQL')
                self.assertEqual(panel.ai_gen_btn.toolTip(), '生成 SQL 草案')
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    # ── 7. Requirement Surface Hierarchy & 4-Button Contract ──────────────

    def test_requirement_surface_hierarchy_and_toolbar_contract(self):
        """RequirementPanel 视觉表面层级清晰，严格保持 4 按钮工具栏契约且无已移除旧字段。"""
        from PyQt6.QtWidgets import QFrame, QTabWidget
        from panels.requirement_panel import RequirementPanel
        panel = RequirementPanel('zh')
        try:
            # 1. 表面层级容器完整
            toolbar = panel.findChild(QFrame, 'page-toolbar')
            self.assertIsNotNone(toolbar, '必须存在 page-toolbar')
            self.assertTrue(toolbar.property('card'), 'page-toolbar 必须具备 card 属性以呈现卡片表面')

            filter_bar = panel.findChild(QFrame, 'page-filter-bar')
            self.assertIsNotNone(filter_bar, '必须存在 page-filter-bar')

            tree_card = panel.findChild(QFrame, 'req-tree-card')
            self.assertIsNotNone(tree_card, '必须存在 req-tree-card')

            detail_card = panel.findChild(QFrame, 'detail-summary-card')
            self.assertIsNotNone(detail_card, '必须存在 detail-summary-card')

            module_tabs = panel.findChild(QTabWidget, 'module-tabs')
            self.assertIsNotNone(module_tabs, '必须存在 module-tabs')

            # 2. 四按钮工具栏契约
            self.assertEqual(panel.scan_btn.text(), '扫描需求目录')
            self.assertEqual(panel.update_all_btn.text(), '更新全部')
            self.assertEqual(panel.bug_btn.text(), '登记缺陷')
            self.assertTrue(panel.toolbar_more_btn.text().startswith('更多'))

            # 3. 严格禁止恢复旧版已移除字段
            self.assertFalse(hasattr(panel, 'online_month_input'), '禁止恢复 online_month_input')
            self.assertFalse(hasattr(panel, 'is_this_month_check'), '禁止恢复 is_this_month_check')
            self.assertFalse(hasattr(panel, 'planned_date_edit'), '禁止恢复 planned_date_edit')
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    # ── 8. Database Workbench Header & Toolbar Contract ───────────────────

    def test_database_workbench_header_and_toolbar_contract(self):
        """四数据库工作台标题/副标题、连接芯片与工具栏按钮 tooltip 契约完整。"""
        from panels.ai_workbench_panel import AiWorkbenchPanel, sql_splitter_tab_id

        # Regression: splitter tab id 保持原有语义，不迁移旧 key
        self.assertEqual(sql_splitter_tab_id('body', 'dm'), 'body-dm')
        self.assertEqual(sql_splitter_tab_id('columns', 'dm'), 'columns-dm')
        self.assertEqual(sql_splitter_tab_id('body', 'dameng'), 'body-dameng')
        self.assertEqual(sql_splitter_tab_id('columns', 'dameng'), 'columns-dameng')

        expected_titles = {
            'oracle': 'Oracle 工作台',
            'mysql': 'MySQL 工作台',
            'oceanbase': 'OceanBase 工作台',
            'dameng': '达梦工作台',
        }
        for dialect, expected_title in expected_titles.items():
            panel = AiWorkbenchPanel('zh', dialect=dialect)
            try:
                self.assertEqual(panel.page_title.text(), expected_title)
                self.assertEqual(panel.page_subtitle.text(), '多标签编辑 · 结构快照 · AI 助手生成不执行')
                self.assertEqual(panel.conn_meta.objectName(), 'status-pill')

                # 工具栏按钮存在有效提示（de-noised hierarchy）
                for btn in (
                    panel.conn_new_btn, panel.conn_edit_btn, panel.conn_del_btn,
                    panel.test_btn, panel.scan_btn, panel.scan_cancel_btn,
                    panel.view_snap_btn, panel.del_snap_btn, panel.model_btn,
                    panel.save_draft_btn,
                ):
                    self.assertTrue(bool(btn.toolTip().strip()), f'{dialect} 按钮 {btn} 缺少 tooltip')
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    # ── 9. Daylight Glass Surface QSS Tokens ──────────────────────────────

    def test_qss_daylight_glass_surface_alignment(self):
        """全局样式在 Calm 和 Black 主题下成功注入 Daylight Glass 表面 Token 且无未解析变量。"""
        from ui.theme_manager import ThemeManager, unresolved_qss_tokens

        for theme in ('calm', 'black'):
            qss = ThemeManager.instance().render(theme)

            # 无悬挂未注入变量
            unresolved = unresolved_qss_tokens(qss)
            self.assertEqual(unresolved, (), f'{theme} 主题存在未解析的 QSS 变量: {unresolved}')

            # 关键选择器存在
            self.assertIn('QSplitter::handle:horizontal', qss)
            self.assertIn('QSplitter#requirement-splitter::handle:horizontal', qss)
            self.assertIn('QFrame#page-toolbar[card="true"]', qss)
            self.assertIn('QFrame#req-tree-card', qss)
            self.assertIn('QTabWidget#module-tabs QTabBar::tab:selected', qss)
            self.assertIn('QTextEdit#ai-prompt-edit', qss)
            self.assertIn('QTextEdit#ai-explain', qss)
            self.assertIn('QFrame#sql-object-pane', qss)
            # Round 4-V2G-B2 选择器
            self.assertIn('QPlainTextEdit#iface-detail-edit', qss)
            self.assertIn('QFrame#chat-session-card', qss)
            self.assertIn('QFrame#chat-composer-card', qss)
            self.assertIn('QPlainTextEdit#chat-composer-input', qss)
            self.assertIn('QFrame#agent-space-card', qss)
            self.assertIn('QFrame#mongo-tree-card', qss)
            self.assertIn('QFrame#credit-filter-card', qss)
            self.assertIn('QGroupBox#gateway-config-group', qss)
            self.assertIn('QGroupBox#docx-file-group', qss)
            self.assertIn('QFrame#ops-list-card', qss)

    # ── 10. Round 4-V2G-B2 Observable Native Surfaces ─────────────────────

    def test_interface_debug_workbench_surfaces(self):
        """Interface Debug: 核心 sidebar / workspace / inspector Surface 与按钮契约完整。"""
        from panels.interface_debug_panel import InterfaceDebugPanel

        with patch.object(InterfaceDebugPanel, '_toggle_capture') as mock_toggle:
            panel = self.track(InterfaceDebugPanel('zh'))
            panel.capture_toggle_btn.click()
            mock_toggle.assert_called_once()

        self.assertEqual(panel._session_list_widget.objectName(), 'iface-session-pane')
        self.assertEqual(panel.detail_workspace.objectName(), 'iface-detail-workspace')
        self.assertEqual(panel.table.objectName(), 'iface-request-table')
        self.assertEqual(panel.overview_edit.objectName(), 'iface-detail-edit')
        self.assertEqual(panel.req_detail.objectName(), 'iface-detail-edit')
        self.assertEqual(panel.resp_detail.objectName(), 'iface-detail-edit')
        self.assertEqual(panel.mid_splitter.objectName(), 'iface-mid-splitter')
        self.assertEqual(panel.mid_splitter.count(), 2)
        self.assertEqual(panel.session_toolbar.objectName(), 'iface-session-toolbar')
        self.assertEqual(panel.request_verify_context.objectName(), 'request-verify-context')
        self.assertEqual(panel.capture_toggle_btn.objectName(), 'primary-btn')

    def test_model_chat_surfaces_and_bubbles(self):
        """Model Chat: 会话、输入岛、气泡语义与空状态完整。"""
        from panels.model_chat_panel import ModelChatPanel

        with patch.object(ModelChatPanel, '_on_action_clicked') as mock_action:
            panel = self.track(ModelChatPanel('zh'))
            panel.send_btn.setEnabled(True)
            panel.send_btn.click()
            mock_action.assert_called_once()

        self.assertEqual(panel.session_card.objectName(), 'chat-session-card')
        self.assertEqual(panel.composer_card.objectName(), 'chat-composer-card')
        self.assertEqual(panel.input.objectName(), 'chat-composer-input')

        # 气泡渲染对象名
        user_row = panel._make_message_row({'role': 'user', 'content': 'Hello'})
        user_bubble = user_row.findChild(QFrame, 'chat-user-bubble')
        self.assertIsNotNone(user_bubble)
        self.assertEqual(user_bubble.objectName(), 'chat-user-bubble')

        assistant_row = panel._make_message_row({'role': 'assistant', 'content': 'Hi there'})
        assistant_bubble = assistant_row.findChild(QFrame, 'chat-assistant-bubble')
        self.assertIsNotNone(assistant_bubble)
        self.assertEqual(assistant_bubble.objectName(), 'chat-assistant-bubble')
        self.assertIsNotNone(panel.empty)

    def test_agent_workbench_surfaces(self):
        """Agent Workbench: 空间卡、消息卡、输入卡、上下文卡语义完整。"""
        from panels.agent_workbench_panel import AgentWorkbenchPanel
        from ui.thinking_indicator import ThinkingIndicator

        with patch.object(AgentWorkbenchPanel, '_on_action_clicked') as mock_action:
            panel = self.track(AgentWorkbenchPanel('zh'))
            panel.send_btn.click()
            mock_action.assert_called_once()

        self.assertEqual(panel.space_card.objectName(), 'agent-space-card')
        self.assertEqual(panel.thread_card.objectName(), 'agent-thread-card')
        self.assertEqual(panel.composer_card.objectName(), 'agent-composer-card')
        self.assertEqual(panel.context_panel.objectName(), 'agent-context-card')
        self.assertEqual(panel.input.objectName(), 'agent-composer-input')
        self.assertEqual(panel.send_btn.objectName(), 'primary-btn')

        # Transient Thinking / ReAct status UI contract (zero worker execution)
        self.assertIsNone(panel._transient_holder)
        self.assertIsNone(panel._transient_indicator)
        panel._set_transient_status('正在分析项目…')
        self.assertIsNotNone(panel._transient_holder)
        self.assertIsNotNone(panel._transient_indicator)
        self.assertIsInstance(panel._transient_indicator, ThinkingIndicator)
        self.assertEqual(panel._transient_indicator.objectName(), 'agent-transient-indicator')
        self.assertIsNone(panel._agent_worker, '瞬态状态切换绝不启动 _WorkbenchWorker')

        panel._clear_transient_status()
        self.assertIsNone(panel._transient_holder)
        self.assertIsNone(panel._transient_indicator)

    def test_mongodb_workbench_surfaces(self):
        """MongoDB Workbench: 集合树、工作区、查询与文档编辑器语义完整。"""
        from panels.db_mongodb_panel import MongoDBWorkbenchPanel

        with patch('panels.db_mongodb_panel.load_connections', return_value=[]), \
             patch.object(MongoDBWorkbenchPanel, '_run_query') as mock_query, \
             patch.object(MongoDBWorkbenchPanel, '_insert_doc') as mock_insert, \
             patch.object(MongoDBWorkbenchPanel, '_delete_selected') as mock_del:
            panel = self.track(MongoDBWorkbenchPanel('zh'))
            panel.query_btn.click()
            panel.insert_btn.click()
            panel.del_btn.click()
            mock_query.assert_called_once()
            mock_insert.assert_called_once()
            mock_del.assert_called_once()
            self.assertIsNone(panel._worker, '测试按钮 callback 时绝不启动 _MongoWorker 或真实连接')

        self.assertEqual(panel.tree_card.objectName(), 'mongo-tree-card')
        self.assertEqual(panel.workspace_card.objectName(), 'mongo-workspace-card')
        self.assertEqual(panel.query_input.objectName(), 'mongo-query-edit')
        self.assertEqual(panel.doc_json.objectName(), 'mongo-doc-edit')
        self.assertEqual(panel.side_tabs.objectName(), 'module-tabs')
        self.assertEqual(panel.query_btn.objectName(), 'primary-btn')

    def test_utility_panels_surfaces(self):
        """工具类页面: 证件、网关、文档、命令库、格式工具表面语义完整。"""
        from panels.credit_panel import CreditCodePanel
        from panels.gateway_panel import GatewayDecodePanel
        from panels.docx_panel import DocxUpdatePanel
        from panels.ops_panel import OpsPanel
        from panels.format_panel import FormatToolsPanel
        from panels.sql_panel import SqlToolPanel

        # 1. 证件面板
        credit = self.track(CreditCodePanel())
        self.assertTrue(any(w.objectName() == 'credit-filter-card' for w in credit.findChildren(QWidget)))
        self.assertEqual(credit.category_tabs.objectName(), 'module-tabs')

        # 2. 网关解密
        gateway = self.track(GatewayDecodePanel('zh'))
        self.assertEqual(gateway.config_group.objectName(), 'gateway-config-group')
        self.assertEqual(gateway.key_cipher.objectName(), 'gateway-key-edit')

        # 3. 文档更新
        docx = self.track(DocxUpdatePanel('zh'))
        self.assertEqual(docx.file_group.objectName(), 'docx-file-group')
        self.assertEqual(docx.output_browser.objectName(), 'docx-output-browser')

        # 4. 命令库
        ops = self.track(OpsPanel('zh'))
        self.assertTrue(any(w.objectName() == 'ops-list-card' for w in ops.findChildren(QWidget)))
        self.assertEqual(ops.command_list.objectName(), 'ops-command-list')

        # 5. 格式工具
        fmt = self.track(FormatToolsPanel('zh'))
        self.assertTrue(any(w.objectName() == 'module-tabs' for w in fmt.findChildren(QWidget)))

        # 6. SQL 升级准备
        sql_tool = self.track(SqlToolPanel())
        self.assertTrue(any(w.objectName() == 'release-filter-zone' for w in sql_tool.findChildren(QWidget)))


if __name__ == '__main__':
    unittest.main()
