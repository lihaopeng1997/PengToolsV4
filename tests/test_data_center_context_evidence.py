"""Unit coverage for the bounded DC evidence ledger and planner wrapper."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import json
import unittest

from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.data_center.context_evidence import (
    DataCenterEvidencePlanner,
    EvidenceCounters,
    EvidenceEpoch,
    EvidenceLedger,
    EvidenceSnapshot,
    MAX_CERTIFICATE_BYTES,
    MAX_CERTIFICATES,
    MAX_LEDGER_BYTES,
    _full_binding,
    _strict_json,
    _validate_record,
)
from tools.data_center.contracts import RunContext, ToolResult
from tools.data_center.tool_registry import DataCenterToolRegistry, QueryOwnerTracker


def _context(**changes) -> RunContext:
    values = {
        "run_id": "evidence-run",
        "tab_id": "evidence-tab",
        "model_config_id": "synthetic-model-config",
        "connection_id": "synthetic-connection",
        "database": "synthetic-db",
        "schema_allowlist": ("synthetic-schema",),
        "profile_revision": "synthetic-revision",
        "intent": "query",
        "policy_version": "synthetic-policy",
    }
    values.update(changes)
    return RunContext(**values)


def _scope(context: RunContext, **changes):
    value = {
        "connection_id": context.connection_id,
        "database": context.database,
        "profile_revision": context.profile_revision,
        "schema_allowlist": list(context.schema_allowlist),
    }
    value.update(changes)
    return value


def _record(
    context: RunContext,
    call_id: str,
    query_id: str,
    data,
    *,
    name: str = "query_readonly",
    scope=None,
):
    result = ToolResult(
        ok=True,
        code="OK",
        data=data,
        query_id=query_id,
        scope=scope if scope is not None else _scope(context),
        truncated=False,
        limits={"projection": "synthetic", "returned_rows": 1},
    )
    return {"call_id": call_id, "name": name, "message": result.as_model_message(call_id)}


def _exact_certificate_record(context: RunContext, call_id: str, query_id: str, target: int, sequence: int):
    binding = _full_binding(context, "mysql", "mysql")
    low, high = 0, target + 2
    best = None
    while low <= high:
        middle = (low + high) // 2
        record = _record(context, call_id, query_id, "x" * middle)
        certificate = _validate_record(
            record,
            epoch=EvidenceEpoch(1),
            binding=binding,
            sequence=sequence,
        )
        if certificate is None:
            raise AssertionError("synthetic certificate failed validation")
        size = len(_strict_json(certificate.as_dict()))
        if size == target:
            return record, certificate
        if size < target:
            best = (record, certificate)
            low = middle + 1
        else:
            high = middle - 1
    if best is None or len(_strict_json(best[1].as_dict())) != target:
        raise AssertionError(f"could not construct exact certificate size {target}")
    return best


class _SnapshotRegistry:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.reads = 0
        self.omission_counts = []

    def snapshot_evidence(self, context, epoch):  # noqa: ARG002
        self.reads += 1
        return self.snapshot if epoch.value == self.snapshot.epoch else None

    def record_evidence_omissions(self, context, epoch, count):  # noqa: ARG002
        self.omission_counts.append(count)
        return True


class DataCenterContextEvidenceTests(unittest.TestCase):
    def test_ledger_accepts_exact_certificate_and_rejects_one_byte_over_without_eviction(self) -> None:
        context = _context()
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        record, certificate = _exact_certificate_record(
            context, "exact-call", "exact-query", MAX_CERTIFICATE_BYTES, 1
        )
        self.assertEqual(len(_strict_json(certificate.as_dict())), MAX_CERTIFICATE_BYTES)
        self.assertTrue(ledger.commit(epoch, context, [record]))

        # Keep all identity lengths fixed and tune the payload to exactly one
        # byte above the per-certificate cap.
        base_record, base_certificate = _exact_certificate_record(
            context, "over-call", "over-query", MAX_CERTIFICATE_BYTES, 2
        )
        base_payload = json.loads(base_record["message"]["content"])
        exact_data_length = len(base_payload["data"])
        over_record = _record(context, "over-call", "over-query", "x" * (exact_data_length + 1))
        self.assertGreater(
            len(_strict_json(_validate_record(
                over_record,
                epoch=epoch,
                binding=_full_binding(context, "mysql", "mysql"),
                sequence=2,
            ).as_dict())),
            MAX_CERTIFICATE_BYTES,
        )
        self.assertTrue(ledger.commit(epoch, context, [over_record]))
        snapshot = ledger.snapshot(epoch, context)
        self.assertIsNotNone(snapshot)
        self.assertEqual([item.call_id for item in snapshot.certificates], ["exact-call"])
        self.assertEqual(snapshot.counters.oversized, 1)

    def test_full_ledger_can_reach_exact_byte_cap(self) -> None:
        context = _context()
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        for index in range(3):
            call_id = f"call-{index:02d}"
            query_id = f"query-{index:02d}"
            record, _certificate = _exact_certificate_record(
                context, call_id, query_id, MAX_CERTIFICATE_BYTES, index + 1
            )
            self.assertTrue(ledger.commit(epoch, context, [record]))
        session = ledger._sessions[epoch.value]
        call_id, query_id = "call-03", "query-03"
        binding = _full_binding(context, "mysql", "mysql")
        low, high = 0, MAX_CERTIFICATE_BYTES
        exact_record = None
        while low <= high:
            middle = (low + high) // 2
            record = _record(context, call_id, query_id, "x" * middle)
            certificate = _validate_record(
                record,
                epoch=epoch,
                binding=binding,
                sequence=4,
            )
            size = len(session.encoded(session.certificates + [certificate], session.counters))
            if size == MAX_LEDGER_BYTES:
                exact_record = record
                break
            if size < MAX_LEDGER_BYTES:
                low = middle + 1
            else:
                high = middle - 1
        self.assertIsNotNone(exact_record, "synthetic ledger should have an exact 256 KiB boundary")
        self.assertTrue(ledger.commit(epoch, context, [exact_record]))
        self.assertEqual(len(session.encoded()), MAX_LEDGER_BYTES)
        self.assertEqual(len(ledger.snapshot(epoch, context).certificates), 4)

    def test_fifo_capacity_duplicate_identity_and_stale_epoch(self) -> None:
        context = _context()
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        records = [
            _record(context, f"call-{index:02d}", f"query-{index:02d}", {"value": index})
            for index in range(MAX_CERTIFICATES + 1)
        ]
        self.assertTrue(ledger.commit(epoch, context, records))
        snapshot = ledger.snapshot(epoch, context)
        self.assertEqual(len(snapshot.certificates), MAX_CERTIFICATES)
        self.assertEqual(snapshot.certificates[0].call_id, "call-01")
        self.assertEqual(snapshot.counters.evicted, 1)

        duplicate = _record(context, "call-01", "another-query", {"value": "duplicate"})
        self.assertFalse(ledger.commit(epoch, context, [duplicate]))
        self.assertIsNone(ledger.snapshot(epoch, context))
        self.assertFalse(ledger.commit(EvidenceEpoch(epoch.value + 100), context, records[:1]))

    def test_binding_checks_all_context_fields_provider_mode_and_epoch(self) -> None:
        context = _context()
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        self.assertTrue(ledger.commit(epoch, context, [_record(context, "c1", "q1", {"v": 1})]))
        field_changes = {
            "run_id": "other-run",
            "tab_id": "other-tab",
            "model_config_id": "other-config",
            "connection_id": "other-connection",
            "database": "other-db",
            "schema_allowlist": ("other-schema",),
            "profile_revision": "other-revision",
            "intent": "explore",
            "policy_version": "other-policy",
        }
        for name, value in field_changes.items():
            with self.subTest(field=name):
                self.assertIsNone(ledger.snapshot(epoch, replace(context, **{name: value})))
        self.assertIsNone(ledger.snapshot(EvidenceEpoch(epoch.value + 1), context))

        wrong_provider = _record(
            context, "c2", "q2", {"v": 2},
            scope=_scope(context, provider="redis"),
        )
        self.assertTrue(ledger.commit(epoch, context, [wrong_provider]))
        snapshot = ledger.snapshot(epoch, context)
        self.assertEqual([item.call_id for item in snapshot.certificates], ["c1"])
        self.assertEqual(snapshot.counters.invalid, 1)

    def test_tabular_projection_may_omit_revision_but_conflicting_revision_is_rejected(self) -> None:
        context = _context()
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        tabular_scope = {
            "connection_id": context.connection_id,
            "database": context.database,
            "schema_allowlist": list(context.schema_allowlist),
        }
        self.assertTrue(ledger.commit(
            epoch,
            context,
            [_record(context, "tabular-call", "tabular-query", {"fact": "Q1"}, scope=tabular_scope)],
        ))
        bad_revision = dict(tabular_scope, profile_revision="other-revision")
        self.assertTrue(ledger.commit(
            epoch,
            context,
            [_record(context, "bad-call", "bad-query", {"fact": "bad"}, scope=bad_revision)],
        ))
        snapshot = ledger.snapshot(epoch, context)
        self.assertEqual([item.call_id for item in snapshot.certificates], ["tabular-call"])
        self.assertEqual(snapshot.counters.invalid, 1)

    def test_registry_requires_real_tracker_idle_and_release_rejects_stale_closure(self) -> None:
        context = _context()
        tracker = QueryOwnerTracker()
        registry = DataCenterToolRegistry({}, query_owner_tracker=tracker)
        epoch = registry.activate_evidence_run(context, provider="mysql", mode="mysql")
        capability = registry.begin_tool_commit(context)
        record = _record(context, "call1", "query1", {"fact": "public"})
        self.assertTrue(capability([record]))
        self.assertIsNotNone(registry.snapshot_evidence(context, epoch))

        tracker.expect_query(context, "pending-call")
        future = Future()
        future.query_id = "pending-call"
        future.session_id = context.tab_id
        future.generation = 1
        self.assertTrue(tracker.register_query_owner(
            context,
            "pending-call",
            session_id=context.tab_id,
            generation=1,
            future=future,
        ))
        self.assertIsNone(registry.snapshot_evidence(context, epoch))
        registry.release_run(context)
        self.assertFalse(capability([record]))
        future.set_result(None)
        self.assertIsNone(registry.snapshot_evidence(context, epoch))

        missing_tracker = DataCenterToolRegistry({})
        missing_epoch = missing_tracker.activate_evidence_run(context, provider="mysql", mode="mysql")
        self.assertIsNone(missing_tracker.snapshot_evidence(context, missing_epoch))
        self.assertFalse(missing_tracker.begin_tool_commit(context)([record]))

        failed_tracker = QueryOwnerTracker()
        failed_registry = DataCenterToolRegistry({}, query_owner_tracker=failed_tracker)
        failed_epoch = failed_registry.activate_evidence_run(context, provider="mysql", mode="mysql")
        failed_tracker.expect_query(context, "unregistered")
        self.assertIsNone(failed_registry.snapshot_evidence(context, failed_epoch))

    def test_dropped_query_is_a_temporary_overlay_and_canonical_history_stays_untouched(self) -> None:
        context = _context()
        q1_data = "Q1-FACT-ALPHA " + "漢🙂" * 2_000
        q1_result = _record(context, "q1-call", "q1-query", q1_data)["message"]
        q2_result = _record(context, "q2-call", "q2-query", {"fact": "Q2-NEWEST"})["message"]
        q1_assistant = {
            "role": "assistant",
            "tool_calls": [{"id": "q1-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}],
        }
        q2_assistant = {
            "role": "assistant",
            "tool_calls": [{"id": "q2-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}],
        }
        messages = [
            {"role": "user", "content": "first question"},
            q1_assistant,
            q1_result,
            q2_assistant,
            q2_result,
            {"role": "user", "content": "current question"},
        ]
        original = json.loads(json.dumps(messages))
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        self.assertTrue(ledger.commit(epoch, context, [
            _record(context, "q1-call", "q1-query", q1_data)
        ]))
        registry = _SnapshotRegistry(ledger.snapshot(epoch, context))
        base = ContextPlanner(ContextLimits(9_000, None, output_reserve_tokens=0))
        planner = DataCenterEvidencePlanner(base, registry, context)
        planner.bind_epoch(epoch)

        plan = planner.plan(system_prompt="synthetic system", messages=messages, tools=())

        self.assertEqual(plan.status, "ready")
        self.assertFalse(any(
            call.get("id") == "q1-call"
            for message in plan.messages for call in message.get("tool_calls", ())
        ))
        self.assertTrue(any(
            call.get("id") == "q2-call"
            for message in plan.messages for call in message.get("tool_calls", ())
        ))
        overlay = next(
            message for message in plan.messages
            if message.get("role") == "assistant" and "UNTRUSTED PROJECTED QUERY REFERENCE" in message.get("content", "")
        )
        self.assertIn("Q1-FACT-ALPHA", overlay["content"])
        self.assertIn("漢🙂", overlay["content"])
        self.assertLessEqual(len(_strict_json(overlay)), 8_192)
        self.assertEqual(json.loads(overlay["content"].split("\n", 1)[1])["queries"][0]["data_available"], True)
        self.assertEqual(messages, original)
        self.assertFalse(any("UNTRUSTED PROJECTED" in str(message) for message in messages))

    def test_tight_context_keeps_metadata_and_counts_data_omissions(self) -> None:
        context = _context()
        q1_data = "Q1-FACT-ALPHA " + "x" * 8_000
        q1_record = _record(context, "q1-call", "q1-query", q1_data)
        q2_result = _record(context, "q2-call", "q2-query", {"fact": "Q2-NEWEST"})["message"]
        messages = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "tool_calls": [{"id": "q1-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            q1_record["message"],
            {"role": "assistant", "tool_calls": [{"id": "q2-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            q2_result,
            {"role": "user", "content": "current question"},
        ]
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        self.assertTrue(ledger.commit(epoch, context, [q1_record]))
        planner = DataCenterEvidencePlanner(
            ContextPlanner(ContextLimits(2_000, None, output_reserve_tokens=0)),
            _SnapshotRegistry(ledger.snapshot(epoch, context)),
            context,
        )
        planner.bind_epoch(epoch)

        plan = planner.plan(system_prompt="synthetic system", messages=messages, tools=())

        self.assertEqual(plan.status, "ready")
        overlay = next(
            message for message in plan.messages
            if message.get("role") == "assistant" and "UNTRUSTED PROJECTED QUERY REFERENCE" in message.get("content", "")
        )
        query = json.loads(overlay["content"].split("\n", 1)[1])["queries"][0]
        self.assertFalse(query["data_available"])
        self.assertIsNone(query["data"])
        self.assertTrue(query["view_truncated"])
        self.assertGreater(query["view_omitted_chars"], 0)
        self.assertEqual(json.loads(overlay["content"].split("\n", 1)[1])["omissions"]["reference_records_omitted"], 0)
        self.assertTrue(any(call.get("id") == "q2-call" for message in plan.messages for call in message.get("tool_calls", ())))

    def test_uncertified_multicall_group_counts_once_per_request(self) -> None:
        context = _context()
        messages = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "tool_calls": [
                {"id": "q1a-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}},
                {"id": "q1b-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}},
            ]},
            _record(context, "q1a-call", "q1a-query", {"v": "x" * 1_000})["message"],
            _record(context, "q1b-call", "q1b-query", {"v": "x" * 1_000})["message"],
            {"role": "assistant", "tool_calls": [{"id": "q2-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            _record(context, "q2-call", "q2-query", {"v": 2})["message"],
            {"role": "user", "content": "current question"},
        ]
        snapshot = EvidenceSnapshot(
            epoch=1,
            binding=_full_binding(context, "mysql", "mysql"),
            certificates=(),
            counters=EvidenceCounters(),
        )
        registry = _SnapshotRegistry(snapshot)
        planner = DataCenterEvidencePlanner(
            ContextPlanner(ContextLimits(2_000, None, output_reserve_tokens=0)),
            registry,
            context,
        )
        planner.bind_epoch(EvidenceEpoch(1))

        plan = planner.plan(system_prompt="synthetic system", messages=messages, tools=())

        self.assertEqual(plan.status, "ready")
        self.assertFalse(any(
            call.get("id") in {"q1a-call", "q1b-call"}
            for message in plan.messages for call in message.get("tool_calls", ())
        ))
        self.assertTrue(any(call.get("id") == "q2-call" for message in plan.messages for call in message.get("tool_calls", ())))
        self.assertEqual(registry.omission_counts, [1])

    def test_fixed_point_replans_when_only_uncertified_omissions_change(self) -> None:
        context = _context()
        q1_record = _record(context, "q1-call", "q1-query", {"fact": "Q1-FACT-" + "x" * 1_800})
        uncertified_result = _record(
            context, "unverified-call", "unverified-query", {"fact": "U-FACT-" + "y" * 500}
        )["message"]
        q2_result = _record(context, "q2-call", "q2-query", {"fact": "Q2-NEWEST"})["message"]
        messages = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "tool_calls": [{"id": "q1-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            q1_record["message"],
            {"role": "assistant", "tool_calls": [{"id": "unverified-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            uncertified_result,
            {"role": "assistant", "tool_calls": [{"id": "q2-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            q2_result,
            {"role": "user", "content": "current question"},
        ]
        original = json.loads(json.dumps(messages))
        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        self.assertTrue(ledger.commit(epoch, context, [q1_record]))
        registry = _SnapshotRegistry(ledger.snapshot(epoch, context))
        delegate = ContextPlanner(ContextLimits(2_500, None, output_reserve_tokens=0))
        base_plan = delegate.plan(system_prompt="s", messages=messages, tools=())
        base_call_ids = {call["id"] for message in base_plan.messages for call in message.get("tool_calls", ())}
        self.assertEqual(base_plan.status, "ready")
        self.assertNotIn("q1-call", base_call_ids)
        self.assertIn("unverified-call", base_call_ids)
        self.assertIn("q2-call", base_call_ids)

        planner = DataCenterEvidencePlanner(delegate, registry, context)
        planner.bind_epoch(epoch)
        plan = planner.plan(system_prompt="s", messages=messages, tools=())

        self.assertEqual(plan.status, "ready")
        call_ids = {call["id"] for message in plan.messages for call in message.get("tool_calls", ())}
        self.assertNotIn("q1-call", call_ids)
        self.assertNotIn("unverified-call", call_ids)
        self.assertIn("q2-call", call_ids)
        overlay = next(
            message for message in plan.messages
            if message.get("role") == "assistant" and "UNTRUSTED PROJECTED QUERY REFERENCE" in message.get("content", "")
        )
        payload = json.loads(overlay["content"].split("\n", 1)[1])
        self.assertEqual(payload["omissions"]["uncertified_groups"], 1)
        self.assertEqual(registry.omission_counts, [1])
        self.assertEqual(messages, original)
        self.assertTrue(all(any(item == anchor for item in plan.messages) for anchor in (messages[0], messages[-1])))

    def test_missing_or_conflicting_certificate_never_restores_content(self) -> None:
        context = _context()
        q1_message = _record(context, "q1-call", "q1-query", {"fact": "Q1-SECRET-FOR-TEST"})["message"]
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "tool_calls": [{"id": "q1-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            q1_message,
            {"role": "assistant", "tool_calls": [{"id": "q2-call", "type": "function", "function": {"name": "query_readonly", "arguments": "{}"}}]},
            _record(context, "q2-call", "q2-query", {"v": 2})["message"],
            {"role": "user", "content": "current"},
        ]
        planner = DataCenterEvidencePlanner(
            ContextPlanner(ContextLimits(1_500, None, output_reserve_tokens=0)),
            _SnapshotRegistry(EvidenceSnapshot(1, _full_binding(context, "mysql", "mysql"), (), EvidenceCounters())),
            context,
        )
        planner.bind_epoch(EvidenceEpoch(1))
        missing = planner.plan(system_prompt="s", messages=messages, tools=())
        self.assertEqual(missing.status, "ready")
        self.assertFalse(any("UNTRUSTED PROJECTED" in str(message) for message in missing.messages))

        ledger = EvidenceLedger()
        epoch = ledger.activate(context, "mysql", "mysql")
        original_record = _record(context, "q1-call", "q1-query", {"fact": "Q1-SECRET-FOR-TEST"})
        self.assertTrue(ledger.commit(epoch, context, [original_record]))
        changed_messages = [dict(item) for item in messages]
        changed_messages[2] = _record(context, "q1-call", "q1-query", {"fact": "forged"})["message"]
        conflict_planner = DataCenterEvidencePlanner(
            ContextPlanner(ContextLimits(1_500, None, output_reserve_tokens=0)),
            _SnapshotRegistry(ledger.snapshot(epoch, context)),
            context,
        )
        conflict_planner.bind_epoch(epoch)
        conflict = conflict_planner.plan(system_prompt="s", messages=changed_messages, tools=())
        self.assertEqual(conflict.status, "ready")
        self.assertFalse(any("UNTRUSTED PROJECTED" in str(message) for message in conflict.messages))
        self.assertNotIn("Q1-SECRET-FOR-TEST", str(conflict.messages))


if __name__ == "__main__":
    unittest.main()
