# -*- coding: utf-8 -*-
"""Isolated production Chrome proof for Prism's collapsed and expanded sidebar.

The historical module/function names remain for the diagnostic launcher. This
fixture uses only a test WebEngine profile, a data-only nav model, and a dummy
host; it never starts MainWindow or a business panel.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import QEventLoop, QStandardPaths, QTimer, Qt, QUrl
from PyQt6.QtWidgets import QApplication, QWidget

from ui import web_shell
from ui.navigation_model import build_web_nav_model_data
from ui.prism_sidebar import PrismSidebar


PROD_HTML = Path(ROOT) / 'resources' / 'webui' / 'vue' / 'chrome.html'
VIEW_HEIGHT = 640


class NativeMenuProbeUnavailable(RuntimeError):
    """The isolated WebEngine probe cannot run in this environment."""


def _wait_for(predicate, *, timeout_ms: int = 8_000) -> bool:
    loop = QEventLoop()
    deadline = time.monotonic() + timeout_ms / 1000
    result = {'value': False}

    def poll() -> None:
        if predicate():
            result['value'] = True
            loop.quit()
        elif time.monotonic() >= deadline:
            loop.quit()
        else:
            QTimer.singleShot(20, poll)

    QTimer.singleShot(0, poll)
    loop.exec()
    return result['value']


def _run_javascript(page, source: str, *, timeout_ms: int = 5_000):
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
        raise AssertionError(f'Chrome JS probe timeout: {source[:120]}')
    return result[0]


def _prepare_app() -> QApplication:
    QStandardPaths.setTestModeEnabled(True)
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication(sys.argv)
    return app


def _assert_webengine_supported(app: QApplication) -> None:
    if sys.platform != 'win32' or app.platformName() != 'windows':
        raise NativeMenuProbeUnavailable(f'需要 Windows QWebEngine，当前为 {sys.platform}/{app.platformName()}')
    if not web_shell.WEB_SHELL_AVAILABLE:
        raise NativeMenuProbeUnavailable(f'PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}')
    if not PROD_HTML.is_file():
        raise NativeMenuProbeUnavailable(f'缺少生产资源: {PROD_HTML}')


class _DummyShellHost(QWidget):
    def __init__(self, bridge, model: dict):
        super().__init__()
        self.resize(920, 720)
        self.requests: list[tuple[str, str]] = []
        self.web_navigation: list[int] = []
        self.native_navigation: list[int] = []
        self.chrome = web_shell.create_chrome_widget(bridge, self)
        self.chrome.setGeometry(24, 24, 72, VIEW_HEIGHT)
        self.chrome.setFixedSize(72, VIEW_HEIGHT)
        self.sidebar = PrismSidebar(model, parent=self)
        self.sidebar.setGeometry(400, 24, 248, VIEW_HEIGHT)
        self.sidebar.set_icon_only(True)
        bridge.navGroupRequested.connect(lambda key, rect: self.requests.append((key, rect)))
        bridge.navigateRequested.connect(self.web_navigation.append)
        self.sidebar.navigate_requested.connect(self.native_navigation.append)
        bridge.set_native_popup_available(True)

    def close_probe(self) -> None:
        self.chrome.close()
        self.sidebar.close()
        self.close()
        self.deleteLater()
        QApplication.processEvents()


def run_native_menu_probe(*, capture_dir: str | os.PathLike[str] | None = None) -> dict:
    """Exercise category popups and expanded leaves with data-only navigation."""
    app = _prepare_app()
    _assert_webengine_supported(app)
    if not web_shell.is_allowed_navigation(QUrl.fromLocalFile(str(PROD_HTML))):
        raise AssertionError('Chrome 本地资源被 Web 策略拒绝')
    if web_shell.is_allowed_navigation(QUrl('https://example.invalid/')):
        raise AssertionError('Web 策略错误放行外部 URL')

    model = build_web_nav_model_data(private_unlocked=True, current=19)
    bridge = web_shell.HomeBridge()
    bridge.set_nav_model(model)
    from ui.theme_manager import ThemeManager
    bridge.set_theme_payload({'id': 'calm', 'is_dark': False, 'tokens': ThemeManager.instance().palette('calm')})
    host = _DummyShellHost(bridge, model)
    ready: list[str] = []
    bridge.pageReadyReceived.connect(ready.append)
    report: dict[str, object] = {'production_html': str(PROD_HTML), 'widths': []}
    capture = Path(capture_dir) if capture_dir else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)
    host.show()
    host.chrome.show()
    host.sidebar.show()
    app.processEvents()
    try:
        if not _wait_for(lambda: 'chrome' in ready):
            raise AssertionError('生产 Chrome 未触发 pageReady')
        if not isinstance(host.chrome.web_view, web_shell.QWebEngineView):
            raise AssertionError('生产链未创建 QWebEngineView')
        self_ids = set(host.sidebar._buttons)
        if (self_ids != {0, 7} or 14 in self_ids or 15 in self_ids or
                list(host.sidebar._group_buttons) != [
                    'datacenter', 'ai', 'delivery', 'ops', 'devtools', 'personal'
                ]):
            raise AssertionError(
                f'原生折叠侧栏未按可见类型展示图标: buttons={sorted(self_ids)}, '
                f'groups={list(host.sidebar._group_buttons)}'
            )
        host.sidebar.set_current(19)
        if not host.sidebar._group_buttons['datacenter'].isChecked():
            raise AssertionError('折叠侧栏未高亮当前叶子所属类型')
        host.sidebar._group_buttons['ops'].click()
        ops_menu = host.sidebar._group_menu
        if ops_menu is None:
            raise AssertionError('运维类型图标未打开子项菜单')
        ops_actions = [action for action in ops_menu.actions() if action.data() is not None]
        if [action.data() for action in ops_actions] != [13, 6]:
            raise AssertionError('运维类型菜单没有展示真实叶子索引')
        next(action for action in ops_actions if action.data() == 13).trigger()
        if host.native_navigation != [13]:
            raise AssertionError(f'运维类型菜单未精确导航到 index 13: {host.native_navigation}')

        for width in (72, 248):
            host.chrome.setFixedSize(width, VIEW_HEIGHT)
            host.sidebar.set_icon_only(width <= 120)
            app.processEvents()
            expected_group_buttons = 6 if width <= 120 else 0
            probe = {}
            for _ in range(24):
                probe = _run_javascript(host.chrome.web_page, '''(() => {
                  const nav = document.querySelector('.nav');
                  const buttons = [...document.querySelectorAll('.nav button.nav-item')];
                  const mysql = buttons.find(b => b.textContent?.includes('MySQL'));
                  const oracle = buttons.find(b => b.textContent?.includes('Oracle'));
                  const datacenter = buttons.find(b => b.classList.contains('group-trigger')
                    && b.title.includes('DATA CENTER'));
                  const foot = document.querySelector('.foot');
                  return {width: innerWidth, buttonCount: buttons.length,
                    titlesComplete: buttons.every(b => !!b.title),
                    mysqlActive: !!(mysql ?? datacenter)?.classList.contains('active'),
                    mysqlAria: (mysql ?? datacenter)?.getAttribute('aria-current'),
                    oracleTitle: oracle?.title ?? datacenter?.title,
                    groupButtons: document.querySelectorAll('.group-trigger').length,
                    popupMenus: document.querySelectorAll('.group-menu').length,
                    scrollRange: nav.scrollHeight - nav.clientHeight,
                    footVisible: !!foot && foot.getBoundingClientRect().bottom <= innerHeight};
                })()''')
                if (int(probe.get('width', 0)) == width and
                        probe.get('groupButtons') == expected_group_buttons):
                    break
                wait_loop = QEventLoop()
                QTimer.singleShot(40, wait_loop.quit)
                wait_loop.exec()
            if (int(probe.get('width', 0)) != width or
                    probe.get('groupButtons') != expected_group_buttons):
                raise AssertionError(f'Chrome 宽度不匹配: {width}, {probe}')
            expected_button_count = 7 if width <= 120 else 21
            expected_current = 'true' if width <= 120 else 'page'
            expected_title = 'DATA CENTER' if width <= 120 else 'Oracle'
            if (probe.get('buttonCount') != expected_button_count or not probe.get('titlesComplete') or
                    not probe.get('mysqlActive') or probe.get('mysqlAria') != expected_current or
                    expected_title not in str(probe.get('oracleTitle')) or
                    probe.get('groupButtons') != expected_group_buttons or probe.get('popupMenus') or
                    (width > 120 and probe.get('scrollRange', 0) <= 0) or not probe.get('footVisible')):
                raise AssertionError(f'Chrome 折叠/展开/选中态异常: {width}, {probe}')

            last = _run_javascript(host.chrome.web_page, '''(() => {
              const nav = document.querySelector('.nav');
              nav.scrollTop = nav.scrollHeight;
              const buttons = [...nav.querySelectorAll('button.nav-item')];
              const last = buttons[buttons.length - 1];
              return {lastText: last?.textContent?.trim(),
                reached: !!last && last.getBoundingClientRect().bottom <= nav.getBoundingClientRect().bottom + 1};
            })()''')
            if not last.get('reached') or (width > 120 and '自我学习' not in str(last.get('lastText'))):
                raise AssertionError(f'Chrome 最末导航项不可达: {width}, {last}')
            host.web_navigation.clear()
            host.requests.clear()
            if width <= 120:
                _run_javascript(host.chrome.web_page, '''(() => {
                  const groups = [...document.querySelectorAll('.group-trigger')];
                  groups.find(b => b.title.includes('DATA CENTER'))?.click();
                  groups.find(b => b.title.includes('OPERATIONS'))?.click();
                  return true;
                })()''')
                if not _wait_for(lambda: len(host.requests) == 2):
                    raise AssertionError(f'折叠类型图标未请求原生菜单: {host.requests}')
                request_keys = [key for key, _rect in host.requests]
                if request_keys != ['datacenter', 'ops']:
                    raise AssertionError(f'分组弹窗请求未使用有效类型key: {request_keys}')
                for _key, rect_json in host.requests:
                    rect = json.loads(rect_json)
                    if not all(isinstance(rect.get(field), (int, float)) for field in (
                        'left', 'top', 'right', 'bottom', 'width', 'height'
                    )):
                        raise AssertionError(f'分组锚点不是有效矩形: {rect}')
            else:
                _run_javascript(host.chrome.web_page, '''(() => {
                  [...document.querySelectorAll('.nav button.nav-item')]
                    .find(b => b.textContent?.includes('Oracle'))?.click();
                  return true;
                })()''')
                if not _wait_for(lambda: host.web_navigation == [18]):
                    raise AssertionError(f'Chrome 叶子没有发出 index 18: {host.web_navigation}')
                if host.requests:
                    raise AssertionError(f'展开叶子错误请求分组弹窗: {host.requests}')
            bridge.push_active(18)
            active_check = (
                '''[...document.querySelectorAll('.nav button.nav-item')]
                  .find(b => b.classList.contains('group-trigger') && b.title.includes('DATA CENTER'))
                  ?.getAttribute('aria-current') === 'true' '''
                if width <= 120 else
                '''[...document.querySelectorAll('.nav button.nav-item')]
                  .find(b => b.textContent?.includes('Oracle'))?.getAttribute('aria-current') === 'page' '''
            )
            if not _wait_for(lambda: bool(_run_javascript(host.chrome.web_page, active_check))):
                raise AssertionError('Chrome 收到 activeChanged 后未选中当前页面/类型')
            bridge.push_active(19)
            if width > 120:
                _wait_for(lambda: bool(_run_javascript(host.chrome.web_page,
                    '''[...document.querySelectorAll('.nav button.nav-item')]
                      .find(b => b.textContent?.includes('MySQL'))?.getAttribute('aria-current') === 'page' ''')))
            evidence = {'requested_width': width, 'probe': probe, 'last': last,
                        'navigation': host.web_navigation[:], 'native_group_requests': len(host.requests)}
            if capture:
                path = capture / f'two_state_width_{width}_webview.png'
                if not host.chrome.web_view.grab().save(str(path), 'PNG'):
                    raise AssertionError(f'Fixture 截图失败: {path}')
                evidence['fixture_screenshot'] = str(path)
            report['widths'].append(evidence)
    finally:
        host.close_probe()
        bridge.deleteLater()
        app.processEvents()
    return report


class PrismNativeMenuRuntimeTests(unittest.TestCase):
    def test_production_webengine_collapsed_groups_and_expanded_leaves(self):
        try:
            report = run_native_menu_probe()
        except NativeMenuProbeUnavailable as exc:
            self.skipTest(str(exc))
        self.assertEqual([item['requested_width'] for item in report['widths']], [72, 248])
        self.assertEqual([item['native_group_requests'] for item in report['widths']], [2, 0])


if __name__ == '__main__':
    unittest.main()
