# -*- coding: utf-8 -*-
"""共享数据库连接编辑对话框。

该控件属于通用 UI 层，供 SQL、Redis 和 MongoDB 工作台复用，
避免数据库页面之间通过私有类产生横向依赖。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tools.db_connect import (
    REDIS_AUTH_ACL,
    REDIS_AUTH_NONE,
    REDIS_AUTH_PASSWORD,
    DbError,
    is_mongo_uri,
    mongo_auth_mechanism,
    normalize_mongo_seed_nodes,
    normalize_redis_auth_mode,
    normalize_redis_seed_nodes,
    probe_connection,
)
from tools.db_contracts import DEFAULT_PORTS, DIALECTS, normalize_oceanbase_mode
from ui.confirm_dialog import show_success, show_warning
from ui.design_system import apply_button
from ui.field_metrics import size_compact_button, size_enum_combo, size_line, wrap_secret_field


class ConnectionDialog(QDialog):
    """创建或编辑一个数据库连接。

    Args:
        language: 界面语言，支持 ``zh`` 或 ``en``。
        item: 已有连接配置；密码不在该控件中回显。
        parent: Qt 父控件。
        locked_dialect: 非空时锁定数据库类型，供特定工作台使用。
    """

    def __init__(
        self,
        language: str = "zh",
        item: dict | None = None,
        parent=None,
        locked_dialect: str = "",
    ) -> None:
        super().__init__(parent)
        self.language = language
        self._locked_dialect = str(locked_dialect or "").strip().lower()
        zh = language == "zh"
        self.setWindowTitle("编辑连接" if zh else "Edit connection")
        self.setMinimumWidth(520)
        self._item = dict(item or {})
        if self._locked_dialect and not self._item.get("dialect"):
            self._item["dialect"] = self._locked_dialect
        self._seed_rows: list[tuple[QLineEdit, QLineEdit, QPushButton]] = []

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(str(self._item.get("name") or ""))
        size_line(self.name, "path")
        self.dialect = QComboBox()
        for key, label in DIALECTS:
            self.dialect.addItem(label, key)
        index = self.dialect.findData(str(self._item.get("dialect") or "oracle"))
        self.dialect.setCurrentIndex(index if index >= 0 else 0)
        size_enum_combo(self.dialect)
        self.dialect.currentIndexChanged.connect(self._on_dialect_changed)
        if self._locked_dialect:
            self.dialect.setEnabled(False)

        self.host = QLineEdit(str(self._item.get("host") or ""))
        size_line(self.host, "path")
        self.host.textChanged.connect(self._on_host_changed)
        self.host_label = QLabel("主机" if zh else "Host")
        self.port = QLineEdit(str(self._item.get("port") or DEFAULT_PORTS["oracle"]))
        size_line(self.port, "std")
        self.port_label = QLabel("端口" if zh else "Port")
        self.database = QLineEdit(str(self._item.get("database") or ""))
        self.database.setPlaceholderText("SID / Service / 库名")
        size_line(self.database, "path")
        self.mode = QComboBox()
        self.mode.addItem("单机" if zh else "Standalone", "standalone")
        self.mode.addItem("集群" if zh else "Cluster", "cluster")
        mode_index = self.mode.findData(str(self._item.get("mode") or "standalone"))
        self.mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        size_enum_combo(self.mode)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_label = QLabel("模式" if zh else "Mode")

        self.seed_host = QWidget()
        self.seed_layout = QVBoxLayout(self.seed_host)
        self.seed_layout.setContentsMargins(0, 0, 0, 0)
        self.seed_layout.setSpacing(4)
        self.seed_label = QLabel("集群节点" if zh else "Seed nodes")
        self.add_seed_btn = QPushButton("+ 添加节点" if zh else "+ Add node")
        apply_button(self.add_seed_btn, "ghost", compact=True)
        self.add_seed_btn.clicked.connect(
            lambda: self._add_seed_row("", 27017 if (self.dialect.currentData() or "") == "mongodb" else 6379)
        )
        self.cluster_hint = QLabel(
            "集群模式忽略 Redis DB 序号，使用上方种子节点发现集群。"
            if zh
            else "Cluster mode ignores Redis DB index and uses seed nodes."
        )
        self.cluster_hint.setObjectName("field-hint")
        self.cluster_hint.setWordWrap(True)

        self.auth_mode = QComboBox()
        self.auth_mode.addItem("无认证" if zh else "None", REDIS_AUTH_NONE)
        self.auth_mode.addItem("仅密码" if zh else "Password", REDIS_AUTH_PASSWORD)
        self.auth_mode.addItem("ACL 用户名 + 密码" if zh else "ACL", REDIS_AUTH_ACL)
        size_enum_combo(self.auth_mode)
        auth_idx = self.auth_mode.findData(normalize_redis_auth_mode(self._item))
        self.auth_mode.setCurrentIndex(auth_idx if auth_idx >= 0 else 0)
        self.auth_mode.currentIndexChanged.connect(self._on_auth_mode_changed)
        self.auth_mode_label = QLabel("认证方式" if zh else "Auth")

        self.username = QLineEdit(str(self._item.get("username") or ""))
        size_line(self.username, "path")
        self.user_label = QLabel("用户" if zh else "User")
        self.password = QLineEdit()
        self.password_row, self.password_reveal = wrap_secret_field(
            self.password,
            reveal_text="查看" if zh else "Show",
            hide_text="隐藏" if zh else "Hide",
        )
        self.password_label = QLabel("密码" if zh else "Password")

        self.mongo_auth_source = QLineEdit(str(self._item.get("auth_source") or ""))
        self.mongo_auth_source.setPlaceholderText("默认：目标库，再否则 admin")
        size_line(self.mongo_auth_source, "path")
        self.mongo_auth_source_label = QLabel("认证库" if zh else "Auth DB")
        self.mongo_auth_mech = QComboBox()
        self.mongo_auth_mech.addItem("自动" if zh else "Auto", "auto")
        self.mongo_auth_mech.addItem("SCRAM-SHA-256", "SCRAM-SHA-256")
        self.mongo_auth_mech.addItem("SCRAM-SHA-1", "SCRAM-SHA-1")
        size_enum_combo(self.mongo_auth_mech)
        mech = mongo_auth_mechanism(self._item) or "auto"
        mech_idx = self.mongo_auth_mech.findData(mech if mech != "" else "auto")
        if mech and mech_idx < 0:
            self.mongo_auth_mech.addItem(mech, mech)
            mech_idx = self.mongo_auth_mech.findData(mech)
        self.mongo_auth_mech.setCurrentIndex(mech_idx if mech_idx >= 0 else 0)
        self.mongo_auth_mech_label = QLabel("认证机制" if zh else "Mechanism")
        self.mongo_uri_hint = QLabel(
            "已填写完整 URI：端口与认证库/机制以 URI 参数为准，不会改写连接串。"
            if zh
            else "Full URI takes precedence; split host/port/auth fields are not merged into it."
        )
        self.mongo_uri_hint.setObjectName("field-hint")
        self.mongo_uri_hint.setWordWrap(True)

        self.mongo_replica_set = QLineEdit(
            str(self._item.get("replica_set_name") or self._item.get("replicaSet") or "")
        )
        self.mongo_replica_set.setPlaceholderText(
            "可选，留空自动发现（mongos/分片集群通常无需填写）"
            if zh
            else "Optional, leave empty for auto discovery"
        )
        size_line(self.mongo_replica_set, "path")
        self.mongo_replica_set_label = QLabel("Replica Set" if zh else "Replica Set")
        self.mongo_replica_set_hint = QLabel(
            "仅在已知名称时填写；mongos/sharded cluster 通常无需填写"
            if zh
            else "Fill only if known; mongos/sharded clusters usually do not need this"
        )
        self.mongo_replica_set_hint.setObjectName("field-hint")
        self.mongo_replica_set_hint.setWordWrap(True)

        self.oracle_hint = QLabel(
            "Oracle 客户端在「设置 → Oracle 兼容」中统一配置主目录和 oci.dll，所有 Oracle 连接共用。"
            if zh
            else "Oracle home and oci.dll are configured once in Settings → Oracle."
        )
        self.oracle_hint.setObjectName("field-hint")
        self.oracle_hint.setWordWrap(True)

        form.addRow("名称" if zh else "Name", self.name)
        form.addRow("类型" if zh else "Type", self.dialect)
        form.addRow(self.host_label, self.host)
        form.addRow(self.port_label, self.port)
        form.addRow(self.seed_label, self.seed_host)
        form.addRow("", self.add_seed_btn)
        self.database_label = QLabel("库名" if zh else "Database")
        form.addRow(self.database_label, self.database)
        form.addRow(self.mode_label, self.mode)
        form.addRow(self.cluster_hint)
        form.addRow(self.auth_mode_label, self.auth_mode)
        form.addRow(self.mongo_auth_source_label, self.mongo_auth_source)
        form.addRow(self.mongo_auth_mech_label, self.mongo_auth_mech)
        form.addRow(self.mongo_replica_set_label, self.mongo_replica_set)
        form.addRow(self.mongo_replica_set_hint)
        form.addRow(self.mongo_uri_hint)
        form.addRow(self.user_label, self.username)
        form.addRow(self.password_label, self.password_row)
        form.addRow(self.oracle_hint)
        root.addLayout(form)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("测试连接" if zh else "Test")
        apply_button(self.test_btn, "secondary", compact=True)
        self.test_btn.clicked.connect(self._on_test_connection)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        cancel = QPushButton("取消" if zh else "Cancel")
        apply_button(cancel, "secondary", compact=True)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存" if zh else "Save")
        apply_button(ok, "primary", compact=True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)
        self._load_seed_rows()
        self._on_dialect_changed()

    def _load_seed_rows(self) -> None:
        while self._seed_rows:
            self._remove_seed_row(self._seed_rows[0][0], force=True)
        dialect = (self.dialect.currentData() or "").lower()
        default_port = 27017 if dialect == "mongodb" else 6379
        if dialect == "mongodb":
            try:
                seeds = normalize_mongo_seed_nodes(self._item)
            except DbError:
                seeds = [{"host": str(self._item.get("host") or ""), "port": default_port}]
        else:
            try:
                seeds = normalize_redis_seed_nodes(self._item)
            except DbError:
                seeds = [{"host": str(self._item.get("host") or ""), "port": default_port}]
        if not seeds:
            seeds = [{"host": "", "port": default_port}]
        for seed in seeds:
            self._add_seed_row(seed.get("host") or "", int(seed.get("port") or default_port))

    def _add_seed_row(self, host: str = "", port: int = 6379) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        host_edit = QLineEdit(str(host or ""))
        size_line(host_edit, "path")
        port_edit = QLineEdit(str(int(port or 6379)))
        size_line(port_edit, "std")
        delete_btn = QPushButton("删除" if self.language == "zh" else "Del")
        size_compact_button(delete_btn)
        apply_button(delete_btn, "ghost", compact=True)
        delete_btn.clicked.connect(lambda: self._remove_seed_row(host_edit))
        layout.addWidget(host_edit, 1)
        layout.addWidget(port_edit)
        layout.addWidget(delete_btn)
        self.seed_layout.addWidget(row)
        self._seed_rows.append((host_edit, port_edit, delete_btn))
        self._refresh_seed_delete_state()

    def _remove_seed_row(self, host_edit: QLineEdit, force: bool = False) -> None:
        if not force and len(self._seed_rows) <= 1:
            return
        for index, (host_w, port_w, btn) in enumerate(self._seed_rows):
            if host_w is host_edit:
                self._seed_rows.pop(index)
                parent = host_w.parentWidget()
                if parent is not None:
                    self.seed_layout.removeWidget(parent)
                    parent.deleteLater()
                break
        self._refresh_seed_delete_state()

    def _refresh_seed_delete_state(self) -> None:
        disable = len(self._seed_rows) <= 1
        for _host, _port, btn in self._seed_rows:
            btn.setEnabled(not disable)

    def _collect_seed_nodes(self) -> list[dict]:
        dialect = (self.dialect.currentData() or "").lower()
        default_port = 27017 if dialect == "mongodb" else 6379
        label = "MongoDB" if dialect == "mongodb" else "Redis"
        seeds = []
        for host_w, port_w, _btn in self._seed_rows:
            host = host_w.text().strip()
            raw_port = port_w.text().strip()
            if not host and not raw_port:
                continue
            try:
                port = int(raw_port or default_port)
            except ValueError as exc:
                raise DbError(f"{label} 端口无效：{raw_port!r}（须为 1–65535）") from exc
            if not 1 <= port <= 65535:
                raise DbError(f"{label} 端口无效：{port}（须为 1–65535）")
            if not host:
                raise DbError("集群节点主机不能为空")
            seeds.append({"host": host, "port": port})
        if not seeds:
            raise DbError("至少保留一个集群节点")
        return seeds

    def _on_mode_changed(self) -> None:
        dialect = self.dialect.currentData() or "oracle"
        if dialect == "oceanbase":
            self._update_oceanbase_mode_ui()
        else:
            self._update_nosql_ui()

    def _on_auth_mode_changed(self) -> None:
        self._update_nosql_ui()

    def _on_host_changed(self, _text: str = "") -> None:
        if (self.dialect.currentData() or "") == "mongodb":
            self._update_nosql_ui()

    def _update_oceanbase_mode_ui(self) -> None:
        zh = self.language == "zh"
        mode = normalize_oceanbase_mode(self.mode.currentData())
        if mode == "oracle":
            self.oracle_hint.setVisible(True)
            self.database_label.setText("SID/服务名" if zh else "SID")
            self.database.setPlaceholderText("ORCL / 服务名")
        else:
            self.oracle_hint.setVisible(False)
            self.database_label.setText("库名" if zh else "Database")
            self.database.setPlaceholderText("mysql 库名（可选，留空可浏览所有库）" if zh else "Database (optional)")

    def _set_redis_seed_visible(self, visible: bool) -> None:
        self.seed_label.setVisible(visible)
        self.seed_host.setVisible(visible)
        self.add_seed_btn.setVisible(visible)
        self.cluster_hint.setVisible(visible)

    def _update_nosql_ui(self) -> None:
        dialect = self.dialect.currentData() or "oracle"
        zh = self.language == "zh"
        mode = str(self.mode.currentData() or "standalone").lower()
        redis_cluster = dialect == "redis" and mode == "cluster"
        mongo = dialect == "mongodb"
        mongo_cluster = mongo and mode == "cluster"
        cluster_active = redis_cluster or mongo_cluster
        uri = mongo and is_mongo_uri(self.host.text())

        self._set_redis_seed_visible(cluster_active and not uri)
        if mongo_cluster:
            self.cluster_hint.setText(
                "集群节点支持多 IP 部署（如多个 mongos 或 Replica Set 节点）；Replica Set 名称仅在已知时填写。"
                if zh
                else "Cluster nodes support multiple IPs (mongos or replica set members)."
            )
        elif redis_cluster:
            self.cluster_hint.setText(
                "集群模式忽略 Redis DB 序号，使用上方种子节点发现集群。"
                if zh
                else "Cluster mode ignores Redis DB index and uses seed nodes."
            )

        self.host.setVisible(not (cluster_active and not uri))
        self.host_label.setVisible(not (cluster_active and not uri))
        self.port.setVisible(not cluster_active and not uri)
        self.port_label.setVisible(not cluster_active and not uri)
        self.port.setEnabled(not uri)
        self.auth_mode_label.setVisible(dialect == "redis")
        self.auth_mode.setVisible(dialect == "redis")
        self.mongo_auth_source_label.setVisible(mongo)
        self.mongo_auth_source.setVisible(mongo)
        self.mongo_auth_mech_label.setVisible(mongo)
        self.mongo_auth_mech.setVisible(mongo)
        self.mongo_replica_set_label.setVisible(mongo_cluster and not uri)
        self.mongo_replica_set.setVisible(mongo_cluster and not uri)
        self.mongo_replica_set_hint.setVisible(mongo_cluster and not uri)
        self.mongo_uri_hint.setVisible(uri)
        if dialect == "redis":
            auth = self.auth_mode.currentData() or REDIS_AUTH_NONE
            self.user_label.setVisible(auth == REDIS_AUTH_ACL)
            self.username.setVisible(auth == REDIS_AUTH_ACL)
            self.password_label.setVisible(auth != REDIS_AUTH_NONE)
            self.password_row.setVisible(auth != REDIS_AUTH_NONE)
            self.database_label.setVisible(not redis_cluster)
            self.database.setVisible(not redis_cluster)
        else:
            self.user_label.setVisible(True)
            self.username.setVisible(True)
            self.password_label.setVisible(True)
            self.password_row.setVisible(True)
            self.database_label.setVisible(True)
            self.database.setVisible(True)

    def _on_dialect_changed(self) -> None:
        dialect = self.dialect.currentData() or "oracle"
        zh = self.language == "zh"
        self.mode.blockSignals(True)
        self.mode.clear()
        if dialect == "oceanbase":
            self.mode.addItem("Oracle 模式" if zh else "Oracle mode", "oracle")
            self.mode.addItem("MySQL 模式" if zh else "MySQL mode", "mysql")
            cur_mode = normalize_oceanbase_mode(self._item.get("mode"))
            idx = self.mode.findData(cur_mode)
            self.mode.setCurrentIndex(idx if idx >= 0 else 0)
            self.mode.setVisible(True)
            self.mode_label.setVisible(True)
            self.mode_label.setText("兼容模式" if zh else "Mode")
            self._set_redis_seed_visible(False)
            self.auth_mode.setVisible(False)
            self.auth_mode_label.setVisible(False)
            self.mongo_auth_source.setVisible(False)
            self.mongo_auth_source_label.setVisible(False)
            self.mongo_auth_mech.setVisible(False)
            self.mongo_auth_mech_label.setVisible(False)
            self.mongo_replica_set_label.setVisible(False)
            self.mongo_replica_set.setVisible(False)
            self.mongo_replica_set_hint.setVisible(False)
            self.mongo_uri_hint.setVisible(False)
            self.host.setVisible(True)
            self.host_label.setVisible(True)
            self.port.setVisible(True)
            self.port_label.setVisible(True)
            self.user_label.setVisible(True)
            self.username.setVisible(True)
            self.password_label.setVisible(True)
            self.password_row.setVisible(True)
            self._update_oceanbase_mode_ui()
        elif dialect in ("redis", "mongodb"):
            self.mode.addItem("单机" if zh else "Standalone", "standalone")
            self.mode.addItem(
                "Redis Cluster" if dialect == "redis" else ("集群" if zh else "Cluster"),
                "cluster",
            )
            cur_mode = str(self._item.get("mode") or "standalone").strip().lower()
            idx = self.mode.findData(cur_mode)
            self.mode.setCurrentIndex(idx if idx >= 0 else 0)
            self.mode.setVisible(True)
            self.mode_label.setVisible(True)
            self.mode_label.setText("模式" if zh else "Mode")
            self.oracle_hint.setVisible(False)
            if dialect == "redis":
                self.database_label.setText("DB 序号" if zh else "DB index")
                self.database.setPlaceholderText("0（集群模式忽略）")
                self.host_label.setText("主机" if zh else "Host")
                self.user_label.setText("用户名" if zh else "Username")
            else:
                self.database_label.setText("目标库" if zh else "Database")
                self.database.setPlaceholderText("例如 admin / prpcar")
                self.host_label.setText("连接串 / 主机" if zh else "URL / Host")
                self.host.setPlaceholderText(
                    "mongodb://user:pass@host/?authSource=admin 或主机名"
                    if zh
                    else "mongodb://... or hostname"
                )
                self.user_label.setText("用户名" if zh else "Username")
            self._load_seed_rows()
            self._update_nosql_ui()
        elif dialect == "oracle":
            self.mode.setVisible(False)
            self.mode_label.setVisible(False)
            self._set_redis_seed_visible(False)
            self.auth_mode.setVisible(False)
            self.auth_mode_label.setVisible(False)
            self.mongo_auth_source.setVisible(False)
            self.mongo_auth_source_label.setVisible(False)
            self.mongo_auth_mech.setVisible(False)
            self.mongo_auth_mech_label.setVisible(False)
            self.mongo_replica_set_label.setVisible(False)
            self.mongo_replica_set.setVisible(False)
            self.mongo_replica_set_hint.setVisible(False)
            self.mongo_uri_hint.setVisible(False)
            self.oracle_hint.setVisible(True)
            self.database_label.setText("SID/服务名" if zh else "SID")
            self.database.setPlaceholderText("ORCL / 服务名")
            self.host_label.setText("主机" if zh else "Host")
            self.host.setVisible(True)
            self.host_label.setVisible(True)
            self.port.setVisible(True)
            self.port_label.setVisible(True)
            self.user_label.setText("用户" if zh else "User")
            self.user_label.setVisible(True)
            self.username.setVisible(True)
            self.password_label.setVisible(True)
            self.password_row.setVisible(True)
        elif dialect == "dameng":
            self.mode.setVisible(False)
            self.mode_label.setVisible(False)
            self._set_redis_seed_visible(False)
            self.auth_mode.setVisible(False)
            self.auth_mode_label.setVisible(False)
            self.mongo_auth_source.setVisible(False)
            self.mongo_auth_source_label.setVisible(False)
            self.mongo_auth_mech.setVisible(False)
            self.mongo_auth_mech_label.setVisible(False)
            self.mongo_replica_set_label.setVisible(False)
            self.mongo_replica_set.setVisible(False)
            self.mongo_replica_set_hint.setVisible(False)
            self.mongo_uri_hint.setVisible(False)
            self.oracle_hint.setVisible(False)
            self.database_label.setText("模式/库名" if zh else "Schema")
            self.database.setPlaceholderText("")
            self.host_label.setText("主机" if zh else "Host")
            self.host.setVisible(True)
            self.port.setVisible(True)
            self.port_label.setVisible(True)
            self.user_label.setVisible(True)
            self.username.setVisible(True)
            self.password_label.setVisible(True)
            self.password_row.setVisible(True)
        else:
            self.mode.setVisible(False)
            self.mode_label.setVisible(False)
            self._set_redis_seed_visible(False)
            self.auth_mode.setVisible(False)
            self.auth_mode_label.setVisible(False)
            self.mongo_auth_source.setVisible(False)
            self.mongo_auth_source_label.setVisible(False)
            self.mongo_auth_mech.setVisible(False)
            self.mongo_auth_mech_label.setVisible(False)
            self.mongo_replica_set_label.setVisible(False)
            self.mongo_replica_set.setVisible(False)
            self.mongo_replica_set_hint.setVisible(False)
            self.mongo_uri_hint.setVisible(False)
            self.oracle_hint.setVisible(False)
            self.database_label.setText("库名" if zh else "Database")
            self.database.setPlaceholderText("mysql 库名（可选，留空可浏览所有库）" if zh else "Database (optional)")
            self.host_label.setText("主机" if zh else "Host")
            self.host.setVisible(True)
            self.port.setVisible(True)
            self.port_label.setVisible(True)
            self.user_label.setVisible(True)
            self.username.setVisible(True)
            self.password_label.setVisible(True)
            self.password_row.setVisible(True)
        self.mode.blockSignals(False)

        defaults = {"1521", "2883", "3306", "5236", "6379", "27017"}
        if not self.port.text().strip() or self.port.text().strip() in defaults:
            self.port.setText(str(DEFAULT_PORTS.get(dialect, 3306)))

    def _draft_item(self) -> tuple[dict, str]:
        item = dict(self._item)
        item["name"] = self.name.text().strip()
        item["dialect"] = self._locked_dialect or self.dialect.currentData() or "oracle"
        item["host"] = self.host.text().strip()
        try:
            item["port"] = int(self.port.text().strip() or DEFAULT_PORTS.get(item["dialect"], 1521))
        except ValueError:
            item["port"] = DEFAULT_PORTS.get(item["dialect"], 1521)
        item["database"] = self.database.text().strip()
        item["username"] = self.username.text().strip()
        if item["dialect"] == "oceanbase":
            item["mode"] = normalize_oceanbase_mode(self.mode.currentData())
        else:
            item["mode"] = self.mode.currentData() or "standalone"
        if item["dialect"] == "redis":
            item["auth_mode"] = self.auth_mode.currentData() or REDIS_AUTH_NONE
            if item["auth_mode"] != REDIS_AUTH_ACL:
                item["username"] = ""
            if item["mode"] == "cluster":
                seeds = self._collect_seed_nodes()
                item["seed_nodes"] = seeds
                item["host"] = seeds[0]["host"]
                item["port"] = seeds[0]["port"]
            else:
                item["seed_nodes"] = [{"host": item["host"] or "127.0.0.1", "port": int(item["port"] or 6379)}]
        if item["dialect"] == "mongodb":
            item["auth_source"] = self.mongo_auth_source.text().strip()
            mech = self.mongo_auth_mech.currentData() or "auto"
            item["auth_mechanism"] = "" if mech == "auto" else mech
            if item["mode"] == "cluster" and not is_mongo_uri(item["host"]):
                seeds = self._collect_seed_nodes()
                item["seed_nodes"] = seeds
                item["host"] = seeds[0]["host"]
                item["port"] = seeds[0]["port"]
                replica_set = self.mongo_replica_set.text().strip()
                item["replica_set_name"] = replica_set
                item["replicaSet"] = replica_set
            else:
                item["seed_nodes"] = [{"host": item["host"] or "127.0.0.1", "port": int(item["port"] or 27017)}]
                item.pop("replica_set_name", None)
                item.pop("replicaSet", None)
        return item, self.password.text()

    def payload(self) -> tuple[dict, str]:
        """返回连接配置和当前输入的明文密码。"""
        return self._draft_item()

    def _on_test_connection(self) -> None:
        zh = self.language == "zh"
        try:
            item, password = self._draft_item()
            result = probe_connection(item, plain_password=password)
        except DbError as exc:
            show_warning(self, "测试连接" if zh else "Test", str(exc))
            return
        except Exception as exc:
            show_warning(self, "测试连接" if zh else "Test", str(exc))
            return
        show_success(self, "测试连接" if zh else "Test", result.get("summary") or "连接成功")

    def accept(self) -> None:
        try:
            self._draft_item()
        except DbError as exc:
            show_warning(self, "保存连接" if self.language == "zh" else "Save", str(exc))
            return
        super().accept()
