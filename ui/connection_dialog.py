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
)

from tools.db_contracts import DEFAULT_PORTS, DIALECTS
from ui.design_system import apply_button
from ui.field_metrics import size_enum_combo, size_line, wrap_secret_field


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
        self.setMinimumWidth(480)
        self._item = dict(item or {})
        if self._locked_dialect and not self._item.get("dialect"):
            self._item["dialect"] = self._locked_dialect

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
        self.host_label = QLabel("主机" if zh else "Host")
        self.port = QLineEdit(str(self._item.get("port") or DEFAULT_PORTS["oracle"]))
        size_line(self.port, "std")
        self.database = QLineEdit(str(self._item.get("database") or ""))
        self.database.setPlaceholderText("SID / Service / 库名")
        size_line(self.database, "path")
        self.mode = QComboBox()
        self.mode.addItem("单机" if zh else "Standalone", "standalone")
        self.mode.addItem("集群" if zh else "Cluster", "cluster")
        mode_index = self.mode.findData(str(self._item.get("mode") or "standalone"))
        self.mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        size_enum_combo(self.mode)
        self.mode_label = QLabel("模式" if zh else "Mode")
        self.username = QLineEdit(str(self._item.get("username") or ""))
        size_line(self.username, "path")
        self.password = QLineEdit()
        self.password_row, self.password_reveal = wrap_secret_field(
            self.password,
            reveal_text="查看" if zh else "Show",
            hide_text="隐藏" if zh else "Hide",
        )
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
        form.addRow("端口" if zh else "Port", self.port)
        self.database_label = QLabel("库名" if zh else "Database")
        form.addRow(self.database_label, self.database)
        form.addRow(self.mode_label, self.mode)
        form.addRow("用户" if zh else "User", self.username)
        form.addRow("密码" if zh else "Password", self.password_row)
        form.addRow(self.oracle_hint)
        root.addLayout(form)

        buttons = QHBoxLayout()
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
        self._on_dialect_changed()

    def _on_dialect_changed(self) -> None:
        dialect = self.dialect.currentData() or "oracle"
        zh = self.language == "zh"
        self.oracle_hint.setVisible(dialect in ("oracle", "oceanbase"))
        self.mode.setVisible(dialect in ("redis", "mongodb"))
        self.mode_label.setVisible(dialect in ("redis", "mongodb"))
        if dialect in ("oracle", "oceanbase"):
            self.database_label.setText("SID/服务名" if zh else "SID")
            self.database.setPlaceholderText("ORCL / 服务名")
            self.host_label.setText("主机" if zh else "Host")
        elif dialect == "dameng":
            self.database_label.setText("模式/库名" if zh else "Schema")
            self.database.setPlaceholderText("")
            self.host_label.setText("主机" if zh else "Host")
        elif dialect == "redis":
            self.database_label.setText("DB 序号" if zh else "DB index")
            self.database.setPlaceholderText("0（集群模式忽略）")
            self.host_label.setText("主机" if zh else "Host")
        elif dialect == "mongodb":
            self.database_label.setText("库名" if zh else "Database")
            self.database.setPlaceholderText("例如 admin / prpcar")
            self.host_label.setText("连接串 / 主机" if zh else "URL / Host")
            self.host.setPlaceholderText(
                "mongodb://user:pass@host1,host2,host3/?replicaSet=rs&authSource=db（集群模式可填多主机）"
                if zh
                else "mongodb://user:pass@host1,host2,host3/?replicaSet=rs&authSource=db"
            )
        else:
            self.database_label.setText("库名" if zh else "Database")
            self.database.setPlaceholderText("mysql 库名，例如 test")
            self.host_label.setText("主机" if zh else "Host")
        defaults = {"1521", "2883", "3306", "5236", "6379", "27017"}
        if not self.port.text().strip() or self.port.text().strip() in defaults:
            self.port.setText(str(DEFAULT_PORTS.get(dialect, 3306)))

    def payload(self) -> tuple[dict, str]:
        """返回连接配置和当前输入的明文密码。"""
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
        item["mode"] = self.mode.currentData() or "standalone"
        return item, self.password.text()
