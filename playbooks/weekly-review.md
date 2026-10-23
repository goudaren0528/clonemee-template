---
type: playbook
slug: weekly-review
status: active
updated: 2026-10-19
tags: [meta]
summary: "周回顾报告的步骤；周日核心流程见 weekly-inventory skill"
---

# 周回顾 `v0`

## 触发
**默认每周日**（可改成你习惯的一天）由你发起，或者你说「做周报 / 周回顾」。回顾本周（ISO 周，周一到周日）。

> 周日的核心是「事情盘点」仪式，按 skill [`weekly-inventory`](../.claude/skills/weekly-inventory/SKILL.md) 执行：先回顾本周，再把所有未结事项过一遍，最后排下周。下面是周回顾报告的部分。

## 步骤
1. `python tools/collect.py --since <本周一> --write`，按 [inbox-merge](inbox-merge.md) 把缺的 journal 补齐。
2. 读上周全部 journal，以及有更新的 project / area 档案。
3. 用 [`templates/review.md`](../templates/review.md) 生成 `reviews/YYYY-Www.md`：
   - 「数据」来自采集结果
   - 「主要推进」按工作面写，每条附 journal 或 PR 链接
   - 「卡点」「反思」只写我说过的；没说过的列成问题问我
4. 顺带做状态维护：
   - 根据本周 journal 直接更新 `profile/now.md`，不用问我（模板默认，不想自动更新可以改成先问）
   - `todo.md` 里已完成的挪进本周 journal，再从 todo 删掉
   - 生活、财务、学习这周有没有该记的，最多问 3 个（健康默认不主动问，你可以改）
   - 下周 7 天内的重要日期、订阅续费，列出来提醒
5. **计划复盘**：`python tools/plan.py`，每个 active 计划过一遍（进度、受阻、要不要调整），按 [plan skill](../.claude/skills/plan/SKILL.md) 生成下周的 `plans/weekly/YYYY-Www.md`（3–5 个焦点）。
6. **记忆巩固**：按 [memory](memory.md) §5 处理过期的会话记忆和到期的记忆卡（`python tools/recall.py --due`）。
7. 结果给我过目，确认后在 journal 记一条 `#meta`。
