# -*- coding: utf-8 -*-
"""Web / Native Dashboard 共用业务 summary。"""

from __future__ import annotations

import datetime

from tools.dashboard_release_items import (
    collect_release_months,
    effective_release_month,
    load_release_board,
    release_display_state,
    valid_iso_date,
)
from tools.requirements import load_requirements, systems_display_text, test_points_progress


_OPEN_STATUSES = frozenset({'已完成', 'done', 'closed', '已关闭', '已上线'})


def _as_date(today) -> datetime.date:
    if isinstance(today, datetime.date):
        return today
    if today:
        parsed = valid_iso_date(today)
        if parsed:
            return datetime.date.fromisoformat(parsed)
    return datetime.date.today()


def weekdays_mon_fri(today: datetime.date) -> list[datetime.date]:
    monday = today - datetime.timedelta(days=today.weekday())
    return [monday + datetime.timedelta(days=i) for i in range(5)]


def auto_release_target_date(requirements, today: datetime.date) -> str:
    month = today.strftime('%Y-%m')
    candidates = []
    for item in requirements or []:
        if not isinstance(item, dict):
            continue
        if valid_iso_date(item.get('actual_online_date')):
            continue
        planned = valid_iso_date(item.get('planned_online_date'))
        if not planned or not planned.startswith(month):
            continue
        if planned < today.isoformat():
            continue
        candidates.append(planned)
    return min(candidates) if candidates else ''


def resolve_release_countdown(requirements, board, today: datetime.date) -> dict:
    manual = valid_iso_date((board or {}).get('release_target_date'))
    auto = auto_release_target_date(requirements, today)
    target = manual or auto
    if not target:
        return {
            'target_date': '',
            'days_left': None,
            'date_text': '计划日期待定',
            'countdown_state': 'unset',
            'source': 'none',
        }
    target_day = datetime.date.fromisoformat(target)
    delta = (target_day - today).days
    source = 'manual' if manual else 'auto'
    if delta > 0:
        state = 'future'
        date_text = f'计划 {target[5:]} 发布'
        days_left = delta
    elif delta == 0:
        state = 'today'
        date_text = f'计划 {target[5:]} 发布'
        days_left = 0
    else:
        state = 'overdue'
        date_text = f'已过期 {abs(delta)} 天'
        days_left = delta
    return {
        'target_date': target,
        'days_left': days_left,
        'date_text': date_text,
        'countdown_state': state,
        'source': source,
    }


def monthly_release_tasks(requirements, month: str, today: datetime.date) -> list[dict]:
    rows = []
    for item in requirements or []:
        if effective_release_month(item) != month:
            continue
        display = release_display_state(item, today=today)
        done_n, total_n = test_points_progress(item.get('test_points'))
        rows.append({
            'id': str(item.get('id') or ''),
            'code': str(item.get('code') or ''),
            'title': str(item.get('title') or item.get('code') or '未命名'),
            'system': systems_display_text(item, empty='未选系统'),
            'status': display['state'],
            'test_points': f'{done_n}/{total_n}',
            'planned_online_date': valid_iso_date(item.get('planned_online_date')),
            'actual_online_date': valid_iso_date(item.get('actual_online_date')),
            'done': bool(display['done']),
            'nav': 10,
        })
    rows.sort(key=lambda row: (row.get('planned_online_date') or '9999-12-31', row.get('title') or ''))
    return rows


def build_dashboard_summary(
    *,
    today=None,
    language: str = 'zh',
    username: str = 'Lihp',
    requirements=None,
    board=None,
    reports=None,
) -> dict:
    day = _as_date(today)
    zh = language == 'zh'
    if requirements is None:
        requirements = load_requirements()
    if board is None:
        board = load_release_board()
    if reports is None:
        from tools.daily_reports import load_reports
        reports = load_reports()

    hour = datetime.datetime.now().hour if today is None else 15
    if zh:
        greeting = '上午好' if hour < 12 else ('下午好' if hour < 18 else '晚上好')
        weekday = '一二三四五六日'[day.weekday()]
        date_line = f'今天是 {day.month} 月 {day.day} 日 星期{weekday} · 本地数据已同步'
    else:
        greeting = 'Good afternoon' if hour < 18 else 'Good evening'
        date_line = f'{day.isoformat()} · Local data synced'

    open_reqs = [
        item for item in requirements
        if str(item.get('status') or '') not in _OPEN_STATUSES
    ]
    week = weekdays_mon_fri(day)
    keys = set(reports.keys()) if isinstance(reports, dict) else set()
    daily_done = sum(1 for d in week if d.isoformat() in keys)
    today_key = day.isoformat()
    if day.weekday() < 5:
        daily_note = '今日已完成' if today_key in keys else '今日未填写'
    else:
        daily_note = '今日已完成' if today_key in keys else '周末'

    month = day.strftime('%Y-%m')
    month_tasks = monthly_release_tasks(requirements, month, day)
    total = len(month_tasks)
    done = sum(1 for row in month_tasks if row.get('done'))
    countdown = resolve_release_countdown(requirements, board, day)

    recent = []
    ordered = sorted(
        requirements,
        key=lambda item: str(item.get('updated_at') or item.get('created_at') or ''),
        reverse=True,
    )
    for item in ordered[:8]:
        status = str(item.get('status') or '进行中')
        cls = 'ok' if status in ('已完成', '已上线') else ('rev' if '评审' in status else 'run')
        recent.append({
            'code': str(item.get('code') or item.get('id') or ''),
            'title': str(item.get('title') or item.get('name') or '未命名需求'),
            'status': cls,
            'color': {'run': '#F59E0B', 'rev': '#3B82F6', 'ok': '#10B981'}.get(cls, '#C9CCDD'),
            'nav': 10,
        })

    tools = [
        {'i': 14, 'zh': '数据中心', 'ds': '6 类数据库 · AI 助手', 'icon': 'db', 'grad': 'c2'},
        {'i': 16, 'zh': '模型对话', 'ds': '内网模型 · 聊天/工作', 'icon': 'chat', 'grad': 'c1'},
        {'i': 11, 'zh': '格式工具', 'ds': 'JSON / XML / SQL', 'icon': 'braces', 'grad': 'c4'},
        {'i': 12, 'zh': '接口排查', 'ds': '多浏览器实时抓包', 'icon': 'plug', 'grad': 'c3'},
    ]
    return {
        'username': username or 'Lihp',
        'greeting': greeting,
        'date_line': date_line,
        'stats': {
            'req_open': len(open_reqs),
            'req_trend': f'共 {len(requirements)} 条',
            'daily_done': daily_done,
            'daily_total': 5,
            'daily_note': daily_note,
        },
        'release': {
            'version': 'RELEASE',
            'total': total,
            'done': done,
            'percent': int(done * 100 / total) if total else 0,
            'days_left': countdown['days_left'],
            'date_text': countdown['date_text'],
            'countdown_state': countdown['countdown_state'],
            'target_date': countdown['target_date'],
        },
        'recent': recent,
        'checklist': [
            {
                't': '本月升级任务',
                'color': '#10B981' if done == total and total else '#E4E1EC',
                'mini': f'{done}/{total} 项',
            },
        ],
        'tools': tools,
        'monthly_release_tasks': month_tasks,
        'release_months': collect_release_months(requirements),
    }
