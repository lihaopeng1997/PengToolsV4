# -*- coding: utf-8 -*-
"""接口排查：导出导入 / URL 替换 / 本机请求测试 定向测试。"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class IfaceRequestTestHelpers(unittest.TestCase):
    def test_normalize_base_host_defaults_http(self):
        from tools.iface_request_test import RequestTestError, normalize_base_host
        self.assertEqual(normalize_base_host('localhost:18031'), 'http://localhost:18031')
        self.assertEqual(normalize_base_host('http://127.0.0.1:9'), 'http://127.0.0.1:9')
        self.assertEqual(normalize_base_host(''), 'http://localhost:18031')
        # 允许用户保存的环境地址（非本机）
        self.assertEqual(normalize_base_host('http://uat.example.com:10110'), 'http://uat.example.com:10110')
        with self.assertRaises(RequestTestError):
            normalize_base_host('http://example.com:80/api')
        with self.assertRaises(RequestTestError):
            normalize_base_host('ftp://x')

    def test_rewrite_url_keeps_path_query(self):
        from tools.iface_request_test import rewrite_url_with_base
        out = rewrite_url_with_base(
            'http://xxx:10110/123/xxx/xxx?a=1',
            'localhost:18031',
        )
        self.assertEqual(out, 'http://localhost:18031/123/xxx/xxx?a=1')

    def test_export_import_roundtrip(self):
        from tools.iface_request_test import (
            EXPORT_KIND, build_export_document, export_document_to_text,
            parse_import_document,
        )
        rec = {
            'url': 'http://api.example.com/order?x=1',
            'method': 'POST',
            'request_headers': {'Content-Type': 'application/json', 'X-Token': 't'},
            'response_headers': {'Content-Type': 'application/json'},
            'request_body': '{"a":1}',
            'response_body': '{"ok":true}',
            'status': 200,
            'query': 'x=1',
        }
        doc = build_export_document([rec])
        self.assertEqual(doc['pengtools_export'], EXPORT_KIND)
        self.assertEqual(doc['count'], 1)
        text = export_document_to_text(doc)
        items = parse_import_document(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'], rec['url'])
        self.assertEqual(items[0]['request_body'], '{"a":1}')
        self.assertEqual(items[0]['response_body'], '{"ok":true}')

    def test_import_rejects_wrong_kind(self):
        from tools.iface_request_test import RequestTestError, parse_import_document
        with self.assertRaises(RequestTestError):
            parse_import_document('{"pengtools_export":"other","items":[]}')
        with self.assertRaises(RequestTestError):
            parse_import_document('not-json')

    def test_fill_form_uses_base_and_params(self):
        from tools.iface_request_test import fill_request_form_from_item
        form = fill_request_form_from_item(
            {
                'url': 'https://prod:10110/api/v1/user?id=9',
                'method': 'POST',
                'request_headers': {'A': '1'},
                'request_body': '{"n":1}',
                'query': 'id=9',
            },
            'http://localhost:18031',
        )
        self.assertEqual(form['method'], 'POST')
        self.assertEqual(form['url'], 'http://localhost:18031/api/v1/user?id=9')
        self.assertIn('A: 1', form['headers_text'])
        self.assertIn('id=9', form['params_text'])
        self.assertEqual(form['body'], '{"n":1}')

    def test_headers_and_params_roundtrip(self):
        from tools.iface_request_test import (
            headers_dict_from_text, headers_text_from_dict,
            merge_url_with_params, params_text_from_query,
        )
        text = headers_text_from_dict({'Content-Type': 'application/json', 'X': 'y'})
        d = headers_dict_from_text(text)
        self.assertEqual(d['Content-Type'], 'application/json')
        self.assertEqual(params_text_from_query('a=1&b=2'), 'a=1\nb=2')
        url = merge_url_with_params('http://localhost:1/p?old=1', 'x=9\ny=8')
        self.assertIn('x=9', url)
        self.assertIn('y=8', url)
        self.assertNotIn('old=1', url)

    def test_send_rejects_bad_scheme(self):
        from tools.iface_request_test import RequestTestError, send_http_request
        with self.assertRaises(RequestTestError):
            send_http_request('GET', 'ftp://example.com/a')

    def test_is_loopback_host(self):
        from tools.iface_request_test import is_loopback_host
        self.assertTrue(is_loopback_host('localhost'))
        self.assertTrue(is_loopback_host('127.0.0.1'))
        self.assertTrue(is_loopback_host('127.1.2.3'))
        self.assertFalse(is_loopback_host('uat.internal'))
        self.assertFalse(is_loopback_host('10.0.0.1'))

    def test_send_env_host_ok(self):
        from tools.iface_request_test import send_http_request
        class _Resp:
            status = 200
            headers = {'Content-Type': 'text/plain'}

            def read(self):
                return b'hello'

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Opener:
            def open(self, req, timeout=None):
                return _Resp()

        with mock.patch('urllib.request.build_opener', return_value=_Opener()):
            result = send_http_request('GET', 'http://uat.internal:10110/ping')
            self.assertTrue(result['ok'])
            self.assertEqual(result['status'], 200)
            self.assertEqual(result['body'], 'hello')

    def test_send_https_default_verify_ssl_true(self):
        """安测：HTTPS 默认走校验证书的 SSL context。"""
        from tools.iface_request_test import send_http_request
        import ssl

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return b'ok'

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Opener:
            def open(self, req, timeout=None):
                return _Resp()

        captured = {}

        def _build_opener(*handlers):
            for h in handlers:
                ctx = getattr(h, '_context', None)
                if ctx is not None:
                    captured['context'] = ctx
            return _Opener()

        with mock.patch('urllib.request.build_opener', side_effect=_build_opener):
            result = send_http_request('GET', 'https://example.com/ping', verify_ssl=True)
            self.assertTrue(result['ok'])
            self.assertTrue(result.get('ssl_verified'))
        # 有 context 时不应是 unverified（check_hostname 通常为 True）
        ctx = captured.get('context')
        if ctx is not None:
            self.assertTrue(getattr(ctx, 'check_hostname', True))

    def test_send_https_can_disable_verify(self):
        from tools.iface_request_test import send_http_request

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return b'ok'

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Opener:
            def open(self, req, timeout=None):
                return _Resp()

        with mock.patch('urllib.request.build_opener', return_value=_Opener()):
            result = send_http_request(
                'GET', 'https://self-signed.local/ping', verify_ssl=False,
            )
            self.assertTrue(result['ok'])
            self.assertFalse(result.get('ssl_verified'))

    def test_extract_sm4_key_from_header(self):
        from tools.iface_request_test import extract_sm4_key_cipher
        key = 'ab' * 64
        rec = {
            'request_headers': {'X-Encrypt-Key': key},
            'request_body': 'cipher',
            'response_headers': {},
            'response_body': '',
        }
        self.assertEqual(extract_sm4_key_cipher(rec, 'request'), key)

    def test_plaintext_bodies_fallback_raw(self):
        from tools.iface_request_test import plaintext_bodies
        rec = {
            'url': 'http://h/a',
            'method': 'GET',
            'request_body': 'raw-req',
            'response_body': 'raw-resp',
            'request_headers': {},
            'response_headers': {},
        }
        out = plaintext_bodies(rec)
        self.assertEqual(out['request_body'], 'raw-req')
        self.assertEqual(out['response_body'], 'raw-resp')
        self.assertFalse(out['request_decrypted'])
        self.assertFalse(out['response_decrypted'])


class IfacePanelRequestTestSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_has_request_test_and_export(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        p = InterfaceDebugPanel(language='zh')
        self.assertTrue(hasattr(p, 'rt_send_btn'))
        self.assertTrue(hasattr(p, 'export_detail_btn'))
        self.assertTrue(hasattr(p, 'rt_import_btn'))
        self.assertTrue(hasattr(p, 'export_list_btn'))
        p.set_language('zh')
        self.assertEqual(p.detail_tabs.tabText(3), '请求测试')
        self.assertEqual(p.export_detail_btn.text(), '导出明细')
        self.assertEqual(p.rt_send_btn.text(), '发送')
        # 停止不清空：模拟有记录后 stop 逻辑只停引擎
        p._records = [{'id': '1', 'url': 'http://x/a', 'method': 'GET'}]
        p._records_by_id = {'1': p._records[0]}
        p._listening = True
        p._stop_listen()
        self.assertEqual(len(p._records), 1)
        self.assertFalse(p._listening)
        p.clear_session()
        self.assertEqual(len(p._records), 0)

    def test_fill_and_export_from_panel(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        from tools.iface_request_test import build_export_document, parse_import_document
        p = InterfaceDebugPanel(language='zh')
        rec = {
            'id': 'r1',
            'url': 'http://remote:10110/api/demo?q=1',
            'method': 'POST',
            'request_headers': {'Content-Type': 'application/json'},
            'response_headers': {},
            'request_body': '{"x":1}',
            'response_body': '{"y":2}',
            'query': 'q=1',
            'status': 200,
        }
        p._records = [rec]
        p._records_by_id = {'r1': rec}
        p._selected_id = 'r1'
        p.rt_base_edit.setText('localhost:18031')
        p._rt_fill_from_selection(silent=True)
        self.assertIn('localhost:18031', p.rt_url.text())
        self.assertIn('/api/demo', p.rt_url.text())
        self.assertEqual(p.rt_method.currentText(), 'POST')
        self.assertIn('{"x":1}', p.rt_body.toPlainText())
        doc = build_export_document([rec])
        items = parse_import_document(json.dumps(doc, ensure_ascii=False))
        self.assertEqual(len(items), 1)
        # 弹窗在 headless 下可能阻塞，mock 掉
        with mock.patch('panels.interface_debug_panel.show_success'), \
             mock.patch('panels.interface_debug_panel.show_info'), \
             mock.patch('panels.interface_debug_panel.show_warning'):
            p._rt_import_text(json.dumps(doc, ensure_ascii=False))
        self.assertIn('localhost', p.rt_url.text())

    def test_response_view_keeps_full_body_and_format_buttons(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        p = InterfaceDebugPanel(language='zh')
        self.assertTrue(hasattr(p, 'rt_req_format_btn'))
        self.assertTrue(hasattr(p, 'rt_resp_format_btn'))
        big = '{"data":"' + ('A' * 20000) + '"}'
        p._rt_set_response_view(big, meta='Status: 200', headers={'Content-Type': 'application/json'})
        text = p.draft_preview.toPlainText()
        self.assertGreaterEqual(len(text), 20000)
        self.assertEqual(p._rt_last_response_body, big)
        emitted = []
        p.open_format_json.connect(lambda t: emitted.append(t))
        p._rt_send_response_to_format()
        self.assertTrue(emitted)
        self.assertIn('AAAA', emitted[0])
        p.rt_body.setPlainText('{"req":1}')
        emitted.clear()
        p._rt_send_request_to_format()
        self.assertTrue(emitted)
        self.assertIn('req', emitted[0])
        # 一键复制
        self.assertTrue(hasattr(p, 'rt_req_copy_btn'))
        self.assertTrue(hasattr(p, 'rt_resp_copy_btn'))
        with mock.patch('panels.interface_debug_panel.show_success'), \
             mock.patch.object(p, '_copy_text') as copy_mock:
            p._rt_copy_request_body()
            self.assertTrue(copy_mock.called)
            self.assertIn('req', copy_mock.call_args[0][0])
            p._rt_copy_response_body()
            self.assertIn('AAAA', copy_mock.call_args[0][0])
        p.close()


if __name__ == '__main__':
    unittest.main()
