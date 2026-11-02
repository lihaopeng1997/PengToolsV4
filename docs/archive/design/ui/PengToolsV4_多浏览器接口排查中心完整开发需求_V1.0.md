# PengToolsV4 多浏览器接口排查中心完整开发需求 V1.0

> 适用版本：PengToolsV4 Private。本文是本轮唯一实施依据；阅读后直接开发，不需要再设计产品流程。

## 1. 目标与范围

把当前“浏览器 F12 找接口 → 复制报文 → 加解密 → 手工改 Postman 地址”的排查流程，收敛为 PengTools 内的本地工作流：实时看到浏览器接口，选中报文后一键送入加解密/格式化，并自动生成指向本地地址的 Postman 与 cURL 验证草稿。

### 1.1 本轮包含

1. 首页待升级事项改为具体、可执行的需求/BUG 列表。
2. SQL 整理页直接选择系统，并和系统配置双向同步。
3. 格式工具增加 Java 开发常用的文本转换能力。
4. 新增独立导航模块 `接口排查`，支持 Chromium 调试端口和 IE 本机代理两种采集方式。
5. 复用现有加解密、JSON、XML、SQL 格式化能力；不破坏现有功能。

### 1.2 绝对边界

- **仅 Private 版**：标准包 `PengToolsHub_Offline_Setup.zip` 不修改、不构建。
- 所有持久数据只写 `config.local_data_dir()` 下的 `data/`；升级不覆盖旧数据。
- 除接口排查的 `127.0.0.1` 调试端口/本机代理例外，其他模块继续禁止 HTTP、WebSocket、浏览器内核、云端服务和外网调用。
- PengTools 不实际向本地/测试/生产接口发请求；只生成草稿。
- 抓到的请求、响应、令牌、Cookie、密钥、解密明文默认只在内存中存活，停止监听或退出时清空，禁止写日志和 JSON。
- 删除/移除操作继续遵守：取消在左、确认在右、默认焦点为取消。

## 2. 首页、SQL 与格式工具

### 2.1 首页待升级事项

修改 `DashboardPanel._fill_release()`。

- 不再按“本周/下周/未定”生成三条汇总行；展示最多 5 条具体事项。
- 排除进度状态：`已上线`、`已关闭`、`已取消`、`暂停`。
- 候选优先级：
  1. 有计划上线日期且早于今天的逾期事项；
  2. 计划上线日期在今天至未来 14 天的事项；
  3. 未填计划日期、但有上线月份的待排期事项。
- 排序：逾期天数从大到小 → 计划日期从近到远 → `updated_at` 从新到旧。
- 每条显示：需求/BUG 标题、目标系统、`逾期 N 天` / `计划 yyyy-MM-dd` / `待排期` 状态、进度状态。
- 点击具体事项：定位到需求管理中该条记录；右上角“升级准备”跳转升级准备页。
- 空状态：`暂无待升级事项`，不再解释分组规则。

### 2.2 SQL 整理页快速选择系统

- 在 SQL 整理页“输入”区第一行，将只读 `current_system_label` 改为 `当前系统` 下拉框，数据源为系统配置中的 `_systems`。
- 选择系统后：更新 `_current_system_idx`、系统配置页 `system_combo`、系统 chip、文件命名、环境和目录模板；不要求用户切到系统配置页。
- 系统配置页仍只承担新增/修改/删除；两处选择始终保持同一当前系统。
- 无配置系统时显示 `请先新增系统`，提供跳转系统配置页的小入口。

### 2.3 格式工具扩展

保留现有 `JSON / XML / SQL` Tab，新增第 4 个 Tab：`文本与开发辅助`。

- 顶部模式下拉：Base64、URL、Unicode、时间戳、Java 堆栈。
- Base64：编码/解码，非 UTF-8 文本报错说明，不猜测二进制文件。
- URL：百分号编码/解码，保留 query 参数可读性。
- Unicode：`\\uXXXX` 与中文双向转换，保留普通反斜杠。
- 时间戳：支持 10/13 位 Unix 时间戳、`yyyy-MM-dd HH:mm:ss`、北京时间（UTC+8）互转。
- Java 堆栈：识别首个异常、每个 `Caused by`、首个业务包 `at ...`；输出“异常链 + 首个业务位置 + 原始堆栈”，支持一键复制精简排查文本。
- 所有代码区/命令示例/高亮继续使用主题 token，不得出现主题切换后文字不可见。

## 3. 新模块：接口排查

### 3.1 导航与布局

- 新增导航 index `12`、stack index `11`：`接口排查`，放到 `开发工具` 分组，图标使用新增本地 SVG `api-debug`。
- 同步 `ui/navigation_model.py`、`ui/icons.py`、`main_window.py`、悬浮工具栏候选清单与中英文文案。
- 加解密页增加次级按钮 `进入接口排查`；接口排查页中“送入加解密”跳回现有加解密页并带入数据。
- 页面采用三段结构：顶部连接区 → 中部请求列表/详情分割区 → 底部本地验证草稿区。长任务（启动浏览器、连接、安装证书、启动代理）显示 Aurora Loading；筛选、选中记录、展开详情不显示 Loading。

### 3.2 持久配置与内存会话

新增 `data/interface_debug.json`，旧数据读取必须 `setdefault`。

```json
{
  "browser_path": "",
  "debug_port": 9222,
  "local_targets": [{"id":"uuid","name":"本地服务","base_url":"http://localhost:8080"}],
  "default_target_id": "",
  "ie_proxy_port": 8899,
  "ie_certificate_thumbprint": ""
}
```

- 仅保存浏览器路径、端口、地址配置与证书指纹；不保存报文、响应、认证头、Cookie、密钥、明文。
- `local_targets` 支持新增、编辑、删除；删除使用统一确认弹框。
- 捕获记录只存面板内存；`停止监听`、切换采集模式、关闭窗口、应用退出均调用 `clear_session()`。

### 3.3 Chromium 通用模式

新建 `tools/browser_debug.py`，用 `websocket-client` 连接 Chrome DevTools Protocol（CDP）；新增依赖写入 `requirements.txt` 并纳入 Private PyInstaller spec。

#### 浏览器发现与选择

- 自动扫描注册表 App Paths、常见 Program Files / LocalAppData 路径，识别 Chrome、Edge、360、QQ、搜狗、Brave、Opera。
- 只将可执行文件加入候选；候选显示浏览器名称与完整路径。
- 提供“手动选择浏览器 EXE”。用户选过后持久保存 `browser_path`。
- Firefox 在候选中可显示，但选择后明确提示：`Firefox 暂不支持实时监听；请使用 Chromium 内核浏览器。` 不尝试伪连接。

#### 一键启动与手动连接

- `一键启动调试浏览器`：以已选 Chromium EXE 启动：
  - `--remote-debugging-address=127.0.0.1`
  - `--remote-debugging-port=<debug_port>`
  - `--user-data-dir=<local_data_dir>/browser_debug_profile`
  - `--no-first-run`
- 不复用用户日常 profile；首次启动后的登录、Cookie 只在该专用 profile 内，由用户自行管理。
- `连接已有调试浏览器`：用户可编辑端口，连接 `127.0.0.1:<port>`；若端口不可用，提示如何用调试参数启动浏览器。
- CDP 只允许 loopback 地址，禁止用户输入远程 host。

#### 实时监听

- 连接后列出页面 target（标题 + URL），用户选择目标页；默认选当前可见的普通 `page` target。
- 启用 `Network.enable`，监听 `requestWillBeSent`、`responseReceived`、`loadingFinished`、`loadingFailed`。
- 请求记录字段：id、开始时间、method、url、path、query、request_headers、request_body、status、mime_type、response_headers、response_body、duration_ms、failure。
- 仅记录监听开始后的 XHR、Fetch、Document 请求；默认过滤静态 `.js/.css/.png/.svg/.woff`，提供“显示静态资源”开关。
- 请求体来自 CDP `postData`；响应完成后用 `Network.getResponseBody` 读取。读取失败只保留元信息，不影响其他记录。
- 列表显示时间、方法、路径、状态、耗时、类型；URL 中 query token 和列表中的认证字段脱敏。
- 详情分为“请求”和“响应”，各展示 URL、参数、头、正文；`Authorization`、`Cookie`、`Set-Cookie` 默认遮罩，提供本次会话有效的“显示敏感字段”开关。

### 3.4 IE 兼容代理模式

IE 没有 CDP，使用本机 MITM 代理。新增 `mitmproxy>=11` 依赖，以应用内部 worker 启动，不要求用户安装 Fiddler 或单独运行命令。

#### 明确授权与系统恢复

- 首次点击“启用 IE 代理监听”时显示高风险确认：将临时修改当前用户 Windows 代理，并可安装本机根证书以解密 HTTPS；取消为默认焦点。
- 代理只监听 `127.0.0.1:<ie_proxy_port>`；不暴露到局域网。
- 启动前备份当前用户 Internet Settings 的 `ProxyEnable`、`ProxyServer`、`ProxyOverride` 到内存和 `data/interface_debug.json` 的恢复快照；仅用于异常恢复，不含任何报文。
- 设置代理：`ProxyEnable=1`、`ProxyServer=127.0.0.1:<port>`、`ProxyOverride=localhost;127.0.0.1;<local>`，调用 Windows InternetSetOption 刷新 WinINet。
- 停止监听、代理启动失败、应用异常退出清理路径均恢复原代理设置；下次启动检测到未恢复快照时优先提示并一键恢复。

#### HTTPS 证书

- HTTP：无需证书，直接采集。
- HTTPS：首次需要生成 mitmproxy CA 并用 `certutil -user -addstore Root <cert>` 安装到当前用户根证书库；再次显示明确确认，不以管理员身份运行。
- 记录 CA SHA-1 thumbprint；设置页/接口排查“IE 代理管理”提供 `移除本机抓包证书`，只允许删除记录的指纹，执行前确认。
- 未安装证书时仍可开始代理，但 HTTPS 列表显示 `HTTPS 未解密：请安装本机抓包证书`，不得把握手失败伪装成无请求。

#### 代理捕获

- mitmproxy addon/worker 将 HTTP flow 转换为与 Chromium 相同的内存记录模型，通过线程安全 signal/queue 回主 UI。
- 捕获范围与 Chromium 相同：过滤静态资源、展示请求/响应、脱敏、停止即清空。
- 禁止代理修改、重放、转发请求内容；它只观察用户在 IE 中主动发起的流量。

## 4. 加解密与格式化联动

- 请求体/响应体检测到 JSON、XML 时显示 `在格式工具中打开`，直接带入对应 Tab。
- 检测到 Base64 或无法解析的文本时显示 `送入加解密`，将该段正文带到现有加解密输入区；不自动猜测请求/响应方向，用户仍选择“请求解密/响应解密”。
- 加解密成功后的 JSON 查看、XML 跳转行为保持现状。
- 任何联动都不自动落盘。

## 5. 本地验证草稿

新建 `tools/interface_drafts.py`，只负责 URL 重写、Postman Collection v2.1 与 cURL 文本生成，不发送网络请求。

### 5.1 URL 重写

- 选择本地地址配置后，用该地址的 scheme/host/port 替换原 URL 的 scheme/host/port；原 path、query 保持不变。
- 目标地址必须是 `http://` 或 `https://`；不允许空地址或带 path 的 base URL，错误时在字段旁提示。

### 5.2 草稿内容

- 默认完整携带请求头和 Cookie；仅在 UI 显示脱敏。
- 生成前弹框提示：`草稿包含 Authorization、Cookie 等敏感信息，仅应导入本机 Postman。`
- 生成 Postman Collection v2.1：collection 内使用 `{{baseUrl}}`，environment 中 `baseUrl=<选择的本地地址>`；保留 method、path、query、headers、raw body。
- 生成 cURL：使用目标 URL、`-X`、headers、`--data-raw`；正确转义 Windows 复制场景。
- 操作按钮：`复制 Postman JSON`、`导出 Postman 文件`、`复制 cURL`。导出路径由用户选择，默认不落盘。
- 不提供“发送”“重放”“调用”按钮，明确标识 `仅生成验证草稿`。

## 6. 项目规则与主题

- 更新 `AGENTS.md`：为接口排查增加“Private 版仅允许 127.0.0.1 CDP/本机代理”的例外说明、IE 代理恢复/证书移除规则、敏感数据禁落盘规则。
- 新增所有 QSS 使用现有主题 token；接口列表、请求详情、脱敏标签、风险提示在 calm/clear/warm/night 下都可读。
- 图标、按钮、Loading、确认弹框沿用现有企业级设计系统；不要再使用原生 QMessageBox。

## 7. 测试与交付

### 单元与定向 UI 测试

1. 首页：逾期、14 天内、仅上线月份、已上线、取消、暂停、空数据的排序和显示。
2. SQL：整理页切换系统与配置页同步；无系统空状态。
3. 格式工具：五种模式转换、错误处理、Java 异常链提取。
4. 浏览器发现：注册表/手选路径、Firefox 提示、loopback 地址限制、启动参数。
5. CDP：用 fake WebSocket/事件验证请求合并、静态过滤、响应失败不崩溃、停止即清空。
6. IE：代理设置备份/恢复、异常恢复、HTTPS 未装证书提示、只删除指定证书指纹。
7. 草稿：URL 重写、Postman JSON、cURL 转义、敏感提示、绝不发网络请求。
8. 四主题下接口详情与代码区文字可读。

### 打包与交接

- 仅运行本轮定向测试，不做全量回归。
- 执行 `build_private_release.ps1`，启动 `dist/PengToolsHub_Private.exe` 验收。
- 标准安装包不得产生改动。
- 更新 `.codex_work/grok_handoff/YYYYMMDD-interface-debug-summary.md`，写清：完成项、测试命令及结果、Private 构建结果、IE 证书/代理未在真实内网验证的限制。
- 仅提交本轮相关源代码、测试、资源、文档；`git commit` 后 `git push origin/main`。
