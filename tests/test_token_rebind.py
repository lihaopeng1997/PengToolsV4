# -*- coding: utf-8 -*-
"""T3 回归：切换连接/快照时 token 作用域隔离，旧 token 不可串用。"""

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ai_object_context import (
    add_field,
    add_object,
    bind_context_to_snapshot,
    context_scope,
    empty_context,
    selected_field_names,
    selected_table_names,
)


def _snap(snapshot_id, fingerprint):
    return {'snapshot_id': snapshot_id, 'fingerprint': fingerprint}


class TokenRebindIsolationTests(unittest.TestCase):
    def _ctx_with_tokens(self):
        snap_a = _snap('sid-a', 'oracle|h1|1521|orcl|u1')
        ctx = empty_context(snap_a)
        token = add_object(ctx, {'owner': 'AUTO', 'name': 'PRPCMAIN', 'object_type': 'TABLE'})
        field = add_field(
            ctx, {'owner': 'AUTO', 'name': 'PRPCMAIN'},
            {'name': 'POLICYNO', 'data_type': 'VARCHAR2'},
        )
        return ctx, snap_a, token, field

    def test_switch_connection_invalidates_old_tokens(self):
        """切换连接（fingerprint 不同）：旧 token 作废，不能再被消费。"""
        ctx, _snap_a, token, field = self._ctx_with_tokens()
        snap_b = _snap('sid-b', 'oracle|h2|1521|orcl|u2')
        changed = bind_context_to_snapshot(ctx, snap_b)
        self.assertTrue(changed)
        self.assertEqual(ctx['selected_objects'], [])
        self.assertEqual(ctx['selected_fields'], [])
        self.assertEqual(context_scope(ctx), ('sid-b', 'oracle|h2|1521|orcl|u2'))
        # 旧 token 引用不能再从上下文中取出供 AI 草案/检索使用
        self.assertEqual(selected_table_names(ctx), [])
        self.assertEqual(selected_field_names(ctx), [])

    def test_snapshot_refresh_invalidates_old_tokens(self):
        """同一连接重新扫描（fingerprint 相同但 snapshot_id 更新）：旧 token 作废。"""
        ctx, _snap_a, _token, _field = self._ctx_with_tokens()
        changed = bind_context_to_snapshot(ctx, _snap('sid-a2', 'oracle|h1|1521|orcl|u1'))
        self.assertTrue(changed)
        self.assertEqual(ctx['selected_objects'], [])
        self.assertEqual(ctx['selected_fields'], [])

    def test_rebind_same_scope_keeps_tokens(self):
        """同快照重复绑定：正常添加流程不受影响，token 保留。"""
        ctx, snap_a, token, _field = self._ctx_with_tokens()
        changed = bind_context_to_snapshot(ctx, dict(snap_a))
        self.assertFalse(changed)
        self.assertEqual(len(ctx['selected_objects']), 1)
        self.assertEqual(len(ctx['selected_fields']), 1)
        self.assertEqual(ctx['selected_objects'][0]['token_id'], token['token_id'])

    def test_bind_none_snapshot_invalidates(self):
        """无连接（快照为空）：旧 token 作废，作用域置空。"""
        ctx, _snap_a, _token, _field = self._ctx_with_tokens()
        changed = bind_context_to_snapshot(ctx, None)
        self.assertTrue(changed)
        self.assertEqual(ctx['selected_objects'], [])
        self.assertEqual(context_scope(ctx), ('', ''))


class TokenRebindWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_bind_snapshot_clears_document_token_spans(self):
        """切换连接后，编辑器文档中的可视 token 片段一并失效移除。"""
        from panels.ai_token_edit import AiPromptEdit
        edit = AiPromptEdit()
        snap_a = _snap('sid-a', 'oracle|h1|1521|orcl|u1')
        edit.bind_snapshot(snap_a)
        token = add_object(edit.context, {'owner': 'AUTO', 'name': 'PRPCMAIN'})
        edit.insert_token('object', token, 0)
        edit.insertPlainText('帮我查一下 ')
        self.assertEqual(edit.token_ids_in_document(), [token['token_id']])
        self.assertIn('表：PRPCMAIN', edit.toPlainText())

        # 切换到另一个连接
        edit.bind_snapshot(_snap('sid-b', 'oracle|h2|1521|orcl|u2'))
        self.assertEqual(edit.context['selected_objects'], [])
        self.assertEqual(edit.token_ids_in_document(), [])
        self.assertNotIn('表：PRPCMAIN', edit.toPlainText())
        # 纯文本问题保留，不影响用户已输入的自然语言
        self.assertIn('帮我查一下', edit.toPlainText())
        edit.close()

    def test_bind_snapshot_same_scope_keeps_spans(self):
        """同快照重绑：可视 token 片段保留（不干扰正常添加流程）。"""
        from panels.ai_token_edit import AiPromptEdit
        edit = AiPromptEdit()
        snap_a = _snap('sid-a', 'oracle|h1|1521|orcl|u1')
        edit.bind_snapshot(snap_a)
        token = add_object(edit.context, {'owner': 'AUTO', 'name': 'PRPCMAIN'})
        edit.insert_token('object', token, 0)
        edit.bind_snapshot(dict(snap_a))
        self.assertEqual(edit.token_ids_in_document(), [token['token_id']])
        self.assertEqual(len(edit.context['selected_objects']), 1)
        edit.close()


if __name__ == '__main__':
    unittest.main()
