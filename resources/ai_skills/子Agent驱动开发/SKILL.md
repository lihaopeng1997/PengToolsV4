---
name: 子Agent驱动开发
description: 当在当前会话中执行具有独立任务的实施计划时使用
---

# 子 Agent 驱动开发

## 概述

通过为每个任务派遣新的子 Agent 来执行计划，任务之间进行两阶段审查：首先是规格合规审查，然后是代码质量审查。

**核心原则：** 每个任务一个新子 Agent + 两阶段审查（先规格后质量）= 高质量、快速迭代

## 与执行计划的区别

- **同一会话**（无上下文切换）
- **每个任务一个新子 Agent**（无上下文污染）
- **每个任务后两阶段审查**：先规格合规，后代码质量
- **更快迭代**（任务之间无需人工介入）

## 流程

```dot
digraph process {
    rankdir=TB;

    subgraph cluster_per_task {
        label="每任务";
        "派遣实施子Agent" [shape=box];
        "子Agent提问？" [shape=diamond];
        "回答问题，提供上下文" [shape=box];
        "子Agent实施、测试、自审查" [shape=box];
        "派遣规格审查子Agent" [shape=box];
        "规格审查确认合规？" [shape=diamond];
        "子Agent修复规格缺口" [shape=box];
        "派遣代码质量审查子Agent" [shape=box];
        "代码质量审查批准？" [shape=diamond];
        "子Agent修复质量问题" [shape=box];
        "标记任务完成" [shape=box];
    }

    "读取计划，提取所有任务完整文本，注明上下文，创建 Todo" [shape=box];
    "还有任务？" [shape=diamond];
    "派遣最终代码审查" [shape=box];
    "使用 superpowers:完成开发分支" [shape=box style=filled fillcolor=lightgreen];

    "读取计划..." -> "派遣实施子Agent";
    "派遣实施子Agent" -> "子Agent提问？";
    "子Agent提问？" -> "回答问题..." [label="yes"];
    "回答问题..." -> "派遣实施子Agent";
    "子Agent提问？" -> "子Agent实施..." [label="no"];
    "子Agent实施..." -> "派遣规格审查子Agent";
    "派遣规格审查子Agent" -> "规格审查确认合规？";
    "规格审查确认合规？" -> "子Agent修复规格缺口" [label="no"];
    "子Agent修复规格缺口" -> "派遣规格审查子Agent" [label="re-review"];
    "规格审查确认合规？" -> "派遣代码质量审查子Agent" [label="yes"];
    "派遣代码质量审查子Agent" -> "代码质量审查批准？";
    "代码质量审查批准？" -> "子Agent修复质量问题" [label="no"];
    "子Agent修复质量问题" -> "派遣代码质量审查子Agent" [label="re-review"];
    "代码质量审查批准？" -> "标记任务完成" [label="yes"];
    "标记任务完成" -> "还有任务？";
    "还有任务？" -> "派遣实施子Agent" [label="yes"];
    "还有任务？" -> "派遣最终代码审查" [label="no"];
    "派遣最终代码审查" -> "使用 superpowers:完成开发分支";
}
```

## 提示词模板

实施子 Agent 提示词应包含：
- 任务的完整文本和上下文
- 项目的关键约束（禁止项、路径规范等）
- 明确的预期输出

规格审查子 Agent 提示词应：
- 对照计划验证每项要求
- 检查是否有遗漏或多余

代码质量审查子 Agent 提示词应：
- 检查代码质量、可维护性
- 检查是否有潜在 Bug

## 质量门禁

- 自审查在移交前捕获问题
- 两阶段审查：规格合规，然后代码质量
- 审查循环确保修复真正有效
- 规格合规防止过度/不足构建
- 代码质量确保实施构建良好

## 红牌

**绝不：**
- 跳过审查（规格合规或代码质量）
- 用未修复问题继续
- 在规格合规审查之前开始代码质量审查（错误顺序）
- 在任一审查有未解决问题时移动到下一个任务
- 让子 Agent 自审查代替实际审查（两者都需要）

## 集成

**必需工作流技能：**
- **superpowers:撰写计划** - 创建此技能执行的计划
- **superpowers:请求代码审查** - 审查者子 Agent 的代码审查模板
- **superpowers:完成开发分支** - 所有任务完成后的完整开发

**子 Agent 应使用：**
- **superpowers:测试驱动开发** - 每个任务遵循 TDD

**替代工作流：**
- **superpowers:执行计划** - 用于并行会话而不是同会话执行
