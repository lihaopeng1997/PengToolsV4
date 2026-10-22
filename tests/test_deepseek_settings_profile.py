# -*- coding: utf-8 -*-
"""Settings form binding tests with every mutable settings path under TEMP."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

import config
from config import DEFAULT_SETTINGS
from panels.settings_panel import SettingsPanel
from tools import intranet_llm


class DeepSeekSettingsProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @contextmanager
    def _panel_under_temp_paths(self, directory):
        paths = (
            ('SETTINGS_FILE', 'settings.json'),
            ('LAYOUT_SPLITTERS_FILE', 'layout_splitters.json'),
            ('AI_LOCAL_FILE', 'ai_local.json'),
            ('DAILY_REPORT_SETTINGS_FILE', 'daily_report_settings.json'),
        )
        from tools import daily_reports, harness_project
        with ExitStack() as stack:
            stack.enter_context(patch.object(config, 'CONFIG_DIR', directory))
            for name, filename in paths:
                stack.enter_context(patch.object(config, name, os.path.join(directory, filename)))
            stack.enter_context(patch.object(intranet_llm, 'AI_LOCAL_FILE', os.path.join(directory, 'ai_local.json')))
            stack.enter_context(patch.object(intranet_llm, 'ensure_config_dir', lambda: None))
            stack.enter_context(patch.object(daily_reports, 'DAILY_REPORT_SETTINGS_FILE', os.path.join(directory, 'daily_report_settings.json')))
            stack.enter_context(patch.object(daily_reports, 'load_reminder_settings', lambda: {'enabled': False, 'time': '17:30'}))
            stack.enter_context(patch.object(harness_project, 'list_projects', lambda: []))
            page = SettingsPanel(DEFAULT_SETTINGS)
            try:
                yield page
            finally:
                page.close()
                self.app.processEvents()

    def test_provider_switch_creates_disabled_credential_isolated_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._panel_under_temp_paths(directory) as page:
                self.assertEqual(page.ai_provider.currentData(), 'intranet')
                page.ai_token.setText('old-intranet-value')
                page.ai_model.setText('intranet-only-model')
                page.ai_provider.setCurrentIndex(page.ai_provider.findData('deepseek'))

                self.assertEqual(page.ai_provider.currentData(), 'deepseek')
                self.assertEqual(page.ai_base_url.text(), 'https://api.deepseek.com')
                self.assertEqual(page.ai_model.text(), 'deepseek-flash')
                self.assertEqual(page._ai_available_models, ['deepseek-flash'])
                self.assertEqual(page._ai_token_action(), 'clear')
                self.assertEqual(page.ai_token.text(), '')
                self.assertFalse(page.ai_enabled.isChecked())
                self.assertTrue(page.ai_ssl_verify.isChecked())
                self.assertEqual(page.ai_agent_capability.currentData(), 'unknown')
                self.assertEqual(page._ai_cfg_from_ui()['provider'], 'deepseek')

                page.ai_provider.setCurrentIndex(page.ai_provider.findData('intranet'))
                self.assertEqual(page.ai_model.text(), 'qwen3.8')
                self.assertEqual(page.ai_base_url.text(), 'http://10.128.25.142:18002/v1')
                self.assertEqual(page._ai_available_models, ['qwen3.8'])

    def test_loaded_model_catalog_survives_form_save_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._panel_under_temp_paths(directory) as page:
                page._fill_ai_form({
                    'id': 'deepseek-profile',
                    'provider': 'deepseek',
                    'name': 'DeepSeek profile',
                    'base_url': 'https://api.deepseek.com',
                    'ssl_verify': True,
                    'model': 'deepseek-v4-pro',
                    'available_models': ['deepseek-flash', 'deepseek-v4-pro'],
                })
                self.assertEqual(
                    page._ai_cfg_from_ui()['available_models'],
                    ['deepseek-flash', 'deepseek-v4-pro'],
                )

    def test_invalid_tls_and_unknown_provider_never_decrypt_into_form(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._panel_under_temp_paths(directory) as page:
                with patch('tools.intranet_llm.decrypt_token', side_effect=AssertionError('must not decrypt')) as decrypt:
                    page._fill_ai_form({
                        'id': 'deepseek-invalid-tls',
                        'provider': 'deepseek',
                        'name': 'Blocked DeepSeek',
                        'base_url': 'https://api.deepseek.com',
                        'ssl_verify': False,
                        'token': 'opaque-encrypted-value',
                    })
                    self.assertFalse(page.ai_ssl_verify.isChecked())
                    self.assertTrue(page.ai_ssl_verify.isEnabled())
                    self.assertEqual(page.ai_token.text(), '')
                    self.assertIn('TLS', page.ai_status.text())

                    page._fill_ai_form({
                        'id': 'unknown-provider',
                        'provider': 'not-recognized',
                        'name': 'Unsupported provider',
                        'token': 'opaque-encrypted-value',
                    })
                    self.assertEqual(page.ai_provider.currentData(), 'not-recognized')
                    self.assertEqual(page.ai_token.text(), '')
                    self.assertTrue(page.ai_status.text())
                    decrypt.assert_not_called()

    def test_deepseek_probe_is_temporary_and_does_not_enable_saved_profile(self):
        class _Signal:
            def connect(self, callback):
                self.callback = callback

        class _Worker:
            def __init__(self, cfg, _parent=None):
                self.cfg = cfg
                self.completed = _Signal()
                self.failed = _Signal()
                self.finished = _Signal()

            def start(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            with self._panel_under_temp_paths(directory) as page:
                page.ai_provider.setCurrentIndex(page.ai_provider.findData('deepseek'))
                page.ai_token.setText('new-local-key')
                page._ai_token_was_edited = True
                with patch('panels.settings_panel._AiProbeWorker', _Worker), patch(
                    'tools.intranet_llm.encrypt_token', side_effect=lambda value: f'test-encrypted:{value}'
                ):
                    page._probe_intranet_model()
                self.assertTrue(page._ai_probe_worker.cfg['enabled'])
                self.assertFalse(page.ai_enabled.isChecked())

                persisted = []
                page.ai_model.setText('deepseek-flash')
                with patch('tools.intranet_llm.upsert_model_item', side_effect=lambda cfg, **kwargs: persisted.append((cfg, kwargs)) or dict(cfg, id='saved-ds')), patch.object(page, '_refresh_ai_list'), patch(
                    'tools.intranet_llm.encrypt_token', side_effect=lambda value: f'test-encrypted:{value}'
                ):
                    page._on_ai_probe_ok(['deepseek-flash'])
                self.assertFalse(persisted[0][0]['enabled'])
                self.assertEqual(persisted[0][1]['token_action'], 'replace')


if __name__ == '__main__':
    unittest.main()
