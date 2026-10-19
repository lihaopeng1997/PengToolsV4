"""Geometry and production-host checks for the Data Center Agent workspace."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

import config
from panels import ai_workbench_panel as ai_module
from panels.data_center_panel import DataCenterPanel
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui import layout_metrics as _lm
from ui.data_center.agent_panel import DataCenterAgentPanel
from ui.data_center.integration import DataCenterWorkbenchAgent


class DataCenterAgentLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="dc-agent-layout-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        config_dir = self.root / "config"
        harness_dir = config_dir / "harness"
        (harness_dir).mkdir(parents=True)
        profile = {
            "id": "fixture-sql",
            "name": "Temporary SQL",
            "dialect": "mysql",
            "provider": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "user": "fixture",
            "database": "fixture_db",
            "environment": "local",
            "readonly": True,
        }
        self.profile = profile
        (harness_dir / "connections.json").write_text(
            json.dumps({"items": [profile]}, ensure_ascii=False), encoding="utf-8"
        )
        (config_dir / "ai_local.json").write_text(
            json.dumps(
                {
                    "active_model_id": "fixture-model",
                    "items": [
                        {
                            "id": "fixture-model",
                            "name": "Fixture model",
                            "model": "fixture-model",
                            "base_url": "http://127.0.0.1:9/v1",
                            "enabled": True,
                            "capability": "text_only",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (config_dir / "settings.json").write_text(
            json.dumps({"font_size": 13, "ui_theme": "calm", "ui_density": "compact"}),
            encoding="utf-8",
        )
        config_values = {
            "CONFIG_DIR": str(config_dir),
            "SETTINGS_FILE": str(config_dir / "settings.json"),
            "LAYOUT_SPLITTERS_FILE": str(config_dir / "layout_splitters.json"),
            "HARNESS_DIR": str(harness_dir),
            "HARNESS_CONNECTIONS_FILE": str(harness_dir / "connections.json"),
            "AI_LOCAL_FILE": str(config_dir / "ai_local.json"),
            "SCHEMA_SNAPSHOT_DIR": str(config_dir / "schema_snapshots"),
            "AGENT_DIR": str(config_dir / "agent"),
            "AGENT_WORKSPACES_DIR": str(config_dir / "agent" / "workspaces"),
            "AGENT_INDEX_FILE": str(config_dir / "agent" / "index.json"),
        }
        self.config_patches = [patch.object(config, key, value) for key, value in config_values.items()]
        for active in self.config_patches:
            active.start()
            self.addCleanup(active.stop)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("database driver call is forbidden in the UI fixture")

        self.forbidden_driver = forbidden
        self.io_patches = [
            patch.object(ai_module, "load_connections", return_value=[profile]),
            patch.object(ai_module, "load_snapshot", return_value=None),
            patch.object(ai_module, "open_connection", side_effect=forbidden),
            patch.object(ai_module, "fetch_query_chunks", side_effect=forbidden),
            patch.object(ai_module, "scan_schema", side_effect=forbidden),
            patch.object(ai_module, "install_splitter_prefs", lambda *_args, **_kwargs: None),
            patch("config.save_settings", side_effect=forbidden),
            patch("config.save_layout_splitter", side_effect=forbidden),
        ]
        for active in self.io_patches:
            active.start()
            self.addCleanup(active.stop)

        catalog = WorkspaceCatalog(loader=lambda: [dict(profile)], saver=forbidden, deleter=forbidden)
        self.panel = DataCenterPanel("zh", catalog=catalog)
        self.panel.setFont(QFont("Microsoft YaHei UI", 13))
        self.panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.panel.resize(960, 640)
        self.panel.show()
        self.app.processEvents()
        self.workspace = self.panel._open_connection("fixture-sql")
        self.assertIsNotNone(self.workspace)
        self.host = self.workspace.host
        self.agent_host = self.host.data_center_agent
        self.agent_panel = self.agent_host.agent_panel
        self.assertIsInstance(self.agent_host, DataCenterWorkbenchAgent)
        self.assertIsInstance(self.agent_panel, DataCenterAgentPanel)
        self.assertIs(self.workspace.agent_widget, self.agent_host)
        self.assertLess(self.host.side_tabs.indexOf(self.agent_host), 0)
        self._wait_for_model_catalog()
        self.agent_host.mode_combo.setCurrentIndex(self.agent_host.mode_combo.findData("draft"))
        self._wait_until(lambda: self.agent_host._snapshot is not None)

    def tearDown(self):
        self.panel.agent_shutdown_cleanup()
        self.panel.hide()
        self.panel.deleteLater()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def _wait_for_model_catalog(self):
        self.assertTrue(self._wait_until(lambda: bool(self.agent_host._models)))
        self.assertEqual(self.agent_host.model_combo.count(), 1)

    def test_composer_is_fixed_below_channel_scroll_and_fits_narrow_viewport(self):
        panel = DataCenterAgentPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_display_context("zh", "draft")
        panel.resize(340, 360)
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel.prompt_input.isVisible())
        self.assertTrue(panel.send_button.isVisible())
        self.assertTrue(panel.stop_button.isVisible())
        self.assertEqual(panel.body_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(panel.body_scroll.verticalScrollBar().maximum(), 0)
        self.assertLess(
            panel.body_scroll.geometry().bottom(),
            panel.prompt_composer.geometry().top(),
        )
        self.assertLessEqual(
            panel.prompt_input.geometry().bottom(),
            panel.prompt_composer.rect().bottom(),
        )
        self.assertLessEqual(
            panel.send_button.geometry().right(),
            panel.prompt_composer.rect().right(),
        )
        for channel in (
            panel.answer_view,
            panel.evidence_view,
            panel.sql_view,
            panel.result_preview_view,
        ):
            content_y = channel.mapTo(panel.body_scroll.widget(), QPoint(0, 0)).y()
            panel.body_scroll.verticalScrollBar().setValue(content_y)
            self.app.processEvents()
            viewport_origin = channel.mapTo(panel.body_scroll.viewport(), QPoint(0, 0))
            channel_rect = QRect(viewport_origin, channel.size())
            self.assertTrue(
                channel_rect.intersects(panel.body_scroll.viewport().rect()),
                f"{channel.objectName()} must remain reachable by scrolling",
            )

    def test_same_production_host_moves_between_drawer_and_rail_without_reset(self):
        self.assertEqual(self.agent_host.mode_combo.currentData(), "draft")
        self.assertEqual(self.agent_panel._display_mode, "draft")
        self.assertIn("草稿", self.agent_panel.preview_badge.text())
        self.assertTrue(self.agent_panel.prompt_input.isEnabled())
        self.assertIsNotNone(self.agent_host._snapshot)
        self.agent_panel.prompt_input.setPlainText("preserve this draft prompt")
        self.agent_panel._append_channel("answer", "preserved answer", self.agent_panel.answer_view)
        controller = self.agent_host._controller
        snapshot = self.agent_host._snapshot
        self.assertIsNotNone(controller)

        self.panel.agent_btn.click()
        self.app.processEvents()
        self.assertTrue(self.workspace.agent_visible)
        self.assertEqual(self.workspace._agent_presentation, "drawer")
        self.assertTrue(self.host.isHidden())
        self.assertTrue(self.agent_host.isVisible())
        self.assertTrue(self.agent_panel.preview_badge.isVisible())
        self.assertTrue(self.agent_panel.prompt_input.isVisible())

        self.panel.resize(1160, 720)
        self.app.processEvents()
        self.assertGreaterEqual(
            self.workspace.view_splitter.width(),
            _lm.REQ_RIGHT_MIN + 340 + self.workspace.view_splitter.handleWidth(),
        )
        self.assertEqual(self.workspace._agent_presentation, "rail")
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.agent_host.isVisible())

        self.panel.agent_btn.click()
        self.app.processEvents()
        self.assertFalse(self.workspace.agent_visible)
        self.assertEqual(self.workspace._agent_presentation, "closed")
        self.assertTrue(self.host.isVisible())
        self.assertFalse(self.agent_host.isVisible())
        self.assertIs(self.agent_host._controller, controller)
        self.assertIs(self.agent_host._snapshot, snapshot)
        self.assertEqual(self.agent_panel.prompt_input.toPlainText(), "preserve this draft prompt")
        self.assertIn("preserved answer", self.agent_panel.answer_view.toPlainText())
        self.assertFalse(controller.is_running)

        self.panel.agent_btn.click()
        self.app.processEvents()
        self.assertTrue(self.workspace.agent_visible)
        self.assertIs(self.agent_host._controller, controller)
        self.assertIs(self.agent_host._snapshot, snapshot)

    def test_draft_copy_wraps_and_target_elides_with_full_tooltip(self):
        self.panel.set_language("zh")
        for width, height in ((960, 640), (1_160, 720)):
            with self.subTest(size=(width, height)):
                self.panel.resize(width, height)
                self.panel.apply_layout_mode("wide", False)
                self.app.processEvents()
                if not self.workspace.agent_visible:
                    self.panel.agent_btn.click()
                self.app.processEvents()

                subtitle = self.agent_panel._preview_subtitle
                full_subtitle = subtitle.toolTip()
                self.assertFalse(subtitle.wordWrap())
                self.assertGreater(subtitle.width(), 0)
                self.assertEqual(
                    subtitle.text(),
                    subtitle.fontMetrics().elidedText(
                        full_subtitle,
                        Qt.TextElideMode.ElideRight,
                        subtitle.contentsRect().width(),
                    ),
                    "the fixed draft explanation must be visibly elided to its real width",
                )
                context_rect = self.agent_panel._context_grid.geometry()
                self.assertGreaterEqual(
                    context_rect.top(),
                    subtitle.geometry().y() + subtitle.height(),
                    "the context row must begin after the one-line fixed explanation",
                )
                self.assertIn("人工检查", full_subtitle)

                target = self.agent_panel.target_value_label
                full_target = target.toolTip()
                self.assertIn("不访问数据库", full_target)
                self.assertFalse(target.wordWrap())
                expected = target.fontMetrics().elidedText(
                    full_target,
                    Qt.TextElideMode.ElideRight,
                    target.contentsRect().width(),
                )
                self.assertEqual(target.text(), expected)
                self.assertEqual(target.property("fullValueText"), full_target)

                composer = self.agent_panel.prompt_composer
                self.assertTrue(self.agent_panel.prompt_input.isVisible())
                self.assertTrue(self.agent_panel.send_button.isVisible())
                self.assertLessEqual(composer.geometry().bottom(), self.agent_panel.rect().bottom())
                self.assertLessEqual(
                    self.agent_panel.send_button.geometry().right(),
                    composer.rect().right(),
                )


if __name__ == "__main__":
    unittest.main()
