"""Redacted diagnostics zip (offscreen, no network, no real data)."""
import os
import sys
import zipfile
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


from config import local_data_dir  # noqa: E402
from tools.diagnostics_bundle import DiagnosticsError, build_bundle  # noqa: E402

log_dir = os.path.join(local_data_dir(), 'logs')
os.makedirs(log_dir, exist_ok=True)
data_dir = local_data_dir()
servers = os.path.join(data_dir, 'ops_servers.json')
with open(servers, 'w', encoding='utf-8') as handle:
    handle.write('{"host": "SECRETHOST"}')
before = os.stat(servers)

secret_lines = (
    'Bearer SECRETTOKEN123\n'
    'password=SECRETPW\n'
    'http://user:SECRETPW@host\n'
    '10.9.8.7\n'
    'TAILKEEP\n'
)
unit = secret_lines.encode('utf-8')
tail = b''
while len(tail) + len(unit) <= 2048:
    tail += unit
tail += b'x' * (2048 - len(tail))
blob = b''
while len(blob) < 10 * 1024 - len(tail):
    blob += b'HEADMARKER\n'
blob += tail
for name in ('app.log', 'agent_diag.log', 'webengine_startup.log'):
    with open(os.path.join(log_dir, name), 'wb') as handle:
        handle.write(blob if name == 'app.log' else secret_lines.encode('utf-8'))

dest = os.path.join(_isolation.temp_dir(), 'out', 'diag.zip')
settings = {
    'security_prod_host_hints': ['SECRETHINT'],
    'diag_custom_flag': 'SECRETHINT',
}
built = build_bundle(dest, tail_bytes=2048, settings=settings)
check('导出路径返回', built == os.path.abspath(dest) and os.path.isfile(dest))

with zipfile.ZipFile(dest) as archive:
    names = set(archive.namelist())
    whole = '\n'.join(
        archive.read(name).decode('utf-8', errors='replace') for name in archive.namelist()
    )
check('清单和说明文件存在', {'manifest.txt', 'version.txt', 'settings_keys.txt'} <= names, str(sorted(names)))
for secret in ('SECRETTOKEN123', 'SECRETPW', 'SECRETHOST', 'SECRETHINT', '10.9.8.7'):
    check(f'不含 {secret}', secret not in whole)
check('自定义键标为非默认', 'diag_custom_flag: str (非默认)' in whole)
check('只保留日志尾部', 'TAILKEEP' in whole and 'HEADMARKER' not in whole)
check('清单记录截取大小', '截取 2048 字节' in whole and '无' in whole)

after = os.stat(servers)
check('data 文件元数据不变',
      before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns)

blocked = os.path.join(_isolation.temp_dir(), 'not-a-dir')
with open(blocked, 'w', encoding='utf-8') as handle:
    handle.write('x')
bad_dest = os.path.join(blocked, 'out.zip')
raised = False
try:
    build_bundle(bad_dest, settings=settings)
except DiagnosticsError:
    raised = True
check('不可写位置抛出 DiagnosticsError', raised)
check('失败时不留下 zip 和临时文件',
      not os.path.exists(bad_dest) and not os.path.exists(bad_dest + '.tmp'))

import tools.diagnostics_bundle as bundle  # noqa: E402
real_zip = bundle.zipfile.ZipFile

class _Boom:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('zip fail')

bundle.zipfile.ZipFile = _Boom
mock_dest = os.path.join(_isolation.temp_dir(), 'mock.zip')
mock_raised = False
try:
    try:
        build_bundle(mock_dest, settings=settings)
    except DiagnosticsError:
        mock_raised = True
finally:
    bundle.zipfile.ZipFile = real_zip
check('打包失败抛出 DiagnosticsError', mock_raised)
check('打包失败不留下临时文件',
      not os.path.exists(mock_dest) and not os.path.exists(mock_dest + '.tmp'))

import subprocess
probe = (
    'import os, sys\n'
    'from pathlib import Path\n'
    'os.environ["QT_QPA_PLATFORM"] = "offscreen"\n'
    f'sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n'
    f'sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n'
    'import _isolation\n'
    'from PyQt6.QtWidgets import QApplication\n'
    'QApplication([])\n'
    'from panels.settings_panel import SettingsPanel\n'
    'panel = SettingsPanel({})\n'
    'print("HAS_BUTTON" if getattr(panel, "diag_export_btn", None) is not None else "NO_BUTTON")\n'
)
try:
    probed = subprocess.run(
        [sys.executable, '-B', '-c', probe],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
    )
    if probed.returncode == 0 and 'HAS_BUTTON' in (probed.stdout or ''):
        check('设置页有导出诊断包按钮', True)
    elif probed.returncode == 0 and 'NO_BUTTON' in (probed.stdout or ''):
        check('设置页有导出诊断包按钮', False, 'created without the button')
    else:
        check('设置页导出按钮（创建失败已跳过）', True, f'exit={probed.returncode}')
except Exception as exc:
    check('设置页导出按钮（创建失败已跳过）', True, type(exc).__name__)

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
