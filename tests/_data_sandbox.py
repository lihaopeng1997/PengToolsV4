# -*- coding: utf-8 -*-
"""把测试进程的用户数据目录重定向到临时沙箱，防止测试写坏真实 data/。

由 ``tests/__init__.py`` 在任何测试模块导入前激活，``python -m unittest tests.test_x``
与 pytest 都会先导入 ``tests`` 包，因此两种运行方式共用这一处隔离。

做两件事：
1. 把 ``config`` 中所有位于真实数据目录下的路径常量（CONFIG_DIR / SETTINGS_FILE /
   AGENT_DIR ...）改指到沙箱；后续 ``from config import X`` 拿到的都是沙箱路径。
2. 替换 ``config.local_data_dir``：无参调用（运行时解析数据目录的写法，如 secure_store、
   ie_proxy、web_diagnostics）返回沙箱；显式传 executable/frozen 的调用保持原语义，
   便于测试打包路径规则。
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REAL_DATA_DIR: str | None = None
SANDBOX_DIR: str | None = None
# 被重定向的 config 常量 -> 原真实路径（回归测试据此检查真实目录未被写）
REAL_PATHS: dict[str, str] = {}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def is_under(path: str, root: str) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def activate() -> str:
    """幂等：返回沙箱目录。"""
    global REAL_DATA_DIR, SANDBOX_DIR
    if SANDBOX_DIR:
        return SANDBOX_DIR
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import config

    original_local_data_dir = config.local_data_dir
    real_dir = original_local_data_dir()
    sandbox = tempfile.mkdtemp(prefix='pengtools-test-data-')

    def local_data_dir(executable=None, frozen=None):
        if executable is None and frozen is None:
            return sandbox
        return original_local_data_dir(executable, frozen)

    local_data_dir.__doc__ = original_local_data_dir.__doc__
    local_data_dir._pengtools_test_sandbox = True
    config.local_data_dir = local_data_dir

    for name, value in list(vars(config).items()):
        if name.isupper() and isinstance(value, str) and value and is_under(value, real_dir):
            REAL_PATHS[name] = value
            relative = os.path.relpath(os.path.abspath(value), os.path.abspath(real_dir))
            setattr(config, name, sandbox if relative == '.' else os.path.join(sandbox, relative))

    REAL_DATA_DIR, SANDBOX_DIR = real_dir, sandbox
    atexit.register(shutil.rmtree, sandbox, True)
    return sandbox
