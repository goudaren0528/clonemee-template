---
name: onboarding
description: 首次使用个人助手模板时的引导。当你说"第一次用""开始用这个仓库""帮我初始化""引导我填一下""onboarding"时使用：先用一份单文件 HTML 问卷（15–20 题，四组：我是谁 / 我在做什么 / 我怎么工作 / 我的生活与健康，每题带推荐答案和理由，可稍后回答、可导出）收集初始化信息，再按回答写进 profile/、projects/、areas/，最后引导你说 3–5 件眼下要做的事写进 todo.md，跑一遍索引。
---

# 首次使用引导 `v0`

别人把这个仓库当自己的助手仓库用，第一次打开时最常问的是「我现在该干什么？」。这个 skill 就是那条引导：**一份问卷把底子填好，再收三五个待办，让助手第二天就能干活**。

- 交互式：每一步都等用户回答，不替他编。
- 只写 `profile/`（含助手人设 `persona.md`）、`projects/`、`areas/`、`todo.md`、`plans/`；**不动** `CLAUDE.md`、skill、工具、注册表的 `tracked`（这两类要用户明确确认，见下面的「拿不准」）。

## 1. 取数 / 前置
- 读 [README.md](../../../README.md)「首次使用（5 分钟）」和 [profile/about.md](../../../profile/about.md)、[preferences.md](../../../profile/preferences.md)、[now.md](../../../profile/now.md) 的现状（都是 `status: draft` 说明还没填过）
- 已经填过（`status: active` 且有真实内容）就别整套重跑：问一句「是补缺，还是重新来一遍」，补缺只问缺的组
- 需要写记录时按 [templates/INDEX.md](../../../templates/INDEX.md) 找模板，按模板写

## 2. 步骤

### A. 初始化问卷
1. 告诉用户：「先答一份问卷，5 分钟左右，不想答的题可以点『暂不回答』」，把问卷的**绝对地址**给他：
   `file:///<仓库绝对路径>/templates/onboarding-questionnaire.html`（`python tools/device.py show` 或当前目录都能拿到路径；Windows 写成 `file:///D:/...`）
2. 问卷内容：20 题，四组——
   | 组 | 题号 | 问什么 | 落在哪 |
   |---|---|---|---|
   | 我是谁 | w01–w05 | 称呼、身份职业、职责、城市时区、GitHub 账号 | `profile/about.md` |
   | 我在做什么 | d01–d05 | 1–3 个项目、仓库位置、半年目标、跟踪的领域、现在最卡的事 | `projects/` · `areas/` · `profile/about.md` · `todo.md` |
   | 我怎么工作 | k01–k06 | 工作时间、回复长度、拿不准时怎么做、哪些事必须先问、常用工具、记录力度 | `profile/preferences.md` |
   | 我的生活与健康 | l01–l04 | 作息、运动、饮食、要提醒的事 | `preferences.md` · `areas/life/` · `areas/work/`（固定日程） |
   - 每题都有推荐答案和理由；用户点「其余按推荐填」的题，写进去时标「（按推荐）」，那不算他本人的结论
   - 用户不想开浏览器，就在对话里按组问，一次不超过 5 题，同样给推荐和理由
3. 等用户把导出的 Markdown 粘回来（或直接口述）。**不要**替他勾选、不要猜没答的题。
4. 导出里有密码、密钥、银行卡号、身份证号这类内容：**先让他删掉再发**，已经写进文件的要立刻删掉并提醒。

### B. 按回答写入
按题号并入，一条一个来源（`问卷 w01`、`问卷 k04（按推荐）`）：
1. `profile/about.md`：身份（称呼、GitHub）、工作（职位、职责）、生活（城市/时区）——用文件里已有的小节，删掉示例，填完把 frontmatter 改成 `status: active`（用户确认过再改）
2. `profile/preferences.md`：沟通、记录与信息管理、工作方式、工具、作息与节奏几节，按 [entries.md#偏好](../../../templates/entries.md#偏好) 的格式追加
3. `profile/now.md`：「工作重点」写项目和半年目标，「近期在意的事」写最卡的那件事；日期写今天
4. `projects/<slug>/index.md`：d01 的每个项目建一份（按 [templates/project.md](../../../templates/project.md)），d02 的仓库写进「仓库」一节；**已经存在的档案只补不覆盖**
5. `areas/<slug>/index.md`：d04 勾的领域各建一份（按 [templates/area.md](../../../templates/area.md)）；「健康」的明细放 `private/health/`，只写用户说了的
6. d03 的半年目标写进 `profile/about.md`「工作 · 半年目标」；他想把它拆成计划时，再按 [plan skill](../plan/SKILL.md) 建 `plans/goals.md` 和对应计划（没有模板文件，别凭空造一份）
7. 固定日程（l04 勾了「固定日程 / 会议」且说了时间）：写进 `areas/work/index.md` 的「固定日程」表，格式让 `python tools/schedule.py` 能读
8. `profile/persona.md`：仓库自带的是**虚构示例人设**（小橙），不是用户的设定。问用户：「想给助手起什么名字？怎么称呼你？对内、对外各用什么语气？」按回答改写名字、自称、署名和两种语气，**把文件开头「这是虚构示例」的说明和示例名删掉**，`status` 改 `active`；用户不想设就保留示例，并告诉他随时可以改。助手的名字只写在这份文件里，不要改 CLAUDE.md
9. **拿不准的**：字段残缺、说法有歧义、互相矛盾，先按最合理的写法写进去并标「待确认」（frontmatter 保持 `draft`），在回复里列出来让他一句话补齐；不要空着不问，也不要编

### C. 录入第一批待办
1. 问：「眼下 3–5 件要做的事是什么？一句话一件，有截止日期就说一下。」（用户已经说了的就不再问）
2. 写进 `todo.md`，格式见 [entries.md#todo](../../../templates/entries.md#todo)：`- [ ] 事项 · 截止日期（可选） · 来源`
   - 需要拆解、需要验收标准的，不放 todo，按 [plan skill](../plan/SKILL.md) 建计划
   - 有今天就要做的，按 [templates/daily-plan.md](../../../templates/daily-plan.md) 建 `plans/daily/<今天>.md`，把这几件事放进去
3. 最后先查再生成：`python tools/build_index.py --check`（有问题会逐条列出来，按提示补 frontmatter；有问题时退出码是 1），再 `python tools/build_index.py`

## 3. 写入
| 内容 | 写到哪 | 模板 |
|---|---|---|
| 身份、工作、生活 | `profile/about.md` | 单例：文件自身结构 |
| 沟通、记录、工具、作息偏好 | `profile/preferences.md` | [entries.md#偏好](../../../templates/entries.md#偏好) |
| 当前状态 | `profile/now.md` | 单例：文件自身结构 |
| 项目 / 领域档案 | `projects/<slug>/index.md` · `areas/<slug>/index.md` | [project.md](../../../templates/project.md) · [area.md](../../../templates/area.md) |
| 半年目标 | `profile/about.md`「工作」 | 单例：文件自身结构 |
| 零散待办 | `todo.md` | [entries.md#todo](../../../templates/entries.md#todo) |
| 今天要做 | `plans/daily/YYYY-MM-DD.md` | [daily-plan.md](../../../templates/daily-plan.md) |
| 这次引导做了什么 | `journal/YYYY/MM/YYYY-MM-DD.md` 一条 `#meta` | [entries.md#journal](../../../templates/entries.md#journal) |

## 4. 回复 / 收尾
- 回复格式：先结论（「底子填好了，今天可以开始」），再用一张小表报**写了哪些文件、几条待办**，「待确认」的单独列出来；最后给一句下一步（「回你一句『今天做什么』，我给你排今天的」）
- 收尾：`python tools/build_index.py`；journal 记一条；更新会话记忆；本地提交（中文 message）。推送按仓库规矩来，用户没说就不推
- **隐私**：问卷、profile、journal 里都不写真实密码、密钥、银行卡号；提醒用户这个仓库要保持 private，个人信息只存在本地仓库

## 禁止
- 替用户回答问卷、把「按推荐」当成他的结论、追问已经点「暂不回答」的题
- 把真实密码、密钥、银行卡号、身份证号写进任何文件，或留在导出的回答里
- 未经确认改 `registry/projects.yaml` 的 `tracked`、改 `CLAUDE.md`、改 skill / 工具 / 模板
- 一次问超过 5 个问题；把对话变成连环追问
- 在别人的仓库里执行（这个 skill 只在用户自己的助手仓库里用）

<!-- 放在 .claude/skills/onboarding/SKILL.md；改动记进 playbooks/README.md 的变更记录 -->
