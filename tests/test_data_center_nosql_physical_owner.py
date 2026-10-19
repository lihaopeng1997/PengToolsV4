"""Physical NoSQL Future ownership and owner-thread cleanup tests."""

from __future__ import annotations

from contextlib import ExitStack
from concurrent.futures import Future
import json
import threading
import unittest
from unittest.mock import patch

import config
import tools.intranet_llm as catalog
import tools.data_center.host_nosql as host_nosql
from tools.agent_core.budget import BudgetLimits
from tools.agent_core.contracts import AgentStatus, CancellationToken
from tools.data_center.contracts import AgentTurn, RunContext, ToolCall, ToolResult
from tools.data_center.host_runtime import DataCenterAgentRuntime


_CALL_ARGS = {
    "redis": {"operation": "SCAN", "cursor": 0, "limit": 1},
    "mongodb": {"collection_id": "orders", "operation": "find", "filter": {}, "limit": 1},
}
_TOOL_NAMES = {"redis": "redis_read", "mongodb": "mongo_read"}
_TRUSTED_FUTURE_ADD_DONE_CALLBACK = Future.add_done_callback


def _context(provider: str, run_id: str) -> RunContext:
    return RunContext(
        run_id=run_id,
        tab_id=f"tab-{run_id}",
        model_config_id="synthetic-model",
        connection_id=f"connection-{provider}",
        database="0" if provider == "redis" else "synthetic",
        schema_allowlist=("orders",),
        profile_revision="synthetic-revision",
        policy_version="dc-02",
    )


class _MetadataHost:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - not used by these tests
        return ToolResult(ok=False, code="CAPABILITY_UNAVAILABLE")


class _SequenceModel:
    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = list(calls)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        if self.calls:
            return AgentTurn(tool_calls=(self.calls.pop(0),), finish_reason="tool_calls")
        return AgentTurn(text="synthetic complete", finish_reason="stop")


class _FakeLease:
    def __init__(
        self,
        provider: str,
        *,
        execute_entered: threading.Event | None = None,
        execute_release: threading.Event | None = None,
        close_entered: threading.Event | None = None,
        close_release: threading.Event | None = None,
    ) -> None:
        self.provider = provider
        self.execute_entered = execute_entered
        self.execute_release = execute_release
        self.close_entered = close_entered
        self.close_release = close_release
        self.calls: list[dict] = []
        self.execute_threads: list[int] = []
        self.close_threads: list[int] = []
        self.close_count = 0

    def execute(self, request, *, cancellation=None, query_id=None, context=None):  # noqa: ARG002
        self.calls.append(dict(request))
        self.execute_threads.append(threading.get_ident())
        if self.execute_entered is not None:
            self.execute_entered.set()
        if self.execute_release is not None:
            self.execute_release.wait(5.0)
        cursor_name = "next_cursor" if self.provider == "redis" else "next_page_token"
        token = f"cursor-{self.provider}"
        data = {cursor_name: token} if len(self.calls) == 1 else {"rows": []}
        return ToolResult(ok=True, code="OK", query_id=query_id, data=data)

    def close(self) -> None:
        self.close_count += 1
        self.close_threads.append(threading.get_ident())
        if self.close_entered is not None:
            self.close_entered.set()
        if self.close_release is not None:
            self.close_release.wait(5.0)


class _BlockingFactory:
    def __init__(self, lease: _FakeLease) -> None:
        self.lease = lease
        self.entered = threading.Event()
        self.release = threading.Event()
        self.threads: list[int] = []

    def __call__(self, **_kwargs):
        self.threads.append(threading.get_ident())
        self.entered.set()
        self.release.wait(5.0)
        return self.lease


class _BlockingPostReadProfile:
    def __init__(self, context: RunContext) -> None:
        self.context = context
        self.calls = 0
        self.threads: list[int] = []
        self.post_read_entered = threading.Event()
        self.post_read_release = threading.Event()

    def profile_snapshot(self, *, connection_id, profile_revision):  # noqa: ARG002
        self.calls += 1
        self.threads.append(threading.get_ident())
        if self.calls == 2:
            self.post_read_entered.set()
            self.post_read_release.wait(5.0)
        return {
            "connection_id": self.context.connection_id,
            "profile_revision": self.context.profile_revision,
            "database": self.context.database,
        }


class _HostLeaseFacade(_FakeLease):
    """Fake facade held by a real HostNoSQLLease/HostNoSQLRouter."""


class _ExternalRouter:
    def __init__(self) -> None:
        self.execute_threads: list[int] = []
        self.close_count = 0

    def execute(self, tool_name, request, *, cancellation=None, query_id=None, context=None):  # noqa: ARG002
        self.execute_threads.append(threading.get_ident())
        return ToolResult(ok=True, code="OK", query_id=query_id, data={"rows": []})

    def close(self) -> None:
        self.close_count += 1


class DataCenterNoSQLPhysicalOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._guard_stack = ExitStack()
        self._guards = {}

        def deny(name: str):
            def blocked(*_args, **_kwargs):
                raise AssertionError(f"forbidden real dependency reached: {name}")
            return blocked

        targets = (
            ("profile_reader", catalog, "_cfg_for_call"),
            ("catalog_reader", catalog, "load_model_catalog"),
            ("urlopen", __import__("urllib.request", fromlist=["urlopen"]), "urlopen"),
            ("socket_create_connection", __import__("socket"), "create_connection"),
            ("socket_connect", __import__("socket").socket, "connect"),
            ("settings_load", config, "load_settings"),
            ("settings_save", config, "save_settings"),
            ("default_decryptor", host_nosql, "_default_decryptor"),
            ("default_redis_client", host_nosql, "_default_redis_client_factory"),
            ("default_mongo_client", host_nosql, "_default_mongo_client_factory"),
        )
        for name, owner, attribute in targets:
            mocked = self._guard_stack.enter_context(
                patch.object(owner, attribute, side_effect=deny(name))
            )
            self._guards[name] = mocked
        self.addCleanup(self._guard_stack.close)

    def tearDown(self) -> None:
        counts = {name: guard.call_count for name, guard in self._guards.items()}
        self.assertEqual(counts, {name: 0 for name in self._guards})

    def _runtime(
        self,
        context: RunContext,
        model: _SequenceModel,
        *,
        factory=None,
        router=None,
        host=None,
        cancellation: CancellationToken | None = None,
        duration: float = 3.0,
    ) -> DataCenterAgentRuntime:
        provider = "redis" if context.database == "0" else "mongodb"
        return DataCenterAgentRuntime(
            context,
            provider=provider,
            model=model,
            metadata_host=_MetadataHost(),
            nosql_host=host,
            nosql_factory=factory,
            nosql_router=router,
            cancellation=cancellation,
            budget_limits=BudgetLimits(max_duration_s=duration),
        )

    @staticmethod
    def _tool_call(provider: str, call_id: str, arguments: dict | None = None) -> ToolCall:
        return ToolCall(call_id, _TOOL_NAMES[provider], arguments or dict(_CALL_ARGS[provider]))

    @staticmethod
    def _owner_future(runtime: DataCenterAgentRuntime, call_id: str):
        tracker = runtime.query_owner_tracker
        key = (tracker._context_key(runtime.context), call_id)
        return tracker, key, tracker._owners[key]

    @staticmethod
    def _watch_retirement(future: Future) -> threading.Event:
        retired = threading.Event()
        future.add_done_callback(lambda _future: retired.set())
        return retired

    def _assert_retired(self, tracker, key, retired: threading.Event) -> None:
        self.assertTrue(retired.wait(2.0), "NoSQL operation Future did not complete")
        self.assertNotIn(key, tracker._owners)

    def test_both_providers_reuse_healthy_factory_lease_for_successful_reads(self) -> None:
        for provider in ("redis", "mongodb"):
            with self.subTest(provider=provider):
                context = _context(provider, f"normal-{provider}")
                first_args = dict(_CALL_ARGS[provider])
                second_args = dict(first_args)
                if provider == "redis":
                    second_args["cursor"] = f"cursor-{provider}"
                calls = [
                    self._tool_call(provider, f"{provider}-page-1", first_args),
                    self._tool_call(provider, f"{provider}-page-2", second_args),
                ]
                model = _SequenceModel(calls)
                lease = _FakeLease(provider)
                factory_calls = []

                def factory(**_kwargs):
                    factory_calls.append(threading.get_ident())
                    return lease

                runtime = self._runtime(context, model, factory=factory)
                try:
                    result = runtime.run("read two synthetic pages")
                    self.assertEqual(result.final_text, "synthetic complete")
                    self.assertEqual(len(model.requests), 3)
                    self.assertEqual(len(factory_calls), 1)
                    self.assertEqual(len(lease.calls), 2)
                    if provider == "redis":
                        self.assertEqual(lease.calls[1].get("cursor"), f"cursor-{provider}")
                    else:
                        # The current Mongo tool contract has no page-token
                        # argument; verify healthy lease reuse across valid reads.
                        self.assertEqual(lease.calls[1], first_args)
                    self.assertEqual(result.tool_results[0].query_id, f"{provider}-page-1")
                    self.assertEqual(result.tool_results[1].query_id, f"{provider}-page-2")
                    tool_messages = [message for message in result.messages if message.get("role") == "tool"]
                    self.assertEqual([message.get("tool_call_id") for message in tool_messages], [f"{provider}-page-1", f"{provider}-page-2"])
                    self.assertEqual(lease.close_count, 0)
                finally:
                    runtime.close()
                self.assertEqual(lease.close_count, 1)
                self.assertEqual(lease.close_threads[0], lease.execute_threads[0])
                self.assertEqual(lease.execute_threads[0], lease.execute_threads[1])

    def test_timeout_and_cancel_keep_exact_future_until_execute_or_factory_cleanup(self) -> None:
        cases = (
            ("redis", "execute", "timeout"),
            ("mongodb", "execute", "cancel"),
            ("redis", "factory", "cancel"),
            ("mongodb", "factory", "timeout"),
        )
        for provider, stage, stop_kind in cases:
            with self.subTest(provider=provider, stage=stage, stop_kind=stop_kind):
                context = _context(provider, f"{stage}-{stop_kind}-{provider}")
                call_id = f"call-{context.run_id}"
                execute_entered = threading.Event()
                execute_release = threading.Event()
                lease = _FakeLease(
                    provider,
                    execute_entered=execute_entered if stage == "execute" else None,
                    execute_release=execute_release if stage == "execute" else None,
                )
                factory = _BlockingFactory(lease) if stage == "factory" else (lambda **_kwargs: lease)
                factory_entered = factory.entered if isinstance(factory, _BlockingFactory) else threading.Event()
                factory_release = factory.release if isinstance(factory, _BlockingFactory) else threading.Event()
                cancellation = CancellationToken()
                model = _SequenceModel([self._tool_call(provider, call_id)])
                duration = 0.08 if stop_kind == "timeout" else 3.0
                runtime = self._runtime(
                    context,
                    model,
                    factory=factory,
                    cancellation=cancellation,
                    duration=duration,
                )
                run_result = []
                run_done = threading.Event()

                def run():
                    try:
                        run_result.append(runtime.run("read one synthetic page"))
                    finally:
                        run_done.set()

                run_thread = threading.Thread(target=run, name=f"test-run-{context.run_id}")
                closer_done = threading.Event()
                closer_started = threading.Event()

                def close_runtime():
                    closer_started.set()
                    runtime.close()
                    closer_done.set()

                closer = None
                try:
                    run_thread.start()
                    entered = execute_entered if stage == "execute" else factory_entered
                    self.assertTrue(entered.wait(2.0), "NoSQL operation did not reach the blocking fake")
                    if stop_kind == "cancel":
                        cancellation.cancel("synthetic cancel")
                    self.assertTrue(run_done.wait(2.0), "logical run did not return at cancellation/deadline")
                    self.assertEqual(len(run_result), 1)
                    result = run_result[0]
                    expected_code = "TIMEOUT" if stop_kind == "timeout" else "CANCELLED"
                    self.assertEqual(result.status, AgentStatus.TIMEOUT if stop_kind == "timeout" else AgentStatus.CANCELLED)
                    self.assertEqual(len(model.requests), 1, "pending physical operation allowed another model turn")
                    self.assertEqual(len(result.tool_results), 1)
                    self.assertEqual(result.tool_results[0].code, expected_code)
                    self.assertEqual(result.tool_results[0].query_id, call_id)
                    paired = [message for message in result.messages if message.get("role") == "tool" and message.get("tool_call_id") == call_id]
                    self.assertEqual(len(paired), 1)
                    tracker, key, owner = self._owner_future(runtime, call_id)
                    self.assertEqual(runtime.registry.query_owner_state(context), "pending")
                    self.assertIs(type(owner.future), Future)
                    self.assertEqual(owner.future.query_id, call_id)
                    self.assertEqual(owner.future.session_id, context.tab_id)
                    self.assertIs(type(owner.future.generation), int)
                    self.assertGreater(owner.future.generation, 0)
                    self.assertTrue(owner.released)
                    self.assertFalse(owner.future.done())
                    retired = self._watch_retirement(owner.future)

                    closer = threading.Thread(target=close_runtime, name=f"test-close-{context.run_id}")
                    closer.start()
                    self.assertTrue(closer_started.wait(1.0))
                    self.assertFalse(closer_done.wait(0.05), "shutdown returned while the owned operation remained blocked")
                    if stage == "execute":
                        execute_release.set()
                    else:
                        factory_release.set()
                    self.assertTrue(closer_done.wait(2.0), "runtime shutdown did not drain owner-thread cleanup")
                    closer.join(1.0)
                    run_thread.join(1.0)
                    self.assertFalse(run_thread.is_alive())
                    self.assertTrue(owner.future.done())
                    self._assert_retired(tracker, key, retired)
                    self.assertEqual(lease.close_count, 1)
                    self.assertEqual(lease.close_threads[0], lease.execute_threads[0] if stage == "execute" else factory.threads[0])
                finally:
                    execute_release.set()
                    factory_release.set()
                    if run_thread.is_alive():
                        run_thread.join(2.0)
                    if closer is not None and closer.is_alive():
                        closer.join(2.0)
                    runtime.close()

    def test_owned_router_cleanup_is_in_future_and_external_router_stays_open(self) -> None:
        context = _context("redis", "owned-router-cleanup")
        call_id = "owned-router-call"
        execute_entered = threading.Event()
        execute_release = threading.Event()
        close_entered = threading.Event()
        close_release = threading.Event()
        facade = _HostLeaseFacade(
            "redis",
            execute_entered=execute_entered,
            execute_release=execute_release,
            close_entered=close_entered,
            close_release=close_release,
        )
        target = host_nosql.NoSQLTarget(
            context.connection_id,
            context.profile_revision,
            "redis",
            "standalone",
            context.database,
        )
        lease = host_nosql.HostNoSQLLease(target, facade, facade, facade)
        model = _SequenceModel([self._tool_call("redis", call_id)])
        runtime = self._runtime(context, model, router={"redis": lease}, duration=0.08)
        run_result = []
        run_done = threading.Event()

        def run():
            try:
                run_result.append(runtime.run("read synthetic key"))
            finally:
                run_done.set()

        worker = threading.Thread(target=run, name="test-owned-router-run")
        close_done = threading.Event()

        def close_runtime():
            runtime.close()
            close_done.set()

        closer = threading.Thread(target=close_runtime, name="test-owned-router-close")
        try:
            worker.start()
            self.assertTrue(execute_entered.wait(2.0))
            self.assertTrue(run_done.wait(2.0))
            self.assertEqual(len(run_result[0].tool_results), 1)
            self.assertEqual(run_result[0].tool_results[0].code, "TIMEOUT")
            tracker, key, owner = self._owner_future(runtime, call_id)
            self.assertEqual(runtime.registry.query_owner_state(context), "pending")
            retired = self._watch_retirement(owner.future)
            closer.start()
            self.assertFalse(close_entered.wait(0.05), "owned router closed while its operation was still executing")
            execute_release.set()
            self.assertTrue(close_entered.wait(2.0), "stopped operation did not start owned-router cleanup")
            self.assertFalse(owner.future.done(), "operation Future completed before its cleanup returned")
            self.assertEqual(runtime.registry.query_owner_state(context), "pending")
            close_release.set()
            self.assertTrue(close_done.wait(2.0))
            closer.join(1.0)
            self._assert_retired(tracker, key, retired)
            self.assertEqual(facade.close_count, 1)
            self.assertEqual(facade.close_threads[0], facade.execute_threads[0])
            self.assertEqual(len(model.requests), 1)
        finally:
            execute_release.set()
            close_release.set()
            if worker.is_alive():
                worker.join(2.0)
            if closer.is_alive():
                closer.join(2.0)
            runtime.close()

        external_context = _context("redis", "external-router")
        external = _ExternalRouter()
        external_model = _SequenceModel([self._tool_call("redis", "external-call")])
        external_runtime = self._runtime(external_context, external_model, router=external)
        try:
            result = external_runtime.run("read synthetic key")
            self.assertEqual(result.final_text, "synthetic complete")
        finally:
            external_runtime.close()
        self.assertEqual(external.close_count, 0)
        self.assertEqual(len(external.execute_threads), 1)

    def test_cross_tool_call_id_reuse_remains_a_conflict(self) -> None:
        context = _context("redis", "cross-tool-id")
        entered = threading.Event()
        release = threading.Event()
        lease = _FakeLease("redis", execute_entered=entered, execute_release=release)
        model = _SequenceModel([self._tool_call("redis", "shared-call-id")])
        runtime = self._runtime(context, model, factory=lambda **_kwargs: lease, duration=0.08)
        run_result = []
        run_done = threading.Event()

        def run_first_call():
            try:
                run_result.append(runtime.run("start one blocked read"))
            finally:
                run_done.set()

        worker = threading.Thread(target=run_first_call, name="test-cross-tool-owner")
        try:
            worker.start()
            self.assertTrue(entered.wait(2.0))
            self.assertTrue(run_done.wait(2.0))
            self.assertEqual(run_result[0].tool_results[0].code, "TIMEOUT")
            tracker, key, owner = self._owner_future(runtime, "shared-call-id")
            self.assertIs(type(owner.future), Future)
            first_future = owner.future
            retired = self._watch_retirement(first_future)
            self.assertTrue(owner.released)
            self.assertEqual(runtime.registry.query_owner_state(context), "pending")

            duplicate_same_tool = runtime.registry.execute(
                self._tool_call("redis", "shared-call-id"),
                context=context,
                cancellation=CancellationToken(),
            )
            duplicate_cross_tool = runtime.registry.execute(
                ToolCall("shared-call-id", "mongo_read", {"collection_id": "orders", "operation": "find", "filter": {}}),
                context=context,
                cancellation=CancellationToken(),
            )
            self.assertEqual(duplicate_same_tool.code, "TOOL_FAILED")
            self.assertEqual(duplicate_cross_tool.code, "TOOL_FAILED")
            self.assertIs(owner.future, first_future)
            self.assertEqual(runtime.registry.query_owner_state(context), "failed")
            self.assertEqual(len(lease.calls), 1, "duplicate IDs submitted a second physical operation")

            runtime.run("the failed owner must remain closed")
            self.assertEqual(len(model.requests), 1, "failed physical owner allowed a later model request")
            self.assertEqual(len(lease.calls), 1)

            release.set()
            self._assert_retired(tracker, key, retired)
            self.assertEqual(runtime.registry.query_owner_state(context), "idle")
        finally:
            release.set()
            if worker.is_alive():
                worker.join(2.0)
            runtime.close()

    def test_submit_rejection_marks_the_expected_owner_failed(self) -> None:
        context = _context("redis", "submit-rejected")
        call_id = "rejected-call"
        model = _SequenceModel([self._tool_call("redis", call_id)])
        lease = _FakeLease("redis")
        runtime = self._runtime(context, model, factory=lambda **_kwargs: lease)
        pool = runtime._nosql_worker._pool
        original_submit = pool.submit

        def reject_query(function, *args, **kwargs):
            if function == runtime._nosql_worker._run:
                raise RuntimeError("synthetic submit rejection")
            return original_submit(function, *args, **kwargs)

        pool.submit = reject_query
        try:
            result = runtime.registry.execute(
                self._tool_call("redis", call_id),
                context=context,
                cancellation=CancellationToken(),
            )
            self.assertEqual(len(model.requests), 0)
            self.assertEqual(result.code, "QUERY_FAILED")
            self.assertEqual(runtime.registry.query_owner_state(context), "failed")
            self.assertEqual(lease.calls, [])
        finally:
            pool.submit = original_submit
            runtime.close()

    def test_post_read_profile_block_returns_stopped_result_and_cleans_before_future_done(self) -> None:
        cases = (
            ("redis", "timeout"),
            ("mongodb", "timeout"),
            ("redis", "cancel"),
            ("mongodb", "cancel"),
        )
        for provider, stop_kind in cases:
            with self.subTest(provider=provider, stop_kind=stop_kind):
                context = _context(provider, f"post-profile-{stop_kind}-{provider}")
                call_id = f"post-profile-call-{context.run_id}"
                profile_host = _BlockingPostReadProfile(context)
                lease = _FakeLease(provider)
                cancellation = CancellationToken()
                model = _SequenceModel([self._tool_call(provider, call_id)])
                runtime = self._runtime(
                    context,
                    model,
                    factory=lambda **_kwargs: lease,
                    host=profile_host,
                    cancellation=cancellation,
                    duration=0.08 if stop_kind == "timeout" else 3.0,
                )
                release_calls = []
                original_release = runtime.registry.release_run

                def count_release(target):
                    release_calls.append(target)
                    return original_release(target)

                runtime.registry.release_run = count_release
                run_result = []
                run_done = threading.Event()

                def run():
                    try:
                        run_result.append(runtime.run("read one synthetic item"))
                    finally:
                        run_done.set()

                run_thread = threading.Thread(target=run, name=f"post-profile-run-{context.run_id}")
                try:
                    run_thread.start()
                    self.assertTrue(profile_host.post_read_entered.wait(2.0), "post-read host check did not block")
                    if stop_kind == "cancel":
                        cancellation.cancel("synthetic explicit cancellation")
                    self.assertTrue(run_done.wait(2.0), "logical run waited for the blocked host check")
                    result = run_result[0]
                    expected_code = "TIMEOUT" if stop_kind == "timeout" else "CANCELLED"
                    expected_status = AgentStatus.TIMEOUT if stop_kind == "timeout" else AgentStatus.CANCELLED
                    self.assertEqual(result.status, expected_status)
                    self.assertEqual(len(result.tool_results), 1)
                    self.assertEqual(result.tool_results[0].code, expected_code)
                    self.assertEqual(result.tool_results[0].query_id, call_id)
                    self.assertEqual(len(model.requests), 1)
                    self.assertEqual(len(release_calls), 1, "logical run release was not exactly once")
                    self.assertEqual(release_calls[0], context)
                    paired = [message for message in result.messages if message.get("role") == "tool" and message.get("tool_call_id") == call_id]
                    self.assertEqual(len(paired), 1)

                    tracker, key, owner = self._owner_future(runtime, call_id)
                    future = owner.future
                    self.assertIs(type(future), Future)
                    self.assertFalse(future.done())
                    self.assertTrue(owner.released)
                    self.assertEqual(runtime.registry.query_owner_state(context), "pending")
                    self.assertEqual(lease.close_count, 0)
                    retired = self._watch_retirement(future)

                    profile_host.post_read_release.set()
                    self.assertTrue(retired.wait(2.0), "stopped operation did not complete owner-thread cleanup")
                    self.assertTrue(future.done())
                    physical_result = future.result(timeout=1.0)
                    self.assertEqual(physical_result.code, expected_code)
                    self.assertEqual(lease.close_count, 1)
                    self.assertEqual(lease.close_threads[0], profile_host.threads[-1])
                    self.assertNotIn(future, runtime._nosql_worker._active)
                    self.assertNotIn(key, tracker._owners)
                    self.assertEqual(len(model.requests), 1)
                finally:
                    profile_host.post_read_release.set()
                    if run_thread.is_alive():
                        run_thread.join(2.0)
                    runtime.close()

    def test_settled_result_wins_later_stop_without_releasing_pending_owner(self) -> None:
        cases = (
            ("redis", "deadline"),
            ("mongodb", "deadline"),
            ("redis", "cancel"),
            ("mongodb", "cancel"),
        )
        for provider, stop_kind in cases:
            with self.subTest(provider=provider, stop_kind=stop_kind):
                context = _context(provider, f"settled-{stop_kind}-{provider}")
                call_id = f"settled-call-{context.run_id}"
                lease = _FakeLease(provider)
                cancellation = CancellationToken()
                model = _SequenceModel([self._tool_call(provider, call_id)])
                runtime = self._runtime(
                    context,
                    model,
                    factory=lambda **_kwargs: lease,
                    cancellation=cancellation,
                    duration=0.10 if stop_kind == "deadline" else 3.0,
                )
                worker = runtime._nosql_worker
                self.assertIsNotNone(worker)
                tracker = runtime.query_owner_tracker
                original_register = tracker.register_query_owner
                owner_registered = threading.Event()

                def observe_owner_registration(*args, **kwargs):
                    registered = original_register(*args, **kwargs)
                    if len(args) > 1 and args[1] == call_id:
                        owner_registered.set()
                    return registered

                tracker.register_query_owner = observe_owner_registration
                selected_entered = threading.Event()
                allow_future_done = threading.Event()
                original_finish = worker._finish_operation

                def hold_after_selection(operation, result, token, deadline):
                    selected = original_finish(operation, result, token, deadline)
                    if selected.ok:
                        selected_entered.set()
                        if not allow_future_done.wait(3.0):
                            raise AssertionError("test did not release settled operation")
                    return selected

                worker._finish_operation = hold_after_selection
                worker_results = []
                original_execute = worker.execute

                def capture_worker_result(*args, **kwargs):
                    selected = original_execute(*args, **kwargs)
                    worker_results.append(selected)
                    return selected

                worker.execute = capture_worker_result
                release_calls = []
                original_release = runtime.registry.release_run

                def count_release(target):
                    release_calls.append(target)
                    return original_release(target)

                runtime.registry.release_run = count_release
                run_result = []
                run_done = threading.Event()

                def run():
                    try:
                        run_result.append(runtime.run("observe settled synthetic read"))
                    finally:
                        run_done.set()

                run_thread = threading.Thread(target=run, name=f"settled-run-{context.run_id}")
                try:
                    run_thread.start()
                    self.assertTrue(selected_entered.wait(2.0), "NoSQL result did not reach the settled boundary")
                    self.assertTrue(owner_registered.wait(2.0), "physical Future was not registered")
                    tracker, key, owner = self._owner_future(runtime, call_id)
                    future = owner.future
                    self.assertIs(
                        type(future),
                        Future,
                        f"owner_state={owner.state}, callback={owner.callback_registered}, "
                        f"active={future in worker._active}, registry_state={runtime.registry.query_owner_state(context)}",
                    )
                    self.assertFalse(future.done(), "settled result incorrectly implied physical completion")
                    self.assertEqual(runtime.registry.query_owner_state(context), "pending")

                    if stop_kind == "cancel":
                        cancellation.cancel("synthetic cancel after result selection")
                    self.assertTrue(run_done.wait(2.0), "logical run waited for the held Future")
                    result = run_result[0]
                    expected_status = AgentStatus.TIMEOUT if stop_kind == "deadline" else AgentStatus.CANCELLED
                    self.assertEqual(result.status, expected_status)
                    self.assertEqual(len(model.requests), 1, "settled owner allowed a later model request")
                    self.assertEqual(len(worker_results), 1)
                    self.assertTrue(worker_results[0].ok, "worker replaced its selected success with a fallback failure")
                    self.assertEqual(worker_results[0].code, "OK")
                    self.assertEqual(worker_results[0].query_id, call_id)
                    self.assertEqual(len(release_calls), 1, "logical run release was not exactly once")
                    self.assertEqual(release_calls[0], context)

                    call_messages = [
                        message for message in result.messages
                        if message.get("role") == "assistant"
                        and any(item.get("id") == call_id for item in message.get("tool_calls", []))
                    ]
                    tool_messages = [
                        message for message in result.messages
                        if message.get("role") == "tool" and message.get("tool_call_id") == call_id
                    ]
                    self.assertEqual(len(call_messages), 1)
                    self.assertEqual(len(tool_messages), 1, "terminal safe result did not pair the selected call")
                    safe_payload = json.loads(tool_messages[0]["content"])
                    self.assertIs(safe_payload.get("ok"), False)
                    self.assertIn(safe_payload.get("code"), {"TIMEOUT", "CANCELLED"})
                    self.assertNotIn(f"cursor-{provider}", json.dumps(safe_payload, sort_keys=True))
                    self.assertTrue(owner.released)
                    self.assertIs(owner.future, future)
                    self.assertEqual(runtime.registry.query_owner_state(context), "pending")

                    retired = self._watch_retirement(future)
                    allow_future_done.set()
                    self.assertTrue(retired.wait(2.0), "settled operation did not physically retire")
                    self.assertTrue(future.done())
                    physical_result = future.result(timeout=1.0)
                    self.assertTrue(physical_result.ok)
                    self.assertEqual(physical_result.code, "OK")
                    self.assertEqual(physical_result.query_id, call_id)
                    self.assertNotIn(future, worker._active)
                    self.assertNotIn(key, tracker._owners)
                finally:
                    allow_future_done.set()
                    tracker.register_query_owner = original_register
                    if run_thread.is_alive():
                        run_thread.join(2.0)
                    runtime.close()

    def test_auxiliary_future_callback_faults_keep_exact_owner_and_active_cleanup(self) -> None:
        cases = (("redis", "raise"), ("mongodb", "noop"))
        for provider, callback_mode in cases:
            with self.subTest(provider=provider, callback_mode=callback_mode):
                context = _context(provider, f"aux-callback-{callback_mode}-{provider}")
                call_id = f"aux-call-{context.run_id}"
                execute_entered = threading.Event()
                execute_release = threading.Event()
                lease = _FakeLease(
                    provider,
                    execute_entered=execute_entered,
                    execute_release=execute_release,
                )
                model = _SequenceModel([self._tool_call(provider, call_id)])
                runtime = self._runtime(context, model, factory=lambda **_kwargs: lease)
                pool = runtime._nosql_worker._pool
                original_submit = pool.submit
                submitted: list[Future] = []

                def fault_instance_callback(function, *args, **kwargs):
                    future = original_submit(function, *args, **kwargs)
                    if function == runtime._nosql_worker._run:
                        if callback_mode == "raise":
                            def broken_callback(_callback, *_callback_args, **_callback_kwargs):
                                raise RuntimeError("synthetic auxiliary callback fault")
                        else:
                            def broken_callback(_callback, *_callback_args, **_callback_kwargs):
                                return None
                        future.add_done_callback = broken_callback
                        submitted.append(future)
                    return future

                pool.submit = fault_instance_callback
                release_calls = []
                original_release = runtime.registry.release_run

                def count_release(target):
                    release_calls.append(target)
                    return original_release(target)

                runtime.registry.release_run = count_release
                run_result = []
                run_done = threading.Event()

                def run():
                    try:
                        run_result.append(runtime.run("exercise synthetic callback fault"))
                    finally:
                        run_done.set()

                run_thread = threading.Thread(target=run, name=f"aux-callback-run-{context.run_id}")
                try:
                    run_thread.start()
                    self.assertTrue(execute_entered.wait(2.0), "actual submitted Future did not enter the fake read")
                    self.assertTrue(run_done.wait(2.0), "faulted auxiliary callback blocked the logical run")
                    result = run_result[0]
                    self.assertEqual(len(model.requests), 1)
                    self.assertEqual(len(result.tool_results), 1)
                    self.assertEqual(result.tool_results[0].code, "QUERY_FAILED")
                    self.assertEqual(result.tool_results[0].query_id, call_id)
                    self.assertEqual(len(release_calls), 1, "logical run release was not exactly once")
                    self.assertEqual(release_calls[0], context)
                    self.assertEqual(len(submitted), 1)

                    tracker, key, owner = self._owner_future(runtime, call_id)
                    future = submitted[0]
                    self.assertIs(type(future), Future)
                    self.assertIs(owner.future, future)
                    self.assertEqual(owner.state, "failed", "altered callback protocol must remain unverified")
                    self.assertFalse(owner.callback_registered)
                    self.assertTrue(owner.released)
                    self.assertFalse(future.done())
                    self.assertIsNotNone(runtime._nosql_worker._active.get(future))
                    retired = threading.Event()
                    _TRUSTED_FUTURE_ADD_DONE_CALLBACK(future, lambda _done: retired.set())

                    execute_release.set()
                    self.assertTrue(retired.wait(2.0), "native bookkeeping callback did not retire the actual Future")
                    self.assertTrue(future.done())
                    self.assertEqual(future.result(timeout=1.0).code, "QUERY_FAILED")
                    self.assertEqual(lease.close_count, 1)
                    self.assertEqual(lease.close_threads[0], lease.execute_threads[0])
                    self.assertNotIn(future, runtime._nosql_worker._active)
                    self.assertNotIn(key, tracker._owners)
                    self.assertEqual(len(model.requests), 1, "unverified owner allowed model continuation")
                finally:
                    execute_release.set()
                    pool.submit = original_submit
                    if run_thread.is_alive():
                        run_thread.join(2.0)
                    runtime.close()


if __name__ == "__main__":
    unittest.main()
