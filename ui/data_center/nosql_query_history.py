"""Shared NoSQL workbench draft and history presentation helpers.

This module contains UI and profile-history adapters only. It never imports a
panel and never opens a database connection; callers dispatch frozen codec
snapshots through their existing workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTabBar, QWidget

from tools.data_center.nosql_workspace import (
    NoSqlWorkspaceCodecError,
    NoSqlWorkspaceSnapshot,
    decode_nosql_workspace,
)
from tools.data_center.profile_host import DataCenterProfileHost, ProfileHostError
from tools.data_center.workspace_store import (
    HistoryRecord,
    WorkspaceTabKind,
    WorkspaceTarget,
    WorkspaceTextKind,
)
from ui.data_center.query_history_service import HistoryCapture


_PROVIDER_KIND = {
    "redis": WorkspaceTabKind.REDIS,
    "mongo": WorkspaceTabKind.MONGO,
}


@dataclass(slots=True)
class NoSqlDraft:
    """One visible draft tab with a fixed target and independent editor text."""

    draft_id: str
    provider: str
    target: WorkspaceTarget | None
    mode: str = ""
    db_index: int | None = None
    collection: str = ""
    editor_texts: dict[str, str] = field(default_factory=dict)
    snapshot: NoSqlWorkspaceSnapshot | None = None
    restored_action: str = ""
    history_id: str = ""
    locked: bool = False
    runnable: bool = True
    validation_code: str = ""
    display_text: str = ""
    _workspace_saved_id: str = ""


class NoSqlHistoryError(ValueError):
    """Safe, user-displayable validation error with no profile payload."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "NOSQL_HISTORY_UNAVAILABLE")
        super().__init__(self.code)


class NoSqlDraftTabs(QWidget):
    """Visible tab strip for independent NoSQL draft contexts.

    Panels own editor widgets and save/load their text in ``NoSqlDraft`` when
    the active tab changes. Identity metadata is shown in the tooltip and
    accessible description, while each draft retains its original target.
    """

    draft_about_to_change = pyqtSignal(object)
    draft_changed = pyqtSignal(object)
    new_requested = pyqtSignal()
    history_requested = pyqtSignal()

    def __init__(self, provider: str, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.language = language
        self._drafts: dict[str, NoSqlDraft] = {}
        self._active: NoSqlDraft | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar = QTabBar(self)
        self.tab_bar.setObjectName("nosql-draft-tabs")
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_draft)
        layout.addWidget(self.tab_bar, 1)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("nosql-draft-status")
        self.status_label.setMinimumWidth(80)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 2)
        self.new_button = QPushButton(self)
        self.new_button.setObjectName("nosql-new-draft")
        self.new_button.clicked.connect(self.new_requested.emit)
        layout.addWidget(self.new_button)
        self.history_button = QPushButton(self)
        self.history_button.setObjectName("nosql-query-history")
        self.history_button.clicked.connect(self.history_requested.emit)
        layout.addWidget(self.history_button)
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self.language = str(language or "en")
        zh = self.language.casefold().startswith("zh")
        self.new_button.setText("新建草稿" if zh else "New draft")
        self.new_button.setAccessibleName("新建独立草稿" if zh else "Create independent draft")
        self.history_button.setText("历史 / 收藏" if zh else "History / favorites")
        self.history_button.setAccessibleName("打开查询历史与收藏" if zh else "Open query history and favorites")
        self.new_button.setToolTip(self.new_button.accessibleName())
        self.history_button.setToolTip(self.history_button.accessibleName())
        self.set_status(self._active)

    def set_status(self, draft: NoSqlDraft | None) -> None:
        if draft is None:
            self.status_label.setText("")
            self.status_label.setToolTip("")
            return
        zh = self.language.casefold().startswith("zh")
        if not draft.runnable:
            text = f"历史不可运行: {draft.validation_code or 'HISTORY_UNAVAILABLE'}" if zh else (
                f"History cannot run: {draft.validation_code or 'HISTORY_UNAVAILABLE'}"
            )
        elif draft.target is None:
            text = "目标不可用" if zh else "Target unavailable"
        elif draft.locked:
            text = "历史目标已固定" if zh else "History target locked"
        else:
            text = "目标已捕获" if zh else "Target captured"
        identity = self._identity_text(draft)
        # A restored validation code can be a single long identifier. Add only
        # zero-width break opportunities so QLabel can wrap it without
        # clipping; the visible code and tooltip remain unchanged.
        display_text = text.replace("_", "_\u200b") if not draft.runnable else text
        self.status_label.setText(display_text)
        self.status_label.setToolTip(f"{text}; {identity}")

    @property
    def current_draft(self) -> NoSqlDraft | None:
        return self._active

    def drafts(self) -> tuple[NoSqlDraft, ...]:
        return tuple(self._drafts[self.tab_bar.tabData(index)] for index in range(self.tab_bar.count()))

    def add_draft(self, draft: NoSqlDraft, *, select: bool = True) -> int:
        if type(draft) is not NoSqlDraft or draft.provider != self.provider:
            raise TypeError("invalid NoSQL draft")
        if draft.draft_id in self._drafts:
            raise ValueError("duplicate NoSQL draft")
        old = self._active
        self._drafts[draft.draft_id] = draft
        self.tab_bar.blockSignals(True)
        index = self.tab_bar.addTab(self._title(draft))
        self.tab_bar.setTabData(index, draft.draft_id)
        self._update_tab_metadata(index, draft)
        if select:
            self.tab_bar.setCurrentIndex(index)
        self.tab_bar.blockSignals(False)
        if select:
            self._activate(old, draft)
        return index

    def update_draft(self, draft: NoSqlDraft) -> None:
        for index in range(self.tab_bar.count()):
            if self.tab_bar.tabData(index) == draft.draft_id:
                self.tab_bar.setTabText(index, self._title(draft))
                self._update_tab_metadata(index, draft)
                return

    def select_draft(self, draft_id: str) -> bool:
        for index in range(self.tab_bar.count()):
            if self.tab_bar.tabData(index) == draft_id:
                self.tab_bar.setCurrentIndex(index)
                return True
        return False

    def close_draft(self, index: int | None = None) -> bool:
        if self.tab_bar.count() <= 1:
            return False
        index = self.tab_bar.currentIndex() if index is None else int(index)
        if index < 0 or index >= self.tab_bar.count():
            return False
        old = self._active
        closing_id = self.tab_bar.tabData(index)
        self.tab_bar.blockSignals(True)
        self.tab_bar.removeTab(index)
        self._drafts.pop(closing_id, None)
        selected_index = self.tab_bar.currentIndex()
        next_id = self.tab_bar.tabData(selected_index) if selected_index >= 0 else None
        new = self._drafts.get(next_id)
        self.tab_bar.blockSignals(False)
        if old is not new:
            self._activate(old, new)
        return True

    def _on_tab_changed(self, index: int) -> None:
        draft_id = self.tab_bar.tabData(index) if index >= 0 else None
        self._activate(self._active, self._drafts.get(draft_id))

    def _activate(self, old: NoSqlDraft | None, new: NoSqlDraft | None) -> None:
        if old is new:
            return
        if old is not None:
            self.draft_about_to_change.emit(old)
        self._active = new
        self.draft_changed.emit(new)

    def _title(self, draft: NoSqlDraft) -> str:
        index = tuple(self._drafts).index(draft.draft_id) + 1
        if draft.history_id:
            prefix = "历史" if self.language.casefold().startswith("zh") else "History"
            return f"{prefix} {draft.history_id[:8]}"
        prefix = "草稿" if self.language.casefold().startswith("zh") else "Draft"
        return f"{prefix} {index}"

    def _identity_text(self, draft: NoSqlDraft) -> str:
        target = draft.target
        if target is None:
            return f"{draft.provider}; unavailable ({draft.validation_code or 'PROFILE_UNAVAILABLE'})"
        return (
            f"provider={draft.provider}; connection_id={target.connection_id}; "
            f"profile_revision={target.profile_revision}; database={target.database}; "
            f"collection/mode={target.schema}; mode={draft.mode}"
        )

    def _update_tab_metadata(self, index: int, draft: NoSqlDraft) -> None:
        identity = self._identity_text(draft)
        reason = f"; {draft.validation_code}" if draft.validation_code else ""
        if draft.locked:
            prefix = "恢复的历史目标" if self.language.casefold().startswith("zh") else "Restored history target"
            identity = f"{prefix}; {identity}"
        self.tab_bar.setTabToolTip(index, identity + reason)
        self.tab_bar.setAccessibleTabName(index, f"{self._title(draft)}; {identity}{reason}")
        self.tab_bar.setTabWhatsThis(index, identity + reason)


def make_nosql_draft(
    provider: str,
    profile: Any,
    profile_host: DataCenterProfileHost,
    *,
    database: str | None = None,
    collection: str = "",
    draft_id: str | None = None,
) -> NoSqlDraft:
    """Capture current selection metadata without connecting to its database."""

    if provider not in _PROVIDER_KIND:
        raise NoSqlHistoryError("UNSUPPORTED_PROVIDER")
    try:
        choice = profile_host.capture_connection(profile, database=database)
    except Exception:
        raise NoSqlHistoryError("PROFILE_UNAVAILABLE") from None
    expected = "redis" if provider == "redis" else "mongo"
    if choice.provider not in ({"redis", "redis_cluster"} if expected == "redis" else {"mongo", "mongodb", "mongo_db"}):
        raise NoSqlHistoryError("UNSUPPORTED_PROVIDER")
    mode = str(choice.mode or "").strip()
    if not mode:
        raise NoSqlHistoryError("PROFILE_UNAVAILABLE")
    if provider == "redis":
        raw_db = profile.get("db", profile.get("database", choice.database or 0)) if isinstance(profile, dict) else choice.database
        try:
            db_index = int(raw_db or 0)
        except (TypeError, ValueError):
            raise NoSqlHistoryError("PROFILE_UNAVAILABLE") from None
        if db_index < 0:
            raise NoSqlHistoryError("PROFILE_UNAVAILABLE")
        target = WorkspaceTarget(
            tab_id=draft_id or uuid.uuid4().hex,
            connection_id=choice.connection_id,
            profile_revision=choice.revision,
            database=str(db_index),
            schema=mode,
            tab_kind=WorkspaceTabKind.REDIS,
        )
        return NoSqlDraft(
            target.tab_id, provider, target, mode=mode, db_index=db_index,
            editor_texts={"command": ""},
        )
    database_name = str(database if database is not None else choice.database).strip()
    if not database_name:
        raise NoSqlHistoryError("PROFILE_UNAVAILABLE")
    collection_name = str(collection or "").strip()
    target = WorkspaceTarget(
        tab_id=draft_id or uuid.uuid4().hex,
        connection_id=choice.connection_id,
        profile_revision=choice.revision,
        database=database_name,
        schema=collection_name,
        tab_kind=WorkspaceTabKind.MONGO,
    )
    return NoSqlDraft(
        target.tab_id, provider, target, mode=mode, collection=collection_name,
        editor_texts={"documents": "", "command": ""},
    )


def validate_nosql_target(
    draft: NoSqlDraft,
    profile_host: DataCenterProfileHost,
) -> dict[str, Any]:
    """Reload the captured profile revision without contacting a DB server."""

    target = draft.target
    if target is None or target.tab_kind is not _PROVIDER_KIND.get(draft.provider):
        raise NoSqlHistoryError("INVALID_TARGET")
    try:
        profile = profile_host.load_connection_profile(target.connection_id, target.profile_revision)
        choice = profile_host.capture_connection(profile, database=target.database)
    except ProfileHostError as error:
        raise NoSqlHistoryError(getattr(error, "code", "PROFILE_UNAVAILABLE")) from None
    except Exception:
        raise NoSqlHistoryError("PROFILE_UNAVAILABLE") from None
    allowed = {"redis", "redis_cluster"} if draft.provider == "redis" else {"mongo", "mongodb", "mongo_db"}
    if choice.provider not in allowed or choice.mode != draft.mode or choice.revision != target.profile_revision:
        raise NoSqlHistoryError("PROFILE_MISMATCH")
    if draft.provider == "redis" and target.database != str(draft.db_index):
        raise NoSqlHistoryError("INVALID_TARGET")
    if draft.provider == "mongo" and choice.database != target.database:
        raise NoSqlHistoryError("PROFILE_MISMATCH")
    return profile


def restore_nosql_history_record(
    record: HistoryRecord,
    provider: str,
    profile_host: DataCenterProfileHost,
) -> NoSqlDraft:
    """Make a locked, non-connecting draft from a NoSQL history/favorite row."""

    draft = NoSqlDraft(
        draft_id=uuid.uuid4().hex,
        provider=provider,
        target=record.target,
        locked=True,
        runnable=False,
        history_id=record.history_id,
        editor_texts={"command": ""} if provider == "redis" else {"documents": "", "command": ""},
    )
    try:
        snapshot = decode_nosql_workspace(record.target, record.text)
        if snapshot.provider != provider:
            raise NoSqlHistoryError("UNSUPPORTED_PROVIDER")
        arguments = snapshot.worker_arguments()
        draft.snapshot = snapshot
        draft.mode = str(arguments["mode"])
        draft.collection = str(arguments.get("collection") or "")
        draft.db_index = arguments.get("db_index")
        draft.restored_action = snapshot.action
        key = "command" if snapshot.action == "command" else "documents"
        draft.editor_texts[key] = snapshot.editor_text
        profile = validate_nosql_target(
            NoSqlDraft(
                draft_id=draft.draft_id,
                provider=provider,
                target=record.target,
                mode=draft.mode,
                db_index=draft.db_index,
                collection=draft.collection,
            ),
            profile_host,
        )
        del profile
        draft.runnable = True
    except (NoSqlWorkspaceCodecError, NoSqlHistoryError, ProfileHostError) as error:
        draft.validation_code = getattr(error, "code", "HISTORY_UNAVAILABLE")
        if draft.snapshot is None:
            draft.display_text = getattr(error, "display_text", "") or str(record.text or "")
            draft.editor_texts["command" if provider == "redis" else "documents"] = draft.display_text
        draft.runnable = False
    except Exception:
        draft.validation_code = "HISTORY_UNAVAILABLE"
        if draft.snapshot is None:
            draft.display_text = str(record.text or "")
            draft.editor_texts["command" if provider == "redis" else "documents"] = draft.display_text
        draft.runnable = False
    return draft


def make_nosql_history_capture(snapshot: NoSqlWorkspaceSnapshot) -> HistoryCapture:
    """Create the app-owned persistence capture from an accepted dispatch."""

    return HistoryCapture(
        history_id=uuid.uuid4().hex,
        target=snapshot.target,
        text=snapshot.body,
        text_kind=WorkspaceTextKind.NOSQL,
        created_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    )


def open_nosql_history_dialog(
    parent: QWidget,
    language: str,
    service_provider: Callable[[], Any],
    restore_callback: Callable[[HistoryRecord], None],
) -> str:
    """Open the shared history/favorites browser and retire its controller."""

    controller = None
    service = None
    try:
        service = service_provider()
        controller = service.new_controller()
        from ui.data_center.query_history import QueryHistoryDialog

        dialog = QueryHistoryDialog(controller, language=language, parent=parent)
        dialog.browser.open_as_new_draft_requested.connect(restore_callback)
        dialog.exec()
        return ""
    except Exception:
        return "HISTORY_UNAVAILABLE"
    finally:
        if controller is not None and service is not None:
            try:
                service.retire_controller(controller)
            except Exception:
                pass


__all__ = [
    "NoSqlDraft",
    "NoSqlDraftTabs",
    "NoSqlHistoryError",
    "make_nosql_draft",
    "make_nosql_history_capture",
    "open_nosql_history_dialog",
    "restore_nosql_history_record",
    "validate_nosql_target",
]
