# -*- coding: utf-8 -*-
"""Unit tests for SshTerminalWidget and _SshTerminalView (UI layer)."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QKeyEvent, QInputMethodEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from PyQt6.QtGui import QFont, QFontInfo
from tools.terminal_emulator import Cell, CellStyle, DEFAULT_STYLE, TerminalEmulator
from ui.ssh_terminal import (
    SshTerminalWidget, _SshTerminalView, pick_terminal_font, terminal_cell_metrics,
    terminal_font_metrics, terminal_grid_size, build_row_foreground_runs,
    resolve_terminal_cell_style,
)


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

    def test_fixed_pitch_font_contract(self):
        font = pick_terminal_font(10)
        self.assertGreaterEqual(font.pointSize(), 8)
        self.assertLessEqual(font.pointSize(), 24)
        metrics = terminal_cell_metrics(font)
        self.assertGreater(metrics['cell_width'], 0)
        self.assertGreater(metrics['cell_height'], 0)
        self.assertLessEqual(metrics['cell_height'], font.pointSize() * 4)
        info = QFontInfo(self.view.font())
        self.assertTrue(info.fixedPitch() or self.view.font().fixedPitch())

    def test_grid_size_grows_and_never_zero(self):
        wide = terminal_grid_size(1000, 600, 10, 20)
        narrow = terminal_grid_size(400, 600, 10, 20)
        tiny = terminal_grid_size(1, 1, 10, 20)
        self.assertGreater(wide[0], narrow[0])
        self.assertGreaterEqual(wide[1], 1)
        self.assertGreaterEqual(tiny[0], 1)
        self.assertGreaterEqual(tiny[1], 1)
        self.assertNotIn('devicePixelRatio', terminal_grid_size.__code__.co_names)
        self.assertNotIn('devicePixelRatio', terminal_cell_metrics.__code__.co_names)

    def test_resize_updates_emulator_cols(self):
        cw, lh = self.view._cell_dimensions()
        cols_a, rows_a = terminal_grid_size(1000, 600, cw, lh)
        cols_b, rows_b = terminal_grid_size(400, 600, cw, lh)
        self.view.resize_pty(cols_a, rows_a)
        self.assertEqual(self.view._emulator.cols, cols_a)
        self.assertEqual(self.view._emulator.rows, rows_a)
        self.view.resize_pty(cols_b, rows_b)
        self.assertEqual(self.view._emulator.cols, cols_b)
        self.assertLess(cols_b, cols_a)
        self.assertGreaterEqual(cols_a, 1)
        self.assertGreaterEqual(rows_a, 1)

    def test_ascii_continuous_run_not_fragmented(self):
        """A. 相同 style 的连续 ASCII 文本应合并为连续 text run，绝不拆成 20 个单字符 run。"""
        text = "user@linux:~$ ls -la"
        row = [Cell(char=ch, width=1, style=DEFAULT_STYLE) for ch in text]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['kind'], 'ascii_run')
        self.assertEqual(runs[0]['text'], text)
        self.assertEqual(runs[0]['col'], 0)
        self.assertEqual(runs[0]['width'], len(text))

    def test_wide_cjk_properly_segments_runs(self):
        """B. 'abc中文def'：abc 与 def 为 ASCII run，中与文作为独立 wide cell。"""
        row = [
            Cell(char='a', width=1, style=DEFAULT_STYLE),
            Cell(char='b', width=1, style=DEFAULT_STYLE),
            Cell(char='c', width=1, style=DEFAULT_STYLE),
            Cell(char='中', width=2, style=DEFAULT_STYLE),
            Cell(char='', width=0, style=DEFAULT_STYLE),
            Cell(char='文', width=2, style=DEFAULT_STYLE),
            Cell(char='', width=0, style=DEFAULT_STYLE),
            Cell(char='d', width=1, style=DEFAULT_STYLE),
            Cell(char='e', width=1, style=DEFAULT_STYLE),
            Cell(char='f', width=1, style=DEFAULT_STYLE),
        ]
        runs = build_row_foreground_runs(row)
        # 应切分为 4 个部分：'abc', '中', '文', 'def'
        self.assertEqual(len(runs), 4)
        self.assertEqual(runs[0]['kind'], 'ascii_run')
        self.assertEqual(runs[0]['text'], 'abc')
        self.assertEqual(runs[0]['col'], 0)

        self.assertEqual(runs[1]['kind'], 'single')
        self.assertEqual(runs[1]['text'], '中')
        self.assertEqual(runs[1]['col'], 3)
        self.assertEqual(runs[1]['width'], 2)

        self.assertEqual(runs[2]['kind'], 'single')
        self.assertEqual(runs[2]['text'], '文')
        self.assertEqual(runs[2]['col'], 5)
        self.assertEqual(runs[2]['width'], 2)

        self.assertEqual(runs[3]['kind'], 'ascii_run')
        self.assertEqual(runs[3]['text'], 'def')
        self.assertEqual(runs[3]['col'], 7)

    def test_style_change_segments_runs(self):
        """C. abc + bold DEF + ghi：样式变化正确拆成 3 个 runs。"""
        s_normal = DEFAULT_STYLE
        s_bold = CellStyle(bold=True)
        row = [
            Cell(char='a', width=1, style=s_normal),
            Cell(char='b', width=1, style=s_normal),
            Cell(char='c', width=1, style=s_normal),
            Cell(char='D', width=1, style=s_bold),
            Cell(char='E', width=1, style=s_bold),
            Cell(char='F', width=1, style=s_bold),
            Cell(char='g', width=1, style=s_normal),
            Cell(char='h', width=1, style=s_normal),
            Cell(char='i', width=1, style=s_normal),
        ]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0]['text'], 'abc')
        self.assertFalse(runs[0]['style'].bold)
        self.assertEqual(runs[1]['text'], 'DEF')
        self.assertTrue(runs[1]['style'].bold)
        self.assertEqual(runs[2]['text'], 'ghi')
        self.assertFalse(runs[2]['style'].bold)

    def test_trailing_wide_cell_zero_width_never_enters_ascii_run(self):
        """D. width=0 的占位 cell 绝不混入 ASCII text run。"""
        row = [
            Cell(char='你', width=2, style=DEFAULT_STYLE),
            Cell(char='', width=0, style=DEFAULT_STYLE),
            Cell(char='x', width=1, style=DEFAULT_STYLE),
        ]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]['text'], '你')
        self.assertEqual(runs[1]['text'], 'x')
        self.assertEqual(runs[1]['col'], 2)

    def test_cell_width_ratio_to_fixed_advance_contract(self):
        """E. cell_width 与 fixed-pitch '0' advance 的比例严格为 1.0，不被 'W' 异常放大。"""
        font = pick_terminal_font(10)
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(font)
        adv_0 = fm.horizontalAdvance('0')
        metrics = terminal_cell_metrics(font)
        ratio = metrics['cell_width'] / adv_0
        self.assertAlmostEqual(ratio, 1.0, delta=0.01)

        # 检查诊断信息 helper 正常工作且不空
        diag = terminal_font_metrics(font)
        self.assertEqual(diag['advance_0'], adv_0)
        self.assertEqual(diag['cell_width'], metrics['cell_width'])
        self.assertGreater(diag['cell_height'], 0)

    def test_terminal_grid_size_and_resize_consistency(self):
        """F. terminal_grid_size 仍使用相同 cell_width，Pty resize cols 语义一致。"""
        cw, lh = self.view._cell_dimensions()
        cols, rows = terminal_grid_size(800, 600, cw, lh)
        self.assertEqual(cols, (800 - 2 * 8) // cw)
        self.assertEqual(rows, (600 - 2 * 5) // lh)
        self.view.resize_pty(cols, rows)
        self.assertEqual(self.view._emulator.cols, cols)
        self.assertEqual(self.view._emulator.rows, rows)

    def test_reverse_style_segments_runs(self):
        """A. normal 'abc' + reverse 'DEF' + normal 'ghi' => 3 个独立 runs。"""
        s_normal = DEFAULT_STYLE
        s_rev = CellStyle(reverse=True)
        row = [
            Cell(char='a', width=1, style=s_normal),
            Cell(char='b', width=1, style=s_normal),
            Cell(char='c', width=1, style=s_normal),
            Cell(char='D', width=1, style=s_rev),
            Cell(char='E', width=1, style=s_rev),
            Cell(char='F', width=1, style=s_rev),
            Cell(char='g', width=1, style=s_normal),
            Cell(char='h', width=1, style=s_normal),
            Cell(char='i', width=1, style=s_normal),
        ]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0]['text'], 'abc')
        self.assertFalse(runs[0]['style'].reverse)
        self.assertEqual(runs[1]['text'], 'DEF')
        self.assertTrue(runs[1]['style'].reverse)
        self.assertEqual(runs[2]['text'], 'ghi')
        self.assertFalse(runs[2]['style'].reverse)

    def test_dim_style_segments_runs(self):
        """B. normal 与 dim 状态不同 => 不得合并。"""
        s_normal = DEFAULT_STYLE
        s_dim = CellStyle(dim=True)
        row = [
            Cell(char='a', width=1, style=s_normal),
            Cell(char='b', width=1, style=s_dim),
        ]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 2)
        self.assertFalse(runs[0]['style'].dim)
        self.assertTrue(runs[1]['style'].dim)

    def test_hidden_style_segments_runs(self):
        """C. normal 与 hidden 状态不同 => 不得合并。"""
        s_normal = DEFAULT_STYLE
        s_hidden = CellStyle(hidden=True)
        row = [
            Cell(char='a', width=1, style=s_normal),
            Cell(char='b', width=1, style=s_hidden),
        ]
        runs = build_row_foreground_runs(row)
        self.assertEqual(len(runs), 2)
        self.assertFalse(runs[0]['style'].hidden)
        self.assertTrue(runs[1]['style'].hidden)

    def test_resolve_terminal_cell_style_reverse_swap(self):
        """D. resolve style：normal fg/bg 正常，reverse 下 effective fg/bg 正确交换。"""
        # 1. 明确带前背景色
        s_normal = CellStyle(fg='#FF0000', bg='#00FF00')
        res_n = resolve_terminal_cell_style(s_normal, default_fg='#FFFFFF', default_bg='#000000')
        self.assertEqual(res_n['foreground'], '#FF0000')
        self.assertEqual(res_n['background'], '#00FF00')

        s_rev = CellStyle(fg='#FF0000', bg='#00FF00', reverse=True)
        res_r = resolve_terminal_cell_style(s_rev, default_fg='#FFFFFF', default_bg='#000000')
        self.assertEqual(res_r['foreground'], '#00FF00')
        self.assertEqual(res_r['background'], '#FF0000')

        # 2. 默认缺省颜色反色
        s_def_rev = CellStyle(reverse=True)
        res_dr = resolve_terminal_cell_style(s_def_rev, default_fg='#111111', default_bg='#222222')
        self.assertEqual(res_dr['foreground'], '#222222')
        self.assertEqual(res_dr['background'], '#111111')

    def test_hidden_effective_style_flag(self):
        """E. hidden: resolve_terminal_cell_style 标记 hidden=True，foreground render path 明确跳过不绘制。"""
        s_hidden = CellStyle(hidden=True)
        res = resolve_terminal_cell_style(s_hidden)
        self.assertTrue(res['hidden'])

        row = [Cell(char='X', width=1, style=s_hidden)]
        runs = build_row_foreground_runs(row)
        eff = resolve_terminal_cell_style(runs[0]['style'])
        self.assertTrue(eff['hidden'])

    def test_terminal_emulator_real_sgr_feeds_renderer(self):
        """F. 使用真实 TerminalEmulator 输入 ANSI SGR，验证真实 parser 生成的 CellStyle 正常被 renderer 解析。"""
        emu = TerminalEmulator(cols=80, rows=24)
        emu.feed_text("\x1b[7mREV\x1b[0m \x1b[8mSECRET\x1b[0m \x1b[2mDIM\x1b[0m")
        row = emu.screen.grid[0]

        runs = build_row_foreground_runs(row, default_fg='#E8EEF4')
        texts = [r['text'] for r in runs if r['text'].strip()]
        self.assertIn('REV', texts)
        self.assertIn('SECRET', texts)
        self.assertIn('DIM', texts)

        rev_run = next(r for r in runs if r['text'] == 'REV')
        self.assertTrue(rev_run['style'].reverse)
        eff_rev = resolve_terminal_cell_style(rev_run['style'], default_fg='#E8EEF4', default_bg='#121A22')
        self.assertEqual(eff_rev['foreground'], '#121A22')
        self.assertEqual(eff_rev['background'], '#E8EEF4')

        sec_run = next(r for r in runs if r['text'] == 'SECRET')
        self.assertTrue(sec_run['style'].hidden)
        eff_sec = resolve_terminal_cell_style(sec_run['style'])
        self.assertTrue(eff_sec['hidden'])

        dim_run = next(r for r in runs if r['text'] == 'DIM')
        self.assertTrue(dim_run['style'].dim)
        eff_dim = resolve_terminal_cell_style(dim_run['style'])
        self.assertTrue(eff_dim['dim'])


if __name__ == '__main__':
    unittest.main()

