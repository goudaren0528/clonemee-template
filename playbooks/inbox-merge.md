---
type: playbook
slug: inbox-merge
status: active
updated: 2026-10-19
tags: [meta]
summary: "把 collect.py 采集的客观数据并入 journal 的步骤"
---

# 采集数据并入 journal `v0`

## 触发
`python tools/collect.py --days N --write` 生成了 `inbox/YYYY-MM-DD-collect.md`。

## 步骤
1. 助手读草稿，按 **工作面 × 日期** 归并成若干条概述，每条一两句、写具体主题（引用 issue 号、PR 标题），不逐条抄提交。
2. 把概述发给我，问三件事：
   - 哪些值得记、哪些跳过
   - 每条背后的主观上下文：为什么做、卡在哪、花了多少精力
   - 有没有不在代码里的工作（会议、沟通、方案、运营）
3. 按我的回答写 journal：
   - **补记过去的日子**：写进对应日期的文件，标题后加 `(补记 YYYY-MM-DD)`，标明写入时间
   - 每条打 `#project/<slug>`，并在对应项目档案的时间线追加一行
   - 口吻：省略主语，只写事（模板默认，可改）
4. 有决策苗头的提议建 decision；项目当前状态有变化的更新档案「当前状态」。
5. 并完后删除 inbox 草稿（它不进 git，客观数据随时可以重新采集）。

## 注意
- 草稿是客观数据，**不能**不经我确认直接搬进 journal。
- 我没补上下文的条目，只写客观事实，后面标 `?`。
