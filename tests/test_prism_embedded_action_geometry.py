"""Regression checks for embedded panel actions at narrow responsive sizes."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication

from panels.docx_panel import DocxUpdatePanel
from panels.gateway_panel import GatewayDecodePanel
from ui.theme_manager import ThemeManager


VIEWPORTS = (
    (960, 640, 856, 446, 'narrow'),
    (1100, 720, 984, 526, 'compact'),
)

MODE_ROUNDTRIP = (
    (856, 446, 'narrow'),
    (984, 526, 'compact'),
    (1060, 638, 'standard'),
    (1192, 738, 'wide'),
    (856, 446, 'narrow'),
)


class PrismEmbeddedActionGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.previous_stylesheet = cls.app.styleSheet()
        cls.previous_font = cls.app.font()
        cls.theme_manager = ThemeManager.instance()

    @classmethod
    def tearDownClass(cls):
        cls.app.setFont(cls.previous_font)
        cls.app.setStyleSheet(cls.previous_stylesheet)

    def _apply_theme(self, font_size: int) -> None:
        self.theme_manager.apply(self.app, 'calm', font_size=font_size)
        font = self.app.font()
        font.setPointSize(max(8, int(font_size) - 2))
        self.app.setFont(font)

    def _settle(self) -> None:
        self.app.processEvents()

    def _close_widget(self, widget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _assert_gateway_viewer_actions_fit(self, panel, language: str, *, require_fit: bool = True) -> None:
        viewer = panel.json_viewer
        path_rect = QRect(viewer.path_value.mapTo(viewer, QPoint(0, 0)), viewer.path_value.size())
        action_layout = GatewayDecodePanel._layout_containing_widget(
            viewer.layout(), viewer.path_value,
        )
        self.assertIsNotNone(action_layout)
        buttons = (
            viewer.copy_path_btn, viewer.expand_btn, viewer.collapse_btn, viewer.glance_btn,
        )
        expected = (
            ('复制路径', '全部展开', '全部折叠', '缩略图') if language == 'zh'
            else ('Copy path', 'Expand all', 'Collapse all', 'Minimap')
        )
        self.assertEqual(tuple(button.text() for button in buttons), expected)
        if require_fit:
            for button in buttons:
                rect = QRect(button.mapTo(viewer, QPoint(0, 0)), button.size())
                self.assertGreaterEqual(
                    button.width(), button.sizeHint().width(),
                    f'{language=}: {button.text()}={button.width()} vs intrinsic {button.sizeHint().width()}',
                )
                self.assertGreaterEqual(button.height(), button.sizeHint().height())
                self.assertFalse(
                    path_rect.intersects(rect),
                    f'{language=}: path field {path_rect} overlaps {button.text()} {rect}',
                )
            self.assertGreaterEqual(viewer.width(), action_layout.sizeHint().width())

    def test_docx_path_rows_preserve_child_minimums_and_action_connections(self):
        action_names = (
            'folder_browse', 'folder_refresh', 'folder_open', 'docx_browse',
            'svn_pull_btn', 'output_dir_browse', 'output_dir_open',
        )
        for font_size in (12, 16):
            self._apply_theme(font_size)
            for language in ('zh', 'en'):
                for _window_width, _window_height, panel_width, panel_height, mode in VIEWPORTS:
                    panel = DocxUpdatePanel(language)
                    panel.resize(panel_width, panel_height)
                    panel.apply_layout_mode(mode, low_height=False)
                    panel.show()
                    self._settle()
                    self.addCleanup(self._close_widget, panel)

                    edits = (panel.folder_path, panel.docx_path, panel.svn_url, panel.output_dir)
                    saved_text = []
                    for edit, sample in zip(edits, (
                        r'D:\Synthetic\module-completeness\folder',
                        r'D:\Synthetic\module-completeness\input.docx',
                        'https://example.invalid/synthetic-repository',
                        r'D:\Synthetic\module-completeness\output',
                    )):
                        edit.blockSignals(True)
                        edit.setText(sample)
                        edit.blockSignals(False)
                        saved_text.append(sample)

                    buttons = [getattr(panel, name) for name in action_names]
                    receiver_counts = [int(button.receivers(button.clicked)) for button in buttons]
                    self.assertTrue(all(count > 0 for count in receiver_counts))
                    self.assertTrue(all(button.text().strip() for button in buttons))

                    for edit in edits:
                        row = edit.parentWidget()
                        row_layout = row.layout()
                        self.assertIsNotNone(row_layout)
                        self.assertGreaterEqual(
                            row.minimumHeight(), row_layout.minimumSize().height(),
                            f'{language=} {font_size=} {panel_width=}',
                        )
                        row_rect = QRect(QPoint(0, 0), row.size())
                        for index in range(row_layout.count()):
                            child = row_layout.itemAt(index).widget()
                            if child is not None and child.isVisible():
                                self.assertTrue(
                                    row_rect.contains(child.geometry()),
                                    f'{type(child).__name__} exceeds its path-row wrapper: '
                                    f'{child.geometry()} vs {row_rect}',
                                )

                    self.assertEqual([edit.text() for edit in edits], saved_text)
                    self.assertEqual(
                        [int(button.receivers(button.clicked)) for button in buttons],
                        receiver_counts,
                    )
                    self.assertEqual(panel.update_btn.text(), '一键更新文档' if language == 'zh' else 'Update document')

    def test_gateway_result_pane_minimum_tracks_child_layout_after_mode_changes(self):
        for font_size in (12, 16):
            self._apply_theme(font_size)
            for language in ('zh', 'en'):
                for _window_width, _window_height, panel_width, panel_height, initial_mode in VIEWPORTS:
                    with patch('config.load_layout_splitter', return_value=None), \
                         patch('config.save_layout_splitter') as save_layout:
                        panel = GatewayDecodePanel(language)
                        panel.resize(panel_width, panel_height)
                        panel.apply_layout_mode(initial_mode, low_height=False)
                        panel.show()
                        self._settle()
                        self.addCleanup(self._close_widget, panel)

                        viewer = panel.json_viewer
                        synthetic_json = '{"fixture":"SYNTHETIC · no external data"}'
                        self.assertTrue(viewer.set_text(synthetic_json))
                        panel.payload_cipher.setPlainText('SYNTHETIC · no decrypt action invoked')
                        initial_text = (viewer.plain_text(), panel.payload_cipher.toPlainText())
                        actions = (viewer.clear_view_btn, viewer.copy_json_btn)
                        receivers = [int(button.receivers(button.clicked)) for button in actions]
                        self.assertTrue(all(count > 0 for count in receivers))

                        for mode_width, mode_height, mode in MODE_ROUNDTRIP:
                            panel.resize(mode_width, mode_height)
                            panel.apply_layout_mode(mode, low_height=False)
                            self._settle()
                            right_pane = panel.splitter.widget(1)
                            self._assert_gateway_viewer_actions_fit(
                                panel, language,
                                require_fit=panel.splitter.orientation() == Qt.Orientation.Horizontal,
                            )
                            if panel.splitter.orientation() == Qt.Orientation.Vertical:
                                self.assertEqual(right_pane.minimumWidth(), 0)
                                right_layout = right_pane.layout()
                                self.assertIsNotNone(right_layout)
                                self.assertGreaterEqual(
                                    right_pane.minimumHeight(), right_layout.minimumSize().height(),
                                    f'{language=} {font_size=} {mode=}',
                                )
                                stable_minimum = right_pane.minimumHeight()
                                panel.apply_layout_mode(mode, low_height=False)
                                self._settle()
                                self.assertEqual(right_pane.minimumHeight(), stable_minimum)
                            else:
                                self.assertEqual(right_pane.minimumHeight(), 0)
                                action_row = GatewayDecodePanel._layout_containing_widget(
                                    panel.json_viewer.layout(), panel.json_viewer.path_value,
                                )
                                self.assertGreaterEqual(
                                    right_pane.minimumWidth(), action_row.sizeHint().width(),
                                    f'{language=} {font_size=} {mode=}',
                                )

                        # Return to the requested narrow configuration and check the
                        # footer after scrolling, where the original three-DIP clip occurred.
                        panel.apply_layout_mode(initial_mode, low_height=False)
                        panel.resize(panel_width, panel_height)
                        self._settle()
                        scroll = panel.workspace_scroll
                        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
                        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                        self._settle()
                        viewport_rect = QRect(QPoint(0, 0), scroll.viewport().size())
                        for button in actions:
                            button_rect = QRect(button.mapTo(scroll.viewport(), QPoint(0, 0)), button.size())
                            self.assertTrue(
                                viewport_rect.contains(button_rect),
                                f'{language=} {font_size=} {panel_width=}: '
                                f'{button.text()}={button_rect}, viewport={viewport_rect}',
                            )
                        self.assertEqual((viewer.plain_text(), panel.payload_cipher.toPlainText()), initial_text)
                        self.assertEqual(
                            [int(button.receivers(button.clicked)) for button in actions], receivers,
                        )
                        self.assertEqual(save_layout.call_count, 0)

    def test_json_viewer_standalone_minimum_and_synthetic_content_remain_unchanged(self):
        from ui.json_viewer import JsonViewer

        self._apply_theme(12)
        viewer = JsonViewer('zh')
        synthetic_json = '{"standalone":"SYNTHETIC"}'
        self.assertTrue(viewer.set_text(synthetic_json))
        self.assertEqual(viewer.plain_text().strip(), '{\n  "standalone": "SYNTHETIC"\n}')
        self.assertEqual(viewer.minimumHeight(), 0)
        self.assertEqual(int(viewer.clear_view_btn.receivers(viewer.clear_view_btn.clicked)), 1)
        self.assertEqual(int(viewer.copy_json_btn.receivers(viewer.copy_json_btn.clicked)), 1)
        self._close_widget(viewer)

    def test_live_font_roundtrip_keeps_layout_floors_and_widget_state(self):
        self._apply_theme(12)
        with patch('config.load_layout_splitter', return_value=None), \
             patch('config.save_layout_splitter') as save_layout:
            docx = DocxUpdatePanel('zh')
            docx.resize(856, 446)
            docx.apply_layout_mode('narrow', low_height=False)
            docx.show()
            self.addCleanup(self._close_widget, docx)

            gateway = GatewayDecodePanel('zh')
            gateway.resize(856, 446)
            gateway.apply_layout_mode('narrow', low_height=False)
            gateway.show()
            self.addCleanup(self._close_widget, gateway)
            self._settle()

            docx.folder_path.blockSignals(True)
            docx.folder_path.setText(r'D:\Synthetic\font-roundtrip')
            docx.folder_path.blockSignals(False)
            gateway.json_viewer.set_text('{"fixture":"font roundtrip"}')
            gateway.payload_cipher.setPlainText('synthetic input')
            initial_state = (
                docx.folder_path.text(),
                gateway.json_viewer.plain_text(),
                gateway.payload_cipher.toPlainText(),
                list(gateway.splitter.sizes()),
            )
            buttons = (docx.folder_browse, docx.folder_refresh, gateway.response_btn,
                       gateway.json_viewer.clear_view_btn, gateway.json_viewer.copy_json_btn)
            initial_receivers = [int(button.receivers(button.clicked)) for button in buttons]
            rows = (docx.folder_path.parentWidget(), docx.docx_path.parentWidget(),
                    docx.svn_url.parentWidget(), docx.output_dir.parentWidget())
            right_pane = gateway.splitter.widget(1)

            for font_size in (16, 12):
                self._apply_theme(font_size)
                docx.layout().activate()
                gateway.layout().activate()
                right_pane.layout().activate()
                self._settle()
                for row in rows:
                    self.assertGreaterEqual(row.minimumHeight(), row.layout().minimumSize().height())
                    row_rect = QRect(QPoint(0, 0), row.size())
                    for index in range(row.layout().count()):
                        child = row.layout().itemAt(index).widget()
                        if child is not None and child.isVisible():
                            self.assertTrue(row_rect.contains(child.geometry()))
                self.assertGreaterEqual(
                    right_pane.minimumHeight(), right_pane.layout().minimumSize().height(),
                )
                self.assertEqual(
                    (docx.folder_path.text(), gateway.json_viewer.plain_text(),
                     gateway.payload_cipher.toPlainText(), list(gateway.splitter.sizes())),
                    initial_state,
                )
                self.assertEqual(
                    [int(button.receivers(button.clicked)) for button in buttons], initial_receivers,
                )
            self.assertEqual(save_layout.call_count, 0)

    def test_gateway_embedded_toolbar_reflows_on_same_widget_font_language_and_width_changes(self):
        with patch('config.load_layout_splitter', return_value=None), \
             patch('config.save_layout_splitter') as save_layout:
            panel = GatewayDecodePanel('zh')
            panel.resize(856, 446)
            panel.apply_layout_mode('narrow', low_height=False)
            panel.show()
            self.addCleanup(self._close_widget, panel)
            self._settle()
            panel.payload_cipher.setPlainText('synthetic payload remains unchanged')
            panel.json_viewer.set_text('{"fixture":"synthetic result"}')
            initial_state = (panel.payload_cipher.toPlainText(), panel.json_viewer.plain_text())

            roundtrips = (
                ('zh', 12, 856, 446, 'narrow'),
                ('en', 12, 1060, 638, 'standard'),
                ('en', 16, 1192, 738, 'wide'),
                ('zh', 16, 1060, 638, 'standard'),
                ('zh', 12, 856, 446, 'narrow'),
            )
            for language, font_size, width, height, mode in roundtrips:
                panel.set_language(language)
                self._apply_theme(font_size)
                panel.resize(width, height)
                panel.apply_layout_mode(mode, low_height=False)
                self._settle()
                self._assert_gateway_viewer_actions_fit(
                    panel, language,
                    require_fit=panel.splitter.orientation() == Qt.Orientation.Horizontal,
                )
                result_pane = panel.splitter.widget(1)
                if panel.splitter.orientation() == Qt.Orientation.Horizontal:
                    action_row = GatewayDecodePanel._layout_containing_widget(
                        panel.json_viewer.layout(), panel.json_viewer.path_value,
                    )
                    self.assertGreaterEqual(result_pane.minimumWidth(), action_row.sizeHint().width())
                else:
                    self.assertEqual(result_pane.minimumWidth(), 0)
                self.assertEqual(
                    (panel.payload_cipher.toPlainText(), panel.json_viewer.plain_text()),
                    initial_state,
                )
            self.assertEqual(save_layout.call_count, 0)

    def test_gateway_viewer_copy_and_fold_callbacks_use_only_synthetic_fixture_state(self):
        class ClipboardSpy:
            def __init__(self):
                self.copied = []

            def setText(self, value):
                self.copied.append(str(value))

        panel = GatewayDecodePanel('en')
        panel.resize(1192, 738)
        panel.apply_layout_mode('wide', low_height=False)
        panel.show()
        self.addCleanup(self._close_widget, panel)
        self._settle()
        viewer = panel.json_viewer
        self.assertTrue(viewer.set_text('{"root":{"child":"synthetic"}}'))
        viewer.path_value.setText('$.root.child')
        clipboard = ClipboardSpy()

        with patch.object(QApplication, 'clipboard', return_value=clipboard):
            viewer.copy_path_btn.click()
            self.assertEqual(clipboard.copied, ['$.root.child'])
            expected_json = viewer.text_edit.toPlainText().strip()
            viewer.copy_json_btn.click()
            self.assertEqual(clipboard.copied[-1], expected_json)

        viewer.expand_btn.click()
        viewer.collapse_btn.click()
        initial_glance = viewer.glance_btn.isChecked()
        viewer.glance_btn.click()
        viewer.glance_btn.click()
        viewer.clear_view_btn.click()
        self.assertEqual(viewer.text_edit.toPlainText(), '')
        self.assertEqual(viewer.glance_btn.isChecked(), initial_glance)
        for button in (
            viewer.copy_path_btn, viewer.expand_btn, viewer.collapse_btn,
            viewer.clear_view_btn, viewer.copy_json_btn,
        ):
            self.assertGreater(int(button.receivers(button.clicked)), 0)
        self.assertGreater(int(viewer.glance_btn.receivers(viewer.glance_btn.toggled)), 0)

    def test_docx_captions_wrap_completely_across_language_and_font_changes(self):
        self._apply_theme(12)
        panel = DocxUpdatePanel('zh')
        panel.resize(856, 446)
        panel.apply_layout_mode('narrow', low_height=False)
        panel.show()
        self._settle()
        self.addCleanup(self._close_widget, panel)

        captions = (
            'folder_row_label', 'doc_list_label', 'docx_row_label',
            'template_row_label', 'svn_row_label', 'output_dir_label',
            'output_row_label', 'author_row_label', 'date_row_label',
        )
        expected_text = {
            'zh': ('文档目录', '点选文档', '当前文档', '模板识别', 'SVN（可选）',
                   '输出目录', '生成结果', '作者', '日期'),
            'en': ('Folder', 'Pick document', 'Current doc', 'Template',
                   'SVN (optional)', 'Output dir', 'Output file', 'Author', 'Date'),
        }

        for language in ('zh', 'en'):
            panel.set_language(language)
            for font_size in (12, 16, 12):
                self._apply_theme(font_size)
                panel.layout().activate()
                panel.file_group.layout().activate()
                self._settle()

                labels = [getattr(panel, name) for name in captions]
                self.assertEqual(tuple(label.text() for label in labels), expected_text[language])
                for label in labels:
                    self.assertTrue(label.wordWrap())
                    self.assertTrue(label.hasHeightForWidth())
                    tokens = label.text().split()
                    required_width = max(
                        80,
                        max((label.fontMetrics().horizontalAdvance(token) + 4 for token in tokens), default=80),
                    )
                    self.assertGreaterEqual(label.width(), required_width)
                    bounds = label.fontMetrics().boundingRect(
                        label.contentsRect(), Qt.TextFlag.TextWordWrap, label.text(),
                    )
                    self.assertGreaterEqual(
                        label.height(), bounds.height(),
                        f'{language=} {font_size=} {label.text()=} {label.geometry()=} {bounds=}',
                    )
                    self.assertTrue(
                        label.contentsRect().contains(bounds),
                        f'{language=} {font_size=} {label.text()=} {label.geometry()=} {bounds=}',
                    )

                for edit in (panel.folder_path, panel.docx_path, panel.svn_url, panel.output_dir):
                    row = edit.parentWidget()
                    row_rect = QRect(QPoint(0, 0), row.size())
                    for index in range(row.layout().count()):
                        child = row.layout().itemAt(index).widget()
                        if child is not None and child.isVisible():
                            self.assertTrue(row_rect.contains(child.geometry()))

                buttons = (panel.folder_browse, panel.folder_refresh, panel.folder_open,
                           panel.docx_browse, panel.svn_pull_btn,
                           panel.output_dir_browse, panel.output_dir_open)
                self.assertTrue(all(button.text().strip() for button in buttons))
                self.assertTrue(all(int(button.receivers(button.clicked)) > 0 for button in buttons))


if __name__ == '__main__':
    unittest.main()
