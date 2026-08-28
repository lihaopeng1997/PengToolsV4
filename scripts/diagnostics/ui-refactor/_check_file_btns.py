# -*- coding: utf-8 -*-
from pathlib import Path
text = Path('panels/requirement_panel.py').read_text(encoding='utf-8')
names = [
    'open_folder_btn', 'refresh_btn', 'update_btn', 'add_file_btn', 'new_text_btn',
    'lock_btn', 'unlock_btn', 'revert_btn', 'commit_btn',
]
labels = ['打开目录', '刷新', '更新', '添加文件', '新建文本', '锁定', '解锁', '回滚', '提交']
idxs = [text.find(f'self.{name} = QPushButton') for name in names]
print(list(zip(labels, idxs)))
assert all(i > 0 for i in idxs), idxs
assert idxs == sorted(idxs), idxs
# labels near buttons
for label, name in zip(labels, names):
    pos = text.find(f'self.{name} = QPushButton')
    snippet = text[pos:pos + 80]
    assert label in snippet, (name, snippet)
print('file-library-buttons-ok')
