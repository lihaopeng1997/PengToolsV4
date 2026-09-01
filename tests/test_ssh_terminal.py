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

    def test_system_message_isolation_contract(self):
        """append_system 绝不修改远端 screen grid、光标或状态机。"""
        emu = self.view._emulator
        emu.feed_text('Remote Shell 1\r\nRemote Shell 2')

        # Snapshot emulator state
        grid_before = [list(row) for row in emu.screen.grid]
        cursor_x_before = emu.screen.cursor_x
        cursor_y_before = emu.screen.cursor_y
        style_before = emu.current_style
        scroll_top_before = emu.screen.scroll_top
        scroll_bottom_before = emu.screen.scroll_bottom
        alt_before = emu.is_alternate_screen()
        state_before = emu._state

        # Call append_system
        self.view.append_system('[终端错误] Connection timed out')
        self.view.append_system('[会话已断开]')

        # Verify emulator state is 100% UNTOUCHED
        self.assertEqual(emu.screen.cursor_x, cursor_x_before)
        self.assertEqual(emu.screen.cursor_y, cursor_y_before)
        self.assertEqual(emu.current_style, style_before)
        self.assertEqual(emu.screen.scroll_top, scroll_top_before)
        self.assertEqual(emu.screen.scroll_bottom, scroll_bottom_before)
        self.assertEqual(emu.is_alternate_screen(), alt_before)
        self.assertEqual(emu._state, state_before)
        for r in range(len(grid_before)):
            for c in range(len(grid_before[r])):
                self.assertEqual(emu.screen.grid[r][c], grid_before[r][c])

        # Verify emulator plain text is unmodified
        self.assertEqual(emu.get_plain_text(), 'Remote Shell 1\nRemote Shell 2')
        # But UI status message is updated
        self.assertIn('会话已断开', self.view._system_status)

    def test_connected_clear_delegates_to_remote_ctrl_l_without_screen_mutation(self):
        """已连接时清屏：发送 Ctrl+L 到远端 PTY，本地绝不私自清屏或伪造 prompt。"""
        mock_shell = MagicMock()
        mock_shell.alive = True
        self.view._shell = mock_shell
        self.view._connected = True

        emu = self.view._emulator
        emu.feed_text('user@linux:~$ top output\r\nPID USER CPU\r\n')

        # Snapshot before clear
        grid_before = [list(row) for row in emu.screen.grid]
        cursor_x_before = emu.screen.cursor_x
        cursor_y_before = emu.screen.cursor_y
        text_before = emu.get_plain_text()

        # Trigger clear action
        self.view.clear_and_ready()

        # 1. Sent Ctrl+L to remote shell
        mock_shell.send.assert_called_once_with(b'\x0c')

        # 2. Before receiving remote echo, local emulator state is 100% untouched
        self.assertEqual(emu.get_plain_text(), text_before)
        self.assertEqual(emu.screen.cursor_x, cursor_x_before)
        self.assertEqual(emu.screen.cursor_y, cursor_y_before)
        self.assertNotIn('$', self.view._system_status)

        # 3. Simulate remote PTY echoing clear ANSI and redraw
        self.view._on_data(self.view._session_generation, b'\x1b[H\x1b[2Juser@linux:~$ ')
        self.assertEqual(self.view.toPlainText(), 'user@linux:~$')

    def test_disconnected_clear_clears_local_display(self):
        """未连接时 clear_and_ready 只清本地残余，绝不伪造远端 $ prompt。"""
        self.view._connected = False
        emu = self.view._emulator
        emu.feed_text('some leftover text\r\n')

        self.view.clear_and_ready()
        text = self.view.toPlainText()
        self.assertNotIn('$', text)
        self.assertEqual(text, '')

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

        # Late error from session 1 arrives -> must not set error in session 2
        self.view._system_status = ''
        self.view._on_shell_error(1, 'Old session error')
        self.assertNotIn('Old session error', self.view._system_status)


if __name__ == '__main__':
    unittest.main()
