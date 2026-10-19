from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
import importlib.util
import threading
import unittest

from tools.data_center.changeset import (
    ChangeSet,
    ChangeSetError,
    EditableSourceKind,
    VerifiedColumn,
    VerifiedTableMetadata,
)
from tools.data_center.changeset_dialects import create_changeset_dialect
from tools.data_center.manual_driver import (
    ManualRelationalDriver,
    classify_relational_statement,
    profile_schema_for_provider,
)
from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionState,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.profile_host import ConnectionChoice
from tools.data_center.readonly_relational_connectors import _OceanBaseOdbcConnection


_PYMYSQL_AVAILABLE = importlib.util.find_spec("pymysql") is not None


class _FakeCursor:
    description = ()

    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", threading.get_ident(), sql, parameters))

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", threading.get_ident(), size))
        return ()

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _FakeConnection:
    def __init__(self):
        self.events = []
        self.autocommit = True
        self.closed = False

    def cursor(self):
        self.events.append(("cursor", threading.get_ident()))
        return _FakeCursor(self)

    def commit(self):
        self.events.append(("commit", threading.get_ident()))

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.events.append(("connection_close", threading.get_ident()))
        self.closed = True


class _MogrifyOnlyConnection:
    """The PyMySQL cursor-formatting surface; no socket/DB connection exists."""

    @staticmethod
    def literal(value):
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, Decimal)):
            return str(value)
        if isinstance(value, bytes):
            return "X'" + value.hex() + "'"
        if isinstance(value, date):
            value = value.isoformat()
        text = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return "'" + text + "'"


class ChangeSetDialectTests(unittest.TestCase):
    @staticmethod
    def _bound(provider, mode, dialect, database="db", schema="schema", *, tab_id="tab-a"):
        choice = ConnectionChoice("conn-a", "revision-a", provider, mode, dialect, database)
        target = ManualSessionTarget(
            tab_id=tab_id,
            connection_id=choice.connection_id,
            profile_revision=choice.revision,
            database=database,
            schema=schema,
            generation=3,
        )
        return choice, target

    @staticmethod
    def _metadata(target, *, table, columns, primary_key=("id",)):
        return VerifiedTableMetadata(
            target=target,
            table=table,
            columns=tuple(VerifiedColumn(name) for name in columns),
            primary_key=tuple(primary_key),
            source_kind=EditableSourceKind.VERIFIED_TABLE,
        )

    @staticmethod
    def _apply_with_real_manual_adapter(choice, target, changes, preview, *, odbc=False):
        raw_connection = _FakeConnection()
        connection = (
            _OceanBaseOdbcConnection(raw_connection, "OceanBase ODBC test")
            if odbc else raw_connection
        )
        provider = choice.provider
        driver = ManualRelationalDriver(connection, provider)

        def classify(sql):
            return classify_relational_statement(sql, provider)

        session = ManualSession(target, lambda _target: driver, classify)
        result = changes.apply(session, preview).result(timeout=3)
        return session, raw_connection, result

    def test_mysql_and_oceanbase_mysql_use_percent_s_positional_parameters(self):
        for provider in ("mysql", "oceanbase_mysql"):
            with self.subTest(provider=provider):
                choice, target = self._bound(provider, "mysql", "mysql", "sales db", "sales db")
                policy = create_changeset_dialect(choice, target)
                metadata = self._metadata(
                    target,
                    table="record%.archive{bind}",
                    columns=("id", "rate%s`", "memo", "changed_on", "payload", "empty_value"),
                )
                changes = ChangeSet(metadata, policy)
                hostile_value = "O'Reilly %s'); DROP TABLE accounts; --"
                changes.stage_update(
                    {"id": 7},
                    {"rate%s`": Decimal("1.00"), "memo": None, "changed_on": date(2025, 1, 2), "payload": b"\x00", "empty_value": ""},
                    {"rate%s`": hostile_value},
                )
                preview = changes.preview()
                statement = preview.statements[0]
                self.assertTrue(statement.sql.startswith("UPDATE `sales db`.`record%%.archive{bind}` SET "))
                self.assertIn("`rate%%s``` = %s", statement.sql)
                self.assertIn("`memo` IS NULL", statement.sql)
                self.assertNotIn("DROP TABLE", statement.sql)
                self.assertEqual(
                    statement.parameters,
                    (hostile_value, 7, Decimal("1.00"), date(2025, 1, 2), b"\x00", ""),
                )

                session, raw_connection, applied = self._apply_with_real_manual_adapter(
                    choice, target, changes, preview
                )
                self.assertTrue(applied.succeeded)
                self.assertEqual(applied.state, ManualSessionState.DIRTY)
                executed = [event for event in raw_connection.events if event[0] == "execute"]
                update = next(event for event in executed if event[2].startswith("UPDATE "))
                self.assertEqual(update[2], statement.sql)
                self.assertIsInstance(update[3], tuple)
                self.assertEqual(update[3], statement.parameters)
                self.assertEqual([event[0] for event in executed].count("commit"), 0)
                self.assertEqual(session.commit().result(timeout=3).state, ManualSessionState.CLEAN)
                session.close().result(timeout=3)

    @unittest.skipUnless(_PYMYSQL_AVAILABLE, "PyMySQL is not installed; no network install attempted")
    def test_pymysql_mogrify_escapes_identifier_percents_without_a_connection(self):
        import pymysql.cursors

        choice, target = self._bound("mysql", "mysql", "mysql", "sales db", "sales db")
        metadata = self._metadata(
            target,
            table="record%.archive{bind}",
            columns=("id", "rate%s`", "memo"),
        )
        changes = ChangeSet(metadata, create_changeset_dialect(choice, target))
        value = "O'Reilly %s'); DROP TABLE accounts; --"
        changes.stage_update({"id": 7}, {"rate%s`": Decimal("1.00"), "memo": None}, {"rate%s`": value})
        statement = changes.preview().statements[0]
        query = pymysql.cursors.Cursor(_MogrifyOnlyConnection()).mogrify(statement.sql, statement.parameters)
        self.assertIn("`record%.archive{bind}`", query)
        self.assertIn("`rate%s```", query)
        self.assertIn("O\\'Reilly %s\\'); DROP TABLE accounts; --' WHERE", query)
        self.assertNotIn("%%", query)

    def test_oracle_uses_named_mapping_and_only_the_captured_schema(self):
        choice, target = self._bound("oracle", "oracle", "oracle", "billing-service", 'Owner Space')
        policy = create_changeset_dialect(choice, target)
        metadata = self._metadata(
            target,
            table="invoice{bind}.archive",
            columns=("id", 'display"name', "memo", "amount", "changed_on", "payload", "empty_value"),
        )
        changes = ChangeSet(metadata, policy)
        hostile_value = "x'); DROP TABLE invoices; --"
        changes.stage_update(
            {"id": 4},
            {"display\"name": "old", "memo": None, "amount": Decimal("1.25"), "changed_on": date(2025, 5, 6), "payload": b"\xff", "empty_value": ""},
            {"display\"name": hostile_value, "amount": Decimal("9.50")},
        )
        statement = changes.preview().statements[0]
        self.assertTrue(statement.sql.startswith('UPDATE "Owner Space"."invoice{bind}.archive" SET '))
        self.assertNotIn("billing-service", statement.sql)
        self.assertIn('"display""name" = :p0', statement.sql)
        self.assertIn('"memo" IS NULL', statement.sql)
        self.assertNotIn("DROP TABLE", statement.sql)
        self.assertIsInstance(statement.parameters, Mapping)
        self.assertEqual(statement.parameters, {
            "p0": hostile_value,
            "p1": Decimal("9.50"),
            "p2": 4,
            "p3": "old",
            "p4": Decimal("1.25"),
            "p5": date(2025, 5, 6),
            "p6": b"\xff",
            "p7": "",
        })
        session, raw_connection, applied = self._apply_with_real_manual_adapter(
            choice, target, changes, changes.preview()
        )
        self.assertTrue(applied.succeeded)
        self.assertEqual(applied.rowcounts, (1,))
        self.assertEqual(applied.state, ManualSessionState.DIRTY)
        update = next(
            event for event in raw_connection.events
            if event[0] == "execute" and event[2].startswith("UPDATE ")
        )
        self.assertEqual(update[2], statement.sql)
        self.assertIsInstance(update[3], dict)
        self.assertEqual(update[3], statement.parameters)
        self.assertEqual(session.commit().result(timeout=3).state, ManualSessionState.CLEAN)
        session.close().result(timeout=3)

    def test_oceanbase_oracle_uses_numeric_tuple_binds_and_existing_odbc_wrapper(self):
        choice, target = self._bound(
            "oceanbase_oracle", "oracle", "oracle", "ob-service", 'Owner"Quoted'
        )
        policy = create_changeset_dialect(choice, target)
        metadata = self._metadata(
            target,
            table="object.records",
            columns=("id", 'label"col', "memo", "amount", "changed_on", "payload", "empty_value"),
        )
        changes = ChangeSet(metadata, policy)
        hostile_value = "'); DROP TABLE t; --"
        changes.stage_update(
            {"id": 9},
            {"label\"col": "old", "memo": None, "amount": Decimal("2.00"), "changed_on": date(2026, 1, 2), "payload": b"\x00\xff", "empty_value": ""},
            {"label\"col": hostile_value, "amount": Decimal("3.75")},
        )
        preview = changes.preview()
        statement = preview.statements[0]
        self.assertTrue(statement.sql.startswith('UPDATE "Owner""Quoted"."object.records" SET '))
        self.assertIn('"label""col" = :1', statement.sql)
        self.assertIn('"memo" IS NULL', statement.sql)
        self.assertNotIn("DROP TABLE", statement.sql)
        self.assertIsInstance(statement.parameters, tuple)
        self.assertEqual(statement.parameters, (
            hostile_value, Decimal("3.75"), 9, "old", Decimal("2.00"), date(2026, 1, 2), b"\x00\xff", "",
        ))
        session, raw_connection, applied = self._apply_with_real_manual_adapter(
            choice, target, changes, preview, odbc=True
        )
        self.assertTrue(applied.succeeded)
        update = next(
            event for event in raw_connection.events
            if event[0] == "execute" and event[2].startswith("UPDATE ")
        )
        self.assertEqual(update[2].count("?"), len(statement.parameters))
        self.assertNotIn(":1", update[2])
        self.assertEqual(update[3], statement.parameters)
        self.assertEqual(applied.state, ManualSessionState.DIRTY)
        self.assertEqual(session.commit().result(timeout=3).state, ManualSessionState.CLEAN)
        session.close().result(timeout=3)

    def test_bind_collisions_in_ob_oracle_identifiers_fail_before_execution(self):
        choice, target = self._bound("oceanbase_oracle", "oracle", "oracle", "service", "OWNER")
        for table in ("table:1", 'table"quoted:2'):
            with self.subTest(table=table):
                metadata = self._metadata(
                    target, table=table, columns=("id", "value"),
                )
                changes = ChangeSet(metadata, create_changeset_dialect(choice, target))
                changes.stage_update({"id": 1}, {"value": "old"}, {"value": "new"})
                with self.assertRaises(ChangeSetError) as error:
                    changes.preview()
                self.assertEqual(error.exception.code, "ODBC_IDENTIFIER_BIND_COLLISION")

    def test_identifiers_allow_unicode_spaces_quotes_dots_and_bind_marker_text(self):
        choice, target = self._bound("oracle", "oracle", "oracle", "service", "Owner 零")
        metadata = self._metadata(
            target,
            table="名称.with space{bind}",
            columns=("id", '列 名"quoted', "old{bind}"),
        )
        changes = ChangeSet(metadata, create_changeset_dialect(choice, target))
        changes.stage_update({"id": 1}, {'列 名"quoted': "before", "old{bind}": "stable"}, {'列 名"quoted': "after"})
        sql = changes.preview().statements[0].sql
        self.assertIn('"名称.with space{bind}"', sql)
        self.assertIn('"列 名""quoted" = :p0', sql)
        self.assertIn('"old{bind}" = :p3', sql)
        self.assertNotIn("service.", sql)

    def test_sql_looking_identifier_is_one_quoted_component(self):
        choice, target = self._bound("mysql", "mysql", "mysql", "db", "db")
        table_name = "orders; DROP TABLE victims--"
        metadata = self._metadata(target, table=table_name, columns=("id", "value"))
        changes = ChangeSet(metadata, create_changeset_dialect(choice, target))
        changes.stage_update({"id": 3}, {"value": "old"}, {"value": "user value'; DELETE"})
        statement = changes.preview().statements[0]
        self.assertTrue(statement.sql.startswith("UPDATE `db`.`orders; DROP TABLE victims--` SET "))
        self.assertEqual(statement.sql.count("UPDATE "), 1)
        self.assertNotIn("user value", statement.sql)

    def test_oracle_profile_owner_flows_as_raw_identifier_without_database_guess(self):
        profile = {
            "id": "conn-a",
            "provider": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "username": "login_owner",
            "schema": '"AppOwner"',
            "service_name": "billing-service",
        }
        schema = profile_schema_for_provider(profile, "oracle")
        self.assertEqual(schema, "AppOwner")
        choice = ConnectionChoice("conn-a", "revision-a", "oracle", "oracle", "oracle", "billing-service")
        target = ManualSessionTarget("tab-a", "conn-a", "revision-a", "billing-service", schema, 3)
        policy = create_changeset_dialect(choice, target)
        self.assertEqual(policy.qualified_table(target, "invoice.records"), '"AppOwner"."invoice.records"')
        self.assertNotIn("billing-service", policy.qualified_table(target, "invoice.records"))

    def test_provider_target_mode_and_scope_mismatches_fail_closed(self):
        choice, target = self._bound("mysql", "mysql", "mysql", "sales", "sales")
        wrong_target = ManualSessionTarget(
            "other-tab", "other-connection", target.profile_revision,
            "sales", "sales", target.generation,
        )
        with self.assertRaises(ChangeSetError) as target_error:
            create_changeset_dialect(choice, wrong_target)
        self.assertEqual(target_error.exception.code, "DIALECT_TARGET_MISMATCH")
        policy = create_changeset_dialect(choice, target)
        wrong_tab = ManualSessionTarget(
            "other-tab", target.connection_id, target.profile_revision,
            target.database, target.schema, target.generation,
        )
        with self.assertRaises(ChangeSetError) as policy_target_error:
            policy.qualified_table(wrong_tab, "items")
        self.assertEqual(policy_target_error.exception.code, "DIALECT_TARGET_MISMATCH")

        bad_mode = ConnectionChoice("conn-a", "revision-a", "oceanbase_oracle", "mysql", "mysql", "sales")
        with self.assertRaises(ChangeSetError) as mode_error:
            create_changeset_dialect(bad_mode, target)
        self.assertEqual(mode_error.exception.code, "UNSUPPORTED_SQL_DIALECT")

        bad_database, bad_target = self._bound("mysql", "mysql", "mysql", "db-one", "db-two")
        with self.assertRaises(ChangeSetError) as scope_error:
            create_changeset_dialect(bad_database, bad_target)
        self.assertEqual(scope_error.exception.code, "MYSQL_SCHEMA_DATABASE_MISMATCH")

        empty_database, empty_target = self._bound("mysql", "mysql", "mysql", "", "")
        with self.assertRaises(ChangeSetError) as missing_database:
            create_changeset_dialect(empty_database, empty_target)
        self.assertEqual(missing_database.exception.code, "MYSQL_DATABASE_REQUIRED")

        dameng, dm_target = self._bound("dameng", "dameng", "oracle", "dm-service", "OWNER")
        with self.assertRaises(ChangeSetError) as unsupported:
            create_changeset_dialect(dameng, dm_target)
        self.assertEqual(unsupported.exception.code, "UNSUPPORTED_SQL_DIALECT")


if __name__ == "__main__":
    unittest.main()
