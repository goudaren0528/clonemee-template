# cloneme —— 个人助手模板

一套用 **Markdown + 一组 Python 脚本 + Claude Code skill** 长期维护的「个人助手」仓库：助手记住你的偏好、项目和计划，把具体工作派发给 Claude Code / Codex / OpenCode 等 agent 去做，你只负责确认和验收。

这份仓库是脱敏后的模板：**规范、skill、工具都是完整可用的**，个人数据被清空成了示例和占位符。按下面「怎么开始」改成你自己的就能用。

- 仓库名为占位：`<your-github>/cloneme`，建议保持 **private**
- 默认用中文，称呼、语气、口头习惯都在 `profile/` 里改

## 这是什么

| 部分 | 作用 |
|---|---|
| `CLAUDE.md` | 核心规则（L0），每次会话自动加载 |
| `profile/` | 你是谁、偏好、当前状态（模板，需要你填） |
| `registry/projects.yaml` | 工作面注册表：你要跟踪哪些 GitHub 仓库 |
| `plans/` `projects/` `areas/` `journal/` `todo.md` | 计划、项目档案、领域、经历、待办 |
| `decisions/` `meetings/` `people/` `reviews/` `runs/` | 决策、会议、人物、回顾、派发记录 |
| `memory/` | 会话记忆、长期记忆卡、术语表 |
| `private/` | 健康、财务等敏感内容（可选，放进 git 时仓库必须 private） |
| `agents/` | 7 个角色：产品、规划、开发、验收、跟进、调研、排查 |
| `.claude/skills/` | 19 个 skill：首次引导、计划、派发、跟进、早间建议、夜间巡检、清理…… |
| `playbooks/` | 低频流程说明：通信、Orca、记忆、注册表、设备 |
| `templates/` | 各类记录的模板（`templates/catalog.yaml` 是总表） |
| `tools/` | 40 个脚本：索引、检索、派发、跟进、巡检、清理、设备识别 |
| `tests/` | 单元测试（Python + Node） |

## 首次使用（5 分钟）

第一次打开时跟着三步走：先答问卷把底子填好，再录几件待办，助手第二天就能干活。

1. **打开问卷**：浏览器打开 [`templates/onboarding-questionnaire.html`](templates/onboarding-questionnaire.html)（双击就行，单文件、不联网）。
   20 题，分四组：我是谁 / 我在做什么 / 我怎么工作 / 我的生活与健康；每题都有推荐答案和理由，没意见的点「其余按推荐填」，不想答的点「暂不回答」。
2. **导出、交给助手**：点「导出回答」复制，在仓库目录开一个 Claude Code 会话（见下面「怎么开始」第 4 步），把内容粘进去说「按 onboarding 引导处理这份问卷」。
   助手按 [onboarding skill](.claude/skills/onboarding/SKILL.md) 写进 `profile/about.md`、`preferences.md`、`now.md`，并问你想给助手什么名字和语气（改写 `profile/persona.md`，仓库里自带的是虚构示例），按需建 `projects/`、`areas/` 档案，拿不准的标「待确认」。
3. **录第一批待办**：接着说 3–5 件眼下要做的事，助手按 [todo 格式](templates/entries.md#todo) 写进 `todo.md`；今天就要做的会建当日计划，最后先跑 `python tools/build_index.py --check` 看有没有缺 frontmatter，再跑 `python tools/build_index.py` 更新索引。

- **可以跳过**：问卷整份不答也能用，助手会在对话里按组问；只想先跑起来，答「我是谁」「我怎么工作」两组就够。
- **填完之后**：`profile/` 从 `draft` 变 `active`，`INDEX.md` 里有你的项目和领域，`todo.md` 有几条待办。跟助手说一句「今天做什么」，它会按这些排今天的事。
- **隐私**：问卷和这些文件里**不要填真实密码、密钥、银行卡号、身份证号**；个人信息只存在你自己的本地仓库，所以仓库要保持 private。

## 怎么开始

### 1. 装依赖

- Python 3.10+：`pip install -r requirements.txt`（只有 `pyyaml`）
- Node.js（可选，只有控制台前端测试用）
- [Claude Code](https://claude.com/claude-code)（必须，仓库的 skill 和 hook 都跑在它上面）
- [Orca](https://orca.so)（可选，但派发、工作区、内置浏览器这些能力靠它；不在 Orca 里也能用本地终端派发）
- Git

### 2. 改 profile

先按 [playbooks/profile-questionnaire.md](playbooks/profile-questionnaire.md) 用问卷收集一遍，或者直接改：

- `profile/about.md`：身份、工作、生活、目标
- `profile/preferences.md`：沟通、记录、工作方式、工具习惯
- `profile/now.md`：当前状态（之后由助手随对话更新）
- `profile/persona.md`：助手的人设（虚构示例：名字、对内对外的说话风格；换成你自己的）

三个文件开头都写了「这份是空模板」，把示例换成你的内容，再改 frontmatter 的 `status: draft → active`。

### 3. 填注册表

`registry/projects.yaml`：

- `identity`：称呼、GitHub 账号、组织、哪些 git author 算你
- `tracked`：你要跟踪的工作面（一个项目可以对应多个仓库），格式看文件里的注释示例
- 铁律：`tracked` 只由你确认，工具不会自动加。之后可以用 `python tools/discover.py` 扫候选
- 设备登记（`registry/devices/<指纹>.yaml`）由 `tools/device.py` 在本机自动生成，记录了真实用户名和本地路径，**不进 git**（`.gitignore` 已排除），换设备各生成各的

### 4. 打开它

在本仓库目录开一个 Claude Code 会话：

```bash
cd <本仓库路径>
claude
```

`.claude/settings.json` 里已经配好四个 hook（会话启动注入上下文、Stop 时检查会话记忆、提问时补上下文、写操作前挡越界），不需要额外安装。

### 5. 可选：定时任务

- 早间建议（周一到周六 10:00）、夜间巡检（每天 22:00）
- Windows 任务计划程序：`powershell -NoProfile -ExecutionPolicy Bypass -File tools/agent-install.ps1`
- Orca 自动化：见 [playbooks/automation.md](playbooks/automation.md)

### 6. 填完之后

跟我说「确认今天要做的事」，或者直接开始聊。助手会根据 `profile/` 和 `registry/` 给你建议。

## skill 清单

| skill | 什么时候用 |
|---|---|
| `onboarding` | 首次使用：问卷初始化 + 录第一批待办 |
| `plan` | 讨论目标、整理计划或待办 |
| `dispatch` | 派发任务给 agent（先出方案，你确认后才派） |
| `task-follow` | 跟进 Agent：盯执行会话、按授权决策、回报主会话 |
| `daily-update` | 整理今天/昨天的进展 |
| `progress-report` | 汇报进展（金字塔结构 + 表格） |
| `report-cards` | 要你拍板 / 验收 / 查看 / 回复的事，一件一张卡片 |
| `morning-brief` | 早间建议与今日事项确认 |
| `nightly-review` | 夜间巡检 |
| `weekly-inventory` | 周日 GTD 事情盘点 |
| `cleanup` | 清理做完的会话和 Orca 工作区 |
| `pm-clarify` / `pm-prd` | 产品 Agent：问卷澄清需求、出 PRD 和原型 |
| `dev-plan` / `dev-build` / `dev-fix` | 开发 Agent：实现计划、写代码自测、按问题修复 |
| `accept-cases` / `accept-test` / `accept-review` | 验收 Agent：设计用例、集成测试、两路审查 |

完整说明见 [playbooks/INDEX.md](playbooks/INDEX.md) 和各 skill 的 `SKILL.md`。

## 工具清单

| 类别 | 工具 |
|---|---|
| 索引与检索 | `build_index.py`（生成 INDEX）、`recall.py`（关键词检索记忆）、`plan.py`、`inventory.py`、`board.py`（台账扫描） |
| 派发与跟进 | `dispatch.py`、`follow.py`、`delivery.py`（开发交付流水线）、`notify.py`（回报 / 待处理清单）、`handoff.py`、`sweep.py` |
| 环境与设备 | `env.py`（识别 Orca / 终端 / 可用 agent）、`device.py`（本设备路径登记）、`clone.py`（克隆缺失仓库） |
| 采集与巡检 | `collect.py`、`discover.py`、`nightly.py`、`experience.py`、`schedule.py`（固定日程） |
| 清理 | `cleanup.py` |
| 控制台 | `console.py`、`console_channel.py`、`console_render.py`、`console_data.py`（本地指挥台页面） |
| 问卷与文档 | `qform.py`、`questionnaire.html`、`prd_html.py` |
| 安全 | `guard_hook.py`（PreToolUse 拦截越界写操作）、`push_guard.py`（推送体积 / 密钥闸）、`install_hooks.py`（装 pre-push 钩子） |
| 通知 | `feishu_notify.py`（可选：把 issue / PR / 合并发到飞书群） |
| 定时任务 | `agent-install.ps1`、`agent-run.ps1`、`start.ps1` |

全部用法见 [tools/INDEX.md](tools/INDEX.md)（从各文件开头的说明生成）。

## 哪些是示例

以下内容是**示例或占位符**，换成你自己的：

- `profile/` 下四个文件：保留结构，示例条目删掉
- `registry/projects.yaml`：`identity` 和 `tracked` 都是占位
- `memory/glossary.md`：术语表示例
- `projects/assistant/index.md`：助手仓库自己的示例档案（可以改名或删掉）
- `tools/questionnaire.html`：自带一份通用偏好问卷，可以照它改题
- `templates/onboarding-questionnaire.html`：首次使用问卷（示例，可以复制后改题、改推荐答案）
- 文档里的 `<your-github>`、`<your-org>`、`<仓库绝对路径>`、`example-project`、`example-repo` 都是占位符
- `agents/*.md` 的「修订记录」只保留了一句模板说明

## 安全与隐私

- 仓库里有你不想公开的内容（`private/`、健康、财务、同事姓名）时，**仓库必须保持 private**
- 不把 webhook、token、密码写进任何文件：`private/feishu-webhook.md` 已经被删除，用环境变量或你自己本地的未提交文件
- `tools/push_guard.py` 会在推送前拦累计超过 10MB / 单个超过 1MB / 带密钥特征的内容，装一次：`python tools/install_hooks.py`
- 派出去的 agent 默认跳过权限确认，所以任务说明里要写清约束（不推送、不合并、不发布、删除前先问）

## 开发与测试

```bash
python tools/build_index.py            # 重新生成所有 INDEX
python tools/build_index.py --check    # 检查模板覆盖和 frontmatter
python tools/env.py                    # 看运行环境
python -B -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.cjs
```

（`test_console_integration` 的 gitignore 用例需要仓库已经 `git init`。）

## 来源

从一份个人助手仓库脱敏而来：删除了个人数据目录（journal、plans、projects、areas、decisions、meetings、people、reviews、runs、memory/sessions、memory/long、private、inbox、todo 的旧内容），把身份、路径、账号、设备、项目名换成了占位符，规范、skill、工具、模板和测试保留。原仓库的历史不随这份模板保留。

## 已知限制

- **外发总开关（`ASSISTANT_NO_SEND=1`）只防测试和演示误发，不是安全边界。** 它拦的是仓库自带工具的常规出口（飞书通知、`orca terminal send` / `orca agent launch`、Windows 通知、`gh_tasks.py` 的写入），不保证拦住所有写法：
  - `tools/gh_tasks.py` 判断 gh 写入时只认分开的参数（`-X POST`、`-f key=value`、`--input`），`--method=POST`、`-XPOST`、`-ftitle=…`、`--input=-` 这类等号 / 紧凑写法不会被识别；
  - `tools/dispatch.py` 的本地派发 / 本地跟进（不经 Orca）直接起子进程，不经过总开关。
- 需要真正的隔离时，请在没有凭据的环境里跑（不配 webhook、不登录 `gh`、不开 Orca），而不是依赖这个开关。
