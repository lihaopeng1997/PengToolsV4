# -*- coding: utf-8 -*-
"""SSH 本地命令历史（仅本机 data/，限长防占内存）。"""
from __future__ import annotations

import json
import os
from datetime import datetime

from config import ensure_config_dir, local_data_dir

HISTORY_FILE = os.path.join(local_data_dir(), "ops_ssh_cmd_history.json")
MAX_ITEMS = 200


def _path() -> str:
    return HISTORY_FILE


def load_history() -> list[dict]:
    try:
        with open(_path(), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if isinstance(item, dict) and str(item.get("cmd") or "").strip():
                out.append({
                    "cmd": str(item.get("cmd") or "").strip(),
                    "ts": str(item.get("ts") or ""),
                    "host": str(item.get("host") or ""),
                })
        return out[-MAX_ITEMS:]
    except (OSError, ValueError, TypeError):
        return []


def save_history(items: list[dict]) -> None:
    ensure_config_dir()
    cleaned = []
    for item in items[-MAX_ITEMS:]:
        cmd = str((item or {}).get("cmd") or "").strip()
        if not cmd:
            continue
        cleaned.append({
            "cmd": cmd[:2000],
            "ts": str((item or {}).get("ts") or ""),
            "host": str((item or {}).get("host") or "")[:80],
        })
    with open(_path(), "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "items": cleaned}, stream, ensure_ascii=False, indent=2)


def append_command(cmd: str, *, host: str = "") -> list[dict]:
    text = str(cmd or "").strip()
    if not text:
        return load_history()
    items = load_history()
    if items and items[-1].get("cmd") == text:
        return items
    items.append({
        "cmd": text[:2000],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "host": str(host or "")[:80],
    })
    if len(items) > MAX_ITEMS:
        items = items[-MAX_ITEMS:]
    save_history(items)
    return items


def command_list() -> list[str]:
    return [str(i.get("cmd") or "") for i in load_history() if i.get("cmd")]
