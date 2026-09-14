"""Called by prism_web_runtime after it redirects all eager config paths."""
import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch


def main(args):
    repo = Path(__file__).resolve().parents[2]
    try:
        source_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo,
                                             text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
        source_changes = subprocess.check_output(
            ['git', 'status', '--porcelain', '--untracked-files=all', '--',
             'run.py', 'main_window.py', 'panels', 'ui', 'frontend', 'resources/style.qss',
             'resources/webui', 'resources/icons'], cwd=repo, text=True,
            stderr=subprocess.DEVNULL, timeout=10).splitlines()
        source_record = {'source_head': source_head, 'ui_source_changes': source_changes}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        source_record = {'source_head': None, 'ui_source_changes': None}
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
    if args.font is not None:
        settings['font_size'] = args.font
    result = {}
    native = getattr(args, 'native', False)
    root = Path(__file__).resolve().parents[2]
    with patch('main_window.load_settings', return_value=settings), \
            patch.object(MainWindow, '_ensure_services'), \
            patch('socket.socket.connect', side_effect=RuntimeError('Shell preview forbids external connections')), \
            (patch.object(web_shell, 'runtime_web_shell_available', return_value=False) if native else nullcontext()):
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
            panel = window.stack.currentWidget()
            if args.tab is not None:
                if args.nav == 7:
                    panel.section_picker.setCurrentIndex(args.tab)
                elif hasattr(panel, 'tabs'):
                    panel.tabs.setCurrentIndex(args.tab)
                elif hasattr(panel, 'side_tabs'):
                    panel.side_tabs.setCurrentIndex(args.tab)
                else:
                    raise ValueError('This preview page has no supported tab selector')
            if args.sample and args.nav == 7:
                panel._refresh_ai_list({'active_model_id': 'preview-a', 'items': [
                    {'id': 'preview-a', 'name': '示例 · 日常助手', 'model': 'demo-chat', 'enabled': False},
                    {'id': 'preview-b', 'name': '示例 · 文档校对', 'model': 'demo-review', 'enabled': False},
                ]})
            if args.sample and args.nav == 22:
                panel.key_name.setText('示例 · preview:settings')
                panel.key_meta.setText('仅供界面检查的示例值，不连接 Redis')
                panel._render_value('string', '{"theme":"calm","preview":true}')
            window.resize(args.width, args.height)
            window._layout_controller.force(args.width, args.height)
            QTimer.singleShot(1000, inspect_chrome)
        def inspect_chrome():
            if native:
                result['expected_active_label'] = window._context_header.name_label.text()
                result['native_sidebar'] = {
                    'visible': window._sidebar.isVisible(),
                    'width': window._sidebar.width(),
                    'checked_nav': [index for index, button in enumerate(window.nav_buttons)
                                    if button is not None and button.isChecked() and index not in (14, 15)],
                }
                result['runtime_unavailable_simulated'] = True
                finish()
                return
            window._chrome_web.web_page.runJavaScript(
                "({sidebar:!!document.querySelector('.sidebar'),"
                "active:[...document.querySelectorAll('.nav-item.active .nav-text')].map(e=>e.textContent.trim()),"
                "width:innerWidth,scrollWidth:document.documentElement.scrollWidth})",
                chrome_inspected)
        def chrome_inspected(dom):
            result['chrome_dom'] = dom
            result['expected_active_label'] = window._context_header.name_label.text()
            result['loaded_pages'] = sorted(window._web_health.loaded_pages)
            result['bridge_ready_pages'] = sorted(window._web_health.bridge_ready_pages)
            finish()
        def finish():
            page = window.stack.currentWidget()
            result.update(nav=args.nav, requested=[args.width, args.height],
                          window=[window.width(), window.height()],
                          page=[page.width(), page.height()],
                          chrome=window.main_shell_renderer, dashboard=window.dashboard_renderer,
                          actual_nav=window._current_nav_index,
                          context_height=window._context_header.height(),
                          status_height=window.statusBar().height(), collapsed=args.collapsed)
            if args.tab is not None:
                actual_tab = (page.sections_stack.currentIndex() if args.nav == 7
                              else (page.tabs if hasattr(page, 'tabs') else page.side_tabs).currentIndex())
                result.update(tab=args.tab, actual_tab=actual_tab)
            folder = root / 'docs/ui/prism-implementation-2026-09/shell'
            folder.mkdir(parents=True, exist_ok=True)
            name = f'nav-{args.nav}-{args.width}-{args.height}' + ('-collapsed' if args.collapsed else '')
            if native:
                name += '-native'
            if args.tab is not None:
                name += f'-tab-{args.tab}'
            if args.sample and args.nav in (7, 22):
                name += '-sample'
                result['sample'] = True
            if args.expected_dpr is not None:
                name += f'-dpr-{round(args.expected_dpr * 100)}'
            if args.font is not None:
                name += f'-font-{args.font}'
            pixmap = window.grab()
            result['window_dpr'] = window.devicePixelRatioF()
            result['image_pixels'] = [pixmap.width(), pixmap.height()]
            result['font_size'] = settings['font_size']
            if args.expected_dpr is not None:
                result['expected_dpr'] = args.expected_dpr
                result['qt_scale_factor'] = os.environ.get('QT_SCALE_FACTOR')
            pixmap.save(str(folder / (name + '.png')))
            result.update(source_record)
            (folder / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
            window._force_exit = True
            window.close()
            window.deleteLater()
            QTimer.singleShot(100, app.quit)
        def ready():
            native_ready = native and window.main_shell_renderer == 'native' and window.dashboard_renderer == 'native'
            web_ready = not native and window._web_health.is_ready() and window.main_shell_renderer == 'web' and window.dashboard_renderer == 'web'
            if native_ready or web_ready:
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
        if args.tab is not None and result['actual_tab'] != args.tab:
            raise SystemExit(5)
        if native:
            sidebar = result['native_sidebar']
            if not sidebar['visible'] or sidebar['checked_nav'] != [args.nav]:
                raise SystemExit(6)
            if result['chrome'] != 'native' or result['dashboard'] != 'native':
                raise SystemExit(7)
        else:
            dom = result.get('chrome_dom') or {}
            if not dom.get('sidebar') or dom.get('active') != [result['expected_active_label']]:
                raise SystemExit(6)
            if dom['scrollWidth'] > dom['width']:
                raise SystemExit(7)
        if args.expected_dpr is not None and abs(result['window_dpr'] - args.expected_dpr) > 0.02:
            raise SystemExit(8)
        if args.expected_dpr is not None:
            expected_pixels = [round(value * args.expected_dpr) for value in result['requested']]
            if result['image_pixels'] != expected_pixels:
                raise SystemExit(9)
