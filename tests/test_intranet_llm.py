# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ai_harness import strip_markdown_fence
from tools.intranet_llm import (
    DEFAULT_AI_LOCAL, IntranetLlmError, _parse_sse_text, build_headers,
    canonical_base_url, host_allowed, is_enabled, load_model_catalog,
    normalize_agent_capability, normalize_agent_reasoning_fields,
    normalize_ai_local, normalize_catalog, resolve_private_host,
    save_model_catalog, validate_base_url, chat_completions,
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

    def test_canonical_url_strips_chat_completions_path(self):
        full = 'http://10.128.25.142:18002/v1/chat/completions'
        self.assertEqual(canonical_base_url(full), 'http://10.128.25.142:18002/v1')
        self.assertEqual(
            canonical_base_url('http://10.128.25.142:18002/v1/'),
            'http://10.128.25.142:18002/v1',
        )
        self.assertTrue(host_allowed('10.128.25.142')[0])

    def test_headers_omit_app_tag_when_empty(self):
        headers = build_headers({'app_tag': '', 'token': ''})
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('X-LLM-Application-Tag', headers)
        tagged = build_headers({'app_tag': 'pengtools', 'token': ''})
        self.assertEqual(tagged['X-LLM-Application-Tag'], 'pengtools')

    def test_parse_sse_concatenates_delta_content(self):
        raw = (
            'data: {"choices":[{"delta":{"content":"sel"}}]}\n'
            'data: {"choices":[{"delta":{"content":"ect 1"}}]}\n'
            'data: [DONE]\n'
        )
        self.assertEqual(_parse_sse_text(raw), 'select 1')

    def test_defaults_match_openai_compat_body(self):
        cfg = normalize_ai_local({})
        self.assertEqual(cfg['app_tag'], '')
        self.assertEqual(cfg['max_tokens'], 8192)
        self.assertEqual(cfg['timeout_seconds'], 120)

    def test_agent_metadata_defaults_and_closed_capability(self):
        cfg = normalize_ai_local({})
        self.assertEqual(cfg['agent_capability'], 'unknown')
        self.assertEqual(cfg['agent_reasoning_fields'], [])
        self.assertEqual(cfg['agent_deadline_seconds'], 60.0)
        self.assertEqual(cfg['agent_max_reasoning_bytes'], 64 * 1024)
        self.assertEqual(cfg['agent_max_text_bytes'], 128 * 1024)
        self.assertEqual(cfg['agent_max_tool_argument_bytes'], 32 * 1024)
        self.assertEqual(cfg['agent_max_raw_buffer_bytes'], 2 * 1024 * 1024)
        self.assertEqual(normalize_agent_capability('unprobed'), 'unknown')
        self.assertEqual(normalize_agent_capability('native_tools'), 'native_tools')

    def test_agent_reasoning_fields_only_keep_explicit_names(self):
        self.assertEqual(
            normalize_agent_reasoning_fields([
                ' reasoning_content ', 'reasoning_content', 'vendor.summary',
                '*', {'field': 'reasoning'}, 7, 'bad field',
            ]),
            ['reasoning_content', 'vendor.summary'],
        )
        self.assertEqual(normalize_agent_reasoning_fields({'field': 'reasoning'}), [])
        self.assertEqual(normalize_agent_reasoning_fields('reasoning_summary'), ['reasoning_summary'])
        self.assertEqual(
            normalize_agent_reasoning_fields('reasoning_content, reasoning_summary'),
            ['reasoning_content', 'reasoning_summary'],
        )

    def test_agent_metadata_survives_legacy_migration_and_catalog_roundtrip(self):
        legacy = normalize_catalog({
            'enabled': True,
            'base_url': 'http://10.0.0.8:8000/v1',
            'model': 'qwen3.6',
            'token': 'enc:old',
            'agent_capability': 'native_tools',
            'agent_reasoning_fields': ['reasoning_summary'],
            'agent_deadline_seconds': 45,
            'agent_max_reasoning_bytes': 4096,
            'agent_max_text_bytes': 8192,
            'agent_max_tool_argument_bytes': 2048,
            'agent_max_raw_buffer_bytes': 65536,
        })
        item = legacy['items'][0]
        self.assertEqual(item['agent_capability'], 'native_tools')
        self.assertEqual(item['agent_reasoning_fields'], ['reasoning_summary'])
        self.assertEqual(item['agent_deadline_seconds'], 45)
        self.assertEqual(item['agent_max_raw_buffer_bytes'], 65536)
        self.assertEqual(item['token'], 'enc:old')

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'ai_local.json')
            from unittest.mock import patch
            with patch('tools.intranet_llm.AI_LOCAL_FILE', path):
                save_model_catalog(legacy)
                loaded = load_model_catalog()
        loaded_item = loaded['items'][0]
        self.assertEqual(loaded_item['agent_capability'], 'native_tools')
        self.assertEqual(loaded_item['agent_reasoning_fields'], ['reasoning_summary'])
        self.assertEqual(loaded_item['agent_max_tool_argument_bytes'], 2048)
        self.assertEqual(loaded_item['token'], 'enc:old')

    def test_legacy_chat_completions_ignores_agent_metadata(self):
        cfg = normalize_ai_local({
            'enabled': True,
            'base_url': 'http://127.0.0.1:8000/v1',
            'model': 'qwen3.6',
            'agent_capability': 'native_tools',
            'agent_reasoning_fields': ['reasoning_summary'],
        })
        with patch(
            'tools.intranet_llm._request',
            return_value={'choices': [{'message': {'content': 'legacy reply'}}]},
        ) as request:
            self.assertEqual(chat_completions([{'role': 'user', 'content': 'ping'}], cfg=cfg), 'legacy reply')
        body = request.call_args.args[3]
        self.assertEqual(body['model'], 'qwen3.6')
        self.assertEqual(body['stream'], True)
        self.assertNotIn('tools', body)
        self.assertNotIn('agent_capability', body)
        self.assertNotIn('agent_reasoning_fields', body)

    def test_legacy_single_config_migrates_to_catalog(self):
        catalog = normalize_catalog({
            'enabled': True,
            'base_url': 'http://10.0.0.8:8000/v1',
            'model': 'qwen3.6',
            'token': 'enc:old',
        })
        self.assertEqual(len(catalog['items']), 1)
        self.assertEqual(catalog['items'][0]['token'], 'enc:old')
        self.assertEqual(catalog['items'][0]['model'], 'qwen3.6')
        self.assertTrue(catalog['active_model_id'])

    def test_dns_rebinding_rejects_public_ip(self):
        from unittest.mock import patch
        fake = [(None, None, None, None, ('8.8.8.8', 0))]
        with patch('tools.intranet_llm.socket.getaddrinfo', return_value=fake):
            with self.assertRaises(IntranetLlmError):
                resolve_private_host('internal.example.local')

    def test_private_ip_skips_dns(self):
        self.assertEqual(resolve_private_host('10.128.1.2'), ['10.128.1.2'])


if __name__ == '__main__':
    unittest.main()
