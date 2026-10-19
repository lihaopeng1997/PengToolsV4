"""Regression coverage for the independent settings persistence domains."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import MethodType
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class SettingsSaveRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from config import DEFAULT_SETTINGS
        from panels.settings_panel import SettingsPanel

        with patch.object(SettingsPanel, '_load_ai_local_values'), \
                patch.object(SettingsPanel, '_load_reminder_values'), \
                patch.object(SettingsPanel, '_refresh_oracle_status'):
            panel = SettingsPanel(dict(DEFAULT_SETTINGS), 'zh')
        return panel

    def test_general_save_preserves_model_and_reminder_drafts(self):
        panel = self._panel()
        try:
            panel.font_size.setValue(15)
            panel.reminder_enabled.setChecked(False)
            panel.reminder_time.setTime(__import__('PyQt6.QtCore', fromlist=['QTime']).QTime(8, 15))
            panel.ai_name.setText('正在编辑的模型')
            panel.ai_model.addItem('draft-response-model')
            panel.ai_model.setCurrentText('draft-response-model')
            panel.ai_timeout.setValue(45)

            loads = []
            applied = []
            with patch.object(panel, '_load_ai_local_values', side_effect=AssertionError('model catalog reloaded')), \
                    patch.object(panel, '_load_reminder_values', side_effect=AssertionError('reminder store reloaded')), \
                    patch.object(panel, '_persist_reminder_settings', return_value={
                        'enabled': False, 'time': '08:15', 'last_reminder_date': '',
                    }) as persist:
                def apply_general(settings):
                    applied.append(dict(settings))
                    loads.append(False)
                    panel.load_values(settings, reload_external=False)
                    panel._set_settings_save_result(True)
                    return True

                panel.settings_changed.connect(apply_general)
                panel._save()
                self.assertTrue(panel._settings_save_in_progress)
                self.assertTrue(panel._save_loading.is_visible_to_user)
                self.assertFalse(panel.save_btn.isEnabled())
                self.assertFalse(panel.restore_btn.isEnabled())
                self.assertFalse(panel.ai_save_btn.isEnabled())
                self.assertFalse(panel.reminder_save_btn.isEnabled())
                self.assertEqual(applied, [])
                # The callback runs on the next Qt turn. Mutations made after
                # the click must not change the immutable save snapshot.
                panel.font_size.setValue(16)
                panel.reminder_time.setTime(
                    __import__('PyQt6.QtCore', fromlist=['QTime']).QTime(9, 20)
                )
                self.app.processEvents()

            self.assertEqual(len(applied), 1)
            self.assertEqual(loads, [False])
            persist.assert_called_once_with(
                notify_ui=True,
                show_toast=False,
                draft={'enabled': False, 'time': '08:15'},
            )
            self.assertEqual(applied[0]['font_size'], 15)
            self.assertFalse(panel._settings_save_in_progress)
            self.assertTrue(panel.save_btn.isEnabled())
            self.assertTrue(panel.ai_save_btn.isEnabled())
            self.assertTrue(panel.reminder_save_btn.isEnabled())
            self.assertFalse(panel.reminder_enabled.isChecked())
            self.assertEqual(panel.reminder_time.time().toString('HH:mm'), '09:20')
            self.assertEqual(panel.ai_name.text(), '正在编辑的模型')
            self.assertEqual(panel.ai_model.currentText(), 'draft-response-model')
            self.assertEqual(panel.ai_timeout.value(), 45)
            self.assertEqual(panel._save_loading._state, 'finish')
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_general_save_failure_keeps_draft_and_reports_failure(self):
        panel = self._panel()
        try:
            panel.ai_name.setText('失败时保留的草稿')
            panel.ai_timeout.setValue(39)
            with patch.object(panel, '_persist_reminder_settings', return_value={}), \
                    patch('ui.confirm_dialog.show_error'):
                panel.settings_changed.connect(
                    lambda _settings: panel._set_settings_save_result(False, '磁盘不可写')
                )
                panel._save()
                self.assertFalse(panel.save_btn.isEnabled())
                self.app.processEvents()

            self.assertFalse(panel._settings_save_in_progress)
            self.assertTrue(panel.save_btn.isEnabled())
            self.assertEqual(panel.ai_name.text(), '失败时保留的草稿')
            self.assertEqual(panel.ai_timeout.value(), 39)
            self.assertEqual(panel._save_loading._state, 'fail')
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_model_save_is_deferred_and_has_success_or_failure_receipt(self):
        panel = self._panel()
        try:
            import tools.intranet_llm as intranet

            panel.ai_name.setText('内存模型配置')
            panel.ai_model.addItem('response-model')
            panel.ai_model.setCurrentText('response-model')
            panel.ai_timeout.setValue(33)
            with tempfile.TemporaryDirectory() as temp_dir, \
                    patch.object(intranet, 'AI_LOCAL_FILE', os.path.join(temp_dir, 'ai_local.json')), \
                    patch.object(intranet, 'ensure_config_dir'), \
                    patch('ui.confirm_dialog.show_success') as success, \
                    patch('ui.confirm_dialog.show_error') as error:
                panel._save_intranet_model()
                self.assertTrue(panel._ai_save_in_progress)
                self.assertTrue(panel._save_loading.is_visible_to_user)
                self.assertFalse(panel.ai_save_btn.isEnabled())
                self.assertFalse(panel.save_btn.isEnabled())
                self.assertFalse(panel.restore_btn.isEnabled())
                self.assertFalse(panel.reminder_save_btn.isEnabled())
                self.assertFalse(os.path.exists(intranet.AI_LOCAL_FILE))
                panel.ai_timeout.setValue(99)
                self.app.processEvents()

                self.assertFalse(panel._ai_save_in_progress)
                self.assertTrue(panel.ai_save_btn.isEnabled())
                self.assertTrue(success.called)
                self.assertFalse(error.called)
                with open(intranet.AI_LOCAL_FILE, encoding='utf-8') as stream:
                    catalog = json.load(stream)
                self.assertEqual(catalog['items'][0]['model'], 'response-model')
                self.assertEqual(catalog['items'][0]['timeout_seconds'], 33)
                self.assertEqual(panel._save_loading._state, 'finish')

                panel.ai_name.setText('失败仍保留的模型草稿')
                with patch.object(intranet, 'upsert_model_item', side_effect=RuntimeError('模型文件不可写')):
                    panel._save_intranet_model()
                    self.assertFalse(panel.ai_save_btn.isEnabled())
                    self.app.processEvents()
                self.assertTrue(panel.ai_save_btn.isEnabled())
                self.assertEqual(panel.ai_name.text(), '失败仍保留的模型草稿')
                self.assertEqual(panel._save_loading._state, 'fail')
                self.assertTrue(error.called)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_settings_rows_and_category_geometry_match_v21_contract(self):
        panel = self._panel()
        try:
            panel.resize(1040, 740)
            panel.apply_layout_mode('standard', False)
            panel.show()
            self.app.processEvents()
            self.assertEqual(panel.section_nav.width(), 180)
            self.assertEqual(panel._settings_body_layout.spacing(), 24)
            self.assertEqual(panel.findChild(__import__('PyQt6.QtWidgets', fromlist=['QWidget']).QWidget, 'settings-workspace').maximumWidth(), 1040)
            rows = panel.findChildren(__import__('PyQt6.QtWidgets', fromlist=['QWidget']).QWidget, 'settings-form-row')
            self.assertGreater(len(rows), 20)
            self.assertTrue(all(row.minimumHeight() >= 72 for row in rows))
            for label, field in (
                (panel.font_label, panel.font_size),
                (panel.density_label, panel.density_combo),
                (panel.sidebar_collapsed_label, panel.sidebar_collapsed_check),
            ):
                label_y = label.mapTo(panel, label.rect().center()).y()
                field_y = field.mapTo(panel, field.rect().center()).y()
                self.assertLessEqual(abs(label_y - field_y), 4)
            self.assertTrue(panel.save_btn.isVisible())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def _production_host(self, panel):
        from config import DEFAULT_SETTINGS
        from main_window import MainWindow

        class _ApplyHost:
            pass

        host = _ApplyHost()
        host.settings_panel = panel
        host._private_unlocked = False
        host._settings = dict(DEFAULT_SETTINGS)
        host.language = 'zh'
        host.status_bar = Mock()
        host._context_header = None
        host.personal_panel = None
        host.requirement_panel = None
        host.format_panel = None
        host.interface_debug_panel = None
        host.quick_panel = None
        host.tray_service = None
        host.ops_panel = None
        host.keep_awake_service = None
        host._language_index = 0
        host._apply_density_preferences = Mock()
        host._apply_nav_texts = Mock()
        host._refresh_brand_icon = Mock()
        host._sync_web_theme = Mock()
        host._set_language = Mock()
        host._apply_settings_impl = MethodType(MainWindow._apply_settings_impl, host)
        host._apply_settings = MethodType(MainWindow._apply_settings, host)
        return host

    def test_real_main_window_apply_callback_preserves_drafts(self):
        panel = self._panel()
        host = self._production_host(panel)
        try:
            panel.ai_name.setText('生产回调草稿')
            panel.ai_timeout.setValue(41)
            panel.reminder_enabled.setChecked(False)
            with patch.object(panel, '_persist_reminder_settings', return_value={}), \
                    patch.object(type(panel), '_refresh_oracle_status'), \
                    patch('main_window.save_settings', side_effect=lambda settings: dict(settings)):
                panel.settings_changed.connect(host._apply_settings)
                panel._save()
                self.assertTrue(panel._settings_save_in_progress)
                self.app.processEvents()

            self.assertFalse(panel._settings_save_in_progress)
            self.assertEqual(panel.ai_name.text(), '生产回调草稿')
            self.assertEqual(panel.ai_timeout.value(), 41)
            self.assertFalse(panel.reminder_enabled.isChecked())
            self.assertEqual(panel._save_loading._state, 'finish')
            host._apply_density_preferences.assert_called_once()
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_real_main_window_post_apply_failure_is_reported_as_partial(self):
        panel = self._panel()
        host = self._production_host(panel)
        host._apply_density_preferences = Mock(side_effect=RuntimeError('density application failed'))
        try:
            panel.ai_name.setText('保留的失败草稿')
            with patch.object(panel, '_persist_reminder_settings', return_value={}), \
                    patch.object(type(panel), '_refresh_oracle_status'), \
                    patch('main_window.save_settings', side_effect=lambda settings: dict(settings)):
                panel.settings_changed.connect(host._apply_settings)
                panel._save()
                self.app.processEvents()

            self.assertFalse(panel._settings_save_in_progress)
            self.assertTrue(panel.save_btn.isEnabled())
            self.assertEqual(panel.ai_name.text(), '保留的失败草稿')
            self.assertEqual(panel._save_loading._state, 'fail')
            status_texts = [str(call.args[0]) for call in host.status_bar.showMessage.call_args_list]
            self.assertTrue(any('已写入' in message and '应用失败' in message for message in status_texts))
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_model_connection_fields_precede_advanced_and_save_stays_reachable(self):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtWidgets import QScrollArea

        panel = self._panel()
        try:
            panel.resize(1040, 740)
            panel.apply_layout_mode('standard', False)
            panel.show()
            panel.section_nav.setCurrentRow(5)
            self.app.processEvents()

            self.assertFalse(panel.ai_advanced_host.isVisible())
            self.assertEqual(panel.ai_list.height(), 72)
            self.assertEqual(panel.save_btn.text(), '保存一般偏好')

            def mid_y(widget):
                return widget.mapTo(panel, widget.rect().center()).y()

            self.assertLess(mid_y(panel.ai_name), mid_y(panel.ai_enabled))
            self.assertLess(mid_y(panel.ai_enabled), mid_y(panel.ai_base_url))
            self.assertLess(mid_y(panel.ai_base_url), mid_y(panel.ai_model))
            self.assertLess(mid_y(panel.ai_model), mid_y(panel.ai_token))
            self.assertLess(mid_y(panel.ai_token), mid_y(panel.ai_save_btn))
            panel.ai_advanced_toggle.setChecked(True)
            self.app.processEvents()
            self.assertTrue(panel.ai_advanced_host.isVisible())
            self.assertLess(mid_y(panel.ai_save_btn), mid_y(panel.ai_agent_capability))
            self.assertLess(
                mid_y(panel.ai_agent_capability),
                mid_y(panel.ai_agent_max_reasoning_bytes),
            )
            self.assertLess(
                mid_y(panel.ai_save_btn) - mid_y(panel.ai_name),
                mid_y(panel.ai_agent_max_raw_buffer_bytes) - mid_y(panel.ai_name),
            )

            scroll = panel.sections_stack.currentWidget()
            self.assertIsInstance(scroll, QScrollArea)
            save_in_view = panel.ai_save_btn.mapTo(scroll.viewport(), QPoint(0, 0))
            self.assertGreaterEqual(save_in_view.y(), -8)
            self.assertLess(
                save_in_view.y(),
                scroll.viewport().height() + 24,
            )
            self.assertEqual(panel.ai_action_row.objectName(), 'settings-model-actions')
            self.assertIs(panel.ai_save_btn.parentWidget(), panel.ai_action_row)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_settings_form_editors_align_left_in_shared_field_column(self):
        from PyQt6.QtCore import QPoint

        panel = self._panel()
        try:
            panel.resize(1040, 740)
            panel.apply_layout_mode('standard', False)
            panel.show()
            self.app.processEvents()

            def left_x(widget):
                return widget.mapTo(panel, widget.rect().topLeft()).x()

            def form_row(widget):
                host = widget
                while host is not None and host.objectName() != 'settings-form-row':
                    host = host.parentWidget()
                return host

            font_x = left_x(panel.font_size)
            self.assertLessEqual(abs(left_x(panel.density_combo) - font_x), 8)
            self.assertLessEqual(abs(left_x(panel.sidebar_collapsed_check) - font_x), 8)
            combo_row = form_row(panel.density_combo)
            self.assertIsNotNone(combo_row)
            combo_left_gap = left_x(panel.density_combo) - combo_row.mapTo(panel, QPoint(0, 0)).x()
            combo_right_gap = (
                combo_row.mapTo(panel, combo_row.rect().topRight()).x()
                - (left_x(panel.density_combo) + panel.density_combo.width())
            )
            self.assertLessEqual(combo_left_gap, 4)
            self.assertGreater(combo_right_gap, combo_left_gap)

            for label, field in (
                (panel.font_label, panel.font_size),
                (panel.density_label, panel.density_combo),
                (panel.sidebar_collapsed_label, panel.sidebar_collapsed_check),
            ):
                label_y = label.mapTo(panel, label.rect().center()).y()
                field_y = field.mapTo(panel, field.rect().center()).y()
                self.assertLessEqual(abs(label_y - field_y), 4)

            panel.section_nav.setCurrentRow(5)
            self.app.processEvents()
            name_x = left_x(panel.ai_name)
            for widget in (
                panel.ai_enabled,
                panel.ai_base_url,
                panel.ai_model,
                panel.ai_token,
            ):
                self.assertLessEqual(abs(left_x(widget) - name_x), 12)

            panel.ai_advanced_toggle.setChecked(True)
            self.app.processEvents()
            self.assertTrue(panel.ai_agent_capability.isVisible())
            self.assertIs(panel.ai_agent_capability.window(), panel.window())

            # The action row spans label and editor columns, unlike labelled
            # fields. Its controls should start at that row's own left edge.
            self.assertLessEqual(abs(left_x(panel.ai_probe_btn) - left_x(panel.ai_action_row)), 4)

            name_row = form_row(panel.ai_name)
            self.assertIsNotNone(name_row)
            self.assertGreaterEqual(panel.ai_name.width(), name_row.width() - 16)
            model_row = form_row(panel.ai_model)
            self.assertIsNotNone(model_row)
            self.assertLess(panel.ai_model.width(), model_row.width() * 0.7)
            model_left_gap = left_x(panel.ai_model) - model_row.mapTo(panel, QPoint(0, 0)).x()
            self.assertLessEqual(model_left_gap, 4)

            for label, field in (
                (panel.ai_name_label, panel.ai_name),
                (panel.ai_enabled_label, panel.ai_enabled),
                (panel.ai_model_label, panel.ai_model),
                (panel.ai_agent_capability_label, panel.ai_agent_capability),
            ):
                label_y = label.mapTo(panel, label.rect().center()).y()
                field_y = field.mapTo(panel, field.rect().center()).y()
                self.assertLessEqual(abs(label_y - field_y), 4)

            panel.resize(856, 458)
            panel.apply_layout_mode('narrow', False)
            panel.section_picker.setCurrentIndex(5)
            self.app.processEvents()
            self.assertTrue(panel.section_picker.isVisible())
            model_right = panel.ai_model.mapTo(panel, panel.ai_model.rect().topRight()).x()
            self.assertLessEqual(model_right, panel.width())
            self.assertGreaterEqual(form_row(panel.ai_name).minimumHeight(), 72)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
