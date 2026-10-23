# 规范目录：skill、流程、模板

> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。
> 高频、有触发场景的写成 skill（Claude Code 按描述自动调用）；低频参考流程是 playbook；模板供新建文件用。变更记录见 [README](README.md)。

**生成于** 2026-10-22

## Skills（`.claude/skills/`）

| skill | 什么时候用 |
|---|---|
| [accept-cases](../.claude/skills/accept-cases/SKILL.md) | 验收用例（验收 Agent 的 skill，开发交付流水线）。PRD 定稿、开发还没开始时使用 |
| [accept-review](../.claude/skills/accept-review/SKILL.md) | 两路审查（验收 Agent 的 skill，开发交付流水线）。一个工作包自测完、跟进 Agent 派来审查时使用 |
| [accept-test](../.claude/skills/accept-test/SKILL.md) | 集成测试（验收 Agent 的 skill，开发交付流水线）。所有工作包合进需求分支后、或修复后复测时使用 |
| [cleanup](../.claude/skills/cleanup/SKILL.md) | 清理做完的会话和 Orca 工作区。当你说"清理一下""关掉做完的会话""删掉没用的工作区""工作区删不掉"，或者开场卡片、夜间巡检、sweep 提示「可以清理 |
| [daily-update](../.claude/skills/daily-update/SKILL.md) | 每日进展整理。当你说今天/昨天的进展、日报、"今天的工作情况"、"今天做了…"时使用 |
| [dev-build](../.claude/skills/dev-build/SKILL.md) | 按工作包实现并自测（开发 Agent 的 skill，开发交付流水线）。实现计划审过、跟进 Agent 派了一个工作包时使用 |
| [dev-fix](../.claude/skills/dev-fix/SKILL.md) | 按问题修复（开发 Agent 的 skill，开发交付流水线）。验收 Agent 的审查报告或测试报告里有「范围内要修」的问题、或者合并有冲突时使用 |
| [dev-plan](../.claude/skills/dev-plan/SKILL.md) | 实现计划（开发 Agent 的 skill，开发交付流水线第一步）。PRD 定稿后、写代码前使用 |
| [dispatch](../.claude/skills/dispatch/SKILL.md) | 派发任务。当你说"派发任务""派发 T2""把这个派给 codex/claude/opencode""开个工作区去做"时使用 |
| [morning-brief](../.claude/skills/morning-brief/SKILL.md) | 早间建议与今日事项确认。周一至周六 10:00 由定时任务以 `claude -p` 无人值守生成当天的建议（plans/daily/<日期>.md，草案）；你说"今天做什么""看看今天的建议""确认今天的事"时，带你过一遍并确认 |
| [nightly-review](../.claude/skills/nightly-review/SKILL.md) | 夜间自动巡检（无人值守）。由定时任务每天 22:00 以 `claude -p` 调用，也可以手动说"跑一次夜间巡检" |
| [onboarding](../.claude/skills/onboarding/SKILL.md) | 首次使用个人助手模板时的引导。当你说"第一次用""开始用这个仓库""帮我初始化""引导我填一下""onboarding"时使用 |
| [plan](../.claude/skills/plan/SKILL.md) | 把讨论整理成计划与任务。当你讨论工作目标、"接下来要做什么"、"整理成计划/待办"、安排一周重点，或确认要推进某件事时使用 |
| [pm-clarify](../.claude/skills/pm-clarify/SKILL.md) | 需求澄清（产品 Agent 的 skill）。你描述一个需求、想法或问题，信息还不足以写 PRD 时使用 |
| [pm-prd](../.claude/skills/pm-prd/SKILL.md) | PRD 与原型（产品 Agent 的 skill）。需求澄清完成、你确认可以写方案时使用 |
| [progress-report](../.claude/skills/progress-report/SKILL.md) | 进展汇报。当你问"进展怎么样""汇报一下进度""现在各个任务/项目到哪了""XX 项目什么情况"时使用 |
| [report-cards](../.claude/skills/report-cards/SKILL.md) | 汇报卡片（向你汇报要你回复的事）。当有事要你回复或拍板时使用 |
| [task-follow](../.claude/skills/task-follow/SKILL.md) | 任务跟进（跟进 Agent 的 skill）。所有派发都配一个跟进会话 |
| [weekly-inventory](../.claude/skills/weekly-inventory/SKILL.md) | 周日事情盘点（GTD 每周回顾，你叫它「事情确认」）。当你说"周日盘点""事情确认""过一遍所有事情""每周回顾"、GTD 时使用 |

## Playbooks（`playbooks/`）

- [Agent 区 `v0`](agents.md) — Agent 区：角色化 agent 的定义方式、各工具怎么注入角色、派发时怎么用
- [定时任务：安装与排错 `v1`](automation.md) — 定时任务（早间建议、夜间巡检）：Orca 自动化为主、Windows 任务计划程序备份，原理、去重、补跑、安装、停用与排错
- [会话间通信 `v0`](comms.md) — 会话间通信：派出去的会话只在完成 / 要拍板 / 卡住 / 交接时回报主会话——先记待处理清单 runs/pending.md，再用 Orc…
- [每日进展整理（已迁移）](daily-update.md) — 已迁移为 skill daily-update · 已迁移为 skill
- [新设备上手 `v0`](device-setup.md) — 换到新设备时：克隆、装依赖、自动登记本地路径、private 不同步
- [采集数据并入 journal `v0`](inbox-merge.md) — 把 collect.py 采集的客观数据并入 journal 的步骤
- [记忆体系 `v0`](memory.md) — 记忆体系规范：分层、时效属性、写入时机、渐进式检索、巩固
- [Orca 能力清单 `v1`](orca.md) — Orca（欧卡）能力清单：自带能力（内置浏览器、computer use、Artifacts、编排）怎么选、边界、兜底、留证，以及工作区 /…
- [偏好问卷 `v0`](profile-questionnaire.md) — 偏好问卷的使用、并入流程和题库维护规则
- [项目建档 `v0`](project-onboarding.md) — 新工作面纳入 tracked 后的建档步骤（GitHub 核实、档案、索引）
- [工作面、仓库与采集 `v1`](registry.md) — 工作面注册表三态、仓库与本地路径模型、采集节奏、项目档案仓库一节、派发到项目干活的步骤
- [周回顾 `v0`](weekly-review.md) — 周回顾报告的步骤；周日核心流程见 weekly-inventory skill

## 模板

按记录类型找模板：[templates/INDEX.md](../templates/INDEX.md)（写任何记录之前先查）。
