"""TEMP-only model and production-chain coverage for manual table editing."""

from __future__ import annotations

import os
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from panels.ai_workbench_panel import AiWorkbenchPanel
from panels.data_center_panel import DataCenterPanel, _FixedSqlWorkbench
from tools.data_center.changeset import VerifiedColumn, VerifiedTableMetadata
from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.manual_session import ManualColumn, ManualQueryPage, ManualSession, ManualSessionState, ManualSessionTarget
from tools.data_center.object_tree_composition import ObjectTreeComposition
from tools.data_center.object_tree import TreeTarget, VerifiedTableRef
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost
from tools.data_center.table_source import TableSourceMetadata
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.data_center.workspace_tree import (
    NODE_KIND_ROLE,
    TREE_GENERATION_ROLE,
    TREE_LOADED_ROLE,
    TREE_NODE_ROLE,
    TREE_TARGET_ROLE,
)
from ui.data_center.manual_query import ManualShutdownStatus
from ui.data_center.table_editor import TableEditorModel, TableEditorWidget


APP = QApplication.instance() or QApplication([])


def _editable_fixture(*, read_only: bool = False):
    target = ManualSessionTarget(
        "temp-table-tab",
        "temp-table-connection",
        "temp-profile-revision",
        database="sales",
        schema="sales",
    )
    choice = ConnectionChoice(
        target.connection_id,
        target.profile_revision,
        "mysql",
        "mysql",
        "mysql",
        "sales",
    )
    tree_target = TreeTarget(
        target.connection_id,
        target.profile_revision,
        "mysql",
        mode="mysql",
        database="sales",
    )
    ref = VerifiedTableRef(tree_target, "temp-signed-node", 1, 0, "view" if read_only else "table", "orders", "sales", "")
    columns = (
        VerifiedColumn("tenant_id", editable=True, comparable=True, required_on_insert=True),
        VerifiedColumn("order_id", editable=True, comparable=True, required_on_insert=True),
        VerifiedColumn("amount", editable=True, comparable=True),
        VerifiedColumn("note", editable=True, comparable=True),
    )
    verified = None if read_only else VerifiedTableMetadata(
        target=target,
        table="orders",
        columns=columns,
        primary_key=("tenant_id", "order_id"),
        source_kind="verified_table",
    )
    metadata = TableSourceMetadata(
        target=target,
        signed_ref=ref,
        columns=(
            _column("tenant_id", "varchar", nullable=False, required=True),
            _column("order_id", "integer", nullable=False, required=True),
            _column("amount", "decimal", nullable=True),
            _column("note", "varchar", nullable=True),
        ),
        primary_key=("tenant_id", "order_id") if not read_only else (),
        is_base_table=not read_only,
        verified_changeset=verified,
        read_only_reason="This view is read-only." if read_only else "",
    )
    page = ManualQueryPage(
        query_id="temp-table-query",
        tab_id=target.tab_id,
        generation=target.generation,
        columns=(
            ManualColumn("tenant_id", "VARCHAR"),
            ManualColumn("order_id", "INTEGER"),
            ManualColumn("amount", "DECIMAL"),
            ManualColumn("note", "VARCHAR"),
        ),
        rows=(("acme", 7, Decimal("1.25"), "old"),),
        page_number=1,
        page_size=50,
        has_more=False,
        result_bytes=32,
    )
    return metadata, choice, page


def _column(name: str, type_name: str, *, nullable: bool, required: bool = False):
    from tools.data_center.table_source import TableColumnMetadata

    return TableColumnMetadata(
        name=name,
        type_name=type_name,
        nullable=nullable,
        has_default=False,
        generated=False,
        editable=True,
        comparable=True,
        required_on_insert=required,
    )


_DATABASE = "sales west"
_TABLE = "orders archive"
_VIEW = "orders summary"
_CONNECTION_ID = "temp-table-route"
_ORACLE_CONNECTION_ID = "temp-oracle-table-route"
_ORACLE_SERVICE = "TEMP_ORACLE_SERVICE"
_ORACLE_LOGIN = "LOGIN"
_ORACLE_OWNER = "MiXeD Owner"
_ORACLE_TABLE = "Order Items"


def _route_profile() -> dict:
    return {
        "id": _CONNECTION_ID,
        "name": "TEMP table route",
        "provider": "mysql",
        "dialect": "mysql",
        "mode": "mysql",
        "host": "fixture.invalid",
        "port": 3306,
        "username": "fixture-user",
        "password": "TEMP_OPAQUE_TOKEN",
        "database": "profile default",
        "environment": "test",
        "group_path": "TEMP/table-editor",
    }


class _RouteCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
        self.rows = []
        self.offset = 0
        self.kind = ""
        self.closed = False

    def execute(self, sql, parameters=None):
        text = str(sql)
        folded = re.sub(r"\s+", " ", text).casefold()
        params = tuple(parameters or ())
        self.connection.events.append(("execute", text, params, threading.get_ident()))
        self.description = None
        self.rowcount = 0
        self.rows = []
        self.offset = 0
        self.kind = ""

        if self.connection.tree_mode:
            if "information_schema.schemata" in folded:
                self.rows = [(self.connection.tree_database,)]
            elif "information_schema.tables" in folded:
                self.rows = (
                    [(_TABLE, "BASE TABLE"), (_VIEW, "VIEW")]
                    if params and params[0] == self.connection.tree_database else []
                )
            self.kind = "tree"
            return self

        if "information_schema.tables" in folded:
            object_types = {(_DATABASE, _TABLE): "BASE TABLE", (_DATABASE, _VIEW): "VIEW"}
            self.rows = [(object_types[params[:2]],)] if params[:2] in object_types else []
            self.kind = "catalog"
        elif "information_schema.columns" in folded:
            object_name = _VIEW if params[:2] == (_DATABASE, _VIEW) else _TABLE
            self.rows = [
                (_DATABASE, object_name, "tenant_id", "varchar(40)", "varchar", "NO", False, "", 1),
                (_DATABASE, object_name, "order_id", "int", "int", "NO", False, "", 2),
                (_DATABASE, object_name, "amount", "decimal(10,2)", "decimal", "YES", False, "", 3),
                (_DATABASE, object_name, "note", "varchar(100)", "varchar", "YES", False, "", 4),
            ]
            self.kind = "catalog"
        elif "information_schema.key_column_usage" in folded:
            self.rows = [("tenant_id", 1), ("order_id", 2)]
            self.kind = "catalog"
        elif folded.lstrip().startswith("select"):
            self.kind = "table_data"
            self.description = (
                ("tenant_id", "VARCHAR", None, None, None, None, False),
                ("order_id", "INTEGER", None, None, None, None, False),
                ("amount", "DECIMAL", None, None, None, None, True),
                ("note", "VARCHAR", None, None, None, None, True),
            )
            self.rows = list(self.connection.table_rows)
            self.rowcount = len(self.rows)
            self.connection.select_sqls.append((text, params))
        elif folded.lstrip().startswith("update "):
            self.kind = "dml"
            self.connection.update_sqls.append((text, params))
            if self.connection.update_gate is not None:
                self.connection.update_entered.set()
                if not self.connection.update_gate.wait(5):
                    raise TimeoutError("TEMP update gate was not released")
            self.rowcount = self.connection.update_rowcount
            if self.rowcount == 1:
                self._apply_update(text, params)
        elif folded.lstrip().startswith("rollback to"):
            self.kind = "control"
            if self.connection.rollback_savepoint_failure:
                raise RuntimeError("TEMP rollback-to-savepoint failure")
        else:
            self.kind = "control"
        return self

    def _apply_update(self, sql, parameters):
        match = re.search(r"\bSET\s+(.*?)\s+WHERE\s+", sql, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            return
        columns = re.findall(r"`([^`]+)`\s*=\s*%s", match.group(1), flags=re.IGNORECASE)
        where = sql[match.end():]
        keys = re.findall(r"`([^`]+)`\s*=\s*%s", where, flags=re.IGNORECASE)[:2]
        values = tuple(parameters or ())
        if len(columns) != 2 or len(keys) != 2 or len(values) < 4:
            return
        new_values = dict(zip(columns, values[:len(columns)]))
        key_values = dict(zip(keys, values[len(columns):len(columns) + len(keys)]))
        for index, row in enumerate(self.connection.table_rows):
            current = {"tenant_id": row[0], "order_id": row[1], "amount": row[2], "note": row[3]}
            if all(current.get(name) == value for name, value in key_values.items()):
                updated = dict(current)
                updated.update(new_values)
                self.connection.table_rows[index] = (
                    updated["tenant_id"], updated["order_id"], updated["amount"], updated["note"],
                )
                return

    def fetchmany(self, size):
        if self.kind == "table_data":
            self.connection.data_fetch_calls += 1
            if self.connection.data_fetch_calls == 2 and self.connection.data_gate is not None:
                self.connection.data_entered.set()
                if not self.connection.data_gate.wait(5):
                    raise TimeoutError("TEMP page gate was not released")
        result = self.rows[self.offset:self.offset + int(size)]
        self.offset += len(result)
        return result

    def fetchone(self):
        result = self.fetchmany(1)
        return result[0] if result else None

    def fetchall(self):
        return self.fetchmany(len(self.rows) - self.offset)

    def close(self):
        self.closed = True
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _RouteConnection:
    def __init__(
        self,
        *,
        tree_mode=False,
        tree_database=_DATABASE,
        update_gate=None,
        update_entered=None,
        update_rowcount=1,
        rollback_savepoint_failure=False,
        data_gate=None,
        data_entered=None,
    ):
        self.tree_mode = bool(tree_mode)
        self.tree_database = str(tree_database)
        self.events = []
        self._autocommit = True
        self.closed = False
        self.table_rows = [("tenant-a", index, Decimal("1.25"), f"note {index}") for index in range(105)]
        self.select_sqls = []
        self.update_sqls = []
        self.update_gate = update_gate
        self.update_entered = update_entered or threading.Event()
        self.update_rowcount = int(update_rowcount)
        self.rollback_savepoint_failure = bool(rollback_savepoint_failure)
        self.data_gate = data_gate
        self.data_entered = data_entered or threading.Event()
        self.data_fetch_calls = 0

    def cursor(self):
        return _RouteCursor(self)

    def get_autocommit(self):
        return self._autocommit

    def autocommit(self, enabled):
        self._autocommit = bool(enabled)
        self.events.append(("autocommit", self._autocommit, threading.get_ident()))

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


class _OracleRouteCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
        self.rows = []
        self.offset = 0
        self.closed = False

    def execute(self, sql, parameters=None):
        text = str(sql)
        folded = re.sub(r"\s+", " ", text).casefold()
        params = tuple(parameters.values()) if isinstance(parameters, dict) else tuple(parameters or ())
        self.connection.events.append(("execute", text, params, threading.get_ident()))
        self.description = None
        self.rows = []
        self.offset = 0
        self.rowcount = -1

        if self.connection.tree_mode and "select owner from (" in folded:
            self.description = (("OWNER", str, None, None, None, None, False),)
            self.rows = [(_ORACLE_OWNER,)]
        elif self.connection.tree_mode and "select object_name, object_type from (" in folded:
            self.description = (
                ("OBJECT_NAME", str, None, None, None, None, False),
                ("OBJECT_TYPE", str, None, None, None, None, False),
            )
            self.rows = [(_ORACLE_TABLE, "TABLE"), ("Order Summary", "VIEW")] if params and params[0] == _ORACLE_OWNER else []
        elif "select sys_context('userenv', 'current_schema') from dual" in folded:
            self.description = (("CURRENT_SCHEMA", str, None, None, None, None, False),)
            self.rows = [(self.connection.current_schema,)]
        elif folded.startswith("alter session set current_schema ="):
            identifier = text.split("=", 1)[1].strip()
            if identifier.startswith('"') and identifier.endswith('"'):
                identifier = identifier[1:-1].replace('""', '"')
            self.connection.current_schema = identifier
        elif "select object_type from all_objects" in folded:
            self.description = (("OBJECT_TYPE", str, None, None, None, None, False),)
            if params[:2] == (_ORACLE_OWNER, _ORACLE_TABLE):
                self.rows = [("TABLE",)]
        elif "all_tab_columns" in folded:
            self.description = tuple((name, str, None, None, None, None, True) for name in (
                "OWNER", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH",
                "DATA_PRECISION", "DATA_SCALE", "NULLABLE", "DEFAULT_LENGTH",
                "IDENTITY_COLUMN", "VIRTUAL_COLUMN", "COLUMN_ID",
            ))
            if params[:2] == (_ORACLE_OWNER, _ORACLE_TABLE):
                self.rows = [
                    (_ORACLE_OWNER, _ORACLE_TABLE, "ID", "NUMBER", 22, 10, 0, "N", None, "NO", "NO", 1),
                    (_ORACLE_OWNER, _ORACLE_TABLE, "AMOUNT", "NUMBER", 22, 10, 2, "Y", None, "NO", "NO", 2),
                ]
        elif "all_constraints" in folded:
            self.description = (("COLUMN_NAME", str, None, None, None, None, False), ("POSITION", int, None, None, None, None, False))
            if params[:2] == (_ORACLE_OWNER, _ORACLE_TABLE):
                self.rows = [("ID", 1)]
        elif folded.lstrip().startswith("select") and f'from "{_ORACLE_OWNER}"."{_ORACLE_TABLE}"'.casefold() in folded:
            self.description = (
                ("ID", int, None, None, None, None, False),
                ("AMOUNT", Decimal, None, None, 10, 2, True),
            )
            self.rows = [(1, Decimal("12.50")), (2, Decimal("14.25"))]
        self.rowcount = len(self.rows) if self.rows else -1
        return self

    def fetchmany(self, size):
        result = self.rows[self.offset:self.offset + int(size)]
        self.offset += len(result)
        return result

    def fetchone(self):
        result = self.fetchmany(1)
        return result[0] if result else None

    def close(self):
        self.closed = True
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _OracleRouteConnection:
    def __init__(self, *, tree_mode=False):
        self.tree_mode = bool(tree_mode)
        self.events = []
        self._autocommit = True
        self.current_schema = _ORACLE_LOGIN
        self.closed = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._autocommit = bool(value)
        self.events.append(("autocommit", self._autocommit, threading.get_ident()))

    def get_autocommit(self):
        return self._autocommit

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _OracleRouteCursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("connection_close", threading.get_ident()))


def _spin_until(predicate, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        try:
            if predicate():
                return True
        except RuntimeError:
            pass
        time.sleep(0.003)
    APP.processEvents()
    return bool(predicate())


def _manual_host_cleanup_complete(host) -> bool:
    status = host.manual_shutdown_cleanup()
    if status is not ManualShutdownStatus.READY:
        return False
    snapshot = host.manual_shutdown_snapshot()
    return all(bool(session.cleanup_complete) and not bool(session.busy) for session in snapshot.sessions)


def _isolate_splitter_config(test_case: unittest.TestCase) -> None:
    """Keep production splitter wiring while routing persistence to fixture memory."""
    values: dict[str, list[int]] = {}
    test_case._splitter_memory = values
    test_case._splitter_config_patches = (
        patch(
            "config.load_layout_splitter",
            side_effect=lambda key: list(values[key]) if key in values else None,
        ),
        patch(
            "config.save_layout_splitter",
            side_effect=lambda key, sizes: values.__setitem__(str(key), [int(size) for size in sizes]),
        ),
    )
    for active_patch in test_case._splitter_config_patches:
        active_patch.start()


def _restore_splitter_config(test_case: unittest.TestCase) -> None:
    for active_patch in reversed(getattr(test_case, "_splitter_config_patches", ())):
        active_patch.stop()


def _tree_child(parent, kind: str):
    for row in range(parent.rowCount()):
        child = parent.child(row)
        if str(child.data(NODE_KIND_ROLE) or "") == kind:
            return child
    return None


class TableEditorModelTests(unittest.TestCase):
    def test_sequential_field_edits_merge_into_one_composite_key_update_and_keep_null_distinct(self):
        metadata, choice, page = _editable_fixture()
        model = TableEditorModel(metadata, choice, language="en")
        model.replace_page(page)

        self.assertTrue(model.setData(model.index(0, 2), "2.50"))
        self.assertTrue(model.setData(model.index(0, 3), ""))
        self.assertEqual(model.value_at(0, 2), Decimal("2.50"))
        self.assertEqual(model.value_at(0, 3), "")
        self.assertEqual(model.staged_count, 1)
        preview = model.change_set.preview()
        self.assertEqual(len(preview.statements), 1)
        self.assertEqual(preview.statements[0].operation, "update")
        self.assertIn("tenant_id", preview.statements[0].sql)
        self.assertIn("order_id", preview.statements[0].sql)

        self.assertTrue(model.set_sql_null(0, 3))
        self.assertIsNone(model.value_at(0, 3))
        self.assertIsNone(model.change_set.preview().statements[0].parameters[1])
        self.assertEqual(model.staged_count, 1)

    def test_insert_into_empty_table_uses_valid_qt_row_notification_and_composite_pk(self):
        metadata, choice, page = _editable_fixture()
        model = TableEditorModel(metadata, choice)
        empty = ManualQueryPage(
            query_id=page.query_id,
            tab_id=page.tab_id,
            generation=page.generation,
            columns=page.columns,
            rows=(),
            page_number=1,
            page_size=50,
            has_more=False,
            result_bytes=0,
        )
        model.replace_page(empty)
        inserted = QSignalSpy(model.rowsInserted)

        self.assertTrue(model.stage_insert({
            "tenant_id": "acme",
            "order_id": 8,
            "amount": Decimal("0.00"),
            "note": "",
        }))
        APP.processEvents()
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0][1:], [0, 0])
        self.assertEqual(model.value_at(0, 3), "")
        self.assertEqual(model.staged_count, 1)
        self.assertEqual(model.change_set.preview().statements[0].operation, "insert")

    def test_read_only_view_has_no_edit_or_insert_path(self):
        metadata, choice, page = _editable_fixture(read_only=True)
        model = TableEditorModel(metadata, choice)
        model.replace_page(page)
        self.assertIsNone(model.change_set)
        self.assertFalse(model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(model.stage_insert({"tenant_id": "acme", "order_id": 8}))
        widget = TableEditorWidget("en")
        widget.bind_model(model, page_size=50)
        self.assertIn("This view is read-only.", widget.read_only_note.text())

        from dataclasses import replace

        table_metadata, table_choice, table_page = _editable_fixture()
        no_key_metadata = replace(
            table_metadata,
            primary_key=(),
            verified_changeset=None,
            read_only_reason="No complete verified primary key is available.",
        )
        no_key_model = TableEditorModel(no_key_metadata, table_choice, language="en")
        no_key_model.replace_page(table_page)
        self.assertFalse(no_key_model.flags(no_key_model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(no_key_model.stage_insert({"tenant_id": "acme", "order_id": 8}))
        widget.bind_model(no_key_model, page_size=50)
        self.assertIn("No complete verified primary key", widget.read_only_note.text())
        widget.deleteLater()
        APP.processEvents()

    def test_search_is_labeled_and_only_filters_loaded_rows(self):
        metadata, choice, page = _editable_fixture()
        model = TableEditorModel(metadata, choice, language="en")
        model.replace_page(page)
        widget = TableEditorWidget("en")
        widget.bind_model(model, page_size=50, has_more=False)
        self.assertEqual(widget.search.placeholderText(), "Search loaded rows")
        self.assertEqual([widget.page_size.itemData(i) for i in range(widget.page_size.count())], [50, 200, 500])
        self.assertEqual(model.headerData(model.columnCount() - 1, Qt.Orientation.Horizontal), "Pending")
        widget.set_language("zh")
        self.assertEqual(widget.search.placeholderText(), "搜索已加载行")
        self.assertEqual(model.headerData(model.columnCount() - 1, Qt.Orientation.Horizontal), "变更")
        self.assertEqual(widget.proxy.rowCount(), 1)
        widget.search.setText("not loaded")
        self.assertEqual(widget.proxy.rowCount(), 0)
        self.assertEqual(model.rowCount(), 1)
        widget.deleteLater()
        APP.processEvents()

    def test_long_values_keep_full_typed_content_and_open_the_inspector(self):
        from dataclasses import replace

        metadata, choice, page = _editable_fixture()
        long_value = "long-value-" * 70
        page = replace(page, rows=(("acme", 7, Decimal("1.25"), long_value),))
        model = TableEditorModel(metadata, choice, language="en")
        model.replace_page(page)
        widget = TableEditorWidget("en")
        widget.bind_model(model, page_size=50)
        index = model.index(0, 3)
        self.assertEqual(model.value_at(0, 3), long_value)
        self.assertEqual(model.data(index, Qt.ItemDataRole.DisplayRole), long_value[:512] + "…")
        self.assertTrue(model.data(index, Qt.ItemDataRole.ToolTipRole).endswith(long_value))

        inspected = {}

        def accept_inspector(dialog):
            editor = dialog.findChild(QPlainTextEdit)
            inspected["text"] = editor.toPlainText() if editor is not None else ""
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", accept_inspector):
            widget._inspect_value(widget.proxy.index(0, 3))
        self.assertEqual(inspected["text"], long_value)
        widget.deleteLater()
        APP.processEvents()

    def test_busy_and_stale_pages_reject_edits_and_generated_keys_cannot_be_inserted(self):
        from dataclasses import replace

        metadata, choice, page = _editable_fixture()
        generated_columns = list(metadata.columns)
        generated_columns[0] = replace(generated_columns[0], generated=True, editable=False)
        generated_metadata = replace(metadata, columns=tuple(generated_columns))
        generated_model = TableEditorModel(generated_metadata, choice, language="en")
        generated_model.replace_page(page)
        self.assertFalse(generated_model.can_insert)
        self.assertFalse(generated_model.stage_insert({"tenant_id": "acme", "order_id": 8}))

        invalid_key_model = TableEditorModel(metadata, choice, language="en")
        invalid_key_model.replace_page(replace(page, rows=(("acme", None, Decimal("1.25"), "bad key"),)))
        self.assertFalse(invalid_key_model.flags(invalid_key_model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(invalid_key_model.setData(invalid_key_model.index(0, 2), "2.50"))
        self.assertFalse(invalid_key_model.stage_delete(0))

        model = TableEditorModel(metadata, choice, language="en")
        model.replace_page(page)
        widget = TableEditorWidget("en")
        widget.bind_model(model, page_size=50, has_more=True)
        original = model.value_at(0, 2)
        model.set_editing_enabled(False)
        self.assertFalse(model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(model.setData(model.index(0, 2), "2.50"))
        self.assertFalse(model.stage_insert({"tenant_id": "acme", "order_id": 8, "amount": Decimal("2.50")}))
        self.assertEqual(model.value_at(0, 2), original)
        self.assertFalse(widget.next_button.isEnabled())

        model.set_editing_enabled(True)
        self.assertTrue(model.setData(model.index(0, 2), "2.50"))
        model.clear_staged_after_apply()
        model.mark_page_stale()
        self.assertFalse(model.can_edit)
        self.assertFalse(model.setData(model.index(0, 2), "3.00"))
        self.assertFalse(model.stage_delete(0))
        self.assertFalse(model.stage_insert({"tenant_id": "acme", "order_id": 8, "amount": Decimal("2.50")}))
        self.assertIn("refresh explicitly", widget.read_only_note.text())
        self.assertTrue(widget.refresh_button.isEnabled())
        self.assertFalse(widget.next_button.isEnabled())
        model.replace_page(page)
        self.assertTrue(model.can_edit)
        widget.deleteLater()
        APP.processEvents()


class TableEditorProductionRouteTests(unittest.TestCase):
    def setUp(self):
        _isolate_splitter_config(self)
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
        self.font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
        self.assertGreaterEqual(self.font_id, 0, "Microsoft YaHei UI test font must register for the Windows QPA capture")
        from ui.theme_manager import ThemeManager
        self.theme_manager = ThemeManager.instance()
        self.theme_manager.apply(APP, "calm")
        self.profile = _route_profile()
        self.profile_host = DataCenterProfileHost(
            connections_loader=lambda: [dict(self.profile)],
        )
        self.manual_connections = []
        self.tree_connections = []
        self.manual_connector_profiles = []
        self.manual_secrets = []
        self.data_gate = threading.Event()
        self.data_entered = threading.Event()
        self.update_gate = threading.Event()
        self.update_entered = threading.Event()
        self.tree_database = _DATABASE

        def tree_connector(provider, profile, secret, **_options):
            self.assertEqual(provider, "mysql")
            self.assertIsInstance(profile, dict)
            self.assertEqual(secret, "")
            connection = _RouteConnection(tree_mode=True, tree_database=self.tree_database)
            self.tree_connections.append(connection)
            return connection

        self.relational = RelationalReadOnlyHost(
            connections={_CONNECTION_ID: self.profile},
            secret_loader=lambda _profile: "",
            connector=tree_connector,
        )
        self.nosql = NoSQLReadOnlyHost(connections={}, secret_loader=lambda _profile: "")
        self.composition = ObjectTreeComposition(relational_host=self.relational, nosql_host=self.nosql)

        def manual_connector(profile, secret):
            self.manual_connector_profiles.append(dict(profile))
            self.manual_secrets.append(secret)
            connection = _RouteConnection(
                update_gate=self.update_gate,
                update_entered=self.update_entered,
                data_gate=self.data_gate,
                data_entered=self.data_entered,
            )
            self.manual_connections.append(connection)
            return connection

        def secret_resolver(value):
            if value != "TEMP_OPAQUE_TOKEN":
                raise AssertionError("unexpected synthetic opaque token")
            return "TEMP_PLAINTEXT_SECRET"

        def host_factory(profile, language):
            fixed_type = type("_TempFixedSqlWorkbench", (_FixedSqlWorkbench, AiWorkbenchPanel), {})
            with (
                patch("panels.ai_workbench_panel.load_connections", return_value=[dict(profile)]),
                patch("panels.ai_workbench_panel.load_snapshot", return_value=None),
                patch("panels.ai_workbench_panel.is_enabled", return_value=False),
                patch("panels.ai_workbench_panel.load_ai_local", return_value={}),
                patch("panels.ai_workbench_panel.open_connection", side_effect=AssertionError("legacy connector used")),
                patch("panels.ai_workbench_panel.run_console_statement", side_effect=AssertionError("legacy console used")),
            ):
                panel = fixed_type(
                    language,
                    connection_id=_CONNECTION_ID,
                    dialect="mysql",
                    data_center_agent_options={"profile_host": self.profile_host},
                    manual_query_options={
                        "profile_host": self.profile_host,
                        "connector": manual_connector,
                        "secret_resolver": secret_resolver,
                        "page_size": 50,
                    },
                )
            panel.set_embedded_compact_mode(True)
            return panel

        catalog = WorkspaceCatalog(
            loader=lambda: [dict(self.profile)],
            saver=lambda item, secret=None: dict(item),
            deleter=lambda _connection_id: None,
        )
        self.panel = DataCenterPanel(
            "en",
            catalog=catalog,
            host_factory=host_factory,
            object_tree_composition_factory=lambda _connection_id: self.composition,
        )
        self.panel.resize(1160, 760)
        self.panel.show()
        APP.processEvents()

    def tearDown(self):
        self.data_gate.set()
        self.update_gate.set()
        if getattr(self, "panel", None) is not None:
            manual_hosts = []
            for workspace in tuple(self.panel._workspaces.values()):
                host = getattr(workspace.host, "manual_query_host", None)
                if host is not None:
                    manual_hosts.append(host)
                    for state in tuple(host._states_by_tab.values()):
                        if state.table_model is not None and state.table_model.has_staged_changes:
                            state.table_model.discard_staged()
                    host.manual_shutdown_cleanup("rollback")
            self.assertTrue(
                _spin_until(
                    lambda: all(_manual_host_cleanup_complete(host) for host in manual_hosts),
                    timeout=8,
                ),
                "manual owners did not reach READY with no busy or retained session before config isolation ended",
            )
            self.assertTrue(
                _spin_until(self.panel.workbench_shutdown_cleanup, timeout=8),
                "workbench/tree owners did not finish before config isolation ended",
            )
            self.assertFalse(self.panel._tree_owners, "tree owners must be released before Qt deferred deletion")
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.theme_manager.apply(APP, "calm")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        _restore_splitter_config(self)

    def _live_table_item(self, *, expected_database: str = _DATABASE, kind: str = "table"):
        item = self.panel.tree.connection_source_item(_CONNECTION_ID)
        self.assertIsNotNone(item)
        self.panel.tree.expand(self.panel.tree.proxy_model.mapFromSource(item.index()))
        self.assertTrue(_spin_until(lambda: bool(item.data(TREE_LOADED_ROLE))), "signed connection root did not load")
        database = _tree_child(item, "database")
        self.assertIsNotNone(database)
        self.panel.tree.expand(self.panel.tree.proxy_model.mapFromSource(database.index()))
        self.assertTrue(_spin_until(lambda: bool(database.data(TREE_LOADED_ROLE))), "signed database child did not load")
        table = _tree_child(database, kind)
        self.assertIsNotNone(table)
        node = table.data(TREE_NODE_ROLE)
        target = table.data(TREE_TARGET_ROLE)
        generation = int(table.data(TREE_GENERATION_ROLE) or 0)
        self.assertEqual(node.database, expected_database)
        self.assertEqual(node.name, _VIEW if kind == "view" else _TABLE)
        self.assertEqual(target.database, "")
        return node, target, generation, table

    def _open_from_live_tree(self):
        node, target, generation, table = self._live_table_item()
        index = self.panel.tree.proxy_model.mapFromSource(table.index())
        self.panel.tree.scrollTo(index)
        QTest.mouseClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=self.panel.tree.visualRect(index).center())
        APP.processEvents()
        QTest.mouseDClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=self.panel.tree.visualRect(index).center())
        self.assertTrue(_spin_until(lambda: bool(self.panel._workspaces.get(_CONNECTION_ID))), "table activation did not open its connection workspace")
        workspace = self.panel._workspaces[_CONNECTION_ID]
        host = workspace.host.manual_query_host
        self.assertTrue(_spin_until(
            lambda: any(state.table_model is not None and not state.busy for state in host._states_by_tab.values()),
        ), "production ManualSession did not deliver a typed table page")
        state = next(state for state in host._states_by_tab.values() if state.table_model is not None)
        return node, target, generation, host, state

    @unittest.skipUnless(
        os.environ.get("PENGTOOLS_WINDOWS_IMPORT_CAPTURE") == "1",
        "set PENGTOOLS_WINDOWS_IMPORT_CAPTURE=1 to run the Windows QPA visual evidence pass",
    )
    def test_windows_calm_import_entry_preview_and_shutdown_modal_captures(self):
        from types import SimpleNamespace

        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QWidget

        from main_window import MainWindow
        from tools.data_center.import_preview import CsvSource

        self.assertEqual(APP.platformName().casefold(), "windows")
        _node, _target, _generation, host, state = self._open_from_live_tree()
        output = Path(tempfile.gettempdir()) / f"pengtools-import-product-captures-{uuid.uuid4().hex}"
        output.mkdir()
        source = output / "visible-preview.csv"
        source.write_text(
            "tenant_id,order_id,amount,note\nfixture-tenant,811,2.75,visible preview\n",
            encoding="utf-8",
            newline="",
        )
        captures = []
        main_modal_records = []

        for language in ("zh", "en"):
            self.panel.set_language(language)
            for width, height, pixel_size in ((960, 640, 17), (1160, 720, 21)):
                self.theme_manager.apply(APP, "calm", font_size=pixel_size)
                self.panel.resize(width, height)
                self.panel.apply_layout_mode("narrow" if width == 960 else "compact", low_height=height < 720)
                APP.processEvents()
                editor = host.table_editor
                button = editor.import_button
                self.assertTrue(button.isVisibleTo(self.panel))
                self.assertTrue(button.isEnabled())
                self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                self.assertEqual(button.font().pixelSize(), pixel_size)
                button_rect = button.rect().translated(button.mapTo(self.panel, button.rect().topLeft()))
                self.assertTrue(self.panel.rect().contains(button_rect))
                panel_capture = self.panel.grab()
                panel_path = output / f"entry-{language}-{width}-font{pixel_size}px.png"
                self.assertTrue(panel_capture.save(str(panel_path), "PNG"))

                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertTrue(_spin_until(lambda: bool(host._import_host._entries)))
                _import_id, entry = next(iter(host._import_host._entries.items()))
                controller, dialog = entry.controller, entry.dialog
                self.assertIsNone(controller.parent())
                self.assertIsNone(dialog.parentWidget())
                screen = dialog.screen()
                if screen is not None:
                    available = screen.availableGeometry()
                    self.assertLessEqual(dialog.width(), available.width())
                    self.assertLessEqual(dialog.height(), available.height())
                self.assertTrue(dialog.content_scroll.isVisible())
                self.assertEqual(dialog.xlsx_blank_null.font().pixelSize(), pixel_size)

                dialog.layout().activate()
                dialog.content_scroll.widget().layout().activate()
                APP.processEvents()
                QTest.qWait(160)
                APP.processEvents()

                dialog.path_edit.setText(str(source))
                dialog._inspect_source()
                self.assertTrue(_spin_until(lambda: controller.progress().state == "SOURCE_READY"))
                dialog._preview_mapping()
                self.assertTrue(_spin_until(lambda: controller.progress().state == "PREVIEW_READY"))
                dialog.layout().activate()
                dialog.content_scroll.widget().layout().activate()
                APP.processEvents()
                QTest.qWait(160)
                APP.processEvents()
                self.assertGreater(dialog.mapping_table.rowCount(), 0)
                self.assertGreater(dialog.preview_table.rowCount(), 0)
                footer_buttons = (
                    dialog.stop_button,
                    dialog.retry_button,
                    dialog.rollback_button,
                    dialog.commit_button,
                    dialog.start_button,
                    dialog.close_button,
                )
                for widget in (
                    dialog.target_label,
                    dialog.status_label,
                    dialog.xlsx_blank_null,
                    *footer_buttons,
                ):
                    if widget.isVisibleTo(dialog):
                        self.assertGreaterEqual(
                            widget.width(), widget.minimumSizeHint().width(),
                            f"control is narrower than its minimum hint: {widget.objectName()}",
                        )
                        self.assertTrue(dialog.rect().contains(
                            widget.rect().translated(widget.mapTo(dialog, widget.rect().topLeft()))
                        ), widget.objectName())
                for widget in (dialog.target_label, dialog.status_label, dialog.xlsx_blank_null,
                               dialog.stop_button, dialog.start_button, dialog.close_button):
                    self.assertTrue(widget.isVisibleTo(dialog), widget.objectName())
                title = dialog.findChild(QLabel, "manual-import-title")
                header = dialog.findChild(QFrame, "manual-import-header")
                self.assertIsNotNone(header)
                self.assertEqual(title.text(), "文件导入" if language == "zh" else "File import")
                title_rect = title.rect().translated(title.mapTo(dialog, title.rect().topLeft()))
                header_rect = header.rect().translated(header.mapTo(dialog, header.rect().topLeft()))
                root_rect = dialog.rect()
                title_metrics = title.fontMetrics()
                self.assertTrue(root_rect.contains(title_rect), f"title is outside the dialog: {title_rect.getRect()} / {root_rect.getRect()}")
                self.assertTrue(header_rect.contains(title_rect), f"title is outside its header: {title_rect.getRect()} / {header_rect.getRect()}")
                self.assertGreaterEqual(title.contentsRect().height(), title_metrics.height())
                self.assertGreaterEqual(title.height(), title.minimumSizeHint().height())
                title_layout_records = {
                    "text": title.text(),
                    "rect_in_dialog": title_rect.getRect(),
                    "header_rect_in_dialog": header_rect.getRect(),
                    "root_rect": root_rect.getRect(),
                    "font_pixels": title.font().pixelSize(),
                    "font_points": title.font().pointSizeF(),
                    "font_family": title.font().family(),
                    "font_metrics_height": title_metrics.height(),
                    "contents_rect": title.contentsRect().getRect(),
                    "minimum_size_hint": [title.minimumSizeHint().width(), title.minimumSizeHint().height()],
                    "layout_settled_ms": 320,
                }
                null_font = dialog.xlsx_blank_null.font()
                footer_font_metrics = [
                    {
                        "object_name": footer_button.objectName(),
                        "text": footer_button.text(),
                        "visible": footer_button.isVisibleTo(dialog),
                        "font_pixels": footer_button.font().pixelSize(),
                        "font_points": footer_button.font().pointSizeF(),
                        "font_family": footer_button.font().family(),
                        "font_metrics_height": footer_button.fontMetrics().height(),
                        "minimum_size_hint": [
                            footer_button.minimumSizeHint().width(),
                            footer_button.minimumSizeHint().height(),
                        ],
                    }
                    for footer_button in footer_buttons
                ]
                for footer_button in footer_buttons:
                    self.assertEqual(
                        footer_button.font().pixelSize(), 12,
                        f"production compact footer font changed: {footer_button.text()!r}",
                    )
                import_captures = []
                for section_name, section in (
                    ("mapping", dialog.mapping_table),
                    ("preview", dialog.preview_table),
                ):
                    dialog.content_scroll.ensureWidgetVisible(section, 0, 4)
                    dialog.layout().activate()
                    dialog.content_scroll.widget().layout().activate()
                    APP.processEvents()
                    QTest.qWait(120)
                    APP.processEvents()
                    visible_rect = section.rect().translated(
                        section.mapTo(dialog.content_scroll.viewport(), section.rect().topLeft())
                    )
                    self.assertTrue(
                        dialog.content_scroll.viewport().rect().intersects(visible_rect),
                        f"{section.objectName()} is outside its bounded scroll viewport: {visible_rect.getRect()}",
                    )
                    import_capture = dialog.grab()
                    import_path = output / f"import-{section_name}-{language}-{width}-font{pixel_size}px.png"
                    self.assertTrue(import_capture.save(str(import_path), "PNG"))
                    import_captures.append({
                        "path": str(import_path), "kind": f"populated_import_{section_name}",
                        "language": language, "logical_size": [dialog.width(), dialog.height()],
                        "host_size": [width, height], "title_layout": title_layout_records,
                        "effective_body_font_pixels": pixel_size,
                        "null_checkbox_font_pixels": null_font.pixelSize(),
                        "null_checkbox_font_points": null_font.pointSizeF(),
                        "null_checkbox_font_family": null_font.family(),
                        "footer_font_metrics": footer_font_metrics,
                        "mapping_rows": dialog.mapping_table.rowCount(), "preview_rows": dialog.preview_table.rowCount(),
                        "scroll_value": dialog.content_scroll.verticalScrollBar().value(),
                        "scroll_viewport_rect": dialog.content_scroll.viewport().rect().getRect(),
                        "theme": APP.property("ui_theme"), "qpa_platform": APP.platformName(),
                        "layout_settled_ms": 120,
                        "dpr": import_capture.devicePixelRatioF(),
                    })
                captures.extend((
                    {"path": str(panel_path), "kind": "table_import_entry", "language": language,
                     "logical_size": [width, height], "effective_font_pixels": pixel_size,
                     "theme": APP.property("ui_theme"), "qpa_platform": APP.platformName(),
                     "dpr": panel_capture.devicePixelRatioF(), "import_button_rect": button_rect.getRect()},
                    *import_captures,
                ))

                host._import_host.cleanup("close")
                self.assertTrue(_spin_until(lambda: not host._import_host._entries))

                self.assertTrue(state.table_model.setData(state.table_model.index(0, 2), "2.75"))
                self.assertGreaterEqual(state.table_model.rowCount(), 2)
                self.assertTrue(state.table_model.setData(state.table_model.index(1, 2), "3.25"))
                production_snapshot = host.manual_shutdown_snapshot()
                local_owner = next(
                    item for item in production_snapshot.sessions
                    if item.tab_id == state.target.tab_id
                )
                self.assertEqual(local_owner.pending_local_changes, 2)
                self.assertFalse(production_snapshot.can_commit)
                unknown_owner = SimpleNamespace(
                    tab_id="temp-import-unknown", generation=1, state="UNKNOWN",
                    busy=False, cleanup_complete=False, pending_local_changes=0,
                    owner_kind="import",
                )
                snapshot = SimpleNamespace(
                    sessions=(unknown_owner, local_owner), can_commit=False,
                )
                shutdown_entry = ("fixture", None, snapshot, "decision_required")
                harness_type = type("WindowsShutdownCapture", (QWidget,), {
                    "_manual_can_commit": MainWindow._manual_can_commit,
                    "_manual_summary": MainWindow._manual_summary,
                    "_ask_manual_shutdown_decision": MainWindow._ask_manual_shutdown_decision,
                })
                harness = harness_type()
                harness.language = language
                harness.resize(width, height)
                harness.show()
                capture_result = {}

                def capture_and_cancel():
                    modal = APP.activeModalWidget()
                    if modal is None:
                        capture_result["error"] = "no active modal dialog"
                        return
                    label = modal.findChild(QLabel, "manual-shutdown-decision-summary")
                    commit = modal.findChild(QPushButton, "manual-shutdown-commit")
                    rollback = modal.findChild(QPushButton, "manual-shutdown-rollback")
                    cancel = modal.findChild(QPushButton, "manual-shutdown-cancel")
                    for button in (commit, rollback, cancel):
                        self.assertGreaterEqual(
                            button.width(), button.minimumSizeHint().width(),
                            f"shutdown action text is clipped: {button.text()!r} width={button.width()} hint={button.minimumSizeHint().width()}",
                        )
                        self.assertTrue(modal.rect().contains(
                            button.rect().translated(button.mapTo(modal, button.rect().topLeft()))
                        ), f"shutdown action falls outside dialog: {button.objectName()}")
                    self.assertTrue(cancel.hasFocus(), "safe Cancel action must receive initial focus")
                    modal_screen = modal.screen()
                    if modal_screen is not None:
                        available = modal_screen.availableGeometry()
                        self.assertLessEqual(modal.width(), available.width())
                        self.assertLessEqual(modal.height(), available.height())
                    action_bounds = {
                        button.objectName(): [button.x(), button.y(), button.width(), button.height()]
                        for button in (commit, rollback, cancel)
                    }
                    image = modal.grab()
                    modal_path = output / f"shutdown-{language}-{width}-font{pixel_size}px.png"
                    image.save(str(modal_path), "PNG")
                    capture_result.update({
                        "path": str(modal_path), "size": [modal.width(), modal.height()],
                        "text": label.text(), "body_font_pixels": label.font().pixelSize(),
                        "cancel_focus": cancel.hasFocus(),
                        "cancel_tooltip": cancel.toolTip(),
                        "commit_enabled": commit.isEnabled(), "rollback_text": rollback.text(),
                        "rollback_tooltip": rollback.toolTip(), "cancel_text": cancel.text(),
                        "action_bounds": action_bounds,
                        "actual_staged_change_count": local_owner.pending_local_changes,
                        "cancel_default": cancel.isDefault(), "theme": APP.property("ui_theme"),
                        "qpa_platform": APP.platformName(), "dpr": image.devicePixelRatioF(),
                    })
                    QTest.keyClick(modal, Qt.Key.Key_Escape)

                QTimer.singleShot(100, capture_and_cancel)
                self.assertIsNone(harness._ask_manual_shutdown_decision([shutdown_entry]))
                self.assertNotIn("error", capture_result, str(capture_result))
                self.assertFalse(capture_result["commit_enabled"])
                self.assertTrue(capture_result["cancel_default"])
                self.assertIn("自动应用" if language == "zh" else "automatically", capture_result["text"])
                self.assertIn("新的回滚" if language == "zh" else "no additional rollback", capture_result["text"])
                self.assertEqual(capture_result["actual_staged_change_count"], 2)
                main_modal_records.append({**capture_result, "language": language, "target": [width, height],
                                           "effective_font_pixels": pixel_size})
                harness.close()
                harness.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                APP.processEvents()
                state.table_model.discard_staged()
                APP.processEvents()

        (output / "manifest.json").write_text(
            json.dumps({"theme": "calm", "qpa_platform": APP.platformName(),
                        "captures": captures, "shutdown_modals": main_modal_records},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.windows_import_capture_directory = output

    def test_visible_import_action_reaches_ready_and_requires_explicit_group_rollback(self):
        from PyQt6.QtWidgets import QMessageBox

        from tests.test_data_center_import_session import _Cursor, _MySqlProviderConnection
        from tools.data_center.import_preview import CsvSource
        from tools.data_center.import_session import ImportSessionState
        from tools.data_center.manual_session import ManualSessionState
        from ui.data_center.manual_query import ManualShutdownStatus

        _node, _tree_target, _tree_generation, host, state = self._open_from_live_tree()
        editor = host.table_editor
        self.assertTrue(editor.import_button.isVisibleTo(self.panel))
        self.assertTrue(editor.import_button.isEnabled())

        provider_connection = _MySqlProviderConnection()
        provider_connection.mysql_columns = [
            (_DATABASE, _TABLE, "tenant_id", "varchar(40)", "varchar", "NO", 0, "", 1),
            (_DATABASE, _TABLE, "order_id", "int", "int", "NO", 0, "", 2),
            (_DATABASE, _TABLE, "amount", "decimal(10,2)", "decimal", "YES", 0, "", 3),
            (_DATABASE, _TABLE, "note", "varchar(100)", "varchar", "YES", 0, "", 4),
        ]

        def execute(sql, parameters):
            values = provider_connection._values(parameters)
            provider_connection.events.append(("execute", threading.get_ident(), sql))
            folded = sql.casefold()
            if "select table_type from information_schema.tables" in folded:
                return _Cursor(provider_connection, [("BASE TABLE",)])
            if "from information_schema.columns" in folded:
                return _Cursor(provider_connection, provider_connection.mysql_columns)
            if "from information_schema.key_column_usage" in folded:
                return _Cursor(provider_connection, [("tenant_id", 1), ("order_id", 2)])
            raise AssertionError(f"unexpected temporary provider catalog query: {sql!r} {values!r}")

        provider_connection.execute = execute
        host.connector = lambda _profile, _secret: provider_connection
        with tempfile.TemporaryDirectory(prefix="pengtools-product-import-route-") as directory:
            source = Path(directory) / "append.csv"
            source.write_text(
                "tenant_id,order_id,amount,note\nnew-tenant,777,2.50,appended\n",
                encoding="utf-8",
                newline="",
            )

            QTest.mouseClick(editor.import_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: bool(host._import_host._entries)), "visible Import action did not create an ImportHost owner")
            import_id, entry = next(iter(host._import_host._entries.items()))
            controller, dialog = entry.controller, entry.dialog
            self.assertIsNone(controller.parent())
            self.assertIsNone(dialog.parentWidget())
            self.assertEqual(entry.parent_sql_tab_id, state.target.tab_id)
            self.assertEqual(entry.parent_sql_generation, state.generation)
            self.assertNotEqual(controller.import_tab_id, state.target.tab_id)
            self.assertEqual(controller.import_generation, 1)

            dialog.path_edit.setText(str(source))
            dialog._inspect_source()
            self.assertTrue(_spin_until(lambda: controller.progress().state in {"SOURCE_READY", "PREVIEW_FAILED"}))
            self.assertEqual(controller.progress().state, "SOURCE_READY", controller.progress().safe_code)
            self.assertEqual(dialog.mapping_table.rowCount(), 4)
            dialog._preview_mapping()
            self.assertTrue(_spin_until(lambda: controller.progress().state in {"PREVIEW_READY", "PREVIEW_FAILED"}))
            self.assertEqual(controller.progress().state, "PREVIEW_READY", controller.progress().safe_code)
            with patch("ui.data_center.import_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(
                lambda: controller.progress().state in {"READY_TO_COMMIT", "FAILED", "UNKNOWN"},
                timeout=8,
            ), "public import route did not finish bounded insert")
            self.assertEqual(controller.progress().state, "READY_TO_COMMIT", controller.progress().safe_code)
            self.assertEqual(controller.session.status.state, ImportSessionState.READY_TO_COMMIT)
            self.assertEqual(controller.session.request.target.tab_id, controller.import_tab_id)
            self.assertEqual(controller.session.request.target.generation, controller.import_generation)
            self.assertTrue(controller.session.status.owner_alive)
            self.assertTrue(controller.session.status.connection_retained)
            self.assertEqual(provider_connection.commit_count, 0)
            self.assertEqual(provider_connection.rollback_count, 0)
            self.assertEqual(provider_connection.inserts[0][1], (("new-tenant", 777, Decimal("2.50"), "appended"),))

            snapshots = host.manual_shutdown_snapshot()
            imported = next(item for item in snapshots.sessions if item.owner_kind == "import")
            self.assertEqual((imported.tab_id, imported.generation), (controller.import_tab_id, controller.import_generation))
            self.assertEqual((imported.parent_sql_tab_id, imported.parent_sql_generation), (state.target.tab_id, state.generation))
            self.assertEqual(imported.state, ManualSessionState.DIRTY)
            self.assertFalse(imported.busy)
            self.assertEqual(host.manual_shutdown_cleanup(None), ManualShutdownStatus.DECISION_REQUIRED)
            sql_tabs = self.panel._workspaces[_CONNECTION_ID].host.sql_tabs
            sql_index = sql_tabs.indexOf(state.tab)
            self.assertGreaterEqual(sql_index, 0)
            self.assertTrue(host.request_tab_close(sql_index))
            self.assertIs(sql_tabs.widget(sql_index), state.tab)
            self.assertEqual(provider_connection.rollback_count, 0)

            host.manual_shutdown_gate(True)
            self.assertFalse(editor.insert_button.isEnabled())
            self.assertFalse(editor.refresh_button.isEnabled())
            self.assertFalse(state.table_model.editing_enabled)
            before_staged = state.table_model.staged_count
            before_table_selects = tuple(len(item.select_sqls) for item in self.manual_connections)
            before_value = state.table_model.value_at(0, 2)
            self.assertFalse(state.table_model.setData(state.table_model.index(0, 2), "99.99"))
            self.assertEqual(state.table_model.value_at(0, 2), before_value)
            self.assertEqual(state.table_model.staged_count, before_staged)
            self.assertFalse(controller.inspect_source(CsvSource(source, encoding="utf-8", delimiter=",")))
            self.assertFalse(controller.preview_mapping((), (), None))
            self.assertFalse(controller.approve_and_start(user_approved=True))
            self.assertFalse(controller.commit())
            self.assertFalse(controller.rollback())
            self.assertFalse(controller.request_close(action="rollback"))
            host.refresh_table_source()
            host.apply_table_preview(None)
            self.assertFalse(state.busy)
            self.assertEqual(
                tuple(len(item.select_sqls) for item in self.manual_connections),
                before_table_selects,
                "gated refresh/apply calls must not issue table database work",
            )
            host.manual_shutdown_gate(False)
            result = host.manual_shutdown_cleanup("rollback")
            self.assertIn(result, {ManualShutdownStatus.PENDING, ManualShutdownStatus.READY})
            finished = _spin_until(
                lambda: host.manual_shutdown_cleanup(None) is ManualShutdownStatus.READY,
                timeout=8,
            )
            if not finished:
                diagnostics = {
                    "status": host.manual_shutdown_cleanup(None),
                    "host_action": host._shutdown_action,
                    "import_action": host._import_host._shutdown_action,
                    "import_snapshot": host._import_host.snapshot(),
                    "session_status": controller.session.status,
                }
            else:
                diagnostics = {}
            self.assertTrue(finished, f"explicit product shutdown rollback did not release every owner: {diagnostics!r}")
            self.assertEqual(controller.session.status.state, ImportSessionState.ROLLED_BACK)
            self.assertEqual((provider_connection.commit_count, provider_connection.rollback_count), (0, 1))
            self.assertTrue(provider_connection.closed)
            self.assertNotIn(import_id, host._import_host._entries)

    def test_visible_import_reaches_ready_and_workspace_group_commit_uses_only_import_connection(self):
        from PyQt6.QtWidgets import QMessageBox

        from tests.test_data_center_import_session import _Cursor, _MySqlProviderConnection
        from tools.data_center.import_session import ImportSessionState
        from ui.data_center.manual_query import ManualShutdownStatus

        _node, _tree_target, _tree_generation, host, parent_state = self._open_from_live_tree()
        editor = host.table_editor
        self.assertTrue(editor.import_button.isVisibleTo(self.panel))
        self.assertTrue(editor.import_button.isEnabled())

        import_connection = _MySqlProviderConnection()
        import_connection.mysql_columns = [
            (_DATABASE, _TABLE, "tenant_id", "varchar(40)", "varchar", "NO", 0, "", 1),
            (_DATABASE, _TABLE, "order_id", "int", "int", "NO", 0, "", 2),
            (_DATABASE, _TABLE, "amount", "decimal(10,2)", "decimal", "YES", 0, "", 3),
            (_DATABASE, _TABLE, "note", "varchar(100)", "varchar", "YES", 0, "", 4),
        ]

        def execute(sql, parameters):
            values = import_connection._values(parameters)
            import_connection.events.append(("execute", threading.get_ident(), sql))
            folded = sql.casefold()
            if "select table_type from information_schema.tables" in folded:
                return _Cursor(import_connection, [("BASE TABLE",)])
            if "from information_schema.columns" in folded:
                return _Cursor(import_connection, import_connection.mysql_columns)
            if "from information_schema.key_column_usage" in folded:
                return _Cursor(import_connection, [("tenant_id", 1), ("order_id", 2)])
            raise AssertionError(f"unexpected temporary provider catalog query: {sql!r} {values!r}")

        import_connection.execute = execute
        host.connector = lambda _profile, _secret: import_connection
        with tempfile.TemporaryDirectory(prefix="pengtools-product-group-commit-") as directory:
            source = Path(directory) / "append.csv"
            source.write_text(
                "tenant_id,order_id,amount,note\nnew-tenant,778,4.25,workspace commit\n",
                encoding="utf-8",
                newline="",
            )

            QTest.mouseClick(editor.import_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: bool(host._import_host._entries)))
            import_id, entry = next(iter(host._import_host._entries.items()))
            controller, dialog = entry.controller, entry.dialog
            self.assertEqual(entry.parent_sql_tab_id, parent_state.target.tab_id)
            self.assertEqual(entry.parent_sql_generation, parent_state.generation)
            self.assertNotEqual(controller.import_tab_id, parent_state.target.tab_id)
            dialog.path_edit.setText(str(source))
            dialog._inspect_source()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "SOURCE_READY"))
            dialog._preview_mapping()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "PREVIEW_READY"))
            with patch("ui.data_center.import_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: controller.progress().state in {
                "READY_TO_COMMIT", "FAILED", "UNKNOWN",
            }, timeout=8))
            self.assertEqual(controller.progress().state, "READY_TO_COMMIT", controller.progress().safe_code)
            self.assertEqual(controller.session.status.state, ImportSessionState.READY_TO_COMMIT)
            self.assertEqual(import_connection.inserts[0][1], (("new-tenant", 778, Decimal("4.25"), "workspace commit"),))
            self.assertEqual((import_connection.commit_count, import_connection.rollback_count), (0, 0))

            parent_connections = tuple(self.manual_connections)
            parent_effects_before = tuple(
                (len(connection.update_sqls), sum(event[0] == "commit" for event in connection.events))
                for connection in parent_connections
            )
            product_snapshot = self.panel.manual_shutdown_snapshot()
            import_owner = next(
                owner for owner in product_snapshot.sessions
                if owner.owner_kind == "import" and owner.tab_id == controller.import_tab_id
            )
            self.assertEqual(import_owner.parent_sql_tab_id, parent_state.target.tab_id)
            self.assertEqual(import_owner.parent_sql_generation, parent_state.generation)
            self.assertEqual(import_owner.state, "DIRTY")
            self.assertFalse(import_owner.busy)
            self.assertTrue(product_snapshot.can_commit)
            self.assertEqual(self.panel.manual_shutdown_cleanup(None), ManualShutdownStatus.DECISION_REQUIRED)

            self.assertTrue(self.panel.manual_shutdown_gate(True))
            result = self.panel.manual_shutdown_cleanup("commit")
            self.assertIn(result, {ManualShutdownStatus.PENDING, ManualShutdownStatus.READY})
            completed = _spin_until(
                lambda: self.panel._manual_status_value(self.panel.manual_shutdown_cleanup(None)) == "ready",
                timeout=8,
            )
            if not completed:
                diagnostics = {
                    "panel_snapshot": self.panel.manual_shutdown_snapshot(),
                    "panel_preflight": self.panel.manual_shutdown_cleanup(None),
                    "host_action": host._shutdown_action,
                    "import_action": host._import_host._shutdown_action,
                    "import_snapshot": host._import_host.snapshot(),
                    "controller_progress": controller.progress(),
                    "session_status": controller.session.status,
                }
            else:
                diagnostics = {}
            self.assertTrue(completed, f"workspace-wide explicit commit did not retire every owner: {diagnostics!r}")
            self.assertEqual(controller.session.status.state, ImportSessionState.COMMITTED)
            self.assertEqual((import_connection.commit_count, import_connection.rollback_count), (1, 0))
            self.assertTrue(import_connection.closed)
            self.assertNotIn(import_id, host._import_host._entries)
            self.assertNotIn(import_connection, parent_connections)
            self.assertEqual(
                tuple(
                    (len(connection.update_sqls), sum(event[0] == "commit" for event in connection.events))
                    for connection in parent_connections
                ),
                parent_effects_before,
                "the parent SQL session must not receive imported rows or the import commit",
            )
            self.assertTrue(all(not connection.update_sqls for connection in parent_connections))

    def test_terminal_import_dialog_is_closed_by_actual_workspace_seal(self):
        from PyQt6.QtWidgets import QMessageBox

        from tests.test_data_center_import_session import _Cursor, _MySqlProviderConnection
        from tools.data_center.import_session import ImportSessionState

        _node, _tree_target, _tree_generation, host, parent_state = self._open_from_live_tree()
        self.assertTrue(parent_state.table_model.setData(parent_state.table_model.index(0, 2), "2.50"))
        parent_preview = parent_state.table_model.change_set.preview()
        self.update_gate.set()
        host.apply_table_preview(parent_preview)
        self.assertTrue(_spin_until(lambda: not parent_state.busy), "parent table update did not finish")
        self.assertEqual(parent_state.session.state, ManualSessionState.DIRTY)
        parent_connection = self.manual_connections[0]
        self.assertEqual(len(parent_connection.update_sqls), 1)
        editor = host.table_editor
        provider_connection = _MySqlProviderConnection()
        provider_connection.mysql_columns = [
            (_DATABASE, _TABLE, "tenant_id", "varchar(40)", "varchar", "NO", 0, "", 1),
            (_DATABASE, _TABLE, "order_id", "int", "int", "NO", 0, "", 2),
            (_DATABASE, _TABLE, "amount", "decimal(10,2)", "decimal", "YES", 0, "", 3),
            (_DATABASE, _TABLE, "note", "varchar(100)", "varchar", "YES", 0, "", 4),
        ]

        def execute(sql, parameters):
            values = provider_connection._values(parameters)
            provider_connection.events.append(("execute", threading.get_ident(), sql))
            folded = sql.casefold()
            if "select table_type from information_schema.tables" in folded:
                return _Cursor(provider_connection, [("BASE TABLE",)])
            if "from information_schema.columns" in folded:
                return _Cursor(provider_connection, provider_connection.mysql_columns)
            if "from information_schema.key_column_usage" in folded:
                return _Cursor(provider_connection, [("tenant_id", 1), ("order_id", 2)])
            raise AssertionError(f"unexpected temporary provider catalog query: {sql!r} {values!r}")

        provider_connection.execute = execute
        host.connector = lambda _profile, _secret: provider_connection
        with tempfile.TemporaryDirectory(prefix="pengtools-terminal-import-dialog-") as directory:
            source = Path(directory) / "terminal.csv"
            source.write_text(
                "tenant_id,order_id,amount,note\nnew-tenant,779,5.00,close workspace\n",
                encoding="utf-8",
                newline="",
            )
            QTest.mouseClick(editor.import_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: bool(host._import_host._entries)))
            import_id, entry = next(iter(host._import_host._entries.items()))
            controller, dialog = entry.controller, entry.dialog
            self.assertEqual(entry.parent_sql_tab_id, parent_state.target.tab_id)
            dialog.path_edit.setText(str(source))
            dialog._inspect_source()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "SOURCE_READY"))
            dialog._preview_mapping()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "PREVIEW_READY"))
            with patch("ui.data_center.import_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: controller.progress().state == "READY_TO_COMMIT", timeout=8))
            APP.processEvents()
            self.assertTrue(dialog.commit_button.isEnabled())
            dialog.commit_button.click()
            self.assertTrue(_spin_until(
                lambda: controller.session.status.state is ImportSessionState.COMMITTED
                and controller.session.cleanup_future.done()
                and not controller.session.status.owner_alive,
                timeout=8,
            ))
            self.assertEqual((provider_connection.commit_count, provider_connection.rollback_count), (1, 0))
            self.assertTrue(dialog.isVisible())
            owner = next(item for item in host.manual_shutdown_snapshot().sessions if item.owner_kind == "import")
            self.assertEqual(owner.tab_id, controller.import_tab_id)
            self.assertTrue(owner.cleanup_complete)
            self.assertEqual(self.panel._manual_status_value(self.panel.manual_shutdown_cleanup(None)), "decision_required")

            workspace = self.panel._workspaces[_CONNECTION_ID]
            with patch.object(self.panel, "_choose_workspace_manual_disposition", return_value="commit") as choose:
                self.panel._close_workspace_tab(self.panel.workspace_tabs.indexOf(workspace))
            choose.assert_called_once()
            self.assertTrue(_spin_until(
                lambda: _CONNECTION_ID not in self.panel._workspaces,
                timeout=10,
            ), "workspace commit did not retire the terminal import dialog and dirty parent owner")
            self.assertFalse(dialog.isVisible())
            self.assertNotIn(import_id, host._import_host._entries)
            self.assertEqual((provider_connection.commit_count, provider_connection.rollback_count), (1, 0))
            self.assertEqual(len(parent_connection.update_sqls), 1)
            self.assertEqual(sum(event[0] == "commit" for event in parent_connection.events), 1)

    def test_standalone_unknown_dialog_ack_is_seen_by_parent_workspace_preflight(self):
        from PyQt6.QtWidgets import QMessageBox

        from tests.test_data_center_import_session import _Cursor, _MySqlProviderConnection
        from tools.data_center.import_session import ImportSessionState

        _node, _tree_target, _tree_generation, host, parent_state = self._open_from_live_tree()
        editor = host.table_editor
        provider_connection = _MySqlProviderConnection()
        provider_connection.commit_error = True
        provider_connection.mysql_columns = [
            (_DATABASE, _TABLE, "tenant_id", "varchar(40)", "varchar", "NO", 0, "", 1),
            (_DATABASE, _TABLE, "order_id", "int", "int", "NO", 0, "", 2),
            (_DATABASE, _TABLE, "amount", "decimal(10,2)", "decimal", "YES", 0, "", 3),
            (_DATABASE, _TABLE, "note", "varchar(100)", "varchar", "YES", 0, "", 4),
        ]

        def execute(sql, parameters):
            values = provider_connection._values(parameters)
            provider_connection.events.append(("execute", threading.get_ident(), sql))
            folded = sql.casefold()
            if "select table_type from information_schema.tables" in folded:
                return _Cursor(provider_connection, [("BASE TABLE",)])
            if "from information_schema.columns" in folded:
                return _Cursor(provider_connection, provider_connection.mysql_columns)
            if "from information_schema.key_column_usage" in folded:
                return _Cursor(provider_connection, [("tenant_id", 1), ("order_id", 2)])
            raise AssertionError(f"unexpected temporary provider catalog query: {sql!r} {values!r}")

        provider_connection.execute = execute
        host.connector = lambda _profile, _secret: provider_connection
        with tempfile.TemporaryDirectory(prefix="pengtools-standalone-import-ack-") as directory:
            source = Path(directory) / "unknown.csv"
            source.write_text(
                "tenant_id,order_id,amount,note\nnew-tenant,780,6.00,unknown result\n",
                encoding="utf-8",
                newline="",
            )
            QTest.mouseClick(editor.import_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: bool(host._import_host._entries)))
            import_id, entry = next(iter(host._import_host._entries.items()))
            controller, dialog = entry.controller, entry.dialog
            self.assertEqual(entry.parent_sql_tab_id, parent_state.target.tab_id)
            dialog.path_edit.setText(str(source))
            dialog._inspect_source()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "SOURCE_READY"))
            dialog._preview_mapping()
            self.assertTrue(_spin_until(lambda: controller.progress().state == "PREVIEW_READY"))
            with patch("ui.data_center.import_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
            self.assertTrue(_spin_until(lambda: controller.progress().state == "READY_TO_COMMIT", timeout=8))
            APP.processEvents()
            self.assertTrue(dialog.commit_button.isEnabled())
            dialog.commit_button.click()
            self.assertTrue(_spin_until(
                lambda: controller.session.status.state is ImportSessionState.UNKNOWN
                and controller.session.cleanup_future.done()
                and not controller.session.status.owner_alive,
                timeout=8,
            ), f"failed commit did not fully retire before acknowledgement: {controller.session.status!r}")
            self.assertEqual((provider_connection.commit_count, provider_connection.rollback_count), (1, 0))

            def acknowledge_from_dialog(box):
                button = next(item for item in box.buttons() if item.text() == "Acknowledge and close")
                button.click()
                return 0

            with patch.object(QMessageBox, "exec", new=acknowledge_from_dialog):
                dialog.close()
            self.assertTrue(controller.unknown_acknowledged)
            self.assertFalse(dialog.isVisible())
            snapshot = self.panel.manual_shutdown_snapshot()
            self.assertFalse(
                any(item.owner_kind == "import" for item in snapshot.sessions),
                "the already acknowledged, dead owner must be collected before the public snapshot",
            )
            self.assertEqual(
                self.panel._manual_status_value(self.panel.manual_shutdown_cleanup(None)),
                "ready_to_close",
                "the clean parent SQL session may close, but no UNKNOWN import decision is repeated",
            )
            self.assertNotIn(import_id, host._import_host._entries)

            workspace = self.panel._workspaces[_CONNECTION_ID]
            with patch.object(
                self.panel,
                "_choose_workspace_manual_disposition",
                side_effect=AssertionError("workspace close must not ask again about the acknowledged UNKNOWN import"),
            ):
                self.panel._close_workspace_tab(self.panel.workspace_tabs.indexOf(workspace))
            self.assertTrue(_spin_until(lambda: _CONNECTION_ID not in self.panel._workspaces, timeout=10))
            self.assertEqual((provider_connection.commit_count, provider_connection.rollback_count), (1, 0))

    def _capture_layouts(self):
        directory = Path(tempfile.gettempdir()) / f"pengtools-table-editor-ui-{uuid.uuid4().hex}"
        directory.mkdir()
        captures = []
        font_results = []
        layout_records = []
        from ui.theme_manager import ThemeManager
        theme_manager = ThemeManager.instance()
        try:
            for language in ("zh", "en"):
                self.panel.set_language(language)
                for width, height, point_size in ((960, 640, 13), (1160, 720, 16)):
                    pixel_size = 17 if point_size == 13 else 21
                    theme_manager.apply(APP, "calm", font_size=pixel_size)
                    self.panel.resize(width, height)
                    self.panel.apply_layout_mode("narrow" if width == 960 else "compact", low_height=height < 720)
                    APP.processEvents()
                    self.assertEqual(
                        self.panel.size().width(), width,
                        f"DataCenterPanel expanded beyond the {width}x{height} logical target",
                    )
                    self.assertEqual(
                        self.panel.size().height(), height,
                        f"DataCenterPanel expanded beyond the {width}x{height} logical target",
                    )
                    self.assertLessEqual(
                        self.panel.minimumSizeHint().height(), height,
                        f"DataCenterPanel minimum height exceeds the {width}x{height} logical target",
                    )
                    workbench = self.panel._workspaces[_CONNECTION_ID].host
                    host = workbench.manual_query_host
                    editor = host.table_editor
                    self.assertEqual(APP.property("ui_theme"), "calm")
                    self.assertIn(f"font-size: {pixel_size}px", APP.property("base_stylesheet"))
                    for widget in (editor.search, editor.insert_button, editor.table):
                        self.assertEqual(widget.font().pixelSize(), pixel_size)
                        self.assertEqual(widget.font().family(), "Microsoft YaHei UI")
                    font_results.append((language, width, height, point_size, pixel_size, editor.table.font().family()))

                    essentials = (
                        workbench.run_btn,
                        workbench.new_tab_btn,
                        workbench.sql_tabs,
                        workbench.sql_tabs.tabBar(),
                        host.transaction_actions,
                        host.commit_btn,
                        host.rollback_btn,
                        host.stop_btn,
                        host.workbench_more_btn,
                        editor.search,
                        editor.page_size,
                        editor.sql_view_button,
                        editor.refresh_button,
                        editor.next_button,
                        editor.table,
                        editor.table.horizontalHeader(),
                        editor.insert_button,
                        editor.delete_button,
                        editor.preview_button,
                        editor.discard_button,
                        editor.status,
                    )
                    for widget in essentials:
                        ancestry = []
                        ancestor = widget
                        while ancestor is not None:
                            ancestry.append((
                                type(ancestor).__name__, ancestor.objectName(), ancestor.geometry().getRect(),
                                ancestor.size().width(), ancestor.size().height(),
                                ancestor.minimumSizeHint().width(), ancestor.minimumSizeHint().height(),
                                ancestor.isVisible(),
                            ))
                            ancestor = ancestor.parentWidget()
                        self.assertTrue(widget.isVisibleTo(self.panel), f"{widget.objectName() or type(widget).__name__} is not visible")
                        top_left = widget.mapTo(self.panel, widget.rect().topLeft())
                        visible_rect = widget.rect().translated(top_left)
                        self.assertTrue(
                            self.panel.rect().contains(visible_rect),
                            f"{widget.objectName() or type(widget).__name__} clips outside target: {visible_rect!r}; ancestry={ancestry!r}; layout={self._layout_measurements(workbench, host, editor)!r}",
                        )
                        self.assertGreater(widget.width(), 0)
                        self.assertGreater(widget.height(), 0)
                    for button in (
                        workbench.run_btn,
                        workbench.new_tab_btn,
                        host.commit_btn,
                        host.rollback_btn,
                        host.stop_btn,
                        host.workbench_more_btn,
                    ):
                        self.assertGreaterEqual(
                            button.width(), button.minimumSizeHint().width(),
                            f"toolbar button label is clipped: {button.text()!r} at {language}/{width}x{height}/{pixel_size}px",
                        )
                        self.assertGreaterEqual(
                            button.height(), button.minimumSizeHint().height(),
                            f"toolbar button height is clipped: {button.text()!r} at {language}/{width}x{height}/{pixel_size}px",
                        )
                    for button in (
                        editor.refresh_button,
                        editor.next_button,
                        editor.sql_view_button,
                        editor.insert_button,
                        editor.delete_button,
                        editor.preview_button,
                        editor.discard_button,
                    ):
                        self.assertGreaterEqual(
                            button.width(), button.minimumSizeHint().width(),
                            f"button label is clipped: {button.text()!r} at {width}x{height}/{point_size}pt",
                        )
                        self.assertGreaterEqual(
                            button.height(), button.minimumSizeHint().height(),
                            f"button height is clipped: {button.text()!r} at {width}x{height}/{point_size}pt; actual={button.height()}, minimumHint={button.minimumSizeHint().height()}, layout={self._layout_measurements(workbench, host, editor)!r}",
                        )
                    self.assertGreater(editor.table.model().rowCount(), 0)
                    self.assertGreaterEqual(editor.status.height(), editor.status.minimumSizeHint().height())
                    self.assertEqual(self.panel.width(), width)
                    self.assertEqual(self.panel.height(), height)
                    pixmap = self.panel.grab()
                    dpr = pixmap.devicePixelRatioF()
                    self.assertAlmostEqual(pixmap.width() / dpr, width)
                    self.assertAlmostEqual(pixmap.height() / dpr, height)
                    path = directory / f"{language}-{width}x{height}-font{pixel_size}px.png"
                    self.assertTrue(pixmap.save(str(path), "PNG"), str(path))
                    captures.append(path)
                    row_height = (
                        editor.table.verticalHeader().sectionSize(0)
                        if editor.table.model() is not None and editor.table.model().rowCount() else 0
                    )
                    visible_rows = (
                        min(editor.table.model().rowCount(), editor.table.viewport().height() // row_height)
                        if row_height else 0
                    )
                    self.assertGreaterEqual(
                        visible_rows, 1,
                        f"the live typed table must show a complete row at {language}/{width}x{height}/{pixel_size}px",
                    )
                    layout_records.append({
                        "path": str(path),
                        "language": language,
                        "logical_size": [width, height],
                        "requested_font_points": point_size,
                        "effective_font_pixels": pixel_size,
                        "font_family": editor.table.font().family(),
                        "theme": APP.property("ui_theme"),
                        "qpa_platform": os.environ.get("QT_QPA_PLATFORM", "unspecified"),
                        "device_pixel_ratio": dpr,
                        "pixel_size": [pixmap.width(), pixmap.height()],
                        "scroll_value": workbench.workspace_scroll.verticalScrollBar().value(),
                        "scroll_maximum": workbench.workspace_scroll.verticalScrollBar().maximum(),
                        "visible_complete_table_rows": visible_rows,
                        "table_viewport_height": editor.table.viewport().height(),
                        "table_row_height": row_height,
                    })
            manifest = directory / "manifest.json"
            manifest.write_text(
                json.dumps({"theme": "calm", "captures": layout_records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        finally:
            theme_manager.apply(APP, "calm")
            APP.processEvents()
        self.layout_captures = tuple(captures)
        self.layout_font_results = tuple(font_results)

    def _layout_measurements(self, workbench, host, editor):
        workspace = self.panel._workspaces[_CONNECTION_ID]
        names = (
            ("panel", self.panel),
            ("body_splitter", self.panel._body_splitter),
            ("workspace_tabs", self.panel.workspace_tabs),
            ("workspace", workspace.host),
            ("workspace_scroll", workbench.workspace_scroll),
            ("columns_splitter", workbench.columns_splitter),
            ("left_pane", workbench.left_pane),
            ("middle_pane", workbench._middle_pane),
            ("transaction_actions", host.transaction_actions),
            ("sql_tabs", workbench.sql_tabs),
            ("sql_tab", workbench.sql_tabs.currentWidget()),
            ("sql_editor", workbench.sql_tabs.currentWidget().editor),
            ("body_splitter", workbench.body_splitter),
            ("result_tabs", workbench.result_tabs),
            ("result_page", workbench.result_tabs.widget(0)),
            ("side_tabs", workbench.side_tabs),
            ("table_editor", editor),
        )
        output = {}
        for name, widget in names:
            policy = widget.sizePolicy()
            output[name] = {
                "rect": widget.rect().translated(widget.mapTo(self.panel, QPoint(0, 0))).getRect(),
                "size": (widget.width(), widget.height()),
                "minimum": (widget.minimumWidth(), widget.minimumHeight()),
                "minimumHint": (widget.minimumSizeHint().width(), widget.minimumSizeHint().height()),
                "hint": (widget.sizeHint().width(), widget.sizeHint().height()),
                "maximum": (widget.maximumWidth(), widget.maximumHeight()),
                "verticalPolicy": str(policy.verticalPolicy()),
                "visible": widget.isVisible(),
            }
        output["scrollbar"] = {
            "value": workbench.workspace_scroll.verticalScrollBar().value(),
            "minimum": workbench.workspace_scroll.verticalScrollBar().minimum(),
            "maximum": workbench.workspace_scroll.verticalScrollBar().maximum(),
            "pageStep": workbench.workspace_scroll.verticalScrollBar().pageStep(),
        }
        return output

    def test_live_signed_tree_routes_to_owner_applies_changes_and_preserves_tabs(self):
        node, tree_target, tree_generation, host, state = self._open_from_live_tree()
        self.layout_captures = ()
        self.assertEqual(state.table_scope.tree_target, tree_target)
        self.assertEqual(state.table_scope.node, node)
        self.assertEqual(state.table_scope.tree_generation, tree_generation)
        self.assertEqual(state.target.database, _DATABASE)
        self.assertEqual(state.target.schema, _DATABASE)
        self.assertEqual(state.target.profile_revision, tree_target.profile_revision)
        self.assertEqual(state.choice.revision, tree_target.profile_revision)
        self.assertEqual(state.choice.database, _DATABASE)
        self.assertIs(state.table_scope.service, self.composition.service)
        self.assertEqual(self.manual_connector_profiles[-1]["database"], _DATABASE)
        self.assertEqual(self.manual_secrets, ["TEMP_PLAINTEXT_SECRET"])
        self.assertEqual(host.table_editor.model.rowCount(), 50)
        self.assertTrue(state.last_page.has_more)
        export_context = host._capture_export_context(state)
        self.assertIsNotNone(export_context)
        self.assertEqual(export_context.pages[0].rows[0][2], Decimal("1.25"))

        connection = self.manual_connections[0]
        owner_events = [
            event for event in connection.events
            if event[0] in {"execute", "autocommit", "rollback", "commit", "connection_close"}
        ]
        owner_threads = {event[-1] for event in owner_events}
        self.assertEqual(len(owner_threads), 1)
        self.assertNotEqual(next(iter(owner_threads)), threading.get_ident())
        model = state.table_model
        table_tab = state.tab
        self.panel.resize(960, 640)
        self.panel.apply_layout_mode("narrow", low_height=True)
        APP.processEvents()
        # The explicit compact-view toggle preserves the tab SQL and any staged
        # overlay; switching focus performs no query or transaction I/O.
        self.assertTrue(model.setData(model.index(0, 2), "2.50"))
        sql_before_toggle = table_tab.editor.toPlainText()
        events_before_toggle = tuple(connection.events)
        self.assertTrue(host._table_focus_active)
        self.assertFalse(table_tab.editor.isVisibleTo(host.panel))
        middle_layout = host.panel._middle_pane.layout()
        toolbar_layout = middle_layout.itemAt(0).layout()
        sql_view_requests = QSignalSpy(host.table_editor.sql_view_requested)
        host.table_editor.sql_view_button.click()
        APP.processEvents()
        self.assertEqual(list(sql_view_requests), [[True]])
        self.assertFalse(host._table_focus_active)
        self.assertGreaterEqual(middle_layout.indexOf(host.transaction_actions), 0)
        self.assertEqual(toolbar_layout.indexOf(host.transaction_actions), -1)
        self.assertTrue(table_tab.editor.isVisibleTo(host.panel))
        host.table_editor.sql_view_button.click()
        APP.processEvents()
        self.assertEqual(list(sql_view_requests), [[True], [False]])
        self.assertTrue(host._table_focus_active)
        self.assertEqual(middle_layout.indexOf(host.transaction_actions), -1)
        self.assertGreaterEqual(toolbar_layout.indexOf(host.transaction_actions), 0)
        self.assertFalse(table_tab.editor.isVisibleTo(host.panel))
        self.assertGreater(host.panel.body_splitter.sizes()[0], 0)
        self.assertTrue(host.panel._middle_pane.isVisibleTo(host.panel))
        self.assertFalse(table_tab.editor.isVisibleTo(host.panel))
        self.assertTrue(host.panel.sql_tabs.isVisibleTo(host.panel))
        self.assertTrue(host.panel.sql_tabs.tabBar().isVisibleTo(host.panel))
        for control in (
            host.panel.run_btn,
            host.panel.new_tab_btn,
            host.commit_btn,
            host.rollback_btn,
            host.stop_btn,
            host.workbench_more_btn,
        ):
            self.assertTrue(control.isVisibleTo(host.panel), control.objectName() or control.text())
        self.assertEqual(middle_layout.indexOf(host.transaction_actions), -1)
        self.assertGreaterEqual(toolbar_layout.indexOf(host.transaction_actions), 0)
        self.assertTrue(model.has_staged_changes)

        # Public snapshot and both preflight spellings stay read-only: the
        # pending overlay is reported separately from the clean DB transaction.
        with patch(
            "PyQt6.QtWidgets.QMessageBox.question",
            side_effect=AssertionError("read-only preflight must not ask or discard"),
        ):
            snapshot = host.manual_shutdown_snapshot()
            table_snapshot = next(item for item in snapshot.sessions if item.tab_id == state.target.tab_id)
            self.assertEqual(table_snapshot.state, ManualSessionState.CLEAN.value)
            self.assertEqual(table_snapshot.pending_local_changes, 1)
            self.assertFalse(snapshot.can_commit)
            self.assertEqual(host.manual_shutdown_cleanup(), ManualShutdownStatus.DECISION_REQUIRED)
            self.assertEqual(host.manual_shutdown_cleanup("preflight"), ManualShutdownStatus.DECISION_REQUIRED)
        self.assertTrue(model.has_staged_changes)
        self.assertEqual(tuple(connection.events), events_before_toggle)

        # The controls retained in the compact SQL pane still perform real tab
        # navigation/creation/close actions while no query or transaction is sent.
        sql_tabs = host.panel.sql_tabs
        tab_bar = sql_tabs.tabBar()
        original_count = sql_tabs.count()
        host.panel.new_tab_btn.click()
        APP.processEvents()
        self.assertEqual(sql_tabs.count(), original_count + 1)
        temporary_index = sql_tabs.currentIndex()
        self.assertEqual(temporary_index, original_count)
        temporary_tab = sql_tabs.widget(temporary_index)
        table_index = sql_tabs.indexOf(table_tab)
        table_tab_rect = tab_bar.tabRect(table_index)
        self.assertFalse(table_tab_rect.isEmpty())
        QTest.mouseClick(tab_bar, Qt.MouseButton.LeftButton, pos=table_tab_rect.center())
        APP.processEvents()
        self.assertIs(sql_tabs.currentWidget(), table_tab)
        self.assertTrue(host._table_focus_active)
        self.assertFalse(table_tab.editor.isVisibleTo(host.panel))
        self.assertFalse(temporary_tab.editor.isVisibleTo(host.panel))
        self.assertTrue(model.has_staged_changes)
        close_button = tab_bar.tabButton(temporary_index, tab_bar.ButtonPosition.RightSide)
        self.assertIsNotNone(close_button, "the SQL tab close control must remain in the compact tab bar")
        self.assertTrue(close_button.isVisibleTo(host.panel))
        QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
        self.assertTrue(_spin_until(lambda: sql_tabs.count() == original_count), "the visible SQL tab close control did not close its tab")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.assertIs(sql_tabs.currentWidget(), table_tab)
        self.assertTrue(host._table_focus_active)
        self.assertFalse(table_tab.editor.isVisibleTo(host.panel))
        self.assertTrue(model.has_staged_changes)
        self.assertEqual(tuple(connection.events), events_before_toggle)

        # Explicitly return to SQL and back with the same tab/model, without
        # executing the SELECT or committing the table overlay.
        host.table_editor.sql_view_button.click()
        APP.processEvents()
        self.assertEqual(list(sql_view_requests), [[True], [False], [True]])
        self.assertFalse(host._table_focus_active)
        self.assertGreaterEqual(middle_layout.indexOf(host.transaction_actions), 0)
        self.assertEqual(toolbar_layout.indexOf(host.transaction_actions), -1)
        self.assertGreater(host.panel.body_splitter.sizes()[0], 0)
        self.assertTrue(table_tab.editor.isVisibleTo(host.panel))
        self.assertTrue(model.has_staged_changes)
        self.assertEqual(table_tab.editor.toPlainText(), sql_before_toggle)
        self.assertEqual(tuple(connection.events), events_before_toggle)
        model.discard_staged()
        APP.processEvents()
        self._capture_layouts()
        self.assertEqual(len(self.layout_captures), 4)
        self.assertEqual(len(self.layout_font_results), 4)
        for path in self.layout_captures:
            self.assertGreater(path.stat().st_size, 1000)

        # Local overlays block query, refresh, next-page and close without I/O.
        self.assertTrue(model.setData(model.index(0, 2), "2.50"))
        self.assertTrue(model.setData(model.index(0, 3), ""))
        preview = model.change_set.preview()
        self.assertEqual(len(preview.statements), 1)
        self.assertIn(f"`{_DATABASE}`.`{_TABLE}`", preview.statements[0].sql)
        selected_selects = len(connection.select_sqls)
        host.fetch_next_page()
        host.refresh_table_source()
        host.run_current()
        self.assertFalse(host._can_open_export_dialog(state))
        self.assertTrue(host.request_tab_close(host.panel.sql_tabs.indexOf(table_tab)))
        self.assertEqual(len(connection.select_sqls), selected_selects)
        self.assertEqual(connection.update_sqls, [])
        self.assertTrue(model.has_staged_changes)
        self.assertFalse(host.table_editor.next_button.isEnabled())

        # The same tab-owned model receives a background page while another tab is visible.
        model.discard_staged()
        APP.processEvents()
        second_tab = host.panel._new_sql_tab()
        host.panel.sql_tabs.setCurrentWidget(table_tab)
        host.fetch_next_page()
        self.assertTrue(self.data_entered.wait(3), "the second owner fetch did not reach its controlled wait")
        host.panel.sql_tabs.setCurrentWidget(second_tab)
        self.data_gate.set()
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertEqual(state.table_model.rowCount(), 100)
        self.assertEqual(len(state.pages), 2)
        host.panel.sql_tabs.setCurrentWidget(table_tab)
        self.assertIs(host.table_editor.model, state.table_model)
        self.assertEqual(host.table_editor.model.rowCount(), 100)

        # Busy apply blocks model edits and a successful apply leaves the old page stale.
        model = state.table_model
        original_value = model.value_at(0, 2)
        self.assertTrue(model.setData(model.index(0, 2), "2.50"))
        self.assertTrue(model.setData(model.index(0, 3), "updated note"))
        update_preview = model.change_set.preview()
        self.assertEqual(len(update_preview.statements), 1)
        self.assertEqual(update_preview.target, state.target)
        self.assertEqual(
            update_preview.statements[0].parameters,
            (Decimal("2.50"), "updated note", "tenant-a", 0, Decimal("1.25"), "note 0"),
        )
        self.assertEqual(update_preview.statements[0].expected_rowcount, 1)
        select_count_before_apply = len(connection.select_sqls)
        with patch("ui.data_center.table_editor.ChangePreviewDialog.exec", return_value=QDialog.DialogCode.Accepted):
            host.table_editor.preview_button.click()
        self.assertTrue(self.update_entered.wait(3), "savepoint batch did not reach its controlled wait")
        self.assertTrue(state.busy)
        self.assertFalse(host.table_editor.next_button.isEnabled())
        self.assertFalse(host.table_editor.refresh_button.isEnabled())
        self.assertFalse(host.table_editor.insert_button.isEnabled())
        self.assertFalse(host.table_editor.preview_button.isEnabled())
        self.assertFalse(host.table_editor.discard_button.isEnabled())
        self.assertFalse(model.setData(model.index(0, 2), "9.99"))
        self.assertFalse(model.stage_insert({"tenant_id": "tenant-a", "order_id": 999, "amount": Decimal("1.00"), "note": "x"}))
        self.assertEqual(model.value_at(0, 2), Decimal("2.50"))
        self.update_gate.set()
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertFalse(model.has_staged_changes)
        self.assertTrue(model.page_stale)
        self.assertFalse(model.setData(model.index(0, 2), "3.00"))
        self.assertEqual(state.session.state, ManualSessionState.DIRTY)
        self.assertEqual(len(connection.update_sqls), 1)
        self.assertEqual(len(connection.select_sqls), select_count_before_apply)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 0)
        self.assertFalse(host._can_open_export_dialog(state))
        self.assertEqual(model.value_at(0, 2), original_value)

        # A deliberate refresh clears the stale-page guard; the tab owner keeps its transaction.
        host.refresh_table_source()
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertFalse(state.table_model.page_stale)
        self.assertEqual(len(connection.select_sqls), select_count_before_apply + 1)
        self.assertEqual(state.table_model.value_at(0, 2), Decimal("2.50"))
        host.commit_current()
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.CLEAN)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), 1)

        # A rowcount conflict keeps the local overlay and does not auto-refresh or commit.
        state.table_model.setData(state.table_model.index(0, 2), "4.00")
        conflict_preview = state.table_model.change_set.preview()
        connection.update_rowcount = 0
        select_count = len(connection.select_sqls)
        before_commit_count = sum(event[0] == "commit" for event in connection.events)
        host.apply_table_preview(conflict_preview)
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertTrue(state.table_model.has_staged_changes)
        self.assertEqual(state.last_error, "ROWCOUNT_CONFLICT")
        self.assertEqual(len(connection.select_sqls), select_count)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), before_commit_count)
        state.table_model.discard_staged()

        # If both DML and savepoint rollback fail, keep the overlay for review
        # and preserve the owner’s UNKNOWN transaction result.
        self.assertTrue(state.table_model.setData(state.table_model.index(0, 2), "5.00"))
        uncertain_preview = state.table_model.change_set.preview()
        connection.update_rowcount = 0
        connection.rollback_savepoint_failure = True
        before_select_count = len(connection.select_sqls)
        before_commit_count = sum(event[0] == "commit" for event in connection.events)
        host.apply_table_preview(uncertain_preview)
        self.assertTrue(_spin_until(lambda: not state.busy))
        self.assertEqual(state.session.state, ManualSessionState.UNKNOWN)
        self.assertTrue(state.table_model.has_staged_changes)
        self.assertEqual(state.last_error, "SAVEPOINT_ROLLBACK_FAILED")
        self.assertEqual(len(connection.select_sqls), before_select_count)
        self.assertEqual(sum(event[0] == "commit" for event in connection.events), before_commit_count)
        state.table_model.discard_staged()

    def test_manual_tab_scope_does_not_accept_unrepresentable_trimmed_database(self):
        # The signed identity is raw. A host that trims a boundary space must
        # reject it before creating a manual session or reaching its connector.
        self.tree_database = f" {_DATABASE} "
        node, target, _generation, table = self._live_table_item(expected_database=self.tree_database)
        self.assertTrue(node.actionable)
        normalized_choice = self.profile_host.capture_connection(self.profile, database=node.database)
        self.assertEqual(normalized_choice.database, _DATABASE)
        self.assertNotEqual(normalized_choice.database, node.database)
        index = self.panel.tree.proxy_model.mapFromSource(table.index())
        activation = QSignalSpy(self.panel.tree.directory_node_activated)
        self.panel.tree.scrollTo(index)
        QTest.mouseClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=self.panel.tree.visualRect(index).center())
        APP.processEvents()
        QTest.mouseDClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=self.panel.tree.visualRect(index).center())
        self.assertTrue(_spin_until(lambda: bool(self.panel._workspaces.get(_CONNECTION_ID))))
        self.assertEqual(len(activation), 1)
        self.assertEqual(activation[0], [_CONNECTION_ID, target, node, _generation])
        workspace = self.panel._workspaces[_CONNECTION_ID]
        host = workspace.host.manual_query_host
        self.assertEqual(target.database, "")
        self.assertEqual(self.manual_connections, [])
        self.assertFalse(any(state.table_scope is not None for state in host._states_by_tab.values()))

    def test_live_view_double_click_routes_to_fixed_scope_and_remains_read_only(self):
        node, target, generation, view = self._live_table_item(kind="view")
        activation = QSignalSpy(self.panel.tree.directory_node_activated)
        index = self.panel.tree.proxy_model.mapFromSource(view.index())
        self.panel.tree.scrollTo(index)
        rect = self.panel.tree.visualRect(index)
        self.assertTrue(rect.isValid() and not rect.isEmpty())
        self.assertEqual(self.panel.tree.indexAt(rect.center()).data(TREE_NODE_ROLE), node)
        QTest.mouseClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        APP.processEvents()
        QTest.mouseDClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertTrue(_spin_until(lambda: bool(self.panel._workspaces.get(_CONNECTION_ID))))
        self.assertEqual(len(activation), 1)
        self.assertEqual(activation[0], [_CONNECTION_ID, target, node, generation])

        workspace = self.panel._workspaces[_CONNECTION_ID]
        host = workspace.host.manual_query_host
        self.assertTrue(_spin_until(
            lambda: any(state.table_model is not None and not state.busy for state in host._states_by_tab.values()),
        ), "production ManualSession did not deliver a typed view page")
        state = next(state for state in host._states_by_tab.values() if state.table_model is not None)
        self.assertEqual(state.table_scope.node.kind, "view")
        self.assertEqual(state.table_scope.tree_generation, generation)
        self.assertEqual(state.target.profile_revision, target.profile_revision)
        self.assertEqual(state.target.database, _DATABASE)
        self.assertEqual(state.target.schema, _DATABASE)
        self.assertEqual(state.choice.database, _DATABASE)
        self.assertIs(state.table_scope.service, self.composition.service)
        self.assertFalse(state.table_model.can_edit)
        self.assertFalse(state.table_model.can_insert)
        self.assertIn("This view is read-only.", state.table_model.metadata.read_only_reason)
        self.assertEqual(len(self.manual_connections), 1)
        self.assertEqual(self.manual_connections[0].update_sqls, [])


class TableEditorOracleProductionRouteTests(unittest.TestCase):
    def setUp(self):
        _isolate_splitter_config(self)
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
        self.font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
        self.assertGreaterEqual(self.font_id, 0, "Microsoft YaHei UI test font must register for Windows QPA")
        from ui.theme_manager import ThemeManager
        self.theme_manager = ThemeManager.instance()
        self.theme_manager.apply(APP, "calm")
        self.profile = {
            "id": _ORACLE_CONNECTION_ID,
            "name": "TEMP Oracle table route",
            "provider": "oracle",
            "engine": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "host": "fixture.invalid",
            "port": 1521,
            "username": _ORACLE_LOGIN,
            "password": "TEMP_OPAQUE_TOKEN",
            "database": _ORACLE_SERVICE,
            "service_name": _ORACLE_SERVICE,
            "schema": _ORACLE_LOGIN,
            "environment": "test",
            "group_path": "TEMP/table-editor",
        }
        self.profile_host = DataCenterProfileHost(connections_loader=lambda: [dict(self.profile)])
        self.tree_connections = []
        self.manual_connections = []
        self.manual_connector_profiles = []

        def tree_connector(provider, profile, secret, **_options):
            self.assertEqual(provider, "oracle")
            self.assertEqual(profile["database"], _ORACLE_SERVICE)
            self.assertEqual(secret, "")
            connection = _OracleRouteConnection(tree_mode=True)
            self.tree_connections.append(connection)
            return connection

        relational = RelationalReadOnlyHost(
            connections={_ORACLE_CONNECTION_ID: dict(self.profile)},
            secret_loader=lambda _profile: "",
            connector=tree_connector,
        )
        self.composition = ObjectTreeComposition(relational_host=relational)

        def manual_connector(profile, secret):
            self.manual_connector_profiles.append(dict(profile))
            self.assertEqual(secret, "TEMP_ORACLE_SECRET")
            connection = _OracleRouteConnection()
            self.manual_connections.append(connection)
            return connection

        def secret_resolver(token):
            self.assertEqual(token, "TEMP_OPAQUE_TOKEN")
            return "TEMP_ORACLE_SECRET"

        def host_factory(profile, language):
            fixed_type = type("_TempOracleFixedSqlWorkbench", (_FixedSqlWorkbench, AiWorkbenchPanel), {})
            with (
                patch("panels.ai_workbench_panel.load_connections", return_value=[dict(profile)]),
                patch("panels.ai_workbench_panel.load_snapshot", return_value=None),
                patch("panels.ai_workbench_panel.is_enabled", return_value=False),
                patch("panels.ai_workbench_panel.load_ai_local", return_value={}),
                patch("panels.ai_workbench_panel.open_connection", side_effect=AssertionError("legacy connector used")),
                patch("panels.ai_workbench_panel.run_console_statement", side_effect=AssertionError("legacy console used")),
            ):
                panel = fixed_type(
                    language,
                    connection_id=_ORACLE_CONNECTION_ID,
                    dialect="oracle",
                    data_center_agent_options={"profile_host": self.profile_host},
                    manual_query_options={
                        "profile_host": self.profile_host,
                        "connector": manual_connector,
                        "secret_resolver": secret_resolver,
                        "page_size": 50,
                    },
                )
            panel.set_embedded_compact_mode(True)
            return panel

        catalog = WorkspaceCatalog(
            loader=lambda: [dict(self.profile)],
            saver=lambda item, secret=None: dict(item),
            deleter=lambda _connection_id: None,
        )
        self.panel = DataCenterPanel(
            "en",
            catalog=catalog,
            host_factory=host_factory,
            object_tree_composition_factory=lambda _connection_id: self.composition,
        )
        self.panel.resize(1160, 760)
        self.panel.show()
        APP.processEvents()

    def tearDown(self):
        if getattr(self, "panel", None) is not None:
            manual_hosts = []
            for workspace in tuple(self.panel._workspaces.values()):
                host = getattr(workspace.host, "manual_query_host", None)
                if host is not None:
                    manual_hosts.append(host)
                    for state in tuple(host._states_by_tab.values()):
                        if state.table_model is not None and state.table_model.has_staged_changes:
                            state.table_model.discard_staged()
                    host.manual_shutdown_cleanup("rollback")
            self.assertTrue(
                _spin_until(
                    lambda: all(_manual_host_cleanup_complete(host) for host in manual_hosts),
                    timeout=8,
                ),
                "manual owners did not reach READY with no busy or retained session before config isolation ended",
            )
            self.assertTrue(
                _spin_until(self.panel.workbench_shutdown_cleanup, timeout=8),
                "workbench/tree owners did not finish before config isolation ended",
            )
            self.assertFalse(self.panel._tree_owners, "tree owners must be released before Qt deferred deletion")
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.theme_manager.apply(APP, "calm")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        _restore_splitter_config(self)

    def test_signed_oracle_table_uses_captured_service_and_exact_owner_through_public_factory(self):
        base_choice = self.profile_host.capture_connection(self.profile, database=_ORACLE_SERVICE)
        item = self.panel.tree.connection_source_item(_ORACLE_CONNECTION_ID)
        self.assertIsNotNone(item)
        self.panel.tree.expand(self.panel.tree.proxy_model.mapFromSource(item.index()))
        self.assertTrue(_spin_until(lambda: bool(item.data(TREE_LOADED_ROLE))), "signed Oracle root did not load")
        schema_item = _tree_child(item, "schema")
        self.assertIsNotNone(schema_item)
        self.panel.tree.expand(self.panel.tree.proxy_model.mapFromSource(schema_item.index()))
        self.assertTrue(_spin_until(lambda: bool(schema_item.data(TREE_LOADED_ROLE))), "signed Oracle owner did not load")
        table_item = _tree_child(schema_item, "table")
        self.assertIsNotNone(table_item)
        node = table_item.data(TREE_NODE_ROLE)
        tree_target = table_item.data(TREE_TARGET_ROLE)
        tree_generation = int(table_item.data(TREE_GENERATION_ROLE) or 0)
        self.assertTrue(node.actionable)
        self.assertEqual(node.kind, "table")
        self.assertEqual(node.name, _ORACLE_TABLE)
        self.assertEqual(node.schema, _ORACLE_OWNER)
        self.assertEqual(node.database, _ORACLE_SERVICE)
        self.assertEqual(tree_target.database, _ORACLE_SERVICE)
        self.assertEqual(tree_target.provider, "oracle")
        self.assertEqual(tree_target.profile_revision, base_choice.revision)
        self.assertNotEqual(node.schema, self.profile["username"])

        activation = QSignalSpy(self.panel.tree.directory_node_activated)
        table_open_futures = []
        original_open_table_source = ManualSession.open_table_source

        def track_table_open(session, *args, **kwargs):
            future = original_open_table_source(session, *args, **kwargs)
            table_open_futures.append(future)
            return future

        index = self.panel.tree.proxy_model.mapFromSource(table_item.index())
        self.panel.tree.scrollTo(index)
        rect = self.panel.tree.visualRect(index)
        self.assertTrue(rect.isValid() and not rect.isEmpty())
        self.assertEqual(self.panel.tree.indexAt(rect.center()).data(TREE_NODE_ROLE), node)
        with patch.object(ManualSession, "open_table_source", track_table_open):
            QTest.mouseClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            APP.processEvents()
            QTest.mouseDClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertTrue(_spin_until(lambda: bool(self.panel._workspaces.get(_ORACLE_CONNECTION_ID))))
        self.assertEqual(len(activation), 1)
        self.assertEqual(activation[0], [_ORACLE_CONNECTION_ID, tree_target, node, tree_generation])

        workspace = self.panel._workspaces[_ORACLE_CONNECTION_ID]
        host = workspace.host.manual_query_host
        _spin_until(lambda: table_open_futures and table_open_futures[0].done())
        state_ready = _spin_until(lambda: any(not state.busy for state in host._states_by_tab.values()))
        completed_codes = []
        for future in table_open_futures:
            if not future.done():
                completed_codes.append("FUTURE_PENDING")
                continue
            try:
                future.result()
                completed_codes.append("OK")
            except Exception as error:
                completed_codes.append(str(getattr(error, "code", "UNEXPECTED_EXCEPTION")))
        owner_events = [event[0] for connection in self.manual_connections for event in connection.events]
        owner_sql_events = [
            (event[1], event[2]) for connection in self.manual_connections for event in connection.events
            if event[0] == "execute"
        ]
        self.assertTrue(
            state_ready and any(state.table_model is not None for state in host._states_by_tab.values()),
            "production Oracle ManualSession did not deliver a typed table page: "
            f"status={getattr(self.panel, 'result_status', None).text() if getattr(self.panel, 'result_status', None) is not None else ''!r}, "
            f"future_codes={completed_codes!r}, manual_connectors={len(self.manual_connections)}, "
            f"owner_events={owner_events!r}, owner_sql_events={owner_sql_events!r}, "
            f"states={[(state.last_error, state.active_operation, state.busy) for state in host._states_by_tab.values()]!r}",
        )
        state = next(state for state in host._states_by_tab.values() if state.table_model is not None)
        self.assertEqual(state.choice.connection_id, _ORACLE_CONNECTION_ID)
        self.assertEqual(state.choice.revision, base_choice.revision)
        self.assertEqual(state.choice.database, _ORACLE_SERVICE)
        self.assertEqual(state.choice.schema_allowlist, (_ORACLE_OWNER,))
        self.assertEqual(state.target.profile_revision, tree_target.profile_revision)
        self.assertEqual(state.target.database, _ORACLE_SERVICE)
        self.assertEqual(state.target.schema, _ORACLE_OWNER)
        self.assertNotEqual(state.target.database, state.target.schema)
        self.assertEqual(state.table_scope.verified_ref.schema, _ORACLE_OWNER)
        self.assertEqual(state.table_scope.verified_ref.database, _ORACLE_SERVICE)
        self.assertIs(state.table_scope.service, self.composition.service)
        self.assertEqual(state.table_model.rowCount(), 2)
        self.assertTrue(state.table_model.can_edit)
        self.assertEqual(state.table_model.value_at(0, 1), Decimal("12.50"))

        self.assertEqual(len(self.manual_connector_profiles), 1)
        manual_profile = self.manual_connector_profiles[0]
        self.assertEqual(manual_profile["database"], _ORACLE_SERVICE)
        self.assertEqual(manual_profile["schema"], _ORACLE_LOGIN)
        self.assertEqual(manual_profile["username"], _ORACLE_LOGIN)
        self.assertEqual(self.profile["database"], _ORACLE_SERVICE)
        self.assertEqual(self.profile["schema"], _ORACLE_LOGIN)
        self.assertEqual(self.profile["username"], _ORACLE_LOGIN)
        self.assertEqual(
            self.profile_host.capture_connection(self.profile, database=_ORACLE_SERVICE).revision,
            base_choice.revision,
        )

        connection = self.manual_connections[0]
        self.assertEqual(connection.current_schema, _ORACLE_OWNER)
        manual_events = [event for event in connection.events if event[0] == "execute"]
        sql_statements = [event[1] for event in manual_events]
        self.assertIn(f'ALTER SESSION SET CURRENT_SCHEMA = "{_ORACLE_OWNER}"', sql_statements)
        metadata_event = next(event for event in manual_events if "select object_type from all_objects" in event[1].casefold())
        self.assertEqual(metadata_event[2], (_ORACLE_OWNER, _ORACLE_TABLE))
        table_select = next(
            event[1] for event in manual_events
            if f'from "{_ORACLE_OWNER}"."{_ORACLE_TABLE}"'.casefold() in event[1].casefold()
        )
        self.assertNotIn(_ORACLE_SERVICE, table_select)
        owner_threads = {event[-1] for event in manual_events}
        self.assertEqual(len(owner_threads), 1)
        self.assertNotEqual(next(iter(owner_threads)), threading.get_ident())


if __name__ == "__main__":
    unittest.main()
