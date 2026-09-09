"""Render one real native panel with temporary data, without external connections.

Run from the repository root. These are review images, not production fixtures.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['PENGTOOLS_DISABLE_MOTION'] = '1'


PAGES = {
    'floating': ('quick_panel', 'QuickPanel'),
    'settings': ('settings_panel', 'SettingsPanel'),
    'chat': ('model_chat_panel', 'ModelChatPanel'),
    'commands': ('ops_panel', 'OpsPanel'),
    'home': ('dashboard_panel', 'DashboardPanel'),
    'requirements': ('requirement_panel', 'RequirementPanel'),
    'release': ('sql_panel', 'SqlToolPanel'),
    'documents': ('docx_panel', 'DocxUpdatePanel'),
    'credit': ('credit_panel', 'CreditCodePanel'),
    'vin': ('vin_panel', 'VinPanel'),
    'crypto': ('gateway_panel', 'GatewayDecodePanel'),
    'format': ('format_panel', 'FormatToolsPanel'),
    'interface': ('interface_debug_panel', 'InterfaceDebugPanel'),
    'logs': ('ops_log_panel', 'OpsLogPanel'),
    'agent': ('agent_workbench_panel', 'AgentWorkbenchPanel'),
    'learning': ('personal_panel', 'PersonalPanel'),
    'daily': ('personal_panel', 'PersonalPanel'),
    'oracle': ('ai_workbench_panel', 'AiWorkbenchPanel'),
    'mysql': ('ai_workbench_panel', 'AiWorkbenchPanel'),
    'oceanbase': ('ai_workbench_panel', 'AiWorkbenchPanel'),
    'dameng': ('ai_workbench_panel', 'AiWorkbenchPanel'),
    'redis': ('db_redis_panel', 'RedisWorkbenchPanel'),
    'mongodb': ('db_mongodb_panel', 'MongoDBWorkbenchPanel'),
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('page', choices=sorted(PAGES))
    parser.add_argument('--width', type=int, default=1144)
    parser.add_argument('--height', type=int, default=740)
    parser.add_argument('--output', required=True)
    parser.add_argument('--tab', type=int, default=0)
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--inspect-layout', action='store_true')
    args = parser.parse_args()
    import config
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from PyQt6.QtGui import QFontDatabase
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    with tempfile.TemporaryDirectory(prefix='pengtools-prism-') as data_dir, \
         patch.object(config, 'local_data_dir', return_value=data_dir), \
         patch('socket.socket.connect', side_effect=RuntimeError('Preview forbids network')):
        # config also exports eager path constants; isolate those before importing panels/tools.
        original = Path(config.CONFIG_DIR).resolve()
        paths = {}
        for key, value in vars(config).items():
            if key.isupper() and isinstance(value, str):
                try:
                    suffix = Path(value).resolve().relative_to(original)
                except (ValueError, OSError):
                    continue
                paths[key] = str(Path(data_dir) / suffix)
        for key, value in paths.items():
            setattr(config, key, value)
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(app, 'calm')
        if args.sample and args.page in ('home', 'requirements'):
            import datetime
            month = datetime.date.today().strftime('%Y-%m')
            fixtures = [{'id': f'preview-{i}', 'code': f'DEMO-{i:03}',
                'title': title, 'record_kind': '需求', 'status': '开发中',
                'system': '演示系统', 'actual_release_date': month + '-20',
                'test_points': [{'id': 'tp1', 'text': '示例输入校验', 'done': True},
                                {'id': 'tp2', 'text': '示例异常提示', 'done': False}]}
                for i, title in enumerate(('示例 · 查询结果展示', '示例 · 上线材料整理', '示例 · 接口异常提示'), 1)]
            Path(config.REQUIREMENTS_FILE).write_text(json.dumps(fixtures, ensure_ascii=False), encoding='utf-8')
        if args.sample and args.page == 'chat':
            from tools.model_chat_store import create_session, append_message, rename_session
            session = create_session(model='演示模型')
            rename_session(session['id'], '示例 · 整理测试思路')
            append_message(session['id'], 'user', '请帮我梳理查询页面需要关注的测试点。')
            append_message(session['id'], 'assistant', '可以从三个方向检查：\n\n1. 正常数据、空数据与长文本的展示。\n2. 加载、失败和重试时是否保留输入。\n3. 键盘操作与窄窗口下按钮是否可达。\n\n以上仅为本地演示内容。')
        module_name, class_name = PAGES[args.page]
        namespace = 'ui.' if args.page == 'floating' else 'panels.'
        cls = getattr(importlib.import_module(namespace + module_name), class_name)
        if args.page == 'floating':
            widget = cls(None, 'zh')
            if args.tab:
                widget.toggle_expanded()
                if args.tab == 2:
                    widget._set_mode('chat')
        elif args.page == 'settings':
            widget = cls(dict(config.DEFAULT_SETTINGS))
        elif args.page in ('credit', 'release'):
            widget = cls()
        elif args.page in ('oracle', 'mysql', 'oceanbase', 'dameng'):
            widget = cls('zh', dialect=args.page)
        else:
            widget = cls('zh')
        if args.page == 'daily':
            widget.open_daily_report()
        elif args.page == 'settings':
            widget.section_nav.setCurrentRow(args.tab)
        elif args.page == 'logs':
            widget._set_work_mode('export' if args.tab == 1 else 'session')
        elif args.page == 'interface':
            widget.detail_tabs.setCurrentIndex(args.tab)
        elif hasattr(widget, 'tabs'):
            widget.tabs.setCurrentIndex(args.tab)
        elif hasattr(widget, 'side_tabs'):
            widget.side_tabs.setCurrentIndex(args.tab)
        if args.sample and args.page == 'chat':
            widget.session_list.setCurrentRow(0)
        if args.page != 'floating':
            widget.resize(args.width, args.height)
        if hasattr(widget, 'apply_layout_mode'):
            widget.apply_layout_mode('standard' if args.width >= 1000 else 'narrow', args.height < 640)
            # Reapply the viewport after mode-specific minimum sizes settle, as the
            # containing main-window layout does for a real child page.
            widget.resize(args.width, args.height)
        widget.show()
        for _ in range(4):
            app.processEvents()
        if args.page != 'floating':
            widget.resize(args.width, args.height)
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtTest import QTest
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QTest.qWait(20)
        if args.inspect_layout:
            from PyQt6.QtWidgets import QWidget
            if widget.height() > args.height:
                from PyQt6.QtWidgets import QSplitter
                from PyQt6.QtCore import Qt
                candidates = [widget] + widget.findChildren(QSplitter)
                for container in candidates:
                    for child in [container] + container.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                        if child.isVisible():
                            print(json.dumps({'height_diagnostic': child.objectName(), 'class': type(child).__name__,
                                'height': child.height(), 'minimum': child.minimumHeight(),
                                'hint': child.minimumSizeHint().height()}))
            for child in widget.findChildren(QWidget):
                parent = child.parentWidget()
                if child.isVisible() and parent and child.width() > parent.width():
                    print(json.dumps({'overflow': child.objectName(), 'class': type(child).__name__,
                        'width': child.width(), 'parent_width': parent.width(),
                        'minimum': child.minimumWidth(), 'hint': child.minimumSizeHint().width()}))
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not widget.grab().save(str(output)):
            raise RuntimeError('Could not save preview')
        print(json.dumps({'page': args.page, 'width': widget.width(), 'height': widget.height(),
                          'requested': [args.width, args.height], 'image': str(output)}))
        widget.close()
        widget.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
