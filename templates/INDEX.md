# 记录类型 → 模板

> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。
> **写任何记录之前，先在这里找到类型、读对应模板，再按模板写**。没有对应类型的新记录，先建模板（你同意）再写。
> 数据源 [catalog.yaml](catalog.yaml)；「条目模板」见 [entries.md](entries.md)；mode：新建 / 追加 / 改写。

**生成于** 2026-10-23

| 记录类型 | 模板 | 写到哪 | mode |
|---|---|---|---|
| 身份档案 | 单例：profile/about.md 自身结构 | `profile/about.md` | 改写 |
| 首次使用问卷 | onboarding-questionnaire.html（示例，可复制后改题和推荐） | `templates/onboarding-questionnaire.html` | 新建（onboarding skill · A 步） |
| 偏好条目 | [entries.md#偏好](entries.md#偏好) | `profile/preferences.md` | 追加 |
| 当前状态 | 单例：profile/now.md 自身结构 | `profile/now.md` | 改写 |
| 助手人设 | 单例：profile/persona.md 自身结构 | `profile/persona.md` | 改写 |
| 人设风格条目 | [entries.md#人设风格](entries.md#人设风格) | `profile/persona.md 的「风格记录」` | 追加 |
| 长期目标 | 单例：plans/goals.md 自身结构 | `plans/goals.md` | 改写 |
| 计划 | [plan.md](plan.md) | `plans/YYYY-MM-DD-<slug>.md` | 新建 |
| 计划任务行 | [entries.md#计划任务](entries.md#计划任务) | `plans/*.md 的「任务」一节` | 追加 |
| 周计划 | [weekly-plan.md](weekly-plan.md) | `plans/weekly/YYYY-Www.md` | 新建 |
| 当日计划 | [daily-plan.md](daily-plan.md) | `plans/daily/YYYY-MM-DD.md` | 新建 |
| 需求澄清问卷 | pm-clarify skill（题目清单 YAML → python tools/qform.py 生成 HTML） | `plans/prd/<slug>/clarify-NN.yaml / .html` | 新建 |
| 需求要点 | pm-clarify skill | `plans/prd/<slug>/requirements.md` | 改写 |
| PRD | pm-prd skill | `plans/prd/<slug>/prd.md` | 改写 |
| 原型 | pm-prd skill | `plans/prd/<slug>/prototype/NN-<屏名>.html` | 新建 |
| PRD 评审页 | 工具生成：python tools/prd_html.py | `plans/prd/<slug>/review.html` | 新建 |
| 执行方案 | [exec-plan.md](exec-plan.md) | `plans/exec/YYYY-MM-DD-<slug>.md` | 改写（规划 Agent 写；你确认后 status active，就是跟进 Agent 的授权） |
| 澄清回答 / 评审意见 | 你导出的原文，不改动 | `plans/prd/<slug>/answers-NN.md · comments-NN.md` | 新建 |
| 以后再说条目 | [entries.md#以后再说](entries.md#以后再说) | `plans/someday.md` | 追加 |
| 零散待办条目 | [entries.md#todo](entries.md#todo) | `todo.md` | 追加 |
| 日记文件 | [journal.md](journal.md) | `journal/YYYY/MM/YYYY-MM-DD.md` | 新建 |
| 日记条目 | [entries.md#journal](entries.md#journal) | `journal/YYYY/MM/YYYY-MM-DD.md` | 追加 |
| 项目档案 | [project.md](project.md) | `projects/<slug>/index.md` | 改写 |
| 领域档案 | [area.md](area.md) | `areas/<slug>/index.md · private/health/index.md` | 改写 |
| 时间线行 | [entries.md#时间线](entries.md#时间线) | `项目 / 领域档案的「时间线」` | 追加 |
| 决策 | [decision.md](decision.md) | `decisions/YYYY-MM-DD-<slug>.md` | 新建 |
| 会议纪要 | [meeting.md](meeting.md) | `meetings/YYYY-MM-DD-<topic>.md` | 新建 |
| 协作者 | [person.md](person.md) | `people/<name>.md` | 改写 |
| 训练记录 | [entries.md#训练记录](entries.md#训练记录) | `private/health/training-log.md` | 追加 |
| 训练计划调整 | [entries.md#训练计划](entries.md#训练计划) | `private/health/index.md 的「训练计划」` | 改写 |
| 饮水记录 | [entries.md#饮水](entries.md#饮水) | `private/health/water-log.md` | 追加 |
| 体检记录 | [entries.md#体检记录](entries.md#体检记录) | `private/health/index.md 的「体检记录」` | 追加 |
| 财务条目 | [entries.md#财务](entries.md#财务) | `private/finance/index.md` | 追加 |
| 周报月报 | [review.md](review.md) | `reviews/YYYY-Www.md · reviews/YYYY-MM.md` | 新建 |
| 进展汇报 | [progress-report.md](progress-report.md) | `reviews/progress/YYYY-MM-DD-<范围>.md` | 新建 |
| 夜间巡检报告 | [progress-report.md](progress-report.md) | `reviews/nightly/YYYY-MM-DD.md` | 新建 |
| 待回复汇报 | [report-cards.md](report-cards.md) | `对话里回复，默认不落文件；你说「存一下」时 reviews/progress/YYYY-MM-DD-cards.md` | 新建（report-cards skill：卡片在前、进展表在后） |
| 汇报卡片 | [entries.md#汇报卡片](entries.md#汇报卡片) | `待回复汇报、进展汇报、早间建议确认、待处理消息的回复里，一件要你回复的事一张` | 追加 |
| 事情盘点表 | 工具生成：python tools/inventory.py --write | `reviews/inventory/YYYY-Www.md` | 新建 |
| 采集草稿 | 工具生成：python tools/collect.py / nightly.py | `inbox/` | 新建 |
| 派发记录 | [run.md](run.md) | `runs/YYYY-MM-DD-<计划>-<任务>.md` | 新建（跟进 Agent 用 tools/follow.py 追加监督 / 决策 / 验收，按授权派生子任务 <父>-S<n>.md） |
| 待处理清单 | [pending.md](pending.md) | `runs/pending.md` | 追加（tools/notify.py 写入；主会话 notify.py done 标已处理） |
| 设备派发快照 | 工具生成：python tools/sweep.py --snapshot | `runs/status/<设备指纹>.md` | 改写（早间建议、夜间巡检会跑） |
| 指挥台回复分表 | [replies.md](replies.md) | `runs/replies.md` | 追加（本地通道 / tools/notify.py 写入；forward / ack / handled 改状态；第一次写入时按模板生成） |
| 交付看板 | [run-delivery.md](run-delivery.md) | `runs/YYYY-MM-DD-<项目>-<需求>.md` | 新建（tools/delivery.py 创建，跟进 Agent 维护） |
| 实现计划 | [impl-plan.md](impl-plan.md) | `<项目仓库>/docs/delivery/<需求>/impl-plan.md` | 新建（dev-plan skill） |
| 验收用例 | [cases.md](cases.md) | `<项目仓库>/docs/delivery/<需求>/cases.md` | 新建（accept-cases skill） |
| 工作包进度 | [package.md](package.md) | `<项目仓库>/docs/delivery/<需求>/packages/P<n>.md` | 改写（dev-build / dev-fix skill） |
| 审查报告 | [review-report.md](review-report.md) | `<项目仓库>/docs/delivery/<需求>/reviews/P<n>-r<N>.md` | 新建（accept-review skill） |
| 测试报告 | [test-report.md](test-report.md) | `<项目仓库>/docs/delivery/<需求>/acceptance/report-r<N>.md` | 新建（accept-test skill） |
| 会话记忆 | [session.md](session.md) | `memory/sessions/YYYY-MM-DD-<会话ID前8位>.md` | 新建 |
| 记忆卡 | [memory-card.md](memory-card.md) | `memory/long/M-xxxx-<slug>.md` | 新建 |
| 术语 | [entries.md#术语](entries.md#术语) | `memory/glossary.md` | 追加 |
| Agent 角色 | [agent-role.md](agent-role.md) | `agents/<slug>.md` | 改写 |
| playbook | [playbook.md](playbook.md) | `playbooks/<slug>.md` | 新建 |
| skill | [skill.md](skill.md) | `.claude/skills/<name>/SKILL.md` | 新建 |
| 规范变更记录 | [entries.md#规范变更](entries.md#规范变更) | `playbooks/README.md` | 追加 |
| 工作面注册 | 格式见 playbooks/registry.md | `registry/projects.yaml` | 改写 |
