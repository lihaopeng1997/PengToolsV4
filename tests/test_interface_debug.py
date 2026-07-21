# -*- coding: utf-8 -*-
"""接口排查 / 首页待升级 / SQL 系统下拉 / 文本辅助 / 草稿 定向测试。"""

from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class InterfaceDebugStoreTests(unittest.TestCase):
    def test_normalize_setdefault_and_ports(self):
        from tools.interface_debug_store import normalize_interface_debug_config
        cfg = normalize_interface_debug_config({'debug_port': '99999', 'local_targets': [
            {'name': 'A', 'base_url': 'http://localhost:8080'},
        ]})
        self.assertEqual(cfg['debug_port'], 65535)
        self.assertEqual(cfg['ie_proxy_port'], 8899)
        self.assertEqual(len(cfg['local_targets']), 1)
        self.assertTrue(cfg['local_targets'][0]['id'])
        self.assertIn('proxy_restore_snapshot', cfg)

    def test_save_load_roundtrip(self):
        from tools.interface_debug_store import load_interface_debug_config, save_interface_debug_config
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'interface_debug.json')
            save_interface_debug_config({
                'browser_path': r'C:\chrome.exe',
                'debug_port': 9333,
                'local_targets': [{'id': 't1', 'name': 'local', 'base_url': 'http://127.0.0.1:8080'}],
                'default_target_id': 't1',
            }, path=path)
            loaded = load_interface_debug_config(path)
            self.assertEqual(loaded['browser_path'], r'C:\chrome.exe')
            self.assertEqual(loaded['debug_port'], 9333)
            self.assertEqual(loaded['default_target_id'], 't1')
            # 敏感字段不得出现
            raw = open(path, encoding='utf-8').read()
            self.assertNotIn('Authorization', raw)
            self.assertNotIn('request_body', raw)


class InterfaceDraftsTests(unittest.TestCase):
    def test_validate_base_url(self):
        from tools.interface_drafts import DraftError, validate_base_url
        self.assertEqual(validate_base_url('http://localhost:8080/'), 'http://localhost:8080')
        with self.assertRaises(DraftError):
            validate_base_url('ftp://x')
        with self.assertRaises(DraftError):
            validate_base_url('http://localhost:8080/api')
        with self.assertRaises(DraftError):
            validate_base_url('')

    def test_rewrite_url_keeps_path_query(self):
        from tools.interface_drafts import rewrite_url
        out = rewrite_url(
            'https://prod.example.com/api/v1/user?id=1&token=secret',
            'http://localhost:8080',
        )
        self.assertEqual(out, 'http://localhost:8080/api/v1/user?id=1&token=secret')

    def test_postman_and_curl(self):
        from tools.interface_drafts import build_curl, build_postman_collection, drafts_as_json_text
        rec = {
            'method': 'POST',
            'url': 'https://api.example.com/order?x=1',
            'path': '/order',
            'request_headers': {'Authorization': 'Bearer tok', 'Content-Type': 'application/json'},
            'request_body': '{"a":1}',
        }
        payload = build_postman_collection(rec, 'http://127.0.0.1:9000')
        self.assertIn('collection', payload)
        self.assertIn('environment', payload)
        coll = payload['collection']
        self.assertEqual(coll['info']['schema'].endswith('v2.1.0/collection.json'), True)
        item = coll['item'][0]
        self.assertEqual(item['request']['method'], 'POST')
        self.assertIn('{{baseUrl}}', item['request']['url']['raw'])
        text = drafts_as_json_text(payload)
        self.assertIn('Bearer tok', text)  # 草稿完整携带，UI 才脱敏
        curl = build_curl(rec, 'http://127.0.0.1:9000')
        self.assertIn('curl', curl)
        self.assertIn('-X', curl)
        self.assertIn('POST', curl)
        self.assertIn('http://127.0.0.1:9000/order?x=1', curl)
        self.assertIn('--data-raw', curl)

    def test_drafts_never_network(self):
        """确保 interface_drafts 模块不 import 网络库发请求。"""
        import tools.interface_drafts as m
        src = open(m.__file__, encoding='utf-8').read()
        self.assertNotIn('urlopen', src)
        self.assertNotIn('requests.', src)
        self.assertNotIn('http.client', src)


class TextDevHelpersTests(unittest.TestCase):
    def test_base64_roundtrip(self):
        from tools.text_dev_helpers import decode_base64, encode_base64
        self.assertEqual(decode_base64(encode_base64('你好中文')), '你好中文')

    def test_base64_non_utf8_error(self):
        from tools.text_dev_helpers import TextHelperError, decode_base64
        import base64
        bad = base64.b64encode(bytes([0xff, 0xfe, 0xfd])).decode('ascii')
        with self.assertRaises(TextHelperError):
            decode_base64(bad)

    def test_url_unicode_timestamp(self):
        from tools.text_dev_helpers import (
            decode_unicode_escapes, decode_url, encode_unicode_escapes, encode_url,
            format_timestamp_bundle,
        )
        self.assertIn('%', encode_url('a=1&b=中文') or 'x')
        self.assertEqual(decode_url(encode_url('hello world')), 'hello world')
        esc = encode_unicode_escapes('测')
        self.assertIn('\\u', esc)
        self.assertEqual(decode_unicode_escapes(esc), '测')
        # 保留普通反斜杠
        self.assertEqual(decode_unicode_escapes(r'path\file \u4e2d'), r'path\file 中')
        bundle = format_timestamp_bundle('1609459200')
        self.assertIn('Unix 秒', bundle)
        self.assertIn('北京时间', bundle)

    def test_java_stack(self):
        from tools.text_dev_helpers import extract_java_stack
        sample = """\
java.lang.RuntimeException: boom
\tat com.example.service.Foo.bar(Foo.java:42)
\tat java.base/java.lang.Thread.run(Thread.java:833)
Caused by: java.lang.IllegalStateException: nested
\tat com.example.repo.Bar.load(Bar.java:10)
"""
        result = extract_java_stack(sample)
        self.assertEqual(result['first_exception'], 'java.lang.RuntimeException')
        self.assertTrue(result['caused_by'])
        self.assertIn('com.example.service.Foo.bar', result['first_business_at'])
        self.assertIn('异常链', result['compact_text'])


class BrowserDebugTests(unittest.TestCase):
    def test_loopback_only(self):
        from tools.browser_debug import is_loopback_host
        self.assertTrue(is_loopback_host('127.0.0.1'))
        self.assertTrue(is_loopback_host('localhost'))
        self.assertFalse(is_loopback_host('192.168.1.1'))
        self.assertFalse(is_loopback_host('example.com'))

    def test_launch_args(self):
        from tools.browser_debug import build_launch_args
        args = build_launch_args(r'C:\chrome.exe', 9222)
        joined = ' '.join(args)
        self.assertIn('--remote-debugging-address=127.0.0.1', joined)
        self.assertIn('--remote-debugging-port=9222', joined)
        self.assertIn('--user-data-dir=', joined)
        self.assertIn('--no-first-run', joined)

    def test_firefox_launch_rejected(self):
        from tools.browser_debug import BrowserDebugError, launch_debug_browser
        with self.assertRaises(BrowserDebugError) as ctx:
            launch_debug_browser(r'C:\Program Files\Mozilla Firefox\firefox.exe', 9222)
        self.assertIn('Firefox', str(ctx.exception))

    def test_merge_cdp_events_and_static_filter(self):
        from tools.browser_debug import merge_cdp_event, should_keep_record
        records = {}
        merge_cdp_event(records, 'Network.requestWillBeSent', {
            'requestId': 'r1',
            'type': 'XHR',
            'request': {
                'method': 'POST',
                'url': 'https://api.example.com/v1/data?token=abc',
                'headers': {'Authorization': 'Bearer x', 'Content-Type': 'application/json'},
                'postData': '{"q":1}',
            },
        })
        merge_cdp_event(records, 'Network.responseReceived', {
            'requestId': 'r1',
            'type': 'XHR',
            'response': {
                'status': 200,
                'mimeType': 'application/json',
                'url': 'https://api.example.com/v1/data?token=abc',
                'headers': {'Set-Cookie': 'sid=1'},
            },
        })
        merge_cdp_event(records, 'Network.loadingFinished', {'requestId': 'r1'})
        rec = records['r1']
        self.assertEqual(rec['method'], 'POST')
        self.assertEqual(rec['status'], 200)
        self.assertEqual(rec['request_body'], '{"q":1}')
        self.assertTrue(should_keep_record(rec, show_static=False))

        merge_cdp_event(records, 'Network.requestWillBeSent', {
            'requestId': 'r2',
            'type': 'Stylesheet',
            'request': {'method': 'GET', 'url': 'https://cdn.example.com/a.css', 'headers': {}},
        })
        self.assertFalse(should_keep_record(records['r2'], show_static=False))
        self.assertTrue(should_keep_record(records['r2'], show_static=True))

        merge_cdp_event(records, 'Network.loadingFailed', {
            'requestId': 'r3',
            'errorText': 'net::ERR_FAILED',
            'request': {},
        })
        # loadingFailed without prior request still creates record with failure
        self.assertIn('r3', records)
        self.assertEqual(records['r3']['failure'], 'net::ERR_FAILED')

    def test_mask_sensitive(self):
        from tools.browser_debug import mask_sensitive_value, mask_url_query
        self.assertEqual(mask_sensitive_value('Authorization', 'Bearer x'), '••••••••')
        self.assertEqual(mask_sensitive_value('Authorization', 'Bearer x', reveal=True), 'Bearer x')
        masked = mask_url_query('https://x.com/a?token=secret&id=1')
        self.assertIn('********', masked)
        self.assertNotIn('secret', masked)
        self.assertIn('id=1', masked)

    def test_clear_session(self):
        from tools.browser_debug import CdpNetworkSession
        s = CdpNetworkSession('ws://127.0.0.1:1')
        s.records['a'] = {'id': 'a'}
        s.clear_session()
        self.assertEqual(s.records, {})


class IeProxyTests(unittest.TestCase):
    def test_flow_to_record_shape(self):
        from tools.ie_proxy import flow_to_record
        from tools.http_capture import flow_to_url_record, HttpCaptureWorker, IeProxyWorker

        class FakeHeaders:
            def __init__(self, d):
                self._d = d

            def items(self, multi=False):
                return list(self._d.items())

            def get(self, k, default=''):
                return self._d.get(k, default)

        class FakeReq:
            method = 'GET'
            pretty_url = 'http://127.0.0.1:8080/api?x=1'
            url = pretty_url
            scheme = 'http'
            host = '127.0.0.1'
            port = 8080
            headers = FakeHeaders({'Cookie': 'a=1'})
            content = b''

            def get_text(self, strict=False):
                return ''

        class FakeResp:
            status_code = 200
            headers = FakeHeaders({'Content-Type': 'application/json'})
            content = b'{"ok":true}'

            def get_text(self, strict=False):
                return '{"ok":true}'

        class FakeFlow:
            request = FakeReq()
            response = FakeResp()
            error = None

        rec = flow_to_record(FakeFlow())
        self.assertEqual(rec['method'], 'GET')
        self.assertEqual(rec['status'], 200)
        self.assertEqual(rec['source'], 'ie_proxy')
        self.assertIn('Cookie', rec['request_headers'])
        self.assertEqual(rec.get('host'), '127.0.0.1')
        self.assertEqual(rec.get('path'), '/api')
        self.assertEqual(rec.get('query'), 'x=1')
        self.assertEqual(rec.get('query_params', {}).get('x'), '1')
        # 新引擎别名
        self.assertIs(IeProxyWorker, HttpCaptureWorker)
        rec2 = flow_to_url_record(FakeFlow(), source='http_capture')
        self.assertEqual(rec2['source'], 'http_capture')
        self.assertIn('url', rec2)

    def test_cert_thumbprint_only_from_config(self):
        from tools import ie_proxy
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'interface_debug.json')
            # 写入带指纹的配置
            from tools.interface_debug_store import save_interface_debug_config
            save_interface_debug_config({'ie_certificate_thumbprint': 'AABBCC'}, path=path)
            with mock.patch('tools.ie_proxy.load_interface_debug_config') as loader, \
                 mock.patch('tools.ie_proxy.save_interface_debug_config') as saver, \
                 mock.patch('tools.ie_proxy.subprocess.run') as run:
                loader.return_value = {
                    'ie_certificate_thumbprint': 'AABBCC',
                    'proxy_restore_snapshot': None,
                }
                saver.side_effect = lambda c, path=None: c
                run.return_value = mock.Mock(returncode=0, stdout='', stderr='')
                ok = ie_proxy.remove_recorded_cert()
                self.assertTrue(ok)
                args = run.call_args[0][0]
                self.assertIn('AABBCC', args)
                self.assertIn('-delstore', args)


class DashboardReleaseTests(unittest.TestCase):
    def test_fill_release_ranking(self):
        try:
            from PyQt6.QtWidgets import QApplication
            from panels.dashboard_panel import DashboardPanel
        except ImportError:
            self.skipTest('PyQt6 missing')
        app = QApplication.instance() or QApplication([])
        panel = DashboardPanel('zh')
        today = datetime.date.today()
        items = [
            {'title': '已上线A', 'status': '已上线', 'planned_online_date': '2020-01-01', 'system': 'S'},
            {'title': '暂停B', 'status': '暂停', 'planned_online_date': str(today), 'system': 'S'},
            {
                'title': '逾期老', 'status': '开发中',
                'planned_online_date': str(today - datetime.timedelta(days=10)),
                'system': 'SysA', 'updated_at': '2026-01-01T00:00:00',
            },
            {
                'title': '逾期新', 'status': '开发中',
                'planned_online_date': str(today - datetime.timedelta(days=3)),
                'system': 'SysB', 'updated_at': '2026-06-01T00:00:00',
            },
            {
                'title': '计划近', 'status': '开发中',
                'planned_online_date': str(today + datetime.timedelta(days=2)),
                'system': 'SysC', 'updated_at': '2026-06-02T00:00:00',
            },
            {
                'title': '待排期', 'status': '开发中',
                'online_month': '2026-08', 'system': 'SysD',
                'updated_at': '2026-06-03T00:00:00',
            },
            {
                'title': '已取消', 'status': '已取消',
                'planned_online_date': str(today - datetime.timedelta(days=1)),
            },
        ]
        panel._fill_release(items)
        # 最多 5 条，排除已上线/暂停/取消
        count = panel.release_list.count()
        self.assertGreaterEqual(count, 3)
        self.assertLessEqual(count, 5)
        # 第一条应为逾期天数更大的「逾期老」
        first = panel.release_list.itemAt(0).widget()
        self.assertIsNotNone(first)
        self.assertEqual(first.title_label.text(), '逾期老')
        self.assertIn('逾期', first.meta_label.text())

    def test_empty_release(self):
        try:
            from PyQt6.QtWidgets import QApplication
            from panels.dashboard_panel import DashboardPanel
        except ImportError:
            self.skipTest('PyQt6 missing')
        app = QApplication.instance() or QApplication([])
        panel = DashboardPanel('zh')
        panel.show()
        panel._fill_release([{'title': 'x', 'status': '已上线'}])
        self.assertEqual(panel.release_list.count(), 0)
        self.assertFalse(panel.release_empty.isHidden())


class NavigationAndPanelSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
            cls.app = QApplication.instance() or QApplication([])
            cls.qt = True
        except ImportError:
            cls.qt = False

    def test_nav_item_12(self):
        from ui.navigation_model import NAV_ITEMS, display_name, icon_role_for
        self.assertIn(12, NAV_ITEMS)
        self.assertEqual(display_name(12, 'zh'), '接口排查')
        self.assertEqual(icon_role_for(12), 'api-debug')

    def test_stack_mapping(self):
        from main_window import MainWindow
        self.assertEqual(MainWindow._stack_index_for_nav(11), 10)
        self.assertEqual(MainWindow._stack_index_for_nav(12), 11)
        self.assertEqual(MainWindow._stack_index_for_nav(5), 5)

    def test_format_panel_has_text_tab(self):
        if not self.qt:
            self.skipTest('no qt')
        from panels.format_panel import FormatToolsPanel
        p = FormatToolsPanel('zh')
        self.assertEqual(p.tabs.count(), 4)
        self.assertIn('文本', p.tabs.tabText(3))

    def test_interface_panel_smoke(self):
        if not self.qt:
            self.skipTest('no qt')
        from panels.interface_debug_panel import InterfaceDebugPanel
        p = InterfaceDebugPanel('zh')
        self.assertFalse(p._listening)
        p.clear_session()
        self.assertEqual(p._records, [])
        p.set_language('en')
        self.assertIn('API', p.page_title.text())

    def test_gateway_has_iface_button(self):
        if not self.qt:
            self.skipTest('no qt')
        from panels.gateway_panel import GatewayDecodePanel
        p = GatewayDecodePanel('zh')
        self.assertTrue(hasattr(p, 'to_iface_btn'))
        p.set_cipher_text('abc')
        self.assertEqual(p.payload_cipher.toPlainText(), 'abc')


class SqlSystemComboTests(unittest.TestCase):
    def test_work_system_combo_exists(self):
        try:
            from PyQt6.QtWidgets import QApplication
            from panels.sql_panel import SqlToolPanel
        except ImportError:
            self.skipTest('PyQt6 missing')
        app = QApplication.instance() or QApplication([])
        panel = SqlToolPanel()
        self.assertTrue(hasattr(panel, 'work_system_combo'))


if __name__ == '__main__':
    unittest.main()
