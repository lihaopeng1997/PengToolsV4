# Agent 上下文 UI 最终候选：2026-10-09

当前分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。原 dirty 工作树保持，无 stage/commit/push、新包或现有应用窗口操作。本阶段根运行验证和最终字体增量独立源码审查均通过；接受上述源码及合成运行范围，完整 Agent/数据中心/UI 目标保持 active。

## 结果与验证范围

工作台已有可发现的冲突结果计数入口，打开非模态、只读、可滚动的查看器。查看公开历史不执行或重放旧工具，不修改 Store，也不接管运行中的 Worker。内容不显示私有 reasoning 字段和工具参数；最终正文加完整本地化截断标记严格不超过 262,144 个字符，窗口 frame 在父面板及可用屏幕范围内。

上下文预算经 Runtime 标量白名单和 GUI 二次校验，仅用于当前 UI。短状态显示请求精简、上下文受限或等待完成；tooltip 解释本机估值、窗口已知/未知及原文保留。异常和私有 payload 被丢弃，预算事件不写入原始对话或 Store。

实际 calm 字体预览还复现已有执行模式英文选项裁切：修改前和新 UI 首候选均使用固定 44px 余量，实际主题编辑区要扣除 83px。根改用 QStyle 的真实编辑区测量，包含主题内边距及箭头，提前 polish 字体。17px 文本最长 141px，修复后编辑区 143px；21px 文本 176px，修复后 178px。新增回归核查初次显示、切换语言和 960×640 下冲突按钮共存，而不是只比较控件总宽度。

最终根定向验证 **73/73，0skip**，17 个相关 source/test 文件执行前后哈希稳定，selected/catalog/urlopen/socket guard 命中均为 0。六个 suite：workbench runtime/layout、context integration、agent runtime、programming runtime、memory/private continuation。模型和文件工具均为脚本响应及 TEMP 工作目录；配置和布局 mocks 保持到真正 DeferredDelete。证据 `evidence/root-agent-context-ui-20261008/toolbar-font-repair/receipt.json`。

另一独立合成矩阵使用 Windows 的微软雅黑、offscreen Qt 和 calm 主题：zh/en × 17/21px × 960×640/1320×860，共 8 个组合。每个组合核查 3 类预算状态是否完整适配、窗口 frame 在父面板/屏幕内、只读和滚动。根目检了 8 幅面板图及最窄 21px 中英文两幅查看器图。其余查看器只有自动几何检查，没有扩大目检声明。首次作者环境无字体的方框图仍保留，只作边界证据；本次新图使用真实字体。现有应用窗口、系统窗口装饰及用户视觉签收仍未验证。

最终四个源哈希：

| 文件 | SHA256 |
| --- | --- |
| panels/agent_workbench_panel.py | e3535f9030a0935ae26437ab13ad2cdca5ba8750f78c81aa1ce392e37e4bcc3c |
| tools/agent_runtime.py | fe7daa02cdb3e19929044d4be3e3ca6babe255f8d732f25f544e4c65e1fd35bc |
| tests/test_agent_workbench_runtime.py | 6a37b8b6c967254ec89e970fcbef0f014b5adecbbb0ca62f924fec16c530efa6 |
| tests/test_agent_workbench_layout.py | 1c67f3dd1ffe5fd173aa3b22b1417f0828b0685759b47526541bb3bbb4dde949 |

## 证据连续性

阶段二 121 项是其当时冻结的 source/runtime 证据。本阶段首作者 22、根 71、marker 修复作者 23/根 72、最终字体修复根 73 分别留在不同包中，不相加成一次执行。前一次首候选审查发现 marker 超界且收尾遇到获准 writer 漂移，所以未接受；marker 修复后的独立增量 review 已接受四源旧候选。字体修改仅由根完成，使用 a7a46099…/202e83c3… 的两份精确 before 字节与哈希，最终独立增量 review 接受。

四源首尾哈希一致；reviewer 没有运行测试，也没有独立执行 Git，根另核实际 branch/HEAD。审查注明新测试能证明 combo 编辑区文字适配，`container.width() <= 960` 不能单独证明所有工具栏子控件边界；本次整体外观另由根 8 幅合成面板目检支持，实际窗口仍待签收。

一个合成 helper 的首次组合执行在真实 catalog guard 处停止：先前导入的 panel 持有函数别名，下一 fixture 只 patch 原模块不能覆盖已绑定的别名。失败时未打印配置、未解密 Key、未发网络或改产品源；不能为该失败补记“零真实 settings 读取”，因为 alias 的 settings 读取发生在目录 guard 之前。随后为 panel 别名显式 patch，并在单独进程运行最终 probe，通过且 guard 为 0。73 项正式最终测试和全部图矩阵使用完整 mocks，不把 helper 失败改写成产品测试全程通过。最初渲染矩阵和失败前候选证据均保持。

## 后续边界

内网公开模型名称已确认 `qwen3.6`，具体变体/服务版本/真实窗口仍未知，见 [Qwen 接入记录](qwen-context-20261008.md)。客户端当前保留未知窗口模式；tokenizer 及私有续接注入后的最终出站计量、可核查语义摘要、检索/归档和数据中心安全空闲边界尚待实现。未启用历史 thinking 保留，也未做权重量化或服务端 KV 策略更改。

整个目标仍有 DM、SSH/TLS、真实六类数据库、性能、正式包和用户视觉签收待完成。当前结果仅支持上述源码及合成运行范围。
