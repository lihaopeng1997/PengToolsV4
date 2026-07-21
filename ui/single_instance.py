# -*- coding: utf-8 -*-
"""单实例守卫：QLocalServer / QLocalSocket 本机 IPC。

Private 与标准版使用不同服务名，互不冲突。
进程异常退出后，下次启动可安全接管同名服务。
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from config import APP_EDITION, APP_VERSION


ACTIVATE_MESSAGE = b'activate\n'


def local_server_name(edition: Optional[str] = None, version: Optional[str] = None) -> str:
    """生成稳定且区分版本/版本号的本机服务名。"""
    ed = (edition or APP_EDITION or 'Private').strip() or 'Private'
    ver = (version or APP_VERSION or '4.27').strip() or '4.27'
    # 仅保留安全字符，避免 QLocalServer 路径问题
    safe_ed = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in ed)
    safe_ver = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in ver)
    return f'PengToolsHub.{safe_ed}.{safe_ver}'


class SingleInstanceGuard(QObject):
    """首进程监听；次进程连接后发送 activate 并应立即退出。"""

    activate_requested = pyqtSignal()

    def __init__(self, server_name: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.server_name = server_name or local_server_name()
        self._server: Optional[QLocalServer] = None
        self._is_primary = False

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def try_become_primary(self) -> bool:
        """尝试成为主实例。成功返回 True；已有主实例返回 False。"""
        if self._notify_existing():
            self._is_primary = False
            return False
        return self._start_server()

    def _notify_existing(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.server_name)
        if not sock.waitForConnected(400):
            sock.abort()
            return False
        sock.write(ACTIVATE_MESSAGE)
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            sock.waitForDisconnected(400)
        return True

    def _start_server(self) -> bool:
        server = QLocalServer(self)
        # 清理可能残留的同名服务器（异常退出后的僵尸）
        try:
            QLocalServer.removeServer(self.server_name)
        except Exception:
            pass
        if not server.listen(self.server_name):
            # 再试一次清理
            try:
                QLocalServer.removeServer(self.server_name)
            except Exception:
                pass
            if not server.listen(self.server_name):
                self._is_primary = False
                return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        self._is_primary = True
        return True

    def _on_new_connection(self):
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            sock.readyRead.connect(lambda s=sock: self._on_socket_ready(s))
            # 即便无数据也尝试读（部分平台连上即写）
            if sock.bytesAvailable() > 0:
                self._on_socket_ready(sock)

    def _on_socket_ready(self, sock: QLocalSocket):
        try:
            data = bytes(sock.readAll())
        except Exception:
            data = b''
        try:
            sock.disconnectFromServer()
        except Exception:
            pass
        if not data or ACTIVATE_MESSAGE.strip() in data or b'activate' in data.lower():
            self.activate_requested.emit()

    def release(self):
        """应用退出时释放本地服务。最小化到托盘不调用。"""
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
    """把 activate 信号接到主窗口：恢复、置顶、提示。"""

    def _activate():
        try:
            window.showNormal()
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
