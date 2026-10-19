"""Bounded single-route native UI geometry/reachability probe.

Run on Windows from the repository root:
    python scripts/diagnostics/prism_module_completeness.py OUT_DIR NAV_ID [zh|en] [FONT_SIZE]

Each invocation constructs one real MainWindow route with a temporary data root. It never
loads user settings or invokes panel actions; socket and subprocess launches are blocked.
"""
from __future__ import annotations
import faulthandler, hashlib, json, os, socket, subprocess, sys, tempfile, time
faulthandler.enable(all_threads=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    'main_window.py', 'ui/navigation_model.py', 'ui/layout_metrics.py', 'ui/responsive.py',
    'ui/page_chrome.py', 'ui/prism_sidebar.py', 'ui/prism_title_bar.py', 'ui/module_tabs.py',
    'ui/query_status_band.py', 'ui/aurora_progress.py', 'ui/json_viewer.py', 'ui/theme_manager.py',
    'ui/field_metrics.py', 'panels/dashboard_panel.py', 'panels/credit_panel.py', 'panels/sql_panel.py',
    'panels/docx_panel.py', 'panels/vin_panel.py', 'panels/gateway_panel.py', 'panels/ops_panel.py',
    'panels/settings_panel.py', 'panels/personal_panel.py', 'panels/requirement_panel.py',
    'panels/format_panel.py', 'panels/interface_debug_panel.py', 'panels/ops_log_panel.py',
    'panels/model_chat_panel.py', 'panels/agent_workbench_panel.py', 'panels/ai_workbench_panel.py',
    'panels/db_redis_panel.py', 'panels/db_mongodb_panel.py',
)
sys.path.insert(0, str(ROOT))
if len(sys.argv) < 3:
    raise SystemExit('usage: python scripts/diagnostics/prism_module_completeness.py OUT_DIR NAV_ID [zh|en] [FONT_SIZE]')
OUTPUT_DIR = Path(sys.argv[1]).resolve()
try:
    NAV_ID = int(sys.argv[2])
    LANGUAGE = sys.argv[3] if len(sys.argv) > 3 else 'zh'
    FONT_SIZE = int(sys.argv[4]) if len(sys.argv) > 4 else 12
except ValueError as exc:
    raise SystemExit(f'invalid route or font size: {exc}')
if LANGUAGE not in ('zh', 'en') or not 8 <= FONT_SIZE <= 24:
    raise SystemExit('LANGUAGE must be zh or en; FONT_SIZE must be between 8 and 24')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['PENGTOOLS_DISABLE_MOTION'] = '1'
os.environ.pop('PENGTOOLS_SYNC_BOOT', None)

from PyQt6.QtCore import QCoreApplication, QEvent, QStandardPaths, Qt, QPoint, QRect
from PyQt6.QtGui import QFontInfo, QFontMetrics
from PyQt6.QtWidgets import QApplication, QAbstractButton, QAbstractScrollArea, QLineEdit, QPlainTextEdit, QTextEdit, QWidget, QMenu
QStandardPaths.setTestModeEnabled(True)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

import config

def make_config_temp(temp_root: str):
    with patch.object(config, 'local_data_dir', return_value=temp_root):
        old_root = Path(config.CONFIG_DIR).resolve()
        replacements = {}
        for key, value in vars(config).items():
            if key.isupper() and isinstance(value, str):
                try:
                    suffix = Path(value).resolve().relative_to(old_root)
                except (ValueError, OSError):
                    continue
                replacements[key] = str(Path(temp_root) / suffix)
        for key, value in replacements.items():
            setattr(config, key, value)
        config.CONFIG_DIR = temp_root

network_attempts = []
process_attempts = []
def blocked_network(*args, **kwargs):
    network_attempts.append('socket')
    raise RuntimeError('module completeness probe forbids network')
def blocked_process(*args, **kwargs):
    process_attempts.append('subprocess')
    raise RuntimeError('module completeness probe forbids process launch')

with tempfile.TemporaryDirectory(prefix='pengtools-module-completeness-') as data_dir:
    make_config_temp(data_dir)
    from ui import web_shell
    from ui.theme_manager import ThemeManager
    import main_window
    from config import DEFAULT_SETTINGS
    from ui.navigation_model import NAV_MODEL
    from PyQt6.QtCore import QTimer

    app = QApplication.instance() or QApplication(['pengtools-module-completeness'])
    app.setQuitOnLastWindowClosed(False)
    ThemeManager.instance().apply(app, 'calm')
    settings = dict(DEFAULT_SETTINGS)
    settings.update({
        'default_language': LANGUAGE, 'font_size': FONT_SIZE, 'ui_theme': 'calm', 'ui_web_shell': False,
        'sidebar_collapsed': False, 'private_unlocked': True,
        'floating_show_on_startup': False, 'floating_enabled': False,
        'floating_always_on_top': False, 'keep_awake_enabled': False,
        'close_ask_each_time': False,
    })

    class AuditMainWindow(main_window.MainWindow):
        def _complete_startup_sync(self):
            return None
        def _startup_tick(self):
            return None
        def _show_startup_loading(self, _message):
            return None
        def _ensure_services(self):
            raise RuntimeError('fixture must not start services')

    expected = {
        0: 'DashboardPanel', 1: 'CreditCodePanel', 2: 'SqlToolPanel', 3: 'DocxUpdatePanel',
        4: 'VinPanel', 5: 'GatewayDecodePanel', 6: 'OpsPanel', 7: 'SettingsPanel',
        8: 'PersonalPanel', 9: 'PersonalPanel', 10: 'RequirementPanel', 11: 'FormatToolsPanel',
        12: 'InterfaceDebugPanel', 13: 'OpsLogPanel', 16: 'ModelChatPanel',
        17: 'AgentWorkbenchPanel', 18: 'AiWorkbenchPanel', 19: 'AiWorkbenchPanel',
        20: 'AiWorkbenchPanel', 21: 'AiWorkbenchPanel', 22: 'RedisWorkbenchPanel',
        23: 'MongoDBWorkbenchPanel',
    }
    all_route_ids = sorted({i for _g, items in NAV_MODEL for i, *_rest in items} | {7})
    if NAV_ID not in expected or NAV_ID not in all_route_ids:
        raise SystemExit(f'unsupported nav id: {NAV_ID}; expected one of {sorted(expected)}')
    route_ids = [NAV_ID]
    dimensions = ((960, 640), (1100, 720), (1280, 800), (1440, 900))
    report = {
        'runner': 'isolated production MainWindow route/factory ancestor clipping, scroll reachability, actions and geometry probe',
        'diagnostic_script': str(Path(__file__).resolve()),
        'diagnostic_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'branch_head': '818fba72bf437de9bd49fcd5b198fb1270096a53',
        'platform': sys.platform, 'qt_platform': app.platformName(),
        'primary_screen': ({'dpr': float(app.primaryScreen().devicePixelRatio()), 'geometry': [int(app.primaryScreen().geometry().width()), int(app.primaryScreen().geometry().height())]} if app.primaryScreen() is not None else None),
        'fixture': {'config_root': data_dir, 'user_settings_loaded': False,
                    'web_shell_enabled': False, 'private_page_unlocked': True, 'language': settings['default_language'], 'font_size': settings['font_size'],
                    'services_started': False, 'external_connections_requested': False},
        'route_ids': route_ids, 'routes': [],
        'safety': {'network_attempts_blocked': 0, 'process_launch_attempts_blocked': 0},
    }
    window = None
    with patch.object(main_window, 'load_settings', return_value=settings.copy()), \
         patch.object(web_shell, 'runtime_web_shell_available', return_value=False), \
         patch('socket.socket.connect', side_effect=blocked_network), \
         patch('socket.socket.connect_ex', side_effect=blocked_network), \
         patch('socket.create_connection', side_effect=blocked_network), \
         patch('subprocess.Popen', side_effect=blocked_process), \
         patch('subprocess.run', side_effect=blocked_process), \
         patch('subprocess.call', side_effect=blocked_process), \
         patch('subprocess.check_call', side_effect=blocked_process), \
         patch('subprocess.check_output', side_effect=blocked_process):
        window = AuditMainWindow()
        report['fixture']['settings_applied_no_persist'] = bool(window._apply_settings(settings, persist=False))
        report['application_font'] = {
            'family': app.font().family(), 'point_size': app.font().pointSizeF(),
            'pixel_size': app.font().pixelSize(),
        }
        report['source_hashes_sha256'] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES
        }
        glyph_sample = '返回首页中文字符'
        glyph_metrics = QFontMetrics(app.font())
        report['application_font']['resolved_family'] = QFontInfo(app.font()).family()
        report['application_font']['glyph_support'] = {char: bool(glyph_metrics.inFontUcs4(ord(char))) for char in glyph_sample}
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        if hasattr(window, '_clock_timer'):
            window._clock_timer.stop()
        window.resize(960, 640)
        window.show()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        # Constructing a panel is the only operation under test.  The Interface Debug
        # activation hook can manipulate the real system proxy/listener, so leave it inert.
        from panels.interface_debug_panel import InterfaceDebugPanel
        original_activate = getattr(InterfaceDebugPanel, 'on_panel_activated', None)
        if original_activate is not None:
            InterfaceDebugPanel.on_panel_activated = lambda self: None
        try:
            for nav_index in route_ids:
                print(f'route-start {nav_index}', flush=True)
                started = time.monotonic()
                navigated = bool(window._show_panel(nav_index))
                app.processEvents()
                stack_index = window._stack_index_for_nav(nav_index)
                page = window.stack.currentWidget()
                print(f'route-opened {nav_index} returned={navigated} widget={type(page).__name__ if page else None}', flush=True)
                route_row = {
                    'nav': nav_index, 'label': window._context_header.title.text() if hasattr(window._context_header, 'title') else '',
                    'navigated': navigated, 'stack_index': stack_index,
                    'widget_class': type(page).__name__ if page else None,
                    'expected_class': expected.get(nav_index),
                    'real_panel_match': bool(page is not None and type(page).__name__ == expected.get(nav_index)),
                    'construct_seconds': round(time.monotonic() - started, 3),
                    'sizes': [], 'long_content_target': None,
                }
                if not navigated or page is None:
                    route_row['error'] = 'production route did not navigate to a widget'
                    report['routes'].append(route_row)
                    continue

                def stop_panel_timers(panel):
                    for timer in panel.findChildren(QTimer):
                        timer.stop()
                stop_panel_timers(page)

                if nav_index == 0 and hasattr(page, '_apply_summary'):
                    synthetic = [{
                        'id': 'demo-module-completeness-001', 'code': 'DEMO-001',
                        'title': '合成长标题 · ' + ('模块完整性边界展示 ' * 12),
                        'record_kind': '需求', 'status': '测试中',
                        'actual_release_date': '2026-09-24',
                        'test_points': [{'id': 'tp-1', 'text': '合成正常输入与异常提示验证', 'done': False}],
                    }]
                    page._apply_summary(synthetic)
                    route_row['long_content_target'] = 'synthetic dashboard task title via production _apply_summary'
                elif nav_index == 10:
                    from PyQt6.QtWidgets import QTreeWidgetItem
                    tree = getattr(page, 'requirement_list', None)
                    if tree is not None:
                        QTreeWidgetItem(tree, ['DEMO-001 · ' + ('合成需求长标题 ' * 12), '开发中', '演示系统'])
                        route_row['long_content_target'] = 'synthetic long QTreeWidget row (no persistence)'

                injected_editor_state = None
                if route_row['long_content_target'] is None:
                    editors = []
                    for editor in page.findChildren((QPlainTextEdit, QTextEdit, QLineEdit)):
                        if not editor.isVisible() or editor.isReadOnly():
                            continue
                        if isinstance(editor, QLineEdit):
                            if editor.echoMode() == QLineEdit.EchoMode.Password:
                                continue
                            if editor.text().strip():
                                continue
                        else:
                            if editor.toPlainText().strip():
                                continue
                        editors.append(editor)
                    if editors:
                        editor = editors[0]
                        old_block = editor.blockSignals(True)
                        sample = 'SYNTHETIC LONG CONTENT · 查询条件、异常边界与说明文本 · ' * 16
                        if isinstance(editor, QLineEdit):
                            editor.setText(sample)
                            editor.setCursorPosition(0)
                        else:
                            editor.setPlainText(sample)
                            editor.moveCursor(editor.textCursor().MoveOperation.Start)
                        editor.blockSignals(old_block)
                        route_row['long_content_target'] = f'{type(editor).__name__}:{editor.objectName() or "unnamed"} (signals blocked)'
                if route_row['long_content_target'] is None:
                    for editor in page.findChildren((QPlainTextEdit, QTextEdit, QLineEdit)):
                        if not editor.isVisible() or editor.isReadOnly():
                            continue
                        if isinstance(editor, QLineEdit) and editor.echoMode() == QLineEdit.EchoMode.Password:
                            continue
                        old_block = editor.blockSignals(True)
                        if isinstance(editor, QLineEdit):
                            previous = editor.text()
                            editor.setText('SYNTHETIC LONG FIELD · bounded layout probe · ' * 8)
                            editor.setCursorPosition(0)
                            injected_editor_state = ('line', editor, previous, old_block)
                        else:
                            previous = editor.toPlainText()
                            editor.setPlainText('SYNTHETIC LONG CONTENT · bounded layout probe · ' * 12)
                            editor.moveCursor(editor.textCursor().MoveOperation.Start)
                            injected_editor_state = ('plain', editor, previous, old_block)
                        route_row['long_content_target'] = f'{type(editor).__name__}:{editor.objectName() or "unnamed"} (temporary fixture text; signals blocked)'
                        break

                for width, height in dimensions:
                    window.resize(width, height)
                    window._layout_controller.force(width, height)
                    for _ in range(3):
                        app.processEvents()
                    window.resize(width, height)
                    app.processEvents()
                    stop_panel_timers(page)
                    actual = (int(window.width()), int(window.height()))
                    body = window._page_body
                    panel_rect = page.rect()
                    visible_buttons = []
                    button_inventory = []
                    clipped_buttons = []
                    scroll_only_buttons = []
                    scroll_area_ancestor_count = 0
                    for button in page.findChildren(QAbstractButton):
                        if not button.isVisible() or button.width() <= 0 or button.height() <= 0:
                            continue
                        rect = QRect(button.mapTo(page, QPoint(0, 0)), button.size())
                        label = (button.text() or button.toolTip() or button.accessibleName() or button.objectName() or type(button).__name__).replace('\n', ' ').strip()
                        visible_buttons.append({'label': label[:100], 'enabled': bool(button.isEnabled())})
                        parents = []
                        scrolls = []
                        clip_rect = QRect(rect)
                        ancestor = button.parentWidget()
                        while ancestor is not None:
                            ancestor_rect = QRect(ancestor.mapTo(page, QPoint(0, 0)), ancestor.size())
                            clip_rect = clip_rect.intersected(ancestor_rect)
                            parents.append(ancestor)
                            if isinstance(ancestor, QAbstractScrollArea):
                                scrolls.append(ancestor)
                            if ancestor is page:
                                break
                            ancestor = ancestor.parentWidget()
                        initial_full = clip_rect.contains(rect)
                        scroll_area_ancestor_count += len(scrolls)
                        reach_diag = []
                        scroll_reachable = initial_full
                        if scrolls and not initial_full:
                            reach_diag = [{'class': type(area).__name__, 'name': area.objectName(),
                                           'h_value': area.horizontalScrollBar().value(), 'h_max': area.horizontalScrollBar().maximum(),
                                           'v_value': area.verticalScrollBar().value(), 'v_max': area.verticalScrollBar().maximum(),
                                           'viewport_rect_page': [area.viewport().mapTo(page, QPoint(0, 0)).x(), area.viewport().mapTo(page, QPoint(0, 0)).y(), area.viewport().width(), area.viewport().height()],
                                           'button_rect_viewport': [button.mapTo(area.viewport(), QPoint(0, 0)).x(), button.mapTo(area.viewport(), QPoint(0, 0)).y(), button.width(), button.height()]}
                                          for area in scrolls]
                            saved_scroll = [(area, area.horizontalScrollBar().value(), area.verticalScrollBar().value()) for area in scrolls]
                            for area in scrolls:
                                pos = button.mapTo(area.viewport(), QPoint(0, 0))
                                delta_x = pos.x() if pos.x() < 0 else max(0, pos.x() + button.width() - area.viewport().width())
                                delta_y = pos.y() if pos.y() < 0 else max(0, pos.y() + button.height() - area.viewport().height())
                                hbar, vbar = area.horizontalScrollBar(), area.verticalScrollBar()
                                hbar.setValue(max(0, min(hbar.maximum(), hbar.value() + delta_x)))
                                vbar.setValue(max(0, min(vbar.maximum(), vbar.value() + delta_y)))
                            app.processEvents()
                            after_rect = QRect(button.mapTo(page, QPoint(0, 0)), button.size())
                            after_clip = QRect(after_rect)
                            ancestor = button.parentWidget()
                            while ancestor is not None:
                                after_clip = after_clip.intersected(QRect(ancestor.mapTo(page, QPoint(0, 0)), ancestor.size()))
                                if ancestor is page:
                                    break
                                ancestor = ancestor.parentWidget()
                            scroll_reachable = after_clip.contains(after_rect)
                            for row_diag, area in zip(reach_diag, scrolls):
                                row_diag.update({'h_max_end': area.horizontalScrollBar().maximum(), 'v_max_end': area.verticalScrollBar().maximum(),
                                                 'h_value_end': area.horizontalScrollBar().value(), 'v_value_end': area.verticalScrollBar().value(),
                                                 'button_rect_viewport_end': [button.mapTo(area.viewport(), QPoint(0, 0)).x(), button.mapTo(area.viewport(), QPoint(0, 0)).y(), button.width(), button.height()],
                                                 'after_rect_page': [after_rect.x(), after_rect.y(), after_rect.width(), after_rect.height()],
                                                 'after_clip_page': [after_clip.x(), after_clip.y(), after_clip.width(), after_clip.height()]})
                            for area, old_h, old_v in saved_scroll:
                                area.horizontalScrollBar().setValue(old_h)
                                area.verticalScrollBar().setValue(old_v)
                            app.processEvents()
                        if not initial_full and scroll_reachable:
                            scroll_only_buttons.append(label[:100])
                        elif not scroll_reachable:
                            clipped_buttons.append({'label': label[:100], 'rect': [rect.x(), rect.y(), rect.width(), rect.height()], 'scroll_ancestors': len(scrolls)})
                        try:
                            direct_receivers = int(button.receivers(button.clicked))
                        except Exception:
                            direct_receivers = None
                        default_action = button.defaultAction() if hasattr(button, 'defaultAction') else None
                        action_receivers = None
                        if default_action is not None:
                            try:
                                action_receivers = int(default_action.receivers(default_action.triggered))
                            except Exception:
                                pass
                        hint = button.sizeHint()
                        text = button.text().replace('\n', ' ').strip()
                        metrics = button.fontMetrics()
                        text_width = int(metrics.horizontalAdvance(text)) if text else 0
                        button_inventory.append({
                            'label': label[:100], 'text': text[:100], 'tooltip': button.toolTip()[:140],
                            'object_name': button.objectName(), 'class': type(button).__name__,
                            'enabled': bool(button.isEnabled()), 'checkable': bool(button.isCheckable()),
                            'rect': [rect.x(), rect.y(), rect.width(), rect.height()],
                            'size_hint': [int(hint.width()), int(hint.height())],
                            'text_width_px': text_width,
                            'size_hint_shortfall': bool(text and (hint.width() > button.width() or hint.height() > button.height())),
                            'initial_full_after_all_ancestor_clips': bool(initial_full),
                            'scroll_reachable_at_scroll_extremes': bool(scroll_reachable),
                            'scroll_ancestor_count': len(scrolls),
                            'scroll_reachability_diagnostics': reach_diag,
                            'clicked_signal_receivers': direct_receivers,
                            'default_action_text': default_action.text() if default_action is not None else '',
                            'default_action_trigger_receivers': action_receivers,
                            'responsive_icon_only': bool(button.property('responsiveIconOnly')),
                        })
                    menus = page.findChildren(QMenu)
                    menu_actions = [a for menu in menus for a in menu.actions() if not a.isSeparator()]
                    menu_inventory = []
                    for menu in menus:
                        menu_inventory.append({'menu_title': menu.title(), 'object_name': menu.objectName(), 'actions': [
                            {'text': a.text(), 'enabled': bool(a.isEnabled()), 'visible': bool(a.isVisible()),
                             'trigger_receivers': (int(a.receivers(a.triggered)) if hasattr(a, 'triggered') else None)}
                            for a in menu.actions() if not a.isSeparator()
                        ]})
                    tab_inventory = []
                    from PyQt6.QtWidgets import QTabWidget
                    for tabs in page.findChildren(QTabWidget):
                        tab_inventory.append({'object_name': tabs.objectName(), 'current_index': tabs.currentIndex(),
                                              'tabs': [tabs.tabText(i) for i in range(tabs.count())]})
                    try:
                        from ui.responsive import ResponsiveActionBar
                        responsive_bars = [{
                            'object_name': bar.objectName(), 'density': getattr(bar, '_density', None).value if getattr(bar, '_density', None) is not None else '',
                            'items': [{'text': item.action.text(), 'role': getattr(item.role, 'value', str(item.role)),
                                       'enabled': bool(item.action.isEnabled()), 'inline': bool(item.button.isVisible()),
                                       'in_more_menu': len(bar._more_menu.actions()) > 0 and item.action in bar._more_menu.actions(),
                                       'trigger_receivers': int(item.action.receivers(item.action.triggered))}
                                      for item in getattr(bar, '_items', [])],
                            'more_button_visible': bool(bar.more_button.isVisible()),
                            'more_menu_labels': [a.text() for a in bar._more_menu.actions() if not a.isSeparator()],
                        } for bar in page.findChildren(ResponsiveActionBar)]
                    except Exception:
                        responsive_bars = []
                    row = {
                        'requested_window': [width, height], 'actual_window': list(actual),
                        'layout_mode': window._layout_controller.mode,
                        'low_height': window._layout_controller.low_height,
                        'sidebar_width': int(window._sidebar.width()),
                        'content_area': [int(window._content_frame.width()), int(window._content_frame.height())],
                        'page_body': [int(body.width()), int(body.height())],
                        'panel': [int(page.width()), int(page.height())],
                        'visible_buttons': len(visible_buttons),
                        'enabled_visible_buttons': sum(1 for b in visible_buttons if b['enabled']),
                        'visible_action_labels': [b['label'] for b in visible_buttons[:28]],
                        'panel_menu_actions': len(menu_actions),
                        'button_inventory': button_inventory,
                        'scroll_only_buttons': scroll_only_buttons,
                        'menu_inventory': menu_inventory,
                        'tab_inventory': tab_inventory,
                        'responsive_action_bars': responsive_bars,
                        'button_clip_count_excluding_scroll_areas': len(clipped_buttons),
                        'button_clip_count_after_scroll_extremes': len(clipped_buttons),
                        'clipped_buttons': clipped_buttons[:20],
                        'route_current_widget_match': window.stack.currentWidget() is page,
                    }
                    if nav_index == 5:
                        viewer = page.json_viewer
                        viewer_layout = viewer.layout()
                        action_row = page._layout_containing_widget(viewer_layout, viewer.path_value)
                        result_pane = page.splitter.widget(1)
                        result_layout = result_pane.layout() if result_pane is not None else None
                        viewer_margins = viewer_layout.contentsMargins()
                        result_margins = result_layout.contentsMargins() if result_layout is not None else None
                        containing_margins = viewer_margins.left() + viewer_margins.right()
                        if result_margins is not None:
                            containing_margins += result_margins.left() + result_margins.right()
                        path_rect = QRect(
                            viewer.path_value.mapTo(viewer, QPoint(0, 0)), viewer.path_value.size(),
                        )
                        embedded_controls = (
                            viewer.copy_path_btn, viewer.expand_btn,
                            viewer.collapse_btn, viewer.glance_btn,
                        )
                        embedded_inventory = []
                        for control in embedded_controls:
                            control_rect = QRect(
                                control.mapTo(viewer, QPoint(0, 0)), control.size(),
                            )
                            hint = control.sizeHint()
                            embedded_inventory.append({
                                'label': control.text(),
                                'rect': [control_rect.x(), control_rect.y(), control_rect.width(), control_rect.height()],
                                'size_hint': [int(hint.width()), int(hint.height())],
                                'icon_size': [int(control.iconSize().width()), int(control.iconSize().height())],
                                'intrinsic_content_fits': bool(
                                    control.width() >= hint.width() and control.height() >= hint.height()
                                ),
                                'overlaps_path_field': bool(path_rect.intersects(control_rect)),
                            })
                        required_row_width = (
                            int(action_row.sizeHint().width()) + int(containing_margins)
                            if action_row is not None else None
                        )
                        row['gateway_embedded_toolbar'] = {
                            'viewer_size': [int(viewer.width()), int(viewer.height())],
                            'action_row_required_width': required_row_width,
                            'action_row_margin_extra': int(containing_margins),
                            'result_pane_width': int(result_pane.width()) if result_pane is not None else None,
                            'result_pane_minimum_width': int(result_pane.minimumWidth()) if result_pane is not None else None,
                            'path_field_rect': [path_rect.x(), path_rect.y(), path_rect.width(), path_rect.height()],
                            'controls': embedded_inventory,
                        }
                    if nav_index == 5 or (width == 960 and height == 640 and nav_index in (3, 7, 12, 22)) or (width == 1440 and nav_index in (0, 10)):
                        capture = OUTPUT_DIR / f'nav-{nav_index}-{width}x{height}.png'
                        capture.parent.mkdir(parents=True, exist_ok=True)
                        row['screenshot'] = str(capture) if window.grab().save(str(capture)) else None
                    route_row['sizes'].append(row)
                if injected_editor_state is not None:
                    kind, editor, previous, old_block = injected_editor_state
                    editor.blockSignals(True)
                    editor.setText(previous) if kind == 'line' else editor.setPlainText(previous)
                    editor.blockSignals(old_block)
                report['routes'].append(route_row)
        finally:
            if original_activate is not None:
                InterfaceDebugPanel.on_panel_activated = original_activate
            if window is not None:
                if hasattr(window, '_clock_timer'):
                    window._clock_timer.stop()
                for panel in window._iter_created_panels():
                    stop_panel_timers(panel)
                window._shutting_down = True
                window.hide()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()

    report['safety']['network_attempts_blocked'] = len(network_attempts)
    report['safety']['process_launch_attempts_blocked'] = len(process_attempts)
    report['summary'] = {
        'route_count': len(report['routes']),
        'route_errors': [r['nav'] for r in report['routes'] if not r.get('navigated') or not r.get('real_panel_match')],
        'out_of_bounds': [
            {'nav': r['nav'], 'size': s['requested_window'], 'buttons': s['clipped_buttons']}
            for r in report['routes'] for s in r['sizes'] if s['button_clip_count_excluding_scroll_areas']
        ],
        'window_size_mismatches': [
            {'nav': r['nav'], 'requested': s['requested_window'], 'actual': s['actual_window']}
            for r in report['routes'] for s in r['sizes'] if s['requested_window'] != s['actual_window']
        ],
        'gateway_embedded_intrinsic_fit_failures': [
            {'nav': r['nav'], 'size': s['requested_window'], 'control': control['label']}
            for r in report['routes'] for s in r['sizes']
            for control in s.get('gateway_embedded_toolbar', {}).get('controls', [])
            if not control['intrinsic_content_fits']
        ],
        'gateway_embedded_path_overlaps': [
            {'nav': r['nav'], 'size': s['requested_window'], 'control': control['label']}
            for r in report['routes'] for s in r['sizes']
            for control in s.get('gateway_embedded_toolbar', {}).get('controls', [])
            if control['overlaps_path_field']
        ],
        'missing_long_state': [r['nav'] for r in report['routes'] if not r['long_content_target']],
    }
    out = OUTPUT_DIR / 'report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(out), 'route_count': len(report['routes']), 'summary': report['summary'], 'safety': report['safety']}, ensure_ascii=False))
