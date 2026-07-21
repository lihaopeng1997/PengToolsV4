# -*- coding: utf-8 -*-
"""请求测试接口库 / 历史 / 分类 定向测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class IfaceRequestLibraryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmpdir.name, 'lib.json')
        self._patcher = mock.patch('tools.iface_request_library.LIBRARY_FILE', self.path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_normalize_has_uncategorized(self):
        from tools.iface_request_library import UNCATEGORIZED_ID, normalize_library
        lib = normalize_library({})
        self.assertTrue(any(c['id'] == UNCATEGORIZED_ID for c in lib['categories']))
        self.assertEqual(lib['history'], [])
        self.assertEqual(lib['apis'], [])

    def test_category_crud(self):
        from tools.iface_request_library import (
            UNCATEGORIZED_ID, add_category, delete_category, load_library,
            rename_category, save_library,
        )
        lib = save_library(load_library())
        lib = add_category(lib, '用户中心')
        self.assertTrue(any(c['name'] == '用户中心' for c in lib['categories']))
        cid = next(c['id'] for c in lib['categories'] if c['name'] == '用户中心')
        lib = rename_category(lib, cid, '账户')
        self.assertTrue(any(c['name'] == '账户' for c in lib['categories']))
        with self.assertRaises(ValueError):
            rename_category(lib, UNCATEGORIZED_ID, 'x')
        lib = delete_category(lib, cid)
        self.assertFalse(any(c['id'] == cid for c in lib['categories']))

    def test_upsert_api_and_filter(self):
        from tools.iface_request_library import (
            add_category, build_api_from_form, filter_items, load_library,
            save_library, upsert_api,
        )
        lib = save_library(load_library())
        lib = add_category(lib, '订单')
        cid = next(c['id'] for c in lib['categories'] if c['name'] == '订单')
        api = build_api_from_form(
            name='下单',
            category_id=cid,
            method='POST',
            url='http://localhost:1/api/order',
            body='{"a":1}',
            headers_text='Content-Type: application/json',
        )
        lib = upsert_api(lib, api)
        self.assertEqual(len(lib['apis']), 1)
        self.assertEqual(lib['apis'][0]['name'], '下单')
        # 更新同 id
        api['body'] = '{"a":2}'
        lib = upsert_api(lib, api)
        self.assertEqual(len(lib['apis']), 1)
        self.assertIn('"a":2', lib['apis'][0]['body'])
        hit = filter_items(lib['apis'], category_id=cid, keyword='下单')
        self.assertEqual(len(hit), 1)
        miss = filter_items(lib['apis'], category_id=cid, keyword='不存在')
        self.assertEqual(len(miss), 0)

    def test_history_cap_and_preview_clip(self):
        from tools.iface_request_library import (
            MAX_RESP_PREVIEW, append_history, build_history_from_send,
            load_library, save_library,
        )
        lib = save_library(load_library())
        lib['max_history'] = 5
        lib = save_library(lib)
        big = 'X' * (MAX_RESP_PREVIEW + 500)
        for i in range(8):
            entry = build_history_from_send(
                method='GET',
                url=f'http://localhost/{i}',
                status=200,
                ok=True,
                response_body=big if i == 0 else 'ok',
            )
            lib = append_history(lib, entry)
        self.assertEqual(len(lib['history']), 5)
        # 最新在前
        self.assertTrue(lib['history'][0]['url'].endswith('/7'))
        # 首条已滚动出
        urls = [h['url'] for h in lib['history']]
        self.assertNotIn('http://localhost/0', urls)
        # 截断标记
        entry0 = build_history_from_send(
            method='GET', url='http://localhost/clip', response_body=big,
        )
        self.assertLessEqual(len(entry0['response_preview']), MAX_RESP_PREVIEW + 40)
        self.assertIn('截断', entry0['response_preview'])

    def test_form_roundtrip(self):
        from tools.iface_request_library import (
            build_api_from_form, form_fields_from_item,
        )
        api = build_api_from_form(
            name='demo',
            category_id='uncategorized',
            method='put',
            url='http://h/p?q=1',
            headers_text='A: 1',
            params_text='q=1',
            body='{}',
            base_host='http://h',
        )
        form = form_fields_from_item(api)
        self.assertEqual(form['method'], 'PUT')
        self.assertEqual(form['url'], 'http://h/p?q=1')
        self.assertEqual(form['headers_text'], 'A: 1')
        self.assertEqual(form['body'], '{}')

    def test_delete_category_moves_apis(self):
        from tools.iface_request_library import (
            UNCATEGORIZED_ID, add_category, build_api_from_form, delete_category,
            load_library, save_library, upsert_api,
        )
        lib = save_library(load_library())
        lib = add_category(lib, '临时')
        cid = next(c['id'] for c in lib['categories'] if c['name'] == '临时')
        api = build_api_from_form(
            name='x', category_id=cid, method='GET', url='http://a/b',
        )
        lib = upsert_api(lib, api)
        lib = delete_category(lib, cid)
        self.assertEqual(lib['apis'][0]['category_id'], UNCATEGORIZED_ID)

    def test_persist_roundtrip_file(self):
        from tools.iface_request_library import (
            append_history, build_api_from_form, build_history_from_send,
            load_library, save_library, upsert_api,
        )
        lib = save_library({'version': 1})
        lib = upsert_api(lib, build_api_from_form(
            name='n', category_id='uncategorized', method='GET', url='http://x/y',
        ))
        lib = append_history(lib, build_history_from_send(
            method='GET', url='http://x/y', status=204, ok=True,
        ))
        reloaded = load_library()
        self.assertEqual(len(reloaded['apis']), 1)
        self.assertEqual(len(reloaded['history']), 1)
        with open(self.path, 'r', encoding='utf-8') as stream:
            raw = json.load(stream)
        self.assertEqual(raw['apis'][0]['name'], 'n')


class IfaceLibraryPanelSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_has_library_widgets(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        from tools.iface_request_library import build_api_from_form, upsert_api
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'lib.json')
            with mock.patch('tools.iface_request_library.LIBRARY_FILE', path), \
                 mock.patch('panels.interface_debug_panel.show_success'), \
                 mock.patch('panels.interface_debug_panel.show_warning'), \
                 mock.patch('panels.interface_debug_panel.show_info'), \
                 mock.patch('panels.interface_debug_panel.confirm_action', return_value=True):
                p = InterfaceDebugPanel(language='zh')
                self.assertTrue(hasattr(p, 'rt_lib_list'))
                self.assertTrue(hasattr(p, 'rt_save_api_btn'))
                self.assertTrue(hasattr(p, 'rt_category_combo'))
                self.assertTrue(hasattr(p, 'rt_lib_mode'))
                p.rt_url.setText('http://localhost:9/api/demo')
                p.rt_method.setCurrentText('POST')
                p.rt_body.setPlainText('{"k":1}')
                api = build_api_from_form(
                    name='demo',
                    category_id='uncategorized',
                    method='POST',
                    url='http://localhost:9/api/demo',
                    body='{"k":1}',
                )
                p._rt_lib = upsert_api(p._rt_lib_data(), api)
                p._rt_lib_refresh_list()
                self.assertGreaterEqual(p.rt_lib_list.count(), 1)
                p.rt_lib_list.setCurrentRow(0)
                p._rt_lib_apply_selected()
                self.assertIn('/api/demo', p.rt_url.text())
                p._rt_send_meta = {
                    'method': 'POST',
                    'url': 'http://localhost:9/api/demo',
                    'headers_text': '',
                    'params_text': '',
                    'body': '{"k":1}',
                    'base_host': 'http://localhost:9',
                    'category_id': 'uncategorized',
                }
                p._rt_send_started_at = 0
                p._rt_append_history_from_send(status=200, ok=True, response_body='{"ok":1}')
                for i in range(p.rt_lib_mode.count()):
                    if p.rt_lib_mode.itemData(i) == 'history':
                        p.rt_lib_mode.setCurrentIndex(i)
                        break
                self.assertGreaterEqual(p.rt_lib_list.count(), 1)
                p.close()


if __name__ == '__main__':
    unittest.main()
