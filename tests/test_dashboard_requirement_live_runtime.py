# -*- coding: utf-8 -*-
"""Isolated Windows-QPA runtime proof for dashboard requirement actions.

The test runs the real MainWindow, production Vue dashboard, HomeBridge,
RequirementDialog, and RequirementPanel test-point editor in a child process.
Only synthetic requirements and Qt WebEngine state are written under TEMP.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv-build" / "Scripts" / "python.exe"


_CHILD_SCRIPT = r'''
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import socket
import sys
import traceback
import uuid

ROOT = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
SCENARIO = sys.argv[3] if len(sys.argv) > 3 else "live"
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
os.environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)

result_path = EVIDENCE / "result.json"
result = {
    "status": "RUNNING",
    "scenario": SCENARIO,
    "qpa": os.environ.get("QT_QPA_PLATFORM"),
    "chromium_flags": os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"),
    "assertions": {},
    "events": {},
}


def write_result(status, error=None):
    result["status"] = status
    if error is not None:
        result["error"] = str(error)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


try:
    import config

    isolated_data = EVIDENCE / "application-data"
    original_config_dir = Path(config.CONFIG_DIR).resolve()
    original_local_data_dir = config.local_data_dir

    def isolated_local_data_dir(executable=None, frozen=None):
        return str(isolated_data)

    config.local_data_dir = isolated_local_data_dir
    config.CONFIG_DIR = str(isolated_data)
    for key, value in list(vars(config).items()):
        if not key.isupper() or not isinstance(value, str):
            continue
        try:
            suffix = Path(value).resolve().relative_to(original_config_dir)
        except (ValueError, OSError):
            continue
        setattr(config, key, str(isolated_data / suffix))
    isolated_data.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtCore import QCoreApplication, QEventLoop, QPoint, QRect, QStandardPaths, Qt, QTimer
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox
    from PyQt6.QtTest import QTest

    QStandardPaths.setTestModeEnabled(True)
    QCoreApplication.setOrganizationName("PengToolsRuntimeTest")
    QCoreApplication.setApplicationName("DashboardRequirementLiveRuntime")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(["PengTools-dashboard-live-runtime"])

    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile,
        QWebEngineUrlRequestInterceptor,
    )

    class LocalOnlyInterceptor(QWebEngineUrlRequestInterceptor):
        def __init__(self):
            super().__init__()
            self.blocked_urls = []

        def interceptRequest(self, info):
            url = info.requestUrl()
            if url.scheme().lower() not in {"file", "qrc", "about", "data"}:
                self.blocked_urls.append(url.toString())
                info.block(True)

    profile_root = EVIDENCE / "webengine-profile"
    cache_root = EVIDENCE / "webengine-cache"
    profile_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(str(profile_root))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    profile.setCachePath(str(cache_root))
    interceptor = LocalOnlyInterceptor()
    profile.setUrlRequestInterceptor(interceptor)
    result["webengine_storage"] = profile.persistentStoragePath()
    result["webengine_cache"] = profile.cachePath()
    result["webengine_cache_type"] = str(profile.httpCacheType())
    result["webengine_profile_off_the_record"] = bool(profile.isOffTheRecord())
    result["webengine_cache_root"] = str(cache_root)
    result["qt_cache_location"] = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    result["url_request_policy"] = {
        "allowed_schemes": ["file", "qrc", "about", "data"],
        "blocked_schemes": "all others",
    }

    from ui.web_diagnostics import set_log_dir, log_file_path
    set_log_dir(str(EVIDENCE / "webengine-logs"))
    result["webengine_log"] = log_file_path()

    python_socket_attempts = []

    def deny_socket_connect(sock, address):
        python_socket_attempts.append(repr(address))
        raise RuntimeError("all Python socket connections are disabled by dashboard runtime test")

    socket.socket.connect = deny_socket_connect
    socket.socket.connect_ex = deny_socket_connect

    import datetime as _datetime
    import uuid as _uuid
    from tools.requirements import load_requirements, save_requirements
    from tools.dashboard_release_items import release_display_state

    token = _uuid.uuid4().hex[:10].upper()
    target_id = f"REQ-SYNTH-DASHBOARD-{token}-TARGET"
    distractor_id = f"REQ-SYNTH-DASHBOARD-{token}-DISTRACTOR"
    target_code = f"SYN-{token}-T"
    distractor_code = f"SYN-{token}-D"
    today = _datetime.date.today()
    release_date = today.isoformat()
    initial_records = [
        {
            "id": target_id,
            "code": target_code,
            "title": "Synthetic dashboard target",
            "record_kind": "需求",
            "category": "功能需求",
            "status": "开发中",
            "system": "Synthetic system",
            "systems": ["Synthetic system"],
            "actual_release_date": release_date,
            "actual_online_date": release_date,
            "online_month": today.strftime("%Y-%m"),
            "test_points": [
                {"id": f"tp-{token}-target", "text": "Synthetic target point", "done": False}
            ],
        },
        {
            "id": distractor_id,
            "code": distractor_code,
            "title": "Synthetic dashboard distractor",
            "record_kind": "需求",
            "category": "功能需求",
            "status": "开发中",
            "system": "Synthetic system",
            "systems": ["Synthetic system"],
            "actual_release_date": release_date,
            "actual_online_date": release_date,
            "online_month": today.strftime("%Y-%m"),
            "test_points": [
                {"id": f"tp-{token}-distractor", "text": "Synthetic distractor point", "done": False}
            ],
        },
    ]
    save_requirements(initial_records if SCENARIO == "live" else [])
    result["expected_initial_display_status"] = {
        target_code: release_display_state(initial_records[0], today=today)["state"],
        distractor_code: release_display_state(initial_records[1], today=today)["state"],
    }
    result["synthetic_ids"] = {"target": target_id, "distractor": distractor_id} if SCENARIO == "live" else {}
    result["synthetic_codes"] = {"target": target_code, "distractor": distractor_code} if SCENARIO == "live" else {}
    result["requirements_file"] = config.REQUIREMENTS_FILE

    from unittest.mock import patch
    import main_window
    from main_window import MainWindow
    from panels.requirement_panel import RequirementDialog
    from panels.test_points_editor import TestPointRow

    settings = config.normalize_settings({
        **config.DEFAULT_SETTINGS,
        "default_language": "zh",
        "ui_web_shell": True,
        "floating_show_on_startup": False,
        "private_unlocked": False,
    })

    # Keep this probe on the real shell and real on-demand requirement path;
    # skip unrelated startup warm-up pages and services.
    patchers = [
        patch.object(main_window, "load_settings", return_value=settings),
        patch.object(main_window, "save_settings", side_effect=lambda value: value),
        patch.object(MainWindow, "_interactive_boot_steps", return_value=[]),
        patch.object(MainWindow, "_warmup_boot_steps", return_value=[]),
    ]
    for patcher in patchers:
        patcher.start()

    from ui import web_shell
    if not web_shell.WEB_SHELL_AVAILABLE:
        raise RuntimeError(f"PyQt6 WebEngine unavailable: {web_shell.WEB_SHELL_IMPORT_ERROR}")

    win = MainWindow()
    result["mainwindow_web_shell_enabled"] = bool(win._web_shell_enabled)
    if not win._web_shell_enabled or win._dash_web is None or win._dash_bridge is None:
        raise RuntimeError("MainWindow did not create the production dashboard bridge and WebEngine view")

    from ui.theme_manager import ThemeManager
    ThemeManager.instance().apply(app, "calm")

    section_requests = []
    requirement_requests = []
    summary_payloads = []
    load_results = []
    render_terminations = []
    requirement_saved = []
    requirements_changed = []
    win._dash_bridge.openRequirementSectionRequested.connect(
        lambda req_id, section: section_requests.append((str(req_id), str(section)))
    )
    win._dash_bridge.openRequirementRequested.connect(
        lambda req_id: requirement_requests.append(str(req_id))
    )
    win._dash_bridge.summaryChanged.connect(
        lambda raw: summary_payloads.append(json.loads(str(raw)))
    )
    win._dash_web.web_view.loadFinished.connect(lambda ok: load_results.append(bool(ok)))
    win._dash_web.web_page.renderProcessTerminated.connect(
        lambda status, code: render_terminations.append((str(status), str(code)))
    )

    state = {
        "trace": [],
        "dashboard_ready": False,
        "initial_dom": None,
        "page_nonce": f"probe-{token}",
        "completion_clicked": False,
        "completion_dialog_clicked": False,
        "completion_verified": False,
        "completion_returned_home": False,
        "test_points_clicked": False,
        "test_points_added": False,
        "test_points_toggle_completed": False,
        "test_points_action_index": 0,
        "test_points_action_triggered": False,
        "test_points_action_results": [],
        "native_test_point_editor_pre_action_captures": {},
        "test_points_verified": False,
        "test_points_returned_home": False,
        "finished": False,
        "modal_focus_checks": 0,
    }
    page = win._dash_web.web_page
    page_identity = id(page)
    app.setQuitOnLastWindowClosed(False)

    def write_and_quit(status, error=None):
        if state["finished"]:
            return
        state["finished"] = True
        result["events"] = {
            "open_requirements": list(requirement_requests),
            "open_requirement_sections": list(section_requests),
            "test_point_actions": list(state["test_points_action_results"]),
            "requirement_saved": requirement_saved,
            "requirements_changed_count": len(requirements_changed),
            "summary_changed_count": len(summary_payloads),
            "load_finished": load_results,
            "render_terminations": render_terminations,
            "blocked_external_requests": list(interceptor.blocked_urls),
            "blocked_python_socket_connects": list(python_socket_attempts),
            "allowed_url_schemes": ["file", "qrc", "about", "data"],
            "trace": list(state["trace"]),
        }
        if SCENARIO == "demo":
            result["assertions"].update({
                "dashboard_ready": state["dashboard_ready"],
                "demo_preview_explanation_visible": state.get("demo_preview_explained", False),
                "demo_count_labeled_as_preview": state.get("demo_count_labeled", False),
                "demo_rows_read_only": state.get("demo_rows_read_only", False),
                "demo_cta_navigated_to_requirements": state.get("demo_cta_navigated", False),
                "no_fake_id_business_action": not requirement_requests and not section_requests,
                "web_page_identity_stable": page_identity == id(win._dash_web.web_page),
                "no_renderer_termination": not render_terminations,
                "no_external_request_attempts": not interceptor.blocked_urls,
                "no_python_socket_connect_attempts": not python_socket_attempts,
            })
        else:
            result["assertions"].update({
                "dashboard_ready": state["dashboard_ready"],
                "initial_dom_has_target_and_distractor": state["initial_dom"] is not None,
                "completion_modal_focus_and_save_click": state["completion_dialog_clicked"],
                "completion_persisted_and_same_dom_refreshed": state["completion_verified"],
                "returned_to_dashboard_after_completion": state["completion_returned_home"],
                "test_points_checked_and_unchecked": state["test_points_toggle_completed"],
                "test_point_checkbox_user_reachable": state.get("test_point_checkbox_reachable", False),
                "test_points_targeted_and_persisted": state["test_points_added"],
                "test_points_natural_signal_and_same_dom_refreshed": state["test_points_verified"],
                "returned_to_dashboard_after_test_points": state["test_points_returned_home"],
                "web_page_identity_stable": page_identity == id(win._dash_web.web_page),
                "no_renderer_termination": not render_terminations,
                "no_external_request_attempts": not interceptor.blocked_urls,
                "no_python_socket_connect_attempts": not python_socket_attempts,
            })
        result["page_identity"] = page_identity
        result["page_url"] = page.url().toString()
        if error is not None:
            result["error"] = str(error)
        write_result(status, error)
        try:
            win._force_exit = True
            win.close()
            win.deleteLater()
            app.processEvents()
        except Exception:
            pass
        QTimer.singleShot(0, app.quit)

    def fail(message):
        state["trace"].append(f"FAIL: {message}")
        write_and_quit("FAIL", message)

    def eval_js(script, callback):
        try:
            page.runJavaScript(script, callback)
        except Exception as exc:
            fail(f"runJavaScript failed: {type(exc).__name__}: {exc}")

    def target_dom_script():
        return f"""(() => {{
          const section = document.querySelector('[data-testid="monthly-release-tasks"]');
          const rows = [...(section?.querySelectorAll('.req-list .task-row') || [])].map(row => {{
            const code = row.querySelector('.req-id-badge')?.textContent?.trim() || '';
            return {{
              code,
              title: row.querySelector('.req-title')?.textContent?.trim() || '',
              status: row.querySelector('.chip')?.textContent?.trim() || '',
              chipClass: row.querySelector('.chip')?.className || '',
              testPoints: row.querySelector('.test-points-badge span')?.textContent?.trim() || '',
              hasCompletionAction: !!row.querySelector('[data-action="completion"]'),
              hasTestPointsAction: !!row.querySelector('[data-action="test_points"]'),
              hasViewAction: !!row.querySelector('[data-action="view"]'),
              isDemoBadge: !!row.querySelector('.demo-badge'),
              isInteractive: row.classList.contains('clickable') || row.getAttribute('role') === 'button',
            }};
          }});
          const demoNotice = section?.querySelector('[data-testid="demo-preview-notice"]');
          const demoCta = section?.querySelector('[data-testid="demo-manage-requirements"]');
          const demoRect = demoCta?.getBoundingClientRect();
          return {{
            title: document.title,
            url: location.href,
            nonce: window.__pengtoolsLiveRuntimeNonce || '',
            rowCount: rows.length,
            rows,
            monthlyCount: section?.querySelector('.dchip')?.textContent?.trim() || '',
            demoNoticeText: demoNotice?.innerText?.trim() || '',
            demoCtaText: demoCta?.textContent?.trim() || '',
            demoCtaVisible: Boolean(demoCta && demoRect && demoRect.width > 0 && demoRect.height > 0),
          }};
        }})()"""

    def on_page_ready(page_name):
        state["trace"].append(f"page_ready:{page_name}")
        if page_name != "dashboard" or state["dashboard_ready"]:
            return
        state["dashboard_ready"] = True
        QTimer.singleShot(250, inspect_initial_dom)

    def inspect_initial_dom():
        eval_js(
            f"window.__pengtoolsLiveRuntimeNonce = {json.dumps(state['page_nonce'])}; "
            + target_dom_script(),
            on_initial_dom,
        )

    def on_initial_dom(dom):
        state["trace"].append(f"initial_dom:{type(dom).__name__}")
        state["initial_dom"] = dom
        if not isinstance(dom, dict):
            return fail("production dashboard returned no initial DOM state")
        if SCENARIO == "demo":
            rows = dom.get("rows") or []
            notice = str(dom.get("demoNoticeText") or "")
            explanation_ok = all(part in notice for part in (
                "示例预览",
                "不代表你的真实任务",
                "不能直接编辑",
                "导入已有文件夹或新建需求",
                "本月实际上线日期",
            ))
            count_ok = "示例预览" in str(dom.get("monthlyCount") or "") and len(rows) > 0
            rows_read_only = all(
                row.get("isDemoBadge")
                and not row.get("hasViewAction")
                and not row.get("hasCompletionAction")
                and not row.get("hasTestPointsAction")
                and not row.get("isInteractive")
                for row in rows
            )
            if not explanation_ok or not dom.get("demoCtaVisible") or dom.get("demoCtaText") != "去管理真实需求":
                return fail(f"demo dashboard omitted its visible read-only explanation or management CTA: {dom}")
            if not count_ok or not rows_read_only:
                return fail(f"demo tasks were not clearly labeled as preview-only, read-only examples: {dom}")
            state["demo_preview_explained"] = True
            state["demo_count_labeled"] = True
            state["demo_rows_read_only"] = True
            result["demo_initial_dom"] = dom
            state["trace"].append("demo_preview_and_read_only_rows_verified")
            QTimer.singleShot(350, capture_demo_visual_and_click_cta)
            return
        rows = dom.get("rows") or []
        by_code = {row.get("code"): row for row in rows}
        target = by_code.get(target_code)
        distractor = by_code.get(distractor_code)
        if len(rows) != 2 or target is None or distractor is None:
            return fail(f"initial Vue DOM did not show exactly the synthetic target and distractor: {dom}")
        if target.get("status") != result["expected_initial_display_status"][target_code] or target.get("testPoints") != "0/1":
            return fail(f"unexpected target initial summary in DOM: {target}")
        if distractor.get("status") != result["expected_initial_display_status"][distractor_code] or distractor.get("testPoints") != "0/1":
            return fail(f"unexpected distractor initial summary in DOM: {distractor}")
        if not target.get("hasCompletionAction") or not target.get("hasTestPointsAction"):
            return fail(f"target row is missing the real dashboard actions: {target}")
        result["initial_dom"] = dom
        result["assertions"]["initial_dom_has_target_and_distractor"] = True
        state["completion_summary_start"] = len(summary_payloads)
        js = f"""(() => {{
          const rows = [...document.querySelectorAll('[data-testid="monthly-release-tasks"] .task-row')];
          const row = rows.find(item => item.querySelector('.req-id-badge')?.textContent?.trim() === {json.dumps(target_code)});
          const button = row?.querySelector('[data-action="completion"]');
          if (!button) return {{clicked:false}};
          button.click();
          return {{clicked:true, code:row.querySelector('.req-id-badge')?.textContent?.trim() || ''}};
        }})()"""
        eval_js(js, on_completion_dom_click)

    def capture_demo_visual_and_click_cta():
        visual_path = EVIDENCE / "dashboard-demo-preview.png"
        visual_pixmap = win.grab()
        if visual_pixmap.isNull() or not visual_pixmap.save(str(visual_path), "PNG"):
            return fail("could not capture the production dashboard demo-preview widget")
        result["demo_visual_evidence"] = {
            "path": str(visual_path),
            "window_size": [win.width(), win.height()],
            "captured_pixel_size": [visual_pixmap.width(), visual_pixmap.height()],
            "dashboard_widget_size": [win._dash_web.width(), win._dash_web.height()],
            "captured_before_management_navigation": True,
        }
        eval_js(
            "(() => { const button = document.querySelector('[data-testid=demo-manage-requirements]'); "
            "if (!button) return false; button.click(); return true; })()",
            on_demo_cta_click,
        )

    def on_demo_cta_click(clicked):
        state["trace"].append(f"demo_cta_click:{clicked!r}")
        if clicked is not True:
            return fail("the demo management CTA was not clickable in the production dashboard DOM")
        QTimer.singleShot(120, verify_demo_navigation)

    def verify_demo_navigation():
        if int(getattr(win, "_current_nav_index", -1)) != 10:
            return fail(f"demo CTA did not navigate through MainWindow to Requirements (current={getattr(win, '_current_nav_index', None)})")
        if win.requirement_panel is None or win.stack.currentWidget() is not win.requirement_panel:
            return fail("demo CTA did not show the existing Requirements panel")
        if requirement_requests or section_requests:
            return fail(f"demo CTA triggered a fake-ID business action: {requirement_requests}; {section_requests}")
        state["demo_cta_navigated"] = True
        result["demo_navigation"] = {
            "current_nav_index": int(win._current_nav_index),
            "requirements_panel_visible": win.stack.currentWidget() is win.requirement_panel,
            "open_requirement_requests": list(requirement_requests),
            "open_requirement_section_requests": list(section_requests),
            "same_page_identity": page_identity == id(win._dash_web.web_page),
        }
        if interceptor.blocked_urls:
            return fail(f"unexpected external requests were attempted: {interceptor.blocked_urls}")
        write_and_quit("PASS")

    def on_completion_dom_click(value):
        state["trace"].append(f"completion_dom_click:{value!r}")
        if not isinstance(value, dict) or not value.get("clicked") or value.get("code") != target_code:
            return fail(f"Vue completion button click did not target the synthetic requirement: {value}")
        state["completion_clicked"] = True
        QTimer.singleShot(25, watch_completion_dialog)

    def connect_requirement_observers(panel):
        if state.get("requirement_observers_connected"):
            return
        panel.requirement_saved.connect(lambda item: requirement_saved.append({
            "id": str((item or {}).get("id") or ""),
            "status": str((item or {}).get("status") or ""),
        }))
        panel.requirements_changed.connect(lambda: requirements_changed.append(True))
        state["requirement_observers_connected"] = True

    def watch_completion_dialog():
        state["trace"].append(f"watch_completion:modal={type(QApplication.activeModalWidget()).__name__ if QApplication.activeModalWidget() else None};requests={len(section_requests)}")
        if state["finished"] or state["completion_dialog_clicked"]:
            return
        modal = QApplication.activeModalWidget()
        if modal is None:
            if state["completion_clicked"]:
                QTimer.singleShot(40, watch_completion_dialog)
            return
        if not isinstance(modal, RequirementDialog):
            return fail(f"unexpected modal on dashboard completion route: {type(modal).__name__}")
        panel = win.requirement_panel
        if panel is None or not isinstance(panel._current, dict) or panel._current.get("id") != target_id:
            return fail("completion route did not focus the exact synthetic requirement")
        connect_requirement_observers(panel)
        state["modal_focus_checks"] += 1
        if not modal.status_combo.hasFocus():
            if state["modal_focus_checks"] < 40:
                QTimer.singleShot(25, watch_completion_dialog)
                return
            return fail("completion dialog did not focus its status control after opening")
        result["completion_dialog"] = {
            "type": type(modal).__name__,
            "target_id": str(panel._current.get("id")),
            "status_combo_focused": True,
            "status_before": modal.status_combo.currentText(),
        }
        index = modal.status_combo.findText("已上线", Qt.MatchFlag.MatchExactly)
        if index < 0:
            return fail("RequirementDialog has no existing 已上线 status option")
        modal.status_combo.setCurrentIndex(index)
        buttons = modal.findChild(QDialogButtonBox)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save) if buttons else None
        if save_button is None or not save_button.isEnabled():
            return fail("RequirementDialog existing Save button is unavailable")
        QTest.mouseClick(save_button, Qt.MouseButton.LeftButton)
        state["completion_dialog_clicked"] = True
        result["completion_dialog"]["save_button_clicked"] = True
        result["completion_dialog"]["status_selected"] = "已上线"
        QTimer.singleShot(120, poll_completion_result)

    def poll_completion_result():
        state["trace"].append(f"poll_completion:modal={type(QApplication.activeModalWidget()).__name__ if QApplication.activeModalWidget() else None};saved={len(requirement_saved)};summary={len(summary_payloads)}")
        if state["finished"] or state["completion_verified"]:
            return
        try:
            records = load_requirements()
        except Exception as exc:
            return fail(f"could not read the isolated requirements file after completion save: {exc}")
        target = next((item for item in records if item.get("id") == target_id), None)
        distractor = next((item for item in records if item.get("id") == distractor_id), None)
        if target is None or distractor is None:
            return fail("one of the two synthetic requirements disappeared after completion save")
        if target.get("status") != "已上线":
            return fail(f"completion dialog did not persist the selected status: {target.get('status')!r}")
        if distractor.get("status") != "开发中":
            return fail(f"completion save changed the distractor status: {distractor.get('status')!r}")
        if not any(item.get("id") == target_id and item.get("status") == "已上线" for item in requirement_saved):
            if len(requirement_saved) == 0:
                QTimer.singleShot(60, poll_completion_result)
                return
            return fail(f"RequirementPanel.requirement_saved did not identify the selected target: {requirement_saved}")
        if len(summary_payloads) <= state["completion_summary_start"]:
            QTimer.singleShot(60, poll_completion_result)
            return
        completion_summary = summary_payloads[-1]
        completion_rows = {
            str(item.get("id") or ""): item
            for item in completion_summary.get("monthly_release_tasks", [])
            if isinstance(item, dict)
        }
        completion_target = completion_rows.get(target_id)
        completion_distractor = completion_rows.get(distractor_id)
        if not completion_target or completion_target.get("status") != "已上线" or not completion_target.get("done"):
            return fail(f"natural requirement_saved summary payload did not include the saved completion state: {completion_summary}")
        if completion_target.get("test_points") != "0/1":
            return fail(f"completion summary payload changed the target test-point count: {completion_target}")
        if not completion_distractor or completion_distractor.get("status") != "今日上线" or completion_distractor.get("done"):
            return fail(f"completion summary payload changed or omitted the distractor: {completion_distractor}")
        state["completion_summary_count_before_return"] = len(summary_payloads)
        result["completion_signal_summary"] = {
            "requirement_saved": list(requirement_saved),
            "natural_summary_payload_target": completion_target,
            "natural_summary_payload_distractor": completion_distractor,
            "summary_count_before_return_navigation": state["completion_summary_count_before_return"],
        }
        if not state["completion_returned_home"]:
            if not win._show_panel(0):
                return fail("existing MainWindow navigation could not return to the dashboard after save")
            state["completion_returned_home"] = True
            QTimer.singleShot(120, lambda: inspect_completion_dom(target, distractor))
            return
        inspect_completion_dom(target, distractor)

    def inspect_completion_dom(target, distractor):
        state["trace"].append("inspect_completion_dom")
        eval_js(target_dom_script(), lambda dom: on_completion_dom_refreshed(dom, target, distractor))

    def on_completion_dom_refreshed(dom, persisted_target, persisted_distractor):
        state["trace"].append(f"completion_dom_callback:{type(dom).__name__}:{dom!r}")
        if not isinstance(dom, dict):
            return fail("could not inspect the dashboard DOM after completion save")
        rows = {row.get("code"): row for row in dom.get("rows") or []}
        target = rows.get(target_code)
        distractor = rows.get(distractor_code)
        if target and "ok" in target.get("chipClass", "") and target.get("testPoints") == "0/1":
            if not distractor or distractor.get("status") != result["expected_initial_display_status"][distractor_code]:
                return fail(f"the completion update changed or hid the distractor row: {dom}")
            if dom.get("nonce") != state["page_nonce"] or page_identity != id(win._dash_web.web_page):
                return fail("dashboard document or QWebEngine page changed during completion refresh")
            state["completion_verified"] = True
            result["completion_after"] = {
                "persisted_target_status": persisted_target.get("status"),
                "persisted_distractor_status": persisted_distractor.get("status"),
                "dom_target": target,
                "dom_distractor": distractor,
                "same_document_nonce": dom.get("nonce"),
                "summary_updates_before_return": state["completion_summary_count_before_return"] - state["completion_summary_start"],
                "summary_updates_during_return": len(summary_payloads) - state["completion_summary_count_before_return"],
            }
            state["test_points_summary_start"] = len(summary_payloads)
            click_test_points_action()
            return
        QTimer.singleShot(100, poll_completion_result)

    def click_test_points_action():
        action = ("check", "uncheck", "add")[state["test_points_action_index"]]
        state["test_points_action"] = action
        state["test_points_action_triggered"] = False
        state["test_points_action_verified"] = False
        state["test_points_returned_home_for_action"] = False
        state["test_points_action_summary_start"] = len(summary_payloads)
        state["test_points_action_changed_start"] = len(requirements_changed)
        js = f"""(() => {{
          const rows = [...document.querySelectorAll('[data-testid="monthly-release-tasks"] .task-row')];
          const row = rows.find(item => item.querySelector('.req-id-badge')?.textContent?.trim() === {json.dumps(target_code)});
          const button = row?.querySelector('[data-action="test_points"]');
          if (!button) return {{clicked:false}};
          button.click();
          return {{clicked:true, code:row.querySelector('.req-id-badge')?.textContent?.trim() || ''}};
        }})()"""
        eval_js(js, on_test_points_dom_click)

    def on_test_points_dom_click(value):
        state["trace"].append(f"test_points_dom_click:{value!r}")
        if not isinstance(value, dict) or not value.get("clicked") or value.get("code") != target_code:
            return fail(f"Vue test-points button click did not target the synthetic requirement: {value}")
        state["test_points_clicked"] = True
        QTimer.singleShot(50, prepare_test_points_editor)

    def prepare_test_points_editor():
        action = state["test_points_action"]
        state["trace"].append(f"prepare_test_points:{action};requests={len(section_requests)};current={getattr(getattr(win, 'requirement_panel', None), '_current', {}).get('id') if isinstance(getattr(getattr(win, 'requirement_panel', None), '_current', None), dict) else None}")
        if state["finished"] or state["test_points_action_triggered"]:
            return
        panel = win.requirement_panel
        if panel is None:
            return fail("test-points target did not create the real RequirementPanel")
        connect_requirement_observers(panel)
        if not isinstance(panel._current, dict) or panel._current.get("id") != target_id:
            return fail("test-points target did not select the exact synthetic requirement")
        editor = panel.test_points_editor
        if editor.isEnabled() is False:
            return fail("test-points target left the real editor disabled")
        if not editor.add_edit.hasFocus():
            state["editor_focus_checks"] = state.get("editor_focus_checks", 0) + 1
            if state["editor_focus_checks"] < 40:
                QTimer.singleShot(25, prepare_test_points_editor)
                return
            return fail("test-points target did not focus the inline editor input")
        if state["test_points_action_index"] == 0 and requirements_changed:
            return fail("requirements_changed was emitted before the test-point edit")
        if len(editor.points()) != 1 or editor.points()[0].get("id") != f"tp-{token}-target":
            return fail(f"the real editor loaded the wrong test-points target: {editor.points()}")
        state["test_points_action_triggered"] = True
        state["test_points_action_summary_start"] = len(summary_payloads)
        state["test_points_action_changed_start"] = len(requirements_changed)
        if action in {"check", "uncheck"}:
            rows = [row for row in editor.findChildren(TestPointRow) if row._point_id == f"tp-{token}-target"]
            if len(rows) != 1:
                return fail(f"the real editor did not render exactly one target TestPointRow: {len(rows)}")
            row = rows[0]
            if win._current_nav_index != 10 or win.stack.currentWidget() is not panel:
                return fail(f"the test-points control is not on the active Requirements route: nav={win._current_nav_index}")
            current_tab = panel.detail_tabs.currentWidget() if hasattr(panel, "detail_tabs") else None
            if current_tab is None or not current_tab.isAncestorOf(editor):
                return fail("HomeBridge focus did not select the native test-points editor tab")
            if not editor.isVisible() or not editor.isEnabled() or not row.isVisible() or not row.check.isVisible() or not row.check.isEnabled():
                return fail("the focused native test-point editor or target checkbox is hidden or disabled")
            viewport = editor.scroll.viewport()
            scroll_value_before_reveal = editor.scroll.verticalScrollBar().value()
            checkbox_center = row.check.rect().center()
            viewport_global = QRect(viewport.mapToGlobal(QPoint(0, 0)), viewport.size())
            checkbox_global_center = row.check.mapToGlobal(checkbox_center)
            center_in_viewport = viewport_global.contains(checkbox_global_center)
            center_in_visible_region = row.check.visibleRegion().contains(checkbox_center)
            route_focus_revealed = center_in_viewport and center_in_visible_region
            if not route_focus_revealed:
                editor.scroll.ensureWidgetVisible(row, 0, 0)
                QTest.qWait(50)
                viewport_global = QRect(viewport.mapToGlobal(QPoint(0, 0)), viewport.size())
                checkbox_global_center = row.check.mapToGlobal(checkbox_center)
                center_in_viewport = viewport_global.contains(checkbox_global_center)
                center_in_visible_region = row.check.visibleRegion().contains(checkbox_center)
            if not center_in_viewport or not center_in_visible_region:
                return fail("the target checkbox center remains outside the editor scroll viewport or visible ancestor region")
            expected_before = action == "uncheck"
            if row.check.isChecked() is not expected_before:
                return fail(f"the target checkbox had unexpected state before {action}: {row.check.isChecked()}")
            state["test_point_checkbox_reachable"] = True
            screenshot_path = EVIDENCE / f"native-test-points-editor-before-{action}.png"
            result["native_test_points_editor_before_checkbox_by_action"] = state["native_test_point_editor_pre_action_captures"]
            state["native_test_point_editor_pre_action_captures"][action] = {
                "action": action,
                "route_nav_index": int(win._current_nav_index),
                "requirement_panel_is_current": win.stack.currentWidget() is panel,
                "test_points_tab_selected_by_home_route": current_tab.isAncestorOf(editor),
                "editor_visible_enabled": bool(editor.isVisible() and editor.isEnabled()),
                "checkbox_visible_enabled": bool(row.check.isVisible() and row.check.isEnabled()),
                "checkbox_checked_before_input": bool(row.check.isChecked()),
                "route_focus_revealed_checkbox": route_focus_revealed,
                "test_scrolled_editor_if_needed": not route_focus_revealed,
                "scroll_value_before_reveal": scroll_value_before_reveal,
                "scroll_value_after_reveal": editor.scroll.verticalScrollBar().value(),
                "checkbox_center_inside_editor_viewport": center_in_viewport,
                "checkbox_center_in_visible_region": center_in_visible_region,
                "checkbox_geometry": [row.check.x(), row.check.y(), row.check.width(), row.check.height()],
                "viewport_geometry": [viewport.x(), viewport.y(), viewport.width(), viewport.height()],
                "screenshot_path": str(screenshot_path),
            }
            editor_pixmap = editor.grab()
            if editor_pixmap.isNull() or not editor_pixmap.save(str(screenshot_path), "PNG"):
                return fail("could not capture the exposed native test-point editor before checkbox input")
            state["test_points_expected_done"] = action == "check"
            QTest.mouseClick(row.check, Qt.MouseButton.LeftButton)
        else:
            point_text = f"Synthetic saved point {token}"
            QTest.keyClicks(editor.add_edit, point_text)
            QTest.mouseClick(editor.add_btn, Qt.MouseButton.LeftButton)
            state["test_points_added"] = True
            state["saved_point_text"] = point_text
        QTimer.singleShot(120, poll_test_points_result)

    def poll_test_points_result():
        action = state["test_points_action"]
        state["trace"].append(f"poll_test_points:{action};changed={len(requirements_changed)};summary={len(summary_payloads)}")
        if state["finished"] or state.get("test_points_action_verified"):
            return
        try:
            records = load_requirements()
        except Exception as exc:
            return fail(f"could not read the isolated requirements file after test-point save: {exc}")
        target = next((item for item in records if item.get("id") == target_id), None)
        distractor = next((item for item in records if item.get("id") == distractor_id), None)
        if target is None or distractor is None:
            return fail("one of the synthetic requirements disappeared after test-point save")
        points = target.get("test_points") or []
        if action in {"check", "uncheck"}:
            expected_done = state["test_points_expected_done"]
            if len(points) != 1 or points[0].get("id") != f"tp-{token}-target" or points[0].get("done") is not expected_done:
                return fail(f"the real checkbox state did not persist for {action}: {points}")
            expected_ratio = "1/1" if expected_done else "0/1"
        else:
            if len(points) != 2 or not any(point.get("text") == state["saved_point_text"] and point.get("done") is False for point in points):
                return fail(f"test point was not persisted to the synthetic target: {points}")
            if points[0].get("done") is not False:
                return fail(f"the unchecked target point changed during add: {points}")
            expected_ratio = "0/2"
        if target.get("status") != "已上线":
            return fail(f"test-point save changed the target status: {target.get('status')!r}")
        if distractor.get("status") != "开发中" or distractor.get("test_points") != initial_records[1]["test_points"]:
            return fail("test-point save changed the synthetic distractor")
        changes_for_action = len(requirements_changed) - state["test_points_action_changed_start"]
        if changes_for_action > 1:
            return fail(f"one {action} action emitted {changes_for_action} requirements_changed signals")
        if changes_for_action < 1:
            QTimer.singleShot(60, poll_test_points_result)
            return
        if len(summary_payloads) <= state["test_points_action_summary_start"]:
            QTimer.singleShot(60, poll_test_points_result)
            return
        summary_updates_before_return = len(summary_payloads) - state["test_points_action_summary_start"]
        if summary_updates_before_return != 1:
            return fail(f"one {action} save should emit one natural summary update before navigation, got {summary_updates_before_return}")
        test_points_summary = summary_payloads[-1]
        test_points_rows = {
            str(item.get("id") or ""): item
            for item in test_points_summary.get("monthly_release_tasks", [])
            if isinstance(item, dict)
        }
        test_points_target = test_points_rows.get(target_id)
        test_points_distractor = test_points_rows.get(distractor_id)
        if not test_points_target or test_points_target.get("status") != "已上线" or not test_points_target.get("done"):
            return fail(f"natural requirements_changed summary payload did not retain the saved completion state: {test_points_summary}")
        if test_points_target.get("test_points") != expected_ratio:
            return fail(f"natural requirements_changed summary payload had the wrong checkbox/add ratio for {action}: {test_points_target}")
        if not test_points_distractor or test_points_distractor.get("status") != "今日上线" or test_points_distractor.get("done"):
            return fail(f"test-point summary payload changed or omitted the distractor: {test_points_distractor}")
        checkbox_visual = {}
        if action in {"check", "uncheck"}:
            editor = win.requirement_panel.test_points_editor
            rows = [row for row in editor.findChildren(TestPointRow) if row._point_id == f"tp-{token}-target"]
            if len(rows) != 1:
                return fail(f"the target TestPointRow disappeared before settled screenshot capture: {len(rows)}")
            checkbox = rows[0].check
            persisted_done = bool(points[0].get("done"))
            if bool(checkbox.isChecked()) != persisted_done:
                return fail(f"settled checkbox widget state disagrees with persisted state: widget={checkbox.isChecked()} persisted={persisted_done}")
            screenshot_path = EVIDENCE / f"native-test-point-checkbox-{action}.png"
            checkbox_pixmap = checkbox.grab()
            if checkbox_pixmap.isNull() or not checkbox_pixmap.save(str(screenshot_path), "PNG"):
                return fail(f"could not capture the settled native checkbox after {action}")
            checkbox_visual = {
                "widget_checked_after_settle": bool(checkbox.isChecked()),
                "persisted_done_after_settle": persisted_done,
                "screenshot_path": str(screenshot_path),
                "screenshot_size": [checkbox_pixmap.width(), checkbox_pixmap.height()],
            }
        state["test_points_action_verified"] = True
        state["test_points_summary_count_before_return"] = len(summary_payloads)
        result["test_points_signal_summaries"] = state["test_points_action_results"]
        state["test_points_action_results"].append({
            "action": action,
            "persisted_done": (points[0].get("done") if points else None),
            "checkbox_visual_after_settle": checkbox_visual,
            "persisted_ratio": test_points_target.get("test_points"),
            "requirements_changed_for_action": changes_for_action,
            "natural_summary_payload_target": test_points_target,
            "natural_summary_payload_distractor": test_points_distractor,
            "summary_count_before_return_navigation": state["test_points_summary_count_before_return"],
            "summary_updates_before_return_navigation": summary_updates_before_return,
        })
        if not win._show_panel(0):
            return fail("existing MainWindow navigation could not return to the dashboard after test-point save")
        state["test_points_returned_home"] = True
        state["test_points_returned_home_for_action"] = True
        QTimer.singleShot(120, lambda: inspect_test_points_dom(target, distractor))

    def inspect_test_points_dom(target, distractor):
        state["trace"].append("inspect_test_points_dom")
        eval_js(target_dom_script(), lambda dom: on_test_points_dom_refreshed(dom, target, distractor))

    def on_test_points_dom_refreshed(dom, persisted_target, persisted_distractor):
        state["trace"].append(f"test_points_dom_callback:{type(dom).__name__}:{dom!r}")
        if not isinstance(dom, dict):
            return fail("could not inspect dashboard DOM after test-point save")
        rows = {row.get("code"): row for row in dom.get("rows") or []}
        target = rows.get(target_code)
        distractor = rows.get(distractor_code)
        expected_ratio = {"check": "1/1", "uncheck": "0/1", "add": "0/2"}[state["test_points_action"]]
        if target and target.get("testPoints") == expected_ratio:
            if not distractor or distractor.get("testPoints") != "0/1":
                return fail(f"the test-point update changed or hid the distractor row: {dom}")
            if "ok" not in target.get("chipClass", ""):
                return fail(f"the test-point update lost the saved requirement status: {target}")
            if dom.get("nonce") != state["page_nonce"] or page_identity != id(win._dash_web.web_page):
                return fail("dashboard document or QWebEngine page changed during test-point refresh")
            action = state["test_points_action"]
            action_result = state["test_points_action_results"][-1]
            action_result.update({
                "dom_target": target,
                "dom_distractor": distractor,
                "same_document_nonce": dom.get("nonce"),
                "summary_updates_during_return_navigation": len(summary_payloads) - state["test_points_summary_count_before_return"],
            })
            result["test_points_after"] = {
                "actions": state["test_points_action_results"],
                "persisted_target_status": persisted_target.get("status"),
                "persisted_target_points": persisted_target.get("test_points"),
                "persisted_distractor_status": persisted_distractor.get("status"),
                "persisted_distractor_points": persisted_distractor.get("test_points"),
                "dom_target": target,
                "dom_distractor": distractor,
                "same_document_nonce": dom.get("nonce"),
            }
            state["test_points_toggle_completed"] = state["test_points_action_index"] >= 1
            if action == "add":
                state["test_points_verified"] = True
            result["synthetic_final"] = {
                "target": {
                    "id": persisted_target.get("id"),
                    "status": persisted_target.get("status"),
                    "test_points": persisted_target.get("test_points"),
                    "test_point_done_values": [bool(point.get("done")) for point in (persisted_target.get("test_points") or [])],
                },
                "distractor": {
                    "id": persisted_distractor.get("id"),
                    "status": persisted_distractor.get("status"),
                    "test_points": persisted_distractor.get("test_points"),
                },
            }
            if action != "add":
                state["test_points_action_index"] += 1
                state["test_points_action_triggered"] = False
                state["test_points_action_verified"] = False
                QTimer.singleShot(100, click_test_points_action)
                return
            visual_path = EVIDENCE / "dashboard-after-live-actions.png"
            visual_pixmap = win.grab()
            if visual_pixmap.isNull() or not visual_pixmap.save(str(visual_path), "PNG"):
                return fail("could not capture the returned production dashboard widget")
            result["dashboard_visual_evidence"] = {
                "path": str(visual_path),
                "window_size": [win.width(), win.height()],
                "captured_pixel_size": [visual_pixmap.width(), visual_pixmap.height()],
                "dashboard_widget_size": [win._dash_web.width(), win._dash_web.height()],
                "web_view_size": [win._dash_web.web_view.width(), win._dash_web.web_view.height()],
                "captured_after_both_saves_on_same_page": True,
            }
            if interceptor.blocked_urls:
                return fail(f"unexpected external requests were attempted: {interceptor.blocked_urls}")
            write_and_quit("PASS")
            return
        QTimer.singleShot(100, poll_test_points_result)

    win._dash_bridge.pageReadyReceived.connect(on_page_ready)
    render_guard = QTimer()
    render_guard.setSingleShot(True)
    render_guard.timeout.connect(lambda: fail("dashboard runtime exceeded the 45 second child watchdog"))
    render_guard.start(45000)
    win.show()
    QTimer.singleShot(0, lambda: on_page_ready("dashboard") if "dashboard" in win._web_health.bridge_ready_pages else None)
    app.exec()
    render_guard.stop()
    if result.get("status") != "PASS":
        raise RuntimeError(result.get("error") or "runtime assertions failed")
    result["webengine_cache_files_after_shutdown"] = [
        str(path.relative_to(cache_root))
        for path in cache_root.rglob("*")
        if path.is_file()
    ]
    write_result("PASS")
    print(json.dumps({"status": result["status"], "result": str(result_path)}, ensure_ascii=False), flush=True)

except BaseException as exc:
    result.setdefault("traceback", traceback.format_exc())
    try:
        if result.get("status") == "RUNNING":
            write_result("FAIL", f"{type(exc).__name__}: {exc}")
    except Exception:
        pass
    print(result["traceback"], file=sys.stderr, flush=True)
    raise
'''


class DashboardRequirementLiveRuntimeTest(unittest.TestCase):
    def _run_isolated_child(self, scenario):
        self.assertTrue(PYTHON.is_file(), f"isolated Python runtime not found: {PYTHON}")
        evidence_dir = Path(tempfile.mkdtemp(prefix=f"pengtools-dashboard-{scenario}-runtime-"))
        script_path = evidence_dir / "dashboard_live_runtime_probe.py"
        log_path = evidence_dir / "runtime.log"
        script_path.write_text(textwrap.dedent(_CHILD_SCRIPT), encoding="utf-8")

        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "windows"
        env.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
        env.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)
        env["PYTHONUNBUFFERED"] = "1"
        qt_temp_root = evidence_dir / "qt-user-data"
        qt_local_app_data = qt_temp_root / "local"
        qt_roaming_app_data = qt_temp_root / "roaming"
        qt_temp = qt_temp_root / "temp"
        for directory in (qt_local_app_data, qt_roaming_app_data, qt_temp):
            directory.mkdir(parents=True, exist_ok=True)
        env["LOCALAPPDATA"] = str(qt_local_app_data)
        env["APPDATA"] = str(qt_roaming_app_data)
        env["TEMP"] = env["TMP"] = str(qt_temp)

        try:
            completed = subprocess.run(
                [str(PYTHON), str(script_path), str(ROOT), str(evidence_dir), scenario],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=55,
                check=False,
            )
            log_path.write_text(completed.stdout or "", encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            log_path.write_text(output, encoding="utf-8")
            self.fail(
                f"Windows-QPA live-runtime child exceeded 55 seconds; evidence={evidence_dir}; "
                f"log={log_path}\n{output[-5000:]}"
            )

        result_path = evidence_dir / "result.json"
        self.assertTrue(
            result_path.is_file(),
            f"child produced no structured result (exit={completed.returncode}); "
            f"evidence={evidence_dir}; log={log_path}\n{completed.stdout[-5000:]}",
        )
        result = __import__("json").loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(
            completed.returncode,
            0,
            f"live-runtime probe failed; evidence={evidence_dir}; result={result_path}; "
            f"log={log_path}\n{completed.stdout[-5000:]}",
        )
        self.assertEqual(result.get("status"), "PASS", result)
        self.assertEqual(result.get("scenario"), scenario)
        self.assertEqual(result.get("qpa"), "windows")
        for path_key in ("webengine_storage", "webengine_cache_root", "webengine_log"):
            configured_path = Path(result.get(path_key, "")).resolve()
            self.assertTrue(
                configured_path == evidence_dir.resolve() or evidence_dir.resolve() in configured_path.parents,
                f"{path_key} escaped TEMP evidence directory: {configured_path}",
            )
        self.assertTrue(result.get("webengine_profile_off_the_record"), result)
        self.assertIn("MemoryHttpCache", result.get("webengine_cache_type", ""), result)
        self.assertEqual(result.get("webengine_cache_files_after_shutdown"), [], result)
        for assertion, passed in result.get("assertions", {}).items():
            self.assertTrue(passed, f"{assertion} failed; result={result_path}")
        print(f"Dashboard {scenario} live-runtime evidence: {evidence_dir}")
        return evidence_dir, result

    def test_real_vue_mainwindow_requirement_save_and_natural_refresh(self):
        evidence_dir, result = self._run_isolated_child("live")
        self.assertIn("REQ-SYNTH-DASHBOARD-", result.get("synthetic_ids", {}).get("target", ""))
        self.assertTrue(result.get("synthetic_final", {}).get("target", {}).get("test_points"))
        self.assertEqual(result.get("synthetic_final", {}).get("target", {}).get("test_point_done_values"), [False, False])
        self.assertEqual(
            [entry.get("persisted_ratio") for entry in result.get("events", {}).get("test_point_actions", [])],
            ["1/1", "0/1", "0/2"],
        )
        self.assertEqual(
            [entry.get("persisted_done") for entry in result.get("events", {}).get("test_point_actions", [])],
            [True, False, False],
        )
        self.assertEqual(
            [entry.get("summary_updates_before_return_navigation") for entry in result.get("events", {}).get("test_point_actions", [])],
            [1, 1, 1],
        )
        native_pre_actions = result.get("native_test_points_editor_before_checkbox_by_action", {})
        native_editor = native_pre_actions.get("check", {})
        native_uncheck_editor = native_pre_actions.get("uncheck", {})
        self.assertEqual(native_editor.get("route_nav_index"), 10)
        self.assertTrue(native_editor.get("requirement_panel_is_current"), result)
        self.assertTrue(native_editor.get("test_points_tab_selected_by_home_route"), result)
        self.assertTrue(native_editor.get("editor_visible_enabled"), result)
        self.assertTrue(native_editor.get("checkbox_visible_enabled"), result)
        self.assertFalse(native_editor.get("checkbox_checked_before_input"), result)
        self.assertTrue(native_editor.get("checkbox_center_inside_editor_viewport"), result)
        self.assertTrue(native_editor.get("checkbox_center_in_visible_region"), result)
        self.assertTrue(Path(native_editor.get("screenshot_path", "")).is_file(), result)
        self.assertTrue(native_uncheck_editor.get("checkbox_checked_before_input"), result)
        self.assertTrue(Path(native_uncheck_editor.get("screenshot_path", "")).is_file(), result)
        checkbox_visuals = [
            entry.get("checkbox_visual_after_settle", {})
            for entry in result.get("events", {}).get("test_point_actions", [])
            if entry.get("action") in {"check", "uncheck"}
        ]
        self.assertEqual([item.get("widget_checked_after_settle") for item in checkbox_visuals], [True, False])
        self.assertEqual([item.get("persisted_done_after_settle") for item in checkbox_visuals], [True, False])
        self.assertTrue(all(Path(item.get("screenshot_path", "")).is_file() for item in checkbox_visuals), result)
        visual = result.get("dashboard_visual_evidence", {})
        self.assertTrue(visual.get("captured_after_both_saves_on_same_page"), result)
        self.assertTrue(Path(visual.get("path", "")).is_file(), result)

    def test_demo_preview_explains_read_only_rows_and_routes_to_requirements(self):
        evidence_dir, result = self._run_isolated_child("demo")
        self.assertEqual(result.get("events", {}).get("open_requirements"), [])
        self.assertEqual(result.get("events", {}).get("open_requirement_sections"), [])
        self.assertEqual(result.get("demo_navigation", {}).get("current_nav_index"), 10)
        self.assertTrue(result.get("demo_navigation", {}).get("requirements_panel_visible"), result)
        self.assertTrue(result.get("demo_navigation", {}).get("same_page_identity"), result)
        self.assertTrue(result.get("demo_initial_dom", {}).get("demoCtaVisible"), result)
        visual = result.get("demo_visual_evidence", {})
        self.assertTrue(visual.get("captured_before_management_navigation"), result)
        self.assertTrue(Path(visual.get("path", "")).is_file(), result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
