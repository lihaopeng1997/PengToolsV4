import ast
from pathlib import Path
from types import SimpleNamespace


import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'ssh_session'))
import _isolation

PANEL = Path('panels/interface_debug_panel.py')
EDITOR = Path('ui/key_value_editor.py')


def parse(path):
    source = (ROOT / path).read_text(encoding='utf-8')
    compile(source, str(path), 'exec')
    return ast.parse(source)


def methods(tree, class_name):
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}


def contains(node, *snippets):
    source = ast.unparse(node)
    for snippet in snippets:
        assert snippet in source, snippet


def isolated(node, **namespace):
    unit = ast.Module(body=[node], type_ignores=[])
    exec(compile(unit, '<isolated P7 method>', 'exec'), namespace)
    return namespace[node.name]


edited = parse(PANEL)
after = methods(edited, 'InterfaceDebugPanel')
setup = after['_setup_ui']

contains(setup, "self.workspace_tabs.addTab(session_page, '会话')",
         "self.workspace_tabs.addTab(self.draft_page, '请求验证')",
         'session_layout.addWidget(self.workspace_scroll, 1)')
assert 'self.detail_tabs.addTab(self.draft_page' not in ast.unparse(setup)
print('PASS 1 · 顶层会话 / 请求验证')

assert ast.unparse(setup).count('_make_overflow_button(tools_row)') == 1
contains(setup, 'self.rt_more_btn, self._rt_actions_menu =',
         'self.rt_lib_cat_filter.currentIndexChanged.connect(self._rt_lib_refresh_list)')
contains(after['_rt_lib_refresh_list'], 'category_id=cat')
contains(after['_rt_current_category_id'], 'self.rt_category_combo.currentData()')
assert 'self.rt_category_combo.currentIndexChanged' not in ast.unparse(setup)
print('PASS 2 · 一个请求验证更多菜单 / 一个分类筛选')

contains(setup, "self.rt_lib_mode.addTab('已保存接口')", "self.rt_lib_mode.addTab('发送记录')",
         'self.rt_library_toggle.toggled.connect(lib_panel.setVisible)',
         'self.rt_split.addWidget(lib_panel)', 'self.rt_split.addWidget(right_form)')
contains(after['_rt_lib_mode_value'], 'self.rt_lib_mode.tabData(self.rt_lib_mode.currentIndex())')
print('PASS 3 · 左侧可折叠，保存 / 记录切换')

contains(setup, 'self.rt_editor_response_splitter = QSplitter(Qt.Orientation.Vertical)',
         'self.rt_editor_response_splitter.addWidget(editor_panel)',
         'self.rt_editor_response_splitter.addWidget(response_panel)',
         "self.rt_tabs.addTab(self.rt_headers, 'Headers')",
         "self.rt_tabs.addTab(self.rt_params, 'Params')",
         "self.rt_tabs.addTab(self.rt_body, 'Body')",
         "self.rt_response_tabs.addTab(self.draft_preview, 'Body')",
         "self.rt_response_tabs.addTab(self.rt_response_headers, 'Headers')",
         'self.rt_response_headers.setReadOnly(True)')
view = SimpleNamespace(
    rt_response_headers=SimpleNamespace(setPlainText=lambda text: header_views.append(text)),
    draft_preview=SimpleNamespace(clear=lambda: body_clears.append(True)),
)
header_views = []
body_clears = []
from tools.app_logging import mask_private_ipv4

render = isolated(after['_rt_set_response_view'], mask_private_ipv4=mask_private_ipv4,
                  mask_sensitive_value=lambda key, value, reveal: value if reveal else '[masked]')
render(view, '', headers={'Set-Cookie': 'synthetic'})
render(view, '')
assert header_views == ['Set-Cookie: [masked]', ''] and len(body_clears) == 2
print('PASS 4 · 请求 / 响应纵向分栏，空响应仍更新并清理头视图')

list_method = after['_rt_lib_refresh_list']
loop = next(node for node in ast.walk(list_method)
            if isinstance(node, ast.For) and ast.unparse(node.target) == 'it')
line_end = next(index for index, node in enumerate(loop.body)
                if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'label')
label_code = compile(ast.Module(body=loop.body[:line_end + 1], type_ignores=[]), '<row label>', 'exec')
from urllib.parse import urlparse

for raw_name in ('POST /sample', 'post   /sample', '', 'POST'):
    item = {'method': 'POST', 'name': raw_name, 'url': 'http://127.0.0.1/sample'}
    for mode in ('library', 'history'):
        state = dict(it=item, mode=mode, cat_map={}, urlparse=urlparse,
                     display_label=lambda row, **_: f"{row['method']} {row['name']}")
        exec(label_code, state)
        assert state['label'] == 'POST /sample'
        assert item['name'] == raw_name
print('PASS 5 · POST 仅显示一次，持久化名称不变')

contains(setup, 'control_height = 32', 'control.setFixedHeight(control_height)',
         "KeyValueEditor(mode='header', control_height=control_height)",
         "KeyValueEditor(mode='query', control_height=control_height)")
editor = methods(parse(EDITOR), 'KeyValueEditor')
contains(editor['__init__'], 'self.add_btn.setFixedHeight(control_height)')
contains(editor['_add_row'], 'control.setFixedHeight(self._control_height)')
print('PASS 6 · 工具栏 / 表单 / 键值行使用 32px')

contains(after['__init__'], 'self._reveal_sensitive = False')
contains(after['_test_listen_loopback'], "socket.create_connection(('127.0.0.1', int(port))")
contains(after['_rt_set_response_view'], 'mask_sensitive_value(key, value, False)')
print('PASS 7 · 敏感确认、回环探测与发送核心保持原样，新增响应头脱敏')


_isolation.assert_untouched()
print('RESULT: 7 passed, 0 failed')
