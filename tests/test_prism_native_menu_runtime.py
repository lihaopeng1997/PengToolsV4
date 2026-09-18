# -*- coding: utf-8 -*-
"""隔离的 Prism 原生菜单边界诊断。

本测试只创建：

* 生产 ``resources/webui/vue/chrome.html`` 对应的 QWebEngineView；
* 一个 fixture ``navModel``；
* HomeBridge、PrismSidebar 与一个临时宿主窗口。

它不导入 ``main_window``，不创建业务 Panel，不读取或写入用户配置/会话，
也不发起网络请求。真实主窗接线仍由 shell_design_finish 负责。
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QEventLoop, QPoint, QStandardPaths, QTimer, Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui import web_shell
from ui.prism_sidebar import PrismSidebar


PROD_HTML = Path(ROOT) / 'resources' / 'webui' / 'vue' / 'chrome.html'
GROUP_KEY = 'workspace:14'
LEAF_INDEX = 18
VIEW_HEIGHT = 640


class NativeMenuProbeUnavailable(RuntimeError):
    """Raised when this Windows-only diagnostic cannot use QWebEngine."""


def prism_nav_fixture() -> dict:
    """Return a data-only model with the same parent/leaf shape as production."""
    return {
        'groups': [
            {
                'key': 'workspace',
                'zh': '工作台',
                'en': 'WORKSPACE',
                'items': [
                    {'i': 0, 'zh': '首页', 'en': 'HOME', 'icon': 'home'},
                    {
                        'i': 14,
                        'zh': '数据中心',
                        'en': 'DATA CENTER',
                        'icon': 'database',
                        'children': [
                            {'i': LEAF_INDEX, 'zh': 'Oracle', 'en': 'ORACLE', 'icon': 'database'},
                            {'i': 19, 'zh': 'MySQL', 'en': 'MYSQL', 'icon': 'database'},
                        ],
                    },
                ],
            },
            {
                'key': 'delivery',
                'zh': '交付管理',
                'en': 'DELIVERY',
                'items': [
                    {'i': 10, 'zh': '需求管理', 'en': 'REQUIREMENTS', 'icon': 'requirements'},
                ],
            },
        ],
        'settings': {'i': 7, 'zh': '设置', 'en': 'SETTINGS', 'icon': 'gear'},
        'current': 0,
    }


def _wait_for(predicate, *, timeout_ms: int = 8_000, poll_ms: int = 20) -> bool:
    """Pump Qt events until ``predicate`` is true or the deadline expires."""
    loop = QEventLoop()
    deadline = time.monotonic() + timeout_ms / 1000
    result = {'value': False}

    def poll() -> None:
        if predicate():
            result['value'] = True
            loop.quit()
            return
        if time.monotonic() >= deadline:
            loop.quit()
            return
        QTimer.singleShot(poll_ms, poll)

    QTimer.singleShot(0, poll)
    loop.exec()
    return result['value']


def _run_javascript(page, source: str, *, timeout_ms: int = 5_000):
    """Run one production-page probe without a browser automation layer."""
    loop = QEventLoop()
    result: list[object] = []
    timed_out = {'value': False}

    def done(value) -> None:
        result.append(value)
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: (timed_out.__setitem__('value', True), loop.quit()))
    timer.start(timeout_ms)
    page.runJavaScript(source, done)
    loop.exec()
    if timed_out['value'] or not result:
        raise AssertionError(f'生产 WebEngine JS probe timeout: {source[:120]}')
    return result[0]


def _save_pixmap(pixmap, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), 'PNG'):
        raise AssertionError(f'截图保存失败: {path}')
    return {
        'path': str(path),
        'width': int(pixmap.width()),
        'height': int(pixmap.height()),
        'device_pixel_ratio': float(pixmap.devicePixelRatio()),
    }


class _DummyShellHost(QWidget):
    """A throwaway host that performs only the later main-window mapping step."""

    def __init__(self, bridge, model: dict, *, width: int, native: bool):
        super().__init__()
        self.setObjectName('prism-native-menu-probe-host')
        self.resize(920, 720)
        self.bridge = bridge
        self.model = model
        self.requests: list[tuple[str, str]] = []
        self.mapped_rects: list[dict[str, float]] = []
        self.navigate_calls: list[int] = []

        self.chrome = web_shell.create_chrome_widget(bridge, self)
        self.chrome.setGeometry(24, 24, width, VIEW_HEIGHT)
        self.chrome.setFixedSize(width, VIEW_HEIGHT)

        # Keep the real PrismSidebar as the popup owner.  It is placed beside
        # the WebView only so the isolated screenshot shows both boundaries;
        # the menu itself is positioned from the WebView-local rect.
        self.sidebar = PrismSidebar(model, parent=self)
        self.sidebar.setGeometry(400, 24, 248, VIEW_HEIGHT)
        self.sidebar.set_icon_only(width <= 120)

        bridge.navGroupRequested.connect(self._on_group_request)
        # Native leaves emit the presentation widget's signal.  The real
        # main-window adapter would connect this to its existing navigation
        # entry; this fixture only records the integer and owns no panel.
        self.sidebar.navigate_requested.connect(self.navigate_calls.append)
        self.native = bool(native)
        # Capability is advertised only after the host-side request slot is
        # connected, matching the production integration order.
        bridge.set_native_popup_available(self.native)

    def _on_group_request(self, group_key: str, anchor_rect_json: str) -> None:
        self.requests.append((str(group_key), str(anchor_rect_json)))
        if not self.native:
            return
        try:
            raw = json.loads(anchor_rect_json)
            values = {key: float(raw[key]) for key in ('left', 'top', 'right', 'bottom', 'width', 'height')}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        if not all(math.isfinite(value) for value in values.values()):
            return
        origin = self.chrome.web_view.mapToGlobal(QPoint(0, 0))
        mapped = {
            'left': origin.x() + values['left'],
            'top': origin.y() + values['top'],
            'right': origin.x() + values['right'],
            'bottom': origin.y() + values['bottom'],
            'width': values['width'],
            'height': values['height'],
        }
        self.mapped_rects.append(mapped)
        self.sidebar.show_group_menu_at(str(group_key), mapped)

    def close_probe(self) -> None:
        if self.sidebar._group_menu is not None:
            self.sidebar._close_group_menu()
        self.chrome.close()
        self.sidebar.close()
        self.close()
        self.deleteLater()
        QApplication.processEvents()


def _prepare_app() -> QApplication:
    # QStandardPaths test mode keeps the default WebEngine profile out of the
    # user's real data directory.  It is process-local and is set before the
    # first QWebEngineView is created.
    QStandardPaths.setTestModeEnabled(True)
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication(sys.argv)
    return app


def _assert_webengine_supported(app: QApplication) -> None:
    if sys.platform != 'win32' or app.platformName() != 'windows':
        raise NativeMenuProbeUnavailable(
            f'需要真实 Windows QWebEngineView，当前为 {sys.platform}/{app.platformName()}'
        )
    if not web_shell.WEB_SHELL_AVAILABLE:
        raise NativeMenuProbeUnavailable(
            f'PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}'
        )
    if not PROD_HTML.is_file():
        raise NativeMenuProbeUnavailable(f'缺少生产资源: {PROD_HTML}')


def _new_bridge(model: dict, *, native: bool):
    from ui.theme_manager import ThemeManager

    bridge = web_shell.HomeBridge()
    bridge.set_nav_model(model)
    bridge.set_theme_payload({
        'id': 'calm',
        'is_dark': False,
        'tokens': ThemeManager.instance().palette('calm'),
    })
    return bridge


def run_native_menu_probe(*, capture_dir: str | os.PathLike[str] | None = None) -> dict:
    """Run the real production chain and return a JSON-friendly evidence map."""
    app = _prepare_app()
    _assert_webengine_supported(app)
    model = prism_nav_fixture()
    capture = Path(capture_dir) if capture_dir else None

    # Verify the production navigation policy without opening a remote URL.
    local_url = QUrl.fromLocalFile(str(PROD_HTML))
    if not web_shell.is_allowed_navigation(local_url):
        raise AssertionError('生产 chrome.html 本地 URL 被 Web 策略拒绝')
    if web_shell.is_allowed_navigation(QUrl('https://example.invalid/')):
        raise AssertionError('生产 Web 策略错误放行外部 URL')

    report: dict[str, object] = {
        'platform': sys.platform,
        'qt_platform': app.platformName(),
        'production_html': str(PROD_HTML),
        'web_policy': {'local_file_allowed': True, 'https_allowed': False},
        'native': [],
        'dom_fallback': {},
    }

    bridge = _new_bridge(model, native=True)
    host = _DummyShellHost(bridge, model, width=72, native=True)
    ready: list[str] = []
    bridge.pageReadyReceived.connect(ready.append)
    host.show()
    host.chrome.show()
    host.sidebar.show()
    app.processEvents()
    try:
        if not _wait_for(lambda: 'chrome' in ready):
            raise AssertionError('生产 chrome.html 未触发 pageReady("chrome")')
        if not isinstance(host.chrome.web_view, web_shell.QWebEngineView):
            raise AssertionError('生产链没有创建 QWebEngineView')

        for width in (72, 248):
            # QWebEngine updates CSS viewport metrics on a fixed-size resize;
            # a plain wrapper setGeometry can leave Chromium's old viewport
            # until a later expose event.
            host.chrome.setFixedSize(width, VIEW_HEIGHT)
            host.sidebar.set_icon_only(width <= 120)
            app.processEvents()
            probe = {}
            for _ in range(24):
                probe = _run_javascript(
                    host.chrome.web_page,
                    '({width: innerWidth, height: innerHeight, group: !!document.querySelector(".group-trigger"), menu: !!document.querySelector(".group-menu")})',
                )
                if int(probe.get('width', 0)) == width:
                    break
                QTest.qWait(50)
            if int(probe.get('width', 0)) != width:
                raise AssertionError(f'QWebEngine CSS width mismatch: requested={width}, probe={probe}')
            if not probe.get('group'):
                raise AssertionError(f'生产 Chrome 缺少组按钮: width={width}')

            host.requests.clear()
            host.mapped_rects.clear()
            host.navigate_calls.clear()
            host.sidebar.set_current(LEAF_INDEX)
            source = host.sidebar._group_buttons[GROUP_KEY]
            source.setFocus(Qt.FocusReason.OtherFocusReason)
            _run_javascript(host.chrome.web_page, 'document.querySelector(".group-trigger")?.click(); true')
            if not _wait_for(lambda: bool(host.requests) and host.sidebar._group_menu is not None and host.sidebar._group_menu.isVisible()):
                raise AssertionError(f'原生组菜单未打开: width={width}, requests={host.requests}')

            menu = host.sidebar._group_menu
            assert menu is not None
            view_origin = host.chrome.web_view.mapToGlobal(QPoint(0, 0))
            menu_origin = menu.mapToGlobal(QPoint(0, 0))
            view_right = view_origin.x() + host.chrome.web_view.width()
            menu_right = menu_origin.x() + menu.width()
            # The 72px trigger is centered, so its anchor can overlap the
            # final few pixels of the WebView.  The invariant is that the
            # top-level 248px popup extends beyond the WebView boundary and
            # is therefore not clipped by its DOM viewport.
            if menu_right <= view_right:
                raise AssertionError(
                    f'原生菜单未跨出 WebView: width={width}, view={view_origin.x()}..{view_right}, menu={menu_origin.x()}..{menu_right}'
                )
            actions = [action for action in menu.actions() if action.data() is not None]
            if not actions or actions[0].data() != LEAF_INDEX:
                raise AssertionError(f'原生菜单叶子数据异常: {[action.data() for action in actions]}')
            if not actions[0].isChecked() and host.sidebar._current == LEAF_INDEX:
                raise AssertionError('当前叶子未在原生菜单中标记')

            evidence = {
                'requested_width': width,
                'web_view_logical_size': [host.chrome.web_view.width(), host.chrome.web_view.height()],
                'css_probe': probe,
                'anchor_local_json': host.requests[-1][1],
                'mapped_anchor': host.mapped_rects[-1] if host.mapped_rects else None,
                    'web_view_global_left': view_origin.x(),
                    'web_view_global_right': view_right,
                    'menu_global_rect': {
                        'left': menu_origin.x(),
                        'top': menu_origin.y(),
                        'width': menu.width(),
                        'height': menu.height(),
                    },
                    'menu_extends_beyond_web_view': menu_right > view_right,
                'menu_object_name': menu.objectName(),
                'dom_menu_present': bool(_run_javascript(host.chrome.web_page, '!!document.querySelector(".group-menu")')),
            }
            if capture:
                evidence['web_view_screenshot'] = _save_pixmap(
                    host.chrome.web_view.grab(), capture / f'native_width_{width}_webview.png'
                )
                evidence['native_popup_screenshot'] = _save_pixmap(
                    menu.grab(), capture / f'native_width_{width}_popup.png'
                )

            # Esc closes the native popup and returns focus to its source
            # button.  No business panel or main-window callback is involved.
            QTest.keyClick(menu, Qt.Key.Key_Escape)
            if not _wait_for(lambda: not menu.isVisible()):
                raise AssertionError(f'Esc 未关闭原生菜单: width={width}')
            if not source.hasFocus():
                raise AssertionError(f'Esc 后焦点未恢复到菜单源按钮: width={width}')
            evidence['esc_closed'] = True
            evidence['focus_restored'] = True

            # Re-open and trigger one leaf.  The only observable side effect
            # in this host is the integer signal; no business object exists.
            host.navigate_calls.clear()
            _run_javascript(host.chrome.web_page, 'document.querySelector(".group-trigger")?.click(); true')
            if not _wait_for(lambda: host.sidebar._group_menu is not None and host.sidebar._group_menu.isVisible()):
                raise AssertionError(f'原生菜单重开失败: width={width}')
            menu = host.sidebar._group_menu
            assert menu is not None
            leaf = next(action for action in menu.actions() if action.data() == LEAF_INDEX)
            leaf.trigger()
            if not _wait_for(lambda: host.navigate_calls == [LEAF_INDEX]):
                raise AssertionError(f'原生叶子未只发出业务无关 index: {host.navigate_calls}')
            evidence['leaf_navigation_signal'] = host.navigate_calls[:]
            report['native'].append(evidence)

    finally:
        host.close_probe()
        bridge.deleteLater()
        app.processEvents()

    # A second production page proves that cap=false remains an in-WebView
    # fallback and does not emit the native request signal.
    bridge_fallback = _new_bridge(model, native=False)
    fallback_host = _DummyShellHost(bridge_fallback, model, width=248, native=False)
    ready_fallback: list[str] = []
    bridge_fallback.pageReadyReceived.connect(ready_fallback.append)
    fallback_host.show()
    fallback_host.chrome.show()
    fallback_host.sidebar.show()
    app.processEvents()
    try:
        if not _wait_for(lambda: 'chrome' in ready_fallback):
            raise AssertionError('cap=false 生产 chrome.html 未触发 pageReady("chrome")')
        fallback_host.requests.clear()
        _run_javascript(fallback_host.chrome.web_page, 'document.querySelector(".group-trigger")?.click(); true')
        if not _wait_for(lambda: bool(_run_javascript(fallback_host.chrome.web_page, '!!document.querySelector(".group-menu")'))):
            raise AssertionError('cap=false 未打开 DOM fallback 菜单')
        fallback_probe = _run_javascript(
            fallback_host.chrome.web_page,
            '(() => { const m = document.querySelector(".group-menu"); const r = m?.getBoundingClientRect(); return {width: innerWidth, menu: !!m, menuWidth: r?.width || 0, menuRight: r?.right || 0, requestCount: 0}; })()',
        )
        if fallback_host.requests:
            raise AssertionError(f'cap=false 错误发出原生请求: {fallback_host.requests}')
        if not fallback_probe.get('menu') or float(fallback_probe.get('menuWidth', 0)) <= 0:
            raise AssertionError(f'cap=false DOM 菜单几何无效: {fallback_probe}')
        fallback_evidence: dict[str, object] = {
            'requested_width': 248,
            'web_view_logical_size': [fallback_host.chrome.web_view.width(), fallback_host.chrome.web_view.height()],
            'css_probe': fallback_probe,
            'native_request_count': len(fallback_host.requests),
        }
        if capture:
            fallback_evidence['web_view_screenshot'] = _save_pixmap(
                fallback_host.chrome.web_view.grab(), capture / 'dom_fallback_width_248_webview.png'
            )
        _run_javascript(
            fallback_host.chrome.web_page,
            'document.querySelector(".group-menu")?.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true})); true',
        )
        if not _wait_for(lambda: not bool(_run_javascript(fallback_host.chrome.web_page, '!!document.querySelector(".group-menu")'))):
            raise AssertionError('cap=false DOM 菜单 Esc 未关闭')
        fallback_focus = _run_javascript(
            fallback_host.chrome.web_page,
            'document.activeElement?.classList.contains("group-trigger") === true',
        )
        if not fallback_focus:
            raise AssertionError('cap=false DOM 菜单 Esc 后焦点未恢复到组按钮')
        fallback_evidence['esc_closed'] = True
        fallback_evidence['focus_restored'] = True
        report['dom_fallback'] = fallback_evidence
    finally:
        fallback_host.close_probe()
        bridge_fallback.deleteLater()
        app.processEvents()

    if capture:
        capture.mkdir(parents=True, exist_ok=True)
        report['report_path'] = str(capture / 'report.json')
        (capture / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    return report


class PrismNativeMenuRuntimeTests(unittest.TestCase):
    """Real Windows-only runtime proof; skipped on non-Windows/without WebEngine."""

    def test_production_webengine_native_popup_and_dom_fallback(self):
        try:
            report = run_native_menu_probe()
        except NativeMenuProbeUnavailable as exc:
            self.skipTest(str(exc))
        self.assertEqual([item['requested_width'] for item in report['native']], [72, 248])
        self.assertTrue(all(item['esc_closed'] and item['focus_restored'] for item in report['native']))
        self.assertTrue(all(item['dom_menu_present'] is False for item in report['native']))
        self.assertEqual(report['dom_fallback']['native_request_count'], 0)
        self.assertTrue(report['dom_fallback']['esc_closed'])
        self.assertTrue(report['dom_fallback']['focus_restored'])


if __name__ == '__main__':
    unittest.main()
