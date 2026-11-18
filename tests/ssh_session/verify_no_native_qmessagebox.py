"""Native QMessageBox calls stay on the shared confirm dialog."""
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

from PyQt6.QtWidgets import QApplication

QApplication([])

PASS, FAIL = [], []

ROOT = Path(__file__).resolve().parents[2]
ALLOW = {
    Path('run.py'),
    Path('ui/dialog_buttons.py'),
    Path('ui/confirm_dialog.py'),
}
CALL = re.compile(r'\bQMessageBox\s*[\.\(]')
SKIP_DIRS = {'.venv', '.venv-build', 'tests', '__pycache__', '.git', 'node_modules', 'site-packages'}


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


def scan_calls():
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_DIRS and not name.startswith('.venv')
        ]
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            path = Path(dirpath) / filename
            rel = path.relative_to(ROOT)
            if rel in ALLOW:
                continue
            for lineno, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if 'qmessagebox-allowed' in line.lower():
                    continue
                if CALL.search(line):
                    hits.append(f'{rel.as_posix()}:{lineno}: {line.strip()}')
    return hits


hits = scan_calls()
check('仓库里没有未允许的 QMessageBox 调用', hits == [], '; '.join(hits[:8]))

import ui.log_analysis.workspace as workspace_mod


class _Status:
    def __init__(self):
        self.texts = []

    def setText(self, text):
        self.texts.append(text)


class _Store:
    def __init__(self):
        self.deleted = []

    def delete(self, ident):
        self.deleted.append(ident)


seen = []


def fake_confirm(parent, title, message, confirm_text='确认删除', danger=True, scope_text=''):
    seen.append((title, message, confirm_text, danger))
    return False


workspace_mod.confirm_action = fake_confirm
host = SimpleNamespace(
    selected_id='cp1',
    restored={'cp1': {'id': 'cp1', 'service': 'nginx'}},
    store=_Store(),
    status=_Status(),
    service=None,
)
workspace_mod.LogAnalysisWorkspace.delete_task(host)
check('删除检查点走统一弹框', seen and seen[0][0] == '删除任务检查点' and seen[0][2] == '删除' and seen[0][3] is True, str(seen))
check('取消删除检查点时不删除', host.store.deleted == [], str(host.store.deleted))

print()
try:
    _isolation.assert_untouched()
    check('未触碰真实数据目录与用户配置', True)
except AssertionError as exc:
    check('未触碰真实数据目录与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
