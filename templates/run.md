---
type: run
slug: <YYYY-MM-DD>-<计划>-<任务号>          # 子任务：<父运行记录>-S<n>
status: dispatched         # planned（只看了方案）→ dispatched（已派发）→ done / failed / abandoned（验收后）
created: YYYY-MM-DD HH:MM
updated: YYYY-MM-DD
task: <计划文件名>#T<n>
project: <项目 slug>
repo: <owner/repo>
agent: <claude | codex | opencode>
role: <角色 slug：dev / accept / research / investigate / product / planner；没有角色留空>
mode: <orca-new-worktree | orca-existing | local-window>
target: <Orca 工作区 id / 本地路径>
workdir: <实际工作目录>
handle: <Orca 返回的 agent handle；本地模式留空>
device: <派发它的设备指纹（tools/device.py）；会话只能在这台设备上查>
report_to: <回报给哪个主会话：派发者的 Orca 终端 handle；不在 Orca 里留空，消息只记待处理清单>
report_session: <派发者的会话 ID 前 8 位>
parent: <只有子任务有：父运行记录的 slug>
summary: <一句话：派了什么、给谁>
---

# [执行] <项目> · T<n> <任务>

## 任务说明
<!-- 派发时发给 agent 的原文（brief）。本地模式下，agent 会被要求先读这一节 -->
目标：
背景：
验收：
约束：
做完：
回报：<只在 完成 / 要拍板（产品、规划 Agent：交接 / 要拍板）时，用 tools/notify.py send 回报主会话；dispatch.py 自动写入。子任务不回报主会话，由派生它的跟进 Agent 来看>

## 授权
<!-- 你给跟进 Agent 的授权（没配跟进时给主会话）。执行方案、PRD 定稿就是授权范围：
     dispatch.py 按「任务引用的执行方案（plans/exec/，status active）> 引用的 PRD（定稿）> 任务自己的执行说明（计划已确认）」生成：
       执行方案就是授权范围。来源：…
       - 范围 / - 验收 / - 可以替你定（依据 profile/preferences.md）/ - 可以派的角色
       - 三级判定：① 有推荐、把握大、可回退、不改范围和验收 → 直接做，记「决策记录」，不发消息；
         ② 中等风险、要判断偏好但不敏感、跨任务、顺序协调 → 报主会话（「要拍板」开头「中等：」）；
         ③ 红线 → 必须你本人（「要拍板」开头「红线：」，主会话出卡片）
       - 必须升级 / - 你写明：<任务行「- 授权：」子项、开场粘贴「授权」行的原文，优先于生成的默认值>
     跟进 Agent 能不能派生子任务只看「可以派的角色」「你写明」两种行。都没有时是默认边界：「未单独授权：……不能派生子任务」。
     授权不发给执行会话（它按「约束」做）。子任务写「沿用父任务的授权」 -->

## 派发
- 时间、环境、命令：<dispatch.py 记录>
- 回报：<回报给哪个主会话；消息先记 runs/pending.md 再推送（playbooks/comms.md）。有跟进 Agent 时由它回报>

## 跟进说明
<!-- dispatch.py 派跟进 Agent 时写入：跟进对象、执行会话的 handle、怎么检查和记录。--no-follow 时为空 -->

## 决策记录
<!-- 跟进 Agent 做的决定（第 1 级直接做；第 2 级报主会话定后回填，理由写「主会话定的」/「你定的」；tools/follow.py --decide）：- YYYY-MM-DD HH:MM — 问题 → 选择 → 理由（备选：…） -->

## 子任务
<!-- 跟进 Agent 按「授权」派生的子任务（tools/follow.py --spawn --run 自动写）：
     - YYYY-MM-DD HH:MM — [S<n>](<子运行记录>.md) <角色> · <执行器> · <仓库> · <标题>（<handle 或方式>，工作目录 …） -->

## 监督
<!-- 跟进 Agent 每次检查有变化时加一行（tools/follow.py --log）：- YYYY-MM-DD HH:MM — <看到了什么：活跃 / 等待输入 / 已结束 / 产出> -->

## 验收
<!-- 结束时填：结果、证据（PR、提交、文件）、是否达到验收标准；对应任务在计划里改状态并写进展 -->
