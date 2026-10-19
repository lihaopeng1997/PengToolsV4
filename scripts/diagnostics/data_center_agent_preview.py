"""Render the standalone data-center Agent panel with synthetic events only.

The script never opens the application, reads user configuration, connects to
a database, or calls a model.  It writes screenshots into a newly-created
named directory under the operating system temporary directory and prints the
paths when finished.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from tools.data_center.contracts import AgentEventEmitter, AgentEventType
from ui.data_center.agent_panel import DataCenterAgentPanel
from ui.theme_manager import ThemeManager


def _emit_demo_timeline(panel: DataCenterAgentPanel) -> None:
    """Feed a small deterministic event timeline through the public facade."""

    sink = panel.start_run(
        "preview-run-001",
        "preview-tab-001",
        target="MySQL · demo_catalog / sales",
        model="内网模型（模拟） · read-only",
    )
    emitter = AgentEventEmitter("preview-run-001", "preview-tab-001")
    events = (
        (AgentEventType.RUN_STARTED, {"status": "preparing"}),
        (AgentEventType.TURN_STARTED, {"turn_index": 0}),
        (AgentEventType.MODEL_REASONING_DELTA, {"text": "先确认对象范围，再执行只读查询。"}),
        (AgentEventType.MODEL_TEXT_DELTA, {"text": "我会检查最近的订单记录，并返回可核验的查询证据。\n"}),
        (
            AgentEventType.TOOL_CALL,
            {
                "name": "query_readonly",
                "call_id": "call-preview-01",
                "arguments": {
                    "sql": "SELECT order_id, status FROM orders ORDER BY created_at DESC LIMIT 5;"
                },
            },
        ),
        (AgentEventType.TOOL_STARTED, {"name": "query_readonly", "call_id": "call-preview-01", "progress": 0.45}),
        (
            AgentEventType.TOOL_RESULT,
            {
                "name": "query_readonly",
                "call_id": "call-preview-01",
                "ok": True,
                "code": "OK",
                "query_id": "q-preview-0001",
                "evidence_id": "ev-preview-0001",
                "data": {
                    "columns": ["order_id", "status"],
                    "rows": [["A-1042", "paid"], ["A-1041", "pending"]],
                    "truncated": False,
                },
                "progress": 0.85,
            },
        ),
        (AgentEventType.MODEL_TEXT_DELTA, {"text": "结果显示 2 条示例记录；证据来自只读查询 q-preview-0001。"}),
        (
            AgentEventType.FINAL,
            {
                "reasoning_summary": "已按目标连接范围完成一次只读查询。",
                "verified": True,
                "verification_status": "verified",
            },
        ),
    )
    for event_type, payload in events:
        sink(emitter.emit(event_type, payload))


def _load_preview_font(app: QApplication, requested: str | Path | None = None) -> str | None:
    """Load a local preview-only CJK font and override the calm QSS family.

    The font is registered only in this diagnostic process.  Nothing is
    copied into the product or its resources.
    """

    candidates: list[Path] = []
    if requested:
        candidates.append(Path(requested))
    windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates.extend((windows_dir / "Fonts" / "simsun.ttc", Path("C:/Windows/Fonts/simsun.ttc")))
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        return None
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    family = str(families[0]).strip() if families else ""
    if not family:
        return None
    preview_font = QFont(family, 12)
    app.setFont(preview_font)
    # ThemeManager.apply installs a QWidget font-family rule.  Append this
    # diagnostic-only override after calm so the local font actually wins.
    app.setStyleSheet(app.styleSheet() + f"\nQWidget {{ font-family: '{family}'; }}\n")
    return family


def render_preview(font_path: str | Path | None = None) -> Path:
    app = QApplication.instance() or QApplication(sys.argv)
    # Read and apply the approved calm theme only to this preview QApplication.
    ThemeManager.instance().apply(app, "calm", font_size=12)
    selected_font = _load_preview_font(app, font_path)
    panel = DataCenterAgentPanel()
    panel.setWindowTitle("数据中心 Agent · 仿真预览")
    panel.resize(1_440, 900)
    panel.show()
    _emit_demo_timeline(panel)
    # The panel deliberately receives events through queued Qt delivery.
    app.processEvents()
    app.processEvents()

    output_dir = Path(tempfile.mkdtemp(prefix="pengtools_data_center_agent_preview_"))
    output_paths: list[Path] = []
    for width, height in ((1_440, 900), (960, 640), (340, 900)):
        # Apply the compact layout before requesting a sidebar width so a
        # previously rendered wide state cannot veto the resize.
        panel._apply_narrow_layout(width < 520)
        panel.resize(width, height)
        panel.show()
        app.processEvents()
        if panel.width() != width or panel.height() != height:
            raise RuntimeError(
                f"preview resize was constrained: requested {width}x{height}, "
                f"got {panel.width()}x{panel.height()}"
            )
        path = output_dir / f"agent_panel_{width}x{height}.png"
        image = panel.grab()
        if image.width() != width or image.height() != height:
            raise RuntimeError(
                f"preview image dimensions mismatch: requested {width}x{height}, "
                f"got {image.width()}x{image.height()}"
            )
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"could not save preview screenshot: {path}")
        output_paths.append(path)

    panel.close()
    panel.deleteLater()
    app.processEvents()
    print(f"preview_dir={output_dir}")
    print(f"preview_font={selected_font or 'system fallback'}")
    for path in output_paths:
        print(f"screenshot={path}")
    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render the standalone Agent panel simulation preview")
    parser.add_argument(
        "--font-path",
        default=None,
        help="optional local font file used only for this preview (defaults to Windows SimSun when available)",
    )
    render_preview(parser.parse_args().font_path)
