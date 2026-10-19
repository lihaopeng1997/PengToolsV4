"""Qt worker seam for the standalone data-center Agent panel.

The host supplies an immutable run snapshot and either an already composed
runtime or a factory that accepts the snapshot's :class:`RunContext`.  This
module never reads configuration and never constructs model or database
dependencies itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot

from tools.data_center.contracts import AgentEvent, AgentEventType, CancellationToken, RunContext
from ui.data_center.agent_panel import AgentEventFacade, DataCenterAgentPanel


_TERMINAL_EVENTS = {
    "final",
    "needs_clarification",
    "cancelled",
    "failed",
    "budget_exhausted",
}


def _event_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


@dataclass(frozen=True, slots=True)
class AgentRunSnapshot:
    """Host-approved labels and scope captured before a run is accepted."""

    context: RunContext
    target: str
    model: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, RunContext):
            raise TypeError("context must be a RunContext")
        if not self.context.run_id or not self.context.tab_id:
            raise ValueError("run_id and tab_id are required")
        object.__setattr__(self, "target", str(self.target or ""))
        object.__setattr__(self, "model", str(self.model or ""))

    @property
    def is_draft(self) -> bool:
        return str(self.context.intent or "").strip().casefold() == "draft"


@dataclass(frozen=True, slots=True)
class _WorkerOutcome:
    prompt: str
    cancelled: bool
    error: str | None = None


class _RuntimeWorker(QObject):
    finished = pyqtSignal(object)

    def __init__(
        self,
        *,
        snapshot: AgentRunSnapshot,
        prompt: str,
        cancellation: CancellationToken,
        event_sink: AgentEventFacade,
        runtime: Any = None,
        runtime_factory: Callable[[RunContext], Any] | None = None,
    ) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._prompt = prompt
        self._cancellation = cancellation
        self._event_sink = event_sink
        self._runtime = runtime
        self._runtime_factory = runtime_factory

    @pyqtSlot()
    def run(self) -> None:
        runtime = None
        last_sequence = 0
        terminal_seen = False

        def forward(event: Any) -> None:
            nonlocal last_sequence, terminal_seen
            if not isinstance(event, AgentEvent):
                return
            context = self._snapshot.context
            if str(event.run_id) != context.run_id or str(event.tab_id) != context.tab_id:
                return
            event_name = _event_name(event.event_type)
            if self._cancellation.is_cancelled() and event_name != "cancelled":
                return
            try:
                last_sequence = max(last_sequence, int(event.sequence))
            except (TypeError, ValueError):
                pass
            terminal_seen = terminal_seen or event_name in _TERMINAL_EVENTS
            self._event_sink(event)

        error: str | None = None
        try:
            runtime = self._runtime
            if runtime is None:
                if self._runtime_factory is None:
                    raise RuntimeError("runtime unavailable")
                runtime = self._runtime_factory(self._snapshot.context)
            runner = getattr(runtime, "run", None)
            if not callable(runner):
                raise TypeError("runtime does not provide run()")
            result = runner(
                self._prompt,
                cancellation=self._cancellation,
                on_event=forward,
            )
            if not terminal_seen:
                if self._cancellation.is_cancelled():
                    event_type = AgentEventType.CANCELLED
                    payload = {"reason": self._cancellation.reason}
                else:
                    event_type = AgentEventType.FINAL
                    payload = {
                        "text": str(getattr(result, "final_text", "") or ""),
                        "verified": False,
                    }
                forward(self._event(last_sequence + 1, event_type, payload))
        except Exception as exc:
            error = type(exc).__name__
            if not terminal_seen:
                if self._cancellation.is_cancelled():
                    event_type = AgentEventType.CANCELLED
                    payload = {"reason": self._cancellation.reason}
                else:
                    event_type = AgentEventType.FAILED
                    payload = {"error": "Agent runtime failed", "code": "RUNTIME_FAILED"}
                forward(self._event(last_sequence + 1, event_type, payload))
        finally:
            closer = getattr(runtime, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
            self.finished.emit(
                _WorkerOutcome(
                    prompt=self._prompt,
                    cancelled=self._cancellation.is_cancelled(),
                    error=error,
                )
            )

    def _event(self, sequence: int, event_type: Any, payload: dict[str, Any]) -> AgentEvent:
        context = self._snapshot.context
        return AgentEvent(context.run_id, context.tab_id, sequence, event_type, payload)


class DataCenterAgentController(QObject):
    """Run one injected Agent runtime at a time outside the Qt UI thread."""

    run_started = pyqtSignal(str)
    run_finished = pyqtSignal(object)
    cleanup_finished = pyqtSignal()
    run_rejected = pyqtSignal(str)

    def __init__(
        self,
        panel: DataCenterAgentPanel,
        snapshot: AgentRunSnapshot,
        *,
        runtime: Any = None,
        runtime_factory: Callable[[RunContext], Any] | None = None,
    ) -> None:
        super().__init__(panel)
        if (runtime is None) == (runtime_factory is None):
            raise ValueError("provide exactly one runtime or runtime_factory")
        runtime_context = getattr(runtime, "context", snapshot.context)
        if runtime is not None and runtime_context != snapshot.context:
            raise ValueError("runtime context does not match snapshot")
        self.panel = panel
        self.snapshot = snapshot
        self._runtime = runtime
        self._runtime_factory = runtime_factory
        self._thread: QThread | None = None
        self._worker: _RuntimeWorker | None = None
        self._cancellation: CancellationToken | None = None
        self._snapshot_consumed = False
        self._pending_outcome: _WorkerOutcome | None = None
        self._run_finished_emitted = False
        self._cleanup_finished_emitted = False
        self._panel_signals_connected = True
        self._disposed = False

        panel.prompt_send_requested.connect(self._on_prompt_send_requested)
        panel.cancel_requested.connect(self._on_cancel_requested)
        panel.new_conversation_requested.connect(self._on_new_conversation)

    @property
    def is_running(self) -> bool:
        return self._thread is not None

    @pyqtSlot(str)
    def _on_prompt_send_requested(self, prompt: str) -> None:
        prompt = str(prompt or "").strip()
        if not prompt:
            self.run_rejected.emit("blank_prompt")
            return
        if self._disposed or self._thread is not None:
            self.run_rejected.emit("run_active")
            return
        if self._snapshot_consumed:
            self.run_rejected.emit("snapshot_consumed")
            return

        context = self.snapshot.context
        self._snapshot_consumed = True
        sink = self.panel.start_run(
            context.run_id,
            context.tab_id,
            target=self.snapshot.target,
            model=self.snapshot.model,
        )
        self.panel.acknowledge_prompt(prompt)
        cancellation = CancellationToken()
        thread = QThread(self)
        worker = _RuntimeWorker(
            snapshot=self.snapshot,
            prompt=prompt,
            cancellation=cancellation,
            event_sink=sink,
            runtime=self._runtime,
            runtime_factory=self._runtime_factory,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._capture_worker_outcome, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        self._cancellation = cancellation
        self.run_started.emit(context.run_id)
        thread.start()

    @pyqtSlot(str)
    def _on_cancel_requested(self, run_id: str) -> None:
        if str(run_id) != self.snapshot.context.run_id:
            return
        token = self._cancellation
        if token is not None:
            token.cancel("user")

    @pyqtSlot()
    def _on_new_conversation(self) -> None:
        token = self._cancellation
        if token is not None:
            token.cancel("new_conversation")

    @pyqtSlot(object)
    def _capture_worker_outcome(self, outcome: _WorkerOutcome) -> None:
        self._pending_outcome = outcome

    @pyqtSlot(object)
    def _on_worker_finished(self, outcome: _WorkerOutcome) -> None:
        self._pending_outcome = outcome
        self._emit_run_finished()

    def _emit_run_finished(self) -> None:
        if self._run_finished_emitted or self._pending_outcome is None:
            return
        self._run_finished_emitted = True
        self.run_finished.emit(self._pending_outcome)

    @pyqtSlot()
    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._worker = None
        self._thread = None
        self._cancellation = None
        if thread is not None:
            thread.deleteLater()
        if not self._cleanup_finished_emitted:
            self._cleanup_finished_emitted = True
            self.cleanup_finished.emit()

    def dispose(self, timeout_ms: int = 5_000) -> bool:
        """Disconnect the panel, cancel any run, and finish thread cleanup."""

        if self._panel_signals_connected:
            for signal, slot in (
                (self.panel.prompt_send_requested, self._on_prompt_send_requested),
                (self.panel.cancel_requested, self._on_cancel_requested),
                (self.panel.new_conversation_requested, self._on_new_conversation),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._panel_signals_connected = False
        self._disposed = True
        token = self._cancellation
        if token is not None:
            token.cancel("shutdown")
        thread = self._thread
        if thread is None:
            return True
        finished = thread.wait(max(0, int(timeout_ms)))
        if finished:
            # Queued UI callbacks cannot run while this GUI-thread method is
            # waiting.  Preserve their observable order before final cleanup.
            self._emit_run_finished()
            self._on_thread_finished()
        return bool(finished)

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """Compatibility alias for hosts that used the earlier name."""

        return self.dispose(timeout_ms)


__all__ = ["AgentRunSnapshot", "DataCenterAgentController"]
