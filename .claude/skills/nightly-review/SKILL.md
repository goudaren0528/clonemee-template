---
name: nightly-review
description: 夜间自动巡检（无人值守）。由定时任务每天 22:00 以 `claude -p` 调用，也可以手动说"跑一次夜间巡检"：读 tools/nightly.py 生成的当日摘要（提交、改动统计、关键 diff），按项目整理进展，更新项目档案、计划进展、巡检报告、journal 和会话记忆，然后本地提交。
---

# 夜间巡检 `v1.1`

这个 skill 由**派发的独立 session** 执行：每天 22:00 定时任务，或者交互时发现漏跑，由主会话后台运行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/agent-run.ps1 -Job nightly -Date <日期>` 补跑。主会话不自己做巡检，只读产出。

你在**无人值守**模式下运行：没有人会回答问题，也没有人批准操作。只做下面列出的事，拿不准的写进报告的「需要你拍板」，不要自己决定。

## 输入
- 日期：调用方给出，默认今天（`YYYY-MM-DD`）
- 摘要：`inbox/nightly-<日期>.md`。不存在就先跑 `python tools/nightly.py --date <日期>`
- 摘要里已经包含每个提交的增删统计和关键 diff 片段。**只读摘要，不要去读或操作各项目仓库**

## 步骤

### 1. 读上下文（少量）
- `python tools/plan.py --report`：项目和任务的当前状态
- `python tools/sweep.py --snapshot --auto-reclaim`：派出去的会话做完没回报的自动回收（只记录，不关终端、不改任务状态），写本设备快照 `runs/status/<指纹>.md`；结果写进巡检报告的「派出去的任务」，要你处理的写进「需要你确认」；输出末尾有「可以清理：N 个会话、M 个工作区」时原样写进「需要你确认」，附「说『清理一下』，清单用 `python tools/cleanup.py`」（cleanup skill；只提醒，不清理）
- `python tools/gh_tasks.py pull --run`：用 GitHub 管任务的项目（项目偏好 `tasks: github`），issue 关了就把计划里的任务打勾（只读 GitHub，只写 assistant）
- 当天的 journal（如果有）：你已经口述过的就不重复，自动内容只补客观部分
- 有动静的项目的档案（`projects/<slug>/index.md`）

### 2. 逐个项目整理进展
对摘要里每个有动静的项目：
- 按**功能**归纳 1–4 条进展，不按提交罗列。依据是提交标题和 diff，引用提交号或 PR 号
- 证据文件、截图、QA 记录造成的大量增删行数不代表工作量，概括时忽略
- 只写客观可见的变化。意图、价值判断标「（推断）」；不推断你的心情和精力

### 3. 更新文件（全部是本仓库内的 Markdown）
写之前按 [templates/INDEX.md](../../../templates/INDEX.md) 找对应模板：巡检报告用 progress-report.md；时间线行、journal 条目、计划进展用 entries.md 的条目模板。

| 文件 | 怎么改 |
|---|---|
| `projects/<slug>/index.md` | 「当前状态」最上面插入或替换一条：`- （自动 · YYYY-MM-DD）<一两句进展，带提交号或 PR>`，保留你手写的内容；「时间线」追加 `- YYYY-MM-DD — （自动）<一句话> · [巡检](../../reviews/nightly/YYYY-MM-DD.md)`；frontmatter 的 `updated` 改为当天 |
| 计划文件 | 只在提交明确对应某个任务时（同一项目，且标题或 issue 号吻合）：任务下更新 `- 进展：…（自动 · 日期）`，任务行的 `upd:` 改为当天，「进展日志」加一行。**不改任务状态标记** |
| `reviews/nightly/YYYY-MM-DD.md` | 按 `progress-report` skill 的金字塔格式（`templates/progress-report.md`）：结论 → 有动静项目的进展表（项目、事项、当前进展、开始日期、记录日期、当前状态）+ 说明 → 下一步建议。另附一节「今天没有动静」列出其余 active 项目 |
| `journal/YYYY/MM/YYYY-MM-DD.md` | 追加一条 `## 22:00 夜间巡检（自动）`，标签 `#auto` 加各项目的 `#project/<slug>`，写 2–4 行要点，并链接巡检报告。文件不存在就按 `templates/journal.md` 新建 |
| 会话记忆 | 按 `templates/session.md` 写本次会话的 `memory/sessions/<日期>-<会话ID前8位>.md`：`kind: 自动`，`topics: [auto, nightly]`，`importance: 1`，要点只放报告链接，「未结」写需要你拍板的事 |

当天各项目都没有提交：报告只写结论「今天各项目没有提交」和没有动静的列表；journal 写一行。

### 4. 收尾
```bash
python tools/build_index.py
git add -A
git commit -m "docs: 夜间巡检 <日期>（自动）"
```

## 禁止
- 修改、checkout、提交任何项目仓库；读写本仓库以外的文件
- `git push`，以及任何对外发送（消息、PR、issue、评论）
- 改 `registry/projects.yaml` 的 tracked / ignored；确认或排除设备克隆
- 碰 `private/`；改 `profile/preferences.md`
- 改计划任务的状态标记，或派发任务
- 关会话、删工作区或分支、结束进程（`cleanup.py --run`、`unlock --kill`）：只提醒，你确认后由主会话做
- 在自动内容里推断你的心情、精力、意图

遇到以上需要决定的情况，写进报告的「需要你拍板」。
