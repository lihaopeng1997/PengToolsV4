"""Build a local review index from recorded shell screenshots and metadata."""
import html
import json
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parents[2] / 'docs/ui/prism-implementation-2026-09/shell'
    cards = []
    for path in sorted(folder.glob('nav-*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        picture = path.with_suffix('.png')
        if not picture.exists():
            continue
        label = row.get('expected_active_label', f"模块 {row['nav']}")
        dom = row.get('chrome_dom') or {}
        verified = bool(dom.get('sidebar') and dom.get('active') == [label]
                        and row.get('loaded_pages') == ['chrome', 'dashboard']
                        and row.get('bridge_ready_pages') == ['chrome', 'dashboard'])
        size = ' × '.join(map(str, row['window']))
        variant = f" · 页签 {row['tab'] + 1}" if 'tab' in row else ''
        if row.get('sample'):
            variant += ' · 示例数据'
        if row.get('expected_dpr') is not None:
            variant += f" · DPR {row['window_dpr']:g} · {row['font_size']}px字体"
        if row.get('collapsed'):
            variant += ' · 侧栏折叠偏好'
        status = '已记录网页就绪与选中状态' if verified else '旧采样：未核对网页就绪'
        title = html.escape(label + variant)
        cards.append(f'''<article data-label="{title}" data-checked="{str(verified).lower()}">
<a href="{picture.name}" target="_blank"><img src="{picture.name}" loading="lazy" alt="{title} {size}"></a>
<div class="caption"><h2>{title}</h2><p>{size} · {status}</p>
<a href="{path.name}" target="_blank">查看运行记录</a></div></article>''')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>晴空棱镜 · 实际窗口检查图集</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f2fb;color:#292343;font:15px/1.6 "Microsoft YaHei",sans-serif}
header{max-width:1400px;margin:auto;padding:38px 28px 22px}h1{margin:0 0 10px;font-size:28px}
header p{max-width:1000px;color:#665f7c}input{font:inherit;padding:10px 14px;border:1px solid #dcd5ed;border-radius:10px;width:min(100%,380px)}
label{display:inline-flex;align-items:center;gap:8px;margin-left:14px}label input{width:auto}
main{max-width:1400px;margin:auto;padding:0 28px 40px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}
article{background:#fff;border:1px solid #e4dff1;border-radius:18px;overflow:hidden;box-shadow:0 5px 22px #51466e08}
img{display:block;width:100%;height:350px;object-fit:contain;background:#f9f8fc}.caption{padding:16px 20px}h2{font-size:17px;margin:0}p{margin:7px 0}a{color:#7354dc}
article[hidden]{display:none}@media(max-width:800px){main{grid-template-columns:1fr}label{margin:12px 0}img{height:auto}}
</style><header><h1>晴空棱镜 · 实际窗口检查图集</h1>
<p>截图来自隔离配置中的真实软件窗口。点击图片查看原图。每张图只表示当时的页面和滚动位置；网页就绪检查不等于所有功能、视觉、DPI及性能验收通过。旧采样可能有空白侧栏，默认隐藏。</p>
<p><a href="../STATUS.md">实施与验证记录</a> · <a href="../dialogs/index.html">子窗口图集</a></p>
<input id="query" placeholder="筛选模块或页签" aria-label="筛选模块或页签"><label><input id="checked" type="checkbox" checked>仅看网页状态已核对的截图</label>
<p id="count" aria-live="polite"></p></header><main>''' + '\n'.join(cards) + '''</main>
<script>
const query=document.querySelector('#query'),checked=document.querySelector('#checked'),cards=[...document.querySelectorAll('article')];
function filter(){let n=0;for(const c of cards){c.hidden=!c.dataset.label.toLowerCase().includes(query.value.toLowerCase())||(checked.checked&&c.dataset.checked!=='true');if(!c.hidden)n++}document.querySelector('#count').textContent=`显示 ${n} / ${cards.length} 张`;}
query.addEventListener('input',filter);checked.addEventListener('change',filter);filter();
</script></html>'''
    (folder / 'index.html').write_text(page, encoding='utf-8')
    print(f'Gallery contains {len(cards)} recorded screenshots')


if __name__ == '__main__':
    main()
