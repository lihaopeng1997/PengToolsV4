# -*- coding: utf-8 -*-
"""日报持久化、提醒、资源图、草稿与 Markdown 导出。

- 历史正文：兼容纯文字段；富文本写入 *_html
- 图片：落在 data/daily_assets/{date}/，JSON 只存相对路径
- 草稿：data/daily_report_drafts.json，防闪退丢字
"""
from __future__ import annotations

import calendar
import datetime
import html
import json
import os
import re
import shutil
import uuid
from typing import Any

from config import (
    DAILY_ASSETS_DIR,
    DAILY_REPORT_DRAFTS_FILE,
    DAILY_REPORT_SETTINGS_FILE,
    DAILY_REPORTS_FILE,
    ensure_config_dir,
    local_data_dir,
)

REPORT_FIELDS = ('completed', 'issues', 'tomorrow', 'notes')
DEFAULT_REMINDER = {
    'enabled': True,
    'time': '17:30',
    'last_reminder_date': '',
    # 历史树折叠偏好
    'history_collapsed_months': [],   # 当前月被用户折叠
    'history_expanded_months': [],    # 非当前月被用户展开
    'history_expand_pinned': True,
}

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DAY_ASSETS_BYTES = 30 * 1024 * 1024
_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
        return value
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write_json(target: str, payload) -> None:
    directory = os.path.dirname(os.path.abspath(target)) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except OSError:
            pass
    os.replace(tmp, target)


def load_reports(path=None):
    value = load_json(path or DAILY_REPORTS_FILE, {})
    if not isinstance(value, dict):
        return {}
    return {str(k): normalize_report(v) for k, v in value.items() if str(k)}


def save_reports(reports, path=None):
    target = path or DAILY_REPORTS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = {}
    for key, report in (reports or {}).items():
        if not str(key):
            continue
        payload[str(key)] = normalize_report(report)
    _atomic_write_json(target, payload)


def load_drafts(path=None):
    value = load_json(path or DAILY_REPORT_DRAFTS_FILE, {})
    if not isinstance(value, dict):
        return {}
    return {str(k): normalize_report(v) for k, v in value.items() if str(k)}


def save_drafts(drafts, path=None):
    target = path or DAILY_REPORT_DRAFTS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    payload = {str(k): normalize_report(v) for k, v in (drafts or {}).items() if str(k)}
    _atomic_write_json(target, payload)


def plain_from_html(html_text: str) -> str:
    """粗粒度去标签，供检索与兼容字段。"""
    text = str(html_text or '')
    if not text:
        return ''
    # QTextEdit 空文档也会导出带 CSS 的完整 HTML，必须先丢掉 head/style
    text = re.sub(r'<style\b[^>]*>.*?</style>', '', text, flags=re.I | re.S)
    text = re.sub(r'<script\b[^>]*>.*?</script>', '', text, flags=re.I | re.S)
    text = re.sub(r'<head\b[^>]*>.*?</head>', '', text, flags=re.I | re.S)
    # 图片用占位
    text = re.sub(r'<img\b[^>]*>', ' [图片] ', text, flags=re.I)
    text = re.sub(r'<\s*br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'</\s*p\s*>', '\n', text, flags=re.I)
    text = re.sub(r'</\s*div\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def normalize_report(report) -> dict:
    """统一日报条目：plain + html + assets。"""
    src = dict(report) if isinstance(report, dict) else {}
    out: dict[str, Any] = {}
    assets = []
    for item in src.get('assets') or []:
        text = str(item or '').replace('\\', '/').strip()
        if text:
            assets.append(text)
    out['assets'] = assets
    for key in REPORT_FIELDS:
        plain = str(src.get(key) or '').strip()
        html_key = f'{key}_html'
        html_val = str(src.get(html_key) or '').strip()
        if not html_val and plain:
            # 旧数据：纯文本 → 简单 HTML
            escaped = html.escape(plain).replace('\n', '<br/>')
            html_val = f'<p>{escaped}</p>' if escaped else ''
        if not plain and html_val:
            plain = plain_from_html(html_val)
        out[key] = plain
        out[html_key] = html_val
    out['updated_at'] = str(src.get('updated_at') or '')
    return out


def fields_snapshot(report: dict | None) -> dict:
    src = normalize_report(report or {})
    snap = {k: str(src.get(k) or '').strip() for k in REPORT_FIELDS}
    for k in REPORT_FIELDS:
        snap[f'{k}_html'] = str(src.get(f'{k}_html') or '').strip()
    return snap


_IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.I)
_IMG_ATTR_RE = re.compile(
    r'(src|width|height)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    re.I,
)


def html_image_signature(html_text: str) -> tuple[tuple[str, str, str], ...]:
    """只比较插图路径和尺寸，忽略 Qt 导出的完整 HTML 外壳。"""
    sigs = []
    for tag in _IMG_TAG_RE.findall(str(html_text or '')):
        attrs = {}
        for match in _IMG_ATTR_RE.finditer(tag):
            key = match.group(1).lower()
            attrs[key] = match.group(2) or match.group(3) or match.group(4) or ''
        src = str(attrs.get('src') or '').replace('\\', '/')
        idx = src.lower().find('daily_assets/')
        if idx >= 0:
            src = src[idx:]
        sigs.append((src, str(attrs.get('width') or ''), str(attrs.get('height') or '')))
    return tuple(sigs)


def is_report_dirty(saved: dict | None, current: dict | None) -> bool:
    """比较用户可见正文和插图，不把 QTextEdit 重写 HTML 当成未保存。"""
    a = normalize_report(saved or {})
    b = normalize_report(current or {})
    for key in REPORT_FIELDS:
        if str(a.get(key) or '').strip() != str(b.get(key) or '').strip():
            return True
        if html_image_signature(a.get(f'{key}_html')) != html_image_signature(b.get(f'{key}_html')):
            return True
    return False


def normalize_reminder(settings):
    result = dict(DEFAULT_REMINDER)
    if isinstance(settings, dict):
        result.update(settings)
    result['enabled'] = bool(result['enabled'])
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(result['time'])):
        result['time'] = DEFAULT_REMINDER['time']
    result['last_reminder_date'] = str(result.get('last_reminder_date', ''))
    def _month_list(value):
        raw = value if isinstance(value, list) else []
        return [
            str(m)[:7] for m in raw
            if re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', str(m)[:7] or '')
        ]
    result['history_collapsed_months'] = _month_list(result.get('history_collapsed_months'))
    result['history_expanded_months'] = _month_list(result.get('history_expanded_months'))
    result['history_expand_pinned'] = bool(result.get('history_expand_pinned', True))
    return result


def load_reminder_settings(path=None):
    return normalize_reminder(load_json(path or DAILY_REPORT_SETTINGS_FILE, {}))


def save_reminder_settings(settings, path=None):
    target = path or DAILY_REPORT_SETTINGS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    normalized = normalize_reminder(settings)
    _atomic_write_json(target, normalized)
    return normalized


def is_reminder_due(settings, now=None):
    now = now or datetime.datetime.now()
    normalized = normalize_reminder(settings)
    return (
        normalized['enabled']
        and now.strftime('%H:%M') >= normalized['time']
        and normalized['last_reminder_date'] != now.date().isoformat()
    )


def month_key(date_value: str) -> str:
    text = str(date_value or '')[:7]
    return text if re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', text) else ''


def month_label(month: str, language: str = 'zh') -> str:
    key = month_key(month)
    if not key:
        return '未分组' if language == 'zh' else 'Ungrouped'
    if language == 'zh':
        y, m = key.split('-')
        return f'{y}年{int(m)}月'
    return key


def days_in_month(month: str) -> int:
    key = month_key(month)
    if not key:
        return 0
    y, m = key.split('-')
    return calendar.monthrange(int(y), int(m))[1]


def group_dates_by_month(dates) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for d in dates or []:
        key = str(d or '')[:10]
        if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', key):
            continue
        mk = month_key(key)
        groups.setdefault(mk, []).append(key)
    for mk in groups:
        groups[mk] = sorted(set(groups[mk]), reverse=True)
    return groups


def asset_dir_for(date_value: str, *, root=None) -> str:
    day = str(date_value or '')[:10]
    base = root or DAILY_ASSETS_DIR
    path = os.path.join(base, day)
    return path


def relative_asset_path(date_value: str, filename: str) -> str:
    day = str(date_value or '')[:10]
    name = os.path.basename(filename)
    return f'daily_assets/{day}/{name}'.replace('\\', '/')


def absolute_asset_path(rel: str, *, data_root=None) -> str:
    text = str(rel or '').replace('\\', '/').lstrip('/')
    root = data_root or local_data_dir()
    return os.path.normpath(os.path.join(root, text))


def day_assets_size(date_value: str, *, root=None) -> int:
    folder = asset_dir_for(date_value, root=root)
    total = 0
    if not os.path.isdir(folder):
        return 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return total


def save_image_bytes(
    date_value: str,
    data: bytes,
    *,
    ext: str = '.png',
    root=None,
) -> str:
    """保存图片字节，返回相对 data 根的路径。超限抛 ValueError。"""
    raw = data or b''
    if not raw:
        raise ValueError('空图片')
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f'单张图片不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB')
    if day_assets_size(date_value, root=root) + len(raw) > MAX_DAY_ASSETS_BYTES:
        raise ValueError(f'当日图片总量不能超过 {MAX_DAY_ASSETS_BYTES // (1024 * 1024)}MB')
    suffix = ext if str(ext).startswith('.') else f'.{ext}'
    suffix = suffix.lower()
    if suffix not in _IMAGE_EXTS:
        suffix = '.png'
    folder = asset_dir_for(date_value, root=root)
    os.makedirs(folder, exist_ok=True)
    name = f'{uuid.uuid4().hex}{suffix}'
    path = os.path.join(folder, name)
    with open(path, 'wb') as stream:
        stream.write(raw)
    return relative_asset_path(date_value, name)


def save_image_file(date_value: str, source_path: str, *, root=None) -> str:
    if not source_path or not os.path.isfile(source_path):
        raise ValueError('图片文件不存在')
    ext = os.path.splitext(source_path)[1].lower() or '.png'
    with open(source_path, 'rb') as stream:
        data = stream.read()
    return save_image_bytes(date_value, data, ext=ext, root=root)


def cleanup_day_assets(date_value: str, *, root=None) -> None:
    folder = asset_dir_for(date_value, root=root)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)


def collect_asset_refs_from_html(html_text: str) -> list[str]:
    """从 HTML 中提取 daily_assets/ 相对路径。"""
    text = str(html_text or '')
    found = re.findall(r'daily_assets/[0-9]{4}-[0-9]{2}-[0-9]{2}/[^"\'\s>]+', text, flags=re.I)
    # file:/// 绝对路径也尝试归一
    for match in re.findall(r'src=["\']([^"\']+)["\']', text, flags=re.I):
        norm = match.replace('\\', '/')
        idx = norm.lower().find('daily_assets/')
        if idx >= 0:
            found.append(norm[idx:])
    # 去重保序
    out = []
    seen = set()
    for item in found:
        key = item.replace('\\', '/')
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def report_markdown(date_value, report, *, data_root=None):
    labels = (
        ('completed', '今日完成'),
        ('issues', '问题与风险'),
        ('tomorrow', '明日计划'),
        ('notes', '备注'),
    )
    normalized = normalize_report(report)
    root = data_root or local_data_dir()
    lines = [f'# 工作日报 · {date_value}']
    for key, label in labels:
        html_val = normalized.get(f'{key}_html') or ''
        plain = normalized.get(key) or ''
        lines.extend(('', f'## {label}'))
        if html_val and '<img' in html_val.lower():
            # 简易：段落文本 + 图片链接
            body = plain or plain_from_html(html_val) or '无'
            lines.append(body)
            for rel in collect_asset_refs_from_html(html_val):
                abs_path = absolute_asset_path(rel, data_root=root)
                lines.append(f'![图片]({abs_path})')
        else:
            lines.append(plain or '无')
    return '\n'.join(lines).strip() + '\n'
