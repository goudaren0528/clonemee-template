---
type: playbook
slug: project-onboarding
status: active
updated: 2026-10-19
tags: [meta]
summary: "新工作面纳入 tracked 后的建档步骤（GitHub 核实、档案、索引）"
---

# 项目建档 `v0`

## 触发
- 我确认某个候选进入 `tracked`（只有我能确认）
- 已有工作面的仓库改名、转仓、换本地路径、加仓库

## 步骤
1. **注册表**：在 `registry/projects.yaml` 的 `tracked` 写好 `slug / name / kind / role / status / repos`。repos 只写 `github`（改过名的旧名放 `aliases`），**不写本地路径**。
2. **GitHub 信息**：对每个仓库跑
   ```bash
   gh repo view <owner/repo> --json nameWithOwner,visibility,primaryLanguage,defaultBranchRef,createdAt,pushedAt,description
   ```
   注意 `nameWithOwner` 与注册表不一致时，说明仓库改名或转仓了，以 GitHub 返回的为准，两处同步改，旧名放进 `aliases`。
3. **本地信息**：跑 `python tools/device.py` 让本设备自动登记克隆，再用 `python tools/device.py show` 找到路径；看根目录有没有 `README.md / AGENTS.md / CLAUDE.md`。路径只留在设备登记里，不写进档案。
4. **建档**：用 [`templates/project.md`](../templates/project.md) 建 `projects/<slug>/index.md`，填 `## 仓库` 表格并写上核实日期。「一句话」可以摘 README，注明「摘自 README」；我的角色和当前状态等我说。
5. **索引**：档案 frontmatter 写好 `summary`、`status`、`started`，跑 `python tools/build_index.py`，项目索引和总地图会自动更新。
6. **记录**：当天 journal 记一条，项目时间线写「建档」。

## 注意
- README 内容只是参考，可能过时或拷错（例如两个仓库的 README 内容一模一样）。有疑问写进档案 `## 待确认`。
- 本地有大量未提交改动、本地分支很旧这类异常，写进 `## 待确认`，不替我下结论。
