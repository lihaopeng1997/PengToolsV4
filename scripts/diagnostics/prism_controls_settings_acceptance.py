"""Run a safe native-shell/settings-page acceptance fixture.

The fixture reuses the service-free ``MainWindow`` subclass from
``prism_visual_acceptance.py`` and mounts the real ``SettingsPanel``.  It
never reads or writes the user's settings, reminder store, model catalog,
Oracle configuration, project packs, or credentials.  The Web shell is
explicitly disabled so this runner only creates a real Windows Qt native
window.  Screenshots and the in-memory save receipts are written below this
script's evidence directory.

Run on a real Windows desktop, for example::

    python scripts/diagnostics/prism_controls_settings_acceptance.py
    python scripts/diagnostics/prism_controls_settings_acceptance.py --interactive

``--interactive`` captures first and then keeps the fixture open for manual
Computer Use inspection.  Closing the fixture quits only this QApplication;
it cannot close or signal the user's existing PengToolsHub process.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "scripts" / "diagnostics" / "prism_controls_settings_acceptance"
TITLE = "PengToolsHub — UI acceptance fixture"
CAPTURE_SIZES = ((1440, 900), (960, 640))
SECTION_SLUGS = (
    "appearance",
    "floating_toolbar",
    "reminders_closing",
    "security",
    "oracle_client",
    "intranet_models",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FAKE_REMINDER = {"enabled": True, "time": "16:45", "last_reminder_date": ""}
FAKE_MODEL_CATALOG = {
    "active_model_id": "fixture-chat",
    "items": [
        {
            "id": "fixture-chat",
            "name": "示例 · 晴空助手",
            "enabled": False,
            "base_url": "http://fixture.invalid/v1",
            "model": "fixture-chat",
            "available_models": ["fixture-chat", "fixture-review"],
            "timeout_seconds": 30,
            "ssl_verify": True,
            "supports_vision": False,
            "project_id": "fixture-project",
            "agent_capability": "text_only",
            "agent_reasoning_fields": ["reasoning_summary"],
            "agent_deadline_seconds": 20,
            "agent_max_reasoning_bytes": 64 * 1024,
            "agent_max_text_bytes": 128 * 1024,
            "agent_max_tool_argument_bytes": 32 * 1024,
            "agent_max_raw_buffer_bytes": 512 * 1024,
            "token": "",
        },
        {
            "id": "fixture-review",
            "name": "示例 · 文档校对",
            "enabled": False,
            "base_url": "http://fixture.invalid/v1",
            "model": "fixture-review",
            "available_models": ["fixture-review"],
            "timeout_seconds": 30,
            "ssl_verify": True,
            "supports_vision": False,
            "project_id": "fixture-project",
            "agent_capability": "text_only",
            "agent_reasoning_fields": [],
            "agent_deadline_seconds": 20,
            "agent_max_reasoning_bytes": 64 * 1024,
            "agent_max_text_bytes": 128 * 1024,
            "agent_max_tool_argument_bytes": 32 * 1024,
            "agent_max_raw_buffer_bytes": 512 * 1024,
            "token": "",
        },
    ],
}


def _fixture_settings() -> dict:
    """Return normalized settings without consulting the real config file."""

    from config import DEFAULT_SETTINGS

    result = dict(DEFAULT_SETTINGS)
    result.update(
        {
            "default_language": "zh",
            "ui_theme": "calm",
            "ui_density": "compact",
            "ui_web_shell": False,
            "home_username": "设置验收示例",
            "private_unlocked": False,
            "sidebar_collapsed": False,
            "floating_show_on_startup": False,
            "floating_always_on_top": False,
            "close_ask_each_time": False,
            "close_default_action": "exit",
            "oracle_client_mode": "auto",
            "oracle_home": "",
            "oracle_oci_lib": "",
        }
    )
    return result


def _import_safe_shell():
    """Load the production shell and the already reviewed safe subclass."""

    from scripts.diagnostics.prism_visual_acceptance import _fixture_main_window_class
    from main_window import MainWindow

    return MainWindow, _fixture_main_window_class(MainWindow)


class FixtureSettingsPanelMixin:
    """Replace every SettingsPanel data boundary with memory-only values."""

    def _fixture_init_state(self, recorder: dict) -> None:
        self._fixture_recorder = recorder
        self._fixture_reminder = copy.deepcopy(FAKE_REMINDER)
        self._fixture_catalog = copy.deepcopy(FAKE_MODEL_CATALOG)

    def _load_reminder_values(self):
        reminder = dict(self._fixture_reminder)
        self.reminder_enabled.blockSignals(True)
        self.reminder_enabled.setChecked(bool(reminder["enabled"]))
        self.reminder_enabled.blockSignals(False)
        if getattr(self, "_reminder_uses_timeedit", False):
            from PyQt6.QtCore import QTime

            hour, minute = (int(part) for part in str(reminder["time"]).split(":", 1))
            self.reminder_time.blockSignals(True)
            self.reminder_time.setTime(QTime(hour, minute))
            self.reminder_time.blockSignals(False)
        else:
            self.reminder_time.blockSignals(True)
            self.reminder_time.setValue(int(str(reminder["time"]).split(":", 1)[0]))
            self.reminder_time.blockSignals(False)

    def _load_ai_local_values(self):
        self._render_fixture_models()

    def _refresh_oracle_status(self):
        zh = self.language == "zh"
        self.oracle_status.setText(
            "Fixture：未读取本机 Oracle 路径；Thin 模式可直接使用。"
            if zh
            else "Fixture: local Oracle paths were not read; Thin mode is ready."
        )

    def _reload_harness_projects(self, current: str = "fixture-project"):
        self.ai_project.blockSignals(True)
        self.ai_project.clear()
        self.ai_project.addItem("示例项目（内存）", "fixture-project")
        self.ai_project.setCurrentIndex(0)
        self.ai_project.blockSignals(False)

    def _render_fixture_models(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidgetItem

        catalog = self._fixture_catalog
        active = str(catalog.get("active_model_id") or "")
        current_id = str(getattr(self, "_ai_editing_id", "") or active)
        self._ai_loading = True
        self.ai_list.blockSignals(True)
        self.ai_list.clear()
        selected = None
        for item in catalog.get("items", []):
            mark = "★ " if item.get("id") == active else ""
            label = f"{mark}{item.get('name') or ''} · {item.get('model') or ''}"
            row = QListWidgetItem(label)
            row.setToolTip(label)
            row.setData(Qt.ItemDataRole.UserRole, copy.deepcopy(item))
            self.ai_list.addItem(row)
            if str(item.get("id") or "") == current_id:
                selected = row
        if selected is None and self.ai_list.count():
            selected = self.ai_list.item(0)
        if selected is not None:
            self.ai_list.setCurrentItem(selected)
        self.ai_list.blockSignals(False)
        self._ai_loading = False
        if selected is not None:
            self._fill_fixture_model(selected.data(Qt.ItemDataRole.UserRole) or {})

    def _fill_fixture_model(self, raw: dict):
        cfg = dict(raw or {})
        self._ai_editing_id = str(cfg.get("id") or "")
        self.ai_name.setText(str(cfg.get("name") or ""))
        self.ai_enabled.setChecked(bool(cfg.get("enabled")))
        capability = self.ai_agent_capability.findData(cfg.get("agent_capability", "unknown"))
        self.ai_agent_capability.setCurrentIndex(max(0, capability))
        self.ai_agent_reasoning_fields.setText(", ".join(cfg.get("agent_reasoning_fields") or []))
        self.ai_agent_deadline_seconds.setValue(int(cfg.get("agent_deadline_seconds") or 20))
        for combo, value in (
            (self.ai_agent_max_reasoning_bytes, cfg.get("agent_max_reasoning_bytes")),
            (self.ai_agent_max_text_bytes, cfg.get("agent_max_text_bytes")),
            (self.ai_agent_max_tool_argument_bytes, cfg.get("agent_max_tool_argument_bytes")),
            (self.ai_agent_max_raw_buffer_bytes, cfg.get("agent_max_raw_buffer_bytes")),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)
        self._reload_harness_projects(str(cfg.get("project_id") or "fixture-project"))
        self.ai_base_url.setText(str(cfg.get("base_url") or ""))
        self.ai_model.blockSignals(True)
        self.ai_model.clear()
        for model in cfg.get("available_models") or []:
            self.ai_model.addItem(str(model))
        self.ai_model.setCurrentText(str(cfg.get("model") or ""))
        self.ai_model.blockSignals(False)
        self.ai_token.clear()
        self.ai_app_tag.clear()
        self.ai_timeout.setValue(int(cfg.get("timeout_seconds") or 30))
        self.ai_ssl_verify.setChecked(bool(cfg.get("ssl_verify", True)))
        self.ai_supports_vision.setChecked(bool(cfg.get("supports_vision", False)))
        self.ai_status.setText(
            "仅内存示例；探测与保存均已禁用网络和真实配置。"
            if self.language == "zh"
            else "Memory-only sample; probing and saving never use network or real config."
        )

    def _on_ai_item_changed(self, current, _previous=None):
        if self._ai_loading or current is None:
            return
        self._fill_fixture_model(current.data(32) or {})

    def _persist_reminder_settings(self, *, notify_ui=True, show_toast=True, draft=None):
        draft = dict(draft or {})
        self._fixture_reminder.update(
            {
                "enabled": bool(draft.get("enabled", self.reminder_enabled.isChecked())),
                "time": str(draft.get("time") or self._current_reminder_time_text()),
            }
        )
        self._fixture_recorder.setdefault("reminder_saves", []).append(dict(self._fixture_reminder))
        if notify_ui:
            self.reminder_settings_changed.emit(dict(self._fixture_reminder))
        return dict(self._fixture_reminder)

    def _save_reminder_settings(self):
        self._persist_reminder_settings(notify_ui=True, show_toast=False)
        self.ai_status.setText(
            "提醒已记录到 fixture 内存（未写入日报配置）。"
            if self.language == "zh"
            else "Reminder recorded in fixture memory; no daily-report file was written."
        )

    def _save_intranet_model(self):
        if self._settings_save_in_progress:
            return
        self._fixture_recorder.setdefault("model_saves", []).append(
            {
                "id": str(getattr(self, "_ai_editing_id", "")),
                "name": self.ai_name.text(),
                "model": self.ai_model.currentText(),
                "base_url": self.ai_base_url.text(),
                "enabled": self.ai_enabled.isChecked(),
            }
        )
        self.ai_status.setText(
            "模型设置已记录到 fixture 内存（未写入 ai_local.json）。"
            if self.language == "zh"
            else "Model settings recorded in fixture memory; ai_local.json was not written."
        )

    def _probe_intranet_model(self):
        self.ai_status.setText(
            "Fixture：未发起模型探测或网络请求。"
            if self.language == "zh"
            else "Fixture: no model probe or network request was made."
        )

    def _new_model_config(self):
        item = copy.deepcopy(FAKE_MODEL_CATALOG["items"][0])
        item.update(id="fixture-new", name="示例 · 新配置", model="fixture-new")
        self._fixture_catalog["items"].append(item)
        self._ai_editing_id = item["id"]
        self._render_fixture_models()

    def _copy_model_config(self):
        item = copy.deepcopy(FAKE_MODEL_CATALOG["items"][0])
        item.update(id="fixture-copy", name="示例 · 副本", model="fixture-copy")
        self._fixture_catalog["items"].append(item)
        self._ai_editing_id = item["id"]
        self._render_fixture_models()

    def _delete_model_config(self):
        target = str(getattr(self, "_ai_editing_id", "") or "")
        self._fixture_catalog["items"] = [
            item for item in self._fixture_catalog["items"] if str(item.get("id")) != target
        ]
        self._ai_editing_id = ""
        self._render_fixture_models()

    def _set_default_model_config(self):
        self._fixture_catalog["active_model_id"] = str(getattr(self, "_ai_editing_id", "") or "")
        self._render_fixture_models()

    def _install_harness_skill(self):
        self.ai_status.setText("Fixture：技能安装已禁用。")

    def _scan_project_tables(self):
        self.ai_status.setText("Fixture：项目扫描已禁用。")

    def _browse_oracle_home(self):
        self.oracle_status.setText("Fixture：未打开文件选择器；未读取本机路径。")

    def _browse_oracle_oci(self):
        self.oracle_status.setText("Fixture：未打开文件选择器；未读取本机路径。")

    def _reset_layout_prefs(self):
        self._fixture_recorder.setdefault("no_op_actions", []).append("reset_layout_prefs")
        self.oracle_status.setText("Fixture：布局复位为无业务 no-op。")


def _make_fixture_settings_panel(recorder: dict):
    from panels.settings_panel import SettingsPanel

    class FixtureSettingsPanel(FixtureSettingsPanelMixin, SettingsPanel):
        def __init__(self, settings, language="zh"):
            self._fixture_init_state(recorder)
            super().__init__(settings, language)

    return FixtureSettingsPanel


def _make_fixture_window(base_window, safe_window, recorder: dict):
    FixtureSettingsPanel = _make_fixture_settings_panel(recorder)

    class FixtureMainWindow(safe_window):
        def __init__(self):
            self._fixture_recorder = recorder
            self._fixture_report = None
            super().__init__()
            self.setWindowTitle(TITLE)
            self.title_bar.brand_name.setText(TITLE)
            self.title_bar.brand_name.setToolTip(TITLE)
            self._ensure_settings_panel()
            self._show_panel(7)

        def _ensure_services(self):
            self._fixture_recorder["services_started"] = False
            return None

        def _ensure_settings_panel(self):
            if self.settings_panel is not None:
                return self.settings_panel
            panel = FixtureSettingsPanel(self._settings, self.language)
            panel.settings_changed.connect(self._on_fixture_settings_changed)
            panel.reset_floating_position.connect(self._on_fixture_no_op)
            panel.layout_prefs_reset.connect(self._on_fixture_no_op)
            panel.floating_opacity_preview.connect(self._on_fixture_opacity_preview)
            self._mount_panel(7, panel)
            self.settings_panel = panel
            self._apply_panel_chrome(panel)
            return panel

        def _on_fixture_no_op(self):
            self._fixture_recorder.setdefault("no_op_actions", []).append("settings_signal")

        def _on_fixture_opacity_preview(self, value):
            self._fixture_recorder["last_opacity_preview"] = int(value)

        def _on_fixture_settings_changed(self, settings):
            payload = dict(settings or {})
            self._settings = payload
            self._fixture_recorder.setdefault("settings_saves", []).append(
                {
                    "font_size": payload.get("font_size"),
                    "ui_density": payload.get("ui_density"),
                    "default_language": payload.get("default_language"),
                    "close_default_action": payload.get("close_default_action"),
                    "persisted": False,
                }
            )
            if self.settings_panel is not None:
                self.settings_panel._set_settings_save_result(True)
            return True

        def _apply_settings(self, settings, *, persist=True):
            # Keep accidental callers on the production name inside memory.
            return self._on_fixture_settings_changed(settings)

        def _write_fixture_report(self):
            report = self._fixture_report
            if not isinstance(report, dict):
                return
            report["receipts"] = copy.deepcopy(self._fixture_recorder)
            report_path = report.get("report_path")
            if report_path:
                Path(report_path).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )

        def closeEvent(self, event):  # noqa: N802
            self._fixture_recorder["close"] = {
                "accepted": True,
                "mechanism": "fixture in-memory closeEvent",
                "qapplication_only": True,
            }
            event.accept()
            self._write_fixture_report()
            app = self.windowHandle() and self.windowHandle().screen()
            del app  # Keep this branch explicit: no process or native PID is touched.
            from PyQt6.QtWidgets import QApplication

            QApplication.instance().quit()

    del base_window  # The type is retained through safe_window's MRO only.
    return FixtureMainWindow


def _prepare_native_app():
    from PyQt6.QtCore import QStandardPaths, Qt
    from PyQt6.QtWidgets import QApplication

    if sys.platform != "win32":
        raise RuntimeError(f"需要真实 Windows Qt，当前平台为 {sys.platform}")
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        raise RuntimeError("拒绝 offscreen Qt；fixture 需要真实 Windows native shell")
    QStandardPaths.setTestModeEnabled(True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(["prism-controls-settings-acceptance"])
    if app.platformName().lower() != "windows":
        raise RuntimeError(f"需要 Windows QPA，当前为 {app.platformName()}")
    return app


def _wait_ms(milliseconds: int) -> None:
    from PyQt6.QtTest import QTest

    QTest.qWait(max(0, int(milliseconds)))


def _save_capture(window, app, panel, width: int, height: int, section_index: int, destination: Path) -> dict:
    panel.apply_layout_mode("standard" if width >= 1280 else "narrow", height < 720)
    window.resize(width, height)
    window._layout_controller.force(width, height)
    app.processEvents()
    if panel.section_nav.isVisible():
        panel.section_nav.setCurrentRow(section_index)
    else:
        panel.section_picker.setCurrentIndex(section_index)
    scroll = panel.sections_stack.widget(section_index)
    if scroll is not None and hasattr(scroll, "verticalScrollBar"):
        scroll.verticalScrollBar().setValue(0)
    app.processEvents()
    _wait_ms(100)
    if panel.sections_stack.currentIndex() != section_index:
        raise AssertionError(f"settings section did not select {section_index}")
    slug = SECTION_SLUGS[section_index]
    path = destination / f"settings_{slug}_{width}x{height}.png"
    pixmap = window.grab()
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"截图保存失败: {path}")
    workspace = window.findChild(__import__("PyQt6.QtWidgets", fromlist=["QWidget"]).QWidget, "settings-workspace")
    return {
        "section_index": section_index,
        "section_slug": slug,
        "section_label": panel.section_picker.itemText(section_index),
        "requested_logical_size": [width, height],
        "actual_window_logical_size": [int(window.width()), int(window.height())],
        "layout_mode": str(window._layout_controller.mode),
        "section_nav_visible": bool(panel.section_nav.isVisible()),
        "section_picker_visible": bool(panel.section_picker.isVisible()),
        "settings_workspace_width": int(workspace.width()) if workspace is not None else None,
        "settings_workspace_max_width": int(workspace.maximumWidth()) if workspace is not None else None,
        "screenshot": {"path": str(path), "width": int(pixmap.width()), "height": int(pixmap.height())},
    }


def _run_qtest_button_smoke(window, app, *, include_close: bool) -> dict:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    window.raise_()
    window.activateWindow()
    app.processEvents()
    result = {"maximize_restore": False, "minimize_restore": False, "close": None}
    QTest.mouseClick(window.title_bar.maximize_button, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    if not window.isMaximized():
        raise AssertionError("QTest maximize click did not maximize fixture")
    QTest.mouseClick(window.title_bar.maximize_button, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    if window.isMaximized():
        raise AssertionError("QTest restore click did not restore fixture")
    result["maximize_restore"] = True
    QTest.mouseClick(window.title_bar.minimize_button, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    if not window.isMinimized():
        raise AssertionError("QTest minimize click did not minimize fixture")
    window.showNormal()
    app.processEvents()
    result["minimize_restore"] = True
    if include_close:
        QTest.mouseClick(window.title_bar.close_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        close_receipt = window._fixture_recorder.get("close")
        if not close_receipt or not close_receipt.get("qapplication_only"):
            raise AssertionError("fixture closeEvent did not record an in-memory receipt")
        result["close"] = True
    else:
        result["close"] = "manual interactive check pending"
    return result


def run_acceptance(*, capture_dir: str | os.PathLike[str] | None = None, interactive: bool = False) -> dict:
    destination = (Path(capture_dir) if capture_dir else EVIDENCE_DIR).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "logs").mkdir(parents=True, exist_ok=True)
    from ui import web_diagnostics
    from ui.theme_manager import ThemeManager

    previous_log_dir = getattr(web_diagnostics, "_LOG_DIR", None)
    web_diagnostics.set_log_dir(str(destination / "logs"))
    recorder = {"services_started": False, "network_attempts": 0}
    report = None
    window = None
    app = None
    settings = _fixture_settings()

    def blocked_network(*_args, **_kwargs):
        recorder["network_attempts"] += 1
        raise RuntimeError("fixture forbids network")

    def blocked_write(*_args, **_kwargs):
        recorder["production_write_attempts"] = recorder.get("production_write_attempts", 0) + 1
        raise RuntimeError("fixture forbids production config writes")

    safe_env = dict(os.environ)
    safe_env.pop("PENGTOOLS_SYNC_BOOT", None)
    try:
        with patch.dict(os.environ, safe_env, clear=True):
            app = _prepare_native_app()
            MainWindow, SafeMainWindow = _import_safe_shell()
            FixtureMainWindow = _make_fixture_window(MainWindow, SafeMainWindow, recorder)
            import config
            from panels import settings_panel
            from ui import web_shell

            with patch.object(web_shell, "runtime_web_shell_available", return_value=False), \
                    patch("main_window.load_settings", return_value=settings), \
                    patch.object(config, "save_settings", side_effect=blocked_write), \
                    patch.object(settings_panel, "save_settings", side_effect=blocked_write), \
                    patch.object(MainWindow, "_show_startup_loading", return_value=None), \
                    patch("socket.socket.connect", side_effect=blocked_network), \
                    patch("socket.create_connection", side_effect=blocked_network):
                ThemeManager.instance().apply(app, "calm", font_size=12)
                window = FixtureMainWindow()
                window.setAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_DeleteOnClose, True)
                window._fixture_report = report = {
                    "runner": "prism_controls_settings_acceptance",
                    "title": TITLE,
                    "platform": sys.platform,
                    "qt_platform": app.platformName(),
                    "headless": False,
                    "safety": {
                        "native_shell_only": True,
                        "embedded_web_enabled": False,
                        "web_shell_runtime_forced_off": True,
                        "user_settings_loaded": False,
                        "production_config_writes_blocked": True,
                        "network_blocked": True,
                        "web_diagnostics_log_dir": str(destination / "logs"),
                        "web_diagnostics_log_dir_isolated": getattr(web_diagnostics, "_LOG_DIR", None) == str(destination / "logs"),
                        "existing_user_pid_touched": False,
                        "close_scope": "fixture QApplication only",
                    },
                    "fixture": {
                        "settings": {"ui_theme": "calm", "ui_density": "compact", "ui_web_shell": False},
                        "fake_reminder": copy.deepcopy(FAKE_REMINDER),
                        "fake_model_ids": [item["id"] for item in FAKE_MODEL_CATALOG["items"]],
                        "business_panels_created": False,
                        "services_started": False,
                    },
                    "window": {
                        "title": window.windowTitle(),
                        "titlebar_text": window.title_bar.brand_name.text(),
                        "frameless": bool(window._window_frame.is_frameless()),
                        "web_shell_enabled": bool(getattr(window, "_web_shell_enabled", False)),
                        "chrome_widget_created": getattr(window, "_chrome_web", None) is not None,
                        "dashboard_web_created": getattr(window, "_dash_web", None) is not None,
                    },
                    "qtest": {},
                    "captures": [],
                    "report_path": str(destination / "report.json"),
                }
                window.show()
                window._layout_controller.force(1440, 900)
                app.processEvents()
                panel = window.settings_panel
                if panel is None or window._current_nav_index != 7:
                    raise AssertionError("real SettingsPanel was not mounted as the active page")
                report["qtest"] = _run_qtest_button_smoke(window, app, include_close=not interactive)
                if not window.isVisible():
                    raise AssertionError("fixture closed before captures")
                for width, height in CAPTURE_SIZES:
                    for section_index in range(panel.sections_stack.count()):
                        report["captures"].append(
                            _save_capture(window, app, panel, width, height, section_index, destination)
                        )
                report["window"].update(
                    {
                        "settings_panel_mounted": True,
                        "captured_section_count": panel.sections_stack.count(),
                        "actual_after_capture": [int(window.width()), int(window.height())],
                    }
                )
                window.resize(*CAPTURE_SIZES[0])
                window._layout_controller.force(*CAPTURE_SIZES[0])
                panel.section_nav.setCurrentRow(0)
                app.processEvents()
                window._write_fixture_report()
                if interactive:
                    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                    app.exec()
                else:
                    # The QTest close click already scheduled QApplication.quit;
                    # process one final turn so deletion is deterministic.
                    app.processEvents()
                window._write_fixture_report()
                return report
    finally:
        try:
            if window is not None and window.isVisible():
                window.close()
                if app is not None:
                    app.processEvents()
            if report is not None and report.get("report_path"):
                report["receipts"] = copy.deepcopy(recorder)
                Path(report["report_path"]).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        finally:
            web_diagnostics.set_log_dir(previous_log_dir)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", default=str(EVIDENCE_DIR), help="write PNGs, logs, and report.json here")
    parser.add_argument("--interactive", action="store_true", help="keep the captured fixture open for manual Qt/Computer Use inspection")
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(capture_dir=args.capture_dir, interactive=args.interactive)
    except Exception as exc:
        print(f"PENDING_ENVIRONMENT: {type(exc).__name__}: {exc}")
        return 2
    if not args.interactive:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
