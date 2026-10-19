"""Render the three inline interface dialogs with harmless local fixtures.

This preview never calls the dialog actions that mutate config or clear history.
It patches the interface config loader before the panel is created and replaces
QDialog.exec with a recorder, so each inline dialog can be inspected without
network access, user data, or a real save/cleanup confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PENGTOOLS_DISABLE_MOTION", "1")


def _fixture_config():
    return {
        "browser_path": "",
        "debug_port": 9222,
        "local_targets": [
            {
                "id": f"target-{index}",
                "name": f"本地演示环境 {index:02d} · " + "长环境标题 " * 3,
                "base_url": f"http://127.0.0.1:180{index:02d}/service/" + "long-path/" * 4,
            }
            for index in range(1, 81)
        ],
        "default_target_id": "target-01",
        "ie_proxy_port": 8899,
        "ie_certificate_thumbprint": "",
        "proxy_restore_snapshot": None,
        "recent_browser_paths": [],
        "ui_prefs": {},
        "url_filter_prefixes": [
            f"https://api.example.test/service/{index:02d}/" + "long-prefix/" * 5
            for index in range(1, 81)
        ],
    }


def _fixture_library():
    return {
        "history": [
            {
                "id": f"history-{index}",
                "method": "GET" if index % 2 else "POST",
                "url": "https://api.example.test/service/" + "long-path/" * 5 + f"item/{index}",
                "status": 200 if index % 3 else 500,
                "ts": "2026-09-15T09:00:00",
                "duration_ms": 120,
            }
            for index in range(1, 81)
        ],
        "apis": [],
        "categories": [{"id": "uncategorized", "name": "未分类"}],
        "last_mode": "history",
        "last_category_id": "uncategorized",
        "max_history": 100,
    }


def _run_one(panel, name, app):
    captured = {}

    def recorder(dialog):
        captured["dialog"] = dialog
        dialog.show()
        app.processEvents()
        return dialog.DialogCode.Rejected

    method = {
        "history-cleanup": panel._show_history_cleanup_dialog,
        "environment": panel._show_environment_config_dialog,
        "url-filter": panel._show_url_filter_config_dialog,
    }[name]
    with patch.object(type(panel).mro()[1], "exec", recorder):
        method()
    dialog = captured.get("dialog")
    if dialog is None:
        raise RuntimeError(f"dialog was not constructed: {name}")
    app.processEvents()
    return dialog


def _geometry_report(dialog, screen_width, screen_height):
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtWidgets import QAbstractScrollArea, QPushButton

    outside = []
    for button in dialog.findChildren(QPushButton):
        if not button.isVisible():
            continue
        ancestor = button.parentWidget()
        scrollable = False
        clipped = False
        while ancestor is not None and ancestor is not dialog:
            scrollable |= isinstance(ancestor, QAbstractScrollArea)
            clipped |= not ancestor.rect().contains(QRect(button.mapTo(ancestor, QPoint()), button.size()))
            ancestor = ancestor.parentWidget()
        if not scrollable and (clipped or not dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size()))):
            outside.append(button.objectName() or button.text())
    return {
        "size": [dialog.width(), dialog.height()],
        "screen": [screen_width, screen_height],
        "exceeds_available": dialog.width() > screen_width - 48 or dialog.height() > screen_height - 48,
        "outside_fixed_buttons": outside,
        "lists": [
            {
                "object_name": widget.objectName(),
                "count": widget.count(),
                "height": widget.height(),
                "vertical_scroll_max": widget.verticalScrollBar().maximum(),
            }
            for widget in dialog.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QListWidget"]).QListWidget)
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dialog", choices=("history-cleanup", "environment", "url-filter", "all"), default="all", nargs="?")
    parser.add_argument("--font", type=int, default=16)
    parser.add_argument("--screen-width", type=int, default=960)
    parser.add_argument("--screen-height", type=int, default=640)
    args = parser.parse_args()

    from PyQt6.QtWidgets import QApplication, QDialog
    from ui.theme_manager import ThemeManager

    app = QApplication(["prism-interface-dialog-preview"])
    ThemeManager.instance().apply(app, "calm", font_size=args.font)

    config = _fixture_config()
    library = _fixture_library()
    from panels.interface_debug_panel import InterfaceDebugPanel

    panel = None
    output_dir = ROOT / "docs/ui/prism-implementation-2026-09/luna-interface"
    output_dir.mkdir(parents=True, exist_ok=True)
    names = ("history-cleanup", "environment", "url-filter") if args.dialog == "all" else (args.dialog,)
    reports = []
    with patch("panels.interface_debug_panel.load_interface_debug_config", return_value=config), \
            patch("panels.interface_debug_panel.save_interface_debug_config", side_effect=AssertionError("preview must not save")), \
            patch("socket.socket.connect", side_effect=RuntimeError("interface dialog preview forbids network")):
        panel = InterfaceDebugPanel("zh")
        panel._rt_lib = library
        panel.rt_lib_mode.setCurrentIndex(1)
        panel._rt_lib_refresh_list()
        for name in names:
            dialog = _run_one(panel, name, app)
            report = {"dialog": name, "font": args.font, **_geometry_report(dialog, args.screen_width, args.screen_height)}
            image = output_dir / f"{name}-{args.font}.png"
            dialog.grab().save(str(image))
            report["image"] = image.name
            reports.append(report)
            dialog.reject()
            dialog.deleteLater()
            app.processEvents()
        panel.close()
        panel.deleteLater()
        app.processEvents()
    output = output_dir / f"report-{args.font}.json"
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    main()
