# -*- coding: utf-8 -*-
"""Terminal Emulator & Screen Model (Pure Python, zero external dependencies).

Architecture:
- TerminalTransport -> TerminalEmulator/ScreenModel -> TerminalView
- Handles incremental UTF-8 decode, ANSI/VT escape sequences, SGR styles,
  East Asian wide characters (wcwidth), scrollback buffer, alternate screens,
  bracketed paste, and cursor tracking.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import unicodedata
from typing import List, Optional, Tuple


def char_width(ch: str) -> int:
    """Calculate character display width (0, 1, or 2)."""
    if not ch:
        return 0
    code = ord(ch)
    # Control characters
    if code < 32 or (0x7F <= code < 0xA0):
        return 0
    # Combining characters / zero-width marks
    if unicodedata.combining(ch):
        return 0
    # East Asian Width: Wide ('W') and Fullwidth ('F') are 2 cells
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ('W', 'F'):
        return 2
    return 1


@dataclass(frozen=True)
class CellStyle:
    fg: Optional[str] = None  # None (default), color name, hex '#RRGGBB', or int
    bg: Optional[str] = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False
    hidden: bool = False


DEFAULT_STYLE = CellStyle()


@dataclass
class Cell:
    char: str = ' '
    width: int = 1  # 1 = single cell, 2 = wide char leading, 0 = wide char trailing
    style: CellStyle = DEFAULT_STYLE

    def is_empty(self) -> bool:
        return self.char in (' ', '') and self.style == DEFAULT_STYLE


# Standard ANSI color table (0-15)
ANSI_COLORS = [
    '#000000', '#CD3131', '#0DBC79', '#E5E510', '#2472C8', '#BC3FBC', '#11A8CD', '#E5E5E5',
    '#666666', '#F14C4C', '#23D18B', '#F5F543', '#3B8EEA', '#D670D6', '#29B8DB', '#FFFFFF',
]


class Screen:
    def __init__(self, cols: int, rows: int):
        self.cols = max(10, int(cols))
        self.rows = max(2, int(rows))
        self.grid: List[List[Cell]] = [
            [Cell() for _ in range(self.cols)] for _ in range(self.rows)
        ]
        self.cursor_x: int = 0
        self.cursor_y: int = 0
        self.saved_cursor_x: int = 0
        self.saved_cursor_y: int = 0
        self.saved_style: CellStyle = DEFAULT_STYLE
        self.scroll_top: int = 0
        self.scroll_bottom: int = self.rows - 1
        self.wrap_next: bool = False

    def clear(self, style: CellStyle = DEFAULT_STYLE):
        self.grid = [
            [Cell(' ', 1, style) for _ in range(self.cols)] for _ in range(self.rows)
        ]
        self.cursor_x = 0
        self.cursor_y = 0
        self.wrap_next = False


class TerminalEmulator:
    STATE_NORMAL = 0
    STATE_ESC = 1
    STATE_CSI = 2
    STATE_OSC = 3
    STATE_OSC_ESC = 4
    STATE_STRING = 5      # For DCS, APC, PM, SOS
    STATE_STRING_ESC = 6  # ESC inside string sequence
    STATE_CHARSET = 7

    def __init__(self, cols: int = 120, rows: int = 32, max_scrollback: int = 10000):
        self.cols = max(20, int(cols))
        self.rows = max(4, int(rows))
        self.max_scrollback = max(1, int(max_scrollback))

        self.main_screen = Screen(self.cols, self.rows)
        self.alt_screen = Screen(self.cols, self.rows)
        self.screen = self.main_screen
        self.scrollback: List[List[Cell]] = []

        self.current_style = DEFAULT_STYLE
        self.cursor_visible: bool = True
        self.application_cursor_keys: bool = False
        self.bracketed_paste_mode: bool = False

        # Parser state
        self._state = self.STATE_NORMAL
        self._params: List[int] = []
        self._current_param = ''
        self._private_mode: str = ''
        self._intermediates: str = ''
        self._osc_buffer: str = ''
        self._decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def is_alternate_screen(self) -> bool:
        return self.screen is self.alt_screen

    def resize(self, cols: int, rows: int):
        new_cols = max(20, int(cols))
        new_rows = max(4, int(rows))
        if new_cols == self.cols and new_rows == self.rows:
            return

        self.cols = new_cols
        self.rows = new_rows
        self._resize_screen(self.main_screen, new_cols, new_rows)
        self._resize_screen(self.alt_screen, new_cols, new_rows)

    def _resize_screen(self, screen: Screen, new_cols: int, new_rows: int):
        screen.cols = new_cols
        screen.rows = new_rows
        screen.scroll_top = 0
        screen.scroll_bottom = new_rows - 1

        new_grid = []
        for r in range(new_rows):
            if r < len(screen.grid):
                row = screen.grid[r][:new_cols]
                while len(row) < new_cols:
                    row.append(Cell())
                new_grid.append(row)
            else:
                new_grid.append([Cell() for _ in range(new_cols)])
        screen.grid = new_grid
        screen.cursor_x = min(screen.cursor_x, new_cols - 1)
        screen.cursor_y = min(screen.cursor_y, new_rows - 1)
        screen.wrap_next = False

    def feed_bytes(self, data: bytes):
        if not data:
            return
        text = self._decoder.decode(data)
        self.feed_text(text)

    def feed_text(self, text: str):
        if not text:
            return
        for ch in text:
            self._process_char(ch)

    def _process_char(self, ch: str):
        if self._state == self.STATE_NORMAL:
            if ch == '\x1b':
                self._state = self.STATE_ESC
            elif ch == '\r':
                self.screen.cursor_x = 0
                self.screen.wrap_next = False
            elif ch == '\n' or ch == '\x0b' or ch == '\x0c':
                self._linefeed()
            elif ch == '\x08':  # Backspace
                self.screen.cursor_x = max(0, self.screen.cursor_x - 1)
                self.screen.wrap_next = False
            elif ch == '\t':  # Tab
                next_tab = (self.screen.cursor_x // 8 + 1) * 8
                self.screen.cursor_x = min(self.cols - 1, next_tab)
                self.screen.wrap_next = False
            elif ch == '\x07':  # Bell
                pass
            elif ord(ch) >= 32:
                self._put_char(ch)

        elif self._state == self.STATE_ESC:
            if ch == '[':
                self._state = self.STATE_CSI
                self._params = []
                self._current_param = ''
                self._private_mode = ''
                self._intermediates = ''
            elif ch == ']':
                self._state = self.STATE_OSC
                self._osc_buffer = ''
            elif ch in ('P', '_', '^', 'X'):  # DCS (P), APC (_), PM (^), SOS (X)
                self._state = self.STATE_STRING
            elif ch in ('(', ')', '*', '+'):
                self._state = self.STATE_CHARSET
            elif ch == '7':  # Save cursor
                self._save_cursor()
                self._state = self.STATE_NORMAL
            elif ch == '8':  # Restore cursor
                self._restore_cursor()
                self._state = self.STATE_NORMAL
            elif ch == 'D':  # Index (linefeed)
                self._linefeed()
                self._state = self.STATE_NORMAL
            elif ch == 'M':  # Reverse Index
                self._reverse_index()
                self._state = self.STATE_NORMAL
            elif ch == 'E':  # Next line
                self.screen.cursor_x = 0
                self._linefeed()
                self._state = self.STATE_NORMAL
            elif ch == 'c':  # Reset
                self.reset()
                self._state = self.STATE_NORMAL
            elif ch in ('=', '>', 'N', 'O', '\\'):  # Keypad mode / Lone ST
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':
                self._state = self.STATE_ESC
            else:
                self._state = self.STATE_NORMAL

        elif self._state == self.STATE_CSI:
            code = ord(ch)
            # Parameter bytes: 0x30–0x3F (0-9:;<=>?)
            if 0x30 <= code <= 0x3F:
                if ch in ('?', '>', '<', '='):
                    self._private_mode += ch
                elif '0' <= ch <= '9':
                    self._current_param += ch
                elif ch in (';', ':'):
                    self._params.append(int(self._current_param) if self._current_param else 0)
                    self._current_param = ''
            # Intermediate bytes: 0x20–0x2F ( !"#$%&'()*+,-./)
            elif 0x20 <= code <= 0x2F:
                self._intermediates += ch
            # Final byte: 0x40–0x7E (@ through ~)
            elif 0x40 <= code <= 0x7E:
                if self._current_param:
                    self._params.append(int(self._current_param))
                    self._current_param = ''
                self._execute_csi(ch)
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':
                self._state = self.STATE_ESC
            elif ch in ('\x18', '\x1a'):  # CAN / SUB cancels sequence
                self._state = self.STATE_NORMAL
            elif code < 0x20:
                pass  # Ignore control chars during CSI
            else:
                self._state = self.STATE_NORMAL

        elif self._state == self.STATE_OSC:
            if ch == '\x07':  # BEL termination
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':  # Candidate for ST (\x1b\)
                self._state = self.STATE_OSC_ESC
            elif ch in ('\x18', '\x1a'):  # Cancel
                self._state = self.STATE_NORMAL
            else:
                self._osc_buffer += ch
                if len(self._osc_buffer) > 4096:
                    self._state = self.STATE_NORMAL

        elif self._state == self.STATE_OSC_ESC:
            if ch == '\\':  # ST (\x1b\) termination complete
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':
                self._state = self.STATE_OSC_ESC
            elif ch == '[':
                self._state = self.STATE_CSI
                self._params = []
                self._current_param = ''
                self._private_mode = ''
                self._intermediates = ''
            elif ch == ']':
                self._state = self.STATE_OSC
                self._osc_buffer = ''
            else:
                self._state = self.STATE_NORMAL
                if ord(ch) >= 32:
                    self._put_char(ch)

        elif self._state == self.STATE_STRING:  # DCS, APC, PM, SOS
            if ch == '\x07':  # BEL termination
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':  # Candidate for ST (\x1b\)
                self._state = self.STATE_STRING_ESC
            elif ch in ('\x18', '\x1a'):
                self._state = self.STATE_NORMAL
            else:
                pass  # Safely consume string payload

        elif self._state == self.STATE_STRING_ESC:
            if ch == '\\':  # ST complete
                self._state = self.STATE_NORMAL
            elif ch == '\x1b':
                self._state = self.STATE_STRING_ESC
            elif ch == '[':
                self._state = self.STATE_CSI
                self._params = []
                self._current_param = ''
                self._private_mode = ''
                self._intermediates = ''
            elif ch == ']':
                self._state = self.STATE_OSC
                self._osc_buffer = ''
            else:
                self._state = self.STATE_NORMAL
                if ord(ch) >= 32:
                    self._put_char(ch)

        elif self._state == self.STATE_CHARSET:
            self._state = self.STATE_NORMAL

    def _put_char(self, ch: str):
        w = char_width(ch)
        if w <= 0:
            return

        if self.screen.wrap_next:
            self.screen.cursor_x = 0
            self._linefeed()
            self.screen.wrap_next = False

        if self.screen.cursor_x + w > self.cols:
            self.screen.cursor_x = 0
            self._linefeed()

        x = self.screen.cursor_x
        y = self.screen.cursor_y
        if 0 <= y < self.rows and 0 <= x < self.cols:
            self.screen.grid[y][x] = Cell(ch, w, self.current_style)
            if w == 2 and x + 1 < self.cols:
                self.screen.grid[y][x + 1] = Cell('', 0, self.current_style)

        if x + w >= self.cols:
            self.screen.cursor_x = self.cols - 1
            self.screen.wrap_next = True
        else:
            self.screen.cursor_x += w

    def _linefeed(self):
        screen = self.screen
        if screen.cursor_y == screen.scroll_bottom:
            self._scroll_up(screen.scroll_top, screen.scroll_bottom)
        elif screen.cursor_y < screen.rows - 1:
            screen.cursor_y += 1
        screen.wrap_next = False

    def _reverse_index(self):
        screen = self.screen
        if screen.cursor_y == screen.scroll_top:
            self._scroll_down(screen.scroll_top, screen.scroll_bottom)
        elif screen.cursor_y > 0:
            screen.cursor_y -= 1
        screen.wrap_next = False

    def _scroll_up(self, top: int, bottom: int):
        if top < 0 or bottom >= self.rows or top >= bottom:
            return
        scrolled_row = self.screen.grid[top]
        if self.screen is self.main_screen and top == 0:
            self.scrollback.append(scrolled_row)
            if len(self.scrollback) > self.max_scrollback:
                self.scrollback.pop(0)

        for r in range(top, bottom):
            self.screen.grid[r] = self.screen.grid[r + 1]
        self.screen.grid[bottom] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]

    def _scroll_down(self, top: int, bottom: int):
        if top < 0 or bottom >= self.rows or top >= bottom:
            return
        for r in range(bottom, top, -1):
            self.screen.grid[r] = self.screen.grid[r - 1]
        self.screen.grid[top] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]

    def _execute_csi(self, cmd: str):
        params = self._params
        p0 = params[0] if params else 0
        p1 = params[1] if len(params) > 1 else 0

        # DEC Private Mode (e.g. CSI ? 1049 h)
        if '?' in self._private_mode:
            if cmd == 'h':
                self._set_mode(params, True)
            elif cmd == 'l':
                self._set_mode(params, False)
            return

        # If other vendor/private prefix or intermediate byte is present and not standard, safely ignore
        if self._private_mode or self._intermediates:
            return

        if cmd == 'A':  # Cursor Up
            n = max(1, p0)
            self.screen.cursor_y = max(self.screen.scroll_top, self.screen.cursor_y - n)
            self.screen.wrap_next = False
        elif cmd == 'B':  # Cursor Down
            n = max(1, p0)
            self.screen.cursor_y = min(self.screen.scroll_bottom, self.screen.cursor_y + n)
            self.screen.wrap_next = False
        elif cmd == 'C':  # Cursor Forward
            n = max(1, p0)
            self.screen.cursor_x = min(self.cols - 1, self.screen.cursor_x + n)
            self.screen.wrap_next = False
        elif cmd == 'D':  # Cursor Back
            n = max(1, p0)
            self.screen.cursor_x = max(0, self.screen.cursor_x - n)
            self.screen.wrap_next = False
        elif cmd == 'E':  # Next Line
            n = max(1, p0)
            self.screen.cursor_x = 0
            self.screen.cursor_y = min(self.screen.scroll_bottom, self.screen.cursor_y + n)
            self.screen.wrap_next = False
        elif cmd == 'F':  # Previous Line
            n = max(1, p0)
            self.screen.cursor_x = 0
            self.screen.cursor_y = max(self.screen.scroll_top, self.screen.cursor_y - n)
            self.screen.wrap_next = False
        elif cmd in ('G', '`'):  # Cursor Horizontal Absolute
            col = max(1, p0) - 1
            self.screen.cursor_x = max(0, min(self.cols - 1, col))
            self.screen.wrap_next = False
        elif cmd in ('H', 'f'):  # Cursor Position
            row = (max(1, p0) - 1) if p0 else 0
            col = (max(1, p1) - 1) if p1 else 0
            self.screen.cursor_y = max(0, min(self.rows - 1, row))
            self.screen.cursor_x = max(0, min(self.cols - 1, col))
            self.screen.wrap_next = False
        elif cmd == 'd':  # Line Position Absolute
            row = max(1, p0) - 1
            self.screen.cursor_y = max(0, min(self.rows - 1, row))
            self.screen.wrap_next = False
        elif cmd == 'J':  # Erase in Display
            self._erase_display(p0)
        elif cmd == 'K':  # Erase in Line
            self._erase_line(p0)
        elif cmd == 'L':  # Insert Lines
            n = max(1, p0)
            for _ in range(n):
                self._scroll_down(self.screen.cursor_y, self.screen.scroll_bottom)
        elif cmd == 'M':  # Delete Lines
            n = max(1, p0)
            for _ in range(n):
                self._scroll_up(self.screen.cursor_y, self.screen.scroll_bottom)
        elif cmd == 'P':  # Delete Characters
            n = max(1, p0)
            y = self.screen.cursor_y
            x = self.screen.cursor_x
            row = self.screen.grid[y]
            del row[x:x + n]
            while len(row) < self.cols:
                row.append(Cell(' ', 1, self.current_style))
        elif cmd == '@':  # Insert Characters
            n = max(1, p0)
            y = self.screen.cursor_y
            x = self.screen.cursor_x
            row = self.screen.grid[y]
            for _ in range(n):
                row.insert(x, Cell(' ', 1, self.current_style))
            self.screen.grid[y] = row[:self.cols]
        elif cmd == 'X':  # Erase Characters
            n = max(1, p0)
            y = self.screen.cursor_y
            x = self.screen.cursor_x
            for i in range(x, min(self.cols, x + n)):
                self.screen.grid[y][i] = Cell(' ', 1, self.current_style)
        elif cmd == 'r':  # Set Scroll Region (DECSTBM)
            top = (max(1, p0) - 1) if p0 else 0
            bottom = (max(1, p1) - 1) if p1 else (self.rows - 1)
            if 0 <= top < bottom < self.rows:
                self.screen.scroll_top = top
                self.screen.scroll_bottom = bottom
                self.screen.cursor_x = 0
                self.screen.cursor_y = 0
        elif cmd == 'm':  # SGR
            self._apply_sgr(params)
        elif cmd == 's':  # Save cursor
            self._save_cursor()
        elif cmd == 'u':  # Restore cursor
            self._restore_cursor()
        elif cmd == 'h':  # Set mode
            self._set_mode(params, True)
        elif cmd == 'l':  # Reset mode
            self._set_mode(params, False)

    def _erase_display(self, mode: int):
        y = self.screen.cursor_y
        x = self.screen.cursor_x
        if mode == 0:  # Cursor to end
            for c in range(x, self.cols):
                self.screen.grid[y][c] = Cell(' ', 1, self.current_style)
            for r in range(y + 1, self.rows):
                self.screen.grid[r] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]
        elif mode == 1:  # Start to cursor
            for r in range(0, y):
                self.screen.grid[r] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]
            for c in range(0, min(self.cols, x + 1)):
                self.screen.grid[y][c] = Cell(' ', 1, self.current_style)
        elif mode in (2, 3):  # Entire screen
            for r in range(self.rows):
                self.screen.grid[r] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]
            if mode == 3 and self.screen is self.main_screen:
                self.scrollback.clear()

    def _erase_line(self, mode: int):
        y = self.screen.cursor_y
        x = self.screen.cursor_x
        if mode == 0:  # Cursor to end
            for c in range(x, self.cols):
                self.screen.grid[y][c] = Cell(' ', 1, self.current_style)
        elif mode == 1:  # Start to cursor
            for c in range(0, min(self.cols, x + 1)):
                self.screen.grid[y][c] = Cell(' ', 1, self.current_style)
        elif mode == 2:  # Entire line
            self.screen.grid[y] = [Cell(' ', 1, self.current_style) for _ in range(self.cols)]

    def _save_cursor(self):
        self.screen.saved_cursor_x = self.screen.cursor_x
        self.screen.saved_cursor_y = self.screen.cursor_y
        self.screen.saved_style = self.current_style

    def _restore_cursor(self):
        self.screen.cursor_x = min(self.cols - 1, self.screen.saved_cursor_x)
        self.screen.cursor_y = min(self.rows - 1, self.screen.saved_cursor_y)
        self.current_style = self.screen.saved_style
        self.screen.wrap_next = False

    def _set_mode(self, params: List[int], enable: bool):
        for p in params or [0]:
            if p == 1:  # Application Cursor Keys (DECCKM)
                self.application_cursor_keys = enable
            elif p == 25:  # Cursor Visibility (DECTCEM)
                self.cursor_visible = enable
            elif p in (47, 1047, 1049):  # Alternate Screen
                if enable and self.screen is self.main_screen:
                    self.alt_screen.clear(self.current_style)
                    self.screen = self.alt_screen
                elif not enable and self.screen is self.alt_screen:
                    self.screen = self.main_screen
            elif p == 2004:  # Bracketed Paste
                self.bracketed_paste_mode = enable

    def _apply_sgr(self, params: List[int]):
        if not params:
            self.current_style = DEFAULT_STYLE
            return

        fg = self.current_style.fg
        bg = self.current_style.bg
        bold = self.current_style.bold
        dim = self.current_style.dim
        italic = self.current_style.italic
        underline = self.current_style.underline
        reverse = self.current_style.reverse
        hidden = self.current_style.hidden

        i = 0
        n = len(params)
        while i < n:
            p = params[i]
            if p == 0:
                fg, bg = None, None
                bold = dim = italic = underline = reverse = hidden = False
            elif p == 1:
                bold = True
            elif p == 2:
                dim = True
            elif p == 3:
                italic = True
            elif p == 4:
                underline = True
            elif p == 7:
                reverse = True
            elif p == 8:
                hidden = True
            elif p == 22:
                bold = dim = False
            elif p == 23:
                italic = False
            elif p == 24:
                underline = False
            elif p == 27:
                reverse = False
            elif p == 28:
                hidden = False
            elif 30 <= p <= 37:
                fg = ANSI_COLORS[p - 30]
            elif p == 38:  # Extended FG
                if i + 2 < n and params[i + 1] == 5:
                    c_idx = params[i + 2]
                    fg = ANSI_COLORS[c_idx % 16] if c_idx < 16 else f'#color{c_idx}'
                    i += 2
                elif i + 4 < n and params[i + 1] == 2:
                    r, g, b = params[i + 2], params[i + 3], params[i + 4]
                    fg = f'#{r:02x}{g:02x}{b:02x}'
                    i += 4
            elif p == 39:
                fg = None
            elif 40 <= p <= 47:
                bg = ANSI_COLORS[p - 40]
            elif p == 48:  # Extended BG
                if i + 2 < n and params[i + 1] == 5:
                    c_idx = params[i + 2]
                    bg = ANSI_COLORS[c_idx % 16] if c_idx < 16 else f'#color{c_idx}'
                    i += 2
                elif i + 4 < n and params[i + 1] == 2:
                    r, g, b = params[i + 2], params[i + 3], params[i + 4]
                    bg = f'#{r:02x}{g:02x}{b:02x}'
                    i += 4
            elif p == 49:
                bg = None
            elif 90 <= p <= 97:
                fg = ANSI_COLORS[(p - 90) + 8]
            elif 100 <= p <= 107:
                bg = ANSI_COLORS[(p - 100) + 8]
            i += 1

        self.current_style = CellStyle(
            fg=fg, bg=bg, bold=bold, dim=dim, italic=italic,
            underline=underline, reverse=reverse, hidden=hidden,
        )

    def reset(self):
        self.main_screen.clear()
        self.alt_screen.clear()
        self.screen = self.main_screen
        self.scrollback.clear()
        self.current_style = DEFAULT_STYLE
        self.cursor_visible = True
        self.application_cursor_keys = False
        self.bracketed_paste_mode = False
        self._state = self.STATE_NORMAL

    def clear_screen(self):
        self.screen.clear()

    def get_screen_rows(self) -> List[List[Cell]]:
        return [list(row) for row in self.screen.grid]

    def get_visible_rows(self, scroll_offset: int = 0) -> List[List[Cell]]:
        """Get visible rows considering scrollback offset (0 = live screen)."""
        offset = max(0, min(len(self.scrollback), int(scroll_offset)))
        if offset == 0 or self.is_alternate_screen():
            return self.get_screen_rows()

        visible = []
        sb_start = len(self.scrollback) - offset
        sb_slice = self.scrollback[sb_start:sb_start + self.rows]
        visible.extend(sb_slice)

        remaining = self.rows - len(visible)
        if remaining > 0:
            visible.extend(self.screen.grid[:remaining])
        return [list(row) for row in visible]

    def get_plain_text(self) -> str:
        """Get text representation of scrollback and current screen."""
        lines = []
        for row in self.scrollback:
            line_str = ''.join(cell.char for cell in row if cell.width != 0).rstrip()
            lines.append(line_str)
        for row in self.screen.grid:
            line_str = ''.join(cell.char for cell in row if cell.width != 0).rstrip()
            lines.append(line_str)
        while lines and not lines[-1]:
            lines.pop()
        return '\n'.join(lines)
