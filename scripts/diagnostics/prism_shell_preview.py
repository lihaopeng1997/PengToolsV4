"""Called by prism_web_runtime after it redirects all eager config paths."""
import json
import os
from pathlib import Path
from unittest.mock import patch


def main(args):
    os.environ['PENGTOOLS_SYNC_BOOT'] = '1'
    from ui import web_shell
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication
    from ui.theme_manager import ThemeManager
    import config
    from main_window import MainWindow
    app = QApplication(['prism-shell-preview'])
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    ThemeManager.instance().apply(app, 'calm')
    settings = dict(config.DEFAULT_SETTINGS)
    settings.update(ui_web_shell=True, floating_enabled=False, private_unlocked=True,
                    sidebar_collapsed=args.collapsed, home_username='演示用户')
    result = {}
    root = Path(__file__).resolve().parents[2]
    with patch('main_window.load_settings', return_value=settings), \
            patch.object(MainWindow, '_ensure_services'), \
            patch('socket.socket.connect', side_effect=RuntimeError('Shell preview forbids external connections')):
        window = MainWindow()
        window.resize(args.width, args.height)
        window.show()
        window._layout_controller.force(args.width, args.height)
        probe = QTimer()
        probe.setInterval(100)
        def capture():
            # Navigate after both WebChannel pages are ready, as a real click
            # would; otherwise the initial chrome payload still selects home.
            window._show_panel(args.nav)
            window.resize(args.width, args.height)
            window._layout_controller.force(args.width, args.height)
            QTimer.singleShot(300, finish)
        def finish():
            page = window.stack.currentWidget()
            result.update(nav=args.nav, requested=[args.width, args.height],
                          window=[window.width(), window.height()],
                          page=[page.width(), page.height()],
                          chrome=window.main_shell_renderer, dashboard=window.dashboard_renderer,
                          actual_nav=window._current_nav_index,
                          context_height=window._context_header.height(),
                          status_height=window.statusBar().height(), collapsed=args.collapsed)
            folder = root / 'docs/ui/prism-implementation-2026-09/shell'
            folder.mkdir(parents=True, exist_ok=True)
            name = f'nav-{args.nav}-{args.width}-{args.height}' + ('-collapsed' if args.collapsed else '')
            window.grab().save(str(folder / (name + '.png')))
            (folder / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
            window._force_exit = True
            window.close()
            window.deleteLater()
            QTimer.singleShot(100, app.quit)
        def ready():
            if window.main_shell_renderer == 'web' and window.dashboard_renderer == 'web':
                probe.stop()
                QTimer.singleShot(1000, capture)
        probe.timeout.connect(ready)
        probe.start()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(lambda: app.exit(2))
        deadline.start(15000)
        code = app.exec()
        if code or not result:
            raise SystemExit(code or 3)
        if result['window'] != result['requested'] or result['actual_nav'] != args.nav:
            raise SystemExit(4)
