---
type: playbook
slug: agents
status: active
updated: 2026-10-22
tags: [meta, dispatch, agents]
summary: "Agent 区：角色化 agent 的定义方式、各工具怎么注入角色、派发时怎么用"
---

# Agent 区 `v0`

> 助手（主会话）负责个人辅助、讨论、确认、派发和监督；具体工作交给**独立角色的 agent**，通过派发会话来用。
> 角色定义放在 [`agents/`](../agents/INDEX.md)，一个角色一个文件，模板 [templates/agent-role.md](../templates/agent-role.md)。

## 各工具怎么注入角色

| 工具 | 原生支持 | 怎么用 | 备注 |
|---|---|---|---|
| **Claude Code** | ✅ | `claude --agent <名字>` 用某个 agent 跑整个会话；`--agents <JSON 或文件>` 启动时临时定义，不用安装；`--append-system-prompt[-file]` 追加系统提示 | 定义文件放 `~/.claude/agents/` 或项目 `.claude/agents/`（frontmatter：name、description、tools、model） |
| **OpenCode** | ✅ | `opencode --agent <名字>` / `opencode run --agent <名字>` | 定义文件放 `~/.config/opencode/agent/*.md`（frontmatter：description、mode、model、permission，正文是提示词）。可以在这里放自己的 planning-agent、coding-executor 这类角色 |
| **Codex** | ⚠ 部分 | `codex --profile <名字>` 叠加 `$CODEX_HOME/<名字>.config.toml`；`-c key=value` 覆盖配置 | 没有「角色」的概念；能不能通过配置注入系统提示待验证（?） |
| **Orca** | 经由提示词 | `orca agent launch / worktree create --agent <claude/codex/opencode> --prompt "<…>"` | `--agent` 选的是工具，不是角色；没有透传启动参数的口子，所以在 Orca 里用**提示词注入** |

## 助手的做法

**v0（已实现）：提示词注入，所有工具和 Orca 都适用**
`python tools/dispatch.py --task T2 --role accept`：读 `agents/accept.md`，把「角色 → 禁止」几节放在任务说明前面，一起发给 agent；执行器默认用角色的 `default_executor`。

**v1（待做）：原生安装**
`tools/agents.py --install` 把 `agents/*.md` 转成各工具的原生格式，写到本设备的用户目录：
- Claude Code → `~/.claude/agents/assistant-<角色>.md`，本地模式用 `claude --agent assistant-<角色>`
- OpenCode → `~/.config/opencode/agent/assistant-<角色>.md`，用 `opencode --agent assistant-<角色>`
- Codex → 继续用提示词注入，或者 profile（验证后）

好处是角色能带自己的工具权限和模型设置；代价是每台设备要安装一次（和定时任务一样）。

## 派发规则：执行器和工作区
角色的 status 是 draft 还是 active 看各自说明；拿不准时先看角色说明 `agents/<slug>.md`。

**先有方案再执行**：开发类 → 产品 Agent 出 PRD；非开发类 → 规划 Agent 出执行方案（[模板](../templates/exec-plan.md)，放 `plans/exec/`）；小事由主会话写带「授权」的执行说明。你确认的方案就是跟进 Agent 的授权范围（dispatch skill 第 0 节）。

| 场景 | 角色 | 默认执行器 | 可选 | Orca 里在哪做 |
|---|---|---|---|---|
| 讨论需求、写 PRD（开发类的方案） | 产品 Agent（product） | Claude Code | OpenCode | 新建工作区（主会话也在 assistant 提交，隔开更安全） |
| 非开发类的事先出执行方案（目标、步骤、谁做、验收、风险、授权范围） | 规划 Agent（planner） | Claude Code（固定） | — | 现有目录（只读项目；方案写进 assistant `plans/exec/`，不提交，交接时主会话提交） |
| 技术调研、读源码出现状报告 | 调研 Agent（research） | Claude Code | — | 现有目录（只读） |
| 只读排查：读代码查 bug、线上 issue | 排查 Agent（investigate） | Codex | OpenCode | 现有目录（只读） |
| 改代码：实现计划、按包实现、修复 | 开发 Agent（dev） | OpenCode | Claude Code | 新建工作区 |
| 验收：用例、两路审查、e2e 和浏览器真测、体验准备 | 验收 Agent（accept） | Codex | Claude Code | 单独审查用现有目录；流水线里由 delivery.py 安排 |
| 跟进：**所有派发默认配一个** | 跟进 Agent（pm） | Claude Code（固定） | — | assistant 目录 |

- **执行器**谁说了算：`--agent` > 角色的 `default_executor` > 任务行 `exec:` > 兜底。兜底（没有角色也没写执行器）：任务标题看起来要改代码的用 OpenCode，其他用 Claude Code，方案里标「兜底」
- **工作区**谁说了算：`--new` / `--existing` > 角色的 `workspace`（new / existing）> 兜底（没有角色时，标题像只读排查、文档整理的用现有目录，其余新建）。方案里写明选了哪种、为什么，你可以改
- 分工：**调研只出现状报告**，不写 PRD（产品拿报告和你澄清后写）；**有 PRD 的审查归验收，没有 PRD 的问题定位归排查**；两者都不改代码
- 回报：派出去的会话只在 完成 / 要拍板 / 卡住 / 交接 时回报派它的主会话（流水线里开发、验收只和跟进 Agent 说话；单个任务有跟进 Agent 时由它回报，产品、规划 Agent 的「交接」自己发）。送达用 `tools/notify.py send`：先记待处理清单 `runs/pending.md`，再推一行给主会话（[comms.md](comms.md)）

## 开发交付流水线
PRD 定稿、你说「确认派发」后，`tools/delivery.py start … --run`：
1. 从 develop 切 Orca 工作区 `req/<需求>`，复制 PRD 到 `docs/delivery/<需求>/`，建交付看板 `runs/<日期>-<项目>-<需求>.md`
2. 同时启动：开发 Agent（dev-plan 写实现计划）、验收 Agent（accept-cases 写用例）、跟进 Agent（在助手仓库里）
3. 之后全由跟进 Agent 用 `delivery.py` 调度：approve → pkg（按依赖并行，≤3 条线，每包一个 Orca 工作区 `pkg/<需求>-P<n>`）→ accept-review → dev-fix → merge → accept-test → sync → finish（本地快进合回 develop）
4. 推送 develop、合到 main、上线由你做

| 角色 | 执行器 | skill |
|---|---|---|
| 开发 Agent | OpenCode | dev-plan · dev-build · dev-fix |
| 验收 Agent | Codex | accept-cases · accept-review · accept-test |
| 跟进 Agent | Claude Code | task-follow |

产物在项目仓库 `docs/delivery/<需求>/`：prd.md、impl-plan.md、cases.md、packages/、reviews/、acceptance/。模板见 templates/INDEX.md（实现计划、验收用例、工作包进度、审查报告、测试报告、交付看板）。

## 跟进 Agent：单个任务
所有任务派发时，默认再在助手仓库里启动一个跟进会话（`--no-follow` 关掉）。产品、规划 Agent 也配，跟进 Agent 不替你答问卷、评审和方案确认，只管卡住、跑偏、超出授权：
- 拿到的是 `agents/pm.md` 的角色设定 + 运行记录里的「跟进说明」（运行记录路径、执行会话的 Orca 终端 handle）
- 用 CronCreate（或 `/loop 5m`）每 5 分钟跑一次 `tools/follow.py` 看现状；Orca 模式能读执行会话的终端（`orca terminal read --screen`）、回复它（`orca terminal send`），本地模式只能看改动和提交
- 授权内的决定按你的做事偏好（`profile/preferences.md`）直接定并记进「决策记录」，超出的升级给你；做完按验收标准核对，写「验收」、改 status，向你汇报
- 授权写在运行记录的「授权」一节，**从确认过的执行方案或 PRD 生成**（任务引用的执行方案 > PRD > 任务自己的执行说明），你在任务行「- 授权：」或开场粘贴「授权」行写的原样记成「你写明」、优先；不发给执行会话。「可以派的角色」或你写明里点了角色的，跟进 Agent 用 `follow.py --spawn` 派生子任务（子运行记录 `<父>-S<n>.md`，回链父任务，不改计划文件、不另配跟进、不回报主会话），见 task-follow 第 7 节
- 决策边界：task-follow skill 第 5 节——范围内按偏好定；默认升级（推送、合并、发布、删除、对外发消息、数据迁移，授权明说可以的照做）；红线（改目标范围验收、花钱、动生产和真实数据、账号权限，写了也升级）。流水线的授权见本节和 task-follow skill 第 5 节

## 体验验收
有界面的交付，你验收时点链接就能用：
- 流水线里：合回 develop（本地）之后、推送之前，跟进 Agent 派验收 Agent 做「体验准备」（accept-test skill）；单任务派发没有验收 Agent，执行会话自己做（dispatch.py 给改代码的任务说明加一句「体验」）
- 用 `tools/experience.py start` 在后台启动、等能打开，给出体验卡（链接、体验要点 3 条左右、用例通过情况、关闭命令）；当天 24:00 看门狗自动关；你「验收通过」或「打回」后执行 `experience.py stop`
- 启动不了照样交「完成」，写清卡在哪、怎么手动启动；没有界面的写「没有界面，不需要体验环境」

## 产品 Agent 的视觉规范
问卷、原型、评审页共用 `tools/ui-tokens.css`：字号六级（12 / 13 / 14 / 16 / 20 / 28）、间距 4 的倍数、圆角三档、浅色深色两套配色；导航标题按钮不用 emoji。qform.py、prd_html.py 生成时自动内嵌；prd_html.py 还会提示原型不合规范、PRD 里疑似写了实现方式的地方。

## 维护
- 新角色按模板建，`status: draft`，你确认能力和习惯后改为 `active`；派发 `draft` 角色时工具会提醒先看角色说明。角色转正或调整都在规范变更记录里加一行
- 角色要写给 agent 看（第二人称），写清能力、习惯、输出、禁止；「参考」一节不注入
- 角色有调整时在规范变更记录里加一行
