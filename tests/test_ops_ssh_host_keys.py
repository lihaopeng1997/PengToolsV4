"""日志排查 SSH 主机指纹：首次记录、之后校验、变更拒绝、可重置。"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools import ops_ssh


def _key(blob: bytes, kind: str = 'ssh-ed25519'):
    key = MagicMock()
    key.asbytes.return_value = blob
    key.get_name.return_value = kind
    return key


class HostKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        mock = patch.object(ops_ssh, 'OPS_SSH_KNOWN_HOSTS_FILE', os.path.join(self.tmp.name, 'known.json'))
        mock.start()
        self.addCleanup(mock.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_first_use_records_then_same_key_passes(self):
        ops_ssh._verify_or_remember_host_key('10.0.0.8', 22, _key(b'A'))
        saved = ops_ssh.load_known_hosts()['10.0.0.8']
        self.assertTrue(saved['fingerprint'].startswith('SHA256:'))
        ops_ssh._verify_or_remember_host_key('10.0.0.8', 22, _key(b'A'))  # 不抛

    def test_changed_key_is_rejected_until_reset(self):
        ops_ssh._verify_or_remember_host_key('10.0.0.8', 2222, _key(b'A'))
        self.assertIn('[10.0.0.8]:2222', ops_ssh.load_known_hosts())
        with self.assertRaises(ops_ssh.HostKeyChangedError) as ctx:
            ops_ssh._verify_or_remember_host_key('10.0.0.8', 2222, _key(b'B'))
        self.assertIn('重置当前服务器指纹', str(ctx.exception))
        self.assertTrue(ops_ssh.forget_host_key('10.0.0.8', 2222))
        ops_ssh._verify_or_remember_host_key('10.0.0.8', 2222, _key(b'B'))
        self.assertFalse(ops_ssh.forget_host_key('10.0.0.9', 22))

    def test_policy_parses_bracketed_hostname(self):
        policy = ops_ssh._TrustOnFirstUsePolicy(2222)
        policy.missing_host_key(None, '[10.0.0.8]:2222', _key(b'A'))
        self.assertIn('[10.0.0.8]:2222', ops_ssh.load_known_hosts())

    def test_no_auto_add_policy_left(self):
        with open(ops_ssh.__file__, encoding='utf-8') as stream:
            self.assertNotIn('AutoAddPolicy', stream.read())


if __name__ == '__main__':
    unittest.main()
