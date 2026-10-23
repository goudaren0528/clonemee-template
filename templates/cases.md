---
type: cases
slug: <需求 slug>
status: draft              # draft（验收 Agent 写）→ approved（跟进 Agent 审过 AC 覆盖）
updated: YYYY-MM-DD
prd: prd.md
summary: "<一句话：多少条用例，覆盖哪些 AC>"
---

# 验收用例：<需求名>

<!-- accept-cases skill 产出，放在项目仓库 docs/delivery/<slug>/cases.md。只从 PRD 出发写，不看实现；先出用例，再测试。 -->

## 测试环境
<!-- 怎么启动（命令、端口、测试账号或种子数据）、用哪种方式测（项目自带的 e2e 框架 / Orca 内置浏览器 / 接口） -->

## 用例
<!-- 编号 C-1…；类型：浏览器 / e2e / 接口；优先级：P0 主流程、P1 边界和异常、P2 兼容。每条都要能对上 AC。 -->

### C-1 <用例名>
- AC：AC-1 · 类型：浏览器 · 优先级：P0
- 前置：<登录状态、数据>
- 步骤：
  1. <打开 …>
  2. <点击 / 填写 …>
- 预期：<看得到、可检查的结果>

## AC 覆盖
| AC | 用例 |
|---|---|

## 不测的
<!-- PRD 里明确不做的、或者测不了的（说明原因） -->
