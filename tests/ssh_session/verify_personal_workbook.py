import ast
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import _isolation

def main():
    from openpyxl import Workbook, load_workbook
    from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QHeaderView, QInputDialog, QLabel

    from panels.personal_panel import KnowledgeTab
    from tools.personal_knowledge import export_workbook_entry, extract_workbook_entries, masked_workbook_rows, WORKBOOK_MASK

    app = QApplication([])
    temp = Path(_isolation.temp_dir())
    fixture = temp / 'fixture.xlsx'
    workbook = Workbook()
    workbook.active.title = 'First'
    for row in (['Name', 'API key', 'Value', 'Description'],
                ['Zulu', 'fictional-z', '10', 'password=fake'],
                ['Alpha', 'fictional-a', '2', 'safe'],
                *([f'User {number}', '', str(number), 'extra'] for number in range(30))):
        workbook.active.append(row)
    workbook.create_sheet('Second').append(['Other', 'Summary'])
    workbook.save(fixture)
    workbook.close()
    first, second = extract_workbook_entries(fixture)
    first['category'] = 'access'
    second['category'] = 'learning'
    with patch('panels.personal_panel.load_seed_entries', return_value=[]), patch(
        'panels.personal_panel.load_custom_entries', return_value=[first, second]
    ):
        tab = KnowledgeTab()
    tab.resize(1100, 500)
    tab.show()
    tab.category_combo.setCurrentIndex(tab.category_combo.findData('access'))
    app.processEvents()
    table = tab.table_view

    assert tab.entry_list.count() == 1 and tab._current['id'] == first['id']
    original_width = table.columnWidth(0)
    header = table.horizontalHeader()
    handle = QPoint(header.sectionSize(0) - 1, header.height() // 2)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(header.viewport(), handle + QPoint(110, 0))
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=handle + QPoint(110, 0))
    QTest.qWait(30)
    manual_width = table.columnWidth(0)
    assert manual_width > original_width
    assert tab.sheet_tabs.count() == 2
    tab.sheet_tabs.setCurrentIndex(1)
    assert tab._current['id'] == second['id']
    QTest.qWait(30)
    assert table.columnWidth(0) != manual_width
    tab.sheet_tabs.setCurrentIndex(0)
    assert tab._current['id'] == first['id']
    QTest.qWait(30)
    assert table.columnWidth(0) == manual_width
    print('PASS category, sheet tabs and scoped P10 drag widths')

    assert table.isRowHidden(0) and 'Name' in table.horizontalHeaderItem(0).text()
    assert table.frozen.isVisible() and table.frozen.isColumnHidden(1)
    for column in (1, 2, 3):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(column, 340)
    QTest.qWait(30)
    table.horizontalScrollBar().setValue(35)
    table.verticalScrollBar().setValue(100)
    assert table.horizontalScrollBar().value() > 0
    assert table.verticalScrollBar().value() > 0
    assert table.frozen.verticalScrollBar().value() == table.verticalScrollBar().value()
    assert table.frozen.viewport().geometry().y() == table.horizontalHeader().height()
    for column in (1, 2, 3):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    print('PASS pinned header and first column while scrolling')

    assert table.item(1, 1).text() == table.item(1, 1).toolTip() == WORKBOOK_MASK
    assert table.item(1, 3).text() == WORKBOOK_MASK
    captured = []
    confirmations = []
    confirm_result = {'allow': True}

    def fake_confirm(_parent, title, message, confirm_text=None, danger=True, scope_text=''):
        confirmations.append((title, message, danger))
        return confirm_result['allow']

    def export_from_panel(visible_only):
        destination = temp / ('visible.xlsx' if visible_only else 'all.xlsx')
        with ExitStack() as stack:
            stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(destination), '')))
            stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
            stack.enter_context(patch('panels.personal_panel.show_success'))
            stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
            tab._export_table(visible_only)
        with destination.open('rb') as stream:
            result = load_workbook(stream)
            try:
                assert result.active.freeze_panes == 'A2'
                return list(result.active.values)
            finally:
                result.close()

    def inspect_dialog(dialog):
        captured.extend(label.text() for label in dialog.findChildren(QLabel))
        assert table.item(1, 1).text() == WORKBOOK_MASK
        table.setCurrentCell(1, 1)
        table.item(1, 1).setText(captured[0])
        try:
            tab._copy_table_cell_action()
            assert app.clipboard().text() == WORKBOOK_MASK
            for copy_table in (tab._copy_current_row, tab._copy_visible_table, tab._copy_all_table, tab._copy_entry):
                copy_table()
                assert 'fictional-' not in app.clipboard().text()
                assert 'password=fake' not in app.clipboard().text()
            for visible_only in (True, False):
                assert export_from_panel(visible_only)[1] == tuple(first['rows'][1])
        finally:
            table.item(1, 1).setText(WORKBOOK_MASK)
        return QDialog.DialogCode.Rejected
    with patch.object(QDialog, 'exec', inspect_dialog):
        tab._open_table_cell(1, 1)
    assert captured == ['fictional-z'] and table.item(1, 1).text() == WORKBOOK_MASK
    tab._copy_table_cell(1, 1)
    assert app.clipboard().text() == WORKBOOK_MASK
    print('PASS masked UI and clipboard, original XLSX values during single-cell reveal')

    tab._sort_sheet(2)
    assert table.verticalHeader().logicalIndex(1) == 3
    assert table.verticalHeader().visualIndex(2) < table.verticalHeader().visualIndex(1)
    assert table.frozen.verticalHeader().visualIndex(2) == table.verticalHeader().visualIndex(2)
    exported = export_from_panel(True)
    assert exported[1][0] == 'User 0'
    assert next(index for index, row in enumerate(exported) if row[0] == 'Alpha') < next(
        index for index, row in enumerate(exported) if row[0] == 'Zulu')
    tab.filter_column.setCurrentIndex(tab.filter_column.findData(0))
    tab.sheet_filter.setText('Alpha')
    assert tab._visible_source_rows() == [0, 2] and table.frozen.isRowHidden(1)
    assert export_from_panel(True) == [tuple(first['rows'][index]) for index in (0, 2)]
    tab.sheet_filter.clear()
    tab.search_edit.setText('Zulu')
    tab._do_search()
    assert tab._visible_source_rows() == [0, 1]
    tab.search_edit.setText('fictional-z')
    tab._do_search()
    assert not tab._filtered
    tab.search_edit.clear()
    tab._do_search()
    print('PASS numeric sort, source-row filtering and masked search')

    tab._sort_sheet(2)
    tab.sheet_filter.setText('Alpha')
    target = table.item(2, 2)
    table.scrollToItem(target)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(target).center())
    assert (table.currentRow(), table.currentColumn()) == (2, 2)
    with patch.object(QInputDialog, 'getMultiLineText', return_value=('55', True)):
        tab._edit_table_cell()
    persisted = json.loads(Path(_isolation.config.PRIVATE_KNOWLEDGE_FILE).read_text(encoding='utf-8'))
    saved = next(entry for entry in persisted if entry['id'] == first['id'])
    assert saved.keys() == (first.keys() | {'pinned', 'builtin_source'})
    assert saved['rows'][1] == first['rows'][1]
    assert saved['rows'][2][1:3] == ['fictional-a', '55']
    assert table.item(2, 2).text() == '55'
    assert masked_workbook_rows(saved)[2][1] == WORKBOOK_MASK
    print('PASS sorted edit preserves source identity, schema and other cell values')

    legacy_export = temp / 'default.xlsx'
    for subset in ((), ([0, 1], [1, 3])):
        export_workbook_entry(saved, legacy_export, *subset)
        result = load_workbook(legacy_export)
        try:
            assert result.active.freeze_panes == 'A2'
            source_rows, source_columns = subset or (range(len(saved['rows'])), range(saved['column_count']))
            assert list(result.active.values) == [
                tuple(saved['rows'][row][column] or None for column in source_columns)
                for row in source_rows
            ]
        finally:
            result.close()
    assert saved['rows'][1] == first['rows'][1]
    print('PASS baseline public export preserves original values for full and subset calls')

    assert confirmations and confirmations[-1][2] is True and '未打码' in confirmations[-1][1]
    confirm_result['allow'] = False
    blocked = temp / 'cancelled.xlsx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(blocked), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        tab._show_entry_detail(first)
        confirmations.clear()
        tab._export_table(False)
    assert len(confirmations) == 1 and not blocked.exists()
    confirm_result['allow'] = True
    allowed = temp / 'confirmed.xlsx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(allowed), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        confirmations.clear()
        tab._export_table(False)
    assert len(confirmations) == 1
    with allowed.open('rb') as stream:
        result = load_workbook(stream)
        try:
            assert list(result.active.values)[1] == tuple(first['rows'][1])
        finally:
            result.close()
    clean = temp / 'clean.xlsx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(clean), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        tab._show_entry_detail(second)
        confirmations.clear()
        tab._export_table(False)
    assert not confirmations and clean.exists()
    print('PASS table export asks before writing masked originals and stays silent without them')

    from docx import Document
    sensitive_doc = {'id': 'word-fixture', 'title': 'Connect notes', 'content_type': 'word_document',
                     'content': 'demo account\npassword=fake-value', 'category': 'learning',
                     'source': 'fixture.docx'}
    tab._show_entry_detail(sensitive_doc)
    confirm_result['allow'] = False
    doc_blocked = temp / 'word-cancelled.docx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(doc_blocked), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        confirmations.clear()
        tab._export_word_document()
    assert len(confirmations) == 1 and not doc_blocked.exists()
    confirm_result['allow'] = True
    doc_kept = temp / 'word-kept.docx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(doc_kept), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        tab._export_word_document()
    assert doc_kept.exists()
    text = '\n'.join(paragraph.text for paragraph in Document(str(doc_kept)).paragraphs)
    assert 'password=fake-value' in text
    tab._show_entry_detail(dict(sensitive_doc, id='word-clean', content='普通的整理说明，没有凭据。'))
    doc_clean = temp / 'word-clean.docx'
    with ExitStack() as stack:
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', return_value=(str(doc_clean), '')))
        stack.enter_context(patch('tools.dialog_paths.remember_dialog_path'))
        stack.enter_context(patch('panels.personal_panel.show_success'))
        stack.enter_context(patch('panels.personal_panel.confirm_action', side_effect=fake_confirm))
        confirmations.clear()
        tab._export_word_document()
    assert not confirmations and doc_clean.exists()
    print('PASS word export asks before writing sensitive text and stays silent without it')

    for relative in ('panels/personal_panel.py', 'tools/personal_knowledge.py', 'ui/table_utils.py'):
        filename = ROOT / relative
        ast.parse(filename.read_bytes(), filename=str(filename))
    tab.close()
    tab.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    _isolation.assert_untouched()
    print('PASS syntax and isolation')
    print('RESULT: 8 passed, 0 failed')


if __name__ == '__main__':
    main()
