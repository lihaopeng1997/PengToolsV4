"""Real NoSQL workbench Run/history wiring against isolated TEMP drivers."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import uuid
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bson import ObjectId
from bson.decimal128 import Decimal128
from PyQt6.QtCore import QCoreApplication, QEvent, QRect, QTimer, Qt
from PyQt6.QtGui import QFont, QFontInfo
from PyQt6.QtWidgets import QApplication, QWidget

from tools.data_center.nosql_workspace import capture_mongo_run, decode_nosql_workspace
from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.workspace_store import WorkspaceStore
from ui.data_center.query_history import QueryHistoryDialog
import ui.data_center.query_history_service as history_service


class _FakeRedisConnection:
    def __init__(self, owner):
        self.owner = owner

    def scan(self, **kwargs):
        return 0, list(self.owner.redis_export_values)

    def type(self, key):
        return b'string' if key in self.owner.redis_export_values else b'none'

    def ttl(self, key):
        return 0

    def strlen(self, key):
        return len(self.owner.redis_export_values[key])

    def getrange(self, key, start, stop):
        self.owner.redis_export_reads.append((key, start, stop, threading.get_ident()))
        return self.owner.redis_export_values[key][start:stop + 1]


class _FakeMongoCursor:
    def __init__(self, owner, collection, filter_value):
        self.owner = owner
        self.collection = collection
        self.filter_value = filter_value
        self.offset = 0
        self.page_limit = None

    def projection(self, value):
        self.owner.mongo_operations.append(("projection", self.collection, value))
        return self

    def sort(self, value):
        self.owner.mongo_operations.append(("sort", self.collection, value))
        return self

    def skip(self, value):
        self.owner.mongo_operations.append(("skip", self.collection, value))
        self.offset = value
        return self

    def limit(self, value):
        self.owner.mongo_operations.append(("limit", self.collection, value))
        self.page_limit = value
        return self

    def __iter__(self):
        docs = self.owner.mongo_documents
        if docs is not None:
            end = self.offset + self.page_limit if self.page_limit is not None else None
            return iter(docs[self.offset:end])
        return iter([{"_id": "TEMP_RESULT", "status": "TEMP"}])

    def batch_size(self, value):
        self.owner.mongo_operations.append(("batch_size", self.collection, value))
        return self

    def close(self):
        self.owner.mongo_operations.append(("cursor_close", self.collection))


class _FakeMongoCollection:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    def find(self, filter_value, projection=None):
        self.owner.mongo_operations.append(("find", self.name, filter_value))
        if projection is not None:
            self.owner.mongo_operations.append(("projection", self.name, projection))
        return _FakeMongoCursor(self.owner, self.name, filter_value)

    def count_documents(self, filter_value):
        self.owner.mongo_operations.append(("count", self.name, filter_value))
        return self.owner.mongo_total

    def find_one(self, selector):
        self.owner.mongo_operations.append(("find_one", self.name, deepcopy(selector)))
        identifier = selector["_id"]["$eq"]
        return next((deepcopy(doc) for doc in self.owner.mongo_edit_documents if doc["_id"] == identifier), None)

    def replace_one(self, selector, replacement, *, upsert):
        from pymongo.results import UpdateResult

        self.owner.mongo_operations.append(("replace_one", self.name, deepcopy(selector), deepcopy(replacement), upsert))
        identifier = selector["_id"]["$eq"]
        for index, doc in enumerate(self.owner.mongo_edit_documents):
            if doc["_id"] == identifier:
                self.owner.mongo_edit_documents[index] = deepcopy(replacement)
                return UpdateResult({"n": 1, "nModified": 1, "updatedExisting": True}, True)
        return UpdateResult({"n": 0, "nModified": 0}, True)

    def insert_one(self, document):
        from pymongo.results import InsertOneResult

        document = deepcopy(document)
        document.setdefault('_id', ObjectId())
        self.owner.mongo_operations.append(('insert_one', self.name, document))
        return InsertOneResult(document['_id'], True)


class _FakeMongoDatabase:
    def __init__(self, owner, name="temp_records"):
        self.owner = owner
        self.name = name

    def __getitem__(self, name):
        return _FakeMongoCollection(self.owner, name)

    def list_collection_names(self):
        return ["records"]


class NoSqlHistoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pengtools-nosql-history-test-")
        self.history_path = os.path.join(self.temp.name, "workspace.sqlite3")
        evidence_root = os.path.join(
            tempfile.gettempdir(), "pengtools-nosql-history-product-20261007", "evidence", "screens"
        )
        os.makedirs(evidence_root, exist_ok=True)
        self.evidence_root = evidence_root
        self.profiles = []
        self.db_calls: list[tuple[str, str, int]] = []
        self.redis_commands: list[tuple[str, int]] = []
        self.redis_export_values = {}
        self.redis_export_reads = []
        self.redis_open_profiles = []
        self.mongo_operations: list[tuple] = []
        self.mongo_total = 23
        self.mongo_documents = None
        self.mongo_edit_documents = []
        self.redis_entered = threading.Event()
        self.redis_release = threading.Event()
        self.redis_release.set()
        self.block_redis = False
        self.redis_fail_after_release = False
        self.panel = None
        self._test_agent_hosts = []
        self.stack = ExitStack()
        self._previous_history_service = history_service._APPLICATION_HISTORY_SERVICE
        # A suite run inside the desktop process may already have an app
        # service bound to the user's workspace path. Keep that object intact
        # and give this test a private singleton until all TEMP owners drain.
        history_service._APPLICATION_HISTORY_SERVICE = None
        self.profile_loader_calls = 0
        self.model_loader_calls = 0
        self.layout_load_calls: list[str] = []
        self.layout_save_calls: list[str] = []

        def load_temp_profiles():
            self.profile_loader_calls += 1
            return {"items": [dict(profile) for profile in self.profiles]}

        def load_temp_models():
            self.model_loader_calls += 1
            return {"items": [], "active_id": ""}

        def load_temp_layout(key):
            self.layout_load_calls.append(str(key))
            return None

        def save_temp_layout(key, sizes):
            self.layout_save_calls.append(str(key))

        self.stack.enter_context(patch("config.DATA_CENTER_WORKSPACE_FILE", self.history_path))
        self.stack.enter_context(patch("config.load_layout_splitter", side_effect=load_temp_layout))
        self.stack.enter_context(patch("config.save_layout_splitter", side_effect=save_temp_layout))
        self.stack.enter_context(patch("panels.db_redis_panel.load_connections", return_value=[]))
        self.stack.enter_context(patch("panels.db_mongodb_panel.load_connections", return_value=[]))
        self.stack.enter_context(patch("panels.db_redis_panel.open_connection", side_effect=self._open_redis))
        self.stack.enter_context(patch("panels.db_redis_panel.close_connection", side_effect=lambda _conn: None))
        self.stack.enter_context(patch("tools.db_connect._run_redis", side_effect=self._run_redis))
        self.stack.enter_context(patch("panels.db_mongodb_panel.open_connection", side_effect=self._open_mongo))
        self.stack.enter_context(patch("panels.db_mongodb_panel.close_connection", side_effect=lambda _conn: None))
        self.profile_host = DataCenterProfileHost(
            connections_loader=load_temp_profiles,
            model_catalog_loader=load_temp_models,
        )
        self.stack.enter_context(patch(
            "ui.data_center.integration.DataCenterProfileHost",
            return_value=self.profile_host,
        ))
        # Intercept both NoSQL panel aliases as well as the embedded Agent's
        # alias. This keeps every constructor path on the in-memory TEMP
        # profile/model loaders before a production widget can be created.
        self.stack.enter_context(patch(
            "panels.db_redis_panel.DataCenterProfileHost",
            return_value=self.profile_host,
        ))
        self.stack.enter_context(patch(
            "panels.db_mongodb_panel.DataCenterProfileHost",
            return_value=self.profile_host,
        ))

    def tearDown(self):
        try:
            self.redis_release.set()
            for host in tuple(self._test_agent_hosts):
                try:
                    if hasattr(host, "workbench_shutdown_cleanup"):
                        host.workbench_shutdown_cleanup()
                    self.assertTrue(
                        self._wait(lambda current=host: not current._legacy_workers),
                        "TEMP database workers must finish before fixture cleanup",
                    )
                    agent = getattr(host, "data_center_agent", None)
                    if agent is not None:
                        self.assertTrue(
                            self._wait(agent.agent_shutdown_cleanup),
                            "TEMP model catalog/controller owners must finish before fixture cleanup",
                        )
                        self.assertTrue(
                            self._wait(lambda current=agent: not current._catalog_threads),
                            "TEMP model catalog QThreads must finish before fixture cleanup",
                        )
                except RuntimeError:
                    # A prior loop may already have processed DeferredDelete.
                    pass
            self.assertGreater(
                self.model_loader_calls,
                0,
                "the production Agent host must exercise only the TEMP model catalog loader",
            )
            if self.panel is not None:
                self.panel.close()
                self.panel.deleteLater()
                self.panel = None
                self._flush()
            service = history_service._APPLICATION_HISTORY_SERVICE
            if service is not None and service is not self._previous_history_service:
                self.assertEqual(
                    os.path.abspath(str(service.store.path)),
                    os.path.abspath(self.history_path),
                    "history must remain on the TEMP database through owner cleanup",
                )
                self.assertTrue(self._wait(service.drain), "service must drain actual worker/controller finish")
                history_service.reset_application_history_service_for_tests()
        finally:
            self.stack.close()
            self.temp.cleanup()
            history_service._APPLICATION_HISTORY_SERVICE = self._previous_history_service

    def _flush(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _wait(self, predicate, timeout: float = 7.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._flush()
            try:
                if predicate():
                    return True
            except RuntimeError:
                pass
            time.sleep(0.003)
        self._flush()
        return bool(predicate())

    @staticmethod
    def _profile(provider: str, *, identity: str | None = None) -> dict:
        if provider == "redis":
            return {
                "id": identity or "temp-nosql-redis",
                "name": "TEMP Redis",
                "provider": "redis",
                "dialect": "redis",
                "mode": "standalone",
                "host": "127.0.0.1",
                "port": 6379,
                "database": 0,
                "auth_mode": "none",
            }
        return {
            "id": identity or "temp-nosql-mongo",
            "name": "TEMP Mongo",
            "provider": "mongodb",
            "dialect": "mongodb",
            "mode": "standalone",
            "host": "127.0.0.1",
            "port": 27017,
            "database": "temp_records",
            "auth_mode": "none",
        }

    def _open_redis(self, item):
        self.db_calls.append(("redis", str(item.get("id") or ""), threading.get_ident()))
        self.redis_open_profiles.append(deepcopy(item))
        return _FakeRedisConnection(self)

    def _run_redis(self, _connection, command, *_args):
        self.redis_commands.append((str(command), threading.get_ident()))
        self.redis_entered.set()
        if self.block_redis:
            self.redis_release.wait(4.0)
        if self.redis_fail_after_release:
            raise RuntimeError("TEMP_FAKE_WORKER_FAILURE")
        return {"columns": [], "rows": []}

    def _open_mongo(self, item):
        self.db_calls.append(("mongo", str(item.get("id") or ""), threading.get_ident()))
        return _FakeMongoDatabase(self, item.get("database") or "admin")

    def _make_panel(self, provider: str, language: str = "en"):
        self.profiles[:] = [self._profile(provider)]
        if provider == "redis":
            from panels.db_redis_panel import RedisWorkbenchPanel

            panel = RedisWorkbenchPanel(
                language=language,
                data_center_agent_options={"profile_host": self.profile_host},
                data_center_history_options={"profile_host": self.profile_host},
            )
        else:
            from panels.db_mongodb_panel import MongoDBWorkbenchPanel

            panel = MongoDBWorkbenchPanel(
                language=language,
                data_center_agent_options={"profile_host": self.profile_host},
                data_center_history_options={"profile_host": self.profile_host},
            )
        self.assertIs(panel._nosql_profile_host, self.profile_host)
        self.assertIs(panel.data_center_agent._profile_host, self.profile_host)
        panel.conn_combo.blockSignals(True)
        panel.conn_combo.clear()
        panel.conn_combo.addItem("TEMP selected profile", dict(self.profiles[0]))
        panel.conn_combo.setCurrentIndex(0)
        panel.conn_combo.blockSignals(False)
        if provider == "mongo":
            panel._selected_db = str(self.profiles[0]["database"])
            panel._selected_coll = "records"
        draft = panel._create_nosql_draft()
        panel.resize(960, 640)
        panel.show()
        self._flush()
        self.panel = panel
        self._test_agent_hosts.append(panel)
        return panel, draft

    def _add_second_profile_draft(self, panel, provider: str):
        second_profile = self._profile(provider, identity=f"temp-nosql-{provider}-second")
        self.profiles.append(second_profile)
        panel.conn_combo.blockSignals(True)
        panel.conn_combo.addItem("TEMP second profile", dict(second_profile))
        panel.conn_combo.setCurrentIndex(1)
        panel.conn_combo.blockSignals(False)
        if provider == "mongo":
            panel._selected_db = str(second_profile["database"])
            panel._selected_coll = "records"
        panel.nosql_draft_tabs.new_button.click()
        second = panel.nosql_draft_tabs.current_draft
        self.assertEqual(second.target.connection_id, second_profile["id"])
        return second

    def _make_embedded_visual_panel(self, provider: str, language: str):
        """Build the same pinned embedded host and workspace as DataCenterPanel."""

        from panels.data_center_panel import DataCenterPanel, _ConnectionWorkspace

        profile = self._profile(provider)
        self.profiles[:] = [profile]
        module_name = "panels.db_redis_panel" if provider == "redis" else "panels.db_mongodb_panel"
        with patch(f"{module_name}.load_connections", return_value=[profile]), patch(
            f"{module_name}.DataCenterProfileHost", return_value=self.profile_host,
        ):
            host = DataCenterPanel._default_host_factory(
                SimpleNamespace(language=language), dict(profile), language,
            )
        self.assertIs(host._nosql_profile_host, self.profile_host)
        self.assertIs(host.data_center_agent._profile_host, self.profile_host)
        host.set_language(language)
        host.apply_layout_mode("narrow", True)
        workspace = _ConnectionWorkspace(str(profile["id"]), profile, host)
        workspace.apply_layout_mode("narrow", True)
        workspace.resize(960, 640)
        workspace.show()
        self._flush()
        # Top-level widgets may adjust to their size hint on show; the real
        # DataCenter workspace then assigns the responsive viewport extent.
        workspace.resize(960, 640)
        self._flush()
        self.panel = workspace
        self._test_agent_hosts.append(host)
        return workspace, host

    def _history_rows(self):
        store = WorkspaceStore(self.history_path)
        try:
            return store.search_history("")
        finally:
            store.close()

    def _wait_saved(self):
        service = history_service._APPLICATION_HISTORY_SERVICE
        self.assertIsNotNone(service)
        self.assertEqual(
            os.path.abspath(str(service.store.path)),
            os.path.abspath(self.history_path),
            "the actual app history service must stay bound to the TEMP workspace file",
        )
        self.assertTrue(self._wait(lambda: (
            service._persistence_controller is not None
            and service._persistence_controller.pending_operations == 0
        )))

    def _restore_from_actual_dialog(self, panel, history_id: str):
        observed: dict[str, object] = {}
        deadline = time.monotonic() + 5.0

        def choose_record():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QueryHistoryDialog) and dialog.isVisible():
                browser = dialog.browser
                for index in range(browser.record_list.count()):
                    item = browser.record_list.item(index)
                    record = item.data(Qt.ItemDataRole.UserRole)
                    if getattr(record, "history_id", "") == history_id:
                        browser.record_list.setCurrentRow(index)
                        self._flush()
                        browser.restore_button.click()
                        observed["selected"] = True
                        dialog.accept()
                        return
            if time.monotonic() >= deadline:
                observed["timed_out"] = True
                if isinstance(dialog, QueryHistoryDialog):
                    dialog.reject()
                return
            QTimer.singleShot(10, choose_record)

        QTimer.singleShot(0, choose_record)
        panel.nosql_draft_tabs.history_button.click()
        self.assertTrue(observed.get("selected"), f"history dialog did not restore record: {observed}")
        self._flush()

    def test_mongo_run_and_paging_capture_the_same_collection_and_full_query_spec(self):
        self.mongo_documents = [{"_id": index, "status": "TEMP"} for index in range(3)]
        panel, draft = self._make_panel("mongo")
        raw = (
            '{"collection":"records","filter":{"status":"TEMP"},'
            '"sort":{"created_at":-1},"projection":{"_id":1},"limit":2}'
        )
        panel.query_input.setPlainText(raw)
        from tools.db_mongo_ops import parse_mongo_query

        with patch("panels.db_mongodb_panel.parse_mongo_query", wraps=parse_mongo_query) as parser:
            panel.query_btn.click()
            first_worker = panel._worker
            self.assertIsNotNone(first_worker)
            self.assertEqual(first_worker.nosql_draft_id, draft.draft_id)
            self.assertTrue(self._wait(lambda: first_worker.isFinished() and first_worker not in panel._legacy_workers))
            self.assertEqual(parser.call_count, 1, "Documents parses once at the dispatch boundary")
            self.assertEqual(first_worker.kwargs["collection"], "records")
            self.assertEqual(first_worker.kwargs["filter"], {"status": "TEMP"})
            self.assertEqual(first_worker.kwargs["sort"], {"created_at": -1})
            self.assertEqual(first_worker.kwargs["projection"], {"_id": 1})
            self.assertEqual((first_worker.kwargs["skip"], first_worker.kwargs["limit"]), (0, 2))
            self._wait_saved()

            panel._shift_page(1)
            second_worker = panel._worker
            self.assertIsNotNone(second_worker)
            self.assertEqual(second_worker.nosql_draft_id, draft.draft_id)
            self.assertTrue(self._wait(lambda: second_worker.isFinished() and second_worker not in panel._legacy_workers))
            self.assertEqual(parser.call_count, 1, "Paging reuses the immutable snapshot without reparsing")
            self.assertEqual(second_worker.kwargs["collection"], "records")
            self.assertEqual(second_worker.kwargs["filter"], {"status": "TEMP"})
            self.assertEqual(second_worker.kwargs["sort"], {"created_at": -1})
            self.assertEqual(second_worker.kwargs["projection"], {"_id": 1})
            self.assertEqual((second_worker.kwargs["skip"], second_worker.kwargs["limit"]), (2, 2))
        self._wait_saved()
        rows = self._history_rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.target.connection_id == "temp-nosql-mongo" for row in rows))
        self.assertEqual([row.row_count for row in rows], [1, 2], "history records returned page rows, not collection totals")
        self.assertFalse(any(operation[0] == "count" for operation in self.mongo_operations))
        self.assertEqual([row.state_code for row in rows], ["COMPLETED", "COMPLETED"])
        page_requests = [op for op in self.mongo_operations if op[0] == "skip"]
        self.assertEqual([op[2] for op in page_requests], [0, 2])

        original_calls = len(self.db_calls)
        # The history dialog is the real browser action. The changed profile
        # revision makes the restored target readable and non-runnable.
        self.profiles[0] = {**self.profiles[0], "host": "127.0.0.2"}
        profile_loads_before_restore = self.profile_loader_calls
        locked_events = []
        panel.nosql_draft_tabs.draft_changed.connect(
            lambda restored: locked_events.append(bool(getattr(restored, "locked", False)))
        )
        self._restore_from_actual_dialog(panel, rows[0].history_id)
        self.assertGreater(
            self.profile_loader_calls,
            profile_loads_before_restore,
            "locked restore revalidates through the injected TEMP-only profile loader",
        )
        restored = panel.nosql_draft_tabs.current_draft
        self.assertTrue(restored.locked)
        self.assertFalse(restored.runnable)
        self.assertFalse(panel.query_btn.isEnabled())
        self.assertIn(restored.validation_code, {"PROFILE_MISMATCH", "PROFILE_UNAVAILABLE"})
        self.assertEqual(panel.query_input.toPlainText(), raw)
        self.assertTrue(locked_events[-1], "history lock must be set before current-tab signal delivery")
        self.assertEqual(len(self.db_calls), original_calls, "restore and tab switch must perform zero DB connects")
        old_draft_id = draft.draft_id
        self.assertTrue(panel.nosql_draft_tabs.select_draft(old_draft_id))
        self.assertTrue(panel.nosql_draft_tabs.select_draft(restored.draft_id))
        self.assertEqual(len(self.db_calls), original_calls, "tab changes do not connect or refresh metadata")

    def test_mongo_lookahead_caps_page_zero_and_bson_rendering_without_count(self):
        from bson import json_util
        from bson.binary import UuidRepresentation

        identifier = ObjectId("650000000000000000000001")
        unique = uuid.UUID("65000000-0000-0000-0000-000000000001")
        self.mongo_documents = [{"_id": index, "nested": {"items": [identifier, Decimal128("1.20"), unique]}} for index in range(3)]
        panel, draft = self._make_panel("mongo")
        panel.set_language("zh")
        self.assertFalse(panel.prev_btn.isEnabled())
        self.assertFalse(panel.next_btn.isEnabled())
        raw = '{"collection":"records","filter":{},"limit":2}'
        panel.query_input.setPlainText(raw)
        panel.query_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual([doc["_id"] for doc in panel._docs], [0, 1])
        self.assertEqual(panel.doc_table.rowCount(), 2)
        self.assertIsNone(panel._total)
        self.assertFalse(panel.prev_btn.isEnabled())
        self.assertTrue(panel.next_btn.isEnabled())
        self.assertIn("还有下一页", panel.page_label.text())
        options = json_util.RELAXED_JSON_OPTIONS.with_options(uuid_representation=UuidRepresentation.STANDARD)
        decoded = json_util.loads(panel.doc_json.toPlainText(), json_options=options)
        self.assertEqual(decoded[0]["nested"]["items"], [identifier, Decimal128("1.20"), unique])
        cell = panel.doc_table.item(0, panel._columns.index("nested")).text()
        self.assertIn("$oid", cell)
        panel.next_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual([doc["_id"] for doc in panel._docs], [2])
        self.assertEqual(panel._offset, 2)
        self.assertTrue(panel.prev_btn.isEnabled())
        self.assertFalse(panel.next_btn.isEnabled())
        calls = len(self.db_calls)
        panel._shift_page(1)
        self.assertEqual(len(self.db_calls), calls)
        panel.set_language("en")
        self.assertIn("Last page", panel.page_label.text())
        panel.prev_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(panel._offset, 0)
        panel.next_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(panel._offset, 2)
        panel.reset_btn.click()
        worker = panel._worker
        self.assertIsNotNone(worker)
        self.assertEqual(worker.kwargs["skip"], 0)
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(panel._offset, 0)
        self.assertEqual([doc["_id"] for doc in panel._docs], [0, 1, 2])
        self.mongo_documents = []
        panel.query_input.setPlainText(raw + " ")
        panel.query_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(panel._docs, [])
        self.assertIn("0 rows", panel.page_label.text())
        self.assertFalse(panel.prev_btn.isEnabled())
        self.assertFalse(panel.next_btn.isEnabled())
        self.assertFalse(any(op[0] == "count" for op in self.mongo_operations))
        self._wait_saved()
        self.assertTrue(any(row.row_count == 0 for row in self._history_rows()))

    def test_mongo_failed_next_retains_page_and_waits_for_owner_before_same_offset_retry(self):
        from tools.db_connect import DbError

        self.mongo_documents = [{"_id": index} for index in range(3)]
        panel, draft = self._make_panel("mongo")
        panel.set_language("zh")
        panel.query_input.setPlainText('{"collection":"records","filter":{},"limit":2}')
        panel.query_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        old_docs = list(panel._docs)
        old_snapshot = panel._result_snapshot
        read_at = panel._page_read_at
        close_entered, close_release = threading.Event(), threading.Event()

        def blocked_close(_connection):
            close_entered.set()
            close_release.wait(3)

        with patch("panels.db_mongodb_panel.find_docs", side_effect=DbError("SYNTHETIC_NEXT_PAGE_FAILED")), \
                patch("panels.db_mongodb_panel.close_connection", side_effect=blocked_close), \
                patch("panels.db_mongodb_panel.show_error"):
            try:
                panel.next_btn.click()
                failed = panel._worker
                self.assertTrue(self._wait(lambda: close_entered.is_set() and panel._page_stale))
                self.assertEqual(failed.kwargs["skip"], 2)
                self.assertEqual(panel._offset, 0)
                self.assertEqual(panel._docs, old_docs)
                self.assertIs(panel._result_snapshot, old_snapshot)
                self.assertIn(read_at, panel.page_label.text())
                self.assertIn("未更新", panel.page_label.text())
                self.assertFalse(panel.next_btn.isEnabled())
                self.assertIn(failed, panel._legacy_workers)
                calls = len(self.db_calls)
                panel._shift_page(1)
                self.assertEqual(len(self.db_calls), calls)
            finally:
                close_release.set()
                self.assertTrue(self._wait(lambda: failed not in panel._legacy_workers))
        self.assertTrue(panel.next_btn.isEnabled())
        panel.next_btn.click()
        retried = panel._worker
        self.assertEqual(retried.kwargs["skip"], 2)
        self.assertTrue(self._wait(lambda: retried not in panel._legacy_workers))
        self.assertEqual(panel._offset, 2)
        self.assertEqual(panel._docs, [{"_id": 2}])
        self.assertFalse(panel._page_stale)
        self.assertFalse(panel.next_btn.isEnabled())
        self.assertFalse(any(op[0] == "count" for op in self.mongo_operations))

    def _open_fixture_document_editor(self, *, sorted_rows=False, database=None, connection_opener=None):
        from bson.binary import Binary

        errors = []
        for name in ("show_error", "show_warning"):
            self.stack.enter_context(patch(f"panels.db_mongodb_panel.{name}", side_effect=lambda *args: errors.append(args[-1])))
        first, second = ObjectId("650000000000000000000001"), ObjectId("650000000000000000000002")
        self.mongo_documents = [{"_id": first, "visible": "a"}, {"_id": second, "visible": "z"}]
        self.mongo_edit_documents = [{**doc, "hidden": {"amount": Decimal128("1.20"), "binary": Binary(b"\x00\xff", subtype=2)}} for doc in self.mongo_documents]
        panel, draft = self._make_panel("mongo")
        if database is not None:
            panel._selected_db = database
            draft = panel._create_nosql_draft()
        if connection_opener is not None:
            panel._connection_opener = connection_opener
        panel.query_input.setPlainText('{"collection":"records","projection":{"visible":1},"limit":2}')
        panel.query_btn.click()
        worker = panel._worker
        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(errors, [])
        if sorted_rows:
            panel.doc_table.setSortingEnabled(True)
            panel.doc_table.sortItems(panel._columns.index("visible"), Qt.SortOrder.DescendingOrder)
        panel.doc_table.selectRow(0)
        self.assertTrue(panel.edit_doc_btn.isEnabled())
        panel.edit_doc_btn.click()
        self.assertTrue(self._wait(lambda: panel._document_dialog is not None or bool(errors)))
        self.assertEqual(errors, [])
        dialog = panel._document_dialog
        self.assertIn("hidden", dialog.editor.toPlainText())
        expected = second if sorted_rows else first
        self.assertIn(str(expected), dialog.identifier.text())
        loaded = next(op for op in self.mongo_operations if op[0] == "find_one")
        self.assertEqual(loaded[2], {"_id": {"$eq": expected}})
        return panel, draft, dialog, expected

    def test_mongo_document_edit_sorted_projected_row_full_read_preview_and_confirm(self):
        panel, draft, dialog, identifier = self._open_fixture_document_editor(sorted_rows=True)
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.editor.setPlainText(dialog.editor.toPlainText().replace('"visible": "z"', '"visible": "changed"'))
        calls = len(self.db_calls)
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertEqual(len(self.db_calls), calls, "Preview is pure and must not reconnect")
        self.assertTrue(dialog.apply_btn.isEnabled())
        self.assertIn('"changed"', dialog.preview.toPlainText())
        self.assertFalse(any(op[0] == "replace_one" for op in self.mongo_operations))
        dialog.apply_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        writes = [op for op in self.mongo_operations if op[0] == "replace_one"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], {"_id": {"$eq": identifier}})
        self.assertFalse(writes[0][4])
        self.assertEqual(writes[0][3]["visible"], "changed")
        self.assertEqual(writes[0][3]["hidden"]["amount"], Decimal128("1.20"))
        self.assertIn("1", dialog.status.text())
        self.assertTrue(panel._page_stale)
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.apply()
        self.assertEqual(len([op for op in self.mongo_operations if op[0] == "replace_one"]), 1)
        dialog.close()

    def test_mongo_document_edit_profile_change_blocks_write_and_start_failure_can_close(self):
        panel, draft, dialog, _identifier = self._open_fixture_document_editor()
        dialog.editor.setPlainText(dialog.editor.toPlainText().replace('"visible": "a"', '"visible": "changed"'))
        with patch("panels.db_mongodb_panel._MongoWorker.start", side_effect=RuntimeError("SYNTHETIC_START_REJECTED")):
            dialog.preview_btn.click()
        self.assertIsNone(dialog._worker)
        self.assertTrue(dialog.preview_btn.isEnabled())
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertTrue(dialog.apply_btn.isEnabled())
        self.profiles[0] = {**self.profiles[0], "host": "changed.invalid"}
        count = len(self.db_calls)
        dialog.apply_btn.click()
        self.assertIsNone(dialog._worker)
        self.assertEqual(len(self.db_calls), count)
        self.assertFalse(any(op[0] == "replace_one" for op in self.mongo_operations))
        self.assertIn("profile", dialog.status.text().lower())
        dialog.close()

    def test_mongo_document_edit_captured_database_background_tab_and_original_owner(self):
        opened, selected, closed = [], [], []
        database = _FakeMongoDatabase(self, "other_records")

        class Client:
            def __getitem__(_client, name):
                selected.append(name)
                self.assertEqual(name, database.name)
                return database

        owner = SimpleNamespace(name="temp_records", client=Client())

        def open_owner(profile):
            opened.append(deepcopy(profile))
            return owner

        self.stack.enter_context(patch("panels.db_mongodb_panel.close_connection", side_effect=closed.append))
        panel, draft, dialog, identifier = self._open_fixture_document_editor(
            database="other_records", connection_opener=open_owner,
        )
        dialog.editor.setPlainText(dialog.editor.toPlainText().replace('"visible": "a"', '"visible": "changed"'))
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertEqual(len(opened), 2, "Preview must not open a database")
        second = self._add_second_profile_draft(panel, "mongo")
        self.assertNotEqual(second.target.connection_id, draft.target.connection_id)
        dialog.apply_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertIs(panel.nosql_draft_tabs.current_draft, second)
        self.assertEqual(panel._docs, [])
        self.assertIn("Matched 1, modified 1", dialog.status.text())
        self.assertEqual(selected, ["other_records"] * 3)
        self.assertEqual(closed, [owner] * 3, "Close the original transport owner, not the derived database")
        self.assertTrue(all(profile == opened[0] for profile in opened))
        self.assertEqual(opened[0]["database"], "temp_records", "Keep the original connection/authentication profile")
        writes = [op for op in self.mongo_operations if op[0] == "replace_one"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], {"_id": {"$eq": identifier}})
        dialog.close()

    def test_mongo_transfer_import_preview_captures_file_and_writes_original_background_target(self):
        from bson import json_util
        from bson.binary import Binary
        from pathlib import Path

        panel, draft, editor, _identifier = self._open_fixture_document_editor()
        editor.close()
        path = Path(self.temp.name) / 'import.json'
        path.write_text(json_util.dumps([{'_id': 5, 'binary': Binary(b'\x00\xff', 2)}, {'name': 'generated'}]), encoding='utf-8')
        count = len(self.db_calls)
        panel.import_docs_btn.click()
        dialog = panel._transfer_dialog
        dialog.path.setText(str(path))
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertIsNotNone(dialog.plan, dialog.status.text())
        self.assertEqual(dialog.plan.generated_ids, 1)
        self.assertEqual(len(self.db_calls), count)
        self.assertFalse(any(op[0] == 'insert_one' for op in self.mongo_operations))
        path.write_text('invalid after preview', encoding='utf-8')
        second = self._add_second_profile_draft(panel, 'mongo')
        dialog.apply_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        writes = [op for op in self.mongo_operations if op[0] == 'insert_one']
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0][2]['binary'], Binary(b'\x00\xff', 2))
        self.assertEqual(self.db_calls[-1][1], draft.target.connection_id)
        self.assertIs(panel.nosql_draft_tabs.current_draft, second)
        self.assertIn('Confirmed 2 / 2', dialog.status.text())
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.apply()
        self.assertEqual(len([op for op in self.mongo_operations if op[0] == 'insert_one']), 2)
        dialog.close()

    def test_mongo_transfer_selected_page_and_whole_query_are_distinct_and_no_count(self):
        from bson import json_util
        from pathlib import Path

        panel, _draft, editor, identifier = self._open_fixture_document_editor(sorted_rows=True)
        editor.close()
        for scope, expected in (('selected', 1), ('page', 2), ('query', 3)):
            count = len(self.db_calls)
            panel.export_docs_btn.click()
            dialog = panel._transfer_dialog
            dialog.scope.setCurrentIndex(dialog.scope.findData(scope))
            path = Path(self.temp.name) / f'{scope}.jsonl'
            dialog.path.setText(str(path))
            if scope == 'query':
                self.mongo_documents = [{'_id': value} for value in range(3)]
                panel.query_input.setPlainText('invalid after capture')
            dialog.apply_btn.click()
            self.assertTrue(self._wait(lambda: dialog._worker is None))
            self.assertTrue(path.exists(), dialog.status.text())
            rows = [json_util.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows), expected)
            self.assertEqual(len(self.db_calls) - count, int(scope == 'query'))
            self.assertIn(f'Export complete: {expected}', dialog.status.text())
            if scope == 'selected':
                self.assertEqual(rows[0]['_id'], identifier)
            dialog.close()
        self.assertFalse(any(op[0] == 'count' for op in self.mongo_operations))
        self.assertEqual(self.mongo_operations[-1], ('cursor_close', 'records'))

    def test_mongo_transfer_failed_repreview_clears_plan_and_changed_profile_blocks_insert(self):
        from pathlib import Path

        panel, _draft, editor, _identifier = self._open_fixture_document_editor()
        editor.close()
        path = Path(self.temp.name) / 'import.json'
        path.write_text('[{"_id":7}]', encoding='utf-8')
        panel.import_docs_btn.click()
        dialog = panel._transfer_dialog
        dialog.path.setText(str(path))
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertTrue(dialog.apply_btn.isEnabled())
        path.write_text('invalid', encoding='utf-8')
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertIsNone(dialog.plan)
        self.assertFalse(dialog.apply_btn.isEnabled())
        path.write_text('[{"_id":7}]', encoding='utf-8')
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.profiles[0] = {**self.profiles[0], 'host': 'changed.invalid'}
        count = len(self.db_calls)
        dialog.apply_btn.click()
        self.assertEqual(len(self.db_calls), count)
        self.assertIn('changed or is unavailable', dialog.status.text())
        self.assertFalse(any(op[0] == 'insert_one' for op in self.mongo_operations))
        dialog.close()

    def test_mongo_transfer_import_stop_keeps_owner_until_insert_returns(self):
        from pathlib import Path
        from PyQt6 import sip

        panel, _draft, editor, _identifier = self._open_fixture_document_editor()
        editor.close()
        path = Path(self.temp.name) / 'import.json'
        path.write_text('[{"_id":8},{"_id":9}]', encoding='utf-8')
        panel.import_docs_btn.click()
        dialog = panel._transfer_dialog
        dialog.path.setText(str(path))
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        entered, release = threading.Event(), threading.Event()
        original = _FakeMongoCollection.insert_one
        def blocked(collection, document):
            entered.set()
            if not release.wait(4):
                raise RuntimeError('SYNTHETIC_TEST_RELEASE_TIMEOUT')
            return original(collection, document)
        with patch.object(_FakeMongoCollection, 'insert_one', blocked):
            dialog.apply_btn.click()
            worker = dialog._worker
            try:
                self.assertTrue(self._wait(entered.is_set))
                dialog.reject()
                self._flush()
                self.assertIn(worker, panel._legacy_workers)
                self.assertTrue(worker.isRunning())
                self.assertFalse(sip.isdeleted(dialog))
            finally:
                release.set()
            self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
            self.assertTrue(self._wait(lambda: sip.isdeleted(dialog)))
        self.assertEqual(len([op for op in self.mongo_operations if op[0] == 'insert_one']), 1)
        self.assertTrue(panel._page_stale)

    def test_mongo_document_edit_stop_after_write_start_reports_effect_and_keeps_owner(self):
        from PyQt6 import sip

        panel, draft, dialog, _identifier = self._open_fixture_document_editor()
        dialog.editor.setPlainText(dialog.editor.toPlainText().replace('"visible": "a"', '"visible": "changed"'))
        dialog.preview_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        entered, release = threading.Event(), threading.Event()
        replace_one = _FakeMongoCollection.replace_one

        def blocked(collection, *args, **kwargs):
            entered.set()
            release.wait(3)
            return replace_one(collection, *args, **kwargs)

        with patch.object(_FakeMongoCollection, "replace_one", new=blocked):
            try:
                dialog.apply_btn.click()
                worker = panel._worker
                self.assertTrue(self._wait(entered.is_set))
                self.assertTrue(worker.mutation_started)
                dialog.reject()
                self.assertFalse(sip.isdeleted(dialog))
                self.assertIn(worker, panel._legacy_workers)
            finally:
                release.set()
                self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        self.assertEqual(self.mongo_edit_documents[0]["visible"], "changed")
        self.assertIn("modified 1", panel.query_status_band.text().lower())
        self.assertTrue(self._wait(lambda: sip.isdeleted(dialog)))

    def test_typed_history_restore_replays_captured_bson_arguments_through_real_worker(self):
        panel, draft = self._make_panel("mongo")
        self.assertIsNotNone(draft.target)
        raw = "db.records.find({})"
        object_id = ObjectId("507f1f77bcf86cd799439011")
        captured_time = datetime(2026, 10, 7, 8, 30, tzinfo=timezone.utc)
        amount = Decimal128("12.50")
        snapshot = capture_mongo_run(
            draft.target,
            raw,
            raw,
            action="query",
            mode=draft.mode,
            database=draft.target.database,
            collection="records",
            filter={"_id": object_id, "created_at": captured_time, "amount": amount},
            projection={"amount": 1},
            sort={"created_at": -1},
            skip=4,
            limit=7,
        )
        expected = snapshot.worker_arguments()
        capture = history_service.HistoryCapture(
            history_id=uuid.uuid4().hex,
            target=snapshot.target,
            text=snapshot.body,
            text_kind=history_service.WorkspaceTextKind.NOSQL,
            created_at="2026-10-07T00:00:00+00:00",
        )
        service = history_service.get_application_history_service()
        service.save_capture(capture, "COMPLETED", row_count=9)
        self._wait_saved()
        original_db_calls = len(self.db_calls)
        self._restore_from_actual_dialog(panel, capture.history_id)
        restored = panel.nosql_draft_tabs.current_draft
        self.assertTrue(restored.locked)
        self.assertTrue(restored.runnable)
        self.assertEqual(restored.target.connection_id, snapshot.target.connection_id)
        self.assertEqual(restored.target.profile_revision, snapshot.target.profile_revision)
        self.assertEqual(restored.target.database, snapshot.target.database)
        self.assertEqual(restored.target.schema, "records")
        self.assertEqual(panel.query_input.toPlainText(), raw)
        self.assertEqual(len(self.db_calls), original_db_calls, "history restore is profile-only and does not connect")

        from tools.db_mongo_ops import parse_mongo_query

        with patch("panels.db_mongodb_panel.parse_mongo_query", wraps=parse_mongo_query) as parser:
            panel.query_btn.click()
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
            parser.assert_not_called()
        arguments = worker.kwargs
        self.assertEqual(arguments["collection"], expected["collection"])
        self.assertIsInstance(arguments["filter"]["_id"], ObjectId)
        self.assertEqual(arguments["filter"]["_id"], object_id)
        self.assertIsInstance(arguments["filter"]["created_at"], datetime)
        self.assertEqual(arguments["filter"]["created_at"], captured_time)
        self.assertIsInstance(arguments["filter"]["amount"], Decimal128)
        self.assertEqual(arguments["filter"]["amount"], amount)
        self.assertEqual(arguments["projection"], {"amount": 1})
        self.assertEqual(arguments["sort"], {"created_at": -1})
        self.assertEqual((arguments["skip"], arguments["limit"]), (4, 7))
        find_call = next(op for op in self.mongo_operations if op[0] == "find")
        self.assertEqual(find_call[1], "records")
        self.assertEqual(find_call[2]["_id"], object_id)
        self.assertEqual(find_call[2]["amount"], amount)
        self._wait_saved()

    def test_mongo_shell_dispatch_uses_one_parse_and_best_effort_history_keeps_store_guard(self):
        panel, _draft = self._make_panel("mongo")
        panel.side_tabs.setCurrentIndex(2)
        raw = '  db.records.find({"status":"TEMP"}).sort({"created_at":-1}).limit(3)  '
        panel.cmd_input.setText(raw)
        from tools.db_mongo_ops import parse_mongo_query

        with patch("panels.db_mongodb_panel.parse_mongo_query", wraps=parse_mongo_query) as parser:
            panel.cmd_btn.click()
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
            self.assertEqual(parser.call_count, 1)
        self.assertEqual(worker.nosql_draft_id, panel.nosql_draft_tabs.current_draft.draft_id)
        self.assertEqual(worker.kind, "command")
        self.assertEqual(worker.kwargs["collection"], "records")
        self.assertEqual(worker.kwargs["filter"], {"status": "TEMP"})
        self.assertEqual(worker.kwargs["sort"], {"created_at": -1})
        self.assertEqual(worker.kwargs["limit"], 3)
        self.assertEqual(panel.cmd_input.text(), "", "only an accepted Shell dispatch clears its input")
        self._wait_saved()
        row = self._history_rows()[0]
        snapshot = decode_nosql_workspace(row.target, row.text)
        self.assertEqual(snapshot.action, "command")
        self.assertEqual(snapshot.editor_text, raw)
        self.assertIsNone(row.row_count, "Mongo Shell does not infer a Documents count")

        # The TEMP-only credential-shaped query reaches the fake worker, but
        # the unchanged WorkspaceStore body guard rejects its history envelope.
        panel.side_tabs.setCurrentIndex(0)
        guarded = '{"collection":"records","filter":{"password":"TEMP"}}'
        panel.query_input.setPlainText(guarded)
        service = history_service._APPLICATION_HISTORY_SERVICE
        failures: list[str] = []
        service.save_failed.connect(failures.append)
        panel.query_btn.click()
        guarded_worker = panel._worker
        self.assertIsNotNone(guarded_worker)
        self.assertTrue(self._wait(lambda: guarded_worker.isFinished() and guarded_worker not in panel._legacy_workers))
        self.assertEqual(
            panel.query_input.toPlainText(), guarded,
            "Documents keeps its query visible for paging; history failure does not dispatch it again",
        )
        self.assertTrue(self._wait(lambda: bool(failures)), "the rejected history write reports a safe state")
        self.assertIn(failures[-1], {"CREDENTIAL_TEXT_REJECTED", "SAVE_FAILED"})
        self.assertEqual(len(self._history_rows()), 1, "credential guard rejected the second TEMP envelope")
        self.assertEqual(len(self.db_calls), 2, "the history failure did not launch an extra DB worker")

    def test_mongo_exact_page_count_type_and_unstarted_history_sink_detach(self):
        panel, _draft = self._make_panel("mongo")
        panel.query_input.setPlainText('{"collection":"records","filter":{"status":"TEMP"}}')
        from panels.db_mongodb_panel import _MongoWorker
        original_emit = _MongoWorker._emit_completed

        def emit_bool_count(worker, kind, payload):
            if kind == "query":
                payload = {**payload, "row_count": True}
            original_emit(worker, kind, payload)

        with patch.object(_MongoWorker, "_emit_completed", new=emit_bool_count):
            panel.query_btn.click()
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
        self.assertFalse(any(operation[0] == "count" for operation in self.mongo_operations))
        self._wait_saved()
        self.assertIsNone(self._history_rows()[0].row_count, "bool is not an exact nonnegative integer count")

        panel.query_input.setPlainText('{"collection":"records","filter":{"status":"TEMP"}}')
        with patch("panels.db_mongodb_panel._MongoWorker.start", side_effect=RuntimeError("TEMP_START_FAILURE")):
            panel.query_btn.click()
        service = history_service._APPLICATION_HISTORY_SERVICE
        self.assertEqual(service._nosql_sinks, {}, "a worker rejected before start cannot hold drain open")
        self.assertIsNone(panel._worker)
        self.assertNotEqual(panel.query_input.toPlainText(), "", "pre-accept start failure keeps editor text")
        self.assertEqual(len(self.db_calls), 1, "a synchronously rejected worker made no fake DB connection")

    def test_mongo_delete_confirmation_names_the_pinned_target_and_can_cancel(self):
        panel, _draft = self._make_panel("mongo")
        panel.query_input.setPlainText('{"collection":"records","filter":{"status":"TEMP"}}')
        db_calls_before = len(self.db_calls)
        with patch("panels.db_mongodb_panel.confirm_action", return_value=False) as confirm:
            panel.del_btn.click()
        confirm.assert_called_once()
        prompt = confirm.call_args.args[2]
        self.assertIn("temp-nosql-mongo/temp_records.records", prompt)
        self.assertIsNone(panel._worker, "declining the existing destructive confirmation starts no worker")
        self.assertEqual(len(self.db_calls), db_calls_before)

    def test_independent_draft_tabs_keep_their_editor_text_and_do_not_connect(self):
        for provider in ("redis", "mongo"):
            panel, first = self._make_panel(provider)
            calls_before = len(self.db_calls)
            if provider == "redis":
                panel.cmd_input.setText("PING FIRST")
            else:
                panel.query_input.setPlainText('{"collection":"records","filter":{"draft":"FIRST"}}')
                panel.cmd_input.setText("db.records.find({\"draft\":\"FIRST\"})")
            panel.nosql_draft_tabs.new_button.click()
            second = panel.nosql_draft_tabs.current_draft
            self.assertIsNotNone(second)
            self.assertNotEqual(first.draft_id, second.draft_id)
            if provider == "redis":
                self.assertEqual(panel.cmd_input.text(), "")
                panel.cmd_input.setText("PING SECOND")
            else:
                self.assertEqual(panel.query_input.toPlainText(), "")
                self.assertEqual(panel.cmd_input.text(), "")
                panel.query_input.setPlainText('{"collection":"records","filter":{"draft":"SECOND"}}')
                panel.cmd_input.setText("db.records.find({\"draft\":\"SECOND\"})")
            self.assertTrue(panel.nosql_draft_tabs.select_draft(first.draft_id))
            if provider == "redis":
                self.assertEqual(panel.cmd_input.text(), "PING FIRST")
            else:
                self.assertIn('"FIRST"', panel.query_input.toPlainText())
                self.assertIn('"FIRST"', panel.cmd_input.text())
            self.assertTrue(panel.nosql_draft_tabs.select_draft(second.draft_id))
            if provider == "redis":
                self.assertEqual(panel.cmd_input.text(), "PING SECOND")
            else:
                self.assertIn('"SECOND"', panel.query_input.toPlainText())
                self.assertIn('"SECOND"', panel.cmd_input.text())
            self.assertEqual(len(self.db_calls), calls_before, "draft creation and tab switches do not connect")
            self.assertTrue(self._wait(panel.agent_shutdown_cleanup))
            panel.close()
            panel.deleteLater()
            self.panel = None
            self._flush()

    def _open_redis_export_fixture(self, *, ambiguous=False, large=False):
        errors = []
        for name in ('show_error', 'show_warning'):
            self.stack.enter_context(patch(f'panels.db_redis_panel.{name}', side_effect=lambda *args: errors.append(args[-1])))
        original_profile = self._profile
        self.stack.enter_context(patch.object(self, '_profile', side_effect=lambda provider, identity=None:
            {**original_profile(provider, identity=identity), 'database': 2 if identity is None else 0}))
        keys = ['中'.encode('gb18030'), '中'.encode('utf-8')] if ambiguous else [b'key']
        self.redis_export_values = {key: (b'\x00\xff' * 50_000 if large else key + b'\x00\xff') for key in keys}
        panel, draft = self._make_panel('redis')
        generation = panel._scan.start('*')
        panel._run_worker('scan', dict(self.profiles[0]), generation=generation,
                          pattern='*', cursor=0, limit=2000)
        self.assertTrue(panel._legacy_workers)
        self.assertTrue(self._wait(lambda: not panel._legacy_workers))
        self.assertEqual(errors, [])
        self.assertEqual(panel.key_list.count(), 1)
        panel.key_list.setCurrentRow(0)
        panel.export_key_btn.click()
        self.assertEqual(errors, [])
        self.assertIsNotNone(panel._export_dialog)
        return panel, draft, panel._export_dialog

    def test_redis_export_ambiguous_raw_key_is_explicit_and_background_target_is_fixed(self):
        import base64
        from pathlib import Path

        panel, draft, dialog = self._open_redis_export_fixture(ambiguous=True)
        path = Path(self.temp.name) / 'key.json'
        dialog.path.setText(str(path))
        self.assertFalse(dialog.export_btn.isEnabled(), 'Ambiguous display name requires an explicit raw-key choice')
        dialog.keys.setCurrentIndex(1)
        key = dialog.keys.currentData()
        self.assertIs(type(key), bytes)
        second = self._add_second_profile_draft(panel, 'redis')
        dialog.export_btn.click()
        self.assertTrue(self._wait(lambda: dialog._worker is None))
        self.assertTrue(path.exists(), dialog.status.text())
        output = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(base64.b64decode(output['key']['base64']), key)
        self.assertEqual(base64.b64decode(output['records'][0]['chunk']['base64']), self.redis_export_values[key])
        self.assertEqual(output['database'], '2')
        self.assertEqual(self.redis_open_profiles[-1]['database'], 2)

        self.assertEqual(self.db_calls[-1][1], draft.target.connection_id)
        self.assertIs(panel.nosql_draft_tabs.current_draft, second)
        self.assertIn('Export complete', dialog.status.text())
        self.assertFalse(dialog.export_btn.isEnabled())
        calls = len(self.db_calls)
        dialog.apply()
        self.assertEqual(len(self.db_calls), calls)
        self.assertTrue(all(call[-1] != threading.get_ident() for call in self.redis_export_reads))
        dialog.close()

    def test_redis_export_start_failure_can_retry_and_profile_change_blocks_read(self):
        from pathlib import Path

        panel, _draft, dialog = self._open_redis_export_fixture()
        path = Path(self.temp.name) / 'key.json'
        dialog.path.setText(str(path))
        with patch('panels.db_redis_panel._RedisWorker.start', side_effect=RuntimeError('SYNTHETIC_START_REJECTED')):
            dialog.export_btn.click()
        self.assertIsNone(dialog._worker)
        self.assertTrue(dialog.export_btn.isEnabled())
        self.profiles[0] = {**self.profiles[0], 'host': 'changed.invalid'}
        count = len(self.db_calls)
        dialog.export_btn.click()
        self.assertEqual(len(self.db_calls), count)
        self.assertFalse(path.exists())
        self.assertIn('changed or is unavailable', dialog.status.text())
        dialog.close()

    def test_redis_export_stop_retains_owner_and_cleans_unpublished_file(self):
        from pathlib import Path
        from PyQt6 import sip

        panel, _draft, dialog = self._open_redis_export_fixture(large=True)
        path = Path(self.temp.name) / 'key.json'
        dialog.path.setText(str(path))
        entered, release = threading.Event(), threading.Event()
        closed = []
        original = _FakeRedisConnection.getrange
        def blocked(connection, key, start, stop):
            entered.set()
            if not release.wait(4):
                raise RuntimeError('SYNTHETIC_TEST_RELEASE_TIMEOUT')
            return original(connection, key, start, stop)
        with patch.object(_FakeRedisConnection, 'getrange', blocked), patch('panels.db_redis_panel.close_connection', side_effect=closed.append):
            dialog.export_btn.click()
            worker = dialog._worker
            try:
                self.assertTrue(self._wait(entered.is_set))
                dialog.reject()
                self._flush()
                self.assertTrue(worker.isRunning())
                self.assertIn(worker, panel._legacy_workers)
                self.assertFalse(sip.isdeleted(dialog))
                self.assertEqual(closed, [])
            finally:
                release.set()
            self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
            self.assertTrue(self._wait(lambda: sip.isdeleted(dialog)))
        self.assertEqual(len(closed), 1)
        self.assertFalse(path.exists())
        self.assertEqual(list(Path(self.temp.name).glob('.pengtools-redis-*')), [])
        self.assertEqual(len(self.redis_export_reads), 1)

    def test_redis_value_binary_identity_chunk_navigation_and_ttl_zero(self):
        panel, _draft, export = self._open_redis_export_fixture(ambiguous=True, large=True)
        export.close()
        panel._on_key_list_clicked(panel.key_list.currentItem())
        self.assertIsNone(panel._value_context)
        self.assertFalse(panel.refresh_val_btn.isEnabled())
        panel.raw_key_combo.setCurrentIndex(1)
        self.assertTrue(self._wait(lambda: panel._worker is None))
        key = panel.raw_key_combo.currentData()
        first = panel._value_page
        self.assertIsNotNone(first, panel.value_page_label.text())
        self.assertEqual(first.context.key, key)
        self.assertEqual(first.ttl, 0)
        self.assertEqual(first.total, 100_000)
        self.assertEqual(panel._raw_string_bytes, self.redis_export_values[key][:65536])
        self.assertTrue(panel.value_next_btn.isEnabled())
        self.assertFalse(panel.value_prev_btn.isEnabled())
        panel.value_next_btn.click()
        self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertEqual(panel._value_page_index, 1)
        self.assertEqual(panel._value_page.position, 65536)
        self.assertEqual(panel._raw_string_bytes, self.redis_export_values[key][65536:])
        self.assertFalse(panel.value_next_btn.isEnabled())
        self.assertTrue(panel.value_prev_btn.isEnabled())
        panel.value_prev_btn.click()
        self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertEqual(panel._value_page_index, 0)
        self.assertEqual(panel._raw_string_bytes, first.data)
        self.assertTrue(all(call[0] == key and call[2] - call[1] < 65536 for call in self.redis_export_reads))
        self.assertEqual(self.redis_open_profiles[-1]['database'], 2)
        calls = len(self.db_calls)
        panel.set_language('zh')
        self.assertIn('当前块', panel.value_page_label.text())
        panel.set_language('en')
        self.assertIn('current chunk', panel.value_page_label.text())
        self.assertEqual(len(self.db_calls), calls, 'Changing language must not requery Redis')

    def test_redis_value_failed_next_keeps_page_and_close_blocks_controls_until_physical_finish(self):
        panel, _draft, export = self._open_redis_export_fixture(large=True)
        export.close()
        panel._on_key_list_clicked(panel.key_list.currentItem())
        self.assertTrue(self._wait(lambda: panel._worker is None))
        first = panel._value_page
        with patch.object(_FakeRedisConnection, 'getrange', side_effect=RuntimeError('SYNTHETIC_READ_FAILURE')):
            panel.value_next_btn.click()
            self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertIs(panel._value_page, first)
        self.assertEqual(panel._value_page_index, 0)
        self.assertEqual(panel._raw_string_bytes, first.data)
        self.assertIn('Previous result retained', panel.value_page_label.text())
        calls = len(self.db_calls)
        panel.set_language('zh')
        self.assertIn('上次结果未更新', panel.value_page_label.text())
        panel.set_language('en')
        self.assertIn('Previous result retained', panel.value_page_label.text())
        self.assertTrue(panel._value_stale)
        self.assertEqual(len(self.db_calls), calls)
        entered, release = threading.Event(), threading.Event()
        def close_blocked(connection):
            entered.set()
            release.wait(4)
        with patch('panels.db_redis_panel.close_connection', side_effect=close_blocked):
            panel.value_next_btn.click()
            worker = panel._worker
            try:
                self.assertTrue(self._wait(entered.is_set))
                self.assertTrue(self._wait(lambda: panel._value_page_index == 1))
                self.assertTrue(worker.isRunning())
                self.assertFalse(panel.value_prev_btn.isEnabled())
                self.assertFalse(panel.refresh_val_btn.isEnabled())
            finally:
                release.set()
            self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertEqual(panel._value_page.position, 65536)
        self.assertFalse(panel._value_stale)
        self.assertTrue(panel.value_prev_btn.isEnabled())
        self.assertTrue(panel.refresh_val_btn.isEnabled())

    def test_redis_value_latest_selection_waits_for_old_owner_and_discards_old_result(self):
        from PyQt6.QtWidgets import QListWidgetItem

        panel, _draft, export = self._open_redis_export_fixture(large=True)
        export.close()
        entered, release = threading.Event(), threading.Event()
        original = _FakeRedisConnection.getrange
        def blocked(connection, key, start, stop):
            if key == b'key':
                entered.set()
                release.wait(4)
            return original(connection, key, start, stop)
        self.redis_export_values[b'next'] = b'NEXT'
        panel._key_identities['next'] = {b'next'}
        with patch.object(_FakeRedisConnection, 'getrange', blocked):
            panel._on_key_list_clicked(panel.key_list.currentItem())
            worker = panel._worker
            try:
                self.assertTrue(self._wait(entered.is_set))
                item = QListWidgetItem('next')
                panel.key_list.addItem(item)
                panel.key_list.setCurrentItem(item)
                calls = len(self.db_calls)
                panel._on_key_list_clicked(item)
                self.assertIsNotNone(panel._pending_value_request)
                self.assertEqual(len(self.db_calls), calls)
                self.assertEqual(panel.string_value.toPlainText(), '')
                self.assertIsNone(panel._value_page)
            finally:
                release.set()
            self.assertTrue(self._wait(lambda: panel._value_page is not None and panel._value_page.context.key == b'next' and panel._worker is None))
        self.assertNotIn(worker, panel._legacy_workers)
        self.assertEqual(panel._raw_string_bytes, b'NEXT')
        self.assertEqual(len(self.db_calls), calls + 1)

    def test_redis_value_hash_cursor_previous_and_profile_change_do_not_retarget(self):
        panel, _draft, export = self._open_redis_export_fixture()
        export.close()
        calls = []
        def hscan(connection, key, *, cursor, count):
            calls.append((key, cursor, count))
            return (7, {b'\xff': b'\x00'}) if cursor == 0 else (0, {b'last': b'LAST'})
        with patch.object(_FakeRedisConnection, 'type', return_value=b'hash'), patch.object(_FakeRedisConnection, 'hscan', new=hscan, create=True):
            panel._on_key_list_clicked(panel.key_list.currentItem())
            self.assertTrue(self._wait(lambda: panel._worker is None))
            self.assertEqual(panel._value_page.data, ((b'\xff', b'\x00'),))
            panel.value_next_btn.click()
            self.assertTrue(self._wait(lambda: panel._worker is None))
            self.assertEqual(panel._value_positions, [0, 7])
            self.assertFalse(panel.value_next_btn.isEnabled())
            panel.value_prev_btn.click()
            self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertEqual([value[1] for value in calls], [0, 7, 0])
        page = panel._value_page
        count = len(self.db_calls)
        self.profiles[0] = {**self.profiles[0], 'host': 'changed.invalid'}
        panel.refresh_val_btn.click()
        self.assertEqual(len(self.db_calls), count)
        self.assertIs(panel._value_page, page)
        self.assertIn('changed or unavailable', panel.value_page_label.text())
        self._add_second_profile_draft(panel, 'redis')
        self.assertIsNone(panel._value_context)
        self.assertEqual(panel.hash_table.rowCount(), 0)

    def test_redis_value_delete_uses_frozen_binary_key_and_cannot_clear_another_draft(self):
        panel, draft, export = self._open_redis_export_fixture(ambiguous=True)
        export.close()
        panel._on_key_list_clicked(panel.key_list.currentItem())
        panel.raw_key_combo.setCurrentIndex(1)
        self.assertTrue(self._wait(lambda: panel._worker is None))
        context = panel._value_context
        entered, release = threading.Event(), threading.Event()
        deleted = []
        def remove(connection, key):
            deleted.append(key)
            entered.set()
            release.wait(4)
            return 1
        with patch.object(_FakeRedisConnection, 'delete', new=remove, create=True), patch('panels.db_redis_panel.confirm_action', return_value=True):
            panel.del_btn.click()
            worker = panel._worker
            try:
                self.assertTrue(self._wait(entered.is_set))
                second = self._add_second_profile_draft(panel, 'redis')
                calls = len(self.db_calls)
            finally:
                release.set()
            self.assertTrue(self._wait(lambda: panel._worker is None))
        self.assertEqual(deleted, [context.key])
        self.assertEqual(worker.item['database'], 2)
        self.assertEqual(worker.nosql_draft_id, draft.draft_id)
        self.assertIs(panel.nosql_draft_tabs.current_draft, second)
        self.assertIsNone(panel._value_page)
        self.assertEqual(panel.key_name.text(), '')
        self.assertEqual(len(self.db_calls), calls)

    def test_redis_busy_shutdown_preserves_input_and_app_sink_records_unknown_after_ui_disconnect(self):
        panel, draft = self._make_panel("redis")
        self.block_redis = True
        self.redis_release.clear()
        raw = "  GET TEMP_KEY  "
        panel.cmd_input.setText(raw)
        panel.cmd_btn.click()
        worker = panel._worker
        self.assertIsNotNone(worker)
        self.assertTrue(self.redis_entered.wait(3.0), "actual QThread must reach the injected fake command runner")
        self.assertEqual(worker.kwargs["sql"], "GET TEMP_KEY")
        self.assertTrue(self._wait(lambda: history_service._APPLICATION_HISTORY_SERVICE is not None))
        service = history_service._APPLICATION_HISTORY_SERVICE
        self.assertIn(id(worker), service._nosql_sinks)

        panel.cmd_input.setText("PING")
        panel.cmd_btn.click()
        self.assertEqual(panel.cmd_input.text(), "PING", "busy rejection keeps the current text")
        panel.workbench_shutdown_cleanup()
        panel.cmd_input.setText("PING AFTER SHUTDOWN")
        panel._run_command()
        self.assertEqual(panel.cmd_input.text(), "PING AFTER SHUTDOWN", "shutdown rejection keeps the current text")
        self.assertFalse(service.drain(), "global history drain remains blocked until actual QThread.finished")
        self.redis_release.set()
        self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
        self._wait_saved()
        rows = self._history_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state_code, "UNKNOWN", "cancelled worker emitted no completed/failed terminal")
        self.assertIsNone(rows[0].row_count, "Redis row count is never inferred from payload text")
        snapshot = decode_nosql_workspace(rows[0].target, rows[0].text)
        self.assertEqual(snapshot.editor_text, raw)
        self.assertEqual(snapshot.dispatched_text, "GET TEMP_KEY")
        self.assertTrue(self._wait(service.drain), "finished terminal save and history controller must drain")
        history_service.reset_application_history_service_for_tests()

    def _assert_redis_busy_other_draft_does_not_receive_terminal(self, *, fail: bool):
        panel, first = self._make_panel("redis")
        second = self._add_second_profile_draft(panel, "redis")
        self.assertTrue(panel.nosql_draft_tabs.select_draft(first.draft_id))
        self.block_redis = True
        self.redis_fail_after_release = fail
        self.redis_release.clear()
        panel.cmd_input.setText("GET TEMP_A")
        panel.cmd_btn.click()
        worker = panel._worker
        self.assertIsNotNone(worker)
        self.assertEqual(worker.nosql_draft_id, first.draft_id)
        self.assertTrue(self.redis_entered.wait(3.0), "actual QThread must reach the TEMP fake command runner")

        self.assertTrue(panel.nosql_draft_tabs.select_draft(second.draft_id))
        self.assertEqual(panel.query_status_band.text(), "")
        panel.cmd_input.setText("PING TEMP_B")
        panel.cmd_output.appendPlainText("B_SENTINEL")
        panel.cmd_btn.click()
        self.assertIs(panel._worker, worker, "busy rejection leaves A as the actual worker")
        self.assertEqual(worker.nosql_draft_id, first.draft_id, "busy B cannot retarget A's terminal")
        self.assertEqual(panel.cmd_input.text(), "PING TEMP_B", "busy B input remains available")
        self.assertEqual(len(self.redis_commands), 1, "busy B starts no second DB command")

        self.redis_release.set()
        self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
        self._wait_saved()
        self.assertEqual(panel.nosql_draft_tabs.current_draft.draft_id, second.draft_id)
        self.assertEqual(panel.cmd_output.toPlainText(), "B_SENTINEL")
        self.assertEqual(panel.cmd_input.text(), "PING TEMP_B")
        self.assertEqual(len(self.redis_commands), 1)
        rows = self._history_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].target.connection_id, first.target.connection_id)
        self.assertEqual(rows[0].state_code, "FAILED" if fail else "COMPLETED")

    def test_redis_busy_second_draft_does_not_receive_first_completion(self):
        self._assert_redis_busy_other_draft_does_not_receive_terminal(fail=False)

    def test_redis_busy_second_draft_does_not_receive_first_failure(self):
        self._assert_redis_busy_other_draft_does_not_receive_terminal(fail=True)

    def test_mongo_busy_second_draft_does_not_receive_first_query(self):
        panel, first = self._make_panel("mongo")
        second = self._add_second_profile_draft(panel, "mongo")
        self.assertTrue(panel.nosql_draft_tabs.select_draft(first.draft_id))
        entered = threading.Event()
        release = threading.Event()

        def blocked_find(*_args, **_kwargs):
            entered.set()
            release.wait(4.0)
            return [{"_id": "A_RESULT"}]

        panel.query_input.setPlainText('{"collection":"records","filter":{"owner":"A"}}')
        with patch("panels.db_mongodb_panel.find_docs", side_effect=blocked_find):
            panel.query_btn.click()
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertEqual(worker.nosql_draft_id, first.draft_id)
            self.assertTrue(entered.wait(3.0), "actual Mongo QThread must reach the TEMP blocked query")

            self.assertTrue(panel.nosql_draft_tabs.select_draft(second.draft_id))
            b_query = '{"collection":"records","filter":{"owner":"B"}}'
            panel.query_input.setPlainText(b_query)
            panel.query_btn.click()
            self.assertIs(panel._worker, worker, "busy rejection keeps the accepted A worker")
            self.assertEqual(worker.nosql_draft_id, first.draft_id)
            self.assertEqual(panel.query_input.toPlainText(), b_query)
            self.assertEqual(panel.query_status_band.text(), "")
            release.set()
            self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))

        self._wait_saved()
        self.assertEqual(panel.nosql_draft_tabs.current_draft.draft_id, second.draft_id)
        self.assertEqual(panel.query_input.toPlainText(), b_query)
        self.assertEqual(panel._docs, [], "A's completed docs must not render in B")
        self.assertEqual(panel.doc_table.rowCount(), 0)
        self.assertEqual(panel.page_label.text(), "")
        self.assertEqual(len(self.db_calls), 1, "busy B starts no DB worker")
        rows = self._history_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].target.connection_id, first.target.connection_id)
        self.assertEqual(rows[0].state_code, "COMPLETED")

    def _assert_mongo_write_does_not_requery_other_draft(self, kind: str):
        panel, first = self._make_panel("mongo")
        second = self._add_second_profile_draft(panel, "mongo")
        self.assertTrue(panel.nosql_draft_tabs.select_draft(first.draft_id))
        entered = threading.Event()
        release = threading.Event()

        def wait_write(*_args, **_kwargs):
            entered.set()
            release.wait(4.0)
            return "TEMP_INSERTED_ID" if kind == "insert" else 1

        panel.query_input.setPlainText('{"collection":"records","filter":{"owner":"A"}}')
        dialog_patch = patch(
            "PyQt6.QtWidgets.QInputDialog.getMultiLineText",
            return_value=('{"owner":"A"}', True),
        )
        worker_patch = patch(f"panels.db_mongodb_panel.{kind}_doc" if kind == "insert" else
                              "panels.db_mongodb_panel.delete_docs", side_effect=wait_write)
        with dialog_patch, worker_patch, patch("panels.db_mongodb_panel.show_info") as show_info:
            if kind == "insert":
                panel.insert_btn.click()
            else:
                with patch("panels.db_mongodb_panel.confirm_action", return_value=True):
                    panel.del_btn.click()
            worker = panel._worker
            self.assertIsNotNone(worker)
            self.assertEqual(worker.nosql_draft_id, first.draft_id)
            self.assertTrue(entered.wait(3.0), f"actual Mongo {kind} worker must reach its TEMP fake operation")
            self.assertTrue(panel.nosql_draft_tabs.select_draft(second.draft_id))
            b_query = '{"collection":"records","filter":{"owner":"B"}}'
            panel.query_input.setPlainText(b_query)
            release.set()
            self.assertTrue(self._wait(lambda: worker.isFinished() and worker not in panel._legacy_workers))
            self.assertEqual(panel.nosql_draft_tabs.current_draft.draft_id, second.draft_id)
            self.assertEqual(panel.query_input.toPlainText(), b_query)
            self.assertEqual(panel.query_status_band.text(), "")
            self.assertEqual(len(self.db_calls), 1, "write completion must not launch a query for draft B")
            show_info.assert_not_called()

    def test_mongo_insert_completion_after_tab_switch_does_not_requery_other_draft(self):
        self._assert_mongo_write_does_not_requery_other_draft("insert")

    def test_mongo_delete_completion_after_tab_switch_does_not_requery_other_draft(self):
        self._assert_mongo_write_does_not_requery_other_draft("delete")

    def test_long_english_stale_status_wraps_in_embedded_960_layout(self):
        from ui.theme_manager import ThemeManager

        theme_manager = ThemeManager.instance()
        prior_theme = theme_manager.theme_id
        prior_style = self.app.styleSheet()
        prior_palette = self.app.palette()
        prior_theme_property = self.app.property("ui_theme")
        prior_base_stylesheet = self.app.property("base_stylesheet")
        theme_manager.apply(self.app, "calm")
        workspace, panel = self._make_embedded_visual_panel("mongo", "en")
        try:
            panel.apply_layout_mode("narrow", True)
            workspace.apply_layout_mode("narrow", True)
            draft = panel.nosql_draft_tabs.current_draft
            draft.runnable = False
            draft.locked = True
            draft.validation_code = "PROFILE_REVISION_MISMATCH_FOR_RESTORED_MONGODB_HISTORY"
            panel.nosql_draft_tabs.set_status(draft)
            saved = []
            for widget in [workspace, *workspace.findChildren(QWidget)]:
                if not widget.isVisible():
                    continue
                saved.append((widget, widget.font()))
                font = QFont(widget.font())
                font.setPixelSize(21)
                widget.setFont(font)
            self._flush()
            workspace.resize(960, 640)
            self._flush()

            tabs = panel.nosql_draft_tabs
            label = tabs.status_label
            if QApplication.platformName() != "windows":
                self.skipTest("真实字体换行视觉断言只在 Windows 窗口平台运行（离屏平台字体度量不同）")
            self.assertEqual((workspace.width(), workspace.height()), (960, 640))
            self.assertEqual((panel.width(), panel.height()), (960, 640))
            self.assertEqual(QFontInfo(label.font()).pixelSize(), 21)
            self.assertTrue(label.wordWrap())
            self.assertIn(draft.validation_code, label.text().replace("\u200b", ""))
            self.assertIn(draft.validation_code, label.toolTip())
            measured = label.fontMetrics().boundingRect(
                QRect(0, 0, label.contentsRect().width(), 1000),
                Qt.TextFlag.TextWordWrap,
                label.text(),
            )
            self.assertLessEqual(
                measured.height(),
                label.contentsRect().height(),
                "the complete stale code must fit in the visible wrapped status label "
                f"(measure={measured.width()}x{measured.height()}, "
                f"contents={label.contentsRect().width()}x{label.contentsRect().height()}, "
                f"text={label.text()!r})",
            )
            bounds = {}
            for name, widget in (
                ("status", label),
                ("new", tabs.new_button),
                ("history", tabs.history_button),
            ):
                pos = widget.mapTo(workspace, widget.rect().topLeft())
                rect = [pos.x(), pos.y(), widget.width(), widget.height()]
                self.assertGreaterEqual(rect[0], 0, name)
                self.assertGreaterEqual(rect[1], 0, name)
                self.assertLessEqual(rect[0] + rect[2], workspace.width(), name)
                self.assertLessEqual(rect[1] + rect[3], workspace.height(), name)
                bounds[name] = rect
            self.assertLessEqual(
                bounds["status"][0] + bounds["status"][2],
                bounds["new"][0],
                "the visible stale reason must not overlap New draft",
            )
            screenshot_root = os.path.join(
                self.evidence_root, "windows-calm-repair",
            )
            os.makedirs(screenshot_root, exist_ok=True)
            screenshot_path = os.path.join(screenshot_root, "mongo_en_21px_stale_status.png")
            self.assertTrue(workspace.grab().save(screenshot_path))
            evidence = {
                "qpa": QApplication.platformName(),
                "device_pixel_ratio": workspace.devicePixelRatioF(),
                "workspace_size": [workspace.width(), workspace.height()],
                "host_size": [panel.width(), panel.height()],
                "status_font_pixel_size": QFontInfo(label.font()).pixelSize(),
                "status_text": label.text().replace("\u200b", ""),
                "status_tooltip": label.toolTip(),
                "status_required_text_bounds": [measured.width(), measured.height()],
                "status_contents_size": [label.contentsRect().width(), label.contentsRect().height()],
                "strip_bounds": bounds,
                "screenshot": os.path.basename(screenshot_path),
            }
            with open(os.path.join(screenshot_root, "stale_status_manifest.json"), "w", encoding="utf-8") as stream:
                json.dump(evidence, stream, ensure_ascii=False, indent=2, sort_keys=True)
            self._wait(panel.agent_shutdown_cleanup)
            workspace.close()
            workspace.deleteLater()
            self.panel = None
            self._flush()
        finally:
            theme_manager._theme_id = prior_theme
            self.app.setStyleSheet(prior_style)
            self.app.setPalette(prior_palette)
            self.app.setProperty("ui_theme", prior_theme_property)
            self.app.setProperty("base_stylesheet", prior_base_stylesheet)

    def test_visual_draft_tabs_matrix_and_reachable_workbench_regions(self):
        from ui.theme_manager import ThemeManager

        theme_manager = ThemeManager.instance()
        prior_theme = theme_manager.theme_id
        prior_style = self.app.styleSheet()
        prior_palette = self.app.palette()
        prior_theme_property = self.app.property("ui_theme")
        prior_base_stylesheet = self.app.property("base_stylesheet")
        theme_manager.apply(self.app, "calm")
        screenshot_root = os.path.join(self.evidence_root, "windows-calm-repair-ordinary")
        os.makedirs(screenshot_root, exist_ok=True)
        screenshot_rows = []
        try:
            for provider in ("redis", "mongo"):
                for language in ("zh", "en"):
                    workspace, panel = self._make_embedded_visual_panel(provider, language)
                    for pixels in (17, 21):
                        # Main-window breakpoints classify 960×640 as narrow
                        # with low-height enabled; DataCenter applies this mode
                        # to its production-created embedded host.
                        panel.apply_layout_mode("narrow", True)
                        workspace.apply_layout_mode("narrow", True)
                        if provider == "mongo":
                            # The two Mongo editor routes each get two locales
                            # at different body sizes while staying visible.
                            panel.side_tabs.setCurrentIndex(0 if pixels == 17 else 2)
                        saved = []
                        for widget in [workspace, *workspace.findChildren(QWidget)]:
                            if not widget.isVisible():
                                continue
                            saved.append((widget, widget.font()))
                            font = QFont(widget.font())
                            font.setPixelSize(pixels)
                            widget.setFont(font)
                        self._flush()
                        workspace.resize(960, 640)
                        self._flush()
                        self.assertEqual((workspace.width(), workspace.height()), (960, 640))
                        self.assertEqual((panel.width(), panel.height()), (960, 640))
                        if provider == "redis":
                            important = [
                                ("key filter", panel.key_filter),
                                ("key tree", panel.key_tree),
                                ("key list", panel.key_list),
                                ("command input", panel.cmd_input),
                                ("command output", panel.cmd_output),
                            ]
                        elif panel.side_tabs.currentIndex() == 0:
                            important = [
                                ("collection tree", panel.coll_tree),
                                ("Documents editor", panel.query_input),
                                ("Documents Run", panel.query_btn),
                                ("Documents result table", panel.doc_table),
                                ("Insert action", panel.insert_btn),
                                ("Delete action", panel.del_btn),
                            ]
                        else:
                            important = [
                                ("collection tree", panel.coll_tree),
                                ("Shell input", panel.cmd_input),
                                ("Shell Run", panel.cmd_btn),
                                ("Shell output", panel.cmd_output),
                            ]
                        bounds = []
                        for name, widget in important:
                            self.assertTrue(
                                widget.isVisible(),
                                f"{provider}/{language}/{pixels}px {name} is reachable",
                            )
                            font_info = QFontInfo(widget.font())
                            # 离屏平台无字体库时 QFontInfo 返回 -1：只校验可解析的字号，几何校验照常
                            if font_info.pixelSize() > 0:
                                self.assertEqual(font_info.pixelSize(), pixels)
                            pos = widget.mapTo(workspace, widget.rect().topLeft())
                            rect = [pos.x(), pos.y(), widget.width(), widget.height()]
                            self.assertGreaterEqual(rect[0], 0, name)
                            self.assertGreaterEqual(rect[1], 0, name)
                            self.assertLessEqual(rect[0] + rect[2], workspace.width(), name)
                            self.assertLessEqual(rect[1] + rect[3], workspace.height(), name)
                            bounds.append({
                                "name": name,
                                "object_name": widget.objectName(),
                                "font_family": font_info.family(),
                                "font_pixel_size": font_info.pixelSize(),
                                "bounds": rect,
                            })
                        image_name = f"{provider}_{language}_{pixels}px.png"
                        image_path = os.path.join(screenshot_root, image_name)
                        self.assertTrue(workspace.grab().save(image_path))
                        screen = QApplication.primaryScreen()
                        screenshot_rows.append({
                            "file": image_name,
                            "provider": provider,
                            "language": language,
                            "body_pixel_size": pixels,
                            "theme": self.app.property("ui_theme"),
                            "qpa": QApplication.platformName(),
                            "device_pixel_ratio": workspace.devicePixelRatioF(),
                            "logical_dpi": screen.logicalDotsPerInch() if screen else None,
                            "workspace_size": [workspace.width(), workspace.height()],
                            "host_size": [panel.width(), panel.height()],
                            "layout_mode": "narrow",
                            "low_height": True,
                            "important_widgets": bounds,
                        })
                        for widget, font in saved:
                            widget.setFont(font)
                        self._flush()
                    self.assertTrue(self._wait(panel.agent_shutdown_cleanup))
                    workspace.close()
                    workspace.deleteLater()
                    self.panel = None
                    self._flush()
            self.assertEqual(len(screenshot_rows), 8)
            with open(os.path.join(screenshot_root, "screen_manifest.json"), "w", encoding="utf-8") as stream:
                json.dump(screenshot_rows, stream, ensure_ascii=False, indent=2, sort_keys=True)
        finally:
            theme_manager._theme_id = prior_theme
            self.app.setStyleSheet(prior_style)
            self.app.setPalette(prior_palette)
            self.app.setProperty("ui_theme", prior_theme_property)
            self.app.setProperty("base_stylesheet", prior_base_stylesheet)


if __name__ == "__main__":
    unittest.main()
