---
name: 撰写计划
description: 当有多步骤任务的规格或需求时，在接触代码之前使用
---

# 撰写计划

## 概述

编写全面的实施计划，假设工程师对代码库零背景、品味可疑。记录他们需要知道的一切：每个任务涉及哪些文件、需要检查的代码和测试、如何测试。将整个计划分解为小到一口的任务。DRY。YAGNI。TDD。频繁提交。

假设他们是有经验的开发者，但几乎不了解我们的工具集或问题领域。假设他们不太了解好的测试设计。

**开头声明：**"我正在使用撰写计划技能来创建实施计划。"

**上下文：**这应该在专门的工作副本中运行（由头脑风暴技能创建）。

**保存计划到：**`docs/plans/YYYY-MM-DD-<feature-name>.md`

## 小到一口的任务粒度

**每步是一个操作（2-5 分钟）：**
- "写一个失败的测试"——步骤
- "运行它确保它失败"——步骤
- "写最少的代码让测试通过"——步骤
- "运行测试确保它们通过"——步骤
- "提交"——步骤

## 计划文档头部

**每个计划必须以此头部开始：**

```markdown
# [功能名称] 实施计划

> **AI 助手注意：** 必须使用 superpowers:执行计划 来逐任务实施此计划。

**目标：** [一句话描述构建内容]

**架构：** [2-3 句话说明方法]

**技术栈：** [主要技术/库]

---
```

## 任务结构

```markdown
### 任务 N: [组件名称]

**文件：**
- 创建：`exact/path/to/file.py`
- 修改：`exact/path/to/existing.py:123-145`
- 测试：`tests/exact/path/to/test.py`

**步骤 1: 写一个失败的测试**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

**步骤 2: 运行测试确认它失败**

运行：`pytest tests/path/test.py::test_name -v`
预期：FAIL，提示 "function not defined"

**步骤 3: 写最少的实现**

```python
def function(input):
    return expected
```

**步骤 4: 运行测试确认它通过**

运行：`pytest tests/path/test.py::test_name -v`
预期：PASS

**步骤 5: 提交**

```bash
svn commit -m "feat: add specific feature"
```
```

## 记住

- 总是使用精确的文件路径
- 计划中包含完整代码（不是"添加验证"）
- 使用预期输出的精确命令
- 使用 @ 语法引用相关技能
- DRY、YAGNI、TDD、频繁提交

## 执行交接

保存计划后，提供执行选择：

**"计划已完成并保存到 `docs/plans/<filename>.md`。两种执行方式：**

**1. 子Agent驱动（当前会话）**——我按任务派遣新的子Agent，任务之间进行审查，快速迭代

**2. 并行会话（新会话）**——在新会话中打开执行计划，带检查点的批量执行

**选择哪种？"**

**如果选择子Agent驱动：**
- **必须使用子技能：** 使用 superpowers:子Agent驱动开发
- 保持在当前会话
- 每个任务一个新子Agent + 代码审查

**如果选择并行会话：**
- 引导用户在新会话中打开执行计划
- **必须使用子技能：** 新会话使用 superpowers:执行计划
