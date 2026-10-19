from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import tools.data_center.workspace_store as workspace_store_module
from tools.data_center.workspace_store import (
    DraftRecord,
    HistoryEvidenceSummary,
    HistoryRecord,
    MAX_HISTORY_EVIDENCE_ITEMS,
    MAX_HISTORY_ROW_COUNT,
    MAX_SEARCH_CHARS,
    MAX_TEXT_BYTES,
    WorkspaceLayout,
    WorkspaceStore,
    WorkspaceStoreError,
    WorkspaceTarget,
    WorkspaceTabKind,
    WorkspaceTextKind,
)


class WorkspaceStoreTests(unittest.TestCase):
    @staticmethod
    def _target(suffix="1"):
        return WorkspaceTarget(f"tab-{suffix}", f"conn-{suffix}", "profile-r7", "sample_db", "public")

    @classmethod
    def _history(
        cls,
        history_id,
        text,
        *,
        kind=WorkspaceTextKind.SQL,
        connection="1",
        row_count=None,
        evidence_summary=(),
    ):
        return HistoryRecord(
            history_id,
            WorkspaceTarget(
                f"tab-{connection}", f"conn-{connection}", "profile-r7", "sample_db", "public",
                WorkspaceTabKind.SQL if kind is WorkspaceTextKind.SQL else WorkspaceTabKind.REDIS,
            ),
            text,
            kind,
            created_at="2026-10-01T09:30:00+08:00",
            state_code="SUCCESS",
            row_count=row_count,
            evidence_summary=evidence_summary,
        )

    @staticmethod
    def _create_v1_database(path: Path, *, malformed: bool = False) -> None:
        connection = sqlite3.connect(path)
        try:
            history_schema = """CREATE TABLE dc_workspace_history (
                history_id TEXT PRIMARY KEY,
                text_kind TEXT NOT NULL CHECK (text_kind IN ('sql', 'nosql')),
                body TEXT NOT NULL,
                tab_id TEXT NOT NULL,
                tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                connection_id TEXT NOT NULL,
                profile_revision TEXT NOT NULL,
                database_name TEXT NOT NULL,
                schema_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                state_code TEXT NOT NULL,
                is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1))
            )"""
            if malformed:
                history_schema = history_schema.replace(
                    "schema_name TEXT NOT NULL,", "renamed_schema TEXT NOT NULL,"
                )
            connection.execute(
                """CREATE TABLE dc_workspace_drafts (
                    draft_id TEXT PRIMARY KEY,
                    text_kind TEXT NOT NULL CHECK (text_kind IN ('sql', 'nosql')),
                    body TEXT NOT NULL,
                    tab_id TEXT NOT NULL,
                    tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                    connection_id TEXT NOT NULL,
                    profile_revision TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_code TEXT NOT NULL
                )"""
            )
            connection.execute(history_schema)
            connection.execute(
                "CREATE INDEX dc_workspace_history_created ON dc_workspace_history(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX dc_workspace_history_connection ON dc_workspace_history(connection_id, created_at DESC)"
            )
            connection.execute(
                """CREATE TABLE dc_workspace_layout (
                    position INTEGER PRIMARY KEY,
                    tab_id TEXT NOT NULL UNIQUE,
                    tab_kind TEXT NOT NULL CHECK (tab_kind IN ('sql', 'redis', 'mongo')),
                    connection_id TEXT NOT NULL,
                    profile_revision TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE dc_workspace_layout_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    selected_tab_id TEXT,
                    saved_at TEXT NOT NULL
                )"""
            )
            connection.execute(f"PRAGMA application_id={workspace_store_module.APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            if not malformed:
                connection.execute(
                    """INSERT INTO dc_workspace_drafts VALUES
                       ('draft-old', 'sql', 'SELECT old_value', 'tab-old', 'sql', 'conn-old',
                        'profile-old', 'db-old', 'owner-old', '2026-10-01T09:30:00+08:00', 'DRAFT')"""
                )
                connection.execute(
                    """INSERT INTO dc_workspace_history VALUES
                       ('history-old', 'sql', 'SELECT old_value', 'tab-old', 'sql', 'conn-old',
                        'profile-old', 'db-old', 'owner-old', '2026-10-01T09:30:00+08:00', 'SUCCESS', 1)"""
                )
                connection.execute(
                    """INSERT INTO dc_workspace_layout VALUES
                       (0, 'tab-old', 'sql', 'conn-old', 'profile-old', 'db-old', 'owner-old')"""
                )
                connection.execute(
                    """INSERT INTO dc_workspace_layout_meta VALUES
                       (1, 'tab-old', '2026-10-01T09:35:00+08:00')"""
                )
            connection.commit()
        finally:
            connection.close()

    def test_temp_sqlite_draft_roundtrip_restores_only_text_and_context(self):
        with TemporaryDirectory(prefix="pengtools-workspace-store-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            self.assertFalse(path.exists())

            draft = DraftRecord(
                draft_id="draft-1",
                target=WorkspaceTarget("tab-1", "conn-1", "profile-r7", "sample_db", "public"),
                text="SELECT id, name FROM sample_table;",
                text_kind=WorkspaceTextKind.SQL,
                updated_at="2026-10-01T09:30:00+08:00",
                state_code="DRAFT",
            )
            store.save_draft(draft)
            restored = store.load_draft("draft-1")

            self.assertEqual(restored, draft)
            self.assertIsInstance(restored.text, str)
            self.assertEqual(restored.target.connection_id, "conn-1")
            self.assertFalse(hasattr(restored, "connection"))
            self.assertFalse(hasattr(restored, "transaction_state"))
            store.close()
            reopened = WorkspaceStore(path)
            self.assertEqual(reopened.load_draft("draft-1"), draft)
            reopened.close()

    def test_history_search_treats_like_wildcards_as_literal_then_favorite_and_clear(self):
        with TemporaryDirectory(prefix="pengtools-workspace-history-") as temp_dir:
            store = WorkspaceStore(Path(temp_dir) / "workspace.sqlite3")
            matching = self._history("history-1", "SELECT * FROM orders WHERE note LIKE '100%done'")
            other = self._history("history-2", "SELECT * FROM customers")
            store.save_history(matching)
            store.save_history(other)

            literal_percent = store.search_history("%")
            self.assertEqual(tuple(record.history_id for record in literal_percent), ("history-1",))
            self.assertEqual(store.search_history("customers", connection_id="conn-1")[0].history_id, "history-2")
            self.assertEqual(store.search_history("custo_ers"), ())
            self.assertEqual(store.search_history("' OR 1=1 --"), ())

            store.set_favorite("history-1")
            favorites = store.list_favorites()
            self.assertEqual(tuple(record.history_id for record in favorites), ("history-1",))
            self.assertTrue(favorites[0].is_favorite)
            self.assertEqual(store.clear_history(), 1)
            self.assertEqual(tuple(record.history_id for record in store.search_history()), ("history-1",))
            self.assertEqual(store.clear_history(include_favorites=True), 1)
            self.assertEqual(store.search_history(), ())
            store.close()

    def test_nosql_history_and_layout_restore_only_order_and_nonsecret_context(self):
        with TemporaryDirectory(prefix="pengtools-workspace-layout-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            nosql = self._history(
                "history-doc",
                '{"find":"customers","filter":{"active":true}}',
                kind=WorkspaceTextKind.NOSQL,
            )
            store.save_history(nosql)
            loaded_nosql = store.search_history("customers")[0]
            self.assertEqual(loaded_nosql, nosql)
            self.assertEqual(loaded_nosql.target.tab_kind, WorkspaceTabKind.REDIS)

            layout = WorkspaceLayout(
                tabs=(
                    WorkspaceTarget("tab-2", "conn-2", "profile-r7", "", "", WorkspaceTabKind.MONGO),
                    WorkspaceTarget("tab-redis", "conn-redis", "profile-r7", "", "", WorkspaceTabKind.REDIS),
                    self._target("1"),
                ),
                selected_tab_id="tab-1",
                saved_at="2026-10-01T09:35:00+08:00",
            )
            store.save_layout(layout)
            restored_layout = store.load_layout()
            self.assertEqual(restored_layout, layout)
            self.assertEqual(
                tuple(tab.tab_kind for tab in restored_layout.tabs),
                (WorkspaceTabKind.MONGO, WorkspaceTabKind.REDIS, WorkspaceTabKind.SQL),
            )

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                app_id = connection.execute("PRAGMA application_id").fetchone()[0]
                layout_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(dc_workspace_layout)")
                }
                history_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(dc_workspace_history)")
                }
            finally:
                connection.close()
            self.assertEqual(version, workspace_store_module.SCHEMA_VERSION)
            self.assertEqual(app_id, workspace_store_module.APPLICATION_ID)
            self.assertEqual(
                layout_columns,
                {"position", "tab_id", "tab_kind", "connection_id", "profile_revision", "database_name", "schema_name"},
            )
            self.assertFalse(
                {"worker", "connection", "transaction", "active_session", "parameters", "results"}
                & history_columns
            )
            self.assertTrue({"row_count", "evidence_summary"} <= history_columns)
            self.assertFalse(Path(f"{path}-wal").exists())
            store.close()

    def test_unknown_stored_tab_kind_fails_closed_without_fallback(self):
        with TemporaryDirectory(prefix="pengtools-workspace-unknown-kind-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            layout = WorkspaceLayout(
                (WorkspaceTarget("tab-r", "conn-r", "r1", tab_kind=WorkspaceTabKind.REDIS),),
                "tab-r",
            )
            store.save_layout(layout)
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute("UPDATE dc_workspace_layout SET tab_kind='unknown'")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(WorkspaceStoreError) as caught:
                store.load_layout()
            self.assertEqual(caught.exception.code, "STORE_RECORD_INVALID")
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("SELECT tab_kind FROM dc_workspace_layout").fetchone()[0],
                    "unknown",
                )
            finally:
                connection.close()
            store.close()

    def test_credential_text_is_rejected_before_database_or_wal_creation(self):
        with TemporaryDirectory(prefix="pengtools-workspace-privacy-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            private_sql = "ALTER USER app IDENTIFIED BY 'private-test-secret';"
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.save_history(self._history("history-secret", private_sql))
            self.assertEqual(caught.exception.code, "CREDENTIAL_TEXT_REJECTED")
            self.assertNotIn("private-test-secret", str(caught.exception))
            self.assertFalse(path.exists())
            self.assertFalse(Path(f"{path}-wal").exists())

            # Credential field names and parameter placeholders contain no saved secret value.
            safe_sql = "SELECT password, token FROM accounts WHERE token = ?"
            store.save_history(self._history("history-safe", safe_sql))
            self.assertEqual(store.search_history("password")[0].text, safe_sql)

            with self.assertRaises(WorkspaceStoreError) as raw_uri:
                store.save_history(
                    self._history("history-uri", "mongodb://db-user:private-test-secret@localhost/sample")
                )
            self.assertEqual(raw_uri.exception.code, "CREDENTIAL_TEXT_REJECTED")
            with self.assertRaises(WorkspaceStoreError) as raw_nosql:
                store.save_history(self._history("history-auth", "AUTH private-test-secret", kind=WorkspaceTextKind.NOSQL))
            self.assertEqual(raw_nosql.exception.code, "CREDENTIAL_TEXT_REJECTED")
            with self.assertRaises(WorkspaceStoreError) as unquoted_secret:
                store.save_history(self._history("history-user", "CREATE USER app PASSWORD private-test-secret"))
            self.assertEqual(unquoted_secret.exception.code, "CREDENTIAL_TEXT_REJECTED")
            self.assertEqual(store.search_history("private-test-secret"), ())
            store.close()
            for artifact in (path, Path(f"{path}-wal")):
                if artifact.exists():
                    self.assertNotIn(b"private-test-secret", artifact.read_bytes())

    def test_failed_write_transaction_preserves_previous_successful_draft(self):
        with TemporaryDirectory(prefix="pengtools-workspace-rollback-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            original = DraftRecord(
                "draft-rollback", self._target(), "SELECT 1", WorkspaceTextKind.SQL,
                updated_at="2026-10-01T09:30:00+08:00",
            )
            store.save_draft(original)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """CREATE TRIGGER fail_draft_update BEFORE UPDATE ON dc_workspace_drafts
                       WHEN NEW.body = 'blocked update'
                       BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END"""
                )
                connection.commit()
            finally:
                connection.close()
            changed = DraftRecord(
                original.draft_id, original.target, "blocked update", WorkspaceTextKind.SQL,
                updated_at="2026-10-01T09:31:00+08:00",
            )
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.save_draft(changed)
            self.assertEqual(caught.exception.code, "STORAGE_FAILED")
            self.assertNotIn("synthetic storage failure", str(caught.exception))
            self.assertEqual(store.load_draft(original.draft_id), original)
            store.close()

    def test_fixed_limits_preserve_prior_records_without_eviction(self):
        with TemporaryDirectory(prefix="pengtools-workspace-limits-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            saved = DraftRecord("draft-limit", self._target(), "SELECT 1", WorkspaceTextKind.SQL)
            store.save_draft(saved)
            with patch.object(workspace_store_module, "MAX_DRAFTS", 1):
                with self.assertRaises(WorkspaceStoreError) as caught:
                    store.save_draft(DraftRecord("draft-over", self._target(), "SELECT 2", WorkspaceTextKind.SQL))
                self.assertEqual(caught.exception.code, "DRAFT_LIMIT_REACHED")
            self.assertEqual(store.load_draft("draft-limit"), saved)

            with patch.object(workspace_store_module, "MAX_HISTORY", 1):
                store.save_history(self._history("history-limit", "SELECT 1"))
                with self.assertRaises(WorkspaceStoreError) as caught:
                    store.save_history(self._history("history-over", "SELECT 2"))
                self.assertEqual(caught.exception.code, "HISTORY_LIMIT_REACHED")
            self.assertEqual(tuple(row.history_id for row in store.search_history()), ("history-limit",))

            store.save_history(self._history("history-favorite-2", "SELECT 2"))
            with patch.object(workspace_store_module, "MAX_FAVORITES", 1):
                store.set_favorite("history-limit")
                with self.assertRaises(WorkspaceStoreError) as caught:
                    store.set_favorite("history-favorite-2")
                self.assertEqual(caught.exception.code, "FAVORITE_LIMIT_REACHED")
            self.assertEqual(tuple(row.history_id for row in store.list_favorites()), ("history-limit",))

            with patch.object(workspace_store_module, "MAX_STORE_BYTES", 1):
                with self.assertRaises(WorkspaceStoreError) as caught:
                    store.save_draft(DraftRecord("draft-too-large-store", self._target(), "SELECT 3", WorkspaceTextKind.SQL))
                self.assertEqual(caught.exception.code, "STORE_LIMIT_REACHED")
            self.assertIsNone(store.load_draft("draft-too-large-store"))
            store.close()

    def test_text_search_and_layout_budgets_fail_before_replacing_saved_state(self):
        with TemporaryDirectory(prefix="pengtools-workspace-bounds-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            prior_layout = WorkspaceLayout((self._target("1"),), "tab-1")
            store.save_layout(prior_layout)
            with self.assertRaises(WorkspaceStoreError) as long_text:
                store.save_history(self._history("history-large", "x" * (MAX_TEXT_BYTES + 1)))
            self.assertEqual(long_text.exception.code, "TEXT_TOO_LARGE")
            with self.assertRaises(WorkspaceStoreError) as long_search:
                store.search_history("x" * (MAX_SEARCH_CHARS + 1))
            self.assertEqual(long_search.exception.code, "SEARCH_TOO_LONG")
            with patch.object(workspace_store_module, "MAX_LAYOUT_TABS", 1):
                too_many = WorkspaceLayout((self._target("1"), self._target("2")), "tab-1")
                with self.assertRaises(WorkspaceStoreError) as layout_limit:
                    store.save_layout(too_many)
                self.assertEqual(layout_limit.exception.code, "LAYOUT_LIMIT_REACHED")
            self.assertEqual(store.load_layout(), prior_layout)
            store.close()

    def test_schema_version_rejects_newer_database_without_overwriting_it(self):
        with TemporaryDirectory(prefix="pengtools-workspace-schema-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version=99")
            finally:
                connection.close()
            store = WorkspaceStore(path)
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.load_draft("draft-1")
            self.assertEqual(caught.exception.code, "SCHEMA_VERSION_UNSUPPORTED")
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
                self.assertEqual(
                    connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                    [],
                )
            finally:
                connection.close()
            store.close()

    def test_existing_unowned_sqlite_file_is_not_migrated_or_modified(self):
        with TemporaryDirectory(prefix="pengtools-workspace-ownership-") as temp_dir:
            path = Path(temp_dir) / "unrelated.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE unrelated(value TEXT)")
                connection.execute("INSERT INTO unrelated(value) VALUES ('keep')")
                connection.commit()
            finally:
                connection.close()
            before = path.read_bytes()

            store = WorkspaceStore(path)
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.load_draft("draft-1")
            self.assertEqual(caught.exception.code, "DATABASE_NOT_OWNED")
            store.close()
            self.assertEqual(path.read_bytes(), before)

    def test_v1_migration_preserves_history_favorites_drafts_and_layout(self):
        with TemporaryDirectory(prefix="pengtools-workspace-v1-migration-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            self._create_v1_database(path)
            store = WorkspaceStore(path)
            self.assertEqual(store.load_draft("draft-old").text, "SELECT old_value")
            migrated = store.search_history()[0]
            self.assertEqual(migrated.history_id, "history-old")
            self.assertEqual(migrated.row_count, None)
            self.assertEqual(migrated.evidence_summary, ())
            self.assertTrue(migrated.is_favorite)
            self.assertEqual(store.list_favorites(), (migrated,))
            self.assertEqual(
                store.load_layout(),
                WorkspaceLayout(
                    (WorkspaceTarget("tab-old", "conn-old", "profile-old", "db-old", "owner-old"),),
                    "tab-old",
                    "2026-10-01T09:35:00+08:00",
                ),
            )

            evidence = (
                HistoryEvidenceSummary("evidence-new", "query_readonly", "OK", 1, False),
            )
            updated = HistoryRecord(
                migrated.history_id,
                migrated.target,
                "SELECT updated_value",
                migrated.text_kind,
                migrated.created_at,
                "SUCCESS",
                False,
                0,
                evidence,
            )
            store.save_history(updated)
            store.close()

            reopened = WorkspaceStore(path)
            restored = reopened.search_history()[0]
            self.assertEqual(restored.text, "SELECT updated_value")
            self.assertEqual(restored.row_count, 0)
            self.assertEqual(restored.evidence_summary, evidence)
            self.assertTrue(restored.is_favorite)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    workspace_store_module.SCHEMA_VERSION,
                )
            finally:
                connection.close()
            reopened.close()

    def test_malformed_v1_schema_is_rejected_without_modifying_file(self):
        with TemporaryDirectory(prefix="pengtools-workspace-malformed-v1-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            self._create_v1_database(path, malformed=True)
            before = path.read_bytes()
            store = WorkspaceStore(path)
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.initialize()
            self.assertEqual(caught.exception.code, "SCHEMA_MALFORMED")
            store.close()
            self.assertEqual(path.read_bytes(), before)

    def test_history_metadata_is_bounded_exact_and_immutable(self):
        with TemporaryDirectory(prefix="pengtools-workspace-history-metadata-validation-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            duplicate = (
                HistoryEvidenceSummary("evidence-same", "query_readonly", "OK", 1, False),
                HistoryEvidenceSummary("evidence-same", "query_readonly", "OK", 1, False),
            )
            invalid_records = (
                self._history("history-bool-count", "SELECT 1", row_count=True),
                self._history("history-negative-count", "SELECT 1", row_count=-1),
                self._history(
                    "history-large-count", "SELECT 1", row_count=MAX_HISTORY_ROW_COUNT + 1
                ),
                self._history("history-list-summary", "SELECT 1", evidence_summary=[]),
                self._history("history-duplicate-evidence", "SELECT 1", evidence_summary=duplicate),
                self._history(
                    "history-too-many-evidence",
                    "SELECT 1",
                    evidence_summary=tuple(
                        HistoryEvidenceSummary(f"evidence-{index}", "query_readonly", "OK")
                        for index in range(MAX_HISTORY_EVIDENCE_ITEMS + 1)
                    ),
                ),
                self._history(
                    "history-bool-evidence-count",
                    "SELECT 1",
                    evidence_summary=(HistoryEvidenceSummary("evidence-count", "query_readonly", "OK", True),),
                ),
                self._history(
                    "history-invalid-truncated-flag",
                    "SELECT 1",
                    evidence_summary=(HistoryEvidenceSummary("evidence-truncated", "query_readonly", "OK", 1, 1),),
                ),
                self._history(
                    "history-invalid-evidence-tool",
                    "SELECT 1",
                    evidence_summary=(HistoryEvidenceSummary("evidence-tool", "arbitrary prose", "OK"),),
                ),
            )
            for record in invalid_records:
                with self.subTest(history_id=record.history_id):
                    with self.assertRaises(WorkspaceStoreError) as caught:
                        store.save_history(record)
                    self.assertEqual(caught.exception.code, "INVALID_RECORD")
            self.assertFalse(path.exists())
            self.assertFalse(Path(f"{path}-wal").exists())
            store.close()

    def test_history_record_and_evidence_summary_are_frozen_and_tuple_backed(self):
        evidence = (HistoryEvidenceSummary("evidence-frozen", "query_readonly", "OK", 2, True),)
        record = self._history(
            "history-frozen-metadata",
            "SELECT 1",
            row_count=2,
            evidence_summary=evidence,
        )
        self.assertIs(type(record.evidence_summary), tuple)
        self.assertEqual(record.evidence_summary, evidence)
        with self.assertRaises(FrozenInstanceError):
            record.row_count = 3
        with self.assertRaises(FrozenInstanceError):
            record.evidence_summary[0].count = 3
        with self.assertRaises(AttributeError):
            record.evidence_summary.append(evidence[0])

    def test_corrupt_persisted_history_metadata_fails_closed(self):
        with TemporaryDirectory(prefix="pengtools-workspace-corrupt-history-metadata-") as temp_dir:
            path = Path(temp_dir) / "workspace.sqlite3"
            store = WorkspaceStore(path)
            store.save_history(self._history("history-metadata", "SELECT 1", row_count=1))
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    "UPDATE dc_workspace_history SET evidence_summary=? WHERE history_id=?",
                    (
                        '[{"evidence_id":"e1","tool_name":"query_readonly",'
                        '"state_code":"OK","count":true,"truncated":false}]',
                        "history-metadata",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(WorkspaceStoreError) as caught:
                store.search_history()
            self.assertEqual(caught.exception.code, "STORE_RECORD_INVALID")
            self.assertNotIn("SELECT 1", str(caught.exception))
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    "UPDATE dc_workspace_history SET evidence_summary='[]', row_count=-1 WHERE history_id=?",
                    ("history-metadata",),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(WorkspaceStoreError) as bad_count:
                store.search_history()
            self.assertEqual(bad_count.exception.code, "STORE_RECORD_INVALID")
            store.close()


if __name__ == "__main__":
    unittest.main()
