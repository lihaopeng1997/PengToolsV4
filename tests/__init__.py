# -*- coding: utf-8 -*-
"""定向测试包。运行：python -m unittest tests.test_<域> -v"""

# 必须先于任何测试模块导入 config：所有用户数据路径改指临时沙箱，测试不得写真实 data/。
from . import _data_sandbox

_data_sandbox.activate()
