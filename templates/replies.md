---
type: replies
slug: replies
status: active
updated: YYYY-MM-DD
tags: [meta, comms, console]
summary: "指挥台回复分表：你在指挥台卡片上发的回复，先记这里再推给主会话，主会话转给接收方、接收方回执；和待处理清单共用编号"
---

# 指挥台回复

<!-- 单例：runs/replies.md，由 tools/notify.py 写入和更新，列名不要改（列定义在 tools/console_contract.py 的 REPLY_COLUMNS，规则见 playbooks/comms.md）。
     编号 N<月日>-<序号>，和 runs/pending.md 共用一套；时间 YYYY-MM-DD HH:MM:SS；设备是发出它的设备指纹，只有这台设备转达。
     页：待处理 / 运行监督 / 派发确认 / 角色管理；关联：原消息编号，或运行记录 / 交付看板的绝对路径，派发确认和角色管理写 —。
     正文是一行；派发确认、角色管理的多条【控制台指令】用 ⏎ 连接。
     送达：同待处理清单（已推送 HH:MM → … / 待转达 / 未推送（原因））。转给：跟进 Agent（handle）/ 执行会话（handle）/ 主会话自己。
     状态：等主会话 → 已送达主会话 → 已转给跟进 → 跟进已收到 / 主会话已处理 / 转不到：原因。回执：MM-DD HH:MM 回执原文。新行加在表格末尾。 -->

| 编号 | 时间 | 设备 | 页 | 事项 | 关联 | 快捷评价 | 正文 | 送达 | 转给 | 状态 | 回执 |
|---|---|---|---|---|---|---|---|---|---|---|---|
