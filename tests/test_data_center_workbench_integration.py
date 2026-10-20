"""Behaviour checks for the production workbench-to-read-only-Agent seam."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QBoxLayout, QWidget

from tools.schema_snapshot import connection_fingerprint
from tools.data_center.profile_host import DataCenterProfileHost
from ui.data_center.agent_controller import DataCenterAgentController


def _profile() -> dict:
    return {
        "id": "temp-mysql-1",
        "name": "TEMP MySQL",
        "provider": "mysql",
        "dialect": "mysql",
        "mode": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "username": "fake-user",
        "password": "",
        "database": "sales",
        "unknown_profile_field": {"keep": "as-is"},
    }


def _model(*, capability: str = "native_tools", model: str = "fake-agent") -> dict:
    return {
        "id": "temp-model-1",
        "name": "TEMP intranet model",
        "enabled": True,
        "base_url": "http://intranet.invalid/v1",
        "token": "TEMP_SECRET_NEVER_EXPOSE",
        "model": model,
        "agent_capability": capability,
        "max_tokens": 256,
        "unknown_model_field": "preserved",
    }


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }


class _ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []
        self.thread_ids = []
        self.returned_mappings = 0

    def stream(self, body, *, cancellation, deadline=None):
        self.bodies.append(dict(body))
        self.thread_ids.append(threading.get_ident())
        if not self.responses:
            raise AssertionError("unexpected model request")
        result = self.responses.pop(0)
        if isinstance(result, dict):
            self.returned_mappings += 1
        return result


class _BlockingTransport:
    """A cooperative model stream used to verify cancellation/cleanup."""

    def __init__(self):
        self.entered = threading.Event()
        self.cancel_seen = threading.Event()
        self.thread_ids: list[int] = []

    def stream(self, body, *, cancellation, deadline=None):
        del body, deadline
        self.thread_ids.append(threading.get_ident())
        self.entered.set()
        while not cancellation.is_cancelled():
            time.sleep(0.002)
        self.cancel_seen.set()
        if False:  # keep this a generator without emitting a post-cancel frame
            yield {}


class _QueryThenBlockingTransport:
    """Return one metadata/query answer, then hold the next run until cancel."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []
        self.entered = threading.Event()
        self.cancel_seen = threading.Event()

    def stream(self, body, *, cancellation, deadline=None):
        del deadline
        self.bodies.append(dict(body))
        if self.responses:
            return self.responses.pop(0)
        self.entered.set()
        while not cancellation.is_cancelled():
            time.sleep(0.002)
        self.cancel_seen.set()
        return {"choices": [{"message": {"role": "assistant", "content": "late cancelled response"}, "finish_reason": "stop"}]}


class _DelayedScriptedTransport:
    """Pause one model request so a real SQL-tab switch can be exercised."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()
        self.thread_ids: list[int] = []
        self.bodies = []

    def stream(self, body, *, cancellation, deadline=None):
        del deadline
        self.bodies.append(dict(body))
        self.thread_ids.append(threading.get_ident())
        if len(self.bodies) == 1:
            self.entered.set()
            while not self.release.wait(0.002):
                if cancellation.is_cancelled():
                    self.cancel_seen.set()
                    break
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = ()
        self._rows = []

    def execute(self, sql, parameters=None):
        text = str(sql)
        self.connection.events.append(("execute", text, parameters, threading.get_ident()))
        lowered = text.casefold()
        if "information_schema.tables" in lowered:
            self.description = (("TABLE_SCHEMA",), ("TABLE_NAME",), ("TABLE_TYPE",), ("TABLE_COMMENT",))
            self._rows = [("sales", "orders", "BASE TABLE", "temporary fixture")]
        elif lowered.startswith("select id from sales.orders"):
            self.description = (("id",),)
            self._rows = [(42,)]
        else:
            self.description = ()
            self._rows = []

    def fetchmany(self, size):
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def close(self):
        self.connection.events.append(("cursor_close", threading.get_ident()))


class _FakeConnection:
    def __init__(self):
        self.autocommit = True
        self.events = []
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.events.append(("rollback", threading.get_ident()))

    def close(self):
        self.closed = True
        self.events.append(("close", threading.get_ident()))


class _FakeMongoCollection:
    def __init__(self):
        self.find_calls: list[dict] = []

    def find(self, filter, projection=None, sort=None, skip=0, limit=None, batch_size=None, max_time_ms=None, allow_disk_use=None):
        self.find_calls.append({
            "filter": filter,
            "projection": projection,
            "sort": sort,
            "skip": skip,
            "limit": limit,
            "batch_size": batch_size,
            "max_time_ms": max_time_ms,
            "allow_disk_use": allow_disk_use,
        })
        return [{"_id": "TEMP_DOC_ID", "total": 19}]


class _FakeMongoDatabase:
    def __init__(self, collection):
        self.collection = collection
        self.lookups: list[str] = []

    def __getitem__(self, name):
        self.lookups.append(str(name))
        if str(name).casefold() != "orders":
            raise AssertionError("Mongo scope expanded beyond the fixed collection")
        return self.collection


class _FakeMongoClient:
    def __init__(self, database):
        self.database = database
        self.closed = False

    def __getitem__(self, name):
        if str(name) != "analytics":
            raise AssertionError("Mongo database changed after target capture")
        return self.database

    def close(self):
        self.closed = True


class DataCenterWorkbenchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pengtools-dc-agent-")
        self.root = Path(self.temp.name)
        self.profile = _profile()
        self.model = _model()
        self.connection_path = self.root / "connections.json"
        self.model_path = self.root / "models.json"
        self._write_temp(self.connection_path, {"items": [self.profile]})
        self._write_temp(self.model_path, {"active_model_id": self.model["id"], "items": [self.model]})
        self.profile_reads: list[int] = []
        self.model_reads: list[int] = []
        self.panel = None
        self.connections: list[_FakeConnection] = []
        self.connector_threads: list[int] = []
        self.extra_widgets: list[QWidget] = []

    def tearDown(self):
        if self.panel is not None:
            host = getattr(self.panel, "data_center_agent", None)
            if host is not None:
                # No GUI-thread wait: wait through the event loop, then the
                # production cleanup contract must report cleared refs.
                self._wait(lambda: host.agent_shutdown_cleanup(), timeout=4.0)
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
        self._flush()
        for widget in self.extra_widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self._flush()
        self.temp.cleanup()

    @staticmethod
    def _write_temp(path: Path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _profile_host(self):
        def load_connections():
            self.profile_reads.append(threading.get_ident())
            return json.loads(self.connection_path.read_text(encoding="utf-8"))

        def load_models():
            self.model_reads.append(threading.get_ident())
            return json.loads(self.model_path.read_text(encoding="utf-8"))

        return DataCenterProfileHost(
            connections_loader=load_connections,
            model_catalog_loader=load_models,
        )

    def _connector(self, profile, secret, **_kwargs):
        self.assertEqual(profile["id"], self.profile["id"])
        self.assertEqual(secret, "")
        self.connector_threads.append(threading.get_ident())
        connection = _FakeConnection()
        self.connections.append(connection)
        return connection

    def _make_panel(self, transport, *, model=None):
        from panels.ai_workbench_panel import AiWorkbenchPanel

        profile = self.profile
        snapshot = {
            "fingerprint": connection_fingerprint(profile),
            "status": "ok",
            "objects": [{"owner": "sales", "name": "orders", "object_type": "TABLE", "columns": []}],
        }
        options = {
            "profile_host": self._profile_host(),
            "model_transport": transport,
            "relational_connector": self._connector,
        }
        with (
            patch("panels.ai_workbench_panel.load_connections", return_value=[profile]),
            patch("panels.ai_workbench_panel.load_snapshot", return_value=snapshot),
            patch("panels.ai_workbench_panel.is_enabled", return_value=True),
            patch("panels.ai_workbench_panel.load_ai_local", return_value=model or self.model),
            patch("panels.ai_workbench_panel.open_connection", side_effect=AssertionError("manual DB connection used")),
            patch("panels.ai_workbench_panel.run_console_statement", side_effect=AssertionError("manual console path used")),
        ):
            self.panel = AiWorkbenchPanel(data_center_agent_options=options)
        host = self.panel.data_center_agent
        self._wait(lambda: host._controller is not None)
        self.assertEqual(host.agent_panel.prompt_input.isEnabled(), True)
        return host

    def _flush(self):
        self.app.processEvents()
        self.app.processEvents()

    def _wait(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._flush()
            try:
                if predicate():
                    return
            except RuntimeError:
                pass
            time.sleep(0.002)
        self._flush()
        self.assertTrue(predicate(), "condition did not become true")

    def test_mysql_production_panel_runs_metadata_query_evidence_and_puts_sql(self):
        transport = _ScriptedTransport([
            _tool_call("metadata-1", "search_objects", {"keyword": "orders", "kinds": ["table"]}),
            _tool_call("query-1", "query_readonly", {"sql": "SELECT id FROM sales.orders", "row_limit": 10}),
            {"choices": [{"message": {"role": "assistant", "content": "orders has id 42; verified by the read-only query."}, "finish_reason": "stop"}]},
        ])
        host = self._make_panel(transport)
        seen = []
        host.agent_panel._event_bridge.received.connect(lambda queued: seen.append(queued.event))
        host.agent_panel.prompt_input.setPlainText("Find orders")
        host.agent_panel.send_button.click()
        self._wait(lambda: host._controller is not None and not host._controller.is_running and host._controller.snapshot.context.run_id != "")
        self._wait(lambda: host.agent_panel.answer_view.toPlainText().strip() != "")

        # The one-use controller has already been replaced with the next fixed
        # snapshot; inspect events from the completed generation instead.
        self.assertEqual(len(transport.bodies), 3)
        sql_events = [event for conn in self.connections for event in conn.events if event[0] == "execute"]
        self.assertTrue(any("information_schema.TABLES" in event[1] for event in sql_events))
        self.assertTrue(any("SELECT id FROM sales.orders" in event[1] for event in sql_events))
        self.assertTrue(all(event[3] != threading.get_ident() for event in sql_events))
        final_events = [event for event in seen if str(getattr(event.event_type, "value", event.event_type)).lower() == "final"]
        self.assertTrue(final_events)
        self.assertTrue(final_events[-1].payload.get("verified"))
        self.assertTrue(final_events[-1].payload.get("evidence_ids"))
        self.assertFalse(any("TEMP_SECRET_NEVER_EXPOSE" in repr(event.payload) for event in seen))
        self.assertNotIn("TEMP_SECRET_NEVER_EXPOSE", host.agent_panel.answer_view.toPlainText())

        editor = self.panel._current_editor()
        before = editor.toPlainText()
        self.assertIn("SELECT id FROM sales.orders", host.agent_panel._channels["sql"].value)
        host.agent_panel.put_sql_button.click()
        self.assertEqual(editor.toPlainText(), before + "SELECT id FROM sales.orders")
        self.assertFalse(self.panel._worker and self.panel._worker.isRunning())
        self.assertFalse(self.panel._ai_worker and self.panel._ai_worker.isRunning())
        self.assertTrue(self.profile_reads)
        self.assertTrue(all(thread_id != threading.get_ident() for thread_id in self.profile_reads))
        self.assertTrue(self.model_reads)
        self.assertTrue(all(thread_id != threading.get_ident() for thread_id in self.model_reads))
        self.assertTrue(self.connector_threads)
        self.assertTrue(all(thread_id != threading.get_ident() for thread_id in self.connector_threads))
        self.assertTrue(transport.thread_ids)
        self.assertTrue(all(thread_id != threading.get_ident() for thread_id in transport.thread_ids))

    def test_text_only_nonstream_draft_uses_zero_database_and_keeps_sql_draft_entry(self):
        self.model = _model(capability="text_only", model="fake-text")
        self._write_temp(self.model_path, {"active_model_id": self.model["id"], "items": [self.model]})
        transport = _ScriptedTransport([
            {"choices": [{"message": {"role": "assistant", "content": "SELECT id FROM sales.orders;"}, "finish_reason": "stop"}]},
        ])
        host = self._make_panel(transport, model=self.model)
        host.mode_combo.setCurrentIndex(host.mode_combo.findData("draft"))
        self._wait(lambda: host._controller is not None and host._controller.snapshot.is_draft)
        self.assertEqual(host.agent_panel.target_value_label.toolTip(), "草稿模式 · 不访问数据库")
        self.assertIn("TEMP intranet model", host.agent_panel.model_value_label.text())
        seen = []
        host.agent_panel._event_bridge.received.connect(lambda queued: seen.append(queued.event))
        host.agent_panel.prompt_input.setPlainText("Draft a query")
        host.agent_panel.send_button.click()
        self._wait(lambda: "SELECT id FROM sales.orders" in host.agent_panel.answer_view.toPlainText())
        self._wait(lambda: host._controller is not None and not host._controller.is_running)
        self.assertEqual(self.connections, [])
        self.assertEqual(self.profile_reads, [])
        self.assertEqual(transport.returned_mappings, 1)
        self.assertEqual(len(transport.bodies), 1)
        self.assertEqual(self.panel.ai_gen_btn.isEnabled(), True)

    def test_draft_target_display_relocalizes_without_rebinding_snapshot(self):
        host = self._make_panel(_ScriptedTransport([]))
        host.mode_combo.setCurrentIndex(host.mode_combo.findData("draft"))
        self._wait(lambda: host._controller is not None and host._controller.snapshot.is_draft)
        snapshot = host._snapshot
        controller = host._controller
        fixed_ids = (
            snapshot.context.run_id,
            snapshot.context.tab_id,
            snapshot.context.model_config_id,
            snapshot.context.connection_id,
            snapshot.context.profile_revision,
            snapshot.context.intent,
        )
        self.assertEqual(host.agent_panel.target_value_label.toolTip(), "草稿模式 · 不访问数据库")
        self.assertIn("草稿模式", host.status_label.text())

        host.set_language("en")

        self.assertIs(host._snapshot, snapshot)
        self.assertIs(host._controller, controller)
        self.assertEqual(
            fixed_ids,
            (
                host._snapshot.context.run_id,
                host._snapshot.context.tab_id,
                host._snapshot.context.model_config_id,
                host._snapshot.context.connection_id,
                host._snapshot.context.profile_revision,
                host._snapshot.context.intent,
            ),
        )
        self.assertEqual(host.agent_panel.target_value_label.toolTip(), "Draft mode · no database access")
        self.assertIn("Fixed: Draft mode · no database access", host.status_label.text())

    def test_query_target_named_like_draft_marker_stays_verbatim_when_language_changes(self):
        from ui.data_center.integration import DataCenterWorkbenchAgent

        marker_like_name = "Draft mode · no database access"
        profile = {
            "id": "temp-redis-marker-name",
            "name": marker_like_name,
            "provider": "redis",
            "dialect": "redis",
            "mode": "standalone",
            "host": "127.0.0.1",
            "port": 6379,
            "database": "0",
            "db": 0,
            "username": "",
            "password": "",
        }
        self._write_temp(self.connection_path, {"items": [profile]})
        self._write_temp(self.model_path, {"active_model_id": self.model["id"], "items": [self.model]})
        transport = _ScriptedTransport([])
        selected = lambda: {"profile": profile, "database": "0", "schema_allowlist": ()}
        with patch.object(
            DataCenterWorkbenchAgent,
            "_target_label",
            staticmethod(lambda _target: marker_like_name),
        ):
            host = DataCenterWorkbenchAgent(
                selection_provider=selected,
                put_sql=lambda _sql: None,
                profile_host=self._profile_host(),
                model_transport=transport,
                redis_client_factory=lambda **_kwargs: self.fail("query target display must not connect to Redis"),
            )
            self.extra_widgets.append(host)
            self._wait(lambda: bool(host._models) and host._controller is not None)
            snapshot = host._snapshot
            self.assertEqual(snapshot.context.intent, "query")
            self.assertEqual(snapshot.context.connection_id, profile["id"])
            self.assertTrue(snapshot.context.profile_revision)

            host.set_language("zh")
            self.assertIs(host._snapshot, snapshot)
            self.assertEqual(host.agent_panel.target_value_label.toolTip(), marker_like_name)
            self.assertEqual(host.agent_panel.target_value_label.property("fullValueText"), marker_like_name)
            host.set_language("en")
            self.assertIs(host._snapshot, snapshot)
            self.assertEqual(host.agent_panel.target_value_label.toolTip(), marker_like_name)
            self.assertEqual(host.agent_panel.target_value_label.property("fullValueText"), marker_like_name)
            self.assertEqual(transport.bodies, [])
            self.assertEqual(self.connector_threads, [])
            self.assertTrue(host.agent_shutdown_cleanup())

    def test_result_intent_survives_draft_failure_mode_switch_and_language_change(self):
        transport = _ScriptedTransport([
            _tool_call("intent-meta", "search_objects", {"keyword": "orders", "kinds": ["table"]}),
            _tool_call("intent-query", "query_readonly", {"sql": "SELECT id FROM sales.orders", "row_limit": 10}),
            {"choices": [{"message": {"role": "assistant", "content": "TEMP query answer"}, "finish_reason": "stop"}]},
        ])
        host = self._make_panel(transport)
        host.agent_panel.prompt_input.setPlainText("Read orders")
        host.agent_panel.send_button.click()
        self._wait(lambda: "TEMP query answer" in host.agent_panel.answer_view.toPlainText())
        self._wait(lambda: host._controller is not None and not host._controller.is_running)
        self.assertEqual(host._last_result_target.intent, "query")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "只读模式")

        host.mode_combo.setCurrentIndex(host.mode_combo.findData("draft"))
        self._wait(lambda: host._controller is not None and host._controller.snapshot.context.intent == "draft")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "只读模式")
        host.set_language("en")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "Read-only mode")

        finished = []
        controller = host._controller
        controller.run_finished.connect(lambda outcome: finished.append(outcome))
        host.agent_panel.prompt_input.setPlainText("Draft a query")
        host.agent_panel.send_button.click()
        self._wait(lambda: bool(finished))
        self.assertEqual(host._last_result_target.intent, "draft")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "Draft mode")
        self.assertIn("Agent run failed", host.status_label.text())
        self.assertNotIn("TEMP", host.status_label.text())

        host.mode_combo.setCurrentIndex(host.mode_combo.findData("query"))
        self._wait(lambda: host._controller is not None and host._controller.snapshot.context.intent == "query")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "Draft mode")
        host.set_language("zh")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "草稿模式")
        self.assertIn("Agent 运行失败", host.status_label.text())
        host._on_run_rejected("TEMP raw error details")
        self.assertEqual(host.status_label.text(), "Agent 未启动")
        self.assertNotIn("TEMP", host.status_label.text())

    def test_draft_intent_stays_captured_when_mode_change_cancels_active_run(self):
        transport = _QueryThenBlockingTransport([
            _tool_call("intent-cancel-meta", "search_objects", {"keyword": "orders", "kinds": ["table"]}),
            _tool_call("intent-cancel-query", "query_readonly", {"sql": "SELECT id FROM sales.orders", "row_limit": 10}),
            {"choices": [{"message": {"role": "assistant", "content": "TEMP prior query answer"}, "finish_reason": "stop"}]},
        ])
        host = self._make_panel(transport)
        host.agent_panel.prompt_input.setPlainText("Read orders")
        host.agent_panel.send_button.click()
        self._wait(lambda: "TEMP prior query answer" in host.agent_panel.answer_view.toPlainText())
        self._wait(lambda: host._controller is not None and not host._controller.is_running)
        connection_count_after_query = len(self.connections)

        host.mode_combo.setCurrentIndex(host.mode_combo.findData("draft"))
        self._wait(lambda: host._controller is not None and host._controller.snapshot.context.intent == "draft")
        host.agent_panel.prompt_input.setPlainText("Hold this draft")
        host.agent_panel.send_button.click()
        self.assertTrue(transport.entered.wait(2.0), "draft model request did not reach the worker")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "草稿模式")

        host.mode_combo.setCurrentIndex(host.mode_combo.findData("query"))
        self.assertTrue(transport.cancel_seen.wait(2.0), "mode change did not cancel the captured draft run")
        self._wait(lambda: host._controller is not None and not host._controller.is_running)
        self._wait(lambda: host._controller is not None and host._controller.snapshot.context.intent == "query")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "草稿模式")
        self.assertEqual(host._last_result_target.intent, "draft")
        self.assertIn("Agent 已取消", host.status_label.text())
        host.set_language("en")
        self.assertEqual(host.agent_panel.preview_badge.accessibleName(), "Draft mode")
        self.assertEqual(len(self.connections), connection_count_after_query)

    def test_write_and_wrong_schema_queries_are_rejected_before_opening_a_lease(self):
        for label, sql in (
            ("write", "DELETE FROM sales.orders"),
            ("wrong schema", "SELECT id FROM other.orders"),
        ):
            with self.subTest(label=label):
                transport = _ScriptedTransport([
                    _tool_call("guard-1", "query_readonly", {"sql": sql, "row_limit": 10}),
                    {"choices": [{"message": {"role": "assistant", "content": "The read-only target guard rejected that request."}, "finish_reason": "stop"}]},
                ])
                host = self._make_panel(transport)
                seen = []
                host.agent_panel._event_bridge.received.connect(lambda queued, target=seen: target.append(queued.event))
                host.agent_panel.prompt_input.setPlainText("Try a guarded query")
                host.agent_panel.send_button.click()
                self._wait(lambda: host.agent_panel.answer_view.toPlainText().strip() != "")
                self._wait(lambda: host._controller is not None and not host._controller.is_running)

                results = [
                    event for event in seen
                    if str(getattr(event.event_type, "value", event.event_type)).lower() == "tool_result"
                    and event.payload.get("name") == "query_readonly"
                ]
                self.assertTrue(results)
                self.assertFalse(results[-1].payload.get("ok"))
                self.assertEqual(self.connections, [])
                self.assertFalse(any(event[0] == "execute" for conn in self.connections for event in conn.events))

                self.assertTrue(host.agent_shutdown_cleanup())
                self.panel.close()
                self.panel.deleteLater()
                self.panel = None

    def test_model_and_connection_revision_changes_after_capture_do_not_run(self):
        for changed_file in ("model", "connection"):
            with self.subTest(changed_file=changed_file):
                transport = _ScriptedTransport([])
                host = self._make_panel(transport)
                if changed_file == "model":
                    edited = {**self.model, "base_url": "http://changed.invalid/v1", "model": "edited-model"}
                    self._write_temp(self.model_path, {"active_model_id": self.model["id"], "items": [edited]})
                else:
                    edited = {**self.profile, "host": "changed.internal"}
                    self._write_temp(self.connection_path, {"items": [edited]})
                seen = []
                host.agent_panel._event_bridge.received.connect(lambda queued, target=seen: target.append(queued.event))
                host.agent_panel.prompt_input.setPlainText("Run against the captured configuration")
                host.agent_panel.send_button.click()
                self._wait(lambda: any(
                    str(getattr(event.event_type, "value", event.event_type)).lower() == "failed"
                    for event in seen
                ))
                self._wait(lambda: host._controller is not None and not host._controller.is_running)
                failed = [
                    event for event in seen
                    if str(getattr(event.event_type, "value", event.event_type)).lower() == "failed"
                ]
                self.assertEqual(failed[-1].payload.get("code"), "RUNTIME_FAILED")
                self.assertEqual(transport.bodies, [])
                self.assertEqual(self.connections, [])
                self.assertTrue(all(ident != threading.get_ident() for ident in self.model_reads))
                self.assertTrue(host.agent_shutdown_cleanup())
                self.panel.close()
                self.panel.deleteLater()
                self.panel = None
                self.model_reads.clear()
                self.profile_reads.clear()

    def test_target_switch_cancels_old_snapshot_before_installing_new_target(self):
        self._write_temp(self.connection_path, {"items": [self.profile]})
        transport = _BlockingTransport()
        host = self._make_panel(transport)
        host.agent_panel.prompt_input.setPlainText("Hold this run while the target changes")
        host.agent_panel.send_button.click()
        self.assertTrue(transport.entered.wait(2.0), "model request did not reach the worker")

        second = {**self.profile, "id": "temp-mysql-2", "name": "TEMP second", "host": "127.0.0.2"}
        self._write_temp(self.connection_path, {"items": [self.profile, second]})
        second_snapshot = {
            "fingerprint": connection_fingerprint(second),
            "status": "ok",
            "objects": [{"owner": "sales", "name": "orders", "object_type": "TABLE", "columns": []}],
        }
        first_snapshot = self.panel._snapshot
        self.panel.conn_combo.blockSignals(True)
        self.panel.conn_combo.addItem("TEMP second", second)
        self.panel.conn_combo.blockSignals(False)
        observed_bindings = []
        original_target_changed = host.target_changed

        def observe_target_changed():
            current_tab = self.panel._current_tab()
            current = current_tab.conn_item if current_tab is not None else None
            selection = self.panel._data_center_agent_selection()
            observed_bindings.append((
                str((current or {}).get("id") or ""),
                str((self.panel._snapshot or {}).get("fingerprint") or ""),
                tuple(selection.get("schema_allowlist") or ()),
            ))
            original_target_changed()

        host.target_changed = observe_target_changed
        import panels.ai_workbench_panel as ai_workbench_module
        with (
            patch.object(ai_workbench_module, "load_snapshot", side_effect=lambda selected: second_snapshot if selected == second["id"] else first_snapshot),
            patch.object(ai_workbench_module, "load_ai_local", return_value=self.model),
            patch.object(ai_workbench_module, "is_enabled", return_value=True),
            patch.object(ai_workbench_module, "open_connection", side_effect=AssertionError("manual SQL connection used")),
            patch.object(ai_workbench_module, "run_console_statement", side_effect=AssertionError("manual SQL console used")),
        ):
            self.panel.conn_combo.setCurrentIndex(self.panel.conn_combo.count() - 1)
        self._wait(
            lambda: host._controller is not None
            and not host._controller.is_running
            and host._controller.snapshot.context.connection_id == second["id"],
            timeout=4.0,
        )
        self.assertTrue(observed_bindings)
        self.assertEqual(observed_bindings[0], (second["id"], second_snapshot["fingerprint"], ("sales",)))
        self.assertTrue(transport.cancel_seen.is_set())
        self.assertEqual(self.connections, [])
        self.assertTrue(host.agent_shutdown_cleanup())

    def test_sql_tab_switch_preserves_run_and_put_sql_checks_the_captured_target(self):
        second = {**self.profile, "id": "temp-mysql-2", "name": "TEMP second", "host": "127.0.0.2"}
        self._write_temp(self.connection_path, {"items": [self.profile, second]})
        first_snapshot = {
            "fingerprint": connection_fingerprint(self.profile),
            "status": "ok",
            "objects": [{"owner": "sales", "name": "orders", "object_type": "TABLE", "columns": []}],
        }
        second_snapshot = {
            "fingerprint": connection_fingerprint(second),
            "status": "ok",
            "objects": [{"owner": "sales", "name": "orders", "object_type": "TABLE", "columns": []}],
        }
        transport = _DelayedScriptedTransport([
            _tool_call("metadata-old-run", "search_objects", {"keyword": "orders", "kinds": ["table"]}),
            _tool_call("query-old-run", "query_readonly", {"sql": "SELECT id FROM sales.orders", "row_limit": 10}),
            {"choices": [{"message": {"role": "assistant", "content": "TEMP result belongs to the first target."}, "finish_reason": "stop"}]},
        ])
        self.addCleanup(transport.release.set)
        host = self._make_panel(transport)
        with patch("panels.ai_workbench_panel.load_snapshot", side_effect=lambda cid: second_snapshot if cid == second["id"] else first_snapshot):
            self.panel.conn_combo.blockSignals(True)
            self.panel.conn_combo.addItem("TEMP second", second)
            self.panel.conn_combo.blockSignals(False)
            second_tab = self.panel._new_sql_tab()
            second_tab.conn_item = dict(second)
            self.panel.sql_tabs.setCurrentIndex(0)

        controller = host._controller
        self.assertIsNotNone(controller)
        captured = controller.snapshot
        self.assertEqual(captured.context.connection_id, self.profile["id"])
        old_run_id = captured.context.run_id
        old_run_tab_id = captured.context.tab_id
        events = []
        host.agent_panel._event_bridge.received.connect(lambda queued: events.append(queued.event))
        host.agent_panel.prompt_input.setPlainText("Read the TEMP orders")
        host.agent_panel.send_button.click()
        self.assertTrue(transport.entered.wait(2.0), "model request did not reach the worker")

        with patch("panels.ai_workbench_panel.load_snapshot", side_effect=lambda cid: second_snapshot if cid == second["id"] else first_snapshot):
            self.panel.sql_tabs.setCurrentIndex(1)
        self.assertIs(host._controller, controller)
        self.assertTrue(host._controller.is_running)
        self.assertFalse(transport.cancel_seen.is_set(), "a visible-tab change cancelled the captured run")
        self.assertEqual(host._snapshot, captured)
        transport.release.set()
        self._wait(
            lambda: host._controller is not None
            and not host._controller.is_running
            and host._controller.snapshot.context.connection_id == second["id"]
            and "TEMP result belongs" in host.agent_panel.answer_view.toPlainText(),
            timeout=5.0,
        )

        self.assertFalse(transport.cancel_seen.is_set())
        self.assertTrue(all(event.run_id == old_run_id and event.tab_id == old_run_tab_id for event in events))
        self.assertEqual(host._last_result_target.connection_id, self.profile["id"])
        self.assertEqual(host._last_result_target.profile_revision, captured.context.profile_revision)
        self.assertEqual(host.agent_panel.target_value_label.toolTip(), captured.target)
        self.assertEqual(self.panel._current_tab().conn_item["id"], second["id"])
        self.assertIn("SELECT id FROM sales.orders", host.agent_panel.sql_view.toPlainText())

        # The generated SQL still belongs to the completed A run while B is
        # selected.  It must remain visible and insertion must leave B intact.
        host.agent_panel.put_sql_button.click()
        self.assertEqual(self.panel._current_editor().toPlainText(), "")
        self.assertIn("未修改编辑器", host.status_label.text())
        self.assertIn("TEMP result belongs", host.agent_panel.answer_view.toPlainText())

        with patch("panels.ai_workbench_panel.load_snapshot", side_effect=lambda cid: second_snapshot if cid == second["id"] else first_snapshot):
            self.panel.sql_tabs.setCurrentIndex(0)
        self._wait(lambda: host._controller is not None and host._controller.snapshot.context.connection_id == self.profile["id"])
        host.agent_panel.put_sql_button.click()
        self.assertIn("SELECT id FROM sales.orders", self.panel._current_editor().toPlainText())
        self.assertFalse(self.panel._worker and self.panel._worker.isRunning())
        self.assertTrue(host.agent_shutdown_cleanup())

    def test_shutdown_cleanup_is_nonblocking_and_clears_only_agent_threads(self):
        transport = _BlockingTransport()
        host = self._make_panel(transport)
        host.agent_panel.prompt_input.setPlainText("Wait for shutdown cancellation")
        host.agent_panel.send_button.click()
        self.assertTrue(transport.entered.wait(2.0), "model request did not reach the worker")

        started = time.monotonic()
        self.assertFalse(host.agent_shutdown_cleanup())
        self.assertLess(time.monotonic() - started, 0.1)
        self._wait(lambda: host.agent_shutdown_cleanup(), timeout=4.0)
        self.assertIsNone(host._controller)
        self.assertIsNone(host._snapshot)
        self.assertFalse(host.agent_panel.prompt_input.isEnabled())
        self.assertFalse(self.connections)

    def test_all_three_production_workbenches_embed_the_agent_without_opening_manual_connections(self):
        from panels.ai_workbench_panel import AiWorkbenchPanel
        from panels.db_mongodb_panel import MongoDBWorkbenchPanel
        from panels.db_redis_panel import RedisWorkbenchPanel

        cases = (
            ("ai", AiWorkbenchPanel, "panels.ai_workbench_panel"),
            ("redis", RedisWorkbenchPanel, "panels.db_redis_panel"),
            ("mongo", MongoDBWorkbenchPanel, "panels.db_mongodb_panel"),
        )
        import tools.intranet_llm as intranet_llm

        for _name, panel_type, module in cases:
            with self.subTest(panel=panel_type.__name__):
                host_options = {
                    "profile_host": DataCenterProfileHost(
                        connections_loader=lambda: [],
                        model_catalog_loader=lambda: {"active_model_id": self.model["id"], "items": [self.model]},
                    ),
                    "model_transport": _ScriptedTransport([]),
                    "relational_connector": self._connector,
                }
                with (
                    patch(f"{module}.load_connections", return_value=[]),
                    patch(f"{module}.load_snapshot", return_value=None) if panel_type is AiWorkbenchPanel else nullcontext(),
                    patch(f"{module}.is_enabled", return_value=False) if panel_type is AiWorkbenchPanel else nullcontext(),
                    patch(f"{module}.load_ai_local", return_value={}) if panel_type is AiWorkbenchPanel else nullcontext(),
                    patch(f"{module}.open_connection", side_effect=AssertionError("manual connection used")),
                    patch.object(intranet_llm, "load_ai_local", return_value={}),
                    patch.object(intranet_llm, "is_enabled", return_value=False),
                ):
                    panel = panel_type(data_center_agent_options=host_options)
                self.extra_widgets.append(panel)
                self._wait(lambda: bool(panel.data_center_agent._models))
                tab_index = panel.side_tabs.indexOf(panel.data_center_agent)
                self.assertGreaterEqual(tab_index, 0)
                self.assertIn("只读 Agent", panel.side_tabs.tabText(tab_index))
                self.assertFalse(panel.data_center_agent.agent_panel.prompt_input.isEnabled())
                self.assertEqual(panel.data_center_agent.agent_panel.preview_badge.text(), "只读")
                self.assertNotIn("simulation", panel.data_center_agent.agent_panel.preview_badge.toolTip().casefold())
                panel.data_center_agent.agent_panel.put_sql_requested.emit("TEMP_AGENT_DRAFT")
                if panel_type is AiWorkbenchPanel:
                    self.assertEqual(panel._current_editor().toPlainText(), "TEMP_AGENT_DRAFT")
                elif panel_type is RedisWorkbenchPanel:
                    self.assertEqual(panel.cmd_input.text(), "TEMP_AGENT_DRAFT")
                else:
                    self.assertEqual(panel.query_input.toPlainText(), "TEMP_AGENT_DRAFT")
                self.assertTrue(panel.agent_shutdown_cleanup())

    def test_agent_selection_controls_fit_one_row_in_the_sidebar(self):
        from ui.data_center.integration import DataCenterWorkbenchAgent

        host = DataCenterWorkbenchAgent(
            selection_provider=lambda: None,
            put_sql=lambda _text: None,
            profile_host=self._profile_host(),
        )
        self.extra_widgets.append(host)
        self._wait(lambda: bool(host._models))
        host.show()
        # 侧栏常见宽度下「模式 + 模型 + ↻」保持一行，且都在侧栏宽度内
        host.resize(340, 700)
        self.app.processEvents()
        self.assertEqual(host._controls_row.direction(), QBoxLayout.Direction.LeftToRight)
        for widget in (host.mode_combo, host.model_combo, host.refresh_button):
            right = widget.mapTo(host, widget.rect().topRight()).x()
            self.assertLessEqual(right, host.width(), widget.objectName() or type(widget).__name__)
        self.assertTrue(host.agent_shutdown_cleanup())

    def test_late_model_catalog_result_cannot_replace_the_users_new_selection(self):
        from ui.data_center.integration import DataCenterWorkbenchAgent

        second_model = {**self.model, "id": "temp-model-2", "name": "TEMP second model", "model": "fake-agent-2"}
        entered_first = threading.Event()
        release_first = threading.Event()
        calls_lock = threading.Lock()
        calls = 0

        def model_catalog_loader():
            nonlocal calls
            with calls_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                entered_first.set()
                release_first.wait(3.0)
            return {"active_model_id": self.model["id"], "items": [self.model, second_model]}

        host = DataCenterWorkbenchAgent(
            selection_provider=lambda: None,
            put_sql=lambda _sql: None,
            profile_host=DataCenterProfileHost(
                connections_loader=lambda: [],
                model_catalog_loader=model_catalog_loader,
            ),
            model_transport=_ScriptedTransport([]),
        )
        self.extra_widgets.append(host)
        self.assertTrue(entered_first.wait(2.0), "initial catalog read did not start")
        host.refresh_models()
        self._wait(lambda: host.model_combo.count() == 2)
        host.model_combo.setCurrentIndex(host.model_combo.findData(second_model["id"]))
        self.assertEqual(host.model_combo.currentData(), second_model["id"])
        release_first.set()
        self._wait(lambda: not host._catalog_threads, timeout=4.0)
        self.assertEqual(host.model_combo.currentData(), second_model["id"])
        self.assertIsNone(host._controller)
        self.assertTrue(host.agent_shutdown_cleanup())

    def test_worker_assembles_each_supported_provider_mode_and_keeps_dameng_closed(self):
        from ui.data_center.integration import DataCenterWorkbenchAgent

        variants = (
            ("oracle", {"provider": "oracle", "dialect": "oracle", "mode": "oracle", "database": "APP", "port": 1521}),
            ("mysql", {"provider": "mysql", "dialect": "mysql", "mode": "mysql", "database": "sales", "port": 3306}),
            ("oceanbase_oracle", {"provider": "oceanbase", "dialect": "oceanbase", "mode": "oracle", "database": "APP", "port": 2881}),
            ("oceanbase_mysql", {"provider": "oceanbase", "dialect": "oceanbase", "mode": "mysql", "database": "sales", "port": 2883}),
            ("redis", {"provider": "redis", "dialect": "redis", "mode": "standalone", "database": "0", "db": 0, "port": 6379}),
            ("mongodb", {"provider": "mongodb", "dialect": "mongodb", "mode": "standalone", "database": "analytics", "port": 27017}),
        )
        connector_calls = []
        assembly_threads = []
        for name, fields in variants:
            with self.subTest(provider=name):
                profile = {
                    "id": f"temp-{name}",
                    "name": f"TEMP {name}",
                    "host": "127.0.0.1",
                    "username": "",
                    "password": "",
                    "auth_mode": "none",
                    **fields,
                }
                self._write_temp(self.connection_path, {"items": [profile]})
                transport = _ScriptedTransport([
                    {"choices": [{"message": {"role": "assistant", "content": f"{name} worker assembly ready."}, "finish_reason": "stop"}]},
                ])

                def selected(current=profile, provider=name):
                    scope = ("orders",) if provider == "mongodb" else ("sales",) if provider in {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"} else ()
                    return {"profile": current, "database": current.get("database", "0"), "schema_allowlist": scope}

                def connector(*_args, **_kwargs):
                    connector_calls.append((name, threading.get_ident()))
                    return _FakeConnection()

                host = DataCenterWorkbenchAgent(
                    selection_provider=selected,
                    put_sql=lambda _sql: None,
                    profile_host=self._profile_host(),
                    model_transport=transport,
                    relational_connector=connector,
                    redis_client_factory=lambda **_kwargs: None,
                    mongo_client_factory=lambda **_kwargs: None,
                )
                self.extra_widgets.append(host)
                self._wait(lambda: bool(host._models))
                if host._controller is None:
                    self.fail(f"supported provider {name} did not install a run controller: {host.status_label.text()!r}")
                captured = host._controller.snapshot.context
                expected_provider = name
                self.assertEqual(captured.connection_id, profile["id"])
                self.assertEqual(captured.profile_revision, host._connection_choice.revision)
                if name in {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"}:
                    self.assertEqual(host._connection_choice.provider, expected_provider)

                host.agent_panel.prompt_input.setPlainText("Validate provider assembly")
                host.agent_panel.send_button.click()
                self._wait(lambda: host.agent_panel.answer_view.toPlainText().strip() != "")
                self._wait(lambda: len(transport.bodies) == 1 and host._controller is not None and not host._controller.is_running)
                assembly_threads.extend(self.profile_reads)
                self.assertTrue(host.agent_shutdown_cleanup())
                self.extra_widgets.remove(host)
                host.close()
                host.deleteLater()
                self.profile_reads.clear()

        self.assertEqual(connector_calls, [])
        self.assertTrue(all(ident != threading.get_ident() for ident in assembly_threads))

        dameng = {
            "id": "temp-dameng",
            "name": "TEMP Dameng",
            "provider": "dameng",
            "dialect": "dameng",
            "mode": "dameng",
            "host": "127.0.0.1",
            "port": 5236,
            "database": "APP",
            "username": "",
            "password": "",
        }
        self._write_temp(self.connection_path, {"items": [dameng]})
        closed_transport = _ScriptedTransport([])
        closed = DataCenterWorkbenchAgent(
            selection_provider=lambda: {"profile": dameng, "database": "APP", "schema_allowlist": ("SALES",)},
            put_sql=lambda _sql: None,
            profile_host=self._profile_host(),
            model_transport=closed_transport,
        )
        self.extra_widgets.append(closed)
        self._wait(lambda: bool(closed._models))
        self.assertIsNone(closed._controller)
        self.assertEqual(closed_transport.bodies, [])
        self.assertTrue(closed.agent_shutdown_cleanup())

    def test_mongo_metadata_and_read_share_the_same_collection_allowlist_and_scope(self):
        from ui.data_center import integration as integration_module
        from ui.data_center.integration import DataCenterWorkbenchAgent

        profile = {
            "id": "temp-mongodb-1",
            "name": "TEMP Mongo",
            "provider": "mongodb",
            "dialect": "mongodb",
            "mode": "standalone",
            "host": "127.0.0.1",
            "port": 27017,
            "database": "analytics",
            "auth_mode": "none",
            "username": "",
            "password": "",
            "unknown_mongo_field": {"preserve": True},
        }
        self._write_temp(self.connection_path, {"items": [profile]})
        collection = _FakeMongoCollection()
        database = _FakeMongoDatabase(collection)
        mongo_client = _FakeMongoClient(database)
        mongo_calls = []

        def mongo_factory(profile, secret, **_kwargs):
            mongo_calls.append((profile["id"], secret, threading.get_ident()))
            return mongo_client

        transport = _ScriptedTransport([
            _tool_call("mongo-meta-1", "search_objects", {"keyword": "orders", "kinds": ["collection"]}),
            _tool_call("mongo-read-1", "mongo_read", {
                "collection_id": "orders",
                "operation": "find",
                "filter": {},
                "projection": {"_id": 0, "total": 1},
                "limit": 5,
            }),
            {"choices": [{"message": {"role": "assistant", "content": "The authorized orders collection contains total 19."}, "finish_reason": "stop"}]},
        ])
        captured_owners = []
        original_owner_init = integration_module._RuntimeOwner.__init__

        def capture_owner(owner, runtime, nosql_factory=None):
            captured_owners.append((runtime, nosql_factory))
            original_owner_init(owner, runtime, nosql_factory)

        host = DataCenterWorkbenchAgent(
            selection_provider=lambda: {
                "profile": profile,
                "database": "analytics",
                "schema_allowlist": ("orders",),
            },
            put_sql=lambda _sql: None,
            profile_host=self._profile_host(),
            model_transport=transport,
            mongo_client_factory=mongo_factory,
        )
        self.extra_widgets.append(host)
        self._wait(lambda: bool(host._models))
        events = []
        host.agent_panel._event_bridge.received.connect(lambda queued: events.append(queued.event))
        with (
            patch.object(integration_module._RuntimeOwner, "__init__", capture_owner),
        ):
            host.agent_panel.prompt_input.setPlainText("Inspect orders, then read the approved collection")
            host.agent_panel.send_button.click()
            self._wait(lambda: "total 19" in host.agent_panel.answer_view.toPlainText())
            self._wait(lambda: len(transport.bodies) == 3 and host._controller is not None and not host._controller.is_running)

        self.assertEqual(len(captured_owners), 1)
        runtime, factory = captured_owners[0]
        self.assertIsNotNone(factory)
        self.assertIs(runtime.metadata_host._factory, factory)
        self.assertIs(runtime._nosql_worker.factory, factory)
        self.assertEqual(factory.target.allowed_collection_ids, ("orders",))
        self.assertEqual(
            database.lookups,
            ["orders"],
            f"mongo_calls={mongo_calls!r}; runtime_context={captured_owners[0][0].context.as_dict() if captured_owners else None!r}; profile_snapshot={captured_owners[0][0]._nosql_worker.host.profile_snapshot(profile['id']).as_dict() if captured_owners else None!r}; events={[(str(getattr(item.event_type, 'value', item.event_type)), dict(item.payload)) for item in events]!r}",
        )
        self.assertEqual(len(collection.find_calls), 1)
        self.assertEqual(mongo_calls[0][0], profile["id"])
        self.assertEqual(mongo_calls[0][1], "")
        self.assertNotEqual(mongo_calls[0][2], threading.get_ident())

        tool_results = [
            event for event in events
            if str(getattr(event.event_type, "value", event.event_type)).lower() == "tool_result"
        ]
        metadata = next(event for event in tool_results if event.payload.get("name") == "search_objects")
        mongo_result = next(event for event in tool_results if event.payload.get("name") == "mongo_read")
        self.assertEqual(metadata.payload["scope"]["schemas"], ["orders"])
        self.assertEqual([item["name"] for item in metadata.payload["data"]["objects"]], ["orders"])
        mongo_scope = mongo_result.payload["scope"]
        self.assertEqual(mongo_scope["connection_id"], profile["id"])
        self.assertEqual(mongo_scope["database"], "analytics")
        self.assertEqual(mongo_scope["profile_revision"], metadata.payload["scope"]["profile_revision"])
        self.assertTrue(mongo_result.payload["ok"])
        self.assertEqual(mongo_result.payload["data"]["collection_id"], "orders")
        final = [
            event for event in events
            if str(getattr(event.event_type, "value", event.event_type)).lower() == "final"
        ][-1]
        self.assertIn("metadata:mongo-meta-1", final.payload["evidence_ids"])
        self.assertNotIn("TEMP_MONGO_SECRET", repr(events))
        self.assertTrue(mongo_client.closed)
        self.assertTrue(host.agent_shutdown_cleanup())


if __name__ == "__main__":
    unittest.main()
