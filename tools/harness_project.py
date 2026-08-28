# -*- coding: utf-8 -*-
"""PTools Harness 项目包与技能安装。

内置约定：resources/harness/projects/<id>/
用户覆盖：data/harness/projects/<id>/ 与 data/harness/skills/
不把业务源码打进 EXE；CodeGraph 可选，默认用 MyBatis XML 抽表名。
"""

from __future__ import annotations

import json
import os
import re
import shutil

from config import (
    HARNESS_PROJECTS_DIR,
    HARNESS_SKILLS_DIR,
    HARNESS_SKILLS_FILE,
    ensure_config_dir,
)

_TABLE_RE = re.compile(
    r'\b(?:from|join|into|update)\s+([A-Za-z][A-Za-z0-9_]*)',
    re.IGNORECASE,
)
_SKIP_TABLES = frozenset({'dual', 'xml', 'select', 'where'})

# 内置默认任务清单（代码常量）；用户 skills.json 同名 task 覆盖内置，其余追加。
DEFAULT_TASKS = [
    {'task': 'sql.draft', 'file': 'sql.md', 'title': '生成 SQL 草案', 'desc': '自然语言转 SQL', 'enabled': True, 'builtin': True},
    {'task': 'sql.optimize', 'file': 'sql_optimize.md', 'title': '优化 SQL', 'desc': '优化已有 SQL', 'enabled': True, 'builtin': True},
    {'task': 'linux.query', 'file': 'log_query.md', 'title': 'Linux 只读查询', 'desc': '自然语言转只读命令', 'enabled': True, 'builtin': True},
    {'task': 'mongo.query', 'file': 'mongo_query.md', 'title': 'Mongo 查询', 'desc': '自然语言查 Mongo', 'enabled': True, 'builtin': True},
    {'task': 'redis.query', 'file': 'redis_query.md', 'title': 'Redis 查询', 'desc': '自然语言查 Redis', 'enabled': True, 'builtin': True},
]


def _builtin_root() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, 'resources', 'harness', 'projects')


def _ensure_user_dirs():
    ensure_config_dir()
    os.makedirs(HARNESS_SKILLS_DIR, exist_ok=True)
    os.makedirs(HARNESS_PROJECTS_DIR, exist_ok=True)


def _read_profile(folder: str) -> dict | None:
    path = os.path.join(folder, 'profile.json')
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = str(data.get('id') or os.path.basename(folder)).strip()
    if not pid:
        return None
    data['id'] = pid
    data['name'] = str(data.get('name') or pid)
    data['_folder'] = folder
    return data


def list_projects() -> list[dict]:
    found: dict[str, dict] = {}
    builtin = _builtin_root()
    if os.path.isdir(builtin):
        for name in sorted(os.listdir(builtin)):
            profile = _read_profile(os.path.join(builtin, name))
            if profile:
                found[profile['id']] = profile
    if os.path.isdir(HARNESS_PROJECTS_DIR):
        for name in sorted(os.listdir(HARNESS_PROJECTS_DIR)):
            profile = _read_profile(os.path.join(HARNESS_PROJECTS_DIR, name))
            if profile:
                found[profile['id']] = profile
    return [found[key] for key in sorted(found)]


def load_project(project_id: str) -> dict:
    wanted = str(project_id or '').strip() or 'prpcar'
    for item in list_projects():
        if item.get('id') == wanted:
            return item
    items = list_projects()
    return items[0] if items else {'id': wanted, 'name': wanted, '_folder': ''}


def active_project_id(cfg=None) -> str:
    if isinstance(cfg, dict) and cfg.get('project_id'):
        return str(cfg.get('project_id') or 'prpcar')
    from tools.intranet_llm import load_ai_local
    return str(load_ai_local().get('project_id') or 'prpcar')


def _skill_candidates(project: dict, filename: str) -> list[str]:
    paths = []
    _ensure_user_dirs()
    paths.append(os.path.join(HARNESS_SKILLS_DIR, filename))
    folder = str(project.get('_folder') or '')
    if folder:
        paths.append(os.path.join(folder, 'skills', filename))
    return paths


def load_skill_text(filename: str, fallback: str, project: dict | None = None) -> str:
    project = project if isinstance(project, dict) else load_project(active_project_id())
    for path in _skill_candidates(project, filename):
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                text = stream.read().strip()
            if text:
                return text
        except OSError:
            continue
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    builtin = os.path.join(root, 'resources', 'ai_skills', filename)
    try:
        with open(builtin, 'r', encoding='utf-8') as stream:
            text = stream.read().strip()
        if text:
            return text
    except OSError:
        pass
    return fallback


def project_context(project: dict | None = None) -> str:
    project = project if isinstance(project, dict) else load_project(active_project_id())
    if not project:
        return ''
    lines = [
        f"当前项目：{project.get('name') or project.get('id')}（{project.get('id')}）",
        f"SQL 方言：{project.get('dialect') or 'oracle'}",
    ]
    prefixes = project.get('table_prefixes') or []
    if prefixes:
        lines.append('表前缀：' + '、'.join(str(item) for item in prefixes))
    tables = list(project.get('key_tables') or [])
    extra_path = os.path.join(str(project.get('_folder') or ''), 'tables.json')
    try:
        with open(extra_path, 'r', encoding='utf-8') as stream:
            extra = json.load(stream)
        if isinstance(extra, list):
            tables.extend(str(item) for item in extra if item)
    except (OSError, ValueError, TypeError):
        pass
    uniq = []
    for name in tables:
        text = str(name).strip()
        if text and text not in uniq:
            uniq.append(text)
    if uniq:
        lines.append('常见表：' + '、'.join(uniq[:40]))
    for hint in project.get('log_hints') or []:
        lines.append(str(hint))
    return '\n'.join(lines)


def _read_skills_manifest() -> list[dict]:
    """读取用户级 skills.json；缺失/损坏返回空列表。"""
    try:
        with open(HARNESS_SKILLS_FILE, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    tasks = data.get('tasks')
    if not isinstance(tasks, list):
        return []
    cleaned = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task = str(item.get('task') or '').strip()
        if not task:
            continue
        cleaned.append({
            'task': task,
            'file': str(item.get('file') or '').strip(),
            'title': str(item.get('title') or task).strip(),
            'desc': str(item.get('desc') or '').strip(),
            'enabled': bool(item.get('enabled', True)),
            'builtin': False,
        })
    return cleaned


def list_tasks() -> list[dict]:
    """合并内置默认清单与用户 skills.json：同名 task 用户覆盖内置，其余追加。"""
    merged: dict[str, dict] = {}
    for item in DEFAULT_TASKS:
        merged[str(item['task'])] = dict(item)
    for item in _read_skills_manifest():
        task = str(item['task'])
        if task in merged:
            # 用户覆盖内置：保留 builtin=True 标记（不可删，仅可停用），其余字段覆盖
            base = merged[task]
            base.update({key: value for key, value in item.items() if key != 'builtin'})
            base['builtin'] = True
        else:
            merged[task] = dict(item)
    return [merged[key] for key in sorted(merged)]


def resolve_task_file(task: str) -> str | None:
    """返回 task 对应的 skill 文件名；未知返回 None。"""
    wanted = str(task or '').strip()
    for item in list_tasks():
        if item.get('task') == wanted:
            file = str(item.get('file') or '').strip()
            return file or None
    return None


def _write_skills_manifest(tasks: list[dict]) -> None:
    _ensure_user_dirs()
    payload = {'tasks': []}
    for item in tasks:
        payload['tasks'].append({
            'task': str(item.get('task') or '').strip(),
            'file': str(item.get('file') or '').strip(),
            'title': str(item.get('title') or '').strip(),
            'desc': str(item.get('desc') or '').strip(),
            'enabled': bool(item.get('enabled', True)),
        })
    with open(HARNESS_SKILLS_FILE, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def add_task(task: str, file: str, title: str = '', desc: str = '') -> None:
    """注册一个用户级 task（写入 skills.json，不覆盖内置文件本身）。

    与内置同名视为重复（应改用 update_task 覆盖），避免误覆盖内置语义。
    """
    task = str(task or '').strip()
    file = str(file or '').strip()
    if not task or not file:
        raise ValueError('task 名与文件名不能为空')
    if any(item['task'] == task for item in DEFAULT_TASKS):
        raise ValueError(f'task 已存在（内置）：{task}')
    current = _read_skills_manifest()
    for item in current:
        if item.get('task') == task:
            raise ValueError(f'task 已存在：{task}')
    current.append({'task': task, 'file': file, 'title': title or task, 'desc': desc or '', 'enabled': True})
    _write_skills_manifest(current)


def update_task(task: str, *, title: str | None = None, desc: str | None = None,
                enabled: bool | None = None, file: str | None = None) -> None:
    """更新 task 的 title/desc/enabled/file。

    用户 task 直接改清单；内置 task 通过在 skills.json 写入覆盖条目实现
    （首次覆盖时新建一条，builtin 标记由 list_tasks 合并时补回）。
    """
    task = str(task or '').strip()
    builtin_default = next((item for item in DEFAULT_TASKS if item['task'] == task), None)
    current = _read_skills_manifest()
    item = next((entry for entry in current if entry.get('task') == task), None)
    if item is None:
        if builtin_default is None:
            raise ValueError(f'未知 task：{task}')
        # 首次覆盖内置：以内置默认值为基底新建用户覆盖条目
        item = {
            'task': task,
            'file': builtin_default['file'],
            'title': builtin_default['title'],
            'desc': builtin_default['desc'],
            'enabled': bool(builtin_default.get('enabled', True)),
        }
        current.append(item)
    if title is not None:
        item['title'] = str(title).strip()
    if desc is not None:
        item['desc'] = str(desc).strip()
    if enabled is not None:
        item['enabled'] = bool(enabled)
    if file is not None:
        item['file'] = str(file).strip()
    _write_skills_manifest(current)


def remove_task(task: str, *, delete_file: bool = False) -> None:
    """删除用户级 task；内置 task 仅清除用户覆盖（还原内置默认）。"""
    task = str(task or '').strip()
    current = _read_skills_manifest()
    remaining = [item for item in current if item.get('task') != task]
    if delete_file:
        for item in current:
            if item.get('task') == task:
                file = str(item.get('file') or '').strip()
                if file:
                    path = os.path.join(HARNESS_SKILLS_DIR, os.path.basename(file))
                    try:
                        if os.path.isfile(path):
                            os.remove(path)
                    except OSError:
                        pass
    _write_skills_manifest(remaining)


def install_skill(source_path: str) -> str:
    """把本地 .md 技能安装到 data/harness/skills/，同名覆盖。"""
    src = os.path.abspath(str(source_path or ''))
    if not src.lower().endswith('.md') or not os.path.isfile(src):
        raise ValueError('请选择 .md 技能文件')
    _ensure_user_dirs()
    name = os.path.basename(src)
    dest = os.path.join(HARNESS_SKILLS_DIR, name)
    shutil.copy2(src, dest)
    return dest


def scan_mybatis_tables(folder: str, limit: int = 80) -> list[str]:
    """从 MyBatis XML 抽表名，不扫描 Java、不建 CodeGraph。"""
    root = os.path.abspath(str(folder or ''))
    if not os.path.isdir(root):
        raise ValueError('目录不存在')
    counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in {'.git', 'target', 'node_modules', '.codegraph'}]
        for filename in filenames:
            if not filename.lower().endswith('.xml'):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as stream:
                    text = stream.read()
            except OSError:
                continue
            if '<mapper' not in text and '<select' not in text.lower():
                continue
            for match in _TABLE_RE.finditer(text):
                name = match.group(1)
                if name.lower() in _SKIP_TABLES:
                    continue
                counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return [name for name, _count in ranked[: max(1, int(limit))]]


def save_project_tables(project_id: str, tables: list[str]) -> str:
    _ensure_user_dirs()
    folder = os.path.join(HARNESS_PROJECTS_DIR, str(project_id or 'prpcar'))
    os.makedirs(os.path.join(folder, 'skills'), exist_ok=True)
    base = load_project(project_id)
    profile = {key: value for key, value in base.items() if not str(key).startswith('_')}
    profile['id'] = str(project_id or 'prpcar')
    path = os.path.join(folder, 'profile.json')
    if not os.path.isfile(path):
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(profile, stream, indent=2, ensure_ascii=False)
    tables_path = os.path.join(folder, 'tables.json')
    cleaned = []
    for name in tables:
        text = str(name).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    with open(tables_path, 'w', encoding='utf-8') as stream:
        json.dump(cleaned, stream, indent=2, ensure_ascii=False)
    return tables_path
