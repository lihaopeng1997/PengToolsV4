# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ai_object_context import (
    add_field, add_object, empty_context, keep_tokens, qualified_name, remove_token,
)
from tools.schema_snapshot import format_field_label, format_object_label, search_fields, search_objects


class AiObjectContextTests(unittest.TestCase):
    def test_duplicate_object_keeps_one_token(self):
        ctx = empty_context({'snapshot_id': 's1', 'fingerprint': 'fp'})
        obj = {'owner': 'AUTO', 'name': 'PRPCMAIN', 'object_type': 'TABLE'}
        a = add_object(ctx, obj)
        b = add_object(ctx, obj)
        self.assertEqual(a['token_id'], b['token_id'])
        self.assertEqual(len(ctx['selected_objects']), 1)
        self.assertEqual(qualified_name(obj), 'AUTO.PRPCMAIN')

    def test_remove_token_syncs_context(self):
        ctx = empty_context()
        obj = {'owner': 'U', 'name': 'T'}
        token = add_object(ctx, obj)
        field = add_field(ctx, obj, {'name': 'A', 'data_type': 'VARCHAR2'})
        remove_token(ctx, field['token_id'])
        self.assertEqual(len(ctx['selected_fields']), 0)
        keep_tokens(ctx, [])
        self.assertEqual(ctx['selected_objects'], [])
        self.assertTrue(token['token_id'])

    def test_search_objects_uses_comment(self):
        snap = {'objects': [
            {'name': 'PRPCMAIN', 'owner': 'AUTO', 'comment': '保单主表', 'object_type': 'TABLE'},
            {'name': 'OTHER', 'owner': 'AUTO', 'comment': '', 'object_type': 'TABLE'},
        ]}
        hits = search_objects(snap, '保单')
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['name'], 'PRPCMAIN')
        self.assertIn('保单主表', format_object_label(hits[0]))

    def test_search_fields_sorts_and_matches_comment(self):
        obj = {
            'name': 'PRPCMAIN',
            'columns': [
                {'name': 'ZFLAG', 'data_type': 'CHAR', 'comment': '标志'},
                {'name': 'POLICYNO', 'data_type': 'VARCHAR2', 'comment': '保单号'},
                {'name': 'AMOUNT', 'data_type': 'NUMBER', 'comment': '保额'},
            ],
        }
        names = [col['name'] for col in search_fields(obj, '')]
        self.assertEqual(names, ['AMOUNT', 'POLICYNO', 'ZFLAG'])
        hits = search_fields(obj, '保单号')
        self.assertEqual([col['name'] for col in hits], ['POLICYNO'])
        self.assertIn('保单号', format_field_label(hits[0]))


class SqlConsoleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_tree_has_no_field_children_and_tokens_insert(self):
        from PyQt6.QtWidgets import QTreeWidgetItem
        from panels.ai_workbench_panel import AiWorkbenchPanel, compose_nl_query
        from tools.ai_object_context import add_object
        panel = AiWorkbenchPanel('zh')
        panel._snapshot = {
            'snapshot_id': 'sid',
            'fingerprint': 'oracle|h|1521|orcl|u',
            'status': 'ok',
            'objects': [{
                'owner': 'AUTO', 'name': 'PRPCMAIN', 'object_type': 'TABLE', 'comment': '',
                'columns': [{'name': 'POLICYNO', 'data_type': 'VARCHAR2', 'nullable': False, 'primary_key': True, 'indexed': True, 'comment': ''}],
            }],
        }
        panel._rebuild_tree()
        def walk(item, acc):
            acc.append(item)
            for i in range(item.childCount()):
                walk(item.child(i), acc)
        items = []
        for i in range(panel.object_tree.topLevelItemCount()):
            walk(panel.object_tree.topLevelItem(i), items)
        kinds = [((it.data(0, 256) or {}) if True else {}) for it in items]
        from PyQt6.QtCore import Qt
        kinds = [(it.data(0, Qt.ItemDataRole.UserRole) or {}).get('kind') for it in items]
        self.assertNotIn('column', kinds)
        token = add_object(panel.nl_input.context, panel._snapshot['objects'][0])
        panel.nl_input.insert_token('object', token, 0)
        self.assertIn('表：PRPCMAIN', panel.nl_input.toPlainText())
        self.assertEqual(compose_nl_query('PRPCMAIN', ['POLICYNO']), '帮我查询表 PRPCMAIN 的字段 POLICYNO')
        with open(os.path.join(ROOT, 'panels', 'ai_workbench_panel.py'), encoding='utf-8') as stream:
            source = stream.read()
        self.assertNotRegex(source, r'def _on_ai_ok[\s\S]*?self\._run_sql')
        self.assertIn('_on_ai_ok', source)
        self.assertTrue(hasattr(panel, 'loading'))
        self.assertIn('start_busy', source)
        self.assertIn('正在执行查询', source)
        self.assertEqual(panel.side_tabs.tabText(0), 'AI 助手')
        self.assertEqual(panel.ai_gen_btn.text(), '生成 SQL 草案')
        self.assertEqual(panel.run_btn.text(), '执行当前 SQL')
        self.assertIn('prepare_request', source)
        self.assertIn('validate_generated_sql', source)
        self.assertIn('SQL 草案 · 未执行', source)
        self.assertNotIn("addTab(ai_page, 'TamengAgent')", source)
        self.assertIn('当前草案任务仍在运行', source)
        panel.nl_input.insertPlainText('帮我查一下')
        self.assertIn('帮我查一下', panel.nl_input.toPlainText())
        panel.nl_input.clear_tokens()
        self.assertIn('帮我查一下', panel.nl_input.toPlainText())
        self.assertNotIn('表：PRPCMAIN', panel.nl_input.toPlainText())
        panel.close()

    def test_generate_without_snapshot_does_not_start_worker(self):
        from unittest.mock import patch
        from panels.ai_workbench_panel import AiWorkbenchPanel
        panel = AiWorkbenchPanel('zh')
        panel._snapshot = None
        panel.nl_input.setPlainText('查询 prpcmain 中创建日期倒序')
        with patch('panels.ai_workbench_panel.is_enabled', return_value=True):
            with patch('panels.ai_workbench_panel.show_warning') as warned:
                panel._run_ai('generate')
        self.assertTrue(warned.called)
        self.assertFalse(panel._agent_busy)
        self.assertIsNone(panel._ai_worker)
        panel.close()

    def test_field_dialog_has_separate_search_and_comments(self):
        from PyQt6.QtCore import Qt
        from panels.ai_token_edit import ObjectPickDialog
        snap = {
            'objects': [{
                'owner': 'AUTO', 'name': 'PRPCMAIN', 'object_type': 'TABLE', 'comment': '保单主表',
                'columns': [
                    {'name': 'ZFLAG', 'data_type': 'CHAR', 'comment': '标志'},
                    {'name': 'POLICYNO', 'data_type': 'VARCHAR2', 'comment': '保单号'},
                ],
            }],
        }
        dialog = ObjectPickDialog('zh', snap, mode='field')
        self.assertTrue(dialog.search.placeholderText().startswith('搜索表名'))
        self.assertTrue(dialog.field_search.placeholderText().startswith('搜索字段名'))
        self.assertIn('保单主表', dialog.obj_list.item(0).text())
        dialog.obj_list.setCurrentRow(0)
        names = [
            dialog.field_list.item(i).data(Qt.ItemDataRole.UserRole)['name']
            for i in range(dialog.field_list.count())
        ]
        self.assertEqual(names, ['POLICYNO', 'ZFLAG'])
        self.assertIn('保单号', dialog.field_list.item(0).text())
        dialog.field_search.setText('标志')
        self.assertEqual(dialog.field_list.count(), 1)
        self.assertEqual(dialog.field_list.item(0).data(Qt.ItemDataRole.UserRole)['name'], 'ZFLAG')
        dialog.close()


if __name__ == '__main__':
    unittest.main()
