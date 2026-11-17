"""The data-center host adapter is the shared agent_core adapter."""
import importlib
import inspect
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


import tools.agent_core.host_model_adapter as core_adapter
import tools.data_center.host_model_adapter as data_center_adapter

for _name in ('AgentModelHostAdapter', 'HostModelAdapter', 'AgentHostModelAdapter'):
    check(
        f'{_name} 同一对象',
        getattr(data_center_adapter, _name) is getattr(core_adapter, _name),
    )

check('__all__ 同一对象', data_center_adapter.__all__ is core_adapter.__all__)

_signature = inspect.signature(core_adapter.AgentModelHostAdapter.__init__)
for _param_name in ('strict_tool_envelope', 'expose_reasoning'):
    _param = _signature.parameters.get(_param_name)
    check(
        f'{_param_name} 默认 False',
        _param is not None and _param.default is False,
    )

check(
    '有 release_run',
    callable(getattr(core_adapter.AgentModelHostAdapter, 'release_run', None)),
)

from PyQt6.QtWidgets import QApplication

QApplication([])

for _module_name in (
    'tools.log_analysis.runtime',
    'tools.data_center.host_runtime',
    'ui.data_center.integration',
):
    try:
        importlib.import_module(_module_name)
    except Exception as exc:  # noqa: BLE001
        check(f'可导入 {_module_name}', False, type(exc).__name__)
    else:
        check(f'可导入 {_module_name}', True)


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
