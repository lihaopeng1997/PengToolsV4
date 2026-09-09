"""Render actual dialogs against a bounded screen using temporary local configuration."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['PENGTOOLS_DISABLE_MOTION'] = '1'

DIALOGS = {
    'connection': ('ui.connection_dialog', 'ConnectionDialog', []),
    'requirement': ('panels.requirement_panel', 'RequirementDialog', []),
    'attachment': ('panels.requirement_panel', 'RequirementAttachmentDialog', [{'name': 'DEMO.txt', 'content': '本地演示内容'}]),
    'ticket': ('panels.ticket_submit_dialog', 'TicketSubmitDialog', [[{'id': 'DEMO-1', 'code': 'DEMO-1', 'title': '示例需求'}]]),
    'ticket-config': ('panels.ticket_submit_dialog', 'TicketSubmitConfigDialog', []),
    'test-points': ('panels.test_points_editor', 'TestPointsDialog', [{'id': 'DEMO-1', 'title': '示例需求', 'test_points': [{'id': 'p1', 'text': '示例测试点，验证长文本换行与操作按钮可达。' * 3, 'done': False}]}]),
    'server': ('panels.ops_log_panel', 'ServerEditorDialog', []),
    'servers': ('panels.ops_log_panel', 'ServerManageDialog', []),
    'category': ('panels.ops_log_panel', 'CategoryManageDialog', []),
    'log-settings': ('panels.ops_log_panel', 'LogSettingsDialog', []),
    'history': ('panels.ops_log_panel', 'CommandHistoryDialog', []),
    'command': ('panels.ops_panel', 'CustomCommandDialog', []),
    'knowledge': ('panels.personal_panel', 'KnowledgeEditDialog', []),
    'paste': ('panels.personal_panel', 'PasteKnowledgeDialog', []),
    'skills': ('panels.model_chat_panel', '_SkillManagerDialog', []),
    'objects': ('panels.ai_token_edit', 'ObjectPickDialog', ['zh', {}]),
    'month': ('panels.requirement_panel', 'MonthPickerDialog', []),
    'svn': ('panels.requirement_panel', 'SvnCheckoutDialog', []),
    'help': ('ui.help_dialog', 'UserGuideDialog', []),
    'confirm': ('ui.confirm_dialog', 'ConfirmActionDialog', ['确认操作', '这是本地演示说明，不执行实际操作。']),
    'notice': ('ui.confirm_dialog', 'AppNoticeDialog', ['操作结果', '这是本地演示通知。']),
    'next': ('ui.confirm_dialog', 'NextStepDialog', ['后续操作', '本地演示', [('view', '查看详情', True)]]),
    'close': ('ui.confirm_dialog', 'CloseActionDialog', []),
    'https': ('ui.confirm_dialog', 'HttpsCertConsentDialog', []),
}
for _kind in ('confirm', 'notice', 'next'):
    _module, _name, _arguments = DIALOGS[_kind]
    _long_arguments = list(_arguments)
    _long_arguments[1] = '\n'.join(f'示例 {i}：详细说明需要完整保留，可通过滚动查看。' for i in range(40))
    DIALOGS[_kind + '-long'] = (_module, _name, _long_arguments)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dialog', choices=sorted(DIALOGS))
    parser.add_argument('--font', type=int, default=13)
    parser.add_argument('--screen-width', type=int, default=960)
    parser.add_argument('--screen-height', type=int, default=640)
    args = parser.parse_args()
    import config
    from PyQt6.QtCore import QRect, QPoint
    from PyQt6.QtWidgets import QApplication, QPushButton, QAbstractScrollArea
    from PyQt6.QtTest import QTest
    app = QApplication(['prism-dialog-preview'])
    from PyQt6.QtGui import QFontDatabase
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    with tempfile.TemporaryDirectory(prefix='prism-dialog-') as directory, \
            patch('socket.socket.connect', side_effect=RuntimeError('Dialog preview forbids network')):
        original = Path(config.CONFIG_DIR).resolve()
        config.local_data_dir = lambda: directory
        for key, value in list(vars(config).items()):
            if key.isupper() and isinstance(value, str):
                try:
                    suffix = Path(value).resolve().relative_to(original)
                except (ValueError, OSError):
                    continue
                setattr(config, key, str(Path(directory) / suffix))
        from ui.theme_manager import ThemeManager
        ThemeManager.instance().apply(app, 'calm', font_size=args.font)
        from ui import dialog_buttons
        original_clamp = dialog_buttons.clamp_dialog_geometry
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, args.screen_width, args.screen_height))
        def clamp(dialog, *positional, **kwargs):
            kwargs['screen'] = screen
            return original_clamp(dialog, *positional, **kwargs)
        with patch.object(dialog_buttons, 'clamp_dialog_geometry', side_effect=clamp):
            module, name, positional = DIALOGS[args.dialog]
            dialog = getattr(importlib.import_module(module), name)(*positional)
            dialog.show()
            QTest.qWait(80)
            outside = []
            for button in dialog.findChildren(QPushButton):
                if not button.isVisible():
                    continue
                ancestor = button.parentWidget()
                scrollable = False
                clipped = False
                while ancestor is not None and ancestor is not dialog:
                    scrollable |= isinstance(ancestor, QAbstractScrollArea)
                    clipped |= not ancestor.rect().contains(QRect(button.mapTo(ancestor, QPoint()), button.size()))
                    ancestor = ancestor.parentWidget()
                if not scrollable and (clipped or not dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size()))):
                    outside.append(button.objectName() or button.text())
            folder = ROOT / 'docs/ui/prism-implementation-2026-09/dialogs'
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f'{args.dialog}-{args.font}.png'
            dialog.grab().save(str(output))
            result = {'dialog': args.dialog, 'font': args.font, 'size': [dialog.width(), dialog.height()],
                      'screen': [args.screen_width, args.screen_height], 'outside_fixed_buttons': outside}
            (folder / f'{args.dialog}-{args.font}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))
            dialog.reject()
            dialog.deleteLater()
            app.processEvents()
            if outside or result['size'][0] > args.screen_width - 48 or result['size'][1] > args.screen_height - 48:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
