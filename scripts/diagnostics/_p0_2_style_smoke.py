# -*- coding: utf-8 -*-
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication, QFrame
from unittest.mock import patch

app = QApplication.instance() or QApplication([])

from ui.responsive import editor_min_height
assert editor_min_height() == 240

with patch('panels.requirement_panel.load_requirements', return_value=[]), \
     patch('panels.requirement_panel.load_requirement_ui', return_value={'splitter_sizes': [330, 820]}):
    from panels.requirement_panel import RequirementPanel
    p = RequirementPanel('zh')
    filters = [w for w in p.findChildren(QFrame) if w.objectName() == 'page-filter-bar']
    assert filters, 'missing page-filter-bar'
    assert p.detail_splitter.widget(0).minimumWidth() == 260
    assert p.detail_splitter.widget(1).minimumWidth() == 520
    assert p._file_row_delegate._row_height == 36
    names = [b.text() for b in p.file_library_action_buttons]
    assert names == ['打开目录', '刷新', '更新', '添加文件', '新建文本', '锁定', '解锁', '回滚', '提交'], names
    p.apply_layout_mode('narrow', False)
    assert p._page_root_layout.spacing() == 10
    p.close()

from panels.sql_panel import SqlToolPanel
s = SqlToolPanel()
assert s.input_sql.minimumHeight() == 240
s.apply_layout_mode('standard', False)
assert s._page_root_layout.spacing() == 16
s.close()

from panels.docx_panel import DocxUpdatePanel
d = DocxUpdatePanel('zh')
assert d.sql_editor.minimumHeight() == 240
assert d.main_splitter is not None and d.editor_splitter is not None
assert d.main_splitter.widget(0).minimumWidth() == 240
assert d.main_splitter.widget(1).minimumWidth() == 520
d.apply_layout_mode('narrow', False)
d.close()

from panels.personal_panel import DailyReportTab
t = DailyReportTab('zh')
assert t.completed.minimumHeight() >= 240
assert t.splitter is not None
assert t.splitter.widget(0).minimumWidth() == 240
t.apply_layout_mode('compact', False)
t.close()

print('P0-2 smoke OK')
