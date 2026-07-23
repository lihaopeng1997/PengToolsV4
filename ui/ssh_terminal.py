# -*- coding: utf-8 -*-
"""简易 SSH 终端（开源自研：PTY 直通 + 右键复制粘贴/查找 + 省内存）。

视觉原则：终端是独立「控制台岛」——与浅色页面强对比，色相贴主题主色（绿/蓝/棕/薄荷），
避免与侧栏/卡片糊成同色，也不用刺眼的纯霓虹。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeyEvent, QTextCursor, QColor, QPalette, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget, QCheckBox, QTextEdit,
)

from tools.ops_ssh_shell import InteractiveShell

_MAX_BLOCKS = 10000
_MAX_PENDING = 256 * 1024
# 稍合并刷新，减少连删时的 UI 重绘次数
_FLUSH_MS = 50


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
        # 终端内查找：深底上用琥珀高亮
        'find': '#5C4A18',
        'find_cur': '#8A6F1E',
    }


class _ShellBridge(QObject):
    data = pyqtSignal(str)
    closed = pyqtSignal()
    error = pyqtSignal(str)


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


class _SshTerminalView(QPlainTextEdit):
    """只显示服务器回显；键盘发给 PTY。"""

    def __init__(self, host: SshTerminalWidget):
        super().__init__(host)
        self._host = host
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setMaximumBlockCount(_MAX_BLOCKS)
        mono = QFont('Cascadia Mono')
        if not mono.exactMatch():
            mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.setFont(mono)
        self.setObjectName('ssh-terminal')
        try:
            self.setFrameShape(QFrame.Shape.NoFrame)
        except Exception:
            pass
        self._apply_terminal_palette()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setViewportMargins(6, 4, 6, 4)

        self._bridge = _ShellBridge(self)
        self._bridge.data.connect(self._on_data)
        self._bridge.closed.connect(self._on_shell_closed)
        self._bridge.error.connect(self._on_shell_error)
        self._shell: InteractiveShell | None = None
        self._connected = False
        self._pending = ''
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._find_spans: list[tuple[int, int]] = []
        self._find_index = -1
        self._ui_active = True
        self._colors = _theme_term_colors()

        self.setPlaceholderText('未连接 — 选择服务器并点击「连接」后，可在此输入命令')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
        # 内联样式保证即时生效，与 QSS 双保险
        self.setStyleSheet(
            f"QPlainTextEdit#ssh-terminal {{"
            f" background-color: {c['bg']};"
            f" color: {c['fg']};"
            f" border: none;"
            f" border-radius: 12px;"
            f" selection-background-color: {c['sel']};"
            f" selection-color: {c['fg']};"
            f" padding: 10px 12px;"
            f" font-family: 'Cascadia Mono','Consolas',monospace;"
            f" font-size: 10.5pt;"
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

    @property
    def shell_alive(self) -> bool:
        return bool(self._shell and self._shell.alive and self._connected)

    def set_ui_active(self, active: bool) -> None:
        self._ui_active = bool(active)
        if active and self._pending:
            self._flush_pending()

    def attach_client(self, client, *, cols: int = 120, rows: int = 32) -> None:
        self.detach()
        bridge = self._bridge

        def on_data(text: str):
            bridge.data.emit(text)

        def on_closed():
            bridge.closed.emit()

        def on_error(msg: str):
            bridge.error.emit(msg)

        shell = InteractiveShell(
            on_data=on_data, on_closed=on_closed, on_error=on_error,
            width=cols, height=rows,
        )
        shell.attach_client(client, owns_client=False)
        self._shell = shell
        self._connected = True
        self.setReadOnly(True)
        self.append_system(
            '[终端已就绪] 可直接输入命令。Ctrl+C 中断 · 右键复制/粘贴 · Ctrl+F 查找\n'
        )
        self.setFocus()

    def detach(self) -> None:
        shell = self._shell
        self._shell = None
        self._connected = False
        self._pending = ''
        self._flush_timer.stop()
        if shell is not None:
            try:
                shell.close()
            except Exception:
                pass

    def append_system(self, text: str) -> None:
        self.moveCursor(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._colors.get('sys') or self._colors['muted']))
        cursor = self.textCursor()
        cursor.setCharFormat(fmt)
        cursor.insertText(text)
        plain = QTextCharFormat()
        plain.setForeground(QColor(self._colors['fg']))
        cursor.setCharFormat(plain)
        self.setTextCursor(cursor)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def clear_and_ready(self) -> None:
        """清屏后保留一行待录入提示（只清本地显示，不发 clear 到远端）。"""
        self.clear()
        if self.shell_alive:
            self.append_system('[已清屏]  ')
            self.moveCursor(QTextCursor.MoveOperation.End)
            plain = QTextCharFormat()
            plain.setForeground(QColor(self._colors['fg']))
            cur = self.textCursor()
            cur.setCharFormat(plain)
            cur.insertText('$ ')
            self.setTextCursor(cur)
            self.moveCursor(QTextCursor.MoveOperation.End)
        else:
            self.setPlaceholderText('未连接 — 选择服务器并点击「连接」后，可在此输入命令')

    def send_command_line(self, text: str) -> None:
        if not self.shell_alive:
            return
        payload = str(text or '')
        if payload and not payload.endswith('\n') and not payload.endswith('\r'):
            payload = payload + '\n'
        try:
            self._shell.send_text(payload)
        except Exception as exc:
            self.append_system(f'\n[发送失败] {exc}\n')

    def resize_pty(self, cols: int, rows: int) -> None:
        if self._shell:
            try:
                self._shell.resize(cols, rows)
            except Exception:
                pass

    def _on_data(self, text: str):
        if not text:
            return
        self._pending += text
        if len(self._pending) > _MAX_PENDING:
            self._pending = self._pending[-_MAX_PENDING // 2:]
            self._pending = '\n…[输出过快已截断]…\n' + self._pending
        if not self._ui_active:
            if len(self._pending) > _MAX_PENDING // 2:
                self._pending = self._pending[-_MAX_PENDING // 4:]
            return
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self):
        if not self._pending:
            self._flush_timer.stop()
            return
        if not self._ui_active:
            return
        chunk, self._pending = self._pending, ''
        if '\x08' in chunk or '\x7f' in chunk:
            self._write_with_backspace(chunk)
        else:
            self.moveCursor(QTextCursor.MoveOperation.End)
            plain = QTextCharFormat()
            plain.setForeground(QColor(self._colors['fg']))
            cur = self.textCursor()
            cur.setCharFormat(plain)
            self.setTextCursor(cur)
            self.insertPlainText(chunk)
            self.moveCursor(QTextCursor.MoveOperation.End)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _delete_prev_char(self) -> bool:
        """删除光标前一个可显示字符。"""
        self.moveCursor(QTextCursor.MoveOperation.End)
        cursor = self.textCursor()
        if cursor.position() <= 0:
            return False
        cursor.deletePreviousChar()
        self.setTextCursor(cursor)
        return True

    def _write_with_backspace(self, text: str):
        """批量处理远端 BS/DEL，避免连删时逐字符刷屏卡顿。

        bash 常见擦除序列：\\x08 空格 \\x08 → 计 1 次删除。
        合并为 beginEditBlock + 批量 deletePreviousChar。
        """
        if not text:
            return
        self.setUpdatesEnabled(False)
        try:
            self.moveCursor(QTextCursor.MoveOperation.End)
            cursor = self.textCursor()
            plain = QTextCharFormat()
            plain.setForeground(QColor(self._colors['fg']))
            cursor.beginEditBlock()
            try:
                i = 0
                n = len(text)
                while i < n:
                    ch = text[i]
                    if ch in ('\x08', '\x7f'):
                        deletes = 0
                        while i < n:
                            if text[i] in ('\x08', '\x7f'):
                                deletes += 1
                                i += 1
                                # bash: BS + space + BS = 视觉上一次擦除
                                if (
                                    i + 1 < n
                                    and text[i] == ' '
                                    and text[i + 1] in ('\x08', '\x7f')
                                ):
                                    i += 2
                            else:
                                break
                        for _ in range(deletes):
                            if cursor.position() <= 0:
                                break
                            cursor.deletePreviousChar()
                    elif ch == '\n':
                        cursor.setCharFormat(plain)
                        cursor.insertText('\n')
                        i += 1
                    else:
                        j = i
                        while j < n and text[j] not in ('\x08', '\x7f', '\n'):
                            j += 1
                        if j > i:
                            cursor.setCharFormat(plain)
                            cursor.insertText(text[i:j])
                        i = j
            finally:
                cursor.endEditBlock()
                self.setTextCursor(cursor)
            self.moveCursor(QTextCursor.MoveOperation.End)
        finally:
            self.setUpdatesEnabled(True)

    def _on_shell_closed(self):
        self._connected = False
        self.append_system('\n[会话已断开]\n')

    def _on_shell_error(self, msg: str):
        self.append_system(f'\n[终端错误] {msg}\n')

    def _show_menu(self, pos):
        menu = QMenu(self)
        copy_act = menu.addAction('复制')
        copy_act.setEnabled(self.textCursor().hasSelection())
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

    def _paste_to_remote(self):
        if not self.shell_alive:
            return
        text = QApplication.clipboard().text() or ''
        if not text:
            return
        text = text.replace('\r\n', '\n').replace('\n', '\r')
        try:
            self._shell.send(text)
        except Exception as exc:
            self.append_system(f'\n[粘贴失败] {exc}\n')

    def clear_find_highlights(self):
        self._find_spans = []
        self._find_index = -1
        self.setExtraSelections([])

    def find_in_buffer(self, query: str, *, backward: bool = False, case_sensitive: bool = False) -> int:
        text = self.toPlainText()
        if not (query or '').strip() or not text:
            self.clear_find_highlights()
            return 0
        hay = text if case_sensitive else text.casefold()
        needle = query if case_sensitive else query.casefold()
        spans = []
        start = 0
        while True:
            i = hay.find(needle, start)
            if i < 0:
                break
            spans.append((i, i + len(query)))
            start = i + max(1, len(needle))
        self._find_spans = spans
        if not spans:
            self.setExtraSelections([])
            self._find_index = -1
            return 0
        if self._find_index < 0 or self._find_index >= len(spans):
            self._find_index = len(spans) - 1 if backward else 0
        else:
            self._find_index = (self._find_index - 1) % len(spans) if backward else (self._find_index + 1) % len(spans)
        self._apply_find_highlights()
        return len(spans)

    def _apply_find_highlights(self):
        c = self._colors
        match_bg = QColor(c['find'])
        curr_bg = QColor(c['find_cur'])
        sels = []
        for i, (a, b) in enumerate(self._find_spans):
            cur = QTextCursor(self.document())
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            fmt = QTextCharFormat()
            fmt.setBackground(curr_bg if i == self._find_index else match_bg)
            fmt.setForeground(QColor(c['fg']))
            sel.format = fmt
            sels.append(sel)
        self.setExtraSelections(sels)
        if 0 <= self._find_index < len(self._find_spans):
            a, _b = self._find_spans[self._find_index]
            c2 = QTextCursor(self.document())
            c2.setPosition(a)
            self.setTextCursor(c2)
            self.ensureCursorVisible()

    def keyPressEvent(self, event: QKeyEvent):
        mods = event.modifiers()
        key = event.key()

        if mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_F:
            self._host.show_find()
            event.accept()
            return

        if (
            (mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_C)
            or (mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Insert)
        ):
            self.copy()
            event.accept()
            return

        if (
            (mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_V)
            or (mods == Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_Insert)
        ):
            self._paste_to_remote()
            event.accept()
            return

        if not self.shell_alive:
            if mods == Qt.KeyboardModifier.ControlModifier and key in (Qt.Key.Key_C, Qt.Key.Key_A):
                super().keyPressEvent(event)
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
            self.append_system(f'\n[发送失败] {exc}\n')
        event.accept()

    def _map_key(self, event: QKeyEvent) -> bytes | str | None:
        key = event.key()
        mods = event.modifiers()
        text = event.text()

        if mods & Qt.KeyboardModifier.ControlModifier and not (mods & Qt.KeyboardModifier.ShiftModifier):
            if key == Qt.Key.Key_C:
                return '\x03'
            if key == Qt.Key.Key_D:
                return '\x04'
            if key == Qt.Key.Key_Z:
                return '\x1a'
            if key == Qt.Key.Key_L:
                return '\x0c'
            if key == Qt.Key.Key_U:
                # 清本行：本地尽量清到行首再交给远端
                self.moveCursor(QTextCursor.MoveOperation.End)
                cur = self.textCursor()
                block = cur.block()
                while cur.position() > block.position():
                    cur.deletePreviousChar()
                self.setTextCursor(cur)
                return '\x15'
            if key == Qt.Key.Key_W:
                return '\x17'
            if key == Qt.Key.Key_A:
                return '\x01'
            if key == Qt.Key.Key_E:
                return '\x05'
            if key == Qt.Key.Key_K:
                return '\x0b'
            if text and 'a' <= text.lower() <= 'z':
                return bytes([ord(text.lower()) - ord('a') + 1])

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return '\r'
        if key == Qt.Key.Key_Backspace:
            # 多数 Linux + xterm 用 DEL；同时很多配置用 BS
            return '\x7f'
        if key == Qt.Key.Key_Tab:
            return '\t'
        if key == Qt.Key.Key_Escape:
            return '\x1b'
        if key == Qt.Key.Key_Delete:
            return '\x1b[3~'
        if key == Qt.Key.Key_Home:
            return '\x1b[H'
        if key == Qt.Key.Key_End:
            return '\x1b[F'
        if key == Qt.Key.Key_PageUp:
            return '\x1b[5~'
        if key == Qt.Key.Key_PageDown:
            return '\x1b[6~'
        if key == Qt.Key.Key_Up:
            return '\x1b[A'
        if key == Qt.Key.Key_Down:
            return '\x1b[B'
        if key == Qt.Key.Key_Right:
            return '\x1b[C'
        if key == Qt.Key.Key_Left:
            return '\x1b[D'
        if text and text.isprintable():
            return text
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            fm = self.fontMetrics()
            cols = max(40, self.viewport().width() // max(1, fm.horizontalAdvance('M')))
            rows = max(10, self.viewport().height() // max(1, fm.lineSpacing()))
            self.resize_pty(cols, rows)
        except Exception:
            pass
