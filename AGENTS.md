# PengTools Hub V4.27 Private — 项目规则

> 详细交接见 `docs/项目交接/PengToolsV4项目交接文档_V4.26_Private.md`（基线交接；当前运行版本以 `resources/build_info.json` 为准）。开发前先读该文档与本规则。
>
> **CodeGraph 图谱**：仓库已 `codegraph init`，索引在 `.codegraph/`。改代码前/中优先用图谱定位，而不是盲目全文翻。
>
> ```powershell
> codegraph status .
> codegraph sync .                          # 改完代码后同步索引
> codegraph query -p . -l 20 <symbol>
> codegraph callers -p . <symbol>
> codegraph callees -p . <symbol>
> codegraph impact -p . <symbol>
> codegraph files -p . --format tree --max-depth 3
> codegraph affected -p . <changed files>   # 找相关测试
> ```

## 产品边界

- Windows 离线桌面工具台（Python 3.12 + PyQt6），无账号、云同步、插件市场、在线更新、遥测。
- 运行代码中不引入 HTTP/WebSocket/浏览器内核/在线 CDN。
- 当前工作主线是 **Private 私人版**；标准包 `PengToolsHub_Offline_Setup.zip` **禁止被私人功能改动**。
- 发布用 `build_private_release.ps1`；常规维护不要跑 `build_release.ps1`。

## 目录与职责

| 路径 | 职责 |
|---|---|
| `run.py` | 入口、高 DPI、QSS、主窗口 |
| `main_window.py` | 导航、Stack、联动、托盘/关闭、彩蛋 |
| `config.py` | `local_data_dir()`、系统/设置/需求 UI 等路径与默认值 |
| `panels/` | 各导航模块 UI |
| `tools/` | 无界面业务逻辑 |
| `resources/` | QSS、图标、发版模板、学习种子 |
| `data/` | 开发态用户数据（不得打进安装包） |
| `tests/` | 定向测试 |
| `PrivateInstaller/` | 私人包安装模板 |

## 导航与 Stack（必须同步维护）

| 导航 index | 模块 | 显示 |
|---|---|---|
| 0 | 工作台 | 常显 |
| 1 | 证件类型 | 常显 |
| 2 | 升级准备（SQL） | 常显 |
| 3 | 接口文档更新 | 常显 |
| 4 | VIN | 常显 |
| 5 | 网关解密 | 常显 |
| 6 | 运维助手 | 常显 |
| 7 | 设置 | 常显（左下角） |
| 8 | 自我学习（PersonalPanel） | 彩蛋解锁后显示 |
| 9 | 日报（同一 PersonalPanel） | **常显，禁止再隐藏** |
| 10 | 需求管理 | **常显，禁止再隐藏** |

- 导航 8/9 → Stack 8（PersonalPanel）；导航 10 → Stack 9（RequirementPanel）。
- 只有「自我学习」允许彩蛋隐藏；密钥与解锁入口勿擅自改、勿写进普通 UI 文案。

## 数据与升级硬规则

- 唯一数据根：`config.local_data_dir()`  
  - 开发：`PengToolsV4/data/`  
  - 打包：`<exe 旁>/data/`  
  - **禁止**写到 `_MEIPASS` 或用户主目录。
- 升级 = 替换 EXE 等程序文件；**不得删除/覆盖**安装目录 `data`。
- 安装包不得包含用户 `data`（构建脚本会清 `PrivateInstaller/data`）。
- JSON 字段：读时 `setdefault`/默认值兼容旧数据；写时保留已知旧字段。
- 删除类操作：取消在左、确认删除在右，**默认焦点取消**。

## 关键业务联动

1. **需求 → 升级准备 → 发版 Excel**  
   先确认升级日期 → 推荐/勾选需求 → 多系统分别生成 SQL → 写 `resources/release_workbook_template.xlsx`（23 列表头 `RELEASE_HEADERS` 必须一致）。开发分支 SVN 可为空，分支列可空，不可阻塞生成。验证 SQL 不进 SVN 提交目录。
2. **需求 → 日报**：`daily_template()` 生成草稿，不覆盖用户已写内容。
3. **需求 → SQL 整理 / DOCX**：主窗口信号 `_receive_requirement_sql` / `_receive_requirement_docx`。

## UI / 交互约束

- 样式统一用 `resources/style.qss`；QComboBox/QDateEdit 复用下拉箭头样式。
- Loading 为不占布局浮层；文件树后台刷新等静默任务 `show_loading=False`。
- 成功/失败/异常三条路径都要结束 Loading。
- 网关解密的密钥/明文/报文默认不落盘、不写日志。
- 运维助手不自动执行破坏性命令。

## 开发与发布

```powershell
python -m pip install -r requirements.txt
python run.py
.\build_private_release.ps1
```

- 测试策略：**本次改造模块定向测试优先**，不必强求全量回归；业务规则要有函数级断言（尤其发版 Excel）。
- 内网 SVN 在本环境无法验证：UI/交付说明只能写「待用户内网验证」。
- 改 `main_window` 导航时同步：菜单、Stack、状态栏文案、中英文。

## 快速定位

- 需求树/文件树/SVN UI：`panels/requirement_panel.py`
- 需求模型/搜索/日报模板：`tools/requirements.py`
- SVN 命令封装：`tools/svn_workspace.py`
- 升级准备 UI：`panels/sql_panel.py`
- 发版 Excel：`tools/release_prep.py`
- 学习库/日报：`panels/personal_panel.py` + `tools/personal_knowledge.py` / `daily_reports.py`
