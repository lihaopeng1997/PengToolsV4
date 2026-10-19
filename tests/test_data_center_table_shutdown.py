"""Public shutdown snapshots must distinguish staged local edits from DB work."""

from __future__ import annotations

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFontDatabase, QFontMetrics
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from panels.data_center_panel import DataCenterPanel
from tools.data_center.workspace_catalog import WorkspaceCatalog
from ui.data_center.manual_query import (
    ManualShutdownSessionSnapshot,
    ManualShutdownSnapshot,
)


APP = QApplication.instance() or QApplication([])


def _dialog_action_button(dialog, action):
    return next(
        (
            button for button in dialog.findChildren(QPushButton)
            if button.property("workspaceDispositionAction") == action
        ),
        None,
    )


class TableShutdownSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
        self.font_id = QFontDatabase.addApplicationFont(str(self.font_path)) if self.font_path.is_file() else -1
        self.assertGreaterEqual(self.font_id, 0, "Microsoft YaHei UI must register for the Windows dialog capture")
        from ui.theme_manager import ThemeManager

        self.theme_manager = ThemeManager.instance()
        self.theme_manager.apply(APP, "calm")
        catalog = WorkspaceCatalog(
            loader=lambda: [],
            saver=lambda item, secret=None: dict(item),
            deleter=lambda _connection_id: None,
        )
        self.panel = DataCenterPanel("en", catalog=catalog)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        APP.processEvents()

    def test_snapshot_signature_includes_unapplied_local_change_count(self):
        base = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("tab-a", 1, "CLEAN", False, False),
        ), True)
        staged = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("tab-a", 1, "CLEAN", False, False, 2),
        ), False)

        self.assertNotEqual(
            self.panel._manual_snapshot_signature(base),
            self.panel._manual_snapshot_signature(staged),
        )

    def _exercise_visible_dialog(self, snapshot, *, language, pixel_size, scenario, dismiss_by_escape=False):
        self.panel.language = language
        self.panel.resize(640, 360)
        self.panel.show()
        self.theme_manager.apply(APP, "calm", font_size=pixel_size)
        APP.processEvents()
        self.assertEqual(APP.property("ui_theme"), "calm")
        self.assertIn(f"font-size: {pixel_size}px", APP.property("base_stylesheet"))

        capture_dir = Path(
            os.environ.get(
                "PENGTOOLS_TABLE_CLOSE_CAPTURE_DIR",
                str(Path(tempfile.gettempdir()) / "pengtools-table-close-dialog-captures-20261006"),
            )
        )
        capture_dir.mkdir(parents=True, exist_ok=True)
        capture_name = f"{scenario}-{language}-{pixel_size}px.png"
        capture_path = capture_dir / capture_name
        observed = {}
        failures = []
        def inspect_visible_dialog():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QDialog):
                failures.append(f"expected visible active QDialog, got {type(dialog).__name__}")
                return
            APP.processEvents()
            if dialog.layout() is not None:
                dialog.layout().activate()
            APP.processEvents()
            observed["qpa_platform"] = APP.platformName()
            observed["theme"] = APP.property("ui_theme")
            observed["dialog_dpr"] = dialog.devicePixelRatioF()
            if os.environ.get("QT_QPA_PLATFORM", "").casefold() == "windows" and APP.platformName() != "windows":
                failures.append(f"expected Windows QPA platform, got {APP.platformName()}")
            observed["visible"] = dialog.isVisible()
            observed["size"] = (dialog.width(), dialog.height())
            observed["size_hint"] = (dialog.sizeHint().width(), dialog.sizeHint().height())
            observed["minimum_size_hint"] = (
                dialog.minimumSizeHint().width(),
                dialog.minimumSizeHint().height(),
            )
            label = dialog.findChild(QLabel, "workspace-manual-disposition-message")
            if label is None:
                failures.append("dialog body label missing")
            else:
                observed["body_text"] = label.text()
                observed["body_rect"] = label.geometry().getRect()
                observed["body_font_px"] = label.font().pixelSize()
                observed["body_font_family"] = label.font().family()
                if label.font().pixelSize() != pixel_size:
                    failures.append(f"body font is {label.font().pixelSize()}px, expected {pixel_size}px")
                if label.width() < 300:
                    failures.append(f"body label is too narrow: {label.width()}px")
                if label.height() < label.minimumSizeHint().height():
                    failures.append("body label is shorter than its wrapped-text minimum size hint")

            buttons = {
                name: _dialog_action_button(dialog, name)
                for name in ("commit", "rollback", "keep")
            }
            observed["buttons"] = {}
            for name, button in buttons.items():
                if button is None:
                    failures.append(f"{name} action missing")
                    continue
                rect = button.geometry()
                metrics = QFontMetrics(button.font())
                text_width = metrics.horizontalAdvance(button.text())
                observed["buttons"][name] = {
                    "text": button.text(),
                    "accessible_name": button.accessibleName(),
                    "tooltip": button.toolTip(),
                    "enabled": button.isEnabled(),
                    "rect": rect.getRect(),
                    "minimum_size_hint": (button.minimumSizeHint().width(), button.minimumSizeHint().height()),
                    "size_hint": (button.sizeHint().width(), button.sizeHint().height()),
                    "font_px": button.font().pixelSize(),
                    "font_family": button.font().family(),
                    "text_width": text_width,
                    "contents_width": button.contentsRect().width(),
                    "contents_height": button.contentsRect().height(),
                    "font_height": metrics.height(),
                }
                if not dialog.rect().contains(rect):
                    failures.append(f"{name} action lies outside the dialog: {rect.getRect()}")
                if button.width() < button.minimumSizeHint().width():
                    failures.append(f"{name} action is narrower than its minimumSizeHint")
                if text_width > button.contentsRect().width():
                    failures.append(f"{name} caption does not fit its content rect: {text_width}>{button.contentsRect().width()}")
                if metrics.height() > button.contentsRect().height():
                    failures.append(f"{name} caption is vertically clipped by its content rect")
            keep_button = buttons.get("keep")
            observed["default_action"] = bool(keep_button and keep_button.isDefault())
            pixmap = dialog.grab()
            observed["capture_saved"] = bool(pixmap.save(str(capture_path)))
            observed["capture_path"] = str(capture_path)
            if not observed["capture_saved"]:
                failures.append("could not save visible dialog capture")

            keep = buttons.get("keep")
            discard = buttons.get("rollback")
            if failures:
                dialog.reject()
            elif dismiss_by_escape:
                from PyQt6.QtCore import Qt
                from PyQt6.QtTest import QTest

                QTest.keyClick(dialog, Qt.Key.Key_Escape)
            elif scenario == "local-only":
                if discard is None or not discard.isEnabled():
                    failures.append("local discard action is unavailable")
                    dialog.reject()
                else:
                    discard.click()
            elif keep is not None:
                keep.click()
            else:
                dialog.reject()

        QTimer.singleShot(80, inspect_visible_dialog)
        action = self.panel._choose_workspace_manual_disposition(snapshot)
        observed["action"] = action
        observed["failures"] = list(failures)
        self.assertFalse(failures, "; ".join(failures))
        self.assertTrue(observed.get("visible"), "the dialog must be visibly shown before inspection")
        self.assertLessEqual(observed["size"][0], 460)
        self.assertGreaterEqual(observed["body_rect"][2], 300)
        self.assertTrue(observed.get("capture_saved"))
        self.assertEqual(observed["body_font_px"], pixel_size)
        self.assertEqual(observed["body_font_family"], "Microsoft YaHei UI")
        return observed

    def test_local_only_overlay_dialog_says_not_applied_and_cannot_commit(self):
        snapshot = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("tab-a", 1, "CLEAN", False, False, 2),
        ), False)
        captures = []
        for language in ("zh", "en"):
            for pixel_size in (17, 21):
                capture = self._exercise_visible_dialog(
                    snapshot,
                    language=language,
                    pixel_size=pixel_size,
                    scenario="local-only",
                )
                captures.append(capture)
                body = capture["body_text"]
                rollback = capture["buttons"]["rollback"]
                commit = capture["buttons"]["commit"]
                self.assertEqual(capture["action"], "rollback")
                self.assertIn("本地表格编辑" if language == "zh" else "local table edit", body)
                self.assertIn("尚未写入数据库" if language == "zh" else "not written to the database", body)
                self.assertIn("关闭不会自动应用" if language == "zh" else "Closing does not apply", body)
                self.assertIn("本地" if language == "zh" else "local", rollback["tooltip"])
                self.assertFalse(commit["enabled"])
        capture_dir = Path(captures[0]["capture_path"]).parent
        (capture_dir / "local-only-manifest.json").write_text(
            json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_unknown_transaction_and_local_overlay_have_distinct_rollback_copy(self):
        snapshot = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("tab-a", 1, "UNKNOWN", False, False, 1),
        ), False)
        captures = []
        for language in ("zh", "en"):
            for pixel_size in (17, 21):
                escape = language == "en" and pixel_size == 21
                capture = self._exercise_visible_dialog(
                    snapshot,
                    language=language,
                    pixel_size=pixel_size,
                    scenario="unknown-local",
                    dismiss_by_escape=escape,
                )
                captures.append(capture)
                body = capture["body_text"]
                rollback = capture["buttons"]["rollback"]
                commit = capture["buttons"]["commit"]
                self.assertIsNone(capture["action"])
                self.assertIn("结果未知" if language == "zh" else "UNKNOWN", body)
                self.assertIn("回滚" if language == "zh" else "rollback", body)
                self.assertIn("无法证明" if language == "zh" else "cannot prove", body)
                self.assertIn("本地" if language == "zh" else "local", rollback["tooltip"])
                self.assertTrue(capture["default_action"])
                self.assertFalse(commit["enabled"])
        capture_dir = Path(captures[0]["capture_path"]).parent
        (capture_dir / "unknown-local-manifest.json").write_text(
            json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_unknown_import_dialog_says_acknowledge_and_release_without_new_rollback(self):
        snapshot = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot(
                "import-tab", 1, "UNKNOWN", False, False, owner_kind="import",
            ),
        ), False)
        captures = []
        for language in ("zh", "en"):
            for pixel_size in (17, 21):
                capture = self._exercise_visible_dialog(
                    snapshot,
                    language=language,
                    pixel_size=pixel_size,
                    scenario="unknown-import",
                )
                captures.append(capture)
                body = capture["body_text"]
                rollback = capture["buttons"]["rollback"]
                self.assertIsNone(capture["action"])
                self.assertEqual(rollback["text"], "确认导入" if language == "zh" else "Ack import")
                self.assertIn(
                    "确认并释放" if language == "zh" else "acknowledge and release",
                    rollback["accessible_name"].casefold(),
                )
                self.assertIn("不会发送新的数据库回滚" if language == "zh" else "no new database rollback", body)
                self.assertIn("不能证明" if language == "zh" else "cannot prove", body)
                if language == "zh":
                    self.assertEqual(body.count("结果未知"), 1)
                    self.assertEqual(body.count("回滚"), 1)
                    self.assertEqual(body.count("确认"), 1)
                else:
                    self.assertEqual(body.count("UNKNOWN"), 1)
                    self.assertEqual(body.count("acknowledge and release"), 1)
                    self.assertEqual(body.count("database rollback"), 1)
                    self.assertEqual(body.count("cannot prove"), 1)
                self.assertIn(
                    "不会发送新的数据库回滚" if language == "zh" else "database rollback is sent",
                    rollback["tooltip"],
                )
                self.assertTrue(capture["default_action"])
                self.assertFalse(capture["buttons"]["commit"]["enabled"])

        mixed_snapshot = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("manual-tab", 2, "UNKNOWN", False, False, 2),
            ManualShutdownSessionSnapshot(
                "import-tab", 1, "UNKNOWN", False, False, owner_kind="import",
            ),
        ), False)
        mixed_captures = []
        for language in ("zh", "en"):
            for pixel_size in (17, 21):
                capture = self._exercise_visible_dialog(
                    mixed_snapshot,
                    language=language,
                    pixel_size=pixel_size,
                    scenario="unknown-import-mixed",
                )
                mixed_captures.append(capture)
                body = capture["body_text"]
                rollback = capture["buttons"]["rollback"]
                self.assertIsNone(capture["action"])
                self.assertEqual(
                    rollback["text"],
                    "回滚／确认／丢弃" if language == "zh" else "Rollback / ack / discard",
                )
                self.assertIn("手动事务结果未知：请求回滚" if language == "zh" else "Manual transaction outcome UNKNOWN", body)
                self.assertIn("尚未写入数据库" if language == "zh" else "are unsaved", body)
                self.assertIn("关闭不会自动应用" if language == "zh" else "will not auto-apply", body)
                if language == "zh":
                    self.assertIn("请求回滚其他事务", rollback["accessible_name"])
                    self.assertIn("确认并释放", rollback["accessible_name"])
                else:
                    self.assertIn("request rollback", rollback["accessible_name"].casefold())
                    self.assertIn("acknowledge and release", rollback["accessible_name"].casefold())
                self.assertTrue(capture["default_action"])
                if language == "zh":
                    self.assertEqual(body.count("结果未知"), 2)
                    self.assertEqual(body.count("新的数据库回滚"), 1)
                    self.assertEqual(body.count("不能证明"), 2)
                else:
                    self.assertEqual(body.count("UNKNOWN"), 2)
                    self.assertEqual(body.count("new database rollback"), 1)
                    self.assertEqual(body.count("cannot prove"), 2)
        mixed_no_local_snapshot = ManualShutdownSnapshot((
            ManualShutdownSessionSnapshot("manual-tab", 2, "UNKNOWN", False, False),
            ManualShutdownSessionSnapshot(
                "import-tab", 1, "UNKNOWN", False, False, owner_kind="import",
            ),
        ), False)
        mixed_no_local_captures = []
        for language in ("zh", "en"):
            for pixel_size in (17, 21):
                capture = self._exercise_visible_dialog(
                    mixed_no_local_snapshot,
                    language=language,
                    pixel_size=pixel_size,
                    scenario="unknown-import-mixed-no-local",
                )
                mixed_no_local_captures.append(capture)
                self.assertEqual(
                    capture["buttons"]["rollback"]["text"],
                    "回滚／确认" if language == "zh" else "Rollback / ack",
                )
                self.assertEqual(capture["body_text"].count("结果未知" if language == "zh" else "UNKNOWN"), 2)
                self.assertNotIn("local table edit" if language == "en" else "本地表格编辑", capture["body_text"])
        capture_dir = Path(captures[0]["capture_path"]).parent
        (capture_dir / "unknown-import-manifest.json").write_text(
            json.dumps(captures + mixed_captures + mixed_no_local_captures, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
