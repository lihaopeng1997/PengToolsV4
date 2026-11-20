# -*- coding: utf-8 -*-
"""JSON 查看器使用的纯逻辑，和 Qt 界面解耦以便回归测试。"""
import json
import re


_SIMPLE_KEY = re.compile(r'^[A-Za-z_$][A-Za-z0-9_$]*$')


def parse_json_text(text):
    if not text or not text.strip():
        raise ValueError('JSON 内容为空')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列，{exc.msg}') from exc
    except RecursionError as exc:
        raise ValueError('JSON 嵌套过深，超过允许层级') from exc


def find_duplicate_json_keys(text, limit=10):
    """检测 JSON 对象中的重复键名；遇到语法错误或过深嵌套返回空列表。"""
    if not text or not text.strip():
        return []
    duplicates = []
    seen_dups = set()

    def _hook(pairs):
        seen = set()
        obj = {}
        for k, v in pairs:
            if k in seen and k not in seen_dups:
                seen_dups.add(k)
                if len(duplicates) < limit:
                    duplicates.append(str(k))
            seen.add(k)
            obj[k] = v
        return obj

    try:
        json.loads(text, object_pairs_hook=_hook)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return []
    return duplicates


def format_json_text(text):
    """格式化 JSON：对象字段按 A-Z 字典序递归排序，数组元素保持原序。"""
    parsed = parse_json_text(text)
    try:
        return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    except RecursionError as exc:
        raise ValueError('JSON 嵌套过深，超过允许层级') from exc


def json_path_child(parent, key):
    if isinstance(key, int):
        return f'{parent}[{key}]'
    if _SIMPLE_KEY.fullmatch(str(key)):
        return f'{parent}.{key}'
    escaped = str(key).replace('\\', '\\\\').replace("'", "\\'")
    return f"{parent}['{escaped}']"


def iter_json_nodes(value, path='$', key='$'):
    """按深度优先顺序返回 (path, key, value)。"""
    yield path, key, value
    if isinstance(value, dict):
        for child_key in sorted(value):
            child_value = value[child_key]
            child_path = json_path_child(path, child_key)
            yield from iter_json_nodes(child_value, child_path, child_key)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            child_path = json_path_child(path, index)
            yield from iter_json_nodes(child_value, child_path, index)


def json_type_name(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, (int, float)):
        return 'number'
    return type(value).__name__


def node_value_text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def node_json_text(value):
    """节点复制使用与格式化文本一致的对象键排序规则。"""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def search_json_nodes(value, query):
    needle = query.strip().casefold()
    if not needle:
        return []
    matches = []
    for path, key, node_value in iter_json_nodes(value):
        # 容器节点不把整个子树序列化后参与匹配，否则搜索一个叶子值会先
        # 命中所有祖先，无法实现查看器需要的精确定位。
        searchable_value = '' if isinstance(node_value, (dict, list)) else node_value_text(node_value)
        haystack = '\n'.join((str(key), path, searchable_value)).casefold()
        if needle in haystack:
            matches.append((path, key, node_value))
    return matches
