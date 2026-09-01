# -*- coding: utf-8 -*-
"""Comprehensive tests for TerminalEmulator (ScreenModel) & ANSI / VT control sequences."""

import unittest
from tools.terminal_emulator import TerminalEmulator, char_width, CellStyle


class TerminalEmulatorTests(unittest.TestCase):
    def test_char_width_ascii_and_cjk(self):
        self.assertEqual(char_width('a'), 1)
        self.assertEqual(char_width('1'), 1)
        self.assertEqual(char_width(' '), 1)
        self.assertEqual(char_width('中'), 2)
        self.assertEqual(char_width('文'), 2)
        self.assertEqual(char_width('保'), 2)
        self.assertEqual(char_width(''), 0)
        self.assertEqual(char_width('\x00'), 0)
        self.assertEqual(char_width('\x1b'), 0)

    def test_incremental_utf8_split_chunks(self):
        emu = TerminalEmulator(cols=40, rows=10)
        chinese_bytes = '测试中文字符串'.encode('utf-8')
        # Split in the middle of a multi-byte character
        chunk1 = chinese_bytes[:5]
        chunk2 = chinese_bytes[5:]

        emu.feed_bytes(chunk1)
        emu.feed_bytes(chunk2)

        text = emu.get_plain_text()
        self.assertEqual(text, '测试中文字符串')

    def test_cr_overwrite_and_linefeed(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('Loading 10%\rLoading 50%\rLoading 100%\r\nDone!')
        lines = emu.get_plain_text().split('\n')
        self.assertEqual(lines[0], 'Loading 100%')
        self.assertEqual(lines[1], 'Done!')

    def test_backspace(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('abc\x08\x08XY')
        text = emu.get_plain_text()
        self.assertEqual(text, 'aXY')

    def test_cursor_positioning_and_sgr_consume(self):
        emu = TerminalEmulator(cols=40, rows=10)
        # SGR red text + bold + clear
        emu.feed_text('\x1b[31;1mRed Bold\x1b[0m Plain')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Red Bold Plain')
        self.assertNotIn('[31', text)
        self.assertNotIn('[0m', text)

        # Check cell styles
        row = emu.screen.grid[0]
        self.assertTrue(row[0].style.bold)
        self.assertEqual(row[0].style.fg, '#CD3131')
        self.assertFalse(row[9].style.bold)
        self.assertIsNone(row[9].style.fg)

    def test_cursor_movement_sequences(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('Line 1\r\nLine 2\r\nLine 3')
        # Move up 2 lines, move to column 10 (1-based: 10)
        emu.feed_text('\x1b[2A\x1b[10GX')
        row0 = ''.join(c.char for c in emu.screen.grid[0] if c.width != 0).rstrip()
        self.assertEqual(row0, 'Line 1   X')

    def test_erase_line_and_erase_display(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('Hello World\r\x1b[KGood')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Good')

        # Erase entire display
        emu.feed_text('\x1b[2J\x1b[HClean')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Clean')

    def test_osc_bel_terminator(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('\x1b]0;my_window_title\x07Visible Text')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Visible Text')
        self.assertNotIn('my_window_title', text)

    def test_osc_st_terminator(self):
        emu = TerminalEmulator(cols=40, rows=10)
        # OSC terminated by ESC \ (ST)
        emu.feed_text('\x1b]0;my_window_title\x1b\\Visible Text')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Visible Text')
        self.assertNotIn('\\', text)
        self.assertNotIn('my_window_title', text)

    def test_osc_st_terminator_split_across_chunks(self):
        emu = TerminalEmulator(cols=40, rows=10)
        chunk1 = b'\x1b]0;title_split\x1b'
        chunk2 = b'\\Visible After Split'
        emu.feed_bytes(chunk1)
        emu.feed_bytes(chunk2)
        text = emu.get_plain_text()
        self.assertEqual(text, 'Visible After Split')
        self.assertNotIn('\\', text)
        self.assertNotIn('title_split', text)

    def test_unsupported_csi_with_private_and_intermediates_safe_ignore(self):
        emu = TerminalEmulator(cols=40, rows=10)
        # CSI with > private prefix
        emu.feed_text('\x1b[>0cVisible After DA')
        text1 = emu.get_plain_text()
        self.assertEqual(text1, 'Visible After DA')
        self.assertNotIn('>0c', text1)

        # CSI with ! intermediate byte (DECSTR)
        emu.feed_text('\r\n\x1b[!pVisible After Soft Reset')
        lines = emu.get_plain_text().split('\n')
        self.assertEqual(lines[1], 'Visible After Soft Reset')
        self.assertNotIn('!p', lines[1])

    def test_dcs_apc_pm_sos_safe_consume(self):
        emu = TerminalEmulator(cols=40, rows=10)
        # DCS terminated by ST
        emu.feed_text('\x1bP1;2;payload\x1b\\Visible After DCS\r\n')
        # APC terminated by ST
        emu.feed_text('\x1b_some_apc_data\x1b\\Visible After APC\r\n')
        # PM terminated by ST
        emu.feed_text('\x1b^some_pm_data\x1b\\Visible After PM\r\n')
        # SOS terminated by ST
        emu.feed_text('\x1bXsome_sos_data\x1b\\Visible After SOS')

        lines = emu.get_plain_text().split('\n')
        self.assertEqual(lines[0], 'Visible After DCS')
        self.assertEqual(lines[1], 'Visible After APC')
        self.assertEqual(lines[2], 'Visible After PM')
        self.assertEqual(lines[3], 'Visible After SOS')
        for leak in ('payload', 'some_apc', 'some_pm', 'some_sos', '\\'):
            for line in lines:
                self.assertNotIn(leak, line)

    def test_dcs_split_across_chunks(self):
        emu = TerminalEmulator(cols=40, rows=10)
        chunk1 = b'\x1bP1;2;long_dcs_pay'
        chunk2 = b'load\x1b\\Visible After DCS Split'
        emu.feed_bytes(chunk1)
        emu.feed_bytes(chunk2)
        text = emu.get_plain_text()
        self.assertEqual(text, 'Visible After DCS Split')
        self.assertNotIn('payload', text)
        self.assertNotIn('\\', text)

    def test_alternate_screen_enter_and_restore(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('Normal Screen Line 1\r\nNormal Screen Line 2\r\n')
        self.assertFalse(emu.is_alternate_screen())

        # Enter alternate screen (vim / top / less)
        emu.feed_text('\x1b[?1049h')
        self.assertTrue(emu.is_alternate_screen())
        emu.feed_text('Vim Editor Buffer\r\n~')
        alt_text = emu.get_plain_text()
        self.assertIn('Vim Editor Buffer', alt_text)
        self.assertNotIn('Normal Screen Line 1', alt_text)

        # Exit alternate screen
        emu.feed_text('\x1b[?1049l')
        self.assertFalse(emu.is_alternate_screen())
        restored_text = emu.get_plain_text()
        self.assertIn('Normal Screen Line 1', restored_text)
        self.assertIn('Normal Screen Line 2', restored_text)
        self.assertNotIn('Vim Editor Buffer', restored_text)

    def test_top_style_repeated_redraw(self):
        emu = TerminalEmulator(cols=60, rows=10)
        # Frame 1
        emu.feed_text('\x1b[H\x1b[Jtop - 10:00:00 up 1 day, CPU: 5%\r\nPID USER CPU\r\n100 root 5.0')
        lines1 = [l.rstrip() for l in emu.get_plain_text().split('\n') if l.strip()]
        self.assertEqual(lines1[0], 'top - 10:00:00 up 1 day, CPU: 5%')
        self.assertEqual(lines1[2], '100 root 5.0')

        # Frame 2 (overwrite at top without creating duplicate scrollback history)
        emu.feed_text('\x1b[H\x1b[Jtop - 10:00:01 up 1 day, CPU: 12%\r\nPID USER CPU\r\n100 root 12.0')
        lines2 = [l.rstrip() for l in emu.get_plain_text().split('\n') if l.strip()]
        self.assertEqual(lines2[0], 'top - 10:00:01 up 1 day, CPU: 12%')
        self.assertEqual(lines2[2], '100 root 12.0')
        self.assertEqual(len(emu.scrollback), 0)

    def test_continuous_flow_fixture(self):
        """Continuous fixture:
        shell prompt -> OSC(ST) -> vim alternate screen -> DCS/unknown CSI -> cursor redraw -> exit alternate -> shell prompt
        """
        emu = TerminalEmulator(cols=80, rows=24)
        # 1. Shell prompt and command
        emu.feed_text('user@pengtools:~$ \x1b]0;terminal_title\x1b\\vim app.py\r\n')
        self.assertIn('user@pengtools:~$ vim app.py', emu.get_plain_text())

        # 2. Enter Vim (alternate screen, clear, redraw)
        emu.feed_text('\x1b[?1049h\x1b[H\x1b[2J')
        self.assertTrue(emu.is_alternate_screen())

        # 3. Terminal query / DCS emitted by vim or plugins
        emu.feed_text('\x1bP1;1;query_plugin_payload\x1b\\\x1b[>0c')
        emu.feed_text('import sys\r\nprint("Hello V4")\r\n~\r\n\x1b[24;1H"app.py" 2L, 29B\x1b[1;1H')

        alt_text = emu.get_plain_text()
        self.assertIn('import sys', alt_text)
        self.assertIn('print("Hello V4")', alt_text)
        self.assertIn('"app.py" 2L, 29B', alt_text)
        self.assertNotIn('query_plugin_payload', alt_text)
        self.assertNotIn('>0c', alt_text)

        # 4. Exit vim
        emu.feed_text('\x1b[?1049l')
        self.assertFalse(emu.is_alternate_screen())

        # 5. Returned to normal shell prompt
        emu.feed_text('user@pengtools:~$ ')
        normal_text = emu.get_plain_text()
        self.assertIn('user@pengtools:~$ vim app.py', normal_text)
        self.assertIn('user@pengtools:~$ ', normal_text)
        self.assertNotIn('import sys', normal_text)

    def test_bracketed_paste_and_application_cursor_modes(self):
        emu = TerminalEmulator(cols=40, rows=10)
        self.assertFalse(emu.bracketed_paste_mode)
        self.assertFalse(emu.application_cursor_keys)

        # Enable bracketed paste and application cursor mode
        emu.feed_text('\x1b[?2004h\x1b[?1h')
        self.assertTrue(emu.bracketed_paste_mode)
        self.assertTrue(emu.application_cursor_keys)

        # Disable
        emu.feed_text('\x1b[?2004l\x1b[?1l')
        self.assertFalse(emu.bracketed_paste_mode)
        self.assertFalse(emu.application_cursor_keys)

    def test_scrollback_cap_preservation(self):
        emu = TerminalEmulator(cols=40, rows=5, max_scrollback=20)
        for i in range(50):
            emu.feed_text(f'Line {i}\r\n')

        self.assertLessEqual(len(emu.scrollback), 20)
        text = emu.get_plain_text()
        self.assertIn('Line 49', text)
        self.assertNotIn('Line 0', text)  # Oldest pruned


if __name__ == '__main__':
    unittest.main()
