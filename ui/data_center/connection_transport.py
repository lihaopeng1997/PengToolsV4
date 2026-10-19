"""Compact Data Center-only SSH/TLS connection settings and test workers."""

from __future__ import annotations

import threading
from typing import Any, Mapping

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.design_system import apply_button
from ui.dialog_buttons import clamp_dialog_geometry
from ui.field_metrics import size_compact_button, size_enum_combo, size_line


_SSH_FIELDS = (
    "ssh_enabled", "ssh_host", "ssh_port", "ssh_username", "ssh_auth_method",
    "ssh_password_token", "ssh_private_key_path", "ssh_key_passphrase_token",
)
_TLS_FIELDS = (
    "tls_enabled", "tls_ca_path", "tls_client_cert_path", "tls_client_key_path",
    "tls_client_key_password_token",
)


def _encrypt_secret(value: str) -> str:
    if not value:
        return ""
    from tools.secure_store import encrypt_secret

    token = encrypt_secret(value)
    if not str(token).startswith(("dpapi:", "enc:")):
        raise RuntimeError("SECURE_STORE_UNAVAILABLE")
    return str(token)


class DataCenterTransportDialog(QDialog):
    """Edit independent SSH/TLS settings without exposing stored secrets."""

    def __init__(self, language: str, profile: Mapping[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.zh = self.language == "zh"
        self._profile = dict(profile or {})
        self.setWindowTitle("连接传输" if self.zh else "Connection transport")
        clamp_dialog_geometry(self, 610, 620, min_width=480, min_height=400)
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setVerticalSpacing(8)
        root.addLayout(form, 1)

        self.ssh_enabled = QCheckBox("使用 SSH 隧道" if self.zh else "Use SSH tunnel")
        self.ssh_enabled.setChecked(bool(self._profile.get("ssh_enabled", False)))
        form.addRow(self.ssh_enabled)
        self.ssh_host = QLineEdit(str(self._profile.get("ssh_host") or ""))
        self.ssh_port = QLineEdit(str(self._profile.get("ssh_port") or 22))
        self.ssh_username = QLineEdit(str(self._profile.get("ssh_username") or ""))
        self.ssh_auth = QComboBox()
        self.ssh_auth.addItem("密码" if self.zh else "Password", "password")
        self.ssh_auth.addItem("私钥文件" if self.zh else "Private key file", "private_key")
        index = self.ssh_auth.findData(str(self._profile.get("ssh_auth_method") or "password"))
        self.ssh_auth.setCurrentIndex(max(index, 0))
        self.ssh_password = QLineEdit()
        self.ssh_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_password.setPlaceholderText("留空保留已保存口令" if self.zh else "Blank keeps the saved secret")
        self.ssh_key_path = QLineEdit(str(self._profile.get("ssh_private_key_path") or ""))
        self.ssh_key_passphrase = QLineEdit()
        self.ssh_key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_key_passphrase.setPlaceholderText("留空保留已保存口令" if self.zh else "Blank keeps the saved secret")
        for widget, kind in (
            (self.ssh_host, "path"), (self.ssh_port, "std"),
            (self.ssh_username, "path"), (self.ssh_password, "path"),
            (self.ssh_key_path, "path"), (self.ssh_key_passphrase, "path"),
        ):
            size_line(widget, kind)
        size_enum_combo(self.ssh_auth)
        form.addRow("SSH 主机" if self.zh else "SSH host", self.ssh_host)
        form.addRow("SSH 端口" if self.zh else "SSH port", self.ssh_port)
        form.addRow("SSH 用户" if self.zh else "SSH user", self.ssh_username)
        form.addRow("认证方式" if self.zh else "Authentication", self.ssh_auth)
        form.addRow("SSH 密码" if self.zh else "SSH password", self.ssh_password)
        key_row = QHBoxLayout()
        key_row.addWidget(self.ssh_key_path, 1)
        self.key_browse = QPushButton("浏览…" if self.zh else "Browse…")
        size_compact_button(self.key_browse)
        apply_button(self.key_browse, "secondary", compact=True)
        self.key_browse.clicked.connect(lambda: self._choose_file(self.ssh_key_path))
        key_row.addWidget(self.key_browse)
        form.addRow("私钥路径" if self.zh else "Private key", key_row)
        form.addRow("私钥口令" if self.zh else "Key passphrase", self.ssh_key_passphrase)

        self.tls_enabled = QCheckBox(
            "启用并验证数据库 TLS" if self.zh else "Enable and verify database TLS"
        )
        self.tls_enabled.setChecked(bool(self._profile.get("tls_enabled", False)))
        form.addRow(self.tls_enabled)
        self.tls_ca = QLineEdit(str(self._profile.get("tls_ca_path") or ""))
        self.tls_cert = QLineEdit(str(self._profile.get("tls_client_cert_path") or ""))
        self.tls_key = QLineEdit(str(self._profile.get("tls_client_key_path") or ""))
        self.tls_key_password = QLineEdit()
        self.tls_key_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.tls_key_password.setPlaceholderText("留空保留已保存口令" if self.zh else "Blank keeps the saved secret")
        for widget in (self.tls_ca, self.tls_cert, self.tls_key, self.tls_key_password):
            size_line(widget, "path")
        self._tls_browse_buttons: list[QPushButton] = []
        form.addRow("CA 证书" if self.zh else "CA certificate", self._file_row(self.tls_ca))
        form.addRow("客户端证书" if self.zh else "Client certificate", self._file_row(self.tls_cert))
        form.addRow("客户端私钥" if self.zh else "Client key", self._file_row(self.tls_key))
        form.addRow("私钥口令" if self.zh else "Key passphrase", self.tls_key_password)

        note = QLabel(
            "首次连接先读取 SSH 指纹；只有你确认后才会认证。TLS 始终校验数据库主机名。"
            if self.zh else
            "SSH fingerprints are read before authentication. Authentication starts only after your confirmation. Database TLS always verifies the original host name."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用" if self.zh else "Apply")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消" if self.zh else "Cancel")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.ssh_enabled.toggled.connect(self._sync_enabled)
        self.ssh_auth.currentIndexChanged.connect(self._sync_enabled)
        self.tls_enabled.toggled.connect(self._sync_enabled)
        self._sync_enabled()

    def _file_row(self, edit: QLineEdit):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        button = QPushButton("浏览…" if self.zh else "Browse…")
        size_compact_button(button)
        apply_button(button, "secondary", compact=True)
        button.clicked.connect(lambda: self._choose_file(edit))
        row.addWidget(button)
        self._tls_browse_buttons.append(button)
        return row

    @staticmethod
    def _choose_file(edit: QLineEdit) -> None:
        title = "选择文件" if getattr(edit.window(), "zh", False) else "Select file"
        path, _selected = QFileDialog.getOpenFileName(edit.window(), title, edit.text())
        if path:
            edit.setText(path)

    def _sync_enabled(self, *_args) -> None:
        ssh = self.ssh_enabled.isChecked()
        password_mode = self.ssh_auth.currentData() == "password"
        self.ssh_host.setEnabled(ssh)
        self.ssh_port.setEnabled(ssh)
        self.ssh_username.setEnabled(ssh)
        self.ssh_auth.setEnabled(ssh)
        self.ssh_password.setEnabled(ssh and password_mode)
        self.ssh_key_path.setEnabled(ssh and not password_mode)
        self.key_browse.setEnabled(ssh and not password_mode)
        self.ssh_key_passphrase.setEnabled(ssh and not password_mode)
        tls = self.tls_enabled.isChecked()
        for widget in (self.tls_ca, self.tls_cert, self.tls_key, self.tls_key_password):
            widget.setEnabled(tls)
        for button in self._tls_browse_buttons:
            button.setEnabled(tls)

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ssh_enabled": self.ssh_enabled.isChecked(),
            "tls_enabled": self.tls_enabled.isChecked(),
        }
        if self.ssh_enabled.isChecked():
            try:
                port = int(self.ssh_port.text().strip() or "22")
            except ValueError:
                raise ValueError("SSH_PORT_INVALID") from None
            if not 1 <= port <= 65535:
                raise ValueError("SSH_PORT_INVALID")
            auth = str(self.ssh_auth.currentData() or "password")
            result.update({
                "ssh_host": self.ssh_host.text().strip(),
                "ssh_port": port,
                "ssh_username": self.ssh_username.text().strip(),
                "ssh_auth_method": auth,
            })
            if auth == "password":
                result["ssh_password_token"] = _encrypt_secret(self.ssh_password.text()) or (
                    str(self._profile.get("ssh_password_token") or "")
                    if self._profile.get("ssh_auth_method") == "password" else ""
                )
            else:
                result["ssh_private_key_path"] = self.ssh_key_path.text().strip()
                result["ssh_key_passphrase_token"] = _encrypt_secret(self.ssh_key_passphrase.text()) or (
                    str(self._profile.get("ssh_key_passphrase_token") or "")
                    if self._profile.get("ssh_auth_method") == "private_key" else ""
                )
        if self.tls_enabled.isChecked():
            result.update({
                "tls_ca_path": self.tls_ca.text().strip(),
                "tls_client_cert_path": self.tls_cert.text().strip(),
                "tls_client_key_path": self.tls_key.text().strip(),
                "tls_client_key_password_token": _encrypt_secret(self.tls_key_password.text()) or str(
                    self._profile.get("tls_client_key_password_token") or ""
                ),
            })
        return result


class ConnectionTransportWorker(QThread):
    """Bounded test/preflight worker; a close request never destroys a live QThread."""

    completed = pyqtSignal(str, object)

    def __init__(
        self,
        action: str,
        profile: Mapping[str, Any],
        database_secret: str = "",
        *,
        manager: Any = None,
        connector: Any = None,
        challenge_id: str = "",
        accepted_fingerprint: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.action = str(action)
        self.profile = dict(profile)
        self.database_secret = str(database_secret or "")
        self.manager = manager
        self.connector = connector
        self.challenge_id = str(challenge_id or "")
        self.accepted_fingerprint = str(accepted_fingerprint or "")
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                self.completed.emit(self.action, {"state": "cancelled"})
                return
            from tools.data_center.connection_transport import DataCenterConnectionTransport
            from tools.data_center.connection_transport import canonical_transport_provider as canonical_provider
            from tools.data_center.ssh_tunnel import SshTunnelError

            profile = dict(self.profile)
            provider = canonical_provider(
                profile.get("provider") or profile.get("engine") or profile.get("dialect"),
                profile.get("mode"),
            )
            spec = DataCenterConnectionTransport.from_profile(profile, provider, profile.get("mode"))
            if self.action in {"inspect", "confirm"}:
                if spec is None or not spec.ssh_enabled:
                    self.completed.emit(self.action, {"state": "invalid"})
                    return
                manager = self._manager()
                if self.action == "inspect":
                    inspection = manager.inspect_host_key(spec)
                else:
                    inspection = manager.confirm_first_use(
                        spec, self.challenge_id, self.accepted_fingerprint
                    )
                self.completed.emit(self.action, {"state": inspection.state, "inspection": inspection})
                return
            if self.action != "connect":
                self.completed.emit(self.action, {"state": "invalid"})
                return
            if self._cancel.is_set():
                self.completed.emit(self.action, {"state": "cancelled"})
                return
            from tools.data_center.readonly_relational_connectors import connect_relational

            if provider in {"redis", "mongodb"}:
                from tools.data_center.nosql_transport import connect_nosql
                connect = self.connector or connect_nosql
            else:
                connect = self.connector or connect_relational
            connection = connect(
                provider,
                profile,
                self.database_secret,
                mode=profile.get("mode"),
                connect_timeout=8,
                read_timeout=8,
                connection_id=str(profile.get("id") or "transport-test"),
                ssh_manager=self._manager() if spec is not None and spec.ssh_enabled else None,
                **({"probe": True} if provider in {"redis", "mongodb"} else {}),
            )
            try:
                connection.close()
            except Exception:
                self.completed.emit(self.action, {"state": "cleanup_pending"})
                return
            self.completed.emit(
                self.action,
                {"state": "cancelled" if self._cancel.is_set() else "connected"},
            )
        except Exception as error:
            code = str(getattr(error, "code", "") or "CONNECT_FAILED")
            if code not in {
                "SSH_HOST_KEY_CHANGED", "SSH_HOST_KEY_CONFIRM_REQUIRED", "SSH_CLEANUP_PENDING",
                "SSH_DRIVER_UNAVAILABLE", "SSH_TRUST_STORE_UNAVAILABLE", "SSH_CHALLENGE_INVALID",
                "SSH_CONFIRMATION_MISMATCH", "TRANSPORT_PROFILE_INVALID", "UNSUPPORTED_TRANSPORT",
                "PROFILE_INCOMPLETE", "PROFILE_INVALID",
            }:
                code = "CONNECT_FAILED"
            self.completed.emit(self.action, {"state": "failed", "code": code})

    def _manager(self):
        if self.manager is None:
            from tools.data_center.connection_transport import default_ssh_tunnel_manager

            self.manager = default_ssh_tunnel_manager()
        return self.manager


__all__ = ["ConnectionTransportWorker", "DataCenterTransportDialog"]
