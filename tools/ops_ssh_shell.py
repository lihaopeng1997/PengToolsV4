# -*- coding: utf-8 -*-
"""交互式 SSH Shell（PTY）— 开源自研实时终端通道。

- Paramiko invoke_shell 申请伪终端（LGPL 开源库）
- 后台线程 recv，回调推送输出（调用方用 Qt 信号回主线程）
- 发送按键/字符串；支持 resize_pty
- 自研 ANSI 粗处理，未使用商业终端源码
"""

from __future__ import annotations

import re
import socket
import threading
import time
from typing import Callable, Optional

from tools.ops_ssh import OpsSshError, close_ssh_client, open_ssh_client, paramiko_available

# 粗剥 CSI / OSC 等转义，避免乱码刷屏（完整 xterm 需 pyte）
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;?]*[ -/]*[@-~]'  # CSI
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC
    r'|\x1b[()][0-9A-Za-z]'  # charset
    r'|\x1b[>=]'
    r'|\x1b.'
)


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，但保留 \\x1b[K (EL) 和 \\x1b[J (ED) 供渲染层处理。"""
    if not text:
        return ''
    # 先把 EL/ED 序列替换为占位符，strip 后再恢复
    # 支持 \x1b[K \x1b[0K \x1b[J \x1b[0J 等
    placeholders = {}
    import re as _re
    for m in _re.finditer(r'\x1b\[[0-9]*[KJ]', text):
        token = m.group()
        key = f'\x00EL{len(placeholders)}\x00'
        placeholders[key] = token
        text = text[:m.start()] + key + text[m.end():]
    t = _ANSI_RE.sub('', text)
    t = t.replace('\x00', '')
    # 恢复 EL/ED 序列
    for key, token in placeholders.items():
        t = t.replace(key, token)
    t = t.replace('\x00', '')
    return t


def normalize_terminal_text(text: str) -> str:
    """把 shell 输出规整成适合终端渲染的文本。

    保留 \\r（CR 行重绘）、\\x08(BS)、\\x7f(DEL)、\\t(TAB)、\\n(LF)，
    仅剥离 ANSI 转义序列与无用控制符。
    必须保留 BS/DEL/CR：远端回显退格和行重绘依赖它们，剥掉会导致「能输入不能删除」「Tab 补全乱行」。
    """
    if not text:
        return ''
    t = strip_ansi(text)
    # 不再把 \r 转成 \n！保留 CR 供渲染层做行内重绘
    # 仅去掉无用控制符，保留 \t \n \r \x08 \x7f
    t = re.sub(r'[\x00-\x07\x0b\x0c\x0e-\x1f]', '', t)
    return t


class InteractiveShell:
    """非 Qt 依赖的交互 shell；通过回调交付数据。"""

    def __init__(
        self,
        *,
        on_data: Optional[Callable[[str], None]] = None,
        on_closed: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        term: str = 'xterm-256color',
        width: int = 120,
        height: int = 32,
    ):
        self.on_data = on_data
        self.on_closed = on_closed
        self.on_error = on_error
        self.term = term
        self.width = max(40, int(width or 120))
        self.height = max(10, int(height or 32))
        self._client = None
        self._channel = None
        self._owns_client = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        ch = self._channel
        if ch is None:
            return False
        try:
            return not ch.closed
        except Exception:
            return False

    def attach_client(self, client, *, owns_client: bool = False) -> None:
        """在已有 SSHClient 上打开 shell 通道。"""
        if not paramiko_available():
            raise OpsSshError('未安装 paramiko')
        if client is None:
            raise OpsSshError('SSH 客户端为空')
        self.close()
        self._client = client
        self._owns_client = bool(owns_client)
        self._stop.clear()
        try:
            chan = client.invoke_shell(
                term=self.term,
                width=self.width,
                height=self.height,
            )
            chan.settimeout(0.05)
            # 尽量不要 delay
            try:
                chan.set_combine_stderr(True)
            except Exception:
                pass
            # invoke_shell 返回后仍须校验通道：部分服务器会立即关闭不支持的 PTY，
            # 此时不能把它暴露为“已就绪”的交互终端。
            if bool(getattr(chan, 'closed', False)):
                try:
                    chan.close()
                except Exception:
                    pass
                raise OpsSshError('交互终端通道已关闭')
            self._channel = chan
        except Exception as exc:
            self._channel = None
            raise OpsSshError(f'无法打开交互终端：{exc}') from exc
        self._thread = threading.Thread(
            target=self._read_loop,
            name='ssh-shell-reader',
            daemon=True,
        )
        self._thread.start()

    def connect_server(self, server: dict, password_override: str | None = None, timeout_sec: int = 30) -> None:
        """自行建立连接并打开 shell。"""
        client = open_ssh_client(server, password_override=password_override, timeout_sec=timeout_sec)
        try:
            self.attach_client(client, owns_client=True)
        except Exception:
            close_ssh_client(client)
            raise

    def send(self, data: str | bytes) -> None:
        with self._lock:
            ch = self._channel
        if ch is None or ch.closed:
            raise OpsSshError('终端未连接')
        if isinstance(data, str):
            payload = data.encode('utf-8', errors='replace')
        else:
            payload = data
        if not payload:
            return
        try:
            ch.send(payload)
        except Exception as exc:
            raise OpsSshError(f'发送失败：{exc}') from exc

    def send_text(self, text: str) -> None:
        """发送一行命令（自动补 \\r）。"""
        t = str(text or '')
        if not t.endswith('\r') and not t.endswith('\n'):
            t += '\r'
        elif t.endswith('\n') and not t.endswith('\r\n'):
            t = t[:-1] + '\r'
        self.send(t)

    def resize(self, width: int, height: int) -> None:
        self.width = max(40, int(width or 80))
        self.height = max(10, int(height or 24))
        with self._lock:
            ch = self._channel
        if ch is None or ch.closed:
            return
        try:
            ch.resize_pty(width=self.width, height=self.height)
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            ch = self._channel
            client = self._client
            owns = self._owns_client
            self._channel = None
            self._client = None
            self._owns_client = False
        if ch is not None:
            try:
                ch.close()
            except Exception:
                pass
        if owns and client is not None:
            close_ssh_client(client)
        th = self._thread
        self._thread = None
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=0.8)

    def _emit_data(self, data: bytes | str) -> None:
        if not data:
            return
        cb = self.on_data
        if cb:
            try:
                cb(data)
            except Exception:
                pass

    def _emit_error(self, msg: str) -> None:
        cb = self.on_error
        if cb:
            try:
                cb(str(msg))
            except Exception:
                pass

    def _emit_closed(self) -> None:
        cb = self.on_closed
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _read_loop(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    ch = self._channel
                if ch is None:
                    break
                if ch.closed:
                    break
                data = b''
                try:
                    if ch.recv_ready():
                        data = ch.recv(4096)
                    elif ch.exit_status_ready():
                        # drain
                        while ch.recv_ready():
                            chunk = ch.recv(4096)
                            if not chunk:
                                break
                            self._emit_data(chunk)
                        break
                    else:
                        time.sleep(0.005)
                        continue
                except socket.timeout:
                    continue
                except Exception as exc:
                    if not self._stop.is_set():
                        self._emit_error(str(exc))
                    break
                if not data:
                    if ch.closed or ch.exit_status_ready():
                        break
                    continue
                self._emit_data(data)
        finally:
            if not self._stop.is_set():
                self._emit_closed()
