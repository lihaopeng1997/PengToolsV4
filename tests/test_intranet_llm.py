# -*- coding: utf-8 -*-
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ai_harness import strip_markdown_fence
from tools.intranet_llm import (
    DEFAULT_AI_LOCAL, IntranetLlmError, _parse_sse_text, build_headers,
    canonical_base_url, host_allowed, is_enabled, normalize_ai_local,
    validate_base_url,
)


class IntranetLlmTests(unittest.TestCase):
    def test_default_disabled(self):
        cfg = normalize_ai_local({})
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['base_url'], '')
        self.assertFalse(is_enabled(cfg))
        self.assertFalse(is_enabled(DEFAULT_AI_LOCAL))

    def test_allows_private_and_loopback(self):
        self.assertTrue(host_allowed('127.0.0.1')[0])
        self.assertTrue(host_allowed('localhost')[0])
        self.assertTrue(host_allowed('10.128.23.10')[0])
        self.assertTrue(host_allowed('192.168.1.8')[0])
        self.assertTrue(host_allowed('172.16.0.2')[0])
        self.assertTrue(validate_base_url('http://10.128.1.2:8000/v1').endswith('/v1'))

    def test_rejects_public_model_hosts(self):
        self.assertFalse(host_allowed('api.openai.com')[0])
        self.assertFalse(host_allowed('api.deepseek.com')[0])
        self.assertFalse(host_allowed('8.8.8.8')[0])
        with self.assertRaises(IntranetLlmError):
            validate_base_url('https://api.openai.com/v1')
        with self.assertRaises(IntranetLlmError):
            validate_base_url('https://api.deepseek.com/v1')

    def test_requires_http_scheme(self):
        with self.assertRaises(IntranetLlmError):
            validate_base_url('ftp://10.0.0.1/v1')
        with self.assertRaises(IntranetLlmError):
            validate_base_url('')

    def test_strip_markdown_fence(self):
        self.assertEqual(strip_markdown_fence('```sql\nselect 1;\n```'), 'select 1;')
        self.assertEqual(strip_markdown_fence('select 1;'), 'select 1;')

    def test_canonical_url_strips_proxyai_chat_path(self):
        full = 'http://10.128.25.142:18002/v1/chat/completions'
        self.assertEqual(canonical_base_url(full), 'http://10.128.25.142:18002/v1')
        self.assertEqual(
            canonical_base_url('http://10.128.25.142:18002/v1/'),
            'http://10.128.25.142:18002/v1',
        )
        self.assertTrue(host_allowed('10.128.25.142')[0])

    def test_proxyai_headers_include_app_tag(self):
        headers = build_headers({'app_tag': 'proxyai', 'token': ''})
        self.assertEqual(headers['X-LLM-Application-Tag'], 'proxyai')
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Authorization', headers)

    def test_parse_sse_concatenates_delta_content(self):
        raw = (
            'data: {"choices":[{"delta":{"content":"sel"}}]}\n'
            'data: {"choices":[{"delta":{"content":"ect 1"}}]}\n'
            'data: [DONE]\n'
        )
        self.assertEqual(_parse_sse_text(raw), 'select 1')

    def test_defaults_match_proxyai_body(self):
        cfg = normalize_ai_local({})
        self.assertEqual(cfg['app_tag'], 'proxyai')
        self.assertEqual(cfg['max_tokens'], 8192)
        self.assertEqual(cfg['timeout_seconds'], 120)


if __name__ == '__main__':
    unittest.main()
