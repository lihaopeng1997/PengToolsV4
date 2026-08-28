# -*- coding: utf-8 -*-
"""模型对话意图分流：轻量关键字规则，判断消息是否属于取数意图。

返回 'sql' / 'linux' / 'none'。仅被 panels/model_chat_panel.py 使用，
不得被其它模块 import（棒2 铁律）。
"""

from __future__ import annotations


def detect_take_data_intent(text: str) -> str:
    t = (text or '').lower()
    linux_keys = ('日志', '查日志', 'tail', 'grep', '进程', '磁盘', '内存',
                  'ps', 'df', 'free', '主机名', 'cpu', 'uptime')
    sql_keys = ('查', '查询', 'select', '表', '字段', '列', '数据库',
                '创建日期', '创建时间', '统计', '合计', 'count', '索引', '关联', 'join')
    if any(k in t for k in linux_keys):
        return 'linux'
    if any(k in t for k in sql_keys):
        return 'sql'
    return 'none'
