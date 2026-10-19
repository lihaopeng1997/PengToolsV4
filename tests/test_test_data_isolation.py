# -*- coding: utf-8 -*-
"""回归：测试进程不得写真实 data/（曾把 ui_web_shell=false、侧栏展开状态写进用户配置）。

隔离由 tests/_data_sandbox.py 在 tests 包导入时完成；本文件验证它确实生效，
并走一遍 MainWindow + 设置页的真实保存链路，确认落盘只发生在沙箱。
"""

import json
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from tests import _data_sandbox

try:
    from PyQt6 import QtWebEngineWidgets  # noqa: F401  # 须先于 QApplication 导入
except ImportError:
    pass

try:
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


def _real_data_snapshot():
    """真实数据目录的顶层条目 + 每个被重定向配置文件的 (size, mtime_ns)。"""
    real_dir = _data_sandbox.REAL_DATA_DIR
    try:
        entries = sorted(os.listdir(real_dir))
    except OSError:
        entries = None
    files = {}
    for name, path in _data_sandbox.REAL_PATHS.items():
        try:
            stat = os.stat(path)
        except OSError:
            files[name] = None
        else:
            files[name] = None if os.path.isdir(path) else (stat.st_size, stat.st_mtime_ns)
    return entries, files


class DataSandboxActivationTests(unittest.TestCase):
    def test_sandbox_is_active_and_outside_real_data_dir(self):
        real_dir = _data_sandbox.REAL_DATA_DIR
        sandbox = _data_sandbox.SANDBOX_DIR
        self.assertTrue(real_dir and sandbox, 'tests 包未激活数据沙箱')
        self.assertFalse(_data_sandbox.is_under(sandbox, real_dir))
        self.assertEqual(config.CONFIG_DIR, sandbox)
        self.assertEqual(config.local_data_dir(), sandbox)

    def test_every_config_path_constant_is_redirected(self):
        real_dir = _data_sandbox.REAL_DATA_DIR
        for name in ('SETTINGS_FILE', 'LAST_CHOICES_FILE', 'AGENT_INDEX_FILE', 'DATA_CENTER_WORKSPACE_FILE'):
            self.assertIn(name, _data_sandbox.REAL_PATHS)
        for name, value in vars(config).items():
            if name.isupper() and isinstance(value, str) and value:
                self.assertFalse(
                    _data_sandbox.is_under(value, real_dir),
                    f'config.{name} 仍指向真实数据目录: {value}',
                )

    def test_explicit_packaging_arguments_keep_original_rules(self):
        exe = os.path.join(ROOT, 'dist', 'PengToolsHub.exe')
        self.assertEqual(config.local_data_dir(exe, True), os.path.join(os.path.dirname(exe), 'data'))
        self.assertEqual(
            _data_sandbox._norm(config.local_data_dir(frozen=False)),
            _data_sandbox._norm(_data_sandbox.REAL_DATA_DIR),
        )


@unittest.skipUnless(QT_AVAILABLE, 'PyQt6 missing')
class MainWindowSettingsRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        sandbox = _data_sandbox.SANDBOX_DIR
        if not sandbox or not _data_sandbox.is_under(config.SETTINGS_FILE, sandbox):
            self.fail('数据沙箱未生效，拒绝在真实 data/ 上运行保存链路')
        self.real_before = _real_data_snapshot()
        # 原生壳更轻、更确定；保存链路与 Web 壳相同。此写入本身也必须落在沙箱。
        config.save_settings(dict(config.DEFAULT_SETTINGS, ui_web_shell=False))
        self.window = None

    def tearDown(self):
        win = self.window
        if win is not None:
            try:
                if win.hotkey_service:
                    win.hotkey_service.unregister()
                if win.quick_panel is not None:
                    win.quick_panel.close_toolbar()
                    win.quick_panel.close()
                    win.quick_panel.deleteLater()
                if win.tray_service is not None:
                    win.tray_service.hide()
                if win.keep_awake_service is not None:
                    win.keep_awake_service.stop()
                win.hide()
                win.deleteLater()
            except Exception:
                pass
            # 不能 win.close()：MainWindow._shutdown 会 QApplication.quit()。
            for _ in range(5):
                self.app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _read_sandbox_settings(self):
        with open(config.SETTINGS_FILE, encoding='utf-8') as stream:
            return json.load(stream)

    def test_sidebar_toggle_and_settings_save_stay_in_sandbox(self):
        from main_window import MainWindow

        self.window = MainWindow()
        expanded_before = bool(self.window._ai_expanded)
        self.window._toggle_ai_group()
        self.assertEqual(self._read_sandbox_settings()['sidebar_expanded_ai'], not expanded_before)

        panel = self.window._ensure_settings_panel()
        new_size = 15 if panel.font_size.value() != 15 else 14
        panel.font_size.setValue(new_size)
        panel._save()
        for _ in range(50):
            if not panel._settings_save_in_progress:
                break
            self.app.processEvents()
        self.assertEqual(self._read_sandbox_settings()['font_size'], new_size)

        self.assertTrue(_data_sandbox.is_under(config.SETTINGS_FILE, _data_sandbox.SANDBOX_DIR))
        self.assertEqual(_real_data_snapshot(), self.real_before, '测试写入了真实 data/ 目录')


if __name__ == '__main__':
    unittest.main()
