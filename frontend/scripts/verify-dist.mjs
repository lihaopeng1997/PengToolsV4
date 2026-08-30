// verify-dist.mjs：构建产物的 file:// 可加载性守护。
// 失败必须 process.exit(1)，不能只打印 warning。
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const distDir = resolve(process.cwd(), 'dist')
const pages = ['chrome.html', 'dashboard.html']
const QWEBCHANNEL_CONTRACT = 'qrc:///qtwebchannel/qwebchannel.js'
// 绝对路径资源在 file:// 下失效；远程资源违反离线运行约束。
const BANNED_SNIPPETS = ['src="/assets/', 'href="/assets/', 'http://', 'https://']

let failed = false
const fail = (msg) => {
  console.error(`VERIFY-FAIL: ${msg}`)
  failed = true
}

for (const page of pages) {
  const path = resolve(distDir, page)
  if (!existsSync(path)) {
    fail(`${page} 不存在（dist=${distDir}）`)
    continue
  }
  const html = readFileSync(path, 'utf-8')

  for (const bad of BANNED_SNIPPETS) {
    if (html.includes(bad)) fail(`${page} 含违规引用：${bad}`)
  }
  if (!html.includes(QWEBCHANNEL_CONTRACT)) {
    fail(`${page} 缺少 Qt WebChannel script contract（${QWEBCHANNEL_CONTRACT}）`)
  }
  // 构建资源必须相对引用（base: './' 产物形如 ./assets/xxx.js）
  if (!html.includes('./assets/')) {
    fail(`${page} 未发现相对资源引用 ./assets/（base 应为 './'）`)
  }
}

if (failed) {
  process.exit(1)
}
console.log('VERIFY-DIST: PASS（双入口存在、资源相对路径、无远程引用、WebChannel contract 完整）')
