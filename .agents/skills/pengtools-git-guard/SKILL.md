---
name: pengtools-git-guard
description: PengToolsHub Git 协同守护规则。在任何会修改仓库代码并提交/push 的任务开始与结束时触发，保证开工基线锁定、修改范围收敛与 push race 检查。
---

# PengToolsHub Git Guard SOP

## 1. 必填输入 (Required Inputs)

每个任务必须由 Task Prompt 明确提供：
- `REPOSITORY`
- `BASELINE_COMMIT`

两个值都必须由当前 Task Prompt 明确提供，不得从 Skill、历史聊天、memory 或旧报告推断。

## 2. 开工基线检查 (Start Guard)

新任务开始时必须首先执行：

```bash
git status --short
git fetch origin --prune
git switch main
git pull --ff-only origin main
git rev-parse HEAD
git rev-parse origin/main
```

校验要求：
- `HEAD == origin/main == BASELINE_COMMIT`
- `working tree clean`

若条件不满足：输出 `RESULT: TASK_LOCK_FAILED` 并立即 STOP。

## 3. 禁止的恢复操作 (Forbidden Operations)

严禁为了制造一致状态自行执行以下操作：
- `git reset --hard`
- `git clean`
- `git stash`
- `git rebase`
- `git merge`
- `git push --force`

## 4. 修改范围锁定 (Scope Guard)

修改完成后必须检查工作区状态：

```bash
git diff --check
git status --short
```

校验要求：
- 严格仅包含当前任务允许的目标文件变更。
- 不得出现未声明的第三个修改或未跟踪文件。若发现范围失控，输出 `RESULT: BLOCKED` 并 STOP。

## 5. 推送竞态检查 (Push Race Guard)

Commit 完成后、Push 前必须再次检查远端状态：

```bash
git fetch origin
git rev-parse origin/main
```

校验要求：
- `origin/main == BASELINE_COMMIT`

若远端已偏离开工基线：输出 `RESULT: REMOTE_MOVED` 并立即 STOP，不得自行 rebase 或 merge。

## 6. 推送后检查 (Post-Push Verification)

远端无变化时执行推送：

```bash
git push origin main
git fetch origin
git rev-parse HEAD
git rev-parse origin/main
git status --short
```

校验要求：
- `HEAD == origin/main`
- `working tree clean`

完成后输出最终报告并 STOP。
