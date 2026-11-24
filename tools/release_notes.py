# -*- coding: utf-8 -*-
"""版本更新公告数据加载与「不再提醒」判定（纯业务逻辑，不依赖 Qt）。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _candidate_paths() -> list[str]:
    paths: list[str] = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        paths.append(os.path.join(meipass, 'resources', 'help', 'release_notes.json'))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths.append(os.path.join(root, 'resources', 'help', 'release_notes.json'))
    return paths


def load_release_notes(path: str | None = None) -> dict[str, Any]:
    """读取内置版本更新公告 JSON；缺失或损坏时安全回退空结构。"""
    candidates = [path] if path else _candidate_paths()
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, 'r', encoding='utf-8-sig') as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                continue
            releases = [item for item in (raw.get('releases') or []) if isinstance(item, dict)]
            latest_ver = str(raw.get('latest_version') or '').strip()
            if not latest_ver and releases:
                latest_ver = str(releases[0].get('version') or '').strip()
            return {
                'latest_version': latest_ver,
                'releases': releases,
            }
        except (OSError, ValueError, TypeError):
            continue
    return {'latest_version': '', 'releases': []}


def latest_release(data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """返回最新版本公告条目。"""
    payload = data if isinstance(data, dict) else load_release_notes()
    releases = payload.get('releases') or []
    if not releases:
        return None
    latest_ver = str(payload.get('latest_version') or '').strip()
    if latest_ver:
        for item in releases:
            if str(item.get('version') or '').strip() == latest_ver:
                return item
    return releases[0]


def should_show_release_announcement(
    settings: dict[str, Any] | None,
    data: dict[str, Any] | None = None,
) -> bool:
    """判定启动时是否应自动弹出最新版本的更新公告。"""
    cfg = settings if isinstance(settings, dict) else {}
    if bool(cfg.get('announcement_muted_all', False)):
        return False
    latest = latest_release(data)
    if not latest:
        return False
    version = str(latest.get('version') or '').strip()
    if not version:
        return False
    dismissed = str(cfg.get('announcement_dismissed_version') or '').strip()
    return dismissed != version


def build_announcement_settings_patch(
    version: str,
    *,
    dismiss_this_version: bool,
    mute_all: bool,
    previous_dismissed: str = '',
) -> dict[str, Any]:
    """根据用户勾选状态生成待落盘的设置补丁。"""
    ver = str(version or '').strip()
    muted = bool(mute_all)
    if muted or dismiss_this_version:
        dismissed_ver = ver or str(previous_dismissed or '').strip()
    else:
        # 若用户取消勾选当前版本不再提醒，仅清除当前版本标记
        prev = str(previous_dismissed or '').strip()
        dismissed_ver = '' if prev == ver else prev
    return {
        'announcement_dismissed_version': dismissed_ver,
        'announcement_muted_all': muted,
    }
