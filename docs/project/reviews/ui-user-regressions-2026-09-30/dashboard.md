# 首页示例任务与测试点操作回归

状态：实现与最终集成验证完成。最终运行在 `main_window.py` 和 `requirement_panel.py` 冻结后执行。

仓库：`D:/PengTools`；分支：`dev/2026-09-23`；审计基准：`818fba72bf437de9bd49fcd5b198fb1270096a53`。

## 变更

需求台账为空时，首页会显示带 `demo-*` 标识的示例任务。示例行仍保持只读。首页现在明确显示“示例预览 · N 项”、说明样例不代表真实任务且不能直接编辑，并提供“去管理真实需求”按钮，通过现有导航进入需求管理（索引 10）。提示说明可导入已有文件夹或新建需求，并说明本月列表按本月实际发布日期展示真实需求。真实需求继续使用原有状态编辑器和测试点编辑器。

`tests/test_dashboard_requirement_live_runtime.py` 增加真实 WebEngine 行为覆盖：空台账下验证提示、示例只读和 CTA 确实打开需求管理，且没有发出假 ID 业务动作；真实合成需求沿原生编辑器把测试点从未勾选改为勾选再取消，核对持久化布尔值、每次唯一的 `requirements_changed` 和导航前自然摘要比例，再返回同一文档检查 DOM。原状态保存及新增测试点流程仍保留。运行时还确认导航后 `RequirementPanel` 和测试点页签是当前页，复选框可见且启用，其点击中心位于滚动视口内并落在可见区域；控件无需额外滚动即可操作。

## 验证与边界

- `npm --prefix frontend run typecheck`：通过，退出码 0。
- `npm --prefix frontend run build:embedded`：通过，退出码 0。
- `npm --prefix frontend run verify:embedded`：通过，退出码 0；双入口、离线资源引用和 WebChannel 合约检查通过。
- `.venv-build\Scripts\python.exe -B -m unittest tests.test_dashboard_requirement_live_runtime -v`：在 `main_window.py` 与 `requirement_panel.py` 最终冻结后运行，2 项测试、退出码 0。覆盖空台账 demo CTA 与真实 Vue→MainWindow→RequirementPanel→原生编辑器保存流程。
- Windows-QPA 子进程只使用 TEMP 中的合成需求、WebEngine profile/cache/log；实际 Vue 页面、MainWindow、HomeBridge 和原生编辑器均参与。两种场景均观测到零外部请求、零 Python socket 连接尝试、零 renderer 终止。demo 场景确认提示包含“导入已有文件夹或新建需求”和本月实际发布日期条件、3 行示例只读、CTA 导航到需求管理索引 10、未发出假 ID 业务动作。
- 合成测试点动作导航前各发出 1 次自然摘要更新，比例依次为 `1/1`、`0/1`、`0/2`；每次回到同一页面和 document nonce 后，DOM 比例与持久化状态一致，返回导航额外摘要更新为 0。原始测试截图为 1440×900 窗口，显示比例为 2160×1350。
- 最终运行的复选框截图分别记录真实点击前、点击后、取消前和取消后：`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-_qu23uv2\native-test-points-editor-before-check.png`（未勾选）、`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-_qu23uv2\native-test-point-checkbox-check.png`（已勾选且持久化 `true`）、`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-_qu23uv2\native-test-points-editor-before-uncheck.png`（已勾选）、`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-_qu23uv2\native-test-point-checkbox-uncheck.png`（未勾选且持久化 `false`）。复选框几何为 `[11, 11, 28, 28]`，编辑器可视区域为 `820×160`；其中心位于视口和可见区域内，无需滚动。截图状态与真实控件及持久化值一致。
- 最终 demo 页面截图：`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-demo-runtime-vmejprbz\dashboard-demo-preview.png`。真实需求交互截图：`C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-_qu23uv2\dashboard-after-live-actions.png`。两个窗口均为 `1440×900`，截图像素为 `2160×1350`；demo 图已包含最终导入/日期说明。
- 需求目录扫描或从所选目录导入台账不在本修复内；此变更只为当前空台账/示例状态提供清楚说明和正确管理入口。

## 基线与产物

接手快照 `C:\Users\Lenovo\AppData\Local\Temp\pengtools-user-regressions-20260930-45b9a3e1c52b46a1b1249ee44f888837\before.json` 中的 16 个输入文件 SHA-256 均与开始编辑时一致。

| 文件 | 接手快照 SHA-256 | 当前 SHA-256 |
| --- | --- | --- |
| `frontend/src/dashboard/components/MonthlyTasks.vue` | `f0ee14c36a2a8d5663d504eb904e350c8110f0841696e8bc6265051862a7fc04` | `CF6B7AB031C35C25D236918CBD6966352EE7CBB789173931B4770D7ABA355063` |
| `tests/test_dashboard_requirement_live_runtime.py` | `8ea11e5f2b3b613f7604d13d5840471df9573778a91ac8fe7f192064671cf743` | `6AA8E734374D9814BFCD508BCBBBDF0484548CBFAC246C59B885A92DD86EA7A8` |

`resources/webui/vue/` 已由 `build:embedded` 生成，入口引用 dashboard JS `dashboard-AmcRCWtM.js` 与 CSS `dashboard-BjN8q73b.css`；未手改构建产物。`typecheck`、`build:embedded`、`verify:embedded` 和冻结后的真实运行测试均通过。当前 HTML SHA-256：`947BD741839350184DC1851208531E9010835D41DA249CBBD55E70A1224CBE31`；JS：`02611804AAF76B2965B6C8863FCBC38719ADB1A9485B0297E9E7DA9718F12A0C`；CSS：`807DF056759CBA17DAB7DF900310ACD708FF5E8B48FA755A3E4E1E0DC91F2B2B`。
