"""Inventory recorded UI evidence without treating screenshots as acceptance."""
import ast
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'docs/ui/prism-implementation-2026-09'
LABELS = {0: '首页', 1: '证件', 2: '发版联动', 3: '接口文档', 4: 'VIN', 5: '加解密',
          6: '命令库', 7: '设置', 8: '学习', 9: '日报', 10: '需求', 11: '格式工具',
          12: '接口排查', 13: '日志', 16: '聊天', 17: '工作', 18: 'Oracle', 19: 'MySQL',
          20: 'OceanBase', 21: '达梦', 22: 'Redis', 23: 'MongoDB'}


def main():
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    lines = ['# 晴空棱镜验收缺口清单', '', f'采集基准：`{sha}`。', '',
             '本表盘点当前文件中的证据，不代表最终验收。源码扫描包含本地未提交内容；截图可能来自旧提交，具体以各JSON中的源码记录为准。没有源码记录的旧图不能视为当前版本证据。', '',
             '## 22个导航入口的窗口样本', '',
             '下表只列网页主壳的已录制初始状态；“—”表示该窗口组合没有对应记录。链接存在不证明布局符合规格，也不证明错误、长内容、执行、取消或业务契约已通过。', '',
             '|入口|1440展开|1440收起|1280|1100|960|', '|---|---|---|---|---|---|']
    records = [(p, json.loads(p.read_text(encoding='utf-8'))) for p in sorted((DEST / 'shell').glob('nav-*.json'))]
    for index, label in LABELS.items():
        cells = []
        for width, height, collapsed in ((1440, 900, False), (1440, 900, True),
                                         (1280, 800, None), (1100, 720, None), (960, 640, None)):
            matches = [(p, row) for p, row in records if p.with_suffix('.png').exists() and row.get('actual_nav') == index
                       and row.get('chrome') == 'web' and row.get('window') == [width, height]
                       and row.get('requested') == [width, height]
                       and (collapsed is None or row.get('collapsed') == collapsed)
                       and (row.get('chrome_dom') or {}).get('active') == [row.get('expected_active_label')]
                       and row.get('loaded_pages') == ['chrome', 'dashboard']
                       and row.get('bridge_ready_pages') == ['chrome', 'dashboard']]
            cells.append(f'[录制{len(matches)}](shell/{matches[0][0].name})' if matches else '—')
        lines.append(f'|{index} {label}|' + '|'.join(cells) + '|')
    lines += ['', '原生首页/侧栏已有四种组合记录，见[实施记录](STATUS.md)。其模拟的是启动时网页不可用，不替代运行中崩溃、实际多屏或全状态验收。', '',
              '## QDialog子类的初始样本', '',
              '这里只扫描panels和ui内直接继承QDialog的类；不包含平台文件选择器、QMessageBox、间接继承或其他动态工厂。13/16表示字体设置，均不是物理DPI测试。', '',
              '|源码类|13字号|16字号|', '|---|---|---|']
    preview = ast.parse((ROOT / 'scripts/diagnostics/prism_dialog_preview.py').read_text(encoding='utf-8-sig'))
    dialog_map = next(node.value for node in preview.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'DIALOGS' for target in node.targets))
    mapping = {ast.literal_eval(key): tuple(ast.literal_eval(part) for part in value.elts[:2])
               for key, value in zip(dialog_map.keys, dialog_map.values)}
    classes, factories = [], []
    for folder in ('panels', 'ui'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            relative = path.relative_to(ROOT).as_posix()
            module = relative[:-3].replace('/', '.')
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == 'QDialog' for base in node.bases):
                    case = next((key for key, value in mapping.items() if value[:2] == (module, node.name)), None)
                    cells = []
                    for font in (13, 16):
                        name = f'{case}-{font}.json'
                        exists = case and (DEST / 'dialogs' / name).exists() and (DEST / 'dialogs' / name).with_suffix('.png').exists()
                        cells.append(f'[初始录制](dialogs/{name})' if exists else '—')
                    classes.append(f'|[{node.name}](../../../{relative})|' + '|'.join(cells) + '|')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'QDialog':
                    owners = [part for part in ast.walk(tree) if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef))
                              and part.lineno <= node.lineno <= part.end_lineno]
                    owner = min(owners, key=lambda part: part.end_lineno - part.lineno).name if owners else '模块级'
                    factories.append(f'|[{relative}](../../../{relative})|{owner} / 行{node.lineno}|待逐项运行核验|')
    lines += classes
    lines += ['', f'当前扫描到{len(classes)}个直接子类，初始截图覆盖不等于保存、取消、拖动、权限和错误状态均已验收。', '',
              '## 临时创建的QDialog', '', '|源码|创建位置|状态|', '|---|---|---|'] + factories
    lines += ['', '## 仍需逐项收尾的验收门槛', '',
              '- 按规格第14.2节补齐窗口组合，以及长内容、执行/失败/取消、极端分栏偏好与数据保持证据。',
              '- Loading按LD-01至LD-08逐调用点审计；组件运行测试不代表所有业务入口接入正确。LD-03/LD-04尚无完整接入清单。',
              '- Redis/MongoDB同步AI等待仍需先确认[线程范围草案](../../project/NOSQL_AI_WAITING_HANDOFF.md)，不能将普通继续消息当成范围批准。',
              '- SQL工作台原_run_sql(reset=True)在启动请求前清空结果；与LD-04保留旧结果的目标存在边界冲突，需核对原行为与批准范围，不能仅为动画擅自修改model逻辑。',
              '- 真实多屏、负坐标跨屏、运行中DPI切换与Windows透明合成尚无完整证据。现有模拟矩形、DPR缩放不替代物理屏幕验证。',
              '- 第14.3节启动基线、10分钟CPU、200次切页内存和帧率仍未完成；发布构建按另行发版范围执行。',
              '- 人工视觉接受与本地图集浏览器交互尚待完成；禁止以静态生成图集作为交互通过证据。', '',
              '后续AI协作提示词已发布：[需求AI接手](../../project/AI_REQUIREMENTS_PROMPT.md)。本表用于防止漏项，不把本轮UI缺陷转交为后续新增需求。', '']
    (DEST / 'ACCEPTANCE_MATRIX.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'Inventory: {len(LABELS)} navigation leaves, {len(classes)} direct dialog classes, {len(factories)} dialog factories')


if __name__ == '__main__':
    main()
