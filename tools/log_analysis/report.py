"""Offline editable report drafts and quota-limited explicit cache storage."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time


def stamp(value):
    if not value:
        return '无'
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(timespec='seconds')
    except (ValueError, OSError, OverflowError):
        return '时间不可用'


def report(snapshot, selected=None):
    events = [e for e in snapshot['events'] if selected is None or e['id'] in selected]
    lines = ['# 日志排查报告', '', '## 排查范围',
             f"- 模式：{snapshot.get('mode', 'live')}",
             f"- 目标：{snapshot['label']} / {snapshot['service']}", f"- 文件：{snapshot['path']}",
             f"- 开始观察：{stamp(snapshot['started'])}", f'- 生成时间：{stamp(time.time())}',
             '- 时间均为本机观察时间；日志时间及服务端时区未经核验。',
             '- 仅覆盖当前任务采集且保留的片段，不代表完整文件或完整业务影响。', '',
             f'## 异常统计（{len(events)} 组）', '相同异常在60秒内合并；次数为观察到的异常起始次数。', '']
    for e in events:
        lines += [f"### {e['id']} · {e['title']}", f"文件：{e.get('source') or snapshot['path'] or '系统指标'}",
                  f"次数：{e['count']}；首次：{stamp(e['first_seen'])}；最近：{stamp(e['last_seen'])}",
                  f"来源：文件代次 {e['generation']}，字节偏移 {e['offset']}；截断：{e['truncated']}",
                  '证据（脱敏）：', '']
        lines += ['    ' + line for line in e['evidence'].splitlines()]
        lines += ['', '#### 分析与建议']
        for analysis in e.get('analyses', []):
            lines += [f"版本时间：{stamp(analysis['time'])}；模型：{analysis['model']}", analysis['text'], '']
        if not e.get('analyses'):
            lines += ['尚未分析，不能据此确认根因。', '']
    lines += ['## 覆盖限制', f"状态：{snapshot['state']}；积压 {snapshot['backlog']} 字节；"
              f"内存淘汰 {snapshot['dropped_events']} 组；超长行截断 {snapshot['truncated_lines']} 次。",
              'SFTP 文件身份仅作锚点检测，无法保证轮转或断线间隙绝对不重不漏。']
    lines += snapshot.get('notices', [])
    if snapshot.get('sources'):
        lines += ['', '## 文件读取范围', '```json', json.dumps(snapshot['sources'], ensure_ascii=False, indent=2), '```']
    if snapshot.get('coverage'):
        lines += ['', '实际范围：' + snapshot['coverage']]
    if snapshot.get('time_filter', {}).get('start'):
        lines += ['', '日志时间筛选（使用用户指定时区，无法解析的行不计入时段统计）：', '```json',
                  json.dumps(snapshot['time_filter'], ensure_ascii=False, indent=2), '```']
    if snapshot.get('metrics'):
        lines += ['', '## 最近一次系统采样（不代表整个任务期间）', '```json',
                  json.dumps(snapshot['metrics'][-1], ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines)


class Store:
    def __init__(self, root, quota=200 * 1024 * 1024, days=7):
        self.root, self.quota, self.days = Path(root), quota, days

    def save(self, snapshot):
        self.root.mkdir(parents=True, exist_ok=True)
        name = str(snapshot['id'])
        if not name.isalnum():
            raise ValueError('INVALID_TASK_ID')
        target = self.root / (name + '.json')
        files = list(self.root.glob('*.json'))
        if any(time.time() - p.stat().st_mtime > self.days * 86400 for p in files):
            raise ValueError('缓存超过7天，请确认清理后继续持久化')
        data = {k: v for k, v in snapshot.items() if k != 'recent'}
        raw = json.dumps({'version': 1, 'task': data}, ensure_ascii=False).encode('utf-8')
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('当前任务证据超过单份缓存16 MiB上限，请分批分析并保存报告')
        if sum(p.stat().st_size for p in files if p != target) + len(raw) > self.quota:
            raise ValueError('缓存容量已满，持久化已暂停，请确认清理')
        temp = target.with_suffix('.tmp')
        temp.write_bytes(raw)
        temp.replace(target)

    def load(self):
        result = []
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                if path.stat().st_size <= 16 * 1024 * 1024:
                    obj = json.loads(path.read_text('utf-8'))
                    if obj.get('version') == 1:
                        result.append(obj['task'])
            except (OSError, ValueError, KeyError):
                continue
        return result

    def clear(self):
        # Only our direct task files; caller must have obtained confirmation.
        for path in self.root.glob('*.json'):
            path.unlink()
