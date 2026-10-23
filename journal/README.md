---
type: playbook
slug: journal-readme
status: active
updated: 2026-10-23
tags: [journal]
summary: "journal 目录说明：按日期的经历记录，只追加；这里是空的，第一次对话后由助手创建"
---

# journal（经历记录）

这个目录现在是空的，第一次和助手对话后，助手会按 `journal/YYYY/MM/YYYY-MM-DD.md` 创建当天的文件。

- **只追加**，不回头改旧条目；省略主语
- 条目格式：`## HH:MM 标题` → 标签行（`#project/<slug>` 或 `#area/<slug>`）→ 正文 → 「结果/产出」「待办」
- 模板见 [templates/journal.md](../templates/journal.md)
