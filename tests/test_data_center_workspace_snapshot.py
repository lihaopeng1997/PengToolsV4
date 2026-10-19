"""Atomic snapshots in temporary SQLite, preserving history and secret gates."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.data_center.workspace_store import (
    DraftRecord, HistoryRecord, WorkspaceLayout, WorkspaceSnapshot, WorkspaceStore,
    WorkspaceStoreError, WorkspaceTabKind, WorkspaceTarget, WorkspaceTextKind,
)


def snapshot(*names):
    targets = tuple(WorkspaceTarget(name, "temp", "rev", "db", "db") for name in names)
    return WorkspaceSnapshot(WorkspaceLayout(targets, targets[-1].tab_id if targets else None),
                             tuple(DraftRecord(target.tab_id + ":sql", target, "SELECT '" + target.tab_id + "'", WorkspaceTextKind.SQL) for target in targets))


class WorkspaceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pengtools-workspace-snapshot-")
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.store = WorkspaceStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_atomic_replacement_preserves_unrelated_drafts_and_history_favorites(self):
        first = snapshot("one", "two")
        unrelated = DraftRecord("standalone", first.layout.tabs[0], "standalone draft", WorkspaceTextKind.SQL)
        history = HistoryRecord("history", first.layout.tabs[0], "SELECT 1", WorkspaceTextKind.SQL)
        self.store.save_draft(unrelated)
        self.store.save_history(history)
        self.store.set_favorite(history.history_id)
        self.store.save_workspace(first)
        self.assertEqual(self.store.load_workspace(), first)
        second = snapshot("second")
        with patch("tools.data_center.workspace_store._write_layout", side_effect=WorkspaceStoreError("STORAGE_FAILED")):
            with self.assertRaises(WorkspaceStoreError):
                self.store.save_workspace(second)
        self.assertEqual(self.store.load_workspace(), first)
        self.assertIsNone(self.store.load_draft("second:sql"))
        self.store.save_workspace(second)
        self.assertEqual(self.store.load_workspace(), second)
        self.assertIsNone(self.store.load_draft("one:sql"))
        self.assertEqual(self.store.load_draft("standalone"), unrelated)
        self.assertEqual(self.store.list_favorites(), (replace(history, is_favorite=True),))

    def test_secret_or_incomplete_snapshot_fails_before_file_creation(self):
        current = snapshot("one")
        secrets = [replace(current, drafts=(replace(current.drafts[0], text=text),)) for text in
                   ("password='synthetic credential'", "db.auth('fixture', 'synthetic')", "db.changeUserPassword('fixture', 'synthetic')")]
        for value in (*secrets, replace(current, drafts=())):
            with self.assertRaises(WorkspaceStoreError):
                self.store.save_workspace(value)
            self.assertFalse(self.path.exists())
        self.store.save_workspace(current)
        invalid = replace(current, layout=replace(current.layout, selected_tab_id="absent"))
        with self.assertRaises(WorkspaceStoreError):
            self.store.save_workspace(invalid)
        self.assertEqual(self.store.load_workspace(), current)

    def test_nosql_editor_slots_preserve_partial_text_and_type_context(self):
        target = WorkspaceTarget("mongo", "temp", "rev", "db", "records", WorkspaceTabKind.MONGO)
        texts = (DraftRecord("mongo:documents", target, '{"_id": {"$oid": "unfinished', WorkspaceTextKind.NOSQL),
                 DraftRecord("mongo:command", target, "db.records.find({})", WorkspaceTextKind.NOSQL))
        value = WorkspaceSnapshot(WorkspaceLayout((target,), "mongo"), texts)
        self.store.save_workspace(value)
        self.assertEqual(self.store.load_workspace(), value)
        with self.assertRaises(WorkspaceStoreError):
            self.store.save_workspace(replace(value, drafts=texts[:1]))
        self.assertEqual(self.store.load_workspace(), value)


if __name__ == "__main__":
    unittest.main()
