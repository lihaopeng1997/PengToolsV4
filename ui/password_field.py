"""Shared masked editor with explicit credential update intent."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from ui.design_system import apply_button
from ui.field_metrics import FIELD_H, size_line
from ui.icons import apply_icon


class PasswordField(QWidget):
    def __init__(self, language="zh", stored="", parent=None):
        super().__init__(parent)
        self.language = language
        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        size_line(self.edit, "path")
        self.edit.setInputMethodHints(
            Qt.InputMethodHint.ImhHiddenText
            | Qt.InputMethodHint.ImhSensitiveData
            | Qt.InputMethodHint.ImhNoAutoUppercase
            | Qt.InputMethodHint.ImhNoPredictiveText
        )
        self.eye = QPushButton(self)
        apply_button(self.eye, "ghost", compact=True)
        self.eye.setFixedSize(FIELD_H, FIELD_H)
        self.eye.setCheckable(True)
        self.eye.setAutoDefault(False)
        self.eye.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.eye)
        self.eye.toggled.connect(self._sync_eye)
        self.edit.textEdited.connect(self._mark_edited)
        self.load(stored)

    def load(self, stored="", *, allow_reveal=True):
        from tools.secure_store import decrypt_secret

        self.eye.setChecked(False)
        self.stored = str(stored or "")
        plain = ""
        if allow_reveal and self.stored.startswith(("dpapi:", "enc:", "b64:")):
            try:
                plain = decrypt_secret(self.stored)
            except Exception:
                plain = ""
        if len(plain.encode("utf-16-le")) // 2 > self.edit.maxLength() or any(
            char in plain for char in ("\r", "\n", "\0")
        ):
            plain = ""
        self._initial = plain
        self._edited = False
        self.edit.setText(plain)
        self.set_language(self.language)

    def set_language(self, language):
        self.language = language
        placeholder = ""
        if self.stored and not self._initial:
            placeholder = (
                "凭据不可回显，留空保留" if language == "zh"
                else "Saved credential unavailable for display. Leave blank to keep."
            )
        self.edit.setPlaceholderText(placeholder)
        self._sync_eye(self.eye.isChecked())

    def _mark_edited(self, _text):
        self._edited = True

    def action(self):
        current = self.edit.text()
        if not self._edited or (self._initial and current == self._initial):
            return "keep"
        return "replace" if current else "clear"

    def _sync_eye(self, checked):
        if checked:
            for field in self.window().findChildren(PasswordField):
                if field is not self:
                    field.eye.setChecked(False)
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        apply_icon(self.eye, "eye-off" if checked else "eye", size=16)
        title = ("隐藏凭据" if checked else "显示凭据") if self.language == "zh" else (
            "Hide credential" if checked else "Show credential"
        )
        self.eye.setToolTip(title)
        self.eye.setAccessibleName(title)

    def event(self, event):
        if hasattr(self, "eye") and (
            event.type() in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate)
            or (event.type() == QEvent.Type.EnabledChange and not self.isEnabled())
        ):
            self.eye.setChecked(False)
        return super().event(event)
