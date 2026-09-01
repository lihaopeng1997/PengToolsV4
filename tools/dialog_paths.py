# -*- coding: utf-8 -*-
import os
from typing import Sequence

_LAST_CHOICES_SECTION = 'dialog_paths'


def get_dialog_start_dir(purpose: str, fallback: str = '') -> str:
    key = str(purpose or '').strip()
    if key:
        try:
            from config import load_last_choices
            data = load_last_choices().get(_LAST_CHOICES_SECTION) or {}
            saved = data.get(key)
            if saved and isinstance(saved, str):
                cleaned = os.path.abspath(saved.strip())
                if os.path.isdir(cleaned):
                    return cleaned
        except Exception:
            pass

    fb = str(fallback or '').strip()
    if fb:
        if os.path.isdir(fb):
            return os.path.abspath(fb)
        parent = os.path.dirname(fb)
        if parent and os.path.isdir(parent):
            return os.path.abspath(parent)

    try:
        home = os.path.expanduser('~')
        if os.path.isdir(home):
            return os.path.abspath(home)
    except Exception:
        pass
    return ''


def get_dialog_save_path(purpose: str, default_filename: str = '', fallback_dir: str = '') -> str:
    directory = get_dialog_start_dir(purpose, fallback=fallback_dir)
    filename = os.path.basename(str(default_filename or '').strip())
    if filename:
        return os.path.join(directory, filename) if directory else filename
    return directory


def remember_dialog_path(
    purpose: str,
    selected_path: str | Sequence[str] | None,
    is_directory: bool = False,
) -> str | None:
    key = str(purpose or '').strip()
    if not key:
        return None

    target = ''
    if isinstance(selected_path, (list, tuple)):
        for item in selected_path:
            if item and isinstance(item, str) and item.strip():
                target = item.strip()
                break
    elif isinstance(selected_path, str):
        target = selected_path.strip()

    if not target:
        return None

    if is_directory:
        saved_dir = os.path.abspath(target)
    else:
        saved_dir = os.path.abspath(os.path.dirname(target))

    if not saved_dir or not os.path.isdir(saved_dir):
        return None

    try:
        from config import load_last_choices, update_last_choices
        data = load_last_choices().get(_LAST_CHOICES_SECTION) or {}
        if not isinstance(data, dict):
            data = {}
        data[key] = saved_dir
        update_last_choices(**{_LAST_CHOICES_SECTION: data})
        return saved_dir
    except Exception:
        return None
