# -*- coding: utf-8 -*-
"""SQL 控制台：多方言连接（Oracle / MySQL / OceanBase / 达梦 / Redis / MongoDB）。

Oracle 瘦模式依赖 cryptography 的 pbkdf2 等子模块；打包时必须显式导入，否则 DPY-3016。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from config import HARNESS_CONNECTIONS_FILE, ensure_config_dir
from tools.db_contracts import DEFAULT_PORTS, DIALECTS, normalize_oceanbase_mode

# PyInstaller 静态分析扫不到 oracledb 内部 import，必须在本模块顶层拉齐。
try:
    from cryptography import x509  # noqa: F401
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
    from cryptography.hazmat.primitives.kdf import pbkdf2  # noqa: F401
    import encodings.idna  # noqa: F401
except Exception:
    pass
from tools.oracle_runtime import (
    OracleRuntimeError, ensure_oracle_client, load_oracle_paths, thick_required_message,
)
from tools.sql_guard import (
    classify_statement, is_read_query, leading_verb, redact_error, reject_reason,
    strip_sql_comments,
)

PAGE_SIZE = 20
MAX_ROWS = 2000  # Redis/Mongo 等非 SQL 路径保护；SQL 分页不再用它做总行上限
FETCH_CHUNK = 500
RESULT_BYTE_LIMIT = 50 * 1024 * 1024
CELL_MAX = 200

NOSQL = frozenset({'redis', 'mongodb'})
REDIS_AUTH_NONE = 'none'
REDIS_AUTH_PASSWORD = 'password'
REDIS_AUTH_ACL = 'acl'
MONGO_MECH_AUTO = 'auto'


class DbError(Exception):
    pass


def is_mongo_uri(host: str) -> bool:
    text = str(host or '').strip().lower()
    return text.startswith('mongodb://') or text.startswith('mongodb+srv://')


def normalize_redis_auth_mode(item: dict | None) -> str:
    """历史配置：有 username → acl；只有密码 → password；都没有 → none。"""
    data = item or {}
    raw = str(data.get('auth_mode') or '').strip().lower()
    if raw in (REDIS_AUTH_NONE, REDIS_AUTH_PASSWORD, REDIS_AUTH_ACL):
        return raw
    if str(data.get('username') or '').strip():
        return REDIS_AUTH_ACL
    if str(data.get('password') or '').strip():
        return REDIS_AUTH_PASSWORD
    return REDIS_AUTH_NONE


def redis_auth_kwargs(item: dict | None, password: str) -> dict[str, Any]:
    mode = normalize_redis_auth_mode(item)
    if mode == REDIS_AUTH_NONE:
        return {'username': None, 'password': None}
    if mode == REDIS_AUTH_PASSWORD:
        return {'username': None, 'password': password or None}
    username = str((item or {}).get('username') or '').strip() or None
    return {'username': username, 'password': password or None}


def normalize_redis_seed_nodes(item: dict | None) -> list[dict]:
    """Cluster seed 列表；旧配置仅 host/port 时视为单个 seed。"""
    data = item or {}
    result: list[dict] = []
    raw_seeds = data.get('seed_nodes')
    if isinstance(raw_seeds, list):
        for entry in raw_seeds:
            if not isinstance(entry, dict):
                continue
            host = str(entry.get('host') or '').strip()
            if not host:
                continue
            try:
                port = int(entry.get('port'))
            except (TypeError, ValueError) as exc:
                raise DbError(f'Redis 端口无效：{entry.get("port")!r}（须为 1–65535）') from exc
            if not 1 <= port <= 65535:
                raise DbError(f'Redis 端口无效：{port}（须为 1–65535）')
            result.append({'host': host, 'port': port})
    if result:
        return result
    host = str(data.get('host') or '').strip() or '127.0.0.1'
    try:
        port = int(data.get('port') or DEFAULT_PORTS.get('redis', 6379))
    except (TypeError, ValueError) as exc:
        raise DbError(f'Redis 端口无效：{data.get("port")!r}（须为 1–65535）') from exc
    if not 1 <= port <= 65535:
        raise DbError(f'Redis 端口无效：{port}（须为 1–65535）')
    return [{'host': host, 'port': port}]


def mongo_auth_source(item: dict | None) -> str:
    data = item or {}
    explicit = str(data.get('auth_source') or '').strip()
    if explicit:
        return explicit
    database = str(data.get('database') or '').strip()
    if database:
        return database
    return 'admin'


def mongo_auth_mechanism(item: dict | None) -> str:
    raw = str((item or {}).get('auth_mechanism') or '').strip()
    if not raw or raw.lower() in (MONGO_MECH_AUTO, 'automatic', 'default'):
        return ''
    return raw


def _redis_error_message(exc: BaseException, auth_mode: str) -> str:
    text = redact_error(str(exc))
    low = text.lower()
    authish = (
        'invalid username-password' in low
        or 'user is disabled' in low
        or 'wrongpass' in low
        or 'noauth' in low
        or 'authentication required' in low
        or 'auth' in low and 'fail' in low
    )
    if authish:
        if auth_mode == REDIS_AUTH_ACL:
            return (
                'Redis 认证失败。\n'
                '当前认证模式：ACL 用户名 + 密码\n'
                '请确认用户名是否存在、是否启用，以及密码是否正确。\n'
                f'原始错误：{text}'
            )
        if auth_mode == REDIS_AUTH_PASSWORD:
            return (
                'Redis 认证失败。\n'
                '当前认证模式：仅密码\n'
                '该集群可能启用了 ACL 用户认证，请尝试选择“ACL 用户名 + 密码”。\n'
                f'原始错误：{text}'
            )
        return (
            'Redis 认证失败。\n'
            '当前认证模式：无认证\n'
            '服务器要求认证，请选择“仅密码”或“ACL 用户名 + 密码”。\n'
            f'原始错误：{text}'
        )
    return f'Redis 连接失败：{text}'


def _mongo_error_message(exc: BaseException) -> str:
    text = redact_error(str(exc))
    code = getattr(exc, 'code', None)
    name = type(exc).__name__
    low = text.lower()
    if code == 18 or 'authentication failed' in low or 'authenticationfailed' in low:
        return (
            'MongoDB 认证失败（AuthenticationFailed / code 18）。\n'
            '请核对用户名、密码、认证库（authSource）和认证机制。\n'
            f'原始错误：{text}'
        )
    if 'ServerSelectionTimeout' in name or 'timed out' in low and 'server' in low:
        return f'MongoDB 服务器选择超时，请检查主机、端口和网络。\n原始错误：{text}'
    if 'InvalidURI' in name or 'invalid uri' in low:
        return f'MongoDB 连接串无效。\n原始错误：{text}'
    if 'network' in low or 'connection refused' in low or '10061' in text:
        return f'MongoDB 网络连接失败。\n原始错误：{text}'
    return f'MongoDB 连接失败：{text}'


def _encrypt(plain: str) -> str:
    if not str(plain or ''):
        return ''
    from tools.secure_store import encrypt_secret
    return encrypt_secret(plain)


def _decrypt(token: str) -> str:
    if not str(token or ''):
        return ''
    from tools.secure_store import decrypt_secret
    return decrypt_secret(token)


def load_connections() -> list[dict]:
    ensure_config_dir()
    try:
        with open(HARNESS_CONNECTIONS_FILE, 'r', encoding='utf-8') as stream:
            raw = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    items = raw.get('items') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            result.append(dict(item))
    return result


def save_connections(items: list[dict]) -> list[dict]:
    ensure_config_dir()
    os.makedirs(os.path.dirname(HARNESS_CONNECTIONS_FILE), exist_ok=True)
    payload = {'items': list(items)}
    with open(HARNESS_CONNECTIONS_FILE, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
    return items


def upsert_connection(item: dict, plain_password: str | None = None) -> dict:
    rows = load_connections()
    data = dict(item)
    data['id'] = str(data.get('id') or uuid.uuid4().hex)
    data['name'] = str(data.get('name') or '未命名连接').strip() or '未命名连接'
    data['dialect'] = str(data.get('dialect') or 'oracle').strip().lower()
    data['host'] = str(data.get('host') or '').strip()
    try:
        data['port'] = int(data.get('port') or DEFAULT_PORTS.get(data['dialect'], 1521))
    except (TypeError, ValueError):
        data['port'] = DEFAULT_PORTS.get(data['dialect'], 1521)
    data['database'] = str(data.get('database') or '').strip()
    data['username'] = str(data.get('username') or '').strip()
    if plain_password is not None:
        data['password'] = _encrypt(plain_password)
    elif 'password' not in data:
        data['password'] = ''
    found = False
    for index, row in enumerate(rows):
        if row.get('id') == data['id']:
            if not data.get('password') and row.get('password'):
                data['password'] = row.get('password')
            rows[index] = data
            found = True
            break
    if not found:
        rows.append(data)
    save_connections(rows)
    return data


def delete_connection(conn_id: str) -> None:
    save_connections([row for row in load_connections() if row.get('id') != conn_id])


def open_connection(item: dict, plain_password: str | None = None):
    dialect = str(item.get('dialect') or 'oracle').lower()
    password = str(plain_password) if plain_password is not None else _decrypt(item.get('password') or '')
    host = str(item.get('host') or '').strip()
    try:
        port = int(item.get('port') or DEFAULT_PORTS.get(dialect, 1521))
    except (TypeError, ValueError):
        port = DEFAULT_PORTS.get(dialect, 1521)
    database = str(item.get('database') or '').strip()
    username = str(item.get('username') or '').strip()
    if dialect == 'oceanbase':
        ob_mode = normalize_oceanbase_mode(item.get('mode'))
        is_oracle_mode = (ob_mode == 'oracle')
        is_mysql_mode = (ob_mode == 'mysql')
    else:
        is_oracle_mode = (dialect == 'oracle')
        is_mysql_mode = (dialect == 'mysql')

    if is_oracle_mode:
        try:
            import oracledb
        except ImportError as exc:
            raise DbError('未安装 oracledb，请安装依赖后重试') from exc
        oracle = load_oracle_paths()
        try:
            ensure_oracle_client(
                oracle['mode'],
                lib_dir=oracle['lib_dir'],
                home=oracle['home'],
                oci_lib=oracle['oci_lib'],
            )
        except OracleRuntimeError as exc:
            raise DbError(str(exc)) from exc
        dsn = database if '/' in database or ':' in database else f'{host}:{port}/{database}'
        try:
            return oracledb.connect(user=username, password=password, dsn=dsn)
        except Exception as exc:
            text = redact_error(str(exc))
            if 'DPY-3010' in text:
                raise DbError(thick_required_message(text)) from exc
            if 'DPY-3016' in text or 'pbkdf2' in text:
                raise DbError(
                    'Oracle 瘦模式缺少 cryptography（pbkdf2）。请换用最新离线安装包；'
                    '或本机已装 Instant Client 时，到设置的 Oracle 兼容中指定主目录和 oci.dll 后重启。'
                    f' 原始错误：{text}'
                ) from exc
            label = 'OceanBase (Oracle 模式)' if dialect == 'oceanbase' else 'Oracle'
            raise DbError(f'{label} 连接失败：{text}') from exc
    if is_mysql_mode:
        try:
            import pymysql
        except ImportError as exc:
            raise DbError('未安装 pymysql，请安装依赖后重试') from exc
        try:
            return pymysql.connect(
                host=host, port=port, user=username, password=password,
                database=database or None, charset='utf8mb4',
                cursorclass=pymysql.cursors.Cursor,
            )
        except Exception as exc:
            label = 'OceanBase (MySQL 模式)' if dialect == 'oceanbase' else 'MySQL'
            raise DbError(f'{label} 连接失败：{exc}') from exc
    if dialect == 'dameng':
        try:
            import dmPython
        except ImportError as exc:
            raise DbError('未安装达梦驱动 dmPython。可在本机安装达梦 Python 驱动后再连。') from exc
        try:
            return dmPython.connect(user=username, password=password, server=f'{host}:{port}', schema=database or None)
        except Exception as exc:
            raise DbError(f'达梦连接失败：{exc}') from exc
    if dialect == 'redis':
        try:
            import redis
        except ImportError as exc:
            raise DbError('未安装 redis，请安装依赖后重试') from exc
        db_index = 0
        try:
            db_index = int(database or 0)
        except ValueError:
            db_index = 0
        mode = str(item.get('mode') or 'standalone').strip().lower()
        auth_mode = normalize_redis_auth_mode(item)
        auth = redis_auth_kwargs(item, password)
        try:
            seeds = normalize_redis_seed_nodes(item)
            if mode == 'cluster':
                try:
                    from redis.cluster import ClusterNode, RedisCluster as ClusterClient
                    nodes = [ClusterNode(seed['host'], seed['port']) for seed in seeds]
                    client = ClusterClient(
                        startup_nodes=nodes,
                        decode_responses=False,
                        socket_connect_timeout=8,
                        **auth,
                    )
                except ImportError:
                    first = seeds[0]
                    client = redis.RedisCluster(
                        host=first['host'],
                        port=first['port'],
                        decode_responses=False,
                        socket_connect_timeout=8,
                        **auth,
                    )
            else:
                first = seeds[0]
                client = redis.Redis(
                    host=first['host'],
                    port=first['port'],
                    db=db_index,
                    decode_responses=False,
                    socket_connect_timeout=8,
                    **auth,
                )
            client.ping()
            return client
        except DbError:
            raise
        except Exception as exc:
            raise DbError(_redis_error_message(exc, auth_mode)) from exc
    if dialect == 'mongodb':
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise DbError('未安装 pymongo，请安装依赖后重试') from exc
        raw_host = str(host or '').strip()
        mongo_mode = str(item.get('mode') or 'standalone').strip().lower()
        try:
            if is_mongo_uri(raw_host):
                client = MongoClient(raw_host, serverSelectionTimeoutMS=8000)
                client.admin.command('ping')
                return client[database or 'admin']
            if not database:
                raise DbError('MongoDB 请填写库名')
            if mongo_mode == 'cluster' and (',' in raw_host or 'replicaSet' in raw_host or '?' in raw_host):
                conn_uri = raw_host
                if not conn_uri.startswith('mongodb'):
                    auth = ''
                    if username:
                        from urllib.parse import quote_plus
                        auth = f'{quote_plus(username)}:{quote_plus(password or "")}@'
                    conn_uri = f'mongodb://{auth}{conn_uri}'
                client = MongoClient(conn_uri, serverSelectionTimeoutMS=8000)
            else:
                kwargs: dict[str, Any] = {
                    'host': raw_host or '127.0.0.1',
                    'port': port,
                    'serverSelectionTimeoutMS': 8000,
                }
                if username:
                    kwargs['username'] = username
                    kwargs['password'] = password
                    kwargs['authSource'] = mongo_auth_source(item)
                    mechanism = mongo_auth_mechanism(item)
                    if mechanism:
                        kwargs['authMechanism'] = mechanism
                client = MongoClient(**kwargs)
            client.admin.command('ping')
            return client[database]
        except DbError:
            raise
        except Exception as exc:
            raise DbError(_mongo_error_message(exc)) from exc
    raise DbError(f'不支持的数据库类型：{dialect}')


def probe_connection(item: dict, plain_password: str | None = None) -> dict:
    """测试当前输入能否连通；不写入配置。"""
    dialect = str(item.get('dialect') or '').lower()
    conn = open_connection(item, plain_password=plain_password)
    try:
        if dialect == 'redis':
            from tools.db_redis_ops import redis_server_info
            info = redis_server_info(conn)
            mode = str(item.get('mode') or 'standalone')
            node = ''
            nodes = info.get('nodes') or []
            if nodes:
                first = nodes[0]
                node = f"{first.get('host', '')}:{first.get('port', '')}"
            else:
                seeds = normalize_redis_seed_nodes(item)
                node = f"{seeds[0]['host']}:{seeds[0]['port']}"
            summary = (
                f"模式：{'Cluster' if mode == 'cluster' else 'Standalone'}\n"
                f"Redis 版本：{info.get('redis_version') or info.get('version') or '未知'}\n"
                f"成功节点：{node or '本机连接'}"
            )
            return {'ok': True, 'summary': summary, 'info': info}
        if dialect == 'mongodb':
            name = str(getattr(conn, 'name', '') or item.get('database') or '')
            return {'ok': True, 'summary': f'MongoDB 已连通，目标库：{name or "admin"}'}
        return {'ok': True, 'summary': '连接成功'}
    finally:
        close_connection(conn)


def close_connection(conn) -> None:
    if conn is None:
        return
    client = getattr(conn, 'client', None)
    if client is not None and client is not conn:
        try:
            client.close()
            return
        except Exception:
            pass
    try:
        conn.close()
    except Exception:
        pass


def _cursor(conn):
    return conn.cursor()


def list_tables(conn, dialect: str) -> list[str]:
    dialect = (dialect or 'oracle').lower()
    if dialect == 'redis':
        return _redis_list_keys(conn, limit=80)
    if dialect == 'mongodb':
        try:
            return sorted(str(name) for name in conn.list_collection_names())
        except Exception as exc:
            raise DbError(f'读取集合失败：{exc}') from exc
    cur = _cursor(conn)
    try:
        if dialect == 'mysql':
            try:
                cur.execute('SHOW TABLES')
                rows = cur.fetchall() or []
                return [str(row[0]) for row in rows if row]
            except Exception:
                cur.execute(
                    "SELECT CONCAT(TABLE_SCHEMA, '.', TABLE_NAME) FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys') "
                    "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                )
                rows = cur.fetchall() or []
                return [str(row[0]) for row in rows if row]
        elif dialect == 'dameng':
            cur.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
        else:
            # oracle / oceanbase
            cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        rows = cur.fetchall() or []
        return [str(row[0]) for row in rows if row]
    except Exception as exc:
        raise DbError(f'读取表清单失败：{exc}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def list_columns(conn, dialect: str, table: str) -> list[str]:
    dialect = (dialect or 'oracle').lower()
    table = str(table or '').strip()
    if dialect == 'redis':
        try:
            return [str(conn.type(table) or 'none')]
        except Exception:
            return []
    if dialect == 'mongodb':
        try:
            doc = conn[table].find_one() or {}
            return [str(key) for key in list(doc.keys())[:20]]
        except Exception:
            return []
    cur = _cursor(conn)
    try:
        if dialect == 'mysql':
            if '.' in table:
                schema_name, tbl_name = table.split('.', 1)
                schema_clean = schema_name.replace('`', '').strip()
                tbl_clean = tbl_name.replace('`', '').strip()
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (schema_clean, tbl_clean),
                )
            else:
                cur.execute(f'SHOW COLUMNS FROM `{table.replace("`", "")}`')
        elif '.' in table and dialect not in ('redis', 'mongodb'):
            owner_name, tbl_name = table.split('.', 1)
            cur.execute(
                "SELECT column_name FROM all_tab_columns WHERE owner = :1 AND table_name = :2 ORDER BY column_id",
                [owner_name.upper(), tbl_name.upper()],
            )
        else:
            # oracle / oceanbase / dameng
            cur.execute(
                "SELECT column_name FROM user_tab_columns WHERE table_name = :1 ORDER BY column_id",
                [table.upper()],
            )
        rows = cur.fetchall() or []
        return [str(row[0]) for row in rows if row]
    except Exception as exc:
        raise DbError(f'读取字段失败：{exc}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def schema_summary(conn, dialect: str, table_limit: int = 40, col_limit: int = 12) -> str:
    tables = list_tables(conn, dialect)[:table_limit]
    lines = [f'方言：{dialect}', f'表数量（截取前 {len(tables)}）：']
    for table in tables:
        try:
            cols = list_columns(conn, dialect, table)[:col_limit]
        except Exception:
            cols = []
        lines.append(f'- {table}({", ".join(cols)})' if cols else f'- {table}')
    return '\n'.join(lines)


def _wrap_paged(sql: str, dialect: str, offset: int, limit: int) -> str:
    body = strip_sql_comments(sql)
    if body.endswith(';'):
        body = body[:-1].strip()
    verb = leading_verb(body)
    if verb in ('show', 'desc', 'describe', 'explain', 'pragma'):
        return body
    dialect = (dialect or 'oracle').lower()
    if dialect == 'mysql':
        return f'SELECT * FROM ({body}) peng_q LIMIT {int(limit)} OFFSET {int(offset)}'
    # oracle / oceanbase / dameng → ROWNUM
    end = int(offset) + int(limit)
    return (
        'SELECT * FROM ('
        f'SELECT peng_q.*, ROWNUM AS peng_rn FROM ({body}) peng_q WHERE ROWNUM <= {end}'
        f') WHERE peng_rn > {int(offset)}'
    )


def _b(value: Any) -> str:
    """Redis 字节安全解码：二进制 key/value 用 errors='replace' 转文本，不抛 UnicodeDecodeError。"""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _redis_list_keys(conn, limit: int = 80) -> list[str]:
    keys = []
    try:
        for key in conn.scan_iter(match='*', count=min(int(limit), 200)):
            keys.append(_b(key))
            if len(keys) >= int(limit):
                break
    except Exception as exc:
        raise DbError(f'Redis SCAN 失败：{exc}') from exc
    return keys


def _parse_redis_command(text: str) -> list[str]:
    import shlex
    raw = strip_sql_comments(text)
    if raw.endswith(';'):
        raw = raw[:-1].strip()
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    return [str(item) for item in parts if str(item)]


def _run_redis(conn, sql: str, offset: int, limit: int) -> dict:
    parts = _parse_redis_command(sql)
    if not parts:
        raise DbError('Redis 命令为空')
    cmd = parts[0].lower()
    args = parts[1:]
    rows = []
    columns = ['field', 'value']
    cursor_out = 0
    has_more = False
    if cmd == 'scan':
        cursor = offset if isinstance(offset, (int, dict)) else (0 if not offset else offset)
        match = '*'
        count = int(limit)
        if 'match' in [a.lower() for a in args]:
            for i, token in enumerate(args):
                if str(token).lower() == 'match' and i + 1 < len(args):
                    match = args[i + 1]
                if str(token).lower() == 'count' and i + 1 < len(args):
                    try:
                        count = int(args[i + 1])
                    except ValueError:
                        pass
        try:
            raw_res = conn.scan(cursor=cursor, match=match, count=max(count, int(limit)))
            if isinstance(raw_res, tuple) and len(raw_res) == 2:
                cursor_out, keys = raw_res
            else:
                cursor_out, keys = 0, []
        except Exception:
            keys = list(conn.scan_iter(match=match, count=max(count, int(limit))))
            cursor_out = 0
        columns = ['key']
        rows = [[_b(key)] for key in keys]
        if isinstance(cursor_out, dict):
            has_more = any(int(c or 0) != 0 for c in cursor_out.values())
        else:
            has_more = int(cursor_out or 0) != 0
        return {
            'columns': columns, 'rows': rows, 'offset': cursor_out,
            'limit': int(limit), 'has_more': has_more, 'sql': sql,
        }
    if cmd in ('get', 'type', 'ttl', 'pttl', 'strlen', 'exists'):
        key = args[0] if args else ''
        value = conn.execute_command(*parts)
        rows = [[cmd, _stringify(value)]]
    elif cmd == 'mget':
        values = conn.mget(args) if args else []
        rows = [[key, _stringify(val)] for key, val in zip(args, values)]
    elif cmd == 'hgetall':
        mapping = conn.hgetall(args[0]) if args else {}
        rows = [[_b(k), _stringify(v)] for k, v in mapping.items()]
    elif cmd in ('hkeys', 'hvals'):
        values = conn.execute_command(*parts) or []
        rows = [[_b(item)] for item in values]
        columns = ['value']
    elif cmd == 'lrange':
        values = conn.execute_command(*parts) or []
        rows = [[str(i), _stringify(v)] for i, v in enumerate(values)]
        columns = ['index', 'value']
    elif cmd in ('smembers',):
        values = list(conn.smembers(args[0]) if args else [])
        rows = [[_stringify(v)] for v in values]
        columns = ['value']
    else:
        value = conn.execute_command(*parts)
        if isinstance(value, (list, tuple)):
            rows = [[_stringify(item)] for item in value]
            columns = ['value']
        elif isinstance(value, dict):
            rows = [[_b(k), _stringify(v)] for k, v in value.items()]
        else:
            rows = [[cmd, _stringify(value)]]
    sliced = rows[int(offset): int(offset) + int(limit)]
    return {
        'columns': columns,
        'rows': sliced,
        'offset': int(offset) + len(sliced),
        'limit': int(limit),
        'has_more': int(offset) + len(sliced) < len(rows),
        'sql': sql,
    }


def _parse_mongo_query(text: str) -> tuple[str, dict]:
    import json
    import re
    from tools.ai_harness import strip_markdown_fence
    raw = strip_markdown_fence(strip_sql_comments(text))
    match = re.search(r'db\.([A-Za-z0-9_]+)\.find\((.*)\)', raw, re.DOTALL)
    if match:
        collection = match.group(1)
        body = (match.group(2) or '').strip()
        filt = json.loads(body) if body else {}
        if not isinstance(filt, dict):
            filt = {}
        return collection, filt
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise DbError('MongoDB 查询必须是 JSON 对象')
    collection = str(data.get('collection') or data.get('coll') or '').strip()
    if not collection:
        raise DbError('MongoDB JSON 需要 collection')
    filt = data.get('filter') if isinstance(data.get('filter'), dict) else {}
    return collection, filt


def _run_mongo(conn, sql: str, offset: int, limit: int) -> dict:
    collection, filt = _parse_mongo_query(sql)
    cursor = conn[collection].find(filt).skip(int(offset)).limit(int(limit) + 1)
    docs = list(cursor)
    has_more = len(docs) > int(limit)
    docs = docs[: int(limit)]
    keys = []
    for doc in docs:
        for key in doc.keys():
            if key not in keys:
                keys.append(str(key))
    columns = keys or ['_id']
    rows = []
    for doc in docs:
        rows.append([_stringify(doc.get(col)) for col in columns])
    return {
        'columns': columns,
        'rows': rows,
        'offset': int(offset) + len(rows),
        'limit': int(limit),
        'has_more': has_more,
        'sql': sql,
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        text = value.decode('utf-8', errors='replace')
    else:
        text = str(value)
    if len(text) > CELL_MAX:
        return text[:CELL_MAX] + '…'
    return text


def estimate_cell_bytes(value) -> int:
    """结果单元格近似体积；与 _stringify 实际保留文本一致。"""
    from datetime import date, datetime
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode('utf-8', errors='replace')
    elif isinstance(value, bool):
        text = str(value)
    elif isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, datetime):
        text = value.isoformat(sep=' ', timespec='seconds')
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    if len(text) > CELL_MAX:
        text = text[:CELL_MAX] + '…'
    return len(text.encode('utf-8', errors='replace'))


def estimate_row_bytes(row) -> int:
    if not row:
        return 0
    return sum(estimate_cell_bytes(cell) for cell in row)


def run_read_query(conn, dialect: str, sql: str, *, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    kind = str(dialect or 'oracle').lower()
    reason = reject_reason(sql, kind)
    if reason:
        raise DbError(reason)
    if kind == 'redis':
        return _run_redis(conn, sql, int(offset), min(int(limit), MAX_ROWS))
    if kind == 'mongodb':
        return _run_mongo(conn, sql, int(offset), min(int(limit), MAX_ROWS))
    if not is_read_query(sql):
        raise DbError('仅允许查询语句')
    page_limit = max(1, int(limit))
    wrapped = _wrap_paged(sql, dialect, offset, page_limit)
    cur = _cursor(conn)
    try:
        cur.execute(wrapped)
        columns = [str(item[0]) for item in (cur.description or [])]
        rows = cur.fetchall() or []
        drop = [i for i, name in enumerate(columns) if str(name).upper() == 'PENG_RN']
        if drop:
            columns = [name for i, name in enumerate(columns) if i not in drop]
            trimmed = []
            for row in rows:
                trimmed.append(tuple(cell for i, cell in enumerate(row) if i not in drop))
            rows = trimmed
        data = [[_stringify(cell) for cell in row] for row in rows]
        has_more = len(data) >= page_limit
        return {
            'columns': columns,
            'rows': data,
            'offset': int(offset) + len(data),
            'limit': page_limit,
            'has_more': has_more,
            'sql': sql,
        }
    except Exception as exc:
        raise DbError(f'查询失败：{redact_error(str(exc))}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_query_chunks(
    conn,
    dialect: str,
    sql: str,
    *,
    offset: int = 0,
    chunk_size: int = FETCH_CHUNK,
    byte_limit: int = RESULT_BYTE_LIMIT,
    cancel=None,
    progress=None,
) -> dict:
    """分块读取查询结果，直到 EOF、取消或字节上限。"""
    import time
    started = time.perf_counter()
    columns: list[str] = []
    rows: list[list] = []
    total_bytes = 0
    cur_offset = int(offset)
    status = 'DONE'
    chunk = max(1, int(chunk_size or FETCH_CHUNK))
    limit = int(byte_limit if byte_limit is not None else RESULT_BYTE_LIMIT)
    try:
        while True:
            if cancel and cancel():
                status = 'CANCELLED'
                break
            page = run_read_query(conn, dialect, sql, offset=cur_offset, limit=chunk)
            if not columns:
                columns = list(page.get('columns') or [])
            batch = list(page.get('rows') or [])
            if not batch:
                status = 'DONE'
                break
            stopped = False
            for row in batch:
                rows.append(row)
                total_bytes += estimate_row_bytes(row)
                if total_bytes >= limit:
                    status = 'LIMIT_REACHED'
                    stopped = True
                    break
            cur_offset = int(page.get('offset') or (cur_offset + len(batch)))
            if progress:
                progress({
                    'rows': int(offset) + len(rows),
                    'bytes': total_bytes,
                    'status': status,
                })
            if stopped:
                break
            if not page.get('has_more'):
                status = 'DONE'
                break
    except Exception as exc:
        status = 'ERROR'
        err = redact_error(str(exc))
        if progress:
            progress({
                'rows': int(offset) + len(rows),
                'bytes': total_bytes,
                'status': status,
            })
        return {
            'columns': columns,
            'rows': rows,
            'offset': cur_offset,
            'has_more': True,
            'sql': sql,
            'status': status,
            'bytes': total_bytes,
            'error': err,
            'elapsed_ms': int((time.perf_counter() - started) * 1000),
            'rowcount': len(rows),
        }
    return {
        'columns': columns,
        'rows': rows,
        'offset': cur_offset,
        'has_more': bool(status == 'LIMIT_REACHED'),
        'sql': sql,
        'status': status,
        'bytes': total_bytes,
        'elapsed_ms': int((time.perf_counter() - started) * 1000),
        'rowcount': len(rows),
    }


def run_console_statement(conn, dialect: str, sql: str, *, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    """手工控制台执行：读语句分页，写语句确认后由调用方决定才进入这里。"""
    import time
    kind = str(dialect or 'oracle').lower()
    info = classify_statement(sql, kind)
    started = time.perf_counter()
    if info.get('empty'):
        raise DbError('语句为空')
    if info.get('is_read'):
        result = run_read_query(conn, dialect, sql, offset=offset, limit=limit)
        result['elapsed_ms'] = int((time.perf_counter() - started) * 1000)
        result['category'] = info.get('category')
        result['rowcount'] = len(result.get('rows') or [])
        result['tx'] = ''
        return result
    if kind == 'redis':
        result = _run_redis(conn, sql, int(offset), min(int(limit), MAX_ROWS))
        result['elapsed_ms'] = int((time.perf_counter() - started) * 1000)
        result['category'] = info.get('category')
        result['rowcount'] = len(result.get('rows') or [])
        result['tx'] = ''
        return result
    if kind == 'mongodb':
        result = _run_mongo_write(conn, sql)
        result['elapsed_ms'] = int((time.perf_counter() - started) * 1000)
        result['category'] = info.get('category')
        return result
    cur = _cursor(conn)
    tx = ''
    try:
        body = strip_sql_comments(sql)
        if body.endswith(';'):
            body = body[:-1].strip()
        cur.execute(body)
        rowcount = cur.rowcount if cur.rowcount is not None else 0
        columns = [str(item[0]) for item in (cur.description or [])]
        rows = []
        if columns:
            fetched = cur.fetchall() or []
            rows = [[_stringify(cell) for cell in row] for row in fetched]
        category = str(info.get('category') or '')
        if category == 'dml':
            try:
                conn.commit()
                tx = 'committed'
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise DbError(f'提交失败，已尝试回滚：{redact_error(str(exc))}') from exc
        elif category == 'ddl':
            tx = 'implicit'
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            'columns': columns or ['result'],
            'rows': rows or ([[f'affected {rowcount}']] if not rows else rows),
            'offset': int(offset),
            'limit': int(limit),
            'has_more': False,
            'sql': sql,
            'rowcount': int(rowcount or len(rows)),
            'elapsed_ms': elapsed,
            'category': category,
            'tx': tx,
        }
    except DbError:
        raise
    except Exception as exc:
        if info.get('category') == 'dml':
            try:
                conn.rollback()
            except Exception:
                pass
        raise DbError(f'执行失败：{redact_error(str(exc))}') from exc
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _run_mongo_write(conn, sql: str) -> dict:
    import json
    from tools.ai_harness import strip_markdown_fence
    raw = strip_markdown_fence(strip_sql_comments(sql))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DbError('MongoDB 写操作需要 JSON 对象') from exc
    if not isinstance(data, dict):
        raise DbError('MongoDB 写操作需要 JSON 对象')
    collection = str(data.get('collection') or data.get('coll') or '').strip()
    if not collection:
        raise DbError('MongoDB JSON 需要 collection')
    coll = conn[collection]
    result_text = ''
    count = 0
    if data.get('insert') is not None:
        payload = data.get('insert')
        if isinstance(payload, list):
            info = coll.insert_many(payload)
            count = len(list(info.inserted_ids or []))
        else:
            info = coll.insert_one(payload if isinstance(payload, dict) else {'value': payload})
            count = 1
            result_text = str(getattr(info, 'inserted_id', ''))
    elif data.get('delete') is not None or data.get('delete_many') is not None:
        filt = data.get('delete') or data.get('delete_many') or {}
        if not isinstance(filt, dict):
            filt = {}
        info = coll.delete_many(filt)
        count = int(getattr(info, 'deleted_count', 0) or 0)
    elif data.get('update') is not None:
        filt = data.get('filter') if isinstance(data.get('filter'), dict) else {}
        update = data.get('update')
        if not isinstance(update, dict):
            raise DbError('Mongo update 需要文档')
        info = coll.update_many(filt, update)
        count = int(getattr(info, 'modified_count', 0) or 0)
    else:
        raise DbError('无法识别的 Mongo 写操作，请使用 insert/update/delete 字段')
    return {
        'columns': ['result', 'count'],
        'rows': [[result_text or 'ok', str(count)]],
        'offset': 0,
        'limit': PAGE_SIZE,
        'has_more': False,
        'sql': sql,
        'rowcount': count,
        'tx': '',
    }
