# -*- coding: utf-8 -*-
"""交互式 SSH Shell（PTY）— 开源自研实时终端通道。

- Paramiko invoke_shell 申请伪终端（LGPL 开源库）
- 后台线程 recv，回调推送输出（调用方用 Qt 信号回主线程）
- 发送按键/字符串；支持 resize_pty
- 自研 ANSI 粗处理，未使用商业终端源码
"""

from __future__ import annotations

import queue
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


class _SessionState:
    """每个活跃会话的独立 I/O 状态，确保线程生命周期与通道严格隔离。"""

    def __init__(self, session_id: int, channel, client, owns_client: bool):
        self.session_id = session_id
        self.channel = channel
        self.client = client
        self.owns_client = owns_client
        self.stop_event = threading.Event()
        self.write_queue: queue.Queue = queue.Queue(maxsize=4096)
        self.reader_thread: Optional[threading.Thread] = None
        self.writer_thread: Optional[threading.Thread] = None

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.write_queue.put_nowait(None)
        except Exception:
            pass
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass
        if self.owns_client and self.client is not None:
            close_ssh_client(self.client)
        th = self.reader_thread
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=0.3)
        wth = self.writer_thread
        if wth and wth.is_alive() and wth is not threading.current_thread():
            wth.join(timeout=0.3)


class InteractiveShell:
    """非 Qt 依赖的交互 shell；通过回调交付数据。"""

    def __init__(
        self,
        *,
        on_data: Optional[Callable[[bytes | str], None]] = None,
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
        self._session_counter = 0
        self._current_session: Optional[_SessionState] = None
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        with self._lock:
            sess = self._current_session
        if sess is None or sess.stop_event.is_set():
            return False
        ch = sess.channel
        if ch is None:
            return False
        try:
            return not getattr(ch, 'closed', False)
        except Exception:
            return False

    @property
    def _channel(self):
        with self._lock:
            return self._current_session.channel if self._current_session else None

    @_channel.setter
    def _channel(self, chan):
        with self._lock:
            if chan is None:
                if self._current_session is not None:
                    self._current_session.channel = None
                return
            self._session_counter += 1
            sess = _SessionState(self._session_counter, chan, None, False)
            self._current_session = sess
            sess.writer_thread = threading.Thread(
                target=self._write_loop,
                args=(sess,),
                name=f'ssh-shell-writer-{sess.session_id}',
                daemon=True,
            )
            sess.writer_thread.start()

    @property
    def _client(self):
        with self._lock:
            return self._current_session.client if self._current_session else None

    @property
    def _owns_client(self):
        with self._lock:
            return self._current_session.owns_client if self._current_session else False

    @property
    def _stop(self):
        with self._lock:
            if self._current_session is not None:
                return self._current_session.stop_event
            evt = threading.Event()
            evt.set()
            return evt

    @property
    def _write_queue(self):
        with self._lock:
            return self._current_session.write_queue if self._current_session else None

    @property
    def _thread(self):
        with self._lock:
            return self._current_session.reader_thread if self._current_session else None

    @property
    def _writer_thread(self):
        with self._lock:
            return self._current_session.writer_thread if self._current_session else None

    def attach_client(self, client, *, owns_client: bool = False) -> None:
        """在已有 SSHClient 上打开 shell 通道。"""
        if not paramiko_available():
            raise OpsSshError('未安装 paramiko')
        if client is None:
            raise OpsSshError('SSH 客户端为空')

        # 1. 关停并孤立旧 session（旧 session 的 stop_event 被 set，绝不再被重置）
        with self._lock:
            old_sess = self._current_session
            self._current_session = None
        if old_sess is not None:
            old_sess.close()

        try:
            chan = client.invoke_shell(
                term=self.term,
                width=self.width,
                height=self.height,
            )
            if hasattr(chan, 'settimeout'):
                chan.settimeout(0.05)
            try:
                chan.set_combine_stderr(True)
            except Exception:
                pass
            if bool(getattr(chan, 'closed', False)):
                try:
                    chan.close()
                except Exception:
                    pass
                raise OpsSshError('交互终端通道已关闭')
        except Exception as exc:
            raise OpsSshError(f'无法打开交互终端：{exc}') from exc

        # 2. 为新 session 创建全新独立状态
        with self._lock:
            self._session_counter += 1
            sess = _SessionState(self._session_counter, chan, client, owns_client)
            self._current_session = sess

        sess.reader_thread = threading.Thread(
            target=self._read_loop,
            args=(sess,),
            name=f'ssh-shell-reader-{sess.session_id}',
            daemon=True,
        )
        sess.reader_thread.start()

        sess.writer_thread = threading.Thread(
            target=self._write_loop,
            args=(sess,),
            name=f'ssh-shell-writer-{sess.session_id}',
            daemon=True,
        )
        sess.writer_thread.start()

    def connect_server(self, server: dict, password_override: str | None = None, timeout_sec: int = 30) -> None:
        """自行建立连接并打开 shell。"""
        client = open_ssh_client(server, password_override=password_override, timeout_sec=timeout_sec)
        try:
            self.attach_client(client, owns_client=True)
        except Exception:
            close_ssh_client(client)
            raise

    def send(self, data: str | bytes) -> None:
        """非阻塞按键/字节入队；不在主线程执行同步网络写。"""
        with self._lock:
            sess = self._current_session
            if sess is None or sess.stop_event.is_set():
                raise OpsSshError('终端未连接')
            ch = sess.channel
            if ch is None or getattr(ch, 'closed', False):
                raise OpsSshError('终端未连接')
            if (sess.writer_thread is None or not sess.writer_thread.is_alive()) and not sess.stop_event.is_set():
                sess.writer_thread = threading.Thread(
                    target=self._write_loop,
                    args=(sess,),
                    name=f'ssh-shell-writer-{sess.session_id}',
                    daemon=True,
                )
                sess.writer_thread.start()
            q = sess.write_queue
        if isinstance(data, str):
            payload = data.encode('utf-8', errors='replace')
        else:
            payload = bytes(data)
        if not payload:
            return
        try:
            q.put_nowait(payload)
        except queue.Full as exc:
            raise OpsSshError('终端发送队列已满') from exc

    def _write_loop(self, sess: _SessionState) -> None:
        """专用后台写线程：严格保证当前 session FIFO 顺序交付到底层 Channel。"""
        stop_event = sess.stop_event
        write_queue = sess.write_queue
        ch = sess.channel
        while not stop_event.is_set():
            try:
                item = write_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is None:
                break
            if ch is None or getattr(ch, 'closed', False):
                break
            try:
                view = memoryview(item)
                while len(view) > 0 and not stop_event.is_set():
                    sent = ch.send(view)
                    if sent <= 0:
                        break
                    view = view[sent:]
            except Exception as exc:
                if not stop_event.is_set():
                    self._emit_error(f'发送失败：{exc}')
                break

    def send_text(self, text: str) -> None:
        """发送一行命令（自动补 \\r）。"""
        t = str(text or '')
        if not t.endswith('\r') and not t.endswith('\n'):
            t += '\r'
        elif t.endswith('\n') and not t.endswith('\r\n'):
            t = t[:-1] + '\r'
        self.send(t)

    def resize(self, width: int, height: int) -> None:
        self.width = max(1, int(width or 1))
        self.height = max(1, int(height or 1))
        with self._lock:
            sess = self._current_session
            ch = sess.channel if sess is not None else None
        if ch is None or getattr(ch, 'closed', False):
            return
        try:
            ch.resize_pty(width=self.width, height=self.height)
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            sess = self._current_session
            self._current_session = None
        if sess is not None:
            sess.close()

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

    def _read_loop(self, sess: _SessionState) -> None:
        stop_event = sess.stop_event
        ch = sess.channel
        try:
            while not stop_event.is_set():
                if ch is None or getattr(ch, 'closed', False):
                    break
                data = b''
                try:
                    if ch.recv_ready():
                        data = ch.recv(4096)
                    elif ch.exit_status_ready():
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
                    if not stop_event.is_set():
                        self._emit_error(str(exc))
                    break
                if not data:
                    if getattr(ch, 'closed', False) or ch.exit_status_ready():
                        break
                    continue
                self._emit_data(data)
        finally:
            if not stop_event.is_set():
                self._emit_closed()
