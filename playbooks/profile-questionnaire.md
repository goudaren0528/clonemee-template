---
type: playbook
slug: profile-questionnaire
status: active
updated: 2026-10-19
tags: [meta]
summary: "偏好问卷的使用、并入流程和题库维护规则"
---

# 偏好问卷 `v0`

用 [`tools/questionnaire.html`](../tools/questionnaire.html) 分批收集我的基础偏好和待澄清的问题。

## 我怎么用
1. 浏览器打开 `tools/questionnaire.html`（用浏览器打开本仓库里的这一份）。回答存在浏览器本地（localStorage），关掉再开还在，可以分几次答。
2. 不想答的点「暂不回答」；需要多说几句的点「补充说明」。
3. 答完一批，点「导出回答」→「复制并标记为已导出」，粘贴到助手会话里。默认只导出上次导出之后新增或修改的回答。

## 助手怎么并入
1. 按导出里每个分区标的目标文件写入：
   - 沟通、记录、工作方式、工具 → `profile/preferences.md`，每条注明「问卷 [id]，日期」
   - 身份、职责、城市等稳定信息 → `profile/about.md`
   - 健康 → `private/health/index.md`
   - 生活 / 财务 / 学习 → 对应 `areas/<slug>/index.md`
   - 项目问题 → 对应 `projects/<slug>/index.md` 的「待确认」打勾或改写；涉及 tracked / ignored / paused 的，按我的回答改 `registry/projects.yaml`（这就是我的确认）
2. 回答影响助手行为的（例如回复长度、记录积极程度、提交时机），同步改 CLAUDE.md 对应条款，并在 [README](README.md) 的变更记录追加一行。
3. 「暂不回答」的题不追问，也不写进记录。
4. 当天 journal 记一条 `#meta`，列出并入了哪些题号。

## 题库怎么维护
- 题库在 html 的 `SECTIONS` 和 `Q` 两个数组里。
- 题号**不复用、不改含义**；改题干可以，改含义就换新题号。旧题加 `retired: true` 隐藏，已有回答保留。
- 日常对话里发现记录有缺口（比如 now.md 某块长期空着），就往对应分区追加新题，告诉我有新题可答。
- 已并入的题不用删。我在浏览器里能看到自己答过什么，新题会显示为「未答」。
