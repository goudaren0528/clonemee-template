---
type: playbook
slug: automation
status: active
updated: 2026-10-20
tags: [meta, automation, orca]
summary: "定时任务（早间建议、夜间巡检）：Orca 自动化为主、Windows 任务计划程序备份，原理、去重、补跑、安装、停用与排错"
---

# 定时任务：安装与排错 `v1`

| 任务 | 时间 | skill | 产出 |
|---|---|---|---|
| 早间建议 | 周一到周六 10:00 | `morning-brief` | `plans/daily/<日期>.md`（草案，开会话时带你确认） |
| 夜间巡检 | 每天 22:00 | `nightly-review` | 项目进展、`reviews/nightly/<日期>.md`、journal、会话记忆 |
| 周日事情盘点 | **不定时**，由你发起 | `weekly-inventory` | `reviews/inventory/YYYY-Www.md`、下周周计划 |

**推荐由 Orca 自动化触发**，Windows 任务计划程序作备份、晚 15 分钟。两边最后都走 `tools/agent-run.ps1`，同一天同一个任务只跑一次。

| 触发方 | 名字 | 时间 | 状态 |
|---|---|---|---|
| **Orca 自动化（主）** | `assistant 早间建议` · `assistant 夜间巡检` | 10:00 · 22:00（Asia/Shanghai） | 装好后按默认时间跑；工作区是仓库主目录（main） |
| Windows 任务计划程序（备份） | `assistant-morning` · `assistant-nightly` | 10:15 · 22:15 | 比 Orca 晚 15 分钟；**连续 2 天 Orca 按时产出后停用（先问你），不删除** |

查看：`orca automations list` · `orca automations runs --id <id>`；`Get-ScheduledTask assistant-* | Get-ScheduledTaskInfo`。

## 一次运行怎么走

| 段 | 做什么 | 谁 |
|---|---|---|
| 〇 · 预检（只有 Orca） | 到点先跑 precheck：`agent-run.ps1 -Job <job> -Trigger orca -Check`。今天已经跑过、或另一边正在跑 → 退出 1，Orca 记「Precheck skipped」，不启动会话 | Orca，`cmd /c`，60 秒超时 |
| 一 · 启动器（只有 Orca） | Orca 在仓库主目录开一个 Claude Code 会话（sonnet、低思考），只跑一条 `agent-run.ps1 -Job <job> -Trigger orca -Detach`：把任务转到后台（`conhost --headless`）后立即返回，十几秒结束。不写文件，不写会话记忆（脚本登记了它的会话 ID，Stop hook 放行） | Orca 自动化的 agent 会话 |
| 二 · 采集（只有夜间巡检） | 各仓库当天我的提交、增删统计、改动文件、关键 diff 片段（跳过证据、截图、锁文件），以及当天的 PR | `tools/nightly.py`，确定性，不调用模型 |
| 三 · 整理 | 无头 `claude -p` 按 skill 写文件、本地提交 | 只开放本仓库读写、`python tools/*`、`git add/commit` 等少量命令 |
| 四 · 跑完 | 确认仓库私有后推送（推送自动过 pre-push 体积闸，超限就不推、日志里能看到原因），弹 Windows 通知 | `agent-run.ps1` |

- 为什么要一个启动器会话：Orca 自动化只能「到点给某个 agent 发一段 prompt」，不能直接跑脚本。让它只做启动，真正的工作还在权限收紧的 `claude -p` 里做，推送、通知、日志、去重都只有 `agent-run.ps1` 一份
- 无头会话本身禁止 push 和任何对外发送；推送由脚本做，脚本推送同样过 pre-push 体积闸（`tools/push_guard.py`：累计 10MB / 单个 1MB / 密钥特征就拒绝），被拦时 `Push-Repo` 返回「推送失败，看日志」，通知里也会带这一句
- 会话启动和结束 hook 在无头模式下同样生效：启动时注入上下文，结束前强制写会话记忆

## 去重和排队（`agent-run.ps1`）
- **同一天只跑一次**：`inbox/logs/<job>-<日期>.log` 里已经有「结束 <job> exit=0」就跳过（写一行「跳过」，不弹通知）。失败（exit≠0）的不算，另一边到点还会再跑一次。要重跑加 `-Force`
- **同时只跑一个**：锁文件 `inbox/logs/agent-run.lock`，进程退出时系统自动释放，不会残留。拿不到就排队（每 30 秒试一次，最多 45 分钟），拿到后再查一次是否已跑过
- **日期**：不给 `-Date` 时，夜间巡检如果是自动触发（`-Trigger task / orca`）在 21 点前跑，算前一天的——那一定是补跑昨晚漏掉的那次；手动的 12 点前算前一天。早间建议总是当天
- 所以两边同时开着也不会重复：Orca 10:00 跑完 → Windows 10:15 看到已跑过就跳过；Orca 没开或跑失败 → Windows 10:15 补上，之后 Orca 再打开补跑时 precheck 跳过
- 去重只在一台设备内有效（日志在 `inbox/`，不进 git）。**只在主力设备上装**，避免多台重复巡检

## Orca 没开、错过了会怎样

> 依据：`orca skills get orca-cli --reference references/automations.md`，以及 Orca 安装目录 `resources/app.asar` 里调度器的代码（只读）；「Orca 关掉」的行为按代码推断，请在你的环境里验证。

| 情况 | 结果 |
|---|---|
| Orca 开着 | 调度器每 60 秒检查一次，到点触发。到点（误差在一分钟内）触发，可用一个临时自动化验证 |
| **Orca 没开** | **不触发**。调度器在 Orca 主进程里，没有独立的后台服务；Windows 上关掉最后一个窗口 Orca 就退出（`orca serve` 模式除外） |
| Orca 开着但没有可用窗口 | 不启动，记「Unavailable」（No Orca window was available） |
| **错过之后再打开 Orca（或电脑从睡眠醒来）** | 在**宽限期内只补跑一次**：只看最近一次应跑时间，离现在不超过 `missedRunGraceMinutes`（默认 720 分钟 = 12 小时）+ 2 分钟就补跑（scheduled 触发，precheck 照常跑），更早的几次不补；超过了记「Skipped」（skipped_missed），不跑 |
| 补跑的时间窗 | 早间建议：10:00 后 12 小时内（到 22:00）；夜间巡检：到第二天 10:00，按上面的日期规则算前一天 |

所以 **Orca 当主力的前提是白天 Orca 一直开着**；Windows 备份只在电脑开着、你登录着时跑，到点关机的话下次开机补跑（StartWhenAvailable）。两边都停用后，Orca 关了超过 12 小时就会漏，要手动补：`orca automations run <id>` 或下面的手动命令。

## 手动跑一次
```powershell
orca automations run <id>                                                                                # 走 Orca 整条链路（跳过 precheck，脚本里照样去重）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job nightly                     # 直接跑今天的夜间巡检
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job morning -Date 2027-03-09 -Force  # 某天的早间建议，已跑过也重跑
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job morning -Check              # 只看今天该不该跑（0 该跑 / 1 已跑过或正在跑）
```

## 安装
**Orca 自动化**（用 `orca` 命令改，不直接改 Orca 的配置文件）：
```powershell
orca automations create --name "assistant 早间建议" --trigger "0 10 * * 1-6" --timezone Asia/Shanghai --provider claude `
  --workspace path:<仓库绝对路径> --extra-agent-args="--model sonnet --effort low" `
  --precheck "powershell -NoProfile -ExecutionPolicy Bypass -File tools/agent-run.ps1 -Job morning -Trigger orca -Check" --precheck-timeout 60 `
  --prompt "<启动器 prompt，见下>" --enabled --json
# 夜间巡检同上：--trigger "0 22 * * *"，-Job nightly
orca automations edit <id> --disabled          # 停用；--enabled 恢复；--trigger / --time 改时间
```
- 启动器 prompt（早间建议；夜间巡检把「早间建议」「morning」换掉）：「【角色设定：定时任务启动器】你只负责启动助手的早间建议，不做别的。本次任务：用 Bash 工具运行下面这一条命令（几秒内返回，真正的工作在后台进行），把它的输出原样回复，然后结束：`powershell -NoProfile -ExecutionPolicy Bypass -File tools/agent-run.ps1 -Job morning -Trigger orca -Detach`。约束：不要打招呼，不要读别的文件，不要写任何文件，不要提交，不要写会话记忆……命令失败就回复错误输出，不要重试、不要排查。」完整原文用 `orca automations show <id>` 看
- precheck 在仓库主目录里跑，和 `--workspace` 无关；命令里的路径用 `/`
- 换设备：`--workspace path:<本设备上助手仓库主克隆的路径>`

**Windows 任务计划程序（备份）**：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1              # 两个都装：10:15 / 22:15，带 -Trigger task
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Disable     # 停用（任务留着当备份）；-Enable 恢复
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Remove      # 全部卸载
```
- 注册在当前用户名下，不需要管理员；只在你登录时运行；用 `conhost --headless` 启动，不弹黑屏窗口
- 改了 `agent-install.ps1` 要在**仓库主目录里**重新跑一次才生效（任务指向跑安装的那份脚本）
- 改时间前可以先导出原任务（`Export-ScheduledTask`），需要还原时 `Register-ScheduledTask -Xml (Get-Content <文件> -Raw) -TaskName <名字>`

## 从 Windows 切到 Orca 的步骤
1. 建好两个 Orca 自动化并启用；手动触发一次早间建议，确认产出正常；Windows 任务改到 10:15 / 22:15（状态：待你执行）
2. 脚本还没更新到位时，Orca 的 precheck 可能不认 `-Check`，到点记「Precheck skipped」，只有 Windows 在跑，不会重复
3. **脚本更新后**：在仓库主目录跑一次 `tools\agent-install.ps1`（让 Windows 任务带上 `-Trigger task`，补跑时日期才按自动规则算）
4. 看连续 2 天：`orca automations runs --id <id>` 里是「Done」，`inbox/logs/<job>-<日期>.log` 里是「开始 … （orca）」「结束 … exit=0」，同一天的 Windows 那次是「跳过」
5. 连续 2 天都按时 → **先问你**，同意后 `tools\agent-install.ps1 -Disable`（停用，不删除），把上面的状态表改掉

## macOS / Linux
没有 `agent-run.ps1`：Orca 自动化的启动器 prompt 改成直接跑 `python3 tools/nightly.py` 和 `claude -p "按 .claude/skills/nightly-review/SKILL.md 执行 <日期> 的夜间巡检。" --permission-mode acceptEdits --allowedTools …`（参数与 `tools/agent-run.ps1` 保持一致），或者用 cron / launchd：早间建议 `0 10 * * 1-6`，夜间巡检 `0 22 * * *`。去重要自己做。

## 排错
- 日志：`inbox/logs/<nightly|morning>-<日期>.log`（不进 git）：转后台、开始（带触发方）、跳过、排队、结束都会记
- 第一段的摘要：`inbox/nightly-<日期>.md`
- Orca：`orca automations runs --id <id> --json` 看 `status`、`error`、`precheckResult`（precheck 的输出）、`outputSnapshot`（启动器的回复）
- Windows：`Get-ScheduledTask assistant-* | Get-ScheduledTaskInfo`
- 启动器会话 ID 登记在 `inbox/logs/launcher-sessions.txt`；启动器要是被 Stop hook 拦下来写了会话记忆，说明登记没成功，看日志第一行
- 第二段失败但第一段成功：第二天会话开场时说一声，我会补跑整理

## 代价
- 每天一到两次无头会话（每次两三分钟），会消耗 Claude 用量；Orca 每次另有一个十几秒的启动器会话（sonnet、低思考，但会加载会话启动上下文）
- 自动内容可能把证据文件之类的噪音误读成进展：发现了就告诉我，我来调整 `nightly.py` 的过滤规则
