"""Localized native text actions shared by chat and knowledge viewers."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMenu


def text_context_menu(editor, *, language='zh', edit_action=None):
    zh = language == 'zh'
    menu = QMenu(editor)
    selected = editor.textCursor().hasSelection()
    content = bool(editor.toPlainText())
    if not editor.isReadOnly():
        action = menu.addAction('撤销' if zh else 'Undo', editor.undo)
        action.setEnabled(editor.document().isUndoAvailable())
        action = menu.addAction('重做' if zh else 'Redo', editor.redo)
        action.setEnabled(editor.document().isRedoAvailable())
        menu.addSeparator()
        action = menu.addAction('剪切' if zh else 'Cut', editor.cut)
        action.setEnabled(selected)
    action = menu.addAction('复制所选文本' if zh else 'Copy selection', editor.copy)
    action.setEnabled(selected)
    action = menu.addAction('复制全部' if zh else 'Copy all', lambda: QApplication.clipboard().setText(editor.toPlainText()))
    action.setEnabled(content)
    if not editor.isReadOnly():
        action = menu.addAction('粘贴' if zh else 'Paste', editor.paste)
        action.setEnabled(editor.canPaste())
    menu.addSeparator()
    action = menu.addAction('全选' if zh else 'Select all', editor.selectAll)
    action.setEnabled(content)
    if edit_action is not None:
        menu.addSeparator()
        menu.addAction('编辑条目' if zh else 'Edit entry', edit_action)
    return menu


def install_text_actions(editor, *, language=lambda: 'zh', edit_action=None):
    editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(position):
        menu = text_context_menu(editor, language=language(), edit_action=edit_action)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(editor.viewport().mapToGlobal(position))

    editor.customContextMenuRequested.connect(show)


_NATIVE_TEXT_ACTIONS = {
    'undo': '撤销', 'redo': '重做', 'cut': '剪切', 'copy': '复制',
    'paste': '粘贴', 'delete': '删除', 'clear': '清空',
    'select all': '全选', 'deselect': '取消选择',
    'copy link location': '复制链接地址', 'copy link': '复制链接',
    'open link': '打开链接', 'copy image': '复制图片',
    'copy selection': '复制所选文本', 'copy all': '复制全部',
}


def localize_native_text_menu(menu):
    """Translate standard text actions without replacing Qt's selection/clipboard logic."""
    for action in menu.actions():
        caption, separator, shortcut = action.text().partition('\t')
        key = caption.replace('&', '').strip().casefold()
        translated = _NATIVE_TEXT_ACTIONS.get(key)
        if translated:
            action.setText(translated + (separator + shortcut if separator else ''))
        if action.menu() is not None:
            localize_native_text_menu(action.menu())
