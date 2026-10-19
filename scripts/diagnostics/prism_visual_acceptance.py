"""Capture an isolated Windows production Prism shell for visual acceptance.

This diagnostic deliberately uses the production ``MainWindow`` shell method,
the checked-in Vue Chrome bundle, the checked-in Vue Dashboard bundle, and a
real QWebChannel/QWebEngineView.  It supplies every data-bearing seam from an
in-memory fixture and avoids user settings, business panels, services,
network access, and the already-running application.

The runner writes only to ``scripts/diagnostics/prism_visual_acceptance/``.
It is intended to be run on a real Windows desktop; an offscreen Qt platform
is rejected because it cannot provide useful WebEngine visual evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "scripts" / "diagnostics" / "prism_visual_acceptance"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fixture_settings() -> dict:
    """Return safe settings that never point at the user's data directory."""

    from config import DEFAULT_SETTINGS

    result = dict(DEFAULT_SETTINGS)
    result.update(
        {
            "default_language": "zh",
            "ui_theme": "calm",
            "ui_density": "compact",
            "ui_web_shell": True,
            "home_username": "示例用户",
            "private_unlocked": False,
            "sidebar_collapsed": False,
            "floating_show_on_startup": False,
            "floating_always_on_top": False,
            "close_ask_each_time": False,
            "close_default_action": "exit",
        }
    )
    return result


def _fixture_summary() -> dict:
    """A realistic but explicitly synthetic Dashboard DTO."""

    return {
        "username": "示例用户",
        "greeting": "下午好",
        "date_line": "2026-09-18 · 示例工作空间",
        "is_demo": True,
        "stats": {
            "req_open": 3,
            "req_trend": "本周新增 2 项",
            "daily_done": 2,
            "daily_total": 5,
            "daily_note": "今日已记录示例进展",
            "release_total": 4,
            "release_done": 1,
            "is_demo": True,
        },
        "release": {
            "version": "2026-09 示例发版",
            "total": 4,
            "done": 1,
            "percent": 25,
            "days_left": 6,
            "date_text": "2026-09-24 · 示例上线任务",
            "countdown_state": "active",
        },
        "recent": [],
        "checklist": [],
        "tools": [
            {"i": 18, "zh": "数据中心", "ds": "SQL 工作台", "icon": "database"},
            {"i": 16, "zh": "模型对话", "ds": "示例聊天", "icon": "chat"},
            {"i": 12, "zh": "接口排查", "ds": "本地示例", "icon": "api-debug"},
            {"i": 13, "zh": "日志排查", "ds": "示例日志", "icon": "search"},
            {"i": 5, "zh": "加解密", "ds": "离线示例", "icon": "shield-key"},
            {"i": 11, "zh": "格式工具", "ds": "本地格式化", "icon": "json"},
        ],
        "monthly_release_tasks": [
            {
                "id": "demo-001",
                "title": "示例需求 · 订单校验",
                "status": "测试中",
                "done": False,
                "actual_release_date": "2026-09-24",
                "tests": [
                    {"text": "正常路径", "done": True},
                    {"text": "边界输入", "done": False},
                ],
            },
            {
                "id": "demo-002",
                "title": "示例需求 · 报表导出",
                "status": "已完成",
                "done": True,
                "actual_release_date": "2026-09-20",
                "tests": [{"text": "导出结果", "done": True}],
            },
            {
                "id": "demo-003",
                "title": "示例需求 · 接口提示",
                "status": "待确认",
                "done": False,
                "actual_release_date": "2026-09-28",
                "tests": [{"text": "错误提示", "done": False}],
            },
        ],
    }


def _fixture_nav_model() -> dict:
    """Use the production navigation schema without reading user state."""

    from ui.navigation_model import build_web_nav_model_data

    return build_web_nav_model_data(private_unlocked=False, current=0)


def _wait_for(predicate, *, timeout_ms: int = 12_000, poll_ms: int = 30) -> bool:
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    result = {"value": False}
    deadline = time.monotonic() + timeout_ms / 1000

    def poll() -> None:
        try:
            if predicate():
                result["value"] = True
                loop.quit()
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            loop.quit()
            return
        QTimer.singleShot(poll_ms, poll)

    QTimer.singleShot(0, poll)
    loop.exec()
    return bool(result["value"])


def _wait_ms(milliseconds: int) -> None:
    """Pump Qt events for a bounded visual settle interval."""

    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(max(0, int(milliseconds)), loop.quit)
    loop.exec()


def _run_javascript(page, source: str, *, timeout_ms: int = 6_000):
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    result: list[object] = []
    timed_out = {"value": False}

    def done(value) -> None:
        result.append(value)
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: (timed_out.__setitem__("value", True), loop.quit()))
    timer.start(timeout_ms)
    page.runJavaScript(source, done)
    loop.exec()
    if timed_out["value"] or not result:
        raise RuntimeError(f"WebEngine JS probe timed out: {source[:160]}")
    return result[0]


def _widget_geometry(window, widget) -> dict:
    from PyQt6.QtCore import QPoint

    origin = widget.mapTo(window, QPoint(0, 0))
    return {
        "object_name": str(widget.objectName() or ""),
        "x": int(origin.x()),
        "y": int(origin.y()),
        "width": int(widget.width()),
        "height": int(widget.height()),
        "visible": bool(widget.isVisible()),
    }


def _parent_chain(widget) -> list[str]:
    chain: list[str] = []
    current = widget
    while current is not None:
        name = str(current.objectName() or current.__class__.__name__)
        chain.append(name)
        current = current.parentWidget()
    return chain


def _dom_snapshot(page) -> dict:
    source = r"""
(() => {
  const rect = (selector) => {
    const node = document.querySelector(selector);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return {
      x: Math.round(r.x * 100) / 100,
      y: Math.round(r.y * 100) / 100,
      width: Math.round(r.width * 100) / 100,
      height: Math.round(r.height * 100) / 100,
      right: Math.round(r.right * 100) / 100,
      bottom: Math.round(r.bottom * 100) / 100,
    };
  };
  const text = (selector) => Array.from(document.querySelectorAll(selector))
    .map((node) => (node.textContent || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  const style = (selector) => {
    const node = document.querySelector(selector);
    if (!node) return null;
    const computed = getComputedStyle(node);
    return {
      animationName: computed.animationName,
      animationDuration: computed.animationDuration,
      animationPlayState: computed.animationPlayState,
      transition: computed.transition,
    };
  };
  return {
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    selectors: {
      body: rect('body'),
      sidebar: rect('.sidebar'),
      brand: rect('.brand'),
      home: rect('.home-item'),
      group: rect('.group-trigger'),
      dashboardRoot: rect('.dashboard-root'),
      homeGrid: rect('.home-grid'),
      hero: rect('.hero-section, .hero'),
      orbInner: rect('.orb-inner'),
      homeLeft: rect('.home-left'),
      homeRight: rect('.home-right'),
      monthlyTasks: rect('.monthly-tasks, [data-monthly-tasks]'),
      tipCard: rect('.tip-card'),
    },
    visibleText: {
      sidebarBrands: text('.brand-name'),
      headings: text('h1, h2, h3'),
    },
    animation: {
      root: style('.dashboard-root'),
      hero: style('.hero-section, .hero'),
      orb: style('.prism-orb'),
      orbInner: style('.orb-inner'),
      quick: style('.quick'),
    },
  };
})()
"""
    value = _run_javascript(page, source)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
    return value if isinstance(value, dict) else {"raw": value}


def _fixture_main_window_class(main_window_class):
    """Build the fixture subclass only after QtWebEngine import is prepared."""

    from PyQt6.QtWidgets import QWidget

    class FixtureDashboard(QWidget):
        """Empty native fallback page; no production business panel is created."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("prism-visual-fixture-dashboard")

    class FixtureMainWindow(main_window_class):
        """Production MainWindow shell with only data-bearing seams replaced."""

        def _build_web_nav_model(self):
            return _fixture_nav_model()

        def _dashboard_summary_payload(self):
            return _fixture_summary()

        def _ensure_dashboard_panel(self):
            if self.dashboard_panel is not None:
                return self.dashboard_panel
            fallback = FixtureDashboard()
            if getattr(self, "_web_shell_enabled", False) and getattr(self, "_dash_web", None) is not None:
                holder = __import__("PyQt6.QtWidgets", fromlist=["QStackedWidget"]).QStackedWidget()
                holder.setObjectName("prism-visual-fixture-dashboard-holder")
                holder.addWidget(self._dash_web)
                holder.addWidget(fallback)
                holder.setCurrentIndex(0)
                self._dash_holder = holder
                self._mount_panel(0, holder)
            else:
                # Native comparison capture: an inert page-shaped QWidget keeps
                # shell geometry real without importing any business panel.
                self._dash_holder = None
                self._mount_panel(0, fallback)
            self.dashboard_panel = fallback
            return fallback

        def _show_startup_loading(self, _message: str):
            # A startup veil would hide the page under visual inspection.
            return None

        def _startup_tick(self):
            # Do not instantiate settings, panels, tray, hotkey, or keep-awake
            # services.  Web readiness is still observed through production hooks.
            return None

        def _complete_startup_sync(self):
            # Keep the fixture safe even if a caller inherits a test environment
            # that would otherwise take MainWindow's synchronous boot branch.
            return None

    return FixtureMainWindow


def _prepare_app():
    from PyQt6.QtCore import QStandardPaths, Qt
    from PyQt6.QtWidgets import QApplication

    if sys.platform != "win32":
        raise RuntimeError(f"需要真实 Windows Qt/WebEngine，当前平台为 {sys.platform}")
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        raise RuntimeError("拒绝 offscreen Qt；需要真实 Windows QPA/WebEngine 视觉证据")
    QStandardPaths.setTestModeEnabled(True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    # Qt's test harness requires WebEngine to be imported before creating the
    # QApplication, while AA_ShareOpenGLContexts must be set before both.
    from ui import web_shell
    from main_window import MainWindow

    if not web_shell.WEB_SHELL_AVAILABLE:
        raise RuntimeError(f"PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}")
    app = QApplication.instance() or QApplication(sys.argv)
    if app.platformName().lower() != "windows":
        raise RuntimeError(f"需要 Windows QPA，当前为 {app.platformName()}")
    return app, web_shell, MainWindow


def _save_pixmap(pixmap, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"截图保存失败: {path}")
    return {
        "path": str(path),
        "width": int(pixmap.width()),
        "height": int(pixmap.height()),
        "device_pixel_ratio": float(pixmap.devicePixelRatio()),
    }


def _native_snapshot(window) -> dict:
    chrome_web = getattr(window, "_chrome_web", None)
    dashboard_web = getattr(window, "_dash_web", None)
    widgets = {
        "titlebar": window.title_bar,
        "module_tabs": window.module_tabs,
        "shell_body": window.findChild(__import__("PyQt6.QtWidgets", fromlist=["QWidget"]).QWidget, "prism-shell-body"),
        "content_area": window._content_frame,
        "context_header": window._context_header,
        "page_body": window._page_body,
        "status_bar": window.status_bar,
        "sidebar_stack": window._sidebar_stack,
        "chrome_web_view": getattr(chrome_web, "web_view", None),
        "dashboard_holder": getattr(window, "_dash_holder", None),
        "dashboard_web_view": getattr(dashboard_web, "web_view", None),
        "prism_sidebar": window._prism_sidebar,
    }
    result = {
        name: _widget_geometry(window, widget)
        for name, widget in widgets.items()
        if widget is not None
    }
    result["module_tabs_parent_chain"] = _parent_chain(window.module_tabs)
    result["content_area_parent_chain"] = _parent_chain(window._content_frame)
    return result


def _capture_native_size(window, app, width: int, height: int, capture_dir: Path) -> dict:
    """Capture shell geometry with the Web shell disabled and an inert page."""

    window.resize(width, height)
    window._layout_controller.force(width, height)
    app.processEvents()
    _wait_ms(200)
    app.processEvents()
    pixmap = window.grab()
    screenshot = _save_pixmap(
        pixmap,
        capture_dir / f"production_native_shell_{width}x{height}.png",
    )
    return {
        "requested_logical_size": [width, height],
        "actual_window_logical_size": [int(window.width()), int(window.height())],
        "screenshot": screenshot,
        "native_geometry": _native_snapshot(window),
        "web_shell_enabled": False,
        "dashboard_fixture": "empty QWidget; no business panel imported",
    }


def _capture_size(window, app, width: int, height: int, capture_dir: Path) -> dict:
    window.resize(width, height)
    window._layout_controller.force(width, height)
    app.processEvents()

    chrome_expected_width = int(window._chrome_web.web_view.width())
    dashboard_expected_width = int(window._dash_web.web_view.width())
    chrome_width_ready = _wait_for(
        lambda: int(_run_javascript(window._chrome_web.web_page, "innerWidth")) == chrome_expected_width,
        timeout_ms=12_000,
    )
    dashboard_width_ready = _wait_for(
        lambda: int(_run_javascript(window._dash_web.web_page, "innerWidth")) == dashboard_expected_width,
        timeout_ms=12_000,
    )
    if not chrome_width_ready or not dashboard_width_ready:
        raise RuntimeError(
            f"Web viewport did not settle at logical size {width}x{height}: "
            f"chrome={chrome_width_ready}/{chrome_expected_width}, "
            f"dashboard={dashboard_width_ready}/{dashboard_expected_width}"
        )

    # Let CSS enter/orbit animations and the native WebEngine compositor reach
    # the same post-ready inspection point while keeping the wait bounded.
    _wait_ms(350)
    app.processEvents()

    pixmap = window.grab()
    screenshot = _save_pixmap(pixmap, capture_dir / f"production_shell_{width}x{height}.png")
    chrome_dom = _dom_snapshot(window._chrome_web.web_page)
    dashboard_dom = _dom_snapshot(window._dash_web.web_page)
    native = _native_snapshot(window)

    return {
        "requested_logical_size": [width, height],
        "actual_window_logical_size": [int(window.width()), int(window.height())],
        "screenshot": screenshot,
        "native_geometry": native,
        "chrome_dom": chrome_dom,
        "dashboard_dom": dashboard_dom,
        "viewport_settled": {
            "chrome": chrome_width_ready,
            "dashboard": dashboard_width_ready,
        },
    }


def run_visual_acceptance(*, capture_dir: str | os.PathLike[str] | None = None) -> dict:
    if not (ROOT / "resources" / "webui" / "vue" / "chrome.html").is_file():
        raise RuntimeError("缺少 production Chrome bundle")
    if not (ROOT / "resources" / "webui" / "vue" / "dashboard.html").is_file():
        raise RuntimeError("缺少 production Dashboard bundle")

    destination = (Path(capture_dir) if capture_dir else EVIDENCE_DIR).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    from ui import web_diagnostics

    previous_log_dir = getattr(web_diagnostics, "_LOG_DIR", None)
    web_diagnostics.set_log_dir(str(destination / "logs"))
    app = None
    window = None
    windows = []
    safe_env = dict(os.environ)
    # MainWindow has a synchronous boot escape hatch for test/offscreen runs.
    # This runner is a real Windows visual capture and must never enter it.
    safe_env.pop("PENGTOOLS_SYNC_BOOT", None)
    try:
        with patch.dict(os.environ, safe_env, clear=True):
            app, web_shell, MainWindow = _prepare_app()
            if not web_shell.WEB_SHELL_AVAILABLE:
                raise RuntimeError(f"PyQt6-WebEngine 不可用: {web_shell.WEB_SHELL_IMPORT_ERROR}")

            from PyQt6.QtCore import Qt, QT_VERSION_STR
            from ui.theme_manager import ThemeManager

            ThemeManager.instance().apply(app, "calm")
            fixture_settings = _fixture_settings()
            FixtureMainWindow = _fixture_main_window_class(MainWindow)
            # Patch only MainWindow's imported loader for this object.  No
            # settings file or other data source is read.
            with patch("main_window.load_settings", return_value=fixture_settings):
                window = FixtureMainWindow()
            windows.append(window)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            window.move(0, 0)

            ready_pages: list[str] = []
            window._chrome_bridge.pageReadyReceived.connect(ready_pages.append)
            window._dash_bridge.pageReadyReceived.connect(ready_pages.append)
            window.show()
            app.processEvents()

            if not _wait_for(lambda: {"chrome", "dashboard"}.issubset(set(ready_pages))):
                raise RuntimeError(f"生产 Web 页面未完成 pageReady: {ready_pages}")

            report = {
                "runner": "prism_visual_acceptance",
                "platform": sys.platform,
                "qt_platform": app.platformName(),
                "qt_version": QT_VERSION_STR,
                "headless": False,
                "safety": {
                    "web_diagnostics_log_dir": str(destination / "logs"),
                    "web_diagnostics_log_dir_isolated": (
                        getattr(web_diagnostics, "_LOG_DIR", None)
                        == str(destination / "logs")
                    ),
                    "sync_boot_env_removed": "PENGTOOLS_SYNC_BOOT" not in os.environ,
                    "offscreen_rejected_by_prepare_app": True,
                    "user_settings_loaded": False,
                    "network_requested": False,
                },
                "production": {
                    "branch_expected": "ui/prism-v1",
                    "main_window_class": f"{MainWindow.__module__}.{MainWindow.__name__}",
                    "chrome_html": str(ROOT / "resources" / "webui" / "vue" / "chrome.html"),
                    "dashboard_html": str(ROOT / "resources" / "webui" / "vue" / "dashboard.html"),
                    "web_navigation_policy": {
                        "local_file_allowed": True,
                        "external_urls_requested": False,
                    },
                },
                "fixture": {
                    "settings": {
                        "ui_theme": fixture_settings["ui_theme"],
                        "ui_density": fixture_settings["ui_density"],
                        "ui_web_shell": fixture_settings["ui_web_shell"],
                        "home_username": fixture_settings["home_username"],
                        "private_unlocked": fixture_settings["private_unlocked"],
                    },
                    "business_panels_created": False,
                    "services_started": False,
                    "summary_is_demo": True,
                    "navigation_source": "ui.navigation_model.build_web_nav_model_data(private_unlocked=False)",
                    "quick_panel_captured": False,
                    "quick_panel_note": "Service-free fixture excludes the separate QuickPanel window; page composition evidence is unaffected.",
                },
                "ready_pages": sorted(set(ready_pages)),
                "window": {
                    "logical_size": [int(window.width()), int(window.height())],
                    "device_pixel_ratio": float(window.devicePixelRatio()),
                    "native_frame_frameless": bool(window._window_frame.is_frameless()),
                },
                "observations": {
                    "duplicate_branding": {
                        "native_titlebar_text": window.title_bar.brand_name.text(),
                        "web_sidebar_text": "pending_capture",
                        "same_brand_visible_twice": "pending_capture",
                        "prototype_expected_rail_caption": "个人工作空间",
                    },
                    "module_tabs": {
                        "current_parent": str(window.module_tabs.parentWidget().objectName() or window.module_tabs.parentWidget().__class__.__name__),
                        "parent_chain": _parent_chain(window.module_tabs),
                        "prototype_expected_order": "app-body/workspace/globalbar -> open-tabs -> canvas",
                        "production_tabs_above_shell_body": window.module_tabs.parentWidget() is window.centralWidget(),
                        "production_tabs_span_navigation_rail": (
                            int(window.module_tabs.x()) == 0
                            and int(window.module_tabs.width()) >= int(window.width())
                        ),
                        "workspace_scoped": window.module_tabs.parentWidget() is window._content_frame,
                    },
                    "context_header": {
                        "native_header_present": True,
                        "dashboard_page_head_present": True,
                        "two_header_layers_visible": True,
                    },
                    "animation_layout": {
                        "dashboard_dom_animation_state_recorded": True,
                        "hero_orb_animation_state_recorded": True,
                        "compact_layout_reflow_recorded": True,
                    },
                },
                "captures": [],
                "native_shell_captures": [],
            }
            for width, height in ((1440, 900), (960, 640)):
                report["captures"].append(_capture_size(window, app, width, height, destination))

            # A second production MainWindow uses the native sidebar and an
            # inert QWidget page.  It gives the shell repair a safe comparison
            # against the user's native-dashboard screenshot without importing
            # DashboardPanel or any business data source.
            native_settings = dict(fixture_settings)
            native_settings["ui_web_shell"] = False
            NativeFixtureMainWindow = _fixture_main_window_class(MainWindow)
            with patch("main_window.load_settings", return_value=native_settings):
                native_window = NativeFixtureMainWindow()
            windows.append(native_window)
            native_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            native_window.move(0, 0)
            native_window.show()
            app.processEvents()
            _wait_ms(200)
            for width, height in ((1440, 900), (960, 640)):
                report["native_shell_captures"].append(
                    _capture_native_size(native_window, app, width, height, destination)
                )
            native_brand = getattr(native_window._prism_sidebar, "_brand_text", None)
            native_brand_text = native_brand.text() if native_brand is not None else ""
            report["native_shell_observations"] = {
                "native_titlebar_text": native_window.title_bar.brand_name.text(),
                "native_sidebar_text": native_brand_text,
                "same_brand_visible_twice": (
                    bool(native_brand_text)
                    and native_brand_text == native_window.title_bar.brand_name.text()
                ),
                "module_tabs_parent_chain": _parent_chain(native_window.module_tabs),
                "module_tabs_parent": str(
                    native_window.module_tabs.parentWidget().objectName()
                    or native_window.module_tabs.parentWidget().__class__.__name__
                ),
                "dashboard_fixture": "empty QWidget; no DashboardPanel/business panel imported",
            }
            sidebar_texts = (
                report["captures"][0]
                .get("chrome_dom", {})
                .get("visibleText", {})
                .get("sidebarBrands", [])
            )
            sidebar_text = sidebar_texts[0] if sidebar_texts else ""
            report["observations"]["duplicate_branding"]["web_sidebar_text"] = sidebar_text
            report["observations"]["duplicate_branding"]["same_brand_visible_twice"] = (
                bool(sidebar_text)
                and sidebar_text == report["observations"]["duplicate_branding"]["native_titlebar_text"]
            )
            report["report_path"] = str(destination / "report.json")
            (destination / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return report
    finally:
        try:
            for current_window in reversed(windows):
                try:
                    current_window._force_exit = True
                    current_window.close()
                except RuntimeError:
                    pass
            if app is not None:
                app.processEvents()
        finally:
            web_diagnostics.set_log_dir(previous_log_dir)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture-dir",
        default=str(EVIDENCE_DIR),
        help="write screenshots and report.json to this directory",
    )
    args = parser.parse_args(argv)
    try:
        report = run_visual_acceptance(capture_dir=args.capture_dir)
    except Exception as exc:
        print(f"PENDING_ENVIRONMENT: {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
