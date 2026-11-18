"""Offline P11 regressions using only disposable profiles and credentials."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation
import config

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout

from panels.data_center_panel import _WorkspaceConnectionDialog
from panels.ops_log_panel import ServerEditorDialog
from panels.settings_panel import SettingsPanel
from tools import db_connect, ops_ssh, secure_store
from tools.data_center.connection_transport import DataCenterConnectionTransport
from tools.data_center.ssh_tunnel import DataCenterSshTunnelManager, SshTunnelError
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.connection_dialog import ConnectionDialog
from ui.data_center.connection_transport import DataCenterTransportDialog
from ui.password_field import PasswordField


PLAINTEXT = 'synthetic value'
STORED = 'enc:fixture'
INVALID = 'enc:fixture-unreadable'
REPLACEMENT = 'synthetic replacement'
REPLACED = 'enc:fixture-replaced'


def unseal(value):
    if value == INVALID:
        raise secure_store.SecureStoreError('injected decrypt failure')
    return {STORED: PLAINTEXT, REPLACED: REPLACEMENT}.get(value, '')


def change(field, value):
    field.edit.selectAll()
    if value:
        QTest.keyClicks(field.edit, value)
    else:
        QTest.keyClick(field.edit, Qt.Key.Key_Backspace)


def verify_widget():
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    first = PasswordField('en', STORED)
    second = PasswordField('zh', STORED)
    layout.addWidget(first)
    layout.addWidget(second)
    dialog.show()
    app.processEvents()
    assert first.edit.text() == PLAINTEXT
    assert PLAINTEXT not in first.edit.displayText()
    assert PLAINTEXT not in first.eye.toolTip()
    first.eye.click()
    assert first.edit.displayText() == PLAINTEXT
    assert second.edit.echoMode() == QLineEdit.EchoMode.Password
    second.eye.click()
    assert first.edit.echoMode() == QLineEdit.EchoMode.Password
    assert first.action() == second.action() == 'keep'
    second.setEnabled(False)
    assert not second.eye.isChecked()
    first.eye.click()
    dialog.hide()
    assert not first.eye.isChecked()
    change(first, REPLACEMENT)
    assert first.action() == 'replace'
    change(first, PLAINTEXT)
    assert first.action() == 'keep'
    change(first, '')
    assert first.action() == 'clear'
    for stored in (INVALID, 'ambiguous-legacy-value'):
        first.load(stored)
        assert first.stored == stored and first.edit.text() == '' and first.action() == 'keep'
        first.eye.click()
        assert first.edit.displayText() == '' and first.action() == 'keep'
    first.load(STORED, allow_reveal=False)
    assert first.edit.text() == '' and first.action() == 'keep'


def verify_database():
    path = str(Path(_isolation.temp_dir()) / 'p11-connections.json')
    with (
        patch.object(db_connect, 'HARNESS_CONNECTIONS_FILE', path),
        patch.object(config, 'HARNESS_CONNECTIONS_FILE', path),
    ):
        initial = db_connect.upsert_connection({'id': 'p11', 'dialect': 'mysql', 'password': INVALID})
        catalog = WorkspaceCatalog()
        catalog.refresh()
        dialog = _WorkspaceConnectionDialog('en', initial)
        item, password = dialog.payload()
        assert password is None
        item['password'] = 'enc:stale-local-value'
        saved = catalog.save(item, password, password_action=dialog.password_action())
        assert saved['password'] == INVALID
        change(dialog.password_row, REPLACEMENT)
        item, password = dialog.payload()
        saved = catalog.save(item, password, password_action=dialog.password_action())
        assert saved['password'] == REPLACED
        dialog = ConnectionDialog('en', saved, locked_dialect='mysql')
        assert dialog.password.text() == REPLACEMENT
        change(dialog.password_row, '')
        item, password = dialog.payload()
        saved = catalog.save(item, password, password_action=dialog.password_action())
        assert password == '' and saved['password'] == ''
        assert db_connect.load_connections()[0]['password'] == ''


def verify_ops():
    category = {'id': ops_ssh.UNCATEGORIZED_ID, 'name': 'Fixture'}
    server = {'id': 'p11', 'host': 'fixture.invalid', 'username': 'tester',
              'password_token': INVALID, 'key_passphrase_token': STORED}
    dialog = ServerEditorDialog('en', server, [category])
    assert dialog.password_edit.text() == ''
    assert dialog.server_data()['password_token'] == INVALID
    assert dialog.server_data()['key_passphrase_token'] == STORED
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    change(dialog.key_passphrase_field, '')
    change(dialog.password_field, REPLACEMENT)
    saved = ops_ssh._normalize_server(dialog.server_data())
    assert saved['key_passphrase_token'] == '' and saved['password_token'] == REPLACED
    dialog = ServerEditorDialog('en', saved, [category])
    change(dialog.password_field, '')
    assert dialog._draft_server(for_persist=False)['password_token'] == ''
    assert ops_ssh._normalize_server(dialog.server_data())['password_token'] == ''
    assert ops_ssh._normalize_server(dict(server, password_token='opaque'))['password_token'] == 'opaque'


def verify_transport():
    profile = {'host': 'db.fixture.invalid', 'ssh_enabled': True, 'ssh_auth_method': 'password',
               'ssh_host': 'jump.fixture.invalid', 'ssh_username': 'tester', 'ssh_password_token': STORED,
               'tls_enabled': True, 'tls_client_cert_path': 'fixture.crt', 'tls_client_key_path': 'fixture.key',
               'tls_client_key_password_token': INVALID}
    dialog = DataCenterTransportDialog('en', profile)
    assert dialog.payload()['tls_client_key_password_token'] == INVALID
    change(dialog.ssh_password, '')
    change(dialog.tls_key_password, REPLACEMENT)
    saved = dict(profile, **dialog.payload())
    spec = DataCenterConnectionTransport.from_profile(saved, 'mysql')
    assert spec.ssh_password_token == '' and spec.tls_client_key_password_token == REPLACED
    try:
        DataCenterSshTunnelManager._authenticate(SimpleNamespace(), None, spec)
    except SshTunnelError as error:
        assert error.code == 'SSH_SECRET_UNAVAILABLE'
    else:
        raise AssertionError('cleared SSH password attempted authentication')
    private_key = dict(profile, ssh_auth_method='private_key', ssh_password_token='',
                       ssh_private_key_path='fixture.ssh', ssh_key_passphrase_token=STORED,
                       tls_client_key_password_token=STORED)
    dialog = DataCenterTransportDialog('en', private_key)
    change(dialog.ssh_key_passphrase, '')
    change(dialog.tls_key_password, '')
    saved = dialog.payload()
    assert saved['ssh_key_passphrase_token'] == saved['tls_client_key_password_token'] == ''


def verify_ai_intent():
    field = PasswordField('en', INVALID)
    panel = SimpleNamespace(ai_token_row=field, _ai_original_provider='intranet',
                            ai_provider=SimpleNamespace(currentData=lambda: 'intranet'))
    action = SettingsPanel._ai_token_action
    assert action(panel) == 'preserve' and action(panel, new_profile=True) == 'clear'
    change(field, REPLACEMENT)
    assert action(panel) == 'replace'
    change(field, '')
    assert action(panel) == 'clear'
    field.load(STORED)
    field.eye.click()
    field.load(INVALID)
    assert not field.eye.isChecked() and action(panel) == 'preserve'


if __name__ == '__main__':
    app = QApplication([])
    with (
        patch.object(secure_store, 'decrypt_secret', side_effect=unseal),
        patch.object(secure_store, 'encrypt_secret', return_value=REPLACED),
        patch.object(ops_ssh, 'encrypt_secret', return_value=REPLACED),
    ):
        for verify in (verify_widget, verify_database, verify_ops, verify_transport, verify_ai_intent):
            verify()
            print(f'PASS  {verify.__name__}')
    _isolation.assert_untouched()
    print('RESULT: 5 passed, 0 failed')
