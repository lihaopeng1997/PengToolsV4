# -*- coding: utf-8 -*-
"""本轮定向：单实例、高对比图标、加解密参数区、监听就绪逻辑。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


class SingleInstanceNameTests(unittest.TestCase):
    def test_private_and_standard_names_differ(self):
        from ui.single_instance import local_server_name
        private = local_server_name('Private', '4.27')
        standard = local_server_name('Standard', '4.27')
        self.assertNotEqual(private, standard)
        self.assertIn('Private', private)
        self.assertIn('Standard', standard)
        self.assertIn('4.27', private)

    def test_guard_primary_and_secondary(self):
        from PyQt6.QtWidgets import QApplication
        from ui.single_instance import SingleInstanceGuard, ACTIVATE_MESSAGE

        app = QApplication.instance() or QApplication([])
        name = 'PengToolsHub.Test.SingleInstance.Unit'
        g1 = SingleInstanceGuard(server_name=name, parent=app)
        self.assertTrue(g1.try_become_primary())
        self.assertTrue(g1.is_primary)

        g2 = SingleInstanceGuard(server_name=name, parent=app)
        self.assertFalse(g2.try_become_primary())
        self.assertFalse(g2.is_primary)

        # 模拟 activate：直接 emit
        hit = {'n': 0}
        g1.activate_requested.connect(lambda: hit.__setitem__('n', hit['n'] + 1))
        g1.activate_requested.emit()
        app.processEvents()
        self.assertEqual(hit['n'], 1)

        g1.release()
        # 释放后应可重新成为主实例
        g3 = SingleInstanceGuard(server_name=name, parent=app)
        self.assertTrue(g3.try_become_primary())
        g3.release()
        self.assertIn(b'activate', ACTIVATE_MESSAGE)


class HighContrastIconTests(unittest.TestCase):
    def test_hc_ico_exists_and_window_icon(self):
        from ui.icons import brand_file, brand_window_icon, brand_tray_icon, icon_file
        hc = brand_file('app_taskbar') or icon_file('app_taskbar_hc')
        self.assertTrue(hc and os.path.isfile(hc), 'high-contrast ICO missing')
        # 主题品牌 ICO 仍在（不被覆盖）
        app_ico = brand_file('app')
        self.assertTrue(app_ico and os.path.isfile(app_ico))
        self.assertNotEqual(os.path.normcase(hc), os.path.normcase(app_ico))
        win = brand_window_icon()
        self.assertFalse(win.isNull())
        tray = brand_tray_icon()
        self.assertFalse(tray.isNull())


class ShouldKeepRecordTests(unittest.TestCase):
    def test_default_keeps_xhr_document_unknown(self):
        from tools.browser_debug import should_keep_record
        self.assertTrue(should_keep_record({
            'resource_type': 'XHR', 'url': 'https://api.x/a',
        }))
        self.assertTrue(should_keep_record({
            'resource_type': 'Document', 'url': 'https://app.x/',
        }))
        self.assertTrue(should_keep_record({
            'resource_type': '', 'url': 'https://api.x/v1',
        }))
        self.assertTrue(should_keep_record({
            'resource_type': 'WebSocket', 'url': 'wss://api.x/ws',
        }))
        self.assertFalse(should_keep_record({
            'resource_type': 'Stylesheet', 'url': 'https://cdn.x/a.css',
        }, show_static=False))
        self.assertTrue(should_keep_record({
            'resource_type': 'Stylesheet', 'url': 'https://cdn.x/a.css',
        }, show_static=True))

    def test_merge_cdp_events_stable_id(self):
        from tools.browser_debug import merge_cdp_event
        records = {}
        rid = merge_cdp_event(records, 'Network.requestWillBeSent', {
            'requestId': 'r1',
            'request': {'method': 'GET', 'url': 'https://a/x', 'headers': {}},
            'type': 'XHR',
        })
        self.assertEqual(rid, 'r1')
        merge_cdp_event(records, 'Network.responseReceived', {
            'requestId': 'r1',
            'response': {'status': 200, 'mimeType': 'application/json', 'url': 'https://a/x', 'headers': {}},
            'type': 'XHR',
        })
        merge_cdp_event(records, 'Network.loadingFinished', {'requestId': 'r1'})
        self.assertEqual(records['r1']['status'], 200)
        # response 先于 request 也不丢
        rid2 = merge_cdp_event(records, 'Network.responseReceived', {
            'requestId': 'r2',
            'response': {'status': 302, 'mimeType': '', 'url': 'https://a/redir', 'headers': {}},
        })
        self.assertEqual(rid2, 'r2')
        self.assertIn('r2', records)
        merge_cdp_event(records, 'Network.loadingFailed', {
            'requestId': 'r3', 'errorText': 'net::ERR_FAILED',
        })
        self.assertEqual(records['r3']['failure'], 'net::ERR_FAILED')


class LoopbackOnlyTests(unittest.TestCase):
    def test_is_loopback_and_reject_public(self):
        from tools.browser_debug import is_loopback_host
        self.assertTrue(is_loopback_host('127.0.0.1'))
        self.assertTrue(is_loopback_host('localhost'))
        self.assertFalse(is_loopback_host('0.0.0.0'))
        self.assertFalse(is_loopback_host('192.168.1.1'))
        self.assertFalse(is_loopback_host('10.0.0.5'))


class GatewayParamsVisibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_params_always_visible_and_key_label(self):
        from panels.gateway_panel import GatewayDecodePanel
        p = GatewayDecodePanel('zh')
        self.assertFalse(p.config_group.isHidden())
        self.assertIn('Key', p.key_label.text())
        self.assertIn('密钥', p.key_label.text())
        self.assertFalse(p.key_cipher.isHidden())
        self.assertGreater(p.key_cipher.minimumHeight(), 40)
        # IV 说明可见
        self.assertIn('IV', p.iv_label.text())
        self.assertTrue(p.iv_value.text())

    def test_set_cipher_does_not_overwrite_key(self):
        from panels.gateway_panel import GatewayDecodePanel
        p = GatewayDecodePanel('zh')
        p.key_cipher.setPlainText('aabbccdd')
        p.set_cipher_text('YmFzZTY0')
        self.assertEqual(p.key_cipher.toPlainText(), 'aabbccdd')
        self.assertEqual(p.payload_cipher.toPlainText(), 'YmFzZTY0')

    def test_decrypt_failure_keeps_key(self):
        from panels.gateway_panel import GatewayDecodePanel
        p = GatewayDecodePanel('zh')
        p.key_cipher.setPlainText('00')
        p.payload_cipher.setPlainText('AA==')
        with mock.patch('panels.gateway_panel.show_warning'):
            p._decrypt('request')
        self.assertEqual(p.key_cipher.toPlainText(), '00')
        self.assertEqual(p.payload_cipher.toPlainText(), 'AA==')


class InterfaceDefaultModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_default_mode_is_proxy(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        p = InterfaceDebugPanel('zh')
        self.assertEqual(p._mode, 'proxy')
        # 产品面只有抓包，不展示模式切换 / 证书 / 浏览器选择
        self.assertTrue(p.mode_combo.isHidden())
        self.assertTrue(p.browser_combo.isHidden())
        self.assertTrue(p.ie_install_cert_btn.isHidden())
        self.assertIn('抓包', p.connect_btn.text())

    def test_ingest_merges_and_main_thread_safe(self):
        from panels.interface_debug_panel import InterfaceDebugPanel
        p = InterfaceDebugPanel('zh')
        p._ingest_record({
            'id': 'x1', 'method': 'GET', 'url': 'https://api/x', 'path': '/x',
            'status': None, 'source': 'local_proxy', 'resource_type': 'XHR',
            'started_at': 1.0,
        })
        p._ingest_record({
            'id': 'x1', 'method': 'GET', 'url': 'https://api/x', 'path': '/x',
            'status': 200, 'response_body': '{"ok":1}', 'source': 'local_proxy',
            'resource_type': 'XHR', 'started_at': 1.0,
        })
        self.assertEqual(p._records_by_id['x1']['status'], 200)
        self.assertIn('ok', p._records_by_id['x1']['response_body'])
        p._rebuild_table()
        self.assertGreaterEqual(p.table.rowCount(), 1)
        p.clear_session()
        self.assertEqual(p._records, [])


if __name__ == '__main__':
    unittest.main()
