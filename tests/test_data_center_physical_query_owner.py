"""Regression tests for physical query worker ownership after run timeout."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import Future

from tools.data_center.contracts import CancellationToken, QueryRequest, RunContext
from tools.data_center.agent import AgentRunner
from tools.data_center.agent_query_tool import AgentQueryTool
from tools.data_center.contracts import AgentTurn, ToolCall
from tools.data_center.query_executor import QueryExecutor
from tools.data_center.session_manager import SessionManager
from tools.data_center.tool_registry import QueryOwnerTracker
from tools.data_center.tool_registry import DataCenterToolRegistry


def _context(*, run_id: str = "run-owner", connection_id: str = "conn-owner") -> RunContext:
    return RunContext(
        run_id=run_id,
        tab_id="tab-owner",
        model_config_id="model-owner",
        connection_id=connection_id,
        database="synthetic",
        schema_allowlist=("APP",),
        profile_revision="profile-owner",
        policy_version="dc-02",
    )


class _BlockingDriver:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.close_count = 0
        self.execute_thread: int | None = None
        self.close_thread: int | None = None
        self.future: object | None = None
        self.future_done_during_close: bool | None = None

    def execute_readonly(self, request, cancellation=None):  # noqa: ARG002
        self.execute_thread = threading.get_ident()
        self.entered.set()
        self.release.wait(2.0)
        return {"columns": [{"name": "value"}], "rows": [[1]]}

    def close(self) -> None:
        self.close_count += 1
        self.close_thread = threading.get_ident()
        done = getattr(self.future, "done", None)
        self.future_done_during_close = bool(done()) if callable(done) else None


class DataCenterPhysicalQueryOwnerTests(unittest.TestCase):
    def test_completed_tracked_query_allows_the_next_model_turn(self) -> None:
        context = _context(run_id="run-complete")

        class FastDriver:
            def execute_readonly(self, request, cancellation=None):  # noqa: ARG002
                return {"columns": [{"name": "value"}], "rows": [[7]]}

            def close(self):
                return None

        manager = SessionManager()
        session = manager.create_session(
            context.connection_id,
            lambda **_kwargs: FastDriver(),
            session_id=context.tab_id,
            dialect="oracle",
        )
        executor = QueryExecutor(manager, default_timeout=1.0)
        tracker = QueryOwnerTracker()
        query_tool = AgentQueryTool(manager, executor, owner_tracker=tracker)
        registry = DataCenterToolRegistry(
            {"query_readonly": query_tool},
            query_owner_tracker=tracker,
        )

        class Model:
            def __init__(self):
                self.requests = []

            def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
                self.requests.append(request)
                if len(self.requests) == 1:
                    return AgentTurn(
                        tool_calls=(ToolCall("query-complete", "query_readonly", {"sql": "select 1"}),),
                        finish_reason="tool_calls",
                    )
                return AgentTurn(text="query complete", finish_reason="stop")

        model = Model()
        try:
            result = AgentRunner(model, registry).run(context, "run a synthetic query")
            self.assertEqual(result.final_text, "query complete")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(registry.query_owner_state(context), "idle")
            self.assertEqual(result.tool_results[0].query_id, "query-complete")
        finally:
            registry.release_run(context)
            executor.shutdown(wait=True)

    def test_timeout_retains_exact_future_until_worker_finally_and_close(self) -> None:
        driver = _BlockingDriver()
        manager = SessionManager()
        session = manager.create_session(
            "conn-owner",
            lambda **_kwargs: driver,
            session_id="tab-owner",
            dialect="oracle",
        )
        executor = QueryExecutor(manager, max_workers=1, default_timeout=None)
        context = _context()
        tracker = QueryOwnerTracker()
        tracker.expect_query(context, "query-owner-1")

        execution = executor.execute_tracked(
            QueryRequest(
                session_id=session.session_id,
                query_id="query-owner-1",
                generation=session.generation,
                sql="select 1",
            ),
            cancellation=CancellationToken(),
            wait_timeout=0.2,
        )
        driver.future = execution.future
        self.assertTrue(driver.entered.wait(1.0))
        self.assertEqual(execution.result.code, "TIMEOUT")
        tracker.register_query_owner(
            context,
            "query-owner-1",
            session_id=session.session_id,
            generation=session.generation,
            future=execution.future,
        )
        self.assertIs(tracker._owners[(tracker._context_key(context), "query-owner-1")].future, execution.future)
        self.assertEqual(tracker.state(context), "pending")
        callback_observed = threading.Event()
        execution.future.add_done_callback(lambda _future: callback_observed.set())

        tracker.release_run(context)
        self.assertEqual(tracker.state(context), "pending")
        self.assertTrue(manager.close_session(session.session_id, session.generation))
        self.assertEqual(driver.close_count, 0)

        driver.release.set()
        self.assertEqual(execution.future.result(timeout=2.0).code, "TIMEOUT")
        self.assertEqual(driver.close_count, 1)
        self.assertFalse(driver.future_done_during_close)
        self.assertEqual(driver.close_thread, driver.execute_thread)
        self.assertTrue(callback_observed.wait(1.0))
        self.assertNotIn((tracker._context_key(context), "query-owner-1"), tracker._owners)
        self.assertEqual(tracker.state(context), "idle")
        executor.shutdown(wait=True)

    def test_owner_state_is_bound_to_complete_run_target_and_untracked_calls_fail_closed(self) -> None:
        context = _context()
        other_run = _context(run_id="run-other")
        tracker = QueryOwnerTracker()
        future = Future()
        future.query_id = "query-owner-2"
        future.session_id = context.tab_id
        future.generation = 1

        tracker.expect_query(context, "query-owner-2")
        tracker.register_query_owner(
            context,
            "query-owner-2",
            session_id=context.tab_id,
            generation=1,
            future=future,
        )
        self.assertEqual(tracker.state(context), "pending")
        self.assertEqual(tracker.state(other_run), "idle")

        tracker.expect_query(context, "query-owner-3")
        tracker.finish_expectation(context, "query-owner-3")
        self.assertEqual(tracker.state(context), "failed")
        tracker.release_run(context)
        self.assertEqual(tracker.state(context), "pending")

        future.set_result(None)
        self.assertEqual(tracker.state(context), "idle")

    def test_owner_rejects_missing_malformed_and_unverifiable_future_provenance(self) -> None:
        context = _context()

        bare_tracker = QueryOwnerTracker()
        bare = Future()
        bare_tracker.expect_query(context, "query-bare")
        self.assertFalse(bare_tracker.register_query_owner(
            context,
            "query-bare",
            session_id=context.tab_id,
            generation=1,
            future=bare,
        ))
        self.assertEqual(bare_tracker.state(context), "failed")
        bare_tracker.release_run(context)
        self.assertEqual(bare_tracker.state(context), "failed")
        bare.set_result(None)
        self.assertEqual(bare_tracker.state(context), "idle")

        active_tracker = QueryOwnerTracker()
        active_future = Future()
        active_tracker.expect_query(context, "query-active-failure")
        self.assertFalse(active_tracker.register_query_owner(
            context,
            "query-active-failure",
            session_id=context.tab_id,
            generation=1,
            future=active_future,
        ))
        self.assertEqual(active_tracker.state(context), "failed")
        active_future.set_result(None)
        active_key = (active_tracker._context_key(context), "query-active-failure")
        self.assertIn(active_key, active_tracker._owners)
        self.assertIsNone(active_tracker._owners[active_key].future)
        self.assertEqual(active_tracker.state(context), "failed")
        active_tracker.release_run(context)
        self.assertNotIn(active_key, active_tracker._owners)
        self.assertEqual(active_tracker.state(context), "idle")

        for metadata_name, metadata_value in (
            ("query_id", "wrong-query"),
            ("session_id", "wrong-session"),
            ("generation", 2),
        ):
            with self.subTest(metadata_name=metadata_name):
                tracker = QueryOwnerTracker()
                future = Future()
                future.query_id = "query-metadata"
                future.session_id = context.tab_id
                future.generation = 1
                setattr(future, metadata_name, metadata_value)
                tracker.expect_query(context, "query-metadata")
                self.assertFalse(tracker.register_query_owner(
                    context,
                    "query-metadata",
                    session_id=context.tab_id,
                    generation=1,
                    future=future,
                ))
                self.assertEqual(tracker.state(context), "failed")
                tracker.release_run(context)
                self.assertEqual(tracker.state(context), "failed")
                future.set_result(None)
                self.assertEqual(tracker.state(context), "idle")

        class DuckFuture:
            query_id = "query-duck"
            session_id = context.tab_id
            generation = 1

            @staticmethod
            def done():
                return "not_done"

            @staticmethod
            def add_done_callback(callback):  # noqa: ARG004
                return None

        duck_tracker = QueryOwnerTracker()
        duck_tracker.expect_query(context, "query-duck")
        self.assertFalse(duck_tracker.register_query_owner(
            context,
            "query-duck",
            session_id=context.tab_id,
            generation=1,
            future=DuckFuture(),
        ))
        self.assertEqual(duck_tracker.state(context), "failed")

        for owner_generation in (True, 0):
            with self.subTest(owner_generation=owner_generation):
                tracker = QueryOwnerTracker()
                future = Future()
                future.query_id = "query-generation"
                future.session_id = context.tab_id
                future.generation = 1
                tracker.expect_query(context, "query-generation")
                self.assertFalse(tracker.register_query_owner(
                    context,
                    "query-generation",
                    session_id=context.tab_id,
                    generation=owner_generation,
                    future=future,
                ))
                self.assertEqual(tracker.state(context), "failed")
                tracker.release_run(context)
                future.set_result(None)
                self.assertEqual(tracker.state(context), "idle")

    def test_future_done_and_callback_checks_remain_fail_closed(self) -> None:
        context = _context()

        def reject_callback(_callback):
            raise RuntimeError("registration rejected")

        done_tracker = QueryOwnerTracker()
        done_future = Future()
        done_future.query_id = "query-done-protocol"
        done_future.session_id = context.tab_id
        done_future.generation = 1
        done_future.done = lambda: "not_done"
        done_tracker.expect_query(context, "query-done-protocol")
        self.assertFalse(done_tracker.register_query_owner(
            context,
            "query-done-protocol",
            session_id=context.tab_id,
            generation=1,
            future=done_future,
        ))
        self.assertEqual(done_tracker.state(context), "failed")
        done_tracker.release_run(context)
        self.assertEqual(done_tracker.state(context), "failed")
        done_key = (done_tracker._context_key(context), "query-done-protocol")
        self.assertIs(done_tracker._owners[done_key].future, done_future)
        done_future.set_result(None)
        self.assertNotIn(done_key, done_tracker._owners)
        self.assertEqual(done_tracker.state(context), "idle")

        for callback_name, callback_method in (
            ("callback-noop", lambda _callback: None),
            ("callback-raises", reject_callback),
        ):
            with self.subTest(callback_name=callback_name):
                callback_tracker = QueryOwnerTracker()
                callback_future = Future()
                callback_future.query_id = callback_name
                callback_future.session_id = context.tab_id
                callback_future.generation = 1
                callback_future.add_done_callback = callback_method
                callback_tracker.expect_query(context, callback_name)
                self.assertFalse(callback_tracker.register_query_owner(
                    context,
                    callback_name,
                    session_id=context.tab_id,
                    generation=1,
                    future=callback_future,
                ))
                self.assertEqual(callback_tracker.state(context), "failed")
                callback_tracker.release_run(context)
                callback_key = (callback_tracker._context_key(context), callback_name)
                self.assertIs(callback_tracker._owners[callback_key].future, callback_future)
                self.assertEqual(callback_tracker.state(context), "failed")
                callback_future.set_result(None)
                self.assertNotIn(callback_key, callback_tracker._owners)
                self.assertEqual(callback_tracker.state(context), "idle")

        class RaisingRegistrationTracker(QueryOwnerTracker):
            @staticmethod
            def _register_future_callback(_future, _callback):
                raise RuntimeError("trusted callback registration failed")

        registration_tracker = RaisingRegistrationTracker()
        registration_future = Future()
        registration_future.query_id = "query-registration-exception"
        registration_future.session_id = context.tab_id
        registration_future.generation = 1
        registration_tracker.expect_query(context, "query-registration-exception")
        self.assertFalse(registration_tracker.register_query_owner(
            context,
            "query-registration-exception",
            session_id=context.tab_id,
            generation=1,
            future=registration_future,
        ))
        registration_key = (registration_tracker._context_key(context), "query-registration-exception")
        self.assertEqual(registration_tracker.state(context), "failed")
        registration_tracker.release_run(context)
        self.assertIs(registration_tracker._owners[registration_key].future, registration_future)
        registration_future.set_result(None)
        self.assertNotIn(registration_key, registration_tracker._owners)
        self.assertEqual(registration_tracker.state(context), "idle")


if __name__ == "__main__":
    unittest.main()
