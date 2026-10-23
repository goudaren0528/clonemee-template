---
type: playbook
slug: readme
status: active
updated: 2026-10-22
tags: [playbook, index]
summary: "规范目录说明：skill、playbook、模板怎么分工，各自放哪，变更记录怎么写"
---

# 规范目录（skills / playbooks / templates）

> 这里是助手「怎么做某件事」的规范。CLAUDE.md 放核心规则（L0），具体流程放这里，按需读。
> 完整清单见 [INDEX.md](INDEX.md)（自动生成）和 [templates/INDEX.md](../templates/INDEX.md)。

## 三种规范怎么分工

| 形态 | 放哪 | 什么时候用 | 例子 |
|---|---|---|---|
| **skill** | `.claude/skills/<slug>/SKILL.md` | 高频、有明确触发场景，希望助手自动调用 | 派发（dispatch）、跟进（task-follow）、周盘点（weekly-inventory） |
| **playbook** | `playbooks/<slug>.md` | 低频、需要查着做的参考流程 | 会话间通信（comms）、Orca 用法（orca）、记忆规则（memory） |
| **模板** | `templates/<slug>.md` + 登记到 `templates/catalog.yaml` | 新建某种记录时照抄结构 | 计划、项目档案、决策、运行记录、审查报告 |

`agents/` 下是角色定义（产品、规划、开发、验收、跟进、调研、排查），派发时用 `--role <slug>` 注入，说明见 [agents.md](agents.md)。

## 目录

| 文件 | 管什么 |
|---|---|
| [agents.md](agents.md) | Agent 区：角色、执行器、派发规则、开发交付流水线 |
| [automation.md](automation.md) | 定时任务：早间建议、夜间巡检怎么装、怎么排错 |
| [comms.md](comms.md) | 会话间通信：回报、待处理清单、指挥台回复、对外通知 |
| [daily-update.md](daily-update.md) | 每日进展怎么整理 |
| [device-setup.md](device-setup.md) | 换设备：路径、clone、需要准备什么 |
| [inbox-merge.md](inbox-merge.md) | 采集草稿怎么并入 journal |
| [memory.md](memory.md) | 记忆分层、会话记忆、记忆卡、巩固 |
| [orca.md](orca.md) | Orca 能做什么、派发和验证该走哪条路 |
| [profile-questionnaire.md](profile-questionnaire.md) | 第一次用：用问卷收集 profile 和 registry |
| [project-onboarding.md](project-onboarding.md) | 新项目怎么建档、怎么进注册表 |
| [registry.md](registry.md) | 工作面注册表、三态、设备模型、采集 |
| [weekly-review.md](weekly-review.md) | 周日 GTD 事情盘点 |

## 变更记录

改 skill、playbook、模板、`CLAUDE.md` 时，在下面加一行（日期 + 改了什么 + 为什么），并在 journal 记一条 `#meta`。新流程先标 `v0`。

- 2026-10-22 — 加了首次使用引导：新 skill `onboarding`（问卷 → 写 profile / 项目 / 领域 → 录第一批待办）、问卷模板 `templates/onboarding-questionnaire.html`、README「首次使用（5 分钟）」
- 2026-10-23 — 模板重新发布：从干净树重建历史，`profile/`、`registry/`、`memory/` 都是空模板和虚构示例，助手人设换成虚构示例 `profile/persona.md`，去掉所有个人项目、账号和个人使用经历
