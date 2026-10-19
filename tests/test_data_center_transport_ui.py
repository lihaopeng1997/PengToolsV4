from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QWidget

from panels.data_center_panel import DataCenterPanel, _WorkspaceConnectionDialog
from tools.data_center.ssh_tunnel import HostKeyInspection
from ui.data_center.connection_transport import (
    ConnectionTransportWorker,
    DataCenterTransportDialog,
)


class _BlockingInspectionManager:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()
        self.inspect_calls = 0
        self.confirm_calls = 0

    def inspect_host_key(self, _spec):
        self.inspect_calls += 1
        self.entered.set()
        try:
            if not self.release.wait(3):
                raise RuntimeError("fixture gate timed out")
            return HostKeyInspection(
                "unknown", "ssh.fixture.invalid", 22, "ssh-ed25519", "SHA256:fixture", "challenge-fixture"
            )
        finally:
            self.exited.set()

    def confirm_first_use(self, *_args):
        self.confirm_calls += 1
        raise AssertionError("closing a pending preflight must never auto-confirm a key")


def _profile(**extra):
    value = {
        "id": "fixture-connection",
        "provider": "mysql",
        "dialect": "mysql",
        "mode": "standalone",
        "host": "db.fixture.invalid",
        "port": 3306,
        "username": "db-user",
        "database": "fixture_db",
        "ssh_enabled": True,
        "ssh_host": "ssh.fixture.invalid",
        "ssh_port": 22,
        "ssh_username": "ssh-user",
        "ssh_auth_method": "password",
        "ssh_password_token": "enc:fixture-ssh-secret",
        "tls_enabled": False,
    }
    value.update(extra)
    return value


class DataCenterTransportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_dialog_masks_and_keeps_separate_ssh_tls_tokens(self):
        dialog = DataCenterTransportDialog("en", _profile())
        self.assertEqual(dialog.ssh_password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.ssh_password.text(), "")
        dialog.tls_enabled.setChecked(True)
        dialog.tls_key_password.setText("tls-fixture-passphrase")
        with patch("tools.secure_store.encrypt_secret", side_effect=lambda value: "enc:fixture-" + value):
            result = dialog.payload()
        self.assertEqual(result["ssh_password_token"], "enc:fixture-ssh-secret")
        self.assertEqual(result["tls_client_key_password_token"], "enc:fixture-tls-fixture-passphrase")
        self.assertNotEqual(result["ssh_password_token"], result["tls_client_key_password_token"])
        self.assertTrue(result["tls_enabled"])
        dialog.close()

    def test_application_cleanup_retains_pending_connection_test_dialog(self):
        class CleanupPanel(DataCenterPanel):
            def __init__(self):
                QWidget.__init__(self)
                self._tree_shutdown_started = True
                self._tree_owners = {}
                self._workspaces = {}

        panel = CleanupPanel()
        manager = _BlockingInspectionManager()
        dialog = _WorkspaceConnectionDialog("en", _profile(), parent=panel)
        worker = ConnectionTransportWorker("inspect", _profile(), manager=manager, parent=dialog)
        dialog._transport_test_context = (_profile(), "db-secret-fixture", manager)
        dialog._transport_test_worker = worker
        worker.completed.connect(dialog._on_transport_test_completed)
        worker.finished.connect(dialog._on_transport_test_finished)
        worker.start()
        try:
            self.assertTrue(manager.entered.wait(2))
            with patch("tools.data_center.connection_transport.retry_default_transport_cleanup", return_value=True):
                try:
                    self.assertFalse(panel.workbench_shutdown_cleanup())
                    self.assertTrue(dialog._transport_test_pending_close)
                    self.assertTrue(worker._cancel.is_set())
                finally:
                    dialog.reject()
                    manager.release.set()
                    self.assertTrue(worker.wait(2000))
                    self.app.processEvents()
                self.assertIsNone(dialog._transport_test_worker)
                self.assertEqual(manager.confirm_calls, 0)
                self.assertTrue(panel.workbench_shutdown_cleanup())
        finally:
            manager.release.set()
            worker.wait(2000)
            panel.deleteLater()
            self.app.processEvents()

    def test_cancel_waits_for_real_blocking_preflight_worker_without_trusting_key(self):
        manager = _BlockingInspectionManager()
        dialog = _WorkspaceConnectionDialog("en", _profile())
        dialog.show()
        worker = ConnectionTransportWorker(
            "inspect", _profile(), manager=manager, parent=dialog
        )
        dialog._transport_test_context = (_profile(), "db-secret-fixture", manager)
        dialog._transport_test_worker = worker
        worker.completed.connect(dialog._on_transport_test_completed)
        worker.finished.connect(dialog._on_transport_test_finished)
        thread_finished = threading.Event()
        worker.finished.connect(thread_finished.set)
        worker.start()
        self.assertTrue(manager.entered.wait(2))

        dialog.reject()
        self.assertTrue(dialog._transport_test_pending_close)
        self.assertTrue(dialog.isVisible())
        self.assertTrue(worker.isRunning())
        self.assertTrue(worker._cancel.is_set())
        manager.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (not thread_finished.is_set() or dialog.isVisible()):
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()

        self.assertTrue(manager.exited.is_set())
        self.assertEqual(manager.inspect_calls, 1)
        self.assertEqual(manager.confirm_calls, 0)
        self.assertTrue(thread_finished.is_set())
        self.assertIsNone(dialog._transport_test_worker)
        self.assertFalse(dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
