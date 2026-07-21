# tools — 无界面业务逻辑（按域索引）

> 文件保持扁平命名（与架构一致，import 稳定：`from tools.xxx import ...`）。  
> 下表仅作阅读分组，**不要**为分组而改 import 路径。

## 需求与发版

| 文件 | 职责 |
|---|---|
| `requirements.py` | 需求模型、标记、日报模板 |
| `release_prep.py` | 发版 Excel（23 列） |
| `svn_workspace.py` | SVN 命令封装 |
| `sql_tool.py` | SQL 拆分/分类 |
| `pinyin_search.py` | 拼音搜索 |

## 文档与知识

| 文件 | 职责 |
|---|---|
| `docx_updater.py` / `docx_template_registry.py` | DOCX 更新 |
| `personal_knowledge.py` | 学习库 |
| `daily_reports.py` | 日报 |
| `json_viewer.py` | JSON 解析（供 UI） |

## 接口排查

| 文件 | 职责 |
|---|---|
| `http_capture.py` / `ie_proxy.py` | MITM 抓包与系统代理 |
| `browser_debug.py` | CDP |
| `interface_debug_store.py` | 配置（无报文） |
| `interface_session_view.py` | 列表列/筛选 |
| `iface_request_test.py` | 环境请求测试、导出导入 |
| `interface_drafts.py` | 草稿/cURL 兼容 |

## 加解密与其它工具

| 文件 | 职责 |
|---|---|
| `gateway_crypto.py` | SM2+SM4 |
| `credit_code.py` / `id_documents.py` / `china_regions.py` | 证件 |
| `vin_generator.py` | VIN |
| `ops_commands.py` | 运维命令 |
| `text_dev_helpers.py` / `xml_formatter.py` | 文本/XML |
