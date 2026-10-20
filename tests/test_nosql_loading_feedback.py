# -*- coding: utf-8 -*-
"""Process-isolated fake-worker checks for Redis/Mongo query feedback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


_CHILD = r'''\
import json
import os
from pathlib import Path
import socket
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(os.environ["PENGTOOLS_NOSQL_REPO_ROOT"])
TEMP_ROOT = Path(os.environ["PENGTOOLS_NOSQL_TEMP_ROOT"])
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PENGTOOLS_DISABLE_MOTION"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

NETWORK_ATTEMPTS = []

def blocked_connect(_sock, address):
    NETWORK_ATTEMPTS.append(repr(address))
    raise AssertionError("NoSql loading regression forbids all socket connections")

def blocked_create_connection(address, *args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(address))
    raise AssertionError("NoSql loading regression forbids create_connection")

socket.socket.connect = blocked_connect
socket.socket.connect_ex = blocked_connect
socket.create_connection = blocked_create_connection

from PyQt6.QtCore import QEvent, QThread, Qt, QStandardPaths, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

QStandardPaths.setTestModeEnabled(True)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(["PengTools-NoSql-Loading-Feedback"])
app.setQuitOnLastWindowClosed(False)

config_root = TEMP_ROOT / "config"
config_root.mkdir(parents=True, exist_ok=True)
import config
config.local_data_dir = lambda executable=None, frozen=None: str(config_root)
config.CONFIG_DIR = str(config_root)
for name, value in tuple(vars(config).items()):
    if name == "CONFIG_DIR":
        continue
    if name.endswith("_FILE"):
        setattr(config, name, str(config_root / (name.lower() + ".json")))
    elif name.endswith("_DIR") and isinstance(value, str):
        setattr(config, name, str(config_root / name.lower()))

from PyQt6.QtCore import pyqtSignal

class FakeWorker(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    instances = []

    def __init__(self, kind, item, **kwargs):
        super().__init__()
        self.kind = kind
        self.item = dict(item or {})
        self.kwargs = kwargs
        self.cancelled = False
        self.generation = int(kwargs.get("generation") or 0)
        self.running = False
        self.start_count = 0
        type(self).instances.append(self)

    def isRunning(self):
        return self.running

    def start(self):
        self.start_count += 1
        self.running = True

    def complete(self, payload, kind=None):
        if not self.running:
            raise AssertionError("fake completion must follow one start")
        self.completed.emit(kind or self.kind, payload)
        self.running = False
        self.finished.emit()

    def fail(self, error, kind=None):
        if not self.running:
            raise AssertionError("fake failure must follow one start")
        self.failed.emit(kind or self.kind, error)
        self.running = False
        self.finished.emit()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def dispatch_once(panel, method, kind, busy_text):
    before = len(FakeWorker.instances)
    method()
    check(len(FakeWorker.instances) == before + 1,
          f"{kind} action should dispatch exactly one worker; got {len(FakeWorker.instances) - before}")
    worker = FakeWorker.instances[-1]
    check(worker.kind == kind, f"expected worker kind {kind}, got {worker.kind}")
    check(worker.start_count == 1, f"{kind} worker should start exactly once")
    check(panel.query_status_band.is_querying(), f"{kind} must show querying before completion")
    check(panel.query_status_band.text() == busy_text,
          f"{kind} busy label mismatch: {panel.query_status_band.text()!r}")
    check(not panel.query_status_band._icon._timer.isActive(),
          "reduced-motion mode should retain static feedback without animation")
    return worker


def redis_payload(worker, keys, *, finished=False, cursor=1):
    return {
        "overview": None,
        "info_table": None,
        "scan": {
            "keys": list(keys),
            "cursor": cursor,
            "finished": finished,
            "partial": False,
            "failed_nodes": [],
        },
        "generation": int(worker.kwargs.get("generation") or 0),
        "append": bool(worker.kwargs.get("append")),
    }


def run_checks():
    from panels import db_mongodb_panel as mongo_module
    from panels import db_redis_panel as redis_module
    from ui.query_status_band import QueryStatusBand

    results = {"redis_actions": [], "mongo_actions": [], "redis_overlap_orders": []}

    # A language/theme update can change inherited font metrics without resizing
    # the fixed-width status row or calling setText again. Verify the live label
    # follows the settled child geometry at both font sizes.
    font_host = QWidget()
    font_layout = QHBoxLayout(font_host)
    font_layout.setContentsMargins(0, 0, 0, 0)
    font_probe = QueryStatusBand()
    font_layout.addWidget(font_probe)
    font_host.setFixedSize(960, 32)
    font_probe.setFixedWidth(960)
    font_probe.show()
    font_host.show()
    full_font_text = "synthetic diagnostic " + ("long detail " * 120)
    font12 = QFont(font_probe.font())
    font12.setPointSize(12)
    font_probe.setFont(font12)
    font_probe.setText(full_font_text, querying=False)
    app.processEvents()

    def settled_elision():
        label = font_probe._label
        return label.fontMetrics().elidedText(
            full_font_text, Qt.TextElideMode.ElideRight, label.contentsRect().width(),
        )

    base_geometry = font_probe.size(), font_probe._label.geometry()
    display12 = font_probe._label.text()
    check(display12 == settled_elision(),
          "12pt status text must match the settled label width after parent layout")
    font16 = QFont(font_probe.font())
    font16.setPointSize(16)
    font_probe.setFont(font16)
    app.processEvents()
    display16 = font_probe._label.text()
    check((font_probe.size(), font_probe._label.geometry()) == base_geometry,
          "font changes must not resize the fixed status band or its laid-out label")
    check(font_probe._label.font().pointSize() == 16 and display16 == settled_elision()
          and display16 != display12,
          "same-width status label must recompute its elision on an inherited 16pt font change")
    font_probe.setFont(font12)
    app.processEvents()
    check((font_probe.size(), font_probe._label.geometry()) == base_geometry,
          "restoring the font must leave settled band and label geometry unchanged")
    check(font_probe._label.font().pointSize() == 12
          and font_probe._label.text() == settled_elision()
          and font_probe._label.text() == display12,
          "same-width status label must restore its 12pt elision without another setText")
    check(font_probe.text() == full_font_text,
          "font changes must preserve the full status text API")
    results["font_elision_font_change_fixed_geometry"] = True
    font_host.close()
    font_host.deleteLater()

    item_redis = {"id": "synthetic-redis", "dialect": "redis", "host": "synthetic.invalid", "port": 6379}
    item_mongo = {"id": "synthetic-mongo", "dialect": "mongodb", "host": "synthetic.invalid", "port": 27017}

    with ExitStack() as stack:
        stack.enter_context(patch.object(redis_module, "load_connections", return_value=[]))
        stack.enter_context(patch.object(mongo_module, "load_connections", return_value=[]))
        stack.enter_context(patch.object(redis_module, "_RedisWorker", FakeWorker))
        stack.enter_context(patch.object(mongo_module, "_MongoWorker", FakeWorker))
        redis_info = stack.enter_context(patch.object(redis_module, "show_info"))
        redis_error = stack.enter_context(patch.object(redis_module, "show_error"))
        stack.enter_context(patch.object(redis_module, "show_warning"))
        mongo_info = stack.enter_context(patch.object(mongo_module, "show_info"))
        mongo_error = stack.enter_context(patch.object(mongo_module, "show_error"))
        stack.enter_context(patch.object(mongo_module, "show_warning"))

        redis = redis_module.RedisWorkbenchPanel("zh")
        mongo = mongo_module.MongoDBWorkbenchPanel("zh")
        redis._current_conn = lambda: dict(item_redis)
        mongo._current_conn = lambda: dict(item_mongo)
        redis.resize(1100, 720)
        mongo.resize(1100, 720)
        redis.show()
        mongo.show()
        app.processEvents()

        check(isinstance(redis.query_status_band, QueryStatusBand), "Redis status band missing")
        check(isinstance(mongo.query_status_band, QueryStatusBand), "Mongo status band missing")
        check(redis.query_status_band.isVisible() and mongo.query_status_band.isVisible(),
              "both status bands must be visible in their real panel layouts")
        check(redis.main_split.width() > 0 and redis.main_split.height() > 0,
              "Redis result workspace must remain laid out below its status band")
        check(mongo.body_splitter.width() > 0 and mongo.body_splitter.height() > 0,
              "Mongo result workspace must remain laid out below its status band")
        check(redis.query_status_band.height() == 32 and mongo.query_status_band.height() == 32,
              "both status bands must retain the shared 32px size")

        # Redis: exercise the actual public actions through the fake worker boundary.
        worker = dispatch_once(redis, redis._test_connection, "test", "正在测试连接…")
        running_count = len(FakeWorker.instances)
        redis._test_connection()
        check(len(FakeWorker.instances) == running_count and worker.isRunning(),
              "Redis foreground busy guard must retain its existing no-second-dispatch behavior")
        worker.complete({"ok": True})
        check(redis.query_status_band.text() == "连接测试完成" and not redis.query_status_band.is_querying(),
              "Redis connection completion status missing")
        check(redis_info.call_count == 1, "existing Redis success callback must still run once")
        results["redis_actions"].append("test")

        redis._key_cache = ["old:key"]
        worker = dispatch_once(redis, redis._refresh_keys, "bootstrap", "正在刷新 Key…")
        check(redis._key_cache == [], "refresh must preserve its existing cache-clear behavior")
        worker.complete(redis_payload(worker, ["synthetic:one"], cursor=7))
        check(redis._key_cache == ["synthetic:one"], "bootstrap callback must still apply returned keys")
        check(redis.query_status_band.text() == "Key 刷新完成", "Redis bootstrap terminal status missing")
        results["redis_actions"].append("refresh_keys")

        worker = dispatch_once(redis, redis._start_search_scan, "scan", "正在扫描 Key…")
        check(redis._key_cache == [] and redis.key_list.count() == 0,
              "search must preserve its existing immediate list/cache clear")
        worker.complete(redis_payload(worker, ["synthetic:search"], cursor=12))
        check(redis._key_cache == ["synthetic:search"], "search scan callback must apply keys")
        results["redis_actions"].append("start_search_scan")

        worker = dispatch_once(redis, redis._load_more_keys, "scan", "正在扫描 Key…")
        check(worker.kwargs.get("append") is True and worker.kwargs.get("cursor") == 12,
              "load-more must preserve its existing append cursor")
        pending_rows = [redis.key_list.item(i).text() for i in range(redis.key_list.count())]
        check(pending_rows == ["synthetic:search"], "load-more should start with existing visible keys")
        check([redis.key_list.item(i).text() for i in range(redis.key_list.count())] == pending_rows,
              "load-more must preserve visible key rows while the worker is pending")
        worker.complete(redis_payload(worker, ["synthetic:more"], finished=True, cursor=0))
        check(redis._key_cache == ["synthetic:search", "synthetic:more"],
              "load-more callback must append keys without losing existing rows")
        results["redis_actions"].append("load_more_keys")

        redis._selected_key = "synthetic:search"
        worker = dispatch_once(redis, redis._load_key_meta, "key_meta", "正在加载 Key 元信息…")
        worker.complete({"key": "synthetic:search", "type": "string", "ttl": 60})
        check(redis._selected_type == "string", "Redis key metadata callback did not run")
        check(redis.query_status_band.text() == "Key 元信息已加载",
              "Redis key metadata completion should stay visible after worker finish")
        results["redis_actions"].append("load_key_meta")

        old_value = redis.string_value.toPlainText()
        worker = dispatch_once(redis, redis._load_key_value, "key_value", "正在加载 Key 值…")
        check(redis.string_value.toPlainText() == old_value,
              "Redis value view must retain its old content while loading")
        worker.complete({"key": "synthetic:search", "type": "string", "value": "synthetic-value"})
        check("synthetic-value" in redis.string_value.toPlainText(), "Redis value callback must render returned value")
        check(redis.query_status_band.text() == "Key 值已加载", "Redis value completion status missing")
        check(redis.string_value.toPlainText() != old_value, "Redis value result should update after completion")
        results["redis_actions"].append("load_key_value")

        redis.cmd_input.setText("GET synthetic:search")
        output_before = redis.cmd_output.toPlainText()
        worker = dispatch_once(redis, redis._run_command, "command", "正在执行命令…")
        check(redis.cmd_input.text() == "", "Redis command input should retain its existing clear behavior")
        pending_output = redis.cmd_output.toPlainText()
        check(output_before in pending_output and "GET synthetic:search" in pending_output
              and "synthetic-command-result" not in pending_output,
              "Redis command must keep its existing prompt append while waiting for result rows")
        worker.complete({"columns": ["value"], "rows": [["synthetic-command-result"]]})
        check("synthetic-command-result" in redis.cmd_output.toPlainText(), "Redis command callback did not render rows")
        check(redis.cmd_output.toPlainText() != output_before, "Redis command result should append after completion")
        results["redis_actions"].append("run_command")

        redis.set_language("en")
        worker = dispatch_once(redis, redis._test_connection, "test", "Testing connection…")
        worker.complete({"ok": True})
        check(redis.query_status_band.text() == "Connection test complete",
              "Redis English terminal label missing")
        results["redis_actions"].append("language_en")

        # Superseded background work remains canceled and cannot repaint or mutate rows.
        redis._key_cache = ["preserved:key"]
        stale_generation = redis._scan.start("*")
        # Register through the real background dispatcher and retain its exact fake worker.
        stale = dispatch_once(
            redis, lambda: redis._run_bg("scan", item_redis, generation=stale_generation),
            "scan", "Scanning keys…",
        )
        current_generation = redis._scan.start("*")
        current = dispatch_once(
            redis, lambda: redis._run_bg("scan", item_redis, generation=current_generation),
            "scan", "Scanning keys…",
        )
        check(stale.cancelled, "replacement scan must preserve existing cancellation semantics")
        stale.complete(redis_payload(stale, ["must:not:apply"]))
        check(redis._key_cache == ["preserved:key"], "canceled stale worker must not mutate cached keys")
        check(redis.query_status_band.text() == "Scanning keys…" and redis.query_status_band.is_querying(),
              "canceled stale completion must not hide the replacement task")
        current.fail("synthetic scan failure")
        check(redis._key_cache == ["preserved:key"], "scan failure must preserve old result rows")
        check("synthetic scan failure" in redis.query_status_band.text()
              and not redis.query_status_band.is_querying(), "Redis failure state missing")
        results["redis_actions"].append("replacement_cancellation_and_failure")

        # Independent bg/fg work: both completion orders keep the remaining task busy,
        # then retain the terminal result from the most recently started task.
        for completion_order in ("foreground_first", "background_first"):
            redis.set_language("zh")
            redis._scan.start("*")
            background = dispatch_once(
                redis, lambda: redis._run_bg("scan", item_redis, generation=redis._scan.generation),
                "scan", "正在扫描 Key…",
            )
            foreground = dispatch_once(redis, redis._test_connection, "test", "正在测试连接…")
            if completion_order == "foreground_first":
                foreground.complete({"ok": True})
                check(redis.query_status_band.text() == "正在扫描 Key…" and redis.query_status_band.is_querying(),
                      "older background worker must remain visible when newer foreground finishes first")
                background.complete(redis_payload(background, ["overlap:fg-first"]))
            else:
                background.complete(redis_payload(background, ["overlap:bg-first"]))
                check(redis.query_status_band.text() == "正在测试连接…" and redis.query_status_band.is_querying(),
                      "newer foreground worker must remain visible when older background finishes first")
                foreground.complete({"ok": True})
            check(redis.query_status_band.text() == "连接测试完成" and not redis.query_status_band.is_querying(),
                  "latest-started terminal result must remain after both overlapping workers finish")
            results["redis_overlap_orders"].append(completion_order)
        check(not redis._query_status_workers, "Redis status registry must release finished worker wrappers")

        # MongoDB: completion/failure signals run through the actual panel slots.
        worker = dispatch_once(mongo, mongo._test_connection, "test", "正在测试连接…")
        running_count = len(FakeWorker.instances)
        mongo._test_connection()
        check(len(FakeWorker.instances) == running_count and worker.isRunning(),
              "Mongo foreground busy guard must retain its existing no-second-dispatch behavior")
        worker.complete({"ok": True})
        check(mongo.query_status_band.text() == "连接测试完成" and not mongo.query_status_band.is_querying(),
              "Mongo connection completion status missing")
        check(mongo_info.call_count == 1, "existing Mongo success callback must still run once")
        results["mongo_actions"].append("test")

        worker = dispatch_once(mongo, mongo._refresh_collections, "collections", "正在加载集合…")
        worker.complete({"collections": ["synthetic_collection"]})
        check(mongo.coll_tree.topLevelItemCount() == 1, "Mongo collection callback did not render tree")
        check(mongo.query_status_band.text() == "集合加载完成", "Mongo collection completion status missing")
        results["mongo_actions"].append("refresh_collections")

        mongo._selected_coll = "synthetic_collection"
        worker = dispatch_once(mongo, mongo._load_schema, "schema", "正在加载结构…")
        worker.complete({"collection": "synthetic_collection", "fields": ["name", "active"]})
        check(mongo._schema_fields == ["name", "active"], "Mongo schema callback did not update fields")
        results["mongo_actions"].append("load_schema")

        mongo._render_docs([{"old": "synthetic-row"}], total=1)
        mongo.query_input.setPlainText(
            '{"collection":"synthetic_collection","filter":{"active":true}}'
        )
        worker = dispatch_once(mongo, mongo._run_query, "query", "正在执行查询…")
        check(mongo._docs == [{"old": "synthetic-row"}], "Mongo must preserve visible docs while query is pending")
        check(worker.kwargs.get("filter") == {"active": True}, "Mongo query filter dispatch changed")
        worker.complete({"docs": [{"new": "query-result"}], "total": 1, "limit": 50})
        check(mongo._docs == [{"new": "query-result"}], "Mongo query callback did not replace result docs")
        check(mongo.query_status_band.text() == "查询完成", "Mongo query completion status missing")
        results["mongo_actions"].append("run_query")

        mongo.query_input.setPlainText(
            '{"collection":"synthetic_collection","filter":{"active":false}}'
        )
        worker = dispatch_once(mongo, mongo._reset_query, "query", "正在执行查询…")
        check(mongo.query_input.toPlainText() == "" and worker.kwargs.get("filter") == {},
              "Mongo reset must preserve its existing immediate reset and query filter behavior")
        check(mongo._docs == [{"new": "query-result"}], "Mongo reset must keep old docs until worker returns")
        worker.complete({"docs": [{"reset": True}], "total": 1, "limit": 50})
        check(mongo._docs == [{"reset": True}], "Mongo reset query callback did not update docs")
        results["mongo_actions"].append("reset_query")

        mongo._offset = 50
        mongo.query_input.setPlainText('db.synthetic_collection.find({"active":true})')
        old_docs = list(mongo._docs)
        worker = dispatch_once(mongo, lambda: mongo._shift_page(-1), "query", "正在执行查询…")
        check(worker.kwargs.get("skip") == 0 and mongo._offset == 0,
              "Mongo page shift dispatch must retain its existing offset behavior")
        check(mongo._docs == old_docs, "Mongo page shift must keep visible docs until result arrives")
        worker.complete({"docs": [{"page": 1}], "total": 1, "limit": 50})
        check(mongo._docs == [{"page": 1}], "Mongo page-shift result callback did not update docs")
        results["mongo_actions"].append("shift_page")

        mongo.cmd_input.setText("db.synthetic_collection.find({})")
        worker = dispatch_once(mongo, mongo._run_command, "command", "正在执行命令…")
        check(mongo.cmd_input.text() == "", "Mongo command input should retain its existing clear behavior")
        worker.complete({"docs": [{"command": "synthetic"}]})
        check("synthetic" in mongo.cmd_output.toPlainText(), "Mongo command callback must still append its result")
        results["mongo_actions"].append("run_command")

        mongo.set_language("en")
        status_font = mongo.query_status_band.font()
        status_font.setPointSize(16)
        mongo.query_status_band.setFont(status_font)
        mongo.query_status_band._label.setFont(status_font)
        mongo.resize(1440, 720)
        app.processEvents()
        mongo._render_docs([{"kept": "on failure"}], total=1)
        mongo.query_input.setPlainText(
            '{"collection":"synthetic_collection","filter":{}}'
        )
        worker = dispatch_once(mongo, mongo._run_query, "query", "Running query…")
        old_docs = list(mongo._docs)
        long_error = "synthetic Mongo query failure: " + ("diagnostic detail " * 120)
        worker.fail(long_error)
        check(mongo._docs == old_docs, "Mongo failure must preserve old result docs")
        full_status = "Operation failed: " + long_error
        check(mongo.query_status_band.text() == full_status
              and mongo.query_status_band._label.toolTip() == full_status
              and mongo.query_status_band._label.accessibleName() == full_status,
              "full English error must remain available through the API, tooltip, and accessibility name")
        wide_display = mongo.query_status_band._label.text()
        check(wide_display != full_status and wide_display.endswith("…"),
              "long English error must be elided even before the narrow resize")
        mongo.resize(960, 720)
        app.processEvents()
        medium_display = mongo.query_status_band._label.text()
        check(medium_display != wide_display and len(medium_display) < len(wide_display)
              and medium_display.endswith("…"),
              "long error display must recompute for a 960px band")
        check(mongo.query_status_band.height() == 32
              and mongo.query_status_band._label.geometry().right() < mongo.query_status_band.width(),
              "16pt status text must fit within the fixed-height band geometry")
        mongo.resize(560, 720)
        app.processEvents()
        narrow_display = mongo.query_status_band._label.text()
        check(narrow_display != medium_display and len(narrow_display) < len(medium_display)
              and narrow_display.endswith("…"),
              "status label must recompute its visible elision when resized narrower")
        check(mongo.query_status_band.text() == full_status,
              "resizing must not truncate the full status text API")
        check("synthetic Mongo query failure" in mongo.query_status_band.text()
              and not mongo.query_status_band.is_querying(), "Mongo failure status missing")
        check(mongo_error.call_count == 1, "existing Mongo error callback must still run once")
        results["mongo_actions"].append("failure")

        worker = dispatch_once(mongo, mongo._refresh_collections, "collections", "Loading collections…")
        worker.complete({"collections": ["english_collection"]})
        check(mongo.query_status_band.text() == "Collections loaded",
              "Mongo English terminal label missing")
        results["mongo_actions"].append("language_en")

        redis_registry = redis._query_status_workers
        dispatch_once(redis, redis._test_connection, "test", "正在测试连接…")
        check(len(redis_registry) == 1, "active Redis feedback should track its existing worker identity")
        redis.close()
        mongo.close()
        redis.deleteLater()
        mongo.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        check(not redis_registry, "destroying the panel must release presentation-only worker references")

        # Preserve the existing shared-band caller contract too, using the same TEMP config root.
        from tests.test_query_status_band import QueryStatusBandTests
        QueryStatusBandTests().test_workbench_query_text_shows_the_status_band_without_clearing_rows()
        results["existing_ai_workbench_status_band_regression"] = "passed"
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    check(NETWORK_ATTEMPTS == [], f"network connection attempted: {NETWORK_ATTEMPTS!r}")
    results["network_attempts"] = NETWORK_ATTEMPTS
    results["config_root"] = str(config_root)
    results["redis_status_registry_empty_after_finish_and_destroy"] = True
    return results

try:
    outcome = run_checks()
    path = TEMP_ROOT / "result.json"
    path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print("NOSQL_LOADING_FAKE_WORKER_CHAIN_OK")
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
except Exception:
    traceback.print_exc()
    raise
'''


class NoSqlLoadingFeedbackProcessTest(unittest.TestCase):
    def test_real_panel_actions_follow_fake_worker_lifecycle_without_network(self):
        evidence_dir = Path(tempfile.mkdtemp(prefix="pengtools-nosql-loading-"))
        env = os.environ.copy()
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "PENGTOOLS_DISABLE_MOTION": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "PENGTOOLS_NOSQL_REPO_ROOT": str(ROOT),
            "PENGTOOLS_NOSQL_TEMP_ROOT": str(evidence_dir),
        })
        completed = subprocess.run(
            [sys.executable, "-B", "-c", _CHILD],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=55,
            check=False,
        )
        transcript = completed.stdout + completed.stderr
        (evidence_dir / "test-process.log").write_text(transcript, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, transcript)
        self.assertIn("NOSQL_LOADING_FAKE_WORKER_CHAIN_OK", completed.stdout)

        result_path = evidence_dir / "result.json"
        self.assertTrue(result_path.is_file(), "isolated test must save file-backed evidence")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["evidence_dir"] = str(evidence_dir)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(result["network_attempts"], [])
        self.assertEqual(result["redis_overlap_orders"], ["foreground_first", "background_first"])
        self.assertEqual(
            result["redis_actions"],
            [
                "test", "refresh_keys", "start_search_scan", "load_more_keys",
                "load_key_meta", "load_key_value", "run_command", "language_en",
                "replacement_cancellation_and_failure",
            ],
        )
        self.assertEqual(
            result["mongo_actions"],
            [
                "test", "refresh_collections", "load_schema", "run_query",
                "reset_query", "shift_page", "run_command", "failure", "language_en",
            ],
        )
        self.assertTrue(result["redis_status_registry_empty_after_finish_and_destroy"])
        self.assertEqual(result["existing_ai_workbench_status_band_regression"], "passed")
        self.assertTrue(Path(result["config_root"]).is_relative_to(evidence_dir))
        self.assertTrue((evidence_dir / "test-process.log").is_file())


if __name__ == "__main__":
    unittest.main()
