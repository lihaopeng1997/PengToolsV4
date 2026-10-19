# -*- coding: utf-8 -*-
"""Focused policy tests for the explicit DeepSeek profile route."""

from __future__ import annotations

import os
import ssl
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from tools import intranet_llm as llm


class DeepSeekProfilePolicyTests(unittest.TestCase):
    def test_only_exact_https_official_host_and_supported_paths_are_accepted(self):
        accepted = (
            'https://api.deepseek.com',
            'https://api.deepseek.com/',
            'https://api.deepseek.com/v1',
            'https://api.deepseek.com/v1/',
            'https://api.deepseek.com:443/v1',
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertEqual(
                    llm.validate_profile_base_url(value, 'deepseek'),
                    value.rstrip('/'),
                )

        rejected = (
            '',
            'http://api.deepseek.com/v1',
            'https://api.deepseek.com:444/v1',
            'https://user:pass@api.deepseek.com/v1',
            'https://api.deepseek.com.evil.example/v1',
            'https://api.deepseek.com/v2',
            'https://api.deepseek.com/v1?x=1',
            'https://api.deepseek.com/v1?',
            'https://api.deepseek.com/v1#frag',
            'https://api.deepseek.com/v1#',
            'https://api.deepseek.com/v1///',
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(llm.IntranetLlmError):
                    llm.validate_profile_base_url(value, 'deepseek')

    def test_explicit_unknown_provider_and_permissive_tls_fail_before_decrypt(self):
        for cfg in (
            {'provider': 'other', 'token': 'do-not-decrypt'},
            {'provider': 'deepseek', 'ssl_verify': False, 'token': 'do-not-decrypt'},
            {'provider': 'deepseek', 'ssl_verify': None, 'token': 'do-not-decrypt'},
            {'provider': 'deepseek', 'ssl_verify': 'true', 'token': 'do-not-decrypt'},
            {'provider': 'deepseek', 'ssl_verify': 0.0, 'token': 'do-not-decrypt'},
        ):
            with self.subTest(provider=cfg.get('provider'), ssl=cfg.get('ssl_verify')):
                with patch('tools.intranet_llm.decrypt_token') as decrypt:
                    with self.assertRaises(llm.IntranetLlmError):
                        llm.build_headers(cfg)
                    decrypt.assert_not_called()

    def test_unknown_provider_fails_before_transport(self):
        cfg = {
            'provider': 'unexpected',
            'enabled': True,
            'base_url': 'https://api.deepseek.com',
            'token': 'opaque-encrypted-value',
        }
        with patch('tools.intranet_llm.decrypt_token') as decrypt, patch(
            'tools.intranet_llm.opener_for_profile'
        ) as opener:
            with self.assertRaises(llm.IntranetLlmError):
                llm._request(cfg, 'GET', 'models')
        decrypt.assert_not_called()
        opener.assert_not_called()

    def test_deepseek_transport_is_direct_tls_and_does_not_follow_redirects(self):
        cfg = {
            'provider': 'deepseek',
            'base_url': 'https://api.deepseek.com/v1',
            'ssl_verify': True,
        }
        # An explicit empty ProxyHandler must avoid consulting environment
        # proxy settings, even when a process has a proxy configured.
        with patch('urllib.request.getproxies', side_effect=AssertionError('proxy discovery is forbidden')):
            opener = llm.opener_for_profile(cfg)
        https = next(item for item in opener.handlers if isinstance(item, urllib.request.HTTPSHandler))
        redirect = next(item for item in opener.handlers if isinstance(item, llm._NoRedirectHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)
        self.assertIsNone(redirect.redirect_request(None, None, 302, 'Found', {}, 'https://example.invalid/'))

    def test_request_routes_deepseek_without_private_dns_resolution(self):
        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"data":[]}'

        class _Opener:
            def __init__(self):
                self.request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return _Response()

        fake = _Opener()
        cfg = {
            'provider': 'deepseek',
            'enabled': True,
            'base_url': 'https://api.deepseek.com/v1',
            'ssl_verify': True,
            'token': 'opaque-encrypted-value',
            'timeout_seconds': 7,
        }
        with patch('tools.intranet_llm.decrypt_token', return_value='local-test-key'), patch(
            'tools.intranet_llm.opener_for_profile', return_value=fake
        ), patch('tools.intranet_llm.resolve_private_host') as private_dns:
            result = llm._request(cfg, 'GET', 'models')
        self.assertEqual(result, {'data': []})
        self.assertEqual(fake.request.full_url, 'https://api.deepseek.com/v1/models')
        self.assertEqual(fake.request.get_header('Authorization'), 'Bearer local-test-key')
        private_dns.assert_not_called()

    def test_provider_change_and_new_profile_require_explicit_token_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = os.path.join(directory, 'catalog.json')
            with patch.object(llm, 'AI_LOCAL_FILE', catalog_path), patch.object(
                llm, 'ensure_config_dir', return_value=None
            ):
                intranet = llm.upsert_model_item(
                    {
                        'id': 'same-profile',
                        'name': 'Intranet profile',
                        'provider': 'intranet',
                        'enabled': False,
                        'base_url': 'http://10.0.0.2:8000/v1',
                        'token': 'opaque-intranet-token',
                    },
                    token_action='replace',
                    make_active=True,
                )
                deepseek = llm.upsert_model_item(
                    {
                        **intranet,
                        'name': 'DeepSeek profile',
                        'provider': 'deepseek',
                        'base_url': 'https://api.deepseek.com',
                        'enabled': False,
                    },
                    token_action='preserve',
                )
                self.assertEqual(intranet['token'], 'opaque-intranet-token')
                self.assertEqual(deepseek['token'], '')
                self.assertEqual(llm.load_model_catalog()['active_model_id'], 'same-profile')

                supplied = llm.upsert_model_item(
                    {**deepseek, 'token': 'opaque-new-deepseek-token'},
                    token_action='replace',
                )
                copied = llm.upsert_model_item(
                    {
                        **supplied,
                        'id': 'new-profile',
                        'name': 'DeepSeek copy',
                    },
                    token_action='clear',
                )
                self.assertEqual(supplied['token'], 'opaque-new-deepseek-token')
                self.assertEqual(copied['token'], '')
                self.assertEqual(llm.load_model_catalog()['active_model_id'], 'same-profile')

    def test_legacy_save_preserves_same_provider_token_but_clears_provider_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = os.path.join(directory, 'catalog.json')
            with patch.object(llm, 'AI_LOCAL_FILE', catalog_path), patch.object(
                llm, 'ensure_config_dir', return_value=None
            ):
                created = llm.save_ai_local({
                    'id': 'legacy-save',
                    'name': 'Legacy-compatible profile',
                    'base_url': 'http://10.0.0.2:8000/v1',
                    'enabled': False,
                    'token': 'opaque-intranet-token',
                })
                updated = llm.save_ai_local({
                    'id': 'legacy-save',
                    'name': 'Legacy-compatible profile',
                    'provider': 'intranet',
                    'base_url': 'http://10.0.0.3:8000/v1',
                    'enabled': False,
                })
                switched = llm.save_ai_local({
                    'id': 'legacy-save',
                    'name': 'Legacy-compatible profile',
                    'provider': 'deepseek',
                    'base_url': 'https://api.deepseek.com',
                    'enabled': False,
                })
                self.assertEqual(created['provider'], 'intranet')
                self.assertEqual(updated['token'], 'opaque-intranet-token')
                self.assertEqual(switched['provider'], 'deepseek')
                self.assertEqual(switched['token'], '')

    def test_provider_and_chat_body_preserve_legacy_route_and_enable_thinking(self):
        self.assertEqual(llm.normalize_ai_local({})['provider'], 'intranet')
        with self.assertRaises(llm.IntranetLlmError):
            llm.provider_for_config({'provider': 'future-provider'})

        cfg = {
            'provider': 'deepseek',
            'enabled': True,
            'base_url': 'https://api.deepseek.com/v1',
            'model': '',
            'token': 'opaque-encrypted-value',
            'max_tokens': 128,
        }
        with patch('tools.intranet_llm._request', return_value={'choices': [{'message': {'content': 'ok'}}]}) as request:
            self.assertEqual(llm.chat_completions([{'role': 'user', 'content': 'hello'}], cfg=cfg), 'ok')
        body = request.call_args.args[3]
        self.assertEqual(body['model'], 'deepseek-flash')
        self.assertEqual(body['thinking'], {'type': 'enabled'})
        self.assertTrue(body['stream'])


if __name__ == '__main__':
    unittest.main()
