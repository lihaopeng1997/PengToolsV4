"""Shared masked editor with explicit credential update intent."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from ui.design_system import apply_button
from ui.field_metrics import FIELD_H, size_line
from ui.icons import apply_icon


class PasswordField(QWidget):
    def __init__(self, language="zh", stored="", parent=None):
        super().__init__(parent)
        self.setObjectName("password-field")
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
        self.eye.setStyleSheet("padding: 0px; min-height: 0px; max-height: 28px;")
        self.eye.setFixedSize(FIELD_H, FIELD_H)
        self.eye.setCheckable(True)
        self.eye.setAutoDefault(False)
        self.eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn = QPushButton(self)
        apply_button(self.clear_btn, "ghost", compact=True)
        self.clear_btn.setFixedHeight(FIELD_H)
        self.clear_btn.setCheckable(True)
        self.clear_btn.setAutoDefault(False)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setVisible(False)
        self._explicit_clear = False
        self.setFixedHeight(FIELD_H)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.eye)
        layout.addWidget(self.clear_btn)
        self.eye.toggled.connect(self._sync_eye)
        self.clear_btn.toggled.connect(self._sync_clear)
        self.edit.textEdited.connect(self._mark_edited)
        self.load(stored)

    def load(self, stored="", *, allow_reveal=True):
        from tools.secure_store import decrypt_secret

        self.eye.setChecked(False)
        self._explicit_clear = False
        self.clear_btn.blockSignals(True)
        self.clear_btn.setChecked(False)
        self.clear_btn.blockSignals(False)
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
        self.clear_btn.setVisible(bool(self.stored and not self._initial))
        self.set_language(self.language)

    def request_clear(self, enabled=True):
        """Explicitly mark or unmark an unrevealable stored credential for clearing."""
        want = bool(enabled and self.stored and not self._initial)
        self._explicit_clear = want
        self.clear_btn.blockSignals(True)
        self.clear_btn.setChecked(want)
        self.clear_btn.blockSignals(False)
        if want:
            self.edit.clear()
        self.set_language(self.language)

    def _sync_clear(self, checked):
        self._explicit_clear = bool(checked and self.stored and not self._initial)
        if self._explicit_clear:
            self.edit.clear()
        self.set_language(self.language)

    def set_language(self, language):
        self.language = language
        placeholder = ""
        if self.stored and not self._initial:
            if self._explicit_clear:
                placeholder = (
                    "保存时将清空已存凭据（再次点击“清空”取消）" if language == "zh"
                    else "Saved credential will be cleared on save (click Clear again to cancel)."
                )
            else:
                placeholder = (
                    "凭据不可回显，留空保留" if language == "zh"
                    else "Saved credential unavailable for display. Leave blank to keep."
                )
        self.edit.setPlaceholderText(placeholder)
        clear_label = ("取消清空" if self._explicit_clear else "清空") if language == "zh" else (
            "Undo clear" if self._explicit_clear else "Clear"
        )
        self.clear_btn.setText(clear_label)
        self.clear_btn.setToolTip(
            ("取消清空已存凭据" if self._explicit_clear else "显式清空已存凭据") if language == "zh" else (
                "Cancel clearing saved credential" if self._explicit_clear else "Explicitly clear saved credential"
            )
        )
        self._sync_eye(self.eye.isChecked())

    def _mark_edited(self, text):
        self._edited = True
        if text and self._explicit_clear:
            self._explicit_clear = False
            self.clear_btn.blockSignals(True)
            self.clear_btn.setChecked(False)
            self.clear_btn.blockSignals(False)
            self.set_language(self.language)

    def action(self):
        current = self.edit.text()
        if self.stored and not self._initial:
            if current:
                return "replace"
            return "clear" if self._explicit_clear else "keep"
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
