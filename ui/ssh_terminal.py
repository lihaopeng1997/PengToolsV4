# -*- coding: utf-8 -*-
"""简易 SSH 终端（开源自研：TerminalEmulator ScreenModel + QPainter 渲染 + PTY 直通）。

视觉原则：终端是独立「控制台岛」——与浅色页面强对比，色相贴主题主色（绿/蓝/棕/薄荷），
避免与侧栏/卡片糊成同色，也不用刺眼的纯霓虹。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent,
    QPainter, QPalette, QPen, QBrush, QInputMethodEvent,
)
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QVBoxLayout, QWidget,
)

from tools.ops_ssh_shell import InteractiveShell
from tools.terminal_emulator import TerminalEmulator, Cell, CellStyle, DEFAULT_STYLE


def _theme_term_colors() -> dict:
    """专用 TERM_* token：控制台深底 + 亮字，与页面 APP_BG/SURFACE 分离。"""
    try:
        from ui.theme_manager import ThemeManager
        p = ThemeManager.instance().palette()
    except Exception:
        p = {}
    bg = p.get('TERM_BG') or '#121A22'
    fg = p.get('TERM_FG') or '#E8EEF4'
    muted = p.get('TERM_MUTED') or '#8B9AAB'
    border = p.get('TERM_BORDER') or '#2A3D48'
    primary = p.get('TERM_SYS') or p.get('PRIMARY') or '#7EC8A3'
    sel = p.get('TERM_SEL') or '#1E3D34'
    chrome = p.get('TERM_CHROME') or bg
    find_bg = p.get('TERM_FIND_BG') or chrome
    return {
        'bg': bg,
        'fg': fg,
        'muted': muted,
        'border': border,
        'primary': primary,
        'sel': sel,
        'sys': primary,
        'chrome': chrome,
        'find_bg': find_bg,
        'find': '#5C4A18',
        'find_cur': '#8A6F1E',
    }


class _ShellBridge(QObject):
    data = pyqtSignal(int, object)   # gen, bytes | str
    closed = pyqtSignal(int)         # gen
    error = pyqtSignal(int, str)     # gen, msg


class SshTerminalWidget(QWidget):
    """外壳：查找条 + 终端正文（主题协调，无割裂顶栏）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ssh-terminal-host')
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._find_bar = QWidget()
        self._find_bar.setObjectName('ssh-find-bar')
        fl = QHBoxLayout(self._find_bar)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.setSpacing(8)
        self._find_edit = QLineEdit()
        self._find_edit.setObjectName('ssh-find-edit')
        self._find_edit.setPlaceholderText('在终端中查找…')
        self._find_edit.returnPressed.connect(lambda: self.find_next(False))
        fl.addWidget(self._find_edit, 1)
        self._find_case = QCheckBox('区分大小写')
        fl.addWidget(self._find_case)
        prev_btn = QPushButton('上一个')
        prev_btn.setObjectName('ssh-find-btn')
        prev_btn.setFixedHeight(28)
        prev_btn.clicked.connect(lambda: self.find_next(True))
        next_btn = QPushButton('下一个')
        next_btn.setObjectName('ssh-find-btn')
        next_btn.setFixedHeight(28)
        next_btn.clicked.connect(lambda: self.find_next(False))
        close_btn = QPushButton('关闭')
        close_btn.setObjectName('ssh-find-btn')
        close_btn.setFixedHeight(28)
        close_btn.clicked.connect(self.hide_find)
        fl.addWidget(prev_btn)
        fl.addWidget(next_btn)
        fl.addWidget(close_btn)
        self._find_status = QLabel('')
        self._find_status.setObjectName('field-hint')
        fl.addWidget(self._find_status)
        self._find_bar.hide()
        root.addWidget(self._find_bar)

        self.view = _SshTerminalView(self)
        root.addWidget(self.view, 1)

    @property
    def shell_alive(self) -> bool:
        return self.view.shell_alive

    def attach_client(self, client, *, cols: int = 120, rows: int = 32) -> None:
        self.view.attach_client(client, cols=cols, rows=rows)

    def detach(self) -> None:
        self.view.detach()

    def append_system(self, text: str) -> None:
        self.view.append_system(text)

    def send_command_line(self, text: str) -> None:
        self.view.send_command_line(text)

    def clear(self) -> None:
        self.view.clear_and_ready()

    def setFocus(self, reason=None):  # noqa: N802
        if reason is None:
            self.view.setFocus()
        else:
            self.view.setFocus(reason)

    def setPlaceholderText(self, text: str) -> None:
        self.view.setPlaceholderText(text)

    def show_find(self):
        self._find_bar.show()
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def hide_find(self):
        self._find_bar.hide()
        self.view.clear_find_highlights()
        self.view.setFocus()

    def find_next(self, backward: bool = False):
        q = self._find_edit.text()
        n = self.view.find_in_buffer(q, backward=backward, case_sensitive=self._find_case.isChecked())
        if not q.strip():
            self._find_status.setText('')
        elif n <= 0:
            self._find_status.setText('未找到')
        else:
            self._find_status.setText(f'{n} 处')

    def refresh_theme(self):
        self.view._apply_terminal_palette()


class _SshTerminalView(QAbstractScrollArea):
    """基于 TerminalEmulator ScreenModel 的 QPainter 高性能终端渲染视图。"""

    def __init__(self, host: SshTerminalWidget):
        super().__init__(host)
        self._host = host
        self.setObjectName('ssh-terminal')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)

        mono = QFont('Cascadia Mono')
        if not mono.exactMatch():
            mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.setFont(mono)

        self._emulator = TerminalEmulator(cols=120, rows=32)
        self._shell: Optional[InteractiveShell] = None
        self._connected = False
        self._session_generation = 0
        self._ui_active = True
        self._placeholder = '未连接 — 选择服务器并点击「连接」后，可在此输入命令'
        self._system_status = ''
        self._preedit = ''
        self._colors = _theme_term_colors()

        # Selection state: (abs_row, col)
        self._sel_start: Optional[Tuple[int, int]] = None
        self._sel_end: Optional[Tuple[int, int]] = None
        self._is_mouse_selecting = False

        # Find state
        self._find_query = ''
        self._find_matches: list[tuple[int, int, int]] = []  # (abs_row, col_start, length)
        self._find_index = -1

        self._bridge = _ShellBridge(self)
        self._bridge.data.connect(self._on_data)
        self._bridge.closed.connect(self._on_shell_closed)
        self._bridge.error.connect(self._on_shell_error)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._apply_terminal_palette()

    def _apply_terminal_palette(self):
        c = _theme_term_colors()
        self._colors = c
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(c['bg']))
        pal.setColor(QPalette.ColorRole.Window, QColor(c['bg']))
        pal.setColor(QPalette.ColorRole.Text, QColor(c['fg']))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c['fg']))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c['sel']))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c['fg']))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c['muted']))
        self.setPalette(pal)

        self.setStyleSheet(
            f"QAbstractScrollArea#ssh-terminal {{"
            f" background-color: {c['bg']};"
            f" color: {c['fg']};"
            f" border: none;"
            f" border-radius: 12px;"
            f"}}"
        )
        host = self._host
        if host is not None:
            host.setStyleSheet(
                f"QWidget#ssh-terminal-host {{"
                f" background-color: {c['bg']};"
                f" border: 1px solid {c['border']};"
                f" border-radius: 14px;"
                f"}}"
                f"QWidget#ssh-find-bar {{"
                f" background-color: {c.get('find_bg') or c['chrome']};"
                f" border-bottom: 1px solid {c['border']};"
                f"}}"
            )
        self.viewport().update()

    @property
    def shell_alive(self) -> bool:
        return bool(self._shell and self._shell.alive and self._connected)

    def setPlaceholderText(self, text: str) -> None:
        self._placeholder = str(text or '')
        self.viewport().update()

    def set_ui_active(self, active: bool) -> None:
        self._ui_active = bool(active)
        if active:
            self._update_scroll_bar()
            self.viewport().update()

    def attach_client(self, client, *, cols: int = 120, rows: int = 32) -> None:
        self.detach()
        self._session_generation += 1
        gen = self._session_generation
        bridge = self._bridge

        def on_data(data):
            bridge.data.emit(gen, data)

        def on_closed():
            bridge.closed.emit(gen)

        def on_error(msg: str):
            bridge.error.emit(gen, str(msg))

        self._emulator = TerminalEmulator(cols=cols, rows=rows)
        shell = InteractiveShell(
            on_data=on_data, on_closed=on_closed, on_error=on_error,
            width=cols, height=rows,
        )
        shell.attach_client(client, owns_client=False)
        self._shell = shell
        self._connected = True
        self._system_status = '[终端已就绪] 可直接输入命令。Ctrl+C 中断 · 右键复制/粘贴 · Ctrl+F 查找'
        self._update_scroll_bar()
        self.setFocus()
        self.viewport().update()

    def detach(self) -> None:
        self._session_generation += 1
        shell = self._shell
        self._shell = None
        self._connected = False
        if shell is not None:
            try:
                shell.close()
            except Exception:
                pass
        self.viewport().update()

    def append_system(self, text: str) -> None:
        """更新系统提示，绝不修改远端 screen grid、光标或状态机。"""
        self._system_status = str(text or '').strip()
        self.viewport().update()

    def clear_and_ready(self) -> None:
        """清屏：
        - 已连接时向远端 PTY 发送 Ctrl+L (\x0c)，由远端 PTY 回显自然重绘/清屏，不本地篡改 screen authority。
        - 未连接时清理本地残余展示。
        """
        self._system_status = ''
        if self.shell_alive and self._shell is not None:
            try:
                self._shell.send(b'\x0c')
            except Exception as exc:
                self.append_system(f'[发送失败] {exc}')
        else:
            self._emulator.clear_screen()
            self._update_scroll_bar()
            self.viewport().update()

    def send_command_line(self, text: str) -> None:
        if not self.shell_alive:
            return
        payload = str(text or '')
        if payload and not payload.endswith('\n') and not payload.endswith('\r'):
            payload = payload + '\r'
        elif payload.endswith('\n') and not payload.endswith('\r'):
            payload = payload[:-1] + '\r'
        try:
            self._shell.send(payload.encode('utf-8'))
        except Exception as exc:
            self.append_system(f'[发送失败] {exc}')

    def resize_pty(self, cols: int, rows: int) -> None:
        self._emulator.resize(cols, rows)
        if self._shell:
            try:
                self._shell.resize(cols, rows)
            except Exception:
                pass
        self._update_scroll_bar()
        self.viewport().update()

    def _on_data(self, gen: int, data: object):
        if gen != self._session_generation:
            return
        was_at_bottom = (self.verticalScrollBar().value() == self.verticalScrollBar().maximum())
        if isinstance(data, bytes):
            self._emulator.feed_bytes(data)
        elif isinstance(data, str):
            self._emulator.feed_text(data)

        self._update_scroll_bar()
        if was_at_bottom:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        if self._ui_active:
            self.viewport().update()

    def _on_shell_closed(self, gen: int):
        if gen != self._session_generation:
            return
        self._connected = False
        self.append_system('[会话已断开]')

    def _on_shell_error(self, gen: int, msg: str):
        if gen != self._session_generation:
            return
        self.append_system(f'[终端错误] {msg}')

    def _on_scroll(self, val: int):
        self.viewport().update()

    def _update_scroll_bar(self):
        sb = self.verticalScrollBar()
        total_scrollback = len(self._emulator.scrollback)
        sb.setRange(0, total_scrollback)
        sb.setPageStep(self._emulator.rows)

    def _cell_dimensions(self) -> Tuple[int, int]:
        fm = self.fontMetrics()
        cw = max(1, fm.horizontalAdvance('M'))
        lh = max(1, fm.lineSpacing())
        return cw, lh

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        c = self._colors
        painter.fillRect(self.viewport().rect(), QColor(c['bg']))

        if not self._connected and not self._emulator.scrollback and all(
            cell.is_empty() for row in self._emulator.screen.grid for cell in row
        ):
            painter.setPen(QColor(c['muted']))
            display_text = self._system_status or self._placeholder
            painter.drawText(
                self.viewport().rect().adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                display_text,
            )
            return

        cw, lh = self._cell_dimensions()
        fm = self.fontMetrics()
        ascent = fm.ascent()

        sb_max = self.verticalScrollBar().maximum()
        sb_val = self.verticalScrollBar().value()
        scroll_offset = sb_max - sb_val

        visible_rows = self._emulator.get_visible_rows(scroll_offset)
        abs_row_start = len(self._emulator.scrollback) - scroll_offset
        if abs_row_start < 0:
            abs_row_start = 0

        # Draw cells
        sel_range = self._get_normalized_selection()
        for r, row in enumerate(visible_rows):
            abs_row = abs_row_start + r
            y = r * lh
            col_x = 0
            for col_idx, cell in enumerate(row):
                if cell.width == 0:
                    continue  # Trailing cell of wide char
                cell_w = cw * max(1, cell.width)
                cell_rect = QRect(col_x, y, cell_w, lh)

                # Background fill
                is_selected = self._is_cell_selected(abs_row, col_idx, sel_range)
                is_find = self._is_cell_find_highlight(abs_row, col_idx)

                if is_find:
                    painter.fillRect(cell_rect, QColor(c['find_cur'] if is_find == 2 else c['find']))
                elif is_selected:
                    painter.fillRect(cell_rect, QColor(c['sel']))
                elif cell.style.bg:
                    bg_color = QColor(cell.style.bg)
                    if bg_color.isValid():
                        painter.fillRect(cell_rect, bg_color)

                # Foreground character
                if cell.char and cell.char != ' ':
                    fg = cell.style.fg
                    fg_color = QColor(fg) if (fg and QColor(fg).isValid()) else QColor(c['fg'])
                    painter.setPen(fg_color)
                    font = self.font()
                    if cell.style.bold:
                        font.setBold(True)
                    if cell.style.underline:
                        font.setUnderline(True)
                    if cell.style.italic:
                        font.setItalic(True)
                    painter.setFont(font)
                    painter.drawText(col_x, y + ascent, cell.char)

                col_x += cell_w

        # Draw Cursor
        if scroll_offset == 0 and self._emulator.cursor_visible:
            cx = self._emulator.screen.cursor_x
            cy = self._emulator.screen.cursor_y
            if 0 <= cy < len(visible_rows) and 0 <= cx < self._emulator.cols:
                cursor_rect = QRect(cx * cw, cy * lh, cw, lh)
                if self.hasFocus():
                    painter.setBrush(QBrush(QColor(c['primary'])))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRect(cursor_rect)
                    # redraw character underneath cursor in inverse color
                    if cy < len(self._emulator.screen.grid) and cx < len(self._emulator.screen.grid[cy]):
                        cur_cell = self._emulator.screen.grid[cy][cx]
                        if cur_cell.char and cur_cell.char != ' ':
                            painter.setPen(QColor(c['bg']))
                            painter.drawText(cx * cw, cy * lh + ascent, cur_cell.char)
                else:
                    painter.setPen(QPen(QColor(c['muted']), 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(cursor_rect.adjusted(0, 0, -1, -1))

        # Draw preedit text if IME is active
        if self._preedit and scroll_offset == 0:
            cx = self._emulator.screen.cursor_x
            cy = self._emulator.screen.cursor_y
            pre_x = cx * cw
            pre_y = cy * lh
            pre_w = fm.horizontalAdvance(self._preedit)
            painter.fillRect(QRect(pre_x, pre_y, pre_w, lh), QColor(c['chrome']))
            painter.setPen(QColor(c['primary']))
            painter.drawText(pre_x, pre_y + ascent, self._preedit)
            painter.drawLine(pre_x, pre_y + lh - 1, pre_x + pre_w, pre_y + lh - 1)

        # Draw system status badge overlay
        if self._system_status and self._connected:
            badge_text = self._system_status.replace('\n', ' · ')
            bw = fm.horizontalAdvance(badge_text) + 16
            bh = lh + 6
            bx = self.viewport().width() - bw - 14
            by = 8
            if bx > 0:
                badge_rect = QRect(bx, by, bw, bh)
                painter.setBrush(QBrush(QColor(c['chrome'])))
                painter.setPen(QPen(QColor(c['border']), 1))
                painter.drawRoundedRect(badge_rect, 4, 4)
                painter.setPen(QColor(c['sys']))
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw, lh = self._cell_dimensions()
        cols = max(40, self.viewport().width() // cw)
        rows = max(10, self.viewport().height() // lh)
        self.resize_pty(cols, rows)

    # --- Mouse Selection ---

    def _pos_to_abs_cell(self, pos: QPoint) -> Tuple[int, int]:
        cw, lh = self._cell_dimensions()
        col = max(0, min(self._emulator.cols - 1, pos.x() // cw))
        row_in_view = max(0, min(self._emulator.rows - 1, pos.y() // lh))
        sb_max = self.verticalScrollBar().maximum()
        sb_val = self.verticalScrollBar().value()
        scroll_offset = sb_max - sb_val
        abs_row_start = len(self._emulator.scrollback) - scroll_offset
        return (max(0, abs_row_start + row_in_view), col)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_start = self._pos_to_abs_cell(event.pos())
            self._sel_end = self._sel_start
            self._is_mouse_selecting = True
            self.viewport().update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_mouse_selecting:
            self._sel_end = self._pos_to_abs_cell(event.pos())
            self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_mouse_selecting = False
            if self._sel_start == self._sel_end:
                self._sel_start = None
                self._sel_end = None
            self.viewport().update()
        super().mouseReleaseEvent(event)

    def _get_normalized_selection(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        if not self._sel_start or not self._sel_end or self._sel_start == self._sel_end:
            return None
        (r1, c1), (r2, c2) = self._sel_start, self._sel_end
        if (r1, c1) > (r2, c2):
            return ((r2, c2), (r1, c1))
        return ((r1, c1), (r2, c2))

    def _is_cell_selected(self, abs_row: int, col: int, sel_range) -> bool:
        if not sel_range:
            return False
        (start_r, start_c), (end_r, end_c) = sel_range
        if abs_row < start_r or abs_row > end_r:
            return False
        if abs_row == start_r and abs_row == end_r:
            return start_c <= col <= end_c
        if abs_row == start_r:
            return col >= start_c
        if abs_row == end_r:
            return col <= end_c
        return True

    def _is_cell_find_highlight(self, abs_row: int, col: int) -> int:
        """0 = none, 1 = match, 2 = current active match"""
        for i, (m_row, m_col, m_len) in enumerate(self._find_matches):
            if m_row == abs_row and m_col <= col < m_col + m_len:
                return 2 if i == self._find_index else 1
        return 0

    def copy(self):
        sel = self._get_normalized_selection()
        if not sel:
            return
        (r1, c1), (r2, c2) = sel
        all_rows = self._emulator.scrollback + self._emulator.screen.grid
        lines = []
        for r in range(r1, min(len(all_rows), r2 + 1)):
            row = all_rows[r]
            sc = c1 if r == r1 else 0
            ec = c2 if r == r2 else len(row) - 1
            line_cells = row[sc:ec + 1]
            line_str = ''.join(c.char for c in line_cells if c.width != 0)
            lines.append(line_str)
        text = '\n'.join(lines)
        if text:
            QApplication.clipboard().setText(text)

    def selectAll(self):  # noqa: N802
        total_rows = len(self._emulator.scrollback) + len(self._emulator.screen.grid)
        self._sel_start = (0, 0)
        self._sel_end = (max(0, total_rows - 1), self._emulator.cols - 1)
        self.viewport().update()

    def toPlainText(self) -> str:  # noqa: N802
        return self._emulator.get_plain_text()

    # --- Find In Buffer ---

    def clear_find_highlights(self):
        self._find_matches = []
        self._find_index = -1
        self.viewport().update()

    def find_in_buffer(self, query: str, *, backward: bool = False, case_sensitive: bool = False) -> int:
        self._find_query = query
        if not (query or '').strip():
            self.clear_find_highlights()
            return 0

        all_rows = self._emulator.scrollback + self._emulator.screen.grid
        matches = []
        pattern = query if case_sensitive else query.casefold()

        for r, row in enumerate(all_rows):
            line_str = ''.join(c.char for c in row if c.width != 0)
            hay = line_str if case_sensitive else line_str.casefold()
            start = 0
            while True:
                idx = hay.find(pattern, start)
                if idx < 0:
                    break
                matches.append((r, idx, len(query)))
                start = idx + max(1, len(pattern))

        self._find_matches = matches
        if not matches:
            self._find_index = -1
            self.viewport().update()
            return 0

        if self._find_index < 0 or self._find_index >= len(matches):
            self._find_index = len(matches) - 1 if backward else 0
        else:
            self._find_index = (self._find_index - 1) % len(matches) if backward else (self._find_index + 1) % len(matches)

        # Scroll to match
        target_abs_row, _, _ = matches[self._find_index]
        sb_val = min(self.verticalScrollBar().maximum(), target_abs_row)
        self.verticalScrollBar().setValue(sb_val)
        self.viewport().update()
        return len(matches)

    # --- Chinese IME & Keyboard ---

    def inputMethodEvent(self, event: QInputMethodEvent):  # noqa: N802
        commit = event.commitString()
        if commit and self.shell_alive:
            try:
                self._shell.send(commit.encode('utf-8'))
            except Exception as exc:
                self.append_system(f'[发送失败] {exc}')
        self._preedit = event.preeditString()
        self.viewport().update()

    def inputMethodQuery(self, query: Qt.InputMethodQuery):  # noqa: N802
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            cw, lh = self._cell_dimensions()
            cx = self._emulator.screen.cursor_x
            cy = self._emulator.screen.cursor_y
            return QRect(cx * cw, cy * lh, cw, lh)
        return super().inputMethodQuery(query)

    def keyPressEvent(self, event: QKeyEvent):
        mods = event.modifiers()
        key = event.key()

        # Find shortcut
        if mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_F:
            self._host.show_find()
            event.accept()
            return

        # Copy shortcuts
        if (
            (mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_C)
            or (mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Insert)
        ):
            self.copy()
            event.accept()
            return

        # Paste shortcuts
        if (
            (mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_V)
            or (mods == Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_Insert)
        ):
            self._paste_to_remote()
            event.accept()
            return

        if not self.shell_alive:
            if mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_A:
                self.selectAll()
                event.accept()
                return
            event.ignore()
            return

        data = self._map_key(event)
        if data is None:
            event.ignore()
            return
        try:
            self._shell.send(data)
        except Exception as exc:
            self.append_system(f'[发送失败] {exc}')
        event.accept()

    def _map_key(self, event: QKeyEvent) -> bytes | str | None:
        key = event.key()
        mods = event.modifiers()
        text = event.text()

        # Ctrl+Key combinations
        if mods & Qt.KeyboardModifier.ControlModifier and not (mods & Qt.KeyboardModifier.ShiftModifier):
            if key == Qt.Key.Key_C:
                return b'\x03'
            if key == Qt.Key.Key_D:
                return b'\x04'
            if key == Qt.Key.Key_Z:
                return b'\x1a'
            if key == Qt.Key.Key_L:
                return b'\x0c'
            if key == Qt.Key.Key_U:
                return b'\x15'
            if key == Qt.Key.Key_W:
                return b'\x17'
            if key == Qt.Key.Key_A:
                return b'\x01'
            if key == Qt.Key.Key_E:
                return b'\x05'
            if key == Qt.Key.Key_K:
                return b'\x0b'
            if text and 'a' <= text.lower() <= 'z':
                return bytes([ord(text.lower()) - ord('a') + 1])

        # Alt+Key combination
        if mods & Qt.KeyboardModifier.AltModifier and text:
            return f'\x1b{text}'.encode('utf-8')

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return b'\r'
        if key == Qt.Key.Key_Backspace:
            return b'\x7f'
        if key == Qt.Key.Key_Tab:
            return b'\t'
        if key == Qt.Key.Key_Escape:
            return b'\x1b'
        if key == Qt.Key.Key_Delete:
            return b'\x1b[3~'
        if key == Qt.Key.Key_Home:
            return b'\x1b[H'
        if key == Qt.Key.Key_End:
            return b'\x1b[F'
        if key == Qt.Key.Key_PageUp:
            return b'\x1b[5~'
        if key == Qt.Key.Key_PageDown:
            return b'\x1b[6~'

        # Arrows (support Application Cursor Mode)
        app_cursor = self._emulator.application_cursor_keys
        if key == Qt.Key.Key_Up:
            return b'\x1bOA' if app_cursor else b'\x1b[A'
        if key == Qt.Key.Key_Down:
            return b'\x1bOB' if app_cursor else b'\x1b[B'
        if key == Qt.Key.Key_Right:
            return b'\x1bOC' if app_cursor else b'\x1b[C'
        if key == Qt.Key.Key_Left:
            return b'\x1bOD' if app_cursor else b'\x1b[D'

        if text and text.isprintable():
            return text.encode('utf-8')
        return None

    def _paste_to_remote(self):
        if not self.shell_alive:
            return
        text = QApplication.clipboard().text() or ''
        if not text:
            return
        # Normalize newlines to CR
        normalized = text.replace('\r\n', '\n').replace('\n', '\r')
        if self._emulator.bracketed_paste_mode:
            payload = f'\x1b[200~{normalized}\x1b[201~'.encode('utf-8')
        else:
            payload = normalized.encode('utf-8')
        try:
            self._shell.send(payload)
        except Exception as exc:
            self.append_system(f'[发送失败] {exc}')

    def _show_menu(self, pos):
        menu = QMenu(self)
        copy_act = menu.addAction('复制')
        copy_act.setEnabled(bool(self._get_normalized_selection()))
        paste_act = menu.addAction('粘贴到远端')
        paste_act.setEnabled(self.shell_alive and bool(QApplication.clipboard().text()))
        menu.addSeparator()
        find_act = menu.addAction('查找…')
        select_act = menu.addAction('全选')
        clear_act = menu.addAction('清屏')
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is copy_act:
            self.copy()
        elif chosen is paste_act:
            self._paste_to_remote()
        elif chosen is find_act:
            self._host.show_find()
        elif chosen is select_act:
            self.selectAll()
        elif chosen is clear_act:
            self.clear_and_ready()
