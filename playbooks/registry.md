---
type: playbook
slug: registry
status: active
updated: 2026-10-19
tags: [meta, registry]
summary: "工作面注册表三态、仓库与本地路径模型、采集节奏、项目档案仓库一节、派发到项目干活的步骤"
---

# 工作面、仓库与采集 `v1`

> 从 CLAUDE.md 移出的细则。铁律仍在 CLAUDE.md §8。

## 注册表三态（`registry/projects.yaml`）

| 状态 | 含义 |
|---|---|
| `tracked` | **我确认要监控的**。只有这里的条目会被采集 |
| `candidates` | 扫描发现、我还没表态的，带客观证据 |
| `ignored` | 我明确排除的，不再提示 |

工具永远不会自动往 `tracked` 里加东西；往已有工作面里加仓库同样要我确认。发现新候选：`python tools/discover.py`。

## 工作面 ≠ 仓库；仓库 ≠ 本地路径

```yaml
- slug: example-project
  repos:
    - { github: <your-org>/example-app }
    - { github: <your-org>/example-api }
- slug: renamed-project
  repos:
    - { github: <your-github>/example-repo, aliases: [<your-github>/old-name] }
- slug: assistant
  repos:
    - { github: <your-github>/cloneme, self: true }
```

- 注册表只记 GitHub 仓库。`aliases` 是改名前的旧名；`self: true` 表示本仓库自身
- `team`（可选）：是不是团队仓库。不写时按 GitHub 账号判断——`identity.github`（和 `identity.github_alt`）是自己的，其余算团队。团队仓库不进开发交付流水线（按团队流程），单任务派发带过去的文件只放进不提交的 `.assistant/`
- **多设备的派发**：派发记录写 `device`（设备指纹）。会话只能在派发它的那台设备上查和处理；每台设备的早间建议、夜间巡检跑 `sweep.py --snapshot`，把本机派发现状写进 `runs/status/<指纹>.md` 随 git 同步，别的设备开场卡片和早间建议读这些快照；待处理清单里的来源在多设备时标上主机名
- **换设备缺克隆**：会话开场卡片提示有未完成任务、但本设备没克隆的项目；`python tools/clone.py --missing` 列全部，`python tools/clone.py <owner/repo> --run [--path …]` 克隆并登记（建议路径按本设备同一账号的仓库放在哪）
- `branches`（可选）：分支约定，默认 `{ main: main, dev: develop }`，开发交付流水线从 dev 分支切需求分支、合回 dev。团队项目分支规范不同时在这里写，比如 `branches: { main: master, dev: dev }`
- 本地路径按设备登记在 `registry/devices/<设备指纹>.yaml`，由 `tools/device.py` 维护：
  - 先校验已登记的路径：同一设备、路径没变就不重扫
  - 遇到没找过的仓库，或者缺失超过 7 天，才扫描磁盘
  - 唯一匹配自动确认；多个匹配等我确认（`device.py show` / `confirm` / `reject`）
  - 会话启动 hook、`collect.py`、`nightly.py` 都会自动跑这一步
- 本设备没有克隆的仓库，从 GitHub API 按我的账号采提交
- 多仓库工作面的提交标出仓库名；同一仓库的多个克隆按 sha 去重

## 采集
- `collect.py`：tracked 且 active 的仓库的提交（只算 `identity.git_authors` 里我的账号，agent 身份不算）、GitHub PR。输出到 `inbox/`，**不直接进 journal**，并入流程见 [inbox-merge](inbox-merge.md)
- `nightly.py`：夜间巡检第一段，见 [automation](automation.md)
- 节奏：`discover.py` 偶尔跑；`collect.py` 在周日盘点前跑

## 项目档案的「仓库」一节
`projects/<slug>/index.md` 的 `## 仓库` 表记录每个仓库的 GitHub 地址、可见性、主语言、默认分支、核实日期；**不写本地路径**。仓库改名或转仓时同时改 registry（旧名放进 `aliases`）和档案，并在时间线记一笔。建档见 [project-onboarding](project-onboarding.md)。

## 去具体项目干活
> 执行在项目目录的会话里做，不在助手工作区里改项目代码（CLAUDE.md §2）。早上确认的任务下有「执行说明」，可以直接粘贴。

1. `python tools/device.py show` 查本设备上的路径；没有克隆就按 GitHub 地址克隆，下次检查会自动登记
2. **先在今天的 journal 追加一条「开始做 X」**；看一眼该项目的未提交改动，多的话提醒我收尾
3. 切到项目目录工作：项目自己的 CLAUDE.md / AGENTS.md 规范照常生效
4. 回到这里，把结果追加进 journal，并更新项目档案
