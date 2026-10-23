---
type: project
slug: assistant
status: active
started: 2026-10-22
updated: 2026-10-22
tags: [project, meta]
summary: "助手仓库本身：CLAUDE.md、skill、工具、模板的改进记在这里（模板自带的一份示例档案）"
---

# 个人助手仓库（本仓库）

> 这是一份**示例档案**：说明「助手本身也是一个项目」。你自己的助手仓库可以直接用这份，也可以改名或删掉。

## 一句话

用 Markdown + 一组脚本 + Claude Code skill 维护的个人助手：记录你的偏好、项目、计划和经历，并把具体工作派发给 agent 执行。

## 现状

- 规范：`CLAUDE.md`（核心规则）、`.claude/skills/`、`playbooks/`、`templates/`、`agents/`
- 工具：`tools/`（索引、检索、派发、跟进、清理、设备识别、定时任务）
- 数据：`profile/`、`projects/`、`plans/`、`journal/`、`memory/`、`private/`

## 仓库

| 仓库 | 可见性 | 说明 |
|---|---|---|
| `<your-github>/cloneme` | private | 这份模板；你的个人实例建议也用私有仓库 |

## 项目偏好

- 改进项先记「改进清单」，不在日常会话里当场改规范（挡路的 bug 除外）
- 改 skill、playbook、模板、`CLAUDE.md` 要在 [playbooks/README.md](../../playbooks/README.md) 的变更记录加一行

## 改进清单

| 日期 | 想法 | 状态 |
|---|---|---|
| — | （示例）把早间建议的定时任务接到自己的日历 | 待评估 |

## 时间线

- 2026-10-22 — 从个人实例脱敏成可共享的模板，仓库改名 cloneme
