"""Per-SQL-workbench ownership and shutdown aggregation for table imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from tools.data_center.import_session import ImportSessionError
from ui.data_center.import_controller import (
    ImportController,
    ImportRegistrySnapshot,
    ImportSessionRegistry,
    ImportTableContext,
)
from ui.data_center.import_dialog import ManualImportDialog


_LIVE_IMPORT_HOSTS: set["ImportHost"] = set()
_ACTIVE_STATES = frozenset({
    "INSPECTING", "PREVIEWING", "QUEUED", "RUNNING", "CANCEL_PENDING",
    "COMMITTING", "ROLLING_BACK",
})
_TERMINAL_STATES = frozenset({
    "COMMITTED", "ROLLED_BACK", "FAILED", "CANCELLED", "CLOSED",
})


@dataclass(frozen=True, slots=True)
class ImportOwnerSnapshot:
    """Safe, immutable import owner state for table, workspace, and app close."""

    import_id: str
    import_tab_id: str
    generation: int
    parent_sql_tab_id: str
    parent_sql_generation: int
    state: str
    busy: bool
    cleanup_complete: bool
    decision_required: bool
    unknown_unacknowledged: bool
    failed_cleanup: bool
    dialog_open: bool


@dataclass(frozen=True, slots=True)
class ImportHostSnapshot:
    owners: tuple[ImportOwnerSnapshot, ...]
    registry: ImportRegistrySnapshot
    can_commit: bool


@dataclass(slots=True)
class _ImportEntry:
    controller: ImportController
    dialog: ManualImportDialog
    parent_sql_tab_id: str
    parent_sql_generation: int
    unknown_acknowledged: bool = False


class ImportHost(QObject):
    """Keep dialogs, controllers, workers, and sessions alive through owner exit."""

    changed = pyqtSignal()

    def __init__(self) -> None:
        # This manager must outlive a tab/panel C++ QObject if a caller closes
        # that widget while an import owner is still working.
        super().__init__(None)
        self.registry = ImportSessionRegistry()
        self._entries: dict[str, _ImportEntry] = {}
        self._shutdown_gated = False
        self._shutdown_action = ""

    def open_import(self, context: ImportTableContext, language: str) -> ImportController | None:
        if self._shutdown_gated or not isinstance(context, ImportTableContext):
            return None
        controller = ImportController(context, registry=self.registry, parent=None)
        dialog = ManualImportDialog(None, controller=controller, language=language)
        entry = _ImportEntry(
            controller,
            dialog,
            context.parent_sql_tab_id,
            context.parent_sql_generation,
        )
        self._entries[controller.import_id] = entry
        controller.progress_changed.connect(self._on_controller_changed)
        controller.operation_finished.connect(self._on_operation_finished)
        dialog.destroyed.connect(self._on_controller_changed)
        _LIVE_IMPORT_HOSTS.add(self)
        dialog.show()
        dialog.raise_()
        self.changed.emit()
        return controller

    def set_shutdown_gate(self, enabled: bool) -> bool:
        self._shutdown_gated = bool(enabled)
        for entry in tuple(self._entries.values()):
            entry.controller.set_shutdown_gate(self._shutdown_gated)
        self._collect_finished()
        return True

    def snapshot(self) -> ImportHostSnapshot:
        self._collect_finished()
        registry_snapshot = self.registry.snapshot()
        owners = tuple(self._owner_snapshot(entry) for entry in tuple(self._entries.values()))
        can_commit = all(
            not owner.busy and owner.state != "UNKNOWN"
            and not owner.unknown_unacknowledged and not owner.failed_cleanup
            for owner in owners
        )
        return ImportHostSnapshot(owners, registry_snapshot, can_commit)

    def status(self, *, parent_sql_tab_id: str | None = None, parent_sql_generation: int | None = None) -> str:
        owners = self._filtered_owners(parent_sql_tab_id, parent_sql_generation)
        if any(owner.busy for owner in owners):
            return "pending"
        if any(owner.decision_required or owner.failed_cleanup for owner in owners):
            return "decision_required"
        if any(not owner.cleanup_complete or owner.dialog_open for owner in owners):
            return "ready_to_close"
        return "ready"

    def has_parent_decision(self, tab_id: str, generation: int) -> bool:
        for owner in self._filtered_owners(tab_id, generation):
            if owner.busy or owner.decision_required or owner.failed_cleanup:
                return True
            entry = self._entries.get(owner.import_id)
            if entry is None:
                continue
            if entry.controller.session is None and not owner.cleanup_complete:
                return True
            if entry.controller.session is not None and not owner.cleanup_complete:
                return True
        return False

    def cleanup(self, action: str | None = None) -> str:
        """Preflight, then explicitly dispatch a whole-host import decision."""
        action_value = "" if action is None else str(action).strip().casefold()
        if action_value not in {"", "preflight", "close", "commit", "rollback"}:
            raise ValueError("unsupported import shutdown action")
        if self._shutdown_action:
            if action_value and action_value not in {self._shutdown_action, "preflight"}:
                return "pending"
            return self._poll_shutdown_action()

        self._collect_finished()
        owners = tuple(self._owner_snapshot(entry) for entry in tuple(self._entries.values()))
        if any(owner.busy for owner in owners):
            return "pending"
        if not action_value or action_value == "preflight":
            if any(owner.decision_required or owner.failed_cleanup for owner in owners):
                return "decision_required"
            if any(not owner.cleanup_complete or owner.dialog_open for owner in owners):
                return "ready_to_close"
            self._collect_finished()
            return "ready"

        if any(owner.decision_required or owner.failed_cleanup for owner in owners):
            reversible_decision = all(
                not owner.failed_cleanup and not owner.busy
                and (not owner.decision_required or owner.state in {"DIRTY", "UNKNOWN"})
                for owner in owners
            )
            commit_decision = all(
                not owner.failed_cleanup and not owner.busy
                and owner.state != "UNKNOWN"
                and (not owner.decision_required or owner.state == "DIRTY")
                for owner in owners
            )
            if (action_value == "rollback" and reversible_decision) or (
                action_value == "commit" and commit_decision
            ):
                pass
            else:
                return "decision_required"
        if action_value == "commit" and not self.snapshot().can_commit:
            return "decision_required"
        if action_value == "close" and any(owner.decision_required for owner in owners):
            return "decision_required"

        self._shutdown_action = action_value
        started = False
        for entry in tuple(self._entries.values()):
            owner = self._owner_snapshot(entry)
            controller = entry.controller
            if action_value == "close":
                if controller.session is None:
                    controller._host_discard_preview()
                if owner.cleanup_complete or controller.session is None:
                    entry.dialog.hide()
                continue
            if (
                owner.cleanup_complete
                and (
                    owner.state in _TERMINAL_STATES
                    or (owner.state == "UNKNOWN" and not owner.unknown_unacknowledged)
                )
            ):
                # A completed owner needs no second database decision. Once a
                # whole-host action has passed preflight, retire its remaining
                # dialog so sibling shutdown can finish.
                entry.dialog.hide()
                continue
            if controller.session is None:
                controller._host_discard_preview()
                entry.dialog.hide()
                continue
            if owner.state == "DIRTY":
                if action_value == "commit":
                    started = controller._start_commit() or started
                elif action_value == "rollback":
                    started = controller._start_rollback() or started
            elif owner.state == "UNKNOWN" and action_value == "rollback":
                if self._acknowledge_unknown(entry):
                    started = True

        if action_value == "close":
            self._shutdown_action = ""
            self._collect_finished()
            return self.status()
        if not started:
            self._shutdown_action = ""
            self._collect_finished()
            return self.status()
        return self._poll_shutdown_action()

    def retry_cleanup(self, import_id: str) -> bool:
        entry = self._entries.get(str(import_id))
        if entry is None:
            return False
        return entry.controller.retry_cleanup()

    def _acknowledge_unknown(self, entry: _ImportEntry) -> bool:
        controller = entry.controller
        session = controller.session
        if session is None:
            return False
        if not self.registry.acknowledge_unknown(controller.import_id):
            return False
        entry.unknown_acknowledged = True
        controller._unknown_acknowledged = True
        status = session.status
        if (
            session.cleanup_future.done()
            and not status.owner_alive
            and not status.connection_retained
        ):
            self.registry.collect_finished()
            self.changed.emit()
            return True
        future = self.registry.request_cleanup(controller.import_id, action="acknowledge")
        if future is None:
            return False
        controller._watch_future("close", future)
        return True

    def _poll_shutdown_action(self) -> str:
        action = self._shutdown_action
        if not action:
            return self.status()
        owners = tuple(self._owner_snapshot(entry) for entry in tuple(self._entries.values()))
        if any(owner.busy for owner in owners):
            return "pending"
        if action == "commit" and any(owner.state == "UNKNOWN" or owner.failed_cleanup for owner in owners):
            self._shutdown_action = ""
            return "decision_required"
        if any(owner.failed_cleanup for owner in owners):
            self._shutdown_action = ""
            return "decision_required"
        if any(owner.decision_required for owner in owners):
            self._shutdown_action = ""
            return "decision_required"
        if any(not owner.cleanup_complete for owner in owners):
            return "pending"
        for entry in tuple(self._entries.values()):
            entry.dialog.hide()
        self._shutdown_action = ""
        self._collect_finished()
        return "ready"

    def _owner_snapshot(self, entry: _ImportEntry) -> ImportOwnerSnapshot:
        controller = entry.controller
        entry.unknown_acknowledged = bool(
            entry.unknown_acknowledged or controller.unknown_acknowledged
        )
        progress = controller.progress()
        session = controller.session
        status = None if session is None else session.status
        state = progress.state
        future_pending = controller.operation_pending
        worker_pending = controller.worker_pending
        failed_cleanup = bool(
            status is not None and (
                status.safe_code == "CONNECTION_CLOSE_FAILED"
                or (status.cleanup_complete is False and session.cleanup_future.done()
                    and state in _TERMINAL_STATES)
                or (status.connection_retained and state in {"COMMITTED", "ROLLED_BACK", "FAILED", "CANCELLED"})
            )
        )
        unknown = state == "UNKNOWN"
        decision_required = state == "READY_TO_COMMIT" or (unknown and not entry.unknown_acknowledged)
        if state == "READY_TO_COMMIT":
            state = "DIRTY"
        busy = bool(future_pending or worker_pending)
        if session is not None and status is not None:
            if state in {"COMMITTED", "ROLLED_BACK", "FAILED", "CANCELLED"} and status.owner_alive and not failed_cleanup:
                busy = True
            cleanup_complete = bool(
                session.cleanup_future.done()
                and not status.owner_alive
                and not status.connection_retained
            )
            if state == "UNKNOWN" and entry.unknown_acknowledged and cleanup_complete:
                decision_required = False
        else:
            preview_retained = controller.source_preview is not None or controller.preview is not None
            dialog_open = bool(entry.dialog is not None and entry.dialog.isVisible())
            cleanup_complete = bool(
                not busy and not preview_retained and not dialog_open
                and controller._close_requested
            )
            if not busy and dialog_open:
                state = "CLEAN"
        return ImportOwnerSnapshot(
            import_id=controller.import_id,
            import_tab_id=controller.import_tab_id,
            generation=controller.import_generation,
            parent_sql_tab_id=entry.parent_sql_tab_id,
            parent_sql_generation=entry.parent_sql_generation,
            state=state,
            busy=busy,
            cleanup_complete=cleanup_complete,
            decision_required=decision_required,
            unknown_unacknowledged=unknown and not entry.unknown_acknowledged,
            failed_cleanup=failed_cleanup,
            dialog_open=bool(entry.dialog is not None and entry.dialog.isVisible()),
        )

    def _filtered_owners(self, parent_sql_tab_id: str | None, parent_sql_generation: int | None) -> tuple[ImportOwnerSnapshot, ...]:
        owners = tuple(self._owner_snapshot(entry) for entry in tuple(self._entries.values()))
        if parent_sql_tab_id is None:
            return owners
        generation = 0 if parent_sql_generation is None else int(parent_sql_generation)
        return tuple(owner for owner in owners if (
            owner.parent_sql_tab_id == str(parent_sql_tab_id)
            and owner.parent_sql_generation == generation
        ))

    def _on_controller_changed(self, *_args: Any) -> None:
        self._collect_finished()
        self.changed.emit()

    def _on_operation_finished(self, operation: str, _result: Any, _error: str) -> None:
        controller = self.sender()
        if isinstance(controller, ImportController):
            entry = self._entries.get(controller.import_id)
            if entry is not None and controller.unknown_acknowledged:
                entry.unknown_acknowledged = True
        self._collect_finished()
        self.changed.emit()

    def _collect_finished(self) -> None:
        self.registry.collect_finished()
        for import_id, entry in tuple(self._entries.items()):
            owner = self._owner_snapshot(entry)
            controller = entry.controller
            session = controller.session
            if session is None:
                if owner.cleanup_complete:
                    self._entries.pop(import_id, None)
            elif owner.cleanup_complete and not owner.unknown_unacknowledged and not owner.dialog_open:
                self._entries.pop(import_id, None)
        if not self._entries:
            _LIVE_IMPORT_HOSTS.discard(self)


__all__ = ["ImportHost", "ImportHostSnapshot", "ImportOwnerSnapshot"]
