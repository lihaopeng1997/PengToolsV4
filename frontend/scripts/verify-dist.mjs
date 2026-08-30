// verify-dist.mjs：构建产物的 file:// 可加载性守护。
// 用法：node scripts/verify-dist.mjs [distDir]
//   默认验证 frontend/dist；embedded 验证 ../resources/webui/vue。
// 失败必须 process.exit(1)，不能只打印 warning。
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const distArg = process.argv[2]
const distDir = resolve(scriptDir, '..', distArg ?? 'dist')

const pages = ['chrome.html', 'dashboard.html']
const QWEBCHANNEL_CONTRACT = 'qrc:///qtwebchannel/qwebchannel.js'
// 绝对路径资源在 file:// 下失效；远程资源违反离线运行约束。
const BANNED_SNIPPETS = ['src="/assets/', 'href="/assets/', 'http://', 'https://']
// 相对资源引用形如 ./assets/xxx（base: './' 的 Vite 产物）。
const ASSET_REF_PATTERN = /\.\.?\/assets\/[A-Za-z0-9._/-]+/g

let failed = false
const fail = (msg) => {
  console.error(`VERIFY-FAIL: ${msg}`)
  failed = true
}

if (!existsSync(distDir)) {
  fail(`dist 目录不存在：${distDir}`)
  process.exit(1)
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
  // 引用的资源文件必须真实存在（不只是字符串匹配）
  const refs = [...new Set(html.match(ASSET_REF_PATTERN) ?? [])]
  if (refs.length === 0) {
    fail(`${page} 未解析到任何 ./assets/ 资源引用`)
  }
  for (const ref of refs) {
    const assetPath = resolve(distDir, ref)
    if (!existsSync(assetPath)) {
      fail(`${page} 引用的资源不存在：${ref}（解析为 ${assetPath}）`)
    }
  }
  console.log(`VERIFY: ${page} OK（${refs.length} 个资源引用全部存在）`)
}

if (failed) {
  process.exit(1)
}
console.log('VERIFY-DIST: PASS（双入口存在、资源相对路径、无远程引用、WebChannel contract 完整、引用资源真实存在）')
