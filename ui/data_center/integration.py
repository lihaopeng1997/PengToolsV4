"""Production Qt assembly for the existing read-only data-center Agent.

This widget is a thin host around the existing DataCenterAgentPanel,
DataCenterAgentController, DataCenterAgentRuntime, and the existing guarded
read-only profile hosts.  Configuration and driver creation happen only in
the controller's QThread.  A submitted run receives one immutable target and
model revision; subsequent UI changes cancel it and wait for controller
cleanup before installing the next snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import uuid
from typing import Any, Callable

from PyQt6.QtCore import QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tools.data_center.contracts import RunContext
from tools.data_center.host_nosql import NoSQLReadOnlyHost
from tools.data_center.host_relational import RelationalReadOnlyHost
from tools.data_center.host_model_adapter import AgentModelHostAdapter
from tools.data_center.host_runtime import DataCenterAgentRuntime
from tools.data_center.profile_host import (
    ConnectionChoice,
    DataCenterProfileHost,
    ModelChoice,
    ProfileHostError,
)
from ui.data_center.agent_controller import AgentRunSnapshot, DataCenterAgentController
from ui.data_center.agent_panel import DataCenterAgentPanel
from ui.design_system import apply_button


_RELATIONAL_PROVIDERS = {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"}
_NOSQL_PROVIDERS = {"redis", "mongodb"}


@dataclass(frozen=True, slots=True)
class _AgentResultTarget:
    connection_id: str
    profile_revision: str
    provider: str
    mode: str
    database: str
    schema_allowlist: tuple[str, ...]
    target_label: str
    model_label: str
    intent: str


class _CatalogThread(QThread):
    completed = pyqtSignal(int, object, str, str)

    def __init__(self, generation: int, profile_host: DataCenterProfileHost, parent=None):
        super().__init__(parent)
        self.generation = int(generation)
        self.profile_host = profile_host

    def run(self) -> None:
        try:
            choices, active_id = self.profile_host.list_model_choices()
            self.completed.emit(self.generation, choices, active_id, "")
        except ProfileHostError as exc:
            self.completed.emit(self.generation, (), "", exc.code)
        except Exception:
            self.completed.emit(self.generation, (), "", "MODEL_CATALOG_UNAVAILABLE")


class _RelationalHostWithoutDialect:
    """Keep OceanBase's fixed provider/mode binding independent of UI dialect labels."""

    def __init__(self, host: RelationalReadOnlyHost):
        self._host = host

    def profile_snapshot(self, connection_id: str):
        return self._host.profile_snapshot(connection_id)

    def create_factory(self, connection_id: str, *, profile_revision=None, provider=None, mode=None):
        return self._host.create_factory(
            connection_id,
            profile_revision=profile_revision,
            provider=provider,
            mode=mode,
        )


class _RuntimeOwner:
    """Close runtime-owned workers and the shared NoSQL lease factory."""

    def __init__(self, runtime: DataCenterAgentRuntime, nosql_factory: Any = None):
        self._runtime = runtime
        self._nosql_factory = nosql_factory
        self._closed = False

    def run(self, *args, **kwargs):
        callback = kwargs.get("on_event")
        terminal_failed = False
        if callable(callback):
            def observe_terminal(event: Any) -> None:
                nonlocal terminal_failed
                event_type = getattr(event, "event_type", "")
                event_type = getattr(event_type, "value", event_type)
                terminal_failed = terminal_failed or str(event_type or "").strip().casefold() == "failed"
                callback(event)

            kwargs["on_event"] = observe_terminal
        result = self._runtime.run(*args, **kwargs)
        if terminal_failed:
            # Some runners report a terminal FAILED event and return normally.
            # Preserve that failure in the controller outcome so the workbench
            # status cannot subsequently claim that the run completed.
            raise _ReportedRuntimeFailure()
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._runtime.close()
        finally:
            close = getattr(self._nosql_factory, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class _ReportedRuntimeFailure(RuntimeError):
    """An Agent runtime reported FAILED through its event stream."""


class DataCenterWorkbenchAgent(QWidget):
    """Configured-model and fixed-target host embedded in a database workbench."""

    def __init__(
        self,
        *,
        selection_provider: Callable[[], Mapping[str, Any] | None],
        put_sql: Callable[[str], None],
        language: str = "zh",
        profile_host: DataCenterProfileHost | None = None,
        model_transport: Any = None,
        relational_connector: Any = None,
        redis_client_factory: Any = None,
        mongo_client_factory: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("data-center-agent-host")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._selection_provider = selection_provider
        self._put_sql = put_sql
        self._profile_host = profile_host or DataCenterProfileHost()
        self._model_transport = model_transport
        self._relational_connector = relational_connector
        self._redis_client_factory = redis_client_factory
        self._mongo_client_factory = mongo_client_factory
        self._language = language
        self._models: tuple[ModelChoice, ...] = ()
        self._model_choices_by_id: dict[str, ModelChoice] = {}
        self._catalog_threads: set[_CatalogThread] = set()
        self._catalog_generation = 0
        self._controller: DataCenterAgentController | None = None
        self._snapshot: AgentRunSnapshot | None = None
        self._connection_choice: ConnectionChoice | None = None
        self._last_result_target: _AgentResultTarget | None = None
        self._last_result_intent: str | None = None
        self._last_run_outcome: str | None = None
        self._refresh_after_cleanup = False
        self._closing = False
        self._controls_row = None
        self._controls_narrow: bool | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        row = QHBoxLayout()
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("只读查询" if language == "zh" else "Read-only query", "query")
        self.mode_combo.addItem("仅生成草稿" if language == "zh" else "Draft only", "draft")
        self.model_combo = QComboBox(self)
        # 大字号/窄工作台下模型名可收缩（悬停看全名），不把整个工作台撑出 960
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(6)
        self.model_combo.setMinimumWidth(120)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh_button = QPushButton("刷新模型" if language == "zh" else "Refresh models", self)
        apply_button(self.refresh_button, "ghost", compact=True)
        self.refresh_button.clicked.connect(self.refresh_models)
        self._controls_row = row
        row.addWidget(self.mode_combo, 0)
        row.addWidget(self.model_combo, 1)
        row.addWidget(self.refresh_button, 0)
        root.addLayout(row)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("ops-safety-note")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.agent_panel = DataCenterAgentPanel(self, target="尚未固定目标", model="尚未选择模型")
        self._set_production_panel_copy()
        self.agent_panel.prompt_input.setEnabled(False)
        # 空间够时面板撑满；窗口矮时整体滚动，而不是把整个工作台撑高
        self.agent_scroll = QScrollArea(self)
        self.agent_scroll.setObjectName("data-center-agent-scroll")
        self.agent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.agent_scroll.setWidgetResizable(True)
        self.agent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.agent_scroll.setWidget(self.agent_panel)
        root.addWidget(self.agent_scroll, 1)

        self.mode_combo.currentIndexChanged.connect(self._on_selection_changed)
        self.model_combo.currentIndexChanged.connect(self._on_selection_changed)
        self.agent_panel.put_sql_requested.connect(self._on_put_sql_requested)
        self.agent_panel.prompt_send_requested.connect(self._on_prompt_requested)

        self.refresh_models()
        self._set_status("读取已配置模型…" if language == "zh" else "Loading configured models…")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        narrow = self.width() < 520
        if self._controls_narrow != narrow and self._controls_row is not None:
            self._controls_narrow = narrow
            self._controls_row.setDirection(
                QHBoxLayout.Direction.TopToBottom if narrow else QHBoxLayout.Direction.LeftToRight
            )

    def set_language(self, language: str) -> None:
        self._language = str(language or "zh")
        zh = self._language == "zh"
        self.mode_combo.setItemText(0, "只读查询" if zh else "Read-only query")
        self.mode_combo.setItemText(1, "仅生成草稿" if zh else "Draft only")
        self.refresh_button.setText("刷新模型" if zh else "Refresh models")
        self._set_production_panel_copy()
        self._refresh_displayed_target()
        self._refresh_display_status()

    def _display_target_label(
        self,
        label: Any,
        intent: str | None = None,
        *,
        internal_unbound: bool = False,
    ) -> str:
        value = str(label or "").strip()
        if str(intent or "").strip().casefold() == "draft":
            return "草稿模式 · 不访问数据库" if self._language == "zh" else "Draft mode · no database access"
        if internal_unbound:
            return "尚未固定目标" if self._language == "zh" else "No target is fixed"
        return value

    def _refresh_displayed_target(self) -> None:
        controller = self._controller
        if controller is not None and controller.is_running:
            snapshot = controller.snapshot
            label = self._display_target_label(
                snapshot.target,
                snapshot.context.intent,
                internal_unbound=(not snapshot.context.connection_id and snapshot.context.intent != "draft"),
            )
            model = snapshot.model
        elif self._last_result_target is not None:
            target = self._last_result_target
            label = self._display_target_label(
                target.target_label,
                target.intent,
                internal_unbound=(not target.connection_id and target.intent != "draft"),
            )
            model = target.model_label
        elif self._snapshot is not None:
            snapshot = self._snapshot
            label = self._display_target_label(
                snapshot.target,
                snapshot.context.intent,
                internal_unbound=(not snapshot.context.connection_id and snapshot.context.intent != "draft"),
            )
            model = snapshot.model
        else:
            label = "尚未固定目标" if self._language == "zh" else "No target is fixed"
            model = "尚未选择模型" if self._language == "zh" else "No model selected"
        self.agent_panel._set_value_label(self.agent_panel.target_value_label, label, limit=180)
        self.agent_panel.target_value_label.setToolTip(label)
        self.agent_panel._set_value_label(self.agent_panel.model_value_label, model, limit=180)

    def _refresh_display_status(self) -> None:
        if self._last_run_outcome is not None:
            self._apply_last_run_status()
            return
        if self._last_result_target is not None:
            last = self._display_target_label(
                self._last_result_target.target_label,
                self._last_result_target.intent,
                internal_unbound=(
                    not self._last_result_target.connection_id
                    and self._last_result_target.intent != "draft"
                ),
            )
            if self._snapshot is not None:
                next_target = self._display_target_label(
                    self._snapshot.target,
                    self._snapshot.context.intent,
                    internal_unbound=(
                        not self._snapshot.context.connection_id
                        and self._snapshot.context.intent != "draft"
                    ),
                )
                model = self._snapshot.model
            else:
                next_target = last
                model = self._last_result_target.model_label
            self._set_status(
                f"上次回答固定于 {last}；下一次提问将使用 {next_target} · {model}。"
                if self._language == "zh" else
                f"Last answer is fixed to {last}; the next prompt will use {next_target} · {model}."
            )
            return
        if self._snapshot is not None:
            target = self._display_target_label(
                self._snapshot.target,
                self._snapshot.context.intent,
                internal_unbound=(
                    not self._snapshot.context.connection_id
                    and self._snapshot.context.intent != "draft"
                ),
            )
            self._set_status(
                f"已固定：{target} · {self._snapshot.model}"
                if self._language == "zh" else
                f"Fixed: {target} · {self._snapshot.model}"
            )

    def _sync_panel_display_context(self) -> None:
        controller = self._controller
        if controller is not None and controller.is_running:
            mode = str(controller.snapshot.context.intent or "query")
        elif self._last_result_target is not None:
            mode = self._last_result_target.intent
        elif self._last_result_intent is not None:
            mode = self._last_result_intent
        elif self._snapshot is not None:
            mode = str(self._snapshot.context.intent or "query")
        else:
            mode = str(self.mode_combo.currentData() or "query")
        self.agent_panel.set_display_context(self._language, mode)

    def _set_production_panel_copy(self) -> None:
        self.agent_panel.setToolTip(
            "数据中心只读 Agent（独立只读连接）"
            if self._language == "zh" else
            "Data-center read-only Agent (dedicated read-only connection)"
        )
        self._sync_panel_display_context()

    def refresh_models(self) -> None:
        if self._closing:
            return
        self._catalog_generation += 1
        thread = _CatalogThread(self._catalog_generation, self._profile_host, self)
        self._catalog_threads.add(thread)
        thread.completed.connect(self._on_model_choices)
        thread.finished.connect(lambda current=thread: self._on_catalog_thread_finished(current))
        thread.start()

    @pyqtSlot(int, object, str, str)
    def _on_model_choices(self, generation: int, values: Any, active_id: str, error: str) -> None:
        if self._closing or int(generation) != self._catalog_generation:
            return
        choices = tuple(item for item in values if isinstance(item, ModelChoice))
        previous = str(self.model_combo.currentData() or "")
        self._models = choices
        self._model_choices_by_id = {item.model_config_id: item for item in choices}
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for item in choices:
            self.model_combo.addItem(f"{item.name} · {item.model}", item.model_config_id)
        selected = previous if previous in self._model_choices_by_id else active_id
        if selected in self._model_choices_by_id:
            self.model_combo.setCurrentIndex(self.model_combo.findData(selected))
        self.model_combo.blockSignals(False)
        if not choices:
            self._retire_controller_if_idle()
            message = "没有可用的已启用模型配置，请先完成模型设置。" if self._language == "zh" else "No enabled configured model is available. Configure a model first."
            if error:
                message += f" ({error})"
            self._set_status(message)
            return
        self._on_selection_changed()

    @pyqtSlot()
    def _on_catalog_thread_finished(self, thread: _CatalogThread) -> None:
        self._catalog_threads.discard(thread)
        thread.deleteLater()

    @pyqtSlot()
    def _on_selection_changed(self, *_args) -> None:
        if self._closing:
            return
        self._sync_panel_display_context()
        if self._controller is not None and self._controller.is_running:
            self._refresh_after_cleanup = True
            self._cancel_active_run("target_or_model_changed")
            self._set_status("目标或模型已变化；正在取消当前固定运行…" if self._language == "zh" else "Target or model changed; cancelling the fixed run…")
            return
        self._retire_controller_if_idle()
        self._install_next_controller()

    def target_changed(self) -> None:
        """Notify the host that a workbench target or metadata scope changed."""
        self._on_selection_changed()

    def sql_tab_visibility_changed(self) -> None:
        """Follow the visible SQL tab without cancelling its predecessor's run."""
        controller = self._controller
        if controller is not None and controller.is_running:
            self._set_status(
                f"当前回答仍固定在 {self._display_target_label(self._snapshot.target, self._snapshot.context.intent, internal_unbound=not self._snapshot.context.connection_id) if self._snapshot else '原目标'}；"
                "切换仅影响下一次提问。"
                if self._language == "zh" else
                f"The current answer stays fixed to {self._display_target_label(self._snapshot.target, self._snapshot.context.intent, internal_unbound=not self._snapshot.context.connection_id) if self._snapshot else 'its captured target'}; "
                "the tab switch applies to the next prompt."
            )
            return
        self.target_changed()

    def _retire_controller_if_idle(self) -> None:
        controller = self._controller
        if controller is None or controller.is_running:
            return
        if controller.dispose(timeout_ms=0):
            self._controller = None
            self._snapshot = None
            self._connection_choice = None
            self.agent_panel.prompt_input.setEnabled(False)

    def _install_next_controller(self) -> None:
        if self._closing or self._controller is not None:
            return
        model_id = str(self.model_combo.currentData() or "").strip()
        model_choice = self._model_choices_by_id.get(model_id)
        if model_choice is None:
            return
        intent = str(self.mode_combo.currentData() or "query")
        connection_choice: ConnectionChoice | None = None
        target_label = (
            "草稿模式 · 不访问数据库" if self._language == "zh" else "Draft mode · no database access"
        ) if intent == "draft" else ("未绑定目标" if self._language == "zh" else "No target bound")
        if intent != "draft":
            selection = self._safe_selection()
            if selection is None:
                self._set_status("请选择一个已配置的目标连接；草稿模式不需要数据库。" if self._language == "zh" else "Select a configured target connection; draft mode needs no database.")
                return
            profile = selection.get("profile")
            if not isinstance(profile, Mapping):
                self._set_status(str(selection.get("reason") or "目标连接不可用；可切换到草稿模式。"))
                return
            try:
                connection_choice = self._profile_host.capture_connection(
                    profile,
                    database=str(selection.get("database") or "") or None,
                    schema_allowlist=tuple(selection.get("schema_allowlist") or ()),
                )
            except ProfileHostError as exc:
                self._set_status(f"目标连接不可用 ({exc.code})；草稿模式仍可用。")
                return
            problem = self._validate_query_target(connection_choice)
            if problem:
                self._set_status(f"{problem}；可切换到草稿模式。")
                return
            target_label = self._target_label(connection_choice)

        context = RunContext(
            run_id=uuid.uuid4().hex,
            tab_id=f"data-center-agent-{uuid.uuid4().hex}",
            model_config_id=model_choice.model_config_id,
            connection_id=connection_choice.connection_id if connection_choice and intent != "draft" else "",
            database=connection_choice.database if connection_choice and intent != "draft" else "",
            schema_allowlist=connection_choice.schema_allowlist if connection_choice and intent != "draft" else (),
            profile_revision=connection_choice.revision if connection_choice and intent != "draft" else "",
            intent="draft" if intent == "draft" else "query",
        )
        snapshot = AgentRunSnapshot(
            context,
            target_label,
            f"{model_choice.name} · {model_choice.model}",
        )
        self._snapshot = snapshot
        self._connection_choice = connection_choice
        self._sync_panel_display_context()
        # Keep completed output visibly attached to the target that produced
        # it while a future controller is prepared for the newly selected tab.
        if self._last_run_outcome is not None:
            self._apply_last_run_status()
        elif self._last_result_target is None:
            self._show_result_target(snapshot)
        controller = DataCenterAgentController(
            self.agent_panel,
            snapshot,
            runtime_factory=lambda fixed_context, fixed_model=model_choice, fixed_target=connection_choice: self._build_runtime(
                fixed_context, fixed_model, fixed_target
            ),
        )
        controller.run_finished.connect(self._on_run_finished)
        controller.run_started.connect(self._on_run_started)
        controller.cleanup_finished.connect(self._on_controller_cleanup_finished)
        controller.run_rejected.connect(self._on_run_rejected)
        self._controller = controller
        self.agent_panel.prompt_input.setEnabled(True)
        if self._last_run_outcome is not None:
            self._apply_last_run_status()
        elif self._last_result_target is None:
            self._set_status(
                f"已固定：{target_label} · {model_choice.name} ({model_choice.capability})"
                if self._language == "zh" else
                f"Fixed: {target_label} · {model_choice.name} ({model_choice.capability})"
            )
        else:
            self._set_status(
                f"上次回答固定于 {self._display_target_label(self._last_result_target.target_label, self._last_result_target.intent, internal_unbound=not self._last_result_target.connection_id and self._last_result_target.intent != 'draft')}；下一次提问将使用 {self._display_target_label(target_label, intent, internal_unbound=intent != 'draft' and connection_choice is None)} · {model_choice.name}。"
                if self._language == "zh" else
                f"Last answer is fixed to {self._display_target_label(self._last_result_target.target_label, self._last_result_target.intent, internal_unbound=not self._last_result_target.connection_id and self._last_result_target.intent != 'draft')}; the next prompt will use {self._display_target_label(target_label, intent, internal_unbound=intent != 'draft' and connection_choice is None)} · {model_choice.name}."
            )

    def _show_result_target(self, snapshot: AgentRunSnapshot) -> None:
        self.agent_panel._set_value_label(
            self.agent_panel.target_value_label,
            self._display_target_label(
                snapshot.target,
                snapshot.context.intent,
                internal_unbound=(not snapshot.context.connection_id and snapshot.context.intent != "draft"),
            ),
            limit=180,
        )
        self.agent_panel.target_value_label.setToolTip(
            self._display_target_label(
                snapshot.target,
                snapshot.context.intent,
                internal_unbound=(not snapshot.context.connection_id and snapshot.context.intent != "draft"),
            )
        )
        self.agent_panel._set_value_label(
            self.agent_panel.model_value_label,
            snapshot.model,
            limit=180,
        )
        self._sync_panel_display_context()

    @pyqtSlot(str)
    def _on_run_started(self, run_id: str) -> None:
        controller = self._controller
        if controller is None or str(run_id) != controller.snapshot.context.run_id:
            return
        snapshot = controller.snapshot
        self._last_run_outcome = None
        self._sync_panel_display_context()
        context = snapshot.context
        self._last_result_target = _AgentResultTarget(
            connection_id=context.connection_id,
            profile_revision=context.profile_revision,
            provider=self._connection_choice.provider if self._connection_choice else "",
            mode=self._connection_choice.mode if self._connection_choice else "",
            database=context.database,
            schema_allowlist=tuple(context.schema_allowlist),
            target_label=snapshot.target,
            model_label=snapshot.model,
            intent=str(context.intent or "query"),
        )
        self._show_result_target(snapshot)
        self._set_status(
            f"正在运行，固定目标：{self._display_target_label(snapshot.target, snapshot.context.intent, internal_unbound=not snapshot.context.connection_id and snapshot.context.intent != 'draft')} · {snapshot.model}"
            if self._language == "zh" else
            f"Running with fixed target: {self._display_target_label(snapshot.target, snapshot.context.intent, internal_unbound=not snapshot.context.connection_id and snapshot.context.intent != 'draft')} · {snapshot.model}"
        )

    @pyqtSlot(str)
    def _on_put_sql_requested(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        result_target = self._last_result_target
        if result_target is not None and result_target.connection_id:
            selection = self._safe_selection()
            profile = selection.get("profile") if isinstance(selection, Mapping) else None
            if not isinstance(profile, Mapping):
                self._set_status(
                    f"生成内容来自 {result_target.target_label}；当前没有匹配连接，未放入编辑器。"
                    if self._language == "zh" else
                    f"Generated content belongs to {result_target.target_label}; no matching connection is selected, so it was not inserted."
                )
                return
            try:
                current = self._profile_host.capture_connection(
                    profile,
                    database=str(selection.get("database") or "") or None,
                    schema_allowlist=tuple(selection.get("schema_allowlist") or ()),
                )
            except ProfileHostError:
                current = None
            if current is None or (
                current.connection_id != result_target.connection_id
                or current.revision != result_target.profile_revision
                or current.provider != result_target.provider
                or current.mode != result_target.mode
                or current.database != result_target.database
                or tuple(current.schema_allowlist) != result_target.schema_allowlist
            ):
                self._set_status(
                    f"生成内容属于 {result_target.target_label}；请切回相同连接和范围后再放入，当前未修改编辑器。"
                    if self._language == "zh" else
                    f"Generated content belongs to {result_target.target_label}; switch back to the same connection and scope before inserting. The editor was not changed."
                )
                return
        self._put_sql(value)

    def _safe_selection(self) -> Mapping[str, Any] | None:
        try:
            value = self._selection_provider()
        except Exception:
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _validate_query_target(target: ConnectionChoice) -> str:
        if target.provider in _RELATIONAL_PROVIDERS:
            if not target.database:
                return "目标数据库/服务名未配置"
            if not target.schema_allowlist:
                return "没有与当前连接匹配的成功 schema 快照，Agent 查询已禁用"
            return ""
        if target.provider == "mongodb":
            if not target.database:
                return "MongoDB 数据库未配置"
            if not target.schema_allowlist:
                return "尚无已加载且授权的 collection"
            return ""
        if target.provider == "redis":
            return "" if target.database else "Redis DB 未配置"
        if target.provider == "dameng":
            return "Dameng Agent 查询尚不支持"
        return "此引擎组合没有已验证的只读 Agent host"

    @staticmethod
    def _target_label(target: ConnectionChoice) -> str:
        scope = ", ".join(target.schema_allowlist[:4])
        if len(target.schema_allowlist) > 4:
            scope += f" +{len(target.schema_allowlist) - 4}"
        suffix = f" · {scope}" if scope else ""
        return f"{target.provider}/{target.mode} · {target.database}{suffix}"

    def _build_runtime(
        self,
        context: RunContext,
        model_choice: ModelChoice,
        connection_choice: ConnectionChoice | None,
    ) -> _RuntimeOwner:
        # This callback is invoked only by DataCenterAgentController's QThread.
        model_config = self._profile_host.load_model_config(
            model_choice.model_config_id,
            model_choice.revision,
        )
        model_adapter = AgentModelHostAdapter(
            model_choice.model_config_id,
            config=model_config,
            transport=self._model_transport,
        )
        if context.intent == "draft":
            runtime = DataCenterAgentRuntime(
                context,
                model_adapter=model_adapter,
                model_config_id=model_choice.model_config_id,
                draft=True,
            )
            return _RuntimeOwner(runtime)

        if connection_choice is None:
            raise ProfileHostError("PROFILE_UNAVAILABLE")
        profile = self._profile_host.load_connection_profile(
            connection_choice.connection_id,
            connection_choice.revision,
        )
        verified = self._profile_host.capture_connection(
            profile,
            database=context.database,
            schema_allowlist=context.schema_allowlist,
        )
        if (
            verified.connection_id != context.connection_id
            or verified.revision != context.profile_revision
            or verified.provider != connection_choice.provider
            or verified.mode != connection_choice.mode
            or verified.database != context.database
            or tuple(verified.schema_allowlist) != tuple(context.schema_allowlist)
        ):
            raise ProfileHostError("PROFILE_MISMATCH")

        relational_host = None
        nosql_host = None
        nosql_factory = None
        provider = connection_choice.provider
        mode = connection_choice.mode
        if provider in _RELATIONAL_PROVIDERS:
            base_host = RelationalReadOnlyHost(
                profile=profile,
                connector=self._relational_connector,
            )
            relational_host = _RelationalHostWithoutDialect(base_host)
        elif provider in _NOSQL_PROVIDERS:
            nosql_host = NoSQLReadOnlyHost(
                profile=profile,
                redis_client_factory=self._redis_client_factory,
                mongo_client_factory=self._mongo_client_factory,
            )
            nosql_factory = nosql_host.create_factory(
                context.connection_id,
                profile_revision=context.profile_revision,
                provider=provider,
                mode=mode,
                database_name=context.database,
                allowed_collection_ids=context.schema_allowlist if provider == "mongodb" else (),
            )
        else:
            raise ProfileHostError("UNSUPPORTED_COMBINATION")

        runtime = DataCenterAgentRuntime(
            context,
            provider=provider,
            mode=mode,
            model_adapter=model_adapter,
            model_config_id=model_choice.model_config_id,
            relational_host=relational_host,
            nosql_host=nosql_host,
            nosql_factory=nosql_factory,
        )
        return _RuntimeOwner(runtime, nosql_factory)

    @pyqtSlot(str)
    def _on_prompt_requested(self, _prompt: str) -> None:
        if self._controller is None:
            self.agent_panel._set_phase(
                "目标或模型尚未就绪" if self._language == "zh" else "Target or model is not ready"
            )

    @pyqtSlot(str)
    def _on_run_rejected(self, reason: str) -> None:
        safe_messages = {
            "blank_prompt": ("请输入问题", "Enter a prompt"),
            "run_active": ("Agent 正在运行", "Agent is already running"),
            "snapshot_consumed": ("请等待目标与模型重新固定", "Wait for a new target and model snapshot"),
        }
        zh, en = safe_messages.get(str(reason or ""), ("Agent 未启动", "Agent did not start"))
        self._set_status(zh if self._language == "zh" else en)

    @pyqtSlot(object)
    def _on_run_finished(self, outcome: Any) -> None:
        self.agent_panel.prompt_input.setEnabled(False)
        if getattr(outcome, "error", None):
            self._last_run_outcome = "error"
        elif getattr(outcome, "cancelled", False):
            self._last_run_outcome = "cancelled"
        else:
            self._last_run_outcome = "completed"
        if self._last_result_target is not None:
            self._last_result_intent = self._last_result_target.intent
        self._sync_panel_display_context()
        self._apply_last_run_status()

    def _apply_last_run_status(self) -> None:
        outcome = self._last_run_outcome
        if outcome == "error":
            self._set_status(
                "Agent 运行失败；检查配置或只读连接状态。"
                if self._language == "zh" else
                "Agent run failed; check configuration or read-only connection status."
            )
        elif outcome == "cancelled":
            self._set_status("Agent 已取消。" if self._language == "zh" else "Agent cancelled.")
        elif outcome == "completed" and self._last_result_target is not None:
            target_label = self._display_target_label(
                self._last_result_target.target_label,
                self._last_result_target.intent,
                internal_unbound=(
                    not self._last_result_target.connection_id
                    and self._last_result_target.intent != "draft"
                ),
            )
            self._set_status(
                f"运行完成；当前回答固定于 {target_label}。"
                if self._language == "zh" else
                f"Run complete; the current answer is fixed to {target_label}."
            )

    @pyqtSlot()
    def _on_controller_cleanup_finished(self) -> None:
        if self._controller is None:
            return
        controller = self._controller
        if controller.is_running:
            return
        if controller.dispose(timeout_ms=0):
            self._controller = None
            self._snapshot = None
            self._connection_choice = None
            self.agent_panel.prompt_input.setEnabled(False)
        if self._closing:
            return
        self._refresh_after_cleanup = False
        self._install_next_controller()

    def _cancel_active_run(self, reason: str) -> None:
        snapshot = self._snapshot
        if snapshot is None or self._controller is None or not self._controller.is_running:
            return
        self.agent_panel.cancel_requested.emit(snapshot.context.run_id)

    def _set_status(self, value: str) -> None:
        self.status_label.setText(str(value or ""))
        self.status_label.setToolTip(str(value or ""))

    def agent_shutdown_cleanup(self) -> bool:
        """Non-blocking, Agent-only cleanup contract for application shutdown."""
        self._closing = True
        self._catalog_generation += 1
        controller = self._controller
        if controller is not None:
            snapshot = self._snapshot
            if snapshot is not None and controller.is_running:
                self.agent_panel.cancel_requested.emit(snapshot.context.run_id)
            if not controller.dispose(timeout_ms=0):
                return False
            self._controller = None
            self._snapshot = None
            self._connection_choice = None
            self.agent_panel.prompt_input.setEnabled(False)
        if any(thread.isRunning() for thread in tuple(self._catalog_threads)):
            return False
        self._catalog_threads.clear()
        return True


__all__ = ["DataCenterWorkbenchAgent"]
