"""Pytest session bootstrap for stable headless PyQt6 tests."""

from __future__ import annotations

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


_SESSION_APP: QApplication | None = None


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    """Keep one QApplication alive for the entire test process."""
    global _SESSION_APP
    _SESSION_APP = QApplication.instance() or QApplication([])
