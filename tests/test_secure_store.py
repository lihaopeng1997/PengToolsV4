# -*- coding: utf-8 -*-
"""安测：凭据存储（DPAPI / Fernet，禁止新 b64）。"""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from unittest import mock


class SecureStoreTests(unittest.TestCase):
    def test_roundtrip_not_plain_and_not_b64(self):
        from tools.secure_store import decrypt_secret, encrypt_secret

        token = encrypt_secret('p@ss-安测-测试')
        self.assertTrue(token)
        self.assertFalse(token.startswith('p@ss'))
        self.assertFalse(token.startswith('b64:'))
        self.assertTrue(token.startswith('dpapi:') or token.startswith('enc:'))
        self.assertEqual(decrypt_secret(token), 'p@ss-安测-测试')

    def test_legacy_b64_still_decrypts(self):
        from tools.secure_store import decrypt_secret, is_weak_token, reencrypt_if_weak

        plain = 'legacy-secret'
        weak = 'b64:' + base64.urlsafe_b64encode(plain.encode('utf-8')).decode('ascii')
        self.assertTrue(is_weak_token(weak))
        self.assertEqual(decrypt_secret(weak), plain)
        upgraded = reencrypt_if_weak(weak)
        self.assertIsNotNone(upgraded)
        self.assertFalse(upgraded.startswith('b64:'))
        self.assertEqual(decrypt_secret(upgraded), plain)

    def test_empty_secret(self):
        from tools.secure_store import decrypt_secret, encrypt_secret

        self.assertEqual(encrypt_secret(''), '')
        self.assertEqual(decrypt_secret(''), '')

    def test_ops_ssh_uses_secure_store(self):
        from tools import ops_ssh
        from tools.secure_store import decrypt_secret as ss_decrypt

        token = ops_ssh.encrypt_secret('ssh-pass')
        self.assertFalse(token.startswith('b64:'))
        self.assertEqual(ops_ssh.decrypt_secret(token), 'ssh-pass')
        self.assertEqual(ss_decrypt(token), 'ssh-pass')

    def test_save_servers_upgrades_weak_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            import tools.ops_ssh as mod

            old = mod.OPS_SERVERS_FILE
            try:
                mod.OPS_SERVERS_FILE = os.path.join(tmp, 'ops_servers.json')
                weak = 'b64:' + base64.urlsafe_b64encode(b'old-pass').decode('ascii')
                servers = [{
                    'id': 's1',
                    'name': 't',
                    'host': '10.0.0.9',
                    'port': 22,
                    'username': 'u',
                    'password_token': weak,
                    'enabled': True,
                }]
                mod.save_server_store(servers=servers, categories=[])
                loaded = mod.load_servers()
                self.assertEqual(len(loaded), 1)
                tok = loaded[0]['password_token']
                self.assertFalse(tok.startswith('b64:'))
                self.assertEqual(mod.decrypt_secret(tok), 'old-pass')
            finally:
                mod.OPS_SERVERS_FILE = old

    def test_backend_name(self):
        from tools.secure_store import backend_name

        name = backend_name()
        self.assertIn(name, ('dpapi', 'fernet', 'none'))


if __name__ == '__main__':
    unittest.main()
