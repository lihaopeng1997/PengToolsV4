"""Typed value inspection through the actual manual-result callback; no DB/config."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QWidget
from tools.data_center.manual_session import ManualColumn, ManualQueryPage
from ui.data_center.manual_query import ManualQueryHost
from ui.data_center.result_model import ManualResultTableModel
from ui.data_center.value_inspector import MAX_PREVIEW_CHARACTERS, ValueInspectorDialog, value_preview

APP = QApplication.instance() or QApplication([])


class ValueInspectorTests(unittest.TestCase):
    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def test_manual_callback_reads_original_long_value_and_rejects_foreign_model(self):
        panel = QWidget()
        panel.language = 'en'
        host = ManualQueryHost(panel, profile_host=Mock())
        host.result_model = ManualResultTableModel(panel)
        value = 'synthetic-value-' * 800
        page = ManualQueryPage('q', 'tab', 1, (ManualColumn('payload'),), ((value,),), 1, 200, False, 20_000)
        host.result_model.replace_page(page)
        observed = []

        def capture(dialog):
            self.assertIsInstance(dialog, ValueInspectorDialog)
            self.assertTrue(dialog.value_view.isReadOnly())
            observed.append(dialog.value_view.toPlainText())
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, 'exec', capture):
            host._inspect_result_value(host.result_model.index(0, 0))
            foreign = ManualResultTableModel()
            foreign.replace_page(page)
            host._inspect_result_value(foreign.index(0, 0))
        self.assertEqual(observed, [value])
        self.assertEqual(host.result_model.pages, (page,))
        panel.deleteLater()

    def test_null_empty_string_and_binary_are_distinct(self):
        for value, expected, kind in ((None, 'NULL', 'NULL'), ('', '', '空字符串'), (b'\x00\xff', '0x00ff', 'bytes')):
            dialog = ValueInspectorDialog('value', value)
            self.assertEqual(dialog.value_view.toPlainText(), expected)
            self.assertIn(kind, dialog.type_label.text())
            self.assertTrue(dialog.limit_note.isHidden())
            dialog.deleteLater()

    def test_large_text_and_binary_are_bounded_and_explicitly_report_truncation(self):
        text = '汉' * (MAX_PREVIEW_CHARACTERS + 100)
        dialog = ValueInspectorDialog('large', text, language='en')
        self.assertEqual(len(dialog.value_view.toPlainText()), MAX_PREVIEW_CHARACTERS)
        self.assertFalse(dialog.limit_note.isHidden())
        self.assertIn('raw value is intact', dialog.limit_note.text())
        self.assertEqual(len(text), MAX_PREVIEW_CHARACTERS + 100)
        preview, count = value_preview(b'\x80' * 1000, limit=14)
        self.assertEqual(preview, '0x' + '80' * 6)
        self.assertEqual(count, 2002)
        dialog.deleteLater()

    def test_driver_backed_value_is_never_stringified_or_read_by_ui(self):
        from ui.data_center.result_find import scan_matches

        calls = []
        class LazyValue:
            def __str__(self):
                calls.append('read')
                raise AssertionError('driver-backed value read on UI thread')

        value = LazyValue()
        page = ManualQueryPage('q', 'tab', 1, (ManualColumn('lob'),), ((value,),), 1, 200, False, 64)
        model = ManualResultTableModel()
        model.replace_page(page)
        self.assertEqual(model.index(0, 0).data(Qt.ItemDataRole.DisplayRole), '<LazyValue>')
        self.assertIs(model.index(0, 0).data(Qt.ItemDataRole.UserRole), value)
        dialog = ValueInspectorDialog('lob', value)
        self.assertIn('尚未载入', dialog.type_label.text())
        self.assertFalse(dialog.limit_note.isHidden())
        self.assertEqual([m for m in scan_matches((page,), 'value') if m is not None], [])
        self.assertEqual(calls, [])
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
