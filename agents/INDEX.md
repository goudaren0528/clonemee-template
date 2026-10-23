# Agent 区

> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。
> 角色化 agent，派发时用 `--role <slug>` 注入。说明见 [playbooks/agents.md](../playbooks/agents.md)。

**生成于** 2026-10-22

- [调研 Agent](research.md) — 调研 Agent：读源码和文档出现状报告（功能、页面、数据模型、接口、已知问题），只陈述现状，不写 PRD、不改代码 · active
- [产品 Agent](product.md) — 产品 Agent：先用 HTML 问卷把需求问清楚，再出带原型、可评论的 PRD · active
- [跟进 Agent](pm.md) — 跟进 Agent（这件事的 PM）：执行方案 / PRD 定稿就是授权范围，范围内按三级判定处理——有推荐、可回退、范围不变默认直接做，中等… · active
- [规划 Agent](planner.md) — 规划 Agent：非开发类的事派发前先出执行方案（目标、步骤、谁做、验收、风险、授权范围），你确认后它就是跟进 Agent 的授权 · active
- [排查 Agent](investigate.md) — 排查 Agent：只读地从现象查到根因，出排查结论（现象、复现、根因、证据、修复建议、影响范围）；不改代码、不动生产 · active
- [开发 Agent](dev.md) — 开发 Agent：按 PRD 写实现计划和工作包，按包实现并自测，按验收的问题修复；不当裁判 · active
- [验收 Agent](accept.md) — 验收 Agent（原 Review Agent）：先出用例，两路审查，e2e 和 Orca 内置浏览器真测，有界面的交付搭好本地体验环境发链… · active
