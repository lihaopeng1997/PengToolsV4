# -*- coding: utf-8 -*-
"""Qt signal regressions for home requirement navigation failures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


_CHILD = r'''
import json
import os
from pathlib import Path
import sys

ROOT = Path(os.environ["PENGTOOLS_HOME_CALLBACK_REPO_ROOT"])
TEMP_ROOT = Path(os.environ["PENGTOOLS_HOME_CALLBACK_TEMP_ROOT"])
MODE = os.environ["PENGTOOLS_HOME_CALLBACK_MODE"]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

config_root = TEMP_ROOT / "config"
config_root.mkdir(parents=True, exist_ok=True)
import config
config.local_data_dir = lambda executable=None, frozen=None: str(config_root)
config.CONFIG_DIR = str(config_root)
for name, value in tuple(vars(config).items()):
    if name == "CONFIG_DIR":
        continue
    if name.endswith("_FILE"):
        setattr(config, name, str(config_root / (name.lower() + ".json")))
    elif name.endswith("_DIR") and isinstance(value, str):
        setattr(config, name, str(config_root / name.lower()))

from PyQt6.QtCore import QCoreApplication, QTimer
app = QCoreApplication(["PengTools-Home-Callback-Probe"])
from main_window import MainWindow
from ui.web_shell import HomeBridge


class StatusBarProbe:
    def __init__(self, events):
        self.events = events
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.events.append("status")
        self.messages.append({"text": str(text), "timeout": int(timeout)})


class PanelProbe:
    def __init__(self, events):
        self.events = events
        self.add_calls = 0
        self.focus_ids = []
        self.focus_sections = []

    def _add_requirement(self):
        self.add_calls += 1
        self.events.append("add")

    def focus_requirement(self, req_id):
        self.focus_ids.append(str(req_id))
        self.events.append("focus:" + str(req_id))

    def focus_requirement_section(self, req_id, section):
        self.focus_sections.append([str(req_id), str(section)])
        self.events.append("focus-section:" + str(req_id) + ":" + str(section))
        return True


class HostProbe:
    def __init__(self, fail_constructor):
        self.events = []
        self.status_bar = StatusBarProbe(self.events)
        self.panel = PanelProbe(self.events)
        self.requirement_panel = None
        self.fail_constructor = fail_constructor
        self.ensure_attempts = 0
        self.show_calls = []

    def _ensure_requirement_panel(self):
        self.ensure_attempts += 1
        self.events.append("ensure")
        if self.fail_constructor:
            raise RuntimeError("SYNTHETIC_PANEL_CONSTRUCTION_FAILURE")
        self.requirement_panel = self.panel
        return self.panel

    def _show_panel(self, index):
        self.show_calls.append(int(index))
        self.events.append("show:" + str(index))
        try:
            self._ensure_requirement_panel()
        except Exception as exc:
            self.status_bar.showMessage(f"打开页面失败：{exc}", 5000)
            return False
        return True


def _route(host, action):
    bridge = HomeBridge()
    returned = []
    section_result = []
    if action == "create":
        bridge.createRequirementRequested.connect(
            lambda: (MainWindow._on_web_create_requirement(host), returned.append(True))
        )
        bridge.createRequirement()
    elif action == "open":
        bridge.openRequirementRequested.connect(
            lambda req_id: (MainWindow._on_web_open_requirement(host, req_id), returned.append(True))
        )
        bridge.openRequirement("REQ-PROBE-OPEN")
    elif action == "section":
        bridge.openRequirementSectionRequested.connect(
            lambda req_id, section: (
                section_result.append(
                    MainWindow._on_web_open_requirement_section(host, req_id, section)
                ),
                returned.append(True),
            )
        )
        bridge.openRequirementSection("REQ-PROBE-SECTION", "completion")
    else:
        raise ValueError("unknown probe action")
    return {
        "signal_returned": bool(returned),
        "section_result": section_result[0] if section_result else None,
        "ensure_attempts": host.ensure_attempts,
        "show_calls": host.show_calls,
        "add_calls": host.panel.add_calls,
        "focus_ids": host.panel.focus_ids,
        "focus_sections": host.panel.focus_sections,
        "status_messages": host.status_bar.messages,
        "events": host.events,
    }


payload = {"mode": MODE, "cases": {}}
def run_probe():
    if MODE == "failure":
        for action in ("create", "open", "section"):
            payload["cases"][action] = _route(HostProbe(fail_constructor=True), action)
    elif MODE == "normal":
        for action in ("create", "open", "section"):
            payload["cases"][action] = _route(HostProbe(fail_constructor=False), action)
    else:
        raise ValueError("unknown probe mode")
    QTimer.singleShot(0, app.quit)

QTimer.singleShot(0, run_probe)
QTimer.singleShot(5000, app.quit)
payload["event_loop_exit_code"] = app.exec()
payload["config_file_count"] = sum(1 for path in config_root.rglob("*") if path.is_file())
print("HOME_REQUIREMENT_NAVIGATION_PROBE_OK", flush=True)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
'''


class HomeRequirementNavigationFailureProcessTest(unittest.TestCase):
    def _run_probe(self, mode: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="pengtools-home-navigation-") as temp_root:
            env = os.environ.copy()
            env.update({
                "QT_QPA_PLATFORM": "offscreen",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                "PENGTOOLS_HOME_CALLBACK_REPO_ROOT": str(ROOT),
                "PENGTOOLS_HOME_CALLBACK_TEMP_ROOT": temp_root,
                "PENGTOOLS_HOME_CALLBACK_MODE": mode,
            })
            env.pop("PENGTOOLS_SYNC_BOOT", None)
            try:
                completed = subprocess.run(
                    [sys.executable, "-B", "-c", _CHILD],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=25,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self.fail(f"{mode} Qt callback subprocess did not exit within its bound")

        has_traceback = "Traceback (most recent call last)" in completed.stderr
        has_unhandled_synthetic_error = bool(
            re.search(r"RuntimeError:\s*SYNTHETIC_PANEL_CONSTRUCTION_FAILURE", completed.stderr)
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"{mode} subprocess exit={completed.returncode}; "
            f"traceback={has_traceback}; synthetic_exception_reported={has_unhandled_synthetic_error}",
        )
        self.assertIn("HOME_REQUIREMENT_NAVIGATION_PROBE_OK", completed.stdout)
        payload_line = completed.stdout.strip().splitlines()[-1]
        payload = json.loads(payload_line)
        self.assertEqual(payload["mode"], mode)
        self.assertEqual(payload["event_loop_exit_code"], 0)
        self.assertFalse(has_traceback)
        self.assertFalse(has_unhandled_synthetic_error)
        self.assertEqual(payload["config_file_count"], 0)
        return payload["cases"]

    def test_constructor_failure_keeps_all_home_requirement_signals_alive(self):
        cases = self._run_probe("failure")

        for action in ("create", "open", "section"):
            with self.subTest(action=action):
                case = cases[action]
                self.assertTrue(case["signal_returned"])
                self.assertEqual(case["ensure_attempts"], 1)
                self.assertEqual(case["add_calls"], 0)
                self.assertEqual(case["focus_ids"], [])
                self.assertEqual(case["focus_sections"], [])
                self.assertEqual(
                    case["status_messages"],
                    [{"text": "打开页面失败：SYNTHETIC_PANEL_CONSTRUCTION_FAILURE", "timeout": 5000}],
                )
        self.assertEqual(cases["create"]["show_calls"], [10])
        self.assertEqual(cases["create"]["section_result"], None)
        self.assertEqual(cases["open"]["show_calls"], [10])
        self.assertEqual(cases["open"]["section_result"], None)
        self.assertEqual(cases["section"]["show_calls"], [])
        self.assertFalse(cases["section"]["section_result"])

    def test_successful_home_actions_use_mounted_panel_and_focus_before_navigation(self):
        cases = self._run_probe("normal")

        create = cases["create"]
        self.assertTrue(create["signal_returned"])
        self.assertEqual(create["ensure_attempts"], 1)
        self.assertEqual(create["show_calls"], [10])
        self.assertEqual(create["add_calls"], 1)

        opened = cases["open"]
        self.assertTrue(opened["signal_returned"])
        self.assertEqual(opened["ensure_attempts"], 1)
        self.assertEqual(opened["show_calls"], [10])
        self.assertEqual(opened["focus_ids"], ["REQ-PROBE-OPEN"])

        section = cases["section"]
        self.assertTrue(section["signal_returned"])
        self.assertTrue(section["section_result"])
        self.assertEqual(section["ensure_attempts"], 2)
        self.assertEqual(section["focus_sections"], [["REQ-PROBE-SECTION", "completion"]])
        self.assertEqual(section["show_calls"], [10])
        self.assertLess(
            section["events"].index("focus-section:REQ-PROBE-SECTION:completion"),
            section["events"].index("show:10"),
        )


if __name__ == "__main__":
    unittest.main()
