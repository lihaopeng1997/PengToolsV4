"""Pytest session bootstrap for stable headless PyQt6 tests."""

from __future__ import annotations

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 必须在任何 QApplication 实例化之前导入 QtWebEngineWidgets，以满足 Qt WebEngine 初始化顺序要求
try:
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass

from PyQt6.QtWidgets import QApplication  # noqa: E402


_SESSION_APP: QApplication | None = None


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    """Keep one QApplication alive for the entire test process."""
    global _SESSION_APP
    _SESSION_APP = QApplication.instance() or QApplication([])
