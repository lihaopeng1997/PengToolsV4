# -*- coding: utf-8 -*-
"""Isolated preview for the Prism title bar and global module tabs.

This script creates a throwaway QMainWindow with the two standalone widgets.
It never imports ``main_window``, starts the single-instance guard, opens a
WebEngine view, reads settings, or touches user data.  It is intended for
visual review and offscreen screenshot capture before shell integration.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.module_tabs import ModuleTabs
from ui.prism_title_bar import PrismTitleBar


def _load_preview_font(app: QApplication, requested: str | Path | None = None) -> str | None:
    """Register a local CJK font for this preview process only.

    The product starts with ``Microsoft YaHei UI`` (see ``run.py`` and the
    calm QSS).  The offscreen Qt runtime used for screenshots may not expose
    that family through font fallback, so register the installed Windows font
    explicitly.  No font is copied into resources or changed system-wide.
    """

    windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates: list[Path] = []
    if requested:
        candidates.append(Path(requested))
    candidates.extend(
        (
            windows_dir / "Fonts" / "msyh.ttc",
            windows_dir / "Fonts" / "simsun.ttc",
            windows_dir / "Fonts" / "simhei.ttf",
            Path("C:/Windows/Fonts/msyh.ttc"),
        )
    )
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        return None
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    family = str(families[0]).strip() if families else ""
    if not family:
        return None
    app.setFont(QFont(family, 10))
    # Keep this diagnostic override local to the preview QApplication.  It
    # mirrors the startup QSS family without editing product style resources.
    app.setStyleSheet(app.styleSheet() + f"\nQWidget {{ font-family: '{family}'; }}\n")
    return family


class PrismShellPreview(QMainWindow):
    """Tiny host that exercises only local Qt window actions."""

    def __init__(self):
        super().__init__()
        self.setObjectName("prism-shell-preview")
        self.setWindowTitle("PengToolsHub · Prism shell preview")
        self.setMinimumSize(960, 640)
        self.resize(1180, 720)

        root = QWidget(self)
        root.setObjectName("preview-root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = PrismTitleBar(parent=root)
        self.tabs = ModuleTabs(parent=root)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.tabs)

        body = QWidget(root)
        body.setObjectName("preview-body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(32, 26, 32, 32)
        body_layout.setSpacing(10)
        heading = QLabel("晴空棱镜 · 顶部与模块标签预览", body)
        heading.setObjectName("preview-heading")
        detail = QLabel(
            "This isolated window demonstrates the calm title bar and navigation tabs.\n"
            "生产主窗将注入真实菜单，并通过 MainWindow 复用既有生命周期。",
            body,
        )
        detail.setObjectName("preview-detail")
        detail.setWordWrap(True)
        body_layout.addWidget(heading)
        body_layout.addWidget(detail)
        body_layout.addStretch(1)
        layout.addWidget(body, 1)
        self.setCentralWidget(root)

        # Use only local preview actions.  Production close wiring remains the
        # host's existing MainWindow.closeEvent path.
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self._toggle_maximized)
        self.title_bar.close_requested.connect(self.close)
        self.title_bar.drag_double_clicked.connect(self._toggle_maximized)
        self.title_bar.pin_toggled.connect(self._set_pinned)
        self.tabs.activate_requested.connect(self._show_tab)
        self.tabs.close_requested.connect(self._close_tab)

        for nav_index in (0, 10, 18, 23):
            self.tabs.open_nav(nav_index, activate=(nav_index == 0))
        self._show_tab(0)

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.title_bar.set_maximized(self.isMaximized())

    def _set_pinned(self, checked: bool):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(checked))
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_tab(self, nav_index: int):
        record = self.tabs.tab_record(nav_index)
        if record is not None:
            self.tabs.set_current_nav(nav_index)
            self.setWindowTitle(f"{record.title} · Prism shell preview")

    def _close_tab(self, nav_index: int):
        if nav_index == 0:
            return
        indices = list(self.tabs.open_nav_indices)
        try:
            at = indices.index(nav_index)
        except ValueError:
            return
        remaining = [value for value in indices if value != nav_index]
        target = remaining[max(0, min(at - 1, len(remaining) - 1))]
        self.tabs.remove_nav(nav_index)
        self._show_tab(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", help="save a screenshot to this path after the window is shown")
    parser.add_argument("--duration", type=int, default=0, help="close after N milliseconds (0 keeps the window open)")
    parser.add_argument("--font-path", help="optional local CJK font file used only by this preview")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)
    selected_font = _load_preview_font(app, args.font_path)
    print(f"preview_font={selected_font or 'system fallback'}")
    window = PrismShellPreview()
    window.show()
    app.processEvents()

    if args.screenshot:
        output = os.path.abspath(args.screenshot)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        window.grab().save(output)
        print(output)
    if args.duration > 0:
        QTimer.singleShot(args.duration, window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
