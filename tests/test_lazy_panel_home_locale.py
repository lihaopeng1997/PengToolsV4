# -*- coding: utf-8 -*-
"""Cold-start locale checks for lazily created native page home buttons."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


_CHILD = r'''
import json
import os
from pathlib import Path
import socket
import sys
import traceback
from unittest.mock import patch

ROOT = Path(os.environ["PENGTOOLS_LOCALE_REPO_ROOT"])
TEMP_ROOT = Path(os.environ["PENGTOOLS_LOCALE_TEMP_ROOT"])
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.pop("PENGTOOLS_SYNC_BOOT", None)

NETWORK_ATTEMPTS = []
def blocked_connect(_sock, address):
    NETWORK_ATTEMPTS.append(repr(address))
    raise AssertionError("Lazy page locale regression forbids all socket connections")

def blocked_create_connection(address, *args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(address))
    raise AssertionError("Lazy page locale regression forbids create_connection")

socket.socket.connect = blocked_connect
socket.socket.connect_ex = blocked_connect
socket.create_connection = blocked_create_connection

from PyQt6.QtCore import QEvent, QStandardPaths, Qt
from PyQt6.QtWidgets import QApplication, QPushButton

QStandardPaths.setTestModeEnabled(True)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(["PengTools-Lazy-Panel-Home-Locale"])
app.setQuitOnLastWindowClosed(False)

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

from main_window import MainWindow

def check(condition, message):
    if not condition:
        raise AssertionError(message)

def home_buttons(window):
    found = []
    for panel in window._iter_created_panels():
        for button in panel.findChildren(QPushButton, "header-home-btn"):
            found.append(button)
    return found

def check_locale(window, language, expected_text, expected_tooltip):
    inventory = home_buttons(window)
    check(inventory, "created panel inventory must include header home buttons")
    for button in inventory:
        check(button.text() == expected_text,
              f"{language} home label mismatch: {button.text()!r}")
        check(button.toolTip() == expected_tooltip,
              f"{language} home tooltip mismatch: {button.toolTip()!r}")
    return len(inventory)

def run_checks():
    settings = dict(config.DEFAULT_SETTINGS)
    settings.update({"default_language": "en", "ui_web_shell": False})
    save_attempts = []
    def forbid_save(*args, **kwargs):
        save_attempts.append(True)
        raise AssertionError("locale smoke must not save settings or user data")

    with patch("main_window.load_settings", return_value=settings), \
         patch("main_window.save_settings", side_effect=forbid_save), \
         patch("main_window.log_web_event", return_value=None), \
         patch("main_window._web_shell.runtime_web_shell_available", return_value=False), \
         patch.object(MainWindow, "_complete_startup_sync", lambda self: None):
        window = MainWindow()
        check(window.language == "en", "window must cold-start in English from synthetic settings")
        check(window.docx_panel is None and window.gateway_panel is None,
              "Docx and Gateway must remain lazy before their routes are entered")
        check(window.tray_service is None and window.hotkey_service is None
              and window.keep_awake_service is None,
              "the test must not initialize tray, hotkey, or keep-awake services")

        window.show()
        app.processEvents()

        check(window._show_panel(3), "real Docx navigation route should create its panel")
        docx = window.docx_panel
        docx_button = docx.findChild(QPushButton, "header-home-btn")
        check(docx_button is not None, "Docx route must create its real header home button")
        check(docx_button.text() == "Home" and docx_button.toolTip() == "Return to Home",
              f"cold-start English Docx button is wrong: {docx_button.text()!r} / {docx_button.toolTip()!r}")

        check(window._show_panel(5), "real Gateway navigation route should create its panel")
        gateway = window.gateway_panel
        gateway_button = gateway.findChild(QPushButton, "header-home-btn")
        check(gateway_button is not None, "Gateway route must create its real header home button")
        check(gateway_button.text() == "Home" and gateway_button.toolTip() == "Return to Home",
              f"cold-start English Gateway button is wrong: {gateway_button.text()!r} / {gateway_button.toolTip()!r}")
        english_count = check_locale(window, "en", "Home", "Return to Home")

        window._set_language(0)
        chinese_count = check_locale(window, "zh", "返回首页", "返回首页")
        check(chinese_count == english_count,
              "language roundtrip must keep the same registered button inventory")

        window._set_language(1)
        final_english_count = check_locale(window, "en", "Home", "Return to Home")
        check(final_english_count == english_count,
              "restoring English must keep all registered home buttons localized")

        check(window._show_panel(3), "Docx route should still work after locale roundtrip")
        routed = []
        original_show_panel = window._show_panel
        def observe_navigation(index):
            routed.append(index)
            return original_show_panel(index)
        window._show_panel = observe_navigation
        docx_button.click()
        check(routed == [0] and window._current_nav_index == 0,
              f"Docx header home callback should route exactly once to Home: {routed!r}")
        check(window.docx_panel is docx and window.gateway_panel is gateway,
              "locale refresh and home navigation must preserve panel identities")

    check(NETWORK_ATTEMPTS == [], f"network connection attempted: {NETWORK_ATTEMPTS!r}")
    check(save_attempts == [], f"settings/data save attempted: {save_attempts!r}")
    config_files = [str(path) for path in config_root.rglob("*") if path.is_file()]
    check(not config_files, f"synthetic config root should remain write-free: {config_files!r}")
    return {
        "cold_start_language": "en",
        "lazy_routes": ["docx", "gateway"],
        "home_button_count": final_english_count,
        "language_roundtrip": ["en", "zh", "en"],
        "navigation_clicks": routed,
        "network_attempts": NETWORK_ATTEMPTS,
        "save_attempts": save_attempts,
        "config_root": str(config_root),
        "config_files": config_files,
    }

try:
    outcome = run_checks()
    outcome["evidence_dir"] = str(TEMP_ROOT)
    (TEMP_ROOT / "result.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("LAZY_PANEL_HOME_LOCALE_OK")
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
except Exception:
    traceback.print_exc()
    raise
'''


class LazyPanelHomeLocaleProcessTest(unittest.TestCase):
    def test_cold_start_english_lazy_routes_and_home_button_language_roundtrip(self):
        evidence_dir = Path(tempfile.mkdtemp(prefix="pengtools-lazy-home-locale-"))
        env = os.environ.copy()
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PENGTOOLS_LOCALE_REPO_ROOT": str(ROOT),
            "PENGTOOLS_LOCALE_TEMP_ROOT": str(evidence_dir),
        })
        env.pop("PENGTOOLS_SYNC_BOOT", None)
        completed = subprocess.run(
            [sys.executable, "-B", "-c", _CHILD],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=55,
            check=False,
        )
        transcript = completed.stdout + completed.stderr
        (evidence_dir / "test-process.log").write_text(transcript, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, transcript)
        self.assertIn("LAZY_PANEL_HOME_LOCALE_OK", completed.stdout)

        result = json.loads((evidence_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["cold_start_language"], "en")
        self.assertEqual(result["lazy_routes"], ["docx", "gateway"])
        self.assertEqual(result["language_roundtrip"], ["en", "zh", "en"])
        self.assertEqual(result["navigation_clicks"], [0])
        self.assertEqual(result["network_attempts"], [])
        self.assertEqual(result["save_attempts"], [])
        self.assertEqual(result["config_files"], [])
        self.assertEqual(Path(result["config_root"]), evidence_dir / "config")
        self.assertEqual(Path(result["evidence_dir"]), evidence_dir)


if __name__ == "__main__":
    unittest.main()
