# -*- coding: utf-8 -*-
"""单实例守卫：QLockFile 所有权 + QLocalServer/QLocalSocket 本机 IPC。

Identity 语义（Step 4C）：
- 服务名跨 APP_VERSION 稳定：PengToolsHub.<edition>。同一 edition 升级换版本后
  仍是同一个桌面实例域；不同 edition 按既有产品设计继续隔离。
- version 参数仅为兼容旧签名保留，不参与 identity。

为什么所有权不能只靠 QLocalServer.listen（Step 4C 实测结论）：
Windows 命名管道默认允许多服务实例（CreateNamedPipe 不带
FILE_FLAG_FIRST_PIPE_INSTANCE 时同名可重复创建），两个进程同时启动时
listen() 可能双双成功 → 双主实例。这正是历史上“能同时多开”的根因。
因此所有权判定改用 QLockFile（Qt 原生，无第三方依赖）：
- race：tryLock 走平台级独占打开，多进程同时竞争恰好一个成功；
- crash：持有者异常退出后锁文件虽残留，QLockFile 按 PID 活性检测判定陈旧
  （不是“文件存在即被锁”），下一次 tryLock 自动清理并取得所有权；
- release：正常退出 unlock + 删除锁文件，同 identity 可立即重新接管。
QLocalServer 只承担“通知已有实例”的 IPC 职责。

状态机（ownership-first，杜绝 notify 失败即无条件 remove 活 endpoint）：
1. tryLock(0) 成功 → 取得所有权 → 建立 IPC server → PRIMARY。
   （若管道名被占：先有限重试通知——可能存在升级前未用锁的旧版本实例；
     无法通知才按残留清理 removeServer 后重试一次；彻底失败 fail closed。）
2. tryLock 失败（所有权被占）→ 有限重试（3×250ms，禁止大 sleep）通知
   existing；任一次成功 → SECONDARY（activate 已送达），调用方立即退出。
3. 通知全部失败 → 持有者可能恰好退出：再给一次 tryLock 机会；仍失败 →
   明确失败。任何无法证明唯一性的路径都 fail closed，绝不无守卫启动。
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable, Optional

from PyQt6.QtCore import QLockFile, QObject, Qt, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from config import APP_EDITION, APP_VERSION


ACTIVATE_MESSAGE = b'activate\n'


def local_server_name(edition: Optional[str] = None, version: Optional[str] = None) -> str:
    """生成跨版本稳定的本机实例 identity：PengToolsHub.<edition>。

    version 参数仅为兼容旧调用签名保留，明确不参与 identity——
    同一 edition 的 4.27 与 4.28 必须互斥，不能各开一套主实例。
    """
    ed = (edition or APP_EDITION or 'Private').strip() or 'Private'
    # 仅保留安全字符，避免锁文件 / QLocalServer 路径问题
    safe_ed = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in ed)
    return f'PengToolsHub.{safe_ed}'


def lock_file_path(server_name: str) -> str:
    """所有权锁文件路径（用户临时目录，随 identity 隔离）。"""
    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in server_name)
    return os.path.join(tempfile.gettempdir(), f'{safe}.lock')


class SingleInstanceGuard(QObject):
    """首进程持有所有权锁 + IPC server；次进程通知 existing 后立即退出。"""

    activate_requested = pyqtSignal()

    # 通知已有实例的有限重试（短等待，禁止大 sleep）
    NOTIFY_ATTEMPTS = 3
    NOTIFY_WAIT_MS = 250

    def __init__(self, server_name: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.server_name = server_name or local_server_name()
        self._lock = QLockFile(lock_file_path(self.server_name))
        self._server: Optional[QLocalServer] = None
        self._is_primary = False
        self.last_error = ''
        # per-connection 状态：缓存分片数据 + exactly-once 处理标记
        self._sock_states: dict[int, dict] = {}

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def try_become_primary(self) -> bool:
        """尝试成为主实例。True=PRIMARY（已持有唯一所有权）；False=SECONDARY
        （activate 已送达已有实例）或守卫失败（fail closed，调用方必须放弃启动）。"""
        # 阶段 1：所有权竞争（平台级原子；崩溃持有者由 PID 活性检测自动判陈旧）
        if self._acquire_lock():
            return self._setup_after_ownership()
        # 阶段 2：所有权被占 → 有限重试通知已有实例
        for _ in range(self.NOTIFY_ATTEMPTS):
            if self._notify_existing():
                self._is_primary = False
                self.last_error = ''
                return False
        # 阶段 3：通知全部失败 → 持有者可能恰好退出（陈旧锁已被 tryLock 清理判定），
        # 再给一次所有权机会；仍失败则 fail closed。
        if self._acquire_lock():
            return self._setup_after_ownership()
        self._is_primary = False
        self.last_error = (
            f'已有实例占用 {self.server_name} 且暂时无法联系，'
            '为避免双开已放弃本次启动'
        )
        return False

    def _acquire_lock(self) -> bool:
        """非阻塞尝试取得所有权锁。成功后本进程是唯一所有者。"""
        if self._lock.tryLock(0):
            return True
        self.last_error = f'实例所有权被其它进程持有：{lock_file_path(self.server_name)}'
        return False

    def _setup_after_ownership(self) -> bool:
        """已持所有权：建立 IPC server 并进入 PRIMARY。

        管道名被占时优先通知（可能是升级前未带锁的旧版本实例仍在运行），
        无法通知才按残留清理 removeServer 重试一次；彻底失败则 fail closed
        （释放锁并放弃启动，绝不无守卫运行）。
        """
        if self._start_server():
            return True
        for _ in range(self.NOTIFY_ATTEMPTS):
            if self._notify_existing():
                self._release_lock()
                self._is_primary = False
                self.last_error = ''
                return False
        try:
            QLocalServer.removeServer(self.server_name)
        except Exception:
            pass
        if self._start_server():
            return True
        self._release_lock()
        self._is_primary = False
        self.last_error = (
            f'IPC listen {self.server_name} 失败且无法联系已有实例，'
            '为避免双开已放弃本次启动'
        )
        return False

    def _start_server(self) -> bool:
        """建立 IPC server（仅所有权持有者允许调用）。"""
        server = QLocalServer(self)
        if not server.listen(self.server_name):
            self.last_error = f'listen {self.server_name} 失败：{server.errorString()}'
            try:
                server.close()
            except Exception:
                pass
            return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        self._is_primary = True
        self.last_error = ''
        return True

    def _notify_existing(self) -> bool:
        """连接已有实例并发送 activate。连接成功即返回 True（无论写入细节）。"""
        sock = QLocalSocket()
        sock.connectToServer(self.server_name)
        if not sock.waitForConnected(self.NOTIFY_WAIT_MS):
            sock.abort()
            return False
        sock.write(ACTIVATE_MESSAGE)
        sock.flush()
        sock.waitForBytesWritten(self.NOTIFY_WAIT_MS)
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            sock.waitForDisconnected(self.NOTIFY_WAIT_MS)
        return True

    def _on_new_connection(self):
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            state = self._sock_states.setdefault(
                id(sock), {'buf': bytearray(), 'handled': False})
            # 同步先读一次：Windows 命名管道上，客户端 write+disconnect 极快时
            # 数据往往已进入缓冲（即便 bytesAvailable()/state 显示已断开也可读），
            # 而事件循环对 readyRead/disconnected 的派发时机并不可靠。
            # 同步路径直接处理绝大多数一次成包的场景。
            self._consume_socket(sock, state)
            if state['handled']:
                self._forget_socket(sock)
                continue
            # 残余/分片：挂信号兜底（exactly-once 由 handled 标记保证）
            sock.readyRead.connect(lambda s=sock: self._on_socket_ready(s))
            sock.disconnected.connect(lambda s=sock: self._on_socket_ready(s))

    def _forget_socket(self, sock: QLocalSocket):
        self._sock_states.pop(id(sock), None)
        try:
            sock.deleteLater()
        except Exception:
            pass

    def _consume_socket(self, sock: QLocalSocket, state: dict) -> None:
        """读缓冲并按 exactly-once 语义处理一次 activate。"""
        try:
            state['buf'] += bytes(sock.readAll())
        except Exception:
            pass
        if state['handled']:
            return
        buf = bytes(state['buf'])
        # 时序事实（Step 4C-A 实测）：newConnection 在客户端 connect 完成瞬间
        # 触发，此时 write 尚未发生（buf 为空、ConnectedState）；而数据到达前
        # 挂上的 readyRead 派发不可靠，且客户端 write+disconnect 极快——断开
        # 通知到达时 Qt 已丢弃未读缓冲。因此首包为空且仍连接时，用有界
        # waitForReadyRead 等待首包（与客户端侧 waitFor* 对称，非 sleep 掩盖）。
        if not buf and sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            try:
                sock.waitForReadyRead(self.NOTIFY_WAIT_MS)
            except Exception:
                pass
            try:
                state['buf'] += bytes(sock.readAll())
            except Exception:
                pass
        if ACTIVATE_MESSAGE.strip() in bytes(state['buf']) or b'activate' in bytes(state['buf']).lower():
            state['handled'] = True
            self.activate_requested.emit()

    def _on_socket_ready(self, sock: QLocalSocket):
        """readyRead / disconnected 共用兜底入口（分片与最终缓冲）。"""
        state = self._sock_states.get(id(sock))
        if state is None:
            return
        self._consume_socket(sock, state)
        disconnected = sock.state() == QLocalSocket.LocalSocketState.UnconnectedState
        if state['handled'] or disconnected:
            # 到断开为止仍无 activate 内容（空/未知数据）不激活；
            # 已处理过则就此收尾。两者都清理 socket 状态。
            self._forget_socket(sock)

    def release(self):
        """应用退出时释放所有权与本地服务。最小化到托盘不调用。"""
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            try:
                QLocalServer.removeServer(self.server_name)
            except Exception:
                pass
            self._server = None
        try:
            self._lock.unlock()
        except Exception:
            pass
        self._is_primary = False


def notify_and_exit_if_secondary(
    app,
    server_name: Optional[str] = None,
) -> Optional[SingleInstanceGuard]:
    """若已有实例则通知并返回 None；否则返回已启动的 Guard。"""
    guard = SingleInstanceGuard(server_name=server_name, parent=app)
    if not guard.try_become_primary():
        return None
    return guard


def wire_activate_handler(
    guard: SingleInstanceGuard,
    window,
    *,
    message: str = 'PengTools 已打开，已为你切换到正在运行的窗口。',
    title: str = 'PengTools',
):
    """把 activate 信号接到主窗口：按当前状态恢复可见并请求置前。

    语义（Step 4C）：最小化 → 仅清除最小化位（保留最大化状态）；隐藏（托盘）
    → show() 恢复；仅被遮挡 → 直接 raise/activate。绝不对已最大化窗口调用
    showNormal() 把它强制拉回普通尺寸。
    """

    def _activate():
        try:
            if window.isMinimized():
                # 仅恢复可见性；原最大化位保留，窗口回到最大化而不是普通尺寸
                window.setWindowState(
                    window.windowState() & ~Qt.WindowState.WindowMinimized)
            elif window.isHidden():
                window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            pass
        # 非阻塞提示：优先托盘气泡，其次状态栏
        try:
            tray = getattr(window, 'tray_service', None)
            if tray is not None and hasattr(tray, 'show_notification'):
                tray.show_notification(title, message)
                return
        except Exception:
            pass
        try:
            if hasattr(window, 'status_bar') and window.status_bar is not None:
                window.status_bar.showMessage(message, 5000)
        except Exception:
            pass

    guard.activate_requested.connect(_activate)
    return _activate
