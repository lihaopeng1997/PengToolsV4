"""Minimal production dashboard smoke using one Qt event loop and no user data."""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, default=1144)
    parser.add_argument('--height', type=int, default=740)
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--motion', action='store_true')
    parser.add_argument('--shell', action='store_true')
    parser.add_argument('--nav', type=int, default=0)
    parser.add_argument('--tab', type=int, default=None)
    parser.add_argument('--collapsed', action='store_true')
    parser.add_argument('--font', type=int, default=None)
    parser.add_argument('--expected-dpr', type=float, default=None)
    args = parser.parse_args()
    if args.shell:
        from prism_shell_preview import main as preview_shell
        preview_shell(args)
        return
    debug_port = None
    if args.motion:
        import socket
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1', 0))
            debug_port = port_probe.getsockname()[1]
        os.environ['QTWEBENGINE_REMOTE_DEBUGGING'] = f'127.0.0.1:{debug_port}'
    print('Importing Qt WebEngine', flush=True)
    from ui import web_shell
    from PyQt6.QtCore import QTimer, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication
    from ui.theme_manager import ThemeManager
    from ui.navigation_model import build_web_nav_model_data

    print('Creating Qt application', flush=True)
    qInstallMessageHandler(lambda kind, context, message: print('Qt: ' + message, flush=True))
    app = QApplication(['prism-runtime-check'])
    from PyQt6.QtGui import QFontDatabase
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    ThemeManager.instance().apply(app, 'calm')
    bridge = web_shell.HomeBridge()
    bridge.set_nav_model(build_web_nav_model_data())
    summary = {}
    if args.sample:
        import datetime
        from tools.dashboard_summary import build_dashboard_summary
        today = datetime.date.today()
        records = [{'id': f'preview-{index}', 'code': f'DEMO-{index:03}',
                    'title': title, 'record_kind': '需求', 'status': '开发中',
                    'system': '演示系统', 'actual_release_date': today.strftime('%Y-%m') + '-20',
                    'test_points': [{'id': 'tp1', 'text': '示例输入校验', 'done': True},
                                    {'id': 'tp2', 'text': '示例异常提示', 'done': False}]}
                   for index, title in enumerate(('示例 · 查询结果展示', '示例 · 上线材料整理', '示例 · 接口异常提示'), 1)]
        summary = build_dashboard_summary(requirements=records, today=today)
    bridge.set_summary_provider(lambda: summary)
    bridge.set_theme_payload({'id': 'calm', 'is_dark': False,
                              'tokens': ThemeManager.instance().palette()})
    result = {'ready': False, 'dom': None, 'motion': {}}
    widget = None
    devtools = []

    def inspected(value):
        result['dom'] = value
        name = f'web-dashboard-{args.width}-{args.height}.png' if args.sample else 'web-dashboard.png'
        output = ROOT / 'docs/ui/prism-implementation-2026-09' / name
        output.parent.mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(output))
        if args.motion:
            check_motion(0)
        else:
            finish()

    def finish():
        print(json.dumps(result, ensure_ascii=False), flush=True)
        evidence = ROOT / 'docs/ui/prism-implementation-2026-09' / f'web-runtime-{args.width}-{args.height}.json'
        evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        for connection in devtools:
            connection.close()
        widget.close()
        widget.deleteLater()
        QTimer.singleShot(100, app.quit)

    def check_motion(index):
        modes = ['running', 'hidden', 'reduced']
        if index == len(modes):
            finish()
            return
        mode = modes[index]
        state = '(() => {const s=getComputedStyle(document.querySelector(".orb-inner"));return {transform:s.transform,state:s.animationPlayState,name:s.animationName}})()'
        setup = ('document.body.classList.remove("page-hidden","motion-disabled");' +
                 ('document.body.classList.add("page-hidden");' if mode == 'hidden' else
                  'document.body.classList.add("motion-disabled");' if mode == 'reduced' else ''))
        def first(value):
            result['motion'][mode] = [value]
            def second(value):
                result['motion'][mode].append(value)
                check_motion(index + 1)
            QTimer.singleShot(300, lambda: widget.web_page.runJavaScript(state, second))
        widget.web_page.runJavaScript(setup, lambda _: QTimer.singleShot(
            60, lambda: widget.web_page.runJavaScript(state, first)))

    def ready(page):
        result['ready'] = page == 'dashboard'
        if args.motion:
            from concurrent.futures import ThreadPoolExecutor
            from urllib.request import urlopen
            import websocket
            pool = ThreadPoolExecutor(max_workers=1)
            def emulate():
                with urlopen(f'http://127.0.0.1:{debug_port}/json/list', timeout=3) as response:
                    targets = json.load(response)
                target = next(target for target in targets if target.get('url', '').endswith('/vue/dashboard.html'))
                connection = websocket.create_connection(target['webSocketDebuggerUrl'],
                                                          suppress_origin=True, timeout=3)
                connection.send(json.dumps({'id': 1, 'method': 'Emulation.setEmulatedMedia',
                    'params': {'features': [{'name': 'prefers-reduced-motion', 'value': 'no-preference'}]}}))
                while True:
                    response = json.loads(connection.recv())
                    if response.get('id') == 1:
                        if 'error' in response:
                            raise RuntimeError(response['error'])
                        return connection
            pending = pool.submit(emulate)
            def poll():
                if not pending.done():
                    QTimer.singleShot(20, poll)
                    return
                try:
                    devtools.append(pending.result())
                except Exception as error:
                    print(f'Motion emulation failed: {error}', flush=True)
                    app.exit(5)
                    return
                finally:
                    pool.shutdown(wait=False)
                inspect_after_paint()
            poll()
        else:
            inspect_after_paint()

    def inspect_after_paint():
        QTimer.singleShot(1000, lambda: widget.web_page.runJavaScript(
            "({hero:!!document.querySelector('.hero'), title:document.title, "
            "width:innerWidth, scrollWidth:document.documentElement.scrollWidth, "
            "reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches, "
            "motionAttribute:document.documentElement.getAttribute('data-motion'), "
            "overflow:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1)"
            ".slice(0,12).map(e=>({tag:e.tagName,cls:String(e.className),right:e.getBoundingClientRect().right}))})", inspected))

    bridge.pageReadyReceived.connect(ready)
    print('Creating production dashboard widget', flush=True)
    widget = web_shell.create_dashboard_widget(bridge)
    widget.resize(args.width, args.height)
    widget.show()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: (print('Dashboard runtime timeout', flush=True), app.exit(2)))
    watchdog.start(12000)
    print('Production dashboard created; waiting for WebChannel', flush=True)
    code = app.exec()
    if code or not result['ready'] or not result['dom']:
        raise SystemExit(code or 3)
    if not result['dom']['hero'] or result['dom']['scrollWidth'] > result['dom']['width']:
        raise SystemExit(4)
    if args.motion:
        motion = result['motion']
        assert motion['running'][0]['transform'] != motion['running'][1]['transform']
        assert motion['hidden'][0] == motion['hidden'][1]
        assert motion['hidden'][0]['state'] == 'paused'
        assert motion['reduced'][0] == motion['reduced'][1]
        assert motion['reduced'][0]['name'] == 'none'


if __name__ == '__main__':
    import tempfile
    import config
    with tempfile.TemporaryDirectory(prefix='prism-web-runtime-') as temporary:
        original = Path(config.CONFIG_DIR).resolve()
        real_local_data_dir = config.local_data_dir
        def isolated_local_data_dir(executable=None, frozen=None):
            # Default application IO stays in the temporary directory. Explicit
            # executable arguments exercise the original pure path calculation
            # (it does not create or read any directory).
            if executable is not None:
                return real_local_data_dir(executable, frozen)
            return temporary
        config.local_data_dir = isolated_local_data_dir
        for key, value in list(vars(config).items()):
            if key.isupper() and isinstance(value, str):
                try:
                    suffix = Path(value).resolve().relative_to(original)
                except (ValueError, OSError):
                    continue
                setattr(config, key, str(Path(temporary) / suffix))
        if '--integration-tests' in sys.argv or '--test-module' in sys.argv:
            import unittest
            from unittest.mock import patch
            if '--test-module' in sys.argv:
                module = sys.argv[sys.argv.index('--test-module') + 1]
            else:
                module = 'tests.test_web_dashboard_runtime'
            from ui import web_shell
            from PyQt6.QtWidgets import QApplication
            test_app = QApplication.instance() or QApplication(['prism-isolated-tests'])
            suite = unittest.defaultTestLoader.loadTestsFromName(module)
            with patch('socket.socket.connect', side_effect=RuntimeError('Runtime check forbids external connections')):
                result = unittest.TextTestRunner(verbosity=2).run(suite)
            raise SystemExit(0 if result.wasSuccessful() else 1)
        else:
            main()
