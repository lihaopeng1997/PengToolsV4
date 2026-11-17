"""Read-only access to the log materials a run is allowed to see.

Two sources are permitted and nothing else:

* files the operator explicitly selected (may live outside the code root);
* files inside the authorized code root.

Selecting one outside file does not widen the code tools, and it does not grant
its parent directory. Every read is budgeted and reports its own truncation so
the UI can show "this is what the model actually received".
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MAX_MATERIAL_FILE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MaterialBudget:
    max_chars_per_file: int = 8000
    max_total_chars: int = 30000
    max_events_per_file: int = 200


@dataclass(frozen=True, slots=True)
class MaterialContent:
    path: str
    label: str
    text: str
    chars: int
    total_chars: int
    truncated: bool
    budget_chars: int
    events: tuple[dict, ...]

    def headline(self, zh: bool = True) -> str:
        if self.truncated:
            return (f'{self.label}（已截断：显示 {self.chars}/{self.total_chars} 字符）'
                    if zh else
                    f'{self.label} (truncated: {self.chars}/{self.total_chars} chars)')
        return f'{self.label}（{self.chars} 字符）' if zh else f'{self.label} ({self.chars} chars)'


def _within(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


class MaterialReader:
    """Resolve and read only the materials this run was granted."""

    def __init__(self, selected_paths, code_root: str, *, budget: MaterialBudget | None = None):
        self.budget = budget or MaterialBudget()
        self.code_root = os.path.realpath(os.path.abspath(code_root)) if code_root else ''
        selected = []
        for item in selected_paths or ():
            text = str(item or '').strip()
            if text:
                selected.append(os.path.realpath(os.path.abspath(text)))
        self.selected = tuple(dict.fromkeys(selected))

    # -- policy -----------------------------------------------------------
    def allowed(self, path: str) -> bool:
        resolved = os.path.realpath(os.path.abspath(str(path or '')))
        if not resolved:
            return False
        if resolved in self.selected:
            return True
        return bool(self.code_root) and _within(self.code_root, resolved)

    def describe(self) -> list[dict]:
        """Preview rows for the UI: what will be sent, and how much is cut."""
        rows = []
        remaining = self.budget.max_total_chars
        for path in self.selected:
            try:
                size = os.path.getsize(path)
            except OSError:
                rows.append({'path': path, 'ok': False, 'reason': 'unreadable'})
                continue
            budget = min(self.budget.max_chars_per_file, max(0, remaining))
            rows.append({
                'path': path,
                'label': os.path.basename(path),
                'bytes': size,
                'budget_chars': budget,
                'will_truncate': size > budget,
                'outside_root': bool(self.code_root) and not _within(self.code_root, path),
            })
            remaining -= budget
        return rows

    # -- read -------------------------------------------------------------
    def read(self, path: str, *, budget_chars: int | None = None) -> MaterialContent:
        target = str(path or '').strip()
        if not target:
            raise ValueError('材料路径为空')
        if not self.allowed(target):
            raise PermissionError('未选择该材料，只读排障不会读取')
        resolved = os.path.realpath(os.path.abspath(target))
        try:
            size = os.path.getsize(resolved)
        except OSError as exc:
            raise OSError(f'无法读取材料: {exc}') from exc
        if size > MAX_MATERIAL_FILE_BYTES:
            raise ValueError('材料文件超过 4 MiB，请先截取或选择更小的日志')

        with open(resolved, 'rb') as stream:
            raw = stream.read()
        text = raw.decode('utf-8', errors='replace')
        limit = min(budget_chars or self.budget.max_chars_per_file, self.budget.max_chars_per_file)
        page = text[:limit]
        events = []
        for number, line in enumerate(page.splitlines()[:self.budget.max_events_per_file], 1):
            events.append({
                'id': f'{os.path.basename(resolved)}:{number}',
                'line': number,
                'text': line[:300],
            })
        return MaterialContent(
            path=resolved,
            label=os.path.basename(resolved),
            text=page,
            chars=len(page),
            total_chars=len(text),
            truncated=len(page) < len(text),
            budget_chars=limit,
            events=tuple(events),
        )

    def read_all(self) -> list[MaterialContent]:
        """Read the granted materials under a shared total budget."""
        results = []
        remaining = self.budget.max_total_chars
        for path in self.selected:
            if remaining <= 0:
                break
            content = self.read(path, budget_chars=min(self.budget.max_chars_per_file, remaining))
            results.append(content)
            remaining -= content.chars
        return results

    def render_for_prompt(self, contents) -> str:
        """Model-facing block: real content plus an explicit truncation note."""
        blocks = []
        for item in contents or ():
            blocks.append(
                f'### 材料 {item.label}\n'
                f'路径: {item.path}\n'
                f'{item.headline()}\n'
                f'```\n{item.text}\n```'
            )
        return '\n\n'.join(blocks)
