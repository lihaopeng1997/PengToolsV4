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

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PyQt6.QtWidgets import QApplication, QWidget
    QT_AVAILABLE = True
except Exception:  # pragma: no cover
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class TestVisualNativeSurfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # ── 1. SQL Workbench ──────────────────────────────────────────────────

    def test_sql_surface_object_names(self):
        """SQL Workbench 核心表面 objectName 存在且符合语义分层。"""
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel = AiWorkbenchPanel('zh')

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

        popup = _SchemaSearchPopup()
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
        panel = RequirementPanel('zh')

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
        panel = RequirementPanel('zh')
        sample = {'id': 'REQ-TEST-1', 'title': 'Test Req', 'status': '待分析', 'record_kind': '需求'}
        panel._requirements = [sample]
        panel._refresh()

        self.assertEqual(sample.get('status'), '待分析', '刷新操作不得篡改业务 status 属性')

    # ── 3. Ops Terminal Island ────────────────────────────────────────────

    def test_ops_terminal_island_structure_and_pure_display_state(self):
        """Ops Terminal Island 结构完整，且状态刷新为纯 display state（无网络/SSH 线程）。"""
        from panels.ops_log_panel import OpsLogPanel
        panel = OpsLogPanel('zh')

        self.assertEqual(panel.term_shell.objectName(), 'ops-term-shell')
        self.assertEqual(panel.term_status_dot.objectName(), 'ops-term-status-dot')
        self.assertEqual(panel.term_session_info.objectName(), 'ops-term-session-info')

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

    # ── 4. Settings ThemeCard & Font Stepper ───────────────────────────────

    def test_settings_theme_card_and_font_stepper(self):
        """ThemeCard 具有 theme-card objectName 与 selected 属性切换契约；字体 px 单位在外。"""
        from panels.settings_panel import SettingsPanel, ThemeCard

        card = ThemeCard('light')
        self.assertEqual(card.objectName(), 'theme-card')
        self.assertFalse(card.property('selected'))
        self.assertTrue(card.current_badge.isHidden())

        card.set_selected(True)
        self.assertTrue(card.property('selected'))
        self.assertFalse(card.current_badge.isHidden())

        card.set_selected(False)
        self.assertFalse(card.property('selected'))
        self.assertTrue(card.current_badge.isHidden())

        panel = SettingsPanel('zh')
        self.assertTrue(hasattr(panel, 'font_size'))
        self.assertTrue(hasattr(panel, 'font_unit_label'))
        self.assertEqual(panel.font_unit_label.text(), 'px')
        self.assertNotEqual(panel.font_size, panel.font_unit_label)

    # ── 5. Redis Workbench Surfaces & Badges ──────────────────────────────

    def test_redis_surfaces(self):
        """Redis Workbench 具有提升后的表面语义 objectName。"""
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = RedisWorkbenchPanel('zh')

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

    def test_redis_badges_content_and_visibility(self):
        """Redis Key 详情 badges 覆盖 string inspect model、list、hash、ttl 及隐藏逻辑。"""
        from panels.db_redis_panel import RedisWorkbenchPanel
        panel = RedisWorkbenchPanel('zh')

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
        panel = RedisWorkbenchPanel('zh')

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


if __name__ == '__main__':
    unittest.main()
