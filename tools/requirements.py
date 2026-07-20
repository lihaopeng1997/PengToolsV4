# -*- coding: utf-8 -*-
import datetime
import json
import os
import re
import uuid

from config import REQUIREMENTS_FILE, ensure_config_dir
from tools.svn_workspace import month_end_date


CATEGORIES = ('功能需求', '缺陷优化', '接口联动', '数据变更', '配置调整', '其他')
STATUSES = ('待分析', '开发中', '待测试', '待上线', '已上线', '暂停')
PRIORITIES = ('普通', '重要', '紧急')

# key, 树节点短名, 对话框全名（适用项名称，≠完成状态）
FLAG_DEFS = (
    ('has_sql', 'SQL', '涉及 SQL'),
    ('needs_peripheral_upgrade', '周边', '通知周边系统'),
    ('needs_interface_update', '接口', '更新接口文档'),
    ('temporary_upgrade', '临时', '临时/紧急升级'),
)

# 详情「完成标记」按钮展示名（与 FLAG_DEFS 的 key 对应）
FLAG_CHIP_LABELS = {
    'has_sql': 'SQL',
    'needs_peripheral_upgrade': '周边通知',
    'needs_interface_update': '接口文档',
    'temporary_upgrade': '临时升级',
}

CATEGORY_KEYWORDS = {
    '缺陷优化': ('bug', '缺陷', '修复', '异常', '报错', '优化'),
    '接口联动': ('接口', 'api', '联调', '报文', '周边系统', '服务调用'),
    '数据变更': ('sql', '数据库', '表结构', '字段', 'ddl', 'dml', '数据修复'),
    '配置调整': ('配置', '参数', '开关', '字典', '菜单', '权限'),
    '功能需求': ('需求', '功能', '新增', '改造', '支持', '实现'),
}

# 系统别名 → 配置名；匹配标题/描述/路径/SVN/SQL 时用
SYSTEM_HINTS = (
    ('车险承保中心', (
        '车险承保中心', '车险承保', '承保中心', 'prpcar', 'autocore', 'sitautocore',
        '出单', '保单', '随车', '车险部', '团车', '非车随车',
    )),
    ('客户信息平台（ECIF）', (
        '客户信息平台（ecif）', '客户信息平台', '客户信息', 'ecif', 'sitecif', '客户平台',
    )),
    ('数据字典', ('数据字典', 'permission', 'sitpermission', '字典管理')),
    ('统一监管接入平台', ('统一监管接入平台', '监管接入', '监管报送', 'relt', 'sitrelt', '统一监管')),
    ('共享中心', ('共享中心', 'sharingcenter', 'sharing', '共享平台')),
)

SQL_TOKEN_RE = re.compile(r'(?i)\b(select|insert|update|delete|merge|create|alter|drop|comment|grant|truncate)\b')
PERIPHERAL_RE = re.compile(r'周边|外围|同步升级|联动升级|通知.*系统|下游系统|上游系统')
TEMPORARY_RE = re.compile(r'临时升级|紧急上线|热修复|hotfix|临时方案|紧急修复', re.I)
INTERFACE_RE = re.compile(r'接口|api|报文|联调|接口文档|swagger|wsdl', re.I)


def flag_is_active(requirement, key):
    if key == 'has_sql':
        return bool(requirement.get('has_sql') or requirement.get('sql_parts'))
    return bool(requirement.get(key))


def active_flags(requirement):
    return [(key, short, full) for key, short, full in FLAG_DEFS if flag_is_active(requirement, key)]


def normalize_flag_done(requirement):
    done = requirement.get('flag_done')
    if not isinstance(done, dict):
        done = {}
    cleaned = {}
    for key, _short, _full in FLAG_DEFS:
        cleaned[key] = bool(done.get(key)) if flag_is_active(requirement, key) else False
    requirement['flag_done'] = cleaned
    return cleaned


def flag_status_text(requirement):
    """左侧树用：待完成 / 已完成文字，不依赖红绿点。"""
    parts = []
    done = normalize_flag_done(requirement)
    for key, short, _full in active_flags(requirement):
        state = '已完成' if done.get(key) else '待完成'
        parts.append(f'{short}·{state}')
    return '  '.join(parts) if parts else '○ 无上线事项'


def flag_chip_text(key, is_done: bool) -> str:
    label = FLAG_CHIP_LABELS.get(key) or key
    mark = '✓' if is_done else '○'
    state = '已完成' if is_done else '待完成'
    return f'{mark} {label} · {state}'


def normalize_requirement(requirement):
    item = dict(requirement or {})
    # 保留 id，避免保存后无法回写同一条
    if not item.get('id'):
        item['id'] = uuid.uuid4().hex
    for key, _short, _full in FLAG_DEFS:
        if key == 'has_sql':
            item['has_sql'] = bool(item.get('has_sql') or item.get('sql_parts'))
        else:
            item[key] = bool(item.get(key))
    if not isinstance(item.get('sql_parts'), list):
        item['sql_parts'] = []
    if not isinstance(item.get('source_files'), list):
        item['source_files'] = []
    if item.get('title') is None:
        item['title'] = ''
    if item.get('code') is None:
        item['code'] = ''
    normalize_flag_done(item)
    return item


def load_requirements(path=None):
    target = path or REQUIREMENTS_FILE
    try:
        with open(target, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
        if not isinstance(value, list):
            return []
        return [normalize_requirement(item) for item in value if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        return []


def save_requirements(requirements, path=None):
    target = path or REQUIREMENTS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = [normalize_requirement(item) for item in (requirements or []) if isinstance(item, dict)]
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def classify_requirement(text):
    lowered = str(text or '').casefold()
    scores = {
        category: sum(lowered.count(keyword.casefold()) for keyword in keywords)
        for category, keywords in CATEGORY_KEYWORDS.items()
    }
    return max(scores, key=scores.get) if any(scores.values()) else '其他'


def _requirement_corpus(requirement):
    """汇总可用于推断的文本（标题/描述/路径/SVN/SQL/附件名）。"""
    if not isinstance(requirement, dict):
        return str(requirement or '')
    chunks = [
        requirement.get('code', ''),
        requirement.get('title', ''),
        requirement.get('description', ''),
        requirement.get('svn_url', ''),
        requirement.get('local_path', ''),
        requirement.get('system', ''),
    ]
    for part in requirement.get('sql_parts') or []:
        chunks.append(part.get('name', ''))
        chunks.append(part.get('content', ''))
    for part in requirement.get('source_files') or []:
        chunks.append(part.get('name', ''))
        chunks.append(part.get('content', ''))
    return '\n'.join(str(value or '') for value in chunks)


def infer_system_name(text, systems=None):
    """从文本推断所属系统；无法确定返回空串。systems 为 load_systems() 列表时优先匹配配置名。"""
    corpus = str(text or '')
    if not corpus.strip():
        return ''
    lowered = corpus.casefold()
    configured = []
    if systems:
        for item in systems:
            name = str(item.get('name', '') if isinstance(item, dict) else item or '').strip()
            if name:
                configured.append(name)
    # 完整配置名优先
    for name in configured:
        if name and name.casefold() in lowered:
            return name
    best_name, best_score = '', 0
    for name, keywords in SYSTEM_HINTS:
        score = sum(lowered.count(keyword.casefold()) for keyword in keywords)
        for keyword in keywords:
            if re.search(r'(?i)(?:^|[/_\-\s])' + re.escape(keyword) + r'(?:[/_\-\s.]|$)', corpus):
                score += 2
        if score > best_score:
            best_name, best_score = name, score
    if best_score <= 0:
        return ''
    if configured and best_name not in configured:
        for name in configured:
            if best_name in name or name in best_name:
                return name
        return best_name
    return best_name


def infer_online_month_from_text(text, default_year=None):
    """从标题/描述/路径推断上线月份 yyyy-MM。"""
    corpus = str(text or '')
    if not corpus.strip():
        return ''
    # REQ-20260715 / 2026-07 / 2026年7月
    match = re.search(r'(?i)(?:REQ|BUG|DEF)[-_]?((20\d{2})(0[1-9]|1[0-2])\d{2})', corpus)
    if match:
        return f'{match.group(2)}-{match.group(3)}'
    match = re.search(r'(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:月|-|/|\.|$)', corpus)
    if match:
        return f'{int(match.group(1)):04d}-{int(match.group(2)):02d}'
    match = re.search(r'(20\d{2})(0[1-9]|1[0-2])\d{0,2}', corpus)
    if match and re.search(r'(?i)req|bug|def|上线|升级', corpus):
        return f'{match.group(1)}-{match.group(2)}'
    year = default_year or datetime.date.today().year
    match = re.search(r'(?<!\d)(0?[1-9]|1[0-2])月', corpus)
    if match:
        return f'{int(year):04d}-{int(match.group(1)):02d}'
    return ''


def infer_upgrade_flags(text, has_sql_parts=False):
    """推断 has_sql / 周边 / 接口 / 临时 标记。"""
    corpus = str(text or '')
    return {
        'has_sql': bool(has_sql_parts or SQL_TOKEN_RE.search(corpus)),
        'needs_peripheral_upgrade': bool(PERIPHERAL_RE.search(corpus)),
        'temporary_upgrade': bool(TEMPORARY_RE.search(corpus)),
        'needs_interface_update': bool(INTERFACE_RE.search(corpus)),
    }


def apply_auto_inference(requirement, systems=None, only_empty=True):
    """填充空字段：系统、标记、上线月份；不覆盖已有明确值（only_empty=True）。

    旧数据兼容：已有字段保持不变；仅补全空值。
    """
    item = normalize_requirement(requirement)
    corpus = _requirement_corpus(item)
    flags = infer_upgrade_flags(corpus, has_sql_parts=bool(item.get('sql_parts')))

    if not only_empty or not str(item.get('system') or '').strip():
        inferred = infer_system_name(corpus, systems=systems)
        if inferred:
            item['system'] = inferred

    if not only_empty or not str(item.get('online_month') or '').strip():
        month = infer_online_month_from_text(corpus)
        if not month and item.get('local_path'):
            try:
                from tools.svn_workspace import infer_online_month
                month = infer_online_month(item.get('local_path', ''))
            except Exception:
                month = ''
        if month:
            item['online_month'] = month
            if not str(item.get('planned_online_date') or '').strip():
                item['planned_online_date'] = month_end_date(month)

    if only_empty:
        if not item.get('has_sql') and not item.get('sql_parts'):
            item['has_sql'] = flags['has_sql']
        if not item.get('needs_peripheral_upgrade'):
            item['needs_peripheral_upgrade'] = flags['needs_peripheral_upgrade']
        if not item.get('temporary_upgrade'):
            item['temporary_upgrade'] = flags['temporary_upgrade']
        if not item.get('needs_interface_update'):
            item['needs_interface_update'] = flags['needs_interface_update']
    else:
        item.update(flags)
        if item.get('sql_parts'):
            item['has_sql'] = True

    if not str(item.get('category') or '').strip() or item.get('category') == '其他':
        classified = classify_requirement(corpus)
        if classified != '其他' or not item.get('category'):
            item['category'] = classified

    if not str(item.get('record_kind') or '').strip():
        item['record_kind'] = 'BUG' if re.search(r'(?i)(?:\bBUG\b|\bDEF[-_]|缺陷|问题单)', corpus) else '需求'

    normalize_flag_done(item)
    return item


def requirement_from_text(text, source_name='直接粘贴', systems=None):
    normalized = str(text or '').strip()
    lines = [re.sub(r'^\s*[#>*\-]+\s*', '', line).strip() for line in normalized.splitlines()]
    lines = [line for line in lines if line]
    code_match = re.search(r'(?i)\b(?:REQ|DEF|BUG)[-_A-Z0-9]{4,}\b', normalized)
    title = next((line for line in lines[:12] if len(line) <= 80), os.path.splitext(source_name)[0])
    if code_match and title.casefold() == code_match.group(0).casefold() and len(lines) > 1:
        title = lines[1]
    now = datetime.datetime.now().isoformat(timespec='seconds')
    flags = infer_upgrade_flags(normalized)
    seed = normalize_requirement({
        'id': uuid.uuid4().hex,
        'code': code_match.group(0) if code_match else '',
        'title': title or '未命名需求',
        'record_kind': 'BUG' if re.search(r'(?i)(?:\bBUG\b|\bDEF[-_]|缺陷|问题单)', normalized) else '需求',
        'description': normalized,
        'category': classify_requirement(normalized),
        'status': '待分析',
        'priority': '普通',
        'system': '',
        'owner': '',
        'planned_online_date': '',
        'actual_online_date': '',
        'online_month': '',
        'has_sql': flags['has_sql'],
        'needs_peripheral_upgrade': flags['needs_peripheral_upgrade'],
        'temporary_upgrade': flags['temporary_upgrade'],
        'needs_interface_update': flags['needs_interface_update'],
        'flag_done': {
            'has_sql': False,
            'needs_peripheral_upgrade': False,
            'needs_interface_update': False,
            'temporary_upgrade': False,
        },
        'sql_parts': [],
        'source_files': [{'name': source_name, 'content': normalized}] if source_name else [],
        'svn_url': '',
        'local_path': '',
        'svn_revision': '',
        'svn_status': '',
        'created_at': now,
        'updated_at': now,
    })
    return apply_auto_inference(seed, systems=systems, only_empty=True)


def merged_sql(requirement):
    blocks = []
    for part in requirement.get('sql_parts', []):
        content = str(part.get('content', '')).strip()
        if content:
            blocks.append(f"-- 需求 SQL：{part.get('name', '未命名.sql')}\n{content}")
    return '\n\n'.join(blocks)


def requirement_search_text(requirement):
    values = [requirement.get(key, '') for key in (
        'code', 'title', 'description', 'record_kind', 'category', 'status', 'priority',
        'system', 'owner', 'online_month', 'svn_url', 'local_path', 'svn_revision', 'svn_status'
    )]
    values.extend(part.get('name', '') + '\n' + part.get('content', '')
                  for part in requirement.get('sql_parts', []))
    for part in requirement.get('source_files', []):
        values.append(part.get('name', '') + '\n' + part.get('content', ''))
        values.extend(str(value) for row in part.get('rows', []) for value in row)
    return '\n'.join(str(value) for value in values).casefold()


def daily_template(requirement):
    name = ' '.join(part for part in (requirement.get('code'), requirement.get('title')) if part)
    flags = []
    if requirement.get('has_sql') or requirement.get('sql_parts'):
        flags.append('含 SQL')
    if requirement.get('needs_interface_update'):
        flags.append('需整理接口文档')
    if requirement.get('needs_peripheral_upgrade'):
        flags.append('需通知周边系统升级')
    if requirement.get('temporary_upgrade'):
        flags.append('临时升级')
    detail = '、'.join(flags) if flags else '暂无特殊升级项'
    kind = requirement.get('record_kind', '需求')
    return {
        'completed': f'- [{kind}] {name}：已接收并完成资料归档',
        'tomorrow': f'- [{kind}] {name}：继续推进分析、开发或验证',
        'notes': f'- 分类：{requirement.get("category", "其他")}；状态：{requirement.get("status", "待分析")}；{detail}',
    }


def requirement_from_working_copy(copy_info, systems=None):
    path = copy_info.get('local_path', '')
    title = os.path.basename(path.rstrip(os.sep)) or copy_info.get('relative_path') or '未命名需求'
    seed = requirement_from_text(title, title, systems=systems)
    seed.update({
        'title': title,
        'record_kind': copy_info.get('record_kind', '需求'),
        'category': '缺陷优化' if copy_info.get('record_kind') == 'BUG' else classify_requirement(title),
        'online_month': copy_info.get('online_month', '') or seed.get('online_month', ''),
        'planned_online_date': month_end_date(copy_info.get('online_month', '') or seed.get('online_month', '')),
        'svn_url': copy_info.get('svn_url', ''),
        'local_path': path,
        'svn_revision': copy_info.get('svn_revision', ''),
        'svn_status': copy_info.get('svn_status', ''),
        'workspace_kind': copy_info.get('workspace_kind', 'svn'),
        'file_count': copy_info.get('file_count', 0),
        'source_modified_at': copy_info.get('source_modified_at', ''),
        'description': f"从本地需求目录扫描导入：{copy_info.get('relative_path', title)}",
        'source_files': [],
    })
    return apply_auto_inference(seed, systems=systems, only_empty=True)


def merge_working_copies(requirements, copies):
    result = list(requirements)
    by_path = {os.path.normcase(os.path.abspath(item.get('local_path'))): item
               for item in result if item.get('local_path')}
    by_url = {item.get('svn_url'): item for item in result if item.get('svn_url')}
    added = 0
    updated = 0
    for copy_info in copies:
        path = copy_info.get('local_path', '')
        url = copy_info.get('svn_url', '')
        existing = by_path.get(os.path.normcase(os.path.abspath(path))) if path else None
        existing = existing or by_url.get(url)
        if existing:
            existing.update({
                'svn_url': url or existing.get('svn_url', ''),
                'local_path': path or existing.get('local_path', ''),
                'svn_revision': copy_info.get('svn_revision', ''),
                'svn_status': copy_info.get('svn_status', ''),
                'workspace_kind': copy_info.get('workspace_kind', existing.get('workspace_kind', 'svn')),
                'file_count': copy_info.get('file_count', existing.get('file_count', 0)),
                'source_modified_at': copy_info.get('source_modified_at', existing.get('source_modified_at', '')),
            })
            if not existing.get('online_month') and copy_info.get('online_month'):
                existing['online_month'] = copy_info['online_month']
                existing['planned_online_date'] = month_end_date(copy_info['online_month'])
            existing['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
            updated += 1
            continue
        item = requirement_from_working_copy(copy_info)
        result.append(item)
        by_path[os.path.normcase(os.path.abspath(path))] = item
        if url:
            by_url[url] = item
        added += 1
    return result, added, updated
