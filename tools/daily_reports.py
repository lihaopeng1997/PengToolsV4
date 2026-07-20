# -*- coding: utf-8 -*-
import datetime
import json
import os
import re

from config import DAILY_REPORTS_FILE, DAILY_REPORT_SETTINGS_FILE, ensure_config_dir


DEFAULT_REMINDER = {'enabled': True, 'time': '17:30', 'last_reminder_date': ''}


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
        return value
    except (OSError, ValueError, TypeError):
        return default


def load_reports(path=None):
    value = load_json(path or DAILY_REPORTS_FILE, {})
    return value if isinstance(value, dict) else {}


def save_reports(reports, path=None):
    target = path or DAILY_REPORTS_FILE
    if path is None:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(reports, stream, ensure_ascii=False, indent=2)


def normalize_reminder(settings):
    result = dict(DEFAULT_REMINDER)
    if isinstance(settings, dict):
        result.update(settings)
    result['enabled'] = bool(result['enabled'])
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(result['time'])):
        result['time'] = DEFAULT_REMINDER['time']
    result['last_reminder_date'] = str(result.get('last_reminder_date', ''))
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
    with open(target, 'w', encoding='utf-8') as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
    return normalized


def is_reminder_due(settings, now=None):
    now = now or datetime.datetime.now()
    normalized = normalize_reminder(settings)
    return (
        normalized['enabled']
        and now.strftime('%H:%M') >= normalized['time']
        and normalized['last_reminder_date'] != now.date().isoformat()
    )


def report_markdown(date_value, report):
    labels = (
        ('completed', '今日完成'),
        ('issues', '问题与风险'),
        ('tomorrow', '明日计划'),
        ('notes', '备注'),
    )
    lines = [f'# 工作日报 · {date_value}']
    for key, label in labels:
        value = str(report.get(key, '')).strip() or '无'
        lines.extend(('', f'## {label}', value))
    return '\n'.join(lines).strip() + '\n'
