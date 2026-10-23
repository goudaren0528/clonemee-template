# 工具索引

> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。
> 全部在本仓库 `tools/` 下，用法以各文件开头的说明为准。

**生成于** 2026-10-23

### `agent-install.ps1`
注册 / 移除 assistant 的定时任务（Windows 任务计划程序，当前用户，不需要管理员）。
```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1                 # 两个都装
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Disable       # 停用（任务留着当备份）；-Enable 恢复
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Remove        # 两个都卸
```

### `agent-run.ps1`
assistant 定时任务入口（Windows）：由 Orca 自动化（主）和任务计划程序（备份）调用，也可以手动运行。
```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job morning -Check   # Orca 的 precheck：0 = 该跑，1 = 已跑过 / 正在跑
```

### `board.py`
台账扫描 —— 今日计划「推进位与排队」三栏对未结任务，找出漏网的。
```
python tools/board.py                     # 表格：不在三栏 / 暂停没写复查日期 / 复查日期到了或过期
python tools/board.py --brief             # 一行摘要（sweep 末尾、开场卡片用；没问题时不输出）
python tools/board.py --json              # 机读（morning-brief、别的工具用）
python tools/board.py --date 2027-03-09   # 按某天的计划算（默认今天）
```

### `build_index.py`
索引生成器 —— 渐进式披露的 L1 层：根 INDEX.md（总地图）+ 各目录 INDEX.md，全部从文件的
```
python tools/build_index.py                     # 生成全部索引（会话启动 hook 也会自动跑）
python tools/build_index.py --backfill-summary  # 给缺 summary 的文档补上（从「一句话」或首段提取）
```

### `cleanup.py`
清理 —— 盘出可以清理的会话（Orca 终端）和工作区（Orca 工作区 / git worktree），你确认后关终端、删工作区、删已合并的分支（cleanup skill）。
```
python tools/cleanup.py                          # 清单：按项目列出可以清理的会话和工作区，有风险的单独列（只读）
python tools/cleanup.py --brief                  # 一行「可以清理：N 个会话、M 个工作区」（sweep 快照、开场卡片、夜间巡检用）
python tools/cleanup.py --for runs/<运行记录>.md   # 只看这个派发（运行记录或交付看板）的会话和工作区，按「已结束」算（跟进 Agent 收尾时用）
python tools/cleanup.py --run                    # 你确认后执行：清掉「可以清理」的全部
```

### `clone.py`
克隆缺失的仓库 —— 换设备时，tracked 里的项目本设备没有克隆，提醒你并在你说「克隆」后帮着克隆、登记。
```
python tools/clone.py --missing                       # 本设备缺哪些克隆，哪些有未完成的任务（优先）
python tools/clone.py <your-github>/example-repo          # 只看：建议克隆到哪
python tools/clone.py <your-github>/example-repo --run [--path D:/example-repo]   # 克隆 + 登记进 Orca + 登记进本设备路径表
```

### `collect.py`
客观工作数据采集器

### `common.py` （模块）
tools 共用的小工具：仓库根目录、命令执行、注册表读取、本地 git 仓库扫描。

### `console.py`
assistant 指挥台 P2 生产入口：只读数据根 → 渲染单文件 → 原子写出 inbox/console/index.html。
```
python -B tools/console.py                  # 默认数据根 = 工具仓库根（common.ROOT）
python -B tools/console.py --root <目录>     # 显式数据根必须是现存本地绝对目录
```

### `console_channel.py`
assistant 指挥台的本地通道（v1.1 P3）：只听 127.0.0.1 的一个小 HTTP 服务，给出能「发送给主会话」的指挥台页面，
```
python tools/console_channel.py ensure [--wait 1.5] [--quiet]   # 会话启动钩子和 start.ps1 调：已在跑就复用，没在跑就拉起
python tools/console_channel.py url                              # 「打开指挥台」：确保在跑并打印 http://127.0.0.1:<端口>/
python tools/console_channel.py stop                             # 「关掉指挥台」：只结束状态文件里记着、health 对得上的那个进程
python tools/console_channel.py status                           # 排查：状态文件、health、是不是这份 assistant
```

### `console_contract.py`
assistant 指挥台 P0 共享契约：快照 TypedDict、固定枚举、路径/文本/链接安全边界与快照校验。

### `console_data.py`
assistant 指挥台 P1 数据适配：只读白名单记录，投影成 P0 定义的 JSON 快照。

### `console_render.py`
assistant 指挥台 P2 离线渲染器：把快照、视觉规范、样式、脚本与固定形象内嵌成单文件 HTML。

### `delivery.py`
开发交付流水线 —— PRD 定稿、你说「确认派发」后，从规划到合回 develop 的派发和调度。
```
python tools/delivery.py status  <看板>                    # 全部任务线现状 + 可以派的包
python tools/delivery.py approve <看板>                    # 实现计划和用例审过：提交到需求分支，工作包读进看板
python tools/delivery.py pkg     <看板> P1                 # 开包：从需求分支切 pkg/<需求>-P1 工作区，启动开发（dev-build）
python tools/delivery.py launch  <看板> --at P1 --skill accept-review [--note "…"]   # 在已有工作区启动一个会话
```

### `device.py`
设备感知的本地路径登记 —— 换设备不写死路径。
```
python tools/device.py                   # 检查（会话启动 hook 也会自动跑）
python tools/device.py --rescan          # 强制重新扫描
python tools/device.py show              # 打印本设备登记情况
python tools/device.py confirm <路径>     # 确认某个待确认的克隆
```

### `discover.py`
工作面发现器
```
python tools/discover.py              # 报告未确认的候选
python tools/discover.py --all        # 连已确认/已忽略的一起列
python tools/discover.py --no-gh      # 只扫本地
```

### `dispatch.py`
任务派发 —— 把已确认的计划任务派给 agent 执行（dispatch skill 的执行部分）。
```
python tools/dispatch.py                          # 今天的当日计划里可派发的任务：只出方案，不执行
python tools/dispatch.py --task T2 --agent codex  # 指定任务和执行器（覆盖任务行的 exec:）
python tools/dispatch.py --task T2 --run          # 真正派发
python tools/dispatch.py --status                 # 看已派发的运行记录和 Orca 工作区状态
```

### `env.py`
运行环境识别 —— 判断当前会话跑在哪里、有哪些能力可用（Orca 里外都能识别）。
```
python tools/env.py          # 一行摘要
python tools/env.py --json   # 结构化结果（dispatch.py 等工具用）
```

### `experience.py`
体验环境 —— 有界面的交付在发「完成」之前，在本地把它启动好，你点链接就能体验。
```
python tools/experience.py start <名字> --url file:///C:/.../index.html     # 静态页面：不用启动，只检查文件在
python tools/experience.py list                  # 本机开着的体验环境（到期的标出来）
python tools/experience.py stop <名字>           # 你「验收通过」或「打回」后关掉
python tools/experience.py stop --expired        # 关掉所有过了到期时间的
```

### `feishu_notify.py`
飞书通知 —— agent 在本地发一张卡片到飞书群（做完 issue / PR / 合并 / 关闭 issue 之后）。

### `follow.py`
任务跟进 —— 跟进 Agent（agents/pm.md · task-follow skill）用的工具：看执行会话现在怎样，记进展和决定。
```
python tools/follow.py runs/<运行记录>.md                       # 快照：运行记录 + Orca 会话状态 + 终端当前一屏 + 改动和提交
python tools/follow.py runs/<运行记录>.md --log "看到了什么"      # 在「监督」加一行
python tools/follow.py runs/<运行记录>.md --decide "问题 → 选择 → 理由（备选：…）"   # 在「决策记录」加一行
python tools/follow.py runs/<运行记录>.md --send "给执行会话的话"  # 发给执行会话（只有 Orca 模式能发），并记进「监督」
```

### `gh_tasks.py`
项目任务放在 GitHub（milestone + issue）—— 项目偏好 `tasks: github` 时用。
```
python tools/gh_tasks.py status [--project example-project]   # milestone 进度、未关的 issue、还没建 issue 的任务
python tools/gh_tasks.py push plans/2026-01-01-example-plan.md [--task T1 ...] [--run]   # 建 milestone 和 issue，回填 issue:（先看方案）
python tools/gh_tasks.py pull [--run]                              # issue 关了 → 任务打勾、记进展（只读 GitHub，只写 assistant）
```

### `guard_hook.py`
PreToolUse hook：主会话越界执行的防线（CLAUDE.md §2：主会话碰项目仓库只做只读查询，执行一律派发）。

### `handoff.py`
交接包 —— 派发时把任务引用到的 assistant 文件（PRD、原型、文档）复制一份进工作区。
```
python tools/handoff.py plans/2026-01-01-example-plan.md T2     # 看这个任务会带哪些文件
```

### `install_hooks.py`
安装 pre-push 体积闸：把仓库的 pre-push 钩子装成调用 tools/push_guard.py 的 sh 脚本（Windows 上 git 自带的 sh 跑）。
```
python tools/install_hooks.py                 # 给 assistant 自己装（主目录和所有工作区共用）
python tools/install_hooks.py --repo <路径>   # 给别的仓库装
python tools/install_hooks.py --check         # 只检查装没装（0 = 装好了，1 = 没装）
```

### `inventory.py`
事情盘点 —— 从所有来源收集「未结的事」，去重后列成清单，一件不漏。
```
python tools/inventory.py              # 打印清单（按来源分组）
python tools/inventory.py --brief      # 一行计数（早间建议、会话启动用）
python tools/inventory.py --write      # 写成本周的盘点表 reviews/inventory/YYYY-Www.md（带决定列）
```

### `nightly.py`
夜间巡检 · 第一段：确定性采集（不调用模型）。
```
python tools/nightly.py                  # 今天
```

### `notify.py`
会话间通信 —— 派出去的会话回报 助手主会话：先记待处理清单 runs/pending.md，再推一行消息到主会话。
```
python tools/notify.py send … --no-push           # 只记清单不推送（主会话自己记一条要你拍板的事）
python tools/notify.py list [--all]                  # 待处理清单（--all 连已处理的一起列）
python tools/notify.py done N1020-01 --note "…"      # 主会话处理完：标为已处理
python tools/notify.py target                        # 现在发消息会推到哪个主会话（排查用）
```

### `plan.py`
计划任务视图 —— 解析 plans/ 下计划文档里的任务行（格式见 .claude/skills/plan/SKILL.md）。
```
python tools/plan.py              # 所有未完成任务：逾期 / 进行中 / 受阻 / 7 天内到期 / 其他
python tools/plan.py --owner me   # 只看某个负责人
python tools/plan.py --agent      # 只看派给 agent 执行的（exec 不是 me / assistant）
python tools/plan.py --brief      # 一行摘要（会话启动 hook 用）
```

### `prd_html.py`
PRD 评审页生成器 —— 把 PRD（Markdown）和原型（HTML）合成一个图文并茂、可以评论的评审页。

### `prompt_hook.py`
UserPromptSubmit hook：会话间通信的主会话一侧。

### `push_guard.py`
pre-push 体积闸：这次推送带出去的新对象累计超过 10MB、单个文件超过 1MB，或新增文本里有密钥特征，就拒绝推送。
```
python tools/push_guard.py --rev HEAD    # 手动检查：HEAD 里还没到任何远端的对象（同步任务推送前也可以先跑这个）
```

### `qform.py`
澄清问卷生成器 —— 把一份题目清单（YAML）生成可以直接在浏览器里回答的 HTML 问卷。
```
python tools/qform.py plans/prd/<slug>/clarify-01.yaml          # 生成同名 .html
```

### `questionnaire.html`
个人偏好问卷（浏览器打开）

### `recall.py`
记忆检索与索引 —— 渐进式披露的 L1 层（规范见 playbooks/memory.md）。
```
python tools/recall.py 关键词 [关键词...]   # 列出命中的记忆和记录：标题 / 日期 / 摘要 / 时效，不输出全文
python tools/recall.py 关键词 --scope memory  # 只搜 memory/（默认搜全部记录）
python tools/recall.py --due                 # 需要巩固或复核的记忆
python tools/recall.py --reindex             # 重建 memory/INDEX.md（会话启动 hook 会自动跑）
```

### `schedule.py`
固定日程 —— areas/work/index.md 的「固定日程」表 → 今天 / 明天 / 提前几天要提醒的事（开场卡片、早间建议共用）。
```
python tools/schedule.py                 # 今天、明天，以及今天该提前提的
python tools/schedule.py --days 7        # 未来 7 天都有什么
python tools/schedule.py --json          # 机读（session_start.py、morning-brief skill 用）
```

### `session_start.py`
SessionStart hook：每次会话启动时检测运行环境和各项状态。

### `start.ps1`
在 assistant 工作区开一个带名字的 Claude Code 会话（Windows）。名字会显示在输入框、/resume 列表和终端标题上。
```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\start.ps1                      # 日常会话，自动按上午 / 晚上命名
powershell -NoProfile -ExecutionPolicy Bypass -File tools\start.ps1 -Kind dev -Topic 派发能力   # 改进会话
```

### `stop_check.py`
Stop hook：保证每次会话都留下会话记忆（规范见 playbooks/memory.md）。

### `sweep.py`
回收派出去的会话 —— 查每个还没结束的派发：会话现在怎样、做完了有没有回报主会话；做完没回报的，把结果收回 assistant。
```
python tools/sweep.py                 # 总览：本机推进位 x/6 + 每个派发一行（设备、会话状态、最后一句、有没有回报、建议）+ 没登记的会话
python tools/sweep.py reclaim <运行记录或看板>   # 回收：把会话最后的总结记进「验收」，在待处理清单里补一条「完成」（不推送）
python tools/sweep.py reclaim <…> --close        # 同上，并关掉这个会话的终端（Orca 工作区和分支不动）
python tools/sweep.py adopt [--handle term_…] [--dry]   # 收编：没登记的会话（不含团队 worker、主会话）补派发记录、收回总结
```
