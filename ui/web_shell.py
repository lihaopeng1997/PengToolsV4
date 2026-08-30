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

from ui.web_diagnostics import log_web_event as _log_web_event
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    # 语义：仅代表 WebEngine Python 模块可导入；运行时健康由 pageReady/renderProcess 信号判定。
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

        _SENSITIVE = ('authorization', 'bearer', 'token', 'password', 'passwd', 'cookie')

        def javaScriptConsoleMessage(self, level, message, line_number, source_id):
            # 只持久化 Warning/Error；level 枚举：0=Info 1=Warning 2=Error
            if level not in (1, 2):
                return
            text = str(message or '')[:300]
            low = text.lower()
            for secret in self._SENSITIVE:
                if secret in low:
                    text = '[redacted]'
                    break
            src = str(source_id or '').split('/')[-1][:60]
            _log_web_event('js_console', page=getattr(self, 'web_name', '?'),
                           level=int(level), source=src, line=int(line_number or 0),
                           message=text)

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

        pageReadyReceived = pyqtSignal(str)

        @pyqtSlot(str)
        def pageReady(self, page_name):
            _log_web_event('page_ready_slot_called', page=str(page_name))
            name = str(page_name or '')
            if name in ('chrome', 'dashboard'):
                self.pageReadyReceived.emit(name)
            else:
                _log_web_event('page_ready_ignored', page=name)

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
        pageReadyReceived = pyqtSignal(str)

        def set_nav_model(self, data):
            pass

        def set_username(self, value):
            pass

        def set_summary_provider(self, provider):
            pass

        def push_active(self, nav_index):
            pass

def enum_value(value):
    """Qt 枚举 → 原生值（日志序列化绝不抛异常）。"""
    return getattr(value, 'value', value)


# WebEngine 运行态：QtWebEngineQuick.initialize() 抛异常时置 True；
# 模块可 import（WEB_SHELL_AVAILABLE）不代表运行时可用。
WEBENGINE_RUNTIME_FAILED = False


def mark_webengine_runtime_failed() -> None:
    global WEBENGINE_RUNTIME_FAILED
    WEBENGINE_RUNTIME_FAILED = True


def runtime_web_shell_available() -> bool:
    return WEB_SHELL_AVAILABLE and not WEBENGINE_RUNTIME_FAILED


class WebHealthTracker(QObject):
    """极小 Web 健康状态机：loaded 与 bridge_ready 双条件，不碰 QWidget 与业务数据。

    健康定义：expected ⊆ loaded_pages 且 expected ⊆ bridge_ready_pages 且无 failed。
    """

    def __init__(self, expected=('chrome', 'dashboard'), parent=None):
        super().__init__(parent)
        self.expected = set(expected)
        self.loaded_pages = set()
        self.bridge_ready_pages = set()
        self.failed_pages = {}

    def mark_loaded(self, page_name: str, ok: bool = True) -> None:
        if page_name not in self.expected:
            return
        if ok:
            self.loaded_pages.add(page_name)
            self.failed_pages.pop(page_name, None)  # 重载成功即恢复
        else:
            self.failed_pages[page_name] = 'load_failed'
            self.loaded_pages.discard(page_name)

    def mark_bridge_ready(self, page_name: str) -> None:
        if page_name in self.expected:
            self.bridge_ready_pages.add(page_name)

    def mark_failed(self, page_name: str, reason: str) -> None:
        if page_name in self.expected:
            self.failed_pages[page_name] = reason
            self.loaded_pages.discard(page_name)
            self.bridge_ready_pages.discard(page_name)

    def missing_loaded_pages(self) -> set:
        return set(self.expected) - set(self.loaded_pages)

    def missing_bridge_pages(self) -> set:
        return set(self.expected) - set(self.bridge_ready_pages)

    def missing_pages(self) -> set:
        return self.missing_loaded_pages() | self.missing_bridge_pages()

    def is_ready(self) -> bool:
        return (not self.failed_pages
                and not self.missing_loaded_pages()
                and not self.missing_bridge_pages())


def _register_channel(page, bridge) -> None:
    channel = QWebChannel(page)
    channel.registerObject('bridge', bridge)
    page.setWebChannel(channel)


def _create_web_widget(page_name: str, html_name: str, bridge: HomeBridge,
                       parent: QWidget | None = None) -> QWidget:
    """统一 Web 视图工厂：page/view/channel/本地 URL/生命周期日志。

    wrapper 挂属性：web_view / web_page / web_name（main_window 经此访问，无需 findChildren）。
    """
    wrap = QWidget(parent)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    page = WebLocalPage(wrap)
    view = QWebEngineView(wrap)
    view.setPage(page)
    _register_channel(page, bridge)

    _PROGRESS_STEPS = {0, 25, 50, 75, 100}
    last = {'v': -1}

    def _on_started():
        _log_web_event('load_started', page=page_name)

    def _on_progress(value):
        if value in _PROGRESS_STEPS and value != last['v']:
            last['v'] = value
            _log_web_event('load_progress', page=page_name, value=value)

    def _on_finished(ok):
        _log_web_event('load_finished', page=page_name, ok=bool(ok))

    def _on_render_terminated(status, exit_code):
        _log_web_event('render_process_terminated', page=page_name,
                       status=enum_value(status), exit_code=enum_value(exit_code))

    view.loadStarted.connect(_on_started)
    view.loadProgress.connect(_on_progress)
    view.loadFinished.connect(_on_finished)
    page.renderProcessTerminated.connect(_on_render_terminated)
    view.load(webui_url(html_name))

    layout.addWidget(view)
    page.web_name = page_name
    wrap.web_view = view
    wrap.web_page = page
    wrap.web_name = page_name
    return wrap


def create_chrome_widget(bridge: HomeBridge, parent: QWidget | None = None) -> QWidget:
    """V2 侧栏铬层（左侧 248px 全高）。

    STEP-4 起 Sidebar 由 Vue 构建（frontend → resources/webui/vue/）；
    legacy chrome.html 仅作应急对照保留。
    """
    return _create_web_widget('chrome', 'vue/chrome.html', bridge, parent)


def create_dashboard_widget(bridge: HomeBridge, parent: QWidget | None = None) -> QWidget:
    """V2 首页（Stack[0] 的 web 版）。"""
    return _create_web_widget('dashboard', 'dashboard.html', bridge, parent)
