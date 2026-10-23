---
name: <kebab-case，和目录名一致>
description: <一句话做什么。当你说"…""…"时使用：<关键步骤概括>。描述决定 Claude Code 什么时候自动调用，触发词要写全>
---

# <skill 名> `v1`

<一两句：这个 skill 解决什么问题；交互还是无人值守。>

## 1. 取数 / 前置
- <要读的文件、要跑的命令（只读）>

## 2. 步骤
1. <动作>
2.

## 3. 写入
| 内容 | 写到哪 | 模板 |
|---|---|---|
| | | 见 templates/INDEX.md |

## 4. 回复 / 收尾
- 回复格式：<先结论……>
- 收尾：`python tools/build_index.py`；journal 记一条；更新会话记忆；提交

## 禁止
- <无人值守时尤其要写清>

<!-- 放在 .claude/skills/<name>/SKILL.md；改动记进 playbooks/README.md 的变更记录 -->
