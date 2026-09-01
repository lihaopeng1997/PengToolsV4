# -*- coding: utf-8 -*-
"""Comprehensive tests for TerminalEmulator (ScreenModel) & InteractiveShell Transport."""

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
        emu.feed_text('Line 1\nLine 2\nLine 3')
        # Move up 2 lines, move to column 10 (1-based: 11)
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

    def test_alternate_screen_enter_and_restore(self):
        emu = TerminalEmulator(cols=40, rows=10)
        emu.feed_text('Normal Screen Line 1\nNormal Screen Line 2\n')
        self.assertFalse(emu.is_alternate_screen())

        # Enter alternate screen (vim / top / less)
        emu.feed_text('\x1b[?1049h')
        self.assertTrue(emu.is_alternate_screen())
        emu.feed_text('Vim Editor Buffer\n~')
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

    def test_vim_fixture(self):
        emu = TerminalEmulator(cols=80, rows=24)
        # 1. Shell prompt before vim
        emu.feed_text('user@linux:~$ vim myfile.txt\n')

        # 2. Vim enters alternate screen, clears, writes status bar and text
        emu.feed_text('\x1b[?1049h\x1b[H\x1b[2J')
        emu.feed_text('Hello PengToolsHub\n~\n~\n\x1b[24;1H"myfile.txt" [New] 1L, 18B\x1b[1;19H')

        self.assertTrue(emu.is_alternate_screen())
        self.assertEqual(emu.screen.cursor_y, 0)
        self.assertEqual(emu.screen.cursor_x, 18)
        self.assertIn('Hello PengToolsHub', emu.get_plain_text())

        # 3. User types in vim -> vim sends insert / cursor move / edits
        emu.feed_text(' V4')
        self.assertIn('Hello PengToolsHub V4', emu.get_plain_text())

        # 4. User exits vim -> exit alternate screen
        emu.feed_text('\x1b[?1049l')
        self.assertFalse(emu.is_alternate_screen())
        self.assertIn('user@linux:~$ vim myfile.txt', emu.get_plain_text())
        self.assertNotIn('Hello PengToolsHub', emu.get_plain_text())

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
            emu.feed_text(f'Line {i}\n')

        self.assertLessEqual(len(emu.scrollback), 20)
        text = emu.get_plain_text()
        self.assertIn('Line 49', text)
        self.assertNotIn('Line 0', text)  # Oldest pruned

    def test_osc_sequences_safely_ignored(self):
        emu = TerminalEmulator(cols=40, rows=10)
        # OSC 0 title sequence
        emu.feed_text('\x1b]0;my_window_title\x07Visible Text')
        text = emu.get_plain_text()
        self.assertEqual(text, 'Visible Text')
        self.assertNotIn('my_window_title', text)


if __name__ == '__main__':
    unittest.main()
