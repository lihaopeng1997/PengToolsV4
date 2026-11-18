"""Fit Qt tables from the displayed cells in their current viewport."""

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHeaderView, QStyleOptionViewItem
from PyQt6.sip import isdeleted


def autofit_columns(table):
    """Install or refresh column fitting for a QTableWidget or QTableView."""
    fit = getattr(table, "_column_autofit", None)
    if fit is None:
        fit = table._column_autofit = _ColumnFit(table)
    fit.bind_model()
    fit.schedule()
    return fit


class _ColumnFit(QObject):
    column_dragged = pyqtSignal(int, int)

    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self._viewport = table.viewport()
        self.header = table.horizontalHeader()
        self._model = None
        self._scope = None
        self._manual_widths = {}
        self._saved_widths = {}
        self._minimum_widths = {}
        self._pressed_header = None
        self._dragging = False
        self._fitting = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fit)
        self._viewport.installEventFilter(self)
        self.watch_header(self.header)
        self.header.sectionResized.connect(self._record_drag)
        self.header.sectionMoved.connect(self.schedule)
        self.header.geometriesChanged.connect(self.schedule)

    def watch_header(self, header):
        header.viewport().installEventFilter(self)

    def configure(self, widths, *, scope=None, minimum=False):
        self._scope = scope
        if minimum:
            self._minimum_widths = widths
        else:
            self._saved_widths = widths
        self.schedule()

    @staticmethod
    def _signals(model):
        return (
            model.modelReset, model.layoutChanged, model.headerDataChanged,
            model.rowsInserted, model.rowsRemoved, model.columnsInserted,
            model.columnsRemoved, model.dataChanged,
        )

    def bind_model(self):
        model = self.table.model()
        if model is self._model:
            return
        if self._model is not None and not isdeleted(self._model):
            for signal in self._signals(self._model):
                signal.disconnect(self.schedule)
        self._model = model
        if model is not None:
            for signal in self._signals(model):
                signal.connect(self.schedule)

    def schedule(self, *_args):
        if not self._fitting and not self._timer.isActive():
            self._timer.start(0)

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if watched is self._viewport:
            if kind in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.FontChange):
                self.schedule()
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pressed_header = watched
        elif kind == QEvent.Type.MouseMove and self._pressed_header is watched:
            self._dragging = bool(event.buttons() & Qt.MouseButton.LeftButton)
        elif kind in (QEvent.Type.MouseButtonRelease, QEvent.Type.UngrabMouse, QEvent.Type.Hide):
            if self._pressed_header is watched:
                self._pressed_header = None
                self._dragging = False
                self.schedule()
        return False

    def _record_drag(self, column, _old, width):
        if not self._fitting and self._dragging and width > 0:
            self._manual_widths.setdefault(self._scope, {})[column] = width
            self.column_dragged.emit(column, width)
            self.schedule()

    def _fit(self):
        table = self.table
        header = self.header
        model = table.model()
        if model is None or not table.isVisible() or self._pressed_header is not None:
            return
        columns = [header.logicalIndex(visual) for visual in range(header.count())]
        columns = [column for column in columns if not header.isSectionHidden(column)]
        if not columns:
            return
        viewport = table.viewport()
        root = table.rootIndex()
        rows = []
        position = 0
        while position < viewport.height():
            row = table.rowAt(position)
            if row < 0:
                break
            rows.append(row)
            position = table.rowViewportPosition(row) + table.rowHeight(row)
        option = QStyleOptionViewItem()
        table.initViewItemOption(option)
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        low = max(48, header.minimumSectionSize())
        high = max(low, min(420, header.maximumSectionSize()))
        manual = self._manual_widths.get(self._scope, {})
        widths = {}
        self._fitting = True
        try:
            if header.stretchLastSection():
                header.setStretchLastSection(False)
            for column in columns:
                mode = header.sectionResizeMode(column)
                if mode == QHeaderView.ResizeMode.Fixed:
                    widths[column] = header.sectionSize(column)
                    continue
                if mode != QHeaderView.ResizeMode.Interactive:
                    header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                explicit = manual.get(column, self._saved_widths.get(column))
                if explicit is not None:
                    widths[column] = explicit
                    continue
                width = max(low, header.sectionSizeHint(column), self._minimum_widths.get(column, 0))
                for row in rows:
                    if width >= high:
                        break
                    index = model.index(row, column, root)
                    widget = table.indexWidget(index)
                    hint = widget.sizeHint() if widget is not None else table.itemDelegateForIndex(index).sizeHint(option, index)
                    width = max(width, hint.width() + 8)
                widths[column] = min(high, width)
            last_column = columns[-1]
            if (last_column not in manual and last_column not in self._saved_widths
                    and header.sectionResizeMode(last_column) != QHeaderView.ResizeMode.Fixed):
                spare = viewport.width() - sum(widths.values())
                widths[last_column] = min(header.maximumSectionSize(), widths[last_column] + max(0, spare))
            for column, width in widths.items():
                if header.sectionSize(column) != width:
                    header.resizeSection(column, width)
        finally:
            self._fitting = False
