"""SSH 密钥登录：配置字段、密码被禁用时自动回退本机密钥、错误提示。"""
import unittest
from unittest.mock import MagicMock, patch

from tools import ops_ssh


class _BadAuthType(Exception):
    def __init__(self, allowed):
        super().__init__(f'Bad authentication type; allowed types: {allowed}')
        self.allowed_types = allowed


_BadAuthType.__name__ = 'BadAuthenticationType'


def _server(**extra):
    data = {'id': 's1', 'name': 'srv', 'host': '10.0.0.8', 'port': 22, 'username': 'root'}
    data.update(extra)
    return data


class KeyAuthTests(unittest.TestCase):
    def test_normalize_keeps_auth_fields_and_encrypts_passphrase(self):
        with patch('tools.ops_ssh.encrypt_secret', side_effect=lambda v: 'ENC:' + v):
            row = ops_ssh._normalize_server(_server(auth_method='KEY', key_path='~/.ssh/id_ed25519',
                                                    key_passphrase='secret'))
        self.assertEqual(row['auth_method'], 'key')
        self.assertEqual(row['key_path'], '~/.ssh/id_ed25519')
        self.assertEqual(row['key_passphrase_token'], 'ENC:secret')
        self.assertNotIn('key_passphrase', row)
        self.assertEqual(ops_ssh._normalize_server(_server())['auth_method'], 'password')

    def test_key_servers_do_not_need_saved_password(self):
        self.assertTrue(ops_ssh.server_has_credentials(_server(auth_method='key')))
        with patch('tools.ops_ssh.decrypt_secret', return_value=''):
            self.assertFalse(ops_ssh.server_has_credentials(_server()))

    def test_key_mode_uses_agent_and_default_keys(self):
        client = MagicMock()
        with patch.object(ops_ssh, 'paramiko_available', return_value=True), \
                patch.object(ops_ssh.paramiko, 'SSHClient', return_value=client):
            got = ops_ssh.open_ssh_client(_server(auth_method='key'))
        self.assertIs(got, client)
        kwargs = client.connect.call_args.kwargs
        self.assertIsNone(kwargs['key_filename'])
        self.assertTrue(kwargs['allow_agent'])
        self.assertTrue(kwargs['look_for_keys'])
        self.assertNotIn('password', kwargs)

    def test_password_disabled_server_falls_back_to_local_keys(self):
        first, second = MagicMock(), MagicMock()
        first.connect.side_effect = _BadAuthType(['publickey', 'gssapi-with-mic'])
        with patch.object(ops_ssh, 'paramiko_available', return_value=True), \
                patch.object(ops_ssh.paramiko, 'SSHClient', side_effect=[first, second]), \
                patch('tools.ops_ssh.decrypt_secret', return_value='pw'):
            got = ops_ssh.open_ssh_client(_server(password_token='x'))
        self.assertIs(got, second)
        self.assertTrue(second.connect.call_args.kwargs['look_for_keys'])

    def test_friendly_error_explains_key_only_server(self):
        message = ops_ssh._friendly_ssh_error(_BadAuthType(['publickey']))
        self.assertIn('不允许密码登录', message)
        self.assertIn('密钥', message)
        self.assertNotIn('用户名或密码不正确', message)


if __name__ == '__main__':
    unittest.main()
