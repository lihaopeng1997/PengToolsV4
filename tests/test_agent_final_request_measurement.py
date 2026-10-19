"""Final-body boundaries using public and private synthetic content only."""
import copy
from collections.abc import Mapping
import json
import unittest

from tools.agent_core.request_budget import (
    RequestEncodingError, RequestInputLimitExceeded,
    encode_model_request, measure_model_request,
)


class FinalRequestMeasurementTests(unittest.TestCase):
    def test_exact_transport_shape_counts_all_fields_and_multibyte_continuation(self):
        body = {
            'model': 'synthetic-model', 'stream': True, 'max_tokens': 128,
            'messages': [{'role': 'system', 'content': '只读'},
                         {'role': 'assistant', 'content': '', 'reasoning_content': '合成私有字段'},
                         {'role': 'tool', 'content': 'result', 'tool_call_id': 'call-1'}],
            'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}],
            'response_format': {'type': 'json_object'}, 'thinking': {'type': 'enabled'},
        }
        original = copy.deepcopy(body)
        expected = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.assertEqual(encode_model_request(body), expected)
        self.assertGreater(len(expected), len(json.dumps(body, ensure_ascii=False)))
        receipt = measure_model_request(body, max_request_bytes=len(expected))
        self.assertEqual(receipt.input_bytes, len(expected))
        self.assertNotIn('合成私有字段', repr(receipt))
        self.assertFalse(hasattr(receipt, 'body'))
        self.assertEqual(body, original)
        with self.assertRaises(RequestInputLimitExceeded) as raised:
            measure_model_request(body, max_request_bytes=len(expected) - 1)
        self.assertEqual(str(raised.exception), 'final_request_input_bytes')
        self.assertEqual(raised.exception.input_bytes, len(expected))
        self.assertEqual(raised.exception.limit_bytes, len(expected) - 1)
        self.assertNotIn('合成私有字段', repr(raised.exception))

    def test_none_preserves_unlimited_legacy_and_invalid_limit_never_encodes_body(self):
        body = {'messages': [{'content': 'synthetic-' * 1000}]}
        self.assertEqual(measure_model_request(body, max_request_bytes=None).input_bytes,
                         len(encode_model_request(body)))
        class Unreadable(Mapping):
            def __iter__(self):
                raise AssertionError('invalid limits must be checked before content')
            def __getitem__(self, key):
                raise AssertionError('invalid limits must be checked before content')
            def __len__(self):
                return 1
        for invalid in (True, False, 0, -1, 1.5, '1024'):
            with self.subTest(invalid_type=type(invalid).__name__), self.assertRaises(ValueError):
                measure_model_request(Unreadable(), max_request_bytes=invalid)

    def test_encoding_failure_has_only_stable_code(self):
        for body in ({'content': '\ud800'}, {'content': object()}, []):
            with self.subTest(type=type(body).__name__), self.assertRaises(RequestEncodingError) as raised:
                encode_model_request(body)
            self.assertEqual(str(raised.exception), 'model_request_encoding')
            self.assertIsNone(raised.exception.__context__)


if __name__ == '__main__':
    unittest.main()
