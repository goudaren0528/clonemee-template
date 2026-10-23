---
name: cleanup
description: 清理做完的会话和 Orca 工作区。当你说"清理一下""关掉做完的会话""删掉没用的工作区""工作区删不掉"，或者开场卡片、夜间巡检、sweep 提示「可以清理：N 个会话、M 个工作区」、跟进 Agent 收尾时使用：用 tools/cleanup.py 按项目列出可以清理的会话和工作区（标已合并 / 有未推送提交 / 有未提交改动 / 还在干活），你确认后关终端、删工作区、删已合并的分支、git worktree prune；有风险的单独问；删不掉的按进程当前目录找出占用者。
---

# 清理会话和工作区 `v0.2`

派发多了，做完的执行会话、跟进会话、空终端、团队 worker 和合并过的工作区会一直挂在 Orca 里，占内存、占 C 盘，也让人找不到正在干活的那个。这个 skill 负责**提醒 + 协助清理**：工具盘点、你确认、主会话执行。交互使用；无人值守的会话（夜间巡检、早间建议）只提醒，不清。

**及时清**：「可以清理」表里的（派发已结束满 15 分钟的会话、空终端、已合并且没有未推送 / 未提交 / 被忽略文件的工作区）由主会话**及时直接清，不用再问**，清完报一句；只有「要问你」表（未推送提交、未提交改动、被忽略的文件、还在干活）和 `unlock --kill` 才问；「宽限期内」表的不清也不问，过了宽限期自动进「可以清理」。什么时候清：
- 处理完一条「完成」消息、关掉一个运行记录之后：`python tools/cleanup.py --for runs/<运行记录>.md --run`（刚关的在宽限期内，这次不会清，工具会说几分钟后可清；交给下一次定时清理）
- 跑 `sweep.py` 看到末尾「可以清理：…」时
- 主会话有派发在跑时，建一个每 30 分钟的 CronCreate：`cleanup.py --run`（只清可以清理的），有清掉的才报一句

## 1. 盘点（只读）
```bash
python tools/cleanup.py                          # 全部：按项目出表
python tools/cleanup.py --for runs/<运行记录>.md   # 只看一个派发（运行记录或交付看板），按「收尾后」算
python tools/cleanup.py --brief                  # 一行摘要（为了快不 fetch，远端引用按本地缓存算）
```
工具会列：

| 类型 | 什么时候算可以清 |
|---|---|
| 会话 | 运行记录 done / failed / abandoned 满 15 分钟（宽限期）里登记的执行会话、跟进 Agent、流水线的开发和验收会话；收编（kind: adopted）且回报已经处理的会话；没有 agent 在跑、30 分钟没输出的空终端 |
| worker | 团队 Orca 编排里已结束（succeeded / failed）的 worker：用 `orca orchestration worker-release` 释放，Orca 会先存档输出 |
| 工作区 | Orca 记着删除失败（removalError）或目录已经没了的；运行记录已结束满 15 分钟的；分支已合进 main / master / develop / 起点分支的 |

每项的「状态」标 **已合并 / 有未推送提交 / 有未提交改动 / 有被忽略的文件 / 还在干活**。后四种进「要问你」表，不会自动清。运行记录刚结束、还没满宽限期的进「宽限期内」表，标「宽限期内，还有 N 分钟」。
不碰：主会话（`inbox/logs/main-sessions.json` 登记的）、当前会话、没结束的派发登记的会话和工作区、各仓库的主目录、运行工具的这个工作区。会话退出了但派发还没结束的，在「提示」里列出来，交给 sweep / 跟进 Agent 重启，不清。

### 宽限期
运行记录一关，下一次定时清理（每 30 分钟）就可能把工作区删掉，跟进 Agent 或主会话来不及再用。所以**只因「运行记录结束」能清的，结束后 15 分钟内不清**（`GRACE_MIN`）：
- 结束时间：`follow.py --close`、`delivery.py finish` 把 status 改成结束态时在 frontmatter 写 `closed_at: 'YYYY-MM-DD HH:MM'`（再关一次会更新）；没有 `closed_at` 的老记录退回用文件修改时间
- 宽限期内的单列「宽限期内」表：不自动清（`--run` 会说「宽限期内没清 N 项，最早 M 分钟后可清」），也不算「要问你」；你点名（`--also 键`）才提前清
- `--for <运行记录>` 收尾时，还没关的运行记录按「现在结束」算，表里显示 15 分钟后可清
- 不加宽限的：分支已合进主干、Orca 删除失败过、目录已经没了、空终端（agent 已经退出）、团队编排 worker 已结束

### 被忽略的文件
被 `.gitignore` / `.git/info/exclude` 挡住的文件不算未提交改动，删工作区会跟着没。工具对每个候选工作区跑 `git ls-files --others --ignored --exclude-standard --directory --no-empty-directory`：
- 整个被忽略的目录只算一项（不递归数文件，大目录也快）；空文件不算
- 可重新生成的不算：`node_modules`、`.next`、`dist`、`build`、`out`、`coverage`、`.turbo`、`.vercel`、`__pycache__`、`*.pyc`、`.venv` / `venv`、`.pytest_cache`、`.mypy_cache`、`.cache`、`*.egg-info` 这类；交接包 `.assistant/handoff`、assistant 工作区的 hook 日志 `inbox/logs`（每个会话都会写）
- 剩下有内容的标「有被忽略的文件 N 个（前两项）」，进「要问你」；`test-results/`、`artifacts/`、嵌套的 `.worktrees/` 都算

### fetch
「有未推送提交」按远端引用算，本地缓存可能过期。所以盘点前对涉及的每个仓库 `git fetch --quiet origin` 一次（并行，30 秒超时，不弹凭据框）：
- 失败不挡，在「提示」里写「xx 仓库 fetch 失败，远端引用可能过期」；没有 origin 的仓库不 fetch
- `--brief` 和 `sweep.py` 的一行摘要为了快不 fetch
- `--run` 删工作区之前再确认一次：盘点时没 fetch 过的仓库当场补 fetch、重新核对，冒出未推送 / 未提交 / 被忽略文件的不清（「⏭ … 没清」）

## 2. 给你确认（「要问你」的）
- **原样贴工具的表**（第一列是项目），先说结论：「可以清理 N 个会话、M 个工作区；另有 K 项要问你」
- 「要问你」里的每一项说清风险：未推送提交是什么（`git -C <工作区> log --oneline @{u}..` 或 `--not --remotes`）、未提交改动是哪些文件、被忽略的是什么（`git -C <工作区> ls-files --others --ignored --exclude-standard --directory`，看是不是测试证据、日志、本地配置）；建议怎么处理（先推送 / 先派发收尾 / 证据挪到运行记录或交付目录 / 确认不要了）
- 你可以只清一部分：「清前三个」「T2 那个先留着」→ 换成 `--only` / `--skip` 的键

## 3. 执行（主会话做；「可以清理」的直接做，「要问你」的你确认后）
```bash
python tools/cleanup.py --run                                # 清「可以清理」的全部
python tools/cleanup.py --run --only k1,k2 --skip k3 --also k4   # 只清几项 / 跳过 / 「要问你」里你点了头的
python tools/cleanup.py --for runs/<运行记录>.md --run        # 只清这个派发的
```
工具按顺序：（盘点时没 fetch 过的仓库补 fetch、重新核对）→ 释放 worker → 关会话 → 删工作区（关掉里面的终端 → `orca worktree rm --force`，Orca 会顺手删能证明已合并的分支 → 目录还在就找占用者，见第 4 节 → 再删 → 核实过已合并、还留着的分支删掉）→ 每个涉及的仓库 `git worktree prune`。每项一行结果，失败的说清原因。
- 清理是 Orca 层面收拾已经做完的东西，你确认过就由主会话直接执行，不用派发。工具只删核实过已合并的分支；没合并的分支、有未推送提交或未提交改动的工作区，没有你点名（`--also`）一律不删
- 跟进 Agent **不自己清**：它收尾时只出清单、写进「完成」汇报，由主会话在你确认后执行（它自己的会话也在清单里）

## 4. 删不掉的工作区
Windows 上目录被进程占着时，`orca worktree rm` 会报 EBUSY（Orca 记成 removalError），或者报成功但目录还在。
```bash
python tools/cleanup.py unlock <目录>          # 按进程当前目录（读进程 PEB）列出占着它的进程，只读
python tools/cleanup.py unlock <目录> --kill   # 你确认后：关这个工作区的终端，结束剩下的进程，删目录、去掉 Orca 登记
```
- 常见原因：OpenCode 后台服务（`opencode serve --service`，常驻）在 Orca 登记的工作区里起 MCP 子进程（各类 MCP 服务器），当前目录就在工作区里。`--run` 删工作区时会自动结束这一类（不影响别的会话），其他进程只列出来
- 还删不掉：报清楚是哪个进程（pid、程序、命令行、父进程）；一个进程都找不到时，多半是杀毒软件或资源管理器开着这个目录，请你关掉再试
- 目录还是有效工作区（没有 removalError、git 能认出来）时 `unlock --kill` 不动它，改走 `--run --only`

## 5. 提醒从哪来
- `python tools/sweep.py` 末尾一行「可以清理：…」；`--snapshot` 写进快照的 `cleanup` 字段 → **开场卡片**显示「可以清理：N 个会话、M 个工作区，说「清理一下」」
- **夜间巡检**（nightly-review）：快照里的这一行写进巡检报告「需要你确认」，只提醒
- **跟进 Agent 收尾**：`follow.py --close` 后直接打出这个派发的清单（刚关的在「宽限期内」，标几分钟后可清）；`delivery.py finish` 提示用 `cleanup.py --for <看板>`。清单写进「完成」汇报

## 6. 收尾
journal 记一条（清了哪些、留了哪些、为什么、删不掉的原因），标签带涉及的 `#project/<slug>`；更新会话记忆；提交。

## 禁止
- 没经你确认就加 `--also`、`unlock --kill`（只清「可以清理」的 `--run` 不用问）
- 无人值守的会话、跟进 Agent 自己执行清理
- 手动 `git branch -D`、`rm -rf` 工作区目录绕过工具的检查（未合并、未推送、未提交的会丢）
- 结束第 4 节以外、和工作区无关的进程
