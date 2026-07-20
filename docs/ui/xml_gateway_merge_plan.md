# XML 格式化 → 网关加解密模块 合并方案

> 状态：**已设计，下一轮落地**  
> 本轮（UI 全量重构 · Loading/弹窗）仅评估与入口文案靠拢，不拆改 `tools/xml_formatter.py` 业务内核。

## 目标

把 XML 能力做成「热门工具」完整度，并**最终落在网关解密（GatewayDecodePanel）同一工作面**，避免用户在多个模块间跳转。

## 现状

| 能力 | 现状 | 位置 |
|------|------|------|
| 外层引号清理 | ✅ | `tools/xml_formatter.normalize_xml_input` |
| 转义字符处理 | ✅ JSON 风格 `\"` `\n` `\t` `\uXXXX` | 同上 |
| 一键格式化 | ✅ | `format_xml_text` + `JsonViewer.format_xml_current` |
| 错误提示（行列） | ✅ 逻辑；本轮 UI 已改统一弹窗 | `show_warning` |
| 复制 | ✅ 明文复制 / 格式化 JSON 复制 | Gateway + JsonViewer |
| 清空 | ✅ Gateway 底栏清空 | 网关面板 |
| 大文本查看 | ⚠️ 可编辑 `QPlainTextEdit`，尚无字号/换行/折叠增强 | JsonViewer 文本页 |
| XML 树视图 | ❌ 仅 JSON 树 | — |
| 独立「XML 工具」分区 | ❌ 工具条一个按钮 | JsonViewer 内 |

## 目标产品形态（下一轮）

```
网关解密
├── 参数区（系统 / 环境 / SM4 Key）
├── 工作区 Splitter
│   ├── 左：密文输入
│   └── 右：结果工作台（Tab）
│         ├── 明文 / JSON（现有）
│         ├── XML 工具（新）
│         │     粘贴 → 清洗 → 格式化 → 错误高亮 → 复制 / 清空
│         └── （可选）树形：JSON 树 | 简易 XML 大纲
└── 动作栏：清空 · 复制 · 请求解密 · 响应解密
```

### 功能清单（对标热门工具）

1. **清洗**：外层多层双引号、首尾空白、常见转义  
2. **一键格式化**：保留声明、缩进 2 空格、中文不破坏  
3. **错误提示**：行/列 + 可选跳转到文本行  
4. **复制**：原文 / 格式化结果  
5. **清空**：仅 XML 区 / 整页（复用网关清空）  
6. **大文本体验**：等宽字体、可选自动换行、字数/行数状态条  
7. **懒人**：解密若像 XML 自动提示「用 XML 页查看」；粘贴即格式化开关  

## 技术落点

| 层级 | 文件 | 动作 |
|------|------|------|
| 纯逻辑 | `tools/xml_formatter.py` | 保持；可增 `minify` / `validate_only` |
| 查看器 | `ui/json_viewer.py` 或拆 `ui/xml_workspace.py` | XML 专用工具条与状态 |
| 宿主 | `panels/gateway_panel.py` | 右栏 Tab 或分区挂载 XML 工作台 |
| 样式 | `resources/style.qss` | `#gateway-xml-zone` 与网关卡片区分色 |
| 测试 | `tests/test_xml_formatter.py` | 逻辑已覆盖；UI 冒烟另增 |

## 不做（明确边界）

- 不引入网络请求 / 在线 schema  
- 不把 XML 工具塞进需求/SQL 面板  
- 不改 `gateway_crypto` 加解密算法  

## 风险与迁移

- `JsonViewer` 被网关独占使用 → 合并成本低  
- 现有「XML 美化」按钮需保留快捷入口，避免用户找不到  
- 大报文（>1MB）时 minidom 可能慢 → 下一轮可加 busy 文案（短任务可不 Loading）  

## 验收标准（下一轮）

- [ ] 粘贴带引号/转义的 XML，一点格式化成功  
- [ ] 非法 XML 弹出企业级警告并显示行列  
- [ ] 复制 / 清空可用  
- [ ] 与解密流程同一面板，无需切换导航  
- [ ] 定向单测 + 手动大文本粘贴抽查  

## 本轮已做的铺垫

- 网关明文区标题改为「解密明文 / JSON·XML 工具」  
- 说明文案点出 XML 美化能力  
- JSON/XML 错误改走统一 `show_warning` 弹窗  
- 纯逻辑与单测保持稳定，供下一轮直接挂载  
