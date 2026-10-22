# -*- coding: utf-8 -*-
"""首页 V2 视觉原型：只读真实台账 → 生成离线单文件 index.html。

用法（仓库根目录）：python prototypes/home-v2/build.py
输出 prototypes/home-v2/index.html（含真实数据，已在 .gitignore 中，不提交）。
拷到内网用 Chrome/Edge 打开即可测流畅度；不写任何 data/ 文件。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from tools.dashboard_summary import build_dashboard_summary  # noqa: E402
from tools.home_actions import requirement_detail  # noqa: E402
from tools.requirements import load_requirements  # noqa: E402


def main() -> str:
    requirements = load_requirements()
    summary = build_dashboard_summary(language='zh', username='Lihp', tools=[])
    details = []
    for item in requirements:
        detail = requirement_detail(item)
        details.append({key: detail.get(key) for key in (
            'id', 'code', 'title', 'status', 'group', 'group_label', 'stage', 'priority', 'system',
            'actual_release_date', 'test_points', 'tp_done', 'tp_total', 'next_action', 'updated_at',
        )})
    payload = {
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'greeting': summary.get('greeting'),
        'username': summary.get('username'),
        'date_line': summary.get('date_line'),
        'stats': summary.get('stats') or {},
        'release': summary.get('release') or {},
        'progress': {k: v for k, v in (summary.get('progress') or {}).items() if k != 'active'},
        'requirements': details,
    }
    data = json.dumps(payload, ensure_ascii=False).replace('</', '<\\/')
    with open(os.path.join(HERE, 'template.html'), encoding='utf-8') as stream:
        html = stream.read()
    html = html.replace('/*__REAL_DATA__*/null', data)
    out = os.path.join(HERE, 'index.html')
    with open(out, 'w', encoding='utf-8') as stream:
        stream.write(html)
    return out


if __name__ == '__main__':
    print(main())
