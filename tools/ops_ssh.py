# -*- coding: utf-8 -*-
"""运维 SSH：连接配置存取 + 流式日志截取（不在远端落临时文件）+ 多机并行。"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import stat as stat_mod
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

from config import OPS_LOG_SETTINGS_FILE, OPS_SERVERS_FILE, ensure_config_dir
from tools.secure_store import (
    decrypt_secret,
    encrypt_secret,
    reencrypt_if_weak,
)

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None


DEFAULT_LOG_SETTINGS = {
    'export_dir': '',
    'context_lines': 20,
    'max_workers': 4,
    'timeout_sec': 30,
    'case_insensitive': True,
    'tail_lines': 100,
    'show_remote_browser': True,
}

# 服务器分类（用户自定义：集成 / 模拟 / 生产 …）
UNCATEGORIZED_ID = 'uncategorized'
DEFAULT_CATEGORY_NAME = '未分类'
SERVER_STORE_VERSION = 2


class OpsSshError(Exception):
    """SSH / 日志导出业务错误。"""


def paramiko_available() -> bool:
    return paramiko is not None


def _read_json(path: str, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
        return data if data is not None else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: str, data) -> None:
    ensure_config_dir()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _default_categories() -> list[dict]:
    return [
        {'id': UNCATEGORIZED_ID, 'name': DEFAULT_CATEGORY_NAME, 'sort': 0},
    ]


def _normalize_category(item: dict, sort_fallback: int = 0) -> dict | None:
    if not isinstance(item, dict):
        return None
    cid = str(item.get('id') or '').strip() or uuid.uuid4().hex[:10]
    name = str(item.get('name') or '').strip() or DEFAULT_CATEGORY_NAME
    try:
        sort = int(item.get('sort', sort_fallback))
    except (TypeError, ValueError):
        sort = sort_fallback
    return {'id': cid, 'name': name, 'sort': sort}



def _normalize_service(item: dict | None, *, index: int = 0) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get('name') or item.get('service') or '').strip()
    log_path = str(item.get('log_path') or item.get('path') or '').strip()
    if not name and not log_path:
        return None
    if not name:
        name = f'服务{index + 1}'
    sid = str(item.get('id') or '').strip() or uuid.uuid4().hex[:10]
    return {
        'id': sid,
        'name': name,
        'log_path': log_path,
        'enabled': bool(item.get('enabled', True)),
    }


def normalize_services(raw, *, default_log_path: str = '') -> list[dict]:
    services: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            svc = _normalize_service(item, index=index)
            if not svc or svc['id'] in seen:
                continue
            seen.add(svc['id'])
            services.append(svc)
    default_log_path = str(default_log_path or '').strip()
    if not services and default_log_path:
        services.append({
            'id': uuid.uuid4().hex[:10],
            'name': '默认服务',
            'log_path': default_log_path,
            'enabled': True,
        })
    return services


def server_services(server: dict | None, *, only_enabled: bool = False) -> list[dict]:
    server = server or {}
    services = normalize_services(
        server.get('services'),
        default_log_path=str(server.get('default_log_path') or ''),
    )
    if only_enabled:
        services = [
            s for s in services
            if s.get('enabled', True) and str(s.get('log_path') or '').strip()
        ]
    return services


def primary_log_path(server: dict | None) -> str:
    server = server or {}
    path = str(server.get('default_log_path') or '').strip()
    if path:
        return path
    for svc in server_services(server, only_enabled=True):
        p = str(svc.get('log_path') or '').strip()
        if p:
            return p
    for svc in server_services(server, only_enabled=False):
        p = str(svc.get('log_path') or '').strip()
        if p:
            return p
    return ''


def make_job_key(server_id: str, service_id: str) -> str:
    return f'{server_id}::{service_id}'


def build_export_jobs(
    servers: list[dict],
    *,
    selected_keys: set[str] | None = None,
    override_path: str = '',
) -> list[dict]:
    jobs: list[dict] = []
    override_path = str(override_path or '').strip()
    for server in servers or []:
        if not isinstance(server, dict):
            continue
        sid = str(server.get('id') or '')
        if override_path:
            if selected_keys is not None:
                hit = sid in selected_keys or any(str(k).startswith(sid + '::') for k in selected_keys)
                if not hit:
                    continue
            key = make_job_key(sid, '__override__')
            jobs.append({
                'server': server,
                'server_id': sid,
                'service_id': '__override__',
                'service_name': '统一路径',
                'log_path': override_path,
                'job_key': key,
            })
            continue
        for svc in server_services(server, only_enabled=False):
            if not svc.get('enabled', True):
                continue
            path = str(svc.get('log_path') or '').strip()
            if not path:
                continue
            svid = str(svc.get('id') or '')
            key = make_job_key(sid, svid)
            if selected_keys is not None and key not in selected_keys:
                continue
            jobs.append({
                'server': server,
                'server_id': sid,
                'service_id': svid,
                'service_name': str(svc.get('name') or '服务'),
                'log_path': path,
                'job_key': key,
            })
    return jobs


def _normalize_server(item: dict, category_ids: set[str] | None = None) -> dict:
    port = item.get('port', 22)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 22
    port = max(1, min(65535, port))
    sid = str(item.get('id') or '').strip() or uuid.uuid4().hex[:12]
    password_token = str(item.get('password_token') or '')
    # 若调用方直接给 password，加密后存（DPAPI / Fernet，禁止新 b64）
    if item.get('password') is not None and str(item.get('password')) != '':
        password_token = encrypt_secret(str(item.get('password')))
    else:
        # 历史 b64/明文在读取规范化时升级为强加密
        upgraded = reencrypt_if_weak(password_token)
        if upgraded:
            password_token = upgraded
    # 分类：优先 category_id，兼容旧字段 group（自由文本分组名）
    category_id = str(item.get('category_id') or '').strip()
    group = str(item.get('group') or '').strip()
    if not category_id:
        category_id = UNCATEGORIZED_ID
    if category_ids is not None and category_id not in category_ids:
        category_id = UNCATEGORIZED_ID
    default_log_path = str(item.get('default_log_path') or '').strip()
    services = normalize_services(item.get('services'), default_log_path=default_log_path)
    if services:
        for svc in services:
            if svc.get('enabled', True) and str(svc.get('log_path') or '').strip():
                default_log_path = str(svc['log_path']).strip()
                break
        else:
            default_log_path = str(services[0].get('log_path') or default_log_path).strip()
    return {
        'id': sid,
        'name': str(item.get('name') or item.get('host') or '未命名').strip() or '未命名',
        'host': str(item.get('host') or '').strip(),
        'port': port,
        'username': str(item.get('username') or '').strip(),
        'password_token': password_token,
        'default_log_path': default_log_path,
        'services': services,
        'category_id': category_id,
        # group 保留与分类名同步，兼容旧 UI/导出
        'group': group,
        'enabled': bool(item.get('enabled', True)),
    }


def normalize_server_store(data=None) -> dict:
    """服务器 + 用户自定义分类。兼容 v1 仅 servers 列表、以及 group 文本。"""
    if not isinstance(data, dict):
        data = {}
    raw_servers = data.get('servers') if isinstance(data.get('servers'), list) else []
    raw_cats = data.get('categories') if isinstance(data.get('categories'), list) else []

    categories: list[dict] = []
    seen = set()
    for index, raw in enumerate(raw_cats):
        cat = _normalize_category(raw, sort_fallback=index)
        if not cat or cat['id'] in seen:
            continue
        seen.add(cat['id'])
        categories.append(cat)
    if UNCATEGORIZED_ID not in seen:
        categories.insert(0, {'id': UNCATEGORIZED_ID, 'name': DEFAULT_CATEGORY_NAME, 'sort': 0})
        seen.add(UNCATEGORIZED_ID)

    # 从旧 group 字段迁移为分类
    name_to_id = {c['name']: c['id'] for c in categories}
    for raw in raw_servers:
        if not isinstance(raw, dict):
            continue
        if str(raw.get('category_id') or '').strip():
            continue
        group = str(raw.get('group') or '').strip()
        if not group:
            continue
        if group in name_to_id:
            raw['category_id'] = name_to_id[group]
            continue
        cid = uuid.uuid4().hex[:10]
        categories.append({'id': cid, 'name': group, 'sort': len(categories)})
        name_to_id[group] = cid
        seen.add(cid)
        raw['category_id'] = cid

    cat_ids = {c['id'] for c in categories}
    servers = []
    for raw in raw_servers:
        if not isinstance(raw, dict):
            continue
        server = _normalize_server(raw, category_ids=cat_ids)
        # 同步 group 显示名为分类名
        cat = next((c for c in categories if c['id'] == server['category_id']), None)
        if cat and cat['id'] != UNCATEGORIZED_ID:
            server['group'] = cat['name']
        elif not server.get('group'):
            server['group'] = ''
        servers.append(server)

    categories.sort(key=lambda c: (int(c.get('sort') or 0), c.get('name') or ''))
    return {
        'version': SERVER_STORE_VERSION,
        'categories': categories,
        'servers': servers,
    }


def load_server_store() -> dict:
    data = _read_json(OPS_SERVERS_FILE, {'version': 1, 'servers': [], 'categories': []})
    return normalize_server_store(data)


def load_servers() -> list[dict]:
    return list(load_server_store().get('servers') or [])


def load_categories() -> list[dict]:
    return list(load_server_store().get('categories') or [])


def save_server_store(servers: list[dict] | None = None, categories: list[dict] | None = None) -> dict:
    """保存服务器与分类。未传的一侧从磁盘读取合并，避免互相覆盖。"""
    current = load_server_store()
    if servers is None:
        servers = current.get('servers') or []
    if categories is None:
        categories = current.get('categories') or []
    # 先以传入 categories 建 id 集，再规范化 servers
    cat_ids = set()
    norm_cats = []
    for index, raw in enumerate(categories or []):
        cat = _normalize_category(raw if isinstance(raw, dict) else {}, sort_fallback=index)
        if not cat or cat['id'] in cat_ids:
            continue
        cat_ids.add(cat['id'])
        norm_cats.append(cat)
    if UNCATEGORIZED_ID not in cat_ids:
        norm_cats.insert(0, {'id': UNCATEGORIZED_ID, 'name': DEFAULT_CATEGORY_NAME, 'sort': 0})
        cat_ids.add(UNCATEGORIZED_ID)
    # 名称 → id 映射，便于编辑器按名称新建
    name_to_id = {c['name']: c['id'] for c in norm_cats}
    norm_servers = []
    for raw in servers or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        # 允许只传 group 名：自动落入/创建分类
        cid = str(item.get('category_id') or '').strip()
        gname = str(item.get('group') or '').strip()
        if not cid and gname:
            if gname in name_to_id:
                cid = name_to_id[gname]
            else:
                cid = uuid.uuid4().hex[:10]
                cat = {'id': cid, 'name': gname, 'sort': len(norm_cats)}
                norm_cats.append(cat)
                name_to_id[gname] = cid
                cat_ids.add(cid)
            item['category_id'] = cid
        server = _normalize_server(item, category_ids=cat_ids)
        cat = next((c for c in norm_cats if c['id'] == server['category_id']), None)
        if cat and cat['id'] != UNCATEGORIZED_ID:
            server['group'] = cat['name']
        norm_servers.append(server)
    norm_cats.sort(key=lambda c: (int(c.get('sort') or 0), c.get('name') or ''))
    payload = {
        'version': SERVER_STORE_VERSION,
        'categories': norm_cats,
        'servers': norm_servers,
    }
    _write_json(OPS_SERVERS_FILE, payload)
    return payload


def save_servers(servers: list[dict]) -> None:
    """兼容旧调用：只更新服务器列表，保留已有分类。"""
    save_server_store(servers=servers, categories=None)


def save_categories(categories: list[dict]) -> None:
    save_server_store(servers=None, categories=categories)


def category_name_map(categories: list[dict] | None = None) -> dict[str, str]:
    cats = categories if categories is not None else load_categories()
    return {c.get('id'): c.get('name') or DEFAULT_CATEGORY_NAME for c in (cats or [])}


def ensure_category(categories: list[dict], name: str) -> tuple[list[dict], str]:
    """按名称确保分类存在，返回 (categories, category_id)。"""
    name = (name or '').strip()
    if not name or name == DEFAULT_CATEGORY_NAME:
        return list(categories or _default_categories()), UNCATEGORIZED_ID
    cats = list(categories or _default_categories())
    for c in cats:
        if c.get('name') == name:
            return cats, c.get('id') or UNCATEGORIZED_ID
    cid = uuid.uuid4().hex[:10]
    cats.append({'id': cid, 'name': name, 'sort': len(cats)})
    return cats, cid


def delete_category(categories: list[dict], servers: list[dict], category_id: str) -> tuple[list[dict], list[dict]]:
    """删除分类并把其下服务器归入未分类。不可删未分类。"""
    if category_id == UNCATEGORIZED_ID:
        return list(categories or []), list(servers or [])
    cats = [c for c in (categories or []) if c.get('id') != category_id]
    if not any(c.get('id') == UNCATEGORIZED_ID for c in cats):
        cats.insert(0, {'id': UNCATEGORIZED_ID, 'name': DEFAULT_CATEGORY_NAME, 'sort': 0})
    fixed = []
    for s in servers or []:
        item = dict(s)
        if item.get('category_id') == category_id:
            item['category_id'] = UNCATEGORIZED_ID
            item['group'] = ''
        fixed.append(item)
    return cats, fixed


def load_log_settings() -> dict:
    data = _read_json(OPS_LOG_SETTINGS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    result = dict(DEFAULT_LOG_SETTINGS)
    result.update({k: data[k] for k in DEFAULT_LOG_SETTINGS if k in data})
    try:
        result['context_lines'] = max(0, min(200, int(result['context_lines'])))
    except (TypeError, ValueError):
        result['context_lines'] = 20
    try:
        result['max_workers'] = max(1, min(16, int(result['max_workers'])))
    except (TypeError, ValueError):
        result['max_workers'] = 4
    try:
        result['timeout_sec'] = max(5, min(300, int(result['timeout_sec'])))
    except (TypeError, ValueError):
        result['timeout_sec'] = 30
    result['case_insensitive'] = bool(result.get('case_insensitive', True))
    result['export_dir'] = str(result.get('export_dir') or '').strip()
    try:
        result['tail_lines'] = max(20, min(5000, int(result.get('tail_lines') or 100)))
    except (TypeError, ValueError):
        result['tail_lines'] = 100
    result['show_remote_browser'] = bool(result.get('show_remote_browser', True))
    return result


def save_log_settings(settings: dict) -> None:
    current = load_log_settings()
    if isinstance(settings, dict):
        current.update(settings)
    try:
        current['context_lines'] = max(0, min(200, int(current['context_lines'])))
    except (TypeError, ValueError):
        current['context_lines'] = 20
    try:
        current['max_workers'] = max(1, min(16, int(current['max_workers'])))
    except (TypeError, ValueError):
        current['max_workers'] = 4
    try:
        current['timeout_sec'] = max(5, min(300, int(current['timeout_sec'])))
    except (TypeError, ValueError):
        current['timeout_sec'] = 30
    current['case_insensitive'] = bool(current.get('case_insensitive', True))
    current['export_dir'] = str(current.get('export_dir') or '').strip()
    try:
        current['tail_lines'] = max(20, min(5000, int(current.get('tail_lines') or 100)))
    except (TypeError, ValueError):
        current['tail_lines'] = 100
    current['show_remote_browser'] = bool(current.get('show_remote_browser', False))
    payload = {k: current.get(k, DEFAULT_LOG_SETTINGS[k]) for k in DEFAULT_LOG_SETTINGS}
    _write_json(OPS_LOG_SETTINGS_FILE, payload)


def split_extra_keywords(text: str) -> list[str]:
    """关键字列表：支持换行、中英文逗号、分号分隔（兼容旧「也包含」字段）。"""
    raw = str(text or '').strip()
    if not raw:
        return []
    parts = re.split(r'[\n,，;；]+', raw)
    result = []
    seen = set()
    for part in parts:
        kw = part.strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        result.append(kw)
    return result


def parse_keywords(text: str) -> tuple[str, list[str]]:
    """统一关键字字段：逗号/分号/换行分隔；第一个为主关键字，其余为附加 AND 条件。"""
    items = split_extra_keywords(text)
    if not items:
        return '', []
    return items[0], items[1:]


def build_remote_grep_command(
    log_path: str,
    keyword: str,
    extra_keywords: list[str] | None = None,
    *,
    context_lines: int = 20,
    case_insensitive: bool = True,
) -> str:
    """构造远端只读 grep 管道。结果走 stdout，不写远端文件。"""
    path = str(log_path or '').strip()
    primary = str(keyword or '').strip()
    if not path:
        raise OpsSshError('请填写日志路径')
    if not primary:
        raise OpsSshError('请填写关键字')
    if any(ch in path for ch in ('\n', '\r', '\x00')):
        raise OpsSshError('日志路径含非法字符')
    extras = [str(x).strip() for x in (extra_keywords or []) if str(x).strip()]
    for kw in [primary, *extras]:
        if any(ch in kw for ch in ('\n', '\r', '\x00')):
            raise OpsSshError('关键字不能包含换行')
    try:
        ctx = max(0, min(200, int(context_lines)))
    except (TypeError, ValueError):
        ctx = 20
    flag_i = ' -i' if case_insensitive else ''
    # grep -a：按文本读，避免被识别成二进制后截断
    cmd = (
        f'grep -a -n{flag_i} -C {ctx} -- {shlex.quote(primary)} {shlex.quote(path)}'
    )
    for extra in extras:
        cmd += f' | grep -a{flag_i} -- {shlex.quote(extra)}'
    return cmd


def _safe_filename_part(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r'[^\w.\-]+', '_', str(text or ''), flags=re.UNICODE).strip('._')
    if not cleaned:
        cleaned = 'host'
    return cleaned[:limit]


def local_export_filename(
    server: dict,
    keyword: str = '',
    *,
    service_name: str = '',
    log_path: str = '',
) -> str:
    """导出文件名：{ip}-{服务名}.log（关键字作为父目录，不写进文件名）。"""
    host = str(server.get('host') or server.get('name') or 'server').strip()
    # IP/主机名保留点号，去掉其它危险字符
    host = re.sub(r'[^\w.\-]+', '_', host, flags=re.UNICODE).strip('._') or 'host'
    if len(host) > 64:
        host = host[:64]
    svc = _safe_filename_part(service_name or '', 32)
    if not svc and log_path:
        base = os.path.basename(str(log_path).rstrip('/\\')) or 'log'
        # 去掉扩展名作服务名兜底
        if base.lower().endswith('.log'):
            base = base[:-4]
        svc = _safe_filename_part(base, 32)
    if not svc:
        svc = 'log'
    return f'{host}-{svc}.log'


def export_keyword_dir(export_dir: str, keyword: str) -> str:
    """在导出根目录下按主关键字建子目录。"""
    root = str(export_dir or '').strip()
    if not root:
        raise OpsSshError('请配置本地导出目录')
    folder_name = _safe_filename_part(keyword or 'export', 48) or 'export'
    path = os.path.join(root, folder_name)
    os.makedirs(path, exist_ok=True)
    return path


def looks_like_remote_file_path(path: str) -> bool:
    """粗判是否像具体日志文件（而非目录）。"""
    p = str(path or '').rstrip('/\\')
    if not p:
        return False
    name = p.rsplit('/', 1)[-1]
    if not name or name in ('.', '..'):
        return False
    return _looks_like_log_file(name) or ('.' in name and not name.startswith('.'))


def resolve_remote_log_file(client, path: str) -> str:
    """将目录或模糊路径解析为具体日志文件（目录取最新 .log）。"""
    remote = str(path or '').strip()
    if not remote:
        raise OpsSshError('日志路径为空')
    files = list_remote_log_files(client, remote)
    if not files:
        raise OpsSshError(f'路径下没有可导出的日志文件：{remote}')
    # list_remote_log_files 已按 mtime 新→旧
    return str(files[0].get('path') or remote)


def extract_log_from_server(
    server: dict,
    *,
    log_path: str,
    keyword: str,
    extra_keywords: list[str] | None = None,
    context_lines: int = 20,
    case_insensitive: bool = True,
    export_dir: str,
    timeout_sec: int = 30,
    password_override: str | None = None,
    service_name: str = '',
    service_id: str = '',
) -> dict:
    """连接单机，流式截取日志到本地。返回结果字典（可 JSON 化）。"""
    if not paramiko_available():
        raise OpsSshError('未安装 paramiko，请执行: pip install paramiko')

    host = str(server.get('host') or '').strip()
    username = str(server.get('username') or '').strip()
    if not host or not username:
        raise OpsSshError('服务器主机或用户名为空')

    path = str(log_path or primary_log_path(server) or '').strip()
    if not path:
        raise OpsSshError('日志路径为空')

    if password_override is not None and str(password_override) != '':
        password = str(password_override)
    else:
        password = decrypt_secret(server.get('password_token') or '')
    if not password:
        raise OpsSshError('密码为空，请编辑服务器并重新输入密码')

    export_dir = str(export_dir or '').strip()
    if not export_dir:
        raise OpsSshError('请配置本地导出目录')
    # 关键字子目录：export_dir/关键字/ip-服务.log
    try:
        target_dir = export_keyword_dir(export_dir, keyword)
    except OpsSshError:
        raise
    except Exception as exc:
        raise OpsSshError(f'无法创建导出目录：{exc}') from exc

    port = int(server.get('port') or 22)
    timeout = max(5, min(300, int(timeout_sec or 30)))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    started = datetime.now().isoformat(timespec='seconds')
    local_path = ''

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        # 配置里常绑目录不带 .log：连上后解析为最新日志文件
        try:
            path = resolve_remote_log_file(client, path)
        except OpsSshError:
            # 若路径已是明确文件但 list 失败，仍尝试原路径
            if not looks_like_remote_file_path(path):
                raise
        local_path = os.path.join(
            target_dir,
            local_export_filename(
                server, keyword, service_name=service_name, log_path=path,
            ),
        )
        remote_cmd = build_remote_grep_command(
            path,
            keyword,
            extra_keywords,
            context_lines=context_lines,
            case_insensitive=case_insensitive,
        )
        # get_pty=False：二进制流更干净；超时靠 channel
        stdin, stdout, stderr = client.exec_command(remote_cmd, timeout=timeout)
        channel = stdout.channel
        channel.settimeout(timeout)
        chunks: list[bytes] = []
        total = 0
        max_bytes = 80 * 1024 * 1024  # 单机 80MB 上限，防拖垮本机
        while not channel.closed or channel.recv_ready():
            if channel.recv_ready():
                piece = channel.recv(65536)
                if not piece:
                    break
                total += len(piece)
                if total > max_bytes:
                    chunks.append(b'\n...[truncated: output exceeded 80MB]...\n')
                    break
                chunks.append(piece)
            elif channel.exit_status_ready():
                break
            else:
                # 等一点数据
                try:
                    piece = channel.recv(65536)
                    if not piece:
                        if channel.exit_status_ready():
                            break
                        continue
                    total += len(piece)
                    if total > max_bytes:
                        chunks.append(b'\n...[truncated: output exceeded 80MB]...\n')
                        break
                    chunks.append(piece)
                except socket.timeout:
                    if channel.exit_status_ready():
                        break
                    raise OpsSshError(f'读取超时（{timeout}s）')

        exit_status = channel.recv_exit_status()
        err_text = ''
        try:
            err_text = stderr.read().decode('utf-8', errors='replace').strip()
        except Exception:
            err_text = ''

        body = b''.join(chunks)
        # grep 无命中通常 exit 1，仍算成功（0 行）
        if exit_status not in (0, 1) and not body:
            detail = err_text or f'远端退出码 {exit_status}'
            raise OpsSshError(detail[:500])

        header = (
            f'# PengTools log extract\n'
            f'# host={host} name={server.get("name")}\n'
            f'# service={service_name or ""}\n'
            f'# path={path}\n'
            f'# keyword={keyword}\n'
            f'# extra={",".join(extra_keywords or [])}\n'
            f'# context={context_lines}\n'
            f'# remote_cmd={remote_cmd}\n'
            f'# started={started}\n'
            f'# note=streamed via SSH stdout; no temp file on server\n'
            f'# ----\n'
        ).encode('utf-8')
        with open(local_path, 'wb') as stream:
            stream.write(header)
            stream.write(body)

        # 粗略统计命中行（含上下文时仅作参考）
        text = body.decode('utf-8', errors='replace')
        line_count = text.count('\n') + (1 if text and not text.endswith('\n') else 0)
        if not text.strip():
            line_count = 0

        return {
            'ok': True,
            'server_id': server.get('id'),
            'server_name': server.get('name'),
            'service_id': service_id or '',
            'service_name': service_name or '',
            'log_path': path,
            'host': host,
            'local_path': local_path,
            'line_count': line_count,
            'bytes': len(body),
            'exit_status': exit_status,
            'message': '无命中' if line_count == 0 else f'已导出 {line_count} 行',
            'stderr': err_text[:300] if err_text else '',
        }
    except OpsSshError:
        raise
    except Exception as exc:
        raise OpsSshError(str(exc) or exc.__class__.__name__) from exc
    finally:
        try:
            client.close()
        except Exception:
            pass


def extract_logs_parallel(
    servers: list[dict] | None = None,
    *,
    jobs: list[dict] | None = None,
    log_path: str = '',
    keyword: str,
    extra_keywords: list[str] | None = None,
    context_lines: int = 20,
    case_insensitive: bool = True,
    export_dir: str,
    timeout_sec: int = 30,
    max_workers: int = 4,
    on_result: Callable[[dict], None] | None = None,
    selected_keys: set[str] | None = None,
) -> list[dict]:
    """多机 × 多服务路径并行导出。优先 jobs；否则从 servers 展开。"""
    if jobs is None:
        jobs = build_export_jobs(
            list(servers or []),
            selected_keys=selected_keys,
            override_path=log_path,
        )
    if not jobs:
        return []
    workers = max(1, min(16, int(max_workers or 4)))
    results: list[dict] = []

    def _run_one(job: dict) -> dict:
        server = job.get('server') or {}
        svc_name = str(job.get('service_name') or '')
        svc_id = str(job.get('service_id') or '')
        path = str(job.get('log_path') or '')
        try:
            result = extract_log_from_server(
                server,
                log_path=path,
                keyword=keyword,
                extra_keywords=extra_keywords,
                context_lines=context_lines,
                case_insensitive=case_insensitive,
                export_dir=export_dir,
                timeout_sec=timeout_sec,
                service_name=svc_name,
                service_id=svc_id,
            )
            result['job_key'] = job.get('job_key') or make_job_key(
                str(server.get('id') or ''), svc_id,
            )
            return result
        except OpsSshError as exc:
            return {
                'ok': False,
                'server_id': server.get('id'),
                'server_name': server.get('name'),
                'service_id': svc_id,
                'service_name': svc_name,
                'log_path': path,
                'host': server.get('host'),
                'local_path': '',
                'line_count': 0,
                'bytes': 0,
                'exit_status': -1,
                'message': str(exc),
                'stderr': '',
                'job_key': job.get('job_key') or '',
            }
        except Exception as exc:  # pragma: no cover
            return {
                'ok': False,
                'server_id': server.get('id'),
                'server_name': server.get('name'),
                'service_id': svc_id,
                'service_name': svc_name,
                'log_path': path,
                'host': server.get('host'),
                'local_path': '',
                'line_count': 0,
                'bytes': 0,
                'exit_status': -1,
                'message': str(exc) or exc.__class__.__name__,
                'stderr': '',
                'job_key': job.get('job_key') or '',
            }

    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = {pool.submit(_run_one, j): j for j in jobs}
        for fut in as_completed(futures):
            item = fut.result()
            results.append(item)
            if on_result is not None:
                try:
                    on_result(item)
                except Exception:
                    pass
    order = {str(j.get('job_key') or i): i for i, j in enumerate(jobs)}
    results.sort(key=lambda r: order.get(str(r.get('job_key')), 9999))
    return results

def _clean_password(password: str) -> str:
    """清理粘贴带来的换行；保留密码内部空格。"""
    text = str(password or '')
    # 去掉首尾空白与粘贴时的 \\r\\n，避免「肉眼看不见」的换行导致认证失败
    text = text.replace('\r', '').replace('\n', '')
    return text.strip()


def _resolve_password(server: dict, password_override: str | None = None) -> str:
    if password_override is not None and str(password_override) != '':
        password = _clean_password(password_override)
    else:
        password = _clean_password(decrypt_secret(server.get('password_token') or ''))
    if not password:
        raise OpsSshError('密码为空，请编辑服务器并重新输入/粘贴密码后保存')
    return password


def _friendly_ssh_error(exc: BaseException) -> str:
    """把 paramiko 常见英文错误转成中文可读说明。"""
    raw = str(exc) or exc.__class__.__name__
    low = raw.lower()
    name = exc.__class__.__name__
    if 'authentication failed' in low or name in ('AuthenticationException', 'BadAuthenticationType'):
        return (
            '认证失败：用户名或密码不正确。\n'
            '请检查：\n'
            '1）用户名是否写全（区分大小写）\n'
            '2）密码是否粘贴时带了多余空格/换行（可点「显示」核对）\n'
            '3）该机是否允许密码登录（部分环境仅密钥）'
        )
    if 'timed out' in low or 'timeout' in low:
        return '连接超时：主机不可达，或 SSH 端口未开放 / 被防火墙拦截。'
    if 'connection refused' in low:
        return '连接被拒绝：请确认目标机 SSH 服务已启动，端口填写正确。'
    if 'no route' in low or 'network is unreachable' in low:
        return '网络不可达：请检查是否在同一网段、是否需要 VPN。'
    if 'name or service not known' in low or 'getaddrinfo' in low or 'nodename nor servname' in low:
        return '主机名无法解析：请改用 IP 地址，或检查 DNS。'
    # 其它错误：尽量中文包装，避免只丢一行英文
    if raw and any(ord(ch) > 127 for ch in raw):
        return raw
    return f'连接失败：{raw}'


def open_ssh_client(
    server: dict,
    password_override: str | None = None,
    timeout_sec: int = 30,
):
    """打开并返回已连接的 paramiko.SSHClient（调用方负责 close）。"""
    if not paramiko_available():
        raise OpsSshError('未安装 paramiko')
    host = str(server.get('host') or '').strip()
    username = str(server.get('username') or '').strip()
    if not host or not username:
        raise OpsSshError('主机或用户名为空')
    password = _resolve_password(server, password_override)
    timeout = max(5, min(300, int(timeout_sec or 30)))
    port = int(server.get('port') or 22)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        return client
    except Exception as first_exc:
        # 部分环境仅 keyboard-interactive 收密码：先建会话再 interactive
        try:
            try:
                client.close()
            except Exception:
                pass
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            # 不带 password 建连接会停在未认证状态
            sock = socket.create_connection((host, port), timeout=timeout)
            transport = paramiko.Transport(sock)
            transport.banner_timeout = timeout
            transport.auth_timeout = timeout
            transport.start_client(timeout=timeout)

            def _handler(title, instructions, prompt_list):  # noqa: ARG001
                # 每个 prompt 回填同一密码（常见「Password:」）
                return [password for _p in prompt_list]

            try:
                transport.auth_password(username, password)
            except Exception:
                transport.auth_interactive(username, _handler)
            if not transport.is_authenticated():
                raise first_exc
            client._transport = transport  # noqa: SLF001 — 复用已认证 transport
            return client
        except Exception:
            try:
                client.close()
            except Exception:
                pass
            raise OpsSshError(_friendly_ssh_error(first_exc)) from first_exc


def close_ssh_client(client) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _format_size(n: int) -> str:
    value = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{int(value)} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{int(n)} B'


def list_remote_dir(client, path: str = '.') -> list[dict]:
    """SFTP 列出目录。返回 [{name,path,is_dir,size,size_text,mtime,mtime_text}]。"""
    if client is None:
        raise OpsSshError('未连接服务器')
    remote = str(path or '.').strip() or '.'
    try:
        sftp = client.open_sftp()
    except Exception as exc:
        raise OpsSshError(f'无法打开 SFTP：{exc}') from exc
    try:
        try:
            # 规范化路径
            if remote not in ('.', '/'):
                remote = sftp.normalize(remote)
            else:
                remote = sftp.normalize(remote)
        except Exception:
            pass
        try:
            attrs = sftp.listdir_attr(remote)
        except FileNotFoundError as exc:
            raise OpsSshError(f'目录不存在：{remote}') from exc
        except PermissionError as exc:
            raise OpsSshError(f'无权限访问：{remote}') from exc
        except Exception as exc:
            raise OpsSshError(str(exc) or '列目录失败') from exc

        entries = []
        for attr in attrs:
            name = attr.filename
            if name in ('.', '..'):
                continue
            mode = int(getattr(attr, 'st_mode', 0) or 0)
            is_dir = stat_mod.S_ISDIR(mode)
            size = int(getattr(attr, 'st_size', 0) or 0)
            mtime = int(getattr(attr, 'st_mtime', 0) or 0)
            full = remote.rstrip('/') + '/' + name if remote != '/' else '/' + name
            if remote == '.':
                full = name
            try:
                mtime_text = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M') if mtime else ''
            except (OverflowError, OSError, ValueError):
                mtime_text = ''
            entries.append({
                'name': name,
                'path': full,
                'is_dir': bool(is_dir),
                'size': size,
                'size_text': '--' if is_dir else _format_size(size),
                'mtime': mtime,
                'mtime_text': mtime_text,
                'mode': mode,
            })
        entries.sort(key=lambda e: (0 if e['is_dir'] else 1, e['name'].casefold()))
        return entries
    finally:
        try:
            sftp.close()
        except Exception:
            pass


def _looks_like_log_file(name: str) -> bool:
    n = str(name or '').casefold()
    if not n or n.startswith('.'):
        return False
    # 常见：app.log / app-2026-07-23.log / app.log.1 / app.log.gz
    if n.endswith(('.log', '.log.gz', '.out', '.txt')):
        return True
    if '.log.' in n:  # app.log.20260723 / app.log.1
        return True
    return False


def list_remote_log_files(client, path: str) -> list[dict]:
    """列出日志文件候选。

    - path 指向文件：返回该文件一条
    - path 指向目录：返回目录下（非递归）匹配的 .log 等，按修改时间新→旧
    每项：name, path, size, size_text, mtime, mtime_text, is_dir=False
    """
    if client is None:
        raise OpsSshError('未连接服务器')
    remote = str(path or '').strip()
    if not remote:
        raise OpsSshError('日志路径为空')
    try:
        sftp = client.open_sftp()
    except Exception as exc:
        raise OpsSshError(f'无法打开 SFTP：{exc}') from exc
    try:
        try:
            remote = sftp.normalize(remote)
        except Exception:
            pass
        try:
            st = sftp.stat(remote)
        except FileNotFoundError as exc:
            raise OpsSshError(f'路径不存在：{remote}') from exc
        except Exception as exc:
            raise OpsSshError(f'无法访问路径：{exc}') from exc

        mode = int(getattr(st, 'st_mode', 0) or 0)
        if not stat_mod.S_ISDIR(mode):
            # 单文件
            name = remote.rsplit('/', 1)[-1] if '/' in remote else remote
            mtime = int(getattr(st, 'st_mtime', 0) or 0)
            size = int(getattr(st, 'st_size', 0) or 0)
            try:
                mtime_text = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M') if mtime else ''
            except (OverflowError, OSError, ValueError):
                mtime_text = ''
            return [{
                'name': name,
                'path': remote,
                'is_dir': False,
                'size': size,
                'size_text': _format_size(size),
                'mtime': mtime,
                'mtime_text': mtime_text,
            }]

        # 目录：列出日志文件
        try:
            attrs = sftp.listdir_attr(remote)
        except Exception as exc:
            raise OpsSshError(f'无法列出日志目录：{exc}') from exc
        files = []
        for attr in attrs:
            name = attr.filename
            if name in ('.', '..'):
                continue
            amode = int(getattr(attr, 'st_mode', 0) or 0)
            if stat_mod.S_ISDIR(amode):
                continue
            if not _looks_like_log_file(name):
                continue
            full = remote.rstrip('/') + '/' + name if remote != '/' else '/' + name
            mtime = int(getattr(attr, 'st_mtime', 0) or 0)
            size = int(getattr(attr, 'st_size', 0) or 0)
            try:
                mtime_text = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M') if mtime else ''
            except (OverflowError, OSError, ValueError):
                mtime_text = ''
            files.append({
                'name': name,
                'path': full,
                'is_dir': False,
                'size': size,
                'size_text': _format_size(size),
                'mtime': mtime,
                'mtime_text': mtime_text,
            })
        files.sort(key=lambda e: (-int(e.get('mtime') or 0), str(e.get('name') or '').casefold()))
        return files
    finally:
        try:
            sftp.close()
        except Exception:
            pass


def remote_home_dir(client) -> str:
    if client is None:
        raise OpsSshError('未连接服务器')
    try:
        sftp = client.open_sftp()
        try:
            return sftp.normalize('.')
        finally:
            sftp.close()
    except Exception:
        # 回退
        try:
            _stdin, stdout, _stderr = client.exec_command('pwd', timeout=10)
            text = stdout.read().decode('utf-8', errors='replace').strip()
            return text or '/'
        except Exception as exc:
            raise OpsSshError(f'无法获取家目录：{exc}') from exc


def parent_remote_path(path: str) -> str:
    p = str(path or '/').rstrip('/')
    if not p or p == '/':
        return '/'
    parent = os.path.dirname(p.replace('\\', '/'))
    return parent or '/'


def exec_remote(
    client,
    command: str,
    *,
    timeout_sec: int = 60,
    max_bytes: int = 2 * 1024 * 1024,
) -> dict:
    """执行远端命令，返回 stdout/stderr/exit_status（截断过大输出）。"""
    if client is None:
        raise OpsSshError('未连接服务器')
    cmd = str(command or '').strip()
    if not cmd:
        raise OpsSshError('命令为空')
    if any(ch in cmd for ch in ('\x00',)):
        raise OpsSshError('命令含非法字符')
    timeout = max(5, min(600, int(timeout_sec or 60)))
    try:
        _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        channel = stdout.channel
        channel.settimeout(timeout)
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        total = 0
        while not channel.closed or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                piece = channel.recv(65536)
                if piece:
                    total += len(piece)
                    if total <= max_bytes:
                        out_chunks.append(piece)
                    continue
            if channel.recv_stderr_ready():
                piece = channel.recv_stderr(65536)
                if piece:
                    err_chunks.append(piece)
                    continue
            if channel.exit_status_ready():
                break
            try:
                piece = channel.recv(65536)
                if not piece:
                    if channel.exit_status_ready():
                        break
                    continue
                total += len(piece)
                if total <= max_bytes:
                    out_chunks.append(piece)
            except socket.timeout:
                if channel.exit_status_ready():
                    break
                raise OpsSshError(f'命令超时（{timeout}s）')
        exit_status = channel.recv_exit_status()
        try:
            err_rest = stderr.read()
            if err_rest:
                err_chunks.append(err_rest)
        except Exception:
            pass
        stdout_text = b''.join(out_chunks).decode('utf-8', errors='replace')
        stderr_text = b''.join(err_chunks).decode('utf-8', errors='replace')
        if total > max_bytes:
            stdout_text += f'\n...[输出已截断，超过 {max_bytes // 1024} KB]...\n'
        return {
            'command': cmd,
            'stdout': stdout_text,
            'stderr': stderr_text,
            'exit_status': exit_status,
            'bytes': total,
        }
    except OpsSshError:
        raise
    except Exception as exc:
        raise OpsSshError(str(exc) or exc.__class__.__name__) from exc


def test_connection(
    server: dict,
    password_override: str | None = None,
    timeout_sec: int = 15,
) -> dict:
    """探测 SSH 连通性。

    成功返回详情 dict：
      ok, host, port, username, name, elapsed_ms, remote_echo, uname
    失败抛 OpsSshError（消息可读）。
    """
    import time

    host = str((server or {}).get('host') or '').strip()
    if not host:
        raise OpsSshError('主机地址为空')
    username = str((server or {}).get('username') or '').strip()
    if not username:
        raise OpsSshError('用户名为空')
    try:
        port = int((server or {}).get('port') or 22)
    except (TypeError, ValueError):
        port = 22
    name = str((server or {}).get('name') or host).strip() or host
    # 无密码时尽早失败（避免长时间卡在认证）
    token = str((server or {}).get('password_token') or '')
    if password_override is None and not token and not (server or {}).get('password'):
        raise OpsSshError('未配置密码，请先编辑服务器并保存密码')

    started = time.perf_counter()
    client = None
    try:
        client = open_ssh_client(
            server, password_override=password_override, timeout_sec=timeout_sec,
        )
        # 基础回显 + 轻量环境信息（只读）
        result = exec_remote(
            client,
            'echo pengtools_ok; uname -n 2>/dev/null; uname -srm 2>/dev/null | head -n 1',
            timeout_sec=min(15, int(timeout_sec or 15)),
        )
        stdout = (result.get('stdout') or '').strip()
        if 'pengtools_ok' not in stdout:
            raise OpsSshError('已建立 TCP/SSH 会话，但远端命令回显异常')
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and ln.strip() != 'pengtools_ok']
        hostname = lines[0] if lines else ''
        uname = lines[1] if len(lines) > 1 else (lines[0] if lines else '')
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            'ok': True,
            'host': host,
            'port': port,
            'username': username,
            'name': name,
            'elapsed_ms': elapsed_ms,
            'remote_echo': 'pengtools_ok',
            'hostname': hostname,
            'uname': uname,
        }
    except OpsSshError:
        raise
    except Exception as exc:
        raise OpsSshError(str(exc) or exc.__class__.__name__) from exc
    finally:
        close_ssh_client(client)


def format_connection_ok(result: dict, language: str = 'zh') -> str:
    """把 test_connection 成功结果格式化为用户可读文案。"""
    r = result or {}
    if language != 'zh':
        parts = [
            f"SSH OK · {r.get('name') or r.get('host')}",
            f"{r.get('username')}@{r.get('host')}:{r.get('port')}",
            f"{r.get('elapsed_ms', 0)} ms",
        ]
        if r.get('hostname'):
            parts.append(f"hostname={r.get('hostname')}")
        if r.get('uname'):
            parts.append(str(r.get('uname')))
        return '\n'.join(parts)
    parts = [
        f"连通成功 · {r.get('name') or r.get('host')}",
        f"地址：{r.get('username')}@{r.get('host')}:{r.get('port')}",
        f"耗时：{r.get('elapsed_ms', 0)} ms",
    ]
    if r.get('hostname'):
        parts.append(f"主机名：{r.get('hostname')}")
    if r.get('uname'):
        parts.append(f"系统：{r.get('uname')}")
    return '\n'.join(parts)
