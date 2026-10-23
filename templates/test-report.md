---
type: test-report
slug: <需求 slug>-r<轮次>
status: done
updated: YYYY-MM-DD
verdict: <通过 | 要修>
summary: "<一句话：多少条用例，过了多少>"
---

# 测试报告：第 <N> 轮

<!-- accept-test skill 产出，放在项目仓库 docs/delivery/<slug>/acceptance/report-r<N>.md，截图放 acceptance/shots/。按 cases.md 逐条执行，一条一行，失败的写清复现步骤。 -->

**结论**：<通过 / 失败 N 条，其中 P0 N 条>

- 测试对象：分支 <req/...>，提交 <sha>
- 环境：<启动命令、地址>

## 结果
| 用例 | AC | 类型 | 结果 | 证据 | 说明 |
|---|---|---|---|---|---|
| C-1 | AC-1 | 浏览器 | 通过 | shots/C-1.png |  |

## 失败详情
<!-- 每条：用例、实际结果、期望、复现步骤、截图或日志 -->

## 自动化
<!-- 写进仓库的 e2e 脚本（路径）和运行命令、结果 -->
