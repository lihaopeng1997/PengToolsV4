# -*- coding: utf-8 -*-
import os
import sys


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def launch_ecif():
    app_dir = get_exe_dir()
    names = ['ECIF_DOCX更新工具.exe', 'ECIF_DOCX_Update_Tool.exe', 'ecif_docx_update_tool.exe']
    for name in names:
        path = os.path.join(app_dir, name)
        if os.path.exists(path):
            try:
                os.startfile(path)
                return True, 'ECIF tool launched'
            except Exception as e:
                return False, f'ECIF launch failed: {e}'
    return False, 'ECIF tool not found. Place ECIF executable in app directory.'
