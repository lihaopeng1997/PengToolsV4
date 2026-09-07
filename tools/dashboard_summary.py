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
        rel_date = valid_iso_date(item.get('actual_release_date')) or valid_iso_date(item.get('actual_online_date'))
        if not rel_date or not rel_date.startswith(month):
            continue
        if rel_date < today.isoformat():
            continue
        candidates.append(rel_date)
    return min(candidates) if candidates else ''


def resolve_release_countdown(requirements, board, today: datetime.date) -> dict:
    manual = valid_iso_date((board or {}).get('release_target_date'))
    auto = auto_release_target_date(requirements, today)
    target = manual or auto
    month = today.strftime('%Y-%m')
    if not target:
        return {
            'target_date': '',
            'days_left': None,
            'date_text': f'{month} 上线任务',
            'countdown_state': 'unset',
            'source': 'none',
        }
    target_day = datetime.date.fromisoformat(target)
    delta = (target_day - today).days
    source = 'manual' if manual else 'auto'
    if delta > 0:
        state = 'future'
        date_text = f'上线日 {target[5:]}'
        days_left = delta
    elif delta == 0:
        state = 'today'
        date_text = f'今日上线 ({target[5:]})'
        days_left = 0
    else:
        state = 'overdue'
        date_text = f'已超期 {abs(delta)} 天 ({target[5:]})'
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
        actual_date = valid_iso_date(item.get('actual_release_date')) or valid_iso_date(item.get('actual_online_date'))
        rows.append({
            'id': str(item.get('id') or ''),
            'code': str(item.get('code') or ''),
            'title': str(item.get('title') or item.get('code') or '未命名'),
            'system': systems_display_text(item, empty='未选系统'),
            'status': display['state'],
            'test_points': f'{done_n}/{total_n}',
            'actual_release_date': actual_date,
            'actual_online_date': actual_date,
            'done': bool(display['done']),
            'nav': 10,
        })
    rows.sort(key=lambda row: (row.get('actual_release_date') or '9999-12-31', row.get('title') or ''))
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
    month = day.strftime('%Y-%m')
    is_demo = False
    if requirements is None:
        loaded = load_requirements()
        if not loaded:
            is_demo = True
            mock_seeds = [
                {
                    'id': 'demo-req-0912',
                    'code': 'DEMO-0912',
                    'title': '【示例】车险承保规则调整',
                    'system': '车险承保',
                    'status': '已完成',
                    'actual_release_date': f'{month}-12',
                    'actual_online_date': f'{month}-12',
                    'test_points': [{'id': str(i), 'text': f'规则测试点{i}', 'done': True} for i in range(1, 9)],
                    'updated_at': f'{month}-06T15:30:00',
                    'is_demo': True,
                },
                {
                    'id': 'demo-req-0918',
                    'code': 'DEMO-0918',
                    'title': '【示例】ECIF 客户查询优化',
                    'system': '客户中心',
                    'status': '测试中',
                    'actual_release_date': f'{month}-18',
                    'actual_online_date': f'{month}-18',
                    'test_points': [{'id': str(i), 'text': f'查询测试点{i}', 'done': i <= 5} for i in range(1, 8)],
                    'updated_at': f'{month}-05T14:20:00',
                    'is_demo': True,
                },
                {
                    'id': 'demo-req-0926',
                    'code': 'DEMO-0926',
                    'title': '【示例】监管接口字段升级',
                    'system': '监管报送',
                    'status': '开发中',
                    'actual_release_date': f'{month}-26',
                    'actual_online_date': f'{month}-26',
                    'test_points': [{'id': str(i), 'text': f'接口测试点{i}', 'done': i <= 2} for i in range(1, 7)],
                    'updated_at': f'{month}-04T11:10:00',
                    'is_demo': True,
                },
            ]
            demo_items = mock_seeds
            requirements = []
        else:
            requirements = list(loaded)
            demo_items = []
    else:
        demo_items = []

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

    display_items = demo_items if is_demo else requirements
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

    real_month_tasks = monthly_release_tasks(requirements, month, day)
    display_month_tasks = monthly_release_tasks(display_items, month, day)
    total = len(real_month_tasks)
    done = sum(1 for row in real_month_tasks if row.get('done'))
    countdown = resolve_release_countdown(display_items, board, day)

    recent = []
    ordered = sorted(
        display_items,
        key=lambda item: str(item.get('updated_at') or item.get('created_at') or ''),
        reverse=True,
    )
    for item in ordered[:8]:
        status = str(item.get('status') or '进行中')
        cls = 'ok' if status in ('已完成', '已上线') else ('rev' if '评审' in status else 'run')
        done_n, total_n = test_points_progress(item.get('test_points'))
        actual_date = valid_iso_date(item.get('actual_release_date')) or valid_iso_date(item.get('actual_online_date'))
        recent.append({
            'id': str(item.get('id') or ''),
            'code': str(item.get('code') or item.get('id') or ''),
            'title': str(item.get('title') or item.get('name') or '未命名需求'),
            'system': systems_display_text(item, empty='未选系统'),
            'actual_release_date': actual_date,
            'actual_online_date': actual_date,
            'test_points': f'{done_n}/{total_n}' if total_n else '',
            'status': cls,
            'status_label': status,
            'color': {'run': '#F59E0B', 'rev': '#3B82F6', 'ok': '#10B981'}.get(cls, '#C9CCDD'),
            'done': status in ('已完成', '已上线'),
            'nav': 10,
            'is_demo': bool(item.get('is_demo')),
        })

    tools = [
        {'i': 18, 'zh': '数据中心', 'ds': '6 类数据库 · AI 助手', 'icon': 'db', 'grad': 'c2'},
        {'i': 16, 'zh': '模型对话', 'ds': '内网模型 · 聊天/工作', 'icon': 'chat', 'grad': 'c1'},
        {'i': 11, 'zh': '格式工具', 'ds': 'JSON / XML / SQL', 'icon': 'braces', 'grad': 'c4'},
        {'i': 12, 'zh': '接口排查', 'ds': '多浏览器实时抓包', 'icon': 'plug', 'grad': 'c3'},
    ]
    demo_month_total = len(display_month_tasks)
    demo_month_done = sum(1 for row in display_month_tasks if row.get('done'))
    return {
        'username': username or 'Lihp',
        'greeting': greeting,
        'date_line': date_line,
        'stats': {
            'req_open': len(open_reqs),
            'req_trend': '暂无真实需求（显示示例）' if is_demo else f'共 {len(requirements)} 条',
            'daily_done': daily_done,
            'daily_total': 5,
            'daily_note': daily_note,
            'monthly_release_total': total,
            'monthly_release_done': done,
            'completed_total': sum(1 for item in requirements if str(item.get('status') or '') in _OPEN_STATUSES),
            'is_demo': is_demo,
        },
        'release': {
            'version': 'RELEASE',
            'total': total if not is_demo else demo_month_total,
            'done': done if not is_demo else demo_month_done,
            'percent': int(done * 100 / total) if total else (
                int(demo_month_done * 100 / demo_month_total) if (is_demo and demo_month_total) else 0
            ),
            'days_left': countdown['days_left'],
            'date_text': countdown['date_text'],
            'countdown_state': countdown['countdown_state'],
            'target_date': countdown['target_date'],
        },
        'recent': recent,
        'checklist': [
            {
                't': '本月上线任务',
                'color': '#10B981' if ((done == total and total) or (is_demo and demo_month_done == demo_month_total and demo_month_total)) else '#E4E1EC',
                'mini': f'{done}/{total} 项' if not is_demo else f'{demo_month_done}/{demo_month_total} 项',
            },
        ],
        'tools': tools,
        'monthly_release_tasks': display_month_tasks,
        'release_months': collect_release_months(display_items),
    }
