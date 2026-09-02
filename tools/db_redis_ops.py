# -*- coding: utf-8 -*-
"""Redis 专用操作：Key 树扫描、类型/TTL/值读取、删除/重命名/过期。

供 panels/db_redis_panel.py 调用；连接由 tools.db_connect.open_connection 建立，
decode_responses=False（返回 bytes），本模块统一做字节安全解码。
"""

from __future__ import annotations

import re
from typing import Any

from tools.db_connect import DbError


def _b(value: Any) -> str:
    """字节安全解码：二进制 key/value 用 errors='replace' 转文本，不抛 UnicodeDecodeError。"""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, dict):
        return '{' + ', '.join(
            f'{_stringify(k)}: {_stringify(v)}' for k, v in value.items()
        ) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_stringify(item) for item in value) + ']'
    if isinstance(value, set):
        return '{' + ', '.join(_stringify(item) for item in sorted(value, key=lambda x: _stringify(x))) + '}'
    return str(value)


def redis_type(conn, key: str) -> str:
    try:
        return _b(conn.type(key))
    except Exception as exc:
        raise DbError(f'读取类型失败：{exc}') from exc


def redis_ttl(conn, key: str) -> int:
    try:
        return int(conn.ttl(key) or -2)
    except Exception as exc:
        raise DbError(f'读取 TTL 失败：{exc}') from exc


def redis_get_value(conn, key: str, kind: str | None = None) -> Any:
    """按类型读取 key 的值；kind 未知时自动探测。"""
    try:
        t = (kind or redis_type(conn, key)).lower()
    except DbError:
        t = ''
    try:
        if t == 'string':
            return _b(conn.get(key))
        if t == 'hash':
            raw = conn.hgetall(key) or {}
            return {_b(k): _stringify(v) for k, v in raw.items()}
        if t == 'list':
            return [_stringify(v) for v in (conn.lrange(key, 0, -1) or [])]
        if t == 'set':
            return [_stringify(v) for v in (conn.smembers(key) or [])]
        if t == 'zset':
            raw = conn.zrange(key, 0, -1, withscores=True) or []
            return [{'member': _b(m), 'score': _stringify(s)} for m, s in raw]
        return _b(conn.get(key))
    except Exception as exc:
        raise DbError(f'读取值失败：{exc}') from exc


def redis_delete_key(conn, key: str) -> int:
    try:
        return int(conn.delete(key) or 0)
    except Exception as exc:
        raise DbError(f'删除失败：{exc}') from exc


def redis_rename_key(conn, old: str, new: str) -> bool:
    if not new or not new.strip():
        raise DbError('新 Key 名不能为空')
    try:
        return bool(conn.rename(old, new.strip()))
    except Exception as exc:
        raise DbError(f'重命名失败：{exc}') from exc


def redis_expire_key(conn, key: str, seconds: int) -> bool:
    try:
        if int(seconds) < 0:
            # 负值 → 移除过期（永不过期）
            return bool(conn.persist(key))
        return bool(conn.expire(key, int(seconds)))
    except Exception as exc:
        raise DbError(f'设置过期失败：{exc}') from exc


def redis_scan_keys(conn, pattern: str = '*', limit: int = 200) -> list[str]:
    """扫描 key（按 pattern），返回解码后的 key 列表。"""
    try:
        keys = []
        for key in conn.scan_iter(match=pattern or '*', count=min(int(limit), 500)):
            keys.append(_b(key))
            if len(keys) >= int(limit):
                break
        return keys
    except Exception as exc:
        raise DbError(f'SCAN 失败：{exc}') from exc


def redis_db_count(conn) -> int:
    """返回当前 DB 的 key 数量（dbsize，cluster 不支持时返回 0）。"""
    try:
        return int(conn.dbsize() or 0)
    except Exception:
        return 0


def redis_server_info(conn) -> dict:
    """返回版本 / 模式 / 内存 / 节点等摘要（失败时返回占位）。"""
    version = ''
    used_memory_human = ''
    cluster_enabled = False
    try:
        server = conn.info('server') or {}
        version = _stringify(server.get('redis_version') or '')
    except Exception:
        server = {}
    try:
        memory = conn.info('memory') or {}
        used_memory_human = _stringify(memory.get('used_memory_human') or '')
    except Exception:
        memory = {}
    try:
        cluster = conn.info('cluster') or {}
        cluster_enabled = str(cluster.get('cluster_enabled') or '0') in ('1', 'True', 'true')
    except Exception:
        cluster = {}
    total_keys = 0
    try:
        total_keys = int(conn.dbsize() or 0)
    except Exception:
        total_keys = 0
    nodes: list[dict] = []
    get_nodes = getattr(conn, 'get_nodes', None)
    if callable(get_nodes):
        try:
            for node in get_nodes() or []:
                host = _stringify(getattr(node, 'host', '') or '')
                port = int(getattr(node, 'port', 0) or 0)
                role = _stringify(
                    getattr(node, 'server_type', None)
                    or getattr(node, 'role', None)
                    or ''
                )
                keys = 0
                try:
                    nconn = node.redis_connection if hasattr(node, 'redis_connection') else None
                    if nconn is not None:
                        keys = int(nconn.dbsize() or 0)
                except Exception:
                    keys = 0
                nodes.append({'host': host, 'port': port, 'role': role, 'keys': keys})
        except Exception:
            nodes = []
    mode = 'cluster' if cluster_enabled or nodes else 'standalone'
    return {
        'version': version,
        'redis_version': version,
        'mode': mode,
        'used_memory_human': used_memory_human,
        'total_keys': total_keys,
        'nodes': nodes,
        'db': 0,
    }


# ─── Key 树分层 ───────────────────────────────────────────────────────────

def build_key_tree(keys: list[str]) -> list[dict]:
    """把扁平 key 列表按 ':' 分层组织为树节点列表。

    返回 [{name, full, is_folder, children}...]，folder 与 key 节点按名字排序，
    key 节点携带完整 key 名（full）。

    注意：key 与文件夹同名时（如 device:001 与 device:001:status 共存），
    key 节点与文件夹**分离存储**，可同时存在——key 显示为 `001`，文件夹显示为
    `001:`，互不覆盖，避免 key 节点被错误复用为文件夹导致层级错乱。
    """
    root: dict = {'name': '', 'full': '', 'is_folder': True, 'folders': {}, 'keys': {}}

    def _folder(node, name):
        folders = node.setdefault('folders', {})
        if name not in folders:
            folders[name] = {'name': name, 'full': '', 'is_folder': True, 'folders': {}, 'keys': {}}
        return folders[name]

    for key in keys:
        parts = key.split(':')
        cur = root
        for part in parts[:-1]:
            cur = _folder(cur, part)
        leaf = parts[-1]
        # key 节点与 folder 节点分别存放，同名不互相覆盖
        cur.setdefault('keys', {})[leaf] = {'name': leaf, 'full': key, 'is_folder': False}

    def _convert(node):
        children = []
        for name, child in node.get('folders', {}).items():
            converted = _convert(child)
            converted['name'] = name
            children.append(converted)
        for name, key_node in node.get('keys', {}).items():
            children.append({
                'name': key_node['name'],
                'full': key_node['full'],
                'is_folder': False,
                'children': [],
            })
        # 文件夹在前，key 节点在后；各自按名字排序
        folders = sorted([c for c in children if c.get('is_folder')], key=lambda c: c['name'].lower())
        keys_only = sorted([c for c in children if not c.get('is_folder')], key=lambda c: c['name'].lower())
        return {
            'name': node.get('name', ''),
            'full': node.get('full', ''),
            'is_folder': node.get('is_folder', True),
            'children': folders + keys_only,
        }

    return _convert(root)['children']


def filter_keys_by_pattern(keys: list[str], pattern: str) -> list[str]:
    """按简单通配符（* ?）过滤 key 列表。"""
    pat = str(pattern or '').strip()
    if not pat or pat == '*':
        return keys
    regex = '^' + re.escape(pat).replace(r'\*', '.*').replace(r'\?', '.') + '$'
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error:
        return [k for k in keys if pat.lower() in k.lower()]
    return [k for k in keys if compiled.match(k)]
