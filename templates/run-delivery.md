---
type: run
kind: delivery
slug: <YYYY-MM-DD>-<项目>-<需求>
status: dispatched         # dispatched → done / failed / abandoned
created: YYYY-MM-DD HH:MM
updated: YYYY-MM-DD
project: <项目 slug>
repo: <owner/repo>
req: <需求 slug>
dev_branch: develop
device: <派发它的设备指纹>
req_branch: req/<需求>
req_worktree: '<需求集成分支的本地路径>'
follower: '<跟进 Agent 的 Orca 终端 handle>'
report_to: '<回报给哪个主会话：start 时主会话的 Orca 终端 handle；不在 Orca 里留空>'
report_session: '<主会话的会话 ID 前 8 位>'
device: '<在哪台设备上跑：设备指纹（tools/device.py）；v1.1 起写，旧记录没有>'
summary: "<一句话：哪个需求、几个包>"
---

# [交付] <项目> · <需求>

<!-- 交付看板（需求级运行记录），tools/delivery.py 创建和更新，跟进 Agent 维护。工作包表的列名不要改。 -->

## 需求
- 回报：<跟进 Agent 在 完成 / 要拍板 / 卡住 时用 tools/notify.py send 回报这个主会话（playbooks/comms.md）>
- PRD：<项目仓库 docs/delivery/<需求>/prd.md>（来自 assistant <plans/prd/...>）
- 产物目录：docs/delivery/<需求>/

## 阶段
<!-- 规划 → 开发 → 集成测试 → 合回 develop → 完成 -->
- 当前：规划

## 工作包
| 包 | 状态 | 依赖 | 分支 | 工作区 | 会话 | 审查轮次 | 更新 |
|---|---|---|---|---|---|---|---|

## 会话
<!-- 每次启动会话加一行：- 时间 — 角色 / skill · 在哪 · handle -->

## 决策记录
<!-- 跟进 Agent 替你做的决定：- YYYY-MM-DD HH:MM — 问题 → 选择 → 理由（备选：…） -->

## 监督
<!-- 有变化时加一行 -->

## 体验
<!-- 合回 develop 后，验收 Agent 体验准备给出的体验卡（链接、体验要点、用例通过情况、关闭命令）；没有界面写「没有界面，不需要体验环境」。你验收后记一行结论并关掉环境 -->

## 验收
<!-- 结束时：逐条 AC 结论和证据、测试报告、合回 develop 的提交 -->
