"""Final HTTP JSON request gate boundaries with synthetic model payloads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from enum import IntEnum
import threading
import unittest
from unittest.mock import patch

from tools.agent_core import AgentModelHostAdapter, CancellationToken
from tools.agent_core.context import ContextLimits, ContextPlan, ContextPlanner, TokenEstimate
from tools.agent_core.model_adapter import ModelCancelled
from tools.agent_core.request_budget import (
    RequestInputLimitExceeded,
    encode_model_request,
    measure_model_request,
)
from tests.test_deepseek_private_reasoning import (
    _config,
    _context,
    _request,
    _text_frames,
)


class _ByteLimitEnum(IntEnum):
    VALUE = 32_000


class _ByteLimitInt(int):
    pass


class _ProbeTransport:
    def __init__(self, steps=()):
        self.steps = list(steps)
        self.bodies = []
        self._lock = threading.Lock()

    def stream(self, body, *, cancellation, deadline=None):
        with self._lock:
            self.bodies.append(dict(body))
            step = self.steps.pop(0)
        return iter(step)


class AgentFinalRequestGateTests(unittest.TestCase):
    def _adapter(self, transport=None, *, retry_count=2):
        return AgentModelHostAdapter(
            'deepseek-profile',
            config=_config(),
            transport=transport,
            retry_count=retry_count,
        )

    def test_exact_fit_sends_and_one_byte_over_blocks_before_transport(self):
        exact_transport = _ProbeTransport([_text_frames('exact')])
        exact_adapter = self._adapter(exact_transport)
        exact_request = _request(_context('exact-fit'))
        exact_body = exact_adapter._adapter._request_body(exact_request)
        expected = encode_model_request(exact_body)
        receipt = measure_model_request(exact_body, max_request_bytes=len(expected))
        self.assertEqual(receipt.input_bytes, len(expected))
        exact_request = replace(exact_request, max_request_bytes=len(expected))
        exact_adapter.complete_turn(
            exact_request, cancellation=CancellationToken(), on_delta=lambda _delta: None,
        )
        self.assertEqual(len(exact_transport.bodies), 1)

        over_transport = _ProbeTransport()
        over_adapter = self._adapter(over_transport, retry_count=2)
        over_request = _request(_context('one-byte-over'))
        over_body = over_adapter._adapter._request_body(over_request)
        over_size = len(encode_model_request(over_body))
        over_request = replace(over_request, max_request_bytes=over_size - 1)
        with self.assertRaises(RequestInputLimitExceeded) as raised:
            over_adapter.complete_turn(
                over_request, cancellation=CancellationToken(), on_delta=lambda _delta: None,
            )
        self.assertEqual(raised.exception.input_bytes, over_size)
        self.assertEqual(raised.exception.limit_bytes, over_size - 1)
        self.assertEqual(str(raised.exception), 'final_request_input_bytes')
        self.assertEqual(over_transport.bodies, [])
        self.assertEqual(over_adapter._adapter._private_continuations, {})
        self.assertEqual(over_adapter._adapter._private_continuation_bytes, {})

    def test_request_contract_rejects_malformed_caps_before_adapter_work(self):
        request = _request(_context('bad-cap'))
        for invalid in (True, False, 0, -1, 1.5, '1024'):
            with self.subTest(invalid=repr(invalid)), self.assertRaises((TypeError, ValueError)):
                replace(request, max_request_bytes=invalid)

    def test_plan_and_request_require_exact_positive_int_or_none_for_byte_cap(self):
        request = _request(_context('strict-cap'))
        plan = ContextPlan(
            status='ready', messages=[], estimate=TokenEstimate(0, 'test'),
            messages_kept=0, messages_dropped=0, reason='test',
            window_known=False, output_reserve_tokens=0,
        )
        invalid = (
            True, False, 0, -1, 1.5, '1024',
            _ByteLimitEnum.VALUE, _ByteLimitInt(32_000),
        )
        for value in invalid:
            with self.subTest(path='plan', value=repr(value)), self.assertRaises(ValueError):
                replace(plan, max_request_bytes=value)
            with self.subTest(path='request', value=repr(value)), self.assertRaises(ValueError):
                replace(request, max_request_bytes=value)

        for value in (None, 32_000):
            with self.subTest(path='plan-valid', value=repr(value)):
                self.assertEqual(replace(plan, max_request_bytes=value).max_request_bytes, value)
            with self.subTest(path='request-valid', value=repr(value)):
                self.assertEqual(replace(request, max_request_bytes=value).max_request_bytes, value)

    def test_default_planner_normalizes_legacy_int_subclass_limits_for_byte_cap(self):
        for old_limit in (_ByteLimitEnum.VALUE, _ByteLimitInt(32_000)):
            with self.subTest(limit_type=type(old_limit).__name__):
                planner = ContextPlanner(ContextLimits(input_token_limit=old_limit))
                plan = planner.plan(
                    system_prompt='system',
                    messages=[{'role': 'user', 'content': 'hello'}],
                    tools=[],
                )
                self.assertEqual(plan.status, 'ready')
                self.assertEqual(plan.max_request_bytes, int(old_limit))
                self.assertIs(type(plan.max_request_bytes), int)
                request = replace(
                    _request(_context('legacy-int-subclass')),
                    max_request_bytes=plan.max_request_bytes,
                )
                self.assertEqual(request.max_request_bytes, int(old_limit))

    def test_oversize_gate_precedes_default_transport_headers_and_network(self):
        adapter = AgentModelHostAdapter('deepseek-profile', config=_config(), retry_count=2)
        request = _request(_context('no-header'))
        body = adapter._adapter._request_body(request)
        request = replace(request, max_request_bytes=len(encode_model_request(body)) - 1)
        with patch('tools.intranet_llm.build_headers') as build_headers, \
                patch('socket.create_connection') as connect:
            with self.assertRaises(RequestInputLimitExceeded):
                adapter.complete_turn(
                    request, cancellation=CancellationToken(), on_delta=lambda _delta: None,
                )
        build_headers.assert_not_called()
        connect.assert_not_called()

    def test_cancel_during_local_measurement_stops_before_transport(self):
        transport = _ProbeTransport([_text_frames('must not send')])
        adapter = self._adapter(transport)
        token = CancellationToken()
        request = _request(_context('cancel-measure'))
        body = adapter._adapter._request_body(request)
        request = replace(request, max_request_bytes=len(encode_model_request(body)))

        import tools.agent_core.model_adapter as model_adapter_module
        measure = model_adapter_module.measure_model_request

        def cancel_after_measure(body, *, max_request_bytes):
            result = measure(body, max_request_bytes=max_request_bytes)
            token.cancel('synthetic cancel during byte measurement')
            return result

        with patch.object(model_adapter_module, 'measure_model_request', side_effect=cancel_after_measure):
            with self.assertRaises(ModelCancelled):
                adapter.complete_turn(request, cancellation=token, on_delta=lambda _delta: None)
        self.assertEqual(transport.bodies, [])

    def test_concurrent_requests_keep_caps_scoped_to_each_request(self):
        transport = _ProbeTransport([_text_frames('allowed')])
        adapter = self._adapter(transport)
        request_a = _request(_context('cap-a'))
        request_b = _request(_context('cap-b'))
        request_b = replace(request_b, system_prompt=request_b.system_prompt + ' extra')
        body_a = adapter._adapter._request_body(request_a)
        body_b = adapter._adapter._request_body(request_b)
        size_a = len(encode_model_request(body_a))
        size_b = len(encode_model_request(body_b))
        request_a = replace(request_a, max_request_bytes=size_a - 1)
        request_b = replace(request_b, max_request_bytes=size_b)

        import tools.agent_core.model_adapter as model_adapter_module
        measure = model_adapter_module.measure_model_request
        rendezvous = threading.Barrier(2)

        def synchronized_measure(body, *, max_request_bytes):
            rendezvous.wait(timeout=3)
            return measure(body, max_request_bytes=max_request_bytes)

        def send(request):
            try:
                adapter.complete_turn(
                    request, cancellation=CancellationToken(), on_delta=lambda _delta: None,
                )
                return 'sent'
            except RequestInputLimitExceeded:
                return 'blocked'

        with patch.object(model_adapter_module, 'measure_model_request', side_effect=synchronized_measure):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(send, request) for request in (request_a, request_b)]
                results = [future.result(timeout=5) for future in futures]

        self.assertEqual(sorted(results), ['blocked', 'sent'])
        self.assertEqual(len(transport.bodies), 1)
        self.assertEqual(transport.bodies[0], body_b)


if __name__ == '__main__':
    unittest.main()
