from __future__ import annotations

import base64
import hashlib
import socket
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.data_center.connection_transport import DataCenterConnectionTransport
from tools.data_center.ssh_tunnel import DataCenterSshTunnelManager, SshTunnelError


def _fingerprint(raw: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return "SHA256:" + encoded


class _FakeKey:
    def __init__(self, raw: bytes):
        self.raw = raw

    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return self.raw


class _FakePKey:
    seen = []

    @classmethod
    def from_path(cls, path, *, passphrase=None):
        cls.seen.append((path, passphrase))
        return (path, passphrase)


class _FakeTransport(threading.Thread):
    auth_passwords = []
    auth_keys = []
    key_bytes = b"fixture-ssh-host-key-v1"

    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock = sock
        self._closed = threading.Event()
        self._authenticated = False

    def start_client(self, timeout=None):
        self.start()

    def run(self):
        self._closed.wait(5)

    def get_remote_server_key(self):
        return _FakeKey(type(self).key_bytes)

    def auth_password(self, username, password):
        type(self).auth_passwords.append((username, password))
        self._authenticated = True

    def auth_publickey(self, username, key):
        type(self).auth_keys.append((username, key))
        self._authenticated = True

    def is_authenticated(self):
        return self._authenticated

    def open_channel(self, kind, destination, origin, timeout=None):
        if kind != "direct-tcpip":
            raise AssertionError("unexpected channel type")
        return socket.create_connection(destination, timeout=timeout)

    def close(self):
        self._closed.set()
        try:
            self.sock.close()
        except OSError:
            pass


class _FakeParamiko:
    Transport = _FakeTransport
    PKey = _FakePKey


class _LoopbackServer:
    def __init__(self, handler):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.settimeout(0.1)
        self.host, self.port = self.listener.getsockname()
        self.handler = handler
        self.stopping = threading.Event()
        self.connections = set()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                self.connections.add(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        try:
            self.handler(connection)
        finally:
            with self.lock:
                self.connections.discard(connection)
            try:
                connection.close()
            except OSError:
                pass

    def close(self):
        self.stopping.set()
        try:
            self.listener.close()
        except OSError:
            pass
        with self.lock:
            connections = tuple(self.connections)
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        self.thread.join(1)


def _echo(connection):
    while True:
        payload = connection.recv(4096)
        if not payload:
            return
        connection.sendall(payload)


class DataCenterSshTransportTests(unittest.TestCase):
    def setUp(self):
        _FakeTransport.auth_passwords.clear()
        _FakeTransport.auth_keys.clear()
        _FakeTransport.key_bytes = b"fixture-ssh-host-key-v1"
        _FakePKey.seen.clear()
        self.ssh_endpoint = _LoopbackServer(lambda connection: connection.recv(4096))
        self.echo_endpoint = _LoopbackServer(_echo)
        self.temp = TemporaryDirectory(prefix="dc-ssh-fixture-")
        self.manager = DataCenterSshTunnelManager(
            Path(self.temp.name) / "known-hosts.json",
            connect_timeout=1,
            cleanup_timeout=1,
            secret_resolver=lambda token: {
                "enc:ssh-password": "fixture-ssh-password",
                "enc:key-passphrase": "fixture-key-passphrase",
            }.get(token, ""),
            paramiko_module=_FakeParamiko,
        )
        self.profile = {
            "id": "mysql-fixture",
            "provider": "mysql",
            "dialect": "mysql",
            "mode": "standalone",
            "host": self.echo_endpoint.host,
            "port": self.echo_endpoint.port,
            "username": "db-fixture-user",
            "ssh_enabled": True,
            "ssh_host": self.ssh_endpoint.host,
            "ssh_port": self.ssh_endpoint.port,
            "ssh_username": "ssh-fixture-user",
            "ssh_auth_method": "password",
            "ssh_password_token": "enc:ssh-password",
            "tls_enabled": False,
        }
        self.spec = DataCenterConnectionTransport.from_profile(self.profile, "mysql", "standalone")

    def tearDown(self):
        self.manager.close()
        self.ssh_endpoint.close()
        self.echo_endpoint.close()
        self.temp.cleanup()

    def test_unknown_fingerprint_requires_explicit_confirmation_before_auth(self):
        inspection = self.manager.inspect_host_key(self.spec)
        self.assertEqual(inspection.state, "unknown")
        self.assertEqual(_FakeTransport.auth_passwords, [])
        with self.assertRaises(SshTunnelError) as raised:
            self.manager.acquire(self.spec, connection_id="mysql-fixture", profile_revision="rev-1")
        self.assertEqual(raised.exception.code, "SSH_HOST_KEY_CONFIRM_REQUIRED")
        self.assertEqual(_FakeTransport.auth_passwords, [])

        confirmed = self.manager.confirm_first_use(
            self.spec, inspection.challenge_id, inspection.fingerprint
        )
        self.assertEqual(confirmed.state, "trusted")
        self.assertEqual(_FakeTransport.auth_passwords, [])

    def test_changed_fingerprint_blocks_auth_and_direct_forward_reuses_last_lease(self):
        original = _FakeTransport.key_bytes
        self.manager.trust_store.add_first_use(
            self.spec.ssh_host, self.spec.ssh_port, "ssh-ed25519", _fingerprint(original)
        )
        _FakeTransport.key_bytes = b"changed-host-key"
        with self.assertRaises(SshTunnelError) as raised:
            self.manager.acquire(self.spec, connection_id="mysql-fixture", profile_revision="rev-1")
        self.assertEqual(raised.exception.code, "SSH_HOST_KEY_CHANGED")
        self.assertEqual(_FakeTransport.auth_passwords, [])

        _FakeTransport.key_bytes = original
        first = self.manager.acquire(self.spec, connection_id="mysql-fixture", profile_revision="rev-1")
        second = self.manager.acquire(self.spec, connection_id="mysql-fixture", profile_revision="rev-1")
        self.assertEqual((first.dial_host, first.dial_port), (second.dial_host, second.dial_port))
        self.assertEqual(_FakeTransport.auth_passwords, [("ssh-fixture-user", "fixture-ssh-password")])
        with socket.create_connection((first.dial_host, first.dial_port), timeout=2) as client:
            client.sendall(b"loopback-forward")
            self.assertEqual(client.recv(100), b"loopback-forward")
        self.assertTrue(first.release())
        self.assertTrue(self.manager._entries)
        self.assertTrue(second.release())
        self.assertFalse(self.manager._entries)
        self.assertFalse(self.manager._pending_transports)
        self.assertTrue(self.manager.close())

    def test_private_key_auth_uses_key_path_and_separate_passphrase_token(self):
        profile = dict(self.profile)
        profile.update({
            "ssh_auth_method": "private_key",
            "ssh_password_token": "",
            "ssh_private_key_path": "C:/fixture/ssh-id_ed25519",
            "ssh_key_passphrase_token": "enc:key-passphrase",
        })
        spec = DataCenterConnectionTransport.from_profile(profile, "mysql", "standalone")
        self.manager.trust_store.add_first_use(
            spec.ssh_host, spec.ssh_port, "ssh-ed25519", _fingerprint(_FakeTransport.key_bytes)
        )
        lease = self.manager.acquire(spec, connection_id="mysql-fixture", profile_revision="rev-key")
        self.assertEqual(_FakePKey.seen, [("C:/fixture/ssh-id_ed25519", "fixture-key-passphrase")])
        self.assertEqual(_FakeTransport.auth_keys, [("ssh-fixture-user", ("C:/fixture/ssh-id_ed25519", "fixture-key-passphrase"))])
        self.assertEqual(_FakeTransport.auth_passwords, [])
        self.assertTrue(lease.release())


if __name__ == "__main__":
    unittest.main()
