"""Real workbench Run, visible history, restore, and lifecycle integration."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont, QFontInfo
from PyQt6.QtWidgets import QApplication, QWidget

from tools.data_center.profile_host import DataCenterProfileHost
from tools.data_center.workspace_store import (
    HistoryRecord,
    WorkspaceStore,
    WorkspaceTabKind,
    WorkspaceTarget,
    WorkspaceTextKind,
)
from tests.test_data_center_manual_integration import _FakeConnection, _profile
from ui.data_center.query_history import QueryHistoryDialog
import ui.data_center.query_history_service as history_service


class AiWorkbenchHistoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pengtools-history-product-")
        self.history_path = os.path.join(self.tmp.name, "workspace.sqlite3")
        self.stack = ExitStack()
        if history_service._APPLICATION_HISTORY_SERVICE is not None:
            service = history_service._APPLICATION_HISTORY_SERVICE
            self.assertTrue(self._wait(service.drain), "previous test history service must finish before isolation")
            history_service.reset_application_history_service_for_tests()
        self.stack.enter_context(patch("config.DATA_CENTER_WORKSPACE_FILE", self.history_path))
        self.stack.enter_context(patch("config.load_layout_splitter", return_value=None))
        self.stack.enter_context(patch("config.save_layout_splitter"))
        self.profile = _profile()
        self.profiles = [self.profile]
        self.connections: list[_FakeConnection] = []
        self.connector_threads: list[int] = []
        self._ui_captures: list[dict[str, object]] = []
        self.block_query = True
        self.entered = threading.Event()
        self.release = threading.Event()
        self.panel = None
        self.profile_host = DataCenterProfileHost(
            connections_loader=lambda: {"items": [dict(row) for row in self.profiles]}
        )
        self.stack.enter_context(patch(
            "panels.ai_workbench_panel.load_connections",
            side_effect=lambda: [dict(row) for row in self.profiles],
        ))
        self.stack.enter_context(patch("panels.ai_workbench_panel.load_snapshot", return_value=None))
        self.stack.enter_context(patch("panels.ai_workbench_panel.is_enabled", return_value=False))
        self.stack.enter_context(patch("panels.ai_workbench_panel.load_ai_local", return_value={}))

    def _flush(self):
        self.app.processEvents()
        self.app.processEvents()

    def _wait(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._flush()
            try:
                if predicate():
                    return True
            except RuntimeError:
                pass
            time.sleep(0.002)
        self._flush()
        return bool(predicate())

    def _connector(self, profile, secret):
        self.assertEqual(secret, "TEMP_PLAINTEXT_SECRET_AT_DRIVER_BOUNDARY")
        self.connector_threads.append(threading.get_ident())
        username = str(profile.get("username") or "").strip().strip('"')
        connection = _FakeConnection(
            block_query=self.block_query,
            current_schema=username.upper() if profile.get("provider") == "oracle" else None,
        )
        connection.entered = self.entered
        connection.release = self.release
        self.connections.append(connection)
        return connection

    def _make_manual_panel(self, profile):
        from panels.ai_workbench_panel import AiWorkbenchPanel

        self.profile = profile
        self.profiles = [profile]
        self.panel = AiWorkbenchPanel(
            connection_id=profile["id"],
            dialect=str(profile.get("dialect") or "mysql"),
            data_center_agent_options={"profile_host": self.profile_host},
            manual_query_options={
                "profile_host": self.profile_host,
                "connector": self._connector,
                "secret_resolver": lambda _encrypted: "TEMP_PLAINTEXT_SECRET_AT_DRIVER_BOUNDARY",
                "page_size": 50,
            },
        )
        # Match DataCenterPanel._default_host_factory: relational workspaces
        # hide duplicated page chrome and use the embedded compact toolbar.
        self.panel.set_embedded_compact_mode(True)
        self.panel.resize(960, 640)
        self.panel.show()
        self._flush()
        return self.panel

    def _history_rows(self):
        store = WorkspaceStore(self.history_path)
        try:
            return store.search_history("")
        finally:
            store.close()

    def _set_pixel_font(self, root: QWidget, pixels: int):
        widgets = [root, *root.findChildren(QWidget)]
        saved = [(widget, widget.font()) for widget in widgets]
        for widget, font in saved:
            updated = QFont(font)
            updated.setPixelSize(pixels)
            widget.setFont(updated)
        if self.panel is not None:
            self.panel._refresh_risk_chip()
        self._flush()

        def restore():
            for widget, font in saved:
                try:
                    widget.setFont(font)
                except RuntimeError:
                    pass
            if self.panel is not None:
                self.panel._refresh_risk_chip()
            self._flush()

        return restore

    def _capture_widget(self, name: str, widget: QWidget, *, language: str, mode: str, pixels: int):
        capture_dir = os.environ.get("PENGTOOLS_HISTORY_CAPTURE_DIR")
        if not capture_dir:
            return
        os.makedirs(capture_dir, exist_ok=True)
        image_path = os.path.join(capture_dir, f"{name}.png")
        pixmap = widget.grab()
        saved = pixmap.save(image_path, "PNG")
        self.assertTrue(saved, f"could not capture visible Qt widget: {name}")
        screen = widget.screen()
        available = screen.availableGeometry() if screen is not None else None
        panel = self.panel
        history = getattr(panel, "history_btn", None)
        risk_chip = getattr(panel, "risk_chip", None)

        def font_rules(stylesheet: str) -> list[dict[str, object]]:
            rules = []
            for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", stylesheet or ""):
                match = re.search(
                    r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*px",
                    declarations,
                    flags=re.IGNORECASE,
                )
                if match:
                    rules.append({
                        "selectors": [selector.strip() for selector in selectors.split(",")],
                        "font_size_px": float(match.group(1)),
                    })
            return rules

        def selector_matches(child: QWidget, selector: str) -> bool:
            simple = re.sub(r"(?::[A-Za-z_-]+(?:\([^)]*\))?)+$", "", selector.strip())
            object_match = re.search(r"#([A-Za-z_][A-Za-z0-9_-]*)", simple)
            if object_match and child.objectName() != object_match.group(1):
                return False
            type_name = simple.split("#", 1)[0].strip()
            if not type_name or type_name == "QWidget":
                return True
            return type(child).__name__ == type_name

        app = QApplication.instance()
        app_stylesheet = app.styleSheet() if app is not None else ""

        def widget_font_metrics(root: QWidget) -> list[dict[str, object]]:
            metrics = []
            for child in (root, *root.findChildren(QWidget)):
                if not child.isVisible():
                    continue
                font = child.font()
                info = QFontInfo(font)
                applicable_rules = []
                ancestor = child
                while ancestor is not None:
                    scope = ancestor.objectName() or type(ancestor).__name__
                    for rule in font_rules(ancestor.styleSheet()):
                        for selector in rule["selectors"]:
                            if selector_matches(child, selector):
                                applicable_rules.append({
                                    "scope": scope,
                                    "selector": selector,
                                    "font_size_px": rule["font_size_px"],
                                })
                    ancestor = ancestor.parentWidget()
                for rule in font_rules(app_stylesheet):
                    for selector in rule["selectors"]:
                        if selector_matches(child, selector):
                            applicable_rules.append({
                                "scope": "QApplication",
                                "selector": selector,
                                "font_size_px": rule["font_size_px"],
                            })
                effective_size = (
                    applicable_rules[0]["font_size_px"]
                    if applicable_rules else info.pixelSize()
                )
                metrics.append({
                    "class": type(child).__name__,
                    "object_name": child.objectName(),
                    "visible": child.isVisible(),
                    "geometry": [child.x(), child.y(), child.width(), child.height()],
                    "widget_font": {
                        "family": font.family(),
                        "pixel_size": font.pixelSize(),
                        "point_size": font.pointSizeF(),
                        "description": font.toString(),
                    },
                    "font_info": {
                        "family": info.family(),
                        "pixel_size": info.pixelSize(),
                        "point_size": info.pointSizeF(),
                    },
                    # Qt style sheets can override QFont values. Preserve the
                    # applicable declarations separately instead of reporting
                    # the forced test QFont as the only rendered size.
                    "applicable_stylesheet_font_rules": applicable_rules,
                    "effective_font_size_px": effective_size,
                })
            return metrics

        menu_actions = []
        editor_menu = getattr(panel, "editor_more_menu", None)
        if editor_menu is not None and widget is panel:
            for action in editor_menu.actions():
                action_font = action.font()
                action_info = QFontInfo(action_font)
                menu_actions.append({
                    "text": action.text(),
                    "visible": action.isVisible(),
                    "enabled": action.isEnabled(),
                    "font": action_font.toString(),
                    "font_info": {
                        "family": action_info.family(),
                        "pixel_size": action_info.pixelSize(),
                        "point_size": action_info.pointSizeF(),
                    },
                })
        self._ui_captures.append({
            "name": name,
            "file": os.path.basename(image_path),
            "pixel_size": pixels,
            "language": language,
            "mode": mode,
            "widget_size": [widget.width(), widget.height()],
            "captured_pixel_size": [pixmap.width(), pixmap.height()],
            "device_pixel_ratio": widget.devicePixelRatioF(),
            "screen_available": (
                [available.x(), available.y(), available.width(), available.height()]
                if available is not None else None
            ),
            "history_button": (
                {
                    "visible": history.isVisible(),
                    "text": history.text(),
                    "geometry": [history.x(), history.y(), history.width(), history.height()],
                    "font": history.font().toString(),
                }
                if history is not None and widget is panel else None
            ),
            "read_only_badge": (
                {
                    "visible": risk_chip.isVisible(),
                    "text": risk_chip.text(),
                    "geometry": [risk_chip.x(), risk_chip.y(), risk_chip.width(), risk_chip.height()],
                    "font": risk_chip.font().toString(),
                    "text_width": risk_chip.fontMetrics().horizontalAdvance(risk_chip.text()),
                    "size_hint": [risk_chip.sizeHint().width(), risk_chip.sizeHint().height()],
                    "maximum_width": risk_chip.maximumWidth(),
                }
                if risk_chip is not None and widget is panel else None
            ),
            "child_widget_fonts_and_bounds": widget_font_metrics(widget),
            "editor_more_menu_actions": menu_actions,
        })
        if widget is panel:
            self.assertEqual(panel.size().width(), 960, f"{name} must use a 960 logical-pixel workbench")
            self.assertEqual(panel.size().height(), 640, f"{name} must use a 640 logical-pixel workbench")

    def _capture_panel_matrix(self, panel: QWidget, mode: str):
        for language in ("zh", "en"):
            panel.set_language(language)
            status = panel.result_status
            if mode == "stale_target":
                record = panel._current_tab()._history_target_record
                short_id = record.history_id[:10]
                reason = (
                    "版本变化，运行已禁用"
                    if language == "zh" else
                    "Revision changed; Run disabled"
                )
                self.assertTrue(status.text().startswith(reason), status.text())
                self.assertIn(short_id, status._label.text())
                self.assertTrue(status._label.text().startswith(reason), status._label.text())
                title = panel.sql_tabs.tabText(panel.sql_tabs.currentIndex())
                self.assertTrue(title.startswith("历史" if language == "zh" else "History"), title)
                identity = panel._history_target_identity(record)
                self.assertIn(record.history_id, status.toolTip())
                self.assertIn(record.target.profile_revision, status.toolTip())
                self.assertIn(record.target.database, status.toolTip())
                self.assertIn(record.target.schema, status.toolTip())
                self.assertIn(identity, status.accessibleDescription())
            elif language == "en":
                self.assertNotIn("未连接", status.text(), status.text())
                if mode == "normal_sql":
                    self.assertIn("Disconnected", status.text())
            for pixels in (17, 21):
                restore = self._set_pixel_font(panel, pixels)
                try:
                    expected_badge = "只读" if language == "zh" else "Read-only"
                    self.assertEqual(panel.risk_chip.text(), expected_badge)
                    self.assertGreaterEqual(
                        panel.risk_chip.width(),
                        panel.risk_chip.fontMetrics().horizontalAdvance(expected_badge),
                        "read-only badge must fit its localized text",
                    )
                    self._capture_widget(
                        f"{mode}_{language}_{pixels}px",
                        panel,
                        language=language,
                        mode=mode,
                        pixels=pixels,
                    )
                finally:
                    restore()

    def _finish_modal_history_restore(self, expected_history_id: str):
        observed: dict[str, object] = {}
        deadline = time.monotonic() + 4.0

        def choose_record():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QueryHistoryDialog) and dialog.isVisible():
                browser = dialog.browser
                for index in range(browser.record_list.count()):
                    item = browser.record_list.item(index)
                    record = item.data(256)  # Qt.UserRole
                    if getattr(record, "history_id", "") == expected_history_id:
                        browser.record_list.setCurrentRow(index)
                        observed.update({
                            "dialog_size": (dialog.width(), dialog.height()),
                            "dialog_font": dialog.font().toString(),
                            "restore_geometry": (
                                browser.restore_button.x(), browser.restore_button.y(),
                                browser.restore_button.width(), browser.restore_button.height(),
                            ),
                            "record_count": browser.record_list.count(),
                            "dialog_visible": dialog.isVisible(),
                        })
                        if os.environ.get("PENGTOOLS_HISTORY_CAPTURE_DIR"):
                            for pixels in (17, 21):
                                restore_font = self._set_pixel_font(dialog, pixels)
                                try:
                                    self._capture_widget(
                                        f"actual_history_dialog_en_{pixels}px",
                                        dialog,
                                        language="en",
                                        mode="actual-history-dialog",
                                        pixels=pixels,
                                    )
                                finally:
                                    restore_font()
                        browser.restore_button.click()
                        return
            if time.monotonic() >= deadline:
                if isinstance(dialog, QueryHistoryDialog):
                    dialog.reject()
                observed["timed_out"] = True
                return
            QTimer.singleShot(10, choose_record)

        QTimer.singleShot(0, choose_record)
        self.panel.history_btn.click()
        return observed

    def _drain_and_reopen_isolated_service(self):
        service = history_service._APPLICATION_HISTORY_SERVICE
        if service is None:
            return
        self.assertTrue(self._wait(service.drain), "history store must wait for every actual controller QThread.finished")
        self.assertTrue(service._store_closed)
        history_service.reset_application_history_service_for_tests()

    def tearDown(self):
        try:
            if self.panel is not None:
                host = getattr(self.panel, "manual_query_host", None)
                self.release.set()
                if host is not None:
                    self.assertTrue(self._wait(lambda: all(
                        not state.session.poll_cleanup().busy
                        and state.session.poll_cleanup().queued_operations == 0
                        for state in host._all_session_states()
                    )))
                    for state in host._all_session_states():
                        if state.busy:
                            # A deliberately disconnected/stale Qt delivery is
                            # view state only; the ManualSession owner is idle.
                            state.busy = False
                    host.manual_shutdown_cleanup("rollback")
                self.panel.workbench_shutdown_cleanup()
                self.assertTrue(self._wait(lambda: not getattr(self.panel, "_legacy_workers", set())))
                if host is not None:
                    from ui.data_center.manual_query import ManualShutdownStatus

                    self.assertTrue(self._wait(
                        lambda: host.manual_shutdown_cleanup() is ManualShutdownStatus.READY
                    ))
                self.panel.close()
                self.panel.deleteLater()
                self.panel = None
                self._flush()
            self._drain_and_reopen_isolated_service()
            capture_dir = os.environ.get("PENGTOOLS_HISTORY_CAPTURE_DIR")
            if capture_dir and self._ui_captures:
                with open(
                    os.path.join(capture_dir, "capture_manifest.json"), "w", encoding="utf-8"
                ) as stream:
                    json.dump(self._ui_captures, stream, ensure_ascii=False, indent=2, sort_keys=True)
        finally:
            # Settings/profile spies and the TEMP path remain active until all
            # QWidget owners and their real Qt worker threads have finished.
            self.stack.close()
            self.tmp.cleanup()

    def test_manual_run_freezes_oracle_service_target_and_actual_history_route_restores_dormant_tab(self):
        oracle = {
            "id": "temp-history-oracle",
            "name": "TEMP Oracle service target",
            "provider": "oracle",
            "engine": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "host": "127.0.0.1",
            "port": 1521,
            "username": "APP",
            "password": "TEMP_ENCRYPTED_SECRET",
            "database": "ORCL_SERVICE",
            "service_name": "ORCL_SERVICE",
        }
        panel = self._make_manual_panel(oracle)
        original_tab = panel._current_tab()
        original_tab.editor.setPlainText("SELECT SLOW FROM dual")

        self.assertTrue(panel.history_btn.isVisible(), "history must be visible in normal SQL mode")
        parent = panel.history_btn.parentWidget()
        self.assertIsNotNone(parent)
        self.assertTrue(parent.rect().contains(panel.history_btn.geometry()))
        self._capture_panel_matrix(panel, "normal_sql")
        panel.set_language("zh")
        panel.run_btn.click()
        self.assertTrue(self.entered.wait(2.0), "the actual Run button must dispatch into the fake provider")
        state = panel.manual_query_host._states_by_tab[id(original_tab)]
        panel.manual_query_host._bridge.completed.disconnect(panel.manual_query_host._deliver)
        original_tab.editor.setPlainText("SELECT EDITED AFTER DISPATCH FROM dual")
        panel._new_sql_tab("SELECT 2 FROM dual")
        self.release.set()

        service = history_service._APPLICATION_HISTORY_SERVICE
        self.assertIsNotNone(service, "the visible Run path should lazily create the app-owned service")
        self.assertTrue(self._wait(lambda: not service._manual_pending))
        self.assertTrue(self._wait(
            lambda: service._persistence_controller is not None
            and service._persistence_controller.pending_operations == 0
        ))
        self.assertTrue(state.busy, "the test disconnected UI delivery while app history completed independently")
        # The real operation Future has completed and the original tab is now
        # stale to the UI. Clear only that abandoned view flag so fixture
        # shutdown can exercise the real ManualSession close owner.
        state.busy = False
        panel.manual_query_host._bridge.completed.connect(panel.manual_query_host._deliver)
        rows = self._history_rows()
        self.assertEqual(len(rows), 1)
        record = rows[0]
        self.assertEqual(record.text, "SELECT SLOW FROM dual")
        self.assertEqual(record.target.connection_id, oracle["id"])
        self.assertTrue(record.target.profile_revision)
        self.assertEqual(record.target.database, "ORCL_SERVICE")
        self.assertEqual(record.target.schema, "APP")
        self.assertEqual(record.state_code, "COMPLETED")
        self.assertEqual(record.row_count, 1)
        self.assertEqual(record.evidence_summary, ())
        normal_zh_snapshot = {
            "panel": (panel.width(), panel.height()),
            "history": (
                panel.history_btn.x(), panel.history_btn.y(),
                panel.history_btn.width(), panel.history_btn.height(),
                panel.history_btn.font().toString(), panel.history_btn.text(),
            ),
        }

        # The product route remains visible while the SQL editor is collapsed
        # into table-focus layout, and the transaction controls retain their
        # existing toolbar owner.
        panel.set_language("en")
        panel.apply_layout_mode("compact", low_height=True, content_width=960)
        panel.manual_query_host._set_table_focus_active(True)
        self._flush()
        self.assertTrue(panel._embedded_compact_mode)
        self.assertEqual((panel.width(), panel.height()), (960, 640))
        self.assertTrue(panel.history_btn.isVisible())
        # 编辑器的 ⋯ 已并入工作台「更多」，工具条只留一个「更多」
        self.assertFalse(panel.editor_more_btn.isVisible())
        self.assertTrue(panel.manual_query_host.workbench_more_btn.isVisible())
        self.assertTrue(panel.manual_query_host._transaction_actions_in_toolbar)
        # 事务按钮留在同一工具条，只在有未提交事务时出现
        for button in (panel.manual_query_host.commit_btn, panel.manual_query_host.rollback_btn):
            self.assertEqual(button.isVisible(), button.isEnabled())
        self.assertTrue(panel.editor_more_menu.actions())
        self._capture_panel_matrix(panel, "compact_table_focus")
        compact_en_snapshot = {
            "panel": (panel.width(), panel.height()),
            "history": (
                panel.history_btn.x(), panel.history_btn.y(),
                panel.history_btn.width(), panel.history_btn.height(),
                panel.history_btn.font().toString(), panel.history_btn.text(),
            ),
            "transaction_toolbar": True,
        }
        with patch(
            "ui.data_center.manual_query.ManualSession",
            side_effect=AssertionError("history restore must not construct a session"),
        ):
            observed = self._finish_modal_history_restore(record.history_id)
        self.assertTrue(observed.get("dialog_visible"), f"actual history dialog snapshot: {observed}")
        restored = panel._current_tab()
        self.assertIsNot(restored, original_tab)
        self.assertTrue(restored._history_target_locked)
        self.assertEqual(restored._history_target_record.history_id, record.history_id)
        self.assertEqual(restored._history_target_record.target.profile_revision, record.target.profile_revision)
        self.assertTrue(panel.run_btn.isEnabled())
        self.assertNotIn(id(restored), panel.manual_query_host._states_by_tab)
        self.assertFalse(hasattr(restored, "manual_session"))
        self.assertEqual(len(self.connections), 1, "restore must not construct/connect another provider")
        panel.run_btn.click()
        self.assertTrue(self._wait(lambda: id(restored) in panel.manual_query_host._states_by_tab))
        self.assertTrue(self._wait(lambda: len(self.connections) == 2))
        restored_state = panel.manual_query_host._states_by_tab[id(restored)]
        self.assertEqual(restored_state.target.connection_id, record.target.connection_id)
        self.assertEqual(restored_state.target.profile_revision, record.target.profile_revision)
        self.assertEqual(restored_state.target.database, "ORCL_SERVICE")
        self.assertEqual(restored_state.target.schema, "APP")
        self.assertTrue(self._wait(lambda: not restored_state.busy))
        visible_results = panel.manual_query_host.result_model
        self.assertEqual(visible_results.rowCount(), 1)
        self.assertEqual(str(visible_results.data(visible_results.index(0, 0))), "42")
        self.assertEqual(str(visible_results.data(visible_results.index(0, 1))), "19.25")

        # A profile/service revision change is readable and cannot fall back to
        # the currently selected connection when explicit Run revalidates it.
        oracle["database"] = "ORCL_CHANGED"
        with patch(
            "ui.data_center.manual_query.ManualSession",
            side_effect=AssertionError("stale restore must remain dormant"),
        ):
            panel._restore_history_record(record)
            stale = panel._current_tab()
            self.assertTrue(stale._history_target_locked)
            self.assertFalse(stale._history_target_valid)
            panel.manual_query_host.run_current()
        self.assertTrue(stale._history_target_locked)
        self.assertFalse(stale._history_target_valid)
        self.assertFalse(panel.run_btn.isEnabled())
        self.assertEqual(stale._history_target_record.history_id, record.history_id)
        self.assertEqual(stale._history_target_record.target.connection_id, record.target.connection_id)
        self.assertEqual(stale._history_target_record.target.profile_revision, record.target.profile_revision)
        self.assertEqual(stale._history_target_record.target.database, record.target.database)
        self.assertEqual(stale._history_target_record.target.schema, record.target.schema)
        self.assertEqual(panel.manual_query_host.result_model.rowCount(), 0)
        self.assertNotIn(id(stale), panel.manual_query_host._states_by_tab)
        self.assertFalse(hasattr(stale, "manual_session"))
        self.assertTrue(stale._history_target_locked)
        self.assertFalse(stale._history_target_valid)
        self.assertIn(record.history_id[:10], panel.result_status.text())
        self.assertIn(record.history_id, panel.result_status.toolTip())
        self.assertIn(record.target.profile_revision, panel.result_status.toolTip())
        self.assertEqual(len(self.connections), 2)
        panel.sql_tabs.setCurrentWidget(restored)
        self._flush()
        self.assertEqual(panel.manual_query_host.result_model.rowCount(), 1)
        self.assertEqual(
            str(panel.manual_query_host.result_model.data(panel.manual_query_host.result_model.index(0, 0))),
            "42",
        )
        self.assertEqual(
            str(panel.manual_query_host.result_model.data(panel.manual_query_host.result_model.index(0, 1))),
            "19.25",
        )
        panel.sql_tabs.setCurrentWidget(stale)
        self._flush()
        self.assertEqual(panel.manual_query_host.result_model.rowCount(), 0)
        self.assertFalse(panel.run_btn.isEnabled())
        self.assertEqual(len(self.connections), 2)
        self._capture_panel_matrix(panel, "stale_target")

        self.profiles.clear()
        with patch(
            "ui.data_center.manual_query.ManualSession",
            side_effect=AssertionError("missing-profile restore must remain dormant"),
        ):
            panel._restore_history_record(record)
            missing = panel._current_tab()
            panel.manual_query_host.run_current()
        self.assertTrue(missing._history_target_locked)
        self.assertFalse(missing._history_target_valid)
        self.assertFalse(panel.run_btn.isEnabled())
        self.assertEqual(panel.manual_query_host.result_model.rowCount(), 0)
        self.assertNotIn(id(missing), panel.manual_query_host._states_by_tab)
        self.assertFalse(hasattr(missing, "manual_session"))
        self.assertIn(record.history_id[:10], panel.result_status.text())
        self.assertIn(record.history_id, panel.result_status.toolTip())
        self.assertTrue(panel.result_status.text().startswith("Profile missing; Run disabled"))
        self.assertIn(record.history_id[:10], panel.result_status._label.text())
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(missing._history_target_validation_code, "PROFILE_UNAVAILABLE")

        redis_profile = {
            **_profile(),
            "id": "temp-history-redis",
            "name": "TEMP Redis unsupported history target",
            "provider": "redis",
            "dialect": "redis",
            "mode": "standalone",
            "database": "0",
        }
        choice = self.profile_host.capture_connection(redis_profile)
        self.profiles[:] = [redis_profile]
        unsupported = HistoryRecord(
            history_id="temp-unsupported-history-id",
            target=WorkspaceTarget(
                tab_id="temp-unsupported-tab:1",
                connection_id=choice.connection_id,
                profile_revision=choice.revision,
                database=choice.database,
                schema="",
                tab_kind=WorkspaceTabKind.SQL,
            ),
            text="SELECT 1",
            text_kind=WorkspaceTextKind.SQL,
            created_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            state_code="COMPLETED",
        )
        panel._restore_history_record(unsupported)
        unsupported_tab = panel._current_tab()
        self.assertTrue(unsupported_tab._history_target_locked)
        self.assertFalse(unsupported_tab._history_target_valid)
        self.assertFalse(panel.run_btn.isEnabled())
        self.assertEqual(unsupported_tab._history_target_validation_code, "MANUAL_PROVIDER_UNSUPPORTED")
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(compact_en_snapshot["history"][2] > 0)
        print("HISTORY_UI_CAPTURE " + json.dumps({
            "normal_zh": normal_zh_snapshot,
            "compact_table_focus_en": compact_en_snapshot,
            "dialog": observed,
        }, ensure_ascii=False, sort_keys=True))
        panel.manual_query_host._set_table_focus_active(False)

    def test_legacy_run_sink_survives_ui_disconnect_and_writes_one_safe_record(self):
        from panels.ai_workbench_panel import AiWorkbenchPanel

        entered = threading.Event()
        release = threading.Event()
        fake_connection = _FakeConnection()
        panel = AiWorkbenchPanel(
            language="en",
            data_center_agent_options={"profile_host": self.profile_host},
        )
        self.panel = panel
        self.connections.append(fake_connection)
        self.stack.enter_context(patch("panels.ai_workbench_panel.open_connection", return_value=fake_connection))
        self.stack.enter_context(patch(
            "panels.ai_workbench_panel.run_console_statement",
            side_effect=lambda *_args, **_kwargs: (
                entered.set(), release.wait(3.0),
                {"status": "OK", "rowcount": 9, "rows": [["RAW RESULT MUST NOT BE SAVED"]]},
            )[-1],
        ))
        panel.resize(960, 640)
        panel.show()
        self._flush()
        tab = panel._current_tab()
        tab.editor.setPlainText("SELECT SLOW FROM sales")
        panel.run_btn.click()
        worker = panel._worker
        self.assertIsNotNone(worker)
        self.assertTrue(entered.wait(2.0), "the actual legacy Run button must dispatch")
        frozen_text = worker.kwargs["sql"]
        tab.editor.setPlainText("SELECT EDITED AFTER DISPATCH FROM sales")
        panel._disconnect_legacy_worker_ui(worker)
        release.set()

        self.assertTrue(self._wait(lambda: worker not in panel._legacy_workers))
        service = history_service._APPLICATION_HISTORY_SERVICE
        self.assertIsNotNone(service)
        self.assertTrue(self._wait(
            lambda: service._persistence_controller is not None
            and service._persistence_controller.pending_operations == 0
        ))
        rows = self._history_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].text, frozen_text)
        self.assertEqual(rows[0].state_code, "COMPLETED")
        self.assertEqual(rows[0].row_count, 9)
        self.assertEqual(rows[0].target.connection_id, self.profile["id"])
        self.assertTrue(rows[0].target.profile_revision)
        self.assertEqual(rows[0].evidence_summary, ())
        self.assertTrue(panel.history_btn.isVisible())


if __name__ == "__main__":
    unittest.main()
