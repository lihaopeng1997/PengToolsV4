# -*- coding: utf-8 -*-
import datetime
import json
import os
import re
import tempfile
import uuid

from config import REQUIREMENTS_FILE, ensure_config_dir
from tools.svn_workspace import month_end_date


CATEGORIES = ('功能需求', '缺陷优化', '接口联动', '数据变更', '配置调整', '其他')
STATUSES = (
    '待分析', '待开发', '开发中', '待测试', '集成测试',
    '用户测试', '模拟测试', '待上线', '已上线', '暂停',
)
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


# 测试任务点：只认列表行，不把普通段落拆成条目
_TEST_POINT_MAX_LEN = 160
_TEST_POINT_LIST_RE = re.compile(
    r'^\s*(?:'
    r'(?:[-*•·□☐☑✓✔])\s+'
    r'|\[(?: |x|X|✓)\]\s+'
    r'|\d+[\.、\)]\s*'
    r')(.+?)\s*$'
)
_TEST_POINT_DONE_PREFIX_RE = re.compile(r'^\s*(?:☑|✓|✔|\[(?:x|X|✓)\])')


def normalize_test_points(value):
    """把任意输入收成 [{id, text, done}]；非法/空文案丢弃。"""
    items = []
    seen_ids = set()
    if not isinstance(value, list):
        return items
    for raw in value:
        existing_id = ''
        if isinstance(raw, str):
            text = raw.strip()
            done = False
        elif isinstance(raw, dict):
            text = str(raw.get('text') or '').strip()
            done = bool(raw.get('done'))
            existing_id = str(raw.get('id') or '').strip()
        else:
            continue
        if not text:
            continue
        point_id = existing_id or uuid.uuid4().hex
        if point_id in seen_ids:
            point_id = uuid.uuid4().hex
        seen_ids.add(point_id)
        items.append({'id': point_id, 'text': text, 'done': done})
    return items


def extract_test_points_from_text(text):
    """从需求说明中识别列表行。普通段落、过长行不提取。"""
    points = []
    seen = set()
    for raw in str(text or '').splitlines():
        match = _TEST_POINT_LIST_RE.match(raw)
        if not match:
            continue
        body = match.group(1).strip()
        if not body or len(body) > _TEST_POINT_MAX_LEN:
            continue
        key = body.casefold()
        if key in seen:
            continue
        seen.add(key)
        points.append({
            'id': uuid.uuid4().hex,
            'text': body,
            'done': bool(_TEST_POINT_DONE_PREFIX_RE.match(raw)),
        })
    return points


def test_points_progress(points):
    items = normalize_test_points(points)
    total = len(items)
    done = sum(1 for item in items if item.get('done'))
    return done, total


def test_points_button_text(points, zh=True):
    done, total = test_points_progress(points)
    if total <= 0:
        return '测试点' if zh else 'Tests'
    return f'{done}/{total}'


def save_requirement_test_points(requirement_id, points, path=None):
    """即时写回测试点，不改需求业务状态。"""
    normalized = normalize_test_points(points)

    def _apply(item):
        item['test_points'] = normalized

    return update_requirement_by_id(requirement_id, _apply, path=path)


def _clean_system_name(value):
    return str(value or '').strip()


def requirement_systems(item):
    """权威系统列表：优先 systems，否则回退旧字段 system。"""
    if not isinstance(item, dict):
        return []
    names = []
    raw = item.get('systems')
    if isinstance(raw, (list, tuple)):
        for value in raw:
            name = _clean_system_name(value)
            if name and name not in names:
                names.append(name)
    if not names:
        name = _clean_system_name(item.get('system'))
        if name:
            names.append(name)
    return names


def requirement_matches_system(item, name):
    wanted = _clean_system_name(name)
    if not wanted:
        return True
    return wanted in requirement_systems(item)


def systems_display_text(item, empty='未选系统'):
    names = requirement_systems(item)
    return '、'.join(names) if names else empty


def binding_for(item, name):
    name = _clean_system_name(name)
    bindings = item.get('system_bindings') if isinstance(item, dict) else None
    raw = bindings.get(name) if isinstance(bindings, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    svn = str(raw.get('svn_url') or '').strip()
    dev = str(raw.get('dev_local_path') or '').strip()
    names = requirement_systems(item) if isinstance(item, dict) else []
    if name and names and name == names[0] and isinstance(item, dict):
        svn = svn or str(item.get('svn_url') or '').strip()
        dev = dev or str(item.get('dev_local_path') or '').strip()
    return {'svn_url': svn, 'dev_local_path': dev}


def sync_system_fields(item):
    """systems 与旧字段 system / 顶层 svn_url / dev_local_path 双写对齐。"""
    if not isinstance(item, dict):
        return item
    names = requirement_systems(item)
    item['systems'] = list(names)
    item['system'] = names[0] if names else ''
    previous = item.get('system_bindings')
    if not isinstance(previous, dict):
        previous = {}
    cleaned = {}
    for name in names:
        bound = binding_for({**item, 'system_bindings': previous}, name)
        cleaned[name] = {
            'svn_url': bound['svn_url'],
            'dev_local_path': bound['dev_local_path'],
        }
    item['system_bindings'] = cleaned
    if item['system']:
        primary = cleaned.get(item['system']) or {}
        item['svn_url'] = str(primary.get('svn_url') or '').strip()
        item['dev_local_path'] = str(primary.get('dev_local_path') or '').strip()
    return item


def explode_requirement_for_release(item):
    """一条需求按系统展开为发版行；无系统时仍返回一行（系统空）。"""
    source = dict(item or {})
    names = requirement_systems(source)
    if not names:
        row = dict(source)
        row['system'] = ''
        row['_release_system'] = ''
        return [row]
    rows = []
    for name in names:
        row = dict(source)
        bound = binding_for(source, name)
        row['system'] = name
        row['svn_url'] = bound['svn_url']
        row['dev_local_path'] = bound['dev_local_path']
        row['_release_system'] = name
        rows.append(row)
    return rows


def apply_release_system_writeback(source, system_name, *, svn_url=None, release_scope=None):
    """发版表改某一系统时，只回写该系统绑定，不覆盖整条 systems。"""
    if not isinstance(source, dict):
        return source
    name = _clean_system_name(system_name)
    sync_system_fields(source)
    if name:
        if name not in source['systems']:
            source['systems'].append(name)
        bindings = source.setdefault('system_bindings', {})
        current = dict(bindings.get(name) or {}) if isinstance(bindings.get(name), dict) else {}
        if svn_url is not None:
            current['svn_url'] = str(svn_url or '').strip()
        current.setdefault('dev_local_path', str(current.get('dev_local_path') or '').strip())
        bindings[name] = current
    if release_scope is not None:
        source['release_scope'] = str(release_scope or '').strip() or '后端：全部'
    return sync_system_fields(source)


def sql_part_system(part):
    if not isinstance(part, dict):
        return ''
    return _clean_system_name(part.get('system'))


def unassigned_sql_parts(item):
    return [
        part for part in ((item or {}).get('sql_parts') or [])
        if isinstance(part, dict) and not sql_part_system(part)
    ]


def has_unassigned_sql_when_multi_system(item):
    return len(requirement_systems(item)) > 1 and bool(unassigned_sql_parts(item))


def sql_parts_for_system(item, system_name):
    names = requirement_systems(item)
    wanted = _clean_system_name(system_name)
    allow_unassigned = len(names) <= 1
    result = []
    for part in ((item or {}).get('sql_parts') or []):
        if not isinstance(part, dict):
            continue
        assigned = sql_part_system(part)
        if assigned:
            if assigned == wanted:
                result.append(part)
        elif allow_unassigned:
            result.append(part)
    return result


def merged_sql_for_system(requirement, system_name):
    blocks = []
    for part in sql_parts_for_system(requirement, system_name):
        content = str(part.get('content', '')).strip()
        if content:
            blocks.append(f"-- 需求 SQL：{part.get('name', '未命名.sql')}\n{content}")
    return '\n\n'.join(blocks)


def clear_workspace_binding(item):
    """用户主动解绑资料目录：路径与目录衍生字段一并清空，避免保存后看起来仍已绑定。"""
    if not isinstance(item, dict):
        return item
    item['local_path'] = ''
    item['workspace_kind'] = ''
    item['file_count'] = 0
    item['svn_revision'] = ''
    item['svn_status'] = ''
    item['svn_locks'] = {}
    item['source_modified_at'] = ''
    return item


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
    cleaned_parts = []
    for part in item['sql_parts']:
        if not isinstance(part, dict):
            continue
        entry = dict(part)
        entry['system'] = sql_part_system(entry)
        cleaned_parts.append(entry)
    item['sql_parts'] = cleaned_parts
    if not isinstance(item.get('source_files'), list):
        item['source_files'] = []
    if item.get('title') is None:
        item['title'] = ''
    if item.get('code') is None:
        item['code'] = ''
    item['pinned'] = bool(item.get('pinned'))
    item['is_monthly_release'] = bool(item.get('is_monthly_release'))
    if item['pinned']:
        item['pinned_at'] = str(item.get('pinned_at') or '')
    else:
        item.pop('pinned_at', None)
    item['svn_url'] = str(item.get('svn_url') or '').strip()
    item['local_path'] = str(item.get('local_path') or '').strip()
    item['dev_local_path'] = str(item.get('dev_local_path') or '').strip()
    if not item['local_path']:
        clear_workspace_binding(item)
    sync_system_fields(item)
    normalize_flag_done(item)
    item['test_points'] = normalize_test_points(item.get('test_points'))
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
    finally:
        # 磁盘台账变化后，旧搜索语料不可靠
        if path is None or path == REQUIREMENTS_FILE:
            clear_requirement_search_cache()


def _atomic_write_json(target, payload):
    """先写临时文件再 replace，避免中途崩溃留下半截 JSON。"""
    directory = os.path.dirname(os.path.abspath(target)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.req-', suffix='.tmp', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def save_requirements(requirements, path=None):
    target = path or REQUIREMENTS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = [normalize_requirement(item) for item in (requirements or []) if isinstance(item, dict)]
    _atomic_write_json(target, payload)
    if path is None or path == REQUIREMENTS_FILE:
        clear_requirement_search_cache()


# 工作台「标记上线 / 恢复待办」与需求状态对齐
ONLINE_STATUS = '已上线'
PENDING_ONLINE_STATUS = '待上线'


def is_online_status(requirement) -> bool:
    return str((requirement or {}).get('status') or '').strip() == ONLINE_STATUS


def release_board_key(requirement_id, month: str) -> str:
    return f"{requirement_id or ''}@{str(month or '')[:7]}"


def is_release_item_completed(requirement, month: str, completed_keys) -> bool:
    """展示层完成：台账已上线 或 看板完成键命中。"""
    if is_online_status(requirement):
        return True
    keys = set(completed_keys or [])
    return release_board_key((requirement or {}).get('id'), month) in keys


def update_requirement_by_id(requirement_id, mutator, path=None):
    """加载台账 → 原地修改匹配 id 的条目 → 写回。返回更新后的 dict，未找到返回 None。"""
    req_id = str(requirement_id or '')
    if not req_id:
        return None
    items = load_requirements(path)
    target = next((item for item in items if str(item.get('id') or '') == req_id), None)
    if target is None:
        return None
    mutator(target)
    target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    save_requirements(items, path)
    return normalize_requirement(target)


def mark_requirement_online(requirement_id, path=None, online_date=None):
    """工作台标记上线：status=已上线；实际上线日期为空时写入今天。"""
    day = str(online_date or datetime.date.today().isoformat())[:10]

    def _apply(item):
        item['status'] = ONLINE_STATUS
        if not str(item.get('actual_online_date') or '').strip():
            item['actual_online_date'] = day

    return update_requirement_by_id(requirement_id, _apply, path=path)


def restore_requirement_from_online(requirement_id, path=None, clear_actual_date=True):
    """恢复待办：仅当当前为已上线时改回待上线；可选清空实际上线日期。

    若台账已是其它状态，不覆盖业务状态，返回 (item, status_changed)。
    """
    items = load_requirements(path)
    req_id = str(requirement_id or '')
    target = next((item for item in items if str(item.get('id') or '') == req_id), None)
    if target is None:
        return None, False
    changed = False
    if is_online_status(target):
        target['status'] = PENDING_ONLINE_STATUS
        if clear_actual_date:
            target['actual_online_date'] = ''
        changed = True
        target['updated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        save_requirements(items, path)
    return normalize_requirement(target), changed


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
        requirement.get('dev_local_path', ''),
        requirement.get('system', ''),
        ' '.join(requirement_systems(requirement)),
    ]
    bindings = requirement.get('system_bindings')
    if isinstance(bindings, dict):
        for name, bound in bindings.items():
            chunks.append(name)
            if isinstance(bound, dict):
                chunks.append(bound.get('svn_url', ''))
                chunks.append(bound.get('dev_local_path', ''))
    for part in requirement.get('sql_parts') or []:
        chunks.append(part.get('name', ''))
        chunks.append(part.get('content', ''))
    for part in requirement.get('source_files') or []:
        chunks.append(part.get('name', ''))
        chunks.append(part.get('content', ''))
    return '\n'.join(str(value or '') for value in chunks)


def infer_system_names(text, systems=None):
    """从文本推断全部命中的系统；无法确定返回空列表。"""
    corpus = str(text or '')
    if not corpus.strip():
        return []
    lowered = corpus.casefold()
    configured = []
    if systems:
        for item in systems:
            name = str(item.get('name', '') if isinstance(item, dict) else item or '').strip()
            if name:
                configured.append(name)
    hits = []
    for name in configured:
        if name and name.casefold() in lowered and name not in hits:
            hits.append(name)
    for name, keywords in SYSTEM_HINTS:
        score = sum(lowered.count(keyword.casefold()) for keyword in keywords)
        for keyword in keywords:
            if re.search(r'(?i)(?:^|[/_\-\s])' + re.escape(keyword) + r'(?:[/_\-\s.]|$)', corpus):
                score += 2
        if score <= 0:
            continue
        resolved = name
        if configured and name not in configured:
            resolved = next((item for item in configured if name in item or item in name), name)
        if resolved and resolved not in hits:
            hits.append(resolved)
    return hits


def infer_system_name(text, systems=None):
    """从文本推断所属系统；无法确定返回空串。systems 为 load_systems() 列表时优先匹配配置名。"""
    names = infer_system_names(text, systems=systems)
    return names[0] if names else ''


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

    旧数据兼容：已有字段保持不变；仅补全「键缺失」或文本空值。
    注意：布尔 False 是用户显式选择，不能当空再推断勾回。
    """
    raw = dict(requirement or {}) if isinstance(requirement, dict) else {}
    item = normalize_requirement(requirement)
    corpus = _requirement_corpus(item)
    flags = infer_upgrade_flags(corpus, has_sql_parts=bool(item.get('sql_parts')))

    if not only_empty or not requirement_systems(item):
        inferred = infer_system_names(corpus, systems=systems)
        if inferred:
            item['systems'] = list(inferred)
            sync_system_fields(item)

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

    flag_keys = (
        'has_sql',
        'needs_peripheral_upgrade',
        'temporary_upgrade',
        'needs_interface_update',
    )
    if only_empty:
        # 仅当原始记录缺少该键时才补全；已存在的 True/False 一律保留
        for key in flag_keys:
            if key not in raw:
                if key == 'has_sql' and item.get('sql_parts'):
                    item['has_sql'] = True
                else:
                    item[key] = flags[key]
            elif key == 'has_sql' and item.get('sql_parts'):
                item['has_sql'] = True
    else:
        item.update(flags)
        if item.get('sql_parts'):
            item['has_sql'] = True

    # 分类：only_empty 时不覆盖用户已选「其他」；强制模式才可重分类
    category = str(item.get('category') or '').strip()
    if not only_empty:
        classified = classify_requirement(corpus)
        if classified != '其他' or not category:
            item['category'] = classified
    elif not category:
        classified = classify_requirement(corpus)
        if classified:
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
        'dev_local_path': '',
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


def requirement_identity(item) -> str:
    """发版勾选/看板定位用的稳定键：id > code > path > title。"""
    if not isinstance(item, dict):
        return ''
    return str(item.get('id') or item.get('code') or item.get('path') or item.get('title') or '').strip()


# 需求搜索语料缓存：避免每次树刷新对全表重算拼音 blob
_REQUIREMENT_SEARCH_CACHE: dict[str, tuple[str, str]] = {}


def clear_requirement_search_cache():
    """台账重载/保存后清空，避免脏缓存。"""
    _REQUIREMENT_SEARCH_CACHE.clear()


def _requirement_search_cache_key(requirement) -> str:
    """用 id + 更新时间 + 几个常搜字段拼缓存键。"""
    if not isinstance(requirement, dict):
        return ''
    return '|'.join((
        str(requirement.get('id') or ''),
        str(requirement.get('updated_at') or ''),
        str(requirement.get('source_modified_at') or ''),
        str(requirement.get('title') or ''),
        str(requirement.get('code') or ''),
        str(requirement.get('status') or ''),
        str(requirement.get('system') or ''),
        str(len(requirement.get('sql_parts') or [])),
        str(requirement.get('file_count') or 0),
    ))


def requirement_search_text(requirement):
    """搜索语料：元数据 + 拼音；不含密钥。SQL/附件正文仅取名称与有限摘要。"""
    from tools.pinyin_search import build_search_blob
    cache_key = _requirement_search_cache_key(requirement)
    if cache_key:
        cached = _REQUIREMENT_SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return cached[1]
    values = [requirement.get(key, '') for key in (
        'code', 'title', 'description', 'record_kind', 'category', 'status', 'priority',
        'system', 'owner', 'online_month', 'svn_url', 'local_path', 'dev_local_path',
        'svn_revision', 'svn_status',
    )]
    values.extend(requirement_systems(requirement))
    bindings = requirement.get('system_bindings')
    if isinstance(bindings, dict):
        for name, bound in bindings.items():
            values.append(name)
            if isinstance(bound, dict):
                values.append(bound.get('svn_url', ''))
                values.append(bound.get('dev_local_path', ''))
    for part in requirement.get('sql_parts', []) or []:
        values.append(part.get('name', ''))
        # 正文过长时只索引前 2k，避免把大报文永久驻留在搜索串
        content = str(part.get('content', '') or '')
        if content:
            values.append(content[:2000])
    for part in requirement.get('source_files', []) or []:
        values.append(part.get('name', ''))
        values.append(part.get('file_type', ''))
        for row in (part.get('rows', []) or [])[:40]:
            values.extend(str(value) for value in row[:12])
    for point in requirement.get('test_points') or []:
        if isinstance(point, dict):
            values.append(point.get('text', ''))
        else:
            values.append(str(point or ''))
    blob = build_search_blob(*values)
    if cache_key:
        # 简单 LRU：超上限时整表清空，避免无限涨
        if len(_REQUIREMENT_SEARCH_CACHE) >= 4000:
            _REQUIREMENT_SEARCH_CACHE.clear()
        _REQUIREMENT_SEARCH_CACHE[cache_key] = (str(requirement.get('id') or ''), blob)
    return blob


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
    system_text = systems_display_text(requirement, empty='未选系统')
    return {
        'completed': f'- [{kind}] {name}：已接收并完成资料归档',
        'tomorrow': f'- [{kind}] {name}：继续推进分析、开发或验证',
        'notes': (
            f'- 分类：{requirement.get("category", "其他")}；状态：{requirement.get("status", "待分析")}；'
            f'系统：{system_text}；{detail}'
        ),
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
