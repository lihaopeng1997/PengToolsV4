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
        self.assertGreaterEqual(p.rt_lib_list.parentWidget().minimumWidth(), 240)
        self.assertTrue(p.rt_lib_mode_label.isHidden())
        self.assertTrue(p.rt_lib_cat_label.isHidden())

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
        self.assertGreaterEqual(p.draft_preview.minimumHeight(), 220)
        p.resize(1100, 900)
        p.show()
        self.app.processEvents()
        p.rt_editor_response_splitter.setSizes([560, 320])
        self.app.processEvents()
        sizes = p.rt_editor_response_splitter.sizes()
        self.assertGreaterEqual(len(sizes), 2)
        self.assertGreaterEqual(sizes[0], sizes[1])

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

    def test_detail_summary_includes_capture_time_and_current_environment(self):
        p = InterfaceDebugPanel('zh')
        p._config['local_targets'] = [{'id': 'env-1', 'name': '测试环境', 'base_url': 'https://test.example.com'}]
        p.local_target_combo.addItem('测试环境', 'env-1')
        p.local_target_combo.setCurrentIndex(p.local_target_combo.count() - 1)
        p._records_by_id = {
            'context': {
                'id': 'context', 'method': 'GET', 'url': 'https://x.com/api?token=secret',
                'status': 200, 'duration_ms': 150, 'mime_type': 'application/json',
                'response_body': '{"ok":true}', 'started_at': 1.0, 'source': 'http_capture',
            }
        }
        p._selected_id = 'context'
        p._refresh_detail()
        self.assertIn('时间', p.detail_summary.text())
        self.assertIn('环境 测试环境', p.detail_summary.text())
        self.assertNotIn('secret', p.detail_summary.text())

    def test_detail_environment_context_uses_name_without_base_url(self):
        p = InterfaceDebugPanel('zh')
        p._config['local_targets'] = [{'id': 'env-private', 'name': '内网测试', 'base_url': 'https://host/?token=private'}]
        p.local_target_combo.clear()
        p.local_target_combo.addItem('内网测试 · https://host/?token=private', 'env-private')
        p._records_by_id = {'r': {'id': 'r', 'method': 'GET', 'url': 'https://x.com/api', 'status': 200, 'started_at': 1.0}}
        p._selected_id = 'r'
        p._refresh_detail()
        self.assertIn('环境 内网测试', p.detail_summary.text())
        self.assertNotIn('private', p.detail_summary.text())

    def test_detail_workspace_keeps_summary_and_readable_response(self):
        p = InterfaceDebugPanel('zh')
        self.assertEqual(
            [p.detail_tabs.tabText(i) for i in range(p.detail_tabs.count())],
            ['概览', '请求', '响应', '请求验证'],
        )
        self.assertGreaterEqual(p.resp_detail.minimumHeight(), 240)
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

    def test_compact_session_view_column_menu_matches_visible_columns(self):
        p = InterfaceDebugPanel('zh')
        actions = {action.text(): action for action in p._cols_menu.actions()}
        for key, label in p.COL_LABELS_ZH.items():
            action = actions[label]
            self.assertEqual(action.isChecked(), not p.table.isColumnHidden(p._column_index(key)))
            self.assertFalse(action.isEnabled())

    def test_session_list_uses_compact_two_line_diagnostics_view(self):
        p = InterfaceDebugPanel('zh')
        p._records_by_id = {
            'two-line': {
                'id': 'two-line', 'seq': 1, 'method': 'GET',
                'url': 'https://api.example.com/v1/orders?trace=1', 'path': '/v1/orders',
                'host': 'api.example.com', 'scheme': 'https', 'status': 200,
                'duration_ms': 125, 'mime_type': 'application/json',
                'resource_type': 'XHR', 'response_body': '{"ok":true}',
                'started_at': 1.0, 'source': 'http_capture',
            }
        }
        p._records = list(p._records_by_id.values())
        p._rebuild_table()
        self.assertGreaterEqual(p.table.verticalHeader().defaultSectionSize(), 48)
        self.assertIn('\n', p.table.item(0, p._column_index('url')).text())
        for key in ('seq', 'protocol', 'name', 'host', 'body', 'type'):
            self.assertTrue(p.table.isColumnHidden(p._column_index(key)))

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

    def test_narrow_layout_can_hide_left_session_pane_without_hiding_capture(self):
        p = InterfaceDebugPanel('zh')
        p.apply_layout_mode('narrow', True)
        self.assertFalse(p.capture_toggle_btn.isHidden())
        self.assertFalse(p._toggle_list_btn.isHidden())
        p._toggle_session_list()
        self.assertTrue(p._session_list_widget.isHidden())
        self.assertFalse(p.session_list_reveal_btn.isHidden())
        p.session_list_reveal_btn.click()
        self.assertFalse(p._session_list_widget.isHidden())

    def test_wide_layout_defaults_prioritize_detail_pane(self):
        p = InterfaceDebugPanel('zh')
        p._prefs['splitter_sizes']['wide'] = [340, 680]
        p.apply_layout_mode('wide', False)
        sizes = p.mid_splitter.sizes()
        self.assertGreater(sizes[1], sizes[0] * 1.7)

    def test_reapplying_same_layout_preserves_dragged_splitter_sizes(self):
        p = InterfaceDebugPanel('zh')
        p.resize(1280, 800)
        p.show()
        self.app.processEvents()
        p.apply_layout_mode('wide', False)
        self.app.processEvents()
        p.mid_splitter.setSizes([500, 780])
        self.app.processEvents()
        expected_sizes = list(p.mid_splitter.sizes())
        p.apply_layout_mode('wide', False)
        self.app.processEvents()
        self.assertEqual(p.mid_splitter.sizes(), expected_sizes)

    def test_workspace_places_capture_and_session_tools_inside_left_pane(self):
        p = InterfaceDebugPanel('zh')
        self.assertTrue(hasattr(p, 'capture_zone'))
        self.assertTrue(hasattr(p, 'session_toolbar_scroll'))
        self.assertTrue(p.capture_zone.isHidden())
        self.assertIs(p.capture_zone.parentWidget(), p)
        self.assertFalse(p.status_label.isVisible())
        self.assertFalse(p.live_status.isVisible())
        # P0：抓包主按钮在页头，不再塞进会话筛选条
        from PyQt6.QtWidgets import QFrame
        header = p.findChild(QFrame, 'page-header')
        self.assertIsNotNone(header)
        self.assertTrue(header.isAncestorOf(p.capture_toggle_btn))
        self.assertIs(p.session_toolbar_scroll.parentWidget(), p._session_list_widget)
        self.assertIs(p.session_toolbar_scroll.widget(), p.session_toolbar)
        self.assertTrue(p.session_toolbar_scroll.widgetResizable())
        self.assertEqual(p.session_toolbar.minimumWidth(), 0)
        self.assertIs(p.mid_splitter.widget(0), p._session_list_widget)
        self.assertIs(p.mid_splitter.widget(1), p.detail_workspace)
        self.assertEqual(p.session_toolbar_scroll.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(p.session_toolbar_scroll.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_session_toolbar_moves_optional_actions_into_overflow_when_left_pane_is_narrow(self):
        p = InterfaceDebugPanel('zh')
        p._update_session_toolbar_overflow(420)
        self.assertFalse(p.session_actions_more_btn.isHidden())
        self.assertTrue(all(chip.isHidden() for chip in p._filter_chips.values()))
        self.assertTrue(p.export_list_btn.isHidden())
        self.assertTrue(p.clear_list_btn.isHidden())
        self.assertFalse(p._toggle_list_btn.isHidden())
        action_labels = [action.text() for action in p._session_actions_menu.actions()]
        self.assertIn('导出会话明细', action_labels)
        self.assertIn('清空会话', action_labels)
        self.assertIn('隐藏会话列表', action_labels)

        p._update_session_toolbar_overflow(960)
        self.assertTrue(p.session_actions_more_btn.isHidden())
        self.assertTrue(all(not chip.isHidden() for chip in p._filter_chips.values()))
        self.assertFalse(p.export_list_btn.isHidden())
        self.assertFalse(p.clear_list_btn.isHidden())
        self.assertFalse(p._toggle_list_btn.isHidden())

    def test_all_action_rows_and_session_columns_adapt_to_available_workspace_width(self):
        p = InterfaceDebugPanel('zh')
        p._update_responsive_workspace(left_width=360, right_width=320, table_width=300)
        self.assertFalse(p.capture_actions_more_btn.isHidden())
        self.assertTrue(p.test_listen_btn.isHidden())
        self.assertTrue(p.restore_proxy_btn.isHidden())
        self.assertFalse(p.req_actions_more_btn.isHidden())
        self.assertTrue(p.format_req_btn.isHidden())
        self.assertTrue(p.gateway_req_btn.isHidden())
        self.assertFalse(p.resp_actions_more_btn.isHidden())
        self.assertTrue(p.format_resp_btn.isHidden())
        self.assertTrue(p.gateway_resp_btn.isHidden())
        self.assertFalse(p.rt_io_more_btn.isHidden())
        self.assertTrue(p.export_detail_btn.isHidden())
        self.assertTrue(p.rt_import_btn.isHidden())
        self.assertTrue(p.table.isColumnHidden(p._column_index('duration')))
        self.assertTrue(p.table.isColumnHidden(p._column_index('time')))
        self.assertFalse(p.table.isColumnHidden(p._column_index('status')))
        self.assertFalse(p.table.isColumnHidden(p._column_index('method')))
        self.assertFalse(p.table.isColumnHidden(p._column_index('url')))

        p._update_responsive_workspace(left_width=960, right_width=860, table_width=760)
        self.assertTrue(p.capture_actions_more_btn.isHidden())
        self.assertFalse(p.test_listen_btn.isHidden())
        self.assertFalse(p.restore_proxy_btn.isHidden())
        self.assertTrue(p.req_actions_more_btn.isHidden())
        self.assertFalse(p.format_req_btn.isHidden())
        self.assertFalse(p.gateway_req_btn.isHidden())
        self.assertTrue(p.resp_actions_more_btn.isHidden())
        self.assertFalse(p.format_resp_btn.isHidden())
        self.assertFalse(p.gateway_resp_btn.isHidden())
        self.assertTrue(p.rt_io_more_btn.isHidden())
        self.assertFalse(p.export_detail_btn.isHidden())
        self.assertFalse(p.rt_import_btn.isHidden())
        self.assertFalse(p.table.isColumnHidden(p._column_index('duration')))
        self.assertFalse(p.table.isColumnHidden(p._column_index('time')))

    def test_request_test_secondary_actions_move_into_overflow_without_hiding_send(self):
        p = InterfaceDebugPanel('zh')
        p._update_responsive_workspace(left_width=760, right_width=420, table_width=720)
        self.assertFalse(p.rt_form_more_btn.isHidden())
        self.assertFalse(p.rt_send_btn.isHidden())
        for widget in (
            p.rt_environment_config_btn, p.rt_fill_btn, p.rt_filter_config_btn,
            p.rt_save_api_btn, p.rt_manage_cat_btn,
        ):
            self.assertTrue(widget.isHidden())
        labels = [action.text() for action in p._rt_form_actions_menu.actions()]
        self.assertIn('环境配置', labels)
        self.assertIn('从会话填充', labels)
        self.assertIn('过滤配置', labels)
        self.assertIn('保存接口', labels)
        self.assertIn('分类管理', labels)

        p._update_responsive_workspace(left_width=760, right_width=860, table_width=720)
        self.assertTrue(p.rt_form_more_btn.isHidden())
        for widget in (
            p.rt_environment_config_btn, p.rt_fill_btn, p.rt_filter_config_btn,
            p.rt_save_api_btn, p.rt_manage_cat_btn,
        ):
            self.assertFalse(widget.isHidden())

    def test_compact_overflow_labels_refresh_after_language_switch(self):
        p = InterfaceDebugPanel('zh')
        p._update_responsive_workspace(left_width=360, right_width=320, table_width=300)
        p.set_language('en')
        self.assertEqual(p.session_actions_more_btn.text(), 'More')
        self.assertEqual(p.capture_actions_more_btn.text(), 'More')
        self.assertIn('Test connection', [action.text() for action in p._capture_actions_menu.actions()])
        self.assertIn('Export session details', [action.text() for action in p._session_actions_menu.actions()])

    def test_result_columns_recalculate_after_deferred_splitter_resize(self):
        p = InterfaceDebugPanel('zh')
        p._update_responsive_workspace(left_width=760, right_width=760, table_width=760)
        self.assertFalse(p.table.isColumnHidden(p._column_index('duration')))
        self.assertFalse(p.table.isColumnHidden(p._column_index('time')))
        p._update_responsive_workspace(left_width=260, right_width=960, table_width=280)
        self.assertTrue(p.table.isColumnHidden(p._column_index('duration')))
        self.assertTrue(p.table.isColumnHidden(p._column_index('time')))

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
