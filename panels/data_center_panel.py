"""Unified Data Center page and fixed-connection workspace tabs."""

from __future__ import annotations

from typing import Any, Callable, Mapping
import uuid

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.workspace_catalog import WorkspaceCatalog
from tools.data_center.completion_cache import CompletionCatalog
from ui.connection_dialog import ConnectionDialog
from ui.confirm_dialog import confirm_action, show_error
from ui.design_system import apply_button
from ui.field_metrics import size_line
from ui import layout_metrics as _lm
from ui.layout_metrics import TREE_ITEM_H
from ui.navigation_model import FIXED_DB_PAGES, dialect_for_nav
from ui.page_chrome import make_page_header, set_header_home_language
from ui.data_center.workspace_tree import TREE_LOADED_ROLE, WorkspaceTree
from ui.data_center.connection_transport import (
    ConnectionTransportWorker,
    DataCenterTransportDialog,
)
from ui.data_center.object_tree_workspace import ObjectTreeWorkspace
from ui.data_center.workspace_snapshot import WorkspacePersistence
from ui.wrap_layout import WrapLayout
from tools.data_center.object_tree import MAX_METADATA_HOSTS, ObjectTreeError, TreeNode


class _WorkspaceConnectionDialog(ConnectionDialog):
    """Add Data Center grouping metadata without changing the shared dialog."""

    def __init__(self, language: str, item: dict | None = None, parent=None):
        super().__init__(language, item, parent)
        zh = language == "zh"
        form = self.form_host.layout()
        self.group_path = QLineEdit(str((item or {}).get("group_path") or ""))
        self.group_path.setPlaceholderText("项目/子项目" if zh else "Project/subproject")
        size_line(self.group_path, "path")
        self.environment = QLineEdit(str((item or {}).get("environment") or ""))
        self.environment.setPlaceholderText("local / test / staging / production" if zh else "local / test / staging / production")
        size_line(self.environment, "std")
        self.favorite = QCheckBox("收藏" if zh else "Favorite")
        self.favorite.setChecked(bool((item or {}).get("favorite", (item or {}).get("is_favorite", False))))
        form.addRow("项目" if zh else "Project", self.group_path)
        form.addRow("环境" if zh else "Environment", self.environment)
        form.addRow("" if zh else "", self.favorite)
        self._transport_settings = {
            key: value for key, value in (item or {}).items()
            if key in {
                "ssh_enabled", "ssh_host", "ssh_port", "ssh_username", "ssh_auth_method",
                "ssh_password_token", "ssh_private_key_path", "ssh_key_passphrase_token",
                "tls_enabled", "tls_ca_path", "tls_client_cert_path", "tls_client_key_path",
                "tls_client_key_password_token",
            }
        }
        self._transport_button = QPushButton("SSH / TLS 设置…" if zh else "SSH / TLS settings…")
        apply_button(self._transport_button, "secondary", compact=True)
        self._transport_button.clicked.connect(self._open_transport_settings)
        form.addRow("传输" if zh else "Transport", self._transport_button)
        self._transport_test_worker = None
        self._transport_test_result = None
        self._transport_test_pending_close = False
        self._transport_test_context = None

    def payload(self) -> tuple[dict, str]:
        item, password = super().payload()
        item["group_path"] = self.group_path.text().strip()
        item["environment"] = self.environment.text().strip()
        item["favorite"] = self.favorite.isChecked()
        transport_keys = {
            "ssh_enabled", "ssh_host", "ssh_port", "ssh_username", "ssh_auth_method",
            "ssh_password_token", "ssh_private_key_path", "ssh_key_passphrase_token",
            "tls_enabled", "tls_ca_path", "tls_client_cert_path", "tls_client_key_path",
            "tls_client_key_password_token",
        }
        for key in transport_keys:
            item.pop(key, None)
        item.update(self._transport_settings)
        if (item.get("ssh_enabled") or item.get("tls_enabled")) and item.get("dialect") == "mysql":
            item["mode"] = "mysql"
        return item, password

    def _open_transport_settings(self) -> None:
        dialog = DataCenterTransportDialog(self.language, self._transport_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._transport_settings = dialog.payload()
        except Exception as error:
            QMessageBox.warning(
                self,
                "连接传输" if self.language == "zh" else "Connection transport",
                "SSH 端口无效。" if self.language == "zh" else str(getattr(error, "args", ["Invalid transport settings"])[0]),
            )

    def accept(self) -> None:
        if self._transport_test_worker is not None and self._transport_test_worker.isRunning():
            return
        try:
            item, _password = super().payload()
            item.update(self._transport_settings)
            if item.get("ssh_enabled") or item.get("tls_enabled"):
                from tools.data_center.connection_transport import (
                    ConnectionTransportError,
                    DataCenterConnectionTransport,
                )
                from tools.data_center.connection_transport import canonical_transport_provider as canonical_provider

                provider = canonical_provider(item.get("dialect"), item.get("mode"))
                DataCenterConnectionTransport.from_profile(item, provider, item.get("mode"))
        except Exception as error:
            code = str(getattr(error, "code", "") or getattr(error, "args", [""])[0])
            labels = {
                "TRANSPORT_PROFILE_INVALID": "SSH/TLS 配置不完整或无效。",
                "UNSUPPORTED_TRANSPORT": "此连接模式或 SSH/TLS 组合暂不支持。请使用单目标连接；目前仅 MySQL、OceanBase MySQL 支持 SSH 与 TLS 同时启用。",
                "TRANSPORT_SECRET_UNAVAILABLE": "无法安全保存传输口令。",
            }
            text = labels.get(code, "请检查连接配置。") if self.language == "zh" else code.replace("_", " ").title()
            QMessageBox.warning(self, "保存连接" if self.language == "zh" else "Save", text)
            return
        super().accept()

    def _on_test_connection(self) -> None:
        if self._transport_test_worker is not None and self._transport_test_worker.isRunning():
            return
        try:
            item, password = self._draft_item()
            item.update(self._transport_settings)
            if not item.get("ssh_enabled") and not item.get("tls_enabled"):
                return super()._on_test_connection()
            from tools.data_center.connection_transport import (
                ConnectionTransportError,
                DataCenterConnectionTransport,
                default_ssh_tunnel_manager,
            )
            from tools.data_center.connection_transport import canonical_transport_provider as canonical_provider

            provider = canonical_provider(item.get("dialect"), item.get("mode"))
            spec = DataCenterConnectionTransport.from_profile(item, provider, item.get("mode"))
            if spec is None:
                return super()._on_test_connection()
            item["provider"] = provider
            item["id"] = str(item.get("id") or ("transport-test-" + uuid.uuid4().hex))
            manager = default_ssh_tunnel_manager() if spec.ssh_enabled else None
            self._transport_test_context = (item, password, manager)
            self._start_transport_test_worker("inspect" if spec.ssh_enabled else "connect")
        except Exception as error:
            code = str(getattr(error, "code", "") or getattr(error, "args", [""])[0])
            text = code.replace("_", " ").title() or ("连接配置无效" if self.language == "zh" else "Invalid connection settings")
            QMessageBox.warning(self, "测试连接" if self.language == "zh" else "Test", text)

    def _start_transport_test_worker(
        self, action: str, *, challenge_id: str = "", accepted_fingerprint: str = ""
    ) -> None:
        if self._transport_test_pending_close:
            return
        item, password, manager = self._transport_test_context
        worker = ConnectionTransportWorker(
            action,
            item,
            password,
            manager=manager,
            challenge_id=challenge_id,
            accepted_fingerprint=accepted_fingerprint,
            parent=self,
        )
        self._transport_test_worker = worker
        self._transport_test_result = None
        worker.completed.connect(self._on_transport_test_completed)
        worker.finished.connect(self._on_transport_test_finished)
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        worker.start()

    def _on_transport_test_completed(self, action: str, result: object) -> None:
        self._transport_test_result = (str(action), result if isinstance(result, dict) else {})

    def _on_transport_test_finished(self) -> None:
        worker = self._transport_test_worker
        if worker is None:
            return
        if not worker.wait(0):
            QTimer.singleShot(10, self._on_transport_test_finished)
            return
        result = self._transport_test_result or (getattr(worker, "action", ""), {"state": "failed", "code": "CONNECT_FAILED"})
        self._transport_test_worker = None
        self._transport_test_result = None
        if worker is not None:
            worker.deleteLater()
        self.test_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        if self._transport_test_pending_close:
            self._transport_test_context = None
            QDialog.reject(self)
            return
        action, payload = result
        state = str(payload.get("state") or "failed")
        if action == "inspect":
            inspection = payload.get("inspection")
            if state == "trusted":
                self._start_transport_test_worker("connect")
            elif state == "unknown" and inspection is not None:
                title = "确认 SSH 主机指纹" if self.language == "zh" else "Confirm SSH host fingerprint"
                prompt = (
                    f"主机：{inspection.host}:{inspection.port}\n密钥类型：{inspection.key_type}\nSHA-256：{inspection.fingerprint}\n\n"
                    "仅在通过独立渠道核实后确认。确认后才会继续认证。"
                    if self.language == "zh" else
                    f"Host: {inspection.host}:{inspection.port}\nKey type: {inspection.key_type}\nSHA-256: {inspection.fingerprint}\n\nConfirm only after verifying this fingerprint through an independent channel. Authentication starts afterward."
                )
                answer = QMessageBox.question(
                    self,
                    title,
                    prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._start_transport_test_worker(
                        "confirm",
                        challenge_id=inspection.challenge_id,
                        accepted_fingerprint=inspection.fingerprint,
                    )
                else:
                    self._transport_test_context = None
            elif state == "changed":
                QMessageBox.warning(self, "SSH" if self.language == "zh" else "SSH", "SSH 主机密钥已变化，已阻止连接。" if self.language == "zh" else "The SSH host key changed; the connection was blocked.")
                self._transport_test_context = None
            else:
                self._show_transport_test_failure(payload)
                self._transport_test_context = None
            return
        if action == "confirm":
            if state == "trusted":
                self._start_transport_test_worker("connect")
            else:
                self._show_transport_test_failure(payload)
                self._transport_test_context = None
            return
        if state == "connected":
            QMessageBox.information(self, "测试连接" if self.language == "zh" else "Test", "连接成功。" if self.language == "zh" else "Connection succeeded.")
        else:
            self._show_transport_test_failure(payload)
        self._transport_test_context = None

    def _show_transport_test_failure(self, payload: Mapping[str, Any]) -> None:
        code = str(payload.get("code") or payload.get("state") or "CONNECT_FAILED")
        messages = {
            "SSH_HOST_KEY_CHANGED": ("SSH 主机指纹已变化，连接已阻止。", "The SSH host key changed. Connection was blocked."),
            "SSH_AUTH_FAILED": ("SSH 认证失败，请检查用户名和认证凭据。", "SSH authentication failed. Check the user and credentials."),
            "TLS_CONFIGURATION_INVALID": ("TLS 配置无效，请检查证书及私钥设置。", "Invalid TLS configuration. Check the certificate and key settings."),
            "SSH_CLEANUP_PENDING": ("连接仍在清理，请稍后重试。", "Connection cleanup is still in progress. Try again shortly."),
            "UNSUPPORTED_TRANSPORT": ("当前连接组合不支持所选传输设置。", "This connection does not support the selected transport settings."),
        }
        pair = messages.get(code, ("连接测试失败，请检查连接配置。", "Connection test failed. Check the connection settings."))
        text = pair[0] if self.language == "zh" else pair[1]
        QMessageBox.warning(self, "测试连接" if self.language == "zh" else "Test", text)

    def _defer_close_for_transport_worker(self) -> bool:
        worker = self._transport_test_worker
        if worker is None:
            return False
        self._transport_test_pending_close = True
        worker.request_cancel()
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        return True

    def reject(self) -> None:
        if self._defer_close_for_transport_worker():
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._defer_close_for_transport_worker():
            event.ignore()
            return
        super().closeEvent(event)


class _FixedSqlWorkbench:
    """Mixin guard preventing AiWorkbenchPanel's legacy first-profile fallback."""

    def _reload_bound_connection(self):
        from panels import ai_workbench_panel as module

        wanted = str(self._connection_id or "")
        try:
            rows = module.load_connections()
        except Exception:
            zh = getattr(self, "language", "zh") == "zh"
            show_error(
                self,
                self._title(),
                "固定连接读取失败；当前连接 ID 保持不变。" if zh else
                "Could not load the fixed connection; its connection ID is unchanged.",
            )
            return False
        profile = next((
            row for row in rows
            if isinstance(row, dict) and str(row.get("id") or "") == wanted
        ), None)
        self._bound_conn_item = dict(profile) if profile is not None else None
        self._snapshot = module.load_snapshot(wanted) if profile is not None else None
        if profile is not None:
            self.nl_input.bind_snapshot(self._snapshot)
            self._bind_editors_schema()
        return True


class _DataCenterRedisPanel:
    def __init__(self, language: str, connection_id: str, **kwargs):
        from tools.data_center.nosql_transport import open_data_center_nosql_connection
        self._connection_opener = open_data_center_nosql_connection
        self._data_center_initializing = True
        self._data_center_connection_id = str(connection_id or "")
        super().__init__(language, **kwargs)
        self._pin_connection()
        self._data_center_initializing = False

    def _refresh_keys(self):
        if getattr(self, "_data_center_initializing", False):
            return
        return super()._refresh_keys()

    def _pin_connection(self):
        combo = self.conn_combo
        wanted = self._data_center_connection_id
        index = next((
            i for i in range(combo.count())
            if isinstance(combo.itemData(i), dict)
            and str(combo.itemData(i).get("id") or "") == wanted
        ), -1)
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._on_connection_changed()
        combo.hide()
        combo.setEnabled(False)
        for name in ("conn_new_btn", "conn_edit_btn", "conn_del_btn"):
            getattr(self, name).hide()


class _DataCenterMongoPanel:
    def __init__(self, language: str, connection_id: str, **kwargs):
        from tools.data_center.nosql_transport import open_data_center_nosql_connection
        self._connection_opener = open_data_center_nosql_connection
        self._data_center_initializing = True
        self._data_center_connection_id = str(connection_id or "")
        super().__init__(language, **kwargs)
        self._pin_connection()
        self._data_center_initializing = False

    def _refresh_collections(self):
        if getattr(self, "_data_center_initializing", False):
            return
        return super()._refresh_collections()

    def _pin_connection(self):
        combo = self.conn_combo
        wanted = self._data_center_connection_id
        index = next((
            i for i in range(combo.count())
            if isinstance(combo.itemData(i), dict)
            and str(combo.itemData(i).get("id") or "") == wanted
        ), -1)
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._on_connection_changed()
        combo.hide()
        combo.setEnabled(False)
        for name in ("conn_new_btn", "conn_edit_btn", "conn_del_btn"):
            getattr(self, name).hide()


class _ConnectionWorkspace(QWidget):
    def __init__(self, connection_id: str, profile: dict, host: QWidget, parent=None):
        super().__init__(parent)
        self.connection_id = str(connection_id)
        self.profile = dict(profile)
        self.host = host
        self.agent_widget = getattr(host, "data_center_agent", None)
        self.side_tabs = getattr(host, "side_tabs", None)
        self.agent_visible = False
        self._layout_mode = "wide"
        self._low_height = False
        self._agent_presentation = "closed"
        self._side_tabs_was_visible = bool(
            self.side_tabs is not None and not self.side_tabs.isHidden()
        )
        self.setProperty("connectionId", self.connection_id)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.view_splitter.setObjectName("data-center-connection-view")
        self.view_splitter.setHandleWidth(8)
        self.view_splitter.setChildrenCollapsible(True)
        self.view_splitter.addWidget(host)
        self.view_splitter.setCollapsible(0, False)
        if self.agent_widget is not None and self.side_tabs is not None:
            agent_index = self.side_tabs.indexOf(self.agent_widget)
            if agent_index >= 0:
                self.side_tabs.removeTab(agent_index)
                self.view_splitter.addWidget(self.agent_widget)
                self.view_splitter.setCollapsible(1, True)
                self.agent_widget.setMinimumWidth(340)
                self.agent_widget.hide()
            else:
                self.agent_widget = None
        else:
            self.agent_widget = None
        self.view_splitter.setStretchFactor(0, 1)
        if self.agent_widget is not None:
            self.view_splitter.setStretchFactor(1, 0)
        layout.addWidget(self.view_splitter, 1)

    def set_agent_visible(self, visible: bool) -> None:
        if self.agent_widget is None:
            return
        self.agent_visible = bool(visible)
        self._apply_agent_presentation()

    def apply_layout_mode(self, mode: str, low_height: bool = False) -> None:
        self._layout_mode = str(mode or "wide")
        self._low_height = bool(low_height)
        self._apply_agent_presentation()

    def _requires_drawer(self) -> bool:
        if self._layout_mode == "narrow":
            return True
        required = _lm.REQ_RIGHT_MIN + 340 + self.view_splitter.handleWidth()
        return self.view_splitter.width() < required

    def _apply_agent_presentation(self) -> None:
        agent = self.agent_widget
        if agent is None:
            return
        if not self.agent_visible:
            agent.hide()
            self.host.show()
            if self.side_tabs is not None:
                self.side_tabs.setVisible(self._side_tabs_was_visible)
            self.view_splitter.setSizes([max(1, self.view_splitter.width()), 0])
            self._agent_presentation = "closed"
            return

        if self.side_tabs is not None:
            # Free the SQL editor/result area while the same Agent host is
            # displayed in the unified workspace's right rail or drawer.
            self.side_tabs.hide()
        if self._requires_drawer():
            changed = self._agent_presentation != "drawer"
            self.host.hide()
            agent.show()
            if changed:
                self.view_splitter.setSizes([0, max(1, self.view_splitter.width())])
            self._agent_presentation = "drawer"
            return

        changed = self._agent_presentation != "rail"
        self.host.show()
        agent.show()
        if changed:
            width = max(1, self.view_splitter.width())
            rail = min(340, max(0, width - _lm.REQ_RIGHT_MIN - self.view_splitter.handleWidth()))
            self.view_splitter.setSizes([max(_lm.REQ_RIGHT_MIN, width - rail), rail])
        self._agent_presentation = "rail"

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self.agent_visible:
            self._apply_agent_presentation()


class DataCenterPanel(QWidget):
    """One unified catalog plus connection-bound, lazily constructed hosts."""

    workspace_opened = pyqtSignal(str)

    def __init__(
        self,
        language: str = "zh",
        *,
        catalog: WorkspaceCatalog | None = None,
        host_factory: Callable[[dict, str], QWidget] | None = None,
        object_tree_composition_factory: Callable[[str], object] | None = None,
        workspace_history_service=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.catalog = catalog or WorkspaceCatalog()
        self._host_factory = host_factory or self._default_host_factory
        self._object_tree_composition_factory = object_tree_composition_factory
        self._workspaces: dict[str, _ConnectionWorkspace] = {}
        self._tree_owners: dict[str, ObjectTreeWorkspace] = {}
        self._completion_catalogs: dict[str, CompletionCatalog] = {}
        self._tree_owner_close_futures: dict[str, object] = {}
        self._tree_owner_poll_scheduled: set[str] = set()
        self._tree_capture_refresh: dict[str, bool] = {}
        self._agent_return_tabs: dict[str, int] = {}
        self._pending_close_ids: set[str] = set()
        self._workspace_close_states: dict[str, dict] = {}
        self._workspace_close_status = ""
        self._manual_shutdown_gated = False
        self._tree_shutdown_started = False
        self.workspace_persistence = WorkspacePersistence(self, service=workspace_history_service)
        self.workspace_persistence.changed.connect(self._sync_actions)
        self._catalog_collapsed = False
        self._layout_mode = "wide"
        self._low_height = False
        self._build_ui()
        self.refresh_catalog()

    def _sync_connection_menu(self) -> None:
        for action, button in getattr(self, "_connection_menu_actions", ()):
            action.setText(button.text())
            action.setEnabled(button.isEnabled())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header, self.page_title, self.page_subtitle = make_page_header(
            "数据中心", "项目、环境、引擎与连接工作区", "database", language=self.language
        )
        self._page_header = header
        # 模块 Tab 已显示“数据中心”，页内不再重复标题
        header.hide()
        root.addWidget(header)

        toolbar = QFrame(self)
        toolbar.setObjectName("data-center-toolbar")
        toolbar.setProperty("surfaceRole", "toolbar")
        row = WrapLayout(toolbar, spacing=6)
        row.setContentsMargins(8, 4, 8, 6)
        self.catalog_toggle_btn = QPushButton()
        self.catalog_toggle_btn.setCheckable(True)
        self.catalog_toggle_btn.setChecked(True)
        apply_button(self.catalog_toggle_btn, "ghost", compact=True)
        self.catalog_toggle_btn.toggled.connect(self._toggle_catalog)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("data-center-search")
        size_line(self.search_edit, "path")
        self.search_edit.setMinimumWidth(150)
        self.search_edit.textChanged.connect(self.tree_filter_changed)
        self.new_btn = self._button()
        self.new_btn.clicked.connect(self._new_connection)
        self.edit_btn = self._button()
        self.edit_btn.clicked.connect(self._edit_connection)
        self.delete_btn = self._button()
        self.delete_btn.clicked.connect(self._delete_connection)
        self.favorite_btn = self._button()
        self.favorite_btn.clicked.connect(self._toggle_favorite)
        self.refresh_btn = self._button()
        self.refresh_btn.clicked.connect(self.refresh_catalog)
        self.open_btn = self._button(primary=True)
        self.open_btn.clicked.connect(self.open_selected_connection)
        self.new_query_btn = self._button()
        self.new_query_btn.clicked.connect(self._new_query)
        self.agent_btn = self._button()
        self.agent_btn.setCheckable(True)
        self.agent_btn.clicked.connect(self._toggle_agent)
        self.workspace_btn = self._button()
        workspace_menu = QMenu(self.workspace_btn)
        self.workspace_save_action = workspace_menu.addAction("")
        self.workspace_restore_action = workspace_menu.addAction("")
        self.workspace_save_action.triggered.connect(self.workspace_persistence.save)
        self.workspace_restore_action.triggered.connect(self.workspace_persistence.restore)
        self.workspace_btn.setMenu(workspace_menu)
        # 低频的编辑/删除/收藏/刷新目录收进「连接管理」菜单；按钮对象保留，
        # 继续承担启用状态与既有处理函数，菜单项只是它们的入口。
        self.connection_menu_btn = self._button()
        self.connection_menu = QMenu(self.connection_menu_btn)
        self._connection_menu_actions = []
        for button in (self.edit_btn, self.delete_btn, self.favorite_btn, self.refresh_btn):
            action = self.connection_menu.addAction("")
            action.triggered.connect(button.click)
            self._connection_menu_actions.append((action, button))
            button.setParent(toolbar)
            button.hide()
        self.connection_menu.aboutToShow.connect(self._sync_connection_menu)
        self.connection_menu_btn.setMenu(self.connection_menu)
        self.connection_menu_btn.setStyleSheet("QPushButton::menu-indicator { width: 0; image: none; }")
        # V1.1：页面不再有独立的全局工具条（DBX 式三栏）。低频入口进「⋯」菜单，
        # 按钮对象保留并继续承担启用状态与既有处理函数。
        for button in (self.open_btn, self.workspace_btn):
            row.addWidget(button)
        root.addWidget(toolbar)
        toolbar.hide()
        self._legacy_toolbar = toolbar
        self.catalog_warning = QLabel(self)
        self.catalog_warning.setObjectName("ops-safety-note")
        self.catalog_warning.setWordWrap(True)
        self.catalog_warning.hide()
        root.addWidget(self.catalog_warning)
        self.workspace_status = QLabel(self)
        self.workspace_status.setObjectName("workspace-status")
        self.workspace_status.setWordWrap(True)
        self.workspace_status.hide()
        root.addWidget(self.workspace_status)

        self._body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._body_splitter.setObjectName("data-center-body-splitter")
        self._body_splitter.setChildrenCollapsible(False)
        self._body_splitter.setHandleWidth(8)

        self.catalog_pane = QFrame(self._body_splitter)
        self.catalog_pane.setObjectName("data-center-catalog-pane")
        catalog_layout = QVBoxLayout(self.catalog_pane)
        catalog_layout.setContentsMargins(8, 6, 6, 8)
        catalog_layout.setSpacing(6)
        catalog_head = QHBoxLayout()
        catalog_head.setContentsMargins(0, 0, 0, 0)
        catalog_head.setSpacing(4)
        self.search_edit.setParent(self.catalog_pane)
        self.search_edit.setMinimumWidth(80)
        catalog_head.addWidget(self.search_edit, 1)
        for button in (self.new_btn, self.connection_menu_btn):
            button.setParent(self.catalog_pane)
            apply_button(button, "ghost", compact=True)
            button.setFixedWidth(30)
            catalog_head.addWidget(button)
        catalog_layout.addLayout(catalog_head)
        self.tree = WorkspaceTree(language=self.language, parent=self.catalog_pane)
        self.tree.setMinimumWidth(150)
        self.tree.setStyleSheet(f"QTreeView::item {{ min-height: {TREE_ITEM_H}px; }}")
        self.tree.connection_selected.connect(self._on_connection_selected)
        self.tree.connection_activated.connect(self._open_connection)
        self.tree.connection_ambiguous.connect(self._on_ambiguous_connection)
        self.tree.directory_connection_expanded.connect(self._on_tree_connection_expanded)
        self.tree.directory_node_expanded.connect(self._on_tree_node_expanded)
        self.tree.directory_more_requested.connect(self._on_tree_more_requested)
        self.tree.directory_node_activated.connect(self._on_tree_node_activated)
        catalog_layout.addWidget(self.tree, 1)
        directory_actions = QFrame(self.catalog_pane)
        directory_action_row = WrapLayout(directory_actions, spacing=6)
        directory_action_row.setContentsMargins(0, 2, 0, 0)
        self.directory_refresh_btn = self._button()
        self.directory_refresh_btn.clicked.connect(self.refresh_selected_directory)
        self.directory_close_btn = self._button()
        self.directory_close_btn.clicked.connect(self.close_selected_directory)
        directory_action_row.addWidget(self.directory_refresh_btn)
        directory_action_row.addWidget(self.directory_close_btn)
        catalog_layout.addWidget(directory_actions)
        directory_actions.hide()
        self.connection_menu.addSeparator()
        for button in (self.open_btn, self.new_query_btn, self.directory_refresh_btn, self.directory_close_btn):
            action = self.connection_menu.addAction("")
            action.triggered.connect(button.click)
            self._connection_menu_actions.append((action, button))
        self.connection_menu.addSeparator()
        self.connection_menu.addAction(self.workspace_save_action)
        self.connection_menu.addAction(self.workspace_restore_action)
        self.catalog_pane.setMinimumWidth(150)

        self.workspace_tabs = QTabWidget(self._body_splitter)
        self.workspace_tabs.setObjectName("data-center-workspace-tabs")
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.tabCloseRequested.connect(self._close_workspace_tab)
        self.workspace_tabs.currentChanged.connect(self._on_workspace_tab_changed)
        self.workspace_tabs.setMovable(True)
        self.workspace_tabs.setDocumentMode(True)
        self.workspace_tabs.addTab(self._make_summary_page(), "连接概览" if self.language == "zh" else "Connections")
        self.workspace_tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        corner = QWidget(self.workspace_tabs)
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 2, 6, 2)
        corner_row.setSpacing(2)
        for button in (self.new_query_btn, self.agent_btn, self.catalog_toggle_btn):
            button.setParent(corner)
            apply_button(button, "ghost", compact=True)
            button.setProperty("cornerAction", True)
            button.setFixedHeight(24)
            corner_row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.workspace_tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._workspace_corner = corner
        self._body_splitter.addWidget(self.catalog_pane)
        self._body_splitter.addWidget(self.workspace_tabs)
        self._body_splitter.setStretchFactor(0, 0)
        self._body_splitter.setStretchFactor(1, 1)
        self._body_splitter.setSizes([260, 900])
        root.addWidget(self._body_splitter, 1)
        self._apply_language()
        self._sync_actions()

    @staticmethod
    def _button(primary: bool = False) -> QPushButton:
        button = QPushButton()
        apply_button(button, "primary" if primary else "secondary", compact=True)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return button

    def _make_summary_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("data-center-summary-page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        self.summary_title = QLabel()
        self.summary_title.setObjectName("section-title")
        layout.addWidget(self.summary_title)
        self.summary_text = QLabel()
        self.summary_text.setObjectName("page-context")
        self.summary_text.setWordWrap(True)
        self.summary_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_text)
        self.summary_hint = QLabel()
        self.summary_hint.setObjectName("field-hint")
        self.summary_hint.setWordWrap(True)
        layout.addWidget(self.summary_hint)
        self.summary_create_btn = QPushButton()
        apply_button(self.summary_create_btn, "secondary", compact=True)  # 工具条已有主操作，概览页不再抢第二个主按钮
        self.summary_create_btn.clicked.connect(self._new_connection)
        layout.addWidget(self.summary_create_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _default_host_factory(self, profile: dict, language: str) -> QWidget:
        connection_id = str(profile.get("id") or profile.get("connection_id") or "")
        dialect = str(profile.get("dialect") or "").strip().lower()
        if not connection_id:
            raise ValueError("Connection ID is required")
        if dialect in {"oracle", "mysql", "oceanbase", "dameng"}:
            from panels.ai_workbench_panel import AiWorkbenchPanel

            fixed_panel_type = type("_FixedAiWorkbenchPanel", (_FixedSqlWorkbench, AiWorkbenchPanel), {})
            host = fixed_panel_type(language, connection_id=connection_id, dialect=dialect)
            compact_mode = getattr(host, "set_embedded_compact_mode", None)
            if callable(compact_mode):
                compact_mode(True)
            return host
        if dialect == "redis":
            from panels.db_redis_panel import RedisWorkbenchPanel

            fixed_panel_type = type("_FixedDataCenterRedisPanel", (_DataCenterRedisPanel, RedisWorkbenchPanel), {})
            return fixed_panel_type(language, connection_id=connection_id)
        if dialect == "mongodb":
            from panels.db_mongodb_panel import MongoDBWorkbenchPanel

            fixed_panel_type = type("_FixedDataCenterMongoPanel", (_DataCenterMongoPanel, MongoDBWorkbenchPanel), {})
            return fixed_panel_type(language, connection_id=connection_id)
        raise ValueError(f"Unsupported database engine: {dialect or 'unknown'}")

    def refresh_catalog(self, *_args) -> None:
        selected = self.tree.selected_connection_id() if hasattr(self, "tree") else ""
        self.catalog.refresh()
        self._rebuild_tree()
        self._retire_missing_tree_owners()
        self._show_catalog_diagnostics()
        if selected and not self.tree.select_connection(selected):
            self._on_connection_selected("")
        elif not selected and self.catalog.summaries:
            self._on_connection_selected("")
        self._sync_actions()

    def _rebuild_tree(self) -> None:
        self.tree.set_connections(
            self.catalog.summaries,
            ambiguous_ids=self.catalog.ambiguous_ids,
            record_errors=self.catalog.record_errors,
            open_connection_ids=set(self._workspaces),
        )

    def _retire_missing_tree_owners(self) -> None:
        valid_ids = {
            summary.connection_id
            for summary in self.catalog.summaries
            if summary.connection_id not in set(self.catalog.ambiguous_ids)
        }
        for connection_id in tuple(self._tree_owners):
            if connection_id not in valid_ids:
                self._begin_tree_owner_close(connection_id)
                self._schedule_tree_owner_poll(connection_id)

    def _tree_owner(self, connection_id: str, *, create: bool = False) -> ObjectTreeWorkspace | None:
        wanted = str(connection_id or "")
        owner = self._tree_owners.get(wanted)
        if owner is not None or not create:
            return owner
        if self._tree_shutdown_started or self._manual_shutdown_gated:
            return None
        if len(self._tree_owners) >= MAX_METADATA_HOSTS:
            self.tree.set_connection_status(
                wanted,
                "已打开 16 个连接目录，请先关闭一个。"
                if self.language == "zh" else
                "16 connection directories are open. Close one before opening another.",
                state="error",
            )
            return None
        try:
            owner = ObjectTreeWorkspace(
                wanted,
                composition_factory=self._object_tree_composition_factory,
                parent=self,
            )
        except Exception:
            self.tree.set_connection_status(
                wanted,
                "目录工作区无法启动。" if self.language == "zh" else "The directory workspace could not start.",
                state="error",
            )
            return None
        owner.root_ready.connect(self._on_tree_root_ready)
        owner.page_ready.connect(self._on_tree_page_ready)
        owner.details_ready.connect(self._on_tree_details_ready)
        owner.request_failed.connect(self._on_tree_request_failed)
        self._tree_owners[wanted] = owner
        return owner

    def _on_tree_connection_expanded(self, connection_id: str) -> None:
        if self._manual_shutdown_gated or self._tree_shutdown_started:
            return
        summary = self.catalog.get_summary(connection_id)
        if summary is None or connection_id in set(self.catalog.ambiguous_ids):
            return
        owner = self._tree_owner(connection_id, create=True)
        if owner is None or owner.is_closing or owner.capture_pending:
            return
        if owner.target is None:
            self._start_tree_capture(connection_id, owner)
            return
        root = owner.root_node
        if root is not None:
            connection_item = self.tree.connection_source_item(connection_id)
            if connection_item is not None and not bool(connection_item.data(TREE_LOADED_ROLE)):
                self.tree.set_directory_loading(connection_id, owner.target, root, owner.generation, True)
                owner.load_children(owner.target, root, generation=owner.generation)

    def _start_tree_capture(
        self,
        connection_id: str,
        owner: ObjectTreeWorkspace,
        *,
        refresh: bool = False,
    ) -> None:
        if owner.is_closing or owner.capture_pending or self._tree_shutdown_started:
            return
        summary = self.catalog.get_summary(connection_id)
        if summary is None:
            return
        prefix: str | None = None
        if str(summary.dialect or "").strip().casefold() == "redis" and not refresh:
            accepted, prefix = self._choose_redis_directory_prefix()
            if not accepted:
                return
            if prefix is None:
                self.tree.set_connection_status(
                    connection_id,
                    "请输入键前缀，或明确选择扫描整个键空间。" if self.language == "zh" else
                    "Enter a key prefix or explicitly choose to scan the whole keyspace.",
                    state="notice",
                )
                return
        self._tree_capture_refresh[connection_id] = bool(refresh)
        self.tree.set_connection_status(
            connection_id,
            "正在读取连接快照…" if self.language == "zh" else "Reading connection snapshot…",
            state="loading",
        )
        if refresh and owner.target is not None:
            request_id = owner.refresh_target()
        else:
            # The catalog's displayed database is deliberately not used as a scope.
            request_id = owner.capture_target(database_scope="", redis_prefix=prefix)
        if not request_id:
            self.tree.set_connection_status(
                connection_id,
                "无法读取连接目录。" if self.language == "zh" else "The connection directory could not be loaded.",
                state="error",
            )

    def _choose_redis_directory_prefix(self) -> tuple[bool, str | None]:
        dialog = QDialog(self)
        dialog.setWindowTitle("Redis 目录范围" if self.language == "zh" else "Redis directory scope")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 12)
        label = QLabel(
            "指定要浏览的键前缀。空前缀会扫描整个键空间，仅在明确勾选后可用。"
            if self.language == "zh" else
            "Enter the key prefix to browse. An empty prefix scans the whole keyspace and is available only after explicit selection.",
            dialog,
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        prefix_edit = QLineEdit(dialog)
        prefix_edit.setPlaceholderText("例如 tenant/" if self.language == "zh" else "For example: tenant/")
        size_line(prefix_edit, "path")
        layout.addWidget(prefix_edit)
        all_keys = QCheckBox(
            "明确扫描整个键空间" if self.language == "zh" else "Explicitly scan the whole keyspace",
            dialog,
        )
        layout.addWidget(all_keys)
        all_keys.toggled.connect(lambda checked: prefix_edit.setEnabled(not checked))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False, None
        if all_keys.isChecked():
            return True, ""
        value = prefix_edit.text()
        return True, value if value else None

    def _on_tree_root_ready(self, connection_id: str, target, root: TreeNode, generation: int) -> None:
        owner = self._tree_owners.get(connection_id)
        if owner is None or owner.is_closing or self._tree_shutdown_started:
            return
        if not self.tree.set_directory_root(connection_id, target, root, generation):
            return
        self._completion_catalogs.setdefault(connection_id, CompletionCatalog(allow_unseeded=False)).reset(target, generation)
        self._publish_completion_catalog(connection_id)
        refresh = self._tree_capture_refresh.pop(connection_id, False)
        self.tree.set_directory_loading(connection_id, target, root, generation, True)
        owner.load_children(target, root, refresh=refresh, generation=generation)

    def _on_tree_page_ready(self, connection_id: str, page, request_cursor: str | None, _generation: int) -> None:
        if not self.tree.set_directory_page(connection_id, page, request_cursor):
            return
        owner = self._tree_owners.get(connection_id)
        catalog = self._completion_catalogs.get(connection_id)
        if owner is None or owner.is_closing or catalog is None:
            return
        refs = []
        for node in page.nodes:
            if node.kind not in {"table", "view"}:
                continue
            try:
                refs.append(owner.table_source_service.resolve_table_source(page.target, node, generation=page.generation))
            except (ObjectTreeError, TypeError, ValueError):
                continue
        if catalog.accept_page(page, refs, request_cursor):
            self._publish_completion_catalog(connection_id)

    def _publish_completion_catalog(self, connection_id: str) -> None:
        workspace = self._workspaces.get(connection_id)
        host = getattr(workspace, "host", None)
        manual = getattr(host, "manual_query_host", None)
        setter = getattr(manual, "set_completion_catalog", None)
        if callable(setter):
            setter(self._completion_catalogs.setdefault(connection_id, CompletionCatalog(allow_unseeded=False)))

    def _on_tree_details_ready(self, connection_id: str, details, _target, _generation: int) -> None:
        self.tree.set_directory_status(connection_id, details.target, details.node_ref, "")
        self.tree.set_directory_details(connection_id, details)

    def _tree_failure_text(self, failure) -> str:
        messages = {
            "PROFILE_UNAVAILABLE": ("无法读取此连接的安全快照。", "The connection snapshot could not be read."),
            "PROFILE_MISMATCH": ("连接配置已变化；请显式刷新目录。", "The connection profile changed; refresh the directory explicitly."),
            "CONNECT_FAILED": ("目录连接失败；保留当前可见结果。", "The directory connection failed; current results were kept."),
            "SCOPE_DENIED": ("此目录范围不受支持；当前结果已保留。", "This directory scope is unsupported; current results were kept."),
            "CAPABILITY_UNAVAILABLE": ("此连接暂不支持目录浏览。", "Directory browsing is unavailable for this connection."),
            "CANCELLED": ("目录读取已取消。", "Directory loading was cancelled."),
            "TIMEOUT": ("目录读取超时；保留当前结果。", "Directory loading timed out; current results were kept."),
        }
        code = str(getattr(failure, "code", "") or "")
        pair = messages.get(code, ("目录读取失败；保留当前结果。", "Directory loading failed; current results were kept."))
        return pair[0 if self.language == "zh" else 1]

    def _on_tree_request_failed(self, connection_id: str, failure) -> None:
        owner = self._tree_owners.get(connection_id)
        target = getattr(owner, "target", None) if owner is not None else None
        parent_ref = str(getattr(failure, "parent_ref", "") or "")
        message = self._tree_failure_text(failure)
        self.tree.set_directory_status(connection_id, target, parent_ref, message)
        if not parent_ref:
            self._tree_capture_refresh.pop(connection_id, None)

    def _on_tree_node_expanded(self, connection_id: str, target, node: TreeNode, generation: int) -> None:
        owner = self._tree_owners.get(connection_id)
        if owner is None or owner.is_closing or self._tree_shutdown_started or self._manual_shutdown_gated:
            return
        if target != owner.target:
            return
        self.tree.set_directory_loading(connection_id, target, node, generation, True)
        owner.load_children(target, node, generation=generation)

    def _on_tree_more_requested(
        self, connection_id: str, target, parent: TreeNode, cursor: str, generation: int
    ) -> None:
        owner = self._tree_owners.get(connection_id)
        if owner is None or owner.is_closing or self._tree_shutdown_started or self._manual_shutdown_gated:
            return
        if target != owner.target:
            return
        self.tree.set_directory_loading(connection_id, target, parent, generation, True)
        owner.load_children(target, parent, cursor=cursor, generation=generation)

    def _on_tree_node_activated(
        self, connection_id: str, target, node: TreeNode, generation: int
    ) -> None:
        owner = self._tree_owners.get(connection_id)
        if owner is None or owner.is_closing or target != owner.target:
            return
        if node.kind in {"table", "view"}:
            if target.provider not in {"mysql", "oracle", "oceanbase_mysql", "oceanbase_oracle"}:
                self.tree.set_directory_status(
                    connection_id,
                    target,
                    node.node_ref,
                    "此连接暂不支持表数据工作区。" if self.language == "zh" else
                    "A table data workspace is unavailable for this provider.",
                )
                return
            workspace = self._open_connection(connection_id)
            if workspace is None:
                return
            manual_host = getattr(workspace.host, "manual_query_host", None)
            open_table = getattr(manual_host, "open_table_source", None)
            if not callable(open_table):
                self.tree.set_directory_status(
                    connection_id,
                    target,
                    node.node_ref,
                    "固定 SQL 工作区不可用；目录保持不变。" if self.language == "zh" else
                    "The fixed SQL workbench is unavailable; the directory is unchanged.",
                )
                return
            service = getattr(owner, "table_source_service", None)
            try:
                opened = bool(open_table(service, target, node, generation))
            except Exception:
                opened = False
            if not opened:
                self.tree.set_directory_status(
                    connection_id,
                    target,
                    node.node_ref,
                    "无法打开此表的固定工作区；请检查连接状态或刷新目录。" if self.language == "zh" else
                    "Could not open this table in the fixed workbench; check the connection or refresh the directory.",
                )
            else:
                self.tree.set_directory_status(connection_id, target, node.node_ref, "")
            return
        if node.kind != "redis_key":
            self.tree.set_directory_status(
                connection_id,
                target,
                node.node_ref,
                "此处可浏览对象目录；不读取表数据，也不修改对象。"
                if self.language == "zh" else
                "Browse the object directory here. Table data is not read, and objects are not modified.",
            )
            return
        self.tree.set_directory_loading(connection_id, target, node, generation, True)
        owner.inspect_node(target, node, generation=generation)

    def refresh_selected_directory(self) -> None:
        connection_id = self.tree.selected_connection_id()
        if not connection_id or self._manual_shutdown_gated or self._tree_shutdown_started:
            return
        owner = self._tree_owner(connection_id, create=True)
        if owner is None or owner.is_closing or owner.capture_pending:
            return
        if owner.target is None:
            self._start_tree_capture(connection_id, owner)
        else:
            self._start_tree_capture(connection_id, owner, refresh=True)

    def close_selected_directory(self) -> None:
        connection_id = self.tree.selected_connection_id()
        if not connection_id:
            return
        self._begin_tree_owner_close(connection_id)
        self._schedule_tree_owner_poll(connection_id)

    def _begin_tree_owner_close(self, connection_id: str):
        owner = self._tree_owners.get(str(connection_id or ""))
        if owner is None:
            return None
        self.tree.set_directory_closing(connection_id, True)
        future = owner.close()
        self._tree_owner_close_futures[connection_id] = future
        self._sync_actions()
        return future

    def _schedule_tree_owner_poll(self, connection_id: str) -> None:
        future = self._tree_owner_close_futures.get(connection_id)
        if future is None:
            return
        if connection_id in getattr(self, "_tree_owner_poll_scheduled", set()):
            return
        if not hasattr(self, "_tree_owner_poll_scheduled"):
            self._tree_owner_poll_scheduled = set()
        self._tree_owner_poll_scheduled.add(connection_id)
        QTimer.singleShot(100, lambda cid=connection_id: self._poll_tree_owner_close(cid))

    def _poll_tree_owner_close(self, connection_id: str) -> None:
        scheduled = getattr(self, "_tree_owner_poll_scheduled", set())
        scheduled.discard(connection_id)
        future = self._tree_owner_close_futures.get(connection_id)
        if future is None:
            return
        if not future.done():
            self._schedule_tree_owner_poll(connection_id)
            return
        try:
            result = future.result()
            completed = bool(getattr(result, "completed", False))
        except Exception:
            completed = False
        if completed:
            self._release_tree_owner(connection_id)
        else:
            self._show_tree_owner_cleanup_error(connection_id)

    def _release_tree_owner(self, connection_id: str) -> None:
        owner = self._tree_owners.get(connection_id)
        self._tree_owner_close_futures.pop(connection_id, None)
        getattr(self, "_tree_owner_poll_scheduled", set()).discard(connection_id)
        if owner is None:
            return
        self._tree_owners.pop(connection_id, None)
        catalog = self._completion_catalogs.pop(connection_id, None)
        if catalog is not None:
            catalog.reset()
            self._publish_completion_catalog(connection_id)
        self.tree.clear_directory(connection_id)
        owner.deleteLater()
        self._sync_actions()

    def _show_tree_owner_cleanup_error(self, connection_id: str) -> None:
        self.tree.set_connection_status(
            connection_id,
            "目录清理尚未完成，该连接保持打开。"
            if self.language == "zh" else
            "Directory cleanup is not complete. This connection remains open.",
            state="error",
        )

    def _show_catalog_diagnostics(self) -> None:
        messages = []
        if self.catalog.last_error:
            messages.append(self.catalog.last_error)
        if self.catalog.ambiguous_ids:
            messages.append(
                (f"检测到重复连接 ID：{len(self.catalog.ambiguous_ids)} 项已禁用打开。" if self.language == "zh" else
                 f"Duplicate connection IDs found: {len(self.catalog.ambiguous_ids)} item(s) cannot be opened.")
            )
        if self.catalog.record_errors:
            messages.append(
                (f"有 {len(self.catalog.record_errors)} 条连接记录无法完整显示；原记录未改写。" if self.language == "zh" else
                 f"{len(self.catalog.record_errors)} connection record(s) could not be fully displayed; source records were left unchanged.")
            )
        if self._workspace_close_status:
            messages.append(self._workspace_close_status)
        self.catalog_warning.setText(" ".join(messages))
        self.catalog_warning.setVisible(bool(messages))

    def _show_catalog_operation_error(self, action: str) -> None:
        zh = self.language == "zh"
        messages = {
            "read": (
                "连接目录不可用；本次操作未保存，请先重新加载。",
                "The connection catalog is unavailable; this operation was not saved. Reload it first.",
            ),
            "save": (
                "连接配置保存状态未确认；请重新加载后再操作。",
                "Could not confirm the connection save; reload before retrying.",
            ),
            "delete": (
                "连接删除状态未确认；请重新加载后再操作。",
                "Could not confirm the connection deletion; reload before retrying.",
            ),
        }
        self.catalog_warning.setText(messages[action][0 if zh else 1])
        self.catalog_warning.setVisible(True)

    def tree_filter_changed(self, text: str) -> None:
        self.tree.set_filter_text(text)

    def _toggle_catalog(self, visible: bool) -> None:
        self._catalog_collapsed = not bool(visible)
        self.catalog_pane.setVisible(bool(visible))
        if visible:
            self._body_splitter.setSizes([260, max(520, self.width() - 280)])
        self._apply_language()

    def _on_connection_selected(self, connection_id: str) -> None:
        self._show_connection_summary(str(connection_id or ""))
        self._sync_actions()

    def _show_connection_summary(self, connection_id: str) -> None:
        summary = self.catalog.get_summary(connection_id) if connection_id else None
        if summary is None:
            count = len(self.catalog.summaries)
            self.summary_title.setText(
                "选择连接以查看摘要" if self.language == "zh" and count else
                "尚未配置连接" if self.language == "zh" else
                "Select a connection for its summary" if count else "No connections configured"
            )
            self.summary_text.setText(
                "单击树节点查看摘要；双击或选择连接后使用“打开工作区”。打开只创建绑定工作区，不会自动连接数据库。"
                if self.language == "zh" else
                "Select a tree item for its summary. Double-click or use Open workspace to create a fixed-target workspace; this does not connect automatically."
            )
            self.summary_hint.setText(
                "连接记录尚未配置。使用“新建连接”添加记录。" if self.language == "zh" and not count else
                "内部连接标签会按连接 ID 固定目标；切换标签不会重定向或结束任务。"
                if self.language == "zh" else
                "No connection records yet. Use New connection to add one." if not count else
                "Each internal workspace is fixed to its connection ID. Switching tabs does not retarget or stop its tasks."
            )
            self.summary_create_btn.setVisible(count == 0)
            self.workspace_tabs.setCurrentIndex(0)
            return
        self.summary_title.setText(summary.name)
        location = " / ".join(summary.group_path) or ("未分组" if self.language == "zh" else "Ungrouped")
        environment_label = self.tree._environment_label(summary.environment_key)
        pieces = [summary.engine_label, environment_label, location]
        if summary.host:
            pieces.append(summary.host)
        if summary.database:
            pieces.append(summary.database)
        self.summary_text.setText(" · ".join(pieces))
        self.summary_hint.setText(
            "已固定连接 ID；工作区打开后，需由你显式执行连接测试、刷新或查询。"
            if self.language == "zh" else
            "The workspace is fixed to this connection ID. Test, refresh, or query only after an explicit action."
        )
        self.summary_create_btn.hide()
        if self.workspace_tabs.currentIndex() == 0:
            self.workspace_tabs.setCurrentIndex(0)

    def _new_connection(self) -> None:
        if self._manual_shutdown_gated:
            return
        if self.catalog.last_error:
            self._show_catalog_operation_error("read")
            return
        dialog = _WorkspaceConnectionDialog(self.language, parent=self)
        if dialog.exec() != ConnectionDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        try:
            saved = self.catalog.save(item, password if password else None)
        except Exception:
            self._show_catalog_operation_error("save")
            return
        if not saved:
            self._show_catalog_operation_error("save")
            return
        self.refresh_catalog()
        if self.catalog.last_error:
            return
        connection_id = str(saved.get("id") or "")
        if connection_id:
            self.tree.select_connection(connection_id)
            self._on_connection_selected(connection_id)

    def _edit_connection(self) -> None:
        if self._manual_shutdown_gated:
            return
        if self.catalog.last_error:
            self._show_catalog_operation_error("read")
            return
        connection_id = self.tree.selected_connection_id()
        if not connection_id or connection_id in self._workspaces:
            return
        profile = self.catalog.get_profile(connection_id)
        if profile is None:
            return
        dialog = _WorkspaceConnectionDialog(self.language, profile, self)
        if dialog.exec() != ConnectionDialog.DialogCode.Accepted:
            return
        item, password = dialog.payload()
        try:
            saved = self.catalog.save(item, password if password else None)
        except Exception:
            self._show_catalog_operation_error("save")
            return
        if not saved:
            self._show_catalog_operation_error("save")
            return
        self.refresh_catalog()
        if self.catalog.last_error:
            return
        self.tree.select_connection(connection_id)
        self._on_connection_selected(connection_id)

    def _delete_connection(self) -> None:
        if self._manual_shutdown_gated:
            return
        if self.catalog.last_error:
            self._show_catalog_operation_error("read")
            return
        connection_id = self.tree.selected_connection_id()
        summary = self.catalog.get_summary(connection_id)
        if summary is None or connection_id in self._workspaces:
            return
        prompt = (
            f"删除连接「{summary.name}」？此操作会删除本地连接记录。"
            if self.language == "zh" else
            f"Delete connection '{summary.name}'? This removes its local connection record."
        )
        if not confirm_action(
            self, "删除连接" if self.language == "zh" else "Delete connection", prompt,
            confirm_text="删除" if self.language == "zh" else "Delete", danger=True,
        ):
            return
        try:
            self.catalog.delete(connection_id)
        except Exception:
            self._show_catalog_operation_error("delete")
            return
        self.refresh_catalog()

    def _toggle_favorite(self) -> None:
        if self._manual_shutdown_gated:
            return
        if self.catalog.last_error:
            self._show_catalog_operation_error("read")
            return
        connection_id = self.tree.selected_connection_id()
        summary = self.catalog.get_summary(connection_id)
        if summary is None:
            return
        try:
            self.catalog.set_favorite(connection_id, not summary.favorite)
        except Exception:
            self._show_catalog_operation_error("save")
            return
        self.refresh_catalog()
        self.tree.select_connection(connection_id)
        self._on_connection_selected(connection_id)

    def _on_ambiguous_connection(self, connection_id: str) -> None:
        self._show_error(
            (f"连接 ID「{connection_id}」出现重复记录。为避免误连其他目标，此项不可打开；请先在连接管理中整理重复项。"
             if self.language == "zh" else
             f"Connection ID '{connection_id}' is duplicated. Opening is disabled to prevent selecting the wrong target; resolve the duplicate records first.")
        )

    def open_selected_connection(self) -> QWidget | None:
        return self._open_connection(self.tree.selected_connection_id())

    def _open_connection(self, connection_id: str):
        wanted = str(connection_id or "")
        if self._manual_shutdown_gated and wanted not in self._workspaces:
            return None
        summary = self.catalog.get_summary(wanted)
        profile = self.catalog.get_profile(wanted)
        if summary is None or profile is None:
            self._show_error("连接记录不存在" if self.language == "zh" else "Connection record was not found")
            return None
        existing = self._workspaces.get(wanted)
        if existing is not None:
            self.workspace_tabs.setCurrentWidget(existing)
            return existing
        try:
            host = self._host_factory(dict(profile), self.language)
            if not isinstance(host, QWidget):
                raise TypeError("host_factory must return a QWidget")
        except Exception as exc:
            self._show_error(
                (f"无法打开工作区：{exc}" if self.language == "zh" else f"Could not open workspace: {exc}")
            )
            return None
        workspace = _ConnectionWorkspace(wanted, profile, host, self.workspace_tabs)
        workspace.setObjectName(f"data-center-workspace-{wanted}")
        title = f"{summary.name} · {summary.engine_label}"
        index = self.workspace_tabs.addTab(workspace, title)
        self.workspace_tabs.setTabToolTip(index, f"{summary.name} · {summary.engine_label} · {wanted}")
        self._workspaces[wanted] = workspace
        self._publish_completion_catalog(wanted)
        self.workspace_tabs.setCurrentIndex(index)
        self._rebuild_tree()
        self.tree.select_connection(wanted)
        self.workspace_opened.emit(wanted)
        self._sync_summary_tab()
        self._apply_host_layout(workspace.host)
        workspace.apply_layout_mode(self._layout_mode, self._low_height)
        self._sync_actions()
        return workspace

    def _sync_summary_tab(self) -> None:
        """「连接概览」只作为没有打开任何连接时的欢迎页；有工作区时隐藏其页签。"""
        has_workspace = bool(self._workspaces)
        self.workspace_tabs.setTabVisible(0, not has_workspace)
        if not has_workspace:
            self.workspace_tabs.setCurrentIndex(0)

    def _new_query(self) -> None:
        if self._manual_shutdown_gated:
            return
        host = self._active_host()
        create = getattr(host, "_new_sql_tab", None)
        if callable(create):
            create()

    def _toggle_agent(self, checked: bool) -> None:
        workspace = self._active_workspace()
        if workspace is None or workspace.agent_widget is None:
            return
        workspace.set_agent_visible(checked)
        self._sync_actions()

    def _active_workspace(self) -> _ConnectionWorkspace | None:
        widget = self.workspace_tabs.currentWidget()
        return widget if isinstance(widget, _ConnectionWorkspace) else None

    def _active_host(self) -> QWidget | None:
        workspace = self._active_workspace()
        return workspace.host if workspace is not None else None

    def active_connection_id(self) -> str:
        workspace = self._active_workspace()
        return workspace.connection_id if workspace is not None else ""

    def active_connection_profile(self) -> dict | None:
        connection_id = self.active_connection_id()
        workspace = self._workspaces.get(connection_id)
        return dict(workspace.profile) if workspace is not None else None

    def focus_legacy_nav(self, nav_index: int) -> bool:
        dialect = dialect_for_nav(int(nav_index))
        if not dialect:
            return False
        selected = self.tree.select_first_engine(dialect)
        if not selected:
            label = next((item[0] for item in FIXED_DB_PAGES if item[1] == dialect), dialect)
            self.summary_title.setText(
                f"{label} · 尚无连接" if self.language == "zh" else f"{label} · no connection"
            )
            self.summary_text.setText(
                "请先新建或导入此引擎的连接；此兼容入口只定位类型，不会自动连接。"
                if self.language == "zh" else
                "Create or import a connection for this engine. This compatibility route only selects the type and never connects automatically."
            )
            self.workspace_tabs.setCurrentIndex(0)
        return selected

    def _on_workspace_tab_changed(self, index: int) -> None:
        workspace = self.workspace_tabs.widget(index) if index >= 0 else None
        if isinstance(workspace, _ConnectionWorkspace):
            self.tree.select_connection(workspace.connection_id)
            self._apply_host_layout(workspace.host)
            workspace.apply_layout_mode(self._layout_mode, self._low_height)
        self._sync_actions()

    def _close_workspace_tab(self, index: int) -> None:
        workspace = self.workspace_tabs.widget(index)
        if not isinstance(workspace, _ConnectionWorkspace):
            return
        if self._manual_shutdown_gated:
            return
        connection_id = workspace.connection_id
        if connection_id in self._pending_close_ids:
            return
        self._pending_close_ids.add(connection_id)
        self._workspace_close_states[connection_id] = {"stage": "preflight"}
        self._advance_workspace_close(connection_id)

    def _poll_workspace_close(self, connection_id: str) -> None:
        self._advance_workspace_close(connection_id)

    @staticmethod
    def _manual_status_value(status) -> str:
        value = getattr(status, "value", status)
        return str(value or "").strip().casefold()

    @staticmethod
    def _manual_snapshot_signature(snapshot) -> tuple:
        sessions = []
        for session in tuple(getattr(snapshot, "sessions", ()) or ()):
            state = getattr(session, "state", "")
            state = getattr(state, "value", state)
            sessions.append((
                str(getattr(session, "tab_id", "")),
                int(getattr(session, "generation", 0) or 0),
                str(state or "").casefold(),
                bool(getattr(session, "busy", False)),
                bool(getattr(session, "cleanup_complete", False)),
                int(getattr(session, "pending_local_changes", 0) or 0),
            ))
        return tuple(sorted(sessions)), bool(getattr(snapshot, "can_commit", False))

    @staticmethod
    def _manual_has_unknown(snapshot) -> bool:
        return any(
            str(getattr(getattr(session, "state", ""), "value", getattr(session, "state", ""))).casefold() == "unknown"
            and not bool(getattr(session, "cleanup_complete", False))
            for session in tuple(getattr(snapshot, "sessions", ()) or ())
        )

    @staticmethod
    def _manual_has_unknown_import(snapshot) -> bool:
        return any(
            str(getattr(getattr(session, "state", ""), "value", getattr(session, "state", ""))).casefold() == "unknown"
            and str(getattr(session, "owner_kind", "manual") or "manual").casefold() == "import"
            and not bool(getattr(session, "cleanup_complete", False))
            for session in tuple(getattr(snapshot, "sessions", ()) or ())
        )

    @staticmethod
    def _manual_has_unknown_manual(snapshot) -> bool:
        return any(
            str(getattr(getattr(session, "state", ""), "value", getattr(session, "state", ""))).casefold() == "unknown"
            and str(getattr(session, "owner_kind", "manual") or "manual").casefold() != "import"
            and not bool(getattr(session, "cleanup_complete", False))
            for session in tuple(getattr(snapshot, "sessions", ()) or ())
        )

    def _workspace_manual_snapshot(self, host):
        snapshot_method = getattr(host, "manual_shutdown_snapshot", None)
        if callable(snapshot_method):
            return snapshot_method()
        from ui.data_center.manual_query import ManualShutdownSnapshot
        return ManualShutdownSnapshot((), not callable(getattr(host, "manual_shutdown_cleanup", None)))

    def _workspace_manual_preflight(self, host):
        cleanup = getattr(host, "manual_shutdown_cleanup", None)
        if not callable(cleanup):
            from ui.data_center.manual_query import ManualShutdownStatus
            return ManualShutdownStatus.READY
        return cleanup(None)

    def _choose_workspace_manual_disposition(self, snapshot):
        from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout
        from ui.design_system import apply_button

        zh = self.language == "zh"
        dirty = sum(
            1 for session in tuple(getattr(snapshot, "sessions", ()) or ())
            if str(getattr(getattr(session, "state", ""), "value", getattr(session, "state", ""))).casefold() == "dirty"
            and not bool(getattr(session, "cleanup_complete", False))
        )
        pending_local = sum(
            max(0, int(getattr(session, "pending_local_changes", 0) or 0))
            for session in tuple(getattr(snapshot, "sessions", ()) or ())
        )
        unknown_import = self._manual_has_unknown_import(snapshot)
        unknown_manual = self._manual_has_unknown_manual(snapshot)
        unknown = unknown_import or unknown_manual
        if unknown_import:
            if zh:
                parts = []
                if dirty:
                    parts.append(f"{dirty}个数据库事务尚未提交。")
                if pending_local:
                    parts.append(
                        f"{pending_local}项本地表格编辑尚未写入数据库；关闭不会自动应用，会放弃编辑，保留工作区会保留编辑。"
                    )
                if unknown_manual:
                    parts.append("手动事务结果未知：请求回滚不能证明写入已撤销。")
                parts.append("导入结果未知：确认并释放；不会发送新的数据库回滚，也不能证明写入已撤销。")
                message = "".join(parts)
            else:
                parts = []
                if dirty:
                    parts.append(f"{dirty} uncommitted database transaction(s).")
                if pending_local:
                    parts.append(
                        f"{pending_local} local table edit(s) are unsaved; closing will not auto-apply them and will discard them, while Keep preserves them."
                    )
                if unknown_manual:
                    parts.append("Manual transaction outcome UNKNOWN: rollback cannot prove the write was undone.")
                parts.append(
                    "Import outcome UNKNOWN: acknowledge and release only; no new database rollback is sent, and this cannot prove the write was undone."
                )
                message = " ".join(parts)
        elif zh:
            parts = []
            if dirty:
                parts.append(f"{dirty} 个未提交的数据库事务")
            if pending_local:
                parts.append(f"{pending_local} 项尚未写入数据库的本地表格编辑")
            if parts:
                description = "和".join(parts)
            elif unknown_import and unknown_manual:
                description = "导入和手动会话需要处理"
            elif unknown_import:
                description = "导入会话需要处理"
            else:
                description = "手动会话需要处理"
            message = "此工作区有" + description + "。"
            if pending_local:
                message += "关闭不会自动应用这些编辑。"
            if unknown_manual:
                message += "事务结果未知：回滚请求无法证明未知写入已经撤销。"
            if pending_local and (dirty or unknown):
                message += "“回滚并丢弃本地”请求回滚、放弃本地编辑并关闭；“保留工作区”保留编辑。"
            elif pending_local:
                message += "“丢弃本地并关闭”放弃编辑；“保留工作区”保留编辑。"
            elif dirty or unknown:
                message += "“提交”提交事务并关闭；“回滚”请求回滚并关闭；“保留工作区”保持打开。"
            else:
                message += "“保留工作区”保持打开。"
        else:
            parts = []
            if dirty:
                parts.append(f"{dirty} uncommitted database transaction(s)")
            if pending_local:
                parts.append(f"{pending_local} local table edit(s) not written to the database")
            if parts:
                description = " and ".join(parts)
            elif unknown_import and unknown_manual:
                description = "import and manual sessions that need a decision"
            elif unknown_import:
                description = "an import session that needs a decision"
            else:
                description = "manual sessions that need a decision"
            message = "This workspace has " + description + "."
            if pending_local:
                message += " Closing does not apply these edits."
            if unknown_manual:
                message += " The transaction outcome is UNKNOWN; requesting rollback cannot prove an unknown write was undone."
            if pending_local and (dirty or unknown):
                message += " “Rollback + discard” requests rollback, discards the local edits, and closes; “Keep workspace” preserves them."
            elif pending_local:
                message += " “Discard local + close” discards the edits; “Keep workspace” preserves them."
            elif dirty or unknown:
                message += " “Commit” submits the transaction and closes; “Rollback” requests rollback and closes; “Keep workspace” leaves the workspace open."
            else:
                message += " “Keep workspace” leaves the workspace open."

        if unknown_import:
            if unknown_manual and pending_local:
                rollback_text = "回滚／确认／丢弃"
                rollback_text_en = "Rollback / ack / discard"
            elif unknown_manual:
                rollback_text = "回滚／确认"
                rollback_text_en = "Rollback / ack"
            elif dirty and pending_local:
                rollback_text = "回滚／确认／丢弃"
                rollback_text_en = "Rollback / ack / discard"
            elif dirty:
                rollback_text = "回滚／确认"
                rollback_text_en = "Rollback / ack"
            elif pending_local:
                rollback_text = "确认／丢弃"
                rollback_text_en = "Ack / discard"
            else:
                rollback_text = "确认导入"
                rollback_text_en = "Ack import"
        elif unknown_manual:
            rollback_text = "回滚并丢弃本地" if pending_local else "回滚并关闭"
            rollback_text_en = "Rollback + discard" if pending_local else "Rollback + close"
        elif pending_local and dirty:
            rollback_text = "回滚并丢弃本地"
            rollback_text_en = "Rollback + discard"
        elif pending_local:
            rollback_text = "丢弃本地并关闭"
            rollback_text_en = "Discard local + close"
        else:
            rollback_text = "回滚并关闭"
            rollback_text_en = "Rollback + close"

        dialog = QDialog(self)
        dialog.setObjectName("workspace-manual-disposition-dialog")
        dialog.setWindowTitle("关闭工作区" if zh else "Close workspace")
        dialog.setMinimumWidth(400)
        dialog.setMaximumWidth(460)
        dialog.selected_action = None

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        message_label = QLabel(message, dialog)
        message_label.setObjectName("workspace-manual-disposition-message")
        message_label.setWordWrap(True)
        message_label.setMinimumWidth(0)
        message_label.setMaximumWidth(420)
        message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(message_label)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        commit = QPushButton("提交并关闭" if zh else "Commit + close", dialog)
        commit.setAccessibleName("提交并关闭" if zh else "Commit database transaction and close workspace")
        commit.setToolTip(
            "提交数据库事务并关闭工作区；有本地未应用编辑或事务结果未知时不可提交。"
            if zh else
            "Commit the database transaction and close the workspace. Disabled while local edits are unapplied or a transaction outcome is unknown."
        )
        apply_button(commit, "primary", compact=True)
        commit.setProperty("workspaceDispositionAction", "commit")
        commit.setEnabled(bool(getattr(snapshot, "can_commit", False)))

        rollback = QPushButton(rollback_text if zh else rollback_text_en, dialog)
        if unknown_import:
            if pending_local and (dirty or unknown_manual):
                rollback_accessible_name = "请求回滚其他事务、确认并释放未知导入、放弃本地编辑并关闭" if zh else "Request rollback of other transactions, acknowledge and release the UNKNOWN import, discard local edits, and close"
            elif pending_local:
                rollback_accessible_name = "确认并释放未知导入、放弃本地编辑并关闭" if zh else "Acknowledge and release the UNKNOWN import, discard local edits, and close"
            elif dirty or unknown_manual:
                rollback_accessible_name = "请求回滚其他事务、确认并释放未知导入后关闭" if zh else "Request rollback of other transactions, acknowledge and release the UNKNOWN import, and close"
            else:
                rollback_accessible_name = "确认并释放未知导入后关闭" if zh else "Acknowledge and release the UNKNOWN import, then close"
        elif pending_local and (dirty or unknown):
            rollback_accessible_name = "回滚并放弃本地编辑后关闭" if zh else "Rollback, discard local edits, and close workspace"
        elif pending_local:
            rollback_accessible_name = "放弃本地编辑并关闭" if zh else "Discard local edits and close workspace"
        else:
            rollback_accessible_name = "回滚数据库事务并关闭" if zh else "Rollback database transaction and close workspace"
        rollback.setAccessibleName(rollback_accessible_name)
        if unknown_import:
            if dirty or unknown_manual:
                tooltip = (
                    "对其他事务请求回滚；对结果未知的导入只确认并释放，不会发送新的数据库回滚，也不能证明未知写入已撤销。"
                    if zh else
                    "Request rollback for other transactions. Only acknowledge and release the UNKNOWN import; no new database rollback is sent, and this does not prove an UNKNOWN write was undone."
                )
            else:
                tooltip = (
                    "对结果未知的导入只确认并释放，不会发送新的数据库回滚，也不能证明写入已撤销。"
                    if zh else
                    "Only acknowledge and release the UNKNOWN import; no database rollback is sent, and this does not prove the write was undone."
                )
            if pending_local:
                tooltip += (
                    f" 同时放弃 {pending_local} 项尚未写入数据库的本地表格编辑；不会自动应用。"
                    if zh else
                    f" Also discard {pending_local} local table edit(s) that have not been written to the database; they are not applied automatically."
                )
        else:
            tooltip = (
                f"向数据库请求回滚；同时放弃 {pending_local} 项尚未应用的本地表格编辑并关闭。未知事务结果仍可能无法确认是否已撤销。"
                if zh and pending_local and (dirty or unknown) else
                f"放弃 {pending_local} 项尚未应用的本地表格编辑并关闭；不会写入数据库。"
                if zh and pending_local else
                "向数据库请求回滚并关闭工作区；未知事务结果仍可能无法确认是否已撤销。"
                if zh else
                f"Request a database rollback, discard {pending_local} unapplied local table edit(s), and close. An UNKNOWN outcome may remain unresolved."
                if pending_local and (dirty or unknown) else
                f"Discard {pending_local} unapplied local table edit(s) and close; no database write is sent."
                if pending_local else
                "Request a database rollback and close. An UNKNOWN outcome may remain unresolved."
            )
        rollback.setToolTip(tooltip)
        apply_button(rollback, "danger", compact=True)
        rollback.setProperty("workspaceDispositionAction", "rollback")

        keep = QPushButton("保留工作区" if zh else "Keep workspace", dialog)
        keep.setAccessibleName("保留工作区" if zh else "Keep workspace open")
        keep.setToolTip(
            "保留工作区打开，并保留本地表格编辑。" if zh else
            "Keep the workspace open and preserve local table edits."
        )
        apply_button(keep, "secondary", compact=True)
        keep.setProperty("workspaceDispositionAction", "keep")
        keep.setDefault(True)
        keep.setAutoDefault(True)

        actions.addWidget(commit)
        actions.addWidget(rollback)
        actions.addStretch(1)
        actions.addWidget(keep)
        layout.addLayout(actions)

        def finish(action: str) -> None:
            dialog.selected_action = action
            dialog.accept()

        commit.clicked.connect(lambda: finish("commit"))
        rollback.clicked.connect(lambda: finish("rollback"))
        keep.clicked.connect(dialog.reject)
        layout.activate()
        dialog.adjustSize()
        dialog.exec()
        return dialog.selected_action

    def _workspace_manual_gate(self, host, enabled: bool) -> bool:
        gate = getattr(host, "manual_shutdown_gate", None)
        has_manual_seam = callable(getattr(host, "manual_shutdown_snapshot", None)) or callable(
            getattr(host, "manual_shutdown_cleanup", None)
        )
        if not callable(gate):
            return not (enabled and has_manual_seam)
        return gate(bool(enabled)) is not False

    def _workspace_close_notice(self, message: str) -> None:
        self._workspace_close_status = str(message or "")
        self._show_catalog_diagnostics()

    def _abort_workspace_close(self, connection_id: str, message: str = "") -> None:
        workspace = self._workspaces.get(connection_id)
        state = self._workspace_close_states.pop(connection_id, {})
        if workspace is not None and state.get("gated"):
            try:
                self._workspace_manual_gate(workspace.host, False)
            except Exception:
                pass
        self._pending_close_ids.discard(connection_id)
        if message:
            self._workspace_close_notice(message)

    def _advance_workspace_close(self, connection_id: str) -> None:
        workspace = self._workspaces.get(connection_id)
        state = self._workspace_close_states.get(connection_id)
        if workspace is None or state is None:
            self._pending_close_ids.discard(connection_id)
            self._workspace_close_states.pop(connection_id, None)
            return
        host = workspace.host
        stage = state.get("stage", "preflight")

        if stage == "preflight":
            try:
                snapshot = self._workspace_manual_snapshot(host)
                status = self._workspace_manual_preflight(host)
            except Exception:
                self._abort_workspace_close(
                    connection_id,
                    "无法检查手动会话；工作区仍保持打开。"
                    if self.language == "zh" else
                    "Manual sessions could not be checked; the workspace remains open.",
                )
                return
            value = self._manual_status_value(status)
            if value == "pending":
                self._workspace_close_notice(
                    "正在等待手动会话安全结束；Stop 仍可使用。"
                    if self.language == "zh" else
                    "Waiting for manual sessions to finish safely; Stop remains available."
                )
                QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                return
            if value in {"error", "cancelled"}:
                self._abort_workspace_close(connection_id)
                return
            if value == "ready":
                action = "seal"
            elif value == "ready_to_close":
                action = "close"
            elif value == "decision_required":
                action = self._choose_workspace_manual_disposition(snapshot)
                if action is None:
                    cleanup = getattr(host, "manual_shutdown_cleanup", None)
                    if callable(cleanup):
                        cleanup("cancel")
                    self._abort_workspace_close(connection_id)
                    return
            else:
                self._abort_workspace_close(connection_id)
                return

            state["baseline"] = self._manual_snapshot_signature(snapshot)
            if not self._workspace_manual_gate(host, True):
                self._abort_workspace_close(
                    connection_id,
                    "无法暂停此工作区的新手动任务；工作区仍保持打开。"
                    if self.language == "zh" else
                    "Could not pause new manual work; the workspace remains open.",
                )
                return
            state["gated"] = True
            try:
                fresh = self._workspace_manual_snapshot(host)
                fresh_status = self._workspace_manual_preflight(host)
            except Exception:
                self._abort_workspace_close(connection_id)
                return
            if (
                self._manual_snapshot_signature(fresh) != state["baseline"]
                or self._manual_status_value(fresh_status) != value
                or (action == "commit" and not bool(getattr(fresh, "can_commit", False)))
            ):
                self._abort_workspace_close(
                    connection_id,
                    "手动会话状态已变化；未开始关闭，请重新操作。"
                    if self.language == "zh" else
                    "Manual session state changed; no close action started. Review and retry.",
                )
                return
            state["unknown_import_ack"] = action == "rollback" and self._manual_has_unknown_import(fresh)
            state["unknown_manual_rollback"] = action == "rollback" and self._manual_has_unknown_manual(fresh)
            if action in {"close", "commit", "rollback"}:
                cleanup = getattr(host, "manual_shutdown_cleanup", None)
                if callable(cleanup):
                    try:
                        result = self._manual_status_value(cleanup(action))
                    except Exception:
                        result = "error"
                else:
                    result = "ready"
                if result == "pending":
                    state["stage"] = "manual"
                    self._workspace_close_notice(
                        "正在等待手动会话安全结束；Stop 仍可使用。"
                        if self.language == "zh" else
                        "Waiting for manual sessions to finish safely; Stop remains available."
                    )
                    QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                    return
                if result != "ready":
                    self._abort_workspace_close(
                        connection_id,
                        "事务状态发生变化；工作区仍保持打开。"
                        if self.language == "zh" else
                        "Transaction state changed; the workspace remains open.",
                    )
                    return
                state["stage"] = "tree"
            else:
                state["stage"] = "tree"
            self._advance_workspace_close(connection_id)
            return

        if stage == "manual":
            try:
                status = self._workspace_manual_preflight(host)
            except Exception:
                status = "error"
            value = self._manual_status_value(status)
            if value == "pending":
                self._workspace_close_notice(
                    "正在等待手动会话安全结束；Stop 仍可使用。"
                    if self.language == "zh" else
                    "Waiting for manual sessions to finish safely; Stop remains available."
                )
                QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                return
            if value != "ready":
                self._abort_workspace_close(
                    connection_id,
                    "手动事务关闭未完成；工作区仍保持打开。"
                    if self.language == "zh" else
                    "Manual transaction cleanup did not finish; the workspace remains open.",
                )
                return
            state["stage"] = "tree"
            self._advance_workspace_close(connection_id)
            return

        if stage == "tree":
            owner = self._tree_owners.get(connection_id)
            if owner is None:
                state["stage"] = "workbench"
                self._advance_workspace_close(connection_id)
                return
            future = self._begin_tree_owner_close(connection_id)
            if future is None or not future.done():
                self._workspace_close_notice(
                    "正在等待此连接的目录资源安全关闭…" if self.language == "zh" else
                    "Waiting for this connection's directory resources to close safely…"
                )
                QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                return
            try:
                cleanup_result = future.result()
                tree_complete = bool(getattr(cleanup_result, "completed", False))
            except Exception:
                tree_complete = False
            if not tree_complete:
                self._abort_workspace_close(
                    connection_id,
                    "目录清理尚未完成，该连接保持打开。" if self.language == "zh" else
                    "Directory cleanup is not complete. This connection remains open.",
                )
                self._show_tree_owner_cleanup_error(connection_id)
                return
            self._release_tree_owner(connection_id)
            state["stage"] = "workbench"
            self._advance_workspace_close(connection_id)
            return

        if stage == "workbench":
            cleanup = getattr(host, "workbench_shutdown_cleanup", None)
            try:
                complete = bool(cleanup()) if callable(cleanup) else not self._has_live_manual_worker(host)
            except Exception:
                complete = False
            if not complete:
                self._workspace_close_notice(
                    "正在等待数据库工作台任务安全结束…"
                    if self.language == "zh" else
                    "Waiting for database workbench tasks to finish safely…"
                )
                QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                return
            state["stage"] = "agent"
            self._advance_workspace_close(connection_id)
            return

        if stage == "agent":
            cleanup = getattr(host, "agent_shutdown_cleanup", None)
            try:
                complete = bool(cleanup()) if callable(cleanup) else True
            except Exception:
                complete = False
            if not complete:
                self._workspace_close_notice(
                    "正在安全停止 Agent 任务…"
                    if self.language == "zh" else
                    "Waiting for Agent tasks to stop safely…"
                )
                QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
                return
            unknown_import_ack = bool(state.get("unknown_import_ack"))
            unknown_manual_rollback = bool(state.get("unknown_manual_rollback"))
            self._pending_close_ids.discard(connection_id)
            self._workspace_close_states.pop(connection_id, None)
            self._finish_workspace_close(connection_id)
            self._workspace_close_notice("")
            if unknown_import_ack and unknown_manual_rollback:
                self._show_error(
                    "工作区已关闭；未知导入结果已确认并释放，没有发送新的数据库回滚；其他未知事务结果仍无法证明写入已撤销。"
                    if self.language == "zh" else
                    "The workspace is closed. The UNKNOWN import was acknowledged and released without a new database rollback; other UNKNOWN transaction outcomes still do not prove writes were undone."
                )
            elif unknown_import_ack:
                self._show_error(
                    "工作区已关闭；未知导入结果已确认并释放，没有发送新的数据库回滚，也不能证明写入已撤销。"
                    if self.language == "zh" else
                    "The workspace is closed. The UNKNOWN import was acknowledged and released without a new database rollback; this does not prove the write was undone."
                )
            elif unknown_manual_rollback:
                self._show_error(
                    "工作区已关闭，但此前事务结果仍未知；回滚请求不证明写入已撤销。"
                    if self.language == "zh" else
                    "The workspace is closed, but the earlier transaction outcome remains UNKNOWN; the rollback request does not prove the write was undone."
                )
            return
        self._abort_workspace_close(connection_id)
        workspace = self._workspaces.get(connection_id)
        if workspace is None:
            self._pending_close_ids.discard(connection_id)
            return
        if self._has_live_manual_worker(workspace.host):
            self._pending_close_ids.discard(connection_id)
            return
        cleanup = getattr(workspace.host, "agent_shutdown_cleanup", None)
        if callable(cleanup) and not cleanup():
            QTimer.singleShot(100, lambda cid=connection_id: self._poll_workspace_close(cid))
            return
        self._pending_close_ids.discard(connection_id)
        self._finish_workspace_close(connection_id)

    @staticmethod
    def _has_live_manual_worker(host: QWidget) -> bool:
        for name in ("_worker", "_bg_worker", "_ai_worker"):
            worker = getattr(host, name, None)
            if worker is not None:
                is_running = getattr(worker, "isRunning", None)
                if callable(is_running):
                    try:
                        if is_running():
                            return True
                    except Exception:
                        return True
        return False

    def _finish_workspace_close(self, connection_id: str) -> None:
        workspace = self._workspaces.pop(connection_id, None)
        if workspace is None:
            return
        index = self.workspace_tabs.indexOf(workspace)
        if index >= 0:
            self.workspace_tabs.removeTab(index)
        workspace.deleteLater()
        self._sync_summary_tab()
        if connection_id not in self._tree_owners:
            self._completion_catalogs.pop(connection_id, None)
        self._rebuild_tree()
        self._sync_actions()

    def _apply_host_layout(self, host: QWidget) -> None:
        apply_mode = getattr(host, "apply_layout_mode", None)
        if callable(apply_mode):
            try:
                apply_mode(self._layout_mode, self._low_height, max(0, self.width() - 260))
            except TypeError:
                apply_mode(self._layout_mode, self._low_height)
            except Exception:
                pass

    def apply_layout_mode(self, mode: str, low_height: bool = False) -> None:
        self._layout_mode = str(mode or "wide")
        self._low_height = bool(low_height)
        for workspace in self._workspaces.values():
            self._apply_host_layout(workspace.host)
            workspace.apply_layout_mode(self._layout_mode, self._low_height)
        if self._body_splitter.count() >= 2 and not self._catalog_collapsed:
            sizes = self._body_splitter.sizes()
            total = sum(sizes) or self.width()
            left = 260 if self._layout_mode in {"wide", "standard"} else min(220, max(160, total // 3))
            self._body_splitter.setSizes([left, max(520, total - left)])

    def set_language(self, language: str) -> None:
        self.language = "en" if str(language).lower().startswith("en") else "zh"
        self.tree.set_language(self.language)
        set_header_home_language(self._page_header, self.language)
        self._apply_language()
        for workspace in self._workspaces.values():
            set_language = getattr(workspace.host, "set_language", None)
            if callable(set_language):
                set_language(self.language)
        self.refresh_catalog()

    def _apply_language(self) -> None:
        zh = self.language == "zh"
        self.page_title.setText("数据中心" if zh else "Data Center")
        self.page_subtitle.setText(
            "项目、环境、引擎与连接工作区" if zh else "Projects, environments, engines, and connection workspaces"
        )
        self.catalog_toggle_btn.setText("«" if self.catalog_toggle_btn.isChecked() else "»")
        self.catalog_toggle_btn.setToolTip(
            ("收起连接目录" if zh else "Hide catalog") if self.catalog_toggle_btn.isChecked() else
            ("展开连接目录" if zh else "Show catalog")
        )
        self.search_edit.setPlaceholderText("搜索项目、环境、引擎或连接" if zh else "Search projects, environments, engines, or connections")
        self.new_btn.setText("+")
        self.new_btn.setToolTip("新建连接" if zh else "New connection")
        self.connection_menu_btn.setText("⋯")
        self.connection_menu_btn.setToolTip("连接管理、工作区与目录操作" if zh else "Connections, workspace and catalog actions")
        self.search_edit.setPlaceholderText("搜索连接、库、表…" if zh else "Search connections, schemas, tables…")
        self.edit_btn.setText("编辑" if zh else "Edit")
        self.delete_btn.setText("删除" if zh else "Delete")
        self.favorite_btn.setText("收藏" if zh else "Favorite")
        self.refresh_btn.setText("刷新目录" if zh else "Refresh catalog")
        self.workspace_btn.setText("工作区" if zh else "Workspace")
        self.workspace_save_action.setText("保存当前工作区" if zh else "Save current workspace")
        self.workspace_restore_action.setText("恢复上次工作区" if zh else "Restore saved workspace")
        self.workspace_btn.setToolTip("保存/恢复草稿和标签顺序；不会恢复数据库会话、事务或表格待应用变更。" if zh else "Save/restore drafts and tab order; database sessions, transactions and staged table changes are not restored.")
        self.directory_refresh_btn.setText("刷新对象" if zh else "Refresh objects")
        self.directory_close_btn.setText("关闭目录" if zh else "Close directory")
        self.open_btn.setText("打开工作区" if zh else "Open workspace")
        self.new_query_btn.setText("+ 查询" if zh else "+ Query")
        self.new_query_btn.setToolTip("在当前连接新建查询页签" if zh else "New query tab on this connection")
        self.agent_btn.setText("AI")
        self.agent_btn.setToolTip("打开/收起 AI 侧栏（绑定当前连接）" if zh else "Toggle the AI panel bound to this connection")
        self.workspace_tabs.setTabText(0, "连接概览" if zh else "Connections")
        self.summary_create_btn.setText("新建连接" if zh else "New connection")
        self._show_connection_summary(self.tree.selected_connection_id())
        self._sync_actions()

    def _sync_actions(self) -> None:
        connection_id = self.tree.selected_connection_id() if hasattr(self, "tree") else ""
        summary = self.catalog.get_summary(connection_id) if connection_id else None
        selected = summary is not None
        allowed = not self._manual_shutdown_gated and not self._tree_shutdown_started
        self.workspace_btn.setEnabled(allowed and not self.workspace_persistence.busy)
        self.new_btn.setEnabled(allowed)
        self.edit_btn.setEnabled(allowed and selected and connection_id not in self._workspaces)
        self.delete_btn.setEnabled(allowed and selected and connection_id not in self._workspaces)
        self.favorite_btn.setEnabled(allowed and selected)
        self.open_btn.setEnabled(allowed and selected)
        self.new_query_btn.setEnabled(allowed)
        self.refresh_btn.setEnabled(allowed)
        selected_tree_owner = self._tree_owners.get(connection_id) if connection_id else None
        self.directory_refresh_btn.setEnabled(
            allowed and selected and not bool(getattr(selected_tree_owner, "is_closing", False))
        )
        self.directory_close_btn.setEnabled(
            allowed and bool(connection_id) and selected_tree_owner is not None
            and not selected_tree_owner.is_closing
        )
        self.catalog_toggle_btn.setEnabled(allowed)
        self.search_edit.setEnabled(allowed)
        self.tree.setEnabled(allowed)
        if selected:
            self.favorite_btn.setText(
                ("取消收藏" if summary.favorite else "收藏") if self.language == "zh" else
                ("Unfavorite" if summary.favorite else "Favorite")
            )
        else:
            self.favorite_btn.setText("收藏" if self.language == "zh" else "Favorite")
        workspace = self._active_workspace()
        host = workspace.host if workspace is not None else None
        self.new_query_btn.setVisible(callable(getattr(host, "_new_sql_tab", None)))
        self.new_query_btn.setEnabled(allowed and callable(getattr(host, "_new_sql_tab", None)))
        agent = workspace.agent_widget if workspace is not None else None
        self.agent_btn.setVisible(agent is not None)
        self.agent_btn.setEnabled(self.agent_btn.isVisible() and allowed)
        if self.agent_btn.isVisible():
            self.agent_btn.setChecked(bool(workspace.agent_visible))
        self.summary_create_btn.setVisible(not selected and not self.catalog.summaries)
        self.summary_create_btn.setEnabled(allowed)

    def manual_shutdown_snapshot(self):
        """Combine safe owner snapshots from open connection workspaces only."""
        from ui.data_center.manual_query import ManualShutdownSnapshot

        sessions = []
        can_commit = True
        for workspace in tuple(self._workspaces.values()):
            snapshot_method = getattr(workspace.host, "manual_shutdown_snapshot", None)
            if callable(snapshot_method):
                snapshot = snapshot_method()
                sessions.extend(tuple(getattr(snapshot, "sessions", ()) or ()))
                can_commit = can_commit and bool(getattr(snapshot, "can_commit", False))
            elif callable(getattr(workspace.host, "manual_shutdown_cleanup", None)):
                # Older manual-capable hosts without a snapshot cannot safely
                # participate in a global Commit decision.
                can_commit = False
        return ManualShutdownSnapshot(tuple(sessions), can_commit)

    def manual_shutdown_cleanup(self, disposition=None):
        """Preflight every open host before broadcasting any manual disposition."""
        from ui.data_center.manual_query import ManualShutdownStatus

        action = None if disposition is None else str(getattr(disposition, "value", disposition)).strip().casefold()
        supported = {None, "preflight", "close", "commit", "rollback", "cancel"}
        if action not in supported:
            raise ValueError("unsupported manual shutdown disposition")
        workspaces = tuple(self._workspaces.values())
        if action == "cancel":
            results = []
            for workspace in workspaces:
                cleanup = getattr(workspace.host, "manual_shutdown_cleanup", None)
                if callable(cleanup):
                    results.append(self._manual_status_value(cleanup("cancel")))
            if "pending" in results:
                return ManualShutdownStatus.PENDING
            return ManualShutdownStatus.CANCELLED

        states = []
        for workspace in workspaces:
            snapshot_method = getattr(workspace.host, "manual_shutdown_snapshot", None)
            cleanup = getattr(workspace.host, "manual_shutdown_cleanup", None)
            snapshot = snapshot_method() if callable(snapshot_method) else None
            status = cleanup(None) if callable(cleanup) else ManualShutdownStatus.READY
            states.append((workspace.host, cleanup, status))

        preflight = self._aggregate_manual_status([state[2] for state in states])
        if action in {None, "preflight"}:
            return preflight
        if preflight in {ManualShutdownStatus.PENDING.value, ManualShutdownStatus.CANCELLED.value}:
            return ManualShutdownStatus(preflight)
        if action == "close" and preflight not in {
            ManualShutdownStatus.READY.value,
            ManualShutdownStatus.READY_TO_CLOSE.value,
        }:
            return ManualShutdownStatus.DECISION_REQUIRED
        if action in {"commit", "rollback"} and preflight != ManualShutdownStatus.DECISION_REQUIRED.value:
            return ManualShutdownStatus.DECISION_REQUIRED
        if action == "commit" and not self.manual_shutdown_snapshot().can_commit:
            return ManualShutdownStatus.DECISION_REQUIRED

        results = []
        for _host, cleanup, _status in states:
            results.append(
                self._manual_status_value(cleanup(action))
                if callable(cleanup) else ManualShutdownStatus.READY.value
            )
        aggregate = self._aggregate_manual_status(results)
        return ManualShutdownStatus(aggregate)

    @staticmethod
    def _aggregate_manual_status(statuses) -> str:
        from ui.data_center.manual_query import ManualShutdownStatus

        values = [DataCenterPanel._manual_status_value(status) for status in statuses]
        if not values or all(value == "ready" for value in values):
            return ManualShutdownStatus.READY.value
        if "pending" in values:
            return ManualShutdownStatus.PENDING.value
        if "decision_required" in values:
            return ManualShutdownStatus.DECISION_REQUIRED.value
        if "cancelled" in values:
            return ManualShutdownStatus.CANCELLED.value
        if "ready_to_close" in values and all(
            value in {"ready", "ready_to_close"} for value in values
        ):
            return ManualShutdownStatus.READY_TO_CLOSE.value
        return ManualShutdownStatus.DECISION_REQUIRED.value

    def manual_shutdown_gate(self, enabled: bool) -> bool:
        """Gate new work in the catalog and each already-open manual host."""
        enabled = bool(enabled)
        changed = []
        try:
            for workspace in tuple(self._workspaces.values()):
                host = workspace.host
                gate = getattr(host, "manual_shutdown_gate", None)
                has_manual_seam = callable(getattr(host, "manual_shutdown_snapshot", None)) or callable(
                    getattr(host, "manual_shutdown_cleanup", None)
                )
                if not callable(gate):
                    if enabled and has_manual_seam:
                        raise RuntimeError("open manual host does not expose a shutdown gate")
                    continue
                if gate(enabled) is False:
                    raise RuntimeError("open manual host rejected the shutdown gate")
                if enabled:
                    changed.append(host)
        except Exception:
            for host in reversed(changed):
                try:
                    host.manual_shutdown_gate(False)
                except Exception:
                    pass
            return False
        self._manual_shutdown_gated = enabled
        self._sync_actions()
        return True

    def workbench_shutdown_cleanup(self) -> bool:
        """Close every metadata owner and poll existing workbench cleanup without waiting."""
        complete = True
        for dialog in self.findChildren(_WorkspaceConnectionDialog):
            dialog._transport_test_pending_close = True
            dialog.reject()
            if dialog._transport_test_worker is not None:
                complete = False
        if not self._tree_shutdown_started:
            self._tree_shutdown_started = True
            self._sync_actions()
        for connection_id, owner in tuple(self._tree_owners.items()):
            future = self._begin_tree_owner_close(connection_id)
            if future is None or not future.done():
                complete = False
                continue
            try:
                result = future.result()
                owner_complete = bool(getattr(result, "completed", False))
            except Exception:
                owner_complete = False
            if owner_complete:
                self._release_tree_owner(connection_id)
            else:
                complete = False
        for workspace in tuple(self._workspaces.values()):
            cleanup = getattr(workspace.host, "workbench_shutdown_cleanup", None)
            try:
                current = bool(cleanup()) if callable(cleanup) else True
                current = current and not self._has_live_manual_worker(workspace.host)
            except Exception:
                current = False
            complete = current and complete
        complete = complete and not self._tree_owners
        if not complete:
            return False
        try:
            from tools.data_center.connection_transport import retry_default_transport_cleanup

            return bool(retry_default_transport_cleanup())
        except Exception:
            return False

    def _show_error(self, message: str) -> None:
        self.summary_title.setText("提示" if self.language == "zh" else "Notice")
        self.summary_text.setText(str(message or ""))
        self.summary_hint.setText("")
        self.workspace_tabs.setCurrentIndex(0)

    def agent_is_running(self) -> bool:
        for workspace in self._workspaces.values():
            agent = getattr(workspace.host, "data_center_agent", None)
            controller = getattr(agent, "_controller", None)
            if controller is not None and bool(getattr(controller, "is_running", False)):
                return True
        return False

    def agent_shutdown_cleanup(self) -> bool:
        complete = True
        for workspace in tuple(self._workspaces.values()):
            cleanup = getattr(workspace.host, "agent_shutdown_cleanup", None)
            if callable(cleanup):
                try:
                    complete = bool(cleanup()) and complete
                except Exception:
                    complete = False
        return complete

    def shutdown_cleanup(self) -> bool:
        return self.agent_shutdown_cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        try:
            manual_status = self._manual_status_value(self.manual_shutdown_cleanup(None))
        except Exception:
            manual_status = "error"
        if manual_status != "ready":
            self._show_error(
                "请先逐个关闭连接工作区并处理手动会话；当前工作区保持打开。"
                if self.language == "zh" else
                "Close each connection workspace and resolve its manual sessions first; this workspace remains open."
            )
            event.ignore()
            return
        if self.workbench_shutdown_cleanup() and self.agent_shutdown_cleanup():
            event.accept()
        else:
            event.ignore()


__all__ = ["DataCenterPanel"]
