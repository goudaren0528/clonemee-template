---
type: playbook
slug: memory
status: active
updated: 2026-10-19
tags: [meta, memory]
summary: "记忆体系规范：分层、时效属性、写入时机、渐进式检索、巩固"
---

# 记忆体系 `v0`

> 目标：助手跨会话、跨设备记得你和我们聊过的事；**每次会话都留记录**；**按需检索**，不把所有东西塞进上下文。
> 全部是仓库里的 Markdown，随 git 同步到每台设备。Claude Code 自带的记忆目录在本机用户目录下、不跨设备，所以不用。

## 1. 分层

| 层 | 存什么 | 在哪 | 时效 |
|---|---|---|---|
| 工作记忆 | 当前对话 | 模型上下文 | 会话内 |
| **短期记忆** | 每次会话的摘要：聊了什么、结论、你的状态信号、未结事项、我的承诺 | `memory/sessions/` | 默认 30 天过期，巩固后归档 |
| 长期 · 情景 | 你的经历（工作和生活） | `journal/` | 永久，只追加 |
| 长期 · 语义 | 关于你和你的事的稳定事实 | `profile/` `projects/` `areas/` `people/` `decisions/` `memory/glossary.md` | 持续改写，带 `updated` |
| **长期 · 记忆卡** | 放不进上面任何地方的跨会话知识 | `memory/long/` | 到 `review_after` 复核 |
| 程序性 | 怎么做事 | `CLAUDE.md` `playbooks/` `templates/` `profile/preferences.md` | 规范演进 |

**放哪的判断**：先找语义层有没有合适的家（偏好 → preferences，项目事实 → 项目档案，术语 → glossary）。都不合适才写记忆卡。会话记忆只做摘要和指针，不复述已写进别处的内容。

### 记忆卡的四种类型
| type | 用途 | 例子 |
|---|---|---|
| `insight` | 对你、对我们协作方式的观察 | （示例）习惯用 HTML 问卷批量回答 |
| `lesson` | 我踩过的坑、验证过的做法 | 某工具在 Windows 下路径要用正斜杠 |
| `thread` | 跨会话没结的话题、等你回答的问题 | private 跨设备同步方案没定 |
| `context` | 背景知识，又不属于某个项目 | 某个外部系统的来龙去脉 |

你要做的事进 `todo.md`，不进 thread；thread 是「我们之间悬而未决的」。

## 2. 时效属性

**会话记忆**（[模板](../templates/session.md)）：`started` `updated` `expires`（默认 +30 天）`status`（active → consolidated → archived）`importance`（1–3）`summary`。

**记忆卡**（[模板](../templates/memory-card.md)）：
- `confidence`：`stated` 你亲口说的 · `observed` 我验证过的事实 · `inferred` 我的推断
- `review_after`：stated / observed 180 天，inferred 60 天，thread 14 天
- `expires`：可选，限时有效的事到期即失效
- `importance`：1–3，决定 INDEX 里的排序
- `status`：active · superseded（被新卡取代，填 `superseded_by`）· archived
- `source`：必须指回原始出处（会话记忆、journal、问卷题号）

推断类记忆在对话里用到时，要说明是推断；被你确认后改为 `stated` 并延长复核期。

## 3. 写入时机

1. **每次会话都有一份会话记忆**：`memory/sessions/<日期>-<会话ID前8位>.md`。
   - 第一轮有实质内容就创建；之后每轮有实质进展就更新 `updated`、要点、未结
   - 会话 ID 在 SessionStart hook 注入的上下文里；Stop hook 会检查，没有就拦一次
   - 闲聊也要建，内容可以很短
2. **能沉淀的当场沉淀**，不等巩固：偏好、项目事实、待办按 CLAUDE.md §4 / §5 直接写进对应文件；会话记忆里只留指针。
3. **写记忆卡前先检索**（`python tools/recall.py <关键词> --scope memory`），有相近的就更新，不重复建。编号用 `python tools/recall.py --next-id`。
4. 写完跑 `python tools/build_index.py`（包含记忆索引；会话启动时也会自动跑）。
5. 健康细节不写进会话记忆和记忆卡，只写「已记到 private/health」。

## 4. 检索：渐进式披露

| 级别 | 内容 | 何时 |
|---|---|---|
| **L0** | `memory/INDEX.md`（一行一条）+ 设备、断档、待处理状态 | 每次会话开始，由 hook 自动注入 |
| **L1** | `python tools/recall.py <关键词>`：命中文件的标题、日期、摘要、时效标记，以及命中的小节 | 话题涉及过去的事时 |
| **L2** | 读原文（只读需要的文件和小节） | L1 确认相关之后 |

**主动检索的触发点**：
- 你提到人名、项目、术语、「上次 / 之前 / 那个」
- 给建议或做判断之前
- 写新记忆卡之前（去重）
- 碰到不认识的简称：先查 glossary

用到记忆时简短说明出处，例如「（据 10-19 会话）」。L1 列表里标了「已过期 / 待复核」的，引用前先确认还成立。

## 5. 巩固（定期整理）

**周日晚上周回顾时**做一次；INDEX 的短期记忆超过 15 条时也要做。

1. `python tools/recall.py --due`，列出过期的会话和到期的记忆卡
2. **会话**：检查未结和承诺，仍有效的转成 thread 卡或 todo；仍有价值的结论写进语义层。然后 `status: consolidated`，移到 `memory/archive/YYYY-MM/`
3. **记忆卡**：复核是否仍成立。成立就延长 `review_after`，不成立改为 superseded 或 archived；重要的推断类找机会问你确认
4. `python tools/build_index.py`，并在 journal 记一条 `#meta`

归档不删除。`recall.py --archive` 还能搜到，git 历史也都在。

## 6. 预算

- INDEX 控制在 80 行以内：短期最多列 15 条，长期卡太多就合并或归档低重要度的
- 会话记忆一般不超过 40 行；记忆卡一般不超过 30 行
