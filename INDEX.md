# INDEX — 个人助手总地图

> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。
> **渐进式披露**：L0 = CLAUDE.md + 会话启动注入（状态 + [记忆索引](memory/INDEX.md)）；L1 = 本页 + 各目录 INDEX；L2 = 具体文件。按关键词找：`python tools/recall.py 关键词`。

**生成于** 2026-10-23

## 目录地图

| 目录 | 放什么 | 数量 | 索引 |
|---|---|---:|---|
| `profile/` | 关于我：身份、偏好、当前状态 | 4 | [INDEX](profile/INDEX.md) |
| `plans/` | **核心**：长期目标、计划、周计划、当日计划、以后再说 | 0 | [INDEX](plans/INDEX.md) |
| `projects/` | 工作面档案：现状、仓库、待办、时间线 | 1 | [INDEX](projects/INDEX.md) |
| `areas/` | 持续性领域：生活、财务、学习（健康和财务明细在 private/） | 1 | [INDEX](areas/INDEX.md) |
| `journal/` | 经历，按日期，只追加 | 0 | — |
| `memory/` | 对话记忆：会话记忆（短期）、记忆卡（长期）、术语表 | 1 | [INDEX](memory/INDEX.md) |
| `decisions/` | 有取舍的决定及理由 | 0 | [INDEX](decisions/INDEX.md) |
| `reviews/` | 夜间巡检、事情盘点、进展汇报、周报月报 | 0 | [INDEX](reviews/INDEX.md) |
| `people/` | 协作者 | 0 | [INDEX](people/INDEX.md) |
| `meetings/` | 会议纪要 | 0 | [INDEX](meetings/INDEX.md) |
| `playbooks/ · .claude/skills/ · templates/` | 规范：skill、流程、模板 | 19 个 skill | [INDEX](playbooks/INDEX.md) |
| `agents/` | Agent 区：角色化 agent（产品、规划、开发、验收、跟进、调研、排查），派发时注入 | 7 | [INDEX](agents/INDEX.md) |
| `runs/` | 派发记录：每次把任务派给 agent 一份（任务说明、监督、验收）；待处理清单 pending.md（派出去的会话回报主会话的消息） | 0 | — |
| `tools/` | 脚本：采集、检索、索引、定时任务、环境识别、派发 | 40 | [INDEX](tools/INDEX.md) |
| `registry/` | 工作面注册表（GitHub 仓库）与各设备的本地路径登记 | 1 | — |

## 现在

- 状态快照：[profile/now.md](profile/now.md) · 偏好：[profile/preferences.md](profile/preferences.md) · 零散待办：[todo.md](todo.md)
- 当日计划：今天还没有
- 最近 journal：—
- GTD：还没做过 GTD 事情盘点；第一次用可以在周日发起一次（weekly-inventory skill）
- 没有未完成的计划任务

## 进行中的项目（1）

- [个人助手仓库（本仓库）](projects/assistant/index.md) — 助手仓库本身：CLAUDE.md、skill、工具、模板的改进记在这里（模板自带

## 进行中的计划

_暂无。_

## 最近决策

_暂无。_

## 外部系统

| 系统 | 管什么 | 怎么访问 |
|---|---|---|
| GitHub | 代码与 PR | `gh` CLI（账号见 registry/projects.yaml 的 `identity.github`） |
| 项目 / 团队知识库（可选） | 代码级记录、团队公共知识 | 按你用的工具填，例如团队 wiki、知识库 |
| 工单 / 消息（可选） | 团队工单 | 按你用的工具填，例如邮件、工单系统 |
