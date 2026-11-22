# -*- coding: utf-8 -*-
"""Shared date-edit and calendar presentation for native workbenches."""

from __future__ import annotations

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QDateEdit,
    QVBoxLayout,
)


def configure_calendar(calendar: QCalendarWidget, *, object_name: str = 'unified-calendar') -> QCalendarWidget:
    """Apply the common calendar behavior used by every date selector."""
    calendar.setObjectName(object_name)
    calendar.setGridVisible(False)
    calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    calendar.setMinimumSize(300, 252)
    return calendar


def configure_date_edit(widget: QDateEdit, *, object_name: str | None = None) -> QDateEdit:
    """Configure a native date edit while preserving its caller-owned name."""
    if object_name:
        widget.setObjectName(object_name)
    widget.setCalendarPopup(True)
    configure_calendar(widget.calendarWidget())
    return widget


def choose_date(parent, current: QDate | None = None) -> QDate | None:
    """Open the shared calendar dialog and return the chosen date."""
    dialog = QDialog(parent)
    dialog.setObjectName('unified-date-dialog')
    dialog.setWindowTitle('选择日期')
    dialog.setMinimumSize(340, 340)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    calendar = configure_calendar(QCalendarWidget())
    selected = current if current and current.isValid() else QDate.currentDate()
    calendar.setSelectedDate(selected)
    layout.addWidget(calendar, 1)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    from ui.dialog_buttons import localize_button_box
    localize_button_box(buttons, 'zh')
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    calendar.activated.connect(lambda _date: dialog.accept())
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return calendar.selectedDate()


class UnifiedDateEdit(QDateEdit):
    """QDateEdit with the shared calendar behavior installed by default."""

    def __init__(self, date: QDate | None = None, parent=None):
        super().__init__(date or QDate.currentDate(), parent)
        configure_date_edit(self)
