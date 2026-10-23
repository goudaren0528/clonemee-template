---
name: plan
description: 把讨论整理成计划与任务。当你讨论工作目标、"接下来要做什么"、"整理成计划/待办"、安排一周重点，或确认要推进某件事时使用：检索上下文、起草 plans/ 下的计划文档（目标→任务→验收→执行者），确认后激活，并与目标、项目档案、todo、周计划互相链接；任务格式为后续派发给 Claude Code / Codex / OpenCode 执行预留字段。
---

# 计划与任务 `v1`

计划是 助手仓库的**核心文档**：你的工作会逐步收敛到这里——先和助手讨论、确认计划，再由助手派发给不同的 agent session 执行并监督进度。所以计划必须是 Markdown 文档、结构稳定、能被工具解析。

## 文档体系

| 文件 | 内容 | 节奏 |
|---|---|---|
| `plans/goals.md` | 长期目标（半年、季度），每个目标链到推进它的计划 | 季度复盘，变化时改 |
| `plans/YYYY-MM-DD-<slug>.md` | 一个计划 = 一件要推进的事：目标、范围、任务、验收、风险、进展日志 | 推进时持续更新 |
| `plans/weekly/YYYY-Www.md` | 周计划：本周 3–5 个焦点，链到计划任务 | 周日晚上做周回顾时生成下周 |
| `todo.md` | 零散小事：一句话说得清、不需要验收标准的 | 随时 |

**判断放哪**：需要拆解、要验收、会持续几天以上、或者可能派给 agent 做的 → 计划；其余进 todo.md。

## 任务格式（工具会解析，保持一致）

```markdown
- [~] T1 动词开头的任务标题 · @me · exec:me · proj:example-project · start:2026-10-20 · due:2026-10-25 · upd:2026-10-21 · dep:T0
  - 验收：做到什么程度算完成（可检查）
  - 说明：给执行者的背景、范围、注意事项（派给 agent 时就是 brief 的主体）
  - 进展：最近一次进展，一句话
```

- 状态：`[ ]` 待办 · `[~]` 进行中 · `[!]` 受阻（在说明里写原因）· `[x]` 完成 · `[-]` 放弃
- 字段之间用 ` · ` 分隔，可以省略：
  - `@`：负责人，`me`（你）、`assistant`（助手在对话里做）或同事名
  - `exec:`：实际执行者，`me` | `assistant` | `claude-code` | `codex` | `opencode` | 工具名
  - `proj:`：项目 slug（派发时据此找本设备路径：`python tools/device.py show`）
  - `repo:`：多仓库工作面里用哪个仓库（仓库名或 owner/repo，如 `repo:example-api`），派发时据此选仓库
  - `start:`：开始日期，任务第一次变成 `[~]` 时填
  - `upd:`：记录日期，每次更新「进展」时改成当天
  - `due:`：截止日期；`dep:`：依赖的任务号
  - `run:`：派发后的运行记录 ID（派发能力上线后由助手填写）
  - `issue:`：对应的 GitHub issue，写成 `owner/repo#12`（项目偏好 `tasks: github` 时由 `tools/gh_tasks.py push` 回填）
- 任务号在一个计划内唯一。跨文件引用写成 `<计划文件名>#T3`

## 项目偏好
每个项目可以有自己的做法，写在项目档案的「项目偏好」一节；工具要读的部分写进档案 frontmatter 的 `prefs`。**给某个项目起草计划或任务前，先读它的项目偏好。**
- `tasks: github`（加 `tasks_repo: owner/repo`）：这个项目的任务和计划用 GitHub 的 milestone + issue 维护。assistant 计划照样写（总账：目标、顺序、和其他项目一起排），你确认后跑 `python tools/gh_tasks.py push <计划>` 出方案、确认后 `--run`：一个计划一个 milestone，每个未完成任务一个 issue，任务行回填 `issue:`。之后详情和讨论在 GitHub，issue 关了由 `gh_tasks.py pull --run` 同步回计划（夜间巡检会跑）。GitHub 上的内容是对外的：不用内部称呼「你」，不带 private 和 assistant 内部链接
- 没写 = 只在 助手仓库维护
- 你提到某个项目「要用 xx 方式」时，记进那个项目的「项目偏好」（带日期和来源），工具要用的同时写进 `prefs`

## 流程

### 1. 检索上下文
- `plans/goals.md`、`python tools/plan.py`（现有任务，避免重复）、相关项目档案、`profile/now.md`
- `python tools/recall.py <关键词>` 找过去讨论过的相关内容

### 2. 起草
按 [templates/plan.md](../../../templates/plan.md) 新建 `plans/<今天>-<slug>.md`，`status: draft`：
- **目标**：一句话 + 完成标准
- **为什么现在做**：关联的长期目标（链到 goals.md）
- **范围**：做什么、不做什么
- **任务**：按上面的格式，每个任务都写验收；能派给 agent 的标 `exec:`，并写清说明
- **风险与未知**、**相关**（项目、决策、会话记忆）
- 只把你说过的当事实；你补充的任务、估计的日期，标「（建议）」

### 3. 确认
把计划摘要用表格发给你（任务、负责人、执行者、截止），问清最多 3 个关键问题。你确认后：
- `status: active`，删掉「（建议）」标记或按反馈修改
- `exec:` 不是 `me` / `assistant` 的任务，**你确认之前不派发**

### 4. 链接
- `plans/goals.md` 对应目标下加一行链接
- 相关项目档案「在做」里链到计划（`plans/…#T2`），时间线记一笔
- 你本人近 7 天要做的任务，在 `todo.md` 写一行指针（不复制细节）
- 本周焦点相关的，写进当周 `plans/weekly/YYYY-Www.md`
- 跑 `python tools/build_index.py`（计划索引和总地图自动更新）；journal 记一条；更新会话记忆；提交

### 5. 维护
- 每日进展（`daily-update` skill）时更新任务状态、`- 进展：` 和 `upd:`，在计划的「进展日志」加一行；夜间巡检只补「进展」，不改状态
- 你问进展时按 `progress-report` skill 汇报
- 周日周回顾：每个 active 计划过一遍（进度、受阻、是否调整），生成下周周计划
- 全部任务完成或放弃 → `status: done` / `dropped`，写一段复盘，从 INDEX 的活跃计划移到已结束

## 派发与监督
确认后的 `exec: claude-code | codex | opencode` 任务，由助手生成 brief、在对应项目目录启动后台 session 执行、记录运行，并定期检查进度、回收产出、更新计划。入口见 [dispatch skill](../dispatch/SKILL.md)；没有 Orca 等运行环境时，由助手生成 brief、你手动执行。

## 查看
```bash
python tools/plan.py               # 所有未完成任务：逾期 / 进行中 / 受阻 / 7 天内到期 / 其他
python tools/plan.py --owner me    # 只看你本人的
python tools/plan.py --agent       # 只看派给 agent 执行的
```
