# -*- coding: utf-8 -*-
"""接口排查 Fiddler 式工作台定向测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class SessionViewLogicTests(unittest.TestCase):
    def _sample(self):
        return [
            {
                'id': '1', 'method': 'GET', 'url': 'https://api.ex.com/v1/ok',
                'path': '/v1/ok', 'status': 200, 'duration_ms': 120,
                'mime_type': 'application/json', 'resource_type': 'XHR',
                'response_body': '{"ok":true}', 'started_at': 1000.0, 'source': 'cdp',
            },
            {
                'id': '2', 'method': 'POST', 'url': 'https://api.ex.com/v1/fail?token=sec',
                'path': '/v1/fail', 'status': 500, 'duration_ms': 2500,
                'mime_type': 'application/json', 'resource_type': 'Fetch',
                'response_body': '{"err":1}', 'started_at': 1001.0, 'source': 'cdp',
                'request_headers': {'Authorization': 'Bearer x'},
            },
            {
                'id': '3', 'method': 'GET', 'url': 'https://cdn.ex.com/a.css',
                'path': '/a.css', 'status': 200, 'duration_ms': 40,
                'mime_type': 'text/css', 'resource_type': 'Stylesheet',
                'started_at': 1002.0, 'source': 'cdp',
            },
            {
                'id': '4', 'method': 'GET', 'url': 'https://api.ex.com/slow',
                'path': '/slow', 'status': 200, 'duration_ms': 4000,
                'mime_type': 'text/xml', 'resource_type': 'XHR',
                'response_body': '<root/>', 'started_at': 1003.0, 'source': 'ie_proxy',
            },
        ]

    def test_content_kind_and_size(self):
        from tools.interface_session_view import content_kind, format_size, response_size_bytes
        recs = self._sample()
        self.assertEqual(content_kind(recs[0]), 'JSON')
        self.assertEqual(content_kind(recs[3]), 'XML')
        self.assertEqual(content_kind(recs[2]), '脚本')
        n = response_size_bytes(recs[0])
        self.assertGreater(n, 0)
        self.assertIn('B', format_size(n))

    def test_filters_combinable(self):
        from tools.interface_session_view import (
            FILTER_FAILED, FILTER_JSON_XML, FILTER_SLOW, FILTER_STATIC, FILTER_XHR,
            filter_and_sort,
        )
        recs = self._sample()
        failed = filter_and_sort(recs, filters=[FILTER_FAILED])
        self.assertEqual([r['id'] for r in failed], ['2'])
        slow = filter_and_sort(recs, filters=[FILTER_SLOW])
        self.assertEqual(set(r['id'] for r in slow), {'2', '4'})
        xhr = filter_and_sort(recs, filters=[FILTER_XHR])
        self.assertNotIn('3', [r['id'] for r in xhr])
        jx = filter_and_sort(recs, filters=[FILTER_JSON_XML])
        self.assertEqual(set(r['id'] for r in jx), {'1', '2', '4'})
        static = filter_and_sort(recs, filters=[FILTER_STATIC], show_static=True)
        self.assertTrue(any(r['id'] == '3' for r in static))
        # 默认隐藏静态
        all_default = filter_and_sort(recs, filters=['all'], show_static=False)
        self.assertNotIn('3', [r['id'] for r in all_default])

    def test_search_and_sort(self):
        from tools.interface_session_view import filter_and_sort
        recs = self._sample()
        hit = filter_and_sort(recs, query='fail token')
        self.assertEqual([r['id'] for r in hit], ['2'])
        by_dur = filter_and_sort(recs, filters=['all'], sort_key='duration', sort_desc=True, show_static=True)
        self.assertEqual(by_dur[0]['id'], '4')

    def test_pretty_body(self):
        from tools.interface_session_view import pretty_body
        kind, text, err = pretty_body('{"a":1}')
        self.assertEqual(kind, 'json')
        self.assertIn('\n', text)
        self.assertIsNone(err)
        kind, text, err = pretty_body('{bad')
        self.assertEqual(kind, 'json')
        self.assertIsNotNone(err)

    def test_ui_prefs_no_payload(self):
        from tools.interface_debug_store import load_interface_debug_config, save_interface_debug_config
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'interface_debug.json')
            save_interface_debug_config({
                'ui_prefs': {
                    'visible_columns': ['status', 'method', 'path', 'size'],
                    'sort_key': 'duration',
                    'active_filters': ['failed'],
                }
            }, path=path)
            cfg = load_interface_debug_config(path)
            self.assertIn('size', cfg['ui_prefs']['visible_columns'])
            self.assertEqual(cfg['ui_prefs']['sort_key'], 'duration')
            raw = open(path, encoding='utf-8').read()
            self.assertNotIn('request_body', raw)
            self.assertNotIn('Authorization', raw)
            self.assertNotIn('Bearer', raw)


try:
    from PyQt6.QtWidgets import QApplication
    from panels.interface_debug_panel import InterfaceDebugPanel
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'PyQt6 missing')
class FiddlerPanelSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_four_detail_tabs_and_columns(self):
        p = InterfaceDebugPanel('zh')
        self.assertEqual(p.detail_tabs.count(), 4)
        self.assertEqual(p.table.columnCount(), 8)
        # 注入假数据
        p._records_by_id = {
            'a': {
                'id': 'a', 'method': 'GET', 'url': 'https://x.com/api?token=1',
                'path': '/api', 'status': 200, 'duration_ms': 1500,
                'mime_type': 'application/json', 'resource_type': 'XHR',
                'response_body': '{"x":1}', 'request_headers': {'Authorization': 'Bearer t'},
                'started_at': 1.0, 'source': 'cdp',
            }
        }
        p._records = list(p._records_by_id.values())
        p._rebuild_table()
        self.assertGreaterEqual(p.table.rowCount(), 1)
        p.table.selectRow(0)
        p._refresh_detail()
        self.assertIn('URL', p.overview_edit.toPlainText())
        self.assertIn('********', p.req_detail.toPlainText())  # 脱敏
        p.reveal_cb.blockSignals(True)
        p._reveal_sensitive = True
        p.reveal_cb.blockSignals(False)
        p._refresh_detail()
        self.assertIn('Bearer', p.req_detail.toPlainText())
        p.clear_session()
        self.assertEqual(p._records, [])
        self.assertEqual(p.table.rowCount(), 0)

    def test_layout_mode_keeps_start_stop(self):
        p = InterfaceDebugPanel('zh')
        p.apply_layout_mode('narrow', True)
        self.assertFalse(p.connect_btn.isHidden())
        self.assertFalse(p.stop_btn.isHidden())
        p.apply_layout_mode('wide', False)
        self.assertFalse(p.mode_combo.isHidden())

    def test_shutdown_clears_memory(self):
        p = InterfaceDebugPanel('zh')
        p._records = [{'id': '1'}]
        p._records_by_id = {'1': {'id': '1', 'url': 'http://x'}}
        p.shutdown_cleanup()
        self.assertEqual(p._records, [])
        self.assertEqual(p._records_by_id, {})


if __name__ == '__main__':
    unittest.main()
