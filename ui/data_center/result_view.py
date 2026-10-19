"""Result-column presentation on a shared typed model; no data copies or I/O."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QPoint, Qt
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView


class ResultTableView(QTableView):
    """Resizable/hideable columns with an optional first-column overlay."""

    def __init__(self, parent=None, *, language: str = "zh") -> None:
        super().__init__(parent)
        self._language = language
        self._freeze_first = False
        self._schema: tuple[str, ...] = ()
        self._observed_model: QAbstractItemModel | None = None
        self.frozen_view = QTableView(self)
        self.frozen_view.setObjectName("result-frozen-first-column")
        self.frozen_view.setStyleSheet("QTableView { border: 0px; }")
        self.frozen_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen_view.verticalHeader().hide()
        self.frozen_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.frozen_view.setVerticalScrollMode(self.verticalScrollMode())
        self.frozen_view.hide()
        self.viewport().stackUnder(self.frozen_view)
        self.horizontalHeader().sectionResized.connect(self._column_resized)
        self.verticalHeader().sectionResized.connect(self._row_resized)
        self.horizontalHeader().geometriesChanged.connect(self._sync_geometry)
        self.verticalHeader().geometriesChanged.connect(self._sync_geometry)
        self.frozen_view.horizontalHeader().sectionResized.connect(self._frozen_column_resized)
        self.verticalScrollBar().valueChanged.connect(self.frozen_view.verticalScrollBar().setValue)
        self.frozen_view.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        for name in ("clicked", "pressed", "doubleClicked", "activated"):
            getattr(self.frozen_view, name).connect(getattr(self, name).emit)
        self.frozen_view.customContextMenuRequested.connect(self._frozen_context_menu)
        for header in (self.horizontalHeader(), self.frozen_view.horizontalHeader()):
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header.customContextMenuRequested.connect(
                lambda point, owner=header: self._header_menu(owner, point)
            )
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self._language = language
        hint = "右键：显示/隐藏列、冻结首列" if language == "zh" else "Right click: show/hide columns, freeze first column"
        for header in (self.horizontalHeader(), self.frozen_view.horizontalHeader()):
            header.setToolTip(hint)

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        previous = self._observed_model
        if previous is not None:
            for signal in (previous.modelReset, previous.columnsInserted, previous.columnsRemoved, previous.headerDataChanged):
                try:
                    signal.disconnect(self._sync_columns)
                except (TypeError, RuntimeError):
                    pass
        super().setModel(model)
        self.frozen_view.setModel(model)
        if self.selectionModel() is not None:
            self.frozen_view.setSelectionModel(self.selectionModel())
        self._observed_model = model
        if model is not None:
            for signal in (model.modelReset, model.columnsInserted, model.columnsRemoved, model.headerDataChanged):
                signal.connect(self._sync_columns)
        self._sync_columns()

    def setSelectionModel(self, selection_model) -> None:  # noqa: N802
        super().setSelectionModel(selection_model)
        frozen = getattr(self, "frozen_view", None)
        if frozen is not None and frozen.model() is self.model() and selection_model is not None:
            frozen.setSelectionModel(selection_model)

    def setSelectionBehavior(self, behavior) -> None:  # noqa: N802
        super().setSelectionBehavior(behavior)
        self.frozen_view.setSelectionBehavior(behavior)

    def setSelectionMode(self, mode) -> None:  # noqa: N802
        super().setSelectionMode(mode)
        self.frozen_view.setSelectionMode(mode)

    def setAlternatingRowColors(self, enabled: bool) -> None:  # noqa: N802
        super().setAlternatingRowColors(enabled)
        self.frozen_view.setAlternatingRowColors(enabled)

    def setContextMenuPolicy(self, policy) -> None:  # noqa: N802
        super().setContextMenuPolicy(policy)
        self.frozen_view.setContextMenuPolicy(policy)

    def setColumnHidden(self, column: int, hidden: bool) -> None:  # noqa: N802
        model = self.model()
        if model is None or not 0 <= column < model.columnCount():
            return
        if hidden and not self.isColumnHidden(column):
            if sum(not self.isColumnHidden(i) for i in range(model.columnCount())) <= 1:
                return
        super().setColumnHidden(column, hidden)
        self._sync_geometry()

    def hideColumn(self, column: int) -> None:  # noqa: N802
        self.setColumnHidden(column, True)

    def showColumn(self, column: int) -> None:  # noqa: N802
        self.setColumnHidden(column, False)

    def set_first_column_frozen(self, enabled: bool) -> None:
        self._freeze_first = bool(enabled)
        if enabled:
            self.showColumn(0)
        self._sync_geometry()

    @property
    def first_column_frozen(self) -> bool:
        return self._freeze_first

    def create_column_menu(self, parent=None) -> QMenu:
        zh = self._language == "zh"
        menu = QMenu("结果列与冻结" if zh else "Result columns and freeze", parent or self)
        count = self.model().columnCount() if self.model() is not None else 0
        freeze = menu.addAction("冻结首列" if zh else "Freeze first column")
        freeze.setObjectName("result-freeze-first-column")
        freeze.setCheckable(True)
        freeze.setChecked(self._freeze_first)
        freeze.setEnabled(count > 0)
        freeze.triggered.connect(self.set_first_column_frozen)
        show_all = menu.addAction("显示所有列" if zh else "Show all columns")
        show_all.setObjectName("result-show-all-columns")
        show_all.setEnabled(any(self.isColumnHidden(i) for i in range(count)))
        show_all.triggered.connect(lambda: [self.showColumn(i) for i in range(count)])
        menu.addSeparator()
        visible = sum(not self.isColumnHidden(i) for i in range(count))
        for column in range(count):
            label = str(self.model().headerData(column, Qt.Orientation.Horizontal) or column + 1)
            action = menu.addAction(f"{column + 1} · {label}")
            action.setObjectName(f"result-column-{column}")
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(column))
            action.setEnabled(self.isColumnHidden(column) or visible > 1)
            action.triggered.connect(lambda checked, col=column: self.setColumnHidden(col, not checked))
        return menu

    def _header_menu(self, header, point: QPoint) -> None:
        menu = self.create_column_menu()
        try:
            menu.exec(header.mapToGlobal(point))
        finally:
            menu.deleteLater()

    def _frozen_context_menu(self, point: QPoint) -> None:
        self.customContextMenuRequested.emit(
            self.viewport().mapFromGlobal(self.frozen_view.viewport().mapToGlobal(point))
        )

    def indexAt(self, point: QPoint) -> QModelIndex:  # noqa: N802
        if self.frozen_view.isVisible():
            overlay_point = self.frozen_view.viewport().mapFromGlobal(self.viewport().mapToGlobal(point))
            if self.frozen_view.viewport().rect().contains(overlay_point):
                return self.frozen_view.indexAt(overlay_point)
        return super().indexAt(point)

    def _sync_columns(self, *_args) -> None:
        model = self.model()
        count = model.columnCount() if model is not None else 0
        schema = tuple(str(model.headerData(i, Qt.Orientation.Horizontal) or "") for i in range(count))
        if schema != self._schema:
            self._schema = schema
            for column in range(count):
                super().setColumnHidden(column, False)
        for column in range(count):
            self.frozen_view.setColumnHidden(column, column != 0)
        if count:
            self.frozen_view.setColumnWidth(0, self.columnWidth(0))
        self.frozen_view.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        self._sync_geometry()

    def _column_resized(self, column: int, _old: int, width: int) -> None:
        if column == 0:
            self.frozen_view.setColumnWidth(0, width)
            self._sync_geometry()

    def _frozen_column_resized(self, column: int, _old: int, width: int) -> None:
        if column == 0 and self.columnWidth(0) != width:
            self.setColumnWidth(0, width)

    def _row_resized(self, row: int, _old: int, height: int) -> None:
        self.frozen_view.setRowHeight(row, height)

    def _sync_geometry(self) -> None:
        frozen = getattr(self, "frozen_view", None)
        if frozen is None:
            return
        model = self.model()
        enabled = bool(self._freeze_first and model is not None and model.columnCount() and not self.isColumnHidden(0))
        frozen.setVisible(enabled)
        if not enabled:
            return
        row_height = self.verticalHeader().defaultSectionSize()
        if frozen.verticalHeader().defaultSectionSize() != row_height:
            frozen.verticalHeader().setDefaultSectionSize(row_height)
        viewport = self.viewport().geometry()
        header = self.horizontalHeader()
        header_height = header.height() if not header.isHidden() else 0
        frozen.horizontalHeader().setVisible(not header.isHidden())
        if header_height:
            frozen.horizontalHeader().setFixedHeight(header_height)
        frozen.setGeometry(viewport.x(), viewport.y() - header_height,
                           min(self.columnWidth(0), viewport.width()), viewport.height() + header_height)
        frozen.verticalScrollBar().setValue(self.verticalScrollBar().value())
        frozen.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_geometry()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._sync_geometry()

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802
        if self.frozen_view.isVisible() and index.column() == 0:
            self.frozen_view.scrollTo(index, hint)
            return
        super().scrollTo(index, hint)
        self._reveal_index(index)

    def moveCursor(self, action, modifiers) -> QModelIndex:  # noqa: N802
        index = super().moveCursor(action, modifiers)
        self._reveal_index(index)
        return index

    def _reveal_index(self, index: QModelIndex) -> None:
        if self.frozen_view.isVisible() and index.isValid() and index.column() > 0:
            left = super().visualRect(index).left()
            width = self.frozen_view.width()
            if left < width:
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + left - width)
