"""Isolated P8 checks using only the copied widget and synthetic JSON."""

import ast
import json
import os
import sys
from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'ssh_session'))
import _isolation

SOURCE_ROOT = ROOT


def run():
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    import ui.json_viewer as module
    from ui.json_viewer import JsonViewer

    assert Path(module.__file__).resolve() == SOURCE_ROOT / 'ui' / 'json_viewer.py'
    for source in (Path(__file__), Path(module.__file__)):
        ast.parse(source.read_text(encoding='utf-8'))

    app = QApplication([])
    viewer = JsonViewer()
    viewer.resize(1100, 650)
    viewer.show()
    app.processEvents()
    model = viewer.child_table.model()
    passed = 0

    def pass_check(name):
        nonlocal passed
        passed += 1
        print(f'PASS {name}')

    def rows():
        return [(model.index(row, 0).data(), model.index(row, 1).data())
                for row in range(model.rowCount())]

    def path():
        return viewer.path_value.text()

    def move_cursor(needle, relative=0):
        text = viewer.plain_text()
        prefix = text[:text.index(needle) + relative]
        cursor = viewer.text_edit.textCursor()
        cursor.setPosition(len(prefix.encode('utf-16-le')) // 2)
        viewer.text_edit.setTextCursor(cursor)

    raw = '{"z":"😀","b":[{"x.y":42,"a":null},false],"a":{}}'
    assert viewer.set_text(raw, auto_format=False) is True
    assert viewer.plain_text() == raw
    assert viewer.splitter.orientation() == Qt.Orientation.Horizontal
    assert viewer.splitter.widget(0) is viewer.text_edit
    assert viewer.splitter.widget(1).widget(0) is viewer.tree
    assert viewer.splitter.widget(1).widget(1) is viewer.child_table
    assert viewer.text_edit.glance_width() > 0
    viewer.glance_btn.setChecked(False)
    assert viewer.text_edit.glance_width() == 0
    viewer.glance_btn.setChecked(True)
    pass_check('01 split panes and existing minimap')

    root = viewer.tree.topLevelItem(0)
    assert root.childCount() == 0
    assert rows() == [('z', '😀'), ('b', '[2]'), ('a', '{0}')]
    root.setExpanded(True)
    assert [root.child(index).text(0) for index in range(root.childCount())] == ['z', 'b', 'a']
    array = root.child(1)
    assert array.childCount() == 0
    assert array.childIndicatorPolicy() == array.ChildIndicatorPolicy.ShowIndicator
    pass_check('02 lazy tree expansion and ordered child table')

    move_cursor('42', 1)
    assert path() == "$.b[0]['x.y']"
    assert viewer.tree.currentItem().text(0) == 'x.y'
    assert rows() == []
    assert viewer.text_edit.textCursor().position() == len(raw[:raw.index('42') + 1].encode('utf-16-le')) // 2
    pass_check('03 compact text to unloaded nested node with UTF-16 offsets')

    viewer.tree.setCurrentItem(array.child(0))
    assert path() == '$.b[0]'
    assert rows() == [('x.y', '42'), ('a', 'null')]
    viewer.tree.setCurrentItem(array.child(1))
    assert viewer.text_edit.textCursor().position() == len(raw[:raw.index('false')].encode('utf-16-le')) // 2
    viewer.tree.setCurrentItem(array.child(0))
    viewer.child_table.doubleClicked.emit(model.index(1, 0))
    assert path() == '$.b[0].a'
    pass_check('04 tree and child table locate text and refresh immediate children')

    assert viewer.format_current() is True
    assert list(json.loads(viewer.plain_text())) == ['z', 'b', 'a']
    assert viewer.plain_text().startswith('{\n  "z"')
    viewer._collapse_all()
    leaf = viewer.tree.topLevelItem(0)
    leaf.setExpanded(True)
    leaf.child(1).setExpanded(True)
    leaf.child(1).child(0).setExpanded(True)
    viewer.tree.setCurrentItem(leaf.child(1).child(0).child(0))
    assert viewer.text_edit.textCursor().block().isVisible()
    assert '"x.y"' in viewer.text_edit.textCursor().block().text()
    pass_check('05 format preserves order and locating a folded line reveals it')

    edge = '{"same":1,"same":2,"escaped\\\"key":{"[a]":"x:y}","\\u0062":true}}'
    assert viewer.set_text(edge, auto_format=False)
    assert rows() == [('same', '1'), ('same', '2'), ('escaped"key', '{2}')]
    move_cursor('2,')
    assert viewer._value_for_item(viewer.tree.currentItem()) == 2
    move_cursor('true', 2)
    assert viewer.tree.currentItem().text(0) == 'b'
    pass_check('06 escaped keys and duplicate source occurrences')

    viewer.search_edit.setText('true')
    viewer.next_match()
    viewer.previous_match()
    assert viewer.text_edit.textCursor().position() == edge.index('true')
    assert viewer.tree.currentItem().text(0) == 'b'
    viewer.search_scope.setCurrentIndex(1)
    viewer.search_edit.setText('[a]')
    assert viewer.tree.currentItem().text(0) == '[a]'
    viewer.search_edit.clear()
    pass_check('07 existing text and tree search navigation')

    viewer.text_edit.setPlainText('{"z": {"fresh": "value"}}')
    QTest.qWait(320)
    move_cursor('"value"', 2)
    assert path() == '$.z.fresh'
    assert viewer.set_text('{"unclosed":', auto_format=False) is False
    assert not viewer.tree.topLevelItemCount() and not model.rowCount()
    assert viewer.plain_text() == '{"unclosed":'
    viewer.clear()
    assert viewer.plain_text() == '' and path() == '$'
    assert viewer.set_text('null') and viewer.tree.currentItem().text(1) == 'null'
    pass_check('08 live editing and public API for invalid, empty and null documents')

    assert viewer.set_text(json.dumps(list(range(19_999))), auto_format=False)
    assert len(viewer._nodes) == 20_000 and not viewer.limit_hint.isVisible()
    assert viewer.set_text(json.dumps({'z': list(range(20_001)), 'a': 0}), auto_format=False)
    root = viewer.tree.topLevelItem(0)
    assert root.childCount() == 0 and len(viewer._items) == 1
    assert len(viewer._nodes) == 20_000 and viewer.limit_hint.isVisible()
    assert '20,000' in viewer.limit_hint.text()
    viewer.set_language('en')
    assert 'including the root' in viewer.limit_hint.text()
    assert model.headerData(0, Qt.Orientation.Horizontal) == 'Child key'
    root.setExpanded(True)
    assert root.childCount() == 1 and root.child(0).childCount() == 0
    viewer.tree.setCurrentItem(root.child(0))
    assert model.rowCount() == 19_998
    viewer._expand_all()
    assert len(viewer._items) == 20_000
    assert root.child(0).child(19_997).text(0) == '[19997]'
    assert root.child(0).childCount() == 19_998
    assert '20000' in viewer.plain_text()
    viewer.clear()
    assert not viewer.limit_hint.isVisible() and not model.rowCount()
    pass_check('09 lazy node cap includes root, ordered cutoff, warning and reset')

    viewer.set_text('{"z":1,"a":2}')
    viewer.copy_formatted_json()
    assert app.clipboard().text() == viewer.plain_text()
    pass_check('10 copy API preserves formatted text')

    viewer.close()
    _isolation.assert_untouched()
    print(f'RESULT: {passed} passed, 0 failed')


if __name__ == '__main__':
    sys.path.insert(0, str(SOURCE_ROOT))
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    run()
