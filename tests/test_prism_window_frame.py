# -*- coding: utf-8 -*-
"""Focused tests for the isolated Prism native-frame adapter."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.prism_title_bar import PrismTitleBar
from ui.prism_window_frame import (
    HTBOTTOMRIGHT,
    HTCAPTION,
    HTCLIENT,
    HTCLOSE,
    HTLEFT,
    HTMAXBUTTON,
    HTMINBUTTON,
    HTTOPLEFT,
    WM_CLOSE,
    WM_NCHITTEST,
    WM_SYSCOMMAND,
    PrismWindowFrame,
    edge_for_position,
    hit_code_for_edges,
    screen_point_from_lparam,
    work_area_at,
)


_APP = QApplication.instance() or QApplication([])


class _FakeNativeMessage:
    def __init__(self, message: int):
        self.hwnd = 0
        self.message = message
        self.wParam = 0
        self.lParam = 0


class PrismWindowFrameTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.setObjectName("frame-test-host")
        self.host.resize(1000, 700)
        layout = QVBoxLayout(self.host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = PrismTitleBar(parent=self.host)
        layout.addWidget(self.title)
        body = QWidget(self.host)
        layout.addWidget(body, 1)
        self.host.show()
        _APP.processEvents()
        self.frame = PrismWindowFrame(self.host, self.title)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        _APP.processEvents()

    def test_negative_coordinates_and_fractional_dpr_map_to_expected_edges(self):
        rect = QRect(-1920, -120, 1280, 900)
        self.assertEqual(edge_for_position(QPoint(-1920, -120), rect), Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
        self.assertEqual(
            edge_for_position(QPoint(rect.right(), rect.bottom()), rect),
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        )
        # At 150% DPR an 8 logical-pixel edge is a 12-pixel native hit zone.
        self.assertEqual(
            edge_for_position(QPoint(rect.left() + 11, 0), rect, device_pixel_ratio=1.5),
            Qt.Edge.LeftEdge,
        )
        self.assertEqual(
            edge_for_position(QPoint(rect.left() + 12, 0), rect, device_pixel_ratio=1.5),
            Qt.Edge(0),
        )
        self.assertEqual(hit_code_for_edges(Qt.Edge.LeftEdge | Qt.Edge.TopEdge), HTTOPLEFT)
        self.assertEqual(hit_code_for_edges(Qt.Edge.RightEdge | Qt.Edge.BottomEdge), HTBOTTOMRIGHT)

    def test_minimum_size_is_raised_without_lowering_existing_host_contract(self):
        self.host.setMinimumSize(1100, 720)
        self.frame.set_minimum_size(QSize(960, 640))
        self.assertEqual(self.host.minimumSize(), QSize(1100, 720))
        self.frame.set_minimum_size(QSize(1200, 760))
        self.assertEqual(self.host.minimumSize(), QSize(1200, 760))
        self.assertEqual(self.frame.minimum_size(), QSize(1200, 760))

    def test_frameless_flag_is_explicit_and_does_not_show_or_hide_host(self):
        self.assertFalse(self.frame.is_frameless())
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.frame.set_frameless(True))
        self.assertTrue(self.frame.is_frameless())
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.frame.set_frameless(False))
        self.assertFalse(self.frame.is_frameless())
        self.assertTrue(self.host.isVisible())

    def test_blank_titlebar_is_caption_but_window_buttons_are_native_codes(self):
        rect = self.host.frameGeometry()
        blank = self.title.mapToGlobal(QPoint(self.title.width() // 2, self.title.height() // 2))
        self.assertEqual(self.frame.hit_test(blank, window_rect=rect), HTCAPTION)
        self.assertEqual(
            self.frame.hit_test(self.title.minimize_button.mapToGlobal(self.title.minimize_button.rect().center()), window_rect=rect),
            HTMINBUTTON,
        )
        self.assertEqual(
            self.frame.hit_test(self.title.maximize_button.mapToGlobal(self.title.maximize_button.rect().center()), window_rect=rect),
            HTMAXBUTTON,
        )
        self.assertEqual(
            self.frame.hit_test(self.title.close_button.mapToGlobal(self.title.close_button.rect().center()), window_rect=rect),
            HTCLOSE,
        )

    def test_maximized_window_disables_edge_resize_but_keeps_controls(self):
        rect = QRect(-100, -80, 1000, 700)
        with patch.object(self.frame, "is_maximized", return_value=True):
            self.assertEqual(self.frame.edge_for_position(QPoint(rect.left(), rect.top()), window_rect=rect), Qt.Edge(0))
            self.assertFalse(self.frame.request_system_resize(Qt.Edge.LeftEdge))
            close_pos = self.title.close_button.mapToGlobal(self.title.close_button.rect().center())
            self.assertEqual(self.frame.hit_test(close_pos, window_rect=self.host.frameGeometry()), HTCLOSE)

    def test_titlebar_double_click_does_not_bubble_from_window_buttons(self):
        doubled = []
        self.title.drag_double_clicked.connect(lambda: doubled.append(True))
        QTest.mouseDClick(self.title, Qt.MouseButton.LeftButton, pos=QPoint(self.title.width() // 2, self.title.height() // 2))
        self.assertEqual(doubled, [True])
        QTest.mouseDClick(self.title.maximize_button, Qt.MouseButton.LeftButton)
        self.assertEqual(doubled, [True])

    def test_native_event_guard_does_not_consume_close_or_system_commands(self):
        self.frame.set_frameless(True)
        self.assertEqual(
            self.frame.native_event(b"windows_generic_MSG", _FakeNativeMessage(WM_CLOSE)),
            (False, 0),
        )
        self.assertEqual(
            self.frame.native_event(b"windows_generic_MSG", _FakeNativeMessage(WM_SYSCOMMAND)),
            (False, 0),
        )
        self.assertEqual(self.frame.native_event(b"windows_generic_MSG", 1), (False, 0))
        self.frame.set_native_enabled(False)
        self.assertEqual(
            self.frame.native_event(b"windows_generic_MSG", _FakeNativeMessage(WM_NCHITTEST)),
            (False, 0),
        )

    def test_lparam_decoding_preserves_negative_monitor_coordinates(self):
        # x=-16, y=-2 in signed 16-bit LOWORD/HIWORD form.
        lparam = (0xFFFE << 16) | 0xFFF0
        self.assertEqual(screen_point_from_lparam(lparam), QPoint(-16, -2))

    def test_work_area_adapter_returns_qrect_without_assuming_primary_origin(self):
        work = work_area_at(QPoint(-100, -100))
        self.assertIsNotNone(work)
        self.assertTrue(work.isValid())


if __name__ == "__main__":
    unittest.main()
