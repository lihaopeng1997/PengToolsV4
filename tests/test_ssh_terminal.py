# -*- coding: utf-8 -*-
"""Unit tests for SshTerminalWidget and _SshTerminalView (UI layer)."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QKeyEvent, QInputMethodEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui.ssh_terminal import SshTerminalWidget, _SshTerminalView


class SshTerminalWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = SshTerminalWidget()
        self.view = self.widget.view

    def tearDown(self):
        self.widget.detach()
        self.widget.deleteLater()

    def test_widget_initial_state(self):
        self.assertFalse(self.widget.shell_alive)
        self.assertIn('未连接', self.view._placeholder)

    def test_key_mapping_enter_backspace_delete(self):
        # Enter -> \r
        event_enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(self.view._map_key(event_enter), b'\r')

        # Backspace -> \x7f
        event_bs = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(self.view._map_key(event_bs), b'\x7f')

        # Delete -> \x1b[3~
        event_del = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(self.view._map_key(event_del), b'\x1b[3~')

    def test_key_mapping_arrows_normal_and_app_mode(self):
        event_up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        # Default normal mode
        self.assertEqual(self.view._map_key(event_up), b'\x1b[A')

        # Switch to application cursor mode
        self.view._emulator.application_cursor_keys = True
        self.assertEqual(self.view._map_key(event_up), b'\x1bOA')

    def test_key_mapping_ctrl_combinations(self):
        # Ctrl+C -> \x03
        event_ctrl_c = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view._map_key(event_ctrl_c), b'\x03')

        # Ctrl+D -> \x04
        event_ctrl_d = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view._map_key(event_ctrl_d), b'\x04')

        # Ctrl+Z -> \x1a
        event_ctrl_z = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view._map_key(event_ctrl_z), b'\x1a')

        # Ctrl+L -> \x0c
        event_ctrl_l = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view._map_key(event_ctrl_l), b'\x0c')

    def test_key_mapping_alt_combination(self):
        # Alt+f -> \x1bf
        event_alt_f = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier, 'f')
        self.assertEqual(self.view._map_key(event_alt_f), b'\x1bf')

    def test_chinese_ime_input_method_event(self):
        mock_shell = MagicMock()
        mock_shell.alive = True
        self.view._shell = mock_shell
        self.view._connected = True

        # Simulate IME commit "测试中文123"
        event = QInputMethodEvent('', [QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.TextFormat, 0, 0, None)])
        event.setCommitString('测试中文123')

        self.view.inputMethodEvent(event)
        mock_shell.send.assert_called_once_with('测试中文123'.encode('utf-8'))

    def test_paste_normal_and_bracketed_paste(self):
        mock_shell = MagicMock()
        mock_shell.alive = True
        self.view._shell = mock_shell
        self.view._connected = True

        QApplication.clipboard().setText('echo 1\r\necho 2')

        # Normal paste
        self.view._paste_to_remote()
        mock_shell.send.assert_called_with(b'echo 1\recho 2')

        # Bracketed paste mode
        self.view._emulator.bracketed_paste_mode = True
        self.view._paste_to_remote()
        mock_shell.send.assert_called_with(b'\x1b[200~echo 1\recho 2\x1b[201~')

    def test_find_in_buffer(self):
        self.view._emulator.feed_text('Error: connection timed out\r\nWarning: retry in 5s\r\nError: second failure\r\n')

        # Search for 'Error'
        count = self.view.find_in_buffer('Error')
        self.assertEqual(count, 2)
        self.assertEqual(self.view._find_index, 0)

        # Search next
        count2 = self.view.find_in_buffer('Error')
        self.assertEqual(count2, 2)
        self.assertEqual(self.view._find_index, 1)

        # Search backward
        count3 = self.view.find_in_buffer('Error', backward=True)
        self.assertEqual(self.view._find_index, 0)

        # Clear
        self.view.clear_find_highlights()
        self.assertEqual(len(self.view._find_matches), 0)

    def test_session_generation_token_isolation(self):
        # 1. Attach session 1 (generation 1)
        self.view._session_generation = 1
        self.view._connected = True

        # Receive data with matching generation
        self.view._on_data(1, b'Session 1 data\r\n')
        self.assertIn('Session 1 data', self.view.toPlainText())

        # 2. Reconnect -> Generation becomes 2
        self.view.detach()
        self.view._session_generation = 2
        self.view._connected = True

        # Late data from session 1 arrives -> must be ignored
        self.view._on_data(1, b'Late data from session 1\r\n')
        self.assertNotIn('Late data from session 1', self.view.toPlainText())

        # Late closed from session 1 arrives -> must not disconnect session 2
        self.view._on_shell_closed(1)
        self.assertTrue(self.view._connected)

        # Late error from session 1 arrives -> must not append error
        self.view._on_shell_error(1, 'Old session error')
        self.assertNotIn('Old session error', self.view.toPlainText())


if __name__ == '__main__':
    unittest.main()
