from __future__ import annotations

from datetime import date
from decimal import Decimal
import threading
import unittest

from tools.data_center.changeset import (
    ChangeSet,
    ChangeSetError,
    EditableSourceKind,
    QmarkSqlDialect,
    READ_ONLY_REASONS,
    VerifiedColumn,
    VerifiedTableMetadata,
    readonly_reason,
)
from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionTarget,
    ManualSessionState,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)


class _Cursor:
    description = ()

    def __init__(self, events, rowcount=1):
        self.events = events
        self.rowcount = rowcount

    def fetchmany(self, size):
        self.events.append(("fetchmany", threading.get_ident(), size))
        return ()

    def close(self):
        self.events.append(("cursor_close", threading.get_ident()))


class _Driver:
    def __init__(self, *, rowcounts=None, block_execute=None):
        self.events = []
        self.commit_count = 0
        self.rowcounts = dict(rowcounts or {})
        self.block_execute = block_execute

    def configure_manual_commit(self):
        self.events.append(("configure", threading.get_ident()))

    def create_savepoint(self, name):
        self.events.append(("savepoint", threading.get_ident(), name))

    def execute(self, sql, parameters):
        self.events.append(("execute", threading.get_ident(), sql, parameters))
        if self.block_execute is not None:
            entered, release = self.block_execute
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test execute unblock timed out")
        return _Cursor(self.events, self.rowcounts.get(sql, 1))

    def rollback_to_savepoint(self, name):
        self.events.append(("rollback_to_savepoint", threading.get_ident(), name))

    def release_savepoint(self, name):
        self.events.append(("release_savepoint", threading.get_ident(), name))

    def commit(self):
        self.events.append(("commit", threading.get_ident()))
        self.commit_count += 1

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.events.append(("connection_close", threading.get_ident()))


class ChangeSetVerticalTests(unittest.TestCase):
    @staticmethod
    def _target(**overrides):
        values = {
            "tab_id": "tab-a",
            "connection_id": "conn-a",
            "profile_revision": "profile-r3",
            "database": "db",
            "schema": "schema",
            "generation": 4,
        }
        values.update(overrides)
        return ManualSessionTarget(**values)

    @staticmethod
    def _metadata(target=None, *, columns=None, primary_key=("tenant_id", "item_id"), source=EditableSourceKind.VERIFIED_TABLE):
        return VerifiedTableMetadata(
            target=target or ChangeSetVerticalTests._target(),
            table="items",
            columns=columns or (
                VerifiedColumn("tenant_id"),
                VerifiedColumn("item_id"),
                VerifiedColumn("name"),
                VerifiedColumn("amount"),
            ),
            primary_key=primary_key,
            source_kind=source,
        )

    @staticmethod
    def _make_session(target, driver):
        factory_threads = []

        def factory(scope):
            factory_threads.append((threading.get_ident(), scope))
            return driver

        def classify(_sql):
            return StatementClassification(StatementKind.DML, TransactionEffect.DIRTY)

        return ManualSession(target, factory, classify), factory_threads

    def test_composite_key_update_preview_savepoint_then_explicit_commit(self):
        target = self._target()
        metadata = self._metadata(target)
        changes = ChangeSet(metadata, QmarkSqlDialect())
        changed = changes.stage_update(
            primary_key={"tenant_id": "tenant-1", "item_id": 7},
            original_values={"name": "old", "amount": Decimal("1.00")},
            new_values={"name": "", "amount": Decimal("2.50")},
        )
        self.assertTrue(changed)
        preview = changes.preview()
        statement = preview.statements[0]
        self.assertEqual(
            statement.sql,
            'UPDATE "db"."schema"."items" SET "name" = ?, "amount" = ? '
            'WHERE "tenant_id" = ? AND "item_id" = ? AND "name" = ? AND "amount" = ?',
        )
        self.assertEqual(
            statement.parameters,
            ("", Decimal("2.50"), "tenant-1", 7, "old", Decimal("1.00")),
        )
        self.assertEqual(statement.expected_rowcount, 1)

        driver = _Driver()
        caller_thread = threading.get_ident()
        session, factory_threads = self._make_session(target, driver)
        self.assertEqual(factory_threads, [])
        applied = changes.apply(session, preview).result(timeout=3)
        self.assertTrue(applied.succeeded)
        self.assertEqual(applied.rowcounts, (1,))
        self.assertEqual(applied.state, ManualSessionState.DIRTY)
        self.assertEqual(session.state, ManualSessionState.DIRTY)
        self.assertEqual(changes.staged_count, 0)
        self.assertEqual(driver.commit_count, 0)
        self.assertEqual(factory_threads, [(session._owner_ident, target)])
        self.assertNotEqual(session._owner_ident, caller_thread)
        self.assertEqual(driver.events[2][0], "execute")
        self.assertEqual(driver.events[2][2], statement.sql)
        self.assertEqual(driver.events[2][3], statement.parameters)
        self.assertTrue(all(event[1] == session._owner_ident for event in driver.events))

        committed = session.commit().result(timeout=3)
        self.assertEqual(committed.state, ManualSessionState.CLEAN)
        self.assertEqual(driver.commit_count, 1)
        session.close().result(timeout=3)

    def test_null_is_is_null_empty_string_and_typed_parameters_are_preserved(self):
        target = self._target()
        metadata = self._metadata(
            target,
            columns=(
                VerifiedColumn("tenant_id"),
                VerifiedColumn("item_id"),
                VerifiedColumn("memo"),
                VerifiedColumn("label"),
                VerifiedColumn("amount"),
                VerifiedColumn("created_on"),
                VerifiedColumn("payload"),
            ),
        )
        changes = ChangeSet(metadata, QmarkSqlDialect())
        changes.stage_update(
            {"tenant_id": "t", "item_id": 2},
            {"memo": None, "label": "", "amount": Decimal("1.00"), "created_on": date(2025, 1, 2), "payload": b"\x00"},
            {"label": "new"},
        )
        preview = changes.preview().statements[0]
        self.assertIn('"memo" IS NULL', preview.sql)
        self.assertIn('"label" = ?', preview.sql)
        self.assertEqual(preview.parameters, ("new", "t", 2, "", Decimal("1.00"), date(2025, 1, 2), b"\x00"))
        self.assertNotIn("None", preview.sql)
        self.assertNotIn("new", preview.sql)

    def test_equal_values_stage_nothing_and_full_parameterized_insert_delete_preview(self):
        target = self._target()
        changes = ChangeSet(self._metadata(target), QmarkSqlDialect())
        self.assertFalse(changes.stage_update(
            {"tenant_id": "t", "item_id": 1},
            {"name": "same", "amount": Decimal("1.0")},
            {"name": "same", "amount": Decimal("1.00")},
        ))
        self.assertEqual(changes.staged_count, 0)
        changes.stage_insert({"tenant_id": "t", "item_id": 2, "name": "O'Reilly", "amount": Decimal("2.00")})
        changes.stage_delete({"tenant_id": "t", "item_id": 3}, {"name": "old", "amount": Decimal("3.00")})
        insert, delete = changes.preview().statements
        self.assertEqual(insert.operation, "insert")
        self.assertEqual(insert.parameters, ("t", 2, "O'Reilly", Decimal("2.00")))
        self.assertNotIn("O'Reilly", insert.sql)
        self.assertEqual(delete.operation, "delete")
        self.assertIn('"tenant_id" = ?', delete.sql)
        self.assertEqual(delete.parameters, ("t", 3, "old", Decimal("3.00")))

    def test_missing_key_unknown_columns_and_unsafe_or_readonly_metadata_are_rejected(self):
        target = self._target()
        with self.assertRaises(ChangeSetError) as no_pk:
            self._metadata(target, primary_key=())
        self.assertEqual(no_pk.exception.code, "VERIFIED_PRIMARY_KEY_REQUIRED")
        with self.assertRaises(ChangeSetError) as unsafe:
            self._metadata(target, columns=(VerifiedColumn("id\x00DROP"),), primary_key=("id\x00DROP",))
        self.assertEqual(unsafe.exception.code, "UNSAFE_IDENTIFIER")
        with self.assertRaises(ChangeSetError) as too_long:
            VerifiedColumn("x" * 257)
        self.assertEqual(too_long.exception.code, "UNSAFE_IDENTIFIER")
        for source in (EditableSourceKind.QUERY_RESULT, EditableSourceKind.JOIN, EditableSourceKind.AGGREGATE, EditableSourceKind.UNVERIFIED):
            with self.subTest(source=source):
                with self.assertRaises(ChangeSetError) as readonly:
                    self._metadata(target, source=source)
                self.assertEqual(readonly.exception.code, "SOURCE_NOT_EDITABLE")
                self.assertEqual(readonly.exception.reason, READ_ONLY_REASONS[source])
                self.assertEqual(readonly_reason(source), readonly.exception.reason)
        changes = ChangeSet(self._metadata(target), QmarkSqlDialect())
        with self.assertRaises(ChangeSetError) as missing_key:
            changes.stage_update({"tenant_id": "t"}, {"name": "old", "amount": Decimal(1)}, {"name": "new"})
        self.assertEqual(missing_key.exception.code, "COMPLETE_PRIMARY_KEY_REQUIRED")
        with self.assertRaises(ChangeSetError) as unknown:
            changes.stage_update({"tenant_id": "t", "item_id": 1}, {"name": "old", "amount": Decimal(1)}, {"not_a_column": "x"})
        self.assertEqual(unknown.exception.code, "UNKNOWN_COLUMN")

    def test_qmark_identifier_quote_escapes_embedded_double_quotes(self):
        self.assertEqual(QmarkSqlDialect().quote_identifier('列 名"quoted'), '"列 名""quoted"')

    def test_stale_confirmation_and_wrong_generation_are_rejected_before_io(self):
        target = self._target()
        changes = ChangeSet(self._metadata(target), QmarkSqlDialect())
        changes.stage_update({"tenant_id": "t", "item_id": 1}, {"name": "old", "amount": Decimal(1)}, {"name": "new"})
        confirmed = changes.preview()
        changes.stage_update({"tenant_id": "t", "item_id": 2}, {"name": "old2", "amount": Decimal(2)}, {"name": "new2"})
        driver = _Driver()
        session, factories = self._make_session(target, driver)
        with self.assertRaises(ChangeSetError) as stale:
            changes.apply(session, confirmed)
        self.assertEqual(stale.exception.code, "PREVIEW_STALE")
        self.assertEqual(factories, [])

        other_target = self._target(generation=5)
        other_session, other_factories = self._make_session(other_target, _Driver())
        with self.assertRaises(ChangeSetError) as mismatch:
            changes.apply(other_session, changes.preview())
        self.assertEqual(mismatch.exception.code, "SESSION_TARGET_MISMATCH")
        self.assertEqual(other_factories, [])
        session.close().result(timeout=3)
        other_session.close().result(timeout=3)

    def test_rowcount_conflict_keeps_changes_and_invalidates_prior_confirmation(self):
        target = self._target()
        changes = ChangeSet(self._metadata(target), QmarkSqlDialect())
        changes.stage_update({"tenant_id": "t", "item_id": 1}, {"name": "old", "amount": Decimal(1)}, {"name": "new"})
        confirmed = changes.preview()
        driver = _Driver(rowcounts={confirmed.statements[0].sql: 0})
        session, _ = self._make_session(target, driver)
        result = changes.apply(session, confirmed).result(timeout=3)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error_code, "ROWCOUNT_CONFLICT")
        self.assertEqual(result.actual_rowcount, 0)
        self.assertEqual(changes.staged_count, 1)
        with self.assertRaises(ChangeSetError) as stale:
            changes.apply(session, confirmed)
        self.assertEqual(stale.exception.code, "PREVIEW_STALE")
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 1)
        self.assertEqual(driver.events.count(next(event for event in driver.events if event[0] == "rollback_to_savepoint")), 1)
        session.close().result(timeout=3)

    def test_busy_changeset_rejects_edits_discard_and_reapply_until_batch_finishes(self):
        target = self._target()
        changes = ChangeSet(self._metadata(target), QmarkSqlDialect())
        changes.stage_update({"tenant_id": "t", "item_id": 1}, {"name": "old", "amount": Decimal(1)}, {"name": "new"})
        confirmed = changes.preview()
        entered, release = threading.Event(), threading.Event()
        driver = _Driver(block_execute=(entered, release))
        session, _ = self._make_session(target, driver)
        applied = changes.apply(session, confirmed)
        self.assertTrue(entered.wait(2))
        self.assertTrue(changes.busy)
        with self.assertRaises(ChangeSetError) as stage_error:
            changes.stage_update({"tenant_id": "t", "item_id": 2}, {"name": "old2", "amount": Decimal(2)}, {"name": "new2"})
        self.assertEqual(stage_error.exception.code, "CHANGESET_BUSY")
        with self.assertRaises(ChangeSetError) as discard_error:
            changes.discard()
        self.assertEqual(discard_error.exception.code, "CHANGESET_BUSY")
        with self.assertRaises(ChangeSetError) as apply_error:
            changes.apply(session, confirmed)
        self.assertEqual(apply_error.exception.code, "CHANGESET_BUSY")
        release.set()
        self.assertTrue(applied.result(timeout=3).succeeded)
        self.assertFalse(changes.busy)
        self.assertEqual(changes.staged_count, 0)
        self.assertEqual(len([event for event in driver.events if event[0] == "execute"]), 1)
        session.close(disposition="rollback").result(timeout=3)


if __name__ == "__main__":
    unittest.main()
