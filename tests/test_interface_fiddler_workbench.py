# -*- coding: utf-8 -*-
"""接口排查 Fiddler 式工作台定向测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

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
            # 旧 size/path 列名应映射到 body/url
            cols = cfg['ui_prefs']['visible_columns']
            self.assertTrue('body' in cols or 'size' in cols or 'url' in cols)
            self.assertEqual(cfg['ui_prefs']['sort_key'], 'duration')
            raw = open(path, encoding='utf-8').read()
            self.assertNotIn('request_body', raw)
            self.assertNotIn('Authorization', raw)
            self.assertNotIn('Bearer', raw)


try:
    from PyQt6.QtCore import Qt
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

    def test_request_test_has_environment_and_filter_config_entries(self):
        p = InterfaceDebugPanel('zh')
        self.assertTrue(hasattr(p, 'rt_environment_config_btn'))
        self.assertTrue(hasattr(p, 'rt_filter_config_btn'))
        self.assertFalse(p.rt_environment_config_btn.isHidden())
        self.assertFalse(p.rt_filter_config_btn.isHidden())
        for widget in (
            p.add_target_btn, p.edit_target_btn, p.del_target_btn,
            p.rt_save_env_btn, p.rt_url_filter_edit, p.rt_url_filter_save_btn,
        ):
            self.assertTrue(widget.isHidden())

    def test_library_history_actions_move_to_context_menu(self):
        p = InterfaceDebugPanel('zh')
        self.assertFalse(p.rt_lib_load_btn.isVisible())
        self.assertFalse(p.rt_lib_resend_btn.isVisible())
        self.assertFalse(p.rt_lib_del_btn.isVisible())
        self.assertFalse(p.rt_lib_clear_btn.isVisible())
        self.assertTrue(hasattr(p, 'rt_history_cleanup_btn'))
        self.assertTrue(p.rt_lib_list.contextMenuPolicy().name == 'CustomContextMenu')

    def test_history_fill_url_preserves_request_editor_content(self):
        p = InterfaceDebugPanel('zh')
        p._rt_lib = {
            'history': [{
                'id': 'history-1', 'method': 'POST', 'url': 'https://api.example.com/orders?trace=1',
                'headers_text': 'Authorization: Bearer private', 'body': '{"saved":true}',
            }],
            'apis': [], 'categories': [{'id': 'uncategorized', 'name': '未分类'}],
            'last_mode': 'history', 'last_category_id': 'uncategorized', 'max_history': 100,
        }
        p.rt_lib_mode.setCurrentIndex(1)
        p._rt_lib_refresh_list()
        p.rt_lib_list.setCurrentRow(0)
        p.rt_headers.setPlainText('X-Keep: editor')
        p.rt_body.setPlainText('{"editing":true}')
        with patch('panels.interface_debug_panel.show_success'):
            p._rt_fill_history_url()
        self.assertEqual(p.rt_url.text(), 'https://api.example.com/orders?trace=1')
        self.assertEqual(p.rt_headers.toPlainText(), 'X-Keep: editor')
        self.assertEqual(p.rt_body.toPlainText(), '{"editing":true}')

    def test_library_activation_fills_form_without_sending(self):
        p = InterfaceDebugPanel('zh')
        p._rt_lib = {
            'history': [],
            'apis': [{'id': 'api-1', 'name': '查询订单', 'method': 'GET', 'url': 'https://api.example.com/orders'}],
            'categories': [{'id': 'uncategorized', 'name': '未分类'}],
            'last_mode': 'library', 'last_category_id': 'uncategorized', 'max_history': 100,
        }
        p.rt_lib_mode.setCurrentIndex(0)
        p.rt_lib_cat_filter.setCurrentIndex(0)
        p._rt_lib_refresh_list()
        p.rt_lib_list.setCurrentRow(0)
        with patch.object(p, '_rt_send') as send, patch('panels.interface_debug_panel.show_success'), patch('panels.interface_debug_panel.show_warning'):
            p._rt_lib_apply_selected()
        self.assertEqual(p.rt_url.text(), 'https://api.example.com/orders')
        send.assert_not_called()

    def test_history_copy_curl_keeps_saved_full_url(self):
        p = InterfaceDebugPanel('zh')
        p._rt_lib = {
            'history': [{
                'id': 'history-curl', 'method': 'GET', 'url': 'https://api.example.com/orders?trace=1',
                'base_host': 'http://127.0.0.1:18031', 'headers_text': '', 'body': '',
            }],
            'apis': [], 'categories': [{'id': 'uncategorized', 'name': '未分类'}],
            'last_mode': 'history', 'last_category_id': 'uncategorized', 'max_history': 100,
        }
        p.rt_lib_mode.setCurrentIndex(1)
        p._rt_lib_refresh_list()
        p.rt_lib_list.setCurrentRow(0)
        with patch('panels.interface_debug_panel.show_success'):
            p._rt_copy_history_curl()
        self.assertIn('https://api.example.com/orders?trace=1', QApplication.clipboard().text())

    def test_request_test_uses_resizable_editor_response_splitter(self):
        p = InterfaceDebugPanel('zh')
        self.assertTrue(hasattr(p, 'rt_editor_response_splitter'))
        self.assertEqual(p.rt_editor_response_splitter.orientation(), Qt.Orientation.Vertical)
        self.assertGreaterEqual(p.draft_preview.minimumHeight(), 120)
        sizes = p.rt_editor_response_splitter.sizes()
        self.assertGreaterEqual(len(sizes), 2)
        self.assertGreater(sizes[1], sizes[0])

    def test_request_test_splitter_persists_only_visual_sizes(self):
        p = InterfaceDebugPanel('zh')
        p.rt_headers.setPlainText('Authorization: Bearer private-token')
        p.rt_body.setPlainText('{"request_body":"private"}')
        p.draft_preview.setPlainText('{"response_body":"private"}')
        p.rt_editor_response_splitter.setSizes([300, 700])
        expected_sizes = list(p.rt_editor_response_splitter.sizes())
        with patch('panels.interface_debug_panel.update_ui_prefs') as save:
            p._save_request_test_splitter_sizes()
        save.assert_called_once_with({'request_test_splitter_sizes': expected_sizes})

    def test_detail_workspace_keeps_summary_and_readable_response(self):
        p = InterfaceDebugPanel('zh')
        self.assertEqual(
            [p.detail_tabs.tabText(i) for i in range(p.detail_tabs.count())],
            ['概览', '请求', '响应', '请求测试'],
        )
        self.assertGreaterEqual(p.resp_detail.minimumHeight(), 180)
        self.assertTrue(p.resp_detail.isReadOnly())
        p._records_by_id = {
            'a': {
                'id': 'a', 'method': 'GET', 'url': 'https://x.com/api?token=secret',
                'status': 200, 'duration_ms': 150, 'mime_type': 'application/json',
                'response_body': '{"ok":true}', 'started_at': 1.0, 'source': 'http_capture',
            }
        }
        p._selected_id = 'a'
        p._refresh_detail()
        self.assertIn('GET', p.detail_summary.text())
        self.assertIn('200', p.detail_summary.text())
        self.assertNotIn('secret', p.detail_summary.text())
        p._reveal_sensitive = True
        p._refresh_detail()
        self.assertNotIn('secret', p.detail_summary.text())
        p.table.clearSelection()
        p._on_row_selected()
        self.assertTrue(p.detail_summary.isHidden())

    def test_four_detail_tabs_and_columns(self):
        p = InterfaceDebugPanel('zh')
        self.assertEqual(p.detail_tabs.count(), 4)
        # Fiddler 式列：# / 结果 / 协议 / 方法 / 名称 / 主机 / URL / Body / 类型 / 耗时 / 时间
        self.assertEqual(p.table.columnCount(), 11)
        # 注入假数据
        p._records_by_id = {
            'a': {
                'id': 'a', 'seq': 1, 'method': 'GET', 'url': 'https://x.com/api?token=1',
                'path': '/api', 'host': 'x.com', 'scheme': 'https', 'status': 200, 'duration_ms': 1500,
                'mime_type': 'application/json', 'resource_type': 'XHR',
                'response_body': '{"x":1}', 'request_headers': {'Authorization': 'Bearer t'},
                'started_at': 1.0, 'source': 'http_capture',
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

    def test_capture_control_is_one_stateful_action_and_keeps_proxy_tools(self):
        p = InterfaceDebugPanel('zh')
        self.assertTrue(hasattr(p, 'capture_toggle_btn'))
        self.assertFalse(p.capture_toggle_btn.isHidden())
        self.assertFalse(p.test_listen_btn.isHidden())
        self.assertFalse(p.restore_proxy_btn.isHidden())
        self.assertTrue(p.capture_toggle_btn.text())
        # 旧属性只保留给底层兼容逻辑，不能再作为可见操作入口。
        self.assertTrue(p.connect_btn.isHidden())
        self.assertTrue(p.stop_btn.isHidden())
        p.apply_layout_mode('wide', False)
        self.assertTrue(p.mode_combo.isHidden())
        self.assertEqual(p._mode, 'proxy')

    def test_capture_action_switches_without_clearing_session(self):
        p = InterfaceDebugPanel('zh')
        p._records = [{'id': '1'}]
        p._records_by_id = {'1': {'id': '1', 'url': 'http://x'}}
        p._listening = True
        p._set_listening_ui(True)
        self.assertIn('停止', p.capture_toggle_btn.text())
        self.assertTrue(p.connect_btn.isHidden())
        self.assertTrue(p.stop_btn.isHidden())
        p._listening = False
        p._set_listening_ui(False)
        self.assertIn('开始', p.capture_toggle_btn.text())
        self.assertEqual(p._records, [{'id': '1'}])
        p.set_language('en')
        p.apply_layout_mode('narrow', True)
        self.assertIn('Start', p.capture_toggle_btn.text())
        self.assertTrue(p.connect_btn.isHidden())
        self.assertTrue(p.stop_btn.isHidden())

    def test_shutdown_clears_memory(self):
        p = InterfaceDebugPanel('zh')
        p._records = [{'id': '1'}]
        p._records_by_id = {'1': {'id': '1', 'url': 'http://x'}}
        p.shutdown_cleanup()
        self.assertEqual(p._records, [])
        self.assertEqual(p._records_by_id, {})

    def test_strip_url_prefixes(self):
        from tools.iface_request_test import strip_url_prefixes
        # 基本剥离
        url = 'http://10.128.24.46:18888/prpcar-api/car/endorse/main/delete/endorse'
        result = strip_url_prefixes(url, ['/prpcar-api/car'])
        self.assertEqual(result, 'http://10.128.24.46:18888/endorse/main/delete/endorse')
        # 保留 query
        url2 = 'http://host:18888/prpcar-api/car/endorse?id=1'
        result2 = strip_url_prefixes(url2, ['/prpcar-api/car'])
        self.assertEqual(result2, 'http://host:18888/endorse?id=1')
        # 无匹配前缀不变
        url3 = 'http://host:18888/api/endorse'
        result3 = strip_url_prefixes(url3, ['/prpcar-api/car'])
        self.assertEqual(result3, 'http://host:18888/api/endorse')
        # 空前缀列表不变
        result4 = strip_url_prefixes(url, [])
        self.assertEqual(result4, url)
        # 多个前缀匹配第一个
        url5 = 'http://host/gw/api/test'
        result5 = strip_url_prefixes(url5, ['/prpcar-api/car', '/gw'])
        self.assertEqual(result5, 'http://host/api/test')


if __name__ == '__main__':
    unittest.main()
