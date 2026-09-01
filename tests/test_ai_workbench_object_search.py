# -*- coding: utf-8 -*-
# Targeted UI tests for unified database object search in AiWorkbenchPanel.

import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_app = QApplication.instance() or QApplication([])

from panels.ai_workbench_panel import AiWorkbenchPanel


class AiWorkbenchObjectSearchTests(unittest.TestCase):
    def setUp(self):
        self.sample_snapshot = {
            'snapshot_id': 'snap_test_01',
            'dialect': 'oracle',
            'objects': [
                {
                    'owner': 'PRPCAR',
                    'name': 'T_POLICY',
                    'object_type': 'TABLE',
                    'comment': '保单主表',
                    'columns': [
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(20)', 'comment': '保单号', 'primary_key': True},
                        {'name': 'APPLY_DATE', 'data_type': 'DATE', 'comment': '投保日期'},
                    ],
                },
                {
                    'owner': 'PRPCAR',
                    'name': 'T_CLAIM',
                    'object_type': 'TABLE',
                    'comment': '理赔表',
                    'columns': [
                        {'name': 'CLAIM_NO', 'data_type': 'VARCHAR2(20)', 'comment': '理赔号', 'primary_key': True},
                        {'name': 'POLICY_NO', 'data_type': 'VARCHAR2(20)', 'comment': '保单号'},
                    ],
                },
            ],
        }
        self.panel = AiWorkbenchPanel(language='zh')
        self.panel._snapshot = self.sample_snapshot
        self.panel._rebuild_tree()

    def tearDown(self):
        self.panel.deleteLater()

    def test_typing_filters_tree_and_field_hit_keeps_parent_table(self):
        # Typing a field name that only exists in T_POLICY and T_CLAIM
        self.panel.object_filter.setText('apply_date')
        self.panel._on_search_timer()

        # T_POLICY has apply_date, so it should NOT be hidden
        found_policy = False
        found_claim = False
        for i in range(self.panel.object_tree.topLevelItemCount()):
            root = self.panel.object_tree.topLevelItem(i)
            for j in range(root.childCount()):
                schema = root.child(j)
                for k in range(schema.childCount()):
                    table_item = schema.child(k)
                    data = table_item.data(0, Qt.ItemDataRole.UserRole) or {}
                    t_name = data.get('object', {}).get('name')
                    if t_name == 'T_POLICY':
                        self.assertFalse(table_item.isHidden(), 'Parent table of matched field must remain visible')
                        found_policy = True
                    elif t_name == 'T_CLAIM':
                        self.assertTrue(table_item.isHidden(), 'Non-matched table should be hidden')
                        found_claim = True
        self.assertTrue(found_policy)
        self.assertTrue(found_claim)

    def test_clearing_filter_restores_tree(self):
        self.panel.object_filter.setText('apply_date')
        self.panel._on_search_timer()
        self.panel.object_filter.setText('')
        self.panel._on_object_filter_text_changed('')

        for i in range(self.panel.object_tree.topLevelItemCount()):
            root = self.panel.object_tree.topLevelItem(i)
            self.assertFalse(root.isHidden())
            for j in range(root.childCount()):
                schema = root.child(j)
                self.assertFalse(schema.isHidden())
                for k in range(schema.childCount()):
                    table_item = schema.child(k)
                    self.assertFalse(table_item.isHidden())

    def test_locate_table_selects_tree_and_loads_detail(self):
        self.panel._locate_table('PRPCAR', 'T_CLAIM')
        selected = self.panel._selected_object()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.get('object', {}).get('name'), 'T_CLAIM')
        self.assertEqual(self.panel._detail_object.get('name'), 'T_CLAIM')
        self.assertGreater(self.panel.field_table.rowCount(), 0)

    def test_locate_field_selects_parent_table_and_field_row(self):
        self.panel._locate_field('PRPCAR', 'T_POLICY', 'APPLY_DATE')
        self.assertEqual(self.panel._detail_object.get('name'), 'T_POLICY')

        # Check field table selection
        selected_fields = self.panel._selected_detail_fields()
        self.assertEqual(len(selected_fields), 1)
        self.assertEqual(selected_fields[0].get('name'), 'APPLY_DATE')

    def test_escape_key_hides_popup(self):
        self.panel.object_filter.setFocus()
        self.panel.object_filter.setText('policy')
        self.panel._on_search_timer()

        if self.panel._search_popup:
            self.panel._search_popup.show()
            self.assertTrue(self.panel._search_popup.isVisible())

            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            handled = self.panel.eventFilter(self.panel.object_filter, event)
            self.assertTrue(handled)
            self.assertFalse(self.panel._search_popup.isVisible())
            self.assertEqual(self.panel.object_filter.text(), 'policy')

    def test_enter_key_activates_selected_suggestion(self):
        self.panel.object_filter.setFocus()
        self.panel.object_filter.setText('T_CLAIM')
        self.panel._on_search_timer()

        if self.panel._search_popup:
            self.panel._search_popup.show()
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            handled = self.panel.eventFilter(self.panel.object_filter, event)
            self.assertTrue(handled)
            self.assertFalse(self.panel._search_popup.isVisible())
            self.assertEqual(self.panel._detail_object.get('name'), 'T_CLAIM')

    def test_run_ai_passes_current_table_and_fields_context(self):
        self.panel._locate_field('PRPCAR', 'T_POLICY', 'APPLY_DATE')
        self.panel.nl_input.setPlainText('按日期查询')

        with patch('panels.ai_workbench_panel.is_enabled', return_value=True):
            with patch('panels.ai_workbench_panel.prepare_request') as mock_prepare:
                mock_prepare.return_value = {
                    'ok': True,
                    'state': 'READY',
                    'evidence': {
                        'dialect': 'oracle',
                        'tables': [{'qualified_name': 'PRPCAR.T_POLICY', 'columns': []}],
                        'confirmed_fields': ['PRPCAR.T_POLICY.APPLY_DATE'],
                    },
                }
                with patch.object(self.panel, '_start_agent_task') as mock_start:
                    self.panel._run_ai('generate')
                    self.assertTrue(mock_prepare.called)
                    kwargs = mock_prepare.call_args[1]
                    self.assertEqual(kwargs['current_table']['name'], 'T_POLICY')
                    self.assertEqual(kwargs['current_fields'][0]['name'], 'APPLY_DATE')
                    self.assertTrue(mock_start.called)

    def test_on_ai_ok_creates_unexecuted_draft_tab(self):
        tab_count_before = self.panel.sql_tabs.count()
        self.panel._pending_evidence = {
            'dialect': 'oracle',
            'tables': [{'qualified_name': 'PRPCAR.T_POLICY', 'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2'}]}],
            'confirmed_fields': ['PRPCAR.T_POLICY.POLICY_NO'],
            'snapshot_id': 'snap_1',
            'scanned_at': '2026-09-01',
        }
        draft = {
            'summary': '查询保单号',
            'sql': 'SELECT POLICY_NO FROM PRPCAR.T_POLICY',
            'risk_level': 'read',
        }
        self.panel._on_ai_ok(draft)
        tab_count_after = self.panel.sql_tabs.count()
        self.assertEqual(tab_count_after, tab_count_before + 1)
        current_tab_title = self.panel.sql_tabs.tabText(self.panel.sql_tabs.currentIndex())
        self.assertIn('SQL 草案 · 未执行', current_tab_title)

    def test_on_ai_ok_blocks_invalid_field_and_creates_no_tab(self):
        tab_count_before = self.panel.sql_tabs.count()
        self.panel._pending_evidence = {
            'dialect': 'oracle',
            'tables': [{'qualified_name': 'PRPCAR.T_POLICY', 'columns': [{'name': 'POLICY_NO', 'data_type': 'VARCHAR2'}]}],
            'confirmed_fields': ['PRPCAR.T_POLICY.POLICY_NO'],
            'snapshot_id': 'snap_1',
            'scanned_at': '2026-09-01',
        }
        # Model generated hallucinated field NON_EXISTENT_FIELD
        draft = {
            'summary': '查询非法字段',
            'sql': 'SELECT NON_EXISTENT_FIELD FROM PRPCAR.T_POLICY',
            'risk_level': 'read',
        }
        with patch('panels.ai_workbench_panel.show_warning') as mock_warn:
            self.panel._on_ai_ok(draft)
            self.assertTrue(mock_warn.called)
        tab_count_after = self.panel.sql_tabs.count()
        self.assertEqual(tab_count_after, tab_count_before)

    def test_run_ai_safe_oceanbase_and_schema_context(self):
        # OceanBase connection with mode='mysql', schema='PRP_SCHEMA', and secret username/password
        conn_item = {
            'id': 'ob_conn_1',
            'name': 'ob-cluster',
            'dialect': 'oceanbase',
            'mode': 'mysql',
            'database': 'INS_DB',
            'schema': 'PRP_SCHEMA',
            'username': 'secret_admin_user',
            'password': 'secret_password_123',
        }
        self.panel._current_conn_item = conn_item
        self.panel.nl_input.setPlainText('查询保单')

        with patch.object(self.panel, '_browse_conn', return_value=conn_item):
            with patch('panels.ai_workbench_panel.is_enabled', return_value=True):
                with patch('panels.ai_workbench_panel.prepare_request') as mock_prepare:
                    mock_prepare.return_value = {
                        'ok': True,
                        'state': 'READY',
                        'evidence': {
                            'dialect': 'oceanbase',
                            'oceanbase_mode': 'mysql',
                            'tables': [{'qualified_name': 'PRP_SCHEMA.T_POLICY', 'columns': []}],
                            'confirmed_fields': ['PRP_SCHEMA.T_POLICY.POLICY_NO'],
                        },
                    }
                    with patch.object(self.panel, '_start_agent_task') as mock_start:
                        self.panel._run_ai('generate')
                        self.assertTrue(mock_start.called)
                        task_kwargs = mock_start.call_args[0][0]
                        self.assertEqual(task_kwargs['database'], 'INS_DB')
                        self.assertEqual(task_kwargs['schema_name'], 'PRP_SCHEMA')
                        self.assertEqual(task_kwargs['oceanbase_mode'], 'mysql')
                        self.assertNotIn('secret_admin_user', str(task_kwargs.values()))
                        self.assertNotIn('secret_password_123', str(task_kwargs.values()))


if __name__ == '__main__':
    unittest.main()
