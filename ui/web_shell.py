# -*- coding: utf-8 -*-
"""V2 Web 壳：QWebEngine 铬层（侧栏/首页）与 Python 桥接。

设计规格：docs/superpowers/specs/2026-08-29-v2-ui-shell-rebuild-design.md
- 仅加载打包内 resources/webui/*.html（本地 file://），WebLocalPage 拦截一切外链；
- 桥接对象不 import panels；首页数据由 main_window 注入 summary provider；
- 任何依赖缺失时 WEB_SHELL_AVAILABLE=False，调用方回退原生侧栏。
"""
from __future__ import annotations

import json
import os
import sys

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEB_SHELL_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失环境
    WEB_SHELL_AVAILABLE = False


def _project_root() -> str:
    if getattr(sys, '_MEIPASS', None):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def webui_url(name: str) -> QUrl:
    """webui 静态页的本地 file:// 地址（仅本地资源，无远程依赖）。"""
    return QUrl.fromLocalFile(os.path.join(_project_root(), 'resources', 'webui', name))


def is_allowed_navigation(url) -> bool:
    """导航白名单：仅本地资源协议。供 WebLocalPage 与测试使用。"""
    scheme = str(url.scheme()).lower()
    return scheme in ('file', 'qrc', 'about', 'data') or scheme == ''


if WEB_SHELL_AVAILABLE:

    class WebLocalPage(QWebEnginePage):
        """仅允许本地资源导航；外链一律拒绝（含 window.open 目标）。"""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if not is_allowed_navigation(url):
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    class HomeBridge(QObject):
        """JS ↔ Python 桥。导航/快速面板由 JS 发起；高亮由 Python 推送。"""

        navigateRequested = pyqtSignal(int)
        paletteRequested = pyqtSignal()
        activeChanged = pyqtSignal(int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._nav_json = '{"groups":[]}'
            self._username = 'Lihp'
            self._summary_provider = None

        # ---- 注入点（main_window 调用）----
        def set_nav_model(self, data):
            self._nav_json = json.dumps(data, ensure_ascii=False)

        def set_username(self, value):
            self._username = str(value or 'Lihp')

        def set_summary_provider(self, provider):
            self._summary_provider = provider

        def push_active(self, nav_index: int):
            self.activeChanged.emit(int(nav_index))

        # ---- JS 调用槽 ----
        @pyqtSlot(int)
        def navigate(self, nav_index):
            self.navigateRequested.emit(int(nav_index))

        @pyqtSlot()
        def openPalette(self):
            self.paletteRequested.emit()

        @pyqtSlot(result=str)
        def navModel(self):
            return self._nav_json

        @pyqtSlot(result=str)
        def homeUsername(self):
            return self._username

        @pyqtSlot(result=str)
        def dashboardSummary(self):
            try:
                data = self._summary_provider() if self._summary_provider else {}
            except Exception:
                data = {}
            return json.dumps(data, ensure_ascii=False)

else:  # pragma: no cover - 依赖缺失环境

    class HomeBridge(QObject):  # 类型占位，保持 import 不炸
        navigateRequested = pyqtSignal(int)
        paletteRequested = pyqtSignal()
        activeChanged = pyqtSignal(int)

        def set_nav_model(self, data):
            pass

        def set_username(self, value):
            pass

        def set_summary_provider(self, provider):
            pass

        def push_active(self, nav_index):
            pass


def _register_channel(page, bridge) -> None:
    channel = QWebChannel(page)
    channel.registerObject('bridge', bridge)
    page.setWebChannel(channel)


def create_chrome_widget(bridge: HomeBridge, parent: QWidget | None = None) -> QWidget:
    """V2 侧栏铬层（左侧 248px 全高）。"""
    wrap = QWidget(parent)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    page = WebLocalPage(wrap)
    view = QWebEngineView(wrap)
    view.setPage(page)
    _register_channel(page, bridge)
    view.load(webui_url('chrome.html'))
    layout.addWidget(view)
    return wrap


def create_dashboard_widget(bridge: HomeBridge, parent: QWidget | None = None) -> QWidget:
    """V2 首页（Stack[0] 的 web 版）。"""
    wrap = QWidget(parent)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    page = WebLocalPage(wrap)
    view = QWebEngineView(wrap)
    view.setPage(page)
    _register_channel(page, bridge)
    view.load(webui_url('dashboard.html'))
    layout.addWidget(view)
    return wrap
