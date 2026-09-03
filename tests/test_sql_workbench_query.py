# -*- coding: utf-8 -*-
"""SQL 工作台：分页 / Fetch All / 50MiB / 补全 / 布局。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.db_connect import (
    PAGE_SIZE, RESULT_BYTE_LIMIT, estimate_cell_bytes, estimate_row_bytes,
    fetch_query_chunks, run_read_query,
)


TOTAL_ROWS = 3500


def _fake_page(_conn, _dialect, sql, *, offset=0, limit=20):
    start = int(offset)
    end = min(start + int(limit), TOTAL_ROWS)
    rows = [[i, f'r{i}'] for i in range(start, end)]
    return {
        'columns': ['ID', 'NAME'],
        'rows': rows,
        'offset': end,
        'limit': int(limit),
        'has_more': end < TOTAL_ROWS,
        'sql': sql,
    }


class EstimateBytesTests(unittest.TestCase):
    def test_cell_types_deterministic(self):
        self.assertEqual(estimate_cell_bytes(None), 0)
        self.assertEqual(estimate_cell_bytes(12), len(b'12'))
        self.assertEqual(estimate_cell_bytes(1.5), len('1.5'.encode()))
        self.assertEqual(estimate_cell_bytes('ab'), 2)
        self.assertEqual(estimate_cell_bytes(b'xy'), 2)
        self.assertGreater(estimate_cell_bytes(datetime(2026, 1, 2, 3, 4, 5)), 0)
        self.assertGreater(estimate_cell_bytes(date(2026, 1, 2)), 0)
        self.assertGreater(estimate_cell_bytes({'k': 1}), 0)

    def test_cell_truncates_like_display(self):
        long = 'x' * 500
        n = estimate_cell_bytes(long)
        self.assertLess(n, 500)
        self.assertEqual(n, estimate_row_bytes([long]))


class PaginationAndFetchTests(unittest.TestCase):
    def test_default_page_size(self):
        self.assertEqual(PAGE_SIZE, 20)

    def test_pages_beyond_2000(self):
        class _Cur:
            description = [('ID',)]

            def __init__(self, n):
                self._n = n

            def execute(self, _sql):
                return None

            def fetchall(self):
                return [(i,) for i in range(self._n)]

            def close(self):
                return None

        def run_with(offset, limit, n):
            with patch('tools.db_connect._cursor', return_value=_Cur(n)):
                return run_read_query(object(), 'mysql', 'SELECT id FROM t', offset=offset, limit=limit)

        first = run_with(0, PAGE_SIZE, 20)
        self.assertEqual(len(first['rows']), 20)
        self.assertTrue(first['has_more'])
        second = run_with(20, PAGE_SIZE, 20)
        self.assertEqual(len(second['rows']), 20)
        self.assertTrue(second['has_more'])
        late = run_with(2000, PAGE_SIZE, 20)
        self.assertEqual(len(late['rows']), 20)
        self.assertTrue(late['has_more'])
        last = run_with(3480, PAGE_SIZE, 19)
        self.assertEqual(len(last['rows']), 19)
        self.assertFalse(last['has_more'])

    def test_fetch_all_to_eof_no_2000_cap(self):
        calls = []

        def counted(*args, **kwargs):
            calls.append(kwargs.get('limit'))
            return _fake_page(*args, **kwargs)

        with patch('tools.db_connect.run_read_query', side_effect=counted):
            result = fetch_query_chunks(None, 'oracle', 'SELECT 1 FROM dual', chunk_size=500)
        self.assertEqual(result['status'], 'DONE')
        self.assertEqual(len(result['rows']), TOTAL_ROWS)
        self.assertGreater(len(calls), 2)
        self.assertTrue(all(c == 500 for c in calls))

    def test_byte_limit_injected(self):
        def fat_page(_conn, _dialect, sql, *, offset=0, limit=20):
            rows = [['x' * 50] for _ in range(int(limit))]
            end = int(offset) + int(limit)
            return {
                'columns': ['C'],
                'rows': rows,
                'offset': end,
                'has_more': True,
                'sql': sql,
            }

        with patch('tools.db_connect.run_read_query', side_effect=fat_page):
            result = fetch_query_chunks(
                None, 'oracle', 'SELECT 1', chunk_size=10, byte_limit=80,
            )
        self.assertEqual(result['status'], 'LIMIT_REACHED')
        self.assertTrue(result['rows'])
        self.assertGreaterEqual(result['bytes'], 80)
        self.assertEqual(RESULT_BYTE_LIMIT, 50 * 1024 * 1024)

    def test_cancel_after_two_chunks(self):
        state = {'n': 0}

        def cancel():
            return state['n'] >= 2

        def page(*args, **kwargs):
            state['n'] += 1
            return _fake_page(*args, **kwargs)

        with patch('tools.db_connect.run_read_query', side_effect=page):
            result = fetch_query_chunks(
                None, 'oracle', 'SELECT 1', chunk_size=100, cancel=cancel,
            )
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(len(result['rows']), 200)
        self.assertEqual(state['n'], 2)

    def test_progress_monotonic(self):
        seen = []

        def progress(payload):
            seen.append((payload['rows'], payload['bytes']))

        with patch('tools.db_connect.run_read_query', side_effect=_fake_page):
            fetch_query_chunks(
                None, 'oracle', 'SELECT 1', chunk_size=500, progress=progress,
            )
        self.assertTrue(seen)
        rows = [item[0] for item in seen]
        nbytes = [item[1] for item in seen]
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(nbytes, sorted(nbytes))
        self.assertGreater(rows[-1], rows[0])


from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QMenu
from panels.ai_token_edit import AiPromptEdit
from panels.ai_workbench_panel import AiWorkbenchPanel
from ui.sql_editor import SqlEditor
from ui.splitter_prefs import SPLITTER_HANDLE_WIDTH


class SqlEditorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_s_prefix_select(self):
        editor = SqlEditor()
        hits = editor._completion_candidates('s')
        self.assertIn('SELECT', hits)
        editor.deleteLater()

    def test_w_and_ord(self):
        editor = SqlEditor()
        w = editor._completion_candidates('w')
        self.assertTrue({'WHERE', 'WITH', 'WHEN'} <= set(w) or all(x in w for x in ('WHERE', 'WITH')))
        self.assertIn('WHERE', w)
        self.assertIn('WITH', w)
        self.assertIn('ORDER BY', editor._completion_candidates('ord'))
        editor.deleteLater()

    def test_schema_table_and_column(self):
        editor = SqlEditor()
        editor.bind_schema({
            'objects': [{
                'name': 'USER_TABLE',
                'owner': 'SCOTT',
                'columns': [{'name': 'ID'}, {'name': 'NAME'}],
            }],
        })
        tables = editor._completion_candidates('USR')
        self.assertTrue(any('USER_TABLE' in item for item in tables))
        cols = editor._completion_candidates('USER_TABLE.')
        self.assertTrue(any(item.endswith('ID') or item.endswith('NAME') for item in cols))
        editor.deleteLater()

    def test_insert_replaces_token(self):
        editor = SqlEditor()
        editor.setPlainText('s')
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        editor._insert_completion('SELECT')
        self.assertEqual(editor.toPlainText(), 'SELECT')
        editor.setPlainText('ord')
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        editor._insert_completion('ORDER BY')
        self.assertEqual(editor.toPlainText(), 'ORDER BY')
        editor.deleteLater()

    def test_selection_qss_high_contrast(self):
        editor = SqlEditor()
        qss = editor.styleSheet()
        self.assertIn('sql-editor', qss)
        self.assertIn('selection-background-color', qss)
        self.assertIn('selection-color', qss)
        editor.deleteLater()

    def test_sql_menu_skips_disabled(self):
        editor = SqlEditor()
        captured = []

        def fake_exec(self, *args, **kwargs):
            captured.extend(a.text() for a in self.actions() if not a.isSeparator())
            return None

        with patch.object(QMenu, 'exec', fake_exec):
            editor._show_menu(QPoint(0, 0))
        self.assertNotIn('撤销', captured)
        self.assertNotIn('剪切', captured)
        editor.deleteLater()


class AiLayoutMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        with patch('panels.ai_workbench_panel.load_connections', return_value=[]):
            return AiWorkbenchPanel(language='zh')

    def test_ai_assistant_layout_contract(self):
        """验证 AI 助手生产布局：
        - AI page 不存在内部垂直 QSplitter (ai_vsplit 已彻底废除)
        - nl_input 不使用 stretch=1 抢剩余空间
        - prompt 有合理受控的 maximumHeight (120~170)
        - ai_explain SizePolicy vertical == Expanding
        - agent_candidates 具备最大高度约束 (<= 160)
        - generate/pick/more/cancel 等业务按钮依然完整可用
        """
        panel = self._panel()
        try:
            # 1. 不存在内部垂直 splitter
            self.assertFalse(hasattr(panel, 'ai_vsplit'))
            ai_tab = panel.side_tabs.widget(0)
            from PyQt6.QtWidgets import QSplitter, QSizePolicy
            v_splitters = [w for w in ai_tab.findChildren(QSplitter) if w.orientation() == Qt.Orientation.Vertical]
            self.assertEqual(len(v_splitters), 0)

            # 2. nl_input 高度受控，不抢占剩余空间
            self.assertGreaterEqual(panel.nl_input.minimumHeight(), 100)
            self.assertLessEqual(panel.nl_input.maximumHeight(), 170)
            layout = ai_tab.layout()
            self.assertEqual(layout.stretch(layout.indexOf(panel.nl_input)), 0)

            # 3. ai_explain 是剩余空间的主要消费者
            self.assertEqual(panel.ai_explain.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
            self.assertEqual(layout.stretch(layout.indexOf(panel.ai_explain)), 1)

            # 4. agent_candidates 具有最大高度上限
            self.assertLessEqual(panel.agent_candidates.maximumHeight(), 160)

            # 5. 核心交互按钮完整
            self.assertTrue(panel.ai_gen_btn.text().startswith('生成'))
            self.assertIsNotNone(panel.ai_pick_btn)
            self.assertIsNotNone(panel.agent_more)
            self.assertIsNotNone(panel.agent_cancel_btn)
        finally:
            panel.deleteLater()

    def test_prompt_menu_no_disabled_standard(self):
        edit = AiPromptEdit()
        captured = []

        def fake_exec(self, *args, **kwargs):
            captured.extend(a.text() for a in self.actions() if not a.isSeparator())
            return None

        with patch.object(QMenu, 'exec', fake_exec):
            edit._show_menu(QPoint(0, 0))
        self.assertTrue(any('表' in t for t in captured))
        self.assertNotIn('剪切', captured)
        self.assertNotIn('撤销', captured)
        self.assertNotIn('删除', captured)
        edit.deleteLater()

    def test_output_menu_copy_select_only(self):
        panel = self._panel()
        panel.ai_explain.setPlainText('hello')
        captured = []

        def fake_exec(self, *args, **kwargs):
            captured.extend(a.text() for a in self.actions() if not a.isSeparator())
            return None

        with patch.object(QMenu, 'exec', fake_exec):
            panel._ai_output_menu(QPoint(0, 0))
        self.assertEqual(captured, ['全选'])
        self.assertNotIn('剪切', captured)
        self.assertNotIn('粘贴', captured)
        panel.deleteLater()


class RedisClusterGuiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_db_worker_run_redis_dict_offset_not_int(self):
        """D. Real GUI worker path: _DbWorker.run() with Redis query + dict offset 不得 int(dict)，验证收到 cursor state"""
        from panels.ai_workbench_panel import _DbWorker

        worker = _DbWorker(
            kind='query',
            item={'dialect': 'redis', 'name': 'test-cluster'},
            sql='SCAN 0',
            offset={'node-a': 100, 'node-b': 0},
            limit=10,
        )
        fake_conn = MagicMock()
        completed_results = []
        worker.completed.connect(lambda res: completed_results.append(res))

        with patch('panels.ai_workbench_panel.open_connection', return_value=fake_conn), \
             patch('panels.ai_workbench_panel.close_connection'), \
             patch('panels.ai_workbench_panel.run_console_statement') as mock_stmt:
            mock_stmt.return_value = {'rows': [['k1']], 'offset': {'node-a': 50, 'node-b': 0}, 'has_more': True}
            worker.run()
            self.assertEqual(len(completed_results), 1)
            mock_stmt.assert_called_once_with(
                fake_conn, 'redis', 'SCAN 0',
                offset={'node-a': 100, 'node-b': 0},
                limit=10,
            )

    def test_panel_query_result_preserves_dict_offset_and_fetch_next(self):
        """E. Real Panel result path: 接收 payload['offset'] = dict 不抛 TypeError，_offset 为 dict，_fetch_next 正确传递"""
        panel = AiWorkbenchPanel(language='zh')
        try:
            panel._query_status = 'query'
            payload = {
                'sql': 'SCAN 0',
                'offset': {'node-a': 100, 'node-b': 0},
                'has_more': True,
                'rows': [['k1'], ['k2']],
                'columns': ['key'],
                'elapsed_ms': 15,
                'warning': 'Redis 节点扫描不完整，失败节点: node-c',
            }
            panel._on_db_ok('query', payload, kwargs={'append': False})
            self.assertIsInstance(panel._offset, dict)
            self.assertEqual(panel._offset, {'node-a': 100, 'node-b': 0})
            self.assertTrue(panel._has_more)
            self.assertIn('node-c', panel.result_status.text())

            with patch.object(panel, '_start_db') as mock_start:
                panel._fetch_next()
                mock_start.assert_called_once_with(
                    'query',
                    sql='SCAN 0',
                    offset={'node-a': 100, 'node-b': 0},
                    limit=20,
                    append=True,
                )
        finally:
            panel.deleteLater()

    def test_fetch_all_guard_when_offset_is_dict(self):
        """F. Fetch All guard: 当 _offset 为 dict 且 has_more=True 时，next_btn enabled, all_btn disabled；调用 _fetch_all 不启动 worker 不 int(dict)"""
        panel = AiWorkbenchPanel(language='zh')
        try:
            panel._offset = {'node-a': 100, 'node-b': 0}
            panel._has_more = True
            panel._last_sql = 'SCAN 0'
            panel._sync_fetch_buttons()

            self.assertTrue(panel.next_btn.isEnabled())
            self.assertFalse(panel.all_btn.isEnabled())

            with patch.object(panel, '_start_db') as mock_start:
                panel._fetch_all()
                mock_start.assert_not_called()
                self.assertIn('下一页', panel.result_status.text())
        finally:
            panel.deleteLater()

    def test_redis_scan_partial_sticky_across_pages_and_resets(self):
        """7 & 8. Partial warning must survive pagination and reset cleanly on new query."""
        panel = AiWorkbenchPanel(language='zh')
        try:
            panel._query_status = 'query'

            # Page 1: node-A failed, node-B has cursor=100, has_more=True
            page1_payload = {
                'sql': 'SCAN 0',
                'offset': {'node-B': 100},
                'has_more': True,
                'partial': True,
                'incomplete': True,
                'failed_nodes': ['node-A'],
                'warning': 'Redis 节点扫描不完整，失败节点: node-A',
                'rows': [['k1']],
                'columns': ['key'],
                'elapsed_ms': 10,
            }
            panel._on_db_ok('query', page1_payload, kwargs={'append': False})
            self.assertTrue(panel._redis_scan_partial)
            self.assertEqual(panel._redis_scan_failed_nodes, ['node-A'])
            self.assertIn('node-A', panel.result_status.text())
            self.assertIn('不完整', panel.result_status.text())

            # Page 2: node-B finishes cleanly with cursor=0, has_more=False, no new failures
            page2_payload = {
                'sql': 'SCAN 0',
                'offset': {'node-B': 0},
                'has_more': False,
                'partial': False,
                'incomplete': False,
                'failed_nodes': [],
                'rows': [['k2']],
                'columns': ['key'],
                'elapsed_ms': 12,
            }
            panel._on_db_ok('query', page2_payload, kwargs={'append': True})
            # 最终状态仍必须保留 sticky partial 与 node-A 告警
            self.assertTrue(panel._redis_scan_partial)
            self.assertEqual(panel._redis_scan_failed_nodes, ['node-A'])
            self.assertIn('node-A', panel.result_status.text())
            self.assertIn('不完整', panel.result_status.text())
            self.assertFalse(panel.next_btn.isEnabled())
            self.assertFalse(panel.all_btn.isEnabled())

            # 8. Reset regression: 执行新 query reset=True
            with patch.object(panel, '_current_conn', return_value={'name': 'test', 'dialect': 'redis'}), \
                 patch.object(panel, '_start_db'):
                panel._current_editor().setPlainText('GET mykey')
                panel._run_sql(reset=True)
            self.assertFalse(panel._redis_scan_partial)
            self.assertEqual(panel._redis_scan_failed_nodes, [])

            # 新的正常 query 完成后不再携带旧的 Redis partial warning
            clean_payload = {
                'sql': 'GET mykey',
                'offset': 0,
                'has_more': False,
                'rows': [['val']],
                'columns': ['value'],
                'elapsed_ms': 5,
            }
            panel._on_db_ok('query', clean_payload, kwargs={'append': False})
            self.assertFalse(panel._redis_scan_partial)
            self.assertNotIn('node-A', panel.result_status.text())
            self.assertNotIn('不完整', panel.result_status.text())
        finally:
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
