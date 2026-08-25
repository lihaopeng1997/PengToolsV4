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

from config import HARNESS_PROJECTS_DIR, HARNESS_SKILLS_DIR, ensure_config_dir

_TABLE_RE = re.compile(
    r'\b(?:from|join|into|update)\s+([A-Za-z][A-Za-z0-9_]*)',
    re.IGNORECASE,
)
_SKIP_TABLES = frozenset({'dual', 'xml', 'select', 'where'})


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
