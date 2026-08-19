# -*- coding: utf-8 -*-
"""一键提签：按环境 SVN 克隆最新签文档，填需求后 import。"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import tempfile

from config import TICKET_SUBMIT_FILE, ensure_config_dir
from tools.requirements import requirement_systems


TICKET_ENVS = ('SIT', 'INT', 'UAT')
TICKET_FOLDER_RE = re.compile(
    r'^(?P<env>[A-Z]+)_(?P=env)_(?P<code>[A-Za-z0-9]+)_(?P<date>\d{8})(?P<slot>\d{1,2})(?P<seq>[A-Z])-(?P<owner>.+)$'
)
HEADER_ALIASES = {
    '系统名': ('系统名',),
    '序号': ('序号',),
    '标签名称': ('标签名称',),
    '上线时间': ('上线时间',),
    '需求编号': ('需求编号',),
    '功能描述': ('功能描述',),
    '文件个数': ('文件个数',),
    '程序清单': ('修改的程序清单与修改说明', '程序清单', '修改的程序清单'),
    '责任人': ('责任人',),
    '升级环境地址': ('升级环境地址', '升级环境'),
    '是否有jar包': ('是否有jar包',),
    '是否有SQL': ('是否有SQL',),
    '备注': ('备注',),
}


def default_ticket_profiles():
    return [
        {
            'id': 'ecif',
            'name': '客户信息平台',
            'folder_code': 'ECIF',
            'source_systems': ['客户信息平台（ECIF）'],
            'owner_default': '李浩鹏',
            'seed_xls': '',
            'envs': {
                'SIT': {'svn_url': '', 'host': ''},
                'INT': {'svn_url': '', 'host': ''},
                'UAT': {'svn_url': '', 'host': ''},
            },
        },
        {
            'id': 'prpcar-share',
            'name': '车险共享中心',
            'folder_code': 'prpcar',
            'source_systems': ['车险承保中心', '共享中心'],
            'owner_default': '李浩鹏',
            'seed_xls': '',
            'envs': {
                'SIT': {'svn_url': '', 'host': ''},
                'INT': {'svn_url': '', 'host': ''},
                'UAT': {'svn_url': '', 'host': ''},
            },
        },
    ]


def _clean_text(value):
    return str(value or '').strip()


def _normalize_env_block(raw):
    item = raw if isinstance(raw, dict) else {}
    return {
        'svn_url': _clean_text(item.get('svn_url')),
        'host': _clean_text(item.get('host')),
    }


def normalize_ticket_profile(raw):
    item = dict(raw or {})
    item['id'] = _clean_text(item.get('id')) or os.urandom(4).hex()
    item['name'] = _clean_text(item.get('name')) or '未命名提签族'
    item['folder_code'] = _clean_text(item.get('folder_code')) or 'SYS'
    systems = []
    for value in item.get('source_systems') or []:
        name = _clean_text(value)
        if name and name not in systems:
            systems.append(name)
    item['source_systems'] = systems
    item['owner_default'] = _clean_text(item.get('owner_default')) or '李浩鹏'
    item['seed_xls'] = _clean_text(item.get('seed_xls'))
    envs = item.get('envs') if isinstance(item.get('envs'), dict) else {}
    item['envs'] = {env: _normalize_env_block(envs.get(env)) for env in TICKET_ENVS}
    return item


def _read_ticket_store(path=None):
    target = path or TICKET_SUBMIT_FILE
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            return loaded
        if isinstance(loaded, list):
            return {'profiles': loaded}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _write_ticket_store(payload, path=None):
    target = path or TICKET_SUBMIT_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)) or '.', exist_ok=True)
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return target


def load_ticket_profiles(path=None):
    loaded = _read_ticket_store(path)
    raw = loaded.get('profiles')
    if isinstance(raw, list) and raw:
        return [normalize_ticket_profile(item) for item in raw if isinstance(item, dict)]
    return [normalize_ticket_profile(item) for item in default_ticket_profiles()]


def save_ticket_profiles(profiles, path=None):
    payload = dict(_read_ticket_store(path))
    payload['profiles'] = [
        normalize_ticket_profile(item) for item in (profiles or []) if isinstance(item, dict)
    ]
    return _write_ticket_store(payload, path)


def load_last_submit(path=None):
    last = _read_ticket_store(path).get('last_submit')
    return dict(last) if isinstance(last, dict) else {}


def save_last_submit(last, path=None):
    payload = dict(_read_ticket_store(path))
    if not payload.get('profiles'):
        payload['profiles'] = [normalize_ticket_profile(item) for item in default_ticket_profiles()]
    payload['last_submit'] = dict(last or {})
    return _write_ticket_store(payload, path)


def configured_ticket_profiles(profiles=None):
    result = []
    for item in (profiles if profiles is not None else load_ticket_profiles()):
        profile = normalize_ticket_profile(item)
        if any(profile['envs'][env]['svn_url'] for env in TICKET_ENVS):
            result.append(profile)
    return result


def default_slot(now=None):
    """上午默认 10，下午默认 15。"""
    moment = now or datetime.datetime.now()
    return '10' if moment.hour < 12 else '15'


def parse_ticket_folder(name):
    text = _clean_text(name).rstrip('/')
    match = TICKET_FOLDER_RE.match(text)
    if not match:
        return None
    data = match.groupdict()
    data['name'] = text
    return data


def find_latest_ticket(names):
    parsed = [item for item in (parse_ticket_folder(name) for name in names or []) if item]
    if not parsed:
        return None
    parsed.sort(key=lambda item: (item['date'], int(item['slot']), item['seq']))
    return parsed[-1]


def next_ticket_folder(existing_names, env, folder_code, owner, now=None, slot=None):
    moment = now or datetime.datetime.now()
    env_key = _clean_text(env).upper()
    code = _clean_text(folder_code)
    person = _clean_text(owner) or '未署名'
    slot_text = _clean_text(slot) or default_slot(moment)
    date_text = moment.strftime('%Y%m%d')
    prefix = f'{env_key}_{env_key}_{code}_{date_text}{slot_text}'
    used = set()
    for item in (parse_ticket_folder(name) for name in existing_names or []):
        if not item:
            continue
        if (
            item['env'] == env_key
            and item['code'] == code
            and item['date'] == date_text
            and item['slot'] == slot_text
        ):
            used.add(item['seq'])
    letter = 'A'
    while letter in used:
        letter = chr(ord(letter) + 1)
        if letter > 'Z':
            raise ValueError(f'{prefix} 当天场次序号已用尽。')
    return f'{prefix}{letter}-{person}'


def format_numbered_cell(values):
    items = [_clean_text(value) for value in (values or []) if _clean_text(value)]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    return '  '.join(f'{index}.{text}' for index, text in enumerate(items, 1))


def yes_no(flag):
    return '是' if flag else '否'


def requirement_candidates_for_profile(requirements, profile):
    wanted = set(normalize_ticket_profile(profile).get('source_systems') or [])
    if not wanted:
        return list(requirements or [])
    result = []
    for item in requirements or []:
        names = requirement_systems(item)
        if not names or any(name in wanted for name in names):
            result.append(item)
    return result


def _header_map(headers):
    mapping = {}
    for index, raw in enumerate(headers or []):
        title = _clean_text(raw)
        if not title:
            continue
        for key, aliases in HEADER_ALIASES.items():
            if title in aliases and key not in mapping:
                mapping[key] = index
    return mapping


def _looks_like_note(value):
    text = _clean_text(value)
    return text.startswith('1、') or text.startswith('2、') or text.startswith('注')


def locate_ticket_sheet(book):
    for sheet in book.sheets():
        for row in range(min(sheet.nrows, 8)):
            headers = [_clean_text(sheet.cell_value(row, col)) for col in range(sheet.ncols)]
            mapping = _header_map(headers)
            if '需求编号' in mapping and '功能描述' in mapping:
                return sheet, row, mapping
    raise ValueError('签文档里找不到「需求编号 / 功能描述」表头，无法套用最新模板。')


def first_data_row(sheet, header_row, mapping):
    code_col = mapping['需求编号']
    for row in range(header_row + 1, sheet.nrows):
        value = sheet.cell_value(row, code_col)
        if _looks_like_note(value):
            continue
        if _clean_text(value) or any(
            _clean_text(sheet.cell_value(row, col))
            for col in mapping.values()
        ):
            return row
    return header_row + 1


def detect_template_system_name(sheet, data_row, mapping):
    col = mapping.get('系统名')
    if col is None:
        return ''
    return _clean_text(sheet.cell_value(data_row, col))


def fill_ticket_xls(source_xls, target_xls, values):
    """按表头写入第一行数据，保留样式与其它页签。"""
    import xlrd
    from xlutils.copy import copy as copy_xls

    book = xlrd.open_workbook(source_xls, formatting_info=True)
    sheet, header_row, mapping = locate_ticket_sheet(book)
    data_row = first_data_row(sheet, header_row, mapping)
    writable = copy_xls(book)
    out = writable.get_sheet(sheet.number)
    payload = dict(values or {})
    if not payload.get('系统名'):
        payload['系统名'] = detect_template_system_name(sheet, data_row, mapping)
    if '序号' in mapping and payload.get('序号') in (None, ''):
        payload['序号'] = 1
    for key, column in mapping.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            value = yes_no(value)
        out.write(data_row, column, value)
    os.makedirs(os.path.dirname(os.path.abspath(target_xls)), exist_ok=True)
    writable.save(target_xls)
    return target_xls


def find_ticket_xls(root):
    matches = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(('.xls', '.xlsx')):
                matches.append(os.path.join(dirpath, name))
    if not matches:
        return None
    preferred = [path for path in matches if '发布单' in os.path.basename(path)]
    return (preferred or matches)[0]


def build_ticket_payload(requirements, *, owner, host='', program_list='', remark='', has_jar=False, has_sql=None):
    items = list(requirements or [])
    codes = [item.get('code') or item.get('title') or '' for item in items]
    titles = [item.get('title') or item.get('code') or '' for item in items]
    if has_sql is None:
        has_sql = any(item.get('has_sql') or item.get('sql_parts') for item in items)
    return {
        '需求编号': format_numbered_cell(codes),
        '功能描述': format_numbered_cell(titles),
        '责任人': _clean_text(owner),
        '升级环境地址': _clean_text(host),
        '程序清单': _clean_text(program_list),
        '备注': _clean_text(remark),
        '是否有jar包': yes_no(has_jar),
        '是否有SQL': yes_no(has_sql),
        '文件个数': _clean_text(program_list) or ('后端' if items else ''),
    }


class TicketSubmitError(ValueError):
    pass


def submit_ticket(
    profile,
    env,
    requirements,
    *,
    owner=None,
    slot=None,
    host=None,
    program_list='',
    remark='',
    has_jar=False,
    has_sql=None,
    now=None,
    seed_xls='',
    svn_ops=None,
):
    """生成签目录并提交到 SVN。svn_ops 可注入以便单测。"""
    from tools.svn_workspace import (
        join_svn_url, svn_export, svn_import, svn_list, svn_mkdir_remote, validate_svn_url,
    )

    ops = svn_ops or {
        'list': svn_list,
        'export': svn_export,
        'mkdir': svn_mkdir_remote,
        'import': svn_import,
        'validate': validate_svn_url,
    }
    profile = normalize_ticket_profile(profile)
    env_key = _clean_text(env).upper()
    if env_key not in TICKET_ENVS:
        raise TicketSubmitError('环境只能是 SIT / INT / UAT。')
    env_conf = profile['envs'].get(env_key) or {}
    svn_url = _clean_text(env_conf.get('svn_url'))
    if not svn_url:
        raise TicketSubmitError(f'还没有配置「{profile["name"]} / {env_key}」的提签 SVN 地址。')
    items = list(requirements or [])
    if not items:
        raise TicketSubmitError('请至少选择一条需求。')
    person = _clean_text(owner) or profile.get('owner_default') or '李浩鹏'
    moment = now or datetime.datetime.now()
    slot_text = _clean_text(slot) or default_slot(moment)
    year = moment.strftime('%Y')
    year_url = join_svn_url(ops['validate'](svn_url), year)
    try:
        existing = ops['list'](year_url)
    except Exception:
        existing = []
        try:
            ops['mkdir'](year_url, f'创建 {year} 提签目录')
        except Exception:
            pass
    folder_name = next_ticket_folder(
        existing, env_key, profile['folder_code'], person, now=moment, slot=slot_text,
    )
    latest = find_latest_ticket(existing)
    seed = _clean_text(seed_xls) or profile.get('seed_xls')
    work = tempfile.mkdtemp(prefix='pengtools-ticket-')
    try:
        template_xls = _resolve_template_xls(ops, year_url, latest, seed, work)
        payload = build_ticket_payload(
            items,
            owner=person,
            host=host if host is not None else env_conf.get('host', ''),
            program_list=program_list,
            remark=remark,
            has_jar=has_jar,
            has_sql=has_sql,
        )
        rel_dir, xls_name = _template_layout(template_xls, latest['name'] if latest else '')
        ticket_root = os.path.join(work, folder_name)
        xls_dir = os.path.join(ticket_root, rel_dir) if rel_dir else ticket_root
        os.makedirs(xls_dir, exist_ok=True)
        target_xls = os.path.join(xls_dir, xls_name)
        fill_ticket_xls(template_xls, target_xls, payload)
        dest_url = join_svn_url(year_url, folder_name)
        imported = ops['import'](ticket_root, dest_url, f'一键提签 {folder_name}')
        keep = os.path.join(tempfile.mkdtemp(prefix='pengtools-ticket-keep-'), os.path.basename(target_xls))
        shutil.copy2(target_xls, keep)
        return {
            'folder': folder_name,
            'url': dest_url,
            'xls': keep,
            'output': imported.get('output', '') if isinstance(imported, dict) else '',
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _resolve_template_xls(ops, year_url, latest, seed, work):
    from tools.svn_workspace import join_svn_url
    if latest:
        ticket_url = join_svn_url(year_url, latest['name'])
        export_dir = os.path.join(work, '_latest')
        ops['export'](ticket_url, export_dir)
        found = find_ticket_xls(export_dir)
        if found:
            return found
    if seed and os.path.isfile(seed):
        return seed
    raise TicketSubmitError('该环境还没有历史签。请在配置里指定一份种子 xls，或先在 SVN 上放一份模板签。')


def _template_layout(template_xls, latest_folder_name):
    name = os.path.basename(template_xls)
    parent = os.path.basename(os.path.dirname(template_xls))
    if latest_folder_name and parent and parent != latest_folder_name and parent != '_latest':
        return parent, name
    if parent in ('升级路径', '升级清单'):
        return parent, name
    return '', name
