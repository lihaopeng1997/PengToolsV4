"""Bounded Agent presentation for the unified data-center workbench.

The widget consumes the UI-independent :class:`AgentEvent` contract and emits
user intent signals. It never creates a model client, database connection,
driver, or tool registry. The reasoning channel accepts only explicit
provider-supplied deltas/summaries; it never infers hidden model reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping
import weakref

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.contracts import AgentEvent
from ui import layout_metrics as _lm
from ui.design_system import apply_button, apply_surface
from ui.field_metrics import apply_caption
from ui.motion import motion_enabled
from ui.theme_manager import ThemeManager


DEFAULT_CHANNEL_LIMITS: dict[str, int] = {
    "reasoning": 6_000,
    "answer": 12_000,
    "evidence": 6_000,
    "sql": 8_000,
    "result": 8_000,
}

_TERMINAL_EVENTS = {"final", "needs_clarification", "cancelled", "failed", "budget_exhausted"}
_REASONING_EVENTS = {"model_reasoning_delta", "reasoning_delta", "reasoning_summary"}
_TEXT_EVENTS = {"model_text_delta", "text_delta"}
_TOOL_EVENTS = {"tool_call", "tool_started", "tool_result"}


def _event_name(value: Any) -> str:
    """Normalize an enum or string event type without importing a runner."""

    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _bounded_label(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _query_worker_notice(payload: Mapping[str, Any]) -> str | None:
    """Accept only the scalar worker-state fields produced by the runner."""

    context_status = payload.get("context_status")
    if type(context_status) is not str or context_status != "deferred":
        return None
    pending = payload.get("query_worker_pending")
    status = payload.get("query_worker_status")
    if type(pending) is not bool or type(status) is not str:
        return None
    if pending is True and status == "pending":
        return "pending"
    if pending is False and status == "unverified":
        return "unverified"
    return None


@dataclass
class _BoundedChannel:
    """Small incremental text buffer with a hard display cap."""

    limit: int
    marker: str = ""
    value: str = ""
    truncated: bool = False

    def append(self, text: Any) -> str:
        if self.truncated or text is None:
            return ""
        chunk = str(text)
        if not chunk:
            return ""
        remaining = max(0, int(self.limit) - len(self.value))
        if len(chunk) <= remaining:
            self.value += chunk
            return chunk
        accepted = chunk[:remaining]
        self.value += accepted
        self.truncated = True
        suffix = self.marker or f"\n… 显示已截断（上限 {self.limit} 字符）"
        return accepted + suffix

    @property
    def display(self) -> str:
        if self.truncated:
            return self.value + (self.marker or f"\n… 显示已截断（上限 {self.limit} 字符）")
        return self.value


@dataclass(frozen=True)
class _QueuedEvent:
    event: AgentEvent
    generation: int


class AgentEventFacade:
    """Generation-bound event sink captured by one host run.

    A worker can retain this callable while a panel is rebound to another
    run.  Its generation is carried with every queued event, so late events
    from the old host cannot append to the new run even when identifiers are
    accidentally reused.
    """

    __slots__ = ("_panel_ref", "generation")

    def __init__(self, panel: "DataCenterAgentPanel", generation: int):
        self._panel_ref = weakref.ref(panel)
        self.generation = int(generation)

    def __call__(self, event: AgentEvent) -> None:
        panel = self._panel_ref()
        if panel is not None:
            panel.queue_event(event, generation=self.generation)

    def emit(self, event: AgentEvent) -> None:
        self(event)

    def post(self, event: AgentEvent) -> None:
        self(event)


class _EventSignalBridge(QObject):
    """Owns the queued signal so a worker never needs to know about Qt UI."""

    received = pyqtSignal(object)


class DataCenterAgentPanel(QWidget):
    """A compact, bilingual Agent event surface for a bound workbench.

    The panel is deliberately host-agnostic.  Call :meth:`start_run`, keep
    the returned :class:`AgentEventFacade`, and feed it pure ``AgentEvent``
    objects from a worker.  All widget mutations happen on the panel's Qt
    thread through a queued signal.
    """

    stop_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal(str)
    cancel_pending = pyqtSignal(str)
    cancel_pending_changed = pyqtSignal(bool)
    copy_sql_requested = pyqtSignal(str)
    put_sql_requested = pyqtSignal(str)
    manual_sql_requested = pyqtSignal(str)
    prompt_send_requested = pyqtSignal(str)
    new_conversation_requested = pyqtSignal()
    event_received = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        target: str = "—",
        model: str = "—",
        run_id: str = "",
        tab_id: str = "",
        channel_limits: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("data-center-agent-panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        limits = dict(DEFAULT_CHANNEL_LIMITS)
        if channel_limits:
            for key, value in channel_limits.items():
                try:
                    limits[key] = max(1, int(value))
                except (TypeError, ValueError):
                    continue
        self._limits = limits
        self._channels: dict[str, _BoundedChannel] = {
            key: _BoundedChannel(value) for key, value in limits.items()
        }
        self._query_ids: list[str] = []
        self._evidence_ids: list[str] = []
        self._run_id = ""
        self._tab_id = ""
        self._binding_generation = 0
        self._last_sequence = 0
        self._active = False
        self._stop_was_requested = False
        self._is_cancel_pending = False
        self._terminal_seen: str | None = None
        self._disposed = False
        self._motion_is_enabled = motion_enabled()
        self._narrow_mode: bool | None = None
        self._display_language = "zh"
        self._display_mode = "query"
        self._phase_texts = ("未绑定运行", "No run bound")
        self._result_status_texts = ("尚无最终结果", "No final result yet")
        self._result_status_role = "neutral"

        self._build_ui(target=target, model=model)

        # Explicit QueuedConnection makes the worker/UI boundary clear even
        # when a synthetic source emits from the panel's own thread.
        self._event_bridge = _EventSignalBridge(self)
        self._event_bridge.received.connect(
            self._consume_queued_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self.event_received.connect(
            self._consume_signal_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self._apply_theme_roles()
        self.set_display_context(self._display_language, self._display_mode)
        self._refresh_send_availability()
        if run_id or tab_id:
            self.start_run(run_id, tab_id, target=target, model=model)
        # Start from the compact arrangement so a host may create the panel
        # directly at sidebar width before Qt has a chance to enforce the
        # wider layout's size hint.  A subsequent resize switches to wide.
        self._apply_narrow_layout(True)

    # ------------------------------------------------------------------
    # Public host seam
    # ------------------------------------------------------------------
    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def tab_id(self) -> str:
        return self._tab_id

    @property
    def binding_generation(self) -> int:
        return self._binding_generation

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    def set_display_context(self, language: str, mode: str) -> None:
        """Update presentation copy for the host-selected language and mode.

        This method deliberately changes labels, placeholders and tooltips only.
        It does not touch the fixed run target, model choice, controller,
        snapshot, event generation, queued events, or cancellation state.
        """

        language_value = str(language or "").strip().lower()
        self._display_language = "zh" if language_value.startswith("zh") else "en"
        mode_value = str(mode or "").strip().lower()
        self._display_mode = mode_value if mode_value in {"query", "draft"} else "query"
        zh = self._display_language == "zh"
        draft = self._display_mode == "draft"

        self.eyebrow_label.setText("数据中心 · AGENT" if zh else "DATA CENTER · AGENT")
        self.title_label.setText(
            ("草稿助手" if draft else "只读查询助手") if zh else
            ("Draft assistant" if draft else "Read-only query assistant")
        )
        self.title_label.setAccessibleName("Agent 面板标题" if zh else "Agent panel title")
        subtitle = (
            ("草稿模式不访问数据库；生成内容需人工检查。" if draft else "仅显示模型明确提供的摘要与工具证据。")
            if zh else
            ("Draft mode does not access the database; review generated content before use." if draft else
             "Shows only explicit provider summaries and tool evidence.")
        )
        self._preview_subtitle.setProperty("fullCopy", subtitle)
        self._preview_subtitle.setText(subtitle)
        self._preview_subtitle.setToolTip(subtitle)
        self.title_label.setToolTip(subtitle)
        self._apply_preview_subtitle_elision()
        self.preview_badge.setText(
            ("草稿" if draft else "只读") if zh else
            ("Draft" if draft else "Read-only")
        )
        mode_description = (
            ("草稿模式：不访问数据库，也不执行生成内容。" if draft else "只读查询会通过工具执行；没有查询证据时，回答尚未核对数据库。")
            if zh else
            ("Draft mode: no database access and generated content is not executed." if draft else
             "Read-only query runs through tools; an answer without query evidence has not been checked against the database.")
        )
        self.preview_badge.setToolTip(mode_description)
        self.preview_badge.setAccessibleName(
            ("草稿模式" if draft else "只读模式") if zh else
            ("Draft mode" if draft else "Read-only mode")
        )
        self.read_only_label.setText(
            ("草稿 · 不访问数据库" if draft else "只读模式") if zh else
            ("Draft · no database access" if draft else "Read-only")
        )
        self.read_only_label.setAccessibleName(
            ("草稿模式" if draft else "只读模式") if zh else
            ("Draft mode" if draft else "Read-only mode")
        )
        self.read_only_label.setToolTip(mode_description)
        self.setToolTip(
            "数据中心 Agent 工作区" if zh else "Data-center Agent workspace"
        )
        self.target_caption.setText("目标" if zh else "Target")
        self.model_caption.setText("模型" if zh else "Model")
        self.new_conversation_button.setText("新建对话" if zh else "New chat")
        self.new_conversation_button.setAccessibleName("新建 Agent 对话" if zh else "Start a new Agent chat")
        self.new_conversation_button.setToolTip("开始新的对话" if zh else "Start a new conversation")
        self.stop_button.setText("停止" if zh else "Stop")
        self.stop_button.setAccessibleName("停止 Agent 运行" if zh else "Stop Agent run")
        self.stop_button.setToolTip("请求停止当前运行" if zh else "Request cancellation of the active run")
        self.prompt_caption.setText("向 Agent 提问" if zh else "Ask the Agent")
        self.prompt_input.setPlaceholderText(
            ("描述要生成的查询草稿；草稿模式不会访问数据库。" if draft else "描述要查询的数据，例如：汇总本月订单状态。")
            if zh else
            ("Describe a query draft; draft mode does not access the database." if draft else
             "Describe the data you want to query, for example: summarize this month's order status.")
        )
        self.prompt_input.setAccessibleName("Agent 提问" if zh else "Agent prompt")
        self.send_button.setText("发送" if zh else "Send")
        self.send_button.setAccessibleName("发送 Agent 提问" if zh else "Send Agent prompt")
        self.send_button.setToolTip("发送当前提问" if zh else "Send the current prompt")
        self.conversation_title.setText("回答流" if zh else "Conversation")
        self.reasoning_caption.setText(
            "思考摘要 · 仅显示模型明确提供的摘要/增量" if zh else
            "Reasoning summary · only explicit provider content is shown"
        )
        self.answer_caption.setText(
            "正文回答 · 验证、推断与截断状态由事件标记" if zh else
            "Answer · verification, inference, and truncation are event-marked"
        )
        self.evidence_title.setText("工具证据" if zh else "Tool evidence")
        self.sql_caption.setText("已查 SQL" if zh else "Queried SQL")
        self.copy_sql_button.setText("复制 SQL" if zh else "Copy SQL")
        self.put_sql_button.setText("应用到编辑器" if zh else "Apply to editor")
        self.copy_sql_button.setToolTip("复制已显示的完整 SQL" if zh else "Copy the complete displayed SQL")
        self.put_sql_button.setToolTip(
            "将已显示的完整 SQL 放入手动编辑器" if zh else
            "Insert the complete displayed SQL into the manual editor"
        )
        self.result_caption.setText("结果预览" if zh else "Result preview")
        self.result_status_label.setToolTip(
            "验证与截断状态来自 Agent 事件" if zh else
            "Verification and truncation status comes from Agent events"
        )
        self.query_id_label.setToolTip("只读工具返回的 query ID" if zh else "Query IDs returned by read-only tools")
        self._apply_target_label_elision()
        self._set_placeholder_copy()
        self._apply_phase_copy()
        self._apply_result_status_copy()
        for key, channel in self._channels.items():
            channel.marker = self._truncation_marker(key)
            view = self._channel_view(key)
            view.setPlainText(channel.display)
            if channel.truncated:
                view.setToolTip(self._truncation_tooltip(key, channel.limit))
        self._refresh_query_label()
        if self.progress_bar.property("hasProgress") is not True:
            self.progress_bar.setFormat("等待模型提供进度" if zh else "Waiting for provider progress")

    def _truncation_marker(self, channel: str) -> str:
        if self._display_language == "zh":
            return f"\n… 显示已截断（上限 {self._limits.get(channel, 0)} 字符）"
        return f"\n… display truncated ({self._limits.get(channel, 0)} character limit)"

    def _truncation_tooltip(self, channel: str, limit: int) -> str:
        return (
            f"此区域已达到显示上限：{limit} 字符" if self._display_language == "zh" else
            f"This channel reached its {limit}-character display limit"
        )

    def _channel_view(self, name: str) -> QPlainTextEdit:
        return {
            "reasoning": self.reasoning_view,
            "answer": self.answer_view,
            "evidence": self.evidence_view,
            "sql": self.sql_view,
            "result": self.result_preview_view,
        }[name]

    def _set_placeholder_copy(self) -> None:
        zh = self._display_language == "zh"
        copies = {
            "reasoning": ("等待模型明确提供的摘要…", "Waiting for an explicit provider summary…"),
            "answer": ("尚无回答", "No answer yet"),
            "evidence": ("尚无工具调用", "No tool calls yet"),
            "sql": ("尚无 SQL", "No SQL yet"),
            "result": ("尚无结果预览", "No result preview yet"),
        }
        for name, view in (
            ("reasoning", self.reasoning_view), ("answer", self.answer_view),
            ("evidence", self.evidence_view), ("sql", self.sql_view),
            ("result", self.result_preview_view),
        ):
            view.setPlaceholderText(copies[name][0 if zh else 1])

    def _apply_phase_copy(self) -> None:
        text = self._phase_texts[0 if self._display_language == "zh" else 1]
        self.phase_label.setText(_bounded_label(text, 160) or "—")
        self.phase_label.setToolTip(text or "—")

    def _apply_result_status_copy(self) -> None:
        text = self._result_status_texts[0 if self._display_language == "zh" else 1]
        self.result_status_label.setText(_bounded_label(text, 220))
        self.result_status_label.setProperty("statusRole", self._result_status_role)
        style = self.result_status_label.style()
        if style is not None:
            style.unpolish(self.result_status_label)
            style.polish(self.result_status_label)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_cancel_pending(self) -> bool:
        return self._is_cancel_pending

    @property
    def channel_limits(self) -> dict[str, int]:
        return dict(self._limits)

    def start_run(
        self,
        run_id: str,
        tab_id: str,
        *,
        target: str | None = None,
        model: str | None = None,
        clear: bool = True,
    ) -> AgentEventFacade:
        """Bind a new host run and return its generation-bound event sink."""

        self._binding_generation += 1
        self._run_id = str(run_id or "")
        self._tab_id = str(tab_id or "")
        self._last_sequence = 0
        self._active = True
        self._stop_was_requested = False
        self._terminal_seen = None
        self._set_cancel_pending(False)
        if target is not None:
            self._set_value_label(self.target_value_label, target, limit=180)
            self._apply_target_label_elision()
        if model is not None:
            self._set_value_label(self.model_value_label, model, limit=180)
        if clear:
            self._reset_channels()
        self._set_phase("等待 Agent 事件…", "Waiting for Agent events…")
        self._set_progress_value(None)
        self.stop_button.setEnabled(True)
        self._refresh_send_availability()
        self.copy_sql_button.setEnabled(True)
        self.put_sql_button.setEnabled(True)
        self.copy_sql_button.setToolTip(
            "复制已显示的完整 SQL" if self._display_language == "zh" else "Copy the complete displayed SQL"
        )
        self.put_sql_button.setToolTip(
            "将已显示的完整 SQL 放入手动编辑器" if self._display_language == "zh" else
            "Insert the complete displayed SQL into the manual editor"
        )
        return AgentEventFacade(self, self._binding_generation)

    # Alias used by small preview hosts.
    bind_run = start_run

    def event_sink(self) -> AgentEventFacade:
        """Return a sink tied to the current generation."""

        return AgentEventFacade(self, self._binding_generation)

    def queue_event(self, event: AgentEvent, generation: int | None = None) -> None:
        """Queue a DTO from any thread for UI-thread consumption."""

        if self._disposed or not isinstance(event, AgentEvent):
            return
        bound_generation = self._binding_generation if generation is None else int(generation)
        self._event_bridge.received.emit(_QueuedEvent(event, bound_generation))

    post_event = queue_event

    def reset_run(self) -> None:
        """Invalidate all previously captured sinks and clear the panel."""

        self._binding_generation += 1
        self._run_id = ""
        self._tab_id = ""
        self._last_sequence = 0
        self._active = False
        self._stop_was_requested = False
        self._terminal_seen = None
        self._set_cancel_pending(False)
        self._reset_channels()
        self._set_phase("未绑定运行", "No run bound")
        self._set_progress_value(None)
        self.stop_button.setEnabled(False)
        self._refresh_send_availability()

    def acknowledge_prompt(self, prompt: str | None = None) -> bool:
        """Clear a prompt after the host has accepted that exact request.

        When ``prompt`` is supplied, a newer user edit is preserved rather
        than being cleared by a late host acknowledgement.
        """

        current = self.prompt_input.toPlainText().strip()
        if not current:
            return False
        if prompt is not None and str(prompt).strip() != current:
            return False
        self.prompt_input.clear()
        return True

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is getattr(self, "stop_button", None) and event.type() == QEvent.Type.EnabledChange:
            self._sync_compact_rail()
        if (
            watched is getattr(self, "target_value_label", None)
            and event.type() == QEvent.Type.Resize
            and not getattr(self, "_eliding_target", False)
        ):
            self._eliding_target = True
            try:
                self._apply_target_label_elision()
            finally:
                self._eliding_target = False
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "_heading_row"):
            return
        self._apply_narrow_layout(self.width() < 520)
        self._apply_preview_subtitle_elision()
        self._apply_target_label_elision()

    # ------------------------------------------------------------------
    # UI construction and visual roles
    # ------------------------------------------------------------------
    def _build_ui(self, *, target: str, model: str) -> None:
        root = QVBoxLayout(self)
        # Let the host request a narrow width while the current wide layout is
        # still mounted; resizeEvent then swaps to the compact arrangement.
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(_lm.SPACING_PAGE, _lm.SPACING_PAGE, _lm.SPACING_PAGE, _lm.SPACING_PAGE)
        root.setSpacing(_lm.SPACING_CARD)

        header = QFrame(self)
        header.setObjectName("agent-context-card")
        apply_surface(header, "card")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(_lm.PAGE_GAP_NARROW, 8, _lm.PAGE_GAP_NARROW, 8)
        header_layout.setSpacing(5)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        eyebrow = QLabel("DATA CENTER · AGENT", header)
        eyebrow.setObjectName("section-title")
        heading.addWidget(eyebrow)
        title = QLabel(header)
        title.setObjectName("page-title")
        title.setAccessibleName("Agent panel title")
        heading.addWidget(title)
        subtitle = QLabel(header)
        subtitle.setObjectName("agent-preview-subtitle")
        # The fixed mode explanation is kept to one line and elided to the
        # real rail width; its full wording stays available as a tooltip.
        subtitle.setWordWrap(False)
        subtitle.setMinimumWidth(0)
        subtitle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        heading.addWidget(subtitle)
        # 紧凑头部：装饰性眉标与说明行不常驻，说明保留在 tooltip 里
        eyebrow.hide()
        subtitle.hide()
        self.eyebrow_label = eyebrow
        self.title_label = title
        self._heading_row = heading_row
        self._preview_subtitle = subtitle
        heading_row.addLayout(heading, 1)

        self.preview_badge = QLabel(header)
        self.preview_badge.setObjectName("status-pill")
        self.preview_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_badge.setToolTip("")
        self.preview_badge.setAccessibleName("Agent mode")
        heading_row.addWidget(self.preview_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addLayout(heading_row)

        context_grid = QGridLayout()
        context_grid.setHorizontalSpacing(6)
        context_grid.setVerticalSpacing(2)
        self.target_caption = QLabel(header)
        self.model_caption = QLabel(header)
        apply_caption(self.target_caption)
        apply_caption(self.model_caption)
        self.target_value_label = QLabel(header)
        self.model_value_label = QLabel(header)
        self.target_value_label.setObjectName("field-value")
        self.model_value_label.setObjectName("field-value")
        self.target_value_label.setProperty("singleLineElide", True)
        # Target names can be longer than the narrow rail. Keep the value to
        # one predictable line and let _apply_target_label_elision() preserve
        # the full value in the tooltip.
        self.target_value_label.setWordWrap(False)
        # 头部行内元素变化会改变标签实际宽度；宽度变了就按新宽度重新省略
        self.target_value_label.installEventFilter(self)
        self.model_value_label.setWordWrap(True)
        self._set_value_label(self.target_value_label, target, limit=180)
        self._set_value_label(self.model_value_label, model, limit=180)
        context_grid.addWidget(self.target_caption, 0, 0)
        context_grid.addWidget(self.target_value_label, 0, 1)
        context_grid.addWidget(self.model_caption, 0, 2)
        context_grid.addWidget(self.model_value_label, 0, 3)
        self.read_only_label = QLabel(header)
        self.read_only_label.setObjectName("system-chip")
        self.read_only_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.read_only_label.setToolTip("")
        context_grid.addWidget(self.read_only_label, 0, 4)
        context_grid.setColumnStretch(1, 1)
        context_grid.setColumnStretch(3, 1)
        header_layout.addLayout(context_grid)
        self._context_grid = context_grid
        self._context_widgets = (
            self.target_caption,
            self.target_value_label,
            self.model_caption,
            self.model_value_label,
            self.read_only_label,
        )

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        phase_caption = QLabel(header)
        apply_caption(phase_caption)
        self.phase_caption = phase_caption
        self.phase_label = QLabel(header)
        self.phase_label.setObjectName("status-pill")
        self.phase_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.phase_label.setToolTip("")
        phase_caption.hide()
        status_row.addWidget(phase_caption)
        status_row.addWidget(self.phase_label, 1)
        self.progress_bar = QProgressBar(header)
        self.progress_bar.setObjectName("agent-progress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumWidth(190)
        self.progress_bar.hide()
        status_row.addWidget(self.progress_bar)
        header_layout.addLayout(status_row)
        self._status_row = status_row

        # 新建对话 / 停止并入标题行，不再单独占一行
        action_row = heading_row
        self.new_conversation_button = QPushButton(header)
        self.new_conversation_button.setObjectName("new-conversation-button")
        apply_button(self.new_conversation_button, "ghost", compact=True)
        self.new_conversation_button.clicked.connect(self._on_new_conversation)
        action_row.addWidget(self.new_conversation_button)
        self.stop_button = QPushButton(header)
        self.stop_button.setObjectName("stop-agent-button")
        apply_button(self.stop_button, "danger", compact=True)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.stop_button.setEnabled(False)
        action_row.addWidget(self.stop_button)
        root.addWidget(header)

        scroll = QScrollArea(self)
        scroll.setObjectName("agent-panel-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_scroll = scroll
        body = QWidget(scroll)
        body.setObjectName("agent-panel-body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(_lm.SPACING_CARD)

        composer = QFrame(self)
        composer.setObjectName("agent-prompt-card")
        apply_surface(composer, "card")
        self.prompt_composer = composer
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(_lm.PAGE_GAP_NARROW, 8, _lm.PAGE_GAP_NARROW, 8)
        composer_layout.setSpacing(5)
        prompt_caption = QLabel(composer)
        prompt_caption.setObjectName("zone-title")
        composer_layout.addWidget(prompt_caption)
        self.prompt_caption = prompt_caption
        self.prompt_input = QPlainTextEdit(composer)
        self.prompt_input.setObjectName("agent-prompt-input")
        self.prompt_input.setAccessibleName("Agent prompt")
        self.prompt_input.setMinimumWidth(0)
        self.prompt_input.setMinimumHeight(64)
        self.prompt_input.setMaximumHeight(112)
        self.prompt_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.prompt_input.textChanged.connect(self._refresh_send_availability)
        composer_layout.addWidget(self.prompt_input)
        prompt_actions = QHBoxLayout()
        prompt_actions.addStretch(1)
        self.send_button = QPushButton(composer)
        self.send_button.setObjectName("send-agent-prompt-button")
        self.send_button.setAccessibleName("Send Agent prompt")
        apply_button(self.send_button, "primary", compact=True)
        self.send_button.clicked.connect(self._on_send_prompt)
        prompt_actions.addWidget(self.send_button)
        composer_layout.addLayout(prompt_actions)

        conversation = QFrame(body)
        conversation.setObjectName("agent-thread-card")
        apply_surface(conversation, "card")
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(14, 12, 14, 12)
        conversation_layout.setSpacing(8)
        conversation_title = QLabel("回答流", conversation)
        conversation_title.setObjectName("zone-title")
        conversation_layout.addWidget(conversation_title)
        self.conversation_title = conversation_title

        reasoning_caption = QLabel("思考摘要 · 仅显示模型明确提供的摘要/增量", conversation)
        reasoning_caption.setObjectName("agent-channel-caption")
        reasoning_caption.setWordWrap(True)
        conversation_layout.addWidget(reasoning_caption)
        self.reasoning_caption = reasoning_caption
        self.reasoning_view = self._make_readonly_view(
            conversation,
            "reasoning-summary-view",
            minimum_height=68,
            maximum_height=190,
        )
        conversation_layout.addWidget(self.reasoning_view)
        self.reasoning_summary_view = self.reasoning_view

        answer_caption = QLabel("正文回答 · 已验证 / 推断 / 截断范围由事件明确标记", conversation)
        answer_caption.setObjectName("agent-channel-caption")
        answer_caption.setWordWrap(True)
        conversation_layout.addWidget(answer_caption)
        self.answer_caption = answer_caption
        self.answer_view = self._make_readonly_view(
            conversation,
            "agent-answer-view",
            minimum_height=126,
            maximum_height=300,
        )
        conversation_layout.addWidget(self.answer_view)
        self.text_view = self.answer_view
        self.result_status_label = QLabel("尚无最终结果", conversation)
        self.result_status_label.setObjectName("status-pill")
        self.result_status_label.setToolTip("Verification and truncation status comes from the Agent event")
        conversation_layout.addWidget(self.result_status_label, 0, Qt.AlignmentFlag.AlignLeft)
        # 紧凑侧栏：没有任何内容时的一句上手提示（默认隐藏，独立面板不显示）
        self.compact_hint = QLabel(body)
        self.compact_hint.setObjectName("field-hint")
        self.compact_hint.setWordWrap(True)
        self.compact_hint.hide()
        body_layout.addWidget(self.compact_hint)
        body_layout.addWidget(conversation)
        self._conversation_card = conversation

        evidence = QFrame(body)
        evidence.setObjectName("agent-context-card")
        apply_surface(evidence, "card")
        evidence_layout = QVBoxLayout(evidence)
        evidence_layout.setContentsMargins(14, 12, 14, 12)
        evidence_layout.setSpacing(8)
        evidence_title = QLabel("工具证据", evidence)
        evidence_title.setObjectName("zone-title")
        evidence_layout.addWidget(evidence_title)
        self.evidence_title = evidence_title
        self.evidence_view = self._make_readonly_view(
            evidence,
            "agent-evidence-view",
            minimum_height=68,
            maximum_height=180,
        )
        evidence_layout.addWidget(self.evidence_view)

        sql_row = QHBoxLayout()
        sql_caption = QLabel("已查 SQL", evidence)
        apply_caption(sql_caption)
        sql_row.addWidget(sql_caption)
        self.sql_caption = sql_caption
        sql_row.addStretch(1)
        self.copy_sql_button = QPushButton("复制 SQL", evidence)
        self.copy_sql_button.setObjectName("copy-sql-button")
        apply_button(self.copy_sql_button, "secondary", compact=True)
        self.copy_sql_button.clicked.connect(self._on_copy_sql)
        sql_row.addWidget(self.copy_sql_button)
        self.put_sql_button = QPushButton("应用到编辑器", evidence)
        self.put_sql_button.setObjectName("put-sql-button")
        apply_button(self.put_sql_button, "secondary", compact=True)
        self.put_sql_button.clicked.connect(self._on_put_sql)
        sql_row.addWidget(self.put_sql_button)
        evidence_layout.addLayout(sql_row)
        self.sql_view = self._make_readonly_view(
            evidence,
            "agent-sql-view",
            minimum_height=58,
            maximum_height=160,
        )
        evidence_layout.addWidget(self.sql_view)
        self.query_id_label = QLabel("query_id：—", evidence)
        self.query_id_label.setObjectName("agent-query-id")
        self.query_id_label.setWordWrap(True)
        self.query_id_label.setToolTip("Query IDs returned by read-only tools")
        evidence_layout.addWidget(self.query_id_label)

        result_caption = QLabel("结果预览", evidence)
        result_caption.setObjectName("agent-channel-caption")
        evidence_layout.addWidget(result_caption)
        self.result_caption = result_caption
        self.result_preview_view = self._make_readonly_view(
            evidence,
            "agent-result-preview-view",
            minimum_height=80,
            maximum_height=220,
        )
        evidence_layout.addWidget(self.result_preview_view)
        body_layout.addWidget(evidence)
        self._evidence_card = evidence
        body_layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        root.addWidget(composer, 0)

        # Compatibility aliases keep existing workbench actions stable.
        self.target_label = self.target_value_label
        self.model_label = self.model_value_label
        self.stop_btn = self.stop_button
        self.copy_sql_btn = self.copy_sql_button
        self.put_sql_btn = self.put_sql_button
        self.new_conversation_btn = self.new_conversation_button
        self.prompt_edit = self.prompt_input
        self.send_btn = self.send_button
        self.result_view = self.result_preview_view

    def _apply_narrow_layout(self, narrow: bool) -> None:
        """Keep the panel usable at the shared 340px right-rail width."""

        narrow = bool(narrow)
        if self._narrow_mode == narrow:
            return
        self._narrow_mode = narrow

        self._heading_row.setDirection(QBoxLayout.Direction.LeftToRight)
        self._status_row.setDirection(QBoxLayout.Direction.LeftToRight)
        self._preview_subtitle.setMaximumWidth(320 if narrow else 16777215)

        for widget in self._context_widgets:
            self._context_grid.removeWidget(widget)
        if narrow:
            self._context_grid.addWidget(self.target_caption, 0, 0)
            self._context_grid.addWidget(self.target_value_label, 0, 1)
            self._context_grid.addWidget(self.model_caption, 1, 0)
            self._context_grid.addWidget(self.model_value_label, 1, 1)
            self._context_grid.addWidget(self.read_only_label, 2, 0, 1, 2)
            self._context_grid.setColumnStretch(0, 0)
            self._context_grid.setColumnStretch(1, 1)
            self.target_value_label.setMaximumWidth(16777215)
            self.model_value_label.setMaximumWidth(16777215)
        else:
            self._context_grid.addWidget(self.target_caption, 0, 0)
            self._context_grid.addWidget(self.target_value_label, 0, 1)
            self._context_grid.addWidget(self.model_caption, 0, 2)
            self._context_grid.addWidget(self.model_value_label, 0, 3)
            self._context_grid.addWidget(self.read_only_label, 0, 4)
            self._context_grid.setColumnStretch(1, 1)
            self._context_grid.setColumnStretch(2, 0)
            self._context_grid.setColumnStretch(3, 1)
            self.target_value_label.setMaximumWidth(16777215)
            self.model_value_label.setMaximumWidth(16777215)
        self.prompt_input.setMinimumHeight(60 if narrow else 68)
        self.prompt_input.setMaximumHeight(88 if narrow else 112)
        self._context_grid.invalidate()
        self.updateGeometry()
        self._apply_preview_subtitle_elision()
        self._apply_target_label_elision()

    def _apply_preview_subtitle_elision(self) -> None:
        """Fit fixed mode copy to one line without losing its full wording."""

        label = getattr(self, "_preview_subtitle", None)
        if label is None:
            return
        full_text = str(label.property("fullCopy") or label.toolTip() or label.text())
        width = label.contentsRect().width()
        if width <= 0:
            label.setText(full_text)
            label.setToolTip(full_text)
            return
        rendered = label.fontMetrics().elidedText(
            full_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        if label.text() != rendered:
            label.setText(rendered)
            label.updateGeometry()
        if label.toolTip() != full_text:
            label.setToolTip(full_text)

    def _apply_target_label_elision(self) -> None:
        """Fit the fixed target to one line while retaining its full tooltip."""

        label = getattr(self, "target_value_label", None)
        if label is None:
            return
        full_text = label.property("fullValueText") or label.toolTip() or label.text() or "—"
        full_text = str(full_text)
        width = label.contentsRect().width()
        if width <= 0:
            label.setToolTip(full_text)
            return
        rendered = label.fontMetrics().elidedText(
            full_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        if label.text() != rendered:
            label.setText(rendered)
        label.setToolTip(full_text)

    # ------------------------------------------------------------------
    # 嵌入式紧凑侧栏（数据中心右栏）
    # ------------------------------------------------------------------
    def set_compact_rail(self, enabled: bool) -> None:
        """DBX 式侧栏：隐藏重复的目标/模型与说明文字，各块只在有内容时出现。"""
        self._compact_rail = bool(enabled)
        if self._compact_rail:
            # 侧栏宽度有限：去掉外边距，允许内容随栏宽收缩，不被右侧截断
            self.layout().setContentsMargins(0, 4, 0, 0)
            self.setMinimumWidth(0)
            self.prompt_input.setMinimumWidth(0)
        if self._compact_rail and not getattr(self, "_compact_rail_wired", False):
            self._compact_rail_wired = True
            for view in (self.reasoning_view, self.answer_view, self.evidence_view, self.sql_view, self.result_preview_view):
                view.textChanged.connect(self._sync_compact_rail)
            self.stop_button.installEventFilter(self)
        zh = getattr(self, "_display_language", "zh") == "zh"
        self.compact_hint.setText(
            "在下方用中文描述要查的数据，例如“本月每天的订单数”。"
            "Agent 会先看表结构，再只读执行并给出结论；生成的 SQL 可一键放入编辑器。"
            if zh else
            "Describe the data you need below. The Agent reads the schema, runs read-only SQL, "
            "and summarizes; generated SQL can be applied to the editor."
        )
        self._sync_compact_rail()

    def _sync_compact_rail(self, *_args) -> None:
        if not getattr(self, "_compact_rail", False):
            return
        has = lambda view: bool(view.toPlainText().strip())  # noqa: E731
        reasoning, answer = has(self.reasoning_view), has(self.answer_view)
        sql, preview, evidence = has(self.sql_view), has(self.result_preview_view), has(self.evidence_view)
        running = self.stop_button.isEnabled()
        self.stop_button.setVisible(running)
        for widget in self._context_widgets:
            widget.setVisible(False)
        self.phase_label.setVisible(running)
        for caption in (self.conversation_title, self.reasoning_caption, self.answer_caption,
                        self.evidence_title, self.query_id_label):
            caption.setVisible(False)
        self.reasoning_view.setVisible(reasoning)
        self.answer_view.setVisible(answer)
        self.result_status_label.setVisible(answer or running)
        self._conversation_card.setVisible(reasoning or answer or running)
        self.evidence_view.setVisible(evidence)
        for widget in (self.sql_caption, self.copy_sql_button, self.put_sql_button, self.sql_view):
            widget.setVisible(sql)
        self.result_caption.setVisible(preview)
        self.result_preview_view.setVisible(preview)
        self._evidence_card.setVisible(sql or preview or evidence)
        self.compact_hint.setVisible(not (reasoning or answer or running or sql or preview or evidence))

    @staticmethod
    def _make_readonly_view(parent: QWidget, object_name: str, *, minimum_height: int, maximum_height: int) -> QPlainTextEdit:
        view = QPlainTextEdit(parent)
        view.setObjectName(object_name)
        view.setReadOnly(True)
        view.setUndoRedoEnabled(False)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        view.setMinimumHeight(minimum_height)
        view.setMaximumHeight(maximum_height)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        view.setProperty("readOnlyField", True)
        view.setPlaceholderText("Waiting for events…")
        return view

    def _apply_theme_roles(self) -> None:
        """Use the existing theme and design-system roles without global QSS."""

        palette = ThemeManager.instance().palette("calm")
        self.setProperty("themeId", "calm")
        self.setProperty("reducedMotion", not self._motion_is_enabled)
        self.setToolTip("")
        # Standalone previews may not have the application stylesheet.  Read
        # semantic tokens for a restrained fallback palette only.
        if not QApplication.instance().styleSheet():
            self.setStyleSheet(
                "QWidget#data-center-agent-panel { background: %s; color: %s; }"
                " QProgressBar#agent-progress { border: 1px solid %s; border-radius: 5px;"
                " text-align: center; background: %s; color: %s; }"
                " QProgressBar#agent-progress::chunk { background: %s; border-radius: 4px; }"
                % (
                    palette.get("APP_BG", _lm.APP_BG),
                    palette.get("TEXT", _lm.TEXT),
                    palette.get("BORDER", _lm.BORDER),
                    palette.get("SURFACE_SOFT", _lm.SURFACE_SOFT),
                    palette.get("TEXT_MUTED", _lm.TEXT_MUTED),
                    palette.get("PRIMARY", _lm.PRIMARY),
                )
            )

    @staticmethod
    def _set_value_label(label: QLabel, value: Any, *, limit: int) -> None:
        text = _bounded_label(value, limit)
        text = text or "—"
        label.setProperty("fullValueText", str(value or "—"))
        label.setText(text)
        label.setToolTip(str(value or "—"))
        if label.property("singleLineElide"):
            width = label.contentsRect().width()
            if width > 0:
                label.setText(
                    label.fontMetrics().elidedText(
                        text,
                        Qt.TextElideMode.ElideRight,
                        width,
                    )
                )

    def _copy(self, zh: str, en: str) -> str:
        return zh if self._display_language == "zh" else en

    # ------------------------------------------------------------------
    # Event queue and filtering
    # ------------------------------------------------------------------
    @pyqtSlot(object)
    def _consume_signal_event(self, value: Any) -> None:
        if isinstance(value, _QueuedEvent):
            self._consume_queued_event(value)
        elif isinstance(value, AgentEvent):
            self._consume_queued_event(_QueuedEvent(value, self._binding_generation))

    @pyqtSlot(object)
    def _consume_queued_event(self, queued: Any) -> None:
        if self._disposed:
            return
        if isinstance(queued, _QueuedEvent):
            event, generation = queued.event, queued.generation
        elif isinstance(queued, AgentEvent):
            event, generation = queued, self._binding_generation
        else:
            return
        if generation != self._binding_generation:
            return
        if self._terminal_seen is not None:
            return
        if str(event.run_id) != self._run_id or str(event.tab_id) != self._tab_id:
            return
        try:
            sequence = int(event.sequence)
        except (TypeError, ValueError):
            return
        if sequence <= self._last_sequence:
            return
        self._last_sequence = sequence
        self._handle_event(event)

    def _handle_event(self, event: AgentEvent) -> None:
        event_type = _event_name(event.event_type)
        payload = dict(event.payload or {})
        self._progress_from_payload(payload)
        if event_type in _REASONING_EVENTS:
            # Only the explicit fields are accepted.  No generic payload scan
            # can accidentally surface hidden model chain-of-thought.
            text = payload.get("text") if "text" in payload else payload.get("reasoning_summary")
            self._append_channel("reasoning", text, self.reasoning_view)
            self._set_phase("已收到明确提供的摘要", "Provider summary received")
            return
        if event_type in _TEXT_EVENTS:
            from tools.agent_core.text_protocol import visible_model_text
            shown = visible_model_text(str(payload.get("text") or ""))
            if shown:
                self._append_channel("answer", shown, self.answer_view)
            self._set_phase("正在生成回答…", "Generating answer…")
            return
        if event_type in _TOOL_EVENTS:
            self._handle_tool_event(event_type, payload)
            return

        if event_type == "run_started":
            from tools.agent_diag import log_agent_event, log_file_path
            log_agent_event('dc_run_started', log=log_file_path())
            status = payload.get("status") or ("准备中" if self._display_language == "zh" else "Preparing")
            status = _bounded_label(status, 100)
            self._set_phase(f"运行开始 · {status}", f"Run started · {status}")
            return
        if event_type == "turn_started":
            turn = payload.get("turn_index")
            if str(turn).isdigit():
                self._set_phase(f"模型处理中 · 第 {int(turn) + 1} 轮", f"Model turn {int(turn) + 1}")
            else:
                self._set_phase("模型处理中", "Model is processing")
            return
        if event_type == "budget":
            worker_notice = _query_worker_notice(payload)
            if worker_notice == "pending":
                self._set_phase("已检测到查询 worker 尚未完成", "Query worker completion is still pending")
            elif worker_notice == "unverified":
                self._set_phase("无法确认查询 worker 已完成", "Could not verify query worker completion")
            else:
                self._set_phase("预算状态已更新", "Budget status updated")
            return
        if event_type == "needs_clarification":
            self._append_channel(
                "answer",
                payload.get("question") or payload.get("text") or self._copy("需要澄清", "Clarification is needed"),
                self.answer_view,
            )
            self._terminal_seen = event_type
            self._active = False
            self._set_cancel_pending(False)
            self.stop_button.setEnabled(False)
            self._set_phase("等待澄清", "Waiting for clarification")
            self._set_result_status("需要澄清", "warning", "Clarification needed")
            return
        if event_type == "final":
            self._handle_final(payload)
            return
        if event_type == "cancelled":
            self._terminal_seen = event_type
            self._active = False
            self._stop_was_requested = False
            self._set_cancel_pending(False)
            self.stop_button.setEnabled(False)
            reason = str(payload.get("reason") or payload.get("error") or "").strip()
            timed_out = "timeout" in reason.lower() or str(payload.get("status") or "").lower() == "timeout"
            phase = "已超时取消" if timed_out else "已取消"
            phase_en = "Cancelled after timeout" if timed_out else "Cancelled"
            if reason:
                phase += f" · {_bounded_label(reason, 100)}"
                phase_en += f" · {_bounded_label(reason, 100)}"
            worker_notice = _query_worker_notice(payload)
            if worker_notice == "pending":
                phase += " · 本轮终止时查询 worker 尚未完成"
                phase_en += " · query worker was unfinished when this run terminated"
            elif worker_notice == "unverified":
                phase += " · 本轮终止时无法确认查询 worker 已完成"
                phase_en += " · worker completion was unverified at run termination"
            self._set_phase(phase, phase_en)
            self._set_result_status(phase, "warning", phase_en)
            return
        if event_type == "failed":
            self._terminal_seen = event_type
            self._active = False
            self._set_cancel_pending(False)
            self.stop_button.setEnabled(False)
            error = payload.get("error") or payload.get("message") or self._copy("执行失败", "Execution failed")
            self._set_phase("执行失败", "Execution failed")
            self._append_channel(
                "evidence",
                (f"失败：{_bounded_label(error, 240)}\n" if self._display_language == "zh" else
                 f"Failure: {_bounded_label(error, 240)}\n"),
                self.evidence_view,
            )
            self._set_result_status("失败项已标记", "danger", "Failure recorded")
            return
        if event_type == "budget_exhausted":
            self._terminal_seen = event_type
            self._active = False
            self._set_cancel_pending(False)
            self.stop_button.setEnabled(False)
            reason = _bounded_label(payload.get("reason") or "budget", 100)
            phase = f"预算耗尽 · {reason}"
            phase_en = f"Budget exhausted · {reason}"
            worker_notice = _query_worker_notice(payload)
            if worker_notice == "pending":
                phase += " · 本轮终止时查询 worker 尚未完成"
                phase_en += " · query worker was unfinished when this run terminated"
            elif worker_notice == "unverified":
                phase += " · 本轮终止时无法确认查询 worker 已完成"
                phase_en += " · worker completion was unverified at run termination"
            self._set_phase(phase, phase_en)
            self._set_result_status(phase, "warning", phase_en)

    def _handle_final(self, payload: Mapping[str, Any]) -> None:
        # A stop request remains pending until the runner confirms cancellation;
        # a late final event must never claim success after cancellation.
        if self._stop_was_requested or self._is_cancel_pending:
            self._set_phase("停止请求已发送，等待取消确认…", "Stop requested; waiting for cancellation confirmation…")
            self._set_result_status("取消待确认", "warning", "Cancellation pending")
            return
        reasoning_summary = payload.get("reasoning_summary")
        if reasoning_summary:
            self._append_reasoning_summary(reasoning_summary)
        if not self._channels["answer"].value:
            final_text = payload.get("text") if "text" in payload else payload.get("answer")
            if final_text:
                self._append_channel("answer", final_text, self.answer_view)
        self._active = False
        self._terminal_seen = "final"
        self.stop_button.setEnabled(False)
        verified = payload.get("verified")
        status = payload.get("verification_status")
        if verified is True or status == "verified":
            label = "已完成 · 已验证"
            label_en = "Completed · verified"
        elif payload.get("inferred") is True or status == "inferred":
            label = "已完成 · 含推断"
            label_en = "Completed · includes inference"
        else:
            label = "已完成 · 未验证"
            label_en = "Completed · unverified"
        if payload.get("truncated") or self._any_channel_truncated():
            label += " · 显示有截断"
            label_en += " · display truncated"
        self._set_phase("已完成", "Completed")
        self._set_result_status(label, "success" if verified is True else "warning", label_en)
        self._refresh_send_availability()

    def _append_reasoning_summary(self, summary: Any) -> None:
        """Append a final summary only when it adds information to deltas."""

        text = str(summary or "")
        if not text:
            return
        current = self._channels["reasoning"].value
        if not current:
            self._append_channel("reasoning", text, self.reasoning_view)
            return
        if text == current or text in current:
            return
        if text.startswith(current):
            self._append_channel("reasoning", text[len(current):], self.reasoning_view)
            return
        self._append_channel("reasoning", "\n" + text, self.reasoning_view)

    def _handle_tool_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        name = _bounded_label(payload.get("name") or self._copy("工具", "tool"), 100)
        call_id = _bounded_label(payload.get("call_id") or payload.get("id") or "—", 80)
        sql = self._extract_sql(payload)
        if sql:
            self._append_channel("sql", sql.rstrip() + "\n", self.sql_view)
        if event_type == "tool_call":
            self._append_channel(
                "evidence",
                (f"调用 {name} · call_id={call_id}\n" if self._display_language == "zh" else
                 f"Call {name} · call_id={call_id}\n"),
                self.evidence_view,
            )
            self._set_phase(f"已请求工具 · {name}", f"Tool requested · {name}")
            return
        if event_type == "tool_started":
            self._append_channel(
                "evidence",
                (f"开始 {name} · call_id={call_id}\n" if self._display_language == "zh" else
                 f"Started {name} · call_id={call_id}\n"),
                self.evidence_view,
            )
            self._set_phase(f"工具查询中 · {name}", f"Tool running · {name}")
            return

        query_id = payload.get("query_id") or payload.get("queryId")
        evidence_id = payload.get("evidence_id") or payload.get("evidenceId")
        query_ids = payload.get("query_ids")
        evidence_ids = payload.get("evidence_ids")
        ids = [query_id]
        if isinstance(query_ids, (list, tuple)):
            ids.extend(query_ids)
        for item in ids:
            self._remember_id(self._query_ids, item)
        ids = [evidence_id]
        if isinstance(evidence_ids, (list, tuple)):
            ids.extend(evidence_ids)
        for item in ids:
            self._remember_id(self._evidence_ids, item)
        code = _bounded_label(payload.get("code") or ("OK" if payload.get("ok", True) else self._copy("失败", "Failed")), 80)
        evidence_line = (
            f"结果 {name} · {code} · query_id={_bounded_label(query_id or '—', 80)}"
            if self._display_language == "zh" else
            f"Result {name} · {code} · query_id={_bounded_label(query_id or '—', 80)}"
        )
        if evidence_id:
            evidence_line += f" · evidence_id={_bounded_label(evidence_id, 80)}"
        evidence_line += "\n"
        self._append_channel("evidence", evidence_line, self.evidence_view)
        preview = payload.get("result_preview") if "result_preview" in payload else payload.get("data")
        if preview is not None:
            self._append_channel("result", self._safe_preview(preview) + "\n", self.result_preview_view)
        self._refresh_query_label()
        self._set_phase("已收到工具结果", "Tool result received")

    @staticmethod
    def _extract_sql(payload: Mapping[str, Any]) -> str:
        keys = ("sql", "query", "statement", "query_text")
        for source in (payload, payload.get("arguments"), payload.get("data")):
            if not isinstance(source, Mapping):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _safe_preview(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _remember_id(bucket: list[str], value: Any) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text and text not in bucket:
            bucket.append(text)

    # ------------------------------------------------------------------
    # Display and actions
    # ------------------------------------------------------------------
    def _append_channel(self, name: str, text: Any, view: QPlainTextEdit) -> None:
        channel = self._channels[name]
        visible = channel.append(text)
        if not visible:
            return
        scroll_bar = view.verticalScrollBar()
        old_scroll_value = scroll_bar.value()
        follow_tail = old_scroll_value >= scroll_bar.maximum() - 1
        cursor = view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(visible)
        view.setTextCursor(cursor)
        if follow_tail:
            view.ensureCursorVisible()
        else:
            scroll_bar.setValue(old_scroll_value)
        if channel.truncated:
            view.setToolTip(self._truncation_tooltip(name, channel.limit))
            if name == "sql":
                self.copy_sql_button.setEnabled(False)
                self.put_sql_button.setEnabled(False)
                warning = (
                    "SQL 显示已截断，复制/放入编辑器已停用，避免误称为完整查询。"
                    if self._display_language == "zh" else
                    "SQL display is truncated; copy and editor handoff are disabled so it is not presented as complete."
                )
                self.copy_sql_button.setToolTip(warning)
                self.put_sql_button.setToolTip(warning)

    def _reset_channels(self) -> None:
        self._channels = {
            key: _BoundedChannel(limit, marker=self._truncation_marker(key))
            for key, limit in self._limits.items()
        }
        self._query_ids.clear()
        self._evidence_ids.clear()
        for view in (
            self.reasoning_view,
            self.answer_view,
            self.evidence_view,
            self.sql_view,
            self.result_preview_view,
        ):
            view.clear()
            view.setToolTip("")
        self._refresh_query_label()
        self._set_result_status("尚无最终结果", "neutral", "No final result yet")
        self.copy_sql_button.setEnabled(True)
        self.put_sql_button.setEnabled(True)
        self.copy_sql_button.setToolTip(
            "复制已显示的完整 SQL" if self._display_language == "zh" else "Copy the complete displayed SQL"
        )
        self.put_sql_button.setToolTip(
            "将已显示的完整 SQL 放入手动编辑器" if self._display_language == "zh" else
            "Insert the complete displayed SQL into the manual editor"
        )

    def _set_phase(self, value: Any, english: Any | None = None) -> None:
        zh_text = _bounded_label(value, 160) or "—"
        en_text = _bounded_label(value if english is None else english, 160) or "—"
        self._phase_texts = (zh_text, en_text)
        self._apply_phase_copy()

    def _set_progress_value(self, value: Any) -> None:
        if value is None:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat(
                "等待模型提供进度" if self._display_language == "zh" else "Waiting for provider progress"
            )
            self.progress_bar.setProperty("hasProgress", False)
            self.progress_bar.hide()
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        number = number * 100 if 0 <= number <= 1 else number
        number = max(0.0, min(100.0, number))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(number))
        self.progress_bar.setFormat(f"{int(number)}%")
        self.progress_bar.setProperty("hasProgress", True)
        self.progress_bar.show()

    def _progress_from_payload(self, payload: Mapping[str, Any]) -> None:
        raw = payload.get("progress", payload.get("percent"))
        if isinstance(raw, Mapping):
            current = raw.get("value", raw.get("current"))
            total = raw.get("total")
            try:
                raw = float(current) / float(total) * 100 if float(total) > 0 else None
            except (TypeError, ValueError, ZeroDivisionError):
                raw = None
        if raw is not None:
            self._set_progress_value(raw)

    def _refresh_query_label(self) -> None:
        if not self._query_ids:
            self.query_id_label.setText("query_id：—" if self._display_language == "zh" else "query_id: —")
            return
        joined = ", ".join(_bounded_label(item, 80) for item in self._query_ids)
        prefix = "query_id：" if self._display_language == "zh" else "query_id: "
        self.query_id_label.setText(_bounded_label(f"{prefix}{joined}", 240))
        self.query_id_label.setToolTip("\n".join(self._query_ids))

    def _set_result_status(self, text: str, role: str, english: str | None = None) -> None:
        self._result_status_texts = (
            _bounded_label(text, 220),
            _bounded_label(text if english is None else english, 220),
        )
        self._result_status_role = str(role or "neutral")
        self._apply_result_status_copy()

    def _any_channel_truncated(self) -> bool:
        return any(channel.truncated for channel in self._channels.values())

    def _set_cancel_pending(self, value: bool) -> None:
        value = bool(value)
        changed = value != self._is_cancel_pending
        self._is_cancel_pending = value
        self._refresh_send_availability()
        if changed:
            self.cancel_pending_changed.emit(value)

    @pyqtSlot()
    def _refresh_send_availability(self) -> None:
        if not hasattr(self, "send_button"):
            return
        has_prompt = bool(self.prompt_input.toPlainText().strip())
        self.send_button.setEnabled(has_prompt and not self._active and not self._is_cancel_pending)

    @pyqtSlot()
    def _on_send_prompt(self) -> None:
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt or self._active or self._is_cancel_pending:
            return
        self.prompt_send_requested.emit(prompt)

    @pyqtSlot()
    def _on_stop_clicked(self) -> None:
        if not self._active or self._is_cancel_pending or not self._run_id:
            return
        self._stop_was_requested = True
        self._set_cancel_pending(True)
        self.stop_button.setEnabled(False)
        self._set_phase("停止请求已发送，等待取消确认…", "Stop requested; waiting for cancellation confirmation…")
        self._set_result_status("取消待确认", "warning", "Cancellation pending")
        self.stop_requested.emit(self._run_id)
        self.cancel_requested.emit(self._run_id)
        self.cancel_pending.emit(self._run_id)

    @pyqtSlot()
    def _on_copy_sql(self) -> None:
        sql = self._channels["sql"].value.rstrip()
        self.copy_sql_requested.emit(sql)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(sql)

    @pyqtSlot()
    def _on_put_sql(self) -> None:
        sql = self._channels["sql"].value.rstrip()
        self.put_sql_requested.emit(sql)
        self.manual_sql_requested.emit(sql)

    @pyqtSlot()
    def _on_new_conversation(self) -> None:
        self.reset_run()
        self.new_conversation_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._disposed = True
        self._binding_generation += 1
        self._active = False
        self._terminal_seen = None
        self._set_cancel_pending(False)
        bridge = getattr(self, "_event_bridge", None)
        if bridge is not None:
            try:
                bridge.received.disconnect(self._consume_queued_event)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(event)


# Short names keep the host import concise.
AgentPanel = DataCenterAgentPanel


__all__ = ["AgentEventFacade", "AgentPanel", "DataCenterAgentPanel"]
