"""Focused tests for the Qt data-center Agent runtime seam."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from tools.data_center.contracts import AgentEvent, AgentEventType, RunContext
from tools.data_center.contracts import AgentTurn, ToolCall
from tools.data_center.budget import Budget, BudgetLimits
from tools.data_center.host_runtime import DataCenterAgentRuntime
from tools.data_center.query_executor import QueryExecutor
from ui.data_center.agent_controller import AgentRunSnapshot, DataCenterAgentController
from ui.data_center.agent_panel import DataCenterAgentPanel


def _snapshot(*, intent: str = "query") -> AgentRunSnapshot:
    return AgentRunSnapshot(
        RunContext(
            run_id="run-controller",
            tab_id="tab-controller",
            model_config_id="model-fixed",
            connection_id="connection-fixed",
            database="database-fixed",
            schema_allowlist=("APP",),
            profile_revision="revision-fixed",
            intent=intent,
            policy_version="dc-02",
        ),
        target="Fixed target",
        model="Fixed model",
    )


class _FakeRuntime:
    def __init__(self, context: RunContext, behavior) -> None:
        self.context = context
        self.behavior = behavior
        self.calls: list[tuple[str, int]] = []
        self.closed = False

    def run(self, prompt, *, cancellation, on_event):
        self.calls.append((prompt, threading.get_ident()))
        return self.behavior(self.context, cancellation, on_event)

    def close(self) -> None:
        self.closed = True


class DataCenterAgentControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self) -> None:
        self.panel = DataCenterAgentPanel()
        self.controllers: list[DataCenterAgentController] = []

    def tearDown(self) -> None:
        for controller in self.controllers:
            self.assertTrue(controller.shutdown())
        self.panel.close()
        self.panel.deleteLater()
        self._flush()

    def _controller(self, snapshot, **kwargs) -> DataCenterAgentController:
        controller = DataCenterAgentController(self.panel, snapshot, **kwargs)
        self.controllers.append(controller)
        return controller

    def _flush(self) -> None:
        self.app.processEvents()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        self.assertTrue(predicate(), "condition did not become true")

    @staticmethod
    def _event(context, sequence, event_type, **payload):
        return AgentEvent(context.run_id, context.tab_id, sequence, event_type, payload)

    def test_success_runs_off_ui_thread_forwards_events_and_cleans_up(self) -> None:
        snapshot = _snapshot()

        def behavior(context, _cancellation, emit):
            emit(self._event(context, 1, AgentEventType.MODEL_TEXT_DELTA, text="worker answer"))
            emit(self._event(context, 2, AgentEventType.FINAL, text="worker answer", verified=True))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        self.panel.prompt_input.setPlainText("  list orders  ")
        self.panel.send_button.click()

        self._wait_until(lambda: not controller.is_running)
        self.assertEqual(runtime.calls[0][0], "list orders")
        self.assertNotEqual(runtime.calls[0][1], threading.get_ident())
        self.assertEqual(self.panel.prompt_input.toPlainText(), "")
        self.assertIn("worker answer", self.panel.answer_view.toPlainText())
        self.assertTrue(runtime.closed)
        self.assertIsNone(controller._worker)
        self.assertIsNone(controller._thread)

    def test_owned_query_worker_holds_qthread_cleanup_until_driver_release(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()
        release = threading.Event()
        future_holder = []
        model_calls = []

        class BlockingDriver:
            def __init__(self):
                self.close_count = 0
                self.execute_thread = None
                self.close_thread = None
                self.future_done_during_close = None

            def execute_readonly(self, request, cancellation=None):  # noqa: ARG002
                self.execute_thread = threading.get_ident()
                entered.set()
                release.wait(3.0)
                return {"columns": [{"name": "value"}], "rows": [[1]]}

            def close(self):
                self.close_count += 1
                self.close_thread = threading.get_ident()
                self.future_done_during_close = future_holder[0].done()

        driver = BlockingDriver()

        class FakeMetadata:
            def execute(self, *args, **kwargs):  # noqa: ARG002
                raise AssertionError("metadata tool is not part of this run")

        class TwoQueryModel:
            def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
                model_calls.append(request)
                return AgentTurn(
                    tool_calls=(
                        ToolCall("q2", "query_readonly", {"sql": "select 1"}),
                        ToolCall("q3", "query_readonly", {"sql": "select 2"}),
                    ),
                    finish_reason="tool_calls",
                )

        original_executor = QueryExecutor
        constructed = []

        def quick_executor(*args, **kwargs):
            kwargs["default_timeout"] = 0.05
            executor = original_executor(*args, **kwargs)
            original_tracked = executor.execute_tracked

            def tracked(*tracked_args, **tracked_kwargs):
                execution = original_tracked(*tracked_args, **tracked_kwargs)
                future_holder.append(execution.future)
                return execution

            executor.execute_tracked = tracked
            constructed.append(executor)
            return executor

        with patch("tools.data_center.host_runtime.QueryExecutor", side_effect=quick_executor):
            runtime = DataCenterAgentRuntime(
                snapshot.context,
                provider="oracle",
                model=TwoQueryModel(),
                metadata_host=FakeMetadata(),
                relational_factory=lambda **_kwargs: driver,
                budget=Budget(BudgetLimits(max_duration_s=None)),
            )

        self.assertTrue(runtime._owned_query_executor)
        self.assertIs(runtime.query_executor, constructed[0])
        controller = self._controller(snapshot, runtime=runtime)
        cleanup = []
        controller.cleanup_finished.connect(lambda: cleanup.append(True))
        self.panel.set_display_context("en", "query")
        self.panel.prompt_input.setPlainText("run a blocking synthetic query")
        self.panel.send_button.click()

        try:
            self._wait_until(entered.is_set)
            self.assertIsNone(runtime.runner._new_budget(None).limits.max_duration_s)
            self.assertEqual(runtime.query_executor.default_timeout, 0.05)
            self._wait_until(lambda: bool(future_holder))
            self._flush()
            self.assertIn(
                "query worker was unfinished when this run terminated",
                self.panel.phase_label.text(),
                self.panel.phase_label.text().encode("unicode_escape").decode("ascii"),
            )
            self.assertEqual(len(model_calls), 1)
            self.assertTrue(controller.is_running)
            self.assertEqual(cleanup, [])
            self.assertEqual(driver.close_count, 0)
            self.assertEqual(runtime.registry.query_owner_state(snapshot.context), "pending")
            session = runtime.session_manager.get_session(snapshot.context.tab_id)
            self.assertIsNotNone(session)
            self.assertTrue(runtime.session_manager.close_session(session.session_id, session.generation))
            self.assertEqual(driver.close_count, 0)
        finally:
            release.set()

        self._wait_until(lambda: not controller.is_running)
        self.assertEqual(driver.close_count, 1)
        self.assertFalse(driver.future_done_during_close)
        self.assertEqual(driver.close_thread, driver.execute_thread)
        self.assertEqual(cleanup, [True])
        self.assertTrue(runtime._closed)
        self.assertEqual(runtime.registry.query_owner_state(snapshot.context), "idle")
        self.assertIn("query worker was unfinished when this run terminated", self.panel.phase_label.text())

    def test_run_finished_precedes_cleanup_finished_and_cleanup_clears_refs(self) -> None:
        snapshot = _snapshot()

        def behavior(context, _cancellation, emit):
            emit(self._event(context, 1, AgentEventType.FINAL, text="ordered"))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        order: list[str] = []
        states: list[tuple[object, object, object]] = []

        def on_run_finished(_outcome):
            order.append("run")
            self.assertTrue(controller.is_running)

        def on_cleanup_finished():
            order.append("cleanup")
            states.append((controller._worker, controller._thread, controller._cancellation))

        controller.run_finished.connect(on_run_finished)
        controller.cleanup_finished.connect(on_cleanup_finished)
        self.panel.prompt_input.setPlainText("ordered lifecycle")
        self.panel.send_button.click()

        self._wait_until(lambda: order == ["run", "cleanup"])
        self.assertEqual(states, [(None, None, None)])

    def test_exception_is_redacted_in_ui_and_outcome_then_worker_is_cleaned(self) -> None:
        snapshot = _snapshot()

        def behavior(_context, _cancellation, _emit):
            raise RuntimeError("password=top-secret")

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        outcomes: list[object] = []
        controller.run_finished.connect(outcomes.append)
        self.panel.prompt_input.setPlainText("fail safely")
        self.panel.send_button.click()

        self._wait_until(lambda: not controller.is_running)
        self.assertIn("执行失败", self.panel.phase_label.text())
        self.assertIn("Agent runtime failed", self.panel.evidence_view.toPlainText())
        self.assertNotIn("top-secret", self.panel.evidence_view.toPlainText())
        self.assertEqual(outcomes[0].error, "RuntimeError")
        self.assertNotIn("top-secret", repr(outcomes[0]))
        self.assertTrue(runtime.closed)

    def test_stop_cancels_the_worker_token_and_reaches_cancelled_state(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()

        def behavior(context, cancellation, emit):
            entered.set()
            self.assertTrue(cancellation.wait(1.0))
            emit(self._event(context, 1, AgentEventType.FINAL, text="late success"))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        self.panel.prompt_input.setPlainText("long request")
        self.panel.send_button.click()
        self._wait_until(entered.is_set)
        self.panel.stop_button.click()

        self._wait_until(lambda: not controller.is_running)
        self.assertIn("已取消", self.panel.phase_label.text())
        self.assertNotIn("late success", self.panel.answer_view.toPlainText())
        self.assertFalse(self.panel.is_cancel_pending)
        self.assertTrue(runtime.closed)

    def test_draft_runs_in_worker_without_controller_database_construction(self) -> None:
        snapshot = _snapshot(intent="draft")
        factory_threads: list[int] = []
        database_constructions: list[bool] = []
        runtimes: list[_FakeRuntime] = []

        def database_sentinel():
            database_constructions.append(True)
            raise AssertionError("controller must not construct database resources")

        def factory(context):
            factory_threads.append(threading.get_ident())

            def behavior(bound_context, _cancellation, emit):
                self.assertEqual(bound_context.intent, "draft")
                emit(self._event(bound_context, 1, AgentEventType.FINAL, text="draft answer"))

            runtime = _FakeRuntime(context, behavior)
            runtime.database_factory = database_sentinel
            runtimes.append(runtime)
            return runtime

        controller = self._controller(snapshot, runtime_factory=factory)
        self.panel.prompt_input.setPlainText("draft only")
        self.panel.send_button.click()
        self._wait_until(lambda: not controller.is_running)

        self.assertEqual(database_constructions, [])
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0].calls[0][0], "draft only")
        self.assertNotEqual(factory_threads[0], threading.get_ident())
        self.assertIn("draft answer", self.panel.answer_view.toPlainText())
        self.assertTrue(runtimes[0].closed)

    def test_duplicate_send_does_not_start_a_second_runtime(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()
        release = threading.Event()
        runtimes: list[_FakeRuntime] = []
        factory_threads: list[int] = []

        def behavior(context, _cancellation, emit):
            entered.set()
            release.wait(1.0)
            emit(self._event(context, 1, AgentEventType.FINAL, text="done"))

        def factory(context):
            factory_threads.append(threading.get_ident())
            runtime = _FakeRuntime(context, behavior)
            runtimes.append(runtime)
            return runtime

        controller = self._controller(snapshot, runtime_factory=factory)
        rejected: list[str] = []
        controller.run_rejected.connect(rejected.append)
        self.panel.prompt_input.setPlainText("once")
        self.panel.send_button.click()
        self._wait_until(entered.is_set)
        self.panel.prompt_send_requested.emit("duplicate")
        release.set()

        self._wait_until(lambda: not controller.is_running)
        self.assertEqual(len(runtimes), 1)
        self.assertNotEqual(factory_threads[0], threading.get_ident())
        self.assertEqual(rejected, ["run_active"])

    def test_shutdown_cancels_waits_and_closes_an_active_runtime(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()

        def behavior(_context, cancellation, _emit):
            entered.set()
            self.assertTrue(cancellation.wait(1.0))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        self.panel.prompt_input.setPlainText("shutdown request")
        self.panel.send_button.click()
        self._wait_until(entered.is_set)

        self.assertTrue(controller.shutdown(1_000))
        self._flush()
        self.assertFalse(controller.is_running)
        self.assertTrue(runtime.closed)
        self.assertIn("已取消", self.panel.phase_label.text())

    def test_dispose_is_idempotent_and_disconnects_all_panel_intents(self) -> None:
        snapshot = _snapshot()
        runtime = _FakeRuntime(snapshot.context, lambda *_args: None)
        controller = self._controller(snapshot, runtime=runtime)
        rejected: list[str] = []
        controller.run_rejected.connect(rejected.append)

        self.assertTrue(controller.dispose())
        self.assertTrue(controller.dispose())
        self.panel.prompt_input.setPlainText("must stay local")
        self.panel.prompt_send_requested.emit("must stay local")
        self.panel.cancel_requested.emit(snapshot.context.run_id)
        self.panel.new_conversation_requested.emit()
        self._flush()

        self.assertEqual(runtime.calls, [])
        self.assertEqual(rejected, [])
        self.assertFalse(controller.is_running)

    def test_running_dispose_cleans_up_and_old_facade_cannot_pollute_new_conversation(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()

        def behavior(context, cancellation, emit):
            entered.set()
            self.assertTrue(cancellation.wait(1.0))
            emit(self._event(context, 1, AgentEventType.FINAL, text="late disposed final"))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        order: list[str] = []
        controller.run_finished.connect(lambda _outcome: order.append("run"))
        controller.cleanup_finished.connect(lambda: order.append("cleanup"))
        self.panel.prompt_input.setPlainText("dispose active")
        self.panel.send_button.click()
        self._wait_until(entered.is_set)
        self.panel.new_conversation_button.click()

        self.assertTrue(controller.dispose(1_000))
        self._flush()
        self.assertEqual(order, ["run", "cleanup"])
        self.assertIsNone(controller._worker)
        self.assertIsNone(controller._thread)
        self.assertIsNone(controller._cancellation)
        self.assertNotIn("late disposed final", self.panel.answer_view.toPlainText())
        self.assertTrue(runtime.closed)

    def test_new_conversation_drops_late_events_from_old_generation(self) -> None:
        snapshot = _snapshot()
        entered = threading.Event()
        release = threading.Event()

        def behavior(context, _cancellation, emit):
            entered.set()
            release.wait(1.0)
            emit(self._event(context, 1, AgentEventType.MODEL_TEXT_DELTA, text="late old text"))
            emit(self._event(context, 2, AgentEventType.FINAL, text="late old final"))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        self.panel.prompt_input.setPlainText("old request")
        self.panel.send_button.click()
        self._wait_until(entered.is_set)
        self.panel.new_conversation_button.click()
        release.set()

        self._wait_until(lambda: not controller.is_running)
        self.assertNotIn("late old", self.panel.answer_view.toPlainText())
        self.assertFalse(self.panel.is_active)
        self.assertEqual(self.panel.run_id, "")
        self.assertTrue(runtime.closed)

    def test_wrong_scope_terminal_does_not_suppress_synthetic_final(self) -> None:
        snapshot = _snapshot()

        class Result:
            final_text = "scoped synthetic final"

        def behavior(context, _cancellation, emit):
            emit(
                AgentEvent(
                    "wrong-run",
                    "wrong-tab",
                    99,
                    AgentEventType.FINAL,
                    {"text": "wrong terminal"},
                )
            )
            return Result()

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        self.panel.prompt_input.setPlainText("scope check")
        self.panel.send_button.click()

        self._wait_until(lambda: not controller.is_running)
        self.assertIn("scoped synthetic final", self.panel.answer_view.toPlainText())
        self.assertNotIn("wrong terminal", self.panel.answer_view.toPlainText())
        self.assertIn("已完成", self.panel.phase_label.text())

    def test_snapshot_is_consumed_after_one_completed_run(self) -> None:
        snapshot = _snapshot()

        def behavior(context, _cancellation, emit):
            emit(self._event(context, 1, AgentEventType.FINAL, text="first"))

        runtime = _FakeRuntime(snapshot.context, behavior)
        controller = self._controller(snapshot, runtime=runtime)
        rejected: list[str] = []
        controller.run_rejected.connect(rejected.append)
        self.panel.prompt_input.setPlainText("first prompt")
        self.panel.send_button.click()
        self._wait_until(lambda: not controller.is_running)

        self.panel.prompt_input.setPlainText("second prompt")
        self.panel.prompt_send_requested.emit("second prompt")
        self._flush()
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(rejected, ["snapshot_consumed"])
        self.assertEqual(self.panel.prompt_input.toPlainText(), "second prompt")


if __name__ == "__main__":
    unittest.main()
