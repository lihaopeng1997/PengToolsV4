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


SCAN_PAGE_COUNT = 500
SCAN_PAGE_LIMIT = 2000
INFO_SECRET_KEYS = frozenset({'requirepass', 'masterauth', 'masteruser'})
INFO_PRIORITY = (
    'redis_version', 'redis_mode', 'os', 'arch_bits', 'uptime_in_days',
    'connected_clients', 'used_memory_human', 'used_memory_peak_human',
    'total_connections_received', 'total_commands_processed',
    'instantaneous_ops_per_sec', 'keyspace_hits', 'keyspace_misses',
    'cluster_enabled',
)


def redis_db_count(conn) -> int | None:
    """DBSIZE；失败返回 None，不用 0 假装空库。"""
    try:
        return int(conn.dbsize())
    except Exception:
        return None


def format_redis_bytes(value) -> str:
    if value is None or value == '':
        return '—'
    try:
        n = int(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or '—'
    if n < 0:
        return '—'
    if n < 1024:
        return f'{n} B'
    for unit, size in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if n >= size:
            val = n / size
            if abs(val - round(val)) < 1e-9:
                return f'{int(round(val))} {unit}'
            return f'{val:.1f} {unit}'
    return f'{n} B'


def split_key_prefix(key: str) -> list[str]:
    """逻辑前缀分段：优先 ':' 与 '/'，不把每个 '.' 拆成深层目录。"""
    text = str(key or '')
    if ':' in text:
        return [part for part in text.split(':') if part != '']
    if '/' in text:
        return [part for part in text.split('/') if part != '']
    return [text] if text else []


def build_prefix_index(keys: list[str], *, incomplete: bool = False) -> dict:
    """从已扫描 keys 构建前缀树（不含叶子 Key）。count 语义由 incomplete 标记。"""
    counts: dict[str, int] = {}
    children: dict[str, set[str]] = {}
    for key in keys:
        parts = split_key_prefix(key)
        if len(parts) < 2:
            continue
        acc = []
        for part in parts[:-1]:
            acc.append(part)
            path = ':'.join(acc)
            counts[path] = counts.get(path, 0) + 1
            parent = ':'.join(acc[:-1])
            children.setdefault(parent, set()).add(path)

    def _node(path: str) -> dict:
        name = path.split(':')[-1] if path else ''
        kids = sorted(children.get(path, []), key=str.lower)
        return {
            'name': name,
            'path': path,
            'count': counts.get(path, 0),
            'incomplete': bool(incomplete),
            'children': [_node(child) for child in kids],
        }

    roots = sorted(children.get('', []), key=str.lower)
    return {
        'incomplete': bool(incomplete),
        'prefixes': [_node(path) for path in roots],
        'counts': counts,
    }


def keys_for_prefix(keys: list[str], prefix: str) -> list[str]:
    path = str(prefix or '')
    if not path:
        return list(keys)
    token = path + ':'
    slash = path + '/'
    return [key for key in keys if key.startswith(token) or key.startswith(slash) or key == path]


class RedisScanState:
    """SCAN 分页 + generation，供 UI 与测试共用。"""

    def __init__(self):
        self.generation = 0
        self.cursor: int | dict = 0
        self.finished = False
        self.partial = False
        self.failed_nodes: list[str] = []
        self.pattern = '*'
        self.keys: list[str] = []

    def start(self, pattern: str = '*') -> int:
        self.generation += 1
        self.cursor = 0
        self.finished = False
        self.partial = False
        self.failed_nodes = []
        self.pattern = pattern or '*'
        self.keys = []
        return self.generation

    def apply(
        self,
        generation: int,
        keys: list[str],
        cursor: int | dict | None,
        finished: bool,
        *,
        partial: bool = False,
        failed_nodes: list[str] | None = None,
    ) -> bool:
        if int(generation) != int(self.generation):
            return False
        seen = set(self.keys)
        for key in keys:
            if key not in seen:
                seen.add(key)
                self.keys.append(key)
        if isinstance(cursor, dict):
            self.cursor = dict(cursor)
        else:
            self.cursor = int(cursor or 0)
        self.partial = bool(partial) or bool(failed_nodes)
        if failed_nodes:
            self.failed_nodes = list(failed_nodes)
        self.finished = bool(finished) and not self.partial
        return True


def _as_int(value) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_role(role: str) -> str:
    text = str(role or '').strip().lower()
    if text in ('master', 'primary'):
        return 'primary'
    if text in ('slave', 'replica'):
        return 'replica'
    return text


def _parse_keyspace(keyspace: dict | None) -> tuple[int | None, int | None, int | None]:
    if not keyspace:
        return None, None, None
    keys_total = 0
    expires_total = 0
    avg_samples: list[int] = []
    found = False
    for name, raw in keyspace.items():
        if not str(name).lower().startswith('db'):
            continue
        found = True
        data = raw
        if not isinstance(data, dict):
            data = {}
            for part in str(raw or '').split(','):
                if '=' not in part:
                    continue
                k, v = part.split('=', 1)
                data[k.strip()] = v.strip()
        keys_total += int(data.get('keys') or 0)
        expires_total += int(data.get('expires') or 0)
        avg = _as_int(data.get('avg_ttl'))
        if avg is not None:
            avg_samples.append(avg)
    if not found:
        return None, None, None
    avg_ttl = None
    if avg_samples:
        avg_ttl = int(sum(avg_samples) / len(avg_samples))
    return keys_total, expires_total, avg_ttl


def _safe_info(conn, section: str | None = None) -> dict:
    try:
        if section:
            payload = conn.info(section) or {}
        else:
            payload = conn.info() or {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _cluster_node_row(node) -> dict:
    host = _stringify(getattr(node, 'host', '') or '')
    try:
        port = int(getattr(node, 'port', 0) or 0)
    except (TypeError, ValueError):
        port = 0
    role = _normalize_role(
        _stringify(getattr(node, 'server_type', None) or getattr(node, 'role', None) or '')
    )
    row = {
        'host': host,
        'port': port,
        'role': role or None,
        'status': 'unavailable',
        'keys': None,
        'expires': None,
        'avg_ttl': None,
        'used_memory': None,
        'used_memory_human': None,
        'redis_version': None,
    }
    nconn = getattr(node, 'redis_connection', None)
    if nconn is None:
        return row
    try:
        server = nconn.info('server') or {}
        if not isinstance(server, dict):
            raise TypeError('invalid server info')
        memory = nconn.info('memory') or {}
        if not isinstance(memory, dict):
            memory = {}
        try:
            keyspace = nconn.info('keyspace') or {}
            if not isinstance(keyspace, dict):
                keyspace = {}
        except Exception:
            keyspace = {}
        if not row['role']:
            try:
                repl = nconn.info('replication') or {}
                row['role'] = _normalize_role(_stringify((repl or {}).get('role') or '')) or None
            except Exception:
                pass
        keys, expires, avg_ttl = _parse_keyspace(keyspace)
        if keys is None:
            try:
                keys = int(nconn.dbsize())
            except Exception:
                keys = None
        used = _as_int(memory.get('used_memory'))
        row.update({
            'status': 'online',
            'keys': keys,
            'expires': expires,
            'avg_ttl': avg_ttl,
            'used_memory': used,
            'used_memory_human': _stringify(memory.get('used_memory_human') or '') or format_redis_bytes(used),
            'redis_version': _stringify(server.get('redis_version') or '') or None,
        })
    except Exception:
        row['status'] = 'unavailable'
    return row


def redis_cluster_nodes_info(conn) -> list[dict]:
    get_nodes = getattr(conn, 'get_nodes', None)
    if not callable(get_nodes):
        return []
    try:
        nodes = list(get_nodes() or [])
    except Exception:
        return []
    rows = []
    for node in nodes:
        try:
            rows.append(_cluster_node_row(node))
        except Exception:
            rows.append({
                'host': '', 'port': 0, 'role': None, 'status': 'unavailable',
                'keys': None, 'expires': None, 'avg_ttl': None,
                'used_memory': None, 'used_memory_human': None, 'redis_version': None,
            })
    return rows


def redis_info_sections(conn) -> dict:
    """安全只读 INFO 扁平表；不含密码类字段。"""
    raw = _safe_info(conn)
    if not raw:
        for section in ('server', 'clients', 'memory', 'stats', 'cluster', 'keyspace', 'replication'):
            raw.update(_safe_info(conn, section))
    items = []
    seen = set()
    for key in INFO_PRIORITY:
        if key in raw and key not in INFO_SECRET_KEYS:
            items.append({'name': key, 'value': _stringify(raw.get(key))})
            seen.add(key)
    extras = []
    for key, value in raw.items():
        name = str(key)
        if name in seen or name.lower() in INFO_SECRET_KEYS:
            continue
        if isinstance(value, dict):
            extras.append({'name': name, 'value': _stringify(value)})
        else:
            extras.append({'name': name, 'value': _stringify(value)})
    extras.sort(key=lambda row: row['name'])
    return {'priority': items, 'all': items + extras}


def redis_overview(conn) -> dict:
    server = _safe_info(conn, 'server')
    memory = _safe_info(conn, 'memory')
    cluster = _safe_info(conn, 'cluster')
    stats = _safe_info(conn, 'stats')
    clients = _safe_info(conn, 'clients')
    version = _stringify(server.get('redis_version') or '') or None
    used_memory = _as_int(memory.get('used_memory'))
    used_human = _stringify(memory.get('used_memory_human') or '') or format_redis_bytes(used_memory)
    cluster_enabled = str(cluster.get('cluster_enabled') or server.get('redis_mode') or '') in (
        '1', 'True', 'true', 'cluster',
    )
    nodes = redis_cluster_nodes_info(conn)
    mode = 'cluster' if cluster_enabled or nodes else 'standalone'
    total_keys = None
    total_exact = False
    if mode == 'standalone':
        total_keys = redis_db_count(conn)
        total_exact = total_keys is not None
        if not nodes:
            ks = _safe_info(conn, 'keyspace')
            keys, expires, avg_ttl = _parse_keyspace(ks)
            nodes = [{
                'host': 'local',
                'port': 0,
                'role': 'primary',
                'status': 'online' if version else 'unavailable',
                'keys': keys if keys is not None else total_keys,
                'expires': expires,
                'avg_ttl': avg_ttl,
                'used_memory': used_memory,
                'used_memory_human': used_human,
                'redis_version': version,
            }]
    else:
        primary_keys = []
        all_ok = True
        for node in nodes:
            if _normalize_role(node.get('role') or '') == 'replica':
                continue
            if node.get('status') != 'online' or node.get('keys') is None:
                all_ok = False
                continue
            primary_keys.append(int(node['keys']))
        if primary_keys and all_ok:
            total_keys = sum(primary_keys)
            total_exact = True
        elif primary_keys:
            total_keys = sum(primary_keys)
            total_exact = False
    online = sum(1 for node in nodes if node.get('status') == 'online')
    return {
        'mode': mode,
        'redis_version': version,
        'used_memory': used_memory,
        'used_memory_human': used_human,
        'total_keys': total_keys,
        'total_keys_exact': total_exact,
        'cluster_node_count': len(nodes) if mode == 'cluster' else max(1, len(nodes)),
        'nodes_online': online,
        'nodes': nodes,
        'info': {
            'redis_version': version,
            'redis_mode': _stringify(server.get('redis_mode') or mode),
            'os': _stringify(server.get('os') or ''),
            'arch_bits': _stringify(server.get('arch_bits') or ''),
            'uptime_in_days': _stringify(server.get('uptime_in_days') or ''),
            'connected_clients': _stringify(clients.get('connected_clients') or ''),
            'used_memory_human': used_human,
            'used_memory_peak_human': _stringify(memory.get('used_memory_peak_human') or ''),
            'total_connections_received': _stringify(stats.get('total_connections_received') or ''),
            'total_commands_processed': _stringify(stats.get('total_commands_processed') or ''),
            'instantaneous_ops_per_sec': _stringify(stats.get('instantaneous_ops_per_sec') or ''),
            'keyspace_hits': _stringify(stats.get('keyspace_hits') or ''),
            'keyspace_misses': _stringify(stats.get('keyspace_misses') or ''),
            'cluster_enabled': _stringify(cluster.get('cluster_enabled') or ('1' if mode == 'cluster' else '0')),
        },
        'error': None if (online or mode == 'standalone') else 'all_nodes_failed',
    }


def redis_server_info(conn) -> dict:
    """兼容旧摘要结构；精确计数语义见 redis_overview。"""
    overview = redis_overview(conn)
    return {
        'version': overview.get('redis_version') or '',
        'redis_version': overview.get('redis_version') or '',
        'mode': overview.get('mode') or 'standalone',
        'used_memory_human': overview.get('used_memory_human') or '',
        'total_keys': overview.get('total_keys') if overview.get('total_keys') is not None else 0,
        'total_keys_exact': bool(overview.get('total_keys_exact')),
        'nodes': overview.get('nodes') or [],
        'db': 0 if overview.get('mode') == 'standalone' else None,
    }


def redis_scan_page(
    conn,
    pattern: str = '*',
    *,
    cursor: int | dict | None = 0,
    count: int = SCAN_PAGE_COUNT,
    limit: int = SCAN_PAGE_LIMIT,
    cancel=None,
) -> dict:
    """分批 SCAN，禁止全库 KEYS 命令。原生支持 Standalone 整数游标与 Cluster 字典游标。"""
    match = pattern or '*'
    collected: list[str] = []
    seen: set[str] = set()
    batch_size = max(16, int(count or SCAN_PAGE_COUNT))
    cap = max(1, int(limit or SCAN_PAGE_LIMIT))

    is_cluster = isinstance(cursor, dict) or getattr(conn, 'is_cluster', False) or hasattr(conn, 'get_nodes')

    if not is_cluster:
        cur = int(cursor or 0)
        finished = False
        try:
            while len(collected) < cap:
                if cancel and cancel():
                    break
                nxt = 0
                batch = []
                try:
                    nxt, batch = conn.scan(cursor=cur, match=match, count=batch_size)
                except Exception:
                    try:
                        for index, key in enumerate(conn.scan_iter(match=match, count=batch_size)):
                            batch.append(key)
                            if index + 1 >= batch_size:
                                break
                        nxt = 0
                    except Exception as exc:
                        raise DbError(f'SCAN 失败：{exc}') from exc
                for key in batch or []:
                    k = _b(key)
                    if k not in seen:
                        seen.add(k)
                        collected.append(k)
                cur = int(nxt or 0) if not isinstance(nxt, dict) else 0
                if cur == 0:
                    finished = True
                    break
        except DbError:
            raise
        except Exception as exc:
            raise DbError(f'SCAN 失败：{exc}') from exc
        return {
            'keys': collected,
            'cursor': cur,
            'finished': finished,
            'incomplete': not finished,
            'partial': False,
            'failed_nodes': [],
            'pattern': match,
            'count': len(collected),
        }

    # ── Cluster 模式：维护 node_name -> cursor 状态 ──
    cursors_map: dict[str, int] = {}
    if isinstance(cursor, dict) and cursor:
        cursors_map = {str(k): int(v or 0) for k, v in cursor.items()}

    failed_nodes: list[str] = []

    try:
        if not cursors_map:
            # 首次全集群扫描：广播 cursor=0
            raw_res = conn.scan(cursor=0, match=match, count=batch_size)
            if isinstance(raw_res, tuple) and len(raw_res) == 2:
                init_cursors, init_batch = raw_res
                if isinstance(init_cursors, dict):
                    cursors_map = {str(k): int(v or 0) for k, v in init_cursors.items()}
                else:
                    cursors_map = {'default': int(init_cursors or 0)}
                for key in init_batch or []:
                    k = _b(key)
                    if k not in seen:
                        seen.add(k)
                        collected.append(k)
            elif isinstance(raw_res, dict):
                cursors_map = {str(k): int(v or 0) for k, v in raw_res.items()}

        # 遍历所有未耗尽节点（node cursor != 0）进行分页
        while len(collected) < cap:
            if cancel and cancel():
                break
            active_nodes = [name for name, c in cursors_map.items() if c != 0]
            if not active_nodes:
                break
            progress_made = False
            for name in active_nodes:
                if cancel and cancel():
                    break
                node_cur = cursors_map.get(name, 0)
                if node_cur == 0:
                    continue

                if hasattr(conn, 'get_node'):
                    try:
                        node_obj = conn.get_node(node_name=name)
                    except Exception:
                        node_obj = None
                    if node_obj is None:
                        # get_node 返回 None 时不得把 target_nodes=None 传给 cluster scan（防全集群广播）
                        if name not in failed_nodes:
                            failed_nodes.append(name)
                        cursors_map.pop(name, None)
                        continue
                else:
                    node_obj = name

                try:
                    cur_res, batch = conn.scan(
                        cursor=node_cur,
                        match=match,
                        count=batch_size,
                        target_nodes=node_obj,
                    )
                    # 必须完整消费该 batch，不得推进 cursor 后丢弃 batch 尾部
                    for key in batch or []:
                        k = _b(key)
                        if k not in seen:
                            seen.add(k)
                            collected.append(k)
                    if isinstance(cur_res, dict):
                        new_cur = cur_res.get(name, 0)
                    else:
                        new_cur = int(cur_res or 0)
                    cursors_map[name] = int(new_cur or 0)
                    progress_made = True
                except Exception:
                    # 单节点网络波动/部分节点异常不拖垮其他节点，记录为 failed_nodes，不假装 finished
                    if name not in failed_nodes:
                        failed_nodes.append(name)
                    cursors_map.pop(name, None)
                    progress_made = True
                if len(collected) >= cap:
                    break
            if not progress_made:
                break
    except DbError:
        raise
    except Exception as exc:
        raise DbError(f'SCAN 失败：{exc}') from exc

    is_partial = bool(failed_nodes)
    finished = (all(c == 0 for c in cursors_map.values()) if cursors_map else True) and not is_partial
    incomplete = (not finished) or is_partial
    return {
        'keys': collected,
        'cursor': cursors_map,
        'finished': finished,
        'incomplete': incomplete,
        'partial': is_partial,
        'failed_nodes': failed_nodes,
        'pattern': match,
        'count': len(collected),
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
